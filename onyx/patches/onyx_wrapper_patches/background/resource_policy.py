"""Background-owned resource policy implementation."""

from __future__ import annotations

import os
from datetime import timedelta

from onyx_wrapper_patches.background.config import _strict_mode


_DISCOVERY_TASKS = {
    "check-for-user-file-processing": "CHECK_FOR_USER_FILE_PROCESSING",
    "check-for-user-file-project-sync": "CHECK_FOR_USER_FILE_PROJECT_SYNC",
    "check-for-user-file-delete": "CHECK_FOR_USER_FILE_DELETE",
    "check-for-indexing": "CHECK_FOR_INDEXING",
    "check-for-port": "CHECK_FOR_PORT",
    "check-for-connector-deletion": "CHECK_FOR_CONNECTOR_DELETION",
    "check-for-vespa-sync": "CHECK_FOR_VESPA_SYNC_TASK",
    "check-for-pruning": "CHECK_FOR_PRUNING",
}
_HOUSEKEEPING_TASKS = {
    "check-for-old-index-reclaim": ("CHECK_FOR_OLD_INDEX_RECLAIM", timedelta(minutes=30)),
    "check-for-stale-capability-runs": ("CHECK_FOR_STALE_CAPABILITY_RUNS", timedelta(minutes=10)),
    "check-for-incognito-file-cleanup": ("CHECK_FOR_INCOGNITO_FILE_CLEANUP", timedelta(minutes=10)),
    "check-for-checkpoint-cleanup": ("CHECK_FOR_CHECKPOINT_CLEANUP", timedelta(hours=1)),
    "check-for-index-attempt-cleanup": ("CHECK_FOR_INDEX_ATTEMPT_CLEANUP", timedelta(minutes=30)),
    "check-for-hierarchy-fetching": ("CHECK_FOR_HIERARCHY_FETCHING", timedelta(hours=1)),
}


def validate_materialized_schedule(tasks, task_ids, monitoring_queue) -> None:
    """Reject duplicate names before mapping and validate each retained producer."""
    expected = {
        name: (getattr(task_ids, identifier), timedelta(minutes=5))
        for name, identifier in _DISCOVERY_TASKS.items()
    }
    expected.update({
        name: (getattr(task_ids, identifier), cadence)
        for name, (identifier, cadence) in _HOUSEKEEPING_TASKS.items()
    })
    names = [task.get("name") for task in tasks]
    if len(names) != len(set(names)):
        raise RuntimeError("duplicate materialized background schedule names")
    if set(names) != set(expected):
        raise RuntimeError("unclassified self-hosted background schedules")
    for task in tasks:
        name = task["name"]
        if (task.get("task"), task.get("schedule")) != expected[name]:
            raise RuntimeError(f"unexpected {name} task identifier or cadence")
        if task.get("options", {}).get("queue") == monitoring_queue:
            raise RuntimeError("a self-hosted task still targets the monitoring queue")


