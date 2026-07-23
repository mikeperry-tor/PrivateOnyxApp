# Patch and component upgrade checklist

Use this checklist whenever changing Onyx, code-interpreter, its Python
executor, SearXNG,
Obscura, Teep, support-image pins, runtime Python inputs, or source-shape
patches. `stack.versions.env` is the committed pin source; `reference_repos/`
contains read-only audit checkouts when present.

## Upgrade procedure

1. Record the old/new image tags, source refs, Python versions, and moving-tag
   support-image versions.
2. Read the upstream implementations named below. Do not infer compatibility
   from a tag or passing import alone.
3. Run `make upgrade` to update pins/hashed locks and rebuild or refresh the
   required images. Runtime startup must not invoke a package manager, `pip`,
   or a Playwright browser download.
4. Run `make check-upgrade`. It first runs the complete deterministic suite and
   local static checks, then validates strict patch installation against the
   exact newly pinned local Onyx, code-interpreter, derived Python executor,
   and derived SearXNG images.
   It also starts the exact pinned OpenSearch image with an isolated disposable
   engine volume, validates its static/runtime policy plus KNN/hybrid/reindex
   and restart behavior, and removes the exact container and volume afterward.
   Missing images are fatal and reported with the build target; validation does
   not silently pull or build a substitute. Patch-validation containers and the
   disposable OpenSearch container have no external network; OpenSearch uses
   only its own loopback TLS endpoint.
5. Inspect effective Compose models and complete every practical live-matrix
   row available in the environment. Record rows blocked by credentials,
   funding, provider stability, private documents, or long runtimes.
6. Update `docs/onyx_patch_info.md`, request/routing/security docs, README, and
   AGENTS when behavior or topology changes.
7. Remove a wrapper patch only when the pinned upstream behavior provides the
   same strict, fail-closed semantics and tests prove it.

Keep API and background bootstrap diagnostics on stderr. Onyx isolated child
processes reserve stdout for their pickled result; validate at least one real
PDF extraction after changing bootstrap or process-isolation behavior.

## Direct Obscura audit

Audit these Obscura 0.1.10 areas (or their new equivalents):

- flattened Target attachment/session identifiers, target creation/closure,
  multi-worker connection assignment, and concurrent hidden-page retention;
- `Page.navigate` command-response/event ordering and lifecycle wait values;
- Network request/response/loading events, redirect collapse, main-frame
  Document selection, JavaScript navigation, request-id/loader-id aliases,
  challenges, and terminal frame URL;
- `Fetch.takeResponseBodyAsStream`, plain/base64 `IO.read`, `IO.close`, body
  eviction, content-type predicate, compressed/chunked/false-length behavior;
- per-body network retention, entry/alias/base64 amplification, aggregate IO
  stream accounting, and initial full-body allocation before retention;
- `Network.clearBrowserCookies` deferral and cross-client non-atomicity;
- the cumulative 45-second pre-navigation deadline across connect, cookie
  clear, target creation, attachment, and domain setup; the separate bounded
  cleanup commands; typed stage-specific expiry; and URL-free correlation logs;
- caller-specific absolute attempt deadlines covering navigation, event wait,
  DOM commands, body-stream creation and reads; exact post-navigation expiry
  stages; and the five-second-before-outer-deadline Onyx partial collector that
  finalizes queued work, preserves ordered completions, and does not retry;
- the common `ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB` ceiling on both retained
  main-resource body and serialized rendered DOM, while SearXNG retains its
  independent fixed 20 MiB search-DOM ceiling;
- proxy resolution, redirect/subresource enforcement, logging/full-URL
  exposure, file-access guards, and the accepted ES-module local-file path.
- bounded structural challenge parsing that excludes script/style/template/
  noscript content; positive status, route, title, visible-prompt, and combined
  visible-plus-structure fixtures; and negative script-only, iframe-only, and
  ordinary article-text fixtures.

Run tagged-image capability tests, not only fake CDP fixtures. Prove static and
JS HTML, HTTP and JavaScript redirects, PDF, accepted raw text, unsupported and
oversized content, challenge/status failures, one origin navigation, body/DOM
byte limits, complete cleanup, and no reconnect/refetch.

### Pinned Obscura limitations and wrapper handling

Treat each item below as an explicit removal checkpoint on every Obscura pin
change. Do not carry a workaround forward merely because its old fixture still
passes:

