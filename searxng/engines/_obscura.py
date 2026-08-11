# SPDX-License-Identifier: AGPL-3.0-or-later
"""Homepage-first Obscura submission and provider admission for offline engines."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import threading
import time
import uuid
from contextlib import contextmanager

from private_onyx_obscura import (
    AnubisChallenge,
    AnubisSolution,
    AnubisSolverError,
    FetchFailure,
    ObscuraClientError,
    PendingAnubisPow,
    SearchBrowserSession,
    SearchInteractionSpec,
    submit_search as submit_search_async,
    abort_anubis_pow as abort_anubis_pow_async,
    resume_anubis_pow as resume_anubis_pow_async,
    solve_anubis_fast,
    validate_wait_until,
)

logger = logging.getLogger("searx.engines._obscura")
# SearXNG's default root level is warning.  These two loggers emit only the
# URL- and query-free lifecycle fields audited below, so keep their information
# diagnostics visible without enabling verbose provider or framework logging.
logger.setLevel(logging.INFO)
logging.getLogger("private_onyx_obscura").setLevel(logging.INFO)
CDP_URL = os.environ.get(
    "SEARXNG_OBSCURA_CDP_URL", "ws://obscura:9222/devtools/browser"
)
WAIT_UNTIL = validate_wait_until(
    os.environ.get("OBSCURA_BROWSER_WAIT_UNTIL_SEARCH", "networkidle2")
)
SEARCH_DOM_LIMIT = 20 * 1024 * 1024
SEARCH_BROWSER_ATTEMPT_TIMEOUT_SECONDS = 50.0
SEARCH_PRE_NAVIGATION_TIMEOUT_SECONDS = 45.0
SEARCH_CLEANUP_TIMEOUT_SECONDS = 5.0
ENGINE_OUTCOME_HEADROOM_SECONDS = 1.0
MINIMUM_START_INTERVAL = 3.0
PROVIDER_SESSION_IDLE_SECONDS = 3600.0
RESERVATION_PARAM = "_wrapper_obscura_reservation_token"
PRE_NAVIGATION_GUARD_PARAM = "_wrapper_obscura_pre_navigation_guard"

INTERACTIONS = {
    "google2": SearchInteractionSpec(
        homepage_url="https://www.google.com/",
        allowed_homepage_hosts=frozenset({"www.google.com", "google.com"}),
        allowed_result_hosts=frozenset({"www.google.com", "google.com"}),
        query_selector='textarea[name="q"]',
        query_field_name="q",
        form_action_path="/search",
        form_method="get",
        allowed_fixed_field_names=frozenset({"hl", "udm", "start", "tbs"}),
    ),
    "brave2": SearchInteractionSpec(
        homepage_url="https://search.brave.com/",
        allowed_homepage_hosts=frozenset({"search.brave.com"}),
        allowed_result_hosts=frozenset({"search.brave.com"}),
        query_selector='textarea[name="q"]',
        query_field_name="q",
        form_action_path="/search",
        form_method="get",
        allowed_fixed_field_names=frozenset({"tf", "offset"}),
    ),
    "duckduckgo2": SearchInteractionSpec(
        homepage_url="https://noai.duckduckgo.com/",
        allowed_homepage_hosts=frozenset({"noai.duckduckgo.com"}),
        allowed_result_hosts=frozenset({"noai.duckduckgo.com"}),
        query_selector='input[name="q"]:not([type="hidden"])',
        query_field_name="q",
        form_action_path="/",
        form_method="get",
        allowed_fixed_field_names=frozenset({"ia"}),
        result_terminal_selector=(
            'li[data-layout="organic"],'
            '[data-testid="no-results"],'
            '[data-layout="no-results"],'
            '.no-results,'
            'form#challenge-form,'
            'form[action*="duckduckgo.com/anomaly.js"],'
            'form[action*="cc=botnet"],'
            '.anomaly-modal__modal'
        ),
        result_pending_selector=(
            'link#deep_preload_link[href*="links.duckduckgo.com/d.js"],'
            'script#deep_preload_script[src*="links.duckduckgo.com/d.js"]'
        ),
    ),
    "startpage2": SearchInteractionSpec(
        homepage_url="https://www.startpage.com/",
        allowed_homepage_hosts=frozenset({"www.startpage.com", "startpage.com"}),
        allowed_result_hosts=frozenset({"www.startpage.com", "startpage.com"}),
        query_selector="input#q",
        query_field_name="query",
        form_action_path="/sp/search",
        form_method="post",
        allowed_fixed_field_names=frozenset({"cat", "page"}),
        anubis_pow=True,
    ),
    "bing2": SearchInteractionSpec(
        homepage_url="https://www.bing.com/",
        allowed_homepage_hosts=frozenset({"www.bing.com", "bing.com"}),
        allowed_result_hosts=frozenset({"www.bing.com", "bing.com"}),
        query_selector='input[name="q"],textarea[name="q"]',
        query_field_name="q",
        form_action_path="/search",
        form_method="get",
        allowed_fixed_field_names=frozenset({"adlt", "setlang", "first"}),
    ),
}
PROVIDER_NAMES = tuple(INTERACTIONS)


def _parse_timed_typing_providers(value: str) -> frozenset[str]:
    if value == "none":
        return frozenset()
    if value == "all":
        return frozenset(PROVIDER_NAMES)
    if not value or any(character.isspace() for character in value):
        raise ValueError("SEARXNG_TIMED_TYPING_PROVIDERS has invalid whitespace")
    names = value.split(",")
    if (
        any(not name for name in names)
        or len(names) != len(set(names))
        or any(name not in INTERACTIONS for name in names)
    ):
        raise ValueError("SEARXNG_TIMED_TYPING_PROVIDERS is invalid")
    return frozenset(names)


TIMED_TYPING_PROVIDERS = _parse_timed_typing_providers(
    os.environ.get("SEARXNG_TIMED_TYPING_PROVIDERS", "none")
)


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
        session_factory=SearchBrowserSession,
        submit_async=submit_search_async,
        resume_async=resume_anubis_pow_async,
        abort_async=abort_anubis_pow_async,
    ) -> None:
        if idle_seconds <= 0:
            raise ValueError("provider session idle timeout must be positive")
        self.engine_name = engine_name
        self.idle_seconds = idle_seconds
        self._session_factory = session_factory
        self._submit_async = submit_async
        self._resume_async = resume_async
        self._abort_async = abort_async
        self._session: SearchBrowserSession | None = None
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

    async def _run_submit(self, query: str, **kwargs):
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
        reused = (
            self._session is not None
            and getattr(self._session, "generation_active", True)
        )
        if self._session is None:
            self._session = self._session_factory()
        session = self._session
        logger.info(
            "%s: browser session query_sequence=%d reused=%s",
            self.engine_name,
            generation,
            str(reused).lower(),
        )
        try:
            return await self._submit_async(
                query,
                session_owner=session,
                **kwargs,
            )
        finally:
            if getattr(session, "_pending_anubis", None) is not None:
                self._idle_deadline = None
                self._idle_handle = None
            elif getattr(session, "generation_active", True):
                self._idle_deadline = loop.time() + self.idle_seconds
                self._idle_handle = loop.call_at(
                    self._idle_deadline,
                    self._begin_expiry,
                    generation,
                )
            elif self._session is session:
                # The shared client already closed an ambiguous generation.
                # Discard its empty owner immediately instead of reporting the
                # next connection as reused and scheduling a redundant close.
                self._session = None

    def submit_sync(self, query: str, **kwargs):
        return _PROVIDER_BROWSER_LOOP.submit(
            self._run_submit(query, **kwargs)
        ).result()

    async def _run_resume(self, continuation_token, solution):
        if self._session is None:
            raise ObscuraClientError(
                FetchFailure.PROTOCOL,
                "anubis-continuation",
                "provider browser has no pending session",
            )
        session = self._session
        generation = self._generation
        loop = asyncio.get_running_loop()
        try:
            return await self._resume_async(
                continuation_token,
                solution,
                session_owner=session,
            )
        finally:
            if getattr(session, "generation_active", True):
                self._idle_deadline = loop.time() + self.idle_seconds
                self._idle_handle = loop.call_at(
                    self._idle_deadline,
                    self._begin_expiry,
                    generation,
                )
            elif self._session is session:
                self._session = None

    def resume_sync(self, continuation_token, solution):
        return _PROVIDER_BROWSER_LOOP.submit(
            self._run_resume(continuation_token, solution)
        ).result()

    def abort_sync(self, continuation_token) -> None:
        async def _abort() -> None:
            if self._session is None:
                return
            session = self._session
            try:
                await self._abort_async(
                    continuation_token,
                    session_owner=session,
                )
            finally:
                if self._session is session:
                    self._session = None

        _PROVIDER_BROWSER_LOOP.submit(_abort()).result()

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
        self.engine_deadline: float | None = None
        self.reservation: str | None = None
        self.last_start = float("-inf")
        self.browser: _ProviderBrowserSession | None = None


_PROVIDERS = {name: _ProviderState() for name in INTERACTIONS}
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
def provider_lease(
    name: str,
    reservation_token: str | None = None,
    *,
    engine_deadline: float | None = None,
):
    """Own one provider through navigation, parsing, and outcome recording."""
    from searx.exceptions import SearxEngineResponseException

    if engine_deadline is not None and not math.isfinite(engine_deadline):
        raise SearxEngineResponseException(
            f"{name}: provider engine deadline is invalid"
        )
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
        state.engine_deadline = engine_deadline
    try:
        yield lambda: _record_start(state)
    finally:
        with state.lock:
            state.active = False
            state.engine_deadline = None
        _provider_capacity_changed()


def _record_start(state: _ProviderState) -> bool:
    while True:
        with state.lock:
            now = time.monotonic()
            remaining = MINIMUM_START_INTERVAL - (now - state.last_start)
            if remaining <= 0:
                state.last_start = now
                return True
        # A single Bing engine attempt can deliberately submit sparse page 2.
        # Recheck at the actual pre-navigation boundary so its second homepage
        # navigation preserves the same exact provider start interval.
        time.sleep(remaining)


def _provider_browser(name: str) -> _ProviderBrowserSession:
    state = _PROVIDERS[name]
    with state.lock:
        if state.browser is None:
            state.browser = _ProviderBrowserSession(name)
        return state.browser


def _mapped_failure(engine_name: str, exc: ObscuraClientError):
    from searx.exceptions import SearxEngineAccessDeniedException
    from searx.exceptions import SearxEngineCaptchaException
    from searx.exceptions import SearxEngineResponseException

    if exc.category is FetchFailure.RATE_LIMITED:
        from searx.exceptions import SearxEngineTooManyRequestsException
        return SearxEngineTooManyRequestsException(message=f"{engine_name}: provider rate limited")
    if exc.category in {FetchFailure.ACCESS_DENIED, FetchFailure.POLICY_DENIED}:
        return SearxEngineAccessDeniedException(message=f"{engine_name}: provider access denied")
    if exc.category is FetchFailure.CAPTCHA:
        return SearxEngineCaptchaException(message=f"{engine_name}: verification page")
    return SearxEngineResponseException(f"{engine_name}: browser request failed ({exc.category.value})")


def submit_search(
    engine_name: str,
    query: str,
    fixed_fields: tuple[tuple[str, str], ...],
    pre_navigation_guard,
) -> str:
    """Return the bounded submitted-result DOM for one leased provider attempt."""
    from searx.exceptions import SearxEngineAccessDeniedException
    from searx.exceptions import SearxEngineCaptchaException
    from searx.exceptions import SearxEngineResponseException
    from searx.exceptions import SearxEngineTooManyRequestsException

    if not callable(pre_navigation_guard):
        raise SearxEngineResponseException(
            f"{engine_name}: provider submission is missing its SearXNG lease guard"
        )
    spec = INTERACTIONS.get(engine_name)
    if spec is None:
        raise SearxEngineResponseException(
            f"{engine_name}: unknown browser interaction"
        )
    try:
        attempt_started = time.monotonic()
        state = _PROVIDERS[engine_name]
        with state.lock:
            engine_deadline = state.engine_deadline
        request_timeout_seconds = SEARCH_BROWSER_ATTEMPT_TIMEOUT_SECONDS
        if engine_deadline is not None:
            available = (
                engine_deadline
                - time.monotonic()
                - ENGINE_OUTCOME_HEADROOM_SECONDS
            )
            if available <= 0:
                raise ObscuraClientError(
                    FetchFailure.PRE_NAVIGATION_TIMEOUT,
                    "search-engine-deadline",
                    "SearXNG engine deadline leaves no browser transaction budget",
                )
            request_timeout_seconds = min(
                request_timeout_seconds,
                available,
            )
        browser = _provider_browser(engine_name)
        result = browser.submit_sync(
            query,
            spec=spec,
            fixed_fields=fixed_fields,
            text_entry_mode=(
                "timed" if engine_name in TIMED_TYPING_PROVIDERS else "instant"
            ),
            cdp_url=CDP_URL,
            wait_until=WAIT_UNTIL,
            dom_limit=SEARCH_DOM_LIMIT,
            pre_navigation_timeout_seconds=min(
                SEARCH_PRE_NAVIGATION_TIMEOUT_SECONDS,
                request_timeout_seconds,
            ),
            cleanup_command_timeout_seconds=SEARCH_CLEANUP_TIMEOUT_SECONDS,
            request_timeout_seconds=request_timeout_seconds,
            pre_navigation_guard=pre_navigation_guard,
        )
        if isinstance(result, PendingAnubisPow):
            solver_deadline = attempt_started + request_timeout_seconds
            try:
                solution = solve_anubis_fast(
                    result.challenge,
                    deadline=solver_deadline,
                )
            except AnubisSolverError as exc:
                try:
                    browser.abort_sync(result.continuation_token)
                except Exception as cleanup_exc:
                    logger.warning(
                        "%s: Anubis solver abort failed (%s)",
                        engine_name,
                        cleanup_exc.__class__.__name__,
                    )
                raise ObscuraClientError(
                    FetchFailure.POST_NAVIGATION_TIMEOUT,
                    "anubis-local-solver",
                    "Anubis proof did not complete inside its local bounds",
                ) from exc
            result = browser.resume_sync(result.continuation_token, solution)
    except ObscuraClientError as exc:
        raise _mapped_failure(engine_name, exc) from exc

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
    dom = result.rendered_html
    if not dom:
        raise SearxEngineResponseException(f"{engine_name}: empty rendered DOM")
    return dom


def parser_mismatch(engine_name: str, html_text: str, reason: str):
    from searx.exceptions import SearxEngineResponseException

    logger.warning("%s: parser mismatch (%s; html_len=%d)", engine_name, reason, len(html_text))
    raise SearxEngineResponseException(f"{engine_name}: provider DOM did not match expected structure")
