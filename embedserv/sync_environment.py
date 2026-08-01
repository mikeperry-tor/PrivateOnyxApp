#!/usr/bin/env python3
"""Atomically synchronize the bundled MLX virtual environment."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid


def _run(command: list[str], *, uv_cache_dir: str) -> None:
    environment = os.environ.copy()
    environment["UV_CACHE_DIR"] = uv_cache_dir
    subprocess.run(command, check=True, env=environment)


def synchronize(
    *,
    venv: Path,
    requirements: Path,
    python_version: str,
    fingerprint: str,
    stamp_name: str,
    uv_cache_dir: str,
) -> None:
    if not requirements.is_file():
        raise RuntimeError(f"requirements lock is missing: {requirements}")
    if not fingerprint or any(character.isspace() for character in fingerprint):
        raise RuntimeError("installation fingerprint is invalid")
    if not stamp_name or "/" in stamp_name or stamp_name in {".", ".."}:
        raise RuntimeError("installation stamp name is invalid")

    venv.parent.mkdir(parents=True, exist_ok=True)
    backup = venv.with_name(f"{venv.name}.private-onyx-backup-{uuid.uuid4().hex}")
    had_existing = venv.exists()
    if had_existing:
        os.replace(venv, backup)

    try:
        _run(
            ["uv", "venv", "--python", python_version, str(venv)],
            uv_cache_dir=uv_cache_dir,
        )
        venv_python = venv / "bin" / "python"
        if not venv_python.is_file():
            raise RuntimeError("uv did not create the expected virtual-environment Python")
        _run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(venv_python),
                "--require-hashes",
                "-r",
                str(requirements),
            ],
            uv_cache_dir=uv_cache_dir,
        )
        _run(
            ["uv", "pip", "check", "--python", str(venv_python)],
            uv_cache_dir=uv_cache_dir,
        )
        installed_version = subprocess.run(
            [
                str(venv_python),
                "-c",
                "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if installed_version != python_version:
            raise RuntimeError(
                f"expected Python {python_version}, installed Python {installed_version}"
            )
        (venv / stamp_name).write_text(f"{fingerprint}\n", encoding="utf-8")
    except BaseException:
        if venv.exists():
            shutil.rmtree(venv)
        if had_existing and backup.exists():
            os.replace(backup, venv)
        raise
    else:
        if backup.exists():
            shutil.rmtree(backup)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--venv", required=True, type=Path)
    parser.add_argument("--requirements", required=True, type=Path)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--stamp-name", required=True)
    parser.add_argument("--uv-cache-dir", required=True)
    arguments = parser.parse_args()

    try:
        synchronize(
            venv=arguments.venv,
            requirements=arguments.requirements,
            python_version=arguments.python_version,
            fingerprint=arguments.fingerprint,
            stamp_name=arguments.stamp_name,
            uv_cache_dir=arguments.uv_cache_dir,
        )
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: failed to synchronize bundled MLX environment: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