- **Main Document retained-body eviction.** In 0.1.10 the per-page response
  store applies `OBSCURA_NETWORK_BODY_BUFFER_ENTRIES` to the main Document and
  subresources alike. The internal main entry can be evicted before
  `emit_navigation_events` creates its loader-id alias; `Page.navigate` has not
  returned yet, so no client can claim the body first. The wrapper maps the
  exact `Fetch.takeResponseBodyAsStream: no cached body` rejection to
  `body-unavailable` and permits only same-navigation HTML/XHTML DOM processing
  to continue. PDF/raw/binary handling remains strict. On upgrade, inspect
  `store_response_body`, eviction order, main-resource alias creation, and
  command/event ordering; test a page with more retained subresources than the
  configured entry count. Remove the HTML exception when the tagged image
  proves the main body remains retrievable. Do not substitute a larger entry
  count, which multiplies the per-entry memory bound.
- **Flattened duplicate session IDs.** Obscura 0.1.10 reuses the attached
  target session ID when Playwright 1.58 calls `new_cdp_session(page)`, causing
  the driver to reject a duplicate target. The shared client uses one minimal
  raw WebSocket CDP session. Re-test the high-level API on either pin change and
  remove the raw transport only if one navigation, event ordering, actual-body
  access, deadlines, redaction, and cleanup remain equivalent.
- **Deferred, non-atomic cookie clearing.** `Network.clearBrowserCookies` can
  wait behind work on the selected child, and another client can interleave
  between the clear and navigation. The wrapper treats clearing as best effort
  and makes no per-user isolation claim. Re-audit command dispatch, default
  context sharing, and two-client interleavings; prefer an upstream isolated
  context if one becomes complete and supported.
- **Connection-arrival hidden page/session retention.** A connection assigned
  during an active navigation can create pre-dispatch blank state that its
  disconnect path does not reclaim. The wrapper bounds client commands and
  closes its WebSocket but does not pretend that this reclaims inaccessible
  server state; worker restart remains the eventual cleanup. Re-test connection
  churn and worker memory.
- **Renderer stalls and incomplete multi-worker diagnostics.** DOM and cleanup
  commands can fail to answer, the multi-worker launcher discards child
  stdout/stderr, and the single-worker path can log full URLs. Caller-owned
  absolute setup/request/cleanup deadlines, opaque correlation IDs, sanitized
  stages, and warning-level typed failures provide the wrapper boundary.
  Re-test a permanently blocked command, target/connection cleanup, child-log
  forwarding, and URL redaction before removing any deadline or diagnostic.
- **Body-memory and classification gaps.** The server fully allocates a
  response before retention checks; the network byte limit is per entry and is
  amplified by entry count, base64, and aliases; the IO store is separately
  aggregate-bounded; and the stream API does not expose the internal
  text/binary decision. Retain strict limits and the mirrored
  `is_text_like_content_type` predicate until the tagged source and runtime
  tests provide authoritative aggregate accounting and classification.
- **Local-file and health gaps.** Disabling `--allow-file-access` does not
  repair the pinned ES-module local-file-read path, and the round-robin health
  endpoint does not establish aggregate child health. Keep the container
  unprivileged/read-only and free of private mounts, and keep request failures
  visible rather than adding public probes or a fallback browser. Re-audit both
  source paths on upgrade.

Playwright Python remains pinned to the version supplied by Onyx (1.58.0) for
compatibility auditing and derived-image validation.
The current duplicate-session restriction and its removal criteria are tracked
in the pinned-limitations list above.

## Onyx API patch audit

Audit both values of `ONYX_AGENT_USE_OBSCURA_BROWSER`. The default `false` mode must
retain the pinned stock requests and Playwright fallback while the narrow
egress adapter still matches the crawler's imported `ssrf_safe_get` and
`fetch_rendered_html` symbols. Confirm every initial URL and requests redirect
receives public-only structural validation without API-side DNS, the session
sets `trust_env=False` and explicitly uses `onyx-public-egress-bridge`, the
Admin private-network value cannot widen it, and Playwright validation is
scoped to this crawler. Confirm the shared launcher still uses the exact
public/host proxy selected for each consumer and `<-loopback>` disables direct
loopback bypass. Exercise a public success, qualifying 403 browser fallback,
public redirect, private/loopback initial URL and redirect, NXDOMAIN, broken
bridge, and remote-DNS upstream mode. SearXNG must remain direct Obscura under
both preference values.

The current default reflects parallel testing in which the stock crawler was
blocked less often and was substantially more reliable than Obscura 0.1.10.
Every Obscura pin upgrade must repeat comparable parallel URL batches and
retries, recording blocked, empty-content, timeout, and successful results.
Monitor upstream Obscura improvements and switch the default to `true` if the
new pin reverses the reliability result while preserving the audited privacy,
cleanup, body-retention, and concurrency properties.

Re-audit the pinned Onyx symbols for:

