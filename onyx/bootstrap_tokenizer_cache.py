#!/usr/bin/env python3
"""Extract Onyx's bundled nomic tokenizer into the shared runtime cache."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


MODEL_REPO = "nomic-ai/nomic-embed-text-v1"
TOKENIZER_FILENAME = "tokenizer.json"
MAX_TOKENIZER_BYTES = 64 * 1024 * 1024
EXTRACT_PROGRAM = f"""\
import pathlib
import sys
from huggingface_hub import hf_hub_download

path = hf_hub_download(
    repo_id={MODEL_REPO!r},
    filename={TOKENIZER_FILENAME!r},
    local_files_only=True,
)
sys.stdout.buffer.write(pathlib.Path(path).read_bytes())
"""


class BootstrapError(RuntimeError):
    """The pinned image could not provide a valid offline tokenizer."""


def _validate_tokenizer(data: bytes) -> None:
    if not data:
        raise BootstrapError("bundled tokenizer is empty")
    if len(data) > MAX_TOKENIZER_BYTES:
        raise BootstrapError("bundled tokenizer exceeds the 64 MiB safety limit")
    try:
        document = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("bundled tokenizer is not valid JSON") from exc
    if not isinstance(document, dict):
        raise BootstrapError("bundled tokenizer JSON is not an object")
    if not isinstance(document.get("model"), dict):
        raise BootstrapError("bundled tokenizer JSON has no model object")


def extract_tokenizer(container_bin: str, image: str) -> bytes:
    command = [
        container_bin,
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        "python",
        image,
        "-c",
        EXTRACT_PROGRAM,
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise BootstrapError(
            f"could not execute container engine: {container_bin}"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        if len(detail) > 1000:
            detail = detail[-1000:]
        suffix = f": {detail}" if detail else ""
        raise BootstrapError(
            "pinned Onyx image does not provide the required offline tokenizer"
            f"{suffix}"
        )
    _validate_tokenizer(completed.stdout)
    return completed.stdout


def install_tokenizer(data: bytes, output: Path) -> bool:
    """Atomically install data; return True only when the file changed."""
    _validate_tokenizer(data)
    try:
        if os.path.lexists(output):
            if output.is_symlink() or not output.is_file():
                raise BootstrapError(
                    f"offline tokenizer target is not a regular file: {output}"
                )
            if (
                output.stat().st_size <= MAX_TOKENIZER_BYTES
                and output.read_bytes() == data
            ):
                return False
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.", dir=output.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o644)
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError as exc:
        raise BootstrapError(f"could not install offline tokenizer at {output}") from exc
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container-bin", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        data = extract_tokenizer(args.container_bin, args.image)
        changed = install_tokenizer(data, args.output)
    except BootstrapError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    state = "installed" if changed else "already current"
    print(f"Offline nomic tokenizer {state}: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
