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
from types import CodeType, SimpleNamespace


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _validate_scheduler_tick(beat) -> None:
    # Startup has already run, so another reference to the installed method is
    # not an independent baseline. Compile the image's upstream source without
    # executing it and compare the actual callable, without inspect.unwrap.
    source_path = Path(beat.__file__)
    upstream = compile(source_path.read_bytes(), str(source_path), "exec", dont_inherit=True)
    (scheduler_code,) = (
        code for code in upstream.co_consts
        if isinstance(code, CodeType) and code.co_name == "DynamicTenantScheduler"
    )
    (tick_code,) = (
        code for code in scheduler_code.co_consts
        if isinstance(code, CodeType) and code.co_name == "tick"
    )
    installed = beat.DynamicTenantScheduler.tick
    assert installed.__code__ == tick_code, "DynamicTenantScheduler.tick was replaced"
    assert installed.__globals__ is vars(beat), "scheduler tick has foreign globals"


def _validate_schedules(background_patch) -> None:
    from onyx.background.celery.apps import app_base
    from onyx.background.celery.apps import beat
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
    _validate_scheduler_tick(beat)
    assert app_base.get_bootsteps() == []
    assert not any(
        name.startswith("onyx.background.celery.apps.monitoring")
        or name.startswith("onyx.background.celery.tasks.monitoring")
        for name in sys.modules
    )
    assert background_patch._INDEXING_SKIP_PATCHED is (os.environ.get("ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_ENABLED") == "true")


def _validate_scheduler_reload() -> None:
    """Use native entries and reload; never open the user's scheduler store."""
    from copy import deepcopy
    from unittest.mock import patch
    from celery import Celery
    from onyx.background.celery.apps import beat
    from onyx.background.celery.tasks import beat_schedule
    from onyx.configs.constants import OnyxCeleryTask, OnyxCeleryQueues
    from onyx_wrapper_patches.background.resource_policy import validate_materialized_schedule

    templates = deepcopy(beat_schedule.beat_task_templates)
    template_by_name = {task["name"]: task for task in templates}
    assert template_by_name["check-for-indexing"]["schedule"] == timedelta(seconds=15)
    assert "cleanup-idle-sandboxes" in template_by_name
    validate_materialized_schedule(
        beat_schedule.get_tasks_to_schedule(), OnyxCeleryTask, OnyxCeleryQueues.MONITORING
    )
    scheduler = object.__new__(beat.DynamicTenantScheduler)
    scheduler.app = Celery("patch-schedule-fixture", broker="memory://")
    scheduler._store = {"entries": {}}
    scheduler.last_beat_multiplier = beat.CLOUD_BEAT_MULTIPLIER_DEFAULT
    scheduler.sync = lambda: None
    expected = scheduler._generate_schedule(["public"], scheduler.last_beat_multiplier)

    def assert_installed():
        assert set(scheduler.schedule) == set(expected)
        for name, generated in expected.items():
            installed = scheduler.schedule[name]
            assert installed.task == generated["task"], name
            assert installed.schedule.run_every == generated["schedule"], name
            assert installed.options == generated.get("options", {}), name
            assert installed.kwargs == generated["kwargs"], name

    with patch.object(beat, "get_all_tenant_ids", return_value=["public"]), patch.object(
        beat.OnyxRuntime, "get_beat_multiplier", return_value=scheduler.last_beat_multiplier
    ):
        scheduler._try_updating_schedule()
        assert_installed()
        scheduler._try_updating_schedule()
        assert_installed()
        assert beat_schedule.beat_task_templates == templates
        # Native comparison checks names, not the contents of same-name entries.
        name = next(iter(expected))
        for field, stale in (("task", "stale-task"), ("schedule", timedelta(seconds=1)), ("options", {"queue": "monitoring"})):
            entry = deepcopy(expected[name])
            entry[field] = stale
            scheduler.schedule[name] = scheduler.Entry(name=name, app=scheduler.app, **entry)
            scheduler._try_updating_schedule()
            try:
                assert_installed()
            except AssertionError:
                pass
            else:
                raise AssertionError(f"stale same-name {field} escaped installed-state assertion")
            # A changed-name controlled fixture forces native replacement.
            scheduler.schedule["obsolete-fixture"] = scheduler.schedule[name]
            scheduler._try_updating_schedule()
            assert_installed()
    print("PINNED_MATERIALIZED_SCHEDULE_RELOAD_AND_STALE_DETECTION_OK")


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