- lite-mode `OpenURLTool.is_available`, including its
  `DISABLE_VECTOR_DB` gate; the crawler/index parallel call; and the indexed
  retrieval exception-to-empty-result path. Confirm the strict availability
  patch installs only in lite mode, `open_url` remains visible in the user
  skill list and constructed Agent tools, and a real crawler request succeeds
  without restoring indexed retrieval. Remove the patch if upstream makes
  crawler-backed `open_url` natively available without a vector database;
- chat-time indexed reuse: URL normalization must resolve only to an existing
  connector document ID, `id_based_retrieval` must remain ACL-filtered, and
  fresh crawling must not ingest content or become `internal_search`. Confirm
  both siblings still run, indexed content is preferred after completion, and
  crawler content remains the fallback;
- `OnyxWebCrawler.contents`, `_fetch_url`, `_fetch_web_content`, outer
  `open_url` timeout callback, copied context, nested executor, and result
  ordering;
- `WebContent` failure shape, requested/terminal URL metadata, snippet and
  indexed-document preference/merge behavior;
- `is_pdf_resource`, `extract_pdf_text`, HTML extraction, raw-text decoding,
  and character-budget call sites. Confirm the per-URL character cap touches
  crawler sections only, while the aggregate LLM-output cap follows the merge;
- built-in-crawler size scope: `ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB` must remain
  authoritative for stock requests PDF/HTML, stock Chromium rendered HTML, and
  direct-Obscura main-body/rendered-DOM limits. Validate the stock constructor
  signature, its pinned 50 MiB PDF and 20 MiB HTML defaults, every downstream
  size-check call site, and UTF-8 byte accounting for rendered HTML. Confirm the
  setting remains absent from `background`, external content providers and
  exact-ID indexed retrieval remain unaffected, and an oversized crawler
  sibling can coexist with a usable indexed sibling;
- built-in versus external provider dispatch and Admin/provider test APIs;
- any new requests, local-browser, Firecrawl, parser, or generic fallback.

Re-audit the exact fake embedding model contract. The saved
`nomic-ai/nomic-embed-text-v23` value must continue to activate the intended
Onyx `nomic-ai` RAG feature gates, while only tokenizer construction is mapped
to the bundled `nomic-ai/nomic-embed-text-v1` tokenizer. Confirm the strict
`HuggingFaceTokenizer.__init__` source-shape check, both API and background
installation points, no Hugging Face lookup for v23, and unchanged behavior
for every other tokenizer model.

Re-audit Onyx's embedding caller failure contract on every pin. The wrapper
currently relies on query embedding making one request, passage embedding
retrying explicit request/HTTP failures three times with fixed five-second
waits, and neither path supplying its own `requests` timeout. Confirm document
processing still propagates terminal embedding exceptions into indexing task
failure handling, and do not mistake its independent heartbeat thread for a
blocked-embedding watchdog. The wrapper lifecycle proxy owns the five-minute
post-readiness blocked-socket bound; the shim must not add a timeout or retry
that can ambiguously replay a POST.

Verify `sitecustomize_api_server` is the only API bootstrap in both modes,
neutral shared helpers are imported rather than executed, and patch drift is
startup-fatal. Test one shared 120-second monotonic invocation state through
outer/nested jobs, five process-global permits, remaining-budget admission,
finalization races before navigation, one release on every path, partial
results, stable ordering, redirect correlation, content dispatch, limits, and
external-provider non-interference. Do not add database/provider-row migration
or startup enforcement of the operator's saved crawler choice.
In both stock and direct Obscura modes, verify mixed-success batches retain
successful documents and citations while appending the final post-fallback
per-URL failure reasons to the LLM-facing response; all-success, all-failure,
and timeout-only response forms must remain unchanged. Confirm the shared
failure-reporting patch is installed before the selected crawler transport.
Inject a permanently blocked per-URL worker and prove completed siblings return
in order before the outer deadline, the unfinished URL receives a visible
failure, queued work cannot navigate after collection finalization, and the
blocked CDP operation expires at its exact logged stage and releases its permit.

## SearXNG audit

The current audited SearXNG pin is `2026.7.15-7b2199ecd`, sourced from commit
`7b2199ecdf75a00981583fa2f392a785dfc4fcee`. Re-audit the pinned offline and
online processors, engine loader, exception suspension mapping, result
containers, timeout/late-result handling, engine selection, round-robin retry
patch points, and last-resort scoring.

Confirm that `use_default_settings.engines.keep_only` remains supported and
that the effective engine list contains exactly `google2`, `brave2`,
`duckduckgo2`, `startpage2`, and `bing2`. No inherited engine may initialize or
perform startup DNS/network work.

For every custom engine verify:

- offline registration and no normal SearXNG HTTP transport;
- URL/query/locale/safe-search/time/page construction;
- sanitized selector fixtures, normalization, explicit no-results marker,
  parser mismatch, exact terminal hosts, and shared block markers;
