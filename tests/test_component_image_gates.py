"""Exercise the real Make image gate without images or a container daemon."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_component_build_identities import _make


class ComponentImageGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="component-image-gate-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.fixture = Path(cls.temporary.name).resolve()
        # Copy public build inputs only; Make evaluates its real selections and
        # hash helpers, while the fake engine supplies the public owner model.
        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
        roots = ("browser/", "executor/", "searxng/", "myst/", "teep/", "tailscale/",
                 "tor/", "embedserv/", "tools/components/", "onyx/code-interpreter/")
        files = {"Makefile", "stack.versions.env", ".env.wrapper.example", ".dockerignore", ".containerignore"}
        for name in tracked:
            if name in files or name.startswith(roots):
                destination = cls.fixture / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, destination)
        cls.inventory_path = cls.fixture / "tools/components/ownership.json"
        cls.inventory = json.loads(cls.inventory_path.read_text())
        repo = cls.inventory["onyx_services"]["opensearch"]["repository"]
        cls.reference, digest = next((reference, digest) for reference, digest in cls.inventory["registry_digests"].items()
                                     if reference.rsplit(":", 1)[0] == repo)
        cls.qualified = cls.reference + "@" + digest
        cls.bin = cls.fixture / "fixture-bin"
        cls.bin.mkdir()
        cls.log = cls.fixture / "gate-calls.jsonl"
        cls.engine = cls.bin / "docker"
        cls.engine.write_text(f"#!{sys.executable}\n" + '''import json, os, pathlib, sys
arguments = sys.argv[1:]
with open(os.environ["GATE_CALL_LOG"], "a") as stream:
    stream.write(json.dumps({"engine": arguments}) + "\\n")
if arguments[0] == "compose":
    assert "config" in arguments and "--no-env-resolution" in arguments
    env_files = [arguments[index+1] for index, value in enumerate(arguments) if value == "--env-file"]
    assert env_files == ["stack.versions.env", ".env.wrapper.example"], env_files
    print(json.dumps({"services": {"opensearch": {"image": os.environ["GATE_OWNER_IMAGE"]}}}))
    raise SystemExit(int(os.environ.get("GATE_CONFIG_EXIT", "0")))
if arguments[:2] == ["image", "inspect"]:
    raise SystemExit(0 if arguments[2] in json.loads(os.environ["GATE_CACHED_IMAGES"]) else 1)
raise SystemExit("image gate attempted an acquisition or unexpected engine operation")
''')
        cls.engine.chmod(0o755)
        shim = cls.bin / "python3"
        shim.write_text(f"#!{sys.executable}\n" + f"REAL_PYTHON = {sys.executable!r}\n" + '''import json, os, sys
arguments = sys.argv[1:]
if arguments and arguments[0] == "tests/run_opensearch_image_validation.py":
    with open(os.environ["GATE_CALL_LOG"], "a") as stream:
        stream.write(json.dumps({"validator": arguments}) + "\\n")
    raise SystemExit(0)
os.execv(REAL_PYTHON, [REAL_PYTHON, *arguments])
''')
        shim.chmod(0o755)

    def run_gate(self, cached, *, missing_map=False, config_exit=0):
        inventory = json.loads(json.dumps(self.inventory))
        if missing_map:
            del inventory["registry_digests"][self.reference]
        self.inventory_path.write_text(json.dumps(inventory))
        self.log.write_text("")
        result = _make(self.fixture, ("test-opensearch-image",), (
            f"CONTAINER_BIN={self.engine}", "ONYX_ENV_FILE=synthetic-private-generated.env",
        ), env={"PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                "GATE_CALL_LOG": str(self.log), "GATE_OWNER_IMAGE": self.reference,
                "GATE_CACHED_IMAGES": json.dumps(cached), "GATE_CONFIG_EXIT": str(config_exit)})
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        # No gate path may turn into a pull/build/start/acquisition workflow.
        self.assertTrue(all("validator" in call or call["engine"][0] in ("compose", "image") for call in calls), calls)
        self.assertTrue(all(call["engine"][:2] == ["image", "inspect"] for call in calls if "engine" in call and call["engine"][0] == "image"))
        return result, calls

    def test_old_mutable_tag_cannot_satisfy_reviewed_image_gate(self):
        result, calls = self.run_gate([self.reference])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls[-1], {"engine": ["image", "inspect", self.qualified]})
        self.assertFalse(any("validator" in call for call in calls))

    def test_inspect_and_validator_receive_the_same_reviewed_digest(self):
        result, calls = self.run_gate([self.qualified])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[1], {"engine": ["image", "inspect", self.qualified]})
        validator = calls[2]["validator"]
        self.assertEqual(validator[validator.index("--image") + 1], self.qualified)
        self.assertEqual(len(calls), 3)

    def test_missing_reviewed_mapping_fails_before_inspection_or_validation(self):
        result, calls = self.run_gate([self.reference, self.qualified], missing_map=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing reviewed digest", result.stderr)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["engine"][0], "compose")

    def test_native_config_failure_cannot_be_hidden_by_valid_partial_json(self):
        result, calls = self.run_gate([self.qualified], config_exit=7)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
