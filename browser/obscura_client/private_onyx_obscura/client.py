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
import re
import socket
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Literal
from urllib.parse import SplitResult, urlsplit, urlunsplit

LOGGER = logging.getLogger("private_onyx_obscura")
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


def normalize_public_url(url: str, *, allow_http: bool) -> tuple[str, str | None]:
    """Validate without resolving the target and remove its fragment."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ObscuraClientError(
            FetchFailure.INVALID_URL, "validate", "malformed URL"
        ) from exc
    if parsed.scheme not in ({"https", "http"} if allow_http else {"https"}):
        raise ObscuraClientError(
            FetchFailure.INVALID_URL, "validate", "URL scheme is not allowed"
        )
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
    """Mirror Obscura v0.1.10 ``is_text_like_content_type`` exactly."""
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


def _challenge(status: int, final_url: str, html: str | None) -> FetchFailure | None:
    if status == 429:
        return FetchFailure.RATE_LIMITED
    if status in {401, 402, 403}:
        return FetchFailure.ACCESS_DENIED
    bounded = (html or "")[:262_144].lower()
    if any(token in bounded for token in ("captcha", "verify you are human", "unusual traffic")):
        return FetchFailure.CAPTCHA
    return None


async def _drain_body(session, request_id: str, expected: int, limit: int) -> bytes:
    handle: str | None = None
    data = bytearray()
    try:
        opened = await session.send(
            "Fetch.takeResponseBodyAsStream", {"requestId": request_id}
        )
        handle = opened.get("stream")
        if not isinstance(handle, str) or not handle:
            raise ObscuraClientError(
                FetchFailure.PROTOCOL, "body", "Obscura returned an invalid body handle"
            )
        while True:
            chunk = await session.send("IO.read", {"handle": handle, "size": 1 << 20})
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
                await session.send("IO.close", {"handle": handle})
            except Exception:
                LOGGER.warning("obscura cleanup failed stage=body-close")


class _RawCdp:
    """Minimal flattened CDP transport for the audited Obscura surface.

    Obscura 0.1.10 reuses its target's attached session id. Playwright 1.58's
    public ``new_cdp_session(page)`` asks for a second attachment with that
    same id and its driver aborts on the duplicate. This transport keeps the
    exact one-session protocol contract without a compatibility shim.
    """

    def __init__(self, websocket):
        self.websocket = websocket
        self.next_id = 0
        self.events: list[dict] = []

    async def send(
        self, method: str, params: dict | None = None, *, session_id: str | None = None
    ) -> dict:
        self.next_id += 1
        command_id = self.next_id
        command = {"id": command_id, "method": method, "params": params or {}}
        if session_id is not None:
            command["sessionId"] = session_id
        await self.websocket.send(json.dumps(command, separators=(",", ":")))
        while True:
            message = await self.websocket.recv()
            if isinstance(message, bytes):
                message = message.decode("utf-8", "strict")
            packet = json.loads(message)
            if packet.get("id") == command_id:
                error = packet.get("error")
                if error:
                    raise ObscuraClientError(
                        FetchFailure.PROTOCOL,
                        "cdp-command",
                        f"Obscura rejected {method}",
                    )
                result = packet.get("result", {})
                if not isinstance(result, dict):
                    raise ObscuraClientError(
                        FetchFailure.PROTOCOL, "cdp-command", "invalid CDP result"
                    )
                return result
            if isinstance(packet, dict) and isinstance(packet.get("method"), str):
                self.events.append(packet)

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


class _Session:
    def __init__(self, cdp: _RawCdp, session_id: str):
        self.cdp = cdp
        self.session_id = session_id

    async def send(self, method: str, params: dict | None = None) -> dict:
        return await self.cdp.send(method, params, session_id=self.session_id)


async def fetch(
    url: str,
    *,
    cdp_url: str,
    wait_until: str,
    allow_http: bool,
    body_limit: int,
    dom_limit: int,
    want: Literal["dom", "body", "both"] = "dom",
    event_timeout_seconds: float = 5.0,
    pre_navigation_guard: Callable[[], bool] | None = None,
) -> FetchResult:
    """Perform exactly one Obscura navigation and consume only its output."""
    if body_limit <= 0 or dom_limit <= 0:
        raise ValueError("body and DOM limits must be positive")
    wait_until = validate_wait_until(wait_until)
    navigation_url, _fragment = normalize_public_url(url, allow_http=allow_http)
    started = time.monotonic()
    websocket = cdp = session = None
    target_id: str | None = None
    stream_started = started
    try:
        from websockets.asyncio.client import connect

        websocket = await connect(
            cdp_url,
            proxy=None,
            open_timeout=10,
            close_timeout=5,
            max_size=max(body_limit, dom_limit) + (1 << 20),
        )
        cdp = _RawCdp(websocket)
        await cdp.send("Network.clearBrowserCookies")
        created = await cdp.send("Target.createTarget", {"url": "about:blank"})
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
        await session.send("Network.enable")
        await session.send("Page.enable")
        await session.send("Page.setLifecycleEventsEnabled", {"enabled": True})
        frame_tree = await session.send("Page.getFrameTree")
        frame_id = frame_tree["frameTree"]["frame"]["id"]
        if pre_navigation_guard is not None and not pre_navigation_guard():
            raise ObscuraClientError(
                FetchFailure.FINALIZED, "pre-navigation", "request invocation is finalized"
            )
        nav_started = time.monotonic()
        nav = await session.send(
            "Page.navigate", {"url": navigation_url, "waitUntil": wait_until}
        )
        loader_id = str(nav.get("loaderId", ""))
        await cdp.wait_for_event(
            "Page.frameStoppedLoading",
            lambda event: event.get("frameId") == frame_id,
            event_timeout_seconds,
        )
        navigation_seconds = time.monotonic() - nav_started
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
        normalize_public_url(final_url, allow_http=allow_http)
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
            document_node = await session.send("DOM.getDocument", {"depth": 0})
            node_id = document_node.get("root", {}).get("nodeId")
            outer = await session.send("DOM.getOuterHTML", {"nodeId": node_id})
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
        stream_started = time.monotonic()
        if want in {"body", "both"}:
            body = await _drain_body(session, request_id, completed, body_limit)
        classification = (
            BodyClassification.TEXT
            if is_text_like_content_type(headers.get("content-type"))
            else BodyClassification.BINARY
        )
        challenge = _challenge(status, final_url, rendered_html)
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
            original_byte_identity=classification is BodyClassification.BINARY,
            lossy_conversion_possible=classification is BodyClassification.TEXT,
            completed_body_bytes=completed,
            rendered_html=rendered_html,
            body=body,
            navigation_seconds=navigation_seconds,
            body_read_seconds=time.monotonic() - stream_started,
            challenge=challenge,
        )
    except ObscuraClientError:
        raise
    except asyncio.TimeoutError as exc:
        raise ObscuraClientError(
            FetchFailure.TRANSPORT, "event-barrier", "Obscura event barrier timed out"
        ) from exc
    except Exception as exc:
        message = str(exc).lower()
        category = (
            FetchFailure.POLICY_DENIED
            if any(token in message for token in ("forbidden", "blocked", "private", "proxy"))
            else FetchFailure.NAVIGATION_TIMEOUT
            if "timeout" in message
            else FetchFailure.TRANSPORT
        )
        raise ObscuraClientError(category, "cdp", "Obscura request failed") from exc
    finally:
        if cdp is not None and target_id:
            try:
                await cdp.send("Target.closeTarget", {"targetId": target_id})
            except Exception:
                LOGGER.warning("obscura cleanup failed stage=target-close")
        if websocket is not None:
            try:
                await websocket.close()
            except Exception:
                LOGGER.warning("obscura cleanup failed stage=connection-close")


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
