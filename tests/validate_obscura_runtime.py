#!/usr/bin/env python3
"""Behavioral checks executed beside the pinned Obscura image."""

from __future__ import annotations

import asyncio
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from private_onyx_obscura import BodyClassification
from private_onyx_obscura import FetchFailure
from private_onyx_obscura import ObscuraSession
from private_onyx_obscura import SearchBrowserSession
from private_onyx_obscura import fetch as fetch_async
from private_onyx_obscura import fetch_sync
from private_onyx_obscura.client import _RawCdp
from private_onyx_obscura.client import _SEARCH_FORM_FUNCTION
from private_onyx_obscura.anubis import worker_preload_source
from websockets.asyncio.client import connect


CDP_URL = os.environ["OBSCURA_TEST_CDP_URL"]
BASE_URL = os.environ["OBSCURA_TEST_BASE_URL"].rstrip("/")
LIMIT = 2 * 1024 * 1024


def fetch(path: str, *, want: str = "both"):
    return fetch_sync(
        f"{BASE_URL}{path}",
        cdp_url=CDP_URL,
        wait_until="load",
        allow_http=True,
        body_limit=LIMIT,
        dom_limit=LIMIT,
        want=want,
        request_timeout_seconds=20,
    )


async def create_target(cdp: _RawCdp) -> tuple[str, str]:
    created = await cdp.send("Target.createTarget", {"url": "about:blank"})
    target_id = str(created["targetId"])
    attached = next(
        event
        for event in cdp.events
        if event.get("method") == "Target.attachedToTarget"
        and event.get("params", {}).get("targetInfo", {}).get("targetId")
        == target_id
    )
    return target_id, str(attached["params"]["sessionId"])


def fixture_get(path: str) -> bytes:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=5) as response:
        return response.read()


async def validate_retained_page_autonomous_work() -> None:
    """Reproduce retained-page work, then prove local parking stops it."""
    fixture_get("/idle-pulse-reset")
    websocket = await connect(CDP_URL, proxy=None)
    cdp = _RawCdp(websocket)
    target_id = ""
    try:
        target_id, session_id = await create_target(cdp)
        nav = await cdp.send(
            "Page.navigate",
            {"url": f"{BASE_URL}/retained-active", "waitUntil": "load"},
            session_id=session_id,
            timeout_seconds=15,
        )
        assert nav.get("loaderId")
        await asyncio.sleep(0.8)
        first_count = int(await asyncio.to_thread(fixture_get, "/idle-pulse-count"))
        await asyncio.sleep(0.3)
        second_count = int(await asyncio.to_thread(fixture_get, "/idle-pulse-count"))
        assert first_count > 0, first_count
        assert second_count > first_count, (first_count, second_count)
    finally:
        if target_id:
            await cdp.send("Target.closeTarget", {"targetId": target_id})
        await websocket.close()

    fixture_get("/idle-pulse-reset")
    owner = SearchBrowserSession()
    owner._connection.websocket = await connect(CDP_URL, proxy=None)
    owner._connection.cdp = _RawCdp(owner._connection.websocket)
    try:
        owner._target_id, owner._session_id = await create_target(
            owner._connection.cdp
        )
        frame_tree = await owner._connection.cdp.send(
            "Page.getFrameTree", session_id=owner._session_id
        )
        owner._frame_id = frame_tree["frameTree"]["frame"]["id"]
        nav = await owner._connection.cdp.send(
            "Page.navigate",
            {"url": f"{BASE_URL}/retained-active", "waitUntil": "load"},
            session_id=owner._session_id,
            timeout_seconds=15,
        )
        assert nav.get("loaderId")
        await asyncio.sleep(0.5)
        active_count = int(await asyncio.to_thread(fixture_get, "/idle-pulse-count"))
        assert active_count > 0, active_count

        retained_target = owner._target_id
        await owner._park_target()
        await asyncio.sleep(0.1)
        parked_count = int(await asyncio.to_thread(fixture_get, "/idle-pulse-count"))
        await asyncio.sleep(0.5)
        final_count = int(await asyncio.to_thread(fixture_get, "/idle-pulse-count"))
        assert final_count == parked_count, (parked_count, final_count)
        assert owner.generation_active
        targets = await owner._connection.cdp.send("Target.getTargets")
        assert [target["targetId"] for target in targets["targetInfos"]] == [
            retained_target
        ]
        location = await owner._connection.cdp.send(
            "Runtime.evaluate",
            {"expression": "location.href", "returnByValue": True},
            session_id=owner._session_id,
        )
        assert location["result"]["value"] == "about:blank", location
    finally:
        await owner.close()
    print("RETAINED_PAGE_AUTONOMOUS_WORK_REPRODUCED_AND_PARKED")


