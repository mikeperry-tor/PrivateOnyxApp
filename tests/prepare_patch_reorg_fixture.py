"""Prepare one disposable PDF submount and synthetic-only HTTP counter overlay.

Run Make with its normal Makefile plus the emitted Make fragment. The normal
document source stays mounted unchanged; this adds only a unique fixture subtree.
Cleanup removes only this newly created directory after the ordinary stack is down.

Docker requires an existing empty directory with the emitted fixture_name in
the configured document source before mounting below its read-only parent.
Create/remove that exact empty mountpoint only with authorization to modify the
private source; this helper deliberately does not access the document source.
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
    fixture = directory / "document"
    fixture.mkdir()
    (fixture / "fixture.pdf").write_bytes(synthetic_pdf())
    output = directory / "counters"
    output.mkdir()
    overlay = directory / "compose.json"
    overlay.write_text(json.dumps({"services": {"doc-drop-web": {
        "volumes": [
            f"{fixture}:/import/docs/{name}:ro",
            f"{repo / 'tests/doc_drop_fixture_counter.py'}:/app/doc_drop_fixture_counter.py:ro",
            f"{output}:/fixture-output",
        ],
        "environment": {
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
