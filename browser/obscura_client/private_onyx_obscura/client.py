"""Audited, one-navigation Obscura CDP client shared by Onyx and SearXNG.

The main request is always a raw ``Page.navigate``.  There is deliberately no
HTTP probe, browser reconnect, navigation retry, or local-renderer fallback.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import math
import re
import secrets
import socket
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from html.parser import HTMLParser
from typing import Callable, Literal, Mapping
from urllib.parse import SplitResult, parse_qsl, unquote, urljoin, urlsplit, urlunsplit

from .anubis import (
    ANUBIS_PASS_PATH,
    AnubisChallenge,
    AnubisProtocolError,
    AnubisSolution,
    PendingAnubisPow,
    parse_anubis_challenge,
    worker_preload_source,
)

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


TextEntryMode = Literal["instant", "timed"]


def _canonical_search_host(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.lower()
        or value.endswith(".")
        or any(character in value for character in (":", "/", "\\", "*", "@"))
    ):
        raise ValueError("search hosts must be lowercase canonical DNS names")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError("search hosts cannot be IP literals")
    try:
        canonical = value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("search host is invalid") from exc
    if canonical != value or "." not in value:
        raise ValueError("search host is not canonical")
    return value


@dataclass(frozen=True)
class SearchInteractionSpec:
    homepage_url: str
    allowed_homepage_hosts: frozenset[str]
    allowed_result_hosts: frozenset[str]
    query_selector: str
    query_field_name: str
    form_action_path: str
    form_method: Literal["get", "post"]
    allowed_fixed_field_names: frozenset[str]
    result_terminal_selector: str | None = None
    result_pending_selector: str | None = None
    anubis_pow: bool = False

    def __post_init__(self) -> None:
        if not self.allowed_homepage_hosts or not self.allowed_result_hosts:
            raise ValueError("search host policies cannot be empty")
        for host in self.allowed_homepage_hosts | self.allowed_result_hosts:
            _canonical_search_host(host)
        try:
            parsed = urlsplit(self.homepage_url)
            port = parsed.port
        except (TypeError, ValueError) as exc:
            raise ValueError("search homepage URL is invalid") from exc
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or not parsed.hostname
            or parsed.hostname.rstrip(".").lower() not in self.allowed_homepage_hosts
            or parsed.fragment
        ):
            raise ValueError("search homepage URL violates its origin policy")
        if not self.query_selector or not self.query_field_name:
            raise ValueError("search control declarations cannot be empty")
        if self.form_method not in {"get", "post"}:
            raise ValueError("search form method must be get or post")
        action = self.form_action_path
        if (
            not action.startswith("/")
            or action.startswith("//")
            or any(character in action for character in ("?", "#", "\\"))
            or re.search(r"%2f|%5c", action, re.IGNORECASE)
        ):
            raise ValueError("search form action must be one absolute path")
        if self.query_field_name in self.allowed_fixed_field_names:
            raise ValueError("query field cannot also be a fixed field")
        for name in self.allowed_fixed_field_names:
            if (
                not isinstance(name, str)
                or not name
                or not name.isascii()
                or not re.fullmatch(r"[A-Za-z0-9_.:-]+", name)
            ):
                raise ValueError("fixed field names must be nonempty ASCII form names")
        readiness_selectors = (
            self.result_terminal_selector,
            self.result_pending_selector,
        )
        if (readiness_selectors[0] is None) != (readiness_selectors[1] is None):
            raise ValueError("search result readiness selectors must be paired")
        for selector in readiness_selectors:
            if selector is None:
                continue
            if (
                not isinstance(selector, str)
                or not selector
                or len(selector) > 4096
                or any(ord(character) < 0x20 for character in selector)
            ):
                raise ValueError("search result readiness selector is invalid")
        if type(self.anubis_pow) is not bool:
            raise ValueError("Anubis support flag must be boolean")


@dataclass(frozen=True)
class SearchSubmissionResult:
    final_url: str
    status: int
    headers: Mapping[str, str]
    rendered_html: str
    challenge: FetchFailure | None
    homepage_navigation_seconds: float
    submission_navigation_seconds: float


@dataclass(frozen=True)
class _PendingAnubisContinuation:
    token: str
    challenge: AnubisChallenge
    boundary: Literal["homepage", "result"]
    challenged_url: str
    challenged_loader: str
    query: str
    spec: SearchInteractionSpec
    fixed_fields: tuple[tuple[str, str], ...]
    text_entry_mode: TextEntryMode
    request_deadline: float
    dom_limit: int
    homepage_navigation_seconds: float
    submission_navigation_seconds: float
    diagnostic_id: str
    preload_identifier: str
    preload_control: str


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
    _VOID = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )
    _ANUBIS_MAIN_PATH = "/.within.website/x/cmd/anubis/static/js/main.mjs"

    def __init__(self, final_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.final_url = final_url
        self.ignored_depth = 0
        self.title_depth = 0
        self.sp_message_depth = 0
        self.visible: list[str] = []
        self.title: list[str] = []
        self.sp_message_visible: list[str] = []
        self.challenge_structure = False
        self.anubis_main_module = False
        self.anubis_version_element = False
        self.anubis_challenge_element = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = {name.lower(): (value or "") for name, value in attrs}
        if tag == "script":
            element_id = values.get("id", "")
            self.anubis_version_element |= element_id == "anubis_version"
            self.anubis_challenge_element |= element_id == "anubis_challenge"
            source = values.get("src", "")
            if source:
                try:
                    document = urlsplit(self.final_url)
                    resolved = urlsplit(urljoin(self.final_url, source))
                    self.anubis_main_module |= (
                        resolved.scheme == document.scheme == "https"
                        and resolved.hostname == document.hostname
                        and (resolved.port or 443) == (document.port or 443)
                        and resolved.username is None
                        and resolved.password is None
                        and unquote(resolved.path) == self._ANUBIS_MAIN_PATH
                    )
                except (TypeError, ValueError):
                    pass
        if tag in self._IGNORED:
            self.ignored_depth += 1
            return
        if self.ignored_depth:
            return
        if tag == "title":
            self.title_depth += 1
        classes = values.get("class", "").split()
        if self.sp_message_depth:
            if tag not in self._VOID:
                self.sp_message_depth += 1
        elif "sp-message" in classes:
            self.sp_message_depth = 1
        candidate = (
            values.get("action", "").lower()
            if tag == "form"
            else values.get("src", "").lower()
        )
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
        if not self.ignored_depth and self.sp_message_depth and tag not in self._VOID:
            self.sp_message_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.ignored_depth or not data.strip():
            return
        self.visible.append(data)
        if self.title_depth:
            self.title.append(data)
        if self.sp_message_depth:
            self.sp_message_visible.append(data)


def _challenge_details(
    status: int, final_url: str, html: str | None
) -> tuple[FetchFailure | None, str]:
    if status == 429:
        return FetchFailure.RATE_LIMITED, "http-429"
    if status in {401, 402, 403}:
        return FetchFailure.ACCESS_DENIED, "http-denial-status"

    parser = _ChallengeSignals(final_url)
    try:
        parser.feed((html or "")[:262_144])
        parser.close()
    except Exception:
        return None, "html-signal-parse-failed"

    visible = " ".join(" ".join(parser.visible).lower().split())
    title = " ".join(" ".join(parser.title).lower().split())
    sp_message = " ".join(" ".join(parser.sp_message_visible).lower().split())
    parsed_url = urlsplit(final_url)
    route = unquote(parsed_url.path).lower()

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
    if (
        parser.anubis_main_module
        and parser.anubis_version_element
        and parser.anubis_challenge_element
        and "verifying your request" in sp_message
    ):
        return FetchFailure.CAPTCHA, "anubis-verification"
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

    The shared client owns event correlation, response-body streaming, strict
    deadlines, and cleanup directly. Keeping that small protocol surface avoids
    a local browser dependency or a second abstraction over these contracts.
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

    async def wait_for_event(
        self,
        method: str,
        predicate,
        timeout: float,
        *,
        start_index: int = 0,
    ) -> dict:
        async def _wait() -> dict:
            while True:
                for event in self.events[start_index:]:
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


class SearchBrowserSession:
    """Opaque owner of one retained search connection and target generation."""

    def __init__(self) -> None:
        self._connection = ObscuraSession()
        self._target_id: str | None = None
        self._session_id: str | None = None
        self._frame_id: str | None = None
        self._pending_anubis: _PendingAnubisContinuation | None = None
        self._cleanup_command_timeout_seconds = CLEANUP_COMMAND_TIMEOUT_SECONDS

    @property
    def generation_active(self) -> bool:
        """Whether this owner still has a retained connection or target."""
        return (
            self._connection.websocket is not None
            or self._target_id is not None
        )

    async def close(self) -> None:
        target_id = self._target_id
        cdp = self._connection.cdp
        self._target_id = None
        self._session_id = None
        self._frame_id = None
        self._pending_anubis = None
        failure: BaseException | None = None
        if cdp is not None and target_id:
            try:
                result = await cdp.send(
                    "Target.closeTarget",
                    {"targetId": target_id},
                    timeout_seconds=self._cleanup_command_timeout_seconds,
                    timeout_stage="cleanup-target-close",
                )
                if result.get("success") is not True:
                    failure = ObscuraClientError(
                        FetchFailure.PROTOCOL,
                        "cleanup-target-close",
                        "Obscura did not confirm retained target closure",
                    )
            except BaseException as exc:
                failure = exc
        try:
            await self._connection.close()
        except BaseException as exc:
            if failure is None:
                failure = exc
        if failure is not None:
            raise failure


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


_SEARCH_FORM_FUNCTION = r"""
function(operation, selector, fieldName, fixedFields, query) {
  function inspect() {
    const controls = Array.from(document.querySelectorAll(selector));
    if (controls.length !== 1) throw new Error("control-count");
    const control = controls[0];
    const tag = String(control.tagName || "").toLowerCase();
    const inputType = String(control.type || "text").toLowerCase();
    if ((tag !== "input" && tag !== "textarea") ||
        (tag === "input" && inputType !== "text" && inputType !== "search") ||
        !control.isConnected || control.disabled || control.readOnly ||
        control.name !== fieldName || control.hidden) {
      throw new Error("control-state");
    }
    const style = globalThis.getComputedStyle ? getComputedStyle(control) : null;
    if (style && (style.display === "none" || style.visibility === "hidden")) {
      throw new Error("control-visibility");
    }
    const rect = control.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0 ||
        rect.right <= 0 || rect.bottom <= 0 ||
        rect.left >= globalThis.innerWidth || rect.top >= globalThis.innerHeight) {
      throw new Error("control-geometry");
    }
    const form = control.form;
    if (!(form instanceof HTMLFormElement) || form.ownerDocument !== document) {
      throw new Error("form-owner");
    }
    const action = new URL(form.action || location.href, location.href);
    const current = new URL(location.href);
    const target = String(form.getAttribute("target") || "").toLowerCase();
    const declaredEnctype = String(
      form.enctype || form.getAttribute("enctype") || ""
    ).toLowerCase();
    const enctype = declaredEnctype || "application/x-www-form-urlencoded";
    return {
      control, form,
      policy: {
        currentScheme: current.protocol,
        currentHost: current.hostname.toLowerCase(),
        currentPort: current.port,
        scheme: action.protocol,
        host: action.hostname.toLowerCase(),
        port: action.port,
        username: action.username,
        password: action.password,
        path: action.pathname,
        method: String(form.method || "get").toLowerCase(),
        target,
        enctype
      }
    };
  }
  function applyFixed(form) {
    for (const pair of fixedFields) {
      const name = pair[0], value = pair[1];
      let controls = Array.from(form.elements).filter(
        item => item && item.name === name && !item.disabled
      );
      if (controls.length === 0) {
        const hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = name;
        form.appendChild(hidden);
        controls = [hidden];
      }
      if (controls.length !== 1 ||
          String(controls[0].tagName || "").toLowerCase() !== "input" ||
          String(controls[0].type || "").toLowerCase() !== "hidden") {
        throw new Error("fixed-field");
      }
      controls[0].value = value;
      const finalControls = Array.from(form.elements).filter(
        item => item && item.name === name && !item.disabled
      );
      if (finalControls.length !== 1 || finalControls[0].value !== value) {
        throw new Error("fixed-field-verify");
      }
    }
  }
  let state = inspect();
  if (operation === "validate") return state.policy;
  applyFixed(state.form);
  if (operation === "instant") {
    const proto = String(state.control.tagName).toLowerCase() === "textarea"
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    if (typeof setter !== "function") throw new Error("native-setter");
    setter.call(state.control, query);
    state.control.dispatchEvent(new Event("input", {bubbles: true}));
    state.control.dispatchEvent(new Event("change", {bubbles: true}));
  } else if (operation === "timed-prepare") {
    if (state.control.value !== "") throw new Error("control-not-empty");
    state.control.focus();
    if (document.activeElement !== state.control) throw new Error("focus");
  }
  state = inspect();
  if (operation === "instant" || operation === "verify" || operation === "submit") {
    if (state.control.value !== query) throw new Error("query-value");
  }
  if (operation === "submit") {
    if (typeof state.form.requestSubmit !== "function") {
      throw new Error("request-submit");
    }
    state.form.requestSubmit();
  }
  return state.policy;
}
"""


_SEARCH_RESULT_STATE_FUNCTION = r"""
function(terminalSelector, pendingSelector) {
  return {
    terminal: document.querySelector(terminalSelector) !== null,
    pending: document.querySelector(pendingSelector) !== null
  };
}
"""


def _validate_search_terminal_url(
    url: str, allowed_hosts: frozenset[str], *, stage: str
) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ObscuraClientError(
            FetchFailure.POLICY_DENIED, stage, "search terminal URL is invalid"
        ) from exc
    host = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or host not in allowed_hosts
    ):
        raise ObscuraClientError(
            FetchFailure.POLICY_DENIED,
            stage,
            "search terminal URL violates its origin policy",
        )


def _validate_form_policy(
    value: object, spec: SearchInteractionSpec, *, stage: str
) -> None:
    if not isinstance(value, dict):
        raise ObscuraClientError(
            FetchFailure.PROTOCOL, stage, "search form inspection was invalid"
        )
    if (
        value.get("currentScheme") != "https:"
        or value.get("currentHost") not in spec.allowed_homepage_hosts
        or value.get("currentPort") not in {"", "443"}
        or
        value.get("scheme") != "https:"
        or value.get("host") not in spec.allowed_result_hosts
        or value.get("port") not in {"", "443"}
        or value.get("username") != ""
        or value.get("password") != ""
        or value.get("path") != spec.form_action_path
        or value.get("method") != spec.form_method
        or value.get("target") not in {"", "_self"}
        or value.get("enctype") != "application/x-www-form-urlencoded"
    ):
        raise ObscuraClientError(
            FetchFailure.POLICY_DENIED,
            stage,
            "search form violates its declared policy",
        )


def _search_event_document(
    cdp: _RawCdp,
    *,
    start_index: int,
    frame_id: str,
    loader_id: str,
    stage: str,
) -> tuple[str, int, dict[str, str], str]:
    events = cdp.events[start_index:]
    if any(
        event.get("method") == "Target.attachedToTarget"
        and event.get("params", {}).get("targetInfo", {}).get("targetId")
        for event in events
    ):
        raise ObscuraClientError(
            FetchFailure.PROTOCOL, stage, "search created an unsupported target"
        )
    documents = [
        event.get("params", {})
        for event in events
        if event.get("method") == "Network.responseReceived"
        and event.get("params", {}).get("type") == "Document"
        and event.get("params", {}).get("frameId") == frame_id
        and event.get("params", {}).get("loaderId") == loader_id
    ]
    if len(documents) != 1:
        raise ObscuraClientError(
            FetchFailure.PROTOCOL,
            stage,
            "expected one terminal search document response",
        )
    document = documents[0]
    request_id = str(document.get("requestId", ""))
    response = document.get("response", {})
    frames = [
        event.get("params", {}).get("frame", {})
        for event in events
        if event.get("method") == "Page.frameNavigated"
        and event.get("params", {}).get("frame", {}).get("id") == frame_id
        and event.get("params", {}).get("frame", {}).get("loaderId") == loader_id
    ]
    finished = {
        str(event.get("params", {}).get("requestId"))
        for event in events
        if event.get("method") == "Network.loadingFinished"
    }
    if not request_id or request_id not in finished or not frames:
        raise ObscuraClientError(
            FetchFailure.PROTOCOL, stage, "search document event set is incomplete"
        )
    return (
        str(frames[-1].get("url", "")),
        int(response.get("status", 0)),
        _normalize_headers(response.get("headers", {})),
        request_id,
    )


async def _search_dom(
    session: _Session,
    *,
    dom_limit: int,
    remaining: Callable[[str, FetchFailure], float],
    stage_prefix: str,
) -> str:
    document = await session.send(
        "DOM.getDocument",
        {"depth": 0},
        timeout_seconds=remaining(
            f"{stage_prefix}-dom-document", FetchFailure.POST_NAVIGATION_TIMEOUT
        ),
        timeout_category=FetchFailure.POST_NAVIGATION_TIMEOUT,
        timeout_stage=f"{stage_prefix}-dom-document",
    )
    node_id = document.get("root", {}).get("nodeId")
    outer = await session.send(
        "DOM.getOuterHTML",
        {"nodeId": node_id},
        timeout_seconds=remaining(
            f"{stage_prefix}-dom-outer-html", FetchFailure.POST_NAVIGATION_TIMEOUT
        ),
        timeout_category=FetchFailure.POST_NAVIGATION_TIMEOUT,
        timeout_stage=f"{stage_prefix}-dom-outer-html",
    )
    html = outer.get("outerHTML")
    if not isinstance(html, str):
        raise ObscuraClientError(
            FetchFailure.PROTOCOL,
            f"{stage_prefix}-dom",
            "Obscura returned an invalid search DOM",
        )
    if len(html.encode("utf-8")) > dom_limit:
        raise ObscuraClientError(
            FetchFailure.OVERSIZE,
            f"{stage_prefix}-dom",
            "search DOM exceeds the configured byte limit",
        )
    return html


async def _search_form_call(
    session: _Session,
    *,
    operation: str,
    spec: SearchInteractionSpec,
    fixed_fields: tuple[tuple[str, str], ...],
    query: str,
    remaining: Callable[[str, FetchFailure], float],
    stage: str,
) -> None:
    result = await session.send(
        "Runtime.callFunctionOn",
        {
            "functionDeclaration": _SEARCH_FORM_FUNCTION,
            "arguments": [
                {"value": operation},
                {"value": spec.query_selector},
                {"value": spec.query_field_name},
                {"value": [list(item) for item in fixed_fields]},
                {"value": query},
            ],
            "returnByValue": True,
            "awaitPromise": True,
        },
        timeout_seconds=remaining(stage, FetchFailure.POST_NAVIGATION_TIMEOUT),
        timeout_category=FetchFailure.POST_NAVIGATION_TIMEOUT,
        timeout_stage=stage,
    )
    remote = result.get("result", {})
    if (
        result.get("exceptionDetails") is not None
        or not isinstance(remote, dict)
        or remote.get("subtype") == "error"
    ):
        raise ObscuraClientError(
            FetchFailure.PROTOCOL, stage, "search form operation failed"
        )
    _validate_form_policy(remote.get("value"), spec, stage=stage)


async def _search_form_present(
    session: _Session,
    *,
    selector: str,
    remaining: Callable[[str, FetchFailure], float],
) -> bool:
    result = await session.send(
        "Runtime.callFunctionOn",
        {
            "functionDeclaration": "function(selector) { return document.querySelector(selector) !== null; }",
            "arguments": [{"value": selector}],
            "returnByValue": True,
            "awaitPromise": True,
        },
        timeout_seconds=remaining(
            "form-presence", FetchFailure.POST_NAVIGATION_TIMEOUT
        ),
        timeout_category=FetchFailure.POST_NAVIGATION_TIMEOUT,
        timeout_stage="form-presence",
    )
    remote = result.get("result", {})
    value = remote.get("value") if isinstance(remote, dict) else None
    if result.get("exceptionDetails") is not None or type(value) is not bool:
        raise ObscuraClientError(
            FetchFailure.PROTOCOL,
            "form-presence",
            "search form presence inspection failed",
        )
    return value


async def _search_result_state(
    session: _Session,
    *,
    terminal_selector: str,
    pending_selector: str,
    remaining: Callable[[str, FetchFailure], float],
) -> tuple[bool, bool]:
    result = await session.send(
        "Runtime.callFunctionOn",
        {
            "functionDeclaration": _SEARCH_RESULT_STATE_FUNCTION,
            "arguments": [
                {"value": terminal_selector},
                {"value": pending_selector},
            ],
            "returnByValue": True,
            "awaitPromise": True,
        },
        timeout_seconds=remaining(
            "result-readiness", FetchFailure.POST_NAVIGATION_TIMEOUT
        ),
        timeout_category=FetchFailure.POST_NAVIGATION_TIMEOUT,
        timeout_stage="result-readiness",
    )
    remote = result.get("result", {})
    value = remote.get("value") if isinstance(remote, dict) else None
    if (
        result.get("exceptionDetails") is not None
        or not isinstance(remote, dict)
        or remote.get("subtype") == "error"
        or not isinstance(value, dict)
        or type(value.get("terminal")) is not bool
        or type(value.get("pending")) is not bool
    ):
        raise ObscuraClientError(
            FetchFailure.PROTOCOL,
            "result-readiness",
            "search result readiness inspection failed",
        )
    return value["terminal"], value["pending"]


async def _wait_for_search_result_dom(
    session: _Session,
    *,
    initial_html: str,
    terminal_selector: str,
    pending_selector: str,
    dom_limit: int,
    request_deadline: float,
    remaining: Callable[[str, FetchFailure], float],
    _clock=time.monotonic,
    _sleep=asyncio.sleep,
) -> str:
    """Wait for a provider terminal state within the existing browser budget."""
    terminal, pending = await _search_result_state(
        session,
        terminal_selector=terminal_selector,
        pending_selector=pending_selector,
        remaining=remaining,
    )
    if terminal:
        return await _search_dom(
            session,
            dom_limit=dom_limit,
            remaining=remaining,
            stage_prefix="result-ready",
        )
    if not pending:
        return initial_html

    while True:
        delay = min(0.1, request_deadline - _clock())
        if delay <= 0:
            return initial_html
        await _sleep(delay)
        if _clock() >= request_deadline:
            return initial_html
        terminal, pending = await _search_result_state(
            session,
            terminal_selector=terminal_selector,
            pending_selector=pending_selector,
            remaining=remaining,
        )
        if terminal or not pending:
            return await _search_dom(
                session,
                dom_limit=dom_limit,
                remaining=remaining,
                stage_prefix="result-ready",
            )


async def _anubis_worker_control(
    session: _Session,
    *,
    control_name: str,
    operation: Literal["status", "remove"],
    remaining: Callable[[str, FetchFailure], float],
) -> dict:
    stage = f"anubis-worker-{operation}"
    result = await session.send(
        "Runtime.callFunctionOn",
        {
            "functionDeclaration": "function(name, operation) { const control = globalThis[name]; if (typeof control !== 'function') throw new Error('missing control'); return control(operation); }",
            "arguments": [{"value": control_name}, {"value": operation}],
            "returnByValue": True,
            "awaitPromise": True,
        },
        timeout_seconds=remaining(stage, FetchFailure.POST_NAVIGATION_TIMEOUT),
        timeout_category=FetchFailure.POST_NAVIGATION_TIMEOUT,
        timeout_stage=stage,
    )
    remote = result.get("result", {})
    value = remote.get("value") if isinstance(remote, dict) else None
    if (
        result.get("exceptionDetails") is not None
        or not isinstance(value, dict)
        or type(value.get("active")) is not bool
        or type(value.get("installed")) is not bool
        or type(value.get("suppressed")) is not int
    ):
        raise ObscuraClientError(
            FetchFailure.PROTOCOL, stage, "Anubis worker control failed"
        )
    return value


def _validate_anubis_worker_status(status: dict) -> None:
    if not status["active"] or not status["installed"]:
        raise ObscuraClientError(
            FetchFailure.PROTOCOL,
            "anubis-worker-status",
            "Anubis worker suppression was not acknowledged",
        )


async def _remove_anubis_preload(
    cdp: _RawCdp,
    session: _Session,
    *,
    session_id: str,
    identifier: str,
    control_name: str,
    remaining: Callable[[str, FetchFailure], float],
) -> None:
    status = await _anubis_worker_control(
        session,
        control_name=control_name,
        operation="status",
        remaining=remaining,
    )
    _validate_anubis_worker_status(status)
    removed = await _anubis_worker_control(
        session,
        control_name=control_name,
        operation="remove",
        remaining=remaining,
    )
    if removed != {"active": False, "installed": False, "suppressed": 0}:
        raise ObscuraClientError(
            FetchFailure.PROTOCOL,
            "anubis-worker-remove",
            "Anubis worker suppression removal was not acknowledged",
        )
    await cdp.send(
        "Page.removeScriptToEvaluateOnNewDocument",
        {"identifier": identifier},
        session_id=session_id,
        timeout_seconds=remaining(
            "anubis-preload-remove", FetchFailure.POST_NAVIGATION_TIMEOUT
        ),
        timeout_category=FetchFailure.POST_NAVIGATION_TIMEOUT,
        timeout_stage="anubis-preload-remove",
    )


async def _enter_search_query(
    session: _Session,
    *,
    spec: SearchInteractionSpec,
    fixed_fields: tuple[tuple[str, str], ...],
    query: str,
    text_entry_mode: TextEntryMode,
    remaining: Callable[[str, FetchFailure], float],
    timing_random=None,
    timing_sleep=asyncio.sleep,
) -> None:
    await _search_form_call(
        session,
        operation="validate",
        spec=spec,
        fixed_fields=fixed_fields,
        query=query,
        remaining=remaining,
        stage="form-validate",
    )
    if text_entry_mode == "instant":
        await _search_form_call(
            session,
            operation="instant",
            spec=spec,
            fixed_fields=fixed_fields,
            query=query,
            remaining=remaining,
            stage="instant-entry",
        )
        return
    await _search_form_call(
        session,
        operation="timed-prepare",
        spec=spec,
        fixed_fields=fixed_fields,
        query=query,
        remaining=remaining,
        stage="timed-entry-prepare",
    )
    random_source = timing_random or secrets.SystemRandom()
    for index, character in enumerate(query):
        for event_type in ("keyDown", "keyUp"):
            params = {"type": event_type, "key": character}
            if event_type == "keyDown":
                params.update({"text": character, "unmodifiedText": character})
            await session.send(
                "Input.dispatchKeyEvent",
                params,
                timeout_seconds=remaining(
                    f"timed-entry-{event_type.lower()}",
                    FetchFailure.POST_NAVIGATION_TIMEOUT,
                ),
                timeout_category=FetchFailure.POST_NAVIGATION_TIMEOUT,
                timeout_stage=f"timed-entry-{event_type.lower()}",
            )
        if index + 1 < len(query):
            delay = random_source.uniform(0.045, 0.135)
            if delay >= remaining(
                "timed-entry-delay", FetchFailure.POST_NAVIGATION_TIMEOUT
            ):
                raise ObscuraClientError(
                    FetchFailure.POST_NAVIGATION_TIMEOUT,
                    "timed-entry-delay",
                    "timed entry delay exceeds the remaining deadline",
                )
            await timing_sleep(delay)
    await _search_form_call(
        session,
        operation="verify",
        spec=spec,
        fixed_fields=fixed_fields,
        query=query,
        remaining=remaining,
        stage="timed-entry-verify",
    )


async def _wait_for_distinct_search_document(
    cdp: _RawCdp,
    *,
    frame_id: str,
    previous_loader: str,
    start_index: int,
    remaining: Callable[[str, FetchFailure], float],
    stage_prefix: str,
) -> str:
    try:
        response = await cdp.wait_for_event(
            "Network.responseReceived",
            lambda event: (
                event.get("type") == "Document"
                and event.get("frameId") == frame_id
                and event.get("loaderId") != previous_loader
            ),
            remaining(
                f"{stage_prefix}-navigation", FetchFailure.POST_NAVIGATION_TIMEOUT
            ),
            start_index=start_index,
        )
    except asyncio.TimeoutError as exc:
        raise ObscuraClientError(
            FetchFailure.POST_NAVIGATION_TIMEOUT,
            f"{stage_prefix}-navigation",
            "search navigation did not produce a distinct document",
        ) from exc
    loader_id = str(response.get("params", {}).get("loaderId", ""))
    if not loader_id or loader_id == previous_loader:
        raise ObscuraClientError(
            FetchFailure.PROTOCOL,
            f"{stage_prefix}-navigation",
            "search navigation did not create a distinct loader",
        )
    try:
        await cdp.wait_for_event(
            "Page.frameStoppedLoading",
            lambda event: event.get("frameId") == frame_id,
            remaining(
                f"{stage_prefix}-event-barrier",
                FetchFailure.POST_NAVIGATION_TIMEOUT,
            ),
            start_index=start_index,
        )
    except asyncio.TimeoutError as exc:
        raise ObscuraClientError(
            FetchFailure.POST_NAVIGATION_TIMEOUT,
            f"{stage_prefix}-event-barrier",
            "search document completion event timed out",
        ) from exc
    return loader_id


async def _submit_form_document(
    cdp: _RawCdp,
    session: _Session,
    *,
    frame_id: str,
    previous_loader: str,
    spec: SearchInteractionSpec,
    fixed_fields: tuple[tuple[str, str], ...],
    query: str,
    dom_limit: int,
    request_deadline: float,
    remaining: Callable[[str, FetchFailure], float],
) -> tuple[str, int, dict[str, str], str, str, float]:
    event_start = len(cdp.events)
    started = time.monotonic()
    await _search_form_call(
        session,
        operation="submit",
        spec=spec,
        fixed_fields=fixed_fields,
        query=query,
        remaining=remaining,
        stage="form-submit",
    )
    loader_id = await _wait_for_distinct_search_document(
        cdp,
        frame_id=frame_id,
        previous_loader=previous_loader,
        start_index=event_start,
        remaining=remaining,
        stage_prefix="submission-result",
    )
    elapsed = time.monotonic() - started
    url, status, headers, _request_id = _search_event_document(
        cdp,
        start_index=event_start,
        frame_id=frame_id,
        loader_id=loader_id,
        stage="result-events",
    )
    _validate_search_terminal_url(url, spec.allowed_result_hosts, stage="result-origin")
    html = await _search_dom(
        session, dom_limit=dom_limit, remaining=remaining, stage_prefix="result"
    )
    if (
        _challenge(status, url, html) is None
        and status != 404
        and status < 500
        and spec.result_terminal_selector is not None
        and spec.result_pending_selector is not None
    ):
        html = await _wait_for_search_result_dom(
            session,
            initial_html=html,
            terminal_selector=spec.result_terminal_selector,
            pending_selector=spec.result_pending_selector,
            dom_limit=dom_limit,
            request_deadline=request_deadline,
            remaining=remaining,
        )
    return url, status, headers, html, loader_id, elapsed


async def submit_search(
    query: str,
    *,
    spec: SearchInteractionSpec,
    fixed_fields: tuple[tuple[str, str], ...],
    text_entry_mode: TextEntryMode,
    cdp_url: str,
    wait_until: str,
    dom_limit: int,
    pre_navigation_guard: Callable[[], bool],
    pre_navigation_timeout_seconds: float,
    cleanup_command_timeout_seconds: float,
    request_timeout_seconds: float,
    session_owner: SearchBrowserSession,
    _timing_random=None,
    _timing_sleep=asyncio.sleep,
) -> SearchSubmissionResult | PendingAnubisPow:
    """Perform one retained-target homepage/form/result browser transaction."""
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    if not isinstance(session_owner, SearchBrowserSession):
        raise TypeError("search requires a SearchBrowserSession")
    if not callable(pre_navigation_guard):
        raise TypeError("search requires a pre-navigation guard")
    if not callable(_timing_sleep):
        raise TypeError("timing sleeper must be callable")
    if text_entry_mode not in {"instant", "timed"}:
        raise ValueError("text entry mode must be instant or timed")
    if dom_limit <= 0:
        raise ValueError("DOM limit must be positive")
    for value, label in (
        (pre_navigation_timeout_seconds, "pre-navigation timeout"),
        (cleanup_command_timeout_seconds, "cleanup timeout"),
        (request_timeout_seconds, "request timeout"),
    ):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{label} must be positive and finite")
    wait_until = validate_wait_until(wait_until)
    if not isinstance(fixed_fields, tuple):
        raise TypeError("fixed fields must be a tuple")
    seen_fields: set[str] = set()
    for item in fixed_fields:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], str)
            or item[0] not in spec.allowed_fixed_field_names
            or item[0] in seen_fields
            or item[0] == spec.query_field_name
        ):
            raise ValueError("fixed fields violate the search specification")
        seen_fields.add(item[0])

    started = time.monotonic()
    request_deadline = started + request_timeout_seconds
    setup_deadline = min(started + pre_navigation_timeout_seconds, request_deadline)
    diagnostic_id = uuid.uuid4().hex[:12]
    stage = "search-connect"
    ambiguous = False
    owner = session_owner
    owner._cleanup_command_timeout_seconds = cleanup_command_timeout_seconds
    if owner._pending_anubis is not None:
        raise ObscuraClientError(
            FetchFailure.PROTOCOL,
            "search-pending-continuation",
            "retained search session already has a pending continuation",
        )

    def remaining(next_stage: str, category: FetchFailure) -> float:
        nonlocal stage
        stage = next_stage
        deadline = setup_deadline if category is FetchFailure.PRE_NAVIGATION_TIMEOUT else request_deadline
        value = deadline - time.monotonic()
        if value <= 0:
            raise ObscuraClientError(
                category, next_stage, f"Obscura did not complete {next_stage} before its deadline"
            )
        return value

    async def command(
        transport: _RawCdp,
        method: str,
        params: dict | None = None,
        *,
        session_id: str | None = None,
        command_stage: str,
        category: FetchFailure,
    ) -> dict:
        return await transport.send(
            method,
            params,
            session_id=session_id,
            timeout_seconds=remaining(command_stage, category),
            timeout_category=category,
            timeout_stage=command_stage,
        )

    LOGGER.info(
        "obscura search started request_id=%s entry_mode=%s wait_until=%s",
        diagnostic_id,
        text_entry_mode,
        wait_until,
    )
    try:
        try:
            cdp = await owner._connection.ensure_connected(
                cdp_url=cdp_url,
                max_size=dom_limit + (1 << 20),
                open_timeout=min(
                    10.0,
                    remaining(
                        "search-connect", FetchFailure.PRE_NAVIGATION_TIMEOUT
                    ),
                ),
                close_timeout=cleanup_command_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise ObscuraClientError(
                FetchFailure.PRE_NAVIGATION_TIMEOUT,
                "search-connect",
                "Obscura search connection setup timed out",
            ) from exc
        if owner._target_id is None:
            created = await command(
                cdp,
                "Target.createTarget",
                {"url": "about:blank"},
                command_stage="search-create-target",
                category=FetchFailure.PRE_NAVIGATION_TIMEOUT,
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
                    FetchFailure.PROTOCOL,
                    "search-target",
                    "Obscura search target attachment is incomplete",
                )
            owner._target_id = target_id
            owner._session_id = session_id
        session_id = owner._session_id
        if not session_id:
            raise ObscuraClientError(
                FetchFailure.PROTOCOL, "search-target", "search session is incomplete"
            )
        for method, params, command_stage in (
            ("Network.enable", None, "search-network-enable"),
            ("Page.enable", None, "search-page-enable"),
            (
                "Page.setLifecycleEventsEnabled",
                {"enabled": True},
                "search-lifecycle-enable",
            ),
        ):
            await command(
                cdp,
                method,
                params,
                session_id=session_id,
                command_stage=command_stage,
                category=FetchFailure.PRE_NAVIGATION_TIMEOUT,
            )
        frame_tree = await command(
            cdp,
            "Page.getFrameTree",
            session_id=session_id,
            command_stage="search-frame-tree",
            category=FetchFailure.PRE_NAVIGATION_TIMEOUT,
        )
        frame_id = str(frame_tree.get("frameTree", {}).get("frame", {}).get("id", ""))
        if not frame_id or (
            owner._frame_id is not None and owner._frame_id != frame_id
        ):
            raise ObscuraClientError(
                FetchFailure.PROTOCOL,
                "search-frame-tree",
                "retained search main frame changed",
            )
        owner._frame_id = frame_id
        cdp.events.clear()
        session = _Session(cdp, session_id)
        preload_identifier = ""
        preload_control = ""
        if spec.anubis_pow:
            preload_control = f"__privateOnyxAnubis_{secrets.token_hex(16)}"
            preload = await command(
                cdp,
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": worker_preload_source(preload_control)},
                session_id=session_id,
                command_stage="anubis-preload-install",
                category=FetchFailure.PRE_NAVIGATION_TIMEOUT,
            )
            preload_identifier = str(preload.get("identifier", ""))
            if not preload_identifier:
                raise ObscuraClientError(
                    FetchFailure.PROTOCOL,
                    "anubis-preload-install",
                    "Obscura did not acknowledge the Anubis preload",
                )

        if not pre_navigation_guard():
            raise ObscuraClientError(
                FetchFailure.FINALIZED,
                "search-pre-navigation",
                "request invocation is finalized",
            )
        homepage_event_start = len(cdp.events)
        homepage_started = time.monotonic()
        nav = await command(
            cdp,
            "Page.navigate",
            {"url": spec.homepage_url, "waitUntil": wait_until},
            session_id=session_id,
            command_stage="homepage-navigation",
            category=FetchFailure.NAVIGATION_TIMEOUT,
        )
        homepage_loader = str(nav.get("loaderId", ""))
        if not homepage_loader:
            raise ObscuraClientError(
                FetchFailure.PROTOCOL,
                "homepage-navigation",
                "homepage navigation returned no loader",
            )
        try:
            await cdp.wait_for_event(
                "Page.frameStoppedLoading",
                lambda event: event.get("frameId") == frame_id,
                remaining(
                    "homepage-event-barrier",
                    FetchFailure.POST_NAVIGATION_TIMEOUT,
                ),
                start_index=homepage_event_start,
            )
        except asyncio.TimeoutError as exc:
            raise ObscuraClientError(
                FetchFailure.POST_NAVIGATION_TIMEOUT,
                "homepage-event-barrier",
                "homepage document completion event timed out",
            ) from exc
        homepage_seconds = time.monotonic() - homepage_started
        homepage_url, homepage_status, _homepage_headers, _request_id = (
            _search_event_document(
                cdp,
                start_index=homepage_event_start,
                frame_id=frame_id,
                loader_id=homepage_loader,
                stage="homepage-events",
            )
        )
        _validate_search_terminal_url(
            homepage_url, spec.allowed_homepage_hosts, stage="homepage-origin"
        )
        homepage_html = await _search_dom(
            session,
            dom_limit=dom_limit,
            remaining=remaining,
            stage_prefix="homepage",
        )
        homepage_challenge, homepage_signal = _challenge_details(
            homepage_status, homepage_url, homepage_html
        )
        if homepage_challenge is not None:
            if (
                spec.anubis_pow
                and homepage_challenge is FetchFailure.CAPTCHA
                and homepage_signal == "anubis-verification"
            ):
                try:
                    challenge = parse_anubis_challenge(homepage_html)
                except AnubisProtocolError as exc:
                    raise ObscuraClientError(
                        FetchFailure.CAPTCHA,
                        "anubis-profile",
                        "provider returned an unsupported Anubis profile",
                    ) from exc
                status_value = await _anubis_worker_control(
                    session,
                    control_name=preload_control,
                    operation="status",
                    remaining=remaining,
                )
                _validate_anubis_worker_status(status_value)
                token = secrets.token_hex(32)
                owner._pending_anubis = _PendingAnubisContinuation(
                    token=token,
                    challenge=challenge,
                    boundary="homepage",
                    challenged_url=homepage_url,
                    challenged_loader=homepage_loader,
                    query=query,
                    spec=spec,
                    fixed_fields=fixed_fields,
                    text_entry_mode=text_entry_mode,
                    request_deadline=request_deadline,
                    dom_limit=dom_limit,
                    homepage_navigation_seconds=homepage_seconds,
                    submission_navigation_seconds=0.0,
                    diagnostic_id=diagnostic_id,
                    preload_identifier=preload_identifier,
                    preload_control=preload_control,
                )
                return PendingAnubisPow(token, challenge)
            raise ObscuraClientError(
                homepage_challenge,
                "homepage-classification",
                "provider homepage returned a blocking classification",
            )
        if homepage_status == 404 or homepage_status >= 500:
            raise ObscuraClientError(
                FetchFailure.HTTP_STATUS,
                "homepage-classification",
                "provider homepage returned a terminal HTTP status",
            )
        del homepage_html

        await _enter_search_query(
            session,
            spec=spec,
            fixed_fields=fixed_fields,
            query=query,
            text_entry_mode=text_entry_mode,
            remaining=remaining,
            timing_random=_timing_random,
            timing_sleep=_timing_sleep,
        )
        (
            result_url,
            result_status,
            result_headers,
            result_html,
            result_loader,
            submission_seconds,
        ) = await _submit_form_document(
            cdp,
            session,
            frame_id=frame_id,
            previous_loader=homepage_loader,
            spec=spec,
            fixed_fields=fixed_fields,
            query=query,
            dom_limit=dom_limit,
            request_deadline=request_deadline,
            remaining=remaining,
        )
        challenge, result_signal = _challenge_details(
            result_status, result_url, result_html
        )
        if (
            challenge is FetchFailure.CAPTCHA
            and result_signal == "anubis-verification"
            and spec.anubis_pow
        ):
            try:
                anubis_challenge = parse_anubis_challenge(result_html)
            except AnubisProtocolError as exc:
                raise ObscuraClientError(
                    FetchFailure.CAPTCHA,
                    "anubis-profile",
                    "provider returned an unsupported Anubis profile",
                ) from exc
            status_value = await _anubis_worker_control(
                session,
                control_name=preload_control,
                operation="status",
                remaining=remaining,
            )
            _validate_anubis_worker_status(status_value)
            token = secrets.token_hex(32)
            owner._pending_anubis = _PendingAnubisContinuation(
                token=token,
                challenge=anubis_challenge,
                boundary="result",
                challenged_url=result_url,
                challenged_loader=result_loader,
                query=query,
                spec=spec,
                fixed_fields=fixed_fields,
                text_entry_mode=text_entry_mode,
                request_deadline=request_deadline,
                dom_limit=dom_limit,
                homepage_navigation_seconds=homepage_seconds,
                submission_navigation_seconds=submission_seconds,
                diagnostic_id=diagnostic_id,
                preload_identifier=preload_identifier,
                preload_control=preload_control,
            )
            return PendingAnubisPow(token, anubis_challenge)
        if spec.anubis_pow:
            await _remove_anubis_preload(
                cdp,
                session,
                session_id=session_id,
                identifier=preload_identifier,
                control_name=preload_control,
                remaining=remaining,
            )
        LOGGER.info(
            "obscura search completed request_id=%s entry_mode=%s "
            "status_class=%sxx homepage_seconds=%.3f submission_seconds=%.3f "
            "challenge=%s",
            diagnostic_id,
            text_entry_mode,
            result_status // 100,
            homepage_seconds,
            submission_seconds,
            challenge.value if challenge is not None else "none",
        )
        return SearchSubmissionResult(
            final_url=result_url,
            status=result_status,
            headers=result_headers,
            rendered_html=result_html,
            challenge=challenge,
            homepage_navigation_seconds=homepage_seconds,
            submission_navigation_seconds=submission_seconds,
        )
    except asyncio.CancelledError:
        ambiguous = True
        raise
    except ObscuraClientError as exc:
        ambiguous = spec.anubis_pow or exc.category not in {
            FetchFailure.FINALIZED,
            FetchFailure.HTTP_STATUS,
            FetchFailure.ACCESS_DENIED,
            FetchFailure.RATE_LIMITED,
            FetchFailure.CAPTCHA,
        }
        LOGGER.warning(
            "obscura search failed request_id=%s entry_mode=%s category=%s "
            "stage=%s elapsed_seconds=%.3f",
            diagnostic_id,
            text_entry_mode,
            exc.category.value,
            exc.stage,
            time.monotonic() - started,
        )
        raise
    except Exception as exc:
        ambiguous = True
        mapped = ObscuraClientError(
            FetchFailure.TRANSPORT, stage, "Obscura search transaction failed"
        )
        LOGGER.warning(
            "obscura search failed request_id=%s entry_mode=%s category=%s "
            "stage=%s elapsed_seconds=%.3f",
            diagnostic_id,
            text_entry_mode,
            mapped.category.value,
            mapped.stage,
            time.monotonic() - started,
        )
        raise mapped from exc
    finally:
        if ambiguous:
            try:
                await owner.close()
            except Exception:
                LOGGER.warning(
                    "obscura cleanup failed request_id=%s stage=search-generation-close",
                    diagnostic_id,
                )


def _validate_anubis_solution(
    pending: _PendingAnubisContinuation, solution: AnubisSolution
) -> None:
    if (
        not isinstance(solution, AnubisSolution)
        or re.fullmatch(r"[0-9a-f]{64}", solution.response) is None
        or type(solution.nonce) is not int
        or solution.nonce < 0
        or type(solution.elapsed_ms) is not int
        or solution.elapsed_ms < 0
    ):
        raise ObscuraClientError(
            FetchFailure.PROTOCOL,
            "anubis-solution",
            "Anubis solution is invalid",
        )
    expected = hashlib.sha256(
        (pending.challenge.random_data + str(solution.nonce)).encode("ascii")
    ).hexdigest()
    if (
        not secrets.compare_digest(solution.response, expected)
        or not solution.response.startswith("0" * pending.challenge.difficulty)
    ):
        raise ObscuraClientError(
            FetchFailure.PROTOCOL,
            "anubis-solution",
            "Anubis solution does not satisfy the pending challenge",
        )


async def resume_anubis_pow(
    continuation_token: str,
    solution: AnubisSolution,
    *,
    session_owner: SearchBrowserSession,
) -> SearchSubmissionResult:
    """Consume one pending proof and continue in its retained target."""
    owner = session_owner
    pending = owner._pending_anubis
    if (
        not isinstance(continuation_token, str)
        or pending is None
        or not secrets.compare_digest(continuation_token, pending.token)
    ):
        try:
            await owner.close()
        except Exception:
            LOGGER.warning("obscura cleanup failed stage=anubis-invalid-token-close")
        raise ObscuraClientError(
            FetchFailure.PROTOCOL,
            "anubis-continuation",
            "Anubis continuation token is invalid",
        )
    try:
        _validate_anubis_solution(pending, solution)
    except ObscuraClientError:
        owner._pending_anubis = None
        try:
            await owner.close()
        except Exception:
            LOGGER.warning(
                "obscura cleanup failed request_id=%s stage=anubis-invalid-solution-close",
                pending.diagnostic_id,
            )
        raise
    owner._pending_anubis = None
    cdp = owner._connection.cdp
    session_id = owner._session_id
    frame_id = owner._frame_id
    if cdp is None or not session_id or not frame_id:
        try:
            await owner.close()
        except Exception:
            LOGGER.warning(
                "obscura cleanup failed request_id=%s stage=anubis-missing-generation-close",
                pending.diagnostic_id,
            )
        raise ObscuraClientError(
            FetchFailure.PROTOCOL,
            "anubis-continuation",
            "Anubis continuation generation is unavailable",
        )
    session = _Session(cdp, session_id)
    stage = "anubis-continuation"

    def remaining(next_stage: str, category: FetchFailure) -> float:
        nonlocal stage
        stage = next_stage
        value = pending.request_deadline - time.monotonic()
        if value <= 0:
            raise ObscuraClientError(
                category,
                next_stage,
                f"Obscura did not complete {next_stage} before its deadline",
            )
        return value

    try:
        event_start = len(cdp.events)
        result = await session.send(
            "Runtime.callFunctionOn",
            {
                "functionDeclaration": "function(path, id, response, nonce, redir, elapsedTime) { const url = new URL(path, location.origin); url.searchParams.set('id', id); url.searchParams.set('response', response); url.searchParams.set('nonce', String(nonce)); url.searchParams.set('redir', redir); url.searchParams.set('elapsedTime', String(elapsedTime)); location.replace(url.href); }",
                "arguments": [
                    {"value": ANUBIS_PASS_PATH},
                    {"value": pending.challenge.challenge_id},
                    {"value": solution.response},
                    {"value": solution.nonce},
                    {"value": pending.challenged_url},
                    {"value": solution.elapsed_ms},
                ],
                "returnByValue": True,
                "awaitPromise": True,
            },
            timeout_seconds=remaining(
                "anubis-pass-submit", FetchFailure.POST_NAVIGATION_TIMEOUT
            ),
            timeout_category=FetchFailure.POST_NAVIGATION_TIMEOUT,
            timeout_stage="anubis-pass-submit",
        )
        if result.get("exceptionDetails") is not None:
            raise ObscuraClientError(
                FetchFailure.PROTOCOL,
                "anubis-pass-submit",
                "Anubis pass navigation failed",
            )
        loader_id = await _wait_for_distinct_search_document(
            cdp,
            frame_id=frame_id,
            previous_loader=pending.challenged_loader,
            start_index=event_start,
            remaining=remaining,
            stage_prefix="anubis-pass",
        )
        pass_requests = []
        for event in cdp.events[event_start:]:
            if event.get("method") != "Network.requestWillBeSent":
                continue
            params = event.get("params", {})
            if params.get("frameId") != frame_id or params.get("loaderId") != loader_id:
                continue
            request = params.get("request", {})
            if params.get("type") != "Document":
                continue
            request_url = str(request.get("url", ""))
            try:
                parsed = urlsplit(request_url)
            except ValueError:
                continue
            if unquote(parsed.path) == ANUBIS_PASS_PATH:
                pass_requests.append((str(request.get("method", "")), parsed))
        if len(pass_requests) > 1:
            raise ObscuraClientError(
                FetchFailure.PROTOCOL,
                "anubis-pass-events",
                "Anubis pass request was not uniquely correlated",
            )
        if pass_requests:
            pass_method, pass_request = pass_requests[0]
            challenged = urlsplit(pending.challenged_url)
            field_pairs = parse_qsl(pass_request.query, keep_blank_values=True)
            fields = dict(field_pairs)
            if (
                pass_method != "GET"
                or pass_request.scheme != "https"
                or pass_request.hostname != challenged.hostname
                or (pass_request.port or 443) != (challenged.port or 443)
                or len(field_pairs) != 5
                or set(fields) != {"id", "response", "nonce", "redir", "elapsedTime"}
                or fields["id"] != pending.challenge.challenge_id
                or fields["response"] != solution.response
                or fields["nonce"] != str(solution.nonce)
                or fields["redir"] != pending.challenged_url
                or fields["elapsedTime"] != str(solution.elapsed_ms)
            ):
                raise ObscuraClientError(
                    FetchFailure.POLICY_DENIED,
                    "anubis-pass-events",
                    "Anubis pass request violated its continuation policy",
                )
        final_url, status, headers, _request_id = _search_event_document(
            cdp,
            start_index=event_start,
            frame_id=frame_id,
            loader_id=loader_id,
            stage="anubis-pass-events",
        )
        _validate_search_terminal_url(
            final_url,
            pending.spec.allowed_result_hosts | pending.spec.allowed_homepage_hosts,
            stage="anubis-pass-origin",
        )
        html = await _search_dom(
            session,
            dom_limit=pending.dom_limit,
            remaining=remaining,
            stage_prefix="anubis-pass",
        )
        challenge = _challenge(status, final_url, html)
        if challenge is not None:
            raise ObscuraClientError(
                challenge,
                "anubis-pass-classification",
                "provider rejected or renewed the Anubis challenge",
            )

        has_form = await _search_form_present(
            session,
            selector=pending.spec.query_selector,
            remaining=remaining,
        )
        if pending.boundary == "homepage" and not has_form:
            raise ObscuraClientError(
                FetchFailure.PROTOCOL,
                "anubis-homepage-restore",
                "Anubis homepage continuation did not restore the declared form",
            )
        submission_seconds = pending.submission_navigation_seconds
        should_submit = pending.boundary == "homepage" or (
            pending.boundary == "result"
            and final_url != pending.challenged_url
            and has_form
        )
        if should_submit:
            await _enter_search_query(
                session,
                spec=pending.spec,
                fixed_fields=pending.fixed_fields,
                query=pending.query,
                text_entry_mode=pending.text_entry_mode,
                remaining=remaining,
            )
            (
                final_url,
                status,
                headers,
                html,
                _loader_id,
                restored_seconds,
            ) = await _submit_form_document(
                cdp,
                session,
                frame_id=frame_id,
                previous_loader=loader_id,
                spec=pending.spec,
                fixed_fields=pending.fixed_fields,
                query=pending.query,
                dom_limit=pending.dom_limit,
                request_deadline=pending.request_deadline,
                remaining=remaining,
            )
            submission_seconds += restored_seconds
            challenge = _challenge(status, final_url, html)
            if challenge is not None:
                raise ObscuraClientError(
                    challenge,
                    "anubis-restored-result-classification",
                    "provider returned another blocking challenge",
                )
        await _remove_anubis_preload(
            cdp,
            session,
            session_id=session_id,
            identifier=pending.preload_identifier,
            control_name=pending.preload_control,
            remaining=remaining,
        )
        LOGGER.info(
            "obscura search completed request_id=%s entry_mode=%s status_class=%sxx "
            "homepage_seconds=%.3f submission_seconds=%.3f challenge=none pow=solved",
            pending.diagnostic_id,
            pending.text_entry_mode,
            status // 100,
            pending.homepage_navigation_seconds,
            submission_seconds,
        )
        return SearchSubmissionResult(
            final_url=final_url,
            status=status,
            headers=headers,
            rendered_html=html,
            challenge=None,
            homepage_navigation_seconds=pending.homepage_navigation_seconds,
            submission_navigation_seconds=submission_seconds,
        )
    except BaseException:
        try:
            await owner.close()
        except Exception:
            LOGGER.warning(
                "obscura cleanup failed request_id=%s stage=anubis-generation-close",
                pending.diagnostic_id,
            )
        raise


async def abort_anubis_pow(
    continuation_token: str, *, session_owner: SearchBrowserSession
) -> None:
    """Consume and close one pending continuation without submitting proof."""
    pending = session_owner._pending_anubis
    if (
        pending is None
        or not isinstance(continuation_token, str)
        or not secrets.compare_digest(continuation_token, pending.token)
    ):
        try:
            await session_owner.close()
        except Exception:
            LOGGER.warning("obscura cleanup failed stage=anubis-invalid-abort-close")
        raise ObscuraClientError(
            FetchFailure.PROTOCOL,
            "anubis-abort",
            "Anubis continuation token is invalid",
        )
    session_owner._pending_anubis = None
    await session_owner.close()


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
