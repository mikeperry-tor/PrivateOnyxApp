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
SECRETS = {
    "SEARXNG_WRAPPER_IMAGE": "local/private-onyx-searxng:inventory",
    "SEARXNG_SECRET": "inventory",
    "USER_AUTH_SECRET": "inventory",
    "MINIO_ROOT_USER": "inventory",
    "MINIO_ROOT_PASSWORD": "inventory",
    "S3_AWS_ACCESS_KEY_ID": "inventory",
    "S3_AWS_SECRET_ACCESS_KEY": "inventory",
}


def duration_seconds(value: str) -> float:
    units = {"h": 3600, "m": 60, "s": 1}
    parts = re.findall(r"([0-9]+(?:[.][0-9]+)?)(h|m|s)", value)
    if not parts or "".join(number + unit for number, unit in parts) != value:
        raise ValueError(f"unsupported Compose duration: {value}")
    return sum(float(number) * units[unit] for number, unit in parts)


def inventory(mode: str) -> list[dict[str, object]]:
    command = [
        "docker", "compose",
        "--env-file", "stack.versions.env",
        "--env-file", ".env.wrapper.example",
        "-f", "docker-compose.yaml",
        "-f", f"docker-compose.{mode}.yml",
        "config", "--no-env-resolution", "--format", "json",
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, **SECRETS},
        check=True,
        capture_output=True,
        text=True,
    )
    model = json.loads(result.stdout)
    rows = []
    for name, service in sorted(model["services"].items()):
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
    args = parser.parse_args()
    print(json.dumps(inventory(args.mode), indent=2, sort_keys=True))