- one atomic pre-thread reservation consumed only by its exact engine attempt,
  one provider lease held through cleanup, exact monotonic 3.0-second start
  interval, no queued busy-provider thread, no all-unavailable fan-out, and
  visible unresponsive records for pre-execution unavailability, and
  different-provider concurrency;
- CAPTCHA, rate-limit, and access-denied exceptions use the ordinary offline
  suspension path; non-blocking failures become unresponsive records;
- Bing's visible `One last step` challenge is a typed CAPTCHA while the same
  phrases in excluded script/style/template/noscript content are ignored;
- engines and the CDP client never retry or select another provider;
- enabled round-robin normal/last-resort order and same-request retry, plus
  disabled-round-robin selected-engine fan-out and disclosure warning.

The derived SearXNG image must install its complete Python dependency set from
the generated hashed `searxng/requirements.txt` lock, use one Granian process,
one replica, perform no Chromium/browser download or runtime installation, and
pass the shared-client import validation. Its local image tag must change when
the upstream pin or any embedded Dockerfile, lock, shared-client, or engine
input changes. Its explicit `PYTHONPATH` must expose
the wrapper patches, SearXNG application root, and shared client before the
embedded interpreter imports `sitecustomize`; verify the real Granian
entrypoint logs every strict patch success and loads every custom engine.

## Routing and Compose audit

Render the exact Makefile layering for lite, full, VPN, no-VPN, upstream proxy,
Tailscale, Podman, and optional executor modes that changed. Verify:

- the committed user-facing default remains `MYST_VPN_ENABLED=false`, so a
  first start needs no Myst identity, registration, wallet, or payment;
- only API/gateway join `onyx-obscura-control`; the gateway is absent from
  `onyx-backend`; SearXNG joins the browser control network;
- both Onyx `NO_PROXY` forms contain `obscura-cdp-gateway` and no removed alias;
- Obscura/SearXNG have no direct Internet route and CDP has no host port;
- public Onyx, browser, executor, and host-capable caller networks/bridges stay
  distinct, fixed-destination, and peer authenticated;
- browser/public/executor share the public policy while host-only exceptions
  remain on the host listener; public search hosts are not locally denied;
- target DNS is Myst/provider, upstream-owned remote DNS, or explicit no-VPN
  DNS as selected, never Docker target DNS;
- wrapper-resolved destinations are complete-set validated and pinned;
  remote-DNS mode retains its explicit private-resolution residual;
- VPN/upstream/no-VPN failures, internal targets, redirects, subresources,
  metadata, mixed answers, and framing errors fail closed;
- long-lived CONNECT streams have no arbitrary total lifetime;
- Obscura remains five workers, 65534:65534, read-only, capability-free,
  secret/data-volume-free, and without storage/file-access flags;
- full local RAG, embedding, configured inference, Teep, hardened publishers,
  and optional Tailscale behavior remain intact.

## OpenSearch single-node policy audit

Re-audit the static single-node policy whenever the pinned OpenSearch image or
Onyx source changes. There is no runtime migration or administrative sidecar.
Confirm all of the following before accepting an upgrade:

- the effective OpenSearch service retains
  `OPENSEARCH_JAVA_OPTS=-Xms512m -Xmx512m`, `node.processors=4`, and
  `DISABLE_PERFORMANCE_ANALYZER_AGENT_CLI=true`. Verify the live JVM heap and
  OpenSearch allocated-processor count rather than relying only on Compose;
- the entrypoint still passes lowercase dotted environment names as `-E`
  settings and accepts all three static
  `search.insights.top_queries.{latency,cpu,memory}.enabled=false` values.
  Recheck upgraded Onyx for any Query Insights consumer and confirm no new
  `top_queries-*` index appears after the normal export boundary;
- `plugins.security.audit.config.index='security-auditlog-'YYYY.MM` remains in
  the effective process arguments and produces a monthly index. OpenSearch 3.6
  omits this plugin setting from its settings APIs, so validate behavior;
- the pinned image's
  `/usr/share/opensearch/config/opensearch-security/audit.yml` schema still
  matches tracked `onyx/opensearch/audit.yml` except for the intentional
  `log_request_body: false` value. Confirm the normal entrypoint accepts the
  read-only bind and uses it when initializing a clean Security index. Preserve
  failure/TLS categories, sensitive-header exclusion, and compliance defaults;
- Onyx still consumes `OPENSEARCH_INDEX_NUM_REPLICAS`, and API plus
  background/indexing paths both receive `0`. This affects newly created
  indices only; the wrapper intentionally contains no existing-index migration;
- neither API nor background gains an admin certificate, OpenSearch password,
  Security API mutation, or new network authority; and
