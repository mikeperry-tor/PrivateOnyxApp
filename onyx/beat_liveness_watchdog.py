#!/usr/bin/env python3
"""Stdlib-only watchdog for the local Celery Beat liveness file."""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
import time
from pathlib import Path


CHECK_INTERVAL_SECONDS = 300
STALE_AFTER_SECONDS = 1200
STARTUP_GRACE_SECONDS = 1200
EXPECTED_PATH = Path("/tmp/onyx_k8s_beat_liveness.txt")


def _valid_mtime(path: Path, expected_uid: int, now_wall: float) -> float | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return None
    if metadata.st_uid != expected_uid or metadata.st_mtime > now_wall + 1:
        return None
    return metadata.st_mtime


def watch(path: Path, program: str, conf: Path) -> None:
    if path != EXPECTED_PATH:
        raise RuntimeError(f"Beat liveness path must be {EXPECTED_PATH}")
    started = time.monotonic()
    missing_observations = 0
    while True:
        time.sleep(CHECK_INTERVAL_SECONDS)
        now_mono = time.monotonic()
        now_wall = time.time()
        mtime = _valid_mtime(path, os.getuid(), now_wall)
        if mtime is None:
            missing_observations += 1
        else:
            missing_observations = 0
        in_startup_grace = now_mono - started <= STARTUP_GRACE_SECONDS
        stale = mtime is not None and now_wall - mtime > STALE_AFTER_SECONDS
        invalid = mtime is None and missing_observations >= 2
        if in_startup_grace or not (stale or invalid):
            continue
        reason = "stale" if stale else "missing-or-invalid"
        print(f"Beat liveness {reason}; restarting {program}", flush=True)
        subprocess.run(
            ["supervisorctl", "-c", str(conf), "restart", program],
            check=True,
        )
        started = time.monotonic()
        missing_observations = 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--program", required=True)
    parser.add_argument("--conf", type=Path, required=True)
    args = parser.parse_args()
    if args.program != "celery_beat":
        raise RuntimeError("watchdog may restart only celery_beat")
    watch(args.path, args.program, args.conf)


if __name__ == "__main__":
    main()
