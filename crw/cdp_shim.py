#!/usr/bin/env python3
"""CDP WebSocket proxy shim between CRW and obscura.

Sits between CRW (the Firecrawl-compatible scraper) and obscura (the stealth
headless browser) to provide:

1. **STEALTH_JS stripping** — CRW unconditionally injects its own stealth
   script via ``Page.addScriptToEvaluateOnNewDocument`` (crates/crw-renderer/
   src/cdp.rs:1911). This script hardcodes Mac-specific WebGL strings that
   conflict with obscura's Linux platform. The shim intercepts these calls
   and replaces the script source with a no-op, letting obscura's own stealth
   mode (enabled via ``--stealth``) handle fingerprinting without conflict.

2. **waitUntil injection** — CRW sends ``Page.navigate`` with only ``{url}``
   and no ``waitUntil`` field. Obscura defaults to ``DomContentLoaded`` and
   returns immediately, without waiting for JS-driven content to load. When
   ``OBSCURA_BROWSER_WAIT_UNTIL_SEARCH`` / ``OBSCURA_BROWSER_WAIT_UNTIL_WEB``
   are set, the shim injects the selected value into ``Page.navigate`` so
   obscura adaptively waits for network
   silence before returning the nav response. This is event-driven: fast
   pages return in ~500ms (the network-idle quiet period), while slow
   Tor/proxy pages get up to ``OBSCURA_NAV_TIMEOUT_MS`` (obscura env var).

   A fixed ``waitFor`` sleep (CRW's alternative) is actively harmful: it
   wastes time on fast pages (always sleeps the full duration even when the
   page is ready) and is insufficient on slow pages (Tor/VPN loads can take
   20-40s, far exceeding any reasonable fixed sleep). The scrape payloads
   omit ``waitFor`` entirely — CRW uses its smart heuristics (SPA selector
   poll, content stability, challenge retry) for post-navigate work instead.

3. **Periodic browser state clearing** — In the normal wrapper path CRW uses
   ``Target.createTarget`` on obscura's default CDP context. Cookies can
   accumulate in the shared ``default_context.cookie_jar`` between WebSocket
   connections. The shim periodically clears cookies via CDP
   ``Network.clearBrowserCookies`` and best-effort ``Storage.clearCookies`` to
   prevent stale cookies from interfering with searches and to limit
   cross-query tracking surface.

4. **Plain HTTP navigation blocking** — By default,
   ``ONYX_AGENT_ALLOW_HTTP_URLS=false`` makes the shim reject CDP
   ``Page.navigate`` and ``Target.createTarget`` calls whose target URL is
   ``http://``. This mirrors the prefetch-blocking proxy policy so a CRW
   escalation cannot silently fetch cleartext HTTP in the browser path.

5. **Unsupported method error suppression** — Obscura doesn't implement
   some CDP methods (e.g. ``Page.stopLoading``). The shim downgrades these
   non-fatal errors from WARNING to DEBUG to reduce log noise.

Architecture::

    CRW :3010 ──ws──> cdp-shim :9224 ──ws──> obscura :9222

CRW's ``CRW_RENDERER__CHROME__WS_URL`` points at ``ws://127.0.0.1:9224``
instead of ``ws://127.0.0.1:9222``.

Other CDP traffic is forwarded transparently in both directions.
All unexpected behavior is logged — no exceptions are silently swallowed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import traceback
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import websockets
from websockets.exceptions import ConnectionClosed

# ── Configuration ────────────────────────────────────────────────────────

LISTEN_HOST = os.environ.get("CDP_SHIM_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("CDP_SHIM_PORT", "9224"))
UPSTREAM_WS_URL = os.environ.get(
    "OBSCURA_CDP_URL", "ws://127.0.0.1:9222/devtools/browser"
)
# Whether to strip CRW's STEALTH_JS injection.
STRIP_STEALTH_JS = os.environ.get("CDP_SHIM_STRIP_STEALTH_JS", "1") == "1"
# Inject a `waitUntil` field into every Page.navigate call so obscura waits
# for the specified lifecycle event before returning the nav response.
#
# Obscura fires lifecycle events in this order (each implies the previous):
#   1. domcontentloaded  — DOM parsed + scripts executed
#   2. load              — all subresources finished, window.onload fired
#   3. networkidle2      — ≤2 active network requests for 500ms
#   4. networkidle0     — 0 active network requests for 500ms
#
# Obscura returns at the first event matching `waitUntil`. The network-idle
# events have a 5-second deadline (separate from OBSCURA_NAV_TIMEOUT_MS).
#
# Two env vars control per-URL waitUntil injection:
#
# OBSCURA_BROWSER_WAIT_UNTIL_SEARCH: Used for search engine URLs (SERPs). These pages
#   are JS-heavy SPAs that load results via XHR/fetch, so networkidle2
#   (≤2 active connections for 500ms) ensures the results have loaded.
#   Default: "networkidle2".
#
# OBSCURA_BROWSER_WAIT_UNTIL_WEB: Used for all other URLs (open_url web pages).
#   These pages typically have their content ready at the `load` event
#   (all subresources finished), and waiting for network idle adds latency
#   without benefit — many modern sites keep long-polling connections open
#   (analytics, websockets) that prevent network idle from ever being reached.
#   Default: "load".
#
# Search engine hosts are matched by eTLD+1 against
# OBSCURA_BROWSER_WAIT_UNTIL_SEARCH_HOSTS (comma-separated, same list as the
# prefetch-blocking proxy's PREFETCH_BLOCK_HOSTS).
#
# Leave both empty to disable injection entirely (obscura defaults to
# DomContentLoaded; CRW uses its smart heuristics for post-navigate work).
WAIT_UNTIL_SEARCH = os.environ.get("OBSCURA_BROWSER_WAIT_UNTIL_SEARCH", "networkidle2").strip()
WAIT_UNTIL_WEB = os.environ.get("OBSCURA_BROWSER_WAIT_UNTIL_WEB", "load").strip()
# Search engine hosts for per-URL waitUntil selection.
WAIT_UNTIL_SEARCH_HOSTS = frozenset(
    h.strip().lower()
    for h in os.environ.get(
        "OBSCURA_BROWSER_WAIT_UNTIL_SEARCH_HOSTS",
        "google.com,search.brave.com,html.duckduckgo.com,startpage.com,bing.com",
    ).split(",")
    if h.strip()
)
# Plain HTTP URLs are blocked by default. This mirrors the prefetch-blocking
# proxy policy so a CRW fallback from prefetch to CDP cannot silently fetch
# http:// pages in the browser path.
ALLOW_HTTP_URLS = os.environ.get("ONYX_AGENT_ALLOW_HTTP_URLS", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
HTTP_URL_BLOCK_MESSAGE = (
    "HTTP URLs are disabled by ONYX_AGENT_ALLOW_HTTP_URLS=false. "
    "Use an https:// URL instead."
)
# Interval for periodic cookie clearing (seconds). 0 = disabled.
# In the normal wrapper path, CRW creates targets on obscura's default CDP
# context. Cookies can persist across CRW WebSocket connections in that
# in-process jar. This loop periodically clears them to prevent indefinite
# accumulation from open_url visits to arbitrary sites. If a future CRW config
# uses Target.createBrowserContext, obscura v0.1.9 clears cookies on
# create/dispose and this loop becomes mostly defensive.
# Default: 3600 (60 minutes) — long enough for multi-query research
# sessions, short enough to limit tracking surface.
CLEAR_STATE_INTERVAL_SECONDS = int(
    os.environ.get("OBSCURA_BROWSER_CLEAR_COOKIES_INTERVAL", "3600")
)
# Reconnect delay when obscura connection drops (seconds).
RECONNECT_DELAY_SECONDS = float(os.environ.get("CDP_SHIM_RECONNECT_DELAY", "2"))
# Max reconnect attempts before giving up.
MAX_RECONNECT_ATTEMPTS = int(os.environ.get("CDP_SHIM_MAX_RECONNECT", "10"))
# Strip proxyServer from Target.createBrowserContext calls. When CRW is
# configured with CRW_CRAWLER__PROXY (pointing at the prefetch-blocking
# proxy), CRW includes proxyServer in createBrowserContext. Obscura would
# then route its browser traffic through the blocking proxy, breaking
# stealth navigation. Stripping proxyServer lets obscura use its own
# --proxy flag instead. Set to "0" only when intentionally testing CRW's
# per-request browserContext proxy path.
STRIP_PROXY_SERVER = os.environ.get("CDP_SHIM_STRIP_PROXY_SERVER", "1") == "1"
# Optional CDP trace mode for debugging browser/search-engine behavior. This is
# intentionally disabled by default and redacts query values so searches are
# not written to logs. Set CDP_SHIM_TRACE_INCLUDE_QUERY_VALUES=1 only for local,
# short-lived debugging sessions where logging full URLs is acceptable.
TRACE_CDP = os.environ.get("CDP_SHIM_TRACE", "0") == "1"
TRACE_INCLUDE_QUERY_VALUES = (
    os.environ.get("CDP_SHIM_TRACE_INCLUDE_QUERY_VALUES", "0") == "1"
)
TRACE_SAFE_QUERY_KEYS = frozenset(
    key.strip()
    for key in os.environ.get(
        "CDP_SHIM_TRACE_SAFE_QUERY_KEYS",
        "udm,hl,gl,start,tbs,safe,filter",
    ).split(",")
    if key.strip()
)
TRACE_MAX_URL_CHARS = int(os.environ.get("CDP_SHIM_TRACE_MAX_URL_CHARS", "240"))

logging.basicConfig(
    level=os.environ.get("CDP_SHIM_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [cdp-shim] %(levelname)s %(message)s",
)
logger = logging.getLogger("cdp-shim")

# Suppress websockets server INFO logs (healthcheck TCP probes cause 400 noise).
logging.getLogger("websockets.server").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)


def _redact_url(url: Any) -> str:
    """Return a log-safe URL with sensitive query values redacted."""
    if not isinstance(url, str) or not url:
        return ""
    try:
        parsed = urlparse(url)
    except Exception:
        return str(url)[:TRACE_MAX_URL_CHARS]

    if parsed.scheme == "data":
        return "data:<redacted>"

    if not parsed.scheme or not parsed.netloc:
        return url[:TRACE_MAX_URL_CHARS]

    query_items: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if TRACE_INCLUDE_QUERY_VALUES or key in TRACE_SAFE_QUERY_KEYS:
            query_items.append((key, value))
        else:
            query_items.append((key, "<redacted>"))

    redacted = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(query_items, doseq=True),
            "",
        )
    )
    return redacted[:TRACE_MAX_URL_CHARS]


def _safe_event_params(params: Any) -> dict[str, Any]:
    """Extract a small, log-safe subset from CDP event params."""
    if not isinstance(params, dict):
        return {}

    out: dict[str, Any] = {}
    if "type" in params:
        out["type"] = params.get("type")
    if "timestamp" in params:
        out["timestamp"] = params.get("timestamp")

    response = params.get("response")
    if isinstance(response, dict):
        out["status"] = response.get("status")
        out["mimeType"] = response.get("mimeType")
        out["url"] = _redact_url(response.get("url"))

    request = params.get("request")
    if isinstance(request, dict):
        out["url"] = _redact_url(request.get("url"))
        out["method"] = request.get("method")

    frame = params.get("frame")
    if isinstance(frame, dict):
        out["url"] = _redact_url(frame.get("url"))
        out["mimeType"] = frame.get("mimeType")

    target_info = params.get("targetInfo")
    if isinstance(target_info, dict):
        out["type"] = target_info.get("type")
        out["url"] = _redact_url(target_info.get("url"))

    for key in (
        "requestId",
        "loaderId",
        "frameId",
        "name",
        "errorText",
        "blockedReason",
        "canceled",
    ):
        if key in params:
            out[key] = params.get(key)

    return out


# ── WebSocket proxy with CDP interception ────────────────────────────────


class CdpProxy:
    """Bidirectional WebSocket proxy with CDP message interception.

    All errors are logged — no exceptions are silently swallowed. If a
    message can't be parsed or an interception fails, the error is logged
    and the message is forwarded unchanged (fail-open) so CRW doesn't hang.
    """

    # CRW's STEALTH_JS starts with this comment — we detect it to strip.
    STEALTH_JS_MARKER = "Hide navigator.webdriver"

    def __init__(self, crw_ws: Any) -> None:
        self.crw_ws = crw_ws
        self.upstream_ws: Any = None
        self.pending_commands: dict[int, dict[str, Any]] = {}

    async def _send_cdp_error(
        self,
        msg_id: Any,
        message: str,
        *,
        code: int = -32000,
    ) -> None:
        """Send a CDP error response directly to CRW."""
        if msg_id is None:
            logger.warning("Cannot send CDP error without command id: %s", message)
            return
        await self.crw_ws.send(
            json.dumps(
                {
                    "id": msg_id,
                    "error": {
                        "code": code,
                        "message": message,
                    },
                }
            )
        )

    def _blocked_http_url_message(self, url: Any) -> str | None:
        """Return a policy error for blocked http:// URLs, else None."""
        if ALLOW_HTTP_URLS or not isinstance(url, str):
            return None
        try:
            parsed = urlparse(url)
        except Exception:
            return None
        if parsed.scheme.lower() != "http":
            return None
        return f"{HTTP_URL_BLOCK_MESSAGE} Blocked URL: {_redact_url(url)}"

    async def run(self) -> None:
        """Connect to obscura and run bidirectional proxy loops."""
        last_error: Exception | None = None
        for attempt in range(1, MAX_RECONNECT_ATTEMPTS + 1):
            try:
                self.upstream_ws = await asyncio.wait_for(
                    websockets.connect(
                        UPSTREAM_WS_URL,
                        max_size=50 * 1024 * 1024,
                        ping_interval=20,
                        ping_timeout=10,
                    ),
                    timeout=10,
                )
                logger.info(
                    "Connected to obscura at %s (attempt %d)",
                    UPSTREAM_WS_URL,
                    attempt,
                )
                last_error = None
                break
            except Exception as e:
                last_error = e
                logger.warning(
                    "Failed to connect to obscura (attempt %d/%d): %s: %s",
                    attempt,
                    MAX_RECONNECT_ATTEMPTS,
                    type(e).__name__,
                    e,
                )
                if attempt < MAX_RECONNECT_ATTEMPTS:
                    await asyncio.sleep(RECONNECT_DELAY_SECONDS * attempt)

        if self.upstream_ws is None:
            logger.error(
                "Could not connect to obscura after %d attempts: %s. "
                "Closing CRW connection.",
                MAX_RECONNECT_ATTEMPTS,
                last_error,
            )
            try:
                await self.crw_ws.close(
                    code=1011, reason="upstream unavailable"
                )
            except Exception as e:
                logger.error(
                    "Failed to close CRW WebSocket after upstream failure: "
                    "%s: %s",
                    type(e).__name__,
                    e,
                )
            return

        try:
            await asyncio.gather(
                self._crw_to_upstream(),
                self._upstream_to_crw(),
            )
        except ConnectionClosed as e:
            logger.info(
                "WebSocket connection closed: %s (code=%s)",
                type(e).__name__,
                getattr(e, "code", "?"),
            )
        except Exception as e:
            logger.error(
                "Unexpected proxy error: %s: %s\n%s",
                type(e).__name__,
                e,
                traceback.format_exc(),
            )
        finally:
            if self.upstream_ws:
                try:
                    await self.upstream_ws.close()
                except Exception as e:
                    logger.debug(
                        "Error closing upstream WebSocket: %s: %s",
                        type(e).__name__,
                        e,
                    )

    async def _crw_to_upstream(self) -> None:
        """Forward CRW → obscura, intercepting STEALTH_JS injection."""
        async for raw_msg in self.crw_ws:
            try:
                await self._handle_crw_message(raw_msg)
            except ConnectionClosed:
                raise
            except Exception as e:
                logger.error(
                    "Error handling CRW message: %s: %s\n%s",
                    type(e).__name__,
                    e,
                    traceback.format_exc(),
                )
                try:
                    if self.upstream_ws and not self.upstream_ws.closed:
                        await self.upstream_ws.send(raw_msg)
                except Exception:
                    logger.error(
                        "Fallback forward also failed — message dropped. "
                        "CRW may hang waiting for a response."
                    )

    async def _handle_crw_message(self, raw_msg: Any) -> None:
        """Process a single message from CRW."""
        if isinstance(raw_msg, bytes):
            await self.upstream_ws.send(raw_msg)
            return

        try:
            msg = json.loads(raw_msg)
        except json.JSONDecodeError as e:
            logger.warning(
                "Non-JSON text message from CRW (%d bytes): %s. "
                "Forwarding as-is.",
                len(raw_msg),
                e,
            )
            await self.upstream_ws.send(raw_msg)
            return

        if not isinstance(msg, dict):
            await self.upstream_ws.send(raw_msg)
            return

        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})

        if not method:
            await self.upstream_ws.send(raw_msg)
            return

        if method in {"Page.navigate", "Target.createTarget"} and isinstance(
            params, dict
        ):
            blocked_message = self._blocked_http_url_message(params.get("url"))
            if blocked_message:
                logger.info(
                    "Blocked %s id=%s url=%s (HTTP URLs disabled)",
                    method,
                    msg_id,
                    _redact_url(params.get("url")),
                )
                await self._send_cdp_error(msg_id, blocked_message)
                return

        if TRACE_CDP and isinstance(msg_id, int):
            trace_url = params.get("url") if isinstance(params, dict) else None
            self.pending_commands[msg_id] = {
                "method": method,
                "url": _redact_url(trace_url),
                "started_at": time.monotonic(),
            }

        # ── Intercept: Page.addScriptToEvaluateOnNewDocument ────────
        if (
            STRIP_STEALTH_JS
            and method == "Page.addScriptToEvaluateOnNewDocument"
        ):
            source = params.get("source", "")
            if self.STEALTH_JS_MARKER in source:
                params["source"] = ";"
                msg["params"] = params
                raw_msg = json.dumps(msg)
                logger.info(
                    "Stripped CRW STEALTH_JS from addScriptToEvaluateOnNewDocument "
                    "(id=%s, source was %d bytes)",
                    msg_id,
                    len(source),
                )
            await self.upstream_ws.send(raw_msg)
            return

        # ── Intercept: Page.navigate (inject waitUntil) ─────────────
        # CRW sends Page.navigate with only {url} — no waitUntil. Obscura
        # defaults to DomContentLoaded. By injecting waitUntil here, obscura
        # adaptively waits for the specified lifecycle event before returning
        # the nav response, bounded by OBSCURA_NAV_TIMEOUT_MS.
        #
        # Per-URL selection: search engine URLs (SERPs) use
        # WAIT_UNTIL_SEARCH (default "networkidle2") because SERPs are
        # JS-heavy SPAs that load results via XHR/fetch — network idle
        # ensures the results have loaded. All other URLs (open_url web
        # pages) use WAIT_UNTIL_WEB (default "load") because their
        # content is typically ready at the load event, and many modern
        # sites keep long-polling connections open that prevent network
        # idle from ever being reached.
        #
        # Obscura lifecycle event firing order (each implies the previous):
        #   1. domcontentloaded  — DOM parsed + scripts executed
        #   2. load              — all subresources finished, onload fired
        #   3. networkidle2      — ≤2 active network requests for 500ms
        #   4. networkidle0     — 0 active network requests for 500ms
        if method == "Page.navigate":
            if "waitUntil" not in params:
                nav_url = params.get("url", "")
                # Parse hostname to check if it's a search engine
                try:
                    from urllib.parse import urlparse as _urlparse
                    nav_host = (_urlparse(nav_url).hostname or "").lower()
                except Exception:
                    nav_host = ""

                is_search = any(
                    nav_host == h or nav_host.endswith("." + h)
                    for h in WAIT_UNTIL_SEARCH_HOSTS
                ) if nav_host else False

                wait_until_val = WAIT_UNTIL_SEARCH if is_search else WAIT_UNTIL_WEB
                if wait_until_val:
                    params["waitUntil"] = wait_until_val
                    msg["params"] = params
                    raw_msg = json.dumps(msg)
                    if TRACE_CDP and isinstance(msg_id, int):
                        self.pending_commands[msg_id]["waitUntil"] = wait_until_val
                        self.pending_commands[msg_id]["search"] = is_search
                    logger.debug(
                        "Injected waitUntil=%s into Page.navigate (id=%s, url=%s, search=%s)",
                        wait_until_val,
                        msg_id,
                        nav_url[:80],
                        is_search,
                    )
            if TRACE_CDP:
                logger.info(
                    "CDP trace command Page.navigate id=%s url=%s waitUntil=%s search=%s",
                    msg_id,
                    _redact_url(params.get("url")),
                    params.get("waitUntil"),
                    self.pending_commands.get(msg_id, {}).get("search"),
                )
            await self.upstream_ws.send(raw_msg)
            return

        # ── Intercept: Target.createBrowserContext (strip proxyServer) ─
        # When CRW is configured with CRW_CRAWLER__PROXY (pointing at the
        # prefetch-blocking proxy), CRW includes proxyServer in
        # createBrowserContext. Obscura would then route its browser traffic
        # through the blocking proxy, breaking stealth navigation. Stripping
        # proxyServer lets obscura use its own --proxy flag instead.
        #
        # With HTTPS_PROXY env vars (our setup), REQUEST_PROXY is not set,
        # so CRW does NOT call createBrowserContext at all — it uses
        # Target.createTarget without browserContextId, which creates the
        # target on obscura's shared default context. The stripping below
        # is a safety net for the CRW_CRAWLER__PROXY case.
        if method == "Target.createBrowserContext":
            had_proxy = "proxyServer" in params
            if STRIP_PROXY_SERVER and had_proxy:
                del params["proxyServer"]
                msg["params"] = params
                raw_msg = json.dumps(msg)
                logger.info(
                    "Stripped proxyServer from Target.createBrowserContext "
                    "(id=%s) — obscura uses its own --proxy",
                    msg_id,
                )
            else:
                logger.info(
                    "Target.createBrowserContext (id=%s, proxyServer=%s) "
                    "— per-request browser context path reached",
                    msg_id,
                    "present" if had_proxy else "absent",
                )
            await self.upstream_ws.send(raw_msg)
            return

        if TRACE_CDP and method in {
            "Target.createTarget",
            "Target.attachToTarget",
            "Page.enable",
            "Network.enable",
            "Runtime.enable",
            "Page.getFrameTree",
            "Runtime.evaluate",
        }:
            trace_params = {}
            if isinstance(params, dict):
                trace_params = {
                    key: _redact_url(value) if key == "url" else value
                    for key, value in params.items()
                    if key in {"url", "targetId", "type", "expression"}
                }
                if "expression" in trace_params:
                    trace_params["expression_len"] = len(
                        str(trace_params.pop("expression"))
                    )
            logger.info(
                "CDP trace command %s id=%s params=%s",
                method,
                msg_id,
                trace_params,
            )

        # ── Default: forward unchanged ──────────────────────────────
        await self.upstream_ws.send(raw_msg)

    async def _upstream_to_crw(self) -> None:
        """Forward obscura → CRW, suppressing non-fatal error logs."""
        async for raw_msg in self.upstream_ws:
            try:
                await self._handle_upstream_message(raw_msg)
            except ConnectionClosed:
                raise
            except Exception as e:
                logger.error(
                    "Error handling upstream message: %s: %s\n%s",
                    type(e).__name__,
                    e,
                    traceback.format_exc(),
                )
                try:
                    await self.crw_ws.send(raw_msg)
                except Exception:
                    logger.error(
                        "Fallback forward to CRW also failed — "
                        "message dropped."
                    )

    async def _handle_upstream_message(self, raw_msg: Any) -> None:
        """Process a single message from obscura."""
        if isinstance(raw_msg, bytes):
            await self.crw_ws.send(raw_msg)
            return

        try:
            msg = json.loads(raw_msg)
        except json.JSONDecodeError as e:
            logger.warning(
                "Non-JSON text message from obscura (%d bytes): %s. "
                "Forwarding as-is.",
                len(raw_msg),
                e,
            )
            await self.crw_ws.send(raw_msg)
            return

        if not isinstance(msg, dict):
            await self.crw_ws.send(raw_msg)
            return

        msg_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params", {})

        if TRACE_CDP and method:
            if method in {
                "Page.frameNavigated",
                "Page.lifecycleEvent",
                "Network.requestWillBeSent",
                "Network.responseReceived",
                "Network.loadingFailed",
                "Network.loadingFinished",
                "Target.targetCreated",
                "Target.targetInfoChanged",
            }:
                logger.info(
                    "CDP trace event %s params=%s",
                    method,
                    _safe_event_params(params),
                )

        if TRACE_CDP and msg_id is not None:
            pending = self.pending_commands.pop(msg_id, None)
            if pending:
                elapsed_ms = int((time.monotonic() - pending["started_at"]) * 1000)
                if "error" in msg:
                    logger.info(
                        "CDP trace response %s id=%s elapsed_ms=%d error=%s url=%s waitUntil=%s",
                        pending.get("method"),
                        msg_id,
                        elapsed_ms,
                        msg.get("error"),
                        pending.get("url"),
                        pending.get("waitUntil"),
                    )
                else:
                    result = msg.get("result")
                    result_keys = sorted(result.keys()) if isinstance(result, dict) else []
                    logger.info(
                        "CDP trace response %s id=%s elapsed_ms=%d result_keys=%s url=%s waitUntil=%s",
                        pending.get("method"),
                        msg_id,
                        elapsed_ms,
                        result_keys,
                        pending.get("url"),
                        pending.get("waitUntil"),
                    )

        # ── Check for error responses ───────────────────────────────
        if msg_id is not None and "error" in msg:
            error = msg.get("error", {})
            error_str = (
                json.dumps(error) if isinstance(error, dict) else str(error)
            )
            # Obscura doesn't implement some CDP methods (e.g.
            # Page.stopLoading). These are non-fatal — CRW continues
            # after the error. Log at DEBUG to avoid noise.
            if "Unknown" in error_str and "method" in error_str:
                logger.debug(
                    "CDP unsupported method error (id=%s, non-fatal): %s",
                    msg_id,
                    error_str,
                )
            else:
                logger.warning(
                    "CDP error response (id=%s): %s",
                    msg_id,
                    error_str,
                )

        await self.crw_ws.send(raw_msg)