- no configuration run deletes historical `security-auditlog-*` or
  `top_queries-*` indices. Retention is a separate operator decision.

The static policy is a clean/current-volume contract. Persistent cluster
settings override startup settings, and an initialized Security index retains
its stored audit configuration. This private wrapper deliberately does not
repair arbitrary older or externally modified state.

Validate a clean volume plus the current populated volume under Docker and
Podman. On a clean volume, generate a harmless failed-authentication request
with a recognizable body and confirm the monthly audit record contains useful
failure evidence but no request body. Confirm newly created Onyx indices have
zero replicas, the cluster is green, all three Query Insights collectors remain
disabled, and no Query Insights daily index is created. Exercise doc-drop Web
Connector indexing/reindexing and `internal_search` concurrently while
recording heap occupancy, GC pauses, circuit breakers, thread-pool queues and
rejections, latency, and container RSS. Also test KNN/hybrid search,
restart/recovery, and TLS/auth. Treat audit schema/path changes, rejected dotted
settings, Onyx replica-setting drift, OOM, circuit-breaker failures, sustained
queue rejection, or material indexing/search regression as blocking upgrade
issues.

`make test-opensearch-image` is the recurring clean-volume image gate and is
part of `make check-upgrade`. It uses only the selected `CONTAINER_BIN` command
surface and an exact-name disposable volume. With a full stack running, use
`make integration-opensearch` for a non-restarting current-volume workload,
`make integration-opensearch-restart` for recovery, and
`make integration-opensearch-onyx` for the exact pinned Onyx mapping/client and
hybrid-query path. These targets create and remove only
`private-onyx-*-validation-*` indices. Run the live targets under both Docker
and Podman for relevant container-engine or OpenSearch/Onyx upgrades; a pass on
one engine does not substitute for the other engine's lifecycle validation.

## Privacy and WebUI CSP audit

Re-audit every privacy setting against the new Onyx source rather than carrying
environment names forward by resemblance. Confirm:

- `DISABLE_TELEMETRY` still gates every anonymous telemetry call;
- empty backend Sentry, PostHog/marketing PostHog, HubSpot, Braintrust, and
  Langfuse credentials still prevent initialization, including in Celery
  workers;
- `AUTO_LLM_CONFIG_URL=""` still removes the recommended-model beat/poller
  work and performs no fetch, and `DISPOSABLE_EMAIL_DOMAINS_URL=""` still
  skips the public domain-list fetch;
- the current reCAPTCHA client and Enterprise backend variable names remain
  empty and `CAPTCHA_ENABLED=false` remains authoritative;
- cloud, paid-EE, Stripe, GTM, Sentry, PostHog, and reCAPTCHA build/runtime
  controls still match their call sites. `NEXT_PUBLIC_*` values can be inlined
  during a WebUI build, so inspect the pinned image/source build in addition to
  the runtime Compose environment;
- release-note refresh remains the only intentional wrapper-independent Onyx
  update poll, and local administrative analytics remain local and usable;
- newly added tracing, analytics, crash-reporting, marketing, update-check,
  remote-config, favicon, font, image, video, CAPTCHA, billing, or CDN clients
  are either required operator-selected functionality or explicitly disabled.

Inspect the pinned WebUI CSP implementation. Onyx emits
a policy unconditionally from `web/src/proxy.ts`; the documented
`WEB_STRICT_CSP_ENABLED` environment name has no implementation and must not be
counted as a defense. Keep the tracked nginx policy as a second CSP header so
the browser intersects it with upstream policy. If upstream implements an
equivalent restrictive policy, remove the wrapper header only after testing the
effective combined response.

The wrapper's current `script-src 'self' 'unsafe-inline'` is an intentional
no-rebuild compatibility exception for Next.js bootstrap and React stream
scripts. Keep `script-src-attr 'none'`; never add `unsafe-eval` or remote script
origins. Do not replace this with a static self-only or nginx-filtered nonce
policy based on response text tests alone: the tested variants
returned HTML but left a real browser blank and unhydrated. A stricter upgrade
requires a source-level per-response nonce that reaches every rendered stream
script while keeping same-origin preload and lazy chunks functional. Once that
browser test passes, remove `'unsafe-inline'`.

Test through both the localhost publisher and every enabled Tailscale frontend:

- a remote Markdown image and the Google Web-result favicon must make no
  outbound browser request and must fail/fall back visibly;
- a code-interpreter image emitted as `[filename](file_link)` must render from
  the relative `/api/chat/file/{id}` endpoint even when `file_link` contains a
  different configured `WEB_DOMAIN` origin; confirm the strict prompt patch
  still rejects drift in both the ordinary-link instruction and its explicit
  prohibition on `![filename](file_link)` and constructed file URLs;
