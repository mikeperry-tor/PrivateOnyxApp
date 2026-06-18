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

Why pin ``renderer: chrome``
    crw's default ``auto`` mode tries a plain HTTP fetch first.  For
    anti-bot-protected SERPs (Google 429, Brave SvelteKit SPA) that either
    fails outright or returns a JS shell with no results.  Pinning the
    ``chrome`` renderer forces crw to drive obscura directly -- obscura is a
    Rust-native headless browser with built-in stealth (anti-fingerprint +
    tracker blocking), which is what actually gets results past the bot walls.
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

# obscura is comparatively slow (real headless browser).  Give crw enough wall
# clock to navigate + wait for the SERP to render before SearXNG's own engine
# timeout fires (engines set timeout: 30.0 in settings.yml).
CRW_WAIT_MS = 4000


def crw_scrape_request(
    params: "OnlineParams",
    target_url: str,
    *,
    wait_ms: int = CRW_WAIT_MS,
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
        # Force obscura.  auto mode picks plain HTTP for these SERPs and either
        # 429s (Google) or returns an empty SPA shell (Brave).
        "renderer": "chrome",
        "waitFor": wait_ms,
    }
    if extra_headers:
        payload["headers"] = extra_headers

    params["method"] = "POST"
    params["url"] = CRW_SCRAPE_URL
    params["data"] = json.dumps(payload)
    params["headers"] = headers


def extract_crw_html(resp: "SXNG_Response") -> str:
    """Pull the rendered HTML out of a crw ``/v1/scrape`` JSON response.

    Returns an empty string on any decode / shape error so the caller's
    XPath simply yields no results (SearXNG treats an empty result list as
    a soft failure, not an engine crash).
    """
    try:
        envelope = json.loads(resp.text)
    except (ValueError, TypeError):
        return ""
    if not isinstance(envelope, dict):
        return ""
    # crw returns success:false + an error field when the target blocks; in
    # that case data may still carry a short HTML stub, but it's useless for
    # parsing, so surface nothing and let SearXNG log the empty result.
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
