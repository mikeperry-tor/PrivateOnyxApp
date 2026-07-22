# Direct Obscura Request Handling Plan

> **Status: implemented (2026-07-15).** The
> [Onyx application network isolation](onyx_network_isolation.md) prerequisite
> was implemented first. This is the implementation record for the atomic
> migration from CRW and the CDP shim to direct, single-navigation Obscura
> integrations. Normative runtime behavior is documented in
> [Request handling](../../request_handling.md),
> [VPN routing and restricted egress](../../vpn_routing_and_proxies.md), and
> [Internal network security](../../internal_network_security.md).
> Autoheal references below describe the historical baseline and are
> superseded by the socket-free Myst health supervisor documented in
> [VPN routing and restricted egress](../../vpn_routing_and_proxies.md).

### Implementation note

The pinned Obscura 0.1.10 server reuses its flattened session identifier when
Playwright 1.58 attaches a public page CDP session, causing Playwright's driver
to reject a duplicate target. The deployed shared client therefore uses the
pinned raw WebSocket CDP transport rather than `new_cdp_session(page)`. It
retains the reviewed one-navigation, event-barrier, actual-request body, typed
failure, and cleanup contracts. Playwright remains pinned and image-validated
for compatibility auditing; the derived SearXNG image contains no browser.

### Initial-testing correction ledger

This implemented plan is also the completion-audit record. During initial and
subsequent testing, every correction within this plan's scope must update this
ledger in the same change set as the code and the normative `docs/` files.
Ledger entries supersede an earlier design statement in this plan where they
conflict; reviewers should treat an unrecorded behavior correction as an
incomplete plan update.

- **2026-07-15 — resolver failures versus policy denials.** Final-hop CONNECT
  and absolute-form HTTP forwarding now return HTTP 502 for NXDOMAIN, empty
  answer sets, and resolver failures. HTTP 403 remains reserved for destination
  policy denials. Browser navigation tunnel failures caused by resolution or
  connection failure map to the typed transport result rather than a CDP
  protocol error. Deterministic proxy/client tests and a live `myp.wtf`
  NXDOMAIN attempt verified the distinction.
- **2026-07-15 — bounded pre-navigation setup and diagnostics.** One cumulative
  45-second client deadline now covers CDP connection, cookie clearing, target
  creation/attachment, domain enablement, and frame-tree setup. Cleanup CDP
  commands have a separate five-second bound. URL-free opaque correlation IDs,
  exact stages, typed categories, elapsed times, status classes, bounded sizes,
  challenge signals, and cleanup outcomes provide caller-side evidence despite
  the pinned multi-worker child-log gap. This correction prevents one deferred
  setup command from consuming Onyx's complete 120-second invocation and leaves
  headroom below a SearXNG engine's 60-second outer timeout.
- **2026-07-15 — CAPTCHA false positives from inactive page scripts.** The
  shared detector no longer searches raw HTML for the substring `captcha`.
  It parses at most 256 KiB, excludes script/style/template/noscript content,
  and requires a terminal challenge route, challenge title, strong visible
  verification phrase, or visible CAPTCHA prompt combined with challenge
  form/iframe structure. The SearXNG post-fetch detector no longer treats an
  iframe source alone as a blocking page; its title and challenge-form markers
  remain. Three live article pages that previously failed because only bundled
  scripts mentioned CAPTCHA returned HTTP 200 and became negative fixtures;
  HTTP 403, visible verification, challenge-title/route, and structured prompt
  cases remain positive fixtures.
- **2026-07-16 — partial-success failure visibility.** The pinned upstream
  `OpenURLTool` formats per-URL failures only when every requested resource
  fails; a mixed result previously returned successful documents while hiding
  blocked, timed-out, or otherwise failed URLs from the model. The API patch
  now records the final post-fallback `FailedFetch` list during result merging
  and appends the existing sanitized upstream failure report to the LLM-facing
  response whenever rich successful results also exist. All-failure and timeout
  responses retain their upstream forms, successful documents/citations remain
  unchanged, and no failure page body is exposed.
- **2026-07-16 — bounded post-navigation work and partial-result collection.**
  Every shared-client attempt now has a caller-owned absolute deadline in
  addition to the cumulative 45-second setup limit: Onyx supplies at most 105
  seconds and SearXNG supplies 50 seconds. `Page.navigate`, the completion
  event, DOM retrieval, retained-body stream creation, and every `IO.read` use
  only the remaining attempt budget and emit a typed, URL-free stage on expiry;
  cleanup retains its separate five-second command bound. Onyx collection ends
  five seconds before its 120-second outer deadline, returns completed URL
  results in input order, creates explicit failures for unfinished URLs, marks
  the shared invocation finalized so queued work cannot navigate late, and
  abandons waiting without retrying or cancelling an already-sent navigation.
  A deterministic orphan fixture proves completed work is retained and the
  collector returns before the blocked thread. Live parallel stress validation
  after restart reproduced one `DOM.getOuterHTML` stall at the exact 105-second
  stage while preserving sibling successes and returning a partial result; no
  outer 120-second aggregate timeout or retained API-to-CDP socket remained.
- **2026-07-16 — pinned main-body eviction and typed protocol visibility.** A
  repeated nine-URL batch showed five ordinary HTTP 200 HTML pages as generic
  browser failures. Exact replay proved `DOM.getOuterHTML` succeeded but every
  `Fetch.takeResponseBodyAsStream` was rejected. Obscura 0.1.10 applies its
  16-entry per-page retained-response limit before creating the navigation
  loader alias, so subresource-heavy pages can evict the main body before
  `Page.navigate` returns and no client-side reordering can claim it first. The
  shared client now maps the sanitized `no cached body` rejection to typed
  `body-unavailable`, preserves same-navigation HTML/XHTML DOM results only,
  and keeps PDF/raw/binary paths fail-closed. All typed client failures are
  warning-level, and Onyx maps protocol, body, charset, and empty-content
  categories to explicit agent-facing reasons rather than the generic browser
  failure. Runtime and upgrade docs record the limitation and removal test;
  increasing the cache entry count is rejected because its byte limit is per
  entry and would multiply worst-case retention.
- **2026-07-16 — explicit stock-crawler compatibility mode.**
  `ONYX_AGENT_USE_OBSCURA_BROWSER` now accepts exactly `true` or `false` and
  initially defaulted to the implemented direct-Obscura crawler. `false` changes only
  built-in-crawler `open_url` to the pinned upstream requests fetch plus its
  Playwright Chromium fallback; SearXNG remains direct Obscura. A
  startup-validated adapter keeps both stock stages public-only: requests uses
  a `trust_env=False` session with an explicit fixed public bridge, initial and
  redirected targets receive structural validation without API-side DNS, the
  Admin private-network setting is ignored for this LLM-controlled path, and
  scoped Playwright validation plus Chromium's `<-loopback>` bypass override
  keep navigations, redirects, and subresources on the same final-hop policy.
  This operator-selected mode intentionally gives up the one-navigation
  guarantee and Obscura containment: a qualifying response can cause a second
  origin request and Chromium runs inside `api_server`. Obscura document/wait
  settings do not apply. README, runtime, routing, security, patch, and upgrade
  docs record the tradeoff and removal/audit conditions.
- **2026-07-16 — stock crawler becomes the operational default.** Parallel
  batches and retries found the public-proxied stock Onyx requests/Playwright
  crawler was blocked less often and considerably more reliable than the
  direct crawler on pinned Obscura 0.1.10. The preference therefore now
  defaults to `false`. SearXNG remains direct Obscura, and neither route's
  privacy or egress policy changes. This is a version-specific operational
  decision: every Obscura upgrade must repeat comparable blocked,
  empty-content, timeout, and success testing and reconsider the default as
  Obscura improves.
- **2026-07-16 — atomic search admission and no unavailable fan-out.** The
  pre-thread availability check and later provider lease were previously a
  time-of-check/time-of-use pair: concurrent searches could both select one
  provider, create two engine threads, and make the loser fail as busy. Provider
  selection now atomically creates a tokenized reservation, the selected engine
  consumes only that token when acquiring its cleanup-scoped lease, and every
  pre-execution failure releases it. If all selected providers are busy,
  cooling, or suspended, the request creates no provider thread instead of
  disclosing the query to every candidate, and reports visible unavailable
  provider records instead of a silent empty success.
- **2026-07-16 — transport-independent mixed-result reporting.** Mixed-success
  failure presentation is now installed before crawler transport selection, so
  the default stock crawler and direct Obscura crawler both expose final
  post-fallback per-URL failures while retaining successful documents and
  citations. Direct-only document and wait configuration is imported and
  validated only when direct mode is selected.
- **2026-07-16 — configured direct main-response limit is authoritative.**
  `ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB` now caps every retained direct-Obscura
  main-response body, including HTML. The earlier fixed 20 MiB HTML response cap
  is removed; the fixed 20 MiB serialized rendered-DOM and search-DOM caps remain.
  An oversized stream is closed immediately rather than drained.
- **2026-07-16 — generated SearXNG lock is the runtime source of truth.** The
  derived image now installs its full Python dependency set from the generated,
  hashed `searxng/requirements.txt`. The manually maintained runtime lock and
  duplicate copied Playwright package were removed, so `make upgrade-python-deps`
  updates the file actually consumed by the image build.
- **2026-07-16 — isolated-runner stdout remains protocol-clean.** API and
  background bootstrap diagnostics are redirected to stderr because Onyx
  reserves isolated child stdout for its pickled result. This restores PDF
  extraction through the direct Obscura crawler.
- **2026-07-16 — derived SearXNG tags track embedded inputs.** The Makefile now
  hashes the pinned upstream tag plus the Dockerfile, generated dependency
  lock, shared Obscura client, and custom engines into the local image tag, so
  source-only changes cannot silently reuse an older derived image.
- **2026-07-16 — Bing verification interstitial is a typed CAPTCHA.** Bing can
  render a `One last step` / `solve the challenge below to continue` page
  without its older challenge selectors. The Bing engine now classifies that
  visible-text pair as CAPTCHA so SearXNG suspends the provider instead of
  reporting a parser crash. The same phrases in inactive script, style,
  template, or noscript content remain ignored.

### Resolved initial-testing finding

- **2026-07-16 — post-navigation orphan and aggregate head-of-line timeout.**
  A model emitted 12 URLs; pinned Onyx truncated the batch to its configured
  maximum of 10. The crawler completed multiple requests, including two typed
  HTTP denials, but one CDP WebSocket remained established for more than five
  minutes after the crawl future hit its 120-second outer timeout. No
  `pre-navigation-timeout` occurred, so the cumulative setup guard was not the
  failing boundary. The remaining candidate is an unbounded post-navigation
  CDP command (`Page.navigate`, DOM retrieval, retained-body stream setup/read,
  or a missing command response); the five-second event barrier and bounded
  cleanup paths did not report expiry. Because `OnyxWebCrawler.contents()`
  waits for its complete nested executor result, the one orphan caused the
  outer crawl future to return no crawler results to the agent, including
  already-completed successes. A subsequent six-URL batch returned three
  successes while the orphaned socket remained, demonstrating degraded rather
  than total renderer/API capacity. The bounded post-navigation and
  deadline-aware partial-collection correction above resolves the identified
  client-side failure mode without a second navigation or unsafe retry; live
  reproduction after restart remains a completion-review checkpoint.

Deterministic validation passed at implementation time. A live explicit
no-VPN run returned HTTP 200 for a direct built-in-crawler transport fetch of
`https://example.com/`; live Google and Brave engine attempts reached their
providers and surfaced HTTP 429/CAPTCHA through SearXNG. The configured Myst
provider set did not establish a stable route during validation, so the VPN,
full-RAG, upstream-proxy, PDF/raw/oversize live matrix remains an operator
follow-up when those external dependencies are available.

## Executive decision

Remove CRW from the wrapper-managed runtime request path and use the tagged,
upstream Obscura image as the single browser service for the built-in Onyx
Web Crawler and custom SearXNG engines. Operators may still deliberately select
another upstream Onyx content provider; that provider is outside this
single-navigation Obscura guarantee and has the privacy semantics documented
below. Run one Compose service with a bounded pool of Obscura renderer worker
processes so unrelated search providers and built-in-crawler `open_url`
requests can make progress concurrently:

```text
Onyx open_url -> internal CDP gateway -+
                                        +-> Obscura -> browser egress bridge
SearXNG engine -> obscura-control ------+            -> public final-hop proxy
                                                     -> selected final route
```

Each `open_url` request performs exactly one browser navigation and consumes
that navigation's retained main-resource body or rendered DOM. Do not add a
preliminary `HEAD`/`GET`, requests fetch, local-Chromium retry, CRW prefetch,
Obscura CLI `--dump original`, or other second content fetch.

Custom SearXNG engines navigate through the same Obscura instance, read the
rendered DOM, and retain their existing selectors and normalization. Rename
the deployed helper from `_crw.py` to `_obscura.py`; no CRW compatibility
alias remains.

Every caller sends raw `Page.navigate` with its request-class-specific Obscura
server-side `waitUntil` value:

- custom SearXNG engines use `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH`, default
  `load`; and
- non-search Onyx `open_url` requests use `OBSCURA_BROWSER_WAIT_UNTIL_WEB`,
  default `domcontentloaded`.

Both defaults are event/lifecycle conditions, not fixed sleeps. Every
navigation also retains a positive finite Obscura safety deadline.

## Required outcomes

- Preserve one-fetch HTML, PDF, and supported raw-text handling for the
  built-in Onyx Web Crawler path.
- Preserve browser fingerprint consistency, explicit waits, challenge
  visibility, and as much useful diagnostic information as the pinned
  unmodified Obscura release exposes. Document its logging limitations rather
  than claiming end-to-end redaction that the upstream image does not provide.
- Preserve one active SearXNG navigation per provider and at most one provider
  navigation start in each exact 3.0-second interval, with zero jitter.
- Preserve the wrapper's current `SEARXNG_ROUND_ROBIN` preference semantics in
  the SearXNG engine-selection and orchestration patches. Each selected custom
  engine performs exactly one Obscura navigation and surfaces its result or
  failure to SearXNG; neither the CDP client nor an engine retries or chooses a
  fallback provider.
- Let SearXNG's existing timeout, result, and engine suspend/block machinery
  own caller-visible search outcomes. An active renderer attempt retains its
  provider lease until cleanup, and later independent searches skip that busy
  provider instead of queuing more threads.
- Run one SearXNG Granian process and five bounded Obscura renderer
  workers, so distinct providers and unscheduled
  `open_url` activity normally have independent rendering capacity.
