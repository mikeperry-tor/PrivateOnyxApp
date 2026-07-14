# Request Handling: Search & open_url() Paths

## Onyx application egress boundary

Onyx application containers use only internal Compose networks and cannot
open direct Internet or host-gateway sockets. Generic public HTTP clients use
`onyx-public-egress-bridge`; supported configured chat inference and the local
embedding shim use `onyx-host-egress-bridge`, except that the exact internal
Teep chat base stays direct on `onyx-teep`. MCP/OAuth and configured Web
Connector clients choose between them from the saved Admin SSRF level for each
new client or crawl. Public and host policies have separate namespaces,
authenticated route brokers, and credentials; browser and executor policies
are separate.

The MCP runtime patch supplies an explicit `trust_env=False` HTTPX proxy
transport for initial URLs, redirects, discovery, registration,
authorization, token, refresh, SSE, and streamable HTTP traffic. The wrapper
does not add a second per-request destination validator inside HTTPX: the
selected request policy and broker enforce every initial and SDK-derived
destination consistently, and the broker resolves and pins the target. Exact
`host.docker.internal` is host-route-only. RFC1918 targets additionally require
`EGRESS_ALLOW_RFC1918=true`; named RFC1918 targets must end in `.local`,
`.internal`, or `.home.arpa`. Loopback, link-local, metadata, and other Docker
aliases remain denied. Other names go directly to a configured upstream proxy,
or use Myst provider DNS when no upstream is configured; only explicit no-VPN
mode uses system DNS for ordinary names.

The Web connector uses the same saved-level selection for requests and
Playwright fallback, including sitemap construction. The exact full-mode
internal origin `http://doc-drop-web:8091/` uses the host policy plus a fixed
gateway; it is not a crawl-wide direct exception. The former bundled Obscura
MCP server was removed intentionally; user-configured MCP servers use the
guarded paths above.

This document describes the full request chains for the two primary web-facing
tool paths in the Onyx agent: the **web search** tool (`web_search`) and the
**open URL** tool (`open_url`). In the recommended README configuration,
`web_search` uses SearXNG plus CRW, and `open_url` uses Onyx's Firecrawl
content provider pointed at the local CRW endpoint. Search-engine pages are
forced onto the Obscura browser path by the prefetch-blocking proxy. Ordinary
non-search `open_url` pages are different: CRW may satisfy them from its
initial HTTP prefetch when that response is usable, and only escalates to
CDP/Obscura when the HTTP response is blocked, thin, or JS-required. If the
Firecrawl content provider is not configured in Onyx Admin, upstream Onyx
falls back to its built-in `OnyxWebCrawler`; that fallback is documented
separately below.

Version scope for this document, based on clean local checkouts under
`reference_repos/`:

- Onyx: `v4.2.5` (`b7482a59fb74`, 2026-07-09)
- CRW: `v0.23.0` (`dc497fdf35a6`, 2026-07-10)
- Obscura: `v0.1.10` (`50e66320b084`)
- SearXNG: master commit `f8ffbf36f903`

SearXNG does not publish Git release tags. It is a rolling release from
`master`; the Docker image tag is computed from the Git commit date and short
hash in `searx/version.py` as `YYYY.M.D-<short-sha>`. For the checkout above,
`git show -s --date='format:%Y.%m.%d' --format='%cd+%h'` returns
`2026.06.26+f8ffbf36f`, so the corresponding Docker tag is
`2026.6.26-f8ffbf36f`. SearXNG's container workflow publishes both that tag
and `latest` to Docker Hub and GHCR after the master-branch integration
workflow succeeds. To pin a SearXNG release, prefer the computed tag
(`docker.io/searxng/searxng:2026.6.26-f8ffbf36f`) or, for exact immutability,
the image digest. To map an image back to source, inspect the OCI label
`org.opencontainers.image.revision`; SearXNG sets it to the full Git commit SHA
when building `container/dist.dockerfile`.

This document focuses on request chains and browser/scraper behavior. For the
Compose-level VPN namespace, optional `EGRESS_UPSTREAM_PROXY_URL`, teep, Tailscale, and
code-interpreter routing switches, see
[VPN routing and proxies](vpn_routing_and_proxies.md). For the Onyx runtime
patches that shape tool availability, prompt text, and
executor pod networking, see [Onyx patch information](onyx_patch_info.md). For
line-oriented upgrade checks, including the SearXNG custom engines and config
overlay notes, see [Onyx wrapper patches](onyx_patches_upgrade.md). For the
tested internal-network reachability of CRW, Obscura, the prefetch proxy, and
network-enabled code-interpreter executors, see
[Internal network security](internal_network_security.md).

