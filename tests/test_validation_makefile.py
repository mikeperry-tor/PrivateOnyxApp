from __future__ import annotations

import os
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = (ROOT / "Makefile").read_text()
IMAGE_SCRIPT_PATH = ROOT / "tests" / "validate_pinned_patch_images.sh"
IMAGE_SCRIPT = IMAGE_SCRIPT_PATH.read_text()


class ValidationMakefileTests(unittest.TestCase):
    def test_layered_validation_targets_are_declared(self) -> None:
        phony_line = next(
            line for line in MAKEFILE.splitlines() if line.startswith(".PHONY:")
        )
        phony_targets = phony_line.removeprefix(".PHONY:").split()
        for target in (
            "test",
            "check",
            "test-images",
            "test-opensearch-image",
            "check-upgrade",
            "integration-opensearch",
            "integration-opensearch-restart",
            "integration-opensearch-onyx",
        ):
            self.assertRegex(MAKEFILE, rf"(?m)^{re.escape(target)}:")
            self.assertIn(target, phony_targets)

        self.assertIn("./tests/validate_pinned_patch_images.sh", MAKEFILE)
        self.assertIn("$(MAKE) --no-print-directory check", MAKEFILE)
        self.assertIn("$(MAKE) --no-print-directory test-images", MAKEFILE)
        self.assertIn("$(MAKE) --no-print-directory test-opensearch-image", MAKEFILE)
        self.assertIn('tests/run_opensearch_image_validation.py', MAKEFILE)
        self.assertIn('tests/opensearch_runtime_validation.py', MAKEFILE)
        self.assertIn('tests/onyx_opensearch_runtime_validation.py', MAKEFILE)

    def test_opensearch_validation_is_container_engine_neutral(self) -> None:
        runtime = (ROOT / "tests" / "opensearch_runtime_validation.py").read_text()
        image = (ROOT / "tests" / "run_opensearch_image_validation.py").read_text()
        onyx = (ROOT / "tests" / "onyx_opensearch_runtime_validation.py").read_text()
        for script in (runtime, image, onyx):
            self.assertIn("container_bin", script)
            self.assertNotIn("/var/run/docker.sock", script)
            self.assertNotIn("docker.sock", script)
        self.assertIn('[client.container_bin, "restart", client.container]', runtime)
        self.assertIn('[args.container_bin, "rm", "--force", name]', image)
        self.assertIn('[args.container_bin, "volume", "rm", "--force", volume]', image)

    def test_image_validation_is_executable_and_does_not_fetch_images(self) -> None:
        self.assertTrue(os.access(IMAGE_SCRIPT_PATH, os.X_OK))
        self.assertIn('image inspect "$image"', IMAGE_SCRIPT)
        self.assertNotRegex(IMAGE_SCRIPT, r'(?m)^.*"\$container_bin" (pull|build)\b')
        self.assertEqual(IMAGE_SCRIPT.count("--network none"), 8)
        self.assertIn("WRAPPER_PATCH_STRICT=true", IMAGE_SCRIPT)
        self.assertIn("PINNED_STOCK_CRAWLER_PATCH_CONTRACT_OK", IMAGE_SCRIPT)
        self.assertIn("PINNED_OBSCURA_CRAWLER_PATCH_CONTRACT_OK", IMAGE_SCRIPT)
        self.assertIn("PINNED_EXECUTOR_SYMPY_OK", IMAGE_SCRIPT)
        self.assertIn("/validation/validate_pinned_background.py", IMAGE_SCRIPT)
        self.assertNotIn("effective={t['name']", IMAGE_SCRIPT)
        self.assertIn("tests.test_searxng_obscura_engines", IMAGE_SCRIPT)

    def test_podman_image_validation_skips_unsupported_executor(self) -> None:
        self.assertIn('*podman*) validate_code_interpreter=false', IMAGE_SCRIPT)
        self.assertIn('if [ "$validate_code_interpreter" = true ]', IMAGE_SCRIPT)
        self.assertIn("supported Podman model omits", IMAGE_SCRIPT)

    def test_upgrade_flow_is_documented_for_agents_and_maintainers(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text()
        upgrade_notes = (ROOT / "docs" / "onyx_patches_upgrade.md").read_text()
        for document in (agents, upgrade_notes):
            self.assertIn("make check-upgrade", document)
            self.assertIn("make test-images", document)
        self.assertIn("runtime-patch-only change", agents)
        self.assertIn("Runtime startup must not install packages", agents)
        self.assertIn("onyx/onyx_data/deployment/.env", agents)


if __name__ == "__main__":
    unittest.main()
