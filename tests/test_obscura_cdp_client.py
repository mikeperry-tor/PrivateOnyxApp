from __future__ import annotations

import asyncio
import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "browser" / "obscura_client"))

from private_onyx_obscura import FetchFailure  # noqa: E402
from private_onyx_obscura import ObscuraClientError  # noqa: E402
from private_onyx_obscura import fetch_sync  # noqa: E402
from private_onyx_obscura import is_text_like_content_type  # noqa: E402
from private_onyx_obscura import normalize_public_url  # noqa: E402
from private_onyx_obscura import validate_wait_until  # noqa: E402
from private_onyx_obscura.client import _RawCdp  # noqa: E402
from private_onyx_obscura.client import _can_preserve_html_dom_without_body  # noqa: E402
from private_onyx_obscura.client import _challenge_details  # noqa: E402
from private_onyx_obscura.client import _drain_body  # noqa: E402


class ObscuraClientTests(unittest.TestCase):
    def test_wait_values_are_exact(self):
        for value in ("domcontentloaded", "load", "networkidle0", "networkidle2"):
            self.assertEqual(validate_wait_until(value), value)
        for value in ("", "Load", "networkidle", "commit"):
            with self.assertRaises(ValueError):
                validate_wait_until(value)

    def test_text_predicate_matches_pinned_obscura(self):
        text = (
            None, "", "text/plain", "text/html; charset=utf-8", "application/json",
            "application/xml", "application/xhtml+xml", "application/javascript",
            "application/ecmascript", "image/svg+xml", "application/ld+json",
            "application/atom+xml",
        )
        binary = ("application/pdf", "application/octet-stream", "image/png")
        for content_type in text:
            self.assertTrue(is_text_like_content_type(content_type), content_type)
        for content_type in binary:
            self.assertFalse(is_text_like_content_type(content_type), content_type)

    def test_structural_url_validation_does_not_resolve(self):
        normalized, fragment = normalize_public_url(
            "https://ExAmPle.COM./a?q=private#section", allow_http=False
        )
        self.assertEqual(normalized, "https://example.com/a?q=private")
        self.assertEqual(fragment, "section")
        forbidden = (
            "http://example.com", "file:///etc/passwd", "https://localhost/x",
            "https://service/x", "https://host.docker.internal/x",
            "https://127.0.0.1/x", "https://10.0.0.1/x", "https://[::1]/x",
            "https://user:secret@example.com/x",
        )
        for value in forbidden:
            with self.assertRaises(ObscuraClientError, msg=value):
                normalize_public_url(value, allow_http=False)

    def test_http_onion_requires_narrow_transport_capability(self):
        onion_url = "http://service.subdomain.onion/path"
        normalized, fragment = normalize_public_url(
            onion_url,
            allow_http=False,
            allow_http_onion=True,
        )
        self.assertEqual(normalized, onion_url)
        self.assertIsNone(fragment)

        for value in (
            onion_url,
            "http://onion/",
            "http://service.onion.example/",
            "http://example.com/",
        ):
            with self.subTest(value=value), self.assertRaises(ObscuraClientError):
                normalize_public_url(
                    value,
                    allow_http=False,
                    allow_http_onion=False,
                )

    def test_sync_adapter_rejects_nested_event_loop(self):
        async def nested():
            with self.assertRaisesRegex(RuntimeError, "active event loop"):
                fetch_sync("https://example.com")

        asyncio.run(nested())

    def test_navigation_tunnel_failure_is_transport_not_protocol(self):
        class WebSocket:
            async def send(self, _message):
                return None

            async def recv(self):
                return (
                    '{"id":1,"error":{"code":-32000,'
                    '"message":"Network error: error sending request for URL"}}'
                )

        async def exercise():
            with self.assertRaises(ObscuraClientError) as raised:
                await _RawCdp(WebSocket()).send(
                    "Page.navigate",
                    {"url": "https://missing.example/"},
                    session_id="session",
                )
            self.assertEqual(raised.exception.category, FetchFailure.TRANSPORT)
            self.assertEqual(raised.exception.stage, "navigation-transport")
            self.assertNotIn("missing.example", str(raised.exception))

        asyncio.run(exercise())

    def test_other_cdp_command_error_remains_protocol(self):
        class WebSocket:
            async def send(self, _message):
                return None

            async def recv(self):
                return '{"id":1,"error":{"code":-32601,"message":"method not found"}}'

        async def exercise():
            with self.assertRaises(ObscuraClientError) as raised:
                await _RawCdp(WebSocket()).send("DOM.getDocument")
            self.assertEqual(raised.exception.category, FetchFailure.PROTOCOL)

        asyncio.run(exercise())

    def test_missing_cached_body_is_typed_body_unavailable(self):
        class WebSocket:
            async def send(self, _message):
                return None

            async def recv(self):
                return (
                    '{"id":1,"error":{"code":-32000,'
                    '"message":"Fetch.takeResponseBodyAsStream: no cached body for loader"}}'
                )

        async def exercise():
            with self.assertRaises(ObscuraClientError) as raised:
                await _RawCdp(WebSocket()).send(
                    "Fetch.takeResponseBodyAsStream",
                    {"requestId": "loader"},
                    session_id="session",
                )
            self.assertEqual(raised.exception.category, FetchFailure.BODY_UNAVAILABLE)
            self.assertEqual(raised.exception.stage, "body-stream-open")
            self.assertNotIn("loader", str(raised.exception))

        asyncio.run(exercise())

    def test_only_both_mode_html_preserves_dom_when_body_was_evicted(self):
        unavailable = ObscuraClientError(
            FetchFailure.BODY_UNAVAILABLE,
            "body-stream-open",
            "body unavailable",
        )
        protocol = ObscuraClientError(
            FetchFailure.PROTOCOL,
            "cdp-command",
            "protocol failure",
        )
        self.assertTrue(
            _can_preserve_html_dom_without_body("both", "text/html", unavailable)
        )
        self.assertTrue(
            _can_preserve_html_dom_without_body(
                "both", "application/xhtml+xml", unavailable
            )
        )
        self.assertFalse(
            _can_preserve_html_dom_without_body("body", "text/html", unavailable)
        )
        self.assertFalse(
            _can_preserve_html_dom_without_body("both", "application/pdf", unavailable)
        )
        self.assertFalse(
            _can_preserve_html_dom_without_body("both", "text/html", protocol)
        )

    def test_cdp_command_timeout_is_typed_and_stage_specific(self):
        class WebSocket:
            async def send(self, _message):
                return None

            async def recv(self):
                await asyncio.Future()

        async def exercise():
            with self.assertRaises(ObscuraClientError) as raised:
                await _RawCdp(WebSocket()).send(
                    "Target.createTarget",
                    timeout_seconds=0.001,
                    timeout_category=FetchFailure.PRE_NAVIGATION_TIMEOUT,
                    timeout_stage="create-target",
                )
            self.assertEqual(
                raised.exception.category, FetchFailure.PRE_NAVIGATION_TIMEOUT
            )
            self.assertEqual(raised.exception.stage, "create-target")
            self.assertNotIn("Target.createTarget", str(raised.exception))

        asyncio.run(exercise())

    def test_fetch_relies_on_connection_isolation_without_cookie_clear(self):
        source = (
            ROOT / "browser/obscura_client/private_onyx_obscura/client.py"
        ).read_text()
        self.assertNotIn('"Network.clearBrowserCookies"', source)
        self.assertNotIn('"Network.getCookies"', source)
        self.assertNotIn('"Network.setCookies"', source)
        self.assertIn('"Target.createTarget"', source)

    def test_reusable_session_is_explicit_and_disables_idle_pings(self):
        from private_onyx_obscura import fetch
        from private_onyx_obscura import ObscuraSession

        self.assertIsNone(inspect.signature(fetch).parameters["session_owner"].default)
        source = inspect.getsource(ObscuraSession.ensure_connected)
        self.assertIn("ping_interval=None", source)

    def test_cleanup_failures_taint_a_reusable_connection(self):
        class Session:
            async def send(self, method, *_args, **_kwargs):
                if method == "Fetch.takeResponseBodyAsStream":
                    return {"stream": "stream"}
                if method == "IO.read":
                    return {"data": "body", "eof": True}
                if method == "IO.close":
                    raise ObscuraClientError(
                        FetchFailure.TRANSPORT,
                        "cleanup-body-close",
                        "stream close failed",
                    )
                raise AssertionError(method)

        cleanup_failures = []

        async def exercise():
            body = await _drain_body(
                Session(),
                "request",
                4,
                1024,
                diagnostic_id="diagnostic",
                cleanup_command_timeout_seconds=1.0,
                command_timeout=lambda _stage: 1.0,
                cleanup_failure=lambda: cleanup_failures.append(True),
            )
            self.assertEqual(body, b"body")

        asyncio.run(exercise())
        self.assertEqual(cleanup_failures, [True])

        from private_onyx_obscura import fetch

        self.assertIn(
            'closed.get("success") is not True',
            inspect.getsource(fetch),
        )

    def test_cancelled_reusable_fetch_discards_connection(self):
        from private_onyx_obscura import fetch
        from private_onyx_obscura import ObscuraSession

        class WebSocket:
            def __init__(self):
                self.closed = False

            async def close(self):
                self.closed = True

        class Cdp:
            def __init__(self):
                self.events = []

            async def send(self, *_args, **_kwargs):
                raise asyncio.CancelledError

        websocket = WebSocket()
        owner = ObscuraSession()
        owner.websocket = websocket
        owner.cdp = Cdp()
        owner.cdp_url = "ws://obscura.invalid/devtools/browser"
        owner.max_size = 1 << 30

        async def exercise():
            await fetch(
                "https://example.com/",
                cdp_url=owner.cdp_url,
                wait_until="load",
                allow_http=False,
                body_limit=1024,
                dom_limit=1024,
                session_owner=owner,
            )

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(exercise())
        self.assertTrue(websocket.closed)
        self.assertIsNone(owner.websocket)

    def test_post_navigation_command_timeout_is_typed_and_stage_specific(self):
        class WebSocket:
            async def send(self, _message):
                return None

            async def recv(self):
                await asyncio.Future()

        async def exercise():
            with self.assertRaises(ObscuraClientError) as raised:
                await _RawCdp(WebSocket()).send(
                    "DOM.getOuterHTML",
                    timeout_seconds=0.001,
                    timeout_category=FetchFailure.POST_NAVIGATION_TIMEOUT,
                    timeout_stage="dom-outer-html",
                )
            self.assertEqual(
                raised.exception.category, FetchFailure.POST_NAVIGATION_TIMEOUT
            )
            self.assertEqual(raised.exception.stage, "dom-outer-html")

        asyncio.run(exercise())

    def test_challenge_detection_ignores_script_only_captcha_markers(self):
        challenge, signal = _challenge_details(
            200,
            "https://example.com/article",
            """
            <html><head><title>Animal reaction times</title>
            <script src="https://captcha.example/api.js">
            const provider = "recaptcha";
            </script></head>
            <body><p>Cats can react faster than humans.</p>
            <iframe src="https://captcha.example/recaptcha/widget"></iframe>
            </body></html>
            """,
        )
        self.assertIsNone(challenge)
        self.assertEqual(signal, "none")

    def test_challenge_detection_requires_visible_or_terminal_signal(self):
        fixtures = (
            (
                "https://example.com/article",
                "<title>Verify you are human</title><body>Please wait</body>",
                "challenge-title",
            ),
            (
                "https://example.com/challenge/managed",
                "<title>Example</title><body></body>",
                "terminal-challenge-route",
            ),
            (
                "https://example.com/article",
                "<body><form action='/captcha'>Complete the captcha</form></body>",
                "visible-human-verification",
            ),
        )
        for url, markup, expected_signal in fixtures:
            with self.subTest(expected_signal=expected_signal):
                challenge, signal = _challenge_details(200, url, markup)
                self.assertEqual(challenge, FetchFailure.CAPTCHA)
                self.assertEqual(signal, expected_signal)

    def test_challenge_detection_preserves_http_status_classification(self):
        challenge, signal = _challenge_details(
            403, "https://example.com/", "<html></html>"
        )
        self.assertEqual(challenge, FetchFailure.ACCESS_DENIED)
        self.assertEqual(signal, "http-denial-status")

    def test_searxng_has_no_second_generic_challenge_detector(self):
        source = (ROOT / "searxng/engines/_obscura.py").read_text()
        self.assertNotIn("BLOCK_MARKER_XPATH", source)
        self.assertIn("result.challenge is FetchFailure.CAPTCHA", source)


if __name__ == "__main__":
    unittest.main()