Unless a section says otherwise, diagrams assume `MYST_VPN_ENABLED=true`.
When `MYST_VPN_ENABLED=false`, restricted networks stay unchanged while policy
proxy traffic leaves the trusted namespace through Docker. When
`EGRESS_UPSTREAM_PROXY_URL` is set, final-hop policies connect through it;
default SearXNG still has no general internet route. See
[VPN routing and proxies](vpn_routing_and_proxies.md#final-hop-routing-matrix).

## Path 1: Web Search (`web_search` tool)

### Request chain diagram

```
┌──────────────────────┐
│  Onyx Agent (LLM)    │
│  web_search tool     │
│  (parallel queries)  │
└─────────┬────────────┘
          │ POST /search (per query, in parallel)
          ▼
┌──────────────────────────────────────────────────────┐
│  SearXNG :8888                                        │
│  ├─ Fans out to all enabled engines in parallel       │
│  ├─ google2, brave2, duckduckgo2, startpage2, bing2   │
│  │  (custom engines in searxng/engines/)              │
│  ├─ Engine suspension on 429 (180s) / 403 (180s)      │
│  │  / CAPTCHA (3600s)                                 │
│  └─ Aggregates + deduplicates results → JSON          │
└─────────┬────────────────────────────────────────────┘
          │ POST /v1/scrape (per engine, in parallel)
          │ {url: <search-engine-url>, formats: ["rawHtml"],
          │  onlyMainContent: false}
          ▼
┌──────────────────────────────────────────────────────┐
│  CRW :3010                                            │
│  ├─ Per-host rate limiter (0.33 RPS = 3s interval,   │
│  │  max_concurrent=1 per eTLD+1)                     │
│  ├─ HTTP prefetch → prefetch-blocking-proxy :3128    │
│  │  (returns 403 for search engines → escalate CDP)  │
│  ├─ CDP client → ws://cdp-shim:9224                   │
│  └─ Returns {success, data: {rawHtml}} envelope       │
└─────────┬────────────────────────────────────────────┘
          │ CDP WebSocket (Target.createTarget,
          │ Page.navigate, Page.addScriptToEvaluate, etc.)
          ▼
┌──────────────────────────────────────────────────────┐
│  CDP Shim :9224 (crw/cdp_shim.py)                    │
│  ├─ Strips STEALTH_JS from addScriptToEvaluateOnNew  │
│  ├─ Injects waitUntil into Page.navigate             │
│  │  (load for SERPs, domcontentloaded for web pages) │
│  ├─ Strips proxyServer from createBrowserContext     │
│  ├─ Periodic cookie clearing (every 60 min)          │
│  ├─ Suppresses non-fatal CDP error logs              │
│  └─ Proxies to → ws://obscura:9222                    │
└─────────┬────────────────────────────────────────────┘
          │ CDP WebSocket (transparent proxy)
          ▼
┌──────────────────────────────────────────────────────┐
│  Obscura :9222 (--stealth --storage-dir)             │
│  ├─ Browser-consistent TLS/HTTP fingerprinting        │
│  ├─ Tracker blocking                                 │
│  ├─ JS fingerprint spoofing (bootstrap.js V8 snapshot)│
│  ├─ Cookie jar on the default CDP context             │
│  └─ Renders SERP → returns HTML                      │
└─────────┬────────────────────────────────────────────┘
          │ explicit proxy → browser bridge → final-hop policy
          ▼
┌──────────────────────────────────────────────────────┐
│  Target search engine                                │
│  (www.google.com, search.brave.com,                  │
│   html.duckduckgo.com, www.startpage.com,            │
│   www.bing.com)                                      │
└──────────────────────────────────────────────────────┘
```

**Key characteristics of the search path:**
- Involves the SearXNG container as an intermediary aggregation layer
- By default, SearXNG rotates among one non-last-resort CRW-backed web engine
  per query (`google2`, `brave2`, `duckduckgo2`, `startpage2`) and uses
  last-resort engines such as `bing2` only when the normal providers are
  already suspended or unavailable. Set `SEARXNG_ROUND_ROBIN=false` to restore
  SearXNG's normal parallel fan-out to all selected engines.
- CRW's per-host rate limiter serializes requests to the same search engine
- The CDP shim strips CRW's conflicting STEALTH_JS, injects `waitUntil` into
  `Page.navigate` for adaptive waiting, strips `proxyServer` only as a safety
  net, and periodically clears cookies when the default CDP context is used
- Obscura `--storage-dir` persists cookies for obscura's own CLI/MCP contexts;
  CRW-driven `/v1/scrape` requests normally use the in-process default CDP
  context and do not rely on storage-dir persistence
- Results are parsed from rendered HTML via XPath in each engine's `response()`
  function
- 429s from search engines trigger SearXNG engine suspension (180s)

---

### 1.1 Onyx → SearXNG

When the LLM calls the `web_search` tool, Onyx's
[`WebSearchTool.run()`](../reference_repos/onyx/backend/onyx/tools/tool_implementations/web_search/web_search_tool.py:234)
executes all queries in parallel via `run_functions_tuples_in_parallel`. Each
query is sent to the configured SearXNG instance as a separate HTTP POST to
`/search`:

```
POST http://<searxng-host>:8888/search
Content-Type: application/x-www-form-urlencoded

q=<query>&format=json
```

The [`SearXNGClient.search()`](../reference_repos/onyx/backend/onyx/tools/tool_implementations/web_search/clients/searxng_client.py:22)
method has a `@retry_builder(tries=3, delay=1, backoff=2)` decorator — retries
on failure with exponential backoff (1s, 2s, 4s).

### 1.2 SearXNG → CRW (custom engines)

SearXNG normally fans out a query to all selected engines in parallel. This
wrapper defaults `SEARXNG_ROUND_ROBIN=true`, so the startup patch in
[`searxng/patches/sitecustomize.py`](../searxng/patches/sitecustomize.py)
reduces each request to one available CRW-backed web provider before SearXNG
launches engine threads. When that provider pool is present, other selected
SearXNG engines from the default category are dropped for that request so only
the chosen provider runs. The five custom engines (`google2`, `brave2`,
`duckduckgo2`, `startpage2`, `bing2`) are defined in
[`searxng/engines/`](../searxng/engines/). Each scheduled engine's `request()`
function rewrites the SearXNG HTTP params to POST to CRW's `/v1/scrape`
endpoint instead of fetching the search engine directly:

```python
# searxng/engines/_crw.py:crw_scrape_request()
headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer " + CRW_API_KEY,  # Makefile-generated placeholder
}
payload = {
    "url": target_url,           # e.g. https://www.google.com/search?q=...&udm=14
    "formats": ["rawHtml"],      # full rendered DOM
    "onlyMainContent": False,    # keep full SERP (not readability-narrowed)
    # No renderer pin — use CRW auto render decision. The compose default
    # sets CRW_RENDERER__MODE=chrome so chrome/obscura is the only JS tier,
    # while RENDER_JS_DEFAULT remains unset so a 403/error from the
    # prefetch-blocking proxy escalates to CDP instead of failing.
    # No waitFor field — page load waiting is handled by the CDP
    # shim's waitUntil injection (load for SERPs, domcontentloaded for
    # web pages by default). CRW uses its smart SPA selector poll + content stability
    # heuristics for any remaining post-navigate work.
}
params["method"] = "POST"
params["url"] = os.environ["CRW_SCRAPE_URL"]
params["data"] = json.dumps(payload)
params["headers"] = headers
```

The engine's `response()` function decodes the CRW JSON envelope and parses
the rendered HTML with XPath to extract search results. If CRW reports a
block (429/403/CAPTCHA), the appropriate SearXNG exception is raised so
SearXNG's engine suspension machinery kicks in. The custom engines also treat
zero parseable organic results as provider unavailability: they log a
privacy-preserving diagnostic and raise `SearxEngineAccessDeniedException`
rather than returning an empty successful result set. In round-robin mode this
lets SearXNG try another provider instead of handing Onyx an empty search
response because one live SERP was a soft-block shell or parser miss.

#### Custom engine parser assumptions

These engines intentionally scrape live, rendered search-result pages rather
than official APIs. Their request path is stable inside the wrapper, but their
parsers depend on target SERP DOM shapes that can change without a SearXNG
upgrade. During upgrades or search-quality debugging, test each custom engine
with a real query and inspect the returned `rawHtml` if result counts fall to
zero.

| Engine | Target URL shape | DOM assumption to verify |
|--------|------------------|--------------------------|
| `google2` | `https://www.google.com/search?q=...&udm=14` | Google Web-only SERPs are requested with `udm=14` to avoid AI Overview rendering; organic result title links remain anchors containing an `h3`; snippets are looked up from the closest result card, commonly `div.g`, `MjjYud`, `N54PNb`, `yuRUbf`, or `data-ved` ancestors, with text under `VwiC3b` / `IsZvec` / `kb0PBd` / `ITZIwc` / `data-sncf` nodes. |
| `brave2` | `https://search.brave.com/search?q=...` | Organic result cards have `data-type="web"`; title links carry class `l1`; title/snippet text remains under `title` and `snippet-description` classes. |
| `duckduckgo2` | `https://html.duckduckgo.com/html/?q=...` | HTML endpoint result cards include both `result` and `web-result`; title links use `result__a`; snippets use `result__snippet`; `/l/?uddg=...` redirects carry the real URL. |
| `startpage2` | `https://www.startpage.com/sp/search?query=...&cat=web` | Post-hydration organic cards carry a `result` class but not `a-bg-result`; title links prefer `data-testid="gl-title-link"` or `result-title`; captcha pages still expose title/meta/form markers caught by `captcha_xpath`. |
| `bing2` | `https://www.bing.com/search?q=...` | Organic results are `li.b_algo` cards under `ol#b_results`; title links are `h2 > a`; snippets prefer `b_caption` paragraphs; `/ck/a?u=a1...` redirects carry a base64url-encoded real URL; dictionary/answer widgets that masquerade as `b_algo` cards are skipped before result extraction; captcha/Turing pages are detected before parsing. |

`bing2` is intentionally enabled as a **last-resort** engine. Bing results can
be broad and noisy, so the wrapper mounts
[`searxng/patches/sitecustomize.py`](../searxng/patches/sitecustomize.py) to
change SearXNG scoring for engines configured with `last_resort: true`.
The stock SearXNG algorithm multiplies the weights of all engines that matched
a merged URL; with a low Bing weight, a result found by both Bing and another
engine would be penalized. The wrapper patch tracks result positions by engine:

- results found by normal engines keep normal SearXNG scoring;
- results found only by a last-resort engine are sorted after all normal-engine
  results and scored with `last_resort_fallback_weight`;
- results found by normal engines and a last-resort engine keep the normal
  score and receive `last_resort_confirmation_bonus` as a small multiplier.

The current `bing2` values in `searxng/core-config/settings.yml` are
`last_resort_fallback_weight: 0.05` and
`last_resort_confirmation_bonus: 0.15`.

When `SEARXNG_ROUND_ROBIN=true`, the same patch file also schedules one
CRW-backed provider per query. It classifies engines marked `last_resort: true`
in the same pass as the round-robin selection: normal providers rotate first,
and `bing2` is eligible only when all selected normal providers are already
suspended or unavailable. It also drops other selected SearXNG engines whenever
the configured provider pool is present, so ordinary Onyx searches receive one
provider's results. If the selected provider returns no main results and
records itself as unresponsive, the patch retries the same SearXNG request with
another untried provider, reaching `bing2` only after the normal provider tier
has failed or is unavailable. The custom engines are expected to raise when
their parser finds zero organic rows, so a live provider should not silently
produce an empty SearXNG result unless all eligible providers have failed or the
engine contract has drifted. In that mode the scoring patch is mostly inert
when one provider succeeds, but it remains active for `SEARXNG_ROUND_ROBIN=false`.

The upgrade inventory in
[SearXNG companion stack](onyx_patches_upgrade.md#searxng-companion-stack)
lists the corresponding file-level checks and config-overlay findings.

### 1.3 CRW → CDP Shim → Obscura

CRW receives the `/v1/scrape` request and runs its normal `FallbackRenderer`
pipeline. The SearXNG engines do **not** pin a per-request renderer. Instead,
the compose default sets `CRW_RENDERER__MODE=chrome`, so chrome/obscura is the
only JS renderer in the ladder, and leaves `CRW_RENDERER__RENDER_JS_DEFAULT`
unset, so CRW stays in auto mode. In auto mode the HTTP prefetch runs first;
the local prefetch-blocking proxy returns 403 for search-engine URLs without
contacting the search engine, and CRW treats that blocked HTTP result as a
signal to escalate to the CDP renderer. The flow is:

1. **Per-host rate limiting**: CRW's
   [`host_limiter`](../reference_repos/crw/crates/crw-renderer/src/host_limiter.rs:83)
   acquires a semaphore (max_concurrent=1 per eTLD+1) and computes a sleep
   interval (1/0.33 RPS = ~3s). The semaphore is held for the entire fetch
   duration, so a second query to the same engine waits.

2. **CDP connection**: CRW opens a WebSocket to
   `ws://cdp-shim:9224/devtools/browser` on the dedicated CDP network.

3. **Target creation**: CRW calls `Target.createTarget` with the search engine
   URL. Obscura creates a new page on its shared default browser context.

4. **Stealth JS injection**: CRW calls `Page.addScriptToEvaluateOnNewDocument`
   with its `STEALTH_JS` constant. The shim detects the marker
   (`"Hide navigator.webdriver"`) and replaces the source with `";"` (no-op),
   letting obscura's own `--stealth` mode handle fingerprinting.

5. **Navigation + render**: The shim injects `waitUntil: "load"`
   into the `Page.navigate` call for search engine URLs (SERPs), or
   `waitUntil: "domcontentloaded"` for other URLs that reach CDP. Obscura navigates to the
   URL and adaptively waits for the specified lifecycle event before returning
   the nav response, bounded by `OBSCURA_NAV_TIMEOUT_MS` (default 45s). The
   wrapper example uses `load` for JS-heavy SERPs and `domcontentloaded` for
   ordinary pages to avoid waiting on unnecessary subresources or long-polling
   connections. Operators can select the stricter network-idle modes when a
   provider needs them. No `waitFor` fixed sleep is used — see §1.6. Obscura
   renders the page with its stealth browser (browser-consistent TLS/HTTP
   fingerprinting, tracker blocking, and JS fingerprint spoofing via
   bootstrap.js) and returns the rendered HTML.

6. **Target close + disconnect**: CRW calls `Target.closeTarget` and closes
   the WebSocket. In the normal wrapper path CRW does not set
   `REQUEST_PROXY`, so it creates targets on obscura's default context. Cookies
   can therefore remain in the in-process default cookie jar until the shim's
   periodic clearing runs or obscura restarts. The compose default
   intentionally avoids CRW's `REQUEST_PROXY` path because that path uses
   per-request `Target.createBrowserContext`; Obscura clears the default cookie
   jar on context create/dispose.

### 1.4 429 / Rate Limit Handling

There are multiple layers of 429 handling:

| Layer | Mechanism | Config |
|-------|-----------|--------|
| **CRW per-host limiter** | 3s interval between requests to same eTLD+1, max 1 concurrent | `CRW_CRAWLER__REQUESTS_PER_SECOND=0.33` |
| **SearXNG engine suspension** | On 429, engine suspended for 180s | `settings.yml: SearxEngineTooManyRequests: 180` |
| **SearXNG ban time** | Base ban 5s, max 120s | `settings.yml: ban_time_on_fail: 5, max_ban_time_on_fail: 120` |
| **Onyx SearXNGClient retry** | 3 retries with exponential backoff (1s, 2s, 4s) | `@retry_builder(tries=3, delay=1, backoff=2)` |
| **CDP shim cookie clearing** | Periodic cookie clearing to limit tracking surface | `OBSCURA_BROWSER_CLEAR_COOKIES_INTERVAL=3600` |

**429 causes in this stack:**

1. **Search-provider fan-out**: With the default `SEARXNG_ROUND_ROBIN=true`,
   each individual SearXNG query uses one CRW-backed search provider, but the
   agent can still issue multiple independent queries simultaneously. Setting
   `SEARXNG_ROUND_ROBIN=false` restores SearXNG's parallel fan-out to all
   selected engines. The per-host rate limiter serializes same-host requests,
   but a search engine may still see bursts from the same VPN exit IP.
2. **IP reputation**: VPN/Tor exit IPs are often flagged by anti-bot systems
   regardless of request pattern.
3. **Residual fingerprint drift**: obscura's TLS and browser-level
   fingerprinting is much better than bare HTTP, but some JS-visible values
   are re-seeded during page initialization while cookies can persist
   for a while (see Known Limitations).

The compose default routes CRW's HTTP prefetch through the prefetch-blocking
proxy, so search-engine prefetches receive a local 403 and never reach the
upstream search engine. The search engine sees only the obscura browser
navigation rather than a bare reqwest prefetch followed by a browser visit.
This search-specific guarantee does not apply to arbitrary non-search
`open_url` targets; see [Known Limitations](#known-limitations).

When a search engine returns 429:
1. Obscura renders the 429 page (or the anti-bot challenge page).
2. CRW returns `success: false` with an error like `"Blocked by anti-bot
   (rate_limited): HTTP 429 Too Many Requests"`.
3. The SearXNG engine's [`_crw.extract_crw_html()`](../searxng/engines/_crw.py:98)
   raises `SearxEngineTooManyRequestsException`.
4. SearXNG suspends the engine for 180 seconds.
5. Onyx's `SearXNGClient` retries up to 3 times (but the engine is suspended,
   so SearXNG returns no results from that engine).
6. Onyx's `WebSearchTool` logs partial failures and continues with results
   from other engines/queries that succeeded.

**Observed Google `udm=14` / Obscura behavior (2026-06-30):**

While adding `udm=14` to `google2`, two different Google responses were
observed from the same host:

- A direct host `curl` request to
  `https://www.google.com/search?q=...&hl=en&udm=14` returned Google's
  JavaScript retry shell (`/httpservice/retry/...`) rather than result DOM.
- A direct CDP navigation through Obscura to the same `udm=14` URL did not
  traverse that JS retry shell. The main document response was immediately
  `HTTP 429 text/html`, the frame navigated to a Google `/sorry/index?...`
  page, and the page then loaded `https://www.google.com/recaptcha/enterprise.js`
  with HTTP 200. The final DOM had no result markers (`<h3>`, `MjjYud`,
  `N54PNb`, `yuRUbf`, `VwiC3b`, `IsZvec`, `kb0PBd`, `ITZIwc`, or
  `data-sncf`) and did contain Google's unusual-traffic / CAPTCHA text.

This means a Google failure can be a server-side anti-bot / sorry response
specific to the Obscura browser fingerprint and exit IP, even when a bare HTTP
client sees a retry shell. Do not treat an empty Google result set after a 429
as a parser failure until the rendered DOM has been inspected. Use the
sanitized CDP trace mode described in
[CDP trace diagnostics](#cdp-trace-diagnostics) to distinguish:

- immediate 429/sorry navigation;
- JavaScript retry shell followed by a different final URL;
- successful `udm=14` SERP DOM whose result selectors need updating.

### 1.5 Result Processing

SearXNG aggregates results from the engines scheduled for the request,
deduplicates, and returns JSON to Onyx. With the default
`SEARXNG_ROUND_ROBIN=true`, the wrapper's SearXNG startup patch schedules one
available CRW-backed search provider per query and selects `bing2` only when
the normal providers are already suspended or unavailable; other default
SearXNG engines are dropped when that provider pool is selected. If a scheduled
provider fails and records itself as unresponsive, SearXNG retries the same
query with another eligible provider before returning the final JSON. Onyx's
`WebSearchTool` separately interweaves results from multiple query strings in
round-robin fashion, converts to `InferenceSection`s, and returns them to the
LLM as cited search results.

### 1.6 Page Load Wait Strategy

The stack uses an **event-driven wait** instead of a fixed delay to determine
when a page is "done loading" and ready for content extraction. This is
critical when `EGRESS_UPSTREAM_PROXY_URL` routes through Tor or a slow VPN, where page loads
can take up to 60 seconds — a fixed 5s sleep would either cut off slow pages
or waste time on fast ones.

**How it works:**

1. CRW sends `Page.navigate {url}` over CDP to the shim.
2. The shim injects `waitUntil` into the navigate params based on the URL:
   - **Search engine URLs** (SERPs): `load` by default, so scripts and ordinary
     subresources complete without waiting for full network idleness
   - **Other URLs that reach CDP** (`open_url` web pages): `domcontentloaded`
     by default, so parsing and synchronous scripts finish without waiting for
     unnecessary subresources or long-polling connections
3. Obscura navigates to the URL and **adaptively waits** for the specified
   lifecycle event before returning the nav response, bounded by
   `OBSCURA_NAV_TIMEOUT_MS` (default 45s). If an operator selects
   `networkidle2`, its quiet period is 500ms with a 5-second deadline separate
   from the navigation timeout.
4. CRW receives the nav response. No `waitFor` field is sent in the scrape
   payload, so CRW skips any blind fixed sleep and uses its smart heuristics
   (SPA selector poll, content stability, challenge retry) for any remaining
   post-navigate work, bounded by `CRW_RENDERER__CHROME_NAV_BUDGET_MS`
   (default 48s).
5. CRW extracts the rendered DOM and returns it.

**Obscura lifecycle event firing order** (each later event includes the earlier
conditions):

```
1. domcontentloaded  — DOM parsed + scripts executed
2. load              — all subresources finished, window.onload fired
3. networkidle2      — ≤2 active network requests for 500ms
4. networkidle0      — 0 active network requests for 500ms
```

Obscura returns at the first event matching `waitUntil`. The network-idle
events have a 5-second deadline (separate from `OBSCURA_NAV_TIMEOUT_MS`).

**Timeout hierarchy (outer → inner):**

| Layer | Timeout | Config |
|-------|---------|--------|
| SearXNG engine timeout | 60s | `settings.yml: timeout: 60.0` (SearXNG's HTTP client timeout for POST to CRW) |
| CRW effective deadline | 300s | `max(deadline_ms_default=300000, ladder_min=~138s)` = 300s. See note below. |
| CRW nav budget | 48s | `CRW_RENDERER__CHROME_NAV_BUDGET_MS=48000` — inner race for post-navigate work |
| Obscura nav timeout | 45s | `OBSCURA_NAV_TIMEOUT_MS=45000` — hard ceiling on `Page.navigate` |
| Obscura CDP command timeout | 90s | `OBSCURA_CDP_COMMAND_TIMEOUT_MS=90000` — per-CDP-command deadline |
| Obscura fetch timeout | 45s | `OBSCURA_FETCH_TIMEOUT_MS=45000` — scripted fetch/XHR timeout |

The hierarchy is layered so each inner timeout is ≤ the outer one: obscura's
nav timeout (45s) < CRW's nav budget (48s) < CRW's chrome timeout (50s) <
CRW's effective deadline (300s). This ensures obscura's adaptive wait gets
a chance to complete before CRW's nav budget fires. The CRW nav budget
(`chrome_nav_budget_ms`) is the inner race around CRW's post-navigation phase
(SPA selector poll, content stability, challenge retry, and DOM extraction).
CRW's `wait_for_page_ready` waits for `Page.loadEventFired` before that race.
The wrapper uses a 48000ms budget so Tor/slow VPN paths have enough time for
obscura navigation and CRW post-navigation work.

**Note on CRW's effective deadline:** CRW computes its own per-request
deadline via `effective_deadline_ms()`, which auto-extends to
`max(deadline_ms_default, ladder_min)`. With chrome-only mode,
`chrome_timeout_ms=50000`, `http_timeout_ms=60000`, and
`deadline_ms_default=300000`:
- `ladder_min = http_timeout(60000) + chrome_timeout(50000) +
  1*CDP_TIER_OVERHEAD(28000) = 138000ms`
- `effective_deadline = max(300000, 138000) = 300000ms` (5 min)

This is the total budget for a single scrape request, including rate-limit
sleep + HTTP prefetch + CDP navigation + render. It is independent of
SearXNG's 60s engine timeout (which is SearXNG's own HTTP client timeout for
the POST to CRW).

The 300s deadline accommodates multi-request scenarios where the agent
issues many parallel `open_url` or search queries. With
`PER_HOST_MAX_CONCURRENT=1`, requests to the same eTLD+1 are serialized —
the 300s deadline gives queued requests enough budget to complete after
waiting behind earlier requests, while serializing per-host to avoid
429s. No happy-path impact: fast pages complete in ~1-5s regardless of the
deadline. The Tower outer timeout auto-widens to cover this.

**HTTP prefetch and the blocking proxy:** CRW's `FallbackRenderer` always runs
an HTTP prefetch before the CDP renderer when JS rendering may be needed. That
prefetch is a normal reqwest fetch, not an obscura browser navigation. CRW uses
it to detect content type before spending a browser render.

The compose default routes CRW's reqwest traffic through
`HTTPS_PROXY=http://crw-prefetch-bridge:3128` and the equivalent
`HTTP_PROXY`. The final-hop prefetch policy rejects
search-engine prefetches with `403 Forbidden` without opening an upstream
connection. CRW auto mode sees that blocked HTTP result and escalates to the
chrome/obscura CDP renderer. Non-search HTTPS requests are forwarded through
the configured egress path and can be returned directly by CRW when the
response is usable. Plain HTTP URLs are blocked by default with a clear
message telling the caller to use HTTPS; set `EGRESS_ALLOW_HTTP_URLS=true`
only when cleartext HTTP fetches are intentionally needed. See §1.7 for the
proxy details.

**Approaches that do NOT work:**

- **`CRW_DOCUMENT__ENABLED=false`**: Does not disable the prefetch. The
  `document.enabled` flag only controls whether CRW's PDF parser runs, not
  whether the HTTP prefetch happens. The prefetch code at `lib.rs:801-810`
  unconditionally runs the HTTP GET and checks `content_type` regardless of
  `document.enabled`.
- **`CRW_RENDERER__MODE=none`**: Disables ALL JS rendering — returns early
  with empty `js_renderers`, so no CDP/obscura path exists. Not viable.
- **Blocking CRW's HTTP access while forcing render-js default true**: In
  `RENDER_JS_DEFAULT=true` mode (`Some(true)` branch), if the HTTP prefetch
  fails, CRW does **not** fall back to CDP — the `?` operator propagates the
  error and the request fails. This stack intentionally leaves
  `CRW_RENDERER__RENDER_JS_DEFAULT` unset so the auto-mode branch can escalate
  to CDP on 403/error.
- **Letting obscura handle PDFs**: Obscura has no PDF support — it's a
  headless browser with no PDF viewer or text extraction. If CRW's prefetch
  is blocked, PDFs would go through obscura and return garbage.

The `http_timeout_ms=60000` is the ceiling for this prefetch. On the happy
path it completes in 1-3s; the 60s value only matters if the prefetch hangs.
It also contributes to `ladder_min` (see formula above).

### 1.6.1 Rate Limiter Interaction with Deadlines

CRW's per-host rate limiter (`CRW_CRAWLER__REQUESTS_PER_SECOND=0.33`,
`CRW_CRAWLER__PER_HOST_MAX_CONCURRENT=1`) serializes requests to the same
eTLD+1. The semaphore is held for the **entire fetch duration** (navigation
+ render), and the rate-limit sleep is deducted from the request's deadline
budget before navigation begins.

This creates a **deadline compounding** problem when multiple CRW requests to
the same host are queued. With the default `SEARXNG_ROUND_ROBIN=true`, that is
most likely when Onyx issues multiple independent search query strings and the
round-robin scheduler selects the same provider for more than one of them. With
`SEARXNG_ROUND_ROBIN=false`, SearXNG's parallel engine fan-out can create the
same queue inside a single logical search.

**Scenario: 3 search queries → 3 Google requests**

For Google (3 requests to `google.com`, semaphore=1, 3s interval, ~45s
nav per request on Tor):

| Req | Semaphore acquired | Rate-limit sleep | Nav starts | Deadline remaining | Nav budget | Outcome |
|-----|--------------------|------------------|------------|--------------------|------------|---------|
| 1   | T=0s               | 0s               | T=0s       | ~300s              | 48s        | ✓ completes at ~45s |
| 2   | T≈45s              | 3s               | T≈48s      | ~252s              | 48s        | ✓ completes at ~93s |
| 3   | T≈93s              | 3s               | T≈96s      | ~204s              | 48s        | ✓ completes at ~141s |
| 4   | T≈141s             | 3s               | T≈144s     | ~156s              | 48s        | ✓ completes at ~189s |
| 5   | T≈189s             | 3s               | T≈192s     | ~108s              | 48s        | ✓ completes at ~237s |
| 6   | T≈237s             | 3s               | T≈240s     | ~60s               | 48s        | ✓ completes at ~285s |

With `deadline_ms_default=300000` (5 min effective deadline), up to 6
requests per engine can complete sequentially within the deadline. The 300s
deadline accommodates multi-request scenarios (e.g., agent issuing 5+
parallel search queries, or crawling many URLs from the same GitHub
repository) while serializing per-host to avoid 429s.

**Mitigation:** `CRW_REQUEST__DEADLINE_MS_DEFAULT=300000` (5 min) sets the
effective per-request deadline to 300s, accommodating multi-request
scenarios where the agent issues many parallel `open_url` or search queries.
This gives queued requests in the per-host rate limiter enough deadline
budget to complete after waiting behind earlier requests (see the table
above — up to 6 requests per engine can complete sequentially). No
happy-path impact: fast pages complete in ~1-5s regardless of the
deadline. The Tower outer timeout auto-widens to cover this.

These settings (`PER_HOST_MAX_CONCURRENT=1`,
`REQUESTS_PER_SECOND=0.33`) are tuned for anti-bot stealth, not throughput.
Increasing `PER_HOST_MAX_CONCURRENT` would allow parallel requests but
risks triggering 429s from the search engines. The per-host rate limiter keeps
same-engine requests serialized.

**Why no `waitFor` sleep?** A fixed `waitFor` sleep (CRW's alternative
mechanism) is actively harmful in this stack:

- **Wastes time on fast pages**: always sleeps the full duration even when
  the page is ready in <1s. With 4 search engines fanning out in parallel,
  a 5s sleep per engine adds 5s of latency to every search.
- **Insufficient on slow pages**: Tor/VPN page loads can take 20-40s. No
  reasonable fixed sleep covers this range — a 5s sleep returns before the
  page has rendered, and a 40s sleep penalizes every fast page.
- **Double-waits with `waitUntil`**: if both `waitUntil=networkidle2` and
  `waitFor=5000` are active, obscura waits for network silence, then CRW
  sleeps another 5s on top — pure waste.

Instead, the `waitUntil` injection makes obscura return as soon as network
activity ceases (event-driven), and CRW's smart heuristics handle any
remaining post-navigate work (SPA selector poll, content stability,
challenge retry) without a blind sleep.

**Disabling `waitUntil` injection:** Set both `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH`
and `OBSCURA_BROWSER_WAIT_UNTIL_WEB` to empty to disable the shim's injection.
Obscura will default to `DomContentLoaded` and return the nav response as
soon as the HTML is parsed, without waiting for network silence. CRW's smart
heuristics still run in the post-navigate phase. This is not recommended —
it will cause JS-heavy SERPs (Brave's SvelteKit SPA) to return empty shells.

### 1.6.2 Obscura CDP Compatibility

Obscura's CDP surface matches the CRW `v0.23.0` renderer calls used by this
stack. CRW sends exact `Page.navigate` commands, uses
`Fetch.continueRequest` while resource interception is enabled, and reads
selected bodies with `Network.getResponseBody`. Obscura supports those calls,
including request overrides supplied to `Fetch.continueRequest` and the real
MIME/body metadata for a directly navigated main resource.

Obscura also implements the opt-in `Fetch.takeResponseBodyAsStream`,
`IO.read`, and `IO.close` calls. CRW does not call those methods, so their
buffer limits do not affect the wrapper's scrape, search, or PDF-prefetch
paths. CRW continues to detect and parse PDFs in its HTTP prefetch tier.

These upstream CDP capabilities do not replace the local shim. The shim still
provides stack policy that neither CRW nor Obscura owns: per-host `waitUntil`
selection, rejection of cleartext HTTP browser navigation, removal of CRW's
conflicting stealth script, removal of per-context `proxyServer`, and periodic
cookie clearing. They also do not replace the prefetch-blocking proxy, because
Obscura only runs after CRW's separate HTTP prefetch has been accepted or
rejected.

### 1.7 Prefetch-Blocking Proxy

The prefetch-blocking proxy ([`crw/prefetch_blocking_proxy.py`](../crw/prefetch_blocking_proxy.py:1))
eliminates the search-engine HTTP prefetch double-hit by intercepting CRW's
HTTP prefetch requests. For configured search-engine hosts it returns
`403 Forbidden` locally, forcing CRW auto mode to escalate to the CDP renderer
(obscura) without contacting the search engine outside the browser path.

**Architecture:**

```
CRW :3010 ──HTTP proxy──> crw-prefetch-bridge :3128
                               │
                               v
                         isolated prefetch policy
                               │
                               ├─ Search engine URL → 403 (no network request)
                               ├─ Internal/private target → 403 (logged)
                               ├─ Other HTTP URL → 403 unless explicitly allowed
                               ├─ Other HTTPS URL → CONNECT prefetch tunnel
                               │
                               └── upstream ──> EGRESS_UPSTREAM_PROXY_URL (Tor/VPN)
                                                (if set)
```

**How it works:**

1. CRW is configured with
   `HTTPS_PROXY=http://crw-prefetch-bridge:3128` and the equivalent
   `HTTP_PROXY`. CRW has no direct internet route. Its CDP and SearXNG peers
   are explicit `NO_PROXY` names on separate internal networks.
2. Before proxy use, CRW 0.23 requires a local DNS result for URL-safety
   prevalidation. Docker resolves known internal services to their real
   private addresses, which CRW rejects. Other public multi-label names are
   forwarded only to `crw-validation-dns` on CRW's loopback; it returns a
   fixed global A record without forwarding the query. This answer is not used
   for the connection. The final-hop proxy performs authoritative DNS and
   destination validation, and CRW has no direct internet route.
3. CRW uses auto mode (`RENDER_JS_DEFAULT` unset) so HTTP prefetch failure
   or 403 status causes escalation to the CDP renderer.
4. The proxy intercepts every HTTP request from CRW:
   - **Search engine URLs** (`google.com`, `search.brave.com`,
     `html.duckduckgo.com`, `startpage.com`, `bing.com`): returns `403 Forbidden`
     immediately — no network request, no double-hit. CRW sees
     `is_auth_blocked` and escalates to obscura.
   - **Internal/private destinations**: returns `403` without opening any
     tunnel or HTTP forwarding path. This covers localhost,
     all `*.docker.internal` names, known legacy Docker Desktop host/gateway
     names, single-label Docker service/container/alias names, literal
     loopback/private/link-local/reserved/non-global IP addresses, and legacy
     IPv4 shorthand forms. Hostnames are IDNA-normalized before these checks,
     including Unicode DNS separator equivalents. When
     `EGRESS_UPSTREAM_PROXY_URL` is empty, Myst
     mode sends A queries directly to the provider resolver inside the tunnel;
     explicit no-VPN mode uses system/Docker DNS. The proxy blocks any name
     that resolves to a blocked address and connects only to that validated
     address set without a second hostname lookup, closing the validation-to-
     connect rebinding race. When `EGRESS_UPSTREAM_PROXY_URL` is set, target
     resolution is left entirely to the upstream proxy protocol. A
     public-looking name that the upstream resolves privately is therefore a
     documented residual risk. Blocked attempts are logged.
   - **Other plain HTTP URLs**: returns `403 Forbidden` by default with the
     message `HTTP URLs are disabled by EGRESS_ALLOW_HTTP_URLS=false. Use
     an https:// URL instead.` Set `EGRESS_ALLOW_HTTP_URLS=true` to allow
     cleartext HTTP fetches. When allowed, the proxy forwards the HTTP request
     to the target through `EGRESS_UPSTREAM_PROXY_URL` if set. If the
     response is usable, CRW may return it without visiting the page in
     Obscura. For HTTP/HTTPS upstream proxies, allowed HTTP origin requests are
     forwarded with an absolute-form request target rather than a CONNECT
     tunnel. For an `https://` upstream proxy, that absolute-form request is
     sent inside the verified TLS connection to the proxy.
   - **Other HTTPS URLs**: accepts the CONNECT tunnel and connects through
     `EGRESS_UPSTREAM_PROXY_URL` if set. For an `https://` upstream proxy,
     the CONNECT request is sent inside the verified TLS connection to the
     proxy. If the HTTP result is usable, CRW may return it without visiting
     the page in Obscura. If the result is blocked, thin, or JS-required, CRW
     may then escalate to CDP.
   - **Request framing**: rejects conflicting or malformed `Content-Length`,
     rejects requests that combine `Content-Length` with `Transfer-Encoding`,
     and requires transfer codings to end in exactly one `chunked` coding.
     Valid fixed-length and chunked bodies, chunk extensions, and trailers are
     streamed without imposing a wrapper size cap.
5. The CDP shim strips `proxyServer` from `Target.createBrowserContext` as a
   safety net. In the compose default, CRW uses `HTTP_PROXY`/`HTTPS_PROXY`
   rather than `CRW_CRAWLER__PROXY`, so `REQUEST_PROXY` is not set and CRW
   usually does not send `createBrowserContext` at all.

When executor networking is enabled, executor pods live only on the internal
`onyx-code-interpreter-executor` network and receive
`HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY=http://executor-egress-bridge:3128`.
Direct sockets have no internet or stack route; the bridge reaches a separate
search-blocking `executor` policy. Only its final-hop route owner shares the
trusted routing namespace.

Each policy listener resolves and accepts only its configured bridge service,
plus loopback for its local healthcheck. Onyx public and host policies use
separate namespaces and authenticated route-broker networks; browser,
prefetch, and executor policies remain inaccessible from application networks.

The local policy healthcheck is routing-aware. In direct VPN mode it verifies
the fixed provider-DNS path; in explicit no-VPN mode it uses the explicitly
selected system resolver; and in upstream-proxy mode it performs a target-free
HTTP(S) or SOCKS protocol/authentication handshake without resolving a
browsing hostname. Each egress bridge then sends a blocked localhost CONNECT
and requires the policy's 403, distinguishing end-to-end forwarding from a
mere listening socket.

**EGRESS_UPSTREAM_PROXY_URL usage:**

When `EGRESS_UPSTREAM_PROXY_URL` is set (e.g., Tor SOCKS proxy), the prefetch-blocking proxy
routes its own upstream requests through `EGRESS_UPSTREAM_PROXY_URL`. This
keeps all final-hop policy connections on the configured upstream path.
The URL is validated before the listener starts; unsupported schemes,
malformed ports, incomplete credentials, and path/query/fragment components
fail startup. Logs show only a credential-free scheme/host/port description.
In VPN mode, a public upstream-proxy hostname is bootstrapped through the Myst
provider resolver and connected by IP. An RFC1918 IPv4 literal needs no DNS
and receives only its exact Myst route exemption; `host.docker.internal` uses
Docker resolution and its existing narrow route. The three operator-local
suffixes use system DNS only with `EGRESS_ALLOW_RFC1918=true`. The
final-hop policy never sends browsing target hostnames to Docker DNS in
upstream-proxy mode. CRW's earlier synthetic preflight may pass through
Docker's embedded resolver, but its only upstream is the loopback-only
`crw-validation-dns` process and no target query leaves CRW's namespace.
The sidecar uses only Python's standard library and does not install packages
at startup.

The CDP shim runs on internal-only networks from the prebuilt
`CDP_SHIM_IMAGE`. Its hashed Python dependency is installed during
`make cdp-shim-build`, before stack startup; the runtime command performs no
package installation and needs no temporary direct-egress path.

For `https://` upstream proxies, the prefetch-blocking proxy verifies the
proxy certificate, sends SNI for the proxy host, and requires TLS 1.3 by
default when the Python/OpenSSL runtime supports it.

The full service-by-service `EGRESS_UPSTREAM_PROXY_URL` behavior, including SearXNG settings
generation and code-interpreter executor pod caveats, is documented in
[VPN routing and proxies](vpn_routing_and_proxies.md#final-hop-routing-matrix).

| Component | Proxy used | How |
|-----------|-----------|-----|
| **Obscura (CDP browser)** | browser policy | `OBSCURA_PROXY=http://obscura-egress-bridge:3128`; `OBSCURA_ALLOW_PRIVATE_NETWORK=true` lets Obscura's own resolver reach that private proxy name |
| **Prefetch-blocking proxy (HTTP/CONNECT)** | `EGRESS_UPSTREAM_PROXY_URL` | `EGRESS_UPSTREAM_PROXY_URL` env var → upstream connection |
| **CRW HTTP prefetch** | prefetch-blocking proxy | `HTTPS_PROXY` / `HTTP_PROXY` env vars on the CRW container |
| **CRW CDP (obscura)** | `EGRESS_UPSTREAM_PROXY_URL` (via obscura) | no `REQUEST_PROXY` in the default path; shim strips `proxyServer` only as a safety net |
| **SearXNG** | none by default | CRW-backed engines use only the internal CRW peer network |
| **Code-interpreter HTTP clients** | executor policy | local executor bridge; search-engine and private/internal targets are blocked |
| **Onyx API/background HTTP helpers** | public-only Onyx bridge/policy/broker | Environment-aware `requests`, `httpx`, and `urllib` clients use `HTTP_PROXY`/`HTTPS_PROXY=http://onyx-public-egress-bridge:3128`; stack-owned internal bypasses come from `onyx/helper-egress.env` |
| **MCP/OAuth and configured Web Connector** | saved-level-selected public or host route | Explicit transports leave target enforcement and public DNS to the selected policy/broker; doc-drop uses one exact host-broker gateway |
| **Configured chat inference / embedding shim** | host-capable Onyx route | Explicit clients preserve exact-host and opt-in RFC1918 policy; the exact internal Teep chat base remains direct on `onyx-teep` |
| **OnyxWebCrawler** | selected Onyx bridge | Does not go through CRW/Obscura; its SSRF-validated request and Playwright fallback use explicit public/host routing |

**CONNECT handling:**

For HTTPS URLs (which include all search engine URLs), reqwest uses HTTP
CONNECT tunneling. The proxy handles CONNECT requests as follows:
- **Port 80 while `EGRESS_ALLOW_HTTP_URLS=false`**: returns `403` before
  opening a tunnel. Other allowed ports are opaque and are not protocol-inspected.
- **Search engine hosts**: returns `403` immediately (rejects the CONNECT).
  This forces CRW's auto mode to escalate to the CDP renderer (obscura),
  eliminating the double-hit. The search engine never sees the bare reqwest
  request.
- **Internal/private hosts**: returns `403` without opening the tunnel. The same
  destination validation is used for `CONNECT`, `GET`, and `HEAD`, so direct
  callers cannot use the proxy as a generic internal TCP tunnel or blind
  internal `HEAD` primitive.
- **Non-search-engine hosts**: establishes the tunnel through `EGRESS_UPSTREAM_PROXY_URL`
  and pipes bidirectionally. For usable HTTP results, CRW can return the
  prefetch result directly. If CRW later escalates to CDP, this can produce
  both a reqwest fetch and an obscura navigation for that non-search URL. The
  stack accepts that tradeoff because:
  - `open_url` URLs are one-off requests, not parallel fan-out, so they're
    less likely to trigger 429s
  - the search-engine anti-bot double-hit is the high-risk path this proxy is
    designed to remove

For plain HTTP URLs, the proxy applies the same search-engine and
internal/private destination blocks, then blocks the request unless
`EGRESS_ALLOW_HTTP_URLS=true`. When HTTP is explicitly allowed, direct and
SOCKS paths use normal origin-form forwarding, while HTTP/HTTPS upstream
proxies receive absolute-form HTTP requests. HTTPS upstream proxies receive
that request inside the verified TLS connection to the proxy. As with HTTPS, an
explicitly allowed usable HTTP result can be returned by CRW without Obscura.

**Keeping search-engine host lists aligned:**

Every CRW-backed SearXNG engine must have its target host covered by
`PREFETCH_BLOCK_HOSTS`. Otherwise CRW's reqwest prefetch can hit that search
engine before obscura navigates, creating the double-hit this proxy exists to
avoid. When adding, removing, or retargeting a CRW-backed engine, update:

- `crw/prefetch_blocking_proxy.py`'s default `PREFETCH_BLOCK_HOSTS`;
- the `prefetch-blocking-proxy` service default in `docker-compose.yaml`;
- `crw/cdp_shim.py` and the `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH_HOSTS` compose
  default when the engine still needs SERP-specific `networkidle2` navigation;
- the environment-variable tables and parser assumptions in this document.

**Proxy configuration — `HTTPS_PROXY` vs `CRW_CRAWLER__PROXY`:**

The prefetch-blocking proxy is configured via `HTTPS_PROXY`/`HTTP_PROXY`
environment variables on the CRW container, **not** via `CRW_CRAWLER__PROXY`.
This is critical for two reasons:

1. **Avoids the 2-second `createBrowserContext` timeout**: `CRW_CRAWLER__PROXY`
   sets CRW's `REQUEST_PROXY` task-local, which causes CRW to include
   `proxyServer` in `Target.createBrowserContext` with a hardcoded 2-second
   timeout (`cdp.rs:1427`). On Tor/slow paths, `createBrowserContext` takes
   >2s → "Timeout after 2000ms" → 504. Using `HTTPS_PROXY` env vars routes
   the HTTP prefetch through the blocking proxy via reqwest's built-in env-var
   proxy support, without setting `REQUEST_PROXY`.

2. **Avoids per-request browser context creation and cookie clearing**: When
   `REQUEST_PROXY` is set, CRW creates a new
   browser context per request (`fetch_with_ws` path, `cdp.rs:1204-1439`)
   with `Target.createBrowserContext`, then disposes it after the fetch.
   In Obscura, `Target.createBrowserContext` and
   `Target.disposeBrowserContext` clear the default cookie jar. That weakens
   session continuity and can erase any cookies gathered by earlier requests.

   With `HTTPS_PROXY` env vars, `REQUEST_PROXY` is not set, so CRW uses
   the no-proxy path (`fetch_with_pool` or `fetch_with_ws` without
   `createBrowserContext`). Obscura's default CDP context is reused across
   requests, and cookies can persist until the shim clears them. The CDP shim's
   `proxyServer` stripping is kept as a safety net but is not needed since
   `REQUEST_PROXY` is not set.

   Verified in production: CDP shim logs show no `createBrowserContext`
   or `proxyServer` stripping when using `HTTPS_PROXY` env vars.

**Environment variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `PREFETCH_PROXY_HOST` | `0.0.0.0` | Listen address |
| `PREFETCH_PROXY_PORT` | `3128` | Listen port |
| `EGRESS_UPSTREAM_PROXY_URL` | (empty) | Upstream proxy for HTTP forwarding and CONNECT tunnels. Supports `http://`, `https://`, `socks5://`, `socks5h://` |
| `EGRESS_ALLOW_HTTP_URLS` | `false` | Allow cleartext `http://` target URLs. When false, the prefetch proxy returns a text/plain 403 telling the agent to use HTTPS, and the CDP shim rejects HTTP browser navigations too. |
| `PREFETCH_BLOCK_HOSTS` | `google.com,search.brave.com,html.duckduckgo.com,startpage.com,bing.com` | Comma-separated search engine hostnames to block immediately (403 without network request) |
| `PREFETCH_BLOCK_INTERNAL_HOSTS` | empty | Optional comma-separated additional internal hostnames to block by name without opening an upstream request. It cannot remove the built-in localhost, `*.docker.internal`, legacy Docker Desktop, or single-label Docker-name blocks. Subdomains of configured names are blocked too. |
| `PREFETCH_PROXY_LOG_LEVEL` | `info` | Log level (debug/info/warning/error) |
| `PREFETCH_TUNNEL_TIMEOUT` | `15` | Timeout for establishing tunnel connections (seconds) |
| `CDP_SHIM_STRIP_PROXY_SERVER` | `1` | Strip `proxyServer` from `Target.createBrowserContext` in the CDP shim (safety net — not needed with `HTTPS_PROXY` env vars since `REQUEST_PROXY` is not set) |

---

## Path 2: Open URL (`open_url` tool)

### Request chain diagram

The open_url path does **not** involve the SearXNG container. It goes directly
from Onyx to a content provider. In the README-recommended configuration, that
provider is **Firecrawl**, configured with API Base URL
`http://crw-service-gateway:3010/v1/scrape` and any non-empty API key placeholder.
Self-hosted CRW runs open by default unless auth keys are configured. Upstream
Onyx falls back to `OnyxWebCrawler` if no content provider is configured,
but that is not the recommended wrapper setup.

**Option A: FirecrawlClient (recommended — CRW, CDP/Obscura when needed)**

```
┌──────────────────────┐
│  Onyx Agent (LLM)    │
│  open_url tool       │
│  (parallel URLs)     │
└─────────┬────────────┘
          │ POST configured Firecrawl URL (per URL, in parallel)
          │ README: http://crw-service-gateway:3010/v1/scrape
          │ {url: <url>, formats: ["markdown"]}
          ▼
┌──────────────────────────────────────────────────────┐
│  CRW :3010                                            │
│  ├─ URL preflight → loopback validation DNS           │
│  ├─ HTTP prefetch via prefetch-blocking-proxy :3128   │
│  ├─ Search → 403 → CDP                                │
│  ├─ Non-search HTTPS → prefetch tunnel                │
│  │  (return prefetch result, or CDP if needed)        │
│  ├─ Non-search HTTP → 403 unless explicitly allowed   │
│  ├─ CDP client → ws://cdp-shim:9224                   │
│  └─ Returns {success, data: {markdown}} envelope      │
└─────────┬────────────────────────────────────────────┘
          │ CDP WebSocket only after CRW escalates
          ▼
┌──────────────────────────────────────────────────────┐
│  CDP Shim :9224 (crw/cdp_shim.py)                    │
│  ├─ STEALTH_JS stripping                             │
│  ├─ waitUntil injection                              │
│  ├─ proxyServer stripping safety net                 │
│  └─ Proxies to → ws://obscura:9222                    │
└─────────┬────────────────────────────────────────────┘
          │ CDP WebSocket
          ▼
┌──────────────────────────────────────────────────────┐
│  Obscura :9222 (--stealth --storage-dir)             │
│  ├─ Renders page (HTML, JS-heavy SPAs)               │
│  └─ Returns rendered DOM → CRW converts to markdown  │
└─────────┬────────────────────────────────────────────┘
          │ HTTPS (through Myst VPN exit IP)
          ▼
┌──────────────────────────────────────────────────────┐
│  Target website (arbitrary URL)                      │
│  (search pages and escalated pages use obscura;       │
│   PDFs and usable non-search HTTPS bypass obscura;   │
│   HTTP URLs blocked unless explicitly allowed)       │
└──────────────────────────────────────────────────────┘
```

**Option B: OnyxWebCrawler (upstream fallback — helper-policy HTTP, no CRW/obscura)**

```
┌──────────────────────┐
│  Onyx Agent (LLM)    │
│  open_url tool       │
│  (parallel URLs)     │
└─────────┬────────────┘
          │ provider.contents(urls)
          ▼
┌──────────────────────────────────────────────────────┐
│  OnyxWebCrawler (in-process, no container)           │
│  ├─ ssrf_safe_get(url) through helper policy         │
│  ├─ 4xx/403 + CF headers → Playwright fallback       │
│  │   (one-shot headless render, if enabled)          │
│  ├─ is_pdf_resource() check:                         │
│  │   PDF → extract_pdf_text() (PyPDF2/pdfplumber)    │
│  │   HTML → decode_html_bytes() → parse to WebContent│
│  ├─ Max sizes: PDF 50MB, HTML 20MB                   │
│  └─ User-Agent: "OnyxWebCrawler/1.0 (+https://...)"  │
└─────────┬────────────────────────────────────────────┘
          │ HTTPS (selected VPN/upstream/no-VPN route)
          ▼
┌──────────────────────────────────────────────────────┐
│  Target website (arbitrary URL)                      │
│  (HTML pages, PDFs, APIs, etc.)                      │
└──────────────────────────────────────────────────────┘
```

**Key characteristics of the open_url path:**
- Does **not** involve the SearXNG container — goes directly from Onyx to the
  content provider
- Two provider options with different capabilities:
  - **FirecrawlClient (recommended)**: Through CRW. Search pages and pages
    that CRW classifies as blocked, thin, or JS-required go through
    CDP/shim/Obscura; usable non-search HTTPS responses can be returned from
    CRW's HTTP prefetch without Obscura. Plain HTTP URLs are blocked by
    default unless `EGRESS_ALLOW_HTTP_URLS=true`. PDFs are handled
    natively by CRW's `pdf_inspector` and bypass Obscura entirely.
  - **OnyxWebCrawler (fallback if no provider is configured)**: In-process HTTP
    via `ssrf_safe_get` and the helper policy, handles PDFs natively (PyPDF2), optional Playwright
    fallback for Cloudflare/bot-challenge 403s; no stealth on the initial HTTP
    request
- CRW's per-host rate limiter applies to the Firecrawl path (same as search)
- The CDP shim's STEALTH_JS stripping, `waitUntil` injection, and periodic
  cookie clearing apply only when the Firecrawl path escalates to CDP/Obscura
- PDF handling differs significantly between the two paths (see §2.2 and §2.3
  below)

---

### 2.1 Onyx → Content Provider

When the LLM calls the `open_url` tool, Onyx's
[`OpenURLTool`](../reference_repos/onyx/backend/onyx/tools/tool_implementations/open_url/open_url_tool.py:1)
fetches the content of one or more URLs. The tool uses a `WebContentProvider`
selected at configuration time:

- **FirecrawlClient**: Sends URLs to the configured scrape endpoint (default
  upstream constant points at Firecrawl's hosted API; README recommendation
  for this deployment: `http://crw-service-gateway:3010/v1/scrape`). Goes through CRW.
  Search-engine targets and pages that need browser rendering go through CDP
  shim → Obscura; ordinary non-search pages may be returned from CRW's HTTP
  prefetch without Obscura. This is the recommended wrapper configuration.
- **OnyxWebCrawler**: In-process HTTP fetch via `ssrf_safe_get` (SSRF-validated
  `requests`) and the fixed public Onyx egress bridge. Handles HTML and PDF natively. Has an optional Playwright
  headless-browser fallback for Cloudflare/bot-challenge 403 responses. Does
  NOT go through CRW/obscura. Upstream Onyx uses this only when no content
  provider is configured.

The provider is selected in
[`providers.py:get_default_content_provider()`](../reference_repos/onyx/backend/onyx/tools/tool_implementations/web_search/providers.py:180).
If no content provider is configured, `OnyxWebCrawler` is the upstream default;
the README setup tells you to configure Firecrawl and set it as default.

SSRF handling differs by provider. The recommended Firecrawl path sends the
target URL as JSON to the configured CRW endpoint (`http://crw-service-gateway:3010/v1/scrape`
in this wrapper); Onyx's upstream `ssrf_safe_get()` target validation is not
applied to that URL before CRW/Obscura handles it. The fallback
`OnyxWebCrawler` path does use `ssrf_safe_get()` and therefore applies Onyx's
SSRF Protection policy to the target URL. Even when the fallback crawler is
allowed to fetch private-network targets, loopback/link-local targets remain
blocked on that LLM-controlled fetch path.

Compose seeds Onyx Admin SSRF protection to Allow Private Network with
loopback disabled; saved Admin state takes precedence. The selected level
chooses the public or host bridge for each new MCP client and Web crawl. These
settings are not firewall rules for CRW, the CDP shim, or Obscura. A page rendered in Obscura can attempt browser requests
to internal addresses that are reachable from the browser namespace; browser
same-origin/CORS behavior may limit reading responses, but it is not a
stack-internal access-control boundary.

### 2.1.1 Wrapper runtime patches

The wrapper mounts `onyx/patches/sitecustomize_base` into the API server and
puts it on `PYTHONPATH`. In full mode, that base `sitecustomize` module is the
one Python imports at process startup. In lite mode, `docker-compose.lite.yml`
places `onyx/patches/sitecustomize` first on `PYTHONPATH`; that lite patch
imports every applicable helper from the base patch module and then applies the
lite-only Open URL availability patch.

These patches do not choose the active content provider; the Onyx Admin
Firecrawl setting does that. They adjust behavior around the
`open_url` path. See [Onyx patch information](onyx_patch_info.md) for the
complete patch inventory and upstreaming notes.

- `apply_open_url_char_limit_patches()` lets wrapper env vars
  `ONYX_OPEN_URL_MAX_CHARS_PER_URL` and `ONYX_OPEN_URL_MAX_TOTAL_CHARS` override
  upstream truncation defaults.
- `apply_coding_agent_repo_download_limit_patch()` makes
  `ONYX_CODE_INTERPRETER_MAX_FILE_SIZE_MB` the shared byte-oriented ceiling for
  API-side GitHub tarball downloads and code-interpreter file uploads. It is
  independent of the Open URL character budgets.
- `apply_code_interpreter_network_description_patches()` updates tool
  descriptions when `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`; it is not part of the
  `open_url` request path. It explains the local CRW/Firecrawl and SearXNG APIs
  only inside coding-agent system prompts, not in Python-tool or code-agent
  tool descriptions.
- Lite mode additionally mounts `onyx/patches/sitecustomize`, which patches
  `OpenURLTool.is_available()` to return `True`.

### 2.2 FirecrawlClient Path (recommended: CRW, CDP/Obscura when needed)

When Firecrawl is configured as the content provider:

```
Onyx open_url → FirecrawlClient.contents(urls)
  → POST <configured Firecrawl base_url>
      README recommendation: http://crw-service-gateway:3010/v1/scrape
      payload: {url: <url>, formats: ["markdown"]}
  → CRW scrape endpoint
    ├─ HTTP prefetch through prefetch-blocking proxy
    │   ├─ search URL → local 403 → auto-escalate to CDP
    │   ├─ plain HTTP non-search URL → 403 unless explicitly allowed
    │   ├─ HTTPS non-search URL → CONNECT tunnel; may return HTTP result
    │   │   or CDP if blocked/thin/JS-needed
    │   ├─ application/pdf → raw bytes extracted, PDF pipeline runs
    │   │   → pdf_inspector extracts text → markdown
    │   │   → obscura/CDP shim BYPASSED entirely
    │   └─ other binary → non-text handling
    └─ CDP path for HTML
        → CDP shim → obscura renders page → CRW converts DOM to markdown
  → WebContent(title, full_content, link)
```

The `FirecrawlClient` sends each URL to the configured scrape endpoint with
`formats: ["markdown"]` and no `waitFor` field. When CRW escalates to CDP,
page readiness remains in the shim/Obscura `waitUntil` path and CRW's
post-navigation heuristics.

CRW's `FallbackRenderer` performs an HTTP prefetch first to check the content
type ([`lib.rs:801-862`](../reference_repos/crw/crates/crw-renderer/src/lib.rs:801)).
This prefetch is a full HTTP `GET`, not a `HEAD` request; see
[CRW MIME Probe Uses GET](#crw-mime-probe-uses-get).
In this deployment the prefetch goes through the prefetch-blocking proxy:

- **Search pages**: CRW uses auto mode (`RENDER_JS_DEFAULT` unset). The proxy
  returns 403 for configured search-engine hosts, forcing CRW to escalate to
  the CDP renderer (CDP shim → obscura). The rendered DOM is extracted to
  markdown via CRW's readability + markdown pipeline. See §1.7 for the
  prefetch-blocking proxy design.

- **Other HTTPS pages**: The proxy forwards the prefetch through a CONNECT
  tunnel. If CRW decides the response is blocked, thin, or JS-required, it
  escalates to the CDP renderer. If the response is usable, CRW can return it
  directly without Obscura. See [Known Limitations](#known-limitations).

- **Other plain HTTP pages**: Blocked by default before any upstream request is
  opened. The response body tells the agent to use an `https://` URL instead.
  If `EGRESS_ALLOW_HTTP_URLS=true`, the proxy forwards the request and the
  same "usable HTTP result can bypass Obscura" limitation applies.

- **PDF documents**: When the HTTP prefetch returns `Content-Type:
  application/pdf`, CRW **bypasses obscura entirely**
  ([`lib.rs:807-808`](../reference_repos/crw/crates/crw-renderer/src/lib.rs:807)).
  The raw PDF bytes are passed to CRW's built-in PDF parser
  ([`crw-extract/src/pdf.rs`](../reference_repos/crw/crates/crw-extract/src/pdf.rs:1)),
  which uses the pure-Rust `pdf_inspector` crate to extract text and convert
  it to markdown. The CDP shim and obscura are never involved in PDF rendering.

- **Other binary types** (images, octet-stream, etc.): Treated as non-text
  content. CRW hashes the bytes for change-tracking purposes but does not
  extract text. The response will have empty markdown content.

### 2.3 OnyxWebCrawler Path (upstream fallback: helper-policy HTTP, no CRW/obscura)

```
Onyx open_url → OnyxWebCrawler.contents(urls)
  → ssrf_safe_get(url) through the selected Onyx egress bridge
  → 4xx response? (403 or Cloudflare cf-ray/cf-mitigated headers)
    → if OPEN_URL_PLAYWRIGHT_FALLBACK_ENABLED:
        → fetch_rendered_html(url) (one-shot headless Chromium render)
        → if rendered HTML looks like CF challenge → CF failure reason
        → else parse rendered HTML → WebContent
    → else: return failure with reason (CLOUDFLARE_CHALLENGE / HTTP_403_BLOCKED / etc.)
  → is_pdf_resource() check
    → PDF: extract_pdf_text() (PyPDF2/pdfplumber)
    → HTML: decode_html_bytes() → _parse_html_to_web_content()
  → WebContent(title, full_content, link)
```

PDFs are detected via `is_pdf_resource(url, content_type, content_sniff)` which
checks:
- URL extension (`.pdf`)
- Content-Type header (`application/pdf`)
- Magic bytes (`%PDF` in the first 1024 bytes)

PDF text extraction uses `extract_pdf_text(content)` which returns
`(text, metadata)`. The text is truncated to `DEFAULT_MAX_PDF_SIZE_BYTES`
(50 MB) and returned as `WebContent`.

This path does **not** go through CRW, the CDP shim, or obscura. It is an
in-process HTTP fetch via `ssrf_safe_get()` (SSRF-validated `requests.get`),
and `requests` sends public targets through `onyx-public-egress-bridge` to the
public policy and route broker. That path applies private/internal and
cleartext URL rules and selects Myst, the configured upstream proxy, or
explicit no-VPN egress. Onyx's internal
dependencies bypass it only through the stack-owned `NO_PROXY` set in
`onyx/helper-egress.env`. Playwright does not inherit that set; the helper
path has no private-target exception. The separate full-mode Web Connector
reaches the exact `doc-drop-web:8091` identity through the host policy, route
broker, and fixed doc-drop gateway.
The initial request uses a simple `OnyxWebCrawler/1.0 (+https://www.onyx.app)`
user agent with no stealth. When the response is a 403 or carries Cloudflare
headers (`cf-ray`, `cf-mitigated`, `Server: cloudflare`), and
`OPEN_URL_PLAYWRIGHT_FALLBACK_ENABLED` is set, the crawler retries via a
one-shot Playwright headless-Chromium render
([`onyx_web_crawler.py:_fetch_via_playwright()`](../reference_repos/onyx/backend/onyx/tools/tool_implementations/open_url/onyx_web_crawler.py:338)).
If the rendered page is itself the Cloudflare challenge interstitial, a
`CLOUDFLARE_CHALLENGE` failure reason is surfaced to the LLM. Pages that
remain blocked after the fallback (or when the fallback is disabled) return
a failure with a descriptive `failure_reason`.

For the Firecrawl/CRW path, PDFs work without a browser PDF viewer. The CDP
shim's STEALTH_JS stripping does not apply to PDF requests because they are
short-circuited before reaching CDP. CRW's PDF parser has its own safety
features: decompression-bomb guard, page count caps, timeout, and optional
sandboxed subprocess execution
([`config.rs:43-91`](../reference_repos/crw/crates/crw-core/src/config.rs:43)).
Scanned/image-only PDFs produce empty or partial text (no OCR) with a warning,
since `pdf_inspector` has no OCR capability.

**PDF parsing configuration** (CRW env vars, [`config.rs:43-91`](../reference_repos/crw/crates/crw-core/src/config.rs:43)):

| Variable | Default | Description |
|----------|---------|-------------|
| `CRW_DOCUMENT__ENABLED` | `true` | Master switch for PDF parsing |
| `CRW_DOCUMENT__MAX_PAGES` | `0` | Max pages to parse (0 = no limit) |
| `CRW_DOCUMENT__ATTEMPT_SCANNED` | `false` | Best-effort extraction from scanned/image PDFs (no OCR; usually empty) |
| `CRW_DOCUMENT__MAX_UPLOAD_BYTES` | `52428800` | Max upload size for `POST /v2/parse` (50 MiB) |
| `CRW_DOCUMENT__UPLOAD_CONCURRENCY` | `4` | Max concurrent upload body buffering |
| `CRW_DOCUMENT__MAX_CONCURRENT_PARSES` | `4` | Process-wide concurrent parse cap (all surfaces) |
| `CRW_DOCUMENT__PARSE_TIMEOUT_MS` | `30000` | Per-parse wall-clock timeout (0 = disabled) |
| `CRW_DOCUMENT__MAX_DECOMPRESSED_BYTES` | `104857600` | Decompression-bomb guard (100 MB, 0 = disabled) |
| `CRW_DOCUMENT__SANDBOX` | `false` | Run each parse in an isolated child process (Unix only) |
| `CRW_DOCUMENT__SANDBOX_MEMORY_BYTES` | `536870912` | Hard address-space limit for sandbox child (512 MiB) |

### 2.4 CRW scrape endpoint compatibility

CRW exposes a native `/v1/*` API and also mounts Firecrawl compatibility
routes under `/firecrawl/*`. This stack uses CRW's native scrape endpoint:

- `/v1/scrape` for SearXNG engines via `_crw.py`. The payload asks for
  `rawHtml`, sets `onlyMainContent: false`, and sends no `renderer` or
  `waitFor` field. CRW uses auto mode (`RENDER_JS_DEFAULT` unset), the
  prefetch-blocking proxy returns 403 for search-engine prefetches, and CRW
  escalates to the chrome/obscura CDP renderer.
- `/v1/scrape` for the README-recommended Onyx Firecrawl content provider
  configuration (`http://crw-service-gateway:3010/v1/scrape`). Onyx's FirecrawlClient
  sends `{url, formats: ["markdown"]}` to the configured URL.
These calls go through CRW's `FallbackRenderer` pipeline. The HTTP prefetch
runs first; PDFs are handled natively by `pdf_inspector` without reaching the
CDP layer. For HTML, the requested format controls the output: `rawHtml` is
the full HTML body used by SearXNG engines for XPath parsing; `markdown` is
readability-extracted and converted to markdown for Onyx's `FirecrawlClient`.
Search-engine scrapes are forced to CDP/Obscura by the prefetch-blocking
proxy. `open_url` scrapes benefit from the CDP shim's `waitUntil` injection
only when CRW escalates to browser rendering; usable non-search HTTPS-prefetch
results can return without reaching CDP. Explicitly allowed plain HTTP
prefetches behave the same way.

CRW `v0.23.0` serializes native `/v1` error responses with the camelCase
`errorCode` field. The SearXNG helper reads that field when mapping CRW
anti-bot, CAPTCHA, 429, and access-denied results into SearXNG engine
suspension exceptions. Its native `/v1/extract` endpoint is an asynchronous,
multi-URL structured-extraction API with per-URL results; it is separate from
`/v1/scrape` and is not called by either wrapper request path. The scrape
success fields consumed here are `data.rawHtml` for search-engine parsing and
`data.markdown` for Onyx `open_url`.

---

## CDP Shim: Logging & Troubleshooting

### Viewing shim logs

The wrapper uses layered compose files and two env files. The correct
invocation depends on whether you're running lite or full mode. The
Makefile targets (`make logs-lite`, `make logs-full`) handle this
automatically, but they tail **all** services. To view just the cdp-shim:

```bash
# Full mode (make up-full):
COMPOSE_FILE=docker-compose.yaml:docker-compose.full.yml \
  docker compose --env-file stack.versions.env \
  --env-file .env.wrapper \
  --env-file onyx/onyx_data/deployment/.env \
  logs -f cdp-shim

# Lite mode (make up-lite):
COMPOSE_FILE=docker-compose.yaml:docker-compose.lite.yml \
  docker compose --env-file stack.versions.env \
  --env-file .env.wrapper \
  --env-file onyx/onyx_data/deployment/.env \
  logs -f cdp-shim

# Last 100 lines only (full mode):
COMPOSE_FILE=docker-compose.yaml:docker-compose.full.yml \
  docker compose --env-file stack.versions.env \
  --env-file .env.wrapper \
  --env-file onyx/onyx_data/deployment/.env \
  logs --tail 100 cdp-shim

# Logs from the last hour with timestamps (full mode):
COMPOSE_FILE=docker-compose.yaml:docker-compose.full.yml \
  docker compose --env-file stack.versions.env \
  --env-file .env.wrapper \
  --env-file onyx/onyx_data/deployment/.env \
  logs --since 1h -t cdp-shim

# View CRW logs (to see CDP connection + scrape activity):
COMPOSE_FILE=docker-compose.yaml:docker-compose.full.yml \
  docker compose --env-file stack.versions.env \
  --env-file .env.wrapper \
  --env-file onyx/onyx_data/deployment/.env \
  logs -f crw

# View obscura logs (to see browser rendering + stealth activity):
COMPOSE_FILE=docker-compose.yaml:docker-compose.full.yml \
  docker compose --env-file stack.versions.env \
  --env-file .env.wrapper \
  --env-file onyx/onyx_data/deployment/.env \
  logs -f obscura
```

> **Note:** The `COMPOSE_FILE` and `--env-file` flags are required because
> the wrapper layers multiple compose files (`docker-compose.yaml` as the
> base, `docker-compose.full.yml` or `docker-compose.lite.yml` as the
> override) and loads env vars from `stack.versions.env` (image/source pins),
> `.env.wrapper` (local runtime config like VPN, ports, proxy, and keys), and
> `onyx/onyx_data/deployment/.env` (Onyx deployment config and database
> credentials). Omitting any layer will result in "required variable is
> missing" errors.

### Log levels

The shim logs at the following levels:

| Level | What it logs |
|-------|-------------|
| `INFO` | Stealth stripping, cookie clearing, connection events |
| `DEBUG` | CDP message forwarding details, unsupported method errors (e.g. Page.stopLoading) |
| `WARNING` | Non-JSON messages, missing CDP fields, unexpected CDP errors |
| `ERROR` | Upstream connection failures, proxy errors, fallback forward failures |

Set `CDP_SHIM_LOG_LEVEL=debug` in docker-compose for verbose logging.

### CDP trace diagnostics

For short browser-path investigations, set `CDP_SHIM_TRACE=1` on the
`cdp-shim` service. Trace mode logs selected CDP command timings plus
navigation, lifecycle, network response, and network failure events. It is
intended for questions like "did Obscura receive a Google 429 immediately, or
did it first render a JS retry shell?"

Trace URLs redact query values by default. For example, a Google request is
logged with the search query redacted but diagnostic keys such as `udm=14`,
`hl`, `start`, and `tbs` retained. Keep
`CDP_SHIM_TRACE_INCLUDE_QUERY_VALUES=0` unless you are doing a short, local
debugging session where full query logging is explicitly acceptable. Use
`CDP_SHIM_TRACE_SAFE_QUERY_KEYS` to adjust the allowlist of non-sensitive query
keys that remain visible.

Useful trace patterns:

- `CDP trace command Page.navigate ... waitUntil=networkidle2 search=True`:
  the shim classified the URL as a SERP and injected the search wait strategy.
- `CDP trace event Network.responseReceived ... status=429 ... url=...udm=14`:
  the browser path received a target-site rate-limit / anti-bot page.
- `CDP trace event Page.frameNavigated ... /sorry/index?...`: Google moved the
  rendered frame into its sorry/CAPTCHA flow.
- `CDP trace event Network.responseReceived ... recaptcha/enterprise.js`:
  a CAPTCHA/sorry page loaded reCAPTCHA assets after the main document block.

Trace mode does not dump response bodies or rendered DOM. If selector debugging
is needed, capture `rawHtml` from CRW separately and handle it as private
browsing/search data.

### Diagnosing issues

**Onyx says `API key validation failed: content fetch returned no results` for
the local Firecrawl provider**:

- Onyx validates by scraping `https://example.com`. The configured URL must be
  the complete endpoint
  `http://crw-service-gateway:3010/v1/scrape`; Onyx does not append
  `/v1/scrape`.
- Check that `crw-validation-dns`, `crw`, `crw-service-gateway`,
  `crw-prefetch-bridge`, and the selected final-hop policy are healthy.
- From the CRW container, `getent ahostsv4 crw-validation.test` must return
  `93.184.216.34`. Known internal names must still return their private Docker
  addresses and be rejected by CRW.
- A CRW log error `Invalid request: DNS resolution failed` before any
  final-hop request normally means the loopback validation resolver is absent
  or CRW's Compose `dns` override was not applied. Do not work around this
  with CRW's allow-loopback test flag; that disables the private-address check
  for reachable internal peers.

**Stealth JS not being stripped**:
- Check that `CDP_SHIM_STRIP_STEALTH_JS=1` is set.
- Check that CRW injects `STEALTH_JS` (look for
  `"Stripped CRW STEALTH_JS"` log lines). If CRW changes the script content,
  the marker detection (`"Hide navigator.webdriver"`) may need updating.

**No STEALTH_JS logs for `open_url` but logs appear for `web_search`**:
- This usually means `open_url` is not using the recommended Firecrawl content
  provider, or CRW's auto-mode HTTP prefetch is returning usable HTTP content
  without escalating to CDP.
- Check that the prefetch-blocking proxy is running (`docker compose ps
  prefetch-blocking-proxy` and `crw-prefetch-bridge`). If either is down,
  CRW prefetch fails closed; it has no direct fallback route.
- Check that CRW has `HTTPS_PROXY=http://crw-prefetch-bridge:3128` and the
  equivalent `HTTP_PROXY` in the effective Compose model.
- Also verify that the Onyx content provider is actually configured as
  Firecrawl (README: API Base URL
  `http://crw-service-gateway:3010/v1/scrape`, any
  non-empty API key placeholder) in Onyx Admin → Web Search. If no content
  provider is configured, `open_url` uses `OnyxWebCrawler` (helper-policy HTTP) and
  never touches CRW at all.
- Symptom: "JS did not run on the page" — for a non-search URL, this can be
  expected. It means CRW returned usable HTTP-prefetch content without Obscura.
  For configured search-engine URLs, it indicates the host list, proxy env, or
  prefetch-blocking proxy is not aligned with the target.

**CDP errors from obscura**:
- Look for `"CDP error response"` warnings — these indicate obscura rejected
  a CDP command (e.g., invalid target ID, method not found).
- "Unknown method" errors (e.g. `Page.stopLoading`) are logged at DEBUG and
  are non-fatal — CRW continues after the error.

**waitUntil injection not working**:
- Check that `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH` and `OBSCURA_BROWSER_WAIT_UNTIL_WEB`
  are set in the cdp-shim service environment (the wrapper example defaults
  to `load` and `domcontentloaded`; Compose fallbacks are `networkidle2` and
  `load` if no wrapper environment supplies them).
- Look for `"Injected waitUntil="` log lines at DEBUG level
  (`CDP_SHIM_LOG_LEVEL=debug`).
- With the wrapper defaults, search engine URLs that reach CDP should show
  `waitUntil=load`; other URLs should show `waitUntil=domcontentloaded`. Non-search
  `open_url` pages that CRW returns from HTTP prefetch will not have
  `waitUntil` logs.
- If pages return too early (empty SERP, SPA shell), obscura
  may be timing out before network idle is reached. Check
  `OBSCURA_NAV_TIMEOUT_MS` — if it's too low for your proxy path, increase
  it (and `CRW_RENDERER__CHROME_TIMEOUT_MS` to match).
- If pages are taking too long, try setting `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH=load`
  and/or `OBSCURA_BROWSER_WAIT_UNTIL_WEB=domcontentloaded` depending on which URL
  class is slow.
- To disable the waitUntil injection: set both `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH=""`
  and `OBSCURA_BROWSER_WAIT_UNTIL_WEB=""`. Obscura will default to
  `DomContentLoaded` and return the nav response as soon as the HTML is parsed,
  without waiting for network silence.
- If you see "Timeout after 12000ms" → 504 errors in CRW logs, the
  `CRW_RENDERER__CHROME_NAV_BUDGET_MS` is too low for your proxy path.
  Increase it (default 48000 for Tor/slow VPN; CRW's own default is 12000).

**Cookie clearing not working**:
- Check for `"Cleared browser cookies"` log lines (should appear every
  `OBSCURA_BROWSER_CLEAR_COOKIES_INTERVAL` seconds).
- If obscura is unreachable, the clearing will fail — check for
  `"Error in clear_state_loop"` errors.

**CRW connection failures**:
- Look for `"Could not connect to obscura"` errors.
- Check that obscura is healthy: `docker compose ps obscura`.
- Check that `OBSCURA_CDP_URL` points at the correct obscura endpoint.

### Key log patterns to grep for

These commands use the full-mode compose invocation. For lite mode,
replace `docker-compose.full.yml` with `docker-compose.lite.yml`.

```bash
# Stealth stripping
COMPOSE_FILE=docker-compose.yaml:docker-compose.full.yml \
  docker compose --env-file stack.versions.env \
  --env-file .env.wrapper \
  --env-file onyx/onyx_data/deployment/.env \
  logs cdp-shim | grep "Stripped CRW STEALTH_JS"

# waitUntil injection (DEBUG level required)
COMPOSE_FILE=docker-compose.yaml:docker-compose.full.yml \
  docker compose --env-file stack.versions.env \
  --env-file .env.wrapper \
  --env-file onyx/onyx_data/deployment/.env \
  logs cdp-shim | grep "Injected waitUntil"

# Errors
COMPOSE_FILE=docker-compose.yaml:docker-compose.full.yml \
  docker compose --env-file stack.versions.env \
  --env-file .env.wrapper \
  --env-file onyx/onyx_data/deployment/.env \
  logs cdp-shim | grep -E "ERROR|WARNING"

# Proxy safety-net stripping
COMPOSE_FILE=docker-compose.yaml:docker-compose.full.yml \
  docker compose --env-file stack.versions.env \
  --env-file .env.wrapper \
  --env-file onyx/onyx_data/deployment/.env \
  logs cdp-shim | grep "Stripped proxyServer"
```

---

## Configuration Reference

### CRW environment variables (docker-compose.yaml)

| Variable | Default | Description |
|----------|---------|-------------|
| `CRW_RENDERER__MODE` | `chrome` | Selects which JS renderers are in the ladder (chrome only, not lightpanda). |
| `CRW_RENDERER__RENDER_JS_DEFAULT` | (unset) | Auto mode: HTTP prefetch first, escalate to CDP on failure/403, blocked/thin content, or JS-required detection. The prefetch-blocking proxy returns 403 for configured search-engine hosts, forcing CRW to escalate to obscura for SERPs. Non-search HTTPS pages may return from the HTTP prefetch when usable; plain HTTP pages are blocked unless explicitly allowed. Do not set this to `true`; forced render-js mode propagates prefetch failures instead of using the auto-mode escalation path. |
| `HTTPS_PROXY` / `HTTP_PROXY` | `http://crw-prefetch-bridge:3128` | Routes CRW's reqwest prefetch through the restricted bridge without setting `REQUEST_PROXY`. |
| `NO_PROXY` | `127.0.0.1,localhost,::1,cdp-shim,searxng-core` | Keeps CRW health checks and its explicit CDP/search peers off the prefetch proxy. |
| `dns` (Compose) | `127.0.0.1` via Docker embedded DNS | Sends CRW's otherwise-unresolvable public URL preflight only to the non-forwarding `crw-validation-dns` process in CRW's network namespace; Docker continues to answer known service names itself. |
| `CRW_CRAWLER__PROXY` | (unset) | Intentionally not used for the prefetch-blocking proxy. Setting it would make CRW include `proxyServer` in `Target.createBrowserContext`; the shim can strip this as a safety net, but the compose default avoids that path entirely. |
| `CRW_RENDERER__CHROME__WS_URL` | `ws://cdp-shim:9224/devtools/browser` | CDP shim endpoint on the dedicated network |
| `CRW_RENDERER__CHROME_TIMEOUT_MS` | `50000` | Per-page navigation timeout for the chrome (obscura) renderer tier. Must be ≥ `OBSCURA_NAV_TIMEOUT_MS` so CRW's deadline doesn't fire before obscura's nav timeout. |
| `CRW_RENDERER__HTTP_TIMEOUT_MS` | `60000` | HTTP prefetch timeout. CRW always runs an HTTP prefetch before the CDP renderer (even with `RENDER_JS_DEFAULT=true`) to check Content-Type. PDFs and usable non-search HTTPS results can bypass obscura; plain HTTP results can only do so if `EGRESS_ALLOW_HTTP_URLS=true`. Ceiling, not delay — completes in 1-3s on happy path. Contributes to `ladder_min`. No happy-path impact. |
| `CRW_RENDERER__CHROME_NAV_BUDGET_MS` | `48000` | Post-navigate budget for the chrome renderer tier. This races CRW's post-navigation work after `Page.loadEventFired`: SPA selector poll, content stability, challenge retry, and DOM extraction. |
| `CRW_REQUEST__DEADLINE_MS_DEFAULT` | `300000` | Baseline per-request deadline (ms). With `auto_extend_deadline_for_ladder=true` (default), effective deadline is `max(this, ladder_min=~138s)` = 300s. Accommodates multi-request scenarios (multiple search query strings, `SEARXNG_ROUND_ROBIN=false` parallel fan-out, GitHub URL crawling) by giving queued requests in the per-host rate limiter enough budget to complete while serializing per-host to avoid 429s. No happy-path impact. See §1.6.1. |
| `CRW_CRAWLER__REQUESTS_PER_SECOND` | `0.33` | Per-host rate limit (~3s interval). The rate-limit sleep is deducted from the request's deadline budget before navigation begins. See §1.6.1 for the compounding interaction with queued same-host requests. |
| `CRW_CRAWLER__PER_HOST_MAX_CONCURRENT` | `1` | Max concurrent requests per eTLD+1. The semaphore is held for the entire fetch duration (navigation + render), serializing requests to the same search engine. See §1.6.1 for deadline compounding under queued same-host requests. |
| `CRW_CRAWLER__STEALTH__JITTER_FACTOR` | `0.2` | ±20% jitter on rate-limit intervals (crawl path only) |

### CDP shim environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CDP_SHIM_HOST` | `0.0.0.0` | Listen address |
| `CDP_SHIM_PORT` | `9224` | Listen port |
| `OBSCURA_CDP_URL` | `ws://obscura:9222/devtools/browser` | Obscura CDP endpoint on the browser-control network |
| `CDP_SHIM_STRIP_STEALTH_JS` | `1` | Strip CRW's STEALTH_JS (1=yes, 0=no) |
| `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH` | `load` in `.env.wrapper.example`; Compose fallback `networkidle2` | `waitUntil` for search engine URLs. Options: `domcontentloaded`, `load`, `networkidle0`, `networkidle2`. |
| `OBSCURA_BROWSER_WAIT_UNTIL_WEB` | `domcontentloaded` in `.env.wrapper.example`; Compose fallback `load` | `waitUntil` for non-search URLs that CRW escalates to CDP. |
| `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH_HOSTS` | `google.com,search.brave.com,html.duckduckgo.com,startpage.com,bing.com` | Comma-separated search engine hostnames for per-URL `waitUntil` selection. |
| `CDP_SHIM_STRIP_PROXY_SERVER` | `1` | Strip `proxyServer` from `Target.createBrowserContext` (safety net — not needed with `HTTPS_PROXY` env vars since `REQUEST_PROXY` is not set). See §1.7. |
| `CDP_SHIM_LOG_LEVEL` | `info` | Log level (debug/info/warning/error) |
| `OBSCURA_BROWSER_CLEAR_COOKIES_INTERVAL` | `3600` | Periodic cookie clearing interval (seconds, 0=disabled) |
| `CDP_SHIM_TRACE` | `0` | Optional sanitized CDP trace mode for short browser/debug sessions. When `1`, logs selected command timings plus navigation, lifecycle, network response, and network failure events. |
| `CDP_SHIM_TRACE_INCLUDE_QUERY_VALUES` | `0` | When `0`, trace URLs redact query values to avoid logging private searches. Set to `1` only for local, short-lived debugging where full query logging is acceptable. |
| `CDP_SHIM_TRACE_SAFE_QUERY_KEYS` | `udm,hl,gl,start,tbs,safe,filter` | Query keys whose values remain visible in trace logs even when query-value redaction is enabled. Keeps diagnostics like `udm=14` visible without logging search terms. |
| `CDP_SHIM_TRACE_MAX_URL_CHARS` | `240` | Maximum length for sanitized URLs in trace logs. |

### Prefetch-blocking proxy environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PREFETCH_PROXY_HOST` | `0.0.0.0` | Listen address |
| `PREFETCH_PROXY_PORT` | `3128` | Listen port |
| `EGRESS_PROXY_ALLOWED_CLIENT_HOSTS` | required except for literal-loopback listeners | Comma-separated dedicated bridge service names allowed to connect; an empty list is valid only when `PREFETCH_PROXY_HOST` is a literal loopback address. |
| `EGRESS_PROXY_TRUSTED_INTERNAL_DESTINATIONS` | empty; the full stack sets exact `doc-drop-web:8091` authority on the host policy and broker | Exact stack-owned `host:port` authorities allowed through their fixed internal gateway despite normal private/cleartext blocking. Valid only for `onyx-helper`; this is not a general bypass list. |
| `EGRESS_UPSTREAM_PROXY_URL` | (empty) | Upstream proxy for HTTP forwarding and CONNECT tunnels. Supports `http://`, `https://`, `socks5://`, `socks5h://`. When set, the proxy routes its own upstream requests through this proxy. |
| `EGRESS_ALLOW_HTTP_URLS` | `false` | Allow cleartext `http://` target URLs in the prefetch proxy and CDP shim. When false, HTTP fetches fail closed with a message telling the agent to use HTTPS. |
| `PREFETCH_BLOCK_HOSTS` | `google.com,search.brave.com,html.duckduckgo.com,startpage.com,bing.com` | Comma-separated search engine hostnames to block immediately (403 without network request) |
| `PREFETCH_BLOCK_INTERNAL_HOSTS` | empty | Optional comma-separated additional internal hostnames to block by name without opening an upstream request. It cannot remove the built-in localhost, `*.docker.internal`, legacy Docker Desktop, or single-label Docker-name blocks. Subdomains of configured names are blocked too. |
| `PREFETCH_PROXY_LOG_LEVEL` | `info` | Log level (debug/info/warning/error) |
| `PREFETCH_TUNNEL_TIMEOUT` | `15` | Timeout for establishing tunnel connections (seconds) |

### Obscura environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OBSCURA_NAV_TIMEOUT_MS` | `45000` | Hard ceiling on a single `Page.navigate`. Must be ≤ `CRW_RENDERER__CHROME_TIMEOUT_MS`. Obscura's own default is 30s; bumped to 45s for Tor/slow VPN. |
| `OBSCURA_CDP_COMMAND_TIMEOUT_MS` | `90000` | Per-CDP-command deadline. A hung page is terminated after this budget. Obscura's own default is 60s; bumped to 90s for Tor. |
| `OBSCURA_FETCH_TIMEOUT_MS` | `45000` | Request timeout for scripted `fetch()`/XHR/ES-module loads. Obscura's own default is 30s; bumped to 45s for slow proxy paths. |

### Obscura command-line flags

| Flag | Description |
|------|-------------|
| `--stealth` | Enable TLS fingerprint impersonation + tracker blocking + JS fingerprint spoofing |
| `--storage-dir /data/obscura` | Persist cookies to `{dir}/cookies.json` (loaded on startup, saved on shutdown) |
| `--host 0.0.0.0` | Bind to all interfaces |
| `--port 9222` | CDP WebSocket port |

### SearXNG settings (settings.yml)

| Setting | Value | Description |
|---------|-------|-------------|
| `formats` | `html`, `json` | JSON output is required by Onyx's `SearXNGClient`; HTML remains available for local/manual diagnostics |
| `ban_time_on_fail` | `5` | Base engine ban time (seconds) |
| `max_ban_time_on_fail` | `120` | Max engine ban time (seconds) |
| `SearxEngineTooManyRequests` | `180` | Suspension time for 429 errors (seconds) |
| `SearxEngineAccessDenied` | `180` | Suspension time for 403 errors (seconds) |
| `SearxEngineCaptcha` | `3600` | Suspension time for CAPTCHA (seconds) |
| `server.secret_key` | Ephemeral `SEARXNG_SECRET` generated by the Makefile | Overwrites the overlay value for wrapper starts |
| `SEARXNG_ROUND_ROBIN` | `true` | Container env consumed by the wrapper SearXNG startup patch; schedules one CRW-backed web provider per query, drops other selected engines when that provider pool is present, retries another provider after unresponsive failures, and uses last-resort providers only when normal providers are suspended/unavailable |
| `outgoing.request_timeout` | `6.0` | Default SearXNG HTTP client timeout; custom CRW-backed engines override this with longer per-engine timeouts |
| Engine `timeout` | `60.0` | Per-engine timeout (seconds) — accommodates obscura render time |
| `google2`, `brave2`, `duckduckgo2`, `startpage2`, `bing2` | enabled | Custom CRW-backed engines mounted from `searxng/engines/` |
| `bing2.last_resort` | `true` | Marks Bing as a fallback-quality engine for the wrapper scoring patch |
| `bing2.last_resort_fallback_weight` | `0.05` | Score weight for Bing-only results, which are sorted after all normal-engine results |
| `bing2.last_resort_confirmation_bonus` | `0.15` | Small multiplier added when Bing confirms a result also found by a normal engine |
| Stock Google/Brave/DuckDuckGo/Startpage/Bing web variants | removed by overlay | Avoids double-querying direct stock engines that are challenge-prone on VPN/datacenter exit IPs |

`searxng/core-config/settings.yml` is a minimal overlay merged with the
SearXNG image defaults via `use_default_settings`. The upgrade procedure for
that overlay is tracked in
[SearXNG settings overlay](onyx_patches_upgrade.md#searxng-settings-overlay).

The Makefile generates `SEARXNG_SECRET` for each stack start. Other SearXNG
env-overridden defaults stay inherited from the image rather than being
repeated in the overlay:
`SEARXNG_PORT`, `SEARXNG_BIND_ADDRESS`, `SEARXNG_BASE_URL`,
`SEARXNG_LIMITER`, `SEARXNG_PUBLIC_INSTANCE`, `SEARXNG_IMAGE_PROXY`,
`SEARXNG_METHOD`, and `SEARXNG_VALKEY_URL`.

---

## Known Limitations

### Non-Search `open_url` Pages May Bypass Obscura

The wrapper guarantees the Obscura browser path for configured search-engine
targets by returning a local `403` from the prefetch-blocking proxy before
CRW's raw HTTP prefetch reaches those hosts. That guarantee is intentionally
scoped to search providers listed in `PREFETCH_BLOCK_HOSTS`.

For arbitrary non-search `open_url` URLs, CRW's first step is still a normal
HTTP prefetch through reqwest. The prefetch-blocking proxy handles those
requests after destination validation:

- plain HTTP non-search URLs are blocked by default unless
  `EGRESS_ALLOW_HTTP_URLS=true`;
- HTTPS non-search URLs are allowed as CONNECT tunnels;
- if `EGRESS_UPSTREAM_PROXY_URL` is set, forwarded HTTPS requests and
  explicitly allowed HTTP requests use that upstream proxy;
- if `MYST_VPN_ENABLED=true` and no explicit proxy is set, they leave through
  the Mysterium namespace;
- if `MYST_VPN_ENABLED=false` and no explicit proxy is set, they leave through
  the Docker bridge.

When the HTTP-prefetch response is usable, CRW may return that response
directly and never call CDP/Obscura. CRW only escalates to Obscura when it
detects a blocked response, a thin/SPA shell, a JS-required page, or a local
403/error from the prefetch proxy. This means Obscura's browser-like TLS/HTTP
fingerprint, JavaScript environment, cookies, and `waitUntil` handling do not
apply to every `open_url` HTML page.

This is not a new HTTPS behavior. Non-search HTTPS URLs have long used HTTP
CONNECT through the prefetch proxy so CRW can perform its end-to-end TLS
prefetch and detect PDFs/content type without MITM. Plain HTTP URLs are now
blocked by default before this limitation can apply; if an operator sets
`EGRESS_ALLOW_HTTP_URLS=true`, the same "usable prefetch can be final"
property applies to those HTTP URLs too. The search-engine path is still
different: configured search-engine hosts receive local `403` responses and
therefore escalate to Obscura without a raw prefetch reaching the search
provider.

Do not describe the Firecrawl `open_url` path as "all HTML goes through
Obscura." The accurate statement is: `open_url` goes through CRW; configured
search pages and pages CRW decides need browser rendering go through
CDP/Obscura; usable non-search HTTPS responses, explicitly allowed HTTP
responses, and PDFs can bypass Obscura.

### CRW MIME Probe Uses GET

CRW's `FallbackRenderer` currently performs a full HTTP `GET` before the CDP
renderer to determine whether the response is a PDF or another content type.
That probe is not a `HEAD` request. For PDFs this lets CRW short-circuit into
its native PDF parser. This non-browser PDF path is currently necessary because
Obscura has no PDF-to-markdown conversion support and cannot act as the PDF
download transport for CRW's markdown extraction path. For ordinary web pages,
though, the same MIME probe can create a real double-hit:

```text
1. reqwest GET for MIME/content sniffing
2. browser/CDP navigation if CRW decides rendering is needed
```

The prefetch-blocking proxy removes this double-hit for configured search
engine hosts by returning a local `403` before the reqwest fetch leaves the
stack. It does not remove the behavior for normal non-search HTTPS pages, or
for plain HTTP pages when `EGRESS_ALLOW_HTTP_URLS=true`. Those pages may
receive only the preliminary reqwest `GET` when CRW considers the result
usable, or may receive both the reqwest `GET` and a browser navigation if CRW
escalates afterward.

The correct fix belongs upstream in CRW: when it only needs MIME/PDF
detection, it should prefer a bounded `HEAD` request where the server supports
it, falling back to a small ranged or capped `GET` only when necessary. That
would preserve PDF detection while avoiding the extra full-body `GET` before a
browser render.

An alternate upstream fix would be for Obscura to download PDF URLs in a form
CRW can hand to its PDF-to-markdown converter. If the browser path could
reliably transport PDFs back to CRW for markdown extraction, CRW would no
longer need a separate MIME-probe `HEAD`/`GET` before deciding whether to use
the browser renderer.

Until CRW changes that behavior, this wrapper accepts the limitation for
non-search pages and only blocks the high-risk search-engine prefetch path.

### Plain Text URLs Through CRW Markdown

The README-recommended `open_url` path asks CRW for `formats: ["markdown"]`.
That works well for normal HTML pages, but it is lossy for raw plaintext files
served over HTTP, such as `raw.githubusercontent.com` YAML, TOML, source code,
or shell scripts. CRW records the upstream MIME type, but the scrape
pipeline only bypasses HTML extraction for PDFs. Non-PDF bodies are stored in
`FetchResult.html` and then passed to `crw_extract::extract()` as `raw_html`
([`http_only.rs:273`](../reference_repos/crw/crates/crw-renderer/src/http_only.rs:273),
[`single.rs:264`](../reference_repos/crw/crates/crw-crawl/src/single.rs:264)).
When `markdown` is requested, CRW runs that string through
`markdown::html_to_markdown()`, which calls `htmd::convert()` as an
HTML-to-Markdown converter
([`lib.rs:459`](../reference_repos/crw/crates/crw-extract/src/lib.rs:459),
[`markdown.rs:5`](../reference_repos/crw/crates/crw-extract/src/markdown.rs:5)).
For a plaintext YAML file, that means the text is interpreted as HTML text
rather than preserved as a literal file, so line breaks and indentation can be
collapsed or normalized before Onyx receives the result.

CRW exposes output-format and extraction controls, but no option that changes
the `markdown` converter's whitespace behavior or tells CRW to preserve
`text/plain` as literal markdown. The relevant request fields are `formats`,
`onlyMainContent`, `includeTags`, `excludeTags`, `cssSelector`, `xpath`,
`renderJs`, `renderer`, `debug`, `parsers`, and related LLM/chunking settings
([`types.rs:151`](../reference_repos/crw/crates/crw-core/src/types.rs:151)).
The available formats are `markdown`, `html`, `rawHtml`, `plainText`, `links`,
`json`, `summary`, and `changeTracking`
([`types.rs:11`](../reference_repos/crw/crates/crw-core/src/types.rs:11)).
`rawHtml` would preserve the original bytes decoded as text when requested
([`lib.rs:704`](../reference_repos/crw/crates/crw-extract/src/lib.rs:704)),
but Onyx's Firecrawl content provider requests only `markdown` and
extracts `data.markdown` from the response. `plainText` is also generated by
an HTML plaintext extractor and explicitly collapses whitespace
([`plaintext.rs:3`](../reference_repos/crw/crates/crw-extract/src/plaintext.rs:3)),
so it is not a safe workaround for indentation-sensitive files.

This flattening happens before Onyx's `open_url` response formatting. Onyx
passes the returned `data.markdown` through as `WebContent.full_content`
([`firecrawl.py:116`](../reference_repos/onyx/backend/onyx/tools/tool_implementations/open_url/firecrawl.py:116))
and later JSON-serializes the content without intentionally stripping
newlines
([`open_url_tool.py:378`](../reference_repos/onyx/backend/onyx/tools/tool_implementations/open_url/open_url_tool.py:378)).
Fixes would need to happen either in CRW (for example, special-case
`text/plain`, `text/yaml`, `application/yaml`, source-code MIME types, or raw
GitHub URLs before HTML-to-Markdown conversion) or in the Onyx/Firecrawl shim
by requesting and preferring `rawHtml` for raw-file URLs.

### Fingerprint Stability

#### Stability Limit

Obscura selects a realistic browser profile from a built-in pool for
each `BrowserContext`, and the default is a single stable profile rather than
rotation. That keeps the User-Agent, `navigator.platform`,
`navigator.userAgentData`, and the declared browser family internally
consistent. `--stealth` also uses the stealth HTTP client so navigation and
scripted fetch/XHR traffic share a browser-like TLS/HTTP fingerprint.

Some JS-visible values are initialized from a per-page seed in
`bootstrap.js` during `__obscura_init()`:

```javascript
_fpSeed = Date.now() ^ (Math.random() * 0xFFFFFFFF >>> 0);
```

That seed influences values such as screen dimensions, `hardwareConcurrency`,
`deviceMemory`, performance memory, canvas output, and WebGL debug renderer
strings, so the stack should not promise perfectly stable cross-navigation
device identity.

#### Cookie interaction

In the normal wrapper path CRW creates pages on obscura's default CDP context,
so cookies can remain in the in-process cookie jar across WebSocket
connections. The shim periodically clears them. The compose default avoids
CRW's `REQUEST_PROXY` / `Target.createBrowserContext` path because Obscura
clears the default cookie jar on context create/dispose, which weakens session
continuity.

#### What is stable by default

| Signal | Stable? | Source |
|--------|---------|--------|
| User-Agent | Yes | Stable `BrowserContext` profile unless `OBSCURA_ROTATE_PROFILE=1` |
| navigator.platform | Yes | Stable `BrowserContext` profile |
| navigator.userAgentData | Yes | Derived from the selected profile |
| TLS/HTTP fingerprint | Mostly | Stealth client presents a browser-like Chrome fingerprint |
| Timezone | Yes | Process `TZ`; default `Europe/Berlin`, override with `OBSCURA_TIMEZONE` |
| Cookies | Usually | Default CDP context jar, cleared periodically by the shim |
| Screen / canvas / hardware values | Not fully | Re-seeded during page initialization |

#### What does not reliably persist

| Storage vector | Persists across navigations? | Why |
|---|---|---|
| Cookies | Usually | Default CDP context jar, unless per-request context create/dispose clears it |
| localStorage | Only for storage-dir-backed contexts | `--storage-dir` supports it, but CRW's default CDP path should not rely on it |
| sessionStorage | No | Page/runtime scoped |
| IndexedDB | No | Stub only — nothing is ever stored |
| Cache API | Not implemented | No `caches` global |
| Service Workers | Stub only | `register()` returns empty promise |
| HTTP cache | Not implemented | No ETag/304 handling |

#### Mitigation

The `OBSCURA_BROWSER_CLEAR_COOKIES_INTERVAL` setting (default: 3600s = 60 minutes)
periodically clears cookies to limit the tracking surface.

The most impactful anti-bot measures in place are:
1. **STEALTH_JS stripping** — lets Obscura's own fingerprint model own the
   browser surfaces instead of combining it with CRW's injected script.
2. **Obscura stealth mode** — browser-like TLS/HTTP fingerprinting, stable
   browser profile by default, and tracker blocking.
3. **CRW per-host rate limiting** — 3s interval between requests to the same
   engine.
4. **SearXNG engine suspension** — 180s cooldown on 429.
