from __future__ import annotations

import os
import re
import subprocess
import tempfile
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
STACK_VERSIONS = (ROOT / "stack.versions.env").read_text()
COMPOSE = (ROOT / "docker-compose.yaml").read_text()


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
            "test-security-images",
            "test-obscura-image",
            "test-tor-image",
            "test-opensearch-image",
            "test-all-images",
            "check-upgrade",
            "integration-chat-stream-cache-lite",
            "integration-chat-stream-cache-full",
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
            r"\t@\$\(MAKE\) --no-print-directory test-opensearch-image\n"
            r"\t@\$\(MAKE\) --no-print-directory test-security-images$",
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
        self.assertEqual(
            MAKEFILE.count("tests/validate_chat_stream_cache_backend.py"), 2
        )
        self.assertRegex(
            MAKEFILE,
            r"(?m)^integration-chat-stream-cache-lite:\n"
            r"\t@COMPOSE_FILE=\$\(LITE_FILES\).*\n"
            r"\t\tpython - postgres < tests/validate_chat_stream_cache_backend.py$",
        )
        self.assertRegex(
            MAKEFILE,
            r"(?m)^integration-chat-stream-cache-full:\n"
            r"\t@COMPOSE_FILE=\$\(FULL_FILES\).*\n"
            r"\t\tpython - redis < tests/validate_chat_stream_cache_backend.py$",
        )

    def test_upgrade_builds_use_refreshed_locks_and_preserve_explicit_images(self) -> None:
        rule = re.search(r"(?m)^upgrade:.*(?:\n\t.*)+", MAKEFILE).group(0)
        targets = ("myst-build teep-build searxng-build executor-build "
                   "code-interpreter-build tor-build tailscale-build "
                   "obscura-image-ready upgrade-onyx")
        images = ("SEARXNG_WRAPPER_IMAGE", "PYTHON_EXECUTOR_IMAGE")
        for explicit in (False, True):
            with self.subTest(explicit=explicit), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "lock").write_text("old")
                definitions = ""
                for image in images:
                    definitions += (
                        f"{image}_ORIGIN := $(origin {image})\n"
                        f"{image} ?= fixture:$(shell cat lock)\n"
                        f"{image} := $({image})\nexport {image}\n"
                    )
                (root / "Makefile").write_text(
                    definitions + rule + "\n"
                    "upgrade-python-deps:\n\t@echo refreshed > lock\n"
                    + targets + ":\n\t@echo $(SEARXNG_WRAPPER_IMAGE) $(PYTHON_EXECUTOR_IMAGE) >> images\n"
                )
                environment = dict(os.environ)
                for image in images:
                    environment.pop(image, None)
                    if explicit:
                        environment[image] = "fixture:explicit"
                result = subprocess.run(["make", "upgrade"], cwd=root, env=environment,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = "fixture:explicit" if explicit else "fixture:refreshed"
                self.assertEqual((root / "images").read_text().splitlines(),
                                 [f"{expected} {expected}"] * len(targets.split()))

    def test_onyx_download_failure_cannot_keep_a_stale_compose_layer(self) -> None:
        rule = re.search(r"(?m)^upgrade-onyx:.*(?:\n\t.*)+", MAKEFILE).group(0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deployment = root / "onyx/onyx_data/deployment"
            deployment.mkdir(parents=True)
            craft = deployment / "docker-compose.craft.yml"
            craft.write_text("stale fixture")
            (root / "runtime.env").touch()
            (root / "Makefile").write_text(
                "ONYX_CONFIG_REF=fixture\nONYX_ENV_FILE=runtime.env\n" + rule
                + "\nsync-onyx-env:\n\t@touch synced\n"
            )
            curl = root / "curl"
            curl.write_text(
                "#!/bin/sh\nset -eu\n"
                'case "$2" in *docker-compose.craft.yml) exit 22;; esac\n'
                'printf fresh > "$4"\n'
            )
            curl.chmod(0o755)
            result = subprocess.run(["make", "upgrade-onyx"], cwd=root,
                                    env=dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"]),
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(craft.read_text(), "stale fixture")
            self.assertFalse((deployment / "docker-compose.yml").exists())
            self.assertFalse((root / "synced").exists())

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
        self.assertEqual(IMAGE_SCRIPT.count("--network none"), IMAGE_SCRIPT.count('"$container_bin" run --rm'))
        self.assertIn("WRAPPER_PATCH_STRICT=true", IMAGE_SCRIPT)
        for setting in (
            "ENABLE_CRAFT=false",
            "IDP_PROFILE_ENRICHMENT_ENABLED=false",
            "LICENSE_ENFORCEMENT_ENABLED=false",
            "ENABLE_PAID_ENTERPRISE_EDITION_FEATURES=false",
        ):
            self.assertIn(setting, IMAGE_SCRIPT)
        self.assertIn(
            'PYTHONPATH="${validation_pythonpath:-/api-patches:/obscura-client:/app}"',
            IMAGE_SCRIPT,
        )
        self.assertIn(
            "onyx/patches/sitecustomize_api_server}:/api-patches:ro",
            IMAGE_SCRIPT,
        )
        self.assertIn(
            'sitecustomize.__file__ == "/api-patches/sitecustomize.py"',
            PINNED_API_VALIDATOR,
        )
        self.assertNotIn("def _install_wrapper_patches", PINNED_API_VALIDATOR)
        self.assertIn("PINNED_WEBUI_STREAMING_CONTRACT_OK", IMAGE_SCRIPT)
        self.assertIn("tests/validate_pinned_webui_settings.js", IMAGE_SCRIPT)
        self.assertIn('require("/validate-settings.js")', IMAGE_SCRIPT)
        self.assertIn("tests/webui_reconnect_harness.js", IMAGE_SCRIPT)
        self.assertIn("tests/validate_nginx_reconnect_image.py", IMAGE_SCRIPT)
        self.assertIn("NGINX_IMAGE ?= $(call env_value,NGINX_IMAGE)", MAKEFILE)
        self.assertIn(
            "NGINX_IMAGE=docker.io/library/nginx:1.25.5-alpine",
            STACK_VERSIONS,
        )
        self.assertIn("image: ${NGINX_IMAGE:?NGINX_IMAGE must be set}", COMPOSE)
        self.assertIn("PINNED_NATIVE_CODING_AGENT_REPO_LIMIT_OK", IMAGE_SCRIPT)
        self.assertIn(
            "CODING_AGENT_GITHUB_MAX_REPO_BYTES == 500 * 1024 * 1024",
            IMAGE_SCRIPT,
        )
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
        self.assertIn("PINNED_EXECUTOR_PACKAGES_OK", IMAGE_SCRIPT)
        for package_probe in (
            "import sympy",
            "from reportlab.graphics import renderPDF",
            "from svglib.svglib import svg2rlg",
        ):
            self.assertIn(package_probe, IMAGE_SCRIPT)
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
