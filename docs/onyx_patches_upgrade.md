# Patch and component upgrade checklist

## Tor and canonical-origin contract

When changing the Tor base pin, re-audit its manifest architectures, source
revision, packaged Tor version, empty image user, entrypoint, UID/GID 101:102,
GeoIP paths, Python 3, and `/var/lib/tor` volume. Rebuild the minimal derived
image, run `make test-tor-image` with Docker and Podman, and verify generated
configs with networking disabled. Reconfirm entrypoint bypass, non-root
read-only/capability-free operation, private cookie-only control health,
named-volume ownership, and that the state bind overrides the inherited
volume.

Reconcile every Tor or canonical-origin change with the
[Tor change and validation map](native_tor_support.md#change-and-validation-map),
[Tor security boundaries](internal_network_security.md#native-tor-and-onion-ingress-boundaries),
[native-Tor final-hop policy](vpn_routing_and_proxies.md#native-tor-final-hop),
[optional Tor lifecycle](resource_minimization.md#optional-tor-lifecycle),
[Podman Tor contract](podman_suport.md#native-tor), and
[request-path behavior](request_handling.md#optional-tor-route). Treat those
documents as applicability and obsolescence checks, including when only a
support image or Compose layer changes.

Render all four Tor role models plus engine corrections and retest direct Tor
egress, unavailable-selector failure, identity persistence, and simultaneous
localhost/Tailscale/onion authentication and streaming.
`WEBUI_CANONICAL_ORIGIN` remains one `WEB_DOMAIN`, passed identically to
backend and web server. Do not infer general multi-origin support: voice,
OAuth/federated callbacks, email links, and similar consumers remain
canonical-origin features, and gateways must not rewrite cookie attributes.

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
   required images. Container and service entrypoints must not invoke a package
   manager, `pip`, or a Playwright browser download. The Makefile may refresh a
   previously installed bundled host MLX environment before starting it, as
   specified below; first-time installation remains explicit.
4. Run `make check-upgrade`. It first runs the complete deterministic suite and
   local static checks, then runs `make test-all-images`: strict patch
   installation against the
   exact newly pinned local Onyx, code-interpreter, derived Python executor,
   and derived SearXNG images. It runs the selected derived Obscura stealth
   image against an
   internal fixture network to validate connection isolation/capacity,
   concurrent navigation, content handling, retained-body behavior, and
   cleanup. It also validates the selected local Tor wrapper image, a fresh
   runtime socket volume, the state-bind override, private
   cookie-authenticated control path, and an unprivileged read-only policy
   consumer without pulling or rebuilding either Tor image.
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
6. Complete the documentation applicability gate below for every component,
   dependency, image, runtime, or Compose layer changed by the upgrade. Update
   the living documents and their tests in the same change; do not add a
   version history or preserve descriptions of removed behavior. README changes
   must remain user-facing.
7. Remove a wrapper patch only when the pinned upstream behavior provides the
   same strict, fail-closed semantics and tests prove it.

Keep API and background bootstrap diagnostics on stderr. Onyx isolated child
processes reserve stdout for their pickled result; validate at least one real
PDF extraction after changing bootstrap or process-isolation behavior.

### Python dependency and host-install policy

Treat every direct dependency in `embedserv/requirements.in`,
`searxng/requirements.in`, and `executor/requirements.in` as an exact component
pin. `make upgrade-python-deps` updates transitive packages and hashes around
those decisions; it must not silently select a new direct component release.
Edit a direct pin deliberately, audit its source and metadata constraints, and
validate the affected component before accepting the regenerated lock.

Compile each lock for the Python runtime that consumes it, not for whichever
Python happens to run `uv` on the maintainer host:

- bundled macOS MLX environment: Python 3.12;
- derived SearXNG image: Python 3.14 on Linux;
- derived Python executor image: Python 3.11 on Linux.

The bundled MLX direct pins form one tested compatibility set:
`mlx-openai-server==1.8.1`, `mlx-embeddings==0.0.5`, `mlx-lm==0.31.3`,
`huggingface_hub==1.16.1`, and `transformers==5.14.1`. Upgrade them together in
a disposable Python 3.12 environment. Require hashed installation and
`uv pip check`, then validate the server CLI, MLX/Metal imports, handler
startup, real selected-model loading, and a multi-input embedding request with
ordered finite vectors of the expected dimension. Also run the model-cache
integrity check and the full-stack embedding readiness path. Keep
`typer==0.25.0` as a sixth direct pin: the Hugging Face CLI still uses Typer,
and 0.26 through 0.27 emit a `click.exceptions.Exit: 0` traceback on successful
help/exit paths while 0.25 does not. Re-test `hf cache verify --help` and a real
verification before moving it. The old 0.20 release can therefore be upgraded,
but the direct compatibility pin itself is still required. The former
Transformers 5.13 upper bound is obsolete.

For SearXNG, upgrade its exact WebSockets pin only after the direct Obscura
client suite and a live restricted-route fetch pass. Upgrade its exact
Playwright pin only with the pinned Onyx browser release and repeat the full
browser compatibility matrix. For the executor, retain exact SymPy selection
and run the executor image contract whenever that pin changes.

End-user upgrades retain the README contract:
`make down-* && git pull && make up-*`. The derived SearXNG and executor image
tags already hash their lock files and wrapper inputs, so their normal start
targets rebuild when those inputs change. For an existing bundled MLX
installation, `make up-full` compares a stamp against a fingerprint of the
hashed lock, target Python, installer implementation, and installer-contract
version before the host proxy starts. A mismatch atomically installs a fresh
environment, runs `uv pip check`, validates Python 3.12, and writes the stamp;
failure removes the partial replacement and restores the prior environment,
then fails startup. A matching environment is not modified. A custom embedding
endpoint and a machine with no prior bundled MLX model/environment are not
modified; first-time bundled setup still requires `make embedserv-install`.

### Documentation applicability gate

The stack documents are regression authorities, not post-upgrade release
notes. For every changed row below, read the linked documents against the new
source, effective Compose model, and live behavior. Validate that every
documented function and boundary still exists, is still necessary, and still
has adequate coverage. Remove obsolete implementation and documentation;
revise insufficient controls and tests. A passing patch install does not
satisfy this gate.

| Changed component or dependency | Required stack-document validation |
| --- | --- |
| Onyx API, workers, connectors, agents, LiteLLM use, or WebUI | [Patch information](onyx_patch_info.md), [Request handling](request_handling.md), [Internal network security](internal_network_security.md), [Local document RAG search](local_docs_rag_search.md), [Resource minimization](resource_minimization.md), and the user-facing [README](../README.md) |
| Obscura or its client/Playwright integration | [Request handling](request_handling.md), [Internal network security](internal_network_security.md), [VPN routing and restricted egress](vpn_routing_and_proxies.md), [Resource minimization](resource_minimization.md), and [Patch information](onyx_patch_info.md) |
| SearXNG, custom engines, or search scheduling | [Request handling](request_handling.md), [Internal network security](internal_network_security.md), [VPN routing and restricted egress](vpn_routing_and_proxies.md), [Resource minimization](resource_minimization.md), [Patch information](onyx_patch_info.md), and the user-facing [README](../README.md) |
| OpenSearch, document connectors, parsers, embedding model/server/shim, or local-document publisher | [Local document RAG search](local_docs_rag_search.md), [Internal network security](internal_network_security.md), [Resource minimization](resource_minimization.md), [Patch information](onyx_patch_info.md), and the user-facing [README](../README.md) |
| Code interpreter, Python executor, Code Agent, or executor dependencies | [Internal network security](internal_network_security.md), [VPN routing and restricted egress](vpn_routing_and_proxies.md), [Resource minimization](resource_minimization.md), [Patch information](onyx_patch_info.md), and the user-facing [README](../README.md) |
| Tor image, configuration, egress, onion ingress, or canonical origin | [Native Tor support](native_tor_support.md), [Internal network security](internal_network_security.md), [VPN routing and restricted egress](vpn_routing_and_proxies.md), [Request handling](request_handling.md), [Resource minimization](resource_minimization.md), [Podman support](podman_suport.md), and the user-facing [README](../README.md) |
| Compose, container engine, network, proxy, VPN, Tailscale, gateway, publisher, health, or lifecycle layer | [Internal network security](internal_network_security.md), [VPN routing and restricted egress](vpn_routing_and_proxies.md), [Podman support](podman_suport.md), [Resource minimization](resource_minimization.md), [Native Tor support](native_tor_support.md) when applicable, and the user-facing [README](../README.md) |
| Teep, Myst, MinIO, Tailscale, nginx, database/cache, or another support/source image | [Internal network security](internal_network_security.md), [VPN routing and restricted egress](vpn_routing_and_proxies.md), [Resource minimization](resource_minimization.md), [Podman support](podman_suport.md), any component-specific document above, and the user-facing [README](../README.md) |

When one change crosses rows, apply the union rather than selecting the
narrowest row. This includes runtime and base images such as Python, Alpine,
nginx, and fixed proxy/bridge images. Update `AGENTS.md` if contributor
instructions or validation commands change. Do not use implemented plans or
reference checkouts as current behavior documentation.

## Direct Obscura audit

Treat the [one-navigation contract](request_handling.md#obscura-one-navigation-contract),
[body and content contract](request_handling.md#body-and-content-handling),
[browser containment boundary](internal_network_security.md#browser-containment-and-residuals),
[direct Obscura route](vpn_routing_and_proxies.md#direct-obscura-path), and
[browser resource policy](resource_minimization.md#searxng-and-browser-search)
as acceptance criteria whenever the Obscura image, client, Playwright
integration, or bridge changes.

Audit these Obscura v0.1.11 areas (or their new equivalents):

- exact source commit/archive digest, digest-pinned multi-architecture builder
  and hardened runtime identities, safe archive extraction, ordered patch
  series, and zero-fuzz `git apply --check` plus application. Rebase each patch
  against the exact candidate source; do not carry an offset, already-applied
  hunk, or source-shape fallback into a release build;
- flattened Target attachment/session identifiers, target creation/closure,
  per-WebSocket context ownership, connection-thread cleanup, and the atomic
  live-connection cap;
- `Page.navigate` command-response/event ordering and lifecycle wait values;
- Network request/response/loading events, redirect collapse, main-frame
  Document selection, JavaScript navigation, request-id/loader-id aliases,
  challenges, and terminal frame URL;
- `Fetch.takeResponseBodyAsStream`, plain/base64 `IO.read`, `IO.close`, body
  eviction, content-type predicate, compressed/chunked/false-length behavior;
- per-body network retention, entry/alias/base64 amplification, per-connection
  IO stream accounting, and initial full-body allocation before retention;
- two-client isolation of cookies, HTTP clients, targets, browser contexts, and
  V8 isolates without a cookie-clear command; also re-audit the v0.1.11
  full-stealth split where accepted extra-header and User-Agent CDP overrides
  update the ordinary HTTP client rather than the wreq navigation client;
- repeated homepage/result and later-query navigation on one retained provider
  target/connection generation; native cookie, selected-profile,
  target-stealth-client/pool, and target-seeded fingerprint continuity;
  bounded CDP event state; disabled idle keepalive pings; one-hour idle
  target-then-connection expiry; and disposal after ambiguous transport,
  submission acknowledgement, event accounting, DOM, target-close command, or
  negative target-close-acknowledgement failure;
- identical target-owned stealth transport for initial GET and native form POST,
  including cookies, proxy, TLS-emulation profile, tracker policy, connection
  pool, 301/302/303 versus 307/308 redirect semantics, and final CDP
  `Network.requestWillBeSent` method reporting. Remove
  `0001-stealth-native-post.patch` only when upstream provides that complete
  contract without falling back to its ordinary context client;
- one unpredictable nonzero target seed injected before each new JavaScript
  realm and stable seed-derived screen/GPU/canvas/audio/hardware/device-memory
  surfaces across homepage, result, and later-query navigation. Remove
  `0002-target-fingerprint-seed.patch` only when upstream provides target- or
  context-stable fingerprint state with the same provider-session lifetime;
- the cumulative 45-second pre-navigation deadline across connect, target
  creation, attachment, and domain setup; the separate bounded
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
byte limits, ten-way direct `open_url` concurrency, simultaneous search
capacity, repeated-target provider continuity, replacement-connection
isolation, connection-cap refusal, complete cleanup, and no reconnect/refetch.
For SearXNG, recheck all five homepage control/form action/method/field
contracts against the exact selected image, prove GET and POST fixture
submission plus POST-to-GET and method-preserving redirects, stable target
profile/fingerprint state, and connection reuse below the library pool-idle
deadline.

### Pinned Obscura limitations and wrapper handling

Treat each item below as an explicit removal checkpoint on every Obscura pin
change. Do not carry a workaround forward merely because its old fixture still
passes:

- **Main Document retained-body eviction.** The per-page response store applies
  `OBSCURA_NETWORK_BODY_BUFFER_ENTRIES` to the main Document and
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
- **Flattened duplicate session IDs.** The pinned server reuses the attached
  target session ID when Playwright 1.58 calls `new_cdp_session(page)`, causing
  the driver to reject a duplicate target. The shared client uses one minimal
  raw WebSocket CDP session. Re-test the high-level API on either pin change and
  remove the raw transport only if one navigation, event ordering, actual-body
  access, deadlines, redaction, and cleanup remain equivalent.
- **Connection isolation.** The wrapper relies on v0.1.11 creating an isolated
  browser context and HTTP client for every WebSocket and does not clear
  cookies. Re-audit the immutable connection template, cookie-delta persistence
  behavior, every state-bearing CDP domain, two-client interleavings, repeated
  target cleanup, provider idle expiry, and connection-thread exit before
  retaining this simplification.
- **Renderer stalls and upstream diagnostics.** DOM and cleanup commands can
  fail to answer, and upstream logs can contain full URLs. Caller-owned
  absolute setup/request/cleanup deadlines, opaque correlation IDs, sanitized
  stages, and warning-level typed failures provide the wrapper boundary.
  Re-test a permanently blocked command, target/connection cleanup, server-log
  visibility, and URL redaction before removing any deadline or diagnostic.
- **Body-memory and classification gaps.** The server fully allocates a
  response before retention checks; the network byte limit is per entry and is
  amplified by entry count, base64, and aliases; each connection's IO store is
  separately bounded; and the stream API does not expose the internal
  text/binary decision. Retain strict limits and the mirrored
  `is_text_like_content_type` predicate until the tagged source and runtime
  tests provide authoritative aggregate accounting and classification.
- **Local-file and health gaps.** Disabling `--allow-file-access` does not
  repair the pinned ES-module local-file-read path, and the HTTP control-plane
  health endpoint does not prove every live connection healthy. Keep the container
  unprivileged/read-only and free of private mounts, and keep request failures
  visible rather than adding public probes or a fallback browser. Re-audit both
  source paths on upgrade.

Playwright Python remains pinned to the version supplied by Onyx (1.58.0) for
compatibility auditing and derived-image validation.
The current duplicate-session restriction and its removal criteria are tracked
in the pinned-limitations list above.

## Onyx API patch audit

Use [Patch information](onyx_patch_info.md) as the patch-family inventory.
Revalidate the user-visible flows in [Request handling](request_handling.md),
the trust and destination claims in
[Internal network security](internal_network_security.md), the Onyx and RAG
resource controls in [Resource minimization](resource_minimization.md), and the
[local-document major-upgrade checklist](local_docs_rag_search.md#major-upgrade-checklist).
Look for newly added network clients, API routes, callbacks, webhooks,
document-push/export paths, automatic fetches, scheduled work, and credential
or content transmission, not only drift in already patched symbols.

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

Exercise HTTP onion initial URLs and redirects in both crawler modes. The API
capability must be present only in the native-Tor egress Compose model, the
shared validator must accept any normalized host ending in `.onion` without
pre-validating its address, and the final-hop exception must require the fixed
Tor Unix socket. Clearnet HTTP and non-Tor remote-DNS upstreams must remain
denied when `EGRESS_ALLOW_HTTP_URLS=false`.

Re-derive the pinned Onyx SSRF levels and every consumer rather than assuming
one global policy covers Web Connectors, `open_url`, MCP, OAuth, integrations,
callbacks, and redirects. Inspect validation-time DNS, connection-time DNS,
redirect handling, proxy environment use, and final-hop destination pinning,
including DNS-rebinding/TOCTOU cases. Verify canonical CORS is not widened.
Inventory new authenticated and unauthenticated APIs, provider exports,
tracing, voice, Craft, mobile/SSO, and OpenAPI surfaces and their feature/auth
gates. Keep `DOCUMENT_PUSH_ENDPOINT_URL` and `DOCUMENT_PUSH_API_KEY` blank in
API and background unless document export is deliberately enabled. On every
Onyx pin, re-audit stock activation and the exact text, metadata, identifiers,
and credentials it transmits. Treat database-configurable tracing and provider
exports as network egress even when no Compose environment variable enables
them.

The stock crawler is the current reliability-oriented default. Every Obscura
pin upgrade must repeat comparable parallel URL batches, recording blocked,
empty-content, timeout, and successful results. Switch the default to `true`
only if the direct path meets that reliability bar while preserving the audited
privacy, cleanup, body-retention, and concurrency properties.

Re-audit the pinned Onyx symbols for:

- lite-mode `OpenURLTool.is_available` and its native `DISABLE_VECTOR_DB`
  crawl-only branch. Confirm `open_url` remains visible in the user skill list
  and constructed Agent tools, no indexed or link-based retrieval runs in lite
  mode, and a real crawler request succeeds without restoring indexed
  retrieval;
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
`nomic-ai/nomic-embed-text-v23` value maps only tokenizer construction to the
bundled `nomic-ai/nomic-embed-text-v1` tokenizer. Confirm the strict
`HuggingFaceTokenizer.__init__` source-shape check, both API and background
installation points, no Hugging Face lookup for v23, and unchanged behavior
for every other tokenizer model. The `nomic-ai` name prefix permits large
chunks only when multipass indexing is enabled; test that condition explicitly
if it is intended.

Re-audit Onyx's embedding caller failure contract on every pin. The wrapper
currently relies on query embedding making one request, passage embedding
retrying qualifying request/HTTP failures three times with fixed five-second
waits, and the model-server client retaining its 30-second connect and
600-second read timeouts. Confirm the shim's 30-second pool wait and shorter
540-second silent upstream-socket timeout release capacity before the caller
abandons a request, document processing propagates terminal exceptions into
indexing task failure handling, and the shim never retries a POST. The wrapper
lifecycle proxy retains its separate five-minute post-readiness blocked-socket
bound.

Audit the exact LiteLLM dependency installed in the pinned Onyx image,
including serialization, model metadata and cost-map loading, callbacks,
telemetry, retries, timeouts, proxy behavior, and environment handling.
Compare it with the matching source in `reference_repos/litellm` when
available. Exercise reasoning/tool history and configured inference through
the real Onyx call path; an isolated import or version comparison is
insufficient. Carry a wrapper control forward only while the installed
dependency still needs it, and add strict source-shape and behavioral coverage
for each new reliance.

Re-audit configured-inference model discovery in
`onyx.server.manage.llm.api`. The shared OpenAI-compatible `/v1/models` helper
must retain its patched signature and HTTP/error-handling source markers,
disable proxy-environment inheritance, keep exact internal Teep direct, and
send public, selected `host.docker.internal`, RFC1918-literal, and supported
operator-local bases through the fixed host-capable bridge. Prove that final-hop
host-port and LAN opt-ins remain the destination authority; model discovery
must not select the public bridge, add a direct LAN exception, or weaken mixed
and non-private DNS-answer rejection.

Verify `sitecustomize_api_server` is the only API bootstrap in both modes,
neutral shared helpers are imported rather than executed, and patch drift is
startup-fatal. Confirm `OpenURLToolOverrideKwargs.max_urls` remains ten and
re-audit the tool definition and `OpenURLTool.run`: the schema must advertise
`maxItems: 10`, ten deduplicated URLs must proceed, and an eleventh must produce
model-visible split guidance before retrieval or navigation rather than being
silently truncated. Test one shared 120-second monotonic invocation state through
outer/nested jobs, ten process-global permits, remaining-budget admission,
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

Reconcile this section with the documented
[SearXNG request flow](request_handling.md#searxng-search),
[browser containment](internal_network_security.md#browser-containment-and-residuals),
[direct Obscura route](vpn_routing_and_proxies.md#direct-obscura-path), and
[search resource policy](resource_minimization.md#searxng-and-browser-search).
Update the user-facing engine description in the [README](../README.md) if
available providers or operator behavior changes.

The current audited SearXNG pin is `2026.7.15-7b2199ecd`, sourced from commit
`7b2199ecdf75a00981583fa2f392a785dfc4fcee`. Re-audit the pinned offline and
online processors, engine loader, exception suspension mapping, result
containers, timeout/late-result handling, engine selection, round-robin
rotation patch points, and last-resort scoring.

Confirm that `use_default_settings.engines.keep_only` remains supported and
that the effective engine list contains exactly `google2`, `brave2`,
`duckduckgo2`, `startpage2`, and `bing2`. No inherited engine may initialize or
perform startup DNS/network work.

For every custom engine verify:

- offline registration and no normal SearXNG HTTP transport;
- URL/query/locale/safe-search/time/page construction;
- sanitized selector fixtures, normalization, explicit no-results marker,
  parser mismatch, exact terminal hosts, and shared block markers;
- DuckDuckGo No-AI query construction (`noai.duckduckgo.com`, `ia=web`),
  semantic organic-row/title/snippet selectors, the `networkidle2` search
  default, and unfinished deep-result preloads mapping to verification
  suspension;
- complete result query/fragment preservation; DuckDuckGo `uddg` wrapper
  admission only for relative or recognized DuckDuckGo `/l/` links and
  decoding exactly once without decoding nested URL values, signatures, or
  encoded separators a second time;
- DuckDuckGo form-based and rendered `anomaly-modal__modal` verification
  fixtures mapping to the ordinary CAPTCHA suspension path;
- one atomic pre-thread reservation consumed only by its exact engine attempt,
  one provider lease held through cleanup, exact monotonic 3.0-second start
  interval, no queued busy-provider thread, no all-unavailable fan-out, and
  visible unresponsive records for pre-execution unavailability, and
  different-provider concurrency;
- a busy, reserved, or cooling regular provider prevents last-resort
  selection; zero-result regular attempts advance sequentially; Bing becomes
  eligible only after every non-suspended regular provider has failed in that
  request or the remaining regular providers are suspended; pre-execution
  unavailability statistics do not name an ineligible last-resort provider;
- CAPTCHA, rate-limit, and access-denied exceptions use the ordinary offline
  suspension path; all three wrapper-owned suspensions remain 3,600 seconds so
  a blocked session expires before provider readmission; non-blocking failures
  become unresponsive records;
- one lazy shared event-loop thread and at most one retained connection per
  exact provider; different-provider navigations remain concurrent while the
  provider lease denies same-provider overlap; one retained target with
  bounded two-stage event accounting per ordinary query and at most two
  transactions/four stages for Bing's sparse-first-page pagination; native cookie, profile,
  fingerprint-seed, and target-owned stealth-client continuity before the
  sliding one-hour idle deadline;
  physical and synchronous-on-access expiry without keepalive pings or
  timer-delay revival; expiry-boundary queries waiting for the old connection
  to close before opening a replacement; a fresh connection after expiry; and
  no state sharing between providers or with `open_url`; target-close
  command/acknowledgement failures must taint the retained generation even
  when result collection otherwise completed; exercise the
  real mixed-capacity composition of five retained provider connections plus
  ten simultaneous request-scoped direct `open_url` connections, then prove
  every provider cookie jar remains usable after the fresh connections close;
- Bing's visible `One last step` challenge is a typed CAPTCHA while the same
  phrases in excluded script/style/template/noscript content are ignored;
- Bing rejects structurally valid but unrelated organic result sets when none
  of the query's bounded literal anchor terms occurs in the extracted rows;
- Bing excludes dictionary/answer widget metadata and organic result
  attribution labels containing `dictionary`;
- Bing does not advertise SafeSearch support and always requests `adlt=off`;
- Bing requests `first=11` only when a successfully parsed first page contains
  fewer than five valid results, reuses the exact lease/session, enforces the
  three-second start interval, shares the original 60-second engine deadline,
  propagates page-two blocks/timeouts/parser failures, removes exact duplicate
  URLs, preserves page order, and caps the combined result at ten;
- engines and the CDP client never retry failed transactions or select another
  provider;
- enabled round-robin normal/last-resort order and sequential next-provider
  rotation, with an attempted-set proof that no provider is selected twice in
  one search; treat rotation as defined scheduling behavior; pre-navigation
  failures must leave cooldown unstamped only when the sole `Page.navigate` was
  not authorized, while every post-guard or ambiguous-navigation failure
  stamps cooldown; retain the provider lease until provider-specific parsing
  and any blocking suspension are recorded, and prove a concurrent waiter
  cannot reserve in the interval between CDP cleanup and suspension; keep that
  outcome ordering in the SearXNG processor rather than adding provider,
  reservation, cooldown, or rotation ownership to the shared CDP client;
  simultaneous searches wait for regular-provider release or the nearest exact
  cooldown deadline without polling, empty success, false rate-limit
  statistics, or premature Bing use; capacity wait occurs before engine
  dispatch; dispatch failure releases the exact reservation; each actual
  rotation receives a fresh configured SearXNG engine window; plus
  disabled-round-robin selected-engine fan-out and disclosure warning.

The API patch must strictly unwrap only the pinned
`SearXNGClient.search` generic three-attempt decorator without replacing its
request/result implementation. Verify each query produces one SearXNG
`/search` HTTP request and that HTTP or parsing failure is returned without
whole-search replay. Also verify the patch does not alter the separate built-in
`open_url` crawler, its HTTP clients, or its transport-specific recovery.

Re-audit `onyx.utils.url.normalize_url`, `WebSearchResult.normalize_link`,
`WebContent.normalize_link`, the crawler-result merge alias, and the generic
OpenURL indexed-document normalizer. The wrapper must preserve complete URLs,
including query strings and fragments, through LLM-facing results, citations,
crawl matching, and document IDs; no query-blind cache or deduplication key may
conflate distinct resources. Connector-specific canonical normalization stays
authoritative. Test at least distinct Hacker News `item?id=...` URLs, a
fragment, and nested encoded separators or signatures. Remove the patch when
pinned Onyx natively preserves identity across all of these paths.

Re-audit Onyx's web-search batch shape before changing the Obscura connection
cap. The pinned tool runner merges repeated `web_search` calls into one call,
the merged query array has no item cap, and `WebSearchTool.run` dispatches every
query concurrently. It may run beside the separately merged `open_url` tool.
The mixed-capacity proof therefore uses five retained provider connections and
the existing ten process-global direct-`open_url` permits; the sixteenth-slot
refusal is a fail-closed server guard, not expected normal Onyx behavior.

The derived SearXNG image must install its complete Python dependency set from
the generated hashed `searxng/requirements.txt` lock, use one Granian process,
one request worker, one replica, perform no Chromium/browser download or
runtime installation, and pass the shared-client import validation. Verify
`GRANIAN_WORKERS=1` in every effective Compose model and exactly one live
request worker before accepting the five-provider/15-connection capacity
proof; multiple request workers would create independent provider owners and
invalidate both global serialization and capacity accounting. Its local image
tag must change when the upstream pin or any embedded Dockerfile, lock,
shared-client, or engine input changes. Its explicit `PYTHONPATH` must expose
the wrapper patches, SearXNG application root, and shared client before the
embedded interpreter imports `sitecustomize`; verify the real Granian
entrypoint logs every strict patch success and loads every custom engine.

## Routing and Compose audit

Use [Internal network security](internal_network_security.md) as the
reachability and compromise-boundary authority and
[VPN routing and restricted egress](vpn_routing_and_proxies.md) as the route,
DNS, and failure authority. Apply the engine-specific contracts in
[Podman support](podman_suport.md), lifecycle and cadence contracts in
[Resource minimization](resource_minimization.md), local full-mode topology in
[Local document RAG search](local_docs_rag_search.md), and
[Native Tor support](native_tor_support.md) for affected Tor layers.

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
- exact `host.docker.internal` defaults to no ordinary integration ports;
  full mode adds only its exact configured embedding authority, and the
  configured upstream-proxy authority remains usable without either becoming
  an ordinary destination;
- recreating the host policy forces fresh dependent-bridge health under both
  Docker and Podman, including warm invalid configuration;
- target DNS is Myst/provider, upstream-owned remote DNS, or explicit no-VPN
  DNS as selected, never Docker target DNS;
- wrapper-resolved destinations are complete-set validated and pinned;
  remote-DNS mode retains its explicit private-resolution residual;
- VPN/upstream/no-VPN failures, internal targets, redirects, subresources,
  metadata, mixed answers, and framing errors fail closed;
- long-lived CONNECT streams have no arbitrary total lifetime;
- Obscura remains one process with 15 isolated live-connection slots,
  65534:65534, read-only, capability-free, secret/data-volume-free, and without
  storage/file-access flags;
- full local RAG, embedding, configured inference, Teep, hardened publishers,
  and optional Tailscale behavior remain intact.

Inventory every added, removed, or renamed service, network, volume, mount,
published port, API endpoint, health check, and environment-controlled route.
Remove obsolete Compose files and layers rather than retaining compatibility
aliases. Render every applicable mode before removal. Only the API and
background may retain their deliberate public/host route selectors and direct
data-service reachability; preserve single-purpose attachment for Obscura,
SearXNG, executors, gateways, publishers, and final-hop bridges. Verify the
code-interpreter controller's container-engine socket authority, socket-free
executor children, and its intentional Podman omission.

For each process, test effective network attachments and credentials, then
reassess SSRF, redirects, DNS rebinding, proxy-environment use, host/LAN and
metadata access, Unix sockets, and direct data-service access. Distinguish
saved application route selection from what a compromised dual-homed process
can reach at the container-network layer. Confirm new APIs, callbacks,
document delivery, and background clients cannot leak prompts, documents,
credentials, URLs, or telemetry outside the documented policy.

## OpenSearch single-node policy audit

Treat [Local document RAG search](local_docs_rag_search.md) as the end-to-end
indexing/search authority, [resource storage and indexing
policy](resource_minimization.md#storage-and-indexing) as the heap, disk, and
idle-work authority, and [internal network
reachability](internal_network_security.md#reachability-and-trust-boundaries)
as the data boundary whenever OpenSearch, its Onyx client, or a
document/embedding component changes.

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

Reconcile all conclusions with the leak surfaces and browser boundaries in
[Internal network security](internal_network_security.md), the
[WebUI browser-egress contract](vpn_routing_and_proxies.md#webui-browser-egress),
the [telemetry and WebUI patch contract](onyx_patch_info.md#telemetry-automatic-fetches-and-webui-egress),
and the user-visible privacy claims in the [README](../README.md). Canonical
origin or onion changes additionally require the corresponding checks in
[Native Tor support](native_tor_support.md).

Re-audit every privacy setting against the new Onyx source rather than carrying
environment names forward by resemblance. Confirm:

- `DISABLE_TELEMETRY` still gates every anonymous telemetry call;
- empty backend Sentry, PostHog/marketing PostHog, HubSpot, Braintrust, and
  Langfuse credentials still prevent initialization, including in Celery
  workers;
- `AUTO_LLM_CONFIG_URL=""` still removes the recommended-model beat/poller
  work and performs no fetch, and `DISPOSABLE_EMAIL_DOMAINS_URL=""` still
  skips the public domain-list fetch;
- `LITELLM_LOCAL_MODEL_COST_MAP=true` still selects LiteLLM's packaged map
  before import and prevents its mutable GitHub cost/context-map fetch;
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

Whenever Onyx or LiteLLM changes, prove
`LITELLM_LOCAL_MODEL_COST_MAP=true` still takes effect before LiteLLM import
and that no mutable remote model map is fetched. Reconfirm canonical CORS,
blank document-push configuration and payload semantics, database-configured
trace/provider exports, and all new public or authenticated API surfaces
against the security audit above.

Inspect the pinned WebUI CSP implementation. Onyx emits a baseline policy
unconditionally from `web/src/proxy.ts` and adds its broader default, script,
connection, image, media, worker, and frame directives when
`WEB_STRICT_CSP_ENABLED=true`. Confirm Compose enables that runtime switch.
Keep the tracked nginx policy as a second CSP header so the browser intersects
it with upstream policy; the upstream policy is not equivalent while it admits
remote image, analytics, font, Sentry, or CDN origins. Remove the wrapper
header only after source and real-browser testing prove the upstream policy is
equally restrictive.

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
- a code-interpreter image must return a relative `/api/chat/file/{id}`
  `file_link` plus exact `[filename](file_link)` `response_markdown`, and that
  ordinary link must render from the relative endpoint through every frontend;
  confirm the strict patch still rejects drift in the function description,
  broader guidance, post-execution reminder, imported URL helper, and
  `PythonTool.run` signature; confirm replace-base Agents retain the Python
  guidance; split Markdown image and ordinary links at every stream boundary
  and verify both emitted and saved answers normalize exact chat-file paths,
  legacy absolute origins become relative, unrelated/incomplete Markdown is
  lossless, extensionless model labels are replaced with the authoritative
  generated filename from live and persisted tool metadata, an underscore-
  corrupted generated UUID is restored only by an exact authoritative match,
  wholly fabricated IDs remain visible and requestable for 404 diagnostics,
  omitted tool artifacts are not appended, and reloaded historical assistant
  messages use the same rule; use arbitrary valid UUIDs, multiple files,
  omissions, duplicate flushes, and every stream split in this test matrix;
  verify a
  non-UUID opaque chat-file ID bypasses the UUID-only `UserFile.id` lookup
  without weakening subsequent authorization or unknown-file handling;
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
- **Embedding shim:** validate one indexed vector per input, response
  reordering, duplicate/missing/out-of-range indices, arbitrary consistent
  dimensions, finite numeric values, request/response limits, active-handler
  bounds, upstream timeout, error scrubbing, and single-attempt forwarding.
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
  remains `Code Interpreter`. Confirm generated-file Markdown normalization is
  installed on the top-level streamed LLM step and saved-message reconstruction,
  and that the network-enabled description has a grammatically valid sandbox
  phrase. Then exercise a real `run_python` tool call.
- **Lite `open_url`, helper routes, embedding tokenizer/shim, privacy settings,
  and CSP:** retain their dedicated audits elsewhere in this checklist.

For local-document, tokenizer, model-name compatibility, embedding shim,
freshness, content-cap, and re-index avoidance changes, also complete the
[Local document RAG major-upgrade checklist](local_docs_rag_search.md#major-upgrade-checklist).
Prove each optimization still saves the documented work without suppressing a
required parse, embedding, index update, or failure. Embedding validation must
remain model-agnostic: accept arbitrary consistent dimensions and finite
numeric values, and do not encode current model dimensions, prefix conventions,
or batch behavior unless they are an explicitly configured contract. Test both
internal-search caps at zero/unlimited and at positive values, including the
aggregate total-content cap; a default of zero makes the limit dormant, not
obsolete. Keep the [README](../README.md) description user-facing and accurate
about the resulting re-indexing savings.

`make check-upgrade` preserves the required order: deterministic checks first,
then `make test-patch-images`, which strictly installs the API and PDF freshness
patch families inside the pinned Onyx backend image, the executor patch inside
the pinned code-interpreter image, the SymPy contract inside the derived Python
executor, and the SearXNG runtime/parser checks inside the derived image. Any
source, signature, model, prompt, or command shape mismatch is an upgrade
blocker, not a reason to weaken validation.

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

For support and source pins, apply the relevant current contracts rather than
treating an image-start test as sufficient: Myst and netns-holder use
[VPN lifecycle](vpn_routing_and_proxies.md#optional-vpn-and-default-no-vpn-lifecycle)
and [Myst resource ownership](resource_minimization.md#optional-myst-routing);
Tailscale, nginx, publishers, gateways, and proxy/bridge images use
[Internal network security](internal_network_security.md) and
[Podman support](podman_suport.md) where applicable; Teep and embedding images
use [Local document RAG search](local_docs_rag_search.md); and MinIO,
OpenSearch, Redis, PostgreSQL, and other data images use
[storage and indexing policy](resource_minimization.md#storage-and-indexing)
plus the documented reachability boundary.

Require an immutable Tailscale digest, exact Myst and Teep Git revisions in
both image labels and build arguments, and the MinIO source revision associated
with its release image. Run
`make health-inventory`, inspect effective startup/steady intervals, and verify
Docker Engine API 1.44+ preserves `start_interval` after the shared Compose
model probe has retained `start_interval`, `!override`, and `gw_priority`. For
Podman, verify
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

Revalidate this subsection together with
[VPN routing and restricted egress](vpn_routing_and_proxies.md),
[optional Myst resource ownership](resource_minimization.md#optional-myst-routing),
and [Podman support](podman_suport.md) on every Myst image, CLI, entrypoint,
network, health, or container-engine change.

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

## Resource minimization audit

Treat [Resource minimization](resource_minimization.md) as the acceptance
contract whenever any application, support image, health check, scheduler,
worker, model lifecycle, storage service, logging path, or Compose layer
changes. Inventory new scheduled tasks, workers, queues, monitoring, logging,
model loading, health activity, storage/indexing work, Craft processes, and
automatic network work. Determine whether every retained wrapper control is
still necessary and sufficient under the new upstream ownership; remove
obsolete duplicate owners rather than preserving compatibility.

For Onyx changes, re-audit the synchronous and asynchronous API database pool
settings against the 20-connection startup warmup, the five-base/15-overflow
contract, the read-only engine's two-base contract, and the 12-worker AnyIO
limit. Verify API and background retain `LOG_TO_FILE=false`; do not add a log
cleanup workflow or reduce the container engine's configured retention as a
substitute. Confirm `MAX_CONCURRENT_PORT_ATTEMPTS=1` still leaves one of the
two doc-processing slots available for ordinary indexing and that ownership
of the `port` queue has not changed.

Run the document's deterministic, pinned-image, lifecycle, and integration
checks, including `make health-inventory`, effective lite/full Docker and
Podman models where supported, idle process/PSS/thread inspection, a full
doc-drop/index/`internal_search` workload, model idle/unload behavior, and
relevant failure/restart tests. New consumption and lost controls are upgrade
failures even when functional request tests pass.

## Minimum deterministic validation

```sh
make check
```

For an image or patch upgrade, the expected local flow is:

```sh
make upgrade
make check-upgrade
```

`make test` runs only the deterministic Python suite.
`make test-patch-images` runs the strict Onyx, code-interpreter, executor, and
SearXNG contracts plus the image-dependent SearXNG parser tests.
`make test-obscura-image` runs the selected tagged server in a networkless
fixture network and proves connection isolation/capacity, repeated-target
provider continuity, concurrent navigation, static and JavaScript HTML,
redirect, PDF/raw/binary handling, main-body eviction behavior, full
TLS-impersonating stealth startup, and cleanup. The separate Tor and OpenSearch
targets validate their own image families.
`make test-all-images` aggregates all four focused image targets, and
`make check-upgrade` runs `make check` followed by that aggregate. Use the
focused target for a focused upgrade; reserve the aggregate gates for broad
`make upgrade`, multi-family changes, or release validation. None of these
targets starts the application stack or performs the live matrix.

Passing these targets does not complete the documentation applicability gate.
Run every affected checklist in [security verification](internal_network_security.md#verification-checklist),
[RAG major-upgrade validation](local_docs_rag_search.md#major-upgrade-checklist),
[resource regression protection](resource_minimization.md#regression-protection),
[routing validation](vpn_routing_and_proxies.md#validation),
[Podman live compatibility](podman_suport.md#live-compatibility-checklist), and
the [Tor change map](native_tor_support.md#change-and-validation-map). For an
Onyx upgrade, explicitly cover canonical CORS, disabled document push,
LiteLLM's local cost map, API database/thread limits, API/background
`LOG_TO_FILE=false`, the port-attempt limit, dual-homed API/background and
data-network reachability, and positive aggregate internal-search cap behavior.

For an Obscura-only pin change, update the tagged upstream image's
multi-architecture manifest digest, release version, exact source revision,
source-archive SHA-256, and digest-pinned Rust builder image in
`stack.versions.env`; audit the source archive root, the Dockerfile's
locked stealth build, and exact patch-series application; then run
`make obscura-build`.
Rebuild the derived SearXNG image only when its embedded shared client changed,
then run `make check`, `make test-obscura-image`, and
`make test-patch-images`. The image test must observe Obscura's full
TLS-impersonation startup diagnostic and the wrapper patchset startup marker,
not its tracker-blocking-only diagnostic.
Do not run the broad dependency-lock or unrelated component upgrade flow.

Also inspect effective lite/full Compose models through the Makefile. Search
current runtime files for removed service/env/path names and manually classify
historical implemented plans and read-only reference repositories rather than
using an indiscriminate zero-match rule.

## Live validation matrix

Use this matrix to prove the current-state documents, not merely container
health. Resolve every affected functionality, applicability, necessity, and
obsolescence question before accepting the upgrade; update or remove the
implementation, tests, and documentation together.

Where external dependencies are available, exercise lite/full with VPN,
explicit no-VPN, and a documented remote-DNS upstream. For each practical row:

- start with the Makefile and inspect health plus API, SearXNG, Obscura,
  gateway, bridge, final-hop, and Myst logs;
- run a real built-in crawler request for static and JS HTML, redirect, PDF,
  raw text, unsupported and oversized content;
- query every custom search engine and distinguish genuine results/no-results
  from visible provider CAPTCHA/429/access denial;
- verify a same-provider lease never overlaps, another provider can progress,
  exactly one Granian request worker owns all five sessions, and
  `open_url`/helpers/executors remain outside search scheduling;
- interrupt Myst, Obscura, gateway, and the final hop and confirm no stale
  connection reuse, direct fallback, or application restart storm;
- test full doc-drop crawl/freshness/native content-hash skip, embedding, and
  `internal_search`;
- test disabled/enabled executor network paths and LLM-facing descriptions;
- exercise configured inference, Teep, WebUI/authentication/MinIO, host
  publishers, and optional Tailscale where configured.

If credentials, funding, provider stability, private documents, or long-lived
external services prevent a row, record exactly what was not run and retain
the deterministic and topology evidence.
