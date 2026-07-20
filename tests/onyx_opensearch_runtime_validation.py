#!/usr/bin/env python3
"""Exercise the pinned Onyx OpenSearch schema/client inside the API container."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path


RESULT_PREFIX = "PRIVATE_ONYX_OPENSEARCH_RESULT="


def _inside_container() -> None:
    from onyx.configs.constants import DocumentSource
    from onyx.context.search.models import IndexFilters
    from onyx.document_index.interfaces_new import TenantState
    from onyx.document_index.opensearch.client import OpenSearchIndexClient
    from onyx.document_index.opensearch.schema import DocumentChunk, DocumentSchema
    from onyx.document_index.opensearch.search import DocumentQuery
    from onyx.document_index.opensearch.search import (
        get_normalization_pipeline_name_and_config,
    )

    suffix = uuid.uuid4().hex[:12]
    source_name = f"private-onyx-client-validation-{suffix}"
    destination_name = f"private-onyx-client-validation-reindex-{suffix}"
    pipeline_name = f"private-onyx-client-validation-{suffix}"
    tenant_state = TenantState(tenant_id="", multitenant=False)
    source = OpenSearchIndexClient(index_name=source_name)
    destination = OpenSearchIndexClient(index_name=destination_name)
    created_source = False
    created_destination = False
    created_pipeline = False
    dimension = 4
    try:
        mappings = DocumentSchema.get_document_schema(
            vector_dimension=dimension, multitenant=False
        )
        settings = DocumentSchema.get_index_settings_based_on_environment()
        replicas = settings.get("index", {}).get("number_of_replicas")
        if replicas != 0:
            raise RuntimeError(f"Onyx generated {replicas!r} replicas instead of zero")
        source.create_index(mappings=mappings, settings=settings)
        created_source = True
        destination.create_index(mappings=mappings, settings=settings)
        created_destination = True
        _, pipeline_config = get_normalization_pipeline_name_and_config()
        source.create_search_pipeline(
            pipeline_id=pipeline_name, pipeline_body=pipeline_config
        )
        created_pipeline = True

        for number in range(20):
            base = float((number % 5) + 1)
            vector = [base, 2.0, 3.0, 4.0]
            source.index_document(
                document=DocumentChunk(
                    document_id=f"private-onyx-validation-document-{number}",
                    chunk_index=0,
                    title=f"Validation title {number}",
                    title_vector=vector,
                    content=f"Private Onyx schema validation alpha beta {number}",
                    content_vector=vector,
                    source_type=DocumentSource.FILE.value,
                    public=True,
                    access_control_list=[],
                    hidden=False,
                    global_boost=0,
                    semantic_identifier=f"validation-{number}",
                    blurb="validation blurb",
                    doc_summary="validation summary",
                    chunk_context="validation context",
                    tenant_id=tenant_state,
                ),
                tenant_state=tenant_state,
            )
        source.refresh_index()
        query = DocumentQuery.get_hybrid_search_query(
            query_text="alpha beta",
            query_vector=[1.0, 2.0, 3.0, 4.0],
            num_hits=10,
            tenant_state=tenant_state,
            index_filters=IndexFilters(access_control_list=None),
            include_hidden=False,
        )
        results = source.search(body=query, search_pipeline_id=pipeline_name)
        if not results:
            raise RuntimeError("Onyx hybrid query returned no validation documents")

        response = source._client.reindex(
            body={
                "source": {"index": source_name},
                "dest": {"index": destination_name},
            },
            refresh=True,
            wait_for_completion=True,
            request_timeout=120,
        )
        if response.get("failures"):
            raise RuntimeError(f"Onyx validation reindex failed: {response['failures']!r}")
        destination.refresh_index()
        destination_count = destination._client.count(index=destination_name).get("count")
        if destination_count != 20:
            raise RuntimeError(
                f"Onyx validation reindex expected 20 documents, found {destination_count!r}"
            )
        print(
            RESULT_PREFIX
            + json.dumps(
                {
                    "hybrid_hits": len(results),
                    "indexed_documents": 20,
                    "reindexed_documents": destination_count,
                    "replicas": replicas,
                    "vector_dimension": dimension,
                },
                sort_keys=True,
            )
        )
    finally:
        if created_pipeline:
            try:
                source.delete_search_pipeline(pipeline_id=pipeline_name)
            except Exception:
                pass
        if created_source:
            try:
                source.delete_index()
            except Exception:
                pass
        if created_destination:
            try:
                destination.delete_index()
            except Exception:
                pass
        source.close()
        destination.close()


def _host(container_bin: str, api_container: str) -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    completed = subprocess.run(
        [container_bin, "exec", "-i", api_container, "python", "-", "--inside-container"],
        input=source,
        text=True,
        capture_output=True,
        timeout=240,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stdout + completed.stderr)[-12000:])
    result_line = next(
        (line for line in completed.stdout.splitlines() if line.startswith(RESULT_PREFIX)),
        None,
    )
    if result_line is None:
        raise RuntimeError("Onyx API-container validation returned no structured result")
    result = json.loads(result_line.removeprefix(RESULT_PREFIX))
    print(json.dumps(result, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container-bin", default="docker")
    parser.add_argument("--api-container", default="onyx-api_server-1")
    parser.add_argument("--inside-container", action="store_true")
    args = parser.parse_args()
    if args.inside_container:
        _inside_container()
    else:
        _host(args.container_bin, args.api_container)


if __name__ == "__main__":
    main()
