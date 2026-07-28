"""Audited, one-navigation Obscura CDP client shared by Onyx and SearXNG.

The main request is always a raw ``Page.navigate``.  There is deliberately no
HTTP probe, browser reconnect, navigation retry, or local-renderer fallback.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import logging
import math
import re
import socket
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from html.parser import HTMLParser
from typing import Callable, Literal
from urllib.parse import SplitResult, unquote, urlsplit, urlunsplit

LOGGER = logging.getLogger("private_onyx_obscura")
PRE_NAVIGATION_TIMEOUT_SECONDS = 45.0
CLEANUP_COMMAND_TIMEOUT_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 105.0
ALLOWED_WAIT_UNTIL = frozenset(
    {"domcontentloaded", "load", "networkidle0", "networkidle2"}
)
INTERNAL_SUFFIXES = (
    ".local",
    ".internal",
    ".home.arpa",
    ".localhost",
    ".docker.internal",
    ".podman.internal",
)
INTERNAL_NAMES = frozenset(
    {
        "localhost",
        "host.docker.internal",
        "host.containers.internal",
        "gateway.docker.internal",
        "metadata.google.internal",
        "metadata",
    }
)


class FetchFailure(StrEnum):
    INVALID_URL = "invalid-url"
    POLICY_DENIED = "final-hop-policy-denied"
    PRE_NAVIGATION_TIMEOUT = "pre-navigation-timeout"
    POST_NAVIGATION_TIMEOUT = "post-navigation-timeout"
    NAVIGATION_TIMEOUT = "navigation-timeout"
    HTTP_STATUS = "http-status"
    ACCESS_DENIED = "access-denied"
    RATE_LIMITED = "rate-limited"
    CAPTCHA = "captcha"
    OVERSIZE = "oversize"
    BODY_UNAVAILABLE = "body-unavailable"
    BYTE_IDENTITY_UNAVAILABLE = "byte-identity-unavailable"
    UNSUPPORTED_CHARSET = "unsupported-charset"
    EMPTY_CONTENT = "empty-content"
    PROTOCOL = "cdp-protocol"
    TRANSPORT = "cdp-transport"
    FINALIZED = "invocation-finalized"


class BodyClassification(StrEnum):
    TEXT = "text"
    BINARY = "binary"


class ObscuraClientError(RuntimeError):
    """Non-secret typed request failure."""

    def __init__(self, category: FetchFailure, stage: str, message: str):
        super().__init__(message)
        self.category = category
        self.stage = stage


@dataclass(frozen=True)
class FetchResult:
    requested_url: str
    navigation_url: str
    final_url: str
    status: int
    status_text: str
    headers: dict[str, str]
    content_type: str | None
    charset: str | None
    frame_id: str
    loader_id: str
    request_id: str
    body_classification: BodyClassification
    original_byte_identity: bool
    lossy_conversion_possible: bool
    completed_body_bytes: int
    rendered_html: str | None
    body: bytes | None
    body_failure: FetchFailure | None
    navigation_seconds: float
    body_read_seconds: float
    challenge: FetchFailure | None


def validate_wait_until(value: str) -> str:
    if value not in ALLOWED_WAIT_UNTIL:
        raise ValueError(
            "waitUntil must be exactly one of domcontentloaded, load, "
            "networkidle0, or networkidle2"
        )
    return value


def _public_ip_literal(host: str) -> bool:
    candidate = host.strip("[]")
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return True
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_unspecified,
            address.is_reserved,
        )
    )


def normalize_public_url(
    url: str, *, allow_http: bool, allow_http_onion: bool = False
) -> tuple[str, str | None]:
    """Validate without resolving the target and remove its fragment."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ObscuraClientError(
            FetchFailure.INVALID_URL, "validate", "malformed URL"
        ) from exc
    if parsed.username is not None or parsed.password is not None:
        raise ObscuraClientError(
            FetchFailure.INVALID_URL, "validate", "URL credentials are forbidden"
        )
    if not parsed.hostname:
        raise ObscuraClientError(FetchFailure.INVALID_URL, "validate", "URL host is required")
    try:
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ObscuraClientError(
            FetchFailure.INVALID_URL, "validate", "URL host is invalid"
        ) from exc
    if parsed.scheme not in {"http", "https"} or (
        parsed.scheme == "http"
        and not (allow_http or (allow_http_onion and host.endswith(".onion")))
    ):
        raise ObscuraClientError(
            FetchFailure.INVALID_URL, "validate", "URL scheme is not allowed"
        )
    if (
        host in INTERNAL_NAMES
        or "." not in host
        or any(host.endswith(suffix) for suffix in INTERNAL_SUFFIXES)
        or not _public_ip_literal(host)
    ):
        raise ObscuraClientError(
            FetchFailure.INVALID_URL, "validate", "URL host is not public"
        )
    display_host = f"[{host}]" if ":" in host else host
    netloc = display_host + (f":{port}" if port is not None else "")
    normalized = urlunsplit(
        SplitResult(parsed.scheme, netloc, parsed.path or "/", parsed.query, "")
    )
    return normalized, parsed.fragment or None


