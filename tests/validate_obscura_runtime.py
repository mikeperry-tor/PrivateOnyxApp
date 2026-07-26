#!/usr/bin/env python3
"""Behavioral checks executed beside the pinned Obscura image."""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

from private_onyx_obscura import BodyClassification
from private_onyx_obscura import FetchFailure
from private_onyx_obscura import fetch_sync
from private_onyx_obscura.client import _RawCdp
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


async def validate_connection_isolation() -> None:
    first_ws = await connect(CDP_URL, proxy=None)
    second_ws = await connect(CDP_URL, proxy=None)
    try:
        first = _RawCdp(first_ws)
        second = _RawCdp(second_ws)
        first_target, first_session = await create_target(first)
        second_target, second_session = await create_target(second)

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


async def validate_connection_limit() -> None:
    connections = [await connect(CDP_URL, proxy=None) for _ in range(15)]
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
    asyncio.run(validate_connection_isolation())
    asyncio.run(validate_connection_limit())
    validate_navigation_contracts()
    print("PINNED_OBSCURA_RUNTIME_CONTRACTS_OK")


if __name__ == "__main__":
    main()
