#!/usr/bin/env python3
"""Fetch and verify one pinned Obscura stealth release archive."""

from __future__ import annotations

import argparse
import hashlib
import io
import os
from pathlib import Path
import tarfile
from urllib.request import urlopen


ARCHITECTURES = {
    "amd64": "x86_64",
    "arm64": "aarch64",
}
EXPECTED_FILES = ("obscura", "obscura-worker")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--architecture", choices=tuple(ARCHITECTURES), required=True)
    parser.add_argument("--amd64-sha256", required=True)
    parser.add_argument("--arm64-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    release_arch = ARCHITECTURES[args.architecture]
    expected_digest = (
        args.amd64_sha256
        if args.architecture == "amd64"
        else args.arm64_sha256
    )
    archive_name = f"obscura-{release_arch}-linux-stealth.tar.gz"
    url = (
        "https://github.com/h4ckf0r0day/obscura/releases/download/"
        f"v{args.version}/{archive_name}"
    )
    with urlopen(url, timeout=120) as response:
        archive = response.read()
    actual_digest = hashlib.sha256(archive).hexdigest()
    if actual_digest != expected_digest:
        raise RuntimeError(
            f"{archive_name} digest mismatch: expected {expected_digest}, "
            f"found {actual_digest}"
        )

    args.output_dir.mkdir(mode=0o755, parents=True, exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
        members = {member.name: member for member in package.getmembers()}
        if set(members) != set(EXPECTED_FILES):
            raise RuntimeError(
                f"{archive_name} members changed: {sorted(members)!r}"
            )
        for name in EXPECTED_FILES:
            member = members[name]
            if not member.isfile() or member.issym() or member.islnk():
                raise RuntimeError(f"{archive_name} member is not a regular file: {name}")
            source = package.extractfile(member)
            if source is None:
                raise RuntimeError(f"{archive_name} member could not be read: {name}")
            destination = args.output_dir / name
            with source, destination.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
            os.chmod(destination, 0o755)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
