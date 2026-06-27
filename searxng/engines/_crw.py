# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared helper: route a SearXNG engine request through the local crw
(Firecrawl-compatible) scrape endpoint, which drives the obscura stealth CDP
browser.

This module is imported by the ``google2`` / ``brave2`` / ``duckduckgo2`` stub
engines.  It is NOT itself a SearXNG engine (no ``request``/``response`` here).

Architecture
------------

::

    searxng-core ──HTTP POST──> crw :3010 /v1/scrape ──CDP──> obscura :9222
                                                                  │
                                                                  ▼
                                                          target search engine

All three containers share one network namespace (``netns-holder``), so crw is
reachable at ``http://127.0.0.1:3010`` from inside searxng-core.

Why omit ``renderer`` and ``waitFor``
    The wrapper leaves CRW in render auto mode and lets the local
    prefetch-blocking proxy return 403 for search-engine prefetches. CRW then
    escalates to the configured chrome tier, which is obscura behind the CDP
    shim. Omitting ``waitFor`` avoids a blind fixed sleep; page readiness is
    handled by the shim's per-URL ``waitUntil`` injection plus CRW's own
    post-navigation heuristics.
"""

import json
import typing as t

if t.TYPE_CHECKING:
    from searx.extended_types import SXNG_Response
    from searx.search.processors import OnlineParams

# crw Firecrawl-compatible scrape endpoint.  Reachable on loopback because
# searxng-core and crw share the netns-holder network namespace.
CRW_SCRAPE_URL = "http://127.0.0.1:3010/v1/scrape"

import os

# crw accepts any non-empty bearer token when auth is not enforced (the wrapper
# deploy runs crw with the default self-host auth bypass).  Read the key from
# the CRW_ONYX_API_KEY env var (passed through to searxng-core in
# docker-compose.yaml, sourced from .env.wrapper) so a future auth-required
# crw flip keeps the engines working without a code change.  Falls back to the
# default self-host key when unset.
CRW_API_KEY = os.environ.get("CRW_ONYX_API_KEY") or "local-crw"

# Page load waiting is handled by the CDP shim (crw/cdp_shim.py), which
# injects `waitUntil: "networkidle2"` into every Page.navigate call so
# obscura adaptively waits for network silence before returning the nav
# response. This is event-driven: fast pages return in ~500ms, slow Tor/
# proxy pages get up to OBSCURA_NAV_TIMEOUT_MS (default 45s).
#
# No `waitFor` field is sent in the scrape payload. A fixed `waitFor` sleep
# is actively harmful: it wastes time on fast pages (always sleeps the full
# duration even when the page is ready) and is insufficient on slow pages
# (Tor/VPN loads can take 20-40s, far exceeding any reasonable fixed sleep).
# Instead, CRW uses its smart heuristics (SPA selector poll, content
# stability, challenge retry) for post-navigate work.
# See docs/request_handling.md §1.6.


def crw_scrape_request(
    params: "OnlineParams",
    target_url: str,
    *,
    extra_headers: t.Dict[str, str] | None = None,
) -> None:
    """Rewrite ``params`` in place so SearXNG's HTTP client POSTs the target
    URL to crw's ``/v1/scrape`` endpoint instead of fetching it directly.

    The crw response is a JSON envelope ``{success, data: {html, ...}}``.
    SearXNG hands the raw response body to the engine's ``response()``; we
    decode the JSON there and pull out ``data.html`` (the rendered DOM).
    """
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + CRW_API_KEY}
    if extra_headers:
        # Extra headers are passed *into the crw scrape payload* (forwarded to
        # the target by obscura), NOT added to the SearXNG->crw HTTP request.
        pass

    payload: t.Dict[str, t.Any] = {
        "url": target_url,
        # rawHtml = the post-render DOM (after JS).  "html" is readability-
        # narrowed; we want the full SERP DOM for XPath parsing.
        "formats": ["rawHtml"],
        "onlyMainContent": False,
        # No `renderer` pin — use auto mode. The compose default already
        # constrains CRW's JS ladder to chrome/obscura via
        # CRW_RENDERER__MODE=chrome. Leaving this request unpinned preserves
        # CRW's HTTP prefetch/PDF detection and lets the prefetch-blocking
        # proxy return 403 for search engine URLs, which auto mode treats as
        # the signal to escalate to the CDP renderer. See
        # docs/request_handling.md §1.7.
        #
        # No `waitFor` field — page load waiting is handled by the CDP shim's
        # waitUntil injection (OBSCURA_WAIT_UNTIL=networkidle2). CRW uses its
        # smart SPA selector poll + content stability heuristics for any
        # remaining post-navigate work instead of a blind fixed sleep.
    }
    if extra_headers:
        payload["headers"] = extra_headers

    params["method"] = "POST"
    params["url"] = CRW_SCRAPE_URL
    params["data"] = json.dumps(payload)
    params["headers"] = headers


def extract_crw_html(resp: "SXNG_Response") -> str:
    """Pull the rendered HTML out of a crw ``/v1/scrape`` JSON response.

    Returns the rendered HTML string on success. When crw reports that the
    target site blocked the request (``success: false`` with an anti-bot
    error), raises the appropriate SearXNG engine exception so the engine
    stats and suspension machinery work correctly:

      - HTTP 429 → :class:`SearxEngineTooManyRequestsException`
      - HTTP 403 → :class:`SearxEngineAccessDeniedException`
      - CAPTCHA  → :class:`SearxEngineCaptchaException`
      - Other    → :class:`SearxEngineResponseException`

    Without this, crw's HTTP 200 + ``success: false`` envelope would be
    treated by SearXNG as a successful search with zero results, hiding
    bot-blockage from the Engines tab stats and never suspending the engine.

    Returns an empty string on non-block decode/shape errors so the caller's
    XPath simply yields no results (SearXNG treats an empty result list as a
    soft failure, not an engine crash).
    """
    try:
        envelope = json.loads(resp.text)
    except (ValueError, TypeError):
        return ""
    if not isinstance(envelope, dict):
        return ""

    # crw returns success:false + error/error_code when the target blocks.
    # Raise the matching SearXNG exception so engine stats + suspension work.
    if envelope.get("success") is False:
        _raise_crw_block_exception(envelope)

    data = envelope.get("data")
    if not isinstance(data, dict):
        return ""
    # rawHtml is the full rendered DOM; fall back to html (readability) then
    # markdown just in case a future crw version renames the field.
    return (
        data.get("rawHtml")
        or data.get("html")
        or data.get("markdown")
        or ""
    )


def _raise_crw_block_exception(envelope: dict) -> None:
    """Raise the SearXNG exception matching crw's block error.

    crw's error field looks like:
      "Blocked by anti-bot (rate_limited): HTTP 429 Too Many Requests"
      "Blocked by anti-bot (generic_block): HTTP 403 with HTML content"
    The metadata.statusCode field carries the HTTP status from the target.
    """
    from searx.exceptions import (
        SearxEngineAccessDeniedException,
        SearxEngineCaptchaException,
        SearxEngineResponseException,
        SearxEngineTooManyRequestsException,
    )

    error_msg = envelope.get("error") or "crw scrape failed"
    error_code = envelope.get("error_code") or ""

    # Extract the target's HTTP status code from metadata or the error string.
    status_code = 0
    data = envelope.get("data")
    if isinstance(data, dict):
        metadata = data.get("metadata")
        if isinstance(metadata, dict):
            status_code = metadata.get("statusCode") or 0

    if not status_code:
        # Fall back to parsing the error string for an HTTP status code.
        import re
        m = re.search(r"HTTP (\d{3})", error_msg)
        if m:
            status_code = int(m.group(1))

    error_lower = error_msg.lower()

    # CAPTCHA detection (check before generic 403).
    if "captcha" in error_lower or error_code == "captcha":
        raise SearxEngineCaptchaException(message=f"crw: {error_msg}")

    if status_code == 429 or "429" in error_msg or "rate_limited" in error_lower:
        raise SearxEngineTooManyRequestsException(message=f"crw: {error_msg}")

    if status_code == 403 or "403" in error_msg or "access" in error_lower or "forbidden" in error_lower:
        raise SearxEngineAccessDeniedException(message=f"crw: {error_msg}")

    # Generic response error for any other failure.
    raise SearxEngineResponseException(f"crw: {error_msg}")