- Keep Onyx's 120-second `open_url` deadline, block built-in-crawler URL work
  on five process-global API permits only for the remaining invocation budget,
  preserve current partial/timeout results, and start no target/navigation
  after the invocation is finalized.
- Keep `open_url`, helpers, and network-enabled executors intentionally outside
  the SearXNG provider scheduler. Enabled executors may reach public search
  engines through the ordinary public policy; removing their former
  search-host denial is an intentional policy simplification that lets the
  browser, generic Onyx, and executor bridges share one public final-hop proxy.
- Preserve final-hop private/internal destination denial on initial requests,
  redirects, and browser subresources when the wrapper controls authoritative
  DNS. For remote-DNS upstream-proxy modes, retain the explicit residual that a
  malicious or misconfigured upstream can resolve a public-looking name
  privately unless it enforces equivalent destination policy.
- Keep target DNS and final routing at the selected final-hop proxy.
- Preserve VPN, configured-upstream, and explicit no-VPN fail-closed behavior.
- Preserve lite/full `open_url`, full-mode local RAG, configured inference,
  local embedding, optional Tailscale, and executor enablement, isolation,
  proxy injection, and route-class semantics except for the deliberate removal
  of the executor-only search-host denial above.
- Remove CRW, its validation DNS, the CDP shim, obsolete networks, secrets,
  images, builds, configuration, health dependencies, tests, and docs.
- Build on the already simplified prerequisite: unused SearXNG Valkey,
  bypassed full-mode Onyx model servers, the fake CRW credential, and duplicate
  identical-policy proxy processes have already been removed.

## Non-goals

- Do not patch, fork, or locally rebuild Obscura. A missing capability blocks
  cutover until a compatible upstream release is pinned and audited.
- Do not use plain HTTP clients as a renderer fallback.
- Do not give Obscura, SearXNG, or Onyx a direct Internet route.
- Do not expose CDP on a host port or general application/data network.
- Do not pass Obscura `--allow-file-access`. The shared client accepts only
  validated `http`/`https` target URLs, and the Obscura container receives no
  private data volume or secret-bearing configuration solely for browsing.
- Do not merge browser, executor, or Onyx caller networks or their fixed
  bridges.
- Do not make document byte limits unlimited.
- Do not claim the document byte limit bounds Obscura's initial response
  allocation. The pinned release reads the complete response before applying
  its retained-body limit.
- Do not make either the Obscura worker count or navigation deadline unbounded.
- Do not imply per-user browser isolation: at the pinned Obscura version the
  CDP clients assigned to a renderer worker share that worker's browser trust
  domain. Multiple workers improve capacity, not per-user isolation.

## Version and source audit

Implement against the committed pins in `stack.versions.env`. Before cutover,
re-audit these symbols in the matching `reference_repos/` checkouts:

| Component | Plan baseline | Primary audit locations |
| --- | --- | --- |
| Onyx | image `v4.2.8`; ref `1afe6fb4c01ab2b39868264fc44c57dd399cc58c` | `backend/onyx/tools/tool_implementations/open_url/onyx_web_crawler.py`, `backend/onyx/tools/tool_implementations/web_search/providers.py`, `backend/onyx/utils/web_content.py`, `backend/onyx/file_processing/extract_file_text.py`, `backend/onyx/utils/playwright_fetch.py`, and the Web UI provider-card source in `reference_repos/onyx` |
| SearXNG | image `2026.7.15-7b2199ecd`; ref `7b2199ecdf75a00981583fa2f392a785dfc4fcee` | online/offline processors, `demo_offline.py`, exception/suspension logic, and search orchestration in `reference_repos/searxng` |
| Obscura | image `h4ckf0r0day/obscura:0.1.10`; ref `50e66320b0842d2844ce298957a335a6bed95c4d` | main-resource/body storage in `crates/obscura-browser/src/page.rs`; Network, Fetch, IO, Page, Target, and dispatch domains in `reference_repos/obscura` |
| CRW removal baseline | image `ghcr.io/us/crw:0.23.0`; ref `dc497fdf35a6d1a941391cbade663601eedac1b6` | `reference_repos/crw`, used only to enumerate preserved or intentionally removed behavior |
| Playwright Python | `1.58.0` from the pinned Onyx source | Onyx dependency metadata and both direct-client call sites; no browser binary belongs in the SearXNG image |
| Code interpreter | image `0.4.4`; ref `8950eadc06567798ec61354f24260e5dc996684b` | `reference_repos/python-sandbox`, as an executor-routing regression target rather than part of the browser path |

These values describe the plan's audited compatibility baseline, not an
invitation to copy stale literals into Compose. The implementation must first
compare them with the committed manifest and checkout identities. Any
difference requires the fresh audit below. Record the tested reported version
for moving-tag support images such as Tailscale and autoheal when they
participate in the runtime matrix. Image tags identify the tested registry
artifacts operationally, while Git refs identify the source trees used for the
source audit. This plan makes no image-digest, registry-tag-immutability,
reproducible-build, provenance, or tag-to-source-hash claim.

- Onyx `OnyxWebCrawler`, `is_pdf_resource`, `extract_pdf_text`, and current
  Playwright fallback/source-shape patch points;
- SearXNG custom engine orchestration, stock timeout/late-result behavior,
  busy-provider selection, and suspension behavior;
- Obscura main-resource aliasing/body retention, collapsed HTTP-redirect event
  and loader-alias behavior, `Network.getResponseBody`,
  `Fetch.takeResponseBodyAsStream`, `IO.read`/`IO.close`, navigation waits,
  multi-worker connection routing, context behavior, deferred non-navigation
  commands, and cookie-clearing CDP methods;
- CRW scrape/search mapping, PDF behavior, waits, retries, proxy assumptions,
  and diagnostics being removed; and
- CDP shim wait rewriting, target selection, proxy/context stripping, stealth
  handling, cookie clearing, and traces being retired.

Any pin change requires a fresh source and runtime audit. Patch drift remains
strict and startup-fatal.

The current Onyx pin supplies Playwright Python `1.58.0`. Use that exact
version in the derived SearXNG image unless the implementation deliberately
updates and re-audits both callers against the pinned Obscura CDP behavior.
Do not let Onyx and SearXNG acquire unrelated Playwright versions by accident.

Before production cutover, run a capability gate against the tagged upstream
Obscura image, not merely a fake CDP server or an assumption that its tag was
built from the audited source ref. In addition to the
validation matrix below, prove that:

- normal and JavaScript-rendered HTML need no second main-resource fetch;
- an HTTP redirect chain yields the characterized terminal status/headers,
  final frame URL, emitted document request id, possible loader-id mismatch,
  and retrievable terminal body; a JavaScript navigation chain exposes and
  selects only its final main-frame document at this pin; blocked redirect
  destinations fail visibly;
- a successful raw navigation returns its command response before the
  synthetic Network/Page event batch, and the matching
  `Page.frameStoppedLoading` event provides a reliable bounded completion
  barrier after the response/loading events;
- PDFs with `Content-Type: application/pdf`, both inline and
  `Content-Disposition: attachment`, remain byte-identical;
- an extensionless `application/octet-stream` PDF is byte-identical and
  detectable from a bounded `%PDF-` prefix;
- misleading-text and missing-type PDFs take the typed
  `byte-identity-unavailable` path even when their observed length happens to
  match, because equal length does not prove that Obscura's lossy UTF-8
  conversion preserved the bytes;
- declared non-UTF-8 text demonstrates the pinned conversion limitation;
- missing/evicted retained body, deliberately discarded oversized body, empty
  retained body, and a completed true zero-length body are distinguishable;
- the completed response supplies an authoritative actual body length when
  `Content-Length` is absent or false, and bounded `IO.read` plus `IO.close`
  behave as assumed; and
- `Network.clearBrowserCookies`, its deferral behind an active same-worker
  navigation, non-atomic interleaving with other connections after the clear,
  and the default-context behavior match the documented best-effort clearing
  model. If a future pin implements real browser
  contexts, stop and redesign the context/clearing lifecycle rather than
  inheriting the old assumptions accidentally.

Failure of any required capability blocks cutover. Evaluate a later
unmodified upstream release through the normal pin-upgrade process; do not
patch or locally rebuild Obscura to make the test pass.

The pinned Obscura `--allow-file-access` option is narrower than its name. In
the single-worker server it gates CDP `Page.navigate` and
`Target.createTarget` calls whose top-level URL is `file:`. The multi-worker
launcher does not propagate the option to its child processes. Separate
upstream guards reject cross-scheme JavaScript navigation plus ordinary
external-script and stylesheet `file:` subresources, but the ES-module path
does not apply that subresource guard: a remote page can name a local `file:`
module, which the generic Obscura client reads and passes to the JavaScript
module loader. The scripted fetch/XHR validator also admits the `file` scheme,
although the pinned stealth/reqwest send path does not itself provide the same
local-file reader. This is an accepted upstream security limitation, not a
capability to emulate or a reason to fork Obscura. Do not enable
`--allow-file-access`; reject direct caller-supplied `file:` URLs in the shared
client; keep the container read-only, free of private mounts and browsing
secrets, and document that those measures reduce impact but do not repair the
upstream module path. Characterize these cases with a non-secret canary file so
an upgrade cannot silently broaden or be falsely claimed to eliminate the
limitation.

Run every Obscura process as the same explicit numeric non-root identity,
`65534:65534`: the multi-worker parent and all five renderer children must
report that UID/GID. This is a wrapper hardening requirement because the
tagged upstream image has no `USER` declaration. A non-secret root-readable,
non-world-readable canary mounted read-only only in the isolated capability
test topology must remain unreadable from both the parent and every child; do
not derive or rebuild the Obscura image for this test. A future upstream
`USER` declaration does not remove this check; change the wrapper identity
only through an explicit image and filesystem compatibility review.

## Target networks and services

Retain:

- internal Onyx application/data/Teep networks from the isolation prerequisite;
- `obscura-control`, reachable by SearXNG and through a narrow Onyx CDP gateway;
- a dedicated internal `onyx-obscura-control` caller network, reachable only by
  `api_server` and that gateway;
- `browser-egress`, reachable only by Obscura and its fixed bridge;
- `browser-policy-upstream`, joining only the browser bridge and
  `netns-holder`;
- separate public and host Onyx bridges and policy-side networks;
- separate optional executor network, bridge, and policy-side network; and
- full-mode doc-drop and embedding networks.

Preserve the isolation prerequisite's hardened fixed-destination host
publishers for nginx/WebUI, doc-drop, and the SearXNG diagnostic endpoint.
Retain the fixed `host-teep-proxy` only in the Teep-through-VPN layer. This is
required for portable Docker Desktop behavior when the underlying service is
internal-only or shares `netns-holder`; it is not permission to publish CDP,
move an Onyx application back into the routing namespace, or replace a fixed
publisher with a client-selectable proxy. Keep the publishers numeric-nonroot,
read-only, capability-free, `no-new-privileges`, forwarding-disabled, and
limited to their one literal destination. Preserve service and publisher
health checks.

Optional layers must remain structurally optional: disabled Tailscale models
omit its frontend gateway, explicit no-VPN models omit `autoheal`, and executor
enablement adds only its existing component network, fixed bridge, policy-side
network, and listener configuration. The already-removed SearXNG Valkey and
bypassed Onyx inference/indexing model-server containers remain absence
assertions; do not recreate them to satisfy an obsolete dependency or delete
existing operator cache/data directories during migration.

Delete every CRW-only network after verifying no retained service uses it.

`obscura-cdp-gateway` joins dedicated internal `onyx-obscura-control` and
`obscura-control`, forwards only the CDP port, publishes no host port, and
exposes no policy/configuration interface. `api_server` joins
`onyx-obscura-control`; background workers, code-interpreter control,
embedding, data, frontend, and every other Onyx service do not. The gateway
must not join the shared `onyx-backend` application network. SearXNG may join
`obscura-control` directly. Neither caller joins `browser-egress`.

Use explicit stack-owned endpoints, for example
`ONYX_OBSCURA_CDP_URL=ws://obscura-cdp-gateway:9222/devtools/browser` and
`SEARXNG_OBSCURA_CDP_URL=ws://obscura:9222/devtools/browser`. Keep these as
literal internal endpoints rather than routine user overrides or arbitrary
external CDP endpoints. Add `obscura-cdp-gateway` to both forms of the
stack-owned Onyx `NO_PROXY` list so the Playwright CDP connection cannot be
redirected through an application egress proxy. Set
`OBSCURA_NAV_TIMEOUT_MS=90000` as a positive,
finite stack-owned Compose literal greater than the 60,000 ms SearXNG outer
response deadline so the two deadlines retain their distinct roles. Set
`OBSCURA_CDP_COMMAND_TIMEOUT_MS=120000` and
`OBSCURA_FETCH_TIMEOUT_MS=45000`; neither command nor scripted-fetch watchdog
may be zero/disabled. Set the shared client's post-command CDP event-barrier
deadline to 5,000 ms. These are reviewed, non-user-facing implementation
literals; do not add runtime parsing or validation code for them. Pass the
resolved `EGRESS_ALLOW_HTTP_URLS` boolean to both `api_server` and
`searxng-core`, so the
shared client's local scheme gate and the public final-hop proxy enforce the
same operator policy. Do not give either caller
`ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS` or a host-capable browser route.

Obscura retains its mandatory proxy setting and can resolve only the internal
browser bridge. Preserve `OBSCURA_ALLOW_PRIVATE_NETWORK=true`: it is required
only so Obscura can resolve and connect to its fixed private Docker bridge
proxy, not as permission for a caller to browse private destinations. Preserve
`OBSCURA_PROXY=http://obscura-egress-bridge:3128` as a stack-owned exact value.
Assert both literals in effective-Compose tests. The browser network
remains internal, so ignoring the proxy does not create direct egress.

Bound Obscura's retained response stores explicitly. Define the wrapper
retention floor as the maximum of the document-byte limit, the 20 MiB rendered
HTML limit, and the 20 MiB search-DOM limit. Generate canonical decimal byte
values in the Compose model and set
`OBSCURA_NETWORK_BODY_BUFFER_ENTRIES=16`,
`OBSCURA_NETWORK_BODY_BUFFER_BYTES=<retention floor>`,
`OBSCURA_IO_STREAM_MAX_ENTRIES=16`, and
`OBSCURA_IO_STREAM_MAX_BYTES=<five times retention floor>`. Compute the
derived values with checked integer arithmetic in the Makefile from the
user-facing document limit. The unmodified distroless Obscura image must not
be expected to run a shell or calculate these values at startup. These bounds
reduce ordinary eviction but do not make retained-body availability a
guarantee; eviction remains a typed failure. Document that
`OBSCURA_NETWORK_BODY_BUFFER_BYTES` is a per-body limit rather than an aggregate
body-store cap, so its entry count multiplies possible retention and base64
storage plus loader aliases can add copies. Keep distinct the IO stream store's
aggregate byte accounting; neither mechanism bounds the earlier full response
allocation or all simultaneous copies in the process.

