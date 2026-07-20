#!/usr/bin/env python3
"""Print deterministic retained health-check inventory from effective Compose."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def duration_seconds(value: str) -> float:
    units = {"h": 3600, "m": 60, "s": 1}
    parts = re.findall(r"([0-9]+(?:[.][0-9]+)?)(h|m|s)", value)
    if not parts or "".join(number + unit for number, unit in parts) != value:
        raise ValueError(f"unsupported Compose duration: {value}")
    return sum(float(number) * units[unit] for number, unit in parts)


def inventory(
    mode: str, container_bin: str, env_files: list[str]
) -> list[dict[str, object]]:
    command = [container_bin, "compose"]
    for env_file in env_files:
        command.extend(("--env-file", env_file))
    command.extend(("config", "--format", "json"))
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=os.environ,
        check=True,
        capture_output=True,
        text=True,
    )
    model = json.loads(result.stdout)
    active_profiles = {
        profile.strip()
        for profile in os.environ.get("COMPOSE_PROFILES", "").split(",")
        if profile.strip()
    }
    rows = []
    for name, service in sorted(model["services"].items()):
        profiles = set(service.get("profiles") or ())
        if profiles and not profiles.intersection(active_profiles):
            continue
        health = service.get("healthcheck")
        if not health or health.get("disable"):
            continue
        interval_seconds = duration_seconds(health["interval"])
        rows.append(
            {
                "service": name,
                "command": health["test"],
                "interval": health["interval"],
                "start_interval": health.get("start_interval"),
                "timeout": health.get("timeout"),
                "retries": health.get("retries"),
                "start_period": health.get("start_period"),
                "checks_per_hour": 3600.0 / interval_seconds,
            }
        )
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("lite", "full"))
    parser.add_argument("--container-bin", required=True)
    parser.add_argument("--env-file", action="append", default=[])
    args = parser.parse_args()
    rows = inventory(args.mode, args.container_bin, args.env_file)
    report = {
        "mode": args.mode,
        "retained_healthchecks": len(rows),
        "steady_checks_per_hour": sum(
            float(row["checks_per_hour"]) for row in rows
        ),
        "services": rows,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