def is_text_like_content_type(content_type: str | None) -> bool:
    """Mirror the pinned Obscura ``is_text_like_content_type`` exactly."""
    if content_type is None:
        return True
    essence = content_type.split(";", 1)[0].strip().lower()
    if not essence:
        return True
    return (
        essence.startswith("text/")
        or essence
        in {
            "application/json",
            "application/xml",
            "application/xhtml+xml",
            "application/javascript",
            "application/ecmascript",
            "image/svg+xml",
        }
        or essence.endswith("+json")
        or essence.endswith("+xml")
    )


def _can_preserve_html_dom_without_body(
    want: str,
    content_type: str | None,
    exc: ObscuraClientError,
) -> bool:
    return (
        want == "both"
        and content_type in {"text/html", "application/xhtml+xml"}
        and exc.category is FetchFailure.BODY_UNAVAILABLE
    )


def _normalize_headers(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ObscuraClientError(
            FetchFailure.PROTOCOL, "events", "document response headers are invalid"
        )
    normalized: dict[str, str] = {}
    for key, item in value.items():
        name = str(key).strip().lower()
        rendered = str(item).strip()
        if not name or "\r" in name or "\n" in name or "\r" in rendered or "\n" in rendered:
            raise ObscuraClientError(
                FetchFailure.PROTOCOL, "events", "document response headers are ambiguous"
            )
        if name in normalized and normalized[name] != rendered:
            raise ObscuraClientError(
                FetchFailure.PROTOCOL, "events", "duplicate response headers are ambiguous"
            )
        normalized[name] = rendered
    return normalized


def _content_type(headers: dict[str, str]) -> tuple[str | None, str | None]:
    raw = headers.get("content-type")
    if raw is None:
        return None, None
    fields = [field.strip() for field in raw.split(";")]
    essence = fields[0].lower() or None
    charset = None
    for field in fields[1:]:
        if field.lower().startswith("charset="):
            charset = field.split("=", 1)[1].strip().strip('"').lower() or None
    return essence, charset


class _ChallengeSignals(HTMLParser):
    """Extract bounded challenge signals without treating script text as content."""

    _IGNORED = frozenset({"script", "style", "template", "noscript"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ignored_depth = 0
        self.title_depth = 0
        self.visible: list[str] = []
        self.title: list[str] = []
        self.challenge_structure = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._IGNORED:
            self.ignored_depth += 1
            return
        if self.ignored_depth:
            return
        if tag == "title":
            self.title_depth += 1
        values = {name.lower(): (value or "").lower() for name, value in attrs}
        candidate = values.get("action", "") if tag == "form" else values.get("src", "")
        if tag == "form" and any(
            marker in candidate for marker in ("/sorry/", "/captcha", "/sp/captcha", "turing")
        ):
            self.challenge_structure = True
        if tag == "iframe" and any(
            marker in candidate for marker in ("recaptcha", "hcaptcha", "/challenge")
        ):
            self.challenge_structure = True

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._IGNORED:
            self.ignored_depth = max(0, self.ignored_depth - 1)
            return
        if not self.ignored_depth and tag == "title":
            self.title_depth = max(0, self.title_depth - 1)

    def handle_data(self, data: str) -> None:
        if self.ignored_depth or not data.strip():
            return
        self.visible.append(data)
        if self.title_depth:
            self.title.append(data)


def _challenge_details(
    status: int, final_url: str, html: str | None
) -> tuple[FetchFailure | None, str]:
    if status == 429:
        return FetchFailure.RATE_LIMITED, "http-429"
    if status in {401, 402, 403}:
        return FetchFailure.ACCESS_DENIED, "http-denial-status"

    parser = _ChallengeSignals()
    try:
        parser.feed((html or "")[:262_144])
        parser.close()
    except Exception:
        return None, "html-signal-parse-failed"

    visible = " ".join(" ".join(parser.visible).lower().split())
    title = " ".join(" ".join(parser.title).lower().split())
    parsed_url = urlsplit(final_url)
    route = unquote(f"{parsed_url.path}?{parsed_url.query}").lower()

    if any(
        marker in route
        for marker in ("/sorry/", "/captcha", "/sp/captcha", "/challenge/", "/verify/")
    ):
        return FetchFailure.CAPTCHA, "terminal-challenge-route"
    if any(
        marker in title
        for marker in (
            "verify you are human",
            "captcha",
            "attention required",
            "security check",
            "just a moment",
        )
    ):
        return FetchFailure.CAPTCHA, "challenge-title"
    if any(
        marker in visible
        for marker in (
            "verify you are human",
            "complete the captcha",
            "solve the captcha",
            "unusual traffic from your computer network",
            "checking your browser before accessing",
        )
    ):
        return FetchFailure.CAPTCHA, "visible-human-verification"
    if parser.challenge_structure and any(
        marker in visible
        for marker in (
            "captcha",
            "i am not a robot",
            "i'm not a robot",
            "verification required",
        )
    ):
        return FetchFailure.CAPTCHA, "visible-structured-challenge"
    if any(marker in title for marker in ("access denied", "request blocked", "forbidden")):
        return FetchFailure.ACCESS_DENIED, "denial-title"
    return None, "none"


def _challenge(status: int, final_url: str, html: str | None) -> FetchFailure | None:
    return _challenge_details(status, final_url, html)[0]


async def _drain_body(
    session,
    request_id: str,
    expected: int,
    limit: int,
    *,
    diagnostic_id: str,
    cleanup_command_timeout_seconds: float,
    command_timeout: Callable[[str], float],
    cleanup_failure: Callable[[], None] | None = None,
) -> bytes:
    handle: str | None = None
    data = bytearray()
    try:
        opened = await session.send(
            "Fetch.takeResponseBodyAsStream",
            {"requestId": request_id},
            timeout_seconds=command_timeout("body-stream-open"),
            timeout_category=FetchFailure.POST_NAVIGATION_TIMEOUT,
            timeout_stage="body-stream-open",
        )
        handle = opened.get("stream")
        if not isinstance(handle, str) or not handle:
            raise ObscuraClientError(
                FetchFailure.PROTOCOL, "body", "Obscura returned an invalid body handle"
            )
        while True:
            chunk = await session.send(
                "IO.read",
                {"handle": handle, "size": 1 << 20},
                timeout_seconds=command_timeout("body-stream-read"),
                timeout_category=FetchFailure.POST_NAVIGATION_TIMEOUT,
                timeout_stage="body-stream-read",
            )
            raw = chunk.get("data", "")
            if not isinstance(raw, str):
                raise ObscuraClientError(
                    FetchFailure.PROTOCOL, "body", "Obscura returned an invalid body chunk"
                )
            try:
                decoded = (
                    base64.b64decode(raw, validate=True)
                    if chunk.get("base64Encoded") is True
                    else raw.encode("utf-8")
                )
            except (ValueError, UnicodeError) as exc:
                raise ObscuraClientError(
                    FetchFailure.PROTOCOL, "body", "Obscura body chunk decoding failed"
                ) from exc
            if len(data) + len(decoded) > limit:
                raise ObscuraClientError(
                    FetchFailure.OVERSIZE, "body", "document exceeds the configured byte limit"
                )
            data.extend(decoded)
            if chunk.get("eof") is True:
                break
        if len(data) != expected:
            raise ObscuraClientError(
                FetchFailure.PROTOCOL, "body", "completed and drained body sizes differ"
            )
        return bytes(data)
    except ObscuraClientError:
        raise
    except Exception as exc:
        raise ObscuraClientError(
            FetchFailure.BODY_UNAVAILABLE, "body", "retained response body is unavailable"
        ) from exc
    finally:
        if handle is not None:
            try:
                await session.send(
                    "IO.close",
                    {"handle": handle},
                    timeout_seconds=cleanup_command_timeout_seconds,
                    timeout_stage="cleanup-body-close",
                )
            except Exception as exc:
                if cleanup_failure is not None:
                    cleanup_failure()
                category = (
                    exc.category.value
                    if isinstance(exc, ObscuraClientError)
                    else FetchFailure.TRANSPORT.value
                )
                LOGGER.warning(
                    "obscura cleanup failed request_id=%s stage=body-close category=%s",
                    diagnostic_id,
                    category,
                )


class _RawCdp:
    """Minimal flattened CDP transport for the audited Obscura surface.

    The pinned server reuses its target's attached session id. Playwright
    1.58's public ``new_cdp_session(page)`` asks for a second attachment with
    that same id and its driver aborts on the duplicate. This transport keeps
    the exact one-session protocol contract without a compatibility shim.
    """

    def __init__(self, websocket):
        self.websocket = websocket
        self.next_id = 0
        self.events: list[dict] = []

    async def send(
        self,
        method: str,
        params: dict | None = None,
        *,
        session_id: str | None = None,
        timeout_seconds: float | None = None,
        timeout_category: FetchFailure = FetchFailure.TRANSPORT,
        timeout_stage: str = "cdp-command",
    ) -> dict:
        self.next_id += 1
        command_id = self.next_id
        command = {"id": command_id, "method": method, "params": params or {}}
        if session_id is not None:
            command["sessionId"] = session_id
        async def _exchange() -> dict:
            await self.websocket.send(json.dumps(command, separators=(",", ":")))
            while True:
                message = await self.websocket.recv()
                if isinstance(message, bytes):
                    message = message.decode("utf-8", "strict")
                packet = json.loads(message)
                if packet.get("id") == command_id:
                    error = packet.get("error")
                    if error:
                        error_message = (
                            str(error.get("message", ""))
                            if isinstance(error, dict)
                            else ""
                        ).upper()
                        navigation_transport_failure = (
                            method == "Page.navigate"
                            and any(
                                token in error_message
                                for token in (
                                    "NETWORK ERROR:",
                                    "ERR_TUNNEL_CONNECTION_FAILED",
                                    "ERR_PROXY_CONNECTION_FAILED",
                                    "ERR_NAME_NOT_RESOLVED",
                                )
                            )
                        )
                        retained_body_unavailable = (
                            method == "Fetch.takeResponseBodyAsStream"
                            and "NO CACHED BODY" in error_message
                        )
                        raise ObscuraClientError(
                            (
                                FetchFailure.TRANSPORT
                                if navigation_transport_failure
                                else FetchFailure.BODY_UNAVAILABLE
                                if retained_body_unavailable
                                else FetchFailure.PROTOCOL
                            ),
                            (
                                "navigation-transport"
                                if navigation_transport_failure
                                else "body-stream-open"
                                if retained_body_unavailable
                                else "cdp-command"
                            ),
                            (
                                "browser proxy could not resolve or connect to destination"
                                if navigation_transport_failure
                                else "same-navigation response body was not retained"
                                if retained_body_unavailable
                                else f"Obscura rejected {method}"
                            ),
                        )
                    result = packet.get("result", {})
                    if not isinstance(result, dict):
                        raise ObscuraClientError(
                            FetchFailure.PROTOCOL, "cdp-command", "invalid CDP result"
                        )
                    return result
                if isinstance(packet, dict) and isinstance(packet.get("method"), str):
                    self.events.append(packet)

        try:
            if timeout_seconds is None:
                return await _exchange()
            if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
                raise asyncio.TimeoutError
            return await asyncio.wait_for(_exchange(), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise ObscuraClientError(
                timeout_category,
                timeout_stage,
                f"Obscura did not complete {timeout_stage} before its deadline",
            ) from exc

    async def wait_for_event(self, method: str, predicate, timeout: float) -> dict:
        async def _wait() -> dict:
            while True:
                for event in self.events:
                    if event.get("method") == method and predicate(event.get("params", {})):
                        return event
                message = await self.websocket.recv()
                if isinstance(message, bytes):
                    message = message.decode("utf-8", "strict")
                packet = json.loads(message)
                if isinstance(packet, dict) and isinstance(packet.get("method"), str):
                    self.events.append(packet)

        return await asyncio.wait_for(_wait(), timeout=timeout)


class ObscuraSession:
    """One reusable, connection-isolated Obscura browser context.

    Callers remain responsible for serializing use.  A session never retries
    or reconnects during one navigation; after an ambiguous client failure the
    connection is discarded and a later navigation may create a fresh one.
    """

    def __init__(self) -> None:
        self.websocket = None
        self.cdp: _RawCdp | None = None
        self.cdp_url: str | None = None
        self.max_size = 0

    async def ensure_connected(
        self,
        *,
        cdp_url: str,
        max_size: int,
        open_timeout: float,
        close_timeout: float,
    ) -> _RawCdp:
        if self.websocket is not None:
            if self.cdp is None or self.cdp_url != cdp_url or self.max_size < max_size:
                raise ObscuraClientError(
                    FetchFailure.PROTOCOL,
                    "connect",
                    "reusable Obscura session parameters changed",
                )
            return self.cdp

        from websockets.asyncio.client import connect

        websocket = await connect(
            cdp_url,
            proxy=None,
            open_timeout=open_timeout,
            close_timeout=close_timeout,
            max_size=max_size,
            # Provider sessions are idle-expired by their owner.  Do not add a
            # periodic internal ping to an otherwise quiet stack.
            ping_interval=None,
        )
        self.websocket = websocket
        self.cdp = _RawCdp(websocket)
        self.cdp_url = cdp_url
        self.max_size = max_size
        return self.cdp

    async def close(self) -> None:
        websocket = self.websocket
        self.websocket = None
        self.cdp = None
        self.cdp_url = None
        self.max_size = 0
        if websocket is not None:
            await websocket.close()


class _Session:
    def __init__(self, cdp: _RawCdp, session_id: str):
        self.cdp = cdp
        self.session_id = session_id

    async def send(self, method: str, params: dict | None = None, **kwargs) -> dict:
        return await self.cdp.send(
            method, params, session_id=self.session_id, **kwargs
        )


async def fetch(
    url: str,
    *,
    cdp_url: str,
    wait_until: str,
    allow_http: bool,
    body_limit: int,
    dom_limit: int,
    allow_http_onion: bool = False,
    want: Literal["dom", "body", "both"] = "dom",
    event_timeout_seconds: float = 5.0,
    pre_navigation_timeout_seconds: float = PRE_NAVIGATION_TIMEOUT_SECONDS,
    cleanup_command_timeout_seconds: float = CLEANUP_COMMAND_TIMEOUT_SECONDS,
    request_timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    pre_navigation_guard: Callable[[], bool] | None = None,
    session_owner: ObscuraSession | None = None,
) -> FetchResult:
    """Perform exactly one Obscura navigation and consume only its output."""
    if body_limit <= 0 or dom_limit <= 0:
        raise ValueError("body and DOM limits must be positive")
    if not math.isfinite(pre_navigation_timeout_seconds) or pre_navigation_timeout_seconds <= 0:
        raise ValueError("pre-navigation timeout must be positive and finite")
    if not math.isfinite(cleanup_command_timeout_seconds) or cleanup_command_timeout_seconds <= 0:
        raise ValueError("cleanup command timeout must be positive and finite")
    if not math.isfinite(request_timeout_seconds) or request_timeout_seconds <= 0:
        raise ValueError("request timeout must be positive and finite")
    wait_until = validate_wait_until(wait_until)
    navigation_url, _fragment = normalize_public_url(
        url,
        allow_http=allow_http,
        allow_http_onion=allow_http_onion,
    )
    started = time.monotonic()
    diagnostic_id = uuid.uuid4().hex[:12]
    request_deadline = started + request_timeout_seconds
    pre_navigation_deadline = min(
        started + pre_navigation_timeout_seconds, request_deadline
    )
    websocket = cdp = session = None
    target_id: str | None = None
    stream_started = started
    stage = "connect"
    reusable = True

    def _discard_connection() -> None:
        nonlocal reusable
        reusable = False

    def _setup_remaining(next_stage: str) -> float:
        nonlocal stage
        stage = next_stage
        remaining = pre_navigation_deadline - time.monotonic()
        if remaining <= 0:
            raise ObscuraClientError(
                FetchFailure.PRE_NAVIGATION_TIMEOUT,
                stage,
                f"Obscura did not complete {stage} before its deadline",
            )
        return remaining

    def _request_remaining(next_stage: str) -> float:
        nonlocal stage
        stage = next_stage
        remaining = request_deadline - time.monotonic()
        if remaining <= 0:
            raise ObscuraClientError(
                FetchFailure.POST_NAVIGATION_TIMEOUT,
                stage,
                f"Obscura did not complete {stage} before its deadline",
            )
        return remaining

    async def _setup_send(
        transport: _RawCdp,
        method: str,
        params: dict | None = None,
        *,
        session_id: str | None = None,
        setup_stage: str,
    ) -> dict:
        return await transport.send(
            method,
            params,
            session_id=session_id,
            timeout_seconds=_setup_remaining(setup_stage),
            timeout_category=FetchFailure.PRE_NAVIGATION_TIMEOUT,
            timeout_stage=setup_stage,
        )

    async def _post_navigation_send(
        transport: _RawCdp,
        method: str,
        params: dict | None = None,
        *,
        session_id: str | None = None,
        command_stage: str,
        timeout_category: FetchFailure = FetchFailure.POST_NAVIGATION_TIMEOUT,
    ) -> dict:
        return await transport.send(
            method,
            params,
            session_id=session_id,
            timeout_seconds=_request_remaining(command_stage),
            timeout_category=timeout_category,
            timeout_stage=command_stage,
        )

    def _log_failure(exc: ObscuraClientError) -> None:
        LOGGER.warning(
            "obscura fetch failed request_id=%s category=%s stage=%s "
            "elapsed_seconds=%.3f target_created=%s",
            diagnostic_id,
            exc.category.value,
            exc.stage,
            time.monotonic() - started,
            bool(target_id),
        )

    LOGGER.info(
        "obscura fetch started request_id=%s wait_until=%s want=%s "
        "pre_navigation_timeout_seconds=%.1f request_timeout_seconds=%.1f",
        diagnostic_id,
        wait_until,
        want,
        pre_navigation_timeout_seconds,
        request_timeout_seconds,
    )
    try:
        try:
            if session_owner is None:
                from websockets.asyncio.client import connect

                websocket = await connect(
                    cdp_url,
                    proxy=None,
                    open_timeout=min(10.0, _setup_remaining("connect")),
                    close_timeout=cleanup_command_timeout_seconds,
                    max_size=max(body_limit, dom_limit) + (1 << 20),
                )
                cdp = _RawCdp(websocket)
            else:
                cdp = await session_owner.ensure_connected(
                    cdp_url=cdp_url,
                    max_size=max(body_limit, dom_limit) + (1 << 20),
                    open_timeout=min(10.0, _setup_remaining("connect")),
                    close_timeout=cleanup_command_timeout_seconds,
                )
                websocket = session_owner.websocket
        except asyncio.TimeoutError as exc:
            raise ObscuraClientError(
                FetchFailure.PRE_NAVIGATION_TIMEOUT,
                "connect",
                "Obscura did not complete connect before its deadline",
            ) from exc
        if cdp is None:
            raise ObscuraClientError(
                FetchFailure.PROTOCOL, "connect", "Obscura connection is incomplete"
            )
        # A reusable connection has one event buffer.  Every target and frame id
        # is unique, but dropping completed-attempt events also keeps the buffer
        # bounded across a busy provider session.
        cdp.events.clear()
        # Obscura gives every WebSocket a private browser context. A default
        # caller owns a fresh connection; an explicit provider session reuses
        # its native cookie jar and HTTP client while this request still owns
        # exactly one fresh target. Neither mode has state to clear here.
        created = await _setup_send(
            cdp,
            "Target.createTarget",
            {"url": "about:blank"},
            setup_stage="create-target",
        )
        target_id = str(created.get("targetId", ""))
        attached = next(
            (
                event
                for event in cdp.events
                if event.get("method") == "Target.attachedToTarget"
                and event.get("params", {}).get("targetInfo", {}).get("targetId")
                == target_id
            ),
            None,
        )
        session_id = str((attached or {}).get("params", {}).get("sessionId", ""))
        if not target_id or not session_id:
            raise ObscuraClientError(
                FetchFailure.PROTOCOL, "target", "Obscura target attachment is incomplete"
            )
        session = _Session(cdp, session_id)
        await _setup_send(
            cdp, "Network.enable", session_id=session_id, setup_stage="network-enable"
        )
        await _setup_send(
            cdp, "Page.enable", session_id=session_id, setup_stage="page-enable"
        )
        await _setup_send(
            cdp,
            "Page.setLifecycleEventsEnabled",
            {"enabled": True},
            session_id=session_id,
            setup_stage="lifecycle-enable",
        )
        frame_tree = await _setup_send(
            cdp,
            "Page.getFrameTree",
            session_id=session_id,
            setup_stage="frame-tree",
        )
        frame_id = frame_tree["frameTree"]["frame"]["id"]
        if pre_navigation_guard is not None and not pre_navigation_guard():
            raise ObscuraClientError(
                FetchFailure.FINALIZED, "pre-navigation", "request invocation is finalized"
            )
        stage = "navigate"
        LOGGER.info(
            "obscura pre-navigation completed request_id=%s elapsed_seconds=%.3f",
            diagnostic_id,
            time.monotonic() - started,
        )
        nav_started = time.monotonic()
        nav = await _post_navigation_send(
            cdp,
            "Page.navigate",
            {"url": navigation_url, "waitUntil": wait_until},
            session_id=session_id,
            command_stage="navigate",
            timeout_category=FetchFailure.NAVIGATION_TIMEOUT,
        )
        loader_id = str(nav.get("loaderId", ""))
        stage = "event-barrier"
        event_budget = min(
            event_timeout_seconds, _request_remaining("event-barrier")
        )
        try:
            await cdp.wait_for_event(
                "Page.frameStoppedLoading",
                lambda event: event.get("frameId") == frame_id,
                event_budget,
            )
        except asyncio.TimeoutError as exc:
            if time.monotonic() >= request_deadline:
                raise ObscuraClientError(
                    FetchFailure.POST_NAVIGATION_TIMEOUT,
                    "event-barrier",
                    "Obscura did not complete event-barrier before its deadline",
                ) from exc
            raise
        navigation_seconds = time.monotonic() - nav_started
        stage = "terminal-events"
        documents = [
            event.get("params", {})
            for event in cdp.events
            if event.get("method") == "Network.responseReceived"
            and event.get("params", {}).get("type") == "Document"
            and event.get("params", {}).get("frameId") == frame_id
        ]
        if len(documents) != 1:
            raise ObscuraClientError(
                FetchFailure.PROTOCOL,
                "events",
                "expected one terminal main-frame document response",
            )
        document = documents[0]
        request_id = str(document.get("requestId", ""))
        response = document.get("response", {})
        frames = [event.get("params", {}).get("frame", {}) for event in cdp.events
                  if event.get("method") == "Page.frameNavigated"
                  and event.get("params", {}).get("frame", {}).get("id") == frame_id]
        finished = {
            str(event.get("params", {}).get("requestId")): int(
                event.get("params", {}).get("encodedDataLength", 0)
            )
            for event in cdp.events
            if event.get("method") == "Network.loadingFinished"
        }
        if not frames or not request_id or not loader_id or request_id not in finished:
            raise ObscuraClientError(
                FetchFailure.PROTOCOL, "events", "terminal document event set is incomplete"
            )
        final_url = str(frames[-1].get("url", ""))
        normalize_public_url(
            final_url,
            allow_http=allow_http,
            allow_http_onion=allow_http_onion,
        )
        status = int(response.get("status", 0))
        headers = _normalize_headers(response.get("headers", {}))
        content_type, charset = _content_type(headers)
        completed = finished[request_id]
        if completed > max(body_limit, dom_limit):
            raise ObscuraClientError(
                FetchFailure.OVERSIZE, "events", "response exceeds the configured byte limit"
            )
        rendered_html = None
        if want in {"dom", "both"}:
            stage = "dom-document"
            document_node = await _post_navigation_send(
                cdp,
                "DOM.getDocument",
                {"depth": 0},
                session_id=session_id,
                command_stage="dom-document",
            )
            node_id = document_node.get("root", {}).get("nodeId")
            stage = "dom-outer-html"
            outer = await _post_navigation_send(
                cdp,
                "DOM.getOuterHTML",
                {"nodeId": node_id},
                session_id=session_id,
                command_stage="dom-outer-html",
            )
            rendered_html = outer.get("outerHTML")
            if not isinstance(rendered_html, str):
                raise ObscuraClientError(
                    FetchFailure.PROTOCOL, "dom", "Obscura returned an invalid DOM"
                )
            if len(rendered_html.encode("utf-8")) > dom_limit:
                raise ObscuraClientError(
                    FetchFailure.OVERSIZE, "dom", "rendered DOM exceeds the configured byte limit"
                )
        body = None
        body_failure = None
        stream_started = time.monotonic()
        if want in {"body", "both"}:
            stage = "body-stream"
            try:
                body = await _drain_body(
                    session,
                    request_id,
                    completed,
                    body_limit,
                    diagnostic_id=diagnostic_id,
                    cleanup_command_timeout_seconds=cleanup_command_timeout_seconds,
                    command_timeout=_request_remaining,
                    cleanup_failure=_discard_connection,
                )
            except ObscuraClientError as exc:
                # The pinned server evicts the oldest retained response after
                # a fixed entry count and creates the main-document loader
                # alias only after navigation completes. Resource-heavy HTML
                # pages can therefore lose their main body before the client
                # can claim it. The rendered DOM is still the authoritative
                # Onyx input for HTML, so preserve that same-navigation result.
                # Raw and binary formats remain strict because they require the
                # retained body.
                if _can_preserve_html_dom_without_body(want, content_type, exc):
                    body_failure = exc.category
                    LOGGER.warning(
                        "obscura retained body unavailable request_id=%s "
                        "stage=%s content_class=html completed_body_bytes=%s",
                        diagnostic_id,
                        exc.stage,
                        completed,
                    )
                else:
                    raise
        classification = (
            BodyClassification.TEXT
            if is_text_like_content_type(headers.get("content-type"))
            else BodyClassification.BINARY
        )
        challenge, challenge_signal = _challenge_details(
            status, final_url, rendered_html
        )
        completion_log = (
            LOGGER.warning
            if challenge is not None or body_failure is not None
            else LOGGER.info
        )
        completion_log(
            "obscura fetch completed request_id=%s status_class=%sxx navigation_seconds=%.3f "
            "body_read_seconds=%.3f completed_body_bytes=%s body_state=%s challenge=%s "
            "challenge_signal=%s",
            diagnostic_id,
            status // 100,
            navigation_seconds,
            time.monotonic() - stream_started,
            completed,
            (
                "unavailable"
                if body_failure is not None
                else "available"
                if body is not None
                else "not-requested"
            ),
            challenge.value if challenge is not None else "none",
            challenge_signal,
        )
        return FetchResult(
            requested_url=url,
            navigation_url=navigation_url,
            final_url=final_url,
            status=status,
            status_text=str(response.get("statusText", "")),
            headers=headers,
            content_type=content_type,
            charset=charset,
            frame_id=frame_id,
            loader_id=loader_id,
            request_id=request_id,
            body_classification=classification,
            original_byte_identity=(
                classification is BodyClassification.BINARY and body is not None
            ),
            lossy_conversion_possible=classification is BodyClassification.TEXT,
            completed_body_bytes=completed,
            rendered_html=rendered_html,
            body=body,
            body_failure=body_failure,
            navigation_seconds=navigation_seconds,
            body_read_seconds=time.monotonic() - stream_started,
            challenge=challenge,
        )
    except asyncio.CancelledError:
        # Cancellation can interrupt an in-flight CDP exchange after its
        # command was sent.  Even if target cleanup succeeds, do not retain a
        # connection with ambiguous protocol state for the next provider call.
        reusable = False
        raise
    except ObscuraClientError as exc:
        reusable = False
        _log_failure(exc)
        raise
    except asyncio.TimeoutError as exc:
        reusable = False
        mapped = ObscuraClientError(
            FetchFailure.TRANSPORT, "event-barrier", "Obscura event barrier timed out"
        )
        _log_failure(mapped)
        raise mapped from exc
    except Exception as exc:
        reusable = False
        message = str(exc).lower()
        category = (
            FetchFailure.POLICY_DENIED
            if any(token in message for token in ("forbidden", "blocked", "private", "proxy"))
            else FetchFailure.NAVIGATION_TIMEOUT
            if "timeout" in message
            else FetchFailure.TRANSPORT
        )
        mapped = ObscuraClientError(category, stage, "Obscura request failed")
        _log_failure(mapped)
        raise mapped from exc
    finally:
        if cdp is not None and target_id:
            try:
                closed = await cdp.send(
                    "Target.closeTarget",
                    {"targetId": target_id},
                    timeout_seconds=cleanup_command_timeout_seconds,
                    timeout_stage="cleanup-target-close",
                )
                if closed.get("success") is not True:
                    reusable = False
                    LOGGER.warning(
                        "obscura cleanup failed request_id=%s stage=target-close "
                        "category=%s elapsed_seconds=%.3f",
                        diagnostic_id,
                        FetchFailure.PROTOCOL.value,
                        time.monotonic() - started,
                    )
            except Exception as exc:
                reusable = False
                category = (
                    exc.category.value
                    if isinstance(exc, ObscuraClientError)
                    else FetchFailure.TRANSPORT.value
                )
                LOGGER.warning(
                    "obscura cleanup failed request_id=%s stage=target-close "
                    "category=%s elapsed_seconds=%.3f",
                    diagnostic_id,
                    category,
                    time.monotonic() - started,
                )
        if session_owner is not None and not reusable:
            try:
                await session_owner.close()
            except Exception:
                LOGGER.warning(
                    "obscura cleanup failed request_id=%s stage=connection-close "
                    "elapsed_seconds=%.3f",
                    diagnostic_id,
                    time.monotonic() - started,
                )
        elif session_owner is None and websocket is not None:
            try:
                await websocket.close()
            except Exception:
                LOGGER.warning(
                    "obscura cleanup failed request_id=%s stage=connection-close "
                    "elapsed_seconds=%.3f",
                    diagnostic_id,
                    time.monotonic() - started,
                )
        if cdp is not None:
            cdp.events.clear()
        LOGGER.info(
            "obscura cleanup completed request_id=%s elapsed_seconds=%.3f",
            diagnostic_id,
            time.monotonic() - started,
        )


def fetch_sync(*args, **kwargs) -> FetchResult:
    """Run one request in a caller-owned event loop on the current thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("fetch_sync cannot run inside an active event loop")
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(fetch(*args, **kwargs))
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()