At the audited Obscura `v0.1.10` pin, preserve stealth and run the service with
the stack-owned Compose command equivalent to
`obscura serve --host 0.0.0.0 --port 9222 --stealth --workers 5`. Do not pass
`--allow-file-access` or `--storage-dir`. These are wrapper-owned Compose
arguments and do not need a caller-side startup validator. Do not expose the
worker count in `.env.wrapper.example` or accept an unreviewed runtime
override. Five is the audited bounded topology, not a claim that Obscura itself
enforces a wrapper range. A deliberate code/Compose change to the literal must
re-run the memory, concurrency, state, and partial-worker-health checks. This is distinct from
`GRANIAN_WORKERS=1`: Granian must remain single-process so the SearXNG provider
scheduler has one process-wide state, while Obscura's child processes provide
rendering capacity. The Obscura frontend assigns each new CDP connection to a
child worker round-robin and pins that connection to the selected child.

Run Obscura as `65534:65534` with a read-only root filesystem, no writable or
private data volume, no injected browsing secret, all capabilities dropped,
and `no-new-privileges`. Keep its environment allowlist minimal and limited to
the reviewed proxy, private-bridge-resolution, timeouts, body-store bounds,
logging, and renderer settings; do not pass host or Onyx secrets through a
broad environment file. Remove the current `obscura-data` volume with the other
obsolete browser-state artifacts after cutover; do not inspect, migrate, or
copy its contents. These controls reduce the impact of the pinned upstream
ES-module local-file issue but do not claim to make files inherently present
in the image unreadable.

Round-robin assignment is capacity distribution, not provider affinity. A
stuck child can still receive a later connection, so some unrelated requests
may time out even while other children remain usable. Do not retry such a
connection in a way that can enqueue abandoned target-creation commands.
Return a typed CDP failure and let a later logical attempt receive the next
worker. If runtime tests show that this residual causes unacceptable
collateral failures, replace the pool with separately health-checked or
provider-affine Obscura services as a deliberate topology revision; do not
silently add client retries.

## Final-hop proxy model

Use the same final-hop model as the isolation prerequisite:

- clients reach only hardened fixed TCP bridges;
- bridges forward to fixed listener ports in `netns-holder`;
- final-hop proxy processes accept their configured bridge peers from
  non-loopback networks and trusted co-residents through shared namespace
  loopback;
- the proxy parses HTTP framing, validates destinations, selects DNS/upstream
  behavior, pins direct addresses, and establishes the connection; and
- no caller can select a resolver, upstream proxy, source address, or route
  class.

Bridge-peer authentication is not isolation between `netns-holder`
co-residents. Myst and the final-hop proxies remain trusted, and any Teep or
Tailscale process deliberately promoted by its Myst-routing switch shares the
namespace's loopback, interfaces, routes, and listeners. This plan must retain
that explicit trust model or move such a component out of the namespace; it
must not describe fixed gateways as a co-resident sandbox.

As part of the atomic migration, move the generic implementation from the
legacy `crw/` path to a neutral location and rename `PREFETCH_*` vocabulary.
Remove named search-host policy modes: after CRW prefetch is gone, search
engines are ordinary public destinations.

After CRW prefetch is removed, use the existing shared public final-hop proxy
for generic Onyx, browser, and optional executor bridges. This deliberately
allows enabled executors to reach public search-engine hosts and removes the
old executor-only search-host deny mode; it is the preferred service-count and
policy simplification, not an accidental weakening or a claim that executor
queries are private from search providers. Each bridge remains on a distinct
caller network and the proxy keeps an explicit peer allowlist. Retain the
separate host-capable proxy for exact host and opt-in RFC1918 Onyx traffic.
Both run hardened in `netns-holder`. This process split is defense in depth and
operational separation, not a sandbox: arbitrary code execution in either
trusted proxy compromises the shared namespace.

Do not introduce an intermediate request-policy process. Do not duplicate
destination parsing across two custom daemons. Do not add a custom stream
protocol, per-route credentials, admission lease, idle lease, or fixed total
CONNECT deadline. Established tunnels close when a peer closes or I/O fails;
protocol clients and endpoints own long-lived connection timeouts.
This prohibition applies to proxy/broker leases and does not prohibit the
API-process-local blocking semaphore below; that semaphore grants no network
authority and coordinates only in-process crawler work.

The host proxy alone retains exact `host.docker.internal`, exact stack-owned
destinations, and `ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS` behavior. Public
Onyx, browser, and executor listeners reject those destinations. Mixed
public/private answers, loopback, link-local, metadata, container-internal
names, and alternate-route attempts fail closed.

Those destination-denial guarantees are authoritative when the wrapper's
final-hop proxy performs DNS and pins the validated address. If an operator
configures a remote-DNS upstream proxy, the wrapper can validate the hostname
and policy class but cannot prove which address the upstream resolver selects.
Document this as a residual DNS-rebinding/private-resolution risk and never
claim address-level private-target denial for that mode. Named
operator-local domains remain forbidden on the public route, and an empty,
failed, mixed, or private answer from any wrapper-controlled lookup must not
fall back to remote DNS.

Each bridge remains numeric-nonroot, read-only, capability-free,
`no-new-privileges`, packet-forwarding-disabled, mount-free, and limited to one
literal listen/forward command. It exposes no host, admin, DNS, metrics, or
control port.

## Direct Obscura client

### Shared package and execution model

Create one neutral wrapper-owned Python package, for example
`browser/obscura_client/private_onyx_obscura/`. It is an in-process library,
not a sidecar, socket service, or part of the Obscura source/image. Maintain
one source implementation:

- mount it read-only into the Onyx API container in lite and full modes and
  add its exact directory to the API service's `PYTHONPATH`; and
- copy the same package into the derived SearXNG image at build time, keep it
  read-only at runtime, and set `PYTHONPATH` explicitly.

Do not keep a second editable implementation under `searxng/` or an Onyx
patch directory. Both integrations must import the shared navigation, body,
validation, logging, and cleanup code.

Use Playwright's Python CDP connection and a raw `CDPSession`; do not use
high-level `page.goto()` for the main navigation because it obscures the
Obscura-specific server-side `waitUntil` behavior. Pin the Playwright version
used by each caller to a version audited against the committed Onyx, SearXNG,
and Obscura pins. A version change requires the same protocol/source audit as
an upstream image change.

Implement one asynchronous client core with a narrow synchronous adapter for
the pinned synchronous Onyx and SearXNG call sites. Each logical fetch thread
owns its event loop, Playwright connection, browser/page handles, CDP session,
tasks, and cleanup. Never share those objects across threads or loops. Reject
nested synchronous-adapter use from an already-running event loop and do not
fall back to a detached helper thread. Close the loop only after body handle,
session, target, connection, and pending client tasks have been cleaned up.

### Onyx invocation deadline and blocking admission

Retain Onyx's existing 120-second `open_url` outer timeout and its normal
partial indexed/crawled-result behavior. For the built-in crawler only, create
one invocation state before Onyx starts the parallel indexed-retrieval and web-
crawl jobs. The state contains the absolute monotonic deadline derived once
from the pinned `OPEN_URL_TIMEOUT_SECONDS` value and a thread-safe finalized
flag. The timeout callback marks that same object finalized. Pass the object
through the context copied into `_fetch_web_content`, then pass it explicitly
from `OnyxWebCrawler.contents()` into each nested per-URL worker; do not assume
that a `ContextVar` propagates through the crawler's second
`ThreadPoolExecutor`.

The pinned Admin content-provider test and feature API also call
`OnyxWebCrawler.contents()` directly, outside `OpenURLTool`. They must not
bypass admission merely because no tool invocation state exists. At
`contents()` entry, create one local state with the same stack-owned 120-second
absolute admission/pre-navigation deadline when the tool context is absent and
pass it explicitly to the per-URL workers. Those direct HTTP paths have no
outer tool timeout callback or indexed-result branch, so an admission expiry
returns their normal failed-provider/API result after the full wait; it does
not acquire or navigate late. A navigation sent before their local deadline is
allowed to finish and clean up under the independent Obscura safety deadline.

Use one API-process-global `threading.BoundedSemaphore(5)` owned by the
`api_server` patch layer. Each built-in-crawler URL worker blocks on that
semaphore for at most `max(0, invocation_deadline - monotonic_now())`. It does
not return an early capacity-specific error merely because all slots are busy.
If no slot is acquired before the invocation deadline, leave the outer Onyx
timeout/partial-result path to produce its existing caller-visible result.
Immediately after an acquire and after any deferred cookie clear, check both
the finalized flag and remaining time. After creating the target, attaching
the session, enabling domains, and registering event listeners, perform one
more explicit check immediately before the sole `Page.navigate` send. A worker
that loses any of those races closes all created CDP state, releases the permit,
and sends no origin request. Target setup is therefore allowed to finish, but
it never authorizes navigation after the invocation has finalized.

Hold the permit from before CDP connection/clearing through target, body,
session, connection, event-loop, and parser cleanup, then release it exactly
once in `finally`. A navigation sent while the invocation was live is not
cancelled when the outer timeout expires; its background worker retains the
permit through cleanup and cannot publish a late result. This makes later
calls wait for real browser capacity while preserving Obscura's independent
finite navigation deadline.

The semaphore bounds live API browser/parse attempts, not waiting Python
threads, and Python does not promise FIFO semaphore wakeups. Concurrent agent
tool calls may therefore leave multiple crawler threads blocked until their
own invocation deadlines and may not be served fairly. That is an explicit
tradeoff for waiting until Onyx's existing timeout instead of returning an
early busy error. Keep one API process and one Compose replica; adding Uvicorn
workers or API replicas multiplies the process-local limit and requires a new
capacity design. SearXNG remains independently bounded by its single process
and provider leases, so the two callers can still contend at Obscura; the plan
does not claim cross-service capacity reservation.

### Result contract and navigation lifecycle

Return a typed result containing at least:

- requested URL and final main-document URL;
- main-document status code and status text when available;
- normalized response headers, parsed content type, and charset;
- main-frame, navigation loader, and terminal document request identity;
- the mirrored Obscura body-storage classification and whether original-byte
  identity is guaranteed;
- authoritative completed body byte count;
- rendered HTML, exact binary bytes, or accepted semantic text as requested;
- navigation and body-read timings;
- challenge/rate-limit/blocked indicators when detectable; and
- a typed failure category.

Typed failures must distinguish invalid or locally forbidden URLs, final-hop
policy denial, navigation timeout, HTTP status failure, 403/429/CAPTCHA or
other challenge, oversize response/DOM, unavailable or evicted body, stream
decode/protocol failure, unavailable byte identity, unsupported charset or
content type, empty/unparseable content, and CDP transport loss. Do not put a
raw body in an exception.

For every logical fetch, in this order:

1. connect once to the configured CDP endpoint and remain pinned to the
   selected Obscura child worker;
2. perform the best-effort cookie clear described below;
3. use the default context, create one fresh page/target, and attach one raw
   CDP session;
4. enable required `Network`, `Page`, and lifecycle events and register all
   response/loading listeners plus a matching `Page.frameStoppedLoading`
   completion future before navigation;
5. run the caller-supplied pre-navigation guard after all target/session/event
   setup and immediately before the origin request; for Onyx this rechecks the
   shared invocation finalization state and remaining deadline, and failure
   closes the target without navigation;
6. send one raw `Page.navigate` with the applicable wait mode and finite
   Obscura navigation deadline;
7. after a successful `Page.navigate` response, await the matching
   `Page.frameStoppedLoading` future within a finite stack-owned CDP event
   completion deadline; the pinned server sends the command response before
   its synthetic Network/Page event batch, and the matching stopped-loading
   event is the barrier after that batch rather than permission to inspect
   listener state immediately after `session.send()` returns;
8. identify the terminal main-frame `Document` response emitted for that raw
   navigation, retain its event request id independently of the navigation
   loader id, and obtain the terminal page URL from the final frame/target
   state as described below;
9. record final URL, status, headers, body state, and authoritative completed
   length, then obtain the requested DOM or retained body from that same
   navigation; and
10. close any IO handle, detach the CDP session, close the target/page, and
   disconnect the client in `finally`, including every timeout and failure
   path.

Register the event-completion future before sending navigation so the barrier
cannot be missed. A failed navigation command that produces no completed event
batch follows its typed navigation/policy failure path without waiting for a
barrier that cannot arrive. A successful command followed by a missing,
mismatched, or late barrier is a typed CDP protocol/transport failure. The event
completion deadline is stack-owned, positive, finite, and only bounds receipt
of the already-produced event batch; it must not initiate or retry navigation.

Subresources and service-worker traffic must never be mistaken for the main
document. Require the audited pin-specific relationship among the final emitted
main-frame `Document` event, its request id, the navigation loader, and final
frame URL. A change to that relationship is a protocol incompatibility; do not
guess a different body. Do not reconnect or retry inside a logical attempt,
because the first navigation may still be running and a retry would violate
both the single-fetch rule and worker accounting.

### Redirect and terminal-document semantics

Treat HTTP redirects followed inside one raw `Page.navigate` differently from
an additional client navigation. At Obscura `v0.1.10`, its HTTP client follows
the HTTP redirect chain internally, reissuing each hop through the mandatory
browser proxy, and returns the terminal response to the page. The final-hop
proxy therefore remains authoritative for every redirect destination, but the
CDP compatibility layer does not expose a Chrome-shaped event for every HTTP
redirect hop. The client can report that the requested and terminal URLs
differ, but it cannot reliably enumerate intermediate hop URLs, statuses, or
headers from this CDP surface; do not invent a redirect history in results or
diagnostics.

The pinned CDP shape has an important asymmetry that the client must implement
deliberately: the emitted terminal `Document` response may retain the URL and
internal request id assigned when that fetch began, while
`Page.frameNavigated`/target state reports the terminal response URL. When an
HTTP redirect changes the URL, `requestId == loaderId` and the loader-id body
alias are not guaranteed. Do not reject a valid redirected response solely for
that mismatch and do not request the body by loader id unconditionally.

