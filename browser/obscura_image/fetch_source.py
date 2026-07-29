#!/usr/bin/env python3
"""Fetch, verify, and safely extract one immutable Obscura source archive."""

from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path, PurePosixPath
import shutil
import tarfile
from urllib.request import urlopen


ARCHIVE_URL = "https://github.com/h4ckf0r0day/obscura/archive/{ref}.tar.gz"


def _validated_members(
    package: tarfile.TarFile, *, expected_root: str
) -> list[tuple[tarfile.TarInfo, PurePosixPath]]:
    seen: set[PurePosixPath] = set()
    validated: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
    for member in package.getmembers():
        path = PurePosixPath(member.name)
        if (
            path.is_absolute()
            or not path.parts
            or path.parts[0] != expected_root
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise RuntimeError("source archive contains an invalid path")
        if path in seen:
            raise RuntimeError("source archive contains a duplicate path")
        seen.add(path)
        if not (member.isdir() or member.isfile()):
            raise RuntimeError("source archive contains a non-regular member")
        validated.append((member, path))
    root = PurePosixPath(expected_root)
    if root not in seen or not any(path != root for _member, path in validated):
        raise RuntimeError("source archive has an unexpected or empty root")
    return validated


def fetch_source(*, ref: str, expected_digest: str, output_dir: Path) -> None:
    if not ref or any(character not in "0123456789abcdef" for character in ref):
        raise ValueError("source ref must be a lowercase hexadecimal commit")
    if len(ref) != 40:
        raise ValueError("source ref must be a complete commit")
    if (
        len(expected_digest) != 64
        or any(character not in "0123456789abcdef" for character in expected_digest)
    ):
        raise ValueError("source digest must be lowercase SHA-256")
    if output_dir.exists():
        raise FileExistsError(f"output already exists: {output_dir}")

    with urlopen(ARCHIVE_URL.format(ref=ref), timeout=120) as response:
        archive = response.read()
    actual_digest = hashlib.sha256(archive).hexdigest()
    if actual_digest != expected_digest:
        raise RuntimeError(
            f"source archive digest mismatch: expected {expected_digest}, "
            f"found {actual_digest}"
        )

    expected_root = f"obscura-{ref}"
    created = False
    try:
        output_dir.mkdir(mode=0o755, parents=True, exist_ok=False)
        created = True
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
            members = _validated_members(package, expected_root=expected_root)
            for member, path in members:
                relative = Path(*path.parts[1:])
                if relative == Path("."):
                    continue
                destination = output_dir / relative
                if member.isdir():
                    destination.mkdir(mode=0o755, parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                source = package.extractfile(member)
                if source is None:
                    raise RuntimeError("source archive member could not be read")
                with source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                destination.chmod(0o755 if member.mode & 0o111 else 0o644)
    except BaseException:
        if created:
            shutil.rmtree(output_dir)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    fetch_source(
        ref=args.ref,
        expected_digest=args.sha256,
        output_dir=args.output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
