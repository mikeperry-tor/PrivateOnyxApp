#!/usr/bin/env python3
"""Build a strictly validated low-idle supervisor configuration."""

from __future__ import annotations

import configparser
import os
import subprocess
import sys
from pathlib import Path


SOURCE_CONFIG = Path("/etc/supervisor/conf.d/supervisord.conf")
DERIVED_CONFIG = Path("/tmp/private-onyx-supervisord.conf")
WORKERS = (
    "celery_worker_primary",
    "celery_worker_light",
    "celery_worker_heavy",
    "celery_worker_docprocessing",
    "celery_worker_user_file_processing",
    "celery_worker_scheduled_tasks",
    "celery_worker_docfetching",
    "celery_worker_monitoring",
)
RETAINED_WORKERS = tuple(
    worker for worker in WORKERS
    if worker not in {"celery_worker_scheduled_tasks", "celery_worker_monitoring"}
)
WORKER_QUEUES = {
    "celery_worker_primary": "celery",
    "celery_worker_light": (
        "vespa_metadata_sync,connector_deletion,doc_permissions_upsert,"
        "checkpoint_cleanup,index_attempt_cleanup,opensearch_migration,"
        "chat_ttl_deletion"
    ),
    "celery_worker_heavy": (
        "connector_pruning,connector_doc_permissions_sync,"
        "connector_external_group_sync,csv_generation,sandbox,"
        "connector_hierarchy_fetching"
    ),
    "celery_worker_docprocessing": "docprocessing,port",
    "celery_worker_user_file_processing": (
        "user_file_processing,user_file_project_sync,user_file_delete,user_file_port"
    ),
    "celery_worker_scheduled_tasks": "scheduled_tasks",
    "celery_worker_docfetching": "connector_doc_fetching",
    "celery_worker_monitoring": "monitoring",
}
REQUIRED_PROGRAMS = WORKERS + (
    "celery_beat",
    "supervisord_watchdog_celery_beat",
    "slack_bot",
    "discord_bot",
    "log-redirect-handler",
)


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, str(default)).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


def _program(name: str) -> str:
    return f"program:{name}"


def derive_config(source: Path = SOURCE_CONFIG) -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    with source.open("r", encoding="utf-8") as stream:
        parser.read_file(stream)

    sections = parser.sections()
    for name in REQUIRED_PROGRAMS:
        section = _program(name)
        if sections.count(section) != 1:
            raise RuntimeError(f"expected exactly one [{section}] section")

    for worker in WORKERS:
        command = parser.get(_program(worker), "command")
        expected = f"celery -A onyx.background.celery.versioned_apps.{worker.removeprefix('celery_worker_')} worker"
        if not command.startswith(expected + "\n"):
            raise RuntimeError(f"unexpected {worker} command")
        queue_lines = [
            line for line in command.splitlines() if line.startswith("-Q ")
        ]
        if queue_lines != [f"-Q {WORKER_QUEUES[worker]}"]:
            raise RuntimeError(f"unexpected {worker} queues")
        if "--without-heartbeat" in command or "--without-gossip" in command:
            raise RuntimeError(f"{worker} already contains wrapper event flags")

    if _flag("ENABLE_CRAFT"):
        raise RuntimeError("this wrapper has no supported Craft sandbox backend")
    parser.remove_section(_program("celery_worker_scheduled_tasks"))
    parser.remove_section(_program("celery_worker_monitoring"))

    for worker in RETAINED_WORKERS:
        section = _program(worker)
        command = parser.get(section, "command")
        parser.set(
            section,
            "command",
            command + "\n    --without-heartbeat --without-gossip",
        )

    for env_name, program in (
        ("ONYX_AGENT_SLACK_BOT", "slack_bot"),
        ("ONYX_AGENT_DISCORD_BOT", "discord_bot"),
    ):
        if not _flag(env_name):
            parser.remove_section(_program(program))

    watchdog_section = _program("supervisord_watchdog_celery_beat")
    watchdog_command = parser.get(watchdog_section, "command")
    if (
        not watchdog_command.startswith("python -m onyx.utils.supervisord_watchdog\n")
        or '--key "onyx:celery:beat:heartbeat"' not in watchdog_command
        or "--program celery_beat" not in watchdog_command
    ):
        raise RuntimeError("unexpected upstream Beat watchdog command")
    parser.set(
        watchdog_section,
        "command",
        "python -S /app/wrapper-beat-liveness-watchdog.py\n"
        f"    --conf {DERIVED_CONFIG}\n"
        "    --program celery_beat\n"
        "    --path /tmp/onyx_k8s_beat_liveness.txt",
    )

    log_section = _program("log-redirect-handler")
    log_command = parser.get(log_section, "command")
    required_logs = {
        "/var/log/celery_worker_scheduled_tasks.log",
        "/var/log/celery_worker_monitoring.log",
        "/var/log/slack_bot.log",
        "/var/log/discord_bot.log",
    }
    lines = log_command.splitlines()
    if not required_logs.issubset({line.strip() for line in lines}):
        raise RuntimeError("upstream log redirect inputs changed")
    removed_logs = {
        "/var/log/celery_worker_scheduled_tasks.log",
        "/var/log/celery_worker_monitoring.log",
    }
    if not _flag("ONYX_AGENT_SLACK_BOT"):
        removed_logs.add("/var/log/slack_bot.log")
    if not _flag("ONYX_AGENT_DISCORD_BOT"):
        removed_logs.add("/var/log/discord_bot.log")
    retained_lines = [line for line in lines if line.strip() not in removed_logs]
    parser.set(log_section, "command", "\n".join(retained_lines))

    if sum(section.startswith("program:celery_worker_") for section in parser.sections()) != 6:
        raise RuntimeError("derived configuration must contain six Celery workers")
    return parser


def main() -> None:
    if sys.argv[1:]:
        raise RuntimeError("background wrapper entrypoint accepts no arguments")
    if Path("/etc/ssl/certs/custom-ca.crt").is_file():
        subprocess.run(["update-ca-certificates"], check=True)
    parser = derive_config()
    with DERIVED_CONFIG.open("w", encoding="utf-8") as stream:
        parser.write(stream, space_around_delimiters=False)
    os.execv(
        "/usr/bin/supervisord",
        ["/usr/bin/supervisord", "-c", str(DERIVED_CONFIG)],
    )


if __name__ == "__main__":
    main()