# ── Periodic browser state clearing ──────────────────────────────────────


async def clear_state_loop() -> None:
    """Periodically clear cookies and browser state on obscura.

    In the normal wrapper path, CRW creates targets on obscura's default CDP
    context. Cookies can accumulate in the shared ``default_context.cookie_jar``
    across WebSocket connections. This loop periodically clears them to:
    - Prevent stale cookies from interfering with searches
    - Limit cross-query tracking surface
    - Avoid cookie accumulation from many different sites (open_url visits)

    Uses CDP ``Network.clearBrowserCookies`` (clears the cookie jar) and
    ``Storage.clearDataForOrigin`` (clears localStorage/sessionStorage).
    Connects via a separate WebSocket to avoid interfering with active
    CRW connections.
    """
    if CLEAR_STATE_INTERVAL_SECONDS <= 0:
        logger.info("Periodic state clearing disabled (interval=0)")
        return

    logger.info(
        "Periodic state clearing every %ds", CLEAR_STATE_INTERVAL_SECONDS
    )

    _clear_id = 900000  # High ID to avoid collision with CRW's IDs

    while True:
        await asyncio.sleep(CLEAR_STATE_INTERVAL_SECONDS)
        try:
            async with websockets.connect(
                UPSTREAM_WS_URL,
                max_size=50 * 1024 * 1024,
                ping_interval=20,
                ping_timeout=10,
            ) as ws:
                # Clear cookies via Network.clearBrowserCookies
                clear_cookies_msg = json.dumps({
                    "id": _clear_id,
                    "method": "Network.clearBrowserCookies",
                    "params": {},
                })
                await ws.send(clear_cookies_msg)
                try:
                    resp = await asyncio.wait_for(ws.recv(), timeout=5)
                    resp_data = json.loads(resp)
                    if "error" in resp_data:
                        logger.warning(
                            "clearBrowserCookies error: %s",
                            resp_data.get("error"),
                        )
                    else:
                        logger.info(
                            "Cleared browser cookies (periodic, %ds interval)",
                            CLEAR_STATE_INTERVAL_SECONDS,
                        )
                except asyncio.TimeoutError:
                    logger.warning(
                        "Timeout waiting for clearBrowserCookies response"
                    )

                # Also clear cookies via the Storage domain (some cookies
                # may be managed differently). Use clearCookies which
                # targets the default context's cookie jar.
                clear_storage_msg = json.dumps({
                    "id": _clear_id + 1,
                    "method": "Storage.clearCookies",
                    "params": {},
                })
                await ws.send(clear_storage_msg)
                try:
                    await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    pass  # Storage.clearCookies may not be supported

        except Exception as e:
            logger.error(
                "Error in clear_state_loop: %s: %s\n%s",
                type(e).__name__,
                e,
                traceback.format_exc(),
            )


