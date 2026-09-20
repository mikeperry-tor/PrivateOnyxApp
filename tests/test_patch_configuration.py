from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import sys
import threading
from unittest.mock import patch

from patch_test_support import FreshPatchTestCase, load_patch
import test_helper_and_inference_proxy_patches as proxy_fixtures

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "browser/obscura_client"))


class PatchConfigurationTests(FreshPatchTestCase):
    def test_helper_callers_share_canonical_acceptance(self):
        common = load_patch("common.config")
        github = load_patch("api.github_egress_patch")
        stock = load_patch("api.onyx_crawler_egress_patch")
        playwright = load_patch("shared.playwright_proxy")
        canonical = "http://onyx-public-egress-bridge:3128"
        table = [(canonical, True), (" \t" + canonical + "\n", True)] + [
            (value, False) for value in (
                None, "", " ", canonical + "/", "HTTP://ONYX-PUBLIC-EGRESS-BRIDGE:3128",
                "http://onyx-public-egress-bridge:03128", "http://onyx-public-egress-bridge:bad",
                "http://user:pass@onyx-public-egress-bridge:3128",
                "http://onyx-host-egress-bridge:3128",
            )
        ]
        fixture = proxy_fixtures.PlaywrightHelperProxyTests()
        for value, accepted in table:
            for strict in (True, False):
                with self.subTest(value=value, strict=strict):
                    env = {"WRAPPER_PATCH_STRICT": str(strict).lower()}
                    if value is not None:
                        env["ONYX_HELPER_HTTP_PROXY_URL"] = value
                    with patch.dict(os.environ, env, clear=True):
                        for validate in (github._validate_proxy, stock._validate_proxy, lambda: common._validated_fixed_proxy_url("ONYX_HELPER_HTTP_PROXY_URL", "onyx-public-egress-bridge")):
                            if accepted:
                                validate()
                            else:
                                with self.assertRaises(RuntimeError):
                                    validate()
                        modules, owner, launches = fixture._fake_modules()
                        output = StringIO()
                        with patch.dict(sys.modules, modules), redirect_stdout(output):
                            if not accepted and strict:
                                with self.assertRaises(RuntimeError):
                                    playwright.apply_playwright_helper_proxy_patch()
                            else:
                                playwright.apply_playwright_helper_proxy_patch()
                        self.assertEqual(bool(getattr(owner, "_wrapper_helper_proxy_patched", False)), accepted)
                        if not accepted:
                            self.assertIn("WARNING", output.getvalue())
                        else:
                            owner.sync_playwright().start().chromium.launch()
                            self.assertEqual(launches[-1]["proxy"]["server"], canonical)

    def test_context_selection_restores_and_isolates_concurrent_calls(self):
        module = load_patch("shared.playwright_proxy")
        public = "http://onyx-public-egress-bridge:3128"
        host = "http://onyx-host-egress-bridge:3128"
        with module.select_playwright_proxy(public):
            for invalid in ("", host + "/", " " + public):
                with self.assertRaises(RuntimeError):
                    with module.select_playwright_proxy(invalid):
                        self.fail("invalid context entered")
                self.assertEqual(module._PLAYWRIGHT_PROXY_OVERRIDE.get(), public)
            with self.assertRaises(ValueError):
                with module.select_playwright_proxy(host):
                    raise ValueError("fixture")
            self.assertEqual(module._PLAYWRIGHT_PROXY_OVERRIDE.get(), public)
        self.assertIsNone(module._PLAYWRIGHT_PROXY_OVERRIDE.get())
        barrier = threading.Barrier(2)

        def run(value):
            with module.select_playwright_proxy(value):
                barrier.wait(timeout=5)
                self.assertEqual(module._PLAYWRIGHT_PROXY_OVERRIDE.get(), value)
            self.assertIsNone(module._PLAYWRIGHT_PROXY_OVERRIDE.get())

        with ThreadPoolExecutor(2) as executor:
            list(executor.map(run, (public, host)))

    def test_http_configuration_is_strict_and_whitespace_normalized(self):
        config = load_patch("api.config")
        for raw, expected in [("  TrUe ", True), ("1", True), ("yes", True), ("on", True), (" FALSE ", False), ("0", False), ("no", False), ("off", False)]:
            with patch.dict(os.environ, {"EGRESS_ALLOW_HTTP_URLS": raw}, clear=True):
                self.assertEqual(config.allow_http(), expected)
        for name, parser, invalid in (
            ("EGRESS_ALLOW_HTTP_URLS", config.allow_http, ("", "invalid", "2")),
            ("EGRESS_ALLOW_HTTP_ONION_URLS", config.allow_http_onion, ("", "yes", "1")),
        ):
            for raw in invalid:
                with patch.dict(os.environ, {name: raw}, clear=True):
                    with self.assertRaises(RuntimeError):
                        parser()
