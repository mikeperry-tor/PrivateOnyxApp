from __future__ import annotations

import os
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = (ROOT / "Makefile").read_text()
IMAGE_SCRIPT_PATH = ROOT / "tests" / "validate_pinned_patch_images.sh"
IMAGE_SCRIPT = IMAGE_SCRIPT_PATH.read_text()
PINNED_API_VALIDATOR = (ROOT / "tests" / "validate_pinned_api.py").read_text()
EXECUTOR_NETWORK_VALIDATOR = (
    ROOT / "tests" / "validate_code_interpreter_executor_network.py"
).read_text()


class ValidationMakefileTests(unittest.TestCase):
    def test_layered_validation_targets_are_declared(self) -> None:
        phony_line = next(
            line for line in MAKEFILE.splitlines() if line.startswith(".PHONY:")
        )
        phony_targets = phony_line.removeprefix(".PHONY:").split()
        for target in (
            "test",
            "check",
            "test-patch-images",
            "test-obscura-image",
            "test-tor-image",
            "test-opensearch-image",
            "test-all-images",
            "check-upgrade",
            "integration-opensearch",
            "integration-opensearch-restart",
            "integration-opensearch-onyx",
        ):
            self.assertRegex(MAKEFILE, rf"(?m)^{re.escape(target)}:")
            self.assertIn(target, phony_targets)

        self.assertIn("./tests/validate_pinned_patch_images.sh", MAKEFILE)
        self.assertIn("$(MAKE) --no-print-directory check", MAKEFILE)
        self.assertIn("$(MAKE) --no-print-directory test-patch-images", MAKEFILE)
        self.assertIn("$(MAKE) --no-print-directory test-obscura-image", MAKEFILE)
        self.assertIn("$(MAKE) --no-print-directory test-tor-image", MAKEFILE)
        self.assertIn("$(MAKE) --no-print-directory test-opensearch-image", MAKEFILE)
        self.assertIn("$(MAKE) --no-print-directory test-all-images", MAKEFILE)
        self.assertNotRegex(MAKEFILE, r"(?m)^test-images:")
        self.assertRegex(
            MAKEFILE,
            r"(?m)^test-all-images:\n"
            r"\t@\$\(MAKE\) --no-print-directory test-patch-images\n"
            r"\t@\$\(MAKE\) --no-print-directory test-obscura-image\n"
            r"\t@\$\(MAKE\) --no-print-directory test-tor-image\n"
            r"\t@\$\(MAKE\) --no-print-directory test-opensearch-image$",
        )
        self.assertRegex(
            MAKEFILE,
            r"(?m)^check-upgrade:\n"
            r"\t@\$\(MAKE\) --no-print-directory check\n"
            r"\t@\$\(MAKE\) --no-print-directory test-all-images$",
        )
        self.assertIn('tests/run_opensearch_image_validation.py', MAKEFILE)
        self.assertIn('tests/validate_obscura_image.py', MAKEFILE)
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
        self.assertIn('f"memlock={args.memlock}"', image)
        self.assertIn('--memlock "$(OPENSEARCH_VALIDATION_MEMLOCK)"', MAKEFILE)

    def test_image_validation_is_executable_and_does_not_fetch_images(self) -> None:
        self.assertTrue(os.access(IMAGE_SCRIPT_PATH, os.X_OK))
        self.assertIn('image inspect "$image"', IMAGE_SCRIPT)
        self.assertNotRegex(IMAGE_SCRIPT, r'(?m)^.*"\$container_bin" (pull|build)\b')
        self.assertEqual(IMAGE_SCRIPT.count("--network none"), 12)
        self.assertIn("WRAPPER_PATCH_STRICT=true", IMAGE_SCRIPT)
        for setting in (
            "ENABLE_CRAFT=false",
            "IDP_PROFILE_ENRICHMENT_ENABLED=false",
            "LICENSE_ENFORCEMENT_ENABLED=false",
            "ENABLE_PAID_ENTERPRISE_EDITION_FEATURES=false",
        ):
            self.assertIn(setting, IMAGE_SCRIPT)
        self.assertIn(
            "PYTHONPATH=/api-patches:/wrapper:/obscura-client:/app",
            IMAGE_SCRIPT,
        )
        self.assertIn(
            "onyx/patches/sitecustomize_api_server:/api-patches:ro",
            IMAGE_SCRIPT,
        )
        self.assertIn(
            'sitecustomize.__file__ == "/api-patches/sitecustomize.py"',
            PINNED_API_VALIDATOR,
        )
        self.assertNotIn("def _install_wrapper_patches", PINNED_API_VALIDATOR)
        self.assertIn("PINNED_WEBUI_STREAMING_CONTRACT_OK", IMAGE_SCRIPT)
        for marker in (
            "/resume-stream?cursor=",
            "chat_heartbeat",
            "message_start",
            "message_delta",
            "reasoning_start",
            "reasoning_done",
            "stop_reason",
            "Server did not honor the incognito request",
        ):
            self.assertIn(marker, IMAGE_SCRIPT)
        self.assertIn("PINNED_URL_IDENTITY_PRESERVATION_OK", IMAGE_SCRIPT)
        self.assertIn("PINNED_STOCK_CRAWLER_PATCH_CONTRACT_OK", IMAGE_SCRIPT)
        self.assertIn("PINNED_OBSCURA_CRAWLER_PATCH_CONTRACT_OK", IMAGE_SCRIPT)
        self.assertIn("PINNED_OPEN_URL_LIMIT_CONTRACT_OK", IMAGE_SCRIPT)
        self.assertIn("PINNED_EXECUTOR_SYMPY_OK", IMAGE_SCRIPT)
        self.assertIn(
            "validate_code_interpreter_executor_network.py", IMAGE_SCRIPT
        )
        self.assertIn(
            "PINNED_EXECUTOR_NATIVE_NETWORK_CONTRACT_OK",
            EXECUTOR_NETWORK_VALIDATOR,
        )
        self.assertIn("/validation/validate_pinned_background.py", IMAGE_SCRIPT)
        self.assertNotIn("effective={t['name']", IMAGE_SCRIPT)
        self.assertIn("tests.test_searxng_obscura_engines", IMAGE_SCRIPT)

    def test_podman_image_validation_skips_unsupported_executor(self) -> None:
        self.assertIn('*podman*) validate_code_interpreter=false', IMAGE_SCRIPT)
        self.assertIn('if [ "$validate_code_interpreter" = true ]', IMAGE_SCRIPT)
        self.assertIn("supported Podman model omits", IMAGE_SCRIPT)

if __name__ == "__main__":
    unittest.main()
