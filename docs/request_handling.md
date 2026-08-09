# Request handling

## Optional Tor route

Native Tor egress changes the selected final route. The public and host
policy proxies connect through a private Unix SOCKS volume and delegate
ordinary target-name resolution to Tor. Private-target policy, redirects,
limits, browser lifecycle, and search-engine selection are unchanged.
Internal/host/opt-in LAN exceptions remain direct. The one URL-policy addition
is that `http://` is accepted when the normalized host ends in `.onion` and
native Tor egress is selected. This does not enable clearnet HTTP.

For Tor and every configured remote-DNS upstream, ordinary target names are
never looked up by Docker, system, or Myst DNS, and returned address metadata
is consumed only as protocol framing. It cannot be reused for a later direct
connection. A missing socket, failed circuit, or malformed SOCKS response
fails closed. The wrapper does not resolve or pre-validate onion names; it
passes the complete hostname to Tor, which remains authoritative for onion-name
validation and connection handling. Onion WebUI ingress is a separate inbound
role.

This document describes the wrapper-managed `web_search` and built-in Onyx
Web Crawler `open_url` paths. Search always uses the pinned Obscura browser.
`open_url` uses the stock Onyx crawler by default and has an explicit direct
Obscura mode. There is no CRW in either path.

## Runtime flows

```text
Onyx open_url (ONYX_AGENT_USE_OBSCURA_BROWSER=true)
  -> private_onyx_obscura shared client
  -> obscura-cdp-gateway (API-only control networks)
  -> Obscura
  -> obscura-egress-bridge
  -> public final-hop proxy
  -> default no-VPN route, optional Myst VPN, configured upstream proxy,
     or Myst plus that proxy

Onyx web_search
  -> searxng-service-gateway
  -> one custom offline SearXNG engine
  -> private_onyx_obscura shared client
  -> Obscura
  -> the same browser bridge and public final hop
```

SearXNG's exact standard-library multiprocessing resource-tracker helper does
not serve requests and therefore skips the application `sitecustomize` patch
set. The Granian parent and request workers continue to install all strict
offline-engine, reservation, and scoring patches. The match is limited to the
canonical resource-tracker command so an unrelated Python helper cannot evade
startup validation.

By default, with `ONYX_AGENT_USE_OBSCURA_BROWSER=false`, the `open_url` half is:

```text
Onyx open_url -> stock Onyx requests fetch -> onyx-public-egress-bridge
              -> public final-hop policy -> target
              -> local Playwright Chromium through the same bridge
                 only when the stock crawler selects its browser fallback
```

## `open_url` and previously indexed documents

`open_url` is a chat-time read tool. It does not ingest a requested URL, add
freshly crawled content to the vector index, participate in connector sync, or
replace the semantic `internal_search` path.

For each tool call in full mode, Onyx first normalizes every requested URL using
connector-owned rules and checks the ordered canonical-ID candidates returned
by that connector. This covers sources such as Google Drive where the pasted
URL can identify a file without identifying which canonical document form was
indexed. The first indexed candidate wins. When Web Search is enabled for the
conversation, Onyx then runs exact-ID chunk retrieval and a fresh crawler
request as failure-tolerant parallel siblings. After both finish, it prefers
the already-indexed representation for a matched URL and otherwise uses the
fresh crawl. The crawl therefore still occurs even when an indexed copy is
ultimately returned. A link-column lookup is a last resort only when ordinary
ID resolution and the crawl both fail. Access filters apply to indexed
retrieval.

Explicitly excluding Web Search for a conversation also disables live
`open_url` fetching. Full mode keeps `open_url` indexed-only and reports an
unavailable URL instead of crawling when neither exact-ID nor link-based
retrieval can serve it. Lite mode has no index, so Agent tool construction
omits `open_url` in this state. The stock and direct-Obscura crawler patches
are not invoked on either disabled-web path.

Lite mode has no usable document index. While Web Search is enabled, its
availability patch leaves the crawler sibling operational while the indexed
sibling fails into an empty result; it does not restore ingestion, RAG, or
indexed retrieval. When Web Search is explicitly excluded, Agent construction
omits `open_url` rather than exposing a tool that could only return the
disabled-web failure.

The limits apply at distinct boundaries:

| Setting | Fresh crawler result | Previously indexed result |
| --- | --- | --- |
| `ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB` | Stock PDF/HTML and Chromium rendered HTML; direct-Obscura main body and rendered DOM | No |
| `ONYX_OPEN_URL_MAX_CHARS_PER_URL` | Post-parse per-page character cap | No |
| `ONYX_OPEN_URL_MAX_TOTAL_CHARS` | Final LLM-facing output after merge | Yes |

The total character budget does not limit the index read itself. Onyx constructs
and emits the rich document response before applying that final LLM-facing
budget, so the model may receive less content than the UI document payload.

`ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB` is isolated from indexing. Compose passes
it only to `api_server`, and it is absent from the full-mode `background`
service. An oversized crawl can therefore fail while a matching previously
indexed copy still succeeds. It applies only to the built-in Onyx Web Crawler;
Firecrawl, Exa, and other selected external content providers own their limits.

The Makefile also uses the value to size retention floors in the shared Obscura
server. That affects browser memory provisioning shared with SearXNG, but
SearXNG keeps its independent fixed 20 MiB DOM limit and no Onyx indexing path
uses Obscura. If the configured `open_url` limit is below 20 MiB, the server
retention floor remains 20 MiB for search while the API client still enforces
the smaller configured limit on `open_url` results.

In direct Obscura mode, the API container and SearXNG import the same client
implementation from `browser/obscura_client`. SearXNG connects on
`obscura-control`; the API can
connect only through `obscura-cdp-gateway` on `onyx-obscura-control`. CDP is
not published on the host or attached to an Onyx data/backend network.

