from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ObscuraDirectComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = (ROOT / "docker-compose.yaml").read_text()
        cls.lite = (ROOT / "docker-compose.lite.yml").read_text()
        cls.manifest = (ROOT / "stack.versions.env").read_text()
        cls.no_proxy = (ROOT / "onyx/helper-egress.env").read_text()
        cls.searxng_dockerfile = (ROOT / "searxng/Dockerfile").read_text()
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
        self.assertIn('- "--workers"\n      - "5"', self.compose)
        self.assertIn('OBSCURA_NAV_TIMEOUT_MS: "90000"', self.compose)
        self.assertIn('OBSCURA_NETWORK_BODY_BUFFER_ENTRIES: "16"', self.compose)
        self.assertNotIn("--storage-dir", self.compose)
        self.assertNotIn("--allow-file-access", self.compose)

    def test_new_manifest_and_no_proxy_names(self):
        self.assertIn("SEARXNG_WRAPPER_IMAGE=", self.manifest)
        self.assertNotIn("CRW_IMAGE=", self.manifest)
        self.assertNotIn("CDP_SHIM_IMAGE=", self.manifest)
        self.assertIn("obscura-cdp-gateway", self.no_proxy)
        self.assertNotIn("crw-service-gateway", self.no_proxy)

    def test_api_bootstrap_is_service_named(self):
        self.assertIn("sitecustomize_api_server", self.compose)
        self.assertIn("sitecustomize_api_server", self.lite)
        self.assertNotIn("./onyx/patches/sitecustomize:/app/wrapper-patches", self.lite)

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

    def test_searxng_pythonpath_exposes_patch_app_and_shared_client(self):
        self.assertIn(
            'PYTHONPATH: "/usr/local/searxng/wrapper-patches:'
            '/usr/local/searxng:/usr/local/lib"',
            self.searxng_compose,
        )
        self.assertNotIn("${PYTHONPATH:-}", self.searxng_compose)


if __name__ == "__main__":
    unittest.main()
