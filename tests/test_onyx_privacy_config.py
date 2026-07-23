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
                'SENTRY_DSN: ""',
                'BRAINTRUST_API_KEY: ""',
                'LANGFUSE_SECRET_KEY: ""',
                'LANGFUSE_PUBLIC_KEY: ""',
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
        self.assertIn("connect-src 'self'", self.csp)
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

    def test_dead_strict_csp_switch_is_not_relied_on(self) -> None:
        self.assertNotIn("WEB_STRICT_CSP_ENABLED", self.compose)


if __name__ == "__main__":
    unittest.main()
