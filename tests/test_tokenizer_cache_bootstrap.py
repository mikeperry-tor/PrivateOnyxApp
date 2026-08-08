from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "onyx" / "bootstrap_tokenizer_cache.py"
)
ROOT = Path(__file__).resolve().parents[1]


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "bootstrap_tokenizer_cache_under_test", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TokenizerCacheBootstrapTests(unittest.TestCase):
    def test_full_start_and_services_share_generated_tokenizer(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        full_prerequisites = next(
            line
            for line in makefile.splitlines()
            if line.startswith("up-full: wrapper-config-preflight")
        )
        self.assertIn("prepare-onyx-tokenizer", full_prerequisites)
        target = makefile.split("prepare-onyx-tokenizer:", 1)[1].split("\n\n", 1)[0]
        self.assertIn("bootstrap_tokenizer_cache.py", target)
        self.assertIn("--image \"$(ONYX_BACKEND_IMAGE)\"", target)

        compose = (ROOT / "compose_overlays/docker-compose.full.yml").read_text(
            encoding="utf-8"
        )
        expected = (
            'ONYX_EMBEDDING_TOKENIZER_FILE: '
            '"/root/.cache/huggingface/private-onyx/'
            'nomic-embed-text-v1/tokenizer.json"'
        )
        self.assertEqual(compose.count(expected), 2)

    def test_extract_uses_pinned_image_without_network(self) -> None:
        module = _load_module()
        tokenizer = b'{"model": {"type": "BPE"}}'
        completed = subprocess.CompletedProcess([], 0, tokenizer, b"")

        with patch.object(module.subprocess, "run", return_value=completed) as run:
            self.assertEqual(
                module.extract_tokenizer("podman", "test/image:tag"), tokenizer
            )

        command = run.call_args.args[0]
        self.assertEqual(command[:5], ["podman", "run", "--rm", "--network", "none"])
        self.assertIn("test/image:tag", command)
        self.assertIn("local_files_only=True", command[-1])

    def test_install_is_atomic_and_idempotent(self) -> None:
        module = _load_module()
        first = b'{"model": {"type": "BPE"}}'
        second = b'{"model": {"type": "Unigram"}}'

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cache" / "tokenizer.json"
            self.assertTrue(module.install_tokenizer(first, output))
            self.assertEqual(output.read_bytes(), first)
            self.assertFalse(module.install_tokenizer(first, output))
            self.assertTrue(module.install_tokenizer(second, output))
            self.assertEqual(output.read_bytes(), second)
            self.assertEqual(list(output.parent.glob(".tokenizer.json.*")), [])

    def test_invalid_image_output_is_rejected(self) -> None:
        module = _load_module()
        completed = subprocess.CompletedProcess([], 0, b"not json", b"")

        with patch.object(module.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(module.BootstrapError, "valid JSON"):
                module.extract_tokenizer("docker", "test/image:tag")


if __name__ == "__main__":
    unittest.main()