For the audited pin, after the stopped-loading event barrier, require the
terminal emitted `Network.responseReceived` event whose type is `Document` and
whose `frameId` is the fresh target's main frame. Use that event's `requestId`
for its matching `Network.loadingFinished` count and same-navigation
retained-body stream. Use the final `Page.frameNavigated`/target URL as the
terminal document URL. The selected event's status and response headers
describe the terminal HTTP response even when its event URL still contains the
pre-redirect URL. Require exactly the characterized event relationship;
missing or ambiguous main-frame document events fail as protocol
incompatibility.

Obscura may also process a bounded JavaScript-triggered navigation chain inside
the same server-side `Page.navigate`. At `v0.1.10`, each internal
`navigate_single` step clears the page's pending network-event collection, so
the synthetic CDP batch exposes only the final JavaScript-chain step rather than
an ordered event history for earlier steps. The client therefore uses the one
characterized terminal main-frame `Document` event and final frame URL and must
not attempt to reconstruct or report the hidden earlier chain. This remains one
client-initiated navigation, while all HTTP requests in the server-side chain
remain subject to the same final-hop policy and Obscura deadline. A future pin
that accumulates multiple chain-step document events requires a fresh protocol
audit instead of silently changing terminal-event selection.

For built-in-crawler `open_url`, preserve input ordering by the originally
requested URL but set the successful `WebContent.link` and document metadata to
the terminal main-document URL. HTML cleanup, PDF/raw dispatch, status handling,
and body limits use only the terminal response and terminal DOM/body. A blocked
redirect destination that prevents a terminal response is a typed final-hop
policy/navigation failure, not an empty document and not a reason to refetch.

For custom SearXNG engines, the terminal navigation URL identifies where the
provider actually landed, but organic result URLs still come only from parsing
the terminal rendered DOM. Classify a terminal 403/429, CAPTCHA, consent/block
page, unexpected cross-provider landing, empty DOM, or challenge markers before
running the organic-result parser. Such a terminal condition produces the
mapped engine failure and suspension behavior below; never parse an
intermediate redirect response or treat a challenge DOM as a valid zero-result
page. Use exact, committed terminal-host allowlists: `google2` permits
`www.google.com`, `google.com`, and `consent.google.com`; `bing2` permits
`www.bing.com` and `bing.com`; `duckduckgo2` permits
`html.duckduckgo.com`, `www.duckduckgo.com`, and `duckduckgo.com`; `brave2`
permits only `search.brave.com`; and `startpage2` permits
`www.startpage.com` and `startpage.com`. Matching is on the normalized exact
host, not a suffix or substring. A regional or newly introduced provider host
fails closed as an unexpected landing until its exact host is reviewed, added,
and covered by fixtures. An origin outside the allowlist is an unexpected
landing, while a consent or block page on an allowed origin is detected from
status and bounded DOM markers.

### Wait settings

Make the navigation wait mode an explicit per-call client argument. The
SearXNG integration passes the resolved
`OBSCURA_BROWSER_WAIT_UNTIL_SEARCH` value; every non-search Onyx `open_url`
path passes the resolved `OBSCURA_BROWSER_WAIT_UNTIL_WEB` value. Do not infer
the request class from the URL or reuse one variable for both paths.

Expose both variables in `.env.wrapper.example` and pass only the applicable
setting to each caller container. At caller startup, validate the resolved
value as exactly one of `domcontentloaded`, `load`, `networkidle0`, or
`networkidle2`. Empty, differently cased, or otherwise invalid values fail
startup visibly. Retain `load` and `domcontentloaded` as the respective
Compose defaults rather than silently selecting a client-library fallback.

Do not add a general post-navigation sleep. If a particular search engine
needs an additional condition after the configured lifecycle event, it must
be an engine-specific, bounded, test-proven DOM/lifecycle predicate. It may
not silently replace either configured `waitUntil` value.

### URL validation and diagnostics

Before connecting, perform syntax and obvious-literal validation without
resolving the target hostname:

- allow `https`, and allow `http` only when `EGRESS_ALLOW_HTTP_URLS=true`;
- reject URL credentials, malformed host/port forms, and unsupported schemes;
- normalize IDNA and trailing dots consistently;
- reject loopback, unspecified, link-local, multicast, private,
  carrier-grade, documentation, benchmark, and every other non-public IP
  literal, including mapped forms;
- reject `localhost`, single-label hosts, Docker/Podman internal aliases and
  defaults, metadata names, and the repository's internal suffix/name
  blocklist; and
- remove the fragment from the network navigation while preserving it only as
  presentation metadata, because fragments are browser-local.

Do not resolve user target names in Onyx, SearXNG, the shared client, or a
validation sidecar. Only the internal CDP service name uses Docker DNS. The
selected final-hop proxy remains authoritative for target resolution and
repeats destination checks for initial navigation, redirects, and
subresources using VPN/provider DNS, upstream remote-DNS semantics, or the
explicit no-VPN resolver as configured. The Obscura path is public-only and
cannot obtain the host proxy's exact-host or RFC1918 exceptions.

Wrapper-owned direct-client diagnostics should capture as much actionable
information as is available: stage, scheme, normalized host, port, terminal
status and URL classification, body/DOM size, timing, wait mode,
worker-facing connection ID, redirect classification, and typed failure
category. SearXNG, not the shared client, logs any resulting suspension
decision. Redact query values in these wrapper logs and
never deliberately log bodies, DOM, cookies, authorization headers, proxy
credentials, API keys, document contents, or CDP stream chunks. Identify
connect, state clear, navigate, wait, body stream, parse, cleanup, and
final-hop-policy failures without exposing private content.

Do not claim that this constitutes end-to-end log redaction. At Obscura
`v0.1.10`, the multi-worker launcher discards child worker stdout/stderr, so
the fixed five-worker deployment cannot surface all renderer-internal errors;
the parent can still report routing and unreachable-child failures, while the
typed direct-client and final-hop-proxy logs remain the primary diagnostics.
The pinned single-worker code path can emit warnings containing full URLs,
including query values, and a future launcher change could expose those lines.
Document both limitations. A desirable upstream improvement is sanitized
worker lifecycle/error forwarding that omits URLs, bodies, cookies, and
credentials; do not promise that improvement or suppress the useful
wrapper-owned diagnostics in the meantime.

### Main-body identity and retrieval

The pinned stream API does not expose Obscura's internal `main_is_binary`
decision directly. Mirror the pinned `is_text_like_content_type` predicate
exactly, including case/parameter normalization, suffix rules, and its
treatment of absent or empty `Content-Type` as text-like. Protect the mirror
with a strict source-shape/version anchor and a fixture table covering every
recognized text type, missing type, and representative binary types. If a
future pin changes the predicate or exposes an authoritative classification
field, stop and re-audit rather than inferring byte identity from valid UTF-8
or matching lengths.

For binary-classified PDFs and supported documents, read the retained body
from the same navigation and pass exact bytes to Onyx's existing in-process
document extraction pipeline. For HTML, use the rendered DOM and Onyx's
existing cleanup pipeline. Treat misleading content types, non-UTF-8 text
conversion, and unavailable body identity as explicit limitations or typed
failures; never silently refetch.

For retained-body access:

1. treat a syntactically valid `Content-Length` only as an early rejection
   hint, never as the authoritative size or existence check;
2. require Obscura's completed actual body count and compare it with the
   applicable limit;
3. open `Fetch.takeResponseBodyAsStream` at most once;
4. call bounded `IO.read` repeatedly, decode only chunks explicitly marked
   base64, and increment the decoded entity-byte count before appending;
5. reject as soon as the count exceeds the limit and close immediately; at EOF,
   require the streamed count to match the completed count when the protocol
   supplies one; and
6. call `IO.close` on success, EOF, timeout, oversize, decode failure,
   transport loss, and caller exception.

Treat every Obscura text-classified retained body as potentially lossy
semantic UTF-8. Report `original_byte_identity=false` and
`lossy_conversion_possible=true` even when the authoritative pre-conversion
count equals the streamed UTF-8 count; equality validates only count accounting
and never proves byte preservation. A mismatch proves conversion, truncation,
or protocol inconsistency and is a typed failure. Accept text-classified raw
content only when the declared charset is absent or normalizes to `utf-8`,
`utf8`, `us-ascii`, or `ascii`; any other declared charset is a typed
unsupported/lossy-charset failure because the original bytes are unavailable.
For binary-classified exact bytes, strict decoding without replacement may use
only those four names plus `iso-8859-1`, `latin-1`, and `windows-1252`.
Rendered DOM is also semantic output and must never carry an original-response
byte-identity claim, regardless of the retained-body classification or counts.

When classification requires magic bytes, read only a bounded prefix from
that one stream. Continue draining the same stream for a binary-classified
document, or close it before using the already-rendered DOM. Never reopen the
body stream or navigate again. A text-classified or missing-type body with
PDF signals is `byte-identity-unavailable`, not parser input.

### Content and memory limits

Expose `ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB`, default `50`, as the one
user-facing raw-document size setting. Accept only a non-empty positive
base-10 integer that can be converted safely to bytes; reject zero, negative,
fractional, non-numeric, and overflowing values before startup. Interpret MiB
as `1024 * 1024` bytes, compute the canonical byte value once, and propagate
that exact value to navigation retention, body streaming, in-process parser
input, and diagnostics. Count actual bytes even when `Content-Length` is
missing, false, duplicated, compressed, or combined unsafely with transfer
encoding. Let the pinned Obscura HTTP stack reject invalid response framing;
the client rejects ambiguous header metadata when it remains observable and
always treats its completed/streamed actual-byte count as authoritative.

Use the configured raw-document limit for every completed main-response body,
including HTML. Preserve a separate fixed 20 MiB limit for the UTF-8 byte size
of serialized rendered DOM, because scripts may expand the DOM after the
response. Document that the DOM-size check occurs after the current API has
serialized it and therefore cannot prevent that transient allocation. Keep
search DOM at exactly 20 MiB. Increasing the raw-document limit must not raise
either rendered-DOM or search-DOM limit.

Retain five as both the stack-owned internal `OnyxWebCrawler` per-invocation
fetch-worker limit and the API-process-global browser admission limit, matching
the fixed five-worker Obscura topology. Patch the pinned
`DEFAULT_MAX_WORKERS` binding and add the process-global bounded semaphore as
needed. Do not add runtime validation for either internal literal, and do not
add `ONYX_OPEN_URL_MAX_CONCURRENT_FETCHES` or any other user-facing concurrency
or worker-count override. A deliberate source/Compose change to either internal
literal or the admission limit must re-run the combined document-limit,
parser-memory, waiting-thread, search-overlap, timeout, and worker-capacity
characterization. Increasing the user-facing raw-document limit should still
carry memory guidance, but it must not silently alter any internal concurrency
literal.

At the pinned release, `Fetch.takeResponseBodyAsStream` streams from an
already-buffered response. `OBSCURA_NETWORK_BODY_BUFFER_BYTES`,
`OBSCURA_IO_STREAM_MAX_BYTES`, and client-side chunk counting bound retention,
CDP accumulation, and parser input; they do not prevent the initial complete
network-body allocation. Derive the Obscura retention floor from the maximum
of the raw-document and HTML/search requirements and set the IO stream budget
and entry count for the bounded caller topology. Do not add runtime validation
for those internal retention literals.

Treat the initial full allocation as an explicit residual memory-denial risk,
not as a reason to refetch or bypass Obscura. Bound Obscura workers and Onyx
fetch/parse concurrency together, test oversized responses, and warn operators
to budget roughly 3 to 5 times the configured document limit per concurrent
large document, potentially more during parser expansion. This is an estimate,
not a memory bound.

The pinned main-resource loader alias may duplicate retained body storage.
Include that copy in memory characterization; do not require canonical alias
storage as a cutover gate when exact retrieved bytes, body state, and cleanup
are otherwise proven. Text-classified bodies remain semantic UTF-8 rather
than an original-byte channel. A PDF signal in a text-classified or untyped
body is a typed byte-identity-unavailable failure and is never passed to the
PDF parser.

Do not add a parser service, Unix socket, private volume, IPC protocol, parser
readiness dependency, or broad PDF-parser patch for this migration. Enforce the
byte cap before invoking Onyx's existing parsing entry points, then preserve the
pinned Onyx behavior: initial `pypdf` construction and metadata processing run
in the API process, PDFium text extraction uses Onyx's isolated-process timeout,
and PDFium failure can fall back to in-process `pypdf` page extraction. The byte
cap bounds parser input size but does not bound parser CPU, transient memory, or
wall time on every path. Treat malformed-document resource exhaustion as an
explicit inherited residual risk rather than claiming bounded parsing. A later
parser-isolation change requires its own focused reliability/security work; it
is not part of the direct-Obscura transport migration.

## Onyx `open_url` integration

Use Onyx's built-in **Onyx Web Crawler** as the wrapper-managed direct-Obscura
content provider and the documented recommended/default selection. Do not
configure Firecrawl as a prerequisite for this path or silently translate a
saved provider setting. Existing operators who want the direct path are
responsible for selecting the built-in crawler and removing or correcting any
stale provider settings through Onyx's supported administration interfaces.
The wrapper performs no database migration, provider-row rewrite, Admin UI or
API configuration patch, compatibility translation, or startup enforcement of
the operator's saved provider choice. Document this as an explicit upgrade
step and make clear that the direct path uses Onyx's existing 120-second Open
URL invocation behavior.

Continue to support an operator's deliberate selection of another upstream
Onyx provider, including Firecrawl or Exa. Do not reject, rewrite, or route
such a provider through the shared Obscura client. The Onyx-to-provider
connection still uses the wrapper's public Onyx egress bridge, public final-hop
destination policy, and selected VPN/upstream/no-VPN route; a provider client
that ignores that proxy has no direct fallback. After receiving the request,
the external provider owns its target fetching, retries, retention, logging,
training, and other data-policy behavior, which is outside this plan's
single-navigation and direct-Obscura guarantees. README/Admin guidance must
warn that requested URLs, document contents, account metadata, and usage can be
disclosed to that provider. An API key associates requests with an account; it
does not imply zero-data-retention, no training, or short retention. Operators
handling private research must verify the provider's current policy and enable
an applicable contractual or account-level ZDR control where available. Never
log provider API keys.

Create `onyx/patches/sitecustomize_api_server/sitecustomize.py` as the only
API-service bootstrap in both lite and full modes. It owns the strict direct
Obscura integration, the invocation state/deadline hook, the process-global
bounded semaphore, and every other API-only wrapper patch. Merge the current
anonymous lite `onyx/patches/sitecustomize/` bootstrap into this service-named
layer; retain its Open URL availability behavior only when the existing lite-
mode condition applies. The full-mode background service continues to use
`sitecustomize_background`, and code interpreter continues to use
`sitecustomize_code_interpreter`.