def _apply_sleepy_background_patch() -> None:
    """Strictly reduce idle-only background scheduling and liveness work."""
    try:
        from onyx.background.celery.apps import app_base
        from onyx.background.celery.apps import beat as beat_app
        from onyx.background.celery.tasks import beat_schedule
        from onyx.configs.app_configs import DISABLE_TELEMETRY
        from onyx.configs.constants import OnyxCeleryQueues
        from onyx.configs.constants import OnyxCeleryTask
        from shared_configs.configs import MULTI_TENANT

        if MULTI_TENANT:
            raise RuntimeError("sleepy background policy requires MULTI_TENANT=false")
        if not DISABLE_TELEMETRY:
            raise RuntimeError(
                "sleepy background policy requires DISABLE_TELEMETRY=true"
            )

        discovery = {
            "check-for-user-file-processing": timedelta(seconds=20),
            "check-for-user-file-project-sync": timedelta(seconds=20),
            "check-for-user-file-delete": timedelta(seconds=20),
            "check-for-indexing": timedelta(seconds=15),
            "check-for-port": timedelta(seconds=30),
            "check-for-connector-deletion": timedelta(seconds=20),
            "check-for-vespa-sync": timedelta(seconds=20),
            "check-for-pruning": timedelta(seconds=20),
        }
        for name, old_schedule in discovery.items():
            matches = [
                task for task in beat_schedule.tasks_to_schedule
                if task.get("name") == name
            ]
            if len(matches) != 1 or matches[0].get("schedule") != old_schedule:
                raise RuntimeError(
                    f"expected one {name} schedule at {old_schedule}, found {matches!r}"
                )
            matches[0]["schedule"] = timedelta(minutes=5)

        removals = {
            "monitor-celery-queues": (
                OnyxCeleryTask.MONITOR_CELERY_QUEUES,
                timedelta(seconds=10),
            ),
            "monitor-background-processes": (
                OnyxCeleryTask.MONITOR_BACKGROUND_PROCESSES,
                timedelta(minutes=5),
            ),
            "monitor-process-memory": (
                OnyxCeleryTask.MONITOR_PROCESS_MEMORY,
                timedelta(minutes=5),
            ),
            "celery-beat-heartbeat": (
                OnyxCeleryTask.CELERY_BEAT_HEARTBEAT,
                timedelta(minutes=1),
            ),
            "emit-version-telemetry": (
                OnyxCeleryTask.EMIT_VERSION_TELEMETRY,
                timedelta(hours=1),
            ),
        }
        craft_enabled = os.environ.get("ENABLE_CRAFT", "false").lower() in {
            "1", "true", "yes", "on"
        }
        if not craft_enabled:
            removals.update(
                {
                    "cleanup-idle-sandboxes": (
                        OnyxCeleryTask.CLEANUP_IDLE_SANDBOXES,
                        timedelta(minutes=1),
                    ),
                    "dispatch-due-scheduled-tasks": (
                        OnyxCeleryTask.SCHEDULED_TASKS_DISPATCH_DUE,
                        timedelta(seconds=30),
                    ),
                    "cleanup-stuck-scheduled-runs": (
                        OnyxCeleryTask.SCHEDULED_TASKS_CLEANUP_STUCK,
                        timedelta(hours=1),
                    ),
                }
            )

        for name, (task_id, cadence) in removals.items():
            matches = [
                task for task in beat_schedule.tasks_to_schedule
                if task.get("name") == name
            ]
            if (
                len(matches) != 1
                or matches[0].get("task") != task_id
                or matches[0].get("schedule") != cadence
            ):
                raise RuntimeError(f"unexpected materialized {name} schedule contract")
            beat_schedule.tasks_to_schedule[:] = [
                task for task in beat_schedule.tasks_to_schedule
                if task.get("name") != name
            ]
        if beat_schedule.get_tasks_to_schedule() is not beat_schedule.tasks_to_schedule:
            raise RuntimeError("get_tasks_to_schedule no longer returns the materialized list")

        validate_materialized_schedule(
            beat_schedule.tasks_to_schedule, OnyxCeleryTask, OnyxCeleryQueues.MONITORING
        )

        if beat_app.DynamicTenantScheduler.RELOAD_INTERVAL != 60:
            raise RuntimeError("unexpected Beat scheduler reload interval")
        beat_app.DynamicTenantScheduler.RELOAD_INTERVAL = 300

        bootsteps = app_base.get_bootsteps()
        if bootsteps != [app_base.LivenessProbe]:
            raise RuntimeError(f"unexpected worker bootsteps: {bootsteps!r}")
        app_base.get_bootsteps = lambda: []

        print(
            "sitecustomize_background: installed strict sleepy background policy",
            flush=True,
        )
    except Exception as e:  # pragma: no cover
        print(
            "sitecustomize_background: failed to patch sleepy background policy: "
            f"{e}",
            flush=True,
        )
        if _strict_mode():
            raise
