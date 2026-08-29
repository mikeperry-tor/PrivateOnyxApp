#!/usr/bin/env python3
"""Validate background wrapper contracts inside the pinned Onyx image."""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _validate_schedules(background_patch, original_tick) -> None:
    from onyx.background.celery.apps import app_base
    from onyx.background.celery.apps.beat import DynamicTenantScheduler
    from onyx.background.celery.tasks import beat_schedule

    effective = {task["name"]: task for task in beat_schedule.get_tasks_to_schedule()}
    discovery = {
        "check-for-user-file-processing",
        "check-for-user-file-project-sync",
        "check-for-user-file-delete",
        "check-for-indexing",
        "check-for-port",
        "check-for-connector-deletion",
        "check-for-vespa-sync",
        "check-for-pruning",
    }
    for name in discovery:
        assert effective[name]["schedule"] == timedelta(minutes=5), name

    removed = {
        "monitor-celery-queues",
        "monitor-background-processes",
        "monitor-process-memory",
        "celery-beat-heartbeat",
        "cleanup-idle-sandboxes",
        "dispatch-due-scheduled-tasks",
        "cleanup-stuck-scheduled-runs",
        "emit-version-telemetry",
    }
    assert not removed.intersection(effective)
    assert effective["check-for-incognito-file-cleanup"]["schedule"] == timedelta(
        minutes=10
    )
    assert DynamicTenantScheduler.RELOAD_INTERVAL == 300
    assert DynamicTenantScheduler.tick is original_tick
    assert app_base.get_bootsteps() == []
    assert not any(
        name.startswith("onyx.background.celery.apps.monitoring")
        or name.startswith("onyx.background.celery.tasks.monitoring")
        for name in sys.modules
    )
    assert background_patch._INDEXING_SKIP_PATCHED is False


def _validate_supervisor() -> None:
    entrypoint = _load(
        "background_entrypoint_validation", "/wrapper-background-entrypoint.py"
    )
    config = entrypoint.derive_config()
    workers = [
        section
        for section in config.sections()
        if section.startswith("program:celery_worker_")
    ]
    assert len(workers) == 6
    assert all(
        config.get(section, "command").count("--without-heartbeat") == 1
        and config.get(section, "command").count("--without-gossip") == 1
        for section in workers
    )
    for removed in (
        "program:celery_worker_monitoring",
        "program:celery_worker_scheduled_tasks",
        "program:slack_bot",
        "program:discord_bot",
    ):
        assert not config.has_section(removed), removed


def _validate_freshness_and_native_hash_gates(background_patch) -> None:
    from onyx.configs.constants import DocumentSource
    from onyx.connectors.models import Document
    from onyx.connectors.models import TextSection
    from onyx.indexing import indexing_pipeline

    document_id = "http://doc-drop-web:8091/audit.pdf"
    parsed = Document(
        id=document_id,
        sections=[TextSection(link=document_id, text="stable parsed content")],
        source=DocumentSource.WEB,
        semantic_identifier="audit.pdf",
        metadata={},
    )
    parsed_hash = parsed.content_hash()
    db_doc = SimpleNamespace(
        id=document_id,
        doc_updated_at=None,
        doc_metadata={},
        content_hash=parsed_hash,
    )

    # Onyx's native hash gate runs only after parsing and avoids all later
    # chunk/embed/index work for an unchanged ordinary document.
    docs, hashes = indexing_pipeline.get_docs_to_update([parsed], [db_doc])
    assert docs == []
    assert hashes == {}

    # Secondary-index writes intentionally bypass the PRESENT index's hash.
    docs, hashes = indexing_pipeline.get_docs_to_update(
        [parsed], [db_doc], ignore_content_hash_gate=True
    )
    assert docs == [parsed]
    assert hashes == {document_id: parsed_hash}

    last_modified = "Sat, 29 Aug 2026 12:00:00 GMT"
    updated_at = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    db_doc.doc_updated_at = updated_at
    db_doc.doc_metadata = background_patch._freshness_metadata(
        {}, last_modified_raw=last_modified, content_length="123"
    )
    sentinel = Document(
        id=document_id,
        sections=[],
        source=DocumentSource.WEB,
        semantic_identifier="audit.pdf",
        metadata={},
        doc_metadata=background_patch._unchanged_freshness_metadata(
            last_modified_raw=last_modified,
            content_length="123",
        ),
        doc_updated_at=updated_at,
    )

    # A trusted pre-parse sentinel remains a skip even when a targeted or
    # secondary operation bypasses both native gates.
    docs, hashes = indexing_pipeline.get_docs_to_update(
        [sentinel],
        [db_doc],
        ignore_timestamp_gate=True,
        ignore_content_hash_gate=True,
    )
    assert docs == []
    assert hashes == {}

    db_doc.doc_metadata = {}
    try:
        indexing_pipeline.get_docs_to_update([sentinel], [db_doc])
    except RuntimeError as exc:
        assert "refusing to index its empty placeholder" in str(exc)
    else:
        raise AssertionError("stale PDF freshness sentinel reached indexing")


def main() -> None:
    from onyx.background.celery.apps.beat import DynamicTenantScheduler

    original_tick = DynamicTenantScheduler.tick
    background_patch = _load(
        "background_patch_validation", "/background/sitecustomize.py"
    )
    _validate_schedules(background_patch, original_tick)
    _validate_supervisor()

    os.environ["WRAPPER_PATCH_STRICT"] = "true"
    os.environ["ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_ENABLED"] = "true"
    background_patch._apply_web_connector_http_freshness_patch()
    assert background_patch._INDEXING_SKIP_PATCHED
    _validate_freshness_and_native_hash_gates(background_patch)
    assert Path("/wrapper-beat-liveness-watchdog.py").is_file()
    print("PINNED_BACKGROUND_CONTRACTS_OK")


if __name__ == "__main__":
    main()