async def validate_playwright_session_attachment() -> None:
    """Prove the public Playwright page-session path uses a distinct ID."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(CDP_URL)
        try:
            context = browser.contexts[0]
            page = await context.new_page()
            try:
                session = await context.new_cdp_session(page)
                try:
                    result = await session.send(
                        "Runtime.evaluate",
                        {"expression": "6 * 7", "returnByValue": True},
                    )
                    assert result["result"]["value"] == 42
                finally:
                    await session.detach()
            finally:
                await page.close()
        finally:
            await browser.close()


async def validate_anubis_worker_preload_runtime() -> None:
    """Exercise exact interception and native delegation in the pinned V8 runtime."""
    websocket = await connect(CDP_URL, proxy=None)
    cdp = _RawCdp(websocket)
    target_id = ""
    try:
        target_id, session_id = await create_target(cdp)
        await cdp.send("Page.enable", {}, session_id=session_id)
        nav = await cdp.send(
            "Page.navigate",
            {"url": f"{BASE_URL}/javascript", "waitUntil": "load"},
            session_id=session_id,
            timeout_seconds=15,
        )
        assert nav.get("loaderId")
        source = worker_preload_source(
            "__privateOnyxAnubis_0123456789abcdef0123456789abcdef"
        )
        setup = await cdp.send(
            "Runtime.evaluate",
            {
                "expression": """
(() => {
  const marker = document.createElement('script');
  marker.src = '/.within.website/x/cmd/anubis/static/js/main.mjs';
  document.head.appendChild(marker);
  globalThis.Worker = class NativeWorker {
    constructor(url, options) {
      this.kind = 'native';
      this.url = String(url);
      this.options = options;
      this.newTargetName = new.target.name;
    }
  };
})()
""",
                "returnByValue": True,
            },
            session_id=session_id,
        )
        assert "exceptionDetails" not in setup, setup
        installed = await cdp.send(
            "Runtime.evaluate",
            {"expression": source, "returnByValue": True},
            session_id=session_id,
        )
        assert "exceptionDetails" not in installed, installed
        exercised = await cdp.send(
            "Runtime.evaluate",
            {
                "expression": """
