from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


PATCH_PATH = (
    Path(__file__).resolve().parents[1]
    / "onyx/patches/sitecustomize_api_server/github_egress_patch.py"
)


def _github_get(url, *, authorization=None, stream=False, follow_redirects=True, timeout=30):
    # Source fixture for the pinned import/call contract; never executed.
    try:
        return ssrf_safe_get(  # noqa: F821
            url, headers=headers, timeout=timeout,  # noqa: F821
            stream=stream, follow_redirects=follow_redirects,
        )
    except SSRFException:  # noqa: F821
        raise
    except requests.Timeout:  # noqa: F821
        raise
    except requests.RequestException:  # noqa: F821
        raise


class GitHubEgressTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("github_egress_patch", PATCH_PATH)
        self.assertIsNotNone(spec)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.env = {"ONYX_HELPER_HTTP_PROXY_URL": self.module.PUBLIC_PROXY_URL}

    def test_public_route_preserves_stream_timeout_and_redirect_selection(self):
        session = MagicMock()
        session.__enter__.return_value = session
        requests = SimpleNamespace(Session=MagicMock(return_value=session))
        for redirects in (True, False):
            with patch.dict("sys.modules", {"requests": requests}), patch.dict(
                os.environ, dict(self.env, HTTPS_PROXY="http://wrong:1234", NO_PROXY="*")
            ), patch("socket.getaddrinfo", side_effect=AssertionError("local DNS")):
                result = self.module._public_get(
                    "https://codeload.github.com/octocat/Hello-World/tar.gz/HEAD",
                    timeout=(30, 300), stream=True, follow_redirects=redirects,
                )
            self.assertIs(result, session.get.return_value)
            self.assertFalse(session.trust_env)
            self.assertEqual(session.proxies, {
                "http": self.module.PUBLIC_PROXY_URL,
                "https": self.module.PUBLIC_PROXY_URL,
            })
            self.assertEqual(session.max_redirects, 10)
            self.assertEqual(session.get.call_args.kwargs, {
                "headers": None, "timeout": (30, 300), "stream": True,
                "allow_redirects": redirects,
            })

    def test_proxy_failure_propagates_without_retry(self):
        session = MagicMock()
        session.__enter__.return_value = session
        session.get.side_effect = OSError("proxy unavailable")
        with patch.dict("sys.modules", {"requests": SimpleNamespace(Session=lambda: session)}), \
                patch.dict(os.environ, self.env):
            with self.assertRaisesRegex(OSError, "proxy unavailable"):
                self.module._public_get("https://api.github.com/repos/octocat/Hello-World")
        session.get.assert_called_once()
        session.__exit__.assert_called_once()

    def test_proxy_is_required_and_cannot_select_host_route(self):
        for value in ("", "http://onyx-host-egress-bridge:3128", "http://other:3128"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"ONYX_HELPER_HTTP_PROXY_URL": value}, clear=True
            ), self.assertRaisesRegex(RuntimeError, "GitHub egress requires"):
                self.module._validate_proxy()

    def test_target_drift_fails_closed(self):
        original = object()
        upstream = SimpleNamespace(ssrf_safe_get=original, MAX_REDIRECTS=10)
        github = SimpleNamespace(ssrf_safe_get=original, _github_get=_github_get)
        self.module._validate_target(github, upstream)
        github.ssrf_safe_get = object()
        with self.assertRaisesRegex(RuntimeError, "import changed"):
            self.module._validate_target(github, upstream)
        github.ssrf_safe_get = original
        github._github_get = lambda url: None
        with self.assertRaisesRegex(RuntimeError, "signature changed"):
            self.module._validate_target(github, upstream)
        github._github_get = _github_get
        with patch.object(self.module.inspect, "getsource", return_value="return requests.get(url)"):
            with self.assertRaisesRegex(RuntimeError, "source changed"):
                self.module._validate_target(github, upstream)
        upstream.MAX_REDIRECTS = 30
        with self.assertRaisesRegex(RuntimeError, "redirect limit changed"):
            self.module._validate_target(github, upstream)


if __name__ == "__main__":
    unittest.main()
