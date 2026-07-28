# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared direct-Obscura navigation and provider admission for offline engines."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from urllib.parse import urlsplit

from private_onyx_obscura import (
    FetchFailure,
    ObscuraClientError,
    ObscuraSession,
    fetch,
    validate_wait_until,
)

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
PROVIDER_SESSION_IDLE_SECONDS = 3600.0
RESERVATION_PARAM = "_wrapper_obscura_reservation_token"
PRE_NAVIGATION_GUARD_PARAM = "_wrapper_obscura_pre_navigation_guard"

TERMINAL_HOSTS = {
    "google2": frozenset({"www.google.com", "google.com", "consent.google.com"}),
    "bing2": frozenset({"www.bing.com", "bing.com"}),
    "duckduckgo2": frozenset({"html.duckduckgo.com", "www.duckduckgo.com", "duckduckgo.com"}),
    "brave2": frozenset({"search.brave.com"}),
    "startpage2": frozenset({"www.startpage.com", "startpage.com"}),
}


class _ProviderBrowserLoop:
    """One lazy event loop for every provider-owned Obscura connection."""

    def __init__(self) -> None:
        self._start_lock = threading.Lock()
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    def ensure_started(self) -> asyncio.AbstractEventLoop:
        with self._start_lock:
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._thread_main,
                    name="searxng-obscura-providers",
                    daemon=True,
                )
                self._thread.start()
        self._ready.wait()
        if self._loop is None:
            raise RuntimeError("provider browser event loop did not start")
        return self._loop

    def submit(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.ensure_started())

    def stop(self) -> None:
        loop = self._loop
        thread = self._thread
        if loop is None or thread is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5.0)
        if thread.is_alive():
            raise RuntimeError("provider browser event loop did not stop")
        self._loop = None
        self._thread = None
        self._ready.clear()


_PROVIDER_BROWSER_LOOP = _ProviderBrowserLoop()


class _ProviderBrowserSession:
    """Own one provider's idle-expiring Obscura connection."""

    def __init__(
        self,
        engine_name: str,
        *,
        idle_seconds: float = PROVIDER_SESSION_IDLE_SECONDS,
        session_factory=ObscuraSession,
        fetch_async=fetch,
    ) -> None:
        if idle_seconds <= 0:
            raise ValueError("provider session idle timeout must be positive")
        self.engine_name = engine_name
        self.idle_seconds = idle_seconds
        self._session_factory = session_factory
        self._fetch_async = fetch_async
        self._session: ObscuraSession | None = None
        self._idle_handle: asyncio.TimerHandle | None = None
        self._idle_deadline: float | None = None
        self._expiry_task: asyncio.Task | None = None
        self._generation = 0

    async def _expire(self, generation: int) -> None:
        if generation != self._generation or self._session is None:
            return
        session = self._session
        self._session = None
        self._idle_handle = None
        self._idle_deadline = None
        try:
            await session.close()
        except Exception as exc:
            logger.warning(
                "%s: idle browser session close failed (%s)",
                self.engine_name,
                exc.__class__.__name__,
            )
        else:
            logger.info("%s: expired idle browser session", self.engine_name)

    def _begin_expiry(self, generation: int) -> None:
        self._idle_handle = None
        self._idle_deadline = None
        task = asyncio.get_running_loop().create_task(self._expire(generation))
        self._expiry_task = task

        def _clear_expiry_task(completed: asyncio.Task) -> None:
            if self._expiry_task is completed:
                self._expiry_task = None

        task.add_done_callback(_clear_expiry_task)

    async def _run_fetch(self, url: str, **kwargs):
        loop = asyncio.get_running_loop()
        if self._idle_handle is not None:
            deadline_reached = (
                self._idle_deadline is not None
                and loop.time() >= self._idle_deadline
            )
            self._idle_handle.cancel()
            self._idle_handle = None
            self._idle_deadline = None
            if deadline_reached:
                # Timer callbacks are not lifecycle authority: a suspended or
                # briefly blocked event loop may not run one at its deadline.
                # Enforce an already-reached deadline synchronously so the next
                # query cannot revive an expired or challenged connection.
                await self._expire(self._generation)
        expiry_task = self._expiry_task
        if expiry_task is not None:
            # Once the idle deadline has started closing a connection, finish
            # that close before opening its replacement. This prevents a query
            # arriving on the expiry boundary from temporarily consuming two
            # provider connection slots or trying to reuse a closing session.
            await asyncio.shield(expiry_task)
        self._generation += 1
        generation = self._generation
        if self._session is None:
            self._session = self._session_factory()
        try:
            return await self._fetch_async(
                url,
                session_owner=self._session,
                **kwargs,
            )
        finally:
            self._idle_deadline = loop.time() + self.idle_seconds
            self._idle_handle = loop.call_at(
                self._idle_deadline,
                self._begin_expiry,
                generation,
            )

    def fetch_sync(self, url: str, **kwargs):
        return _PROVIDER_BROWSER_LOOP.submit(self._run_fetch(url, **kwargs)).result()

    def close(self) -> None:
        if (
            self._session is None
            and self._idle_handle is None
            and self._expiry_task is None
        ):
            return

        async def _close() -> None:
            if self._idle_handle is not None:
                self._idle_handle.cancel()
                self._idle_handle = None
                self._idle_deadline = None
            expiry_task = self._expiry_task
            if expiry_task is not None:
                await asyncio.shield(expiry_task)
            if self._session is not None:
                session = self._session
                self._session = None
                await session.close()

        _PROVIDER_BROWSER_LOOP.submit(_close()).result()