(() => {
  const controlName = '__privateOnyxAnubis_0123456789abcdef0123456789abcdef';
  const direct = new Worker('/.within.website/x/cmd/anubis/static/js/worker/sha256-webcrypto.mjs');
  const localBlob = new Worker(`blob:${location.origin}/local-worker`);
  const foreignBlob = new Worker('blob:https://foreign.example/worker');
  class ChildWorker extends Worker {}
  const unrelated = new ChildWorker('/ordinary-worker.js', {type: 'module'});
  const before = globalThis[controlName]('status');
  const removed = globalThis[controlName]('remove');
  return {
    directInert: direct.kind === undefined && direct.onmessage === null,
    localBlobInert: localBlob.kind === undefined && localBlob.onmessage === null,
    foreignBlobNative: foreignBlob.kind === 'native',
    unrelatedNative: unrelated.kind === 'native',
    unrelatedNewTarget: unrelated.newTargetName,
    unrelatedOption: unrelated.options.type,
    before,
    removed,
    restoredNative: globalThis.Worker.name === 'NativeWorker'
  };
})()
""",
                "returnByValue": True,
            },
            session_id=session_id,
        )
        assert "exceptionDetails" not in exercised, exercised
        value = exercised["result"]["value"]
        assert value["directInert"] is True, value
        assert value["localBlobInert"] is True, value
        assert value["foreignBlobNative"] is True, value
        assert value["unrelatedNative"] is True, value
        assert value["unrelatedNewTarget"] == "ChildWorker", value
        assert value["unrelatedOption"] == "module", value
        assert value["before"] == {
            "active": True,
            "installed": True,
            "suppressed": 2,
        }, value
        assert value["removed"] == {
            "active": False,
            "installed": False,
            "suppressed": 0,
        }, value
        assert value["restoredNative"] is True, value
    finally:
        if target_id:
            await cdp.send("Target.closeTarget", {"targetId": target_id})
        await websocket.close()


async def validate_patched_search_runtime() -> None:
    """Exercise retained-target GET/POST and fingerprint contracts."""
    websocket = await connect(CDP_URL, proxy=None)
    cdp = _RawCdp(websocket)
    target_id = ""
    try:
        target_id, session_id = await create_target(cdp)
        for method, params in (
            ("Network.enable", {}),
            ("Page.enable", {}),
            ("Page.setLifecycleEventsEnabled", {"enabled": True}),
        ):
            await cdp.send(method, params, session_id=session_id)
        frame_tree = await cdp.send("Page.getFrameTree", session_id=session_id)
        frame_id = frame_tree["frameTree"]["frame"]["id"]
        fixture_url = urlsplit(BASE_URL)
        current_form_policy: tuple[str, str] | None = None

        async def evaluate(expression: str):
            result = await cdp.send(
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "returnByValue": True,
                    "awaitPromise": True,
                },
                session_id=session_id,
                timeout_seconds=15,
            )
            assert "exceptionDetails" not in result, result
            return result["result"].get("value")

        async def navigate(path: str) -> tuple[int, str]:
            nonlocal current_form_policy
            current_form_policy = {
                "/search-get-home": ("/search-get-result", "get"),
                "/search-post-home": ("/search-post-result", "post"),
                "/search-post-302-home": ("/search-post-302", "post"),
                "/search-post-307-home": ("/search-post-307", "post"),
            }.get(path)
            event_start = len(cdp.events)
            nav = await cdp.send(
                "Page.navigate",
                {"url": f"{BASE_URL}{path}", "waitUntil": "load"},
                session_id=session_id,
                timeout_seconds=15,
            )
            loader_id = nav["loaderId"]
            await cdp.wait_for_event(
                "Page.frameStoppedLoading",
                lambda event: event.get("frameId") == frame_id,
                10,
                start_index=event_start,
            )
            documents = [
                event["params"]
                for event in cdp.events[event_start:]
                if event.get("method") == "Network.responseReceived"
                and event.get("params", {}).get("type") == "Document"
                and event.get("params", {}).get("frameId") == frame_id
                and event.get("params", {}).get("loaderId") == loader_id
            ]
            assert len(documents) == 1
            connection_id = int(
                await evaluate(
                    "document.querySelector('main').dataset.connection"
                )
            )
            fingerprint = await evaluate(
                "(()=>{const c=document.createElement('canvas');"
                "const gl=c.getContext('webgl');const a=new AudioContext();"
                "return JSON.stringify([screen.width,screen.height,"
                "navigator.hardwareConcurrency,navigator.deviceMemory,"
                "navigator.platform,navigator.userAgent,"
                "gl&&gl.getParameter(0x9246),c.toDataURL(),"
                "a.sampleRate,a.baseLatency]);})()"
            )
            return connection_id, fingerprint

        async def form_call(operation: str, query: str):
            assert current_form_policy is not None
            result = await cdp.send(
                "Runtime.callFunctionOn",
                {
                    "functionDeclaration": _SEARCH_FORM_FUNCTION,
                    "arguments": [
                        {"value": operation},
                        {"value": 'textarea[name="q"]'},
                        {"value": "q"},
                        {"value": [["lang", "en"]]},
                        {"value": query},
                        {
                            "value": [
                                [fixture_url.hostname],
                                [fixture_url.hostname],
                                current_form_policy[0],
                                current_form_policy[1],
                                f"{fixture_url.scheme}:",
                                [str(fixture_url.port or "")],
                            ]
                        },
                    ],
                    "returnByValue": True,
                },
                session_id=session_id,
                timeout_seconds=15,
            )
            assert "exceptionDetails" not in result, result
            return result["result"]

        async def submit(expected_method: str, *, mode: str) -> tuple[int, str]:
            event_start = len(cdp.events)
            if mode == "instant":
                await form_call("instant", "fixture")
            else:
                await form_call("timed-prepare", "fixture")
                for character in "fixture":
                    await cdp.send(
                        "Input.dispatchKeyEvent",
                        {
                            "type": "keyDown",
                            "key": character,
                            "text": character,
                            "unmodifiedText": character,
                        },
                        session_id=session_id,
                    )
                    await cdp.send(
                        "Input.dispatchKeyEvent",
                        {"type": "keyUp", "key": character},
                        session_id=session_id,
                    )
                await form_call("verify", "fixture")
            await form_call("submit", "fixture")
            document = await cdp.wait_for_event(
                "Network.responseReceived",
                lambda event: (
                    event.get("type") == "Document"
                    and event.get("frameId") == frame_id
                ),
                10,
                start_index=event_start,
            )
            loader_id = document["params"]["loaderId"]
            await cdp.wait_for_event(
                "Page.frameStoppedLoading",
                lambda event: event.get("frameId") == frame_id,
                10,
                start_index=event_start,
            )
            documents = [
                event["params"]
                for event in cdp.events[event_start:]
                if event.get("method") == "Network.responseReceived"
                and event.get("params", {}).get("type") == "Document"
                and event.get("params", {}).get("frameId") == frame_id
                and event.get("params", {}).get("loaderId") == loader_id
            ]
            assert len(documents) == 1
            request_methods = [
                event["params"]["request"]["method"]
                for event in cdp.events[event_start:]
                if event.get("method") == "Network.requestWillBeSent"
                and event.get("params", {}).get("type") == "Document"
                and event.get("params", {}).get("frameId") == frame_id
                and event.get("params", {}).get("loaderId") == loader_id
            ]
            assert request_methods == [expected_method], request_methods
            observed_method = await evaluate(
                "document.querySelector('main').dataset.method"
            )
            assert observed_method == expected_method
            connection_id = int(
                await evaluate(
                    "document.querySelector('main').dataset.connection"
                )
            )
            fingerprint = await evaluate(
                "(()=>{const c=document.createElement('canvas');"
                "const gl=c.getContext('webgl');const a=new AudioContext();"
                "return JSON.stringify([screen.width,screen.height,"
                "navigator.hardwareConcurrency,navigator.deviceMemory,"
                "navigator.platform,navigator.userAgent,"
                "gl&&gl.getParameter(0x9246),c.toDataURL(),"
                "a.sampleRate,a.baseLatency]);})()"
            )
            return connection_id, fingerprint

        observations = []
        observations.append(await navigate("/search-get-home"))
        observations.append(await submit("GET", mode="instant"))
        observations.append(await navigate("/search-post-home"))
        observations.append(await submit("POST", mode="timed"))
        observations.append(await navigate("/search-post-302-home"))
        observations.append(await submit("GET", mode="instant"))
        observations.append(await navigate("/search-post-307-home"))
        observations.append(await submit("POST", mode="instant"))
        observations.append(await navigate("/search-get-home"))
        assert len({item[0] for item in observations}) == 1, observations
        assert len({item[1] for item in observations}) == 1, observations
    finally:
        if target_id:
            closed = await cdp.send(
                "Target.closeTarget", {"targetId": target_id}, timeout_seconds=5
            )
            assert closed == {"success": True}
        await websocket.close()


async def validate_connection_isolation() -> None:
    first_ws = await connect(CDP_URL, proxy=None)
    second_ws = await connect(CDP_URL, proxy=None)
    try:
        first = _RawCdp(first_ws)
        second = _RawCdp(second_ws)
        first_target, first_session = await create_target(first)
        second_target, second_session = await create_target(second)

        async def evaluate(cdp, session_id: str, expression: str):
            result = await cdp.send(
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "returnByValue": True,
                    "awaitPromise": True,
                },
                session_id=session_id,
                timeout_seconds=15,
            )
            assert "exceptionDetails" not in result, result
            return result["result"].get("value")

        created_context = await first.send("Target.createBrowserContext")
        context_id = created_context["browserContextId"]
        first_contexts = await first.send("Target.getBrowserContexts")
        second_contexts = await second.send("Target.getBrowserContexts")
        assert context_id in first_contexts["browserContextIds"]
        assert context_id not in second_contexts["browserContextIds"]

        for cdp, session_id in (
            (first, first_session),
            (second, second_session),
        ):
            await cdp.send("Network.enable", session_id=session_id)
            await cdp.send("Page.enable", session_id=session_id)
            await cdp.send(
                "Page.navigate",
                {"url": f"{BASE_URL}/static", "waitUntil": "load"},
                session_id=session_id,
                timeout_seconds=15,
            )

        assert await evaluate(
            first,
            first_session,
            "(() => { globalThis.__privateOnyxIsolation='first'; "
            "return globalThis.__privateOnyxIsolation; })()",
        ) == "first"
        assert await evaluate(
            second,
            second_session,
            "typeof globalThis.__privateOnyxIsolation",
        ) == "undefined"

        for cdp, session_id in (
            (first, first_session),
            (second, second_session),
        ):
            await cdp.send(
                "Page.navigate",
                {"url": f"{BASE_URL}/connection-state/cache-page", "waitUntil": "load"},
                session_id=session_id,
                timeout_seconds=15,
            )
        first_cache = await evaluate(
            first, first_session, "globalThis.__privateOnyxCacheObservations"
        )
        second_cache = await evaluate(
            second, second_session, "globalThis.__privateOnyxCacheObservations"
        )
        assert first_cache == [1, 1], first_cache
        assert second_cache == [2, 2], second_cache

        cookie_result = await first.send(
            "Network.setCookie",
            {
                "name": "private-onyx-isolation",
                "value": "first",
                "url": f"{BASE_URL}/",
            },
            session_id=first_session,
        )
        assert cookie_result == {"success": True}
        second_cookie_result = await second.send(
            "Network.setCookie",
            {
                "name": "private-onyx-isolation",
                "value": "second",
                "url": f"{BASE_URL}/",
            },
            session_id=second_session,
        )
        assert second_cookie_result == {"success": True}
        first_cookies = await first.send(
            "Network.getCookies", session_id=first_session
        )
        second_cookies = await second.send(
            "Network.getCookies", session_id=second_session
        )
        assert any(
            cookie["name"] == "private-onyx-isolation"
            for cookie in first_cookies["cookies"]
        )
        assert all(
            cookie["name"] != "private-onyx-isolation"
            or cookie["value"] == "second"
            for cookie in second_cookies["cookies"]
        )
        assert await first.send(
            "Storage.clearCookies", {}, session_id=first_session
        ) == {}
        first_cookies = await first.send(
            "Network.getCookies", session_id=first_session
        )
        second_cookies = await second.send(
            "Network.getCookies", session_id=second_session
        )
        assert all(
            cookie["name"] != "private-onyx-isolation"
            for cookie in first_cookies["cookies"]
        )
        assert any(
            cookie["name"] == "private-onyx-isolation"
            and cookie["value"] == "second"
            for cookie in second_cookies["cookies"]
        )
        first_targets = await first.send("Target.getTargets")
        second_targets = await second.send("Target.getTargets")
        assert [target["targetId"] for target in first_targets["targetInfos"]] == [
            first_target
        ]
        assert [target["targetId"] for target in second_targets["targetInfos"]] == [
            second_target
        ]
    finally:
        await first_ws.close()
        await second_ws.close()

    # The stack supplies no --storage-dir, so a later connection must also
    # start from Obscura's immutable empty template rather than inheriting the
    # completed connection's cookie or target state.
    third_ws = await connect(CDP_URL, proxy=None)
    try:
        third = _RawCdp(third_ws)
        third_target, third_session = await create_target(third)
        third_cookies = await third.send(
            "Network.getCookies", session_id=third_session
        )
        assert all(
            cookie["name"] != "private-onyx-isolation"
            for cookie in third_cookies["cookies"]
        )
        third_targets = await third.send("Target.getTargets")
        assert [target["targetId"] for target in third_targets["targetInfos"]] == [
            third_target
        ]
        assert await third.send("Target.getBrowserContexts") == {
            "browserContextIds": []
        }
        assert await evaluate(
            third,
            third_session,
            "typeof globalThis.__privateOnyxIsolation",
        ) == "undefined"
    finally:
        await third_ws.close()


async def validate_connection_limit() -> None:
    async def open_used_connections() -> list:
        connections = []
        try:
            for _index in range(15):
                connection = await connect(CDP_URL, proxy=None)
                await _RawCdp(connection).send("Target.getTargets")
                connections.append(connection)
        except Exception:
            for connection in connections:
                await connection.close()
            raise
        return connections

    connections = await open_used_connections()
    try:
        try:
            await connect(CDP_URL, proxy=None)
        except Exception as exc:
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
            assert status in {None, 503}, (type(exc).__name__, status)
        else:
            raise AssertionError("connection above --max-connections was accepted")
    finally:
        for connection in connections:
            await connection.close()

    # Closing all admitted clients must synchronously release enough server
    # state for an immediate full replacement wave. The wrapper never retries
    # a refused browser connection.
    replacements = await open_used_connections()
    for connection in replacements:
        await connection.close()


async def validate_reusable_provider_session() -> None:
    async def navigate(path: str, session: ObscuraSession):
        return await fetch_async(
            f"{BASE_URL}{path}",
            cdp_url=CDP_URL,
            wait_until="load",
            allow_http=True,
            body_limit=LIMIT,
            dom_limit=LIMIT,
            want="dom",
            request_timeout_seconds=20,
            session_owner=session,
        )

    first = ObscuraSession()
    try:
        await navigate("/session/set", first)
        retained = await navigate("/session/check", first)
        assert 'id="session">retained<' in (retained.rendered_html or "")
    finally:
        await first.close()

    second = ObscuraSession()
    try:
        isolated = await navigate("/session/check", second)
        assert 'id="session">missing<' in (isolated.rendered_html or "")
    finally:
        await second.close()


async def validate_mixed_retained_and_request_scoped_capacity() -> None:
    """Prove the stack's five-search plus ten-open_url connection model."""

    async def navigate(path: str, session: ObscuraSession):
        return await fetch_async(
            f"{BASE_URL}{path}",
            cdp_url=CDP_URL,
            wait_until="load",
            allow_http=True,
            body_limit=LIMIT,
            dom_limit=LIMIT,
            want="dom",
            request_timeout_seconds=20,
            session_owner=session,
        )

    retained = [ObscuraSession() for _ in range(5)]
    try:
        await asyncio.gather(
            *(navigate("/session/set", session) for session in retained)
        )

        # Onyx merges web_search calls but executes every query in the merged
        # list concurrently. It can also execute that web_search tool beside one
        # direct open_url call, whose process-global permit pool admits at most
        # ten fresh connections. Five retained provider connections plus those
        # ten request-scoped connections are therefore the real mixed maximum.
        fresh = await asyncio.gather(
            *(
                asyncio.to_thread(fetch, f"/stress/{index}", want="dom")
                for index in range(10)
            )
        )
        assert all("parallel-ten" in (result.rendered_html or "") for result in fresh)

        continuity = await asyncio.gather(
            *(navigate("/session/check", session) for session in retained)
        )
        assert all(
            'id="session">retained<' in (result.rendered_html or "")
            for result in continuity
        )
    finally:
        await asyncio.gather(
            *(session.close() for session in retained),
            return_exceptions=True,
        )