Turn `sitecustomize_base` into a non-bootstrap shared helper location, renamed
to a neutral path such as `onyx/patches/shared/`: it may contain reusable patch
functions imported explicitly by service layers, but it must not contain a
`sitecustomize.py` or apply patches merely because its directory is on
`PYTHONPATH`. Inventory the current helper functions and move API-only install
calls to `sitecustomize_api_server`; leave background-only install calls in
`sitecustomize_background`. A helper used by multiple services remains shared,
but each service bootstrap explicitly chooses whether to invoke it. Mount only
the service bootstrap plus shared helper directory needed by that service and
order `PYTHONPATH` accordingly. This cleanup clarifies ownership; it is not
permission to change unrelated patch behavior.

Patch the narrowest stable Onyx boundaries needed for the direct integration:
the outer `open_url` invocation/timeout callback, `OnyxWebCrawler.contents()`
batch propagation, direct-caller local state and admission, `_fetch_url`, plus
only imported provider/limit bindings that must change for configured values
to take effect.
At startup, verify the expected modules, class/method signatures, 120-second
timeout structure and callback, context-copy boundary, nested executor shape,
requests-first/local-Playwright source shape, PDF/HTML helper signatures,
shared-client import, and pinned source/protocol patch points without requiring
a live browser connection. These strict source-shape checks protect runtime
patch application; they do not parse or validate stack-owned internal
parameters. Any static mismatch prevents API readiness in strict mode; live
Obscura capability and transport failure remains request-local when `open_url`
is invoked, preserving optional browsing startup semantics.

When **Onyx Web Crawler** is selected, the patched transport must not call
`ssrf_safe_get`, another requests client,
upstream `fetch_rendered_html`, local Chromium, CRW, Firecrawl, Obscura CLI, or
a second Obscura navigation. Preserve upstream `contents()` ordering and
per-URL failure isolation: parallel fetches may complete out of order, but
outputs remain aligned with input URLs and one failure does not discard
successful peers. Deliberately selected non-built-in providers do not pass
through this patched transport.

Do not use terminal `WebContent.link` as the correlation key for the built-in
provider. In the patched `_fetch_web_content`, require
`OnyxWebCrawler.contents(urls)` to return exactly one element per input, then
positionally zip the requested URLs with those results and fail the whole
batch visibly on a cardinality mismatch. Carry an internal crawled record with
`requested_url`, `terminal_url`, and the resulting section. Use
`requested_url` for input ordering, `url_snippet_map` lookup, `FailedFetch.url`,
indexed-result preference, deduplication, and the lookup performed by
`_merge_indexed_and_crawled_results`. Use `terminal_url` for
`WebContent.link`, the section's source link/citation, and document metadata.
Build the merge map from the internal requested-URL field, never by recovering
it from `section.center_chunk.source_links`. Normalize requested keys with the
same upstream function on both sides. This preserves a successful redirected
crawl as the fallback for its original request while truthfully citing the
terminal page. Keep deliberately selected external-provider behavior outside
this built-in-only pairing patch.

Preserve Onyx's outer timeout response instead of inventing an LLM-facing
"browser busy" failure. A URL that spends its remaining invocation budget
waiting for the API semaphore starts no navigation; if no indexed or crawled
peer succeeded, upstream's existing `The call to open_url timed out` result is
the visible outcome. If indexed or crawled peers did succeed, preserve the
current partial-result behavior. Once the outer call finalizes, background
workers may only finish an already-sent navigation and cleanup; they cannot
start a target/navigation, publish content, or overwrite caller-visible state.

Disable retries that would create another main-document navigation. Any future
retry policy is a user-visible change to the single-navigation guarantee and
must identify each deliberate attempt in results and diagnostics rather than
appearing as an internal transport retry.

Classify the one navigation using final URL, status, normalized MIME,
Obscura's mirrored binary/text classification, and at most one bounded prefix
from the retained body. Preserve PDF detection by final URL, MIME, and `%PDF-`
magic, but parse only binary-classified exact bytes. Only `text/html` and
`application/xhtml+xml` use rendered DOM and the existing cleanup/metadata/link
pipeline. The raw-text MIME allowlist is exactly `text/plain`,
`text/markdown`, `text/csv`, `text/tab-separated-values`, `application/json`,
`application/ld+json`, `application/xml`, `text/xml`, `application/yaml`,
`application/x-yaml`, `text/yaml`, `text/x-yaml`, `application/toml`,
`text/css`, `application/javascript`, and `text/javascript`.

Also accept a supported source path only when its normalized MIME is absent,
`text/plain`, or `application/octet-stream`, and its final path has exactly one
of these case-insensitive suffixes: `.txt`, `.md`, `.rst`, `.csv`, `.tsv`,
`.json`, `.jsonl`, `.xml`, `.yaml`, `.yml`, `.toml`, `.ini`, `.cfg`, `.conf`,
`.log`, `.py`, `.js`, `.mjs`, `.cjs`, `.ts`, `.tsx`, `.jsx`, `.java`, `.c`,
`.h`, `.cc`, `.cpp`, `.hpp`, `.cs`, `.go`, `.rs`, `.rb`, `.php`, `.sh`,
`.bash`, `.zsh`, `.fish`, `.sql`, `.css`, `.scss`, or `.less`. A suffix never
overrides an explicit conflicting MIME. Binary-classified raw text is decoded
strictly from exact bytes using only the charset allowlist above.
Text-classified raw text follows the potentially-lossy semantic UTF-8 contract:
matching counts do not establish byte identity, a count mismatch fails, and a
declared non-UTF-8/ASCII charset fails.

DOCX, PPTX, XLSX, EML, EPUB, images, unsupported binary, corrupt/encrypted or
image-only documents, and empty content retain explicit unsupported or
unparseable outcomes without OCR, a generic file-parser fallback, or a second
fetch. Preserve post-extraction per-URL and total character budgets separately
from byte limits.

Treat accepted JSON, XML, YAML, and source formats only as bounded passive
text. Do not resolve XML entities, schemas, or includes; construct YAML
objects; execute source; or dereference embedded URLs. Optional syntax
validation must remain networkless and resource-bounded. Adding an archive,
office, email, or other active parser later requires a separate format and
resource-limit review; it must not arrive through a generic-parser fallback.

Adapt direct-client failures through one small built-in-crawler boundary to the
ordinary `WebContent` form that the pinned Onyx `open_url` pipeline already
expects: `scrape_successful=False`, empty content, the requested link, and a
concise non-secret `failure_reason`. Do not mirror the SearXNG exception
taxonomy, add per-search-engine response shapes, or special-case each HTTP
status when the same normal Onyx failure result is sufficient.

## SearXNG direct engine integration

Convert every custom browser-backed engine to SearXNG's direct callback
contract using `engine_type = "offline"` and `search(query, params)`. Re-audit
the exact offline processor contract at the committed SearXNG pin. The
"offline" label means the engine callback owns the Obscura navigation; it does
not mean the engine is network-free. The ordinary online `request()` /
SearXNG HTTP-client / `response()` path must not run, because that would create
an extra origin request or require a fake response.

Rename `_crw.py` to `_obscura.py` atomically and update every importer. Before
cutover and every SearXNG pin change, enumerate all `_crw`/`_obscura` importers
and all enabled custom engines rather than assuming the current list is
permanent. The current inventory is `google2`, `bing2`, `duckduckgo2`,
`brave2`, and `startpage2`.

Each engine must preserve its current URL/query encoding, locale, safe-search,
time range, category, and pagination construction; perform one Obscura
navigation with `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH`; obtain the bounded
rendered DOM; and pass it to a pure parser helper. Preserve selectors, final
result URL normalization, title/content extraction, language/category
metadata, no-results detection, and scoring inputs. Map 403, 429, CAPTCHA,
consent/block pages, unexpected provider landings, timeout, empty/oversized
DOM, and CDP loss to explicit pinned exception/result forms. Classify these
conditions from the terminal status, URL, and DOM before the organic parser.
A genuine provider no-results page remains a successful empty result; a page
that is empty or structurally unexpected because access was blocked does not.
No shared-client or Playwright layer may perform an invisible retry.

The pinned `OfflineProcessor` sends every exception through
`handle_exception(..., suspend=False)`, unlike the online processor's standard
handling of provider blocking exceptions. Patch only that processor boundary
so `SearxEngineCaptchaException`, `SearxEngineTooManyRequestsException`, and
`SearxEngineAccessDeniedException` take the same `suspend=True` path they use
in the online processor. Leave SearXNG's configured suspension durations,
continuous-error accounting, timeout handling, unresponsive-engine records,
and round-robin decisions authoritative. Do not implement suspension timers,
retry loops, attempt counters, or provider selection in `_obscura.py`, the CDP
client, or an individual engine.

Use this outcome mapping. The last column records only what the engine returns
or raises; subsequent provider selection is owned by the existing SearXNG
orchestration patch:

| Observed outcome | Engine outcome |
| --- | --- |
| Engine-specific no-results selector matches on an allowed terminal host, with no block marker | successful empty result |
| HTTP 401/402/403, `consent.google.com`, consent/access-denied landing, blocking empty DOM, or unexpected terminal host | `SearxEngineAccessDeniedException` |
| HTTP 429 or explicit too-many-requests landing | `SearxEngineTooManyRequestsException` |
| CAPTCHA/human-verification marker | `SearxEngineCaptchaException` |
| HTTP 404/5xx, final-hop denial, navigation timeout/failure, oversized body/DOM, missing event barrier/body, or CDP/stream transport loss | visible `SearxEngineResponseException` or the closest standard SearXNG transport exception |
| Allowed-host DOM that has neither the engine's result selector nor its explicit no-results selector | visible `SearxEngineResponseException`; identify selector incompatibility without DOM content |
| Invalid query/page/locale/time-range input | `ValueError`, using the pinned offline processor's established invalid-input contract |
| Engine code, parser syntax, invariant, or configuration error | visible implementation failure |
| Provider already active or inside its minimum-start interval | visible `SearxEngineResponseException`; do not wait or retry inside the engine |

The pinned `OfflineProcessor` handles `ValueError` as invalid engine input: it
logs the invalid-input condition but does not add an unresponsive-engine
record, suspend the provider, or make the round-robin patch eligible to retry
another provider. Preserve that contract rather than raising
`SearxParameterException`, which the pinned offline processor catches as a
generic engine failure. Keep the `ValueError` message bounded and omit the
query text and other secret-bearing values. Tests must prove invalid input
does not trigger provider fallback or query disclosure.

Do not infer “no results” merely from zero parsed result cards. Each engine must
commit a narrow `no_results_xpath` backed by a captured sanitized fixture; if
no reviewed selector exists, zero cards take the selector-incompatibility row.
Before either the no-results or organic-result selector, evaluate the exact
shared block-marker XPath for a title containing case-insensitive `captcha`,
`access denied`, `verify you are human`, `unusual traffic`, or `too many
requests`, or a form action containing `/sorry/`, `/captcha`, `/sp/captcha`, or
`turing`. The direct client has already evaluated bounded visible text, terminal
challenge routes/titles, and visible prompts paired with challenge form/iframe
structure while excluding inactive script/style/template/noscript content.
An iframe source alone is not a blocking signal. Retain the existing narrower
Bing and Startpage CAPTCHA selectors as additional engine-specific markers.
Any marker change requires a sanitized positive and negative fixture so
ordinary result text or bundled challenge libraries cannot trigger it.

Return `EngineResults` or the exact result-list form required by the pinned
offline processor. Remove CRW API URL/key handling, CRW JSON-envelope parsing,
`enable_http: true` settings that existed only for CRW, and fake HTTP response
construction. Engine and processor tests must prove that every empty, blocked,
timeout, transport, and parser outcome reaches the ordinary SearXNG result or
unresponsive-engine path. A provider selected later by SearXNG's existing
round-robin patch is a distinct search attempt that can disclose the query to
that provider; redirects and subresources of one navigation are not extra
provider attempts.

Build a small derived SearXNG image from the committed base-image tag.
Install the audited Playwright client from committed `requirements.in` and a
hashed lock file, but do not install or download Chromium because Obscura owns
the renderer. Copy the shared client and custom engines into the image,
validate imports at build time, record the resulting wrapper image tag in
`stack.versions.env`, and add Makefile image-ready/build and Python dependency
upgrade coverage. Perform no package installation at container startup. Build
proxy/network behavior remains the existing explicit build-time behavior and
must not depend on Myst runtime readiness or application egress proxies.

## SearXNG scheduling and timeout ownership

Set `GRANIAN_WORKERS=1` explicitly and prevent horizontal SearXNG scaling
unless the scheduler is redesigned. Keep the process-wide provider admission
state in the SearXNG patch/helper layer, never in the shared CDP client. Key it
by stable provider identity and allow one active navigation per provider plus
at most one navigation start in any exact 3.0-second monotonic interval, with
zero jitter. Reserve before the engine thread/navigation is admitted, record
the start immediately before the sole `Page.navigate`, and hold the active
lease through DOM handling and target/session/connection cleanup. A busy or
cooling provider is unavailable; do not create a waiting engine thread.
Redirects and subresources stay charged to the selected provider. These are
reviewed SearXNG implementation constants, not parameters to parse or validate
at runtime.

Preserve the behavior of the existing SearXNG wrapper patches rather than
introducing a new CDP-level fallback or deadline system:

- With `SEARXNG_ROUND_ROBIN=true`, when the configured custom-provider pool is
  present, drop other selected engines, rotate among one available normal
  custom provider, and use a provider marked `last_resort: true` only when no
  normal provider is available. If that provider produces no main results and
  records itself unresponsive, the existing `Search.search_standard()` patch
  may select another untried provider. Its current request-start/timeout
  semantics remain unchanged.
- With `SEARXNG_ROUND_ROBIN=false`, preserve ordinary SearXNG selected-engine
  fan-out. The last-resort scoring patch continues to demote last-resort-only
  results and treat last-resort matches as confirmation of normal results.
- Suspended, busy, and cooling providers are unavailable to selection. Search
  engines surface standard success, empty-success, or failure outcomes;
  SearXNG alone decides whether an engine is suspended and whether another
  provider is tried.

Do not add an absolute cross-attempt deadline, custom finalization/result-write
gate, attempt token, fallback budget, cancellation protocol, or timeout reset
logic for direct Obscura. Retain the configured SearXNG engine timeout and
stock worker timeout behavior. If an engine thread outlives the HTTP wait,
SearXNG's existing late-result handling remains authoritative, while the
thread keeps its provider lease until the single navigation and cleanup finish.
Never respond to a timeout by reconnecting, starting another navigation inside
the engine/CDP client, or releasing a lease while its renderer work is live.

