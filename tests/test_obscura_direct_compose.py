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
            ROOT / "browser/obscura_image/fetch_source.py"
        ).read_text()
        cls.obscura_patch_series = (
            ROOT / "browser/obscura_image/patches/series"
        ).read_text().splitlines()
        cls.obscura_patches = {
            name: (ROOT / "browser/obscura_image/patches" / name).read_text()
            for name in cls.obscura_patch_series
        }
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

    def test_manifest_pins_obscura_0_2_0(self):
        self.assertIn(
            "OBSCURA_RELEASE_VERSION=0.2.0",
            self.manifest,
        )
        self.assertIn(
            "OBSCURA_UPSTREAM_IMAGE=docker.io/h4ckf0r0day/obscura:0.2.0"
            "@sha256:78c99ac89d010d444d96d85c183a2db912c41f807b7807d697df98ab7e4bd3c2",
            self.manifest,
        )
        self.assertIn(
            "OBSCURA_WRAPPER_IMAGE_REPOSITORY=local/private-onyx-obscura",
            self.manifest,
        )
        self.assertIn(
            "OBSCURA_SOURCE_REF=97124edeb2ea610615e78f43e097454e3b221f6b",
            self.manifest,
        )
        self.assertIn(
            "OBSCURA_SOURCE_SHA256="
            "e9fa0387f51afc6f33a0e16b0aa31c2da071151fd70cea83583b176e6d0f79bc",
            self.manifest,
        )
        self.assertNotIn("\nOBSCURA_IMAGE=", self.manifest)
        self.assertIn(
            "OBSCURA_RELEASE_VERSION)-stealth-$(OBSCURA_WRAPPER_SOURCE_HASH)",
            self.makefile,
        )

    def test_obscura_image_uses_verified_pinned_source_and_patchset(self):
        for digest_name in ("OBSCURA_SOURCE_SHA256",):
            match = re.search(
                rf"^{digest_name}=([0-9a-f]{{64}})$",
                self.manifest,
                re.MULTILINE,
            )
            self.assertIsNotNone(match)
        self.assertIsNotNone(
            re.search(r"^OBSCURA_SOURCE_REF=[0-9a-f]{40}$", self.manifest, re.MULTILINE)
        )
        self.assertIsNotNone(
            re.search(
                r"^OBSCURA_BUILDER_IMAGE=.+@sha256:[0-9a-f]{64}$",
                self.manifest,
                re.MULTILINE,
            )
        )
        self.assertIn("fetch_source.py", self.obscura_dockerfile)
        self.assertIn(
            "RUN set -eu; \\\n    python3 /wrapper/fetch_source.py",
            self.obscura_dockerfile,
        )
        self.assertIn(
            "ARG OBSCURA_UPSTREAM_IMAGE="
            "docker.io/h4ckf0r0day/obscura:0.2.0"
            "@sha256:78c99ac89d010d444d96d85c183a2db912c41f807b7807d697df98ab7e4bd3c2",
            self.obscura_dockerfile,
        )
        self.assertIn("obscura/archive/{ref}.tar.gz", self.obscura_fetcher)
        self.assertIn("actual_digest != expected_digest", self.obscura_fetcher)
        self.assertIn("member.isdir() or member.isfile()", self.obscura_fetcher)
        self.assertIn(
            "browser/obscura_image/fetch_source.py",
            self.makefile,
        )
        self.assertEqual(
            self.obscura_patch_series,
            [
                "0001-stealth-native-post.patch",
                "0002-target-fingerprint-seed.patch",
                "0003-search-runtime-compatibility.patch",
            ],
        )
        self.assertIn("cargo build --release --locked --features stealth", self.obscura_dockerfile)
        self.assertNotIn("--features render", self.obscura_dockerfile)
        self.assertNotIn("--features render,stealth", self.obscura_dockerfile)
        self.assertIn("--bin obscura --bin obscura-worker", self.obscura_dockerfile)
        self.assertNotIn("fetch_release.py", self.makefile + self.obscura_dockerfile)
        self.assertNotIn("OBSCURA_RELEASE_AMD64_SHA256", self.manifest + self.makefile)
        self.assertIn("obscura-build:", self.makefile)
        native_post = self.obscura_patches["0001-stealth-native-post.patch"]
        self.assertIn("navigate_with_profile", native_post)
        self.assertIn("post_form_with_callbacks", native_post)
        self.assertIn("response.url.as_str()", native_post)
        self.assertIn("redirected_navigation_method", native_post)
        fingerprint = self.obscura_patches["0002-target-fingerprint-seed.patch"]
        self.assertIn("__obscura_registerLinkedStylesheet", fingerprint)
        self.assertIn("set_fingerprint_seed(self.fingerprint_seed)", fingerprint)
        compatibility = self.obscura_patches[
            "0003-search-runtime-compatibility.patch"
        ]
        self.assertIn("PerformanceNavigationTiming", compatibility)
        self.assertIn("SVGAElement", compatibility)
        self.assertIn("nomodule", compatibility)
        self.assertIn(
            "private_onyx_module_capable_runtime_skips_parser_nomodule",
            compatibility,
        )
        self.assertIn(
            "private_onyx_module_capable_runtime_skips_dynamic_nomodule",
            compatibility,
        )
        self.assertIn("_windowNamedPropertyNames.delete(name)", compatibility)
        self.assertIn("private_onyx_window_named_property_assignment", compatibility)
        self.assertIn('OBSCURA_MODULE_BUDGET_MS: "10000"', self.compose)

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

    def test_searxng_provider_capacity_requires_one_request_worker(self):
        self.assertIn('GRANIAN_WORKERS: "1"', self.compose)

    def test_timed_typing_setting_is_passed_without_compose_parsing(self):
        expected = (
            'SEARXNG_TIMED_TYPING_PROVIDERS: '
            '"${SEARXNG_TIMED_TYPING_PROVIDERS:-none}"'
        )
        self.assertIn(expected, self.compose)
        self.assertIn(expected, self.searxng_compose)
        for forbidden in (
            "OBSCURA_ROTATE_PROFILE",
            "OBSCURA_PROFILE",
            "FINGERPRINT_SEED",
            "POOL_IDLE_TIMEOUT",
        ):
            self.assertNotIn(forbidden, self.compose)


if __name__ == "__main__":
    unittest.main()