class _ProviderState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = False
        self.reservation: str | None = None
        self.last_start = float("-inf")
        self.browser: _ProviderBrowserSession | None = None


_PROVIDERS = {name: _ProviderState() for name in TERMINAL_HOSTS}
_PROVIDER_CAPACITY_CONDITION = threading.Condition()
_PROVIDER_CAPACITY_GENERATION = 0


def _provider_capacity_changed() -> None:
    global _PROVIDER_CAPACITY_GENERATION

    with _PROVIDER_CAPACITY_CONDITION:
        _PROVIDER_CAPACITY_GENERATION += 1
        _PROVIDER_CAPACITY_CONDITION.notify_all()


def provider_capacity_generation() -> int:
    with _PROVIDER_CAPACITY_CONDITION:
        return _PROVIDER_CAPACITY_GENERATION


def _next_provider_ready_delay(names: tuple[str, ...]) -> float | None:
    now = time.monotonic()
    delays = []
    for name in names:
        state = _PROVIDERS[name]
        with state.lock:
            if state.active or state.reservation is not None:
                continue
            remaining = MINIMUM_START_INTERVAL - (now - state.last_start)
            if remaining <= 0:
                return 0.0
            delays.append(remaining)
    return min(delays) if delays else None


def wait_for_provider_capacity_change(
    generation: int,
    names: tuple[str, ...],
) -> None:
    """Wait until provider ownership changes or the nearest cooldown expires."""
    timeout = _next_provider_ready_delay(names)
    if timeout == 0.0:
        return
    with _PROVIDER_CAPACITY_CONDITION:
        if generation == _PROVIDER_CAPACITY_GENERATION:
            _PROVIDER_CAPACITY_CONDITION.wait(timeout=timeout)


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
    _provider_capacity_changed()
    return token


def release_provider_reservation(name: str, token: str | None) -> None:
    if not token or name not in _PROVIDERS:
        return
    state = _PROVIDERS[name]
    released = False
    with state.lock:
        if state.reservation == token:
            state.reservation = None
            released = True
    if released:
        _provider_capacity_changed()


@contextmanager
def provider_lease(name: str, reservation_token: str | None = None):
    """Own one provider through navigation, parsing, and outcome recording."""
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
        _provider_capacity_changed()


def _record_start(state: _ProviderState) -> bool:
    with state.lock:
        state.last_start = time.monotonic()
    return True


def _provider_browser(name: str) -> _ProviderBrowserSession:
    state = _PROVIDERS[name]
    with state.lock:
        if state.browser is None:
            state.browser = _ProviderBrowserSession(name)
        return state.browser


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
    pre_navigation_guard,
) -> str:
    """Navigate once and return a bounded rendered DOM; never retry."""
    from searx.exceptions import SearxEngineAccessDeniedException
    from searx.exceptions import SearxEngineCaptchaException
    from searx.exceptions import SearxEngineResponseException
    from searx.exceptions import SearxEngineTooManyRequestsException

    if not callable(pre_navigation_guard):
        raise SearxEngineResponseException(
            f"{engine_name}: provider navigation is missing its SearXNG lease guard"
        )
    try:
        result = _provider_browser(engine_name).fetch_sync(
            target_url,
            cdp_url=CDP_URL,
            wait_until=WAIT_UNTIL,
            allow_http=ALLOW_HTTP,
            body_limit=SEARCH_DOM_LIMIT,
            dom_limit=SEARCH_DOM_LIMIT,
            want="dom",
            request_timeout_seconds=SEARCH_BROWSER_ATTEMPT_TIMEOUT_SECONDS,
            pre_navigation_guard=pre_navigation_guard,
        )
    except ObscuraClientError as exc:
        raise _mapped_failure(engine_name, exc) from exc

    host = (urlsplit(result.final_url).hostname or "").rstrip(".").lower()
    if host not in TERMINAL_HOSTS[engine_name] or host == "consent.google.com":
        raise SearxEngineAccessDeniedException(
            message=f"{engine_name}: unexpected or consent terminal origin"
        )
    if result.challenge is FetchFailure.RATE_LIMITED:
        raise SearxEngineTooManyRequestsException(
            message=f"{engine_name}: provider rate limited"
        )
    if result.challenge is FetchFailure.ACCESS_DENIED:
        raise SearxEngineAccessDeniedException(
            message=f"{engine_name}: provider access denied"
        )
    if result.challenge is FetchFailure.CAPTCHA:
        raise SearxEngineCaptchaException(
            message=f"{engine_name}: verification page"
        )
    if result.status == 404 or result.status >= 500:
        raise SearxEngineResponseException(f"{engine_name}: provider HTTP failure")
    dom = result.rendered_html or ""
    if not dom:
        raise SearxEngineResponseException(f"{engine_name}: empty rendered DOM")
    return dom


def parser_mismatch(engine_name: str, html_text: str, reason: str):
    from searx.exceptions import SearxEngineResponseException

    logger.warning("%s: parser mismatch (%s; html_len=%d)", engine_name, reason, len(html_text))
    raise SearxEngineResponseException(f"{engine_name}: provider DOM did not match expected structure")