def _validate_native_connector_entry(background_patch) -> None:
    """Account for connectivity bodies before the real patched scrape boundary."""
    import io
    import requests
    from unittest.mock import patch
    from onyx.connectors.web import connector

    enabled = background_patch._INDEXING_SKIP_PATCHED
    url = "http://doc-drop-web:8091/onyx-reorg-controlled/fixture.pdf"
    body = b"controlled synthetic PDF bytes"
    modified = "Sat, 29 Aug 2026 12:00:00 GMT"
    record = SimpleNamespace(
        id=url, doc_updated_at=datetime(2026, 8, 29, 12, tzinfo=timezone.utc),
        content_hash="synthetic-hash", chunk_count=1,
        doc_metadata=background_patch._freshness_metadata(
            {}, last_modified_raw=modified, content_length=str(len(body))
        ),
    )
    events = []

    def send(adapter, request, **kwargs):
        del adapter
        assert request.url == url
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.request = request
        response.headers.update({"content-type": "application/pdf", "last-modified": modified, "content-length": str(len(body))})
        response.raw = io.BytesIO(body if request.method == "GET" else b"")
        assert not kwargs.get("stream"), "native connectivity/scrape requests changed streaming behavior"
        events.append(request.method)
        return response

    def browser():
        events.append("browser")
        return SimpleNamespace(stop=lambda: None), SimpleNamespace(close=lambda: None, add_cookies=lambda cookies: None)

    def parse(data):
        assert data == body
        events.append("parse")
        return "synthetic parsed content", {}

    with patch.object(requests.adapters.HTTPAdapter, "send", send), patch.object(
        connector, "start_playwright", browser
    ), patch.object(connector, "extract_pdf_text", parse), patch.object(
        background_patch, "_get_db_document", return_value=record
    ):
        instance = connector.WebConnector(url, web_connector_type="single")
        instance.validate_connector_settings()
        assert events == ["GET"], events
        documents = [doc for batch in instance.load_from_state() for doc in batch]
    assert len(documents) == 1
    assert documents[0].id == url
    if enabled:
        assert events == ["GET", "GET", "browser", "HEAD"], events
        assert documents[0].doc_metadata[background_patch.FRESHNESS_UNCHANGED_KEY]
        assert not documents[0].sections
    else:
        assert events == ["GET", "GET", "browser", "HEAD", "GET", "parse"], events
    print(f"PINNED_NATIVE_CONNECTOR_TRAFFIC_OK freshness={enabled} connectivity_GETs=2 scrape_GETs={0 if enabled else 1}")


def main() -> None:
    from patch_activation_probe import assert_activation
    assert_activation()
    from onyx_wrapper_patches.background import document_freshness as background_patch
    _validate_schedules(background_patch)
    _validate_scheduler_reload()
    _validate_native_connector_entry(background_patch)
    _validate_supervisor()
    from validate_native_bot_tools import validate_native_tools, validate_bot_requests
    validate_native_tools(background=True)
    validate_bot_requests()

    os.environ["WRAPPER_PATCH_STRICT"] = "true"
    os.environ["ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_ENABLED"] = "true"
    if not background_patch._INDEXING_SKIP_PATCHED:
        background_patch._apply_web_connector_http_freshness_patch()
    assert background_patch._INDEXING_SKIP_PATCHED
    _validate_freshness_and_native_hash_gates(background_patch)
    assert Path("/wrapper-beat-liveness-watchdog.py").is_file()
    print("PINNED_BACKGROUND_CONTRACTS_OK")


if __name__ == "__main__":
    main()
