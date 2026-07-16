from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "onyx/patches/sitecustomize_api_server/onyx_crawler_egress_patch.py"
)
CLIENT_PATH = ROOT / "browser/obscura_client"


def _load_module():
    import sys

    sys.path.insert(0, str(CLIENT_PATH))
    try:
        spec = importlib.util.spec_from_file_location(
            "onyx_crawler_egress_patch", MODULE_PATH
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(CLIENT_PATH))


class FakeResponse:
    def __init__(self, status: int = 200, location: str | None = None):
        self.status_code = status
        self.headers = {} if location is None else {"Location": location}
        self.closed = False

    @property
    def is_redirect(self):
        return 300 <= self.status_code < 400 and "Location" in self.headers

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.trust_env = True
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)

    def close(self):
        self.closed = True


class CrawlerEgressPatchTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self.env = {
            "ONYX_HELPER_HTTP_PROXY_URL": self.module.PUBLIC_PROXY_URL,
            "EGRESS_ALLOW_HTTP_URLS": "false",
        }

    def test_mode_defaults_false_and_is_strict(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(self.module.use_obscura_browser())
        for raw, expected in (("true", True), ("false", False)):
            with patch.dict(
                os.environ, {"ONYX_AGENT_USE_OBSCURA_BROWSER": raw}, clear=True
            ):
                self.assertEqual(self.module.use_obscura_browser(), expected)
        with patch.dict(
            os.environ, {"ONYX_AGENT_USE_OBSCURA_BROWSER": "yes"}, clear=True
        ):
            with self.assertRaisesRegex(RuntimeError, "exactly true or false"):
                self.module.use_obscura_browser()

    def test_request_uses_fixed_proxy_without_environment_or_local_dns(self):
        response = FakeResponse()
        session = FakeSession([response])
        with patch.dict(os.environ, self.env, clear=True):
            result = self.module._proxied_get(
                "https://example.com/a#fragment",
                _session_factory=lambda: session,
                _ssrf_exception_type=ValueError,
            )
        self.assertIs(result, response)
        self.assertFalse(session.trust_env)
        self.assertEqual(session.calls[0][0], "https://example.com/a")
        self.assertEqual(
            session.calls[0][1]["proxies"],
            {
                "http": self.module.PUBLIC_PROXY_URL,
                "https": self.module.PUBLIC_PROXY_URL,
            },
        )
        self.assertFalse(session.calls[0][1]["allow_redirects"])
        self.assertTrue(session.closed)

    def test_redirect_is_revalidated_before_second_request(self):
        redirect = FakeResponse(302, "http://localhost/private")
        session = FakeSession([redirect])
        with patch.dict(os.environ, self.env, clear=True):
            with self.assertRaises(ValueError):
                self.module._proxied_get(
                    "https://example.com/",
                    _session_factory=lambda: session,
                    _ssrf_exception_type=ValueError,
                )
        self.assertTrue(redirect.closed)
        self.assertTrue(session.closed)
        self.assertEqual(len(session.calls), 1)

    def test_public_relative_redirect_stays_proxied(self):
        redirect = FakeResponse(302, "/next")
        final = FakeResponse()
        session = FakeSession([redirect, final])
        with patch.dict(os.environ, self.env, clear=True):
            result = self.module._proxied_get(
                "https://example.com/start",
                _session_factory=lambda: session,
                _ssrf_exception_type=ValueError,
            )
        self.assertIs(result, final)
        self.assertEqual(
            [url for url, _kwargs in session.calls],
            ["https://example.com/start", "https://example.com/next"],
        )

    def test_http_requires_explicit_wrapper_opt_in(self):
        session = FakeSession([])
        with patch.dict(os.environ, self.env, clear=True):
            with self.assertRaises(ValueError):
                self.module._proxied_get(
                    "http://example.com/",
                    _session_factory=lambda: session,
                    _ssrf_exception_type=ValueError,
                )
        self.assertEqual(session.calls, [])

    def test_admin_private_allowance_cannot_widen_crawler_route(self):
        session = FakeSession([])
        with patch.dict(os.environ, self.env, clear=True):
            with self.assertRaises(ValueError):
                self.module._proxied_get(
                    "https://127.0.0.1/",
                    allow_private_network=True,
                    _session_factory=lambda: session,
                    _ssrf_exception_type=ValueError,
                )
        self.assertEqual(session.calls, [])

    def test_source_has_no_target_dns_or_proxy_bypass(self):
        source = MODULE_PATH.read_text()
        self.assertNotIn("getaddrinfo", source)
        self.assertNotIn("socket.", source)
        self.assertIn("session.trust_env = False", source)
        self.assertIn('"proxies" in kwargs', source)


if __name__ == "__main__":
    unittest.main()
