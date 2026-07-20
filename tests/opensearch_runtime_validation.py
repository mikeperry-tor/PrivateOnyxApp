#!/usr/bin/env python3
"""Container-engine-neutral OpenSearch runtime and workload validation."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import statistics
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote


HEAP_BYTES = 512 * 1024 * 1024
EXPECTED_PLUGINS = {
    "opensearch-alerting",
    "opensearch-anomaly-detection",
    "opensearch-asynchronous-search",
    "opensearch-cross-cluster-replication",
    "opensearch-custom-codecs",
    "opensearch-flow-framework",
    "opensearch-geospatial",
    "opensearch-index-management",
    "opensearch-job-scheduler",
    "opensearch-knn",
    "opensearch-ltr",
    "opensearch-ml",
    "opensearch-neural-search",
    "opensearch-notifications",
    "opensearch-notifications-core",
    "opensearch-observability",
    "opensearch-performance-analyzer",
    "opensearch-reports-scheduler",
    "opensearch-search-relevance",
    "opensearch-security",
    "opensearch-security-analytics",
    "opensearch-skills",
    "opensearch-sql",
    "opensearch-system-templates",
    "opensearch-ubi",
    "query-insights",
}


class ValidationError(RuntimeError):
    pass


@dataclass
class OpenSearchClient:
    container_bin: str
    container: str
    latencies: list[float] = field(default_factory=list)

    def exec_text(self, shell_command: str) -> str:
        completed = subprocess.run(
            [self.container_bin, "exec", self.container, "sh", "-c", shell_command],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return completed.stdout

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | str | None = None,
        *,
        timeout: float = 60.0,
    ) -> dict[str, Any] | list[Any]:
        command = [
            self.container_bin,
            "exec",
            "-i",
            "-e",
            f"PRIVATE_ONYX_VALIDATION_METHOD={method}",
            "-e",
            f"PRIVATE_ONYX_VALIDATION_PATH={path}",
            self.container,
            "sh",
            "-c",
            'exec curl --silent --show-error --fail-with-body --insecure '
            '--user "admin:$OPENSEARCH_INITIAL_ADMIN_PASSWORD" '
            '--request "$PRIVATE_ONYX_VALIDATION_METHOD" '
            '--header "Content-Type: application/json" '
            '${PRIVATE_ONYX_VALIDATION_HAS_BODY:+--data-binary @-} '
            '"https://127.0.0.1:9200$PRIVATE_ONYX_VALIDATION_PATH"',
        ]
        if body is not None:
            command[5:5] = ["-e", "PRIVATE_ONYX_VALIDATION_HAS_BODY=1"]
            input_text = body if isinstance(body, str) else json.dumps(body)
        else:
            input_text = None
        started = time.monotonic()
        completed = subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        self.latencies.append(time.monotonic() - started)
        if completed.returncode != 0:
            detail = (completed.stdout + completed.stderr).strip()
            raise ValidationError(f"{method} {path} failed: {detail[-2000:]}")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"{method} {path} returned invalid JSON") from exc

    def wait_ready(self, timeout: float = 180.0) -> None:
        deadline = time.monotonic() + timeout
        last_error = "not attempted"
        while time.monotonic() < deadline:
            try:
                health = self.request("GET", "/_cluster/health")
                if isinstance(health, dict) and health.get("status") in {"green", "yellow"}:
                    return
            except (ValidationError, subprocess.TimeoutExpired) as exc:
                last_error = str(exc)
            time.sleep(1)
        raise ValidationError(f"OpenSearch did not become ready: {last_error}")


def _only_node(payload: dict[str, Any]) -> dict[str, Any]:
    nodes = payload.get("nodes")
    if not isinstance(nodes, dict) or len(nodes) != 1:
        raise ValidationError(f"expected exactly one OpenSearch node, found {nodes!r}")
    node = next(iter(nodes.values()))
    if not isinstance(node, dict):
        raise ValidationError("OpenSearch node payload is malformed")
    return node


def _walk_ints(value: Any, key: str) -> int:
    if isinstance(value, dict):
        return sum(
            int(child) if name == key and isinstance(child, (int, float))
            else _walk_ints(child, key)
            for name, child in value.items()
        )
    if isinstance(value, list):
        return sum(_walk_ints(child, key) for child in value)
    return 0


def _vector(number: int, dimension: int = 4) -> list[float]:
    values = [float(((number + offset * 3) % 17) + 1) for offset in range(dimension)]
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values]


def _bulk_documents(index: str, start: int, count: int, dimension: int = 4) -> str:
    lines: list[str] = []
    for number in range(start, start + count):
        lines.append(json.dumps({"index": {"_index": index, "_id": f"doc-{number}"}}))
        lines.append(
            json.dumps(
                {
                    "document_id": f"document-{number}",
                    "content": f"private onyx validation document {number} alpha beta gamma",
                    "content_vector": _vector(number, dimension),
                }
            )
        )
    return "\n".join(lines) + "\n"


def _runtime_counters(client: OpenSearchClient) -> dict[str, int]:
    payload = client.request("GET", "/_nodes/stats/jvm,breaker,thread_pool")
    if not isinstance(payload, dict):
        raise ValidationError("node stats payload is malformed")
    return {
        "breaker_tripped": _walk_ints(payload.get("nodes", {}), "tripped"),
        "thread_pool_rejected": _walk_ints(payload.get("nodes", {}), "rejected"),
        "gc_collection_millis": _walk_ints(payload.get("nodes", {}), "collection_time_in_millis"),
    }


def _heap_used_bytes(client: OpenSearchClient) -> int:
    payload = client.request("GET", "/_nodes/stats/jvm")
    if not isinstance(payload, dict):
        raise ValidationError("JVM stats payload is malformed")
    node = _only_node(payload)
    value = node.get("jvm", {}).get("mem", {}).get("heap_used_in_bytes")
    if not isinstance(value, int):
        raise ValidationError("JVM heap-used metric is absent")
    return value


def _heap_usage_summary(samples: list[int], final_sample: int) -> dict[str, int]:
    return {
        "heap_used_max_sample_bytes": max(*samples, final_sample),
        "heap_used_final_bytes": final_sample,
    }


def _assert_no_failure_counter_increase(
    before: dict[str, int], after: dict[str, int]
) -> None:
    for counter in ("breaker_tripped", "thread_pool_rejected"):
        if after[counter] > before[counter]:
            raise ValidationError(
                f"{counter} increased from {before[counter]} to {after[counter]}"
            )


def _validate_static_runtime(client: OpenSearchClient, expected_version: str | None) -> dict[str, Any]:
    root = client.request("GET", "/")
    if not isinstance(root, dict):
        raise ValidationError("root response is malformed")
    version = root.get("version", {}).get("number")
    if expected_version and version != expected_version:
        raise ValidationError(f"expected OpenSearch {expected_version}, found {version!r}")

    nodes = client.request("GET", "/_nodes")
    if not isinstance(nodes, dict):
        raise ValidationError("nodes response is malformed")
    node = _only_node(nodes)
    heap = node.get("jvm", {}).get("mem", {}).get("heap_max_in_bytes")
    allocated = node.get("os", {}).get("allocated_processors")
    configured = node.get("settings", {}).get("node", {}).get("processors")
    if heap != HEAP_BYTES:
        raise ValidationError(f"expected {HEAP_BYTES}-byte heap, found {heap!r}")
    if allocated != 4 or configured != "4":
        raise ValidationError(
            f"expected four allocated/configured processors, found {allocated!r}/{configured!r}"
        )

    command_line = client.exec_text('tr "\\000" "\\n" </proc/1/cmdline').splitlines()
    required_arguments = {
        "-Xms512m",
        "-Xmx512m",
        "-Enode.processors=4",
        "-Esearch.insights.top_queries.latency.enabled=false",
        "-Esearch.insights.top_queries.cpu.enabled=false",
        "-Esearch.insights.top_queries.memory.enabled=false",
        "-Eplugins.security.audit.config.index='security-auditlog-'YYYY.MM",
    }
    missing_arguments = required_arguments - set(command_line)
    if missing_arguments:
        raise ValidationError(
            f"OpenSearch PID 1 is missing required arguments: {sorted(missing_arguments)}"
        )
    pa_agent = client.exec_text(
        'printf "%s" "$DISABLE_PERFORMANCE_ANALYZER_AGENT_CLI"'
    ).strip()
    if pa_agent != "true":
        raise ValidationError(
            f"Performance Analyzer agent disable flag is not true: {pa_agent!r}"
        )

    plugins_payload = client.request("GET", "/_cat/plugins?format=json")
    if not isinstance(plugins_payload, list):
        raise ValidationError("plugin inventory is malformed")
    plugins = {
        entry.get("component")
        for entry in plugins_payload
        if isinstance(entry, dict) and isinstance(entry.get("component"), str)
    }
    if plugins != EXPECTED_PLUGINS:
        raise ValidationError(
            "OpenSearch plugin inventory drifted: "
            f"missing={sorted(EXPECTED_PLUGINS - plugins)!r} "
            f"unexpected={sorted(plugins - EXPECTED_PLUGINS)!r}"
        )

    audit = client.request("GET", "/_plugins/_security/api/audit")
    if not isinstance(audit, dict):
        raise ValidationError("Security audit response is malformed")
    audit_settings = audit.get("config", {}).get("audit", {})
    if audit_settings.get("log_request_body") is not False:
        raise ValidationError("Security audit request-body logging is not disabled")
    if audit_settings.get("exclude_sensitive_headers") is not True:
        raise ValidationError("Security audit sensitive-header exclusion is not enabled")

    return {
        "version": version,
        "heap_max_bytes": heap,
        "allocated_processors": allocated,
        "plugin_count": len(plugins),
        "performance_analyzer_agent_disabled": True,
    }


def _index_names(client: OpenSearchClient) -> set[str]:
    payload = client.request(
        "GET", "/_cat/indices?format=json&expand_wildcards=all&h=index"
    )
    if not isinstance(payload, list):
        raise ValidationError("index inventory is malformed")
    return {
        entry["index"]
        for entry in payload
        if isinstance(entry, dict) and isinstance(entry.get("index"), str)
    }


def run_validation(
    client: OpenSearchClient,
    *,
    documents: int,
    concurrency: int,
    iterations: int,
    vector_dimension: int,
    expected_version: str | None,
    restart: bool,
) -> dict[str, Any]:
    if documents < 10 or concurrency < 1 or iterations < 1 or vector_dimension < 1:
        raise ValidationError(
            "documents, concurrency, iterations, and vector_dimension must be positive"
        )
    client.wait_ready()
    static = _validate_static_runtime(client, expected_version)
    before = _runtime_counters(client)
    heap_samples = [_heap_used_bytes(client)]
    indices_before = _index_names(client)
    suffix = uuid.uuid4().hex[:12]
    source = f"private-onyx-validation-{suffix}"
    destination = f"private-onyx-validation-reindex-{suffix}"
    pipeline = f"private-onyx-validation-{suffix}"
    index_body = {
        "settings": {
            "index": {"knn": True, "number_of_shards": 1, "number_of_replicas": 0}
        },
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "document_id": {"type": "keyword"},
                "content": {"type": "text"},
                "content_vector": {
                    "type": "knn_vector",
                    "dimension": vector_dimension,
                    "method": {
                        "name": "hnsw",
                        "space_type": "cosinesimil",
                        "engine": "lucene",
                        "parameters": {"ef_construction": 64, "m": 8},
                    },
                },
            },
        },
    }
    pipeline_body = {
        "description": "Private Onyx validation normalization",
        "phase_results_processors": [
            {
                "normalization-processor": {
                    "normalization": {"technique": "min_max"},
                    "combination": {
                        "technique": "arithmetic_mean",
                        "parameters": {"weights": [0.5, 0.5]},
                    },
                }
            }
        ],
    }
    try:
        client.request("PUT", f"/{source}", index_body)
        client.request("PUT", f"/{destination}", index_body)
        client.request("PUT", f"/_search/pipeline/{pipeline}", pipeline_body)
        bulk = client.request(
            "POST",
            "/_bulk?refresh=true",
            _bulk_documents(source, 0, documents, vector_dimension),
            timeout=180,
        )
        if not isinstance(bulk, dict) or bulk.get("errors") is not False:
            raise ValidationError("initial bulk indexing reported errors")

        count = client.request("GET", f"/{source}/_count")
        if not isinstance(count, dict) or count.get("count") != documents:
            raise ValidationError(f"expected {documents} indexed documents, found {count!r}")

        keyword_query = {"size": 10, "query": {"match": {"content": "alpha beta"}}}
        knn_query = {
            "size": 10,
            "query": {
                "knn": {
                    "content_vector": {
                        "vector": _vector(3, vector_dimension),
                        "k": 10,
                    }
                }
            },
        }
        hybrid_query = {
            "size": 10,
            "query": {
                "hybrid": {
                    "queries": [
                        {"match": {"content": "alpha beta"}},
                        {
                            "knn": {
                                "content_vector": {
                                    "vector": _vector(3, vector_dimension),
                                    "k": 10,
                                }
                            }
                        },
                    ]
                }
            },
        }
        for path, query in (
            (f"/{source}/_search", keyword_query),
            (f"/{source}/_search", knn_query),
            (f"/{source}/_search?search_pipeline={quote(pipeline)}", hybrid_query),
        ):
            result = client.request("POST", path, query)
            if not isinstance(result, dict) or not result.get("hits", {}).get("hits"):
                raise ValidationError(f"query returned no hits: {path}")
        heap_samples.append(_heap_used_bytes(client))

        def mixed_operation(number: int) -> None:
            if number % 4 == 0:
                payload = _bulk_documents(
                    source, documents + number * 5, 5, vector_dimension
                )
                result = client.request("POST", "/_bulk", payload)
                if not isinstance(result, dict) or result.get("errors") is not False:
                    raise ValidationError("concurrent bulk indexing reported errors")
            else:
                query = hybrid_query if number % 2 else knn_query
                path = f"/{source}/_search"
                if query is hybrid_query:
                    path += f"?search_pipeline={quote(pipeline)}"
                result = client.request("POST", path, query)
                if not isinstance(result, dict) or not result.get("hits", {}).get("hits"):
                    raise ValidationError("concurrent search returned no hits")

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            list(executor.map(mixed_operation, range(iterations)))
        client.request("POST", f"/{source}/_refresh")
        heap_samples.append(_heap_used_bytes(client))

        client.request(
            "POST",
            "/_reindex?refresh=true&wait_for_completion=true",
            {"source": {"index": source}, "dest": {"index": destination}},
            timeout=180,
        )
        source_count = client.request("GET", f"/{source}/_count")
        destination_count = client.request("GET", f"/{destination}/_count")
        if source_count.get("count") != destination_count.get("count"):
            raise ValidationError("reindexed document count does not match source")
        heap_samples.append(_heap_used_bytes(client))

        client.request("DELETE", f"/{source}/_doc/doc-0?refresh=true")
        deleted = client.request(
            "POST",
            f"/{source}/_count",
            {"query": {"term": {"document_id": "document-0"}}},
        )
        if deleted.get("count") != 0:
            raise ValidationError("deleted validation document remains searchable")

        pre_restart = _runtime_counters(client)
        _assert_no_failure_counter_increase(before, pre_restart)
        if restart:
            subprocess.run(
                [client.container_bin, "restart", client.container],
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
            client.wait_ready(timeout=240)
            post_restart = _runtime_counters(client)
            recovered = client.request("GET", f"/{destination}/_count")
            if recovered.get("count") != destination_count.get("count"):
                raise ValidationError("reindexed document count changed after restart")
            heap_samples.append(_heap_used_bytes(client))
        else:
            post_restart = before

        health = client.request("GET", "/_cluster/health?wait_for_status=green&timeout=60s")
        if not isinstance(health, dict) or health.get("status") != "green":
            raise ValidationError(f"cluster did not return green: {health!r}")
        after = _runtime_counters(client)
        _assert_no_failure_counter_increase(post_restart, after)
        indices_after = _index_names(client)
        new_query_insight_indices = {
            name
            for name in indices_after - indices_before
            if name.startswith("top_queries-")
        }
        if new_query_insight_indices:
            raise ValidationError(
                f"Query Insights created unexpected indices: {sorted(new_query_insight_indices)}"
            )
        expected_audit_index = f"security-auditlog-{datetime.now(UTC):%Y.%m}"
        if expected_audit_index not in indices_after:
            raise ValidationError(
                f"expected monthly audit index {expected_audit_index!r} is absent"
            )
        if restart:
            counter_delta = {
                key: (pre_restart[key] - before[key]) + (after[key] - post_restart[key])
                for key in before
            }
        else:
            counter_delta = {key: after[key] - before[key] for key in before}
        ordered = sorted(client.latencies)
        p95_index = min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)
        heap_summary = _heap_usage_summary(heap_samples, _heap_used_bytes(client))
        return {
            **static,
            "documents_requested": documents,
            "documents_reindexed": destination_count.get("count"),
            "concurrency": concurrency,
            "mixed_iterations": iterations,
            "vector_dimension": vector_dimension,
            "restart_validated": restart,
            "breaker_tripped_delta": counter_delta["breaker_tripped"],
            "thread_pool_rejected_delta": counter_delta["thread_pool_rejected"],
            "gc_collection_millis_delta": counter_delta["gc_collection_millis"],
            **heap_summary,
            "monthly_audit_index": expected_audit_index,
            "new_query_insight_indices": 0,
            "request_latency_median_seconds": round(statistics.median(ordered), 4),
            "request_latency_p95_seconds": round(ordered[p95_index], 4),
        }
    finally:
        for method, path in (
            ("DELETE", f"/_search/pipeline/{pipeline}"),
            ("DELETE", f"/{source}"),
            ("DELETE", f"/{destination}"),
        ):
            try:
                client.request(method, path)
            except (ValidationError, subprocess.TimeoutExpired):
                pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container-bin", default="docker")
    parser.add_argument("--container", required=True)
    parser.add_argument("--documents", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--iterations", type=int, default=24)
    parser.add_argument("--vector-dimension", type=int, default=128)
    parser.add_argument("--expected-version")
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()
    client = OpenSearchClient(args.container_bin, args.container)
    result = run_validation(
        client,
        documents=args.documents,
        concurrency=args.concurrency,
        iterations=args.iterations,
        vector_dimension=args.vector_dimension,
        expected_version=args.expected_version,
        restart=args.restart,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