def validate_navigation_contracts() -> None:
    static = fetch("/static")
    assert static.status == 200
    assert static.body == (
        b"<html><head><title>Static</title></head>"
        b"<body><main id='static'>static fixture</main></body></html>"
    )
    assert "static fixture" in (static.rendered_html or "")
    assert static.body_failure is None

    javascript = fetch("/javascript", want="dom")
    assert "id=\"state\">rendered<" in (javascript.rendered_html or "")

    post_message = fetch("/post-message", want="dom")
    assert "id=\"message-state\">frame-ready<" in (
        post_message.rendered_html or ""
    )

    modern_javascript = fetch("/modern-javascript", want="dom")
    modern_html = modern_javascript.rendered_html or ""
    assert "custom-event" in modern_html, modern_html
    assert "rgb(4, 5, 6)" in modern_html, modern_html
    assert 'class="clone">cloned<' in modern_html, modern_html
    assert 'id="named-state">named-shadow<' in modern_html, modern_html
    assert 'id="timing-state">function<' in modern_html, modern_html
    assert 'id="svg-state">true<' in modern_html, modern_html
    assert 'id="stream-state">streamed<' in modern_html, modern_html
    assert 'id="module-state">module-graph<' in modern_html, modern_html

    compressed = fetch("/compressed")
    assert "id=\"compressed\">decoded gzip<" in (compressed.rendered_html or "")
    assert b"decoded gzip" in (compressed.body or b"")

    charset = fetch("/charset", want="dom")
    assert "id=\"charset\">café €<" in (charset.rendered_html or "")

    redirected = fetch("/redirect")
    assert redirected.final_url == f"{BASE_URL}/final"
    assert b"redirect terminal" in (redirected.body or b"")

    pdf = fetch("/document.pdf", want="body")
    assert pdf.body_classification is BodyClassification.BINARY
    assert pdf.original_byte_identity is True
    assert (pdf.body or b"").startswith(b"%PDF-1.4")

    raw = fetch("/raw.txt", want="body")
    assert raw.body_classification is BodyClassification.TEXT
    assert raw.lossy_conversion_possible is True
    assert raw.body == b"raw text fixture\n"

    unsupported = fetch("/unsupported.bin", want="body")
    assert unsupported.body_classification is BodyClassification.BINARY
    assert unsupported.body == b"\x00\x01\x02private-onyx"

    counted = fetch("/counted/one-navigation")
    assert "id=\"count\">1<" in (counted.rendered_html or "")
    assert b"id='count'>1<" in (counted.body or b"")

    heavy = fetch("/heavy")
    assert "heavy fixture" in (heavy.rendered_html or "")
    assert heavy.body is None
    assert heavy.body_failure is FetchFailure.BODY_UNAVAILABLE

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(fetch, ("/barrier/first", "/barrier/second"))
        )
    assert all("parallel" in (result.rendered_html or "") for result in results)

    with ThreadPoolExecutor(max_workers=10) as executor:
        stress_results = list(
            executor.map(fetch, (f"/stress/{index}" for index in range(10)))
        )
    assert all(
        "parallel-ten" in (result.rendered_html or "")
        for result in stress_results
    )

    with ThreadPoolExecutor(max_workers=15) as executor:
        capacity_results = list(
            executor.map(fetch, (f"/capacity/{index}" for index in range(15)))
        )
    assert all(
        "parallel-fifteen" in (result.rendered_html or "")
        for result in capacity_results
    )


def main() -> None:
    asyncio.run(validate_retained_page_autonomous_work())
    asyncio.run(validate_playwright_session_attachment())
    asyncio.run(validate_anubis_worker_preload_runtime())
    asyncio.run(validate_patched_search_runtime())
    asyncio.run(validate_connection_isolation())
    asyncio.run(validate_connection_limit())
    asyncio.run(validate_reusable_provider_session())
    asyncio.run(validate_mixed_retained_and_request_scoped_capacity())
    validate_navigation_contracts()
    print("PINNED_OBSCURA_RUNTIME_CONTRACTS_OK")


if __name__ == "__main__":
    main()
