#!/usr/bin/env python3
"""Fail-closed ownership marker for Docker/Podman shared database binds."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence


VALID_ENGINES = {"docker", "podman"}


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


def claim(marker: Path, engine: str) -> str:
    engine = _validate_engine(engine)
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        owner = read_owner(marker)
        if owner != engine:
            raise GuardError(
                f"shared database/index data is claimed by {owner}; run that "
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
    args = parser.parse_args(argv)
    if args.action != "status" and args.engine is None:
        parser.error("--engine is required for claim and release")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.action == "claim":
            owner = claim(args.marker, args.engine)
            print(f"Shared database/index data claimed by {owner}.")
        elif args.action == "release":
            release(args.marker, args.engine)
            print(f"Shared database/index data released for {args.engine}.")
        else:
            owner = read_owner(args.marker)
            print(owner if owner is not None else "unclaimed")
    except GuardError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