Obscura v0.2.0 gives every WebSocket connection its own browser context, HTTP
client, cookie jar, targets, headers, User-Agent state, OS thread, and V8
isolates. Direct `open_url` uses one fresh connection and target per navigation.
SearXNG instead gives each of its five providers one lazy connection and one
target retained together until the provider has been idle for one hour. Later
queries reuse that target, its native cookie jar, selected profile,
target-owned stealth HTTP client/connection pool, and target-scoped fingerprint
seed. Neither path issues `Network.clearBrowserCookies`.

The single Obscura process accepts at most 15 live WebSockets: ten direct
`open_url` attempts plus one retained connection for each of the five search
providers. This is the actual mixed Onyx maximum in one API process:
`open_url` has ten process-global browser permits, while the five SearXNG
provider owners retain at most one connection each. Connections above the cap
receive HTTP 503 instead of entering a server queue as a fail-closed guard
against a changed worker/process model or another unexpected CDP caller; normal
Onyx tool execution is expected to remain within the 15-slot composition.

In the stealth-feature build, upstream v0.2.0 accepts
`Network.setExtraHTTPHeaders` and `Network.setUserAgentOverride` but applies
them to the ordinary context HTTP client while navigation uses its separate
wreq client, so those overrides do not reach the wire. The wrapper does not
call either command; its isolation contract therefore covers the cookie jar,
targets, contexts, V8 state, and connection cleanup actually used by this
stack. Re-audit this upstream split before depending on either override.

The client uses flattened CDP messages over the pinned WebSocket transport so
it can own event correlation, retained-body streaming, deadlines, redaction,
and cleanup directly. Obscura assigns distinct identifiers to explicit target
attachments, and the tagged-image gate exercises Playwright 1.58's public
`new_cdp_session(page)` path. The raw transport remains the smaller exact
implementation of the wrapper's request contracts; it is not a workaround for
session-identifier reuse. Direct `open_url` preserves the exact
one-`Page.navigate` contract; SearXNG uses the separate two-document
transaction below.

## Obscura `open_url` one-navigation contract

For each accepted target the shared client:

1. validates the URL syntax and scheme without resolving the target;
2. creates one fresh target in the connection-isolated context, enables the
   required CDP domains, and registers
   event observation before navigation;
3. runs the caller's pre-navigation finalization guard;
4. sends exactly one raw `Page.navigate`;
5. waits for the matching main-frame completion event;
6. identifies the terminal main-frame Document request across redirects and
   JavaScript navigation, retaining its actual request id, status, headers,
   final frame URL, and challenge state;
7. obtains rendered DOM and, when Obscura retained it, that same navigation's
   response body;
8. closes every IO stream, target, and request-owned WebSocket on success or
   failure.

The client does not issue `HEAD`, `GET`, range, MIME-probe, CLI, normal
SearXNG HTTP-client, or retry requests. It does not reconnect after a CDP
failure. Redirects and browser subresources are part of the single browser
navigation; they are not wrapper refetches.

Search uses `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH` (default `networkidle2`) so
JavaScript result payloads have time to hydrate after the page load event.
Built-in `open_url` uses `OBSCURA_BROWSER_WAIT_UNTIL_WEB` (default
`domcontentloaded`). Accepted values are the finite Obscura lifecycle values
validated by the shared client. These are event conditions, not sleeps;
Obscura also has a finite navigation deadline. Lowering the search wait to
`load` or `domcontentloaded` can capture DuckDuckGo No-AI's shell before its
organic rows exist; `networkidle0` waits more strictly and can add substantial
latency without proving that application JavaScript has committed its result
DOM.

A search interaction may therefore declare paired pending and terminal DOM
selectors. After the initial bounded result-DOM capture, the shared client
waits only when the pending selector exists and no terminal selector exists.
It polls on the provider's existing event-loop task until a terminal selector
appears, the pending selector disappears, or the existing absolute browser
transaction deadline is spent. There is no separate readiness timeout. A
terminal observation causes one new bounded DOM capture; spending the browser
deadline returns the already-captured pending DOM so provider parsing and
outcome recording still receive their reserved engine-window headroom.

The wrapper raises Obscura's populated-page ES-module active-work budget from
three to ten seconds. DuckDuckGo's result module graph can exceed the upstream
default over Tor even while the complete page remains within Obscura's
30-second script deadline and the caller's 50-second attempt deadline. This is
not an added sleep: fast module graphs return immediately, and all enclosing
deadlines remain authoritative.

Connection, target creation, attachment, and CDP domain setup share one
absolute 45-second pre-navigation deadline. It is not a fresh 45-second
allowance for each command. Expiry closes the connection, returns a typed
`pre-navigation-timeout`, and leaves headroom beneath SearXNG's 60-second
engine deadline and Onyx's 120-second invocation deadline. Cleanup CDP commands
have a separate five-second bound so an unresponsive renderer cannot retain a
caller permit merely by blocking target or body-stream closure. Connection
isolation is a browser-state boundary between requests, not an authorization
boundary between the API and SearXNG callers that can both reach CDP.

## Body and content handling

Rendered HTML comes from `DOM.getOuterHTML`. The terminal main-resource body
comes from `Fetch.takeResponseBodyAsStream` followed by bounded `IO.read` and
an unconditional `IO.close`. Plain and base64 chunks are counted as actual
bytes. The client closes an oversized stream immediately and returns a typed
failure; it does not continue consuming attacker-controlled bytes.

The pinned server's per-page response cache evicts the oldest entry after
`OBSCURA_NETWORK_BODY_BUFFER_ENTRIES` (fixed at 16 here), but it does not
protect the main Document entry. It creates the loader-id alias only after
navigation and network collection complete. On a page with enough subresources,
the main body can therefore be evicted before the alias is created and before
any CDP client can call
`Fetch.takeResponseBodyAsStream`; moving the client call earlier cannot repair
this ordering.