- uploaded/local/background images, a `blob:` image preview, and an embedded
  `data:` DOCX image must still render;
- login, chat hydration/streaming, same-origin fetches, voice HTTP/WebSockets,
  lazy route chunks, downloads, and local/blob PDF previews must still work;
- Stripe Elements, GTM, reCAPTCHA, external analytics/crash clients, remote
  frames/media/fonts/workers, remote scripts, inline event-handler attributes,
  and eval must be blocked; required Next inline script blocks remain the known
  compatibility exception;
- the response must contain both Onyx's CSP and one wrapper policy with no
  remote HTTP(S) source;
- external user navigation and configured backend integrations must retain
  their documented behavior; do not describe CSP as a browser VPN or proxy.

Re-audit `PythonToolRenderer.tsx`. Its highlight.js exception path must HTML-
escape `&`, `<`, and `>` or render the original code as a normal React text
child; raw code must never reach `dangerouslySetInnerHTML`. The current wrapper
does not rewrite compiled chunks because their immutable cache URLs would make
that incomplete for existing clients. Remove this checkpoint once the pinned
source and built image contain the safe fallback.

Validate the nginx fragment with the pinned nginx image and inspect the
effective Compose mount. A generated `onyx/onyx_data` refresh must not remove
the tracked `/etc/nginx/conf.d/webui-csp.conf` bind mount.

## Runtime patch contract audit

Retest every wrapper patch summarized in
[Patch information](onyx_patch_info.md). Do not treat a successful import as
sufficient. For each family, confirm both its strict installation boundary and
its user-visible behavior:

- **Deep Research and tool choice:** the two rewritten agent loops must match
  every exact replacement once; all accepted mixed tool calls execute, control
  tools remain single-call-only, nested placements are unique, batch overflow
  executes nothing, worker concurrency remains bounded, and only the four
  audited forced-tool call sites become automatic.
- **Reasoning and saved history:** re-audit the structured-message builder,
  chat reconstruction, LiteLLM serialization, all rebuilt agent loops, native
  detector signature/model-map fallback, saved response helper, and complete
  `convert_chat_history()` signature. Exercise a reasoning-bearing tool turn,
  a follow-up that needs saved tool output, successful final synthesis, and a
  final-synthesis-only failure.
- **Context and output limits:** re-audit both configured-token lookup source
  markers, the complete internal-search formatter signature/JSON construction,
  all three `open_url`/search positional defaults, and the repository downloader
  signature/defaults. Test invalid, zero/unlimited, normal, and smaller-than-
  notice budgets; no result may exceed its per-result or aggregate cap. Keep
  the crawler-only per-URL cap distinct from the post-merge aggregate cap and
  from the built-in crawler's response/rendered-document byte cap.
- **PDF freshness:** validate exact `_do_scrape()` and
  `get_docs_to_update()` signatures/source markers plus connector, database,
  and result shapes. Exercise unchanged, changed, missing-validator, 401/403/404,
  non-PDF, non-allowlisted, normal sync, and forced-reindex cases. Only a
  matching unchanged or terminal-unreadable sentinel may skip indexing.
- **Executor networking:** validate the exact code-interpreter command-builder
  signature/source, one `docker run`, one native network argument, and the
  native run-argument setting containing exactly eight proxy variables.
  Malformed configuration or argv must fail validation. Verify every API-side
  tool description/prompt matches restricted proxy-only access in enabled mode
  and remains stock in disabled mode.
- **Executor dependencies:** inspect the pinned upstream executor environment,
  regenerate the hashed wrapper lock, and confirm the derived tag changes for
  every Dockerfile or lock change. Run the selected image with networking
  disabled; verify the exact SymPy version and a symbolic solve. Confirm the
  unconditional Python tool description and guidance list only packages the
  executor actually contains. Confirm the LLM-facing tool name, built-in map,
  saved-row remapping, and prompt use only `run_python`, while the display name
  remains `Code Interpreter`. Then exercise a real `run_python` tool call.
- **Lite `open_url`, helper routes, embedding tokenizer/shim, privacy settings,
  and CSP:** retain their dedicated audits elsewhere in this checklist.

`make check-upgrade` preserves the required order: deterministic checks first,
then strict installation of the API and PDF freshness patch families inside
the pinned Onyx backend image, the executor patch inside the pinned code-
interpreter image, the SymPy contract inside the derived Python executor, and
the SearXNG runtime/parser checks inside the derived image. Any source,
signature, model, prompt, or command shape mismatch is an upgrade blocker, not
a reason to weaken validation.

The executor image contract is Docker-only because the socket-dependent code
interpreter is not supported or stored under Podman. With
`CONTAINER_BIN=podman`, image validation explicitly skips that one contract;
the Onyx and SearXNG contracts remain required.

