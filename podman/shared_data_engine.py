#!/usr/bin/env python3
"""Fail-closed ownership marker for Docker/Podman shared database binds."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import unquote, urlsplit


VALID_ENGINES = {"docker", "podman"}
SHARED_WRITER_SERVICES = {"relational_db", "opensearch"}
MYST_CONTAINER_NAME = "myst-client-vpn"


class GuardError(RuntimeError):
    pass


def _validate_engine(engine: str) -> str:
    normalized = engine.strip().lower()
    if normalized not in VALID_ENGINES:
        raise GuardError(f"unsupported shared-data engine: {engine!r}")
    return normalized


def read_owner(marker: Path) -> str | None:
    try:
        owner = marker.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    if owner not in VALID_ENGINES:
        raise GuardError(f"invalid shared-data engine marker at {marker}")
    return owner


def _engine_for_command(command: str) -> str:
    name = Path(command).name.lower()
    return "podman" if "podman" in name else "docker"


def _available_command(command: str) -> bool:
    if os.sep in command:
        return os.path.isfile(command) and os.access(command, os.X_OK)
    return shutil.which(command) is not None


def _running_shared_writers(command: str) -> set[str]:
    inspected = subprocess.run(
        [
            command,
            "ps",
            "--filter",
            "label=com.docker.compose.project=onyx",
            "--filter",
            "status=running",
            "--format",
            '{{.Label "com.docker.compose.service"}}',
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if inspected.returncode != 0:
        detail = inspected.stderr.strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise GuardError(f"could not inspect {command} for shared-data writers{suffix}")
    writers = SHARED_WRITER_SERVICES.intersection(inspected.stdout.splitlines())
    myst = subprocess.run(
        [
            command,
            "ps",
            "--filter",
            f"name=^{MYST_CONTAINER_NAME}$",
            "--filter",
            "status=running",
            "--format",
            "{{.Names}}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if myst.returncode != 0:
        detail = myst.stderr.strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise GuardError(f"could not inspect {command} for Myst writer{suffix}")
    if MYST_CONTAINER_NAME in myst.stdout.splitlines():
        writers.add("myst-client")
    return writers


def _podman_machine_is_stopped(command: str) -> bool:
    inspected = subprocess.run(
        [command, "machine", "inspect", "--format", "{{.State}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    states = {
        line.strip().lower()
        for line in inspected.stdout.splitlines()
        if line.strip()
    }
    return inspected.returncode == 0 and states == {"stopped"}


def _docker_local_endpoint_is_absent(command: str) -> str | None:
    """Return an absent local Docker endpoint, or None when absence is unproved."""
    inspected = subprocess.run(
        [
            command,
            "context",
            "inspect",
            "--format",
            "{{.Endpoints.docker.Host}}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    endpoints = [
        line.strip() for line in inspected.stdout.splitlines() if line.strip()
    ]
    if inspected.returncode != 0 or len(endpoints) != 1:
        return None
    endpoint = endpoints[0]
    parsed = urlsplit(endpoint)
    if parsed.scheme != "unix" or parsed.netloc or not parsed.path:
        return None
    socket_path = unquote(parsed.path)
    if not os.path.isabs(socket_path) or os.path.lexists(socket_path):
        return None
    return endpoint


def inspect_first_claim(commands: Iterable[str], engine: str) -> None:
    checked: set[tuple[str, str]] = set()
    for command in commands:
        command_engine = _engine_for_command(command)
        identity = (
            command_engine,
            os.path.realpath(command) if os.sep in command else command,
        )
        if identity in checked or not _available_command(command):
            continue
        checked.add(identity)
        try:
            writers = _running_shared_writers(command)
        except GuardError:
            if (
                command_engine != engine
                and command_engine == "podman"
                and _podman_machine_is_stopped(command)
            ):
                print(f"Skipping stopped unselected Podman machine ({command}).")
                continue
            if command_engine != engine and command_engine == "docker":
                absent_endpoint = _docker_local_endpoint_is_absent(command)
                if absent_endpoint is not None:
                    print(
                        "Skipping unselected Docker client with absent local "
                        f"endpoint ({command}: {absent_endpoint})."
                    )
                    continue
            raise
        if writers and command_engine != engine:
            names = ", ".join(sorted(writers))
            raise GuardError(
                f"refusing first {engine} claim while {command_engine} has running "
                f"shared-data writer(s): {names}"
            )


def claim(
    marker: Path,
    engine: str,
    *,
    inspect_commands: Iterable[str] = ("docker", "podman"),
    adopt_unclaimed: bool = False,
) -> str:
    engine = _validate_engine(engine)
    marker.parent.mkdir(parents=True, exist_ok=True)
    owner = read_owner(marker)
    if owner is not None:
        if owner != engine:
            raise GuardError(
                f"shared persistent data is claimed by {owner}; run that "
                "engine's matching make down-* target before starting " + engine
            )
        # Re-inspect on every same-engine claim. This catches an out-of-band
        # container started by the other engine after the marker was created,
        # especially a Myst daemon sharing the wallet/database bind.
        if not adopt_unclaimed:
            inspect_first_claim(inspect_commands, engine)
        return owner

    if not adopt_unclaimed:
        inspect_first_claim(inspect_commands, engine)
    try:
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        owner = read_owner(marker)
        if owner != engine:
            raise GuardError(
                f"shared persistent data is claimed by {owner}; run that "
                "engine's matching make down-* target before starting {engine}"
            )
        return owner
    try:
        os.write(descriptor, (engine + "\n").encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return engine


def release(marker: Path, engine: str) -> None:
    engine = _validate_engine(engine)
    owner = read_owner(marker)
    if owner is None:
        return
    if owner != engine:
        raise GuardError(
            f"refusing to release {owner}'s shared-data claim as {engine}"
        )
    marker.unlink()


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("claim", "release", "status"))
    parser.add_argument("--engine", choices=sorted(VALID_ENGINES))
    parser.add_argument(
        "--marker",
        type=Path,
        default=Path("docker-data/host-services/shared-data-engine"),
    )
    parser.add_argument(
        "--container-bin",
        help="selected engine command to inspect before the first claim",
    )
    parser.add_argument(
        "--adopt-unclaimed",
        action="store_true",
        help="seed an absent marker after the operator has verified both engines are down",
    )
    args = parser.parse_args(argv)
    if args.action != "status" and args.engine is None:
        parser.error("--engine is required for claim and release")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.action == "claim":
            commands = [args.container_bin] if args.container_bin else []
            commands.extend(("docker", "podman"))
            owner = claim(
                args.marker,
                args.engine,
                inspect_commands=commands,
                adopt_unclaimed=args.adopt_unclaimed,
            )
            print(f"Shared persistent data claimed by {owner}.")
        elif args.action == "release":
            release(args.marker, args.engine)
            print(f"Shared persistent data released for {args.engine}.")
        else:
            owner = read_owner(args.marker)
            print(owner if owner is not None else "unclaimed")
    except GuardError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