The shared client recognizes Obscura's exact `no cached body` rejection as a
typed `body-unavailable` result. For an HTML/XHTML response requested with both
DOM and body, Onyx preserves the rendered DOM from that same navigation and
logs `body_state=unavailable`; HTML processing never requires response-byte
identity and this is not a refetch or fallback navigation. PDF, raw text, and
other binary paths still fail closed when the retained body is unavailable.
Do not raise the response-entry count as a workaround: the byte limit is per
entry, so doing so multiplies worst-case retained memory. Re-audit and remove
this narrow HTML-only workaround when an Obscura upgrade protects or separately
retains the main Document body.

Challenge classification uses terminal HTTP status, terminal route, and a
bounded structural parse of at most 256 KiB of rendered HTML. HTTP 401/402/403
and 429 remain authoritative denial/rate-limit signals. CAPTCHA classification
requires a challenge route or title, a strong visible verification phrase, or
visible CAPTCHA text paired with challenge form/iframe structure. Script,
style, template, and noscript text is excluded, and an embedded CAPTCHA iframe
or library alone is not a challenge. Diagnostics report only the sanitized
signal name, never matching page text or markup.

The built-in crawler dispatches the same-navigation result as follows:

- PDF is recognized by terminal URL, MIME type, or PDF magic and parsed with
  the pinned Onyx in-process PDF extractor.
- HTML uses the rendered DOM and the pinned Onyx HTML-to-content path.
- Raw text is accepted only for the explicit MIME, extension, and charset
  allowlists in `obscura_crawler_patch.py`.
- Unsupported, conflicting, misleading, or incorrectly decoded content fails
  visibly. There is no generic-parser or remote-provider fallback.

Binary response bodies are byte-accounted. Text-classified bodies are always
marked potentially lossy: equal encoded and retained byte counts do not make
them byte-identical, and rendered DOM is never described as response-byte
identical. Declared non-UTF-8 text must satisfy the strict permitted decoding
contract.

`ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB` defaults to 20 MiB and is a positive
integer. In direct Obscura mode the same value independently caps the retained
main-resource response body, including HTML, and serialized rendered DOM for
the fresh crawler sibling. It drives the shared Obscura retention floors but
does not cap exact-ID reads of previously indexed documents or configure RAG
ingestion. SearXNG search DOM retains its independent fixed 20 MiB cap.
Existing Onyx character budgets apply after parsing. Increasing the document
limit increases potential memory use across ten simultaneous direct API
fetches and concurrent search connections.

These limits do not bound Obscura's initial response or DOM allocation. The
pinned server can read a complete response before applying retained-body
limits. Its network retention limit is per body and may be amplified by entry
count, base64 representation, and request/loader aliases. Every isolated
connection has one IO stream slot sized to the retention floor because the
wrapper opens at most one stream on that connection. Neither limit is an
aggregate browser process-memory bound. In-process PDF parsing also has no
complete CPU, transient-memory, or parser wall-time bound beyond the outer
invocation.

## Onyx `open_url`: Obscura mode

The tool definition tells the agent that one `open_url` call accepts at most
ten URLs and includes the same `maxItems: 10` machine-readable constraint.
Pinned Onyx otherwise logs and silently truncates a larger deduplicated list.
The wrapper instead rejects the entire over-limit call before retrieval or
navigation and returns model-visible guidance to split the URLs across
additional calls; it does not hide which inputs were omitted.

The API-only startup patch strictly replaces the built-in `OnyxWebCrawler`
fetch path. Patch source-shape drift is startup-fatal. A crawler invocation
creates one absolute 120-second monotonic deadline and finalization object
before outer parallel work begins. All nested crawler jobs receive that same
state.

Ten process-global permits bound active direct-Obscura API fetches. A job may
wait for a permit only for its remaining invocation budget. It checks
finalization before setup and again immediately before `Page.navigate`;
finalized work cannot send a new origin request. A navigation already sent
retains its permit through cleanup. Admission is process-local and non-FIFO,
and waiting caller threads are not themselves a durable queue or a
cross-process capacity reservation.
The shared 45-second setup bound means a connection blocked before navigation
releases its caller-side permit within that budget. The client does not retry;
closing the connection tears down its isolated server-side context.

Results remain ordered by requested URL. Failures and snippets are correlated
with that requested URL even after redirects; successful content and citations
use the terminal URL. Cardinality mismatches fail loudly. Individual transport,
protocol, retained-body, status, challenge, type, parse, limit, and timeout
failures become the normal unsuccessful `WebContent` result without hiding the
typed reason or discarding other successful URLs. Every typed shared-client
failure is warning-level and carries its opaque request ID and sanitized stage
so production log levels do not hide protocol failures.

For a mixed result, successful documents and citations remain the primary tool
response and a trailing `Partial open_url failure report` lists each final
post-fallback failed URL with its sanitized reason. This closes an upstream
presentation gap where per-URL reasons were emitted only when every URL failed.
Timeout-only and all-failure responses keep their normal upstream form, and
denial/challenge response bodies are never passed to the model.

Each Onyx browser attempt has a 105-second absolute bound from connection start;
each SearXNG attempt has a 50-second bound beneath its 60-second engine timeout.
Navigation, the completion event, DOM commands, retained-body stream creation,
and every stream read use only the remaining attempt budget and report the exact
sanitized expiry stage. Cleanup remains independently bounded.

Onyx stops collecting per-URL work five seconds before its 120-second outer
deadline. It preserves completed results in requested-URL order, represents
unfinished work as explicit per-URL failures, finalizes the shared invocation
so queued workers cannot begin a late navigation, and returns without waiting
for an orphaned thread. It neither retries nor cancels a navigation already
sent; that background attempt is instead bounded by its own absolute deadline
and must release its browser permit during cleanup.

