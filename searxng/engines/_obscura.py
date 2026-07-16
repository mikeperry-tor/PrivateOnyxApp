# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared direct-Obscura navigation and provider admission for offline engines."""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from urllib.parse import urlsplit

from private_onyx_obscura import FetchFailure, ObscuraClientError, fetch_sync, validate_wait_until

logger = logging.getLogger("searx.engines._obscura")
CDP_URL = os.environ.get(
    "SEARXNG_OBSCURA_CDP_URL", "ws://obscura:9222/devtools/browser"
)
WAIT_UNTIL = validate_wait_until(
    os.environ.get("OBSCURA_BROWSER_WAIT_UNTIL_SEARCH", "load")
)
ALLOW_HTTP = os.environ.get("EGRESS_ALLOW_HTTP_URLS", "false").lower() in {
    "1", "true", "yes", "on"
}
SEARCH_DOM_LIMIT = 20 * 1024 * 1024
SEARCH_BROWSER_ATTEMPT_TIMEOUT_SECONDS = 50.0
MINIMUM_START_INTERVAL = 3.0
RESERVATION_PARAM = "_wrapper_obscura_reservation_token"

TERMINAL_HOSTS = {
    "google2": frozenset({"www.google.com", "google.com", "consent.google.com"}),
    "bing2": frozenset({"www.bing.com", "bing.com"}),
    "duckduckgo2": frozenset({"html.duckduckgo.com", "www.duckduckgo.com", "duckduckgo.com"}),
    "brave2": frozenset({"search.brave.com"}),
    "startpage2": frozenset({"www.startpage.com", "startpage.com"}),
}
_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LOWER = "abcdefghijklmnopqrstuvwxyz"
BLOCK_MARKER_XPATH = (
    f'//title[contains(translate(., "{_UPPER}", "{_LOWER}"), "captcha") '
    f'or contains(translate(., "{_UPPER}", "{_LOWER}"), "access denied") '
    f'or contains(translate(., "{_UPPER}", "{_LOWER}"), "verify you are human") '
    f'or contains(translate(., "{_UPPER}", "{_LOWER}"), "unusual traffic") '
    f'or contains(translate(., "{_UPPER}", "{_LOWER}"), "too many requests")] '
    '| //form[contains(translate(@action, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", '
    '"abcdefghijklmnopqrstuvwxyz"), "/sorry/") '
    'or contains(translate(@action, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", '
    '"abcdefghijklmnopqrstuvwxyz"), "/captcha") '
    'or contains(translate(@action, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", '
    '"abcdefghijklmnopqrstuvwxyz"), "/sp/captcha") '
    'or contains(translate(@action, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", '
    '"abcdefghijklmnopqrstuvwxyz"), "turing")]'
)


class _ProviderState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = False
        self.reservation: str | None = None
        self.last_start = float("-inf")


_PROVIDERS = {name: _ProviderState() for name in TERMINAL_HOSTS}


def provider_available(name: str) -> bool:
    state = _PROVIDERS[name]
    with state.lock:
        return (
            not state.active
            and state.reservation is None
            and time.monotonic() - state.last_start >= MINIMUM_START_INTERVAL
        )


def reserve_provider(name: str) -> str | None:
    """Atomically reserve a provider before SearXNG creates its engine thread."""
    state = _PROVIDERS[name]
    with state.lock:
        if (
            state.active
            or state.reservation is not None
            or time.monotonic() - state.last_start < MINIMUM_START_INTERVAL
        ):
            return None
        token = uuid.uuid4().hex
        state.reservation = token
        return token


def release_provider_reservation(name: str, token: str | None) -> None:
    if not token or name not in _PROVIDERS:
        return
    state = _PROVIDERS[name]
    with state.lock:
        if state.reservation == token:
            state.reservation = None


@contextmanager
def _lease(name: str, reservation_token: str | None = None):
    from searx.exceptions import SearxEngineResponseException

    state = _PROVIDERS[name]
    with state.lock:
        now = time.monotonic()
        if reservation_token is not None:
            if state.reservation != reservation_token or state.active:
                raise SearxEngineResponseException(
                    f"{name}: provider reservation is invalid or no longer active"
                )
            state.reservation = None
        elif (
            state.active
            or state.reservation is not None
            or now - state.last_start < MINIMUM_START_INTERVAL
        ):
            raise SearxEngineResponseException(f"{name}: provider is busy or cooling down")
        state.active = True
    try:
        yield lambda: _record_start(state)
    finally:
        with state.lock:
            state.active = False