# ── Server ───────────────────────────────────────────────────────────────


async def handle_connection(crw_ws: Any) -> None:
    """Handle a single CRW WebSocket connection."""
    peer = "?"
    try:
        peer = crw_ws.remote_address
    except Exception:
        pass
    logger.info("CRW connected from %s", peer)
    proxy = CdpProxy(crw_ws)
    try:
        await proxy.run()
    except Exception as e:
        logger.error(
            "Unhandled error in connection handler for %s: %s: %s\n%s",
            peer,
            type(e).__name__,
            e,
            traceback.format_exc(),
        )
    finally:
        logger.info("CRW connection from %s closed", peer)


async def main() -> None:
    logger.info(
        "CDP shim starting on %s:%d → %s",
        LISTEN_HOST,
        LISTEN_PORT,
        UPSTREAM_WS_URL,
    )
    logger.info(
        "Config: strip_stealth=%s, wait_until_search=%s, wait_until_web=%s, "
        "allow_http_urls=%s, strip_proxy=%s, clear_state_interval=%ds, trace=%s",
        STRIP_STEALTH_JS,
        WAIT_UNTIL_SEARCH or "(disabled)",
        WAIT_UNTIL_WEB or "(disabled)",
        ALLOW_HTTP_URLS,
        STRIP_PROXY_SERVER,
        CLEAR_STATE_INTERVAL_SECONDS,
        TRACE_CDP,
    )
    if TRACE_CDP:
        logger.info(
            "CDP trace query logging: include_values=%s, safe_keys=%s, max_url_chars=%d",
            TRACE_INCLUDE_QUERY_VALUES,
            ",".join(sorted(TRACE_SAFE_QUERY_KEYS)) or "(none)",
            TRACE_MAX_URL_CHARS,
        )

    # Start the periodic state clearing loop.
    clear_task = asyncio.create_task(clear_state_loop())

    # CRW connects to ws://<host>:<port>/devtools/browser — the /devtools/browser
    # path is part of the CDP protocol URL. In websockets 13.x, process_request
    # takes (connection, request) and returning None lets the handshake proceed.
    # We also set origins=None to disable Origin header checking.
    async def _accept_all_paths(connection, request):
        return None

    async with websockets.serve(
        handle_connection,
        LISTEN_HOST,
        LISTEN_PORT,
        max_size=50 * 1024 * 1024,
        ping_interval=20,
        ping_timeout=10,
        origins=None,
        process_request=_accept_all_paths,
    ):
        logger.info("CDP shim listening on ws://%s:%d", LISTEN_HOST, LISTEN_PORT)
        try:
            await asyncio.Future()  # run forever
        except asyncio.CancelledError:
            logger.info("Shutting down CDP shim...")
            clear_task.cancel()
            try:
                await clear_task
            except asyncio.CancelledError:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(
            "Fatal error: %s: %s\n%s",
            type(e).__name__,
            e,
            traceback.format_exc(),
        )
        raise