Operators must select **Onyx Web Crawler** in the Web Search Admin page to use
this path. The wrapper does not rewrite saved provider rows or enforce a
provider choice at startup. Deliberately selecting Firecrawl, Exa, or another
external Onyx content provider remains supported through Onyx's public egress
route, but it is outside the one-navigation guarantee: that provider owns the
subsequent target fetch and its current retention, training, and zero-data-
retention policy. Its API key also associates activity with the operator's
account.

Full-mode doc-drop ingestion, local embedding, and `internal_search` do not use
Obscura and are unchanged.

## Onyx `open_url`: default stock crawler mode

`ONYX_AGENT_USE_OBSCURA_BROWSER` accepts exactly `true` or `false` and defaults
to `false`; any other value is startup-fatal. When it is `false`, the API patch
does not install the direct Obscura crawler replacement. It retains the pinned
upstream `OnyxWebCrawler`, including its requests-first behavior and its local
Playwright Chromium fallback for qualifying 403/challenge responses. One URL
can consequently produce a requests fetch followed by a second browser
navigation. Upstream request, browser, parser, timeout, and failure semantics
otherwise apply. The wrapper makes `ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB`
authoritative for both the requests-fetched PDF/HTML byte checks and the UTF-8
byte size of local Chromium's rendered HTML. `OBSCURA_BROWSER_WAIT_UNTIL_WEB`
does not configure this mode. Wrapper character budgets and mixed-result
failure reporting remain installed.

The stock size checks occur after materialization: requests has already loaded
the final response body, and Playwright has already constructed
`page.content()`. The limit prevents oversized content from reaching parsing
and the LLM, but it is not a transport download cap or complete peak-memory
bound. Apart from this unified document limit, the built-in stock crawler has
no other byte-size cap. The post-parse per-URL and aggregate character budgets
described above still apply. An explicitly selected external content provider
retains its own limits.

The stock transport is still wrapper-constrained. A narrow startup-validated
adapter replaces only this crawler's imported `ssrf_safe_get`: it validates
the initial URL and every redirect structurally as public-only, never performs
target DNS in the API container, creates a requests session with
`trust_env=False`, ignores the Onyx Admin private-network setting for this
LLM-controlled path, and supplies the exact public bridge proxy explicitly.
The crawler's Playwright fallback receives the same scoped structural check.
When the Tor egress layer is selected, that structural check accepts `http://`
for any host ending in `.onion`; every initial URL and redirect still reaches
the final-hop policy, which independently requires its fixed Tor Unix socket.
All Onyx Playwright launches use an explicit proxy bypass value of
`<-loopback>`, which disables Chromium's normal implicit loopback exception;
the initial navigation, browser redirects, and subresources must therefore
cross the selected fixed bridge. These measures prevent `NO_PROXY`, Admin
SSRF settings, or a localhost redirect from changing the route class.

The final-hop public policy remains authoritative for target DNS, complete
answer-set classification, private/special-address denial, and the selected
VPN/upstream/no-VPN route. A proxy or route failure stays closed. In configured
remote-DNS upstream mode, the same documented residual applies: the wrapper
cannot inspect the upstream proxy's answer and relies on it to reject private
resolution. Local Chromium runs inside `api_server`, rather than the hardened
Obscura container, and shares that service's process/filesystem trust domain;
this default is a reliability-over-containment tradeoff, not equivalent browser
containment. Operators can select the more isolated Obscura mode explicitly.

This preference never changes `web_search`. The custom SearXNG engines remain
direct-Obscura clients with their one-navigation, scheduling, and failure
contracts.

The stock crawler remains the default reliability-oriented path. This is not a
claim that requests/Chromium is universally less detectable. On each Obscura
upgrade, repeat parallel blocked, empty-content, timeout, and successful-result
tests across the same URL set and reconsider the default when the direct path
meets the same reliability bar.

## SearXNG search

The derived search service uses SearXNG `2026.7.15-7b2199ecd`. Its offline
processor, search orchestration, result container, timeout handling, and
exception contracts are startup-validated by the wrapper patch.

The wrapper configures an empty optional-plugin mapping. This JSON/diagnostic
deployment does not use SearXNG's calculator, hash, hostname, UI-helper, or
tracker-remover plugins, so they are not imported or initialized. In
particular, the tracker remover's init hook otherwise downloads a mutable
ClearURLs rule list from unrelated Internet hosts on each fresh cache,
producing avoidable startup traffic, delay, and failure logs even when its
`active` preference is false. The custom engines continue to unwrap their
provider-owned Google, DuckDuckGo, Startpage, and Bing redirect formats before
returning results; omitting optional plugins does not add a search navigation
or change final-hop routing.

The settings loader also keeps none of SearXNG's inherited default engines and
then adds only the five wrapper engines below. This is stronger than marking
stock engines disabled: unused engines are never imported or initialized, so
they cannot perform startup DNS/network work or retain engine-specific state.

