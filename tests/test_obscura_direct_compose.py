from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ObscuraDirectComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = (ROOT / "docker-compose.yaml").read_text()
        cls.full = (ROOT / "compose_overlays/docker-compose.full.yml").read_text()
        cls.lite = (ROOT / "compose_overlays/docker-compose.lite.yml").read_text()
        cls.manifest = (ROOT / "stack.versions.env").read_text()
        cls.no_proxy = (ROOT / "onyx/helper-egress.env").read_text()
        cls.searxng_dockerfile = (ROOT / "searxng/Dockerfile").read_text()
        cls.obscura_dockerfile = (
            ROOT / "browser/obscura_image/Dockerfile"
        ).read_text()
        cls.obscura_fetcher = (
            ROOT / "browser/obscura_image/fetch_release.py"
        ).read_text()
        cls.searxng_compose = (ROOT / "searxng/docker-compose.yml").read_text()
        cls.makefile = (ROOT / "Makefile").read_text()

    def test_direct_topology_literals(self):
        self.assertIn(
            'ONYX_AGENT_USE_OBSCURA_BROWSER: "${ONYX_AGENT_USE_OBSCURA_BROWSER:-false}"',
            self.compose,
        )
        self.assertIn('ONYX_OBSCURA_CDP_URL: "ws://obscura-cdp-gateway:9222/devtools/browser"', self.compose)
        self.assertIn('SEARXNG_OBSCURA_CDP_URL: "ws://obscura:9222/devtools/browser"', self.compose)
        self.assertIn('networks: [onyx-obscura-control, obscura-control]', self.compose)
        self.assertIn('[searxng-api, obscura-control]', self.compose)

    def test_obscura_is_bounded_and_hardened(self):
        self.assertIn('user: "65534:65534"', self.compose)
        self.assertIn('- "--max-connections"\n      - "15"', self.compose)
        self.assertNotIn("--workers", self.compose)
        self.assertIn('OBSCURA_NAV_TIMEOUT_MS: "90000"', self.compose)
        self.assertIn('OBSCURA_NETWORK_BODY_BUFFER_ENTRIES: "16"', self.compose)
        self.assertIn('OBSCURA_IO_STREAM_MAX_ENTRIES: "1"', self.compose)
        self.assertNotIn("--storage-dir", self.compose)
        self.assertNotIn("--allow-file-access", self.compose)

    def test_manifest_pins_obscura_0_1_11(self):
        self.assertIn(
            "OBSCURA_RELEASE_VERSION=0.1.11",
            self.manifest,
        )
        self.assertIn(
            "OBSCURA_UPSTREAM_IMAGE=docker.io/h4ckf0r0day/obscura:0.1.11"
            "@sha256:e5fd7b8032a5fedc6e8e27f9d4f2e35327ea08b2548ab1372775974add171547",
            self.manifest,
        )
        self.assertIn(
            "OBSCURA_WRAPPER_IMAGE_REPOSITORY=local/private-onyx-obscura",
            self.manifest,
        )
        self.assertNotIn("\nOBSCURA_IMAGE=", self.manifest)
        self.assertIn(
            "OBSCURA_RELEASE_VERSION)-stealth-$(OBSCURA_WRAPPER_SOURCE_HASH)",
            self.makefile,
        )

    def test_obscura_image_uses_verified_multi_arch_stealth_release(self):
        for digest_name in (
            "OBSCURA_RELEASE_AMD64_SHA256",
            "OBSCURA_RELEASE_ARM64_SHA256",
        ):
            match = re.search(
                rf"^{digest_name}=([0-9a-f]{{64}})$",
                self.manifest,
                re.MULTILINE,
            )
            self.assertIsNotNone(match)
        self.assertIn("fetch_release.py", self.obscura_dockerfile)
        self.assertIn(
            "ARG OBSCURA_UPSTREAM_IMAGE="
            "docker.io/h4ckf0r0day/obscura:0.1.11"
            "@sha256:e5fd7b8032a5fedc6e8e27f9d4f2e35327ea08b2548ab1372775974add171547",
            self.obscura_dockerfile,
        )
        self.assertIn("linux-stealth.tar.gz", self.obscura_fetcher)
        self.assertIn("actual_digest != expected_digest", self.obscura_fetcher)
        self.assertIn("set(members) != set(EXPECTED_FILES)", self.obscura_fetcher)
        self.assertIn(
            "browser/obscura_image/fetch_release.py",
            self.makefile,
        )
        self.assertIn("obscura-build:", self.makefile)

    def test_new_manifest_and_no_proxy_names(self):
        self.assertIn("SEARXNG_WRAPPER_IMAGE_REPOSITORY=", self.manifest)
        self.assertNotIn("SEARXNG_WRAPPER_IMAGE=", self.manifest)
        self.assertNotIn("CRW_IMAGE=", self.manifest)
        self.assertNotIn("CDP_SHIM_IMAGE=", self.manifest)
        self.assertIn("obscura-cdp-gateway", self.no_proxy)
        self.assertNotIn("crw-service-gateway", self.no_proxy)

    def test_api_bootstrap_is_service_named(self):
        self.assertIn("sitecustomize_api_server", self.compose)
        self.assertIn("sitecustomize_api_server", self.lite)
        self.assertNotIn("./onyx/patches/sitecustomize:/app/wrapper-patches", self.lite)

    def test_unsupported_craft_backend_is_explicitly_disabled(self):
        self.assertIn('ENABLE_CRAFT: "false"', self.compose)
        self.assertIn('ENABLE_CRAFT: "false"', self.full)
        background_patch = (
            ROOT / "onyx/patches/sitecustomize_background/sitecustomize.py"
        ).read_text()
        self.assertIn("_apply_sleepy_background_patch()", background_patch)
        for name in (
            "cleanup-idle-sandboxes",
            "dispatch-due-scheduled-tasks",
            "cleanup-stuck-scheduled-runs",
        ):
            self.assertIn(name, background_patch)

    def test_searxng_build_consumes_generated_dependency_lock(self):
        image_tag = re.search(
            r"^SEARXNG_IMAGE_TAG=(.+)$", self.manifest, re.MULTILINE
        )
        self.assertIsNotNone(image_tag)
        self.assertIn(
            f"ARG SEARXNG_UPSTREAM_IMAGE=docker.io/searxng/searxng:{image_tag.group(1)}",
            self.searxng_dockerfile,
        )
        self.assertIn(
            "COPY searxng/requirements.txt /tmp/requirements.txt",
            self.searxng_dockerfile,
        )
        self.assertNotIn("runtime-requirements.txt", self.searxng_dockerfile)
        self.assertNotIn("playwright-client", self.searxng_dockerfile)
        self.assertNotIn(
            '--build-arg ONYX_BACKEND_IMAGE="$(ONYX_BACKEND_IMAGE)"',
            self.makefile,
        )

    def test_searxng_wrapper_tag_tracks_every_embedded_input(self):
        self.assertIn("SEARXNG_WRAPPER_BUILD_INPUTS :=", self.makefile)
        self.assertIn(
            "browser/obscura_client/private_onyx_obscura/*.py", self.makefile
        )
        self.assertIn("searxng/engines/*.py", self.makefile)
        self.assertIn("searxng/requirements.txt", self.makefile)
        self.assertIn(
            "$(SEARXNG_WRAPPER_IMAGE_REPOSITORY):$(SEARXNG_IMAGE_TAG)-$(SEARXNG_WRAPPER_SOURCE_HASH)",
            self.makefile,
        )

    def test_searxng_pythonpath_exposes_patch_app_and_shared_client(self):
        self.assertIn(
            'PYTHONPATH: "/usr/local/searxng/wrapper-patches:'
            '/usr/local/searxng:/usr/local/lib"',
            self.searxng_compose,
        )
        self.assertNotIn("${PYTHONPATH:-}", self.searxng_compose)


if __name__ == "__main__":
    unittest.main()