def _record_start(state: _ProviderState) -> bool:
    with state.lock:
        state.last_start = time.monotonic()
    return True


def _mapped_failure(engine_name: str, exc: ObscuraClientError):
    from searx.exceptions import SearxEngineAccessDeniedException
    from searx.exceptions import SearxEngineResponseException

    if exc.category is FetchFailure.RATE_LIMITED:
        from searx.exceptions import SearxEngineTooManyRequestsException
        return SearxEngineTooManyRequestsException(message=f"{engine_name}: provider rate limited")
    if exc.category in {FetchFailure.ACCESS_DENIED, FetchFailure.POLICY_DENIED}:
        return SearxEngineAccessDeniedException(message=f"{engine_name}: provider access denied")
    return SearxEngineResponseException(f"{engine_name}: browser request failed ({exc.category.value})")


def navigate(
    engine_name: str,
    target_url: str,
    reservation_token: str | None = None,
) -> str:
    """Navigate once and return a bounded rendered DOM; never retry."""
    from searx.exceptions import SearxEngineAccessDeniedException
    from searx.exceptions import SearxEngineCaptchaException
    from searx.exceptions import SearxEngineResponseException
    from searx.exceptions import SearxEngineTooManyRequestsException

    with _lease(engine_name, reservation_token) as record_start:
        try:
            result = fetch_sync(
                target_url,
                cdp_url=CDP_URL,
                wait_until=WAIT_UNTIL,
                allow_http=ALLOW_HTTP,
                body_limit=SEARCH_DOM_LIMIT,
                dom_limit=SEARCH_DOM_LIMIT,
                want="dom",
                request_timeout_seconds=SEARCH_BROWSER_ATTEMPT_TIMEOUT_SECONDS,
                pre_navigation_guard=record_start,
            )
        except ObscuraClientError as exc:
            raise _mapped_failure(engine_name, exc) from exc

        host = (urlsplit(result.final_url).hostname or "").rstrip(".").lower()
        if host not in TERMINAL_HOSTS[engine_name] or host == "consent.google.com":
            raise SearxEngineAccessDeniedException(
                message=f"{engine_name}: unexpected or consent terminal origin"
            )
        if result.status == 429:
            raise SearxEngineTooManyRequestsException(message=f"{engine_name}: HTTP 429")
        if result.status in {401, 402, 403}:
            raise SearxEngineAccessDeniedException(message=f"{engine_name}: access denied")
        if result.status == 404 or result.status >= 500:
            raise SearxEngineResponseException(f"{engine_name}: provider HTTP failure")
        if result.challenge is FetchFailure.CAPTCHA:
            raise SearxEngineCaptchaException(message=f"{engine_name}: verification page")
        dom = result.rendered_html or ""
        if not dom:
            raise SearxEngineResponseException(f"{engine_name}: empty rendered DOM")
        from lxml import html as lxml_html

        parsed = lxml_html.fromstring(dom)
        markers = parsed.xpath(BLOCK_MARKER_XPATH)
        if markers:
            title = " ".join(parsed.xpath("//title//text()")[:1]).lower()
            if "too many requests" in title:
                raise SearxEngineTooManyRequestsException(
                    message=f"{engine_name}: too many requests marker"
                )
            if "access denied" in title:
                raise SearxEngineAccessDeniedException(
                    message=f"{engine_name}: access-denied marker"
                )
            raise SearxEngineCaptchaException(
                message=f"{engine_name}: verification marker"
            )
        return dom


def parser_mismatch(engine_name: str, html_text: str, reason: str):
    from searx.exceptions import SearxEngineResponseException

    logger.warning("%s: parser mismatch (%s; html_len=%d)", engine_name, reason, len(html_text))
    raise SearxEngineResponseException(f"{engine_name}: provider DOM did not match expected structure")