`google2`, `brave2`, `duckduckgo2`, `startpage2`, and `bing2` are custom
offline engines. Each begins at its declared provider homepage, validates the
completed homepage document, populates that page's declared query control and
fixed fields, calls the owning form's native `requestSubmit()`, validates a
distinct completed result document, and passes only the bounded result DOM to
its parser. Bing alone performs one additional homepage/form/result transaction
when a successfully parsed first page contains fewer than five valid organic
results; it requests page two in the same provider lease and retained browser
session, merges exact-URL-unique results, and returns at most ten. This is
bounded pagination after a successful parse, not a replay of a failed
transaction. There is no constructed result URL, direct-navigation
compatibility mode, failure retry, or fallback. The shared client owns form/origin
policy plus bounded generic HTTP and challenge classification; provider parsers
retain only provider-specific DOM checks. Generic detection
ignores script, style, template, and noscript text and does not classify an
embedded CAPTCHA iframe by itself, avoiding the prior Brave false positive.
Challenge-route markers are matched only against the decoded terminal path,
never the result query string, so a user query containing a path such as
`/challenge/` cannot suspend a provider.
Explicit provider no-results selectors return no results; missing expected
structure is an unresponsive parser mismatch. The engines cannot call
SearXNG's normal HTTP transport, retry internally, or choose another provider.

The provider forms receive these explicit engine-owned values in addition to
their own successful controls: Google receives `hl=en`, `udm=14`, and optional
`start`/`tbs`; Brave receives optional `tf`/`offset`; DuckDuckGo receives
`ia=web`; Startpage receives `cat=web` and optional `page`; and Bing receives
`adlt=off`, `setlang=en`, and optional `first`. Existing provider-generated
hidden state remains in the form. Unknown, duplicate, or conflicting explicit
fields fail visibly.

Provider result URLs retain their complete query strings and fragments.
`duckduckgo2` starts at `https://noai.duckduckgo.com/`, submits its homepage
form, and parses organic `data-layout` / `data-testid` rows rather than
generated class names. Obscura's network-idle lifecycle can complete after a
successful `d.js` response but before DuckDuckGo commits those rows. While the
`deep_preload` marker is present without organic, no-results, or explicit
challenge structure, the shared client therefore waits through the remaining
browser transaction window described below. The marker remains present after
successful hydration and is only a pending-state gate, never proof that
hydration is still incomplete by itself.
DuckDuckGo's `/l/` `uddg` result wrapper is unwrapped only on a relative link
or a recognized DuckDuckGo host and is decoded exactly once by query parsing;
the resulting URL is not decoded again, because its remaining escapes can be
meaningful nested URL values, signatures, or encoded separators. SearXNG's
optional tracker-remover plugin remains disabled and does not rewrite results.
DuckDuckGo's form-based and rendered `anomaly-modal__modal` verification pages
are typed CAPTCHA failures and enter the ordinary one-hour blocking suspension.
An unfinished `deep_preload_link` without one of those explicit challenge
markers is instead a parser/runtime failure: incomplete JavaScript hydration
must not suspend the provider as though an IP challenge had been proved.

Startpage's explicit CAPTCHA route and its Anubis `sp-message` page containing
the visible `Verifying your request` state plus the same-origin Anubis module
marker are typed CAPTCHA failures. During the v0.2.0 audit Startpage served
Anubis v1.25.0's `fast` algorithm at difficulty 5: its main ES module creates
four same-origin classic workers from direct `.mjs` URLs under Obscura's
advertised hardware profile. Obscura's current `Worker` compatibility shim
executes those worker bodies cooperatively in the page's V8 isolate rather
than in dedicated isolates. A live wait through the existing 50-second browser
transaction budget starved the page-runtime CDP inspection and expired at
`result-readiness`; adding an Anubis pending selector is therefore not a
working continuation.

The live v1.25.0 flow does not create blob workers, but newer Anubis releases
prefetch one worker source and create workers from a blob URL. Forward-compatible
browser execution consequently requires both direct and blob-backed dedicated
workers. It also requires a bounded multi-document continuation: successful
proof submission navigates through the same-origin Anubis pass endpoint and a
redirect, while the original Startpage search is a POST whose body is not
preserved by that GET redirect. The current client owns neither that
continuation nor an authorized one-time form resubmission. It returns the
captured verification DOM to the provider parser, which records the explicit
challenge through the ordinary CAPTCHA suspension path; it does not treat the
page as result-DOM drift, wait on a separate allowance, construct a result URL,
or retry the failed transaction. The deferred implementation alternatives and
their ownership constraints are recorded in
[`docs/plans/deferred/anubis_pow_support.md`](plans/deferred/anubis_pow_support.md).

Bing's rendered `One last step` / `solve the challenge below to continue`
interstitial is a CAPTCHA failure, even when it contains none of Bing's older
challenge selectors. Detection uses visible body text only; matching text in
script, style, template, or noscript content does not block a valid results
page. This typed failure enters SearXNG's ordinary provider-suspension path
instead of being reported as a parser crash.

Bing currently serves both `input[type=search]` and `textarea` query controls,
so its exact selector admits either while still requiring exactly one enabled,
visible `q` control owned by the declared HTTPS `/search` GET form. Some Bing
homepage variants omit the search form entirely after rendering; those remain
visible parser/protocol failures and are never replaced with a constructed
result URL or same-engine retry.

Bing can also return a coherent but unrelated set of ordinary `b_algo` cards
while leaving the requested query in the page title and search box. This has no
distinct challenge or dictionary-widget DOM shape. The engine therefore
requires at least one of up to eight of the query's longest non-numeric terms
of four or more characters to appear literally in the extracted titles,
snippets, or URLs. A mismatch is an unresponsive parser failure, not an empty
result, so round-robin search can try another provider without exposing the
unrelated cards. Queries without a term long enough for a reliable lexical
check retain the structural parser contract.

Bing dictionary and answer results remain excluded from this research-oriented
engine. The filter covers Bing's widget metadata and organic result attribution
labels containing `dictionary`; ordinary non-dictionary organic results remain
eligible for the query-coherence check. If those filters and the ordinary URL,
title, and query-coherence checks leave zero through four first-page results,
the engine submits page two with `first=11`. Page two uses the same strict
challenge, structure, and coherence checks; a page-two block, timeout, or parser
failure is not hidden by returning the partial first page. Exact duplicate URLs
are removed while preserving page order, and the combined result is capped at
ten. Five or more first-page results, and explicit requests for pages after the
first, perform no automatic extra pagination.

