from __future__ import annotations

import asyncio
import inspect
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "browser" / "obscura_client"))

from private_onyx_obscura import FetchFailure  # noqa: E402
from private_onyx_obscura import ObscuraClientError  # noqa: E402
from private_onyx_obscura import SearchBrowserSession  # noqa: E402
from private_onyx_obscura import SearchInteractionSpec  # noqa: E402
from private_onyx_obscura import PendingAnubisPow  # noqa: E402
from private_onyx_obscura import abort_anubis_pow  # noqa: E402
from private_onyx_obscura import submit_search  # noqa: E402
from private_onyx_obscura import fetch_sync  # noqa: E402
from private_onyx_obscura import is_text_like_content_type  # noqa: E402
from private_onyx_obscura import normalize_public_url  # noqa: E402
from private_onyx_obscura import validate_wait_until  # noqa: E402
from private_onyx_obscura.client import _RawCdp  # noqa: E402
from private_onyx_obscura.client import _SEARCH_RESULT_STATE_FUNCTION  # noqa: E402
from private_onyx_obscura.client import _can_preserve_html_dom_without_body  # noqa: E402
from private_onyx_obscura.client import _challenge_details  # noqa: E402
from private_onyx_obscura.client import _drain_body  # noqa: E402
from private_onyx_obscura.client import _SEARCH_FORM_FUNCTION  # noqa: E402
from private_onyx_obscura.client import _wait_for_search_result_dom  # noqa: E402
from private_onyx_obscura.client import _validate_anubis_worker_status  # noqa: E402


