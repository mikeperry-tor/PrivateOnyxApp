#!/usr/bin/env python3
"""Validate background wrapper contracts inside the pinned Onyx image."""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import timedelta
from pathlib import Path


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
    }
    assert not removed.intersection(effective)
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
    assert Path("/wrapper-beat-liveness-watchdog.py").is_file()
    print("PINNED_BACKGROUND_CONTRACTS_OK")


if __name__ == "__main__":
    main()