One Granian request worker owns scheduling and all five provider-session
owners. `GRANIAN_WORKERS=1` is therefore part of the capacity and serialization
contract, not only a resource preference. Each provider has one atomic
pre-thread reservation, one active lease, and an exact 3.0-second minimum
interval between navigation starts, with zero jitter. The engine thread must
consume its exact reservation token. Busy and cooling providers are skipped
before an engine thread is created. If every eligible regular provider is
active, reserved, or cooling, the request waits for provider capacity instead
of returning an empty result or spilling into Bing. Provider release uses a
condition notification; cooldown-only waits use the nearest exact monotonic
deadline, so there is no polling sleep or periodic scheduler wakeup.

The SearXNG offline processor retains the provider lease across the complete
engine call: CDP setup, homepage navigation, text entry, form submission,
result navigation, local cleanup, provider-specific DOM parsing,
blocking-response classification, and suspension recording. This
ordering is required because concurrent search requests can otherwise reserve
a provider in the interval after CDP cleanup releases it but before SearXNG
records a CAPTCHA, access denial, or 429 suspension. Provider admission,
round-robin rotation, and suspension remain SearXNG responsibilities. The
shared CDP client accepts only a caller-supplied pre-navigation guard and owns
no provider names, reservations, cooldowns, suspension state, or rotation.

One lazy process-local event-loop thread owns all five independent provider
connection/target generations. Each exact provider retains at most one
WebSocket and one target, and its existing
lease is the sole same-provider serialization authority. Different providers
can navigate concurrently on the shared loop, so concurrent agent search calls
do not become process-global serialization. After each browser transaction
that leaves its generation reusable, the provider owner arms one monotonic
one-hour idle deadline before provider-specific parsing and SearXNG outcome
recording finish. Another query before that deadline reuses the same target,
native cookie jar, selected browser profile, target-owned stealth HTTP client
and pool, and target-scoped fingerprint bundle, then moves the idle deadline.
A new main-document navigation creates a fresh JavaScript realm, but the
source-built Obscura patch injects the same unpredictable target seed into each
realm. Native form POST uses that same target stealth client as GET, including
its cookies, proxy, TLS-emulation profile, redirect handling, and pool. Natural
upstream/library idle socket expiry is allowed; the retained client may open a
replacement socket without rotating browser state.

No query by the deadline physically closes the target followed by its
connection. A later query creates a fresh generation. The owner checks an
elapsed deadline synchronously, so delayed timer scheduling cannot revive an
expired session. This is a sliding idle lifetime, not an absolute maximum
session age. Transport, protocol, submission acknowledgement, event-accounting,
DOM, or cleanup ambiguity discards the generation earlier and never replays the
failed query. An owner whose ambiguous generation was already closed is removed
immediately; it is not reported as reused and does not receive a redundant idle
close.

Each ordinary search attempt has exactly two main-document stages. Bing's
successful sparse-first-page path has two such transactions and therefore at
most four stages: homepage and result for page one, then homepage and result
for page two. Redirects and subresources remain within their stage. Every
homepage and result independently
receive HTTPS/default-port/exact-host, lifecycle, status, challenge, and
20 MiB rendered-DOM validation. The homepage DOM is released before result
extraction and never reaches a provider parser. Form policy requires one
visible editable main-frame control, its owning self-targeted URL-encoded form,
the declared action path/method, and an allowed result origin. Popups, child
frames, same-document/XHR-only results, and a missing distinct result loader
fail visibly. A missing or empty form `enctype` is normalized only to HTML's
standard `application/x-www-form-urlencoded` default; any explicit different
encoding remains a policy failure.

Instant query entry is the default. It uses the control prototype's native
value setter followed by one bubbling `input` and `change` event.
`SEARXNG_TIMED_TYPING_PROVIDERS` accepts exact provider names, `none`, or `all`
and selects timed CDP key events without changing any navigation or submission
path. Timed entry adds 45–135 ms after each code point except the last and can
disclose successive query prefixes to that provider through autocomplete. It
is an experimental request-shape option, not an anti-bot guarantee.

The wrapper owns the blocking suspension values rather than inheriting them
from SearXNG: CAPTCHA/verification, access denial, and HTTP 429 each suspend a
provider for 3,600 seconds. The reusable browser generation's equal 3,600-second
idle clock starts first, when the browser transaction returns; the offline
processor then classifies the returned DOM, records the blocking suspension,
and releases the provider lease. Its browser deadline therefore precedes its
unsuspension deadline by the parser/outcome-recording interval. This ordering
is deliberate: a provider becoming eligible again must receive a fresh target,
fingerprint bundle, cookie jar, stealth client, and connection generation
instead of resuming the state that encountered the block. A delayed timer
callback cannot violate that property because admission checks the monotonic
deadline synchronously and closes an expired generation before navigation.
Transport or protocol ambiguity closes the generation immediately instead of
arming the idle clock. Session state does not otherwise change suspension,
provider selection, cooldown, or scoring.

With `SEARXNG_ROUND_ROBIN=true` (default), the existing orchestration selects
one available normal custom provider and removes the other selected engines
from that attempt. A completed zero-result attempt or an unresponsive attempt
with no main results advances sequentially to another normal provider. A
normal provider that is merely active, reserved, or cooling remains eligible
and prevents selection of a last-resort provider; the request waits rather than
spilling into Bing merely because concurrent searches occupy the normal
providers. Pre-execution unavailability reporting follows the same eligibility
rule, so neither occupied regular providers nor an ineligible Bing are recorded
as rate-limited in engine statistics.
Bing becomes eligible only after every selected non-suspended normal provider
has failed in that request or the remaining normal providers are already
suspended. Last-resort scoring is retained. Blocking conditions flow through
the ordinary offline processor and suspend the provider. SearXNG owns caller
timeouts, late results, unresponsive-engine reporting, and suspension state.

