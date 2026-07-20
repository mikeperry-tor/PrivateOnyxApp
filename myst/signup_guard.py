#!/usr/bin/env python3
"""Fail closed before starting a standalone Myst signup container."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence


class GuardError(RuntimeError):
    pass


def inspect_container(command: str, name: str) -> dict[str, object] | None:
    result = subprocess.run(
        [command, "inspect", name], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        combined = (result.stdout + "\n" + result.stderr).lower()
        absent_markers = (
            "no such container",
            "no such object",
            "does not exist",
            "no container with name or id",
        )
        if any(marker in combined for marker in absent_markers):
            return None
        detail = result.stderr.strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise GuardError(f"could not inspect {command} container {name}{suffix}")
    try:
        payload = json.loads(result.stdout)
        if not isinstance(payload, list) or len(payload) != 1:
            raise ValueError("expected one inspection result")
        inspected = payload[0]
        if not isinstance(inspected, dict):
            raise ValueError("inspection result is not an object")
        return inspected
    except (json.JSONDecodeError, ValueError) as exc:
        raise GuardError(f"malformed {command} inspection for {name}: {exc}") from exc


def validate_existing(
    inspected: dict[str, object] | None,
    *,
    allowed_project: str,
    engine: str,
    require_existing: bool = False,
) -> None:
    kind = classify_existing(
        inspected,
        allowed_project=allowed_project,
        engine=engine,
    )
    if kind == "absent":
        if require_existing:
            raise GuardError(f"no {engine} signup container exists")
        return
    if kind == "setup":
        return
    raise GuardError(
        f"refusing signup against existing {engine} container: integrated Myst exists; "
        "stop the integrated stack first"
    )


def classify_existing(
    inspected: dict[str, object] | None,
    *,
    allowed_project: str,
    engine: str,
) -> str:
    if inspected is None:
        return "absent"
    config = inspected.get("Config")
    if not isinstance(config, dict):
        raise GuardError(f"{engine} container inspection has no Config object")
    labels = config.get("Labels") or {}
    if not isinstance(labels, dict):
        raise GuardError(f"{engine} container inspection has malformed labels")
    project = labels.get("com.docker.compose.project")
    environment = config.get("Env") or []
    if not isinstance(environment, list):
        raise GuardError(f"{engine} container inspection has malformed environment")
    setup_only = "MYST_SETUP_ONLY=true" in environment
    if project == allowed_project and setup_only:
        return "setup"
    if project == "onyx" and not setup_only:
        return "integrated"
    raise GuardError(
        f"refusing Myst lifecycle action against existing {engine} container: "
        f"project={project!r}, setup_only={setup_only}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container-bin", required=True)
    parser.add_argument("--container-name", required=True)
    parser.add_argument("--allowed-project", required=True)
    parser.add_argument("--require-existing", action="store_true")
    parser.add_argument("--classify", action="store_true")
    args = parser.parse_args(argv)
    try:
        inspected = inspect_container(args.container_bin, args.container_name)
        if args.classify:
            if args.require_existing:
                parser.error("--classify and --require-existing cannot be combined")
            print(
                classify_existing(
                    inspected,
                    allowed_project=args.allowed_project,
                    engine=args.container_bin,
                )
            )
        else:
            validate_existing(
                inspected,
                allowed_project=args.allowed_project,
                engine=args.container_bin,
                require_existing=args.require_existing,
            )
    except GuardError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