class ObscuraClientTests(unittest.TestCase):
    @staticmethod
    def _search_spec(method: str = "get") -> SearchInteractionSpec:
        return SearchInteractionSpec(
            homepage_url="https://search.example/",
            allowed_homepage_hosts=frozenset({"search.example"}),
            allowed_result_hosts=frozenset({"search.example"}),
            query_selector='textarea[name="q"]',
            query_field_name="q",
            form_action_path="/search",
            form_method=method,
            allowed_fixed_field_names=frozenset({"lang"}),
            result_terminal_selector=".terminal",
            result_pending_selector=".pending",
        )

    def test_search_spec_validation_is_strict(self):
        spec = SearchInteractionSpec(
            homepage_url="https://search.example/",
            allowed_homepage_hosts=frozenset({"search.example"}),
            allowed_result_hosts=frozenset({"search.example"}),
            query_selector='textarea[name="q"]',
            query_field_name="q",
            form_action_path="/search",
            form_method="get",
            allowed_fixed_field_names=frozenset({"lang"}),
        )
        self.assertEqual(spec.form_action_path, "/search")
        for changes in (
            {"homepage_url": "http://search.example/"},
            {"allowed_result_hosts": frozenset({"*.example"})},
            {"form_action_path": "//other.example/search"},
            {"form_action_path": "/search?fixed=1"},
            {"allowed_fixed_field_names": frozenset({"q"})},
            {"result_terminal_selector": ".result"},
            {
                "result_terminal_selector": ".result",
                "result_pending_selector": "pending\nselector",
            },
        ):
            values = {
                "homepage_url": spec.homepage_url,
                "allowed_homepage_hosts": spec.allowed_homepage_hosts,
                "allowed_result_hosts": spec.allowed_result_hosts,
                "query_selector": spec.query_selector,
                "query_field_name": spec.query_field_name,
                "form_action_path": spec.form_action_path,
                "form_method": spec.form_method,
                "allowed_fixed_field_names": spec.allowed_fixed_field_names,
            }
            values.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                SearchInteractionSpec(**values)

    def test_anubis_worker_can_be_armed_before_any_worker_starts(self):
        _validate_anubis_worker_status(
            {"active": True, "installed": True, "suppressed": 0}
        )
        for status in (
            {"active": False, "installed": True, "suppressed": 0},
            {"active": True, "installed": False, "suppressed": 0},
        ):
            with self.subTest(status=status), self.assertRaises(ObscuraClientError):
                _validate_anubis_worker_status(status)

    def test_anubis_challenge_is_returned_before_any_worker_starts(self):
        challenge = {
            "challenge": {
                "id": "fixture-challenge",
                "method": "fast",
                "randomData": "ab" * 64,
                "difficulty": 2,
                "spent": False,
            },
            "rules": {"algorithm": "fast", "difficulty": 2},
        }
        document = (
            '<script id="anubis_version" type="application/json">"v1.25.0"</script>'
            '<script id="anubis_challenge" type="application/json">'
            + json.dumps(challenge)
            + "</script>"
            '<script type="module" src="/.within.website/x/cmd/anubis/static/js/main.mjs"></script>'
            '<div class="sp-message">Verifying your request</div>'
        )

        class WebSocket:
            async def close(self):
                return None

        class Cdp:
            def __init__(self):
                self.events = []

            async def send(self, method, params=None, **_kwargs):
                params = params or {}
                if method == "Target.createTarget":
                    self.events.append(
                        {
                            "method": "Target.attachedToTarget",
                            "params": {
                                "sessionId": "session",
                                "targetInfo": {"targetId": "target"},
                            },
                        }
                    )
                    return {"targetId": "target"}
                if method in {
                    "Network.enable",
                    "Page.enable",
                    "Page.setLifecycleEventsEnabled",
                }:
                    return {}
                if method == "Page.getFrameTree":
                    return {"frameTree": {"frame": {"id": "frame"}}}
                if method == "Page.addScriptToEvaluateOnNewDocument":
                    return {"identifier": "preload"}
                if method == "Page.navigate":
                    self.events.extend(
                        [
                            {
                                "method": "Network.responseReceived",
                                "params": {
                                    "type": "Document",
                                    "frameId": "frame",
                                    "loaderId": "homepage",
                                    "requestId": "request-homepage",
                                    "response": {
                                        "url": "https://www.startpage.com/",
                                        "status": 200,
                                        "headers": {"content-type": "text/html"},
                                    },
                                },
                            },
                            {
                                "method": "Page.frameNavigated",
                                "params": {
                                    "frame": {
                                        "id": "frame",
                                        "loaderId": "homepage",
                                        "url": "https://www.startpage.com/",
                                    }
                                },
                            },
                            {
                                "method": "Network.loadingFinished",
                                "params": {"requestId": "request-homepage"},
                            },
                            {
                                "method": "Page.frameStoppedLoading",
                                "params": {"frameId": "frame"},
                            },
                        ]
                    )
                    return {"loaderId": "homepage"}
                if method == "DOM.getDocument":
                    return {"root": {"nodeId": 1}}
                if method == "DOM.getOuterHTML":
                    return {"outerHTML": document}
                if method == "Runtime.callFunctionOn":
                    return {
                        "result": {
                            "value": {
                                "active": True,
                                "installed": True,
                                "suppressed": 0,
                            }
                        }
                    }
                if method == "Target.closeTarget":
                    return {"success": True}
                raise AssertionError(method)

            async def wait_for_event(
                self, method, predicate, _timeout, *, start_index=0
            ):
                return next(
                    event
                    for event in self.events[start_index:]
                    if event["method"] == method
                    and predicate(event.get("params", {}))
                )

        async def exercise():
            cdp = Cdp()
            owner = SearchBrowserSession()
            owner._connection.cdp = cdp
            owner._connection.websocket = WebSocket()
            owner._connection.cdp_url = "ws://obscura.invalid/devtools/browser"
            owner._connection.max_size = 1 << 30
            result = await submit_search(
                "fixture query",
                spec=SearchInteractionSpec(
                    homepage_url="https://www.startpage.com/",
                    allowed_homepage_hosts=frozenset(
                        {"www.startpage.com", "startpage.com"}
                    ),
                    allowed_result_hosts=frozenset(
                        {"www.startpage.com", "startpage.com"}
                    ),
                    query_selector="input#q",
                    query_field_name="query",
                    form_action_path="/sp/search",
                    form_method="post",
                    allowed_fixed_field_names=frozenset({"cat"}),
                    anubis_pow=True,
                ),
                fixed_fields=(("cat", "web"),),
                text_entry_mode="instant",
                cdp_url=owner._connection.cdp_url,
                wait_until="load",
                dom_limit=1 << 20,
                pre_navigation_guard=lambda: True,
                pre_navigation_timeout_seconds=5,
                cleanup_command_timeout_seconds=1,
                request_timeout_seconds=10,
                session_owner=owner,
            )
            self.assertIsInstance(result, PendingAnubisPow)
            self.assertEqual(result.challenge.challenge_id, "fixture-challenge")
            await abort_anubis_pow(
                result.continuation_token, session_owner=owner
            )

        asyncio.run(exercise())

    def test_search_result_readiness_uses_protocol_arguments(self):
        self.assertNotIn("deep_preload", _SEARCH_RESULT_STATE_FUNCTION)
        source = (
            ROOT
            / "browser/obscura_client/private_onyx_obscura/client.py"
        ).read_text()
        self.assertIn('{"value": terminal_selector}', source)
        self.assertIn('{"value": pending_selector}', source)

    def test_search_result_readiness_uses_existing_absolute_deadline(self):
        class Clock:
            def __init__(self):
                self.now = 10.0

            def __call__(self):
                return self.now

            async def sleep(self, delay):
                self.now += delay

        class Session:
            def __init__(self, states):
                self.states = list(states)
                self.calls = []

            async def send(self, method, params=None, **_kwargs):
                self.calls.append((method, params))
                if method == "Runtime.callFunctionOn":
                    state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
                    return {"result": {"value": state}}
                if method == "DOM.getDocument":
                    return {"root": {"nodeId": 1}}
                if method == "DOM.getOuterHTML":
                    return {"outerHTML": "<html>settled</html>"}
                raise AssertionError(method)

        async def exercise(states, deadline):
            clock = Clock()
            session = Session(states)

            def remaining(_stage, _category):
                value = deadline - clock()
                self.assertGreater(value, 0)
                return value

            result = await _wait_for_search_result_dom(
                session,
                initial_html="<html>pending</html>",
                terminal_selector=".terminal",
                pending_selector=".pending",
                dom_limit=1024,
                request_deadline=deadline,
                remaining=remaining,
                _clock=clock,
                _sleep=clock.sleep,
            )
            return result, clock, session

        settled, clock, session = asyncio.run(
            exercise(
                [
                    {"terminal": False, "pending": True},
                    {"terminal": True, "pending": True},
                ],
                11.0,
            )
        )
        self.assertEqual(settled, "<html>settled</html>")
        self.assertEqual(clock.now, 10.1)
        self.assertEqual(
            [call[0] for call in session.calls],
            [
                "Runtime.callFunctionOn",
                "Runtime.callFunctionOn",
                "DOM.getDocument",
                "DOM.getOuterHTML",
            ],
        )

        pending, clock, session = asyncio.run(
            exercise([{"terminal": False, "pending": True}], 10.25)
        )
        self.assertEqual(pending, "<html>pending</html>")
        self.assertEqual(clock.now, 10.25)
        self.assertNotIn("DOM.getDocument", [call[0] for call in session.calls])

        immediate, clock, session = asyncio.run(
            exercise([{"terminal": True, "pending": True}], 11.0)
        )
        self.assertEqual(immediate, "<html>settled</html>")
        self.assertEqual(clock.now, 10.0)
        self.assertEqual(
            [call[0] for call in session.calls],
            [
                "Runtime.callFunctionOn",
                "DOM.getDocument",
                "DOM.getOuterHTML",
            ],
        )

        unchanged, clock, session = asyncio.run(
            exercise([{"terminal": False, "pending": False}], 11.0)
        )
        self.assertEqual(unchanged, "<html>pending</html>")
        self.assertEqual(clock.now, 10.0)
        self.assertEqual(
            [call[0] for call in session.calls], ["Runtime.callFunctionOn"]
        )

    def test_search_form_data_is_passed_as_protocol_arguments(self):
        self.assertNotIn("private query", _SEARCH_FORM_FUNCTION)
        source = (
            ROOT
            / "browser/obscura_client/private_onyx_obscura/client.py"
        ).read_text()
        self.assertIn('{"value": query}', source)
        self.assertIn('{"value": [list(item) for item in fixed_fields]}', source)

    def test_search_owner_closes_target_before_connection(self):
        calls = []

        class Cdp:
            async def send(self, method, params, **_kwargs):
                calls.append((method, params))
                return {"success": True}

        class WebSocket:
            async def close(self):
                calls.append(("websocket-close", None))

        owner = SearchBrowserSession()
        owner._connection.cdp = Cdp()
        owner._connection.websocket = WebSocket()
        owner._target_id = "target"
        asyncio.run(owner.close())
        self.assertEqual(
            calls,
            [
                ("Target.closeTarget", {"targetId": "target"}),
                ("websocket-close", None),
            ],
        )
        asyncio.run(owner.close())
        self.assertEqual(len(calls), 2)

    def test_search_owner_closes_connection_when_target_close_fails(self):
        calls = []

        class Cdp:
            async def send(self, method, _params, **_kwargs):
                calls.append(method)
                raise ObscuraClientError(
                    FetchFailure.PROTOCOL,
                    "cleanup-target-close",
                    "target close failed",
                )

        class WebSocket:
            async def close(self):
                calls.append("websocket-close")

        owner = SearchBrowserSession()
        owner._connection.cdp = Cdp()
        owner._connection.websocket = WebSocket()
        owner._target_id = "target"
        with self.assertRaises(ObscuraClientError):
            asyncio.run(owner.close())
        self.assertEqual(calls, ["Target.closeTarget", "websocket-close"])
        self.assertIsNone(owner._target_id)
        self.assertIsNone(owner._connection.websocket)

    def test_search_target_parking_failure_discards_generation(self):
        calls = []

        class WebSocket:
            async def close(self):
                calls.append("websocket-close")

        class Cdp:
            events = []

            async def send(self, method, _params=None, **_kwargs):
                calls.append(method)
                if method == "Page.navigate":
                    raise ObscuraClientError(
                        FetchFailure.PROTOCOL,
                        "search-target-park",
                        "parking failed",
                    )
                if method == "Target.closeTarget":
                    return {"success": True}
                raise AssertionError(method)

        async def exercise():
            owner = SearchBrowserSession()
            owner._connection.cdp = Cdp()
            owner._connection.websocket = WebSocket()
            owner._target_id = "target"
            owner._session_id = "session"
            owner._frame_id = "frame"
            with self.assertRaises(ObscuraClientError) as raised:
                await owner._park_target()
            self.assertEqual(raised.exception.stage, "search-target-park")
            self.assertFalse(owner.generation_active)

        asyncio.run(exercise())
        self.assertEqual(
            calls,
            ["Page.navigate", "Target.closeTarget", "websocket-close"],
        )

    def test_search_transaction_reuses_one_target_and_partitions_two_loaders(self):
        class WebSocket:
            def __init__(self):
                self.closed = 0

            async def close(self):
                self.closed += 1

        class Cdp:
            def __init__(self):
                self.events = []
                self.calls = []
                self.attempt = 0
                self.current_document = "homepage"

            def document_events(self, loader, url):
                request = f"request-{loader}"
                self.events.extend(
                    [
                        {
                            "method": "Network.responseReceived",
                            "params": {
                                "type": "Document",
                                "frameId": "frame",
                                "loaderId": loader,
                                "requestId": request,
                                "response": {
                                    "url": url,
                                    "status": 200,
                                    "headers": {"content-type": "text/html"},
                                },
                            },
                        },
                        {
                            "method": "Page.frameNavigated",
                            "params": {
                                "frame": {
                                    "id": "frame",
                                    "loaderId": loader,
                                    "url": url,
                                }
                            },
                        },
                        {
                            "method": "Network.loadingFinished",
                            "params": {"requestId": request},
                        },
                        {
                            "method": "Page.frameStoppedLoading",
                            "params": {"frameId": "frame"},
                        },
                    ]
                )

            async def send(self, method, params=None, **_kwargs):
                params = params or {}
                self.calls.append((method, params))
                if method == "Target.createTarget":
                    self.events.append(
                        {
                            "method": "Target.attachedToTarget",
                            "params": {
                                "sessionId": "session",
                                "targetInfo": {"targetId": "target"},
                            },
                        }
                    )
                    return {"targetId": "target"}
                if method in {
                    "Network.enable",
                    "Page.enable",
                    "Page.setLifecycleEventsEnabled",
                }:
                    return {}
                if method == "Page.getFrameTree":
                    return {"frameTree": {"frame": {"id": "frame"}}}
                if method == "Page.navigate":
                    if params.get("url") == "about:blank":
                        self.current_document = "blank"
                        return {"loaderId": f"park-{self.attempt}"}
                    self.attempt += 1
                    loader = f"homepage-{self.attempt}"
                    self.current_document = "homepage"
                    self.document_events(loader, "https://search.example/")
                    return {"loaderId": loader}
                if method == "DOM.getDocument":
                    return {"root": {"nodeId": 1}}
                if method == "DOM.getOuterHTML":
                    return {
                        "outerHTML": (
                            "<html><body>homepage form</body></html>"
                            if self.current_document == "homepage"
                            else "<html><body>submitted results</body></html>"
                        )
                    }
                if method == "Runtime.callFunctionOn":
                    if params["functionDeclaration"] == _SEARCH_RESULT_STATE_FUNCTION:
                        return {
                            "result": {
                                "value": {"terminal": True, "pending": False}
                            }
                        }
                    operation = params["arguments"][0]["value"]
                    assert params["arguments"][5]["value"] == [
                        ["search.example"],
                        ["search.example"],
                        "/search",
                        self.form_method,
                        "https:",
                        ["", "443"],
                    ]
                    if operation == "submit":
                        # Result events deliberately precede the command
                        # acknowledgement; _RawCdp has the same buffering
                        # contract while awaiting this result.
                        loader = f"result-{self.attempt}"
                        self.current_document = "result"
                        self.document_events(
                            loader, "https://search.example/search"
                        )
                    return {"result": {"type": "boolean"}}
                if method == "Input.dispatchKeyEvent":
                    return {}
                if method == "Target.closeTarget":
                    return {"success": True}
                raise AssertionError(method)

            async def wait_for_event(
                self, method, predicate, _timeout, *, start_index=0
            ):
                return next(
                    event
                    for event in self.events[start_index:]
                    if event["method"] == method
                    and predicate(event.get("params", {}))
                )

        async def exercise(method, mode):
            cdp = Cdp()
            cdp.form_method = method
            websocket = WebSocket()
            owner = SearchBrowserSession()
            owner._connection.cdp = cdp
            owner._connection.websocket = websocket
            owner._connection.cdp_url = "ws://obscura.invalid/devtools/browser"
            owner._connection.max_size = 1 << 30
            delays = []

            class TimingRandom:
                @staticmethod
                def uniform(start, end):
                    self.assertEqual((start, end), (0.045, 0.135))
                    return 0.09

            async def timing_sleep(delay):
                delays.append(delay)

            for query in ("ab", "c"):
                result = await submit_search(
                    query,
                    spec=self._search_spec(method),
                    fixed_fields=(("lang", "en"),),
                    text_entry_mode=mode,
                    cdp_url=owner._connection.cdp_url,
                    wait_until="load",
                    dom_limit=1 << 20,
                    pre_navigation_guard=lambda: True,
                    pre_navigation_timeout_seconds=5,
                    cleanup_command_timeout_seconds=1,
                    request_timeout_seconds=10,
                    session_owner=owner,
                    _timing_random=TimingRandom(),
                    _timing_sleep=timing_sleep,
                )
                self.assertEqual(result.final_url, "https://search.example/search")
                self.assertIn("submitted results", result.rendered_html)
            self.assertEqual(
                [call[0] for call in cdp.calls].count("Target.createTarget"), 1
            )
            self.assertEqual(
                [call[0] for call in cdp.calls].count("Page.navigate"), 4
            )
            self.assertEqual(
                [
                    call[1]["url"]
                    for call in cdp.calls
                    if call[0] == "Page.navigate"
                ],
                [
                    "https://search.example/",
                    "about:blank",
                    "https://search.example/",
                    "about:blank",
                ],
            )
            self.assertNotIn(
                "Target.closeTarget", [call[0] for call in cdp.calls]
            )
            form_calls = [
                call[1]
                for call in cdp.calls
                if call[0] == "Runtime.callFunctionOn"
                and call[1].get("functionDeclaration") == _SEARCH_FORM_FUNCTION
            ]
            self.assertTrue(form_calls)
            self.assertTrue(all("awaitPromise" not in call for call in form_calls))
            if mode == "timed":
                texts = [
                    call[1].get("text")
                    for call in cdp.calls
                    if call[0] == "Input.dispatchKeyEvent"
                    and call[1]["type"] == "keyDown"
                ]
                self.assertEqual("".join(texts), "abc")
                self.assertEqual(delays, [0.09])
            await owner.close()
            self.assertEqual(websocket.closed, 1)
            self.assertEqual(
                [call[0] for call in cdp.calls].count("Target.closeTarget"), 1
            )

        for method in ("get", "post"):
            for mode in ("instant", "timed"):
                with self.subTest(method=method, mode=mode):
                    asyncio.run(exercise(method, mode))

    def test_search_form_function_enforces_native_entry_and_native_submission(self):
        self.assertIn('Object.getOwnPropertyDescriptor(proto, "value")', _SEARCH_FORM_FUNCTION)
        self.assertIn("Object.getPrototypeOf(proto)", _SEARCH_FORM_FUNCTION)
        self.assertEqual(_SEARCH_FORM_FUNCTION.count('new Event("input"'), 1)
        self.assertEqual(_SEARCH_FORM_FUNCTION.count('new Event("change"'), 1)
        self.assertIn("state.form.requestSubmit()", _SEARCH_FORM_FUNCTION)
        self.assertIn("function exactMember", _SEARCH_FORM_FUNCTION)
        self.assertNotIn(".includes(", _SEARCH_FORM_FUNCTION)
        self.assertNotIn("location.assign", _SEARCH_FORM_FUNCTION)
        self.assertNotIn("fetch(", _SEARCH_FORM_FUNCTION)

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

    def test_challenge_detection_requires_complete_anubis_structure(self):
        markup = """
            <html><body>
              <div class="sp-message"><span>Verifying your request...</span></div>
              <script id="anubis_version" type="application/json">"v1.25.0"</script>
              <script id="anubis_challenge" type="application/json">{}</script>
              <script type="module"
                src="/.within.website/x/cmd/anubis/static/js/main.mjs?cacheBuster=v1.25.0">
              </script>
            </body></html>
        """
        challenge, signal = _challenge_details(
            200, "https://www.startpage.com/", markup
        )
        self.assertEqual(challenge, FetchFailure.CAPTCHA)
        self.assertEqual(signal, "anubis-verification")

        incomplete = (
            markup.replace('id="anubis_challenge"', 'id="other"'),
            markup.replace("sp-message", "ordinary-message"),
            markup.replace(
                "/.within.website/x/cmd/anubis/static/js/main.mjs",
                "https://untrusted.example/.within.website/x/cmd/anubis/static/js/main.mjs",
            ),
        )
        for candidate in incomplete:
            with self.subTest(candidate=candidate):
                challenge, signal = _challenge_details(
                    200, "https://www.startpage.com/", candidate
                )
                self.assertIsNone(challenge)
                self.assertEqual(signal, "none")

    def test_challenge_detection_preserves_http_status_classification(self):
        challenge, signal = _challenge_details(
            403, "https://example.com/", "<html></html>"
        )
        self.assertEqual(challenge, FetchFailure.ACCESS_DENIED)
        self.assertEqual(signal, "http-denial-status")

    def test_challenge_route_detection_ignores_result_query_values(self):
        challenge, signal = _challenge_details(
            200,
            (
                "https://www.google.com/search?"
                "q=site%3Aexample.com%2Fchallenge%2F"
            ),
            "<title>Search results</title><body>ordinary results</body>",
        )
        self.assertIsNone(challenge)
        self.assertEqual(signal, "none")

    def test_searxng_has_no_second_generic_challenge_detector(self):
        source = (ROOT / "searxng/engines/_obscura.py").read_text()
        self.assertNotIn("BLOCK_MARKER_XPATH", source)
        self.assertIn("result.challenge is FetchFailure.CAPTCHA", source)


if __name__ == "__main__":
    unittest.main()