Apply strict source-shape checks only to the upstream SearXNG methods that the
offline-processor, round-robin selection/retry, and last-resort scoring patches
replace. Do not add runtime validation of the patches' own provider list,
timeouts, worker count, interval, jitter, suspension values, or other
non-user-facing constants. Wrapper-owned logs must omit query text and URLs
containing query values. Document that disabling round robin can disclose the
same query to every selected engine and increases renderer concurrency and
rate-limit exposure.

The fixed five-worker Obscura topology allows other providers and `open_url`
to make progress while one child is occupied, subject to the documented
round-robin lack of affinity. A test-only one-worker characterization
demonstrates serialized navigation-related CDP work, but one worker is not a
user-facing low-memory configuration. Changing the production literal
requires the topology, capacity, memory, logging, and browser-state re-audit
described above.

`open_url` may overlap SearXNG and can still contribute to upstream 403/429;
that tradeoff is documented rather than hidden behind a global scheduler.

The one-navigation guarantee counts the main-document navigation. Redirects
and browser subresources intentionally create additional HTTP requests, but
must remain within the same target, final-hop policy, deadline, and provider
lease; client code must not initiate an independent probe or refetch.

## Browser state and trust

Use the same stable Obscura fingerprint configuration on every renderer worker
and explicit navigation waits. On the CDP connection selected for each
request, use the audited root/browser-level CDP command form on that selected
worker connection to invoke only the best-effort clearing commands verified
against the audited Obscura source and separately exercised tagged image before
creating the request target.
At the audited pin this means `Network.clearBrowserCookies`; do not vaguely
claim that all browser storage was cleared, use `Target.createBrowserContext`
as a clearing mechanism, or claim that the compatibility context provides
isolation. Add another clearing command only after proving its scope,
ordering, and multi-worker behavior against the pin. A clearing failure fails
that request.

Cookie clearing operates on the selected worker's shared default cookie jar.
At Obscura `v0.1.10`, the worker defers foreign non-`Fetch` CDP commands while
a navigation is active. A request's clear can therefore wait behind another
same-worker navigation, but it must complete before that request creates its
target; it is not expected to perturb that already-active navigation midway
through.
Account for that deferred wait in request latency and retain any provider
reservation/lease while applicable. Test the ordering against the tagged image:
the clear remains pending during the earlier navigation, then succeeds before
target creation, and clear failure fails the request.

A fresh target separates page/session runtime state but does not provide a
first-party, user, cookie, or general storage boundary. Do not claim
localStorage or IndexedDB clearing/persistence behavior that the pin does not
implement.

Do not persist browser state with `--storage-dir`, rely on the multi-worker
launcher to propagate it, or add a state-clearer sidecar, readiness epoch,
shared state volume, or stack-wide health gate.

Best-effort cookie clearing is not a request boundary or isolation guarantee.
The pinned worker serializes individual CDP commands and defers a clear behind
an already-active navigation, but it does not make one connection's
`clearBrowserCookies` -> target creation -> `Page.navigate` sequence atomic
against commands from another connection. Another client can interleave after
the clear and before navigation, can introduce cookies that the later request
then observes, or can clear cookies during another request's pre-navigation
window. Fresh targets share the same default jar, state outside the one
verified cookie command remains unknown, and different children have
independent in-memory state. Treat the clear only as exposure reduction, test
two-client interleavings explicitly, and disclose that cross-request cookie
state can still occur. Any upstream change to command dispatch or navigation
concurrency requires this behavior to be re-audited. If stronger guarantees
are needed, prefer separate Obscura instances or upstream-supported isolated
contexts over claiming that this best-effort mechanism is sufficient.

Onyx and SearXNG share full CDP authority at the pinned version. Network
placement excludes other services but does not provide confidentiality or
integrity isolation between those clients. Document this residual risk.

Also document, without adding a local workaround, the audited connection-
arrival leak: when a new CDP connection reaches a worker during an active
navigation, the pinned server can create a blank page/session before deferring
the connection's commands, while disconnect cleanup does not reclaim that
pre-dispatch state. Concurrent connection churn can therefore retain hidden
pages/sessions and their memory until the worker restarts. Re-audit this source
path on every Obscura pin change.

## Readiness and failure behavior

Optional browsing readiness is local and does not gate core Onyx startup:

```text
selected routing substrate -> final-hop proxy -> fixed bridge
Obscura local CDP -> CDP gateway and SearXNG
Onyx API does not wait for the browsing chain
```

Proxy readiness validates configuration and DNS/upstream substrate without an
arbitrary public fetch. Bridge readiness traverses the fixed hop and expects a
blocked-target denial. Obscura health checks the local CDP frontend shape.
Because the pinned multi-worker frontend routes probes round-robin and does not
provide an aggregate child-health API, that probe does not prove every child
healthy. A failed child produces a visible CDP/502 failure for assigned
requests; other children remain usable. Do not add periodic public probes or a
custom worker supervisor solely to hide this limitation. SearXNG may wait for
its browser dependency; Onyx verifies CDP capabilities when `open_url` is
invoked, without blocking core startup. Mode-required data and embedding
dependencies remain unchanged.

Failure of Obscura, a bridge, final-hop proxy, Myst, or configured upstream
fails the dependent request path. There is no direct, cross-listener,
local-Chromium, CRW, or plain-HTTP fallback. Only Myst retains VPN-mode
autoheal.

Exercise recovery explicitly: VPN-enabled initial connection; explicit
no-VPN startup without an `autoheal` service; VPN loss and recovery without
direct egress or an application-tier restart storm; supported upstream-proxy
mode with remote target DNS; Obscura restart followed by a clean connection on
the next request rather than reuse of a stale target/body; and final-hop proxy
failure with a visible fail-closed request error. Do not label every stateless
bridge or gateway for autoheal. Health checks, entrypoints, and runtime patch
initialization must use preinstalled tools and must never run a package
manager, `pip`, or a browser download.

## Implementation sequence

Perform the migration as one reviewable sequence with an atomic runtime
cutover:

1. Re-run the implemented `onyx_network_isolation.md` acceptance checks and
   inspect the effective prerequisite topology first. Do not implement this
   migration against a restored shared-namespace or transitional CRW network
   model. Then add characterization fixtures before changing transport behavior. Lock
   current Onyx HTML/PDF/text/status/ordering behavior; every custom engine's
   URL construction, sanitized DOM parsing, error mapping, suspension,
   round-robin and scoring; wait selection; provider start control; final-hop
   denial/DNS behavior; and full-mode local PDF ingestion.
2. Implement and fake-CDP-test the shared client, source-shape anchors,
   post-command event barrier, best-effort cookie clear and interleaving model,
   main-response selection, body classification, limits, redaction, and
   cleanup. Then test the tagged, unmodified Obscura pin. A failure of byte
   identity, body state, wait/event semantics, characterized cookie behavior,
   or proxy enforcement blocks cutover.
3. Build the derived SearXNG image, convert all custom engines to the offline
   contract, add process-wide provider admission, and adapt the existing
   round-robin, last-resort, offline-processor, and suspension patches without
   adding CDP/engine retries or a new timeout system. Create the
   service-named API patch layer and neutral shared-helper directory, then add
   the strict Onyx invocation deadline/finalization, blocking admission, and
   requested/terminal redirect correlation and crawler transport patches in
   both modes while the old runtime path still remains the deployed path. Do
   not add database, saved-provider migration, Admin UI/API configuration, or
   provider-choice enforcement code.
4. In one Compose/Makefile cutover, add the final CDP gateway and direct caller
   configuration, switch Onyx and SearXNG together, and remove CRW, validation
   DNS, the CDP shim, CRW-only networks, secrets, pins, images, build targets,
   health dependencies, environment names, and compatibility aliases. Do not
   ship a mixed topology in which either caller can silently use the old path.
5. Render effective lite/full and optional-overlay models, run the request and
   routing matrix, search the repository for stale `crw`, `CRW_`, `cdp-shim`,
   `PREFETCH_`, and Firecrawl wrapper references, and manually classify any
   historical/upstream names that legitimately remain. Update README,
   AGENTS.md, request/routing/security docs, patch/upgrade docs, and local-RAG
   docs before marking the plan implemented.

The removal inventory must be concrete rather than satisfied by a generic
stale-name search. Reconcile the current tree and effective Compose models,
then remove or rename at least:

- the `crw`, `crw-validation-dns`, `crw-service-gateway`, CRW prefetch bridge,
  prefetch-only policy service, and `cdp-shim` services and dependencies;
- `crw/cdp_shim.py`, its Dockerfile and dependency locks,
  `crw/validation_dns.py`, CRW-envelope/validation-only tests, and obsolete
  CRW-specific Compose fragments;
- `CRW_IMAGE`, `CDP_SHIM_IMAGE`, CRW API/render/PDF/prefetch/validation/timeout
  variables, generated CRW credentials, `CDP_SHIM_*`, `STRIP_PROXY_SERVER`,
  `PREFETCH_*`, search-host policy modes, and shim-only CDP URLs;
- CRW/CDP-shim image-ready, build, startup, upgrade, and dependency-update
  prerequisites; replace `crw-service-gateway` with
  `obscura-cdp-gateway` in both uppercase and lowercase forms of the
  stack-owned Onyx `NO_PROXY` list rather than retaining an obsolete alias; and
- old wait injection, stealth-script stripping, context-proxy stripping,
  shim target coordination, shim tracing, and periodic-clearer artifacts after
  their direct-client replacements are proven; and
- the anonymous `onyx/patches/sitecustomize/` lite bootstrap and executable
  `sitecustomize_base/sitecustomize.py`, after their behavior is inventoried
  into `sitecustomize_api_server`, `sitecustomize_background`, and the neutral
  non-bootstrap shared-helper directory without changing unrelated semantics.

Move genuinely generic final-hop policy code and tests out of misleading
`crw`/`prefetch` names rather than deleting the retained enforcement. Preserve
unrelated patches for lite `open_url`, character budgets, helper proxy routing,
full-mode document freshness, Deep Research/tool batching, code-interpreter,
and local embedding. Compare the complete runtime-patch inventory in
`docs/onyx_patch_info.md`; an unclassified patch is a release blocker.

Roll back the Direct Obscura migration as one unit: restore its previous
Compose, pins, patches, and CRW/CDP-shim request path together while retaining
the already-implemented Onyx network-isolation prerequisite. Never partially
restore only CRW or only the shim, and never move Onyx back into the Myst
namespace. Old images or volumes may be cleaned up only through normal
documented cleanup after rollback confidence; destructive cleanup is not part
of startup or cutover.

## Documentation and operator migration

Documentation changes are part of the atomic cutover, not follow-up work. Do
not leave current runtime documentation describing CRW, Firecrawl as a
wrapper-required/default path, the CDP shim, prefetch blocking, periodic cookie
clearing, or removed service names as active behavior. Preserve the separate
supported-external-provider privacy warning. Prefer deleting obsolete
operational prose to retaining a historical appendix that can be mistaken for
a supported path.

Update each owner document as follows:

- `README.md`: replace the CRW/Firecrawl architecture, Admin setup, component
  lists, diagrams, first-run procedure, image-build instructions, health
  guidance, and environment table. Recommend the built-in **Onyx Web Crawler**
  and tell operators to select it when they want the wrapper-managed direct
  path; remove any claim that a Firecrawl base URL/key is required. Document
  deliberate Firecrawl, Exa, and other upstream Onyx provider selection as
  supported through the public Onyx egress route but outside the direct-
  Obscura/one-navigation guarantee. Explain that the provider's own target
  fetching and data handling follow its current retention, training, and ZDR
  policy and that an API key associates the request with an account. Explain
  the one-main-navigation guarantee for the built-in crawler, 50 MiB default
  and larger-value memory tradeoff, separate search/web wait settings, fixed
  internal five-worker capacity, SearXNG-owned provider scheduling and
  suspension, and the fact that `open_url`, helpers, and enabled executors
  remain unscheduled. State that enabled executors may reach public search
  engines through the shared public policy and explain the default
  one-provider selection/retry semantics and query-disclosing fan-out of
  `SEARXNG_ROUND_ROBIN=false`.
- `docs/request_handling.md`: replace both CRW-centric request diagrams with
  direct Onyx/SearXNG-to-Obscura flows. Document raw `Page.navigate`, exact wait
  ownership, main frame/loader/request tracking, redirect and subresource
  accounting, DOM versus same-navigation body retrieval, MIME/URL/magic
  dispatch, binary-byte guarantees, the always-potentially-lossy semantic
  contract for text-classified bodies, exact raw MIME/extension/charset
  allowlists, byte versus
  character limits, in-process parsing, typed failures, post-command event
  completion barrier, the Onyx 120-second invocation deadline, blocking
  process-global API admission and its process-local/non-FIFO/waiting-thread
  limits, no post-finalization navigation, best-effort cookie clearing plus its
  pinned deferral and non-atomic interleaving, shared CDP authority, provider
  leases through cleanup, requested-versus-terminal redirect correlation,
  operator responsibility for selecting the built-in provider and correcting
  stale saved settings without a wrapper database/configuration migration,
  standard Onyx `open_url` failure results, existing SearXNG round-robin,
  last-resort, timeout, unresponsive-engine, and suspension ownership,
  disabled-round-robin fan-out, possible query disclosure to more than one
  provider, wrapper-owned diagnostic redaction, pinned
  Obscura logging gaps/full-URL risk, the narrow scope of
  `--allow-file-access`, the accepted upstream ES-module local-file read path,
  the container impact reductions that do not repair it, and the absence of
  any refetch or direct fallback. Scope these guarantees to the built-in
  crawler and custom search engines; document that deliberately selected
  external content providers are
  reached through the wrapper's public Onyx egress route while the provider
  owns its subsequent target fetching and data policy. State that full-mode
  local document ingestion is unchanged and does not use Obscura. Also document
  the pinned concurrent-connection hidden-page/session retention limitation;
  the network body buffer's per-body, entry-count-multiplied retention and
  base64/alias copies; the IO stream store's distinct aggregate accounting; and
  why these still do not form an aggregate process-memory bound.