Confirm Compose still sets `ENABLE_CRAFT=false` for the API and full-mode
background services unless the wrapper deliberately adds and documents a
Craft backend. Re-audit the exact schedule names, tasks, and original cadences:
seven discovery schedules must be rewritten to five minutes; the three Craft
cleanup schedules and queue/process/memory monitoring schedules must be
removed; conditional schedules must remain absent; and Beat reload must remain
five minutes. Confirm `DynamicTenantScheduler.tick` remains the unmodified
upstream method, worker bootsteps are empty, and Beat logs show no
sandbox-manager or monitoring task initialization. The upstream marker should
continue representing the live scheduler loop; schedule-refresh failures must
remain logged application errors rather than causing watchdog restart loops.

Confirm the background image's supervisor `environment=PYTHONPATH=...` setting.
While it resets worker `PYTHONPATH` to `/app`, full-mode Compose must mount the
strict background bootstrap at `/app/sitecustomize.py` and its shared helper at
`/app/wrapper_env_patches.py`, in addition to the wrapper-directory mounts used
by the container entry process. Verify patch-success diagnostics in the actual
Celery worker and in a spawn-based document-fetching child, then run a real
doc-drop Web connector crawl and PDF extraction.

Keep the control-process exclusion narrow. The wrapper entrypoint and local
watchdog must use `python -S`; the exact `/usr/bin/supervisord` argv must skip
background patch installation; and Beat, Celery workers, and spawn-based
indexing children must still emit patch-success diagnostics. Inspect process
PSS, mappings, and thread counts after an image upgrade to ensure the control
programs have not resumed importing NumPy, tokenizer, or Onyx application
modules.

Also diff the pinned supervisor configuration against
`onyx/background_entrypoint.py`. It must still identify exactly eight upstream
worker programs, remove only the scheduled/monitoring pair, retain six workers
with `--without-heartbeat --without-gossip`, preserve the bounded concurrency
values, and select the exact two bot programs from default-off booleans.
Those booleans must remain the full-mode-only wrapper settings
`ONYX_AGENT_SLACK_BOT` and `ONYX_AGENT_DISCORD_BOT`; neither belongs in the
lite effective model.
Validate that the local watchdog accepts only an owner-matched regular file,
uses two-observation missing-file handling plus the bounded startup grace, and
invokes `supervisorctl restart celery_beat` only. Exercise one representative
connector indexing workload before accepting lower concurrency.

For SearXNG, verify the exact standard-library multiprocessing resource-tracker
command skips the application patch bootstrap while the Granian parent and
request workers retain all strict patch diagnostics. Treat any other Python
`-c` command as an application process unless its role is separately audited
and tested.

For support and source pins, require an immutable Tailscale digest, exact Myst
and Teep Git revisions in both image labels and build arguments, and
the MinIO source revision associated with its release image. Run
`make health-inventory`, inspect effective startup/steady intervals, and verify
Docker Engine 25+/Compose 2.20.2+ preserve `start_interval`. For Podman, verify
the wrapper's native `StartupHealthCheck` translation against the exact
effective Compose health set and inspect the separate one- or ten-minute
regular cadence; Compose rendering alone is not sufficient.

For a Myst source, entrypoint, or health-supervisor change, exercise three
graceful PID-1 restarts and a supported runtime disconnect on both Docker and
Podman. Require old interface/route cleanup, one-attempt recovery in the
unchanged holder namespace, no successful application-path request while
unready, and exact startup/no-VPN non-arming behavior. Reconfirm that the
health command has no container-engine socket and remains the only periodic
readiness owner.

In explicit no-VPN mode, also confirm that `netns-holder` remains the namespace
owner, the Myst container starts only its inert readiness sentinel, no Myst
daemon or route-reconciliation loop exists, and the unchanged healthcheck
rejects a stale `myst0` or missing direct default route on both engines.

### Pinned Myst signup and payment CLI contracts

The standalone signup container is a non-restarting, TequilAPI-only service.
Its entrypoint performs no identity, registration, or order mutation; the
host-side `myst/vpn_cli.py` helper is the sole workflow owner. The integrated
entrypoint also performs no signup or financial mutation. Preserve this split
on every Myst pin change so a long user payment pause, command failure, or
health failure cannot race a second order or cause restart churn.

The helper deliberately parses pinned human-readable CLI output because the
CLI does not expose a stable machine-output mode. Audit all of these exact
contracts against the new binary and update fixtures before accepting a pin:

- The pinned CLI can emit an `[ERROR]` line while returning exit status zero.
  Every non-create command treats either a nonzero exit or this marker as
  failure. `orders create` is the sole exception because only its authoritative
  pre/post order-list postcondition decides whether a remote mutation occurred.

