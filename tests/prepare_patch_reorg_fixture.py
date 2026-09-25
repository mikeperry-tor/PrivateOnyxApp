"""Prepare a disposable PDF route and synthetic-only HTTP counter overlay.

The normal document source stays mounted unchanged. The exact fixture path
uses a separate read-only tree outside the private document mount.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import uuid

from patch_activation_probe import synthetic_pdf


def prepare(directory: Path) -> dict:
    directory.mkdir(parents=False, exist_ok=False)
    directory = directory.resolve()
    repo = Path(__file__).resolve().parents[1]
    name = "onyx-reorg-" + uuid.uuid4().hex
    fixture_root = directory / "document"
    fixture = fixture_root / name
    fixture.mkdir(parents=True)
    (fixture / "fixture.pdf").write_bytes(synthetic_pdf())
    output = directory / "counters"
    output.mkdir()
    overlay = directory / "compose.json"
    overlay.write_text(json.dumps({"services": {"doc-drop-web": {
        "volumes": [
            f"{fixture_root}:/fixture-data:ro",
            f"{repo / 'tests/doc_drop_fixture_counter.py'}:/app/doc_drop_fixture_counter.py:ro",
            f"{output}:/fixture-output",
        ],
        "environment": {
            "ONYX_TEST_FIXTURE_DIRECTORY": "/fixture-data",
            "ONYX_TEST_FIXTURE_PATH": f"/{name}/fixture.pdf",
            "ONYX_TEST_COUNTER_FILE": "/fixture-output/counters.json",
        },
        "command": ["python", "/app/doc_drop_fixture_counter.py", "8091", "--bind", "0.0.0.0", "--directory", "/import/docs"],
    }}}, indent=2) + "\n")
    fragment = directory / "fixture.mk"
    fragment.write_text(
        f"override FULL_FILES := $(FULL_FILES):{overlay}\n"
        "patch-reorg-fixture-model:\n"
        '\t@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) config --format json --no-env-resolution\n'
    )
    manifest = {"fixture_name": name, "url": f"http://doc-drop-web:8091/{name}/fixture.pdf",
                "make_fragment": str(fragment), "counter_file": str(output / "counters.json"),
                "connector_id": None, "credential_id": None, "cc_pair_id": None}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="new disposable directory")
    args = parser.parse_args()
    print(json.dumps(prepare(args.directory), indent=2))
