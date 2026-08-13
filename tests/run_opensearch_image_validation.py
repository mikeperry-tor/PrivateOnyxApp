#!/usr/bin/env python3
"""Validate the pinned OpenSearch image using an isolated disposable container."""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from opensearch_runtime_validation import OpenSearchClient, run_validation


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
        timeout=240,
    )


def _validate_failed_auth_audit(
    client: OpenSearchClient, container_bin: str, container: str
) -> None:
    marker = f"private-onyx-audit-body-{uuid.uuid4().hex}"
    audit_index = f"security-auditlog-{datetime.now(UTC):%Y.%m}"
    client.request("POST", f"/{audit_index}/_refresh")
    baseline_payload = client.request(
        "POST",
        f"/{audit_index}/_search",
        {
            "size": 0,
            "track_total_hits": True,
            "query": {"term": {"audit_category.keyword": "FAILED_LOGIN"}},
        },
    )
    if not isinstance(baseline_payload, dict):
        raise RuntimeError("clean Security audit baseline response is malformed")
    baseline_count = baseline_payload.get("hits", {}).get("total", {}).get("value")
    if not isinstance(baseline_count, int):
        raise RuntimeError("clean Security audit baseline count is absent")

    probe = subprocess.run(
        [
            container_bin,
            "exec",
            "-i",
            container,
            "sh",
            "-c",
            'curl --silent --insecure --user "admin:deliberately-wrong" '
            '--request POST --header "Content-Type: application/json" '
            '--data-binary @- --output /dev/null --write-out "%{http_code}" '
            'https://127.0.0.1:9200/_search',
        ],
        input=json.dumps({"query": {"match": {"audit_probe": marker}}}),
        text=True,
        capture_output=True,
        timeout=30,
    )
    if probe.returncode != 0 or probe.stdout != "401":
        raise RuntimeError(
            "failed-auth audit probe did not receive HTTP 401: "
            f"status={probe.returncode} response={probe.stdout!r}"
        )

    deadline = time.monotonic() + 60
    last_payload: dict[str, object] | list[object] = {}
    while time.monotonic() < deadline:
        client.request("POST", f"/{audit_index}/_refresh")
        payload = client.request(
            "POST",
            f"/{audit_index}/_search",
            {
                "size": 100,
                "track_total_hits": True,
                "sort": [{"@timestamp": {"order": "desc"}}],
                "query": {"term": {"audit_category.keyword": "FAILED_LOGIN"}},
            },
        )
        last_payload = payload
        if isinstance(payload, dict):
            total = payload.get("hits", {}).get("total", {}).get("value")
            if isinstance(total, int) and total > baseline_count:
                break
        time.sleep(0.5)
    else:
        raise RuntimeError("clean Security audit index did not record failed login")
    if marker in json.dumps(last_payload):
        raise RuntimeError("Security audit record retained the failed-auth request body")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container-bin", default="docker")
    parser.add_argument("--image", required=True)
    parser.add_argument("--audit-config", type=Path, required=True)
    parser.add_argument("--documents", type=int, default=300)
    parser.add_argument("--vector-dimension", type=int, default=128)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--iterations", type=int, default=24)
    parser.add_argument("--expected-version")
    parser.add_argument("--memlock", default="-1:-1")
    args = parser.parse_args()

    audit_config = args.audit_config.resolve(strict=True)
    name = f"private-onyx-opensearch-validation-{uuid.uuid4().hex[:10]}"
    volume = f"{name}-data"
    password = f"V-{secrets.token_urlsafe(32)}-9aA!"
    command = [
        args.container_bin,
        "run",
        "--detach",
        "--name",
        name,
        "--network",
        "none",
        "--volume",
        f"{volume}:/usr/share/opensearch/data",
        "--ulimit",
        f"memlock={args.memlock}",
        "--env",
        "discovery.type=single-node",
        "--env",
        f"OPENSEARCH_INITIAL_ADMIN_PASSWORD={password}",
        "--env",
        "OPENSEARCH_JAVA_OPTS=-Xms512m -Xmx512m",
        "--env",
        "node.processors=4",
        "--env",
        "DISABLE_PERFORMANCE_ANALYZER_AGENT_CLI=true",
        "--env",
        "plugins.security.audit.config.index='security-auditlog-'YYYY.MM",
        "--env",
        "search.insights.top_queries.latency.enabled=false",
        "--env",
        "search.insights.top_queries.cpu.enabled=false",
        "--env",
        "search.insights.top_queries.memory.enabled=false",
        "--volume",
        f"{audit_config}:/usr/share/opensearch/config/opensearch-security/audit.yml:ro",
        args.image,
    ]
    started = False
    volume_created = False
    try:
        _run([args.container_bin, "volume", "create", volume])
        volume_created = True
        _run(command)
        started = True
        client = OpenSearchClient(args.container_bin, name)
        result = run_validation(
            client,
            documents=args.documents,
            concurrency=args.concurrency,
            iterations=args.iterations,
            vector_dimension=args.vector_dimension,
            expected_version=args.expected_version,
            restart=True,
        )
        _validate_failed_auth_audit(client, args.container_bin, name)
        result["clean_disposable_volume"] = True
        result["failed_auth_audit_body_redacted"] = True
        result["image"] = args.image
        print(json.dumps(result, indent=2, sort_keys=True))
    except BaseException:
        if started:
            logs = _run(
                [args.container_bin, "logs", "--tail", "120", name],
                check=False,
            )
            print("Recent disposable OpenSearch logs:", file=sys.stderr)
            print((logs.stdout + logs.stderr)[-12000:], file=sys.stderr)
        raise
    finally:
        if started:
            _run([args.container_bin, "rm", "--force", name], check=False)
        if volume_created:
            _run([args.container_bin, "volume", "rm", "--force", volume], check=False)


if __name__ == "__main__":
    main()
