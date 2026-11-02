#!/usr/bin/env python3
"""Hash selected build inputs without reading through source symlinks."""
from __future__ import annotations

import hashlib
import argparse
from pathlib import Path
import stat


def identity(selection: str, revision: str, inputs: list[Path], *, dockerfile: Path | None = None) -> str:
    # Check ancestors before touching any selected policy/content. Absolute
    # overrides must use canonical paths rather than symlinked source trees.
    for path in inputs:
        for part in (*reversed(path.parents), path):
            if part.is_symlink():
                raise ValueError(f"symlink build input is unsupported; use a canonical source path: {part}")
    if dockerfile is not None:
        if dockerfile not in inputs:
            raise ValueError("selected Dockerfile must be an explicit build input")
        specific = dockerfile.with_name(dockerfile.name + ".dockerignore")
        if specific.exists() or specific.is_symlink():
            raise ValueError(f"Dockerfile-specific ignore policy is unsupported: {specific}")
    digest = hashlib.sha256()
    for value in (selection, revision):
        digest.update(value.encode() + b"\0")
    policies = [path for path in inputs if path.name in (".dockerignore", ".containerignore")]
    rules = [[line.strip() for line in path.read_text().splitlines()
              if line.strip() and not line.lstrip().startswith("#")] for path in policies]
    # Over-hash rather than omit potentially copied cache files under custom rules.
    skip_cache = bool(rules) and all("**/__pycache__" in lines and
                                    not any(line.startswith("!") for line in lines) for lines in rules)

    def visit(path: Path) -> None:
        if path.is_symlink():
            raise ValueError(f"symlink build input is unsupported: {path}")
        mode = path.stat().st_mode
        digest.update(str(path).encode() + b"\0" + str(stat.S_IMODE(mode)).encode() + b"\0")
        if path.is_dir():
            digest.update(b"directory\0")
            for child in sorted(path.iterdir()):
                if not (skip_cache and child.name == "__pycache__"):
                    visit(child)
        elif path.is_file():
            digest.update(b"file\0" + path.read_bytes())
        else:
            raise ValueError(f"build input must be a regular file/directory: {path}")

    for path in inputs:
        visit(path)
    return digest.hexdigest()[:12]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dockerfile", type=Path, required=True)
    parser.add_argument("selection")
    parser.add_argument("revision")
    parser.add_argument("inputs", type=Path, nargs="+")
    args = parser.parse_args()
    try:
        print(identity(args.selection, args.revision, args.inputs, dockerfile=args.dockerfile))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"build identity failed: {exc}") from None