- `docs/vpn_routing_and_proxies.md`: replace CRW, prefetch, and shim topology,
  service tables, health edges, and `NO_PROXY` ownership with the CDP gateway,
  direct SearXNG attachment, browser bridge, and shared public final-hop proxy.
  Require both stack-owned `NO_PROXY` forms to contain only the new
  `obscura-cdp-gateway` service name for this path, with no retained CRW alias.
  Remove search-host blocking modes. Preserve and re-document the distinct
  public/host Onyx bridges, optional executor bridge, VPN/upstream/no-VPN DNS
  matrix, exact host/RFC1918 exceptions, no-VPN absence of autoheal, trusted
  `netns-holder` co-residence, and fail-closed reconnection behavior. Make clear
  that only internal service names use Docker DNS; browser target DNS remains
  at the selected final hop. State that executor search-host denial is
  deliberately removed and public Onyx, browser, and executor bridges share
  the public proxy process while retaining distinct networks and peer checks.
  Distinguish wrapper-resolved, address-pinned denial from the residual private
  resolution/rebinding risk of an operator-selected remote-DNS upstream proxy.
- `docs/internal_network_security.md`: update reachability tables and trust
  boundaries for direct CDP clients, the narrow Onyx gateway, SearXNG's control
  attachment, Obscura workers, and the distinct fixed bridges. Document that
  Onyx and SearXNG have mutually non-isolated CDP authority, fresh targets are
  not cookie/user boundaries, cookie clearing is best-effort, the pinned clear
  is deferred behind active same-worker navigation but is not atomic with the
  following target/navigation across clients, non-cookie state remains
  unverified, and multi-worker state is per worker. Retain canonical
  internal-name/address denial, redirect validation, no-target-Docker-DNS,
  Onyx SSRF interaction, shared-public-proxy failure domains, and the initial
  full-body allocation risk. Record both pinned availability/memory limitations:
  concurrent connection arrival can retain an unreachable blank page/session
  until worker restart, and the network body byte limit applies per entry with
  entry-count, base64, and alias amplification. Distinguish the IO stream
  store's aggregate limit without presenting it as an aggregate process-memory
  bound. Carry the
  same remote-DNS limitation without claiming address-level private-target
  denial when resolution is delegated.
- `docs/onyx_patch_info.md`: add the strict direct `OnyxWebCrawler` patch,
  service-named API bootstrap, neutral shared-helper ownership, shared-client
  import, invocation deadline/finalization state, deadline-bounded blocking
  semaphore, content dispatch, limits, and in-process parser boundary, scoped
  so deliberately selected external Onyx providers remain upstream-owned;
  update the SearXNG section for offline engines, explicit
  blocking-condition suspension through the ordinary processor, pre-thread
  provider reservation, preservation of the current round-robin retry and
  last-resort scoring patches, disabled-round-robin fan-out, stock timeout and
  late-result handling, and the derived image. Delete
  CRW/shim patch descriptions while retaining and
  accurately scoping helper proxy routing, lite `open_url`, local-document,
  character-budget, Deep Research, code-interpreter, and other unaffected
  patches.
- `docs/onyx_patches_upgrade.md`: add source-shape and runtime audit anchors for
  the service-named bootstrap/shared-helper split, Playwright `1.58.0`, the
  outer `open_url` timeout callback and context-copy boundary,
  `OnyxWebCrawler` nested executor, deadline/finalization propagation, global
  blocking admission and exact release, PDF/HTML helpers, SearXNG offline
  processors/orchestration, Obscura waits, worker routing, context/cookie
  behavior, deferred cookie commands, terminal redirect event/request/loader
  identity, body aliases, `is_text_like_content_type`, body streams, launcher
  logging behavior, and IO cleanup. Identify Obscura `v0.1.10` and its exact
  audited Git ref in the worker-count, navigation-deadline, redirect, state,
  body, and logging limitation and upgrade-check text. Add the capability,
  one-fetch, existing round-robin/suspension behavior, provider-lease cleanup,
  multi-worker, byte-identity, conversion, memory, and deletion-assertion tests.
  Require the upgrade audit and resulting docs to revisit the hidden-page/
  session retention and per-body network-store/aggregate IO-store distinctions.
  Removed CRW,
  validation-DNS, prefetch, and shim steps become absence checks so an upgrade
  cannot reintroduce them accidentally.
- `docs/local_docs_rag_search.md`: state that doc-drop crawling, PDF freshness,
  embedding, and `internal_search` retain their current paths; local doc-drop
  URLs are never sent to Obscura. Update only component or helper-route names
  made stale by the migration.
- `.env.wrapper.example`: expose and explain
  `ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB`,
  `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH=load`, and
  `OBSCURA_BROWSER_WAIT_UNTIL_WEB=domcontentloaded`, including allowed wait
  values and memory warnings. Retain documented operator controls such as
  `SEARXNG_ROUND_ROBIN`, including its default one-provider selection with
  SearXNG-owned same-request retry and its disabled concurrent fan-out/query-
  disclosure semantics, executor networking and allowed public
  search-engine access, VPN/upstream routing, HTTP, and
  RFC1918 policy with their new semantics. Remove the periodic
  `OBSCURA_BROWSER_CLEAR_COOKIES_INTERVAL` control and all CRW, shim, prefetch,
  search-block, and removed-policy settings without compatibility aliases.
  Do not add an open-URL concurrency option. Keep CDP endpoints, navigation
  safety and event-completion deadlines, both internal five-worker literals,
  scheduler internals, trace settings, retention limits, and helper `NO_PROXY`
  stack-owned.
- `AGENTS.md`: replace the CRW runtime map, key locations, build commands, and
  request-handling invariants with the shared client, derived SearXNG image,
  direct Obscura flow, SearXNG-only scheduling, deadline-bounded blocking API
  admission, service-named `sitecustomize` ownership, distinct bridges, and
  best-effort deferred cookie-clearing model. Preserve privacy, fail-closed
  routing, build-time dependency, patch-upgrade, and full-stack validation
  rules.
- `stack.versions.env` and plan records: remove CRW/shim pins, add the derived
  SearXNG wrapper tag, and keep the tested Obscura tag and independently
  audited source identity.
  Add supersession notes to implemented plans whose final topology still shows
  transitional CRW components, without rewriting their historical decisions.

Use the following ownership model consistently across diagrams and prose; do
not leave a removed CRW/shim responsibility without a named replacement:

| Behavior | Final owner |
| --- | --- |
| single navigation, main-response identity, body/DOM retrieval, redaction, and cleanup | shared in-process Obscura client |
| rendering, stable fingerprint, retained response, and finite server navigation deadline | Obscura worker selected for the CDP connection |
| HTML cleanup, raw-text decoding, PDF extraction, and character budgets | Onyx process |
| search URL construction and DOM parsing | custom SearXNG offline engines, each performing one attempt with no retry |
| provider selection/retry, leases, suspension, stock timeout handling, and last-resort scoring | SearXNG processors and single-process wrapper orchestration patches honoring `SEARXNG_ROUND_ROBIN` |
| search versus web lifecycle wait | each caller using its validated `OBSCURA_BROWSER_WAIT_UNTIL_*` value |
| post-navigation synthetic event completion barrier | shared client awaiting the matching stopped-loading event before reading listener state |
| best-effort deferred cookie clear | shared client on the selected worker connection before target creation, without atomicity across clients |
| private/internal denial, redirect/subresource enforcement, target DNS, and selected route | public final-hop proxy and configured final route; address-level denial is not claimed when an operator delegates DNS to a remote upstream proxy |
| Onyx-to-external-content-provider route | public Onyx egress bridge, public final-hop policy, and configured final route |
| external provider target fetching and data handling | deliberately selected external content provider under its own current policy |
| CDP reachability | internal networks and the narrow Onyx gateway |
| upgrade/source compatibility | strict runtime patches, image builds, capability tests, and upgrade documentation |

For operator rollout, record the tested pre/post effective service inventory
rather than promising one unconditional container-count reduction across all
optional overlays. The upgrade notes must require selecting Onyx Web Crawler
when the operator wants the direct path, stopping the old stack, starting the
complete new topology, and verifying
health, provider searches, HTML/PDF/raw `open_url`, exact binary fixtures and
semantic text results, DNS route,
egress identity, denial cases, and full-mode local RAG before cleanup. Do not
run old and new request paths concurrently.

Deployment moved this record to `docs/plans/implemented/obscura_direct.md`,
updated its status and normative-document links, rewrote the prerequisite link
to `onyx_network_isolation.md`, and updated the prerequisite plan's backlink.

The final documentation must also disclose residual risks without presenting
them as implementation failures: CDP is powerful and network-confined rather
than authenticated; CONNECT enforcement cannot inspect encrypted paths or
response content; Obscura fully buffers a response before downstream limits;
its network body byte limit applies per retained body, the entry count and
base64/alias copies can multiply memory, and the distinct aggregate IO-stream
limit does not cap total process memory; the pinned entry eviction can remove
the main Document before its loader alias is created, so same-navigation HTML
uses its rendered DOM while formats requiring original bytes fail visibly; a
CDP connection accepted while its selected worker is
navigating can leave an otherwise unreachable blank page/session retained
until worker restart because the pinned disconnect path does not clean that
pre-dispatch state;
one main navigation can include redirects and many subresources; browser
workers have shared default-context state and no per-user isolation;
the disabled `--allow-file-access` flag blocks direct top-level CDP file
navigation but does not prevent the pinned ES-module local-file read path, and
container hardening only reduces that upstream issue's impact;
best-effort cookie clears are not atomic with the following navigation and can
be interleaved by another client; Onyx and SearXNG can disrupt one another
through CDP; the API semaphore bounds active work but not waiting threads, is
not FIFO, is process-local, and reserves no capacity against SearXNG;
provider-unaffined round-robin workers have incomplete aggregate
health; the pinned multi-worker launcher
discards renderer stdout/stderr and the single-worker code path may log full
URLs; unscheduled `open_url`/helper/executor requests can contribute to
search-engine throttling; deliberately selected external Onyx content
providers are reached through public Onyx egress but process disclosed data
according to their own retention/training/ZDR policy; the shared public proxy
is a failure/contention domain; SearXNG's existing same-request retry after an
unresponsive engine can disclose the same query to more than one provider;
disabling round robin deliberately fans the same query out concurrently and
can leave one late attempt per provider; a remote-DNS
upstream proxy can resolve an allowed public name to an address the wrapper
cannot validate, so address-level private-target denial is not guaranteed in
that mode; and explicit no-VPN mode intentionally uses the
system-routed final hop while preserving destination policy and application
isolation.

## Validation

Keep the new tests discoverable under `tests/` with focused modules. The
expected initial layout is:

- `test_obscura_cdp_client.py` for protocol, lifecycle, body, validation, and
  redaction behavior;
- `test_onyx_obscura_crawler_patch.py` for strict patching, content dispatch,
  limits, ordering, and forbidden fallbacks;
- `test_searxng_obscura_engines.py` for every engine parser plus provider
  admission, round-robin integration, and SearXNG failure handling;
- `test_obscura_direct_compose.py` (or one equivalently named structured
  Compose test) for topology, reachability, settings, image tags, and deletion
  assertions; and
- the neutrally renamed restricted-egress test module for retained final-hop
  policy and framing behavior.

Do not create the old plan's parser-service, state-clearer, broker, queue, or
cooperative-cancellation test modules. Their absence is part of the target
architecture. Use small purpose-built, sanitized engine fixtures rather than
copies of complete third-party result pages.

Add deterministic unit and effective-Compose tests for:

- one shared client implementation/import path, pinned Playwright availability,
  no Chromium in the SearXNG image, no startup package installation, and no
  cross-thread/loop sharing of Playwright or CDP objects;
- `sitecustomize_api_server` as the only API bootstrap in both modes,
  `sitecustomize_background` as the background bootstrap, no anonymous lite or
  executable base bootstrap, explicit imports from a non-bootstrap shared
  helper directory, and unchanged behavior for every moved unrelated patch;
- event/listener registration before navigation; main-frame/document selection
  amid subresources; collapsed HTTP redirects and JavaScript navigation chains;
  terminal final URL/status, event request-id/loader-id mismatch, actual-request
  body retrieval, and blocked redirect destinations; body/session/
  target/connection cleanup on every path; deterministic event-loop closure;
  and no reconnect, hidden retry, detached thread, or second navigation;
- single-navigation HTML, JS DOM, redirects, PDFs, accepted raw text,
  missing/false/duplicate/conflicting length, chunked and compressed bodies,
  oversize, body eviction, challenge pages, client timeout, and no second
  origin hit;
- binary/text predicate fixtures and source-shape drift, missing content type,
  one body-stream open, plain and base64 `IO.read`, byte-count equality and cap
  boundaries, equal-count text still marked potentially lossy/non-byte-identical,
  rendered DOM never marked response-byte-identical, unequal-count failure,
  exact MIME/extension/charset allowlists, declared
  non-UTF-8 text-classified failure, strict exact-byte charset decoding,
  prefix-then-close behavior, and `IO.close` on every failure path;
- the configured main-response limit including HTML, separate fixed 20 MiB
  rendered-DOM limit, script-expanded DOM, bounded search DOM,
  raw-document limit parsing/overflow, canonical MiB
  propagation, stream-retention consistency, and bounded active fetch
  concurrency;
- Onyx strict patch success/drift failure in lite and full modes, built-in
  provider selection, external provider non-interference and documented
  privacy/ZDR warning, stable output ordering and per-URL failure isolation,
  requested-URL correlation across redirects, terminal citations/metadata,
  requested-key snippet/failure/index-preference behavior, and cardinality
  mismatch failure,
  PDF URL/MIME/magic detection, accepted raw-text decoding, unsupported-format
  handling, the normal unsuccessful-`WebContent` shape with concise failure
  reasons across status/challenge/transport failures, character budgets, and
  absence of requests/local-Chromium/CRW/Firecrawl/CLI/parser fallback;
- one 120-second absolute monotonic invocation deadline established before the
  outer parallel jobs; the same finalization object reaching
  `_fetch_web_content` and being passed explicitly through the nested crawler
  executor; five process-global permits; blocking only for remaining budget;
  no early capacity-specific agent error; finalization/acquire and
  post-clear and post-target-setup/pre-navigation finalization race checks; no
  origin request after finalization; permits retained by already-sent late navigation through
  cleanup and released exactly once on every path; existing partial/indexed
  result behavior; non-FIFO/process-local behavior; bounded active work but
  characterized waiting-thread growth; one API process/replica; and no claim
  of capacity reservation against SearXNG; direct Admin-test and feature-API
  crawler calls use their own shared-per-call 120-second state rather than
  bypassing admission; no database/provider-row migration, Admin UI/API config
  patch, saved-timeout rewrite, or provider-choice startup enforcement;
