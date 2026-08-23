from __future__ import annotations

import asyncio
import hashlib
import logging
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "browser" / "obscura_client"))

from private_onyx_obscura import (  # noqa: E402
    AnubisChallenge,
    AnubisSolution,
    FetchFailure,
    ObscuraClientError,
    SearchBrowserSession,
    SearchInteractionSpec,
    abort_anubis_pow,
    resume_anubis_pow,
)
from private_onyx_obscura.anubis import ANUBIS_PASS_PATH  # noqa: E402
from private_onyx_obscura.client import (  # noqa: E402
    _PendingAnubisContinuation,
    _SEARCH_FORM_FUNCTION,
)


RESULT_URL = "https://www.startpage.com/sp/search?segment=fixture"
HOMEPAGE_URL = "https://www.startpage.com/"
RESULT_HTML = "<html><body><article>fixture result</article></body></html>"
CHALLENGE_HTML = "<html><title>Verify you are human</title></html>"


def _spec() -> SearchInteractionSpec:
    return SearchInteractionSpec(
        homepage_url=HOMEPAGE_URL,
        allowed_homepage_hosts=frozenset({"www.startpage.com", "startpage.com"}),
        allowed_result_hosts=frozenset({"www.startpage.com", "startpage.com"}),
        query_selector="input#q",
        query_field_name="query",
        form_action_path="/sp/search",
        form_method="post",
        allowed_fixed_field_names=frozenset({"cat"}),
        anubis_pow=True,
    )


class _WebSocket:
    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