Next-provider rotation is the defined round-robin scheduling behavior. It never
selects the same provider twice within one SearXNG search. Bing's bounded
page-two fetch remains inside its one selected engine attempt and is not a
second selection. The provider name
enters the request-local attempted set before its engine thread runs, regardless
of whether failure occurs during connection setup, navigation, response
classification, or parsing. CAPTCHA, access-denied, and 429 attempts can
therefore advance only to a different eligible provider; they are never
replayed against the blocked provider.

The timeout layers are nested or sequential as follows; none is a remainder
carried from one provider rotation into another:

- Provider reservation, active-lease, and the initial exact three-second
  cooldown wait happen before the selected provider's engine clock starts.
  They can extend the SearXNG HTTP request but do not consume a partial provider
  budget. Bing's second homepage navigation is different: its pre-navigation
  guard enforces the same interval inside the already-running engine window.
- Every selected provider receives a fresh SearXNG 60-second engine window.
  Rotation never gives a later provider the earlier provider's remainder. The
  custom offline engines' explicit `timeout: 60.0` entries are authoritative;
  the general `outgoing.request_timeout: 6.0` applies to SearXNG network
  clients, not these browser-owned offline engines. Onyx sends no
  `timeout_limit`, and the deployment does not set `max_request_timeout`.
- Inside that 60-second window, each browser transaction has a nominal
  50-second absolute deadline, capped to the remaining engine window minus one
  second of outcome-processing headroom. Connection/target setup, homepage
  navigation, entry events and optional 45–135 ms timed-entry delays, form
  submission, result navigation, completion events, the homepage and initial
  result DOM reads, and any declared terminal-DOM readiness wait and recapture
  consume that transaction's deadline. No browser stage receives a fresh
  deadline or fixed readiness allowance.
  Bing's page-two transaction shares the original 60-second engine window; it
  does not receive a fresh engine budget, and its deadline is only the
  remaining bounded portion.
- Browser setup has a 45-second absolute sub-deadline within the 50 seconds.
  WebSocket opening is further limited to the smaller of ten seconds and the
  remaining setup time. These are ceilings within the browser budget, not
  additional time.
- Cleanup is deliberately outside the browser transaction deadline so an
  ambiguous generation can still fail closed after its 50 seconds are spent.
  Target closure has a five-second command bound and WebSocket closure has its
  own five-second close bound; in the worst ambiguous path they can run
  sequentially and bring the engine thread close to or beyond SearXNG's
  60-second wait. A successful reusable transaction performs neither immediate
  close. The Obscura server's configured 90-second navigation and 120-second
  command ceilings are lower-level safety bounds; the client's remaining
  50-second deadline normally expires first and they add no time.
- Provider parsing, challenge classification, and suspension recording use
  whatever remains of SearXNG's 60 seconds after browser work. SearXNG's
  timeout is a waiting boundary, not thread cancellation: it marks the engine
  unresponsive and may rotate after 60 seconds, while the late engine thread
  continues cleanup/outcome processing and retains its provider lease until it
  actually finishes.

A request that sequentially reaches all five distinct providers therefore has
up to 300 seconds of nominal provider execution windows, plus admission waits.
There is intentionally no separate whole-SearXNG-request deadline in this
wrapper. The one-hour browser idle lifetime and blocking suspension are state
lifetime/readmission controls, not execution budgets.

Onyx merges multiple `web_search` tool calls in one tool batch into one call,
then executes every query in the merged `queries` array concurrently; the
pinned schema has no query-count cap. It can also execute that merged search
beside a merged `open_url` call. Requests beyond currently available search
providers therefore wait in SearXNG admission, while direct `open_url` remains
independently bounded by its ten process-global browser permits.

The API bootstrap removes the pinned Onyx `SearXNGClient.search` retry wrapper.
Each query therefore submits the SearXNG `/search` HTTP request exactly once,
while SearXNG alone owns any sequential next-provider rotation inside that
request. This patch is scoped to `web_search`; it does not unwrap or otherwise
change built-in `open_url` crawler retries or transport recovery. The pinned
Onyx call uses no `requests.post` timeout. Its ten-minute tool-runner timeout is
an outer soft wait over the concurrently executed tool batch, not a second
search retry, provider budget, or provider scheduling authority. On timeout
Onyx stops awaiting unfinished worker results, but Python worker threads and
their in-flight SearXNG requests continue in the background; it is not hard
cancellation and does not release a SearXNG provider lease.

The API bootstrap also preserves each provider's complete result URL when
Onyx constructs `WebSearchResult`. The pinned model otherwise applies a
generic normalizer that removes every query string and fragment, breaking
query-addressed resources such as Hacker News items and YouTube videos. The
same query-blind behavior also affects crawled `WebContent`, crawl-result
merging, and the generic indexed-document fallback and can conflate distinct
resources. The patch therefore preserves query and fragment identity in the
generic utility and every already-imported model/merge alias. Connector-owned
canonical normalization remains authoritative for known document systems;
the generic fallback retains scheme, lower-case host, and trailing-slash
formatting without dropping query or fragment. The preserved URL is used for
the LLM-facing link, citation source, crawl matching, and document identity.

The three-second provider cooldown begins in the pre-navigation guard
immediately before homepage `Page.navigate`, the first provider-origin request.
A WebSocket refusal, connection
failure, target creation/attachment failure, or CDP-domain setup failure before
that guard proves that no origin search request was sent and does not stamp the
cooldown. Bing's sparse-result pagination calls the same guard before its
second homepage navigation, waits out any remainder of the exact interval, and
stamps a new start time. There is still no automatic same-engine failure retry:
only a later,
independent SearXNG search may select that provider and open a replacement
generation. Once the guard has run, cooldown applies even if either document
stage or submission fails ambiguously, and neither the client nor scheduler
repeats that provider.