- every enabled custom engine's offline registration, URL/query/locale/
  safe-search/time/page construction, sanitized DOM selectors and result
  normalization, genuine no-results handling, terminal challenge/block-page
  classification, exact host and block-marker allowlists, zero-result parser
  mismatch versus explicit no-results selectors, every blocking exception's
  ordinary offline-processor `suspend=True` path, non-blocking engine failures
  reaching the unresponsive-engine path, invalid input using the pinned
  `ValueError` contract without an unresponsive record, suspension, fallback,
  or query disclosure, current round-robin/last-resort scoring, no retry/provider
  selection in an engine or CDP client, and absence of the normal SearXNG HTTP
  path;
- SearXNG provider serialization by stable identity, an exact monotonic
  3.0-second minimum start interval with zero jitter, redirect accounting,
  different-provider concurrency, retained leases and eventual target cleanup,
  atomic tokenized reservation before thread creation, reservation release on
  pre-execution failure, busy/cooling-provider skipping, no all-unavailable
  fan-out, absence of queued provider threads, and single-Granian-process/
  single-replica topology; current
  enabled round-robin selection and same-request retry, normal-before-last-
  resort ordering, removal of other selected engines when the custom pool is
  present, disabled-round-robin selected-engine fan-out, stock timeout/late-
  result behavior, and query-disclosure warnings;
- CDP method/version compatibility, search
  `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH=load`, non-search `open_url`
  `OBSCURA_BROWSER_WAIT_UNTIL_WEB=domcontentloaded`, all other allowed wait
  values, empty/invalid user-setting startup failure, per-call separation,
  target selection, stable
  fingerprints across workers, successful
  command-response-before-event ordering, the matching stopped-loading barrier
  before listener inspection, best-effort same-connection cookie clearing,
  clearing failure, and per-worker trust boundaries; do not invent user-facing
  controls for internal deadlines or validation code for their literals;
- exact `Network.clearBrowserCookies` use at the audited pin, rejection of
  unsupported clearing/context claims, and tagged-image proof that a clear is
  deferred behind an active same-worker navigation, does not perturb it
  midway, and completes before the new target is created; also prove with two
  clients that the subsequent target/navigation sequence is not atomic and
  that cookie interleaving remains possible. Onyx finalization during a
  deferred clear or after target/session/listener setup must cause complete
  cleanup with no navigation and one permit release;
- the production `--host 0.0.0.0 --port 9222 --stealth --workers 5` command,
  absence of `--allow-file-access` and `--storage-dir`, test-only one-worker
  serialization, `65534:65534` on the parent and all five children, denial of
  the root-only canary, read-only/capability-free/no-secret hardening,
  five-worker concurrent progress, the internal Onyx fetch
  worker literal of five with no user-facing concurrency override,
  round-robin assignment to an occupied/dead child, typed failures without
  unsafe reconnect, no user-facing worker-count override/validation, and
  characterized memory as worker/document concurrency changes;
- direct caller `file:` rejection; the tagged pin's top-level CDP file
  rejection without `--allow-file-access`; ordinary script, stylesheet, and
  cross-scheme navigation guards; the unguarded ES-module local-file canary;
  scripted fetch/XHR behavior; no private Obscura mounts or browsing secrets;
  and documentation that these impact reductions do not fix the upstream
  security issue;
- initial full-body allocation versus retained/CDP/parser limits, actual-byte
  enforcement, per-body network retention versus aggregate IO-stream
  accounting, retention-floor and checked IO-byte derivation from the user document limit,
  required private-network/proxy environment, misleading text MIME rejection,
  base64 and retained-alias copy memory, and documented memory-planning
  guidance;
- internal/Docker/Podman/metadata and every non-public address class, IDNA,
  trailing dots, credentials, fragments, redirects, mixed DNS answers, no
  client-side target lookup, wrapper-resolved address pinning, explicit absence
  of an address-level private-denial guarantee in upstream remote-DNS modes,
  and identical validated
  `EGRESS_ALLOW_HTTP_URLS` propagation to both direct-client callers and the
  public final-hop policy;
- wrapper-owned query/header/body/cookie/credential redaction and
  stage-specific diagnostics without private content; characterized pinned
  Obscura multi-worker child-log loss and single-worker full-URL warning risk;
- public listener equivalence for generic Onyx/browser/executor traffic,
  deliberate executor access to public search engines, removal of search-host
  denial modes, and denial of all host-route exceptions;
- exact host and opt-in RFC1918 behavior only on the host proxy;
- HTTP request-smuggling/framing defenses and bridge source authentication;
- CRLF-only field framing, forbidden-control rejection, strict chunk-extension
  and trailer validation, and fail-closed empty/error operator-local DNS;
- absence of any broker protocol, broker credential, broker-side admission or
  capacity lease, or total tunnel deadline;
- bridge hardening, fixed destinations, dedicated `onyx-obscura-control`
  membership limited to API/gateway, absence of gateway membership on
  `onyx-backend`, exact uppercase/lowercase `NO_PROXY` replacement, network
  separation, and inability to route packets between caller networks;
- explicit public/host route classes, host-only trusted authorities, combined
  optional-overlay Compose models, and deadline-free framed body streaming;
- removal of CRW, validation DNS, CDP shim, legacy env names, images, networks,
  secrets, health dependencies, and docs; continued absence of Valkey and
  obsolete model servers;
- lite/full startup, real `open_url`, every custom search engine, local RAG,
  configured inference, embedding, VPN/no-VPN/upstream modes, Tailscale, and
  executor modes when external dependencies are available.

Use a test-only instrumented origin with separate main-document, redirect,
subresource, method, and range counters for static and JS HTML, valid and
mislabelled PDFs, accepted raw text, unsupported binary, status/challenge,
empty, delayed/hung, chunked, compressed, false-length, and oversized cases.
Allow its private test address only in an isolated test topology; never weaken
production destination policy. Assert no preliminary `HEAD`, range probe,
helper fetch, normal SearXNG HTTP request, CLI fetch, or second navigation.

Exercise at least this runtime matrix where its external dependencies are
available:

| Stack | VPN | Upstream proxy | Required result |
| --- | --- | --- | --- |
| lite | enabled | none | browser traffic uses the Myst route and provider DNS |
| lite | disabled | none | explicit no-VPN routing works and no Myst/autoheal dependency stalls startup |
| lite | enabled or another documented supported form | `socks5h` | the upstream proxy resolves target names and there is no direct fallback |
| full | enabled | none | the browser route works and local-RAG regressions pass |
| full | disabled | none | explicit no-VPN routing and local RAG both work |
| full | documented supported form | configured upstream | browser and helper route classes obey their respective policies while local services remain local |

For every practical row, start through the Makefile, inspect effective service
health and targeted logs, and test a real query through every supported custom
engine. Exercise `open_url` on static HTML, JavaScript-rendered HTML, PDF,
accepted raw text, and an oversized response. Also:

- prove concurrent requests for one provider never overlap, another provider
  can progress, and an `open_url` to the same public engine remains
  intentionally outside the provider scheduler;
- confirm a public search-engine URL reached by `open_url`, a helper, or an
  enabled executor receives the real upstream response/challenge rather than
  a removed local search-host-policy denial;
- verify target names do not reach Docker's embedded resolver, public egress
  identity matches the selected route, and representative internal,
  container, metadata, mixed-answer, redirect, and subresource targets fail;
- interrupt and recover Myst, Obscura, and the final-hop proxy as applicable,
  checking that stale CDP connections are not reused and no direct path or
  application-tier restart storm appears; and
- inspect startup/runtime logs to prove no package manager, `pip`, Playwright
  browser download, or other dependency installation runs after image build.

Run regressions outside the new request path explicitly: full-mode doc-drop
crawling, PDF freshness/reindexing, embedding, and `internal_search`; disabled
and enabled code-interpreter networking plus its LLM-facing capability text;
remaining Onyx helper downloads and local-Chromium consumers; Teep inference,
WebUI, authentication, MinIO, hardened host publishers, and optional Tailscale
exposure. Aggregate SearXNG success does not replace testing each custom
engine individually.

At minimum run:

```sh
make help
python3 -m unittest discover -s tests -p 'test_*.py' -v
git diff --check
```

During iteration, run a focused module with
`python3 -m unittest tests.test_obscura_cdp_client -v`. Render lite, full, and
affected optional-overlay Compose models through the Makefile's exact
layering. Search for stale `crw`, `CRW_`, `cdp-shim`, `PREFETCH_`, and
obsolete wrapper-required Firecrawl setup references, then manually classify
historical plan text and upstream identifiers that legitimately remain; a
blind zero-match assertion is not sufficient.

## Acceptance criteria

The migration is complete only when:

1. With `ONYX_AGENT_USE_OBSCURA_BROWSER=true`, every
   built-in-crawler Onyx document and custom SearXNG search attempt uses one
   Obscura navigation. With the default `false` stock setting, only
   the built-in crawler uses stock requests/Playwright through the constrained
   public route; SearXNG remains direct Obscura. Deliberately selected external Onyx providers are
   supported through public Onyx egress, explicitly outside that guarantee,
   and carry a documented retention/training/ZDR warning for the provider's
   subsequent data handling.
2. No retained path can silently refetch or fall back to CRW/local Chromium.
   The only local-Chromium crawler fallback is the default stock mode selected
   by `ONYX_AGENT_USE_OBSCURA_BROWSER=false`, remains public-proxied, and is
   documented as potentially making a second origin request.
3. Obscura and SearXNG have no direct route, target DNS remains final-hop, and
   both direct-client callers receive the same validated
   `EGRESS_ALLOW_HTTP_URLS` policy as the public final-hop proxy. Address-level
   private-target denial is asserted only for wrapper-resolved,
   address-pinned destinations; remote-DNS upstream mode carries the
   documented residual.
4. Browser, executor, public Onyx, and host Onyx networks/bridges remain
   distinct and cannot select one another's listener; the CDP gateway uses the
   dedicated API-only control network, not `onyx-backend`, and both Onyx
   `NO_PROXY` forms contain its exact service name.
5. Final-hop proxies are the only custom egress enforcement processes; no
   broker or isolated duplicate policy stage exists.
6. Long-lived CONNECT tunnels have no arbitrary proxy total lifetime.
7. The configured document input byte limit, including for HTML main responses,
   full-buffer residual memory risk, separate fixed rendered-DOM limit, fetch
   concurrency, and error reporting are tested without a
   parser sidecar or IPC protocol. PDF parsing stays close to pinned Onyx
   behavior, and its in-process pypdf paths are documented as not having a
   complete CPU, transient-memory, or wall-time bound. Text-classified bodies
   are always marked potentially lossy and never treated as byte-identical on
   the strength of equal counts.
8. One audited shared client owns URL validation, raw `Page.navigate`, the
   post-response stopped-loading event barrier, terminal main-document
   identification across HTTP redirects and JavaScript navigation chains,
   actual-request body retrieval, body classification/streaming, typed results,
   wrapper-owned redaction, and complete per-thread/loop cleanup for both
   callers.
9. In direct Obscura mode, the strict lite/full Onyx patch uses the built-in crawler,
   owns one invocation deadline/finalization object plus deadline-bounded
   blocking admission, leaves deliberately selected external providers
   upstream-owned, preserves output ordering and content semantics, correlates
   redirects by requested URL while citing the terminal URL, performs no
   database or provider-configuration migration/enforcement, and cannot reach
   requests, local Chromium, Firecrawl, CRW, CLI,
   generic-parser, or second-fetch fallbacks. The default stock mode instead
   retains the pinned requests/Playwright behavior behind its strict
   public-egress adapter. No direct-Obscura crawler starts a target/navigation after its
   outer result is finalized, including when finalization occurs after target,
   session, domain, and listener setup but before `Page.navigate`.
10. Every custom search engine uses the pinned SearXNG offline/direct contract,
    preserves its URL/parser/error/scoring semantics, surfaces classified
    blocking conditions through SearXNG's ordinary suspension machinery,
    distinguishes an
    explicit no-results selector from parser mismatch, enforces exact terminal
    hosts and block markers, and cannot invoke the normal SearXNG HTTP request
    path, retry internally, or choose another provider.
11. One Granian process owns provider scheduling with an exact 3.0-second
   per-provider start interval and zero jitter. With round robin enabled, the
   existing SearXNG patch selects one available normal custom provider, excludes
   other selected engines when that pool is present, reaches the last-resort
   tier only when normal providers are unavailable, and may select an untried
   provider only after SearXNG records the preceding one unresponsive and has no
   main results. With round robin disabled, ordinary selected-engine fan-out
   and last-resort scoring remain in force. SearXNG owns timeout, late-result,
   and suspend/block decisions; a live navigation retains its provider lease
   through cleanup, and no provider accumulates queued engine threads.
12. The fixed tagged five-worker Obscura service runs its parent and children
    as `65534:65534`, preserves `--stealth`, omits `--allow-file-access` and
    `--storage-dir`, uses the exact reviewed proxy/private-resolution,
    timeout/watchdog, and bounded-retention settings, while Onyx has both a
    five-worker per-invocation executor and five process-global active permits.
    Waiting uses only the remaining 120-second invocation budget and produces
    no early capacity-specific agent error. Round-robin non-affinity, process-local and
    non-FIFO admission, waiting-thread growth, search contention, per-worker
    state, and partial worker-health limitations are explicit and tested; no
    count is user-configurable. Direct Admin-test and feature-API crawler calls
    use the same admission bound with a local deadline rather than bypassing it.
    Runtime and upgrade docs explicitly disclose the pinned hidden-page/session
    retention behavior under concurrent connection arrival, the network body
    store's per-entry/copy-amplified retention, and the IO store's distinct
    aggregate accounting without claiming an aggregate process-memory cap.
13. Search uses `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH` (default `load`) and
    non-search `open_url` uses `OBSCURA_BROWSER_WAIT_UNTIL_WEB` (default
    `domcontentloaded`); allowed values and startup failure are explicit and
    tested.
14. Search rate/concurrency behavior, best-effort cookie clearing, its pinned
    deferral behind active same-worker navigation, its non-atomic interleaving
    with other clients before the following navigation, lack of real browser
    contexts, unknown non-cookie state, and shared CDP/browser-state risks are
    explicit and tested.
15. The derived SearXNG image contains the pinned Playwright client and shared
    package but no downloaded Chromium or runtime dependency installation.
16. All obsolete services, pins, files, settings, tests, and documentation are
    removed atomically.
17. README, AGENTS.md, runtime docs, patch docs, upgrade docs, and this plan
    describe the deployed topology and service-named patch ownership without
    compatibility aliases, an anonymous lite/base bootstrap, or historical
    obsolete broker concepts as current or planned behavior.