class _ContinuationCdp:
    def __init__(
        self,
        *,
        pass_url: str = RESULT_URL,
        pass_html: str = RESULT_HTML,
        pass_has_form: bool = False,
        pass_method: str | None = "GET",
        restored_url: str = RESULT_URL,
        restored_html: str = RESULT_HTML,
        pass_field_overrides: dict[str, str] | None = None,
        pass_event_count: int = 1,
        form_policy_host: str = "www.startpage.com",
        status_value: dict | None = None,
        remove_value: dict | None = None,
    ) -> None:
        self.events: list[dict] = []
        self.calls: list[tuple[str, dict]] = []
        self.form_operations: list[str] = []
        self.pass_url = pass_url
        self.pass_html = pass_html
        self.pass_has_form = pass_has_form
        self.pass_method = pass_method
        self.restored_url = restored_url
        self.restored_html = restored_html
        self.pass_field_overrides = pass_field_overrides or {}
        self.pass_event_count = pass_event_count
        self.form_policy_host = form_policy_host
        self.status_value = status_value or {
            "active": True,
            "installed": True,
            "suppressed": 0,
        }
        self.remove_value = remove_value or {
            "active": False,
            "installed": False,
            "suppressed": 0,
        }
        self.current_html = ""
        self.current_has_form = False
        self.pass_request_url = ""

    def _document(self, loader: str, url: str, html: str) -> None:
        request_id = f"request-{loader}"
        self.current_html = html
        self.events.extend(
            [
                {
                    "method": "Network.responseReceived",
                    "params": {
                        "type": "Document",
                        "frameId": "frame",
                        "loaderId": loader,
                        "requestId": request_id,
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
                    "params": {"requestId": request_id},
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
        if method == "Runtime.callFunctionOn":
            declaration = params.get("functionDeclaration", "")
            arguments = params.get("arguments", [])
            values = [argument.get("value") for argument in arguments]
            if values and values[0] == ANUBIS_PASS_PATH:
                fields = {
                    "id": values[1],
                    "response": values[2],
                    "nonce": str(values[3]),
                    "redir": values[4],
                    "elapsedTime": str(values[5]),
                }
                fields.update(self.pass_field_overrides)
                self.pass_request_url = (
                    f"https://www.startpage.com{ANUBIS_PASS_PATH}?"
                    + urlencode(fields)
                )
                if self.pass_method is not None:
                    self.events.extend(
                        {
                            "method": "Network.requestWillBeSent",
                            "params": {
                                "type": "Document",
                                "frameId": "frame",
                                "loaderId": "pass",
                                "request": {
                                    "method": self.pass_method,
                                    "url": self.pass_request_url,
                                },
                            },
                        }
                        for _index in range(self.pass_event_count)
                    )
                self.current_has_form = self.pass_has_form
                self._document("pass", self.pass_url, self.pass_html)
                return {"result": {"value": None}}
            if len(values) == 2 and values[0] == "preload-control":
                operation = values[1]
                if operation == "status":
                    return {"result": {"value": self.status_value}}
                if operation == "remove":
                    return {"result": {"value": self.remove_value}}
            if declaration == _SEARCH_FORM_FUNCTION:
                operation = values[0]
                expected_policy = values[5]
                if self.form_policy_host not in expected_policy[0]:
                    return {
                        "exceptionDetails": {"text": "Uncaught Error: form-policy"},
                        "result": {"subtype": "error"},
                    }
                self.form_operations.append(operation)
                if operation == "submit":
                    self.current_has_form = False
                    self._document("restored", self.restored_url, self.restored_html)
                return {
                    "result": {
                        "value": {
                            "currentScheme": "https:",
                            "currentHost": self.form_policy_host,
                            "currentPort": "",
                            "scheme": "https:",
                            "host": "www.startpage.com",
                            "port": "",
                            "username": "",
                            "password": "",
                            "path": "/sp/search",
                            "method": "post",
                            "target": "",
                            "enctype": "application/x-www-form-urlencoded",
                        }
                    }
                }
            if "document.querySelector(selector) !== null" in declaration:
                return {"result": {"value": self.current_has_form}}
            raise AssertionError((declaration, values))
        if method == "DOM.getDocument":
            return {"root": {"nodeId": 1}}
        if method == "DOM.getOuterHTML":
            return {"outerHTML": self.current_html}
        if method == "Page.removeScriptToEvaluateOnNewDocument":
            return {}
        if method == "Page.navigate" and params.get("url") == "about:blank":
            return {"loaderId": "parked"}
        if method == "Target.closeTarget":
            return {"success": True}
        raise AssertionError(method)

    async def wait_for_event(
        self, method, predicate, _timeout, *, start_index=0
    ):
        return next(
            event
            for event in self.events[start_index:]
            if event["method"] == method and predicate(event.get("params", {}))
        )


def _owner(
    cdp: _ContinuationCdp,
    *,
    boundary: str,
    token: str = "continuation-token",
    query: str = "fixture query",
    challenge_id: str = "challenge-id",
    random_data: str = "fixture-random-data",
    deadline: float | None = None,
) -> tuple[SearchBrowserSession, _WebSocket, AnubisSolution]:
    challenge = AnubisChallenge(
        "v1.25.0", challenge_id, random_data, "fast", 0
    )
    nonce = 314159
    response = hashlib.sha256(
        (random_data + str(nonce)).encode("ascii")
    ).hexdigest()
    solution = AnubisSolution(response, nonce, 7)
    websocket = _WebSocket()
    owner = SearchBrowserSession()
    owner._connection.cdp = cdp
    owner._connection.websocket = websocket
    owner._target_id = "target"
    owner._session_id = "session"
    owner._frame_id = "frame"
    owner._pending_anubis = _PendingAnubisContinuation(
        token=token,
        challenge=challenge,
        boundary=boundary,
        challenged_url=(HOMEPAGE_URL if boundary == "homepage" else RESULT_URL),
        challenged_loader="challenge-loader",
        query=query,
        spec=_spec(),
        fixed_fields=(("cat", "web"),),
        text_entry_mode="instant",
        request_deadline=deadline or time.monotonic() + 10,
        dom_limit=1 << 20,
        homepage_navigation_seconds=1.0,
        submission_navigation_seconds=(0.0 if boundary == "homepage" else 2.0),
        diagnostic_id="diagnostic-id",
        preload_identifier="preload-id",
        preload_control="preload-control",
    )
    return owner, websocket, solution


class AnubisContinuationTests(unittest.TestCase):
    def test_homepage_challenge_restores_one_post_and_cleans_preload(self):
        cdp = _ContinuationCdp(
            pass_url=HOMEPAGE_URL,
            pass_html="<html><form><input id='q'></form></html>",
            pass_has_form=True,
        )
        owner, websocket, solution = _owner(cdp, boundary="homepage")
        result = asyncio.run(
            resume_anubis_pow(
                "continuation-token", solution, session_owner=owner
            )
        )

        self.assertEqual(result.final_url, RESULT_URL)
        self.assertEqual(result.rendered_html, RESULT_HTML)
        self.assertEqual(cdp.form_operations, ["validate", "instant", "submit"])
        self.assertEqual(cdp.form_operations.count("submit"), 1)
        self.assertIsNone(owner._pending_anubis)
        self.assertTrue(owner.generation_active)
        self.assertEqual(websocket.closed, 0)
        methods = [method for method, _params in cdp.calls]
        self.assertEqual(methods.count("Page.navigate"), 1)
        self.assertIn(
            ("Page.navigate", {"url": "about:blank", "waitUntil": "load"}),
            cdp.calls,
        )
        self.assertNotIn("Network.clearBrowserCookies", methods)
        self.assertLess(
            methods.index("Runtime.callFunctionOn", 6),
            methods.index("Page.removeScriptToEvaluateOnNewDocument"),
        )

    def test_result_challenge_accepts_direct_result_with_omitted_pass_event(self):
        cdp = _ContinuationCdp(pass_method=None)
        owner, _websocket, solution = _owner(cdp, boundary="result")
        result = asyncio.run(
            resume_anubis_pow(
                "continuation-token", solution, session_owner=owner
            )
        )

        self.assertEqual(result.final_url, RESULT_URL)
        self.assertEqual(cdp.form_operations, [])
        self.assertTrue(owner.generation_active)

    def test_result_challenge_restores_at_most_one_post_from_homepage(self):
        cdp = _ContinuationCdp(
            pass_url=HOMEPAGE_URL,
            pass_html="<html><form><input id='q'></form></html>",
            pass_has_form=True,
        )
        owner, _websocket, solution = _owner(cdp, boundary="result")
        result = asyncio.run(
            resume_anubis_pow(
                "continuation-token", solution, session_owner=owner
            )
        )

        self.assertEqual(result.final_url, RESULT_URL)
        self.assertEqual(cdp.form_operations.count("submit"), 1)

    def test_renewed_challenge_closes_generation_without_second_post(self):
        cdp = _ContinuationCdp(pass_html=CHALLENGE_HTML)
        owner, websocket, solution = _owner(cdp, boundary="result")
        with self.assertRaises(ObscuraClientError) as raised:
            asyncio.run(
                resume_anubis_pow(
                    "continuation-token", solution, session_owner=owner
                )
            )

        self.assertEqual(raised.exception.category, FetchFailure.CAPTCHA)
        self.assertEqual(cdp.form_operations, [])
        self.assertFalse(owner.generation_active)
        self.assertEqual(websocket.closed, 1)

    def test_restored_post_challenge_closes_without_retry(self):
        cdp = _ContinuationCdp(
            pass_url=HOMEPAGE_URL,
            pass_html="<html><form><input id='q'></form></html>",
            pass_has_form=True,
            restored_html=CHALLENGE_HTML,
        )
        owner, _websocket, solution = _owner(cdp, boundary="homepage")
        with self.assertRaises(ObscuraClientError) as raised:
            asyncio.run(
                resume_anubis_pow(
                    "continuation-token", solution, session_owner=owner
                )
            )

        self.assertEqual(raised.exception.category, FetchFailure.CAPTCHA)
        self.assertEqual(cdp.form_operations.count("submit"), 1)
        self.assertFalse(owner.generation_active)

    def test_wrong_method_pass_event_is_rejected_and_closes_generation(self):
        cdp = _ContinuationCdp(pass_method="POST")
        owner, _websocket, solution = _owner(cdp, boundary="result")
        with self.assertRaises(ObscuraClientError) as raised:
            asyncio.run(
                resume_anubis_pow(
                    "continuation-token", solution, session_owner=owner
                )
            )

        self.assertEqual(raised.exception.category, FetchFailure.POLICY_DENIED)
        self.assertEqual(raised.exception.stage, "anubis-pass-events")
        self.assertFalse(owner.generation_active)

    def test_pass_field_mismatch_and_duplicate_events_fail_closed(self):
        cases = (
            {"pass_field_overrides": {"id": "different-challenge"}},
            {"pass_event_count": 2},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                cdp = _ContinuationCdp(**changes)
                owner, _websocket, solution = _owner(cdp, boundary="result")
                with self.assertRaises(ObscuraClientError):
                    asyncio.run(
                        resume_anubis_pow(
                            "continuation-token", solution, session_owner=owner
                        )
                    )
                self.assertFalse(owner.generation_active)

    def test_missing_homepage_form_and_invalid_form_policy_fail_closed(self):
        cases = (
            _ContinuationCdp(pass_url=HOMEPAGE_URL, pass_has_form=False),
            _ContinuationCdp(
                pass_url=HOMEPAGE_URL,
                pass_html="<html><form><input id='q'></form></html>",
                pass_has_form=True,
                form_policy_host="other.example",
            ),
        )
        for cdp in cases:
            with self.subTest(form_policy_host=cdp.form_policy_host):
                owner, _websocket, solution = _owner(cdp, boundary="homepage")
                with self.assertRaises(ObscuraClientError):
                    asyncio.run(
                        resume_anubis_pow(
                            "continuation-token", solution, session_owner=owner
                        )
                    )
                self.assertFalse(owner.generation_active)

    def test_worker_removal_postconditions_are_required(self):
        for value in (
            {"active": True, "installed": False, "suppressed": 0},
            {"active": False, "installed": True, "suppressed": 0},
            {"active": False, "installed": False, "suppressed": 1},
        ):
            with self.subTest(value=value):
                cdp = _ContinuationCdp(remove_value=value)
                owner, _websocket, solution = _owner(cdp, boundary="result")
                with self.assertRaises(ObscuraClientError) as raised:
                    asyncio.run(
                        resume_anubis_pow(
                            "continuation-token", solution, session_owner=owner
                        )
                    )
                self.assertEqual(
                    raised.exception.stage, "anubis-worker-remove"
                )
                self.assertFalse(owner.generation_active)

    def test_worker_ownership_tamper_closes_generation(self):
        cdp = _ContinuationCdp(
            status_value={"active": True, "installed": False, "suppressed": 0}
        )
        owner, _websocket, solution = _owner(cdp, boundary="result")
        with self.assertRaises(ObscuraClientError) as raised:
            asyncio.run(
                resume_anubis_pow(
                    "continuation-token", solution, session_owner=owner
                )
            )
        self.assertEqual(raised.exception.stage, "anubis-worker-status")
        self.assertFalse(owner.generation_active)

    def test_invalid_token_solution_and_expired_deadline_close_generation(self):
        cases = ("token", "solution", "deadline")
        for case in cases:
            with self.subTest(case=case):
                cdp = _ContinuationCdp()
                owner, websocket, solution = _owner(
                    cdp,
                    boundary="result",
                    deadline=(time.monotonic() - 1 if case == "deadline" else None),
                )
                token = "wrong-token" if case == "token" else "continuation-token"
                if case == "solution":
                    solution = AnubisSolution("0" * 64, 0, 7)
                with self.assertRaises(ObscuraClientError):
                    asyncio.run(
                        resume_anubis_pow(token, solution, session_owner=owner)
                    )
                self.assertFalse(owner.generation_active)
                self.assertEqual(websocket.closed, 1)

    def test_continuation_is_single_use(self):
        cdp = _ContinuationCdp()
        owner, websocket, solution = _owner(cdp, boundary="result")
        asyncio.run(
            resume_anubis_pow(
                "continuation-token", solution, session_owner=owner
            )
        )
        with self.assertRaises(ObscuraClientError):
            asyncio.run(
                resume_anubis_pow(
                    "continuation-token", solution, session_owner=owner
                )
            )
        self.assertFalse(owner.generation_active)
        self.assertEqual(websocket.closed, 1)

    def test_abort_consumes_exact_token_and_closes_generation(self):
        for token in ("continuation-token", "wrong-token"):
            with self.subTest(token=token):
                cdp = _ContinuationCdp()
                owner, websocket, _solution = _owner(cdp, boundary="result")
                if token == "wrong-token":
                    with self.assertRaises(ObscuraClientError):
                        asyncio.run(
                            abort_anubis_pow(token, session_owner=owner)
                        )
                else:
                    asyncio.run(abort_anubis_pow(token, session_owner=owner))
                self.assertFalse(owner.generation_active)
                self.assertEqual(websocket.closed, 1)

    def test_wrapper_logs_exclude_continuation_secrets(self):
        values = {
            "token": "secret-continuation-token",
            "query": "secret-query-value",
            "challenge": "secret-challenge-id",
            "random": "secret-random-data",
        }
        cdp = _ContinuationCdp()
        owner, _websocket, solution = _owner(
            cdp,
            boundary="result",
            token=values["token"],
            query=values["query"],
            challenge_id=values["challenge"],
            random_data=values["random"],
        )
        with self.assertLogs("private_onyx_obscura", logging.INFO) as captured:
            asyncio.run(
                resume_anubis_pow(
                    values["token"], solution, session_owner=owner
                )
            )

        output = "\n".join(captured.output)
        for value in (*values.values(), solution.response, str(solution.nonce)):
            self.assertNotIn(value, output)
        self.assertIn("pow=solved", output)


if __name__ == "__main__":
    unittest.main()