The Bing engine does not advertise SearXNG SafeSearch support and always sends
the explicit provider parameter `adlt=off`. It therefore has no engine-specific
SafeSearch control in Preferences and cannot inherit a stricter global
SafeSearch selection.

With round robin disabled, ordinary selected-engine fan-out returns. That can
disclose the same query concurrently to several providers and can leave one
late attempt per provider. `open_url`, generic helpers, and enabled executors
are intentionally outside the search-provider scheduler. Enabled executors
may reach public search engines through the shared public final-hop policy.
Direct-Obscura `open_url` retains its separate ten process-global permits and
fresh connection per navigation; it does not use the SearXNG provider loop or
provider leases.

## Routing, DNS, and failure behavior

The clients validate URL syntax but deliberately do not resolve public target
names. Browser target DNS is authoritative at the selected final hop: Myst
provider DNS in VPN mode, the configured upstream proxy when it owns remote
DNS, or explicit system DNS in no-VPN mode. Docker DNS resolves only internal
service names.

The public final-hop policy rejects canonical internal names and non-public
addresses with HTTP 403 for initial requests, redirects, and subresources when
the wrapper performs DNS and pins the approved addresses. In a deliberately selected
remote-DNS upstream-proxy mode, a malicious or misconfigured upstream can
resolve a public-looking name privately; the wrapper cannot claim address-
level private-target denial in that mode. Proxy-side NXDOMAIN, no-address, and
resolver failures return HTTP 502 rather than being mislabeled as policy
denials; Obscura exposes the resulting tunnel failure as a typed browser
transport failure. There is no direct network fallback
if Obscura, its bridge, the proxy, Myst, or an upstream proxy fails.

Wrapper-owned diagnostics omit query strings, request bodies, response
contents, cookies, credentials, and sensitive headers. They report only a
stage, typed category, status class, safe host where needed, and bounded sizes.
Each shared-client attempt has a random opaque correlation ID. Start, completed
setup, terminal result, typed failure, and cleanup records include that ID plus
elapsed time; timeout records identify the exact setup or cleanup stage without
logging the target URL. SearXNG keeps these sanitized information-level records
visible under its otherwise warning-oriented default logger and adds only the
engine name, provider-local query sequence, and whether the retained browser
session was reused.
The wrapper-selected image retains the digest-pinned upstream runtime but
builds the exact SHA-256-verified upstream source revision with the no-render
`stealth` feature set and the three strict wrapper patch-series entries, then
copies only the resulting server binaries into that runtime. This is required
for wreq/BoringSSL TLS fingerprint impersonation and for the target-scoped
fingerprint seed, stealth-native form POST, and focused provider JavaScript
runtime-compatibility contracts. The stack consumes
DOM and response-body CDP surfaces, not screenshots, screencasts, or PDF
export, so it does not compile the v0.2.0 raster renderer or incur its image,
font, layout, and capture resource work. JavaScript, DOM, module, charset, and
compressed-stealth-response improvements remain present in the no-render
build.
The patch removes the submitted URL and body from Obscura's JS-triggered
main-navigation diagnostics so search queries are not written there. Obscura
does not offer equivalent end-to-end redaction for every upstream subresource
diagnostic and may still log other full URLs. Treat its logs as private
browsing data.

Obscura is run as UID/GID 65534, read-only, capability-free, without browser
data or secret mounts, `--storage-dir`, or `--allow-file-access`. The shared
client rejects `file:` targets. These controls reduce impact but do not repair
the pinned upstream ES-module local-file-read path; Obscura remains a trusted
browser component and must not receive private mounts.

SearXNG provider continuity retains all native state accepted by that
connection, including first- and third-party cookies and HTTP-client state. It
does not export, import, inspect, count, filter, or persist cookies. State is
partitioned by provider and SearXNG process, not authenticated user or
conversation, and never crosses into `open_url`, connectors, or executors.
This is an intentional single-user deployment boundary.

## Diagnostics

Use the Makefile-selected mode and targeted logs:

```sh
make ps-lite                 # or make ps-full
docker logs searxng-core --since 10m
docker logs onyx-obscura-1 --since 10m
docker logs onyx-obscura-cdp-gateway-1 --since 10m
docker logs onyx-onyx-public-egress-proxy-1 --since 10m
docker logs onyx-api_server-1 --since 10m
```

Expected search failures include provider 429, access-denied, CAPTCHA, parser
mismatch, timeout, and suspension records. They must not become empty-success
substitutes. For `open_url`, verify the API startup log contains either
`installed strict direct Obscura crawler` (explicit Obscura mode) or
`installed proxied stock Onyx crawler with public-only requests and Playwright
fallback` (default stock mode). Lite mode uses Onyx's native crawl-only branch
and must additionally log `installed mixed-result open_url failure reporting`.
A missing gateway, selected bridge, or final hop must fail the feature closed
without moving an application onto a public network.

Relevant controls are documented in `.env.wrapper.example`:

- `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH` and
  `OBSCURA_BROWSER_WAIT_UNTIL_WEB`;
- `ONYX_AGENT_USE_OBSCURA_BROWSER`;
- `ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB`;
- `EGRESS_ALLOW_HTTP_URLS`;
- `SEARXNG_ROUND_ROBIN`;
- the VPN and upstream-proxy route settings.

Obscura connection capacity, direct API fetch concurrency, browser navigation
deadline, and the internal retention limits are fixed reviewed implementation
values, not user-facing tuning controls.