- `identities list` emits one optional `[+]` marker and one `0x` plus 40
  hexadecimal address per line; embedded addresses in diagnostics are not
  identities. A successful empty list is distinct from a command failure.
  Creation must be followed by a second successful listing, and multiple
  identities require the explicit `MYST_VPN_IDENTITY` selector in both setup
  commands and integrated startup.
- `identities get <id>` emits `Registration Status:`, `Channel address:`, and
  `Balance:` lines. Registration uses the identity-addressed
  `identities register <id>` command, never `account register`, because the
  latter acts on Myst's current identity and its upstream command action can
  print failure while returning success.
- `identities balance <id>` is the explicit remote refresh. A subsequent
  `identities get` supplies the authoritative status/channel/balance snapshot;
  empty or malformed output is never treated as zero.
- Although CLI usage text says `order`, the pinned dispatcher accepts the
  `orders get-all|get|create|gateways` form used by the wrapper. `get-all`
  emits either `No orders found` or `Order ID '<id>' is in state: '<state>'`.
  The only pinned states are `initial`, `new`, `paid`, and `failed`; unknown
  states and order IDs outside the pinned conservative character set fail
  closed. Only `initial`/`new` are payable. `paid` with a refreshed zero
  balance is settlement-in-progress and must not create another order.
- `orders gateways` emits repeated `Gateway:`, `Suggested minimum order:`, and
  `Supported currencies:` lines. The pinned create command rejects amounts
  less than or equal to the reported minimum, not merely amounts below it.
  Gateway, currency, finite positive decimal amount, two-letter country, and
  gateway `key=value` data are validated before mutation, including bounded
  gateway/key character sets, nonempty values, and rejection of control
  characters; the first returned gateway is never selected implicitly.
- Several pinned `orders create` validation branches log an error and return
  exit status zero. Conversely, a remote order may be committed even if the
  client loses its response. Therefore exit status is never the creation
  postcondition: diff the successful pre/post `get-all` results, require exactly
  one newly observed order, require any reported ID to equal it, retrieve that
  exact order, and never retry an ambiguous result automatically.
- `orders get` emits one `Data:` JSON value. Extract a single URL only from the
  recognized `payment_url`, `pay_url`, `payment_link`, `redirect_url`,
  `checkout_url`, or `url` keys, including nested objects/lists. Require
  credential-free HTTPS with a hostname and no control characters. Do not
  restore the old arbitrary-URL grep fallback, echo complete gateway payloads,
  or display URLs for paid or failed orders.
- Direct transfer output depends on a distinct 40-hex `Channel address` and
  the pinned Polygon chain/token assumptions. Reconfirm chain ID 137, the MYST
  token address, active-Hermes channel derivation, and the warning not to fund
  the identity address.

Run `tests/test_myst_vpn_cli.py` plus the pinned image command-contract checks.
Exercise read-only identity, balance refresh, gateway, order-list, and exact
order-detail commands on both engines. Creating a real payment order remains
an explicit external financial mutation; use an upstream sandbox if one is
available rather than adding it to unattended validation.

## Minimum deterministic validation

```sh
make check
```

For an image or patch upgrade, the expected local flow is:

```sh
make upgrade
make check-upgrade
```

`make test` runs only the deterministic Python suite. `make test-images` runs
only strict pinned-image contracts plus the image-dependent SearXNG parser
tests. Neither target starts the application stack or performs the live matrix.

Also inspect effective lite/full Compose models through the Makefile. Search
current runtime files for removed service/env/path names and manually classify
historical implemented plans and read-only reference repositories rather than
using an indiscriminate zero-match rule.

## Live validation matrix

Where external dependencies are available, exercise lite/full with VPN,
explicit no-VPN, and a documented remote-DNS upstream. For each practical row:

- start with the Makefile and inspect health plus API, SearXNG, Obscura,
  gateway, bridge, final-hop, and Myst logs;
- run a real built-in crawler request for static and JS HTML, redirect, PDF,
  raw text, unsupported and oversized content;
- query every custom search engine and distinguish genuine results/no-results
  from visible provider CAPTCHA/429/access denial;
- verify a same-provider lease never overlaps, another provider can progress,
  and `open_url`/helpers/executors remain outside search scheduling;
- interrupt Myst, Obscura, gateway, and the final hop and confirm no stale
  connection reuse, direct fallback, or application restart storm;
- test full doc-drop crawl/freshness/reindex, embedding and `internal_search`;
- test disabled/enabled executor network paths and LLM-facing descriptions;
- exercise configured inference, Teep, WebUI/authentication/MinIO, host
  publishers, and optional Tailscale where configured.

If credentials, funding, provider stability, private documents, or long-lived
external services prevent a row, record exactly what was not run and retain
the deterministic and topology evidence.
