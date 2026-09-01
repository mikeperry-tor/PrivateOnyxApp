from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OnyxPrivacyConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compose = (ROOT / "docker-compose.yaml").read_text()
        cls.full = (ROOT / "compose_overlays/docker-compose.full.yml").read_text()
        cls.csp = (ROOT / "onyx/nginx/webui-csp.conf").read_text()

    def test_backend_automatic_reporting_and_fetches_are_disabled(self) -> None:
        for compose in (self.compose, self.full):
            for setting in (
                'DISABLE_TELEMETRY: "true"',
                'AUTO_LLM_CONFIG_URL: ""',
                'DISPOSABLE_EMAIL_DOMAINS_URL: ""',
                'LITELLM_LOCAL_MODEL_COST_MAP: "true"',
                'SENTRY_DSN: ""',
                'BRAINTRUST_API_KEY: ""',
                'LANGFUSE_SECRET_KEY: ""',
                'LANGFUSE_PUBLIC_KEY: ""',
                'IDP_PROFILE_ENRICHMENT_ENABLED: "false"',
                'DOCUMENT_PUSH_ENDPOINT_URL: ""',
                'DOCUMENT_PUSH_API_KEY: ""',
                'LICENSE_ENFORCEMENT_ENABLED: "false"',
                'ENABLE_PAID_ENTERPRISE_EDITION_FEATURES: "false"',
                'ENABLE_CRAFT: "false"',
                'HUBSPOT_TRACKING_URL: ""',
                'POSTHOG_API_KEY: ""',
                'MARKETING_POSTHOG_API_KEY: ""',
            ):
                self.assertIn(setting, compose)

    def test_current_recaptcha_settings_are_explicitly_disabled(self) -> None:
        for compose in (self.compose, self.full):
            for setting in (
                'CAPTCHA_ENABLED: "false"',
                'RECAPTCHA_ENTERPRISE_PROJECT_ID: ""',
                'RECAPTCHA_ENTERPRISE_API_KEY: ""',
                'RECAPTCHA_SITE_KEY: ""',
                'RECAPTCHA_HOSTNAME_ALLOWLIST: ""',
            ):
                self.assertIn(setting, compose)

    def test_webui_csp_restricts_browser_egress_and_script_attributes(self) -> None:
        self.assertIn("script-src 'self' 'unsafe-inline'", self.csp)
        self.assertIn("script-src-attr 'none'", self.csp)
        self.assertIn("connect-src 'self' blob:", self.csp)
        self.assertIn("frame-src 'self' blob:", self.csp)
        self.assertIn("img-src 'self' blob: data:", self.csp)
        self.assertNotIn("'strict-dynamic'", self.csp)
        self.assertNotIn("'unsafe-eval'", self.csp)
        self.assertNotIn("http:", self.csp)
        self.assertNotIn("https:", self.csp)
        self.assertIn(
            "./onyx/nginx/webui-csp.conf:/etc/nginx/conf.d/webui-csp.conf:ro",
            self.compose,
        )

    def test_upstream_and_wrapper_strict_csp_are_both_enabled(self) -> None:
        self.assertIn('WEB_STRICT_CSP_ENABLED: "true"', self.compose)
        self.assertIn(
            "./onyx/nginx/webui-csp.conf:/etc/nginx/conf.d/webui-csp.conf:ro",
            self.compose,
        )

    def test_reconnect_settings_are_internal_and_same_origin_only(self) -> None:
        example = (ROOT / ".env.wrapper.example").read_text(encoding="utf-8")
        for name in (
            "CHAT_STREAM_BUFFER_TTL_S",
            "CHAT_STREAM_BUFFER_DONE_TTL_S",
            "CHAT_STREAM_BUFFER_MAX_BYTES",
        ):
            self.assertNotIn(name, example)
        self.assertIn("script-src 'self'", self.csp)
        self.assertIn("connect-src 'self'", self.csp)
        self.assertNotIn("data:; script-src", self.csp)
        for mount in (
            "webui-reconnect-http.conf:/etc/nginx/conf.d/webui-reconnect-http.conf:ro",
            "webui-reconnect-server.inc:/etc/nginx/wrapper/webui-reconnect-server.inc:ro",
            "webui-reconnect.js:/usr/share/private-onyx/webui-reconnect.js:ro",
            "run-nginx-wrapper.sh:/usr/local/bin/run-nginx-wrapper.sh:ro",
        ):
            self.assertIn(mount, self.compose)


if __name__ == "__main__":
    unittest.main()
