# Direct Obscura Request Handling Plan

> **Status: planned.** This is a standalone implementation plan for replacing
> CRW with direct, single-navigation Obscura integrations for Onyx `open_url`
> and the custom SearXNG search engines. It describes a future topology, not the
> currently deployed stack. Until the plan is implemented and this file is
> moved to `docs/plans/implemented/`, the normative runtime documentation is
> [Request handling](../request_handling.md),
> [VPN routing and restricted egress](../vpn_routing_and_proxies.md), and
> [Internal network security](../internal_network_security.md).

## Executive Decision

Remove CRW from the runtime request path and make Obscura the single browser
renderer for both of these flows:

```text
OnyxWebCrawler
  -> direct CDP client
  -> Obscura
  -> narrow egress bridge
  -> shared restricted-egress policy
  -> VPN, configured upstream proxy, or explicit no-VPN route

SearXNG custom search engine
  -> direct Playwright/CDP client
  -> the same Obscura instance
  -> narrow egress bridge
  -> shared restricted-egress policy
  -> VPN, configured upstream proxy, or explicit no-VPN route
```

The Onyx path must perform exactly one browser navigation for each requested
URL and reuse that navigation's main-resource bytes or rendered DOM. HTML is
converted with Onyx's existing cleanup pipeline. PDF and supported non-HTML
documents are retrieved byte-for-byte from the same navigation and passed to
Onyx's existing parsers. There must be no preliminary `HEAD`/`GET`, CRW
prefetch, requests-based fetch, local-Chromium retry, MCP call, Obscura CLI
`--dump original` call, or other second content fetch.

The SearXNG path is HTML-only. Each custom engine must navigate its search URL
through Obscura, read the rendered DOM, and parse it with the engine's existing
selectors and result normalization. The current `_crw.py` integration must be
changed to use the Playwright Python library and a raw CDP session directly,
then renamed to `_obscura.py` as part of the same cutover so the deployed code
does not retain a misleading CRW abstraction.

This is a simplification project, but removal count is not the only success
measure. The result must preserve or strengthen:

- one-fetch document handling, including PDFs;
- search-engine rendering and anti-bot visibility;
- browser fingerprint consistency and explicit wait behavior;
- search-host rate and concurrency control;
- private/internal destination denial, including redirects;
- DNS confinement to the selected final hop;
- VPN/upstream-proxy/no-VPN fail-closed routing;
- lite- and full-mode `open_url` availability;
- local-document RAG behavior in full mode;
- strict patch application and upgrade validation;
- bounded download, parse, timeout, and concurrency behavior; and
- useful, non-secret diagnostics.

## How to Use This Plan

Implement this as one atomic request-path migration. It is reasonable to
prepare and test commits by workstream, but no supported deployed topology may
send some Onyx requests through CRW and others directly through Obscura. A
hybrid state would retain the complexity this plan removes and make DNS,
rate-limit, fallback, readiness, and double-fetch behavior difficult to prove.

Before changing a subsystem, read the corresponding current documentation:

- [Request handling](../request_handling.md) for current `web_search`,
  `open_url`, CRW, Obscura, CDP shim, cookies, waits, rate limits, and fallback
  behavior.
- [VPN routing and restricted egress](../vpn_routing_and_proxies.md) for the
  trusted routing namespace, final-hop policy proxies, upstream proxy modes,
  readiness, and autoheal behavior.
- [Internal network security](../internal_network_security.md) for component
  bridges, destination validation, Docker-internal name blocking, DNS
  behavior, Onyx SSRF interactions, and residual risks.
- [Onyx patch information](../onyx_patch_info.md) for every runtime patch and
  its strict startup contract.
- [Onyx wrapper patch upgrades](../onyx_patches_upgrade.md) for upstream
  source-shape audits and image/ref upgrade procedure.
- [Local document RAG](../local_docs_rag_search.md) for the full-mode Web
  connector and PDF indexing path that this migration must not alter.
- [Restricted egress plan](implemented/restricted_egress.md) for the decisions
  that produced the current component-network and final-hop-proxy model.

When current implementation and documentation disagree, resolve the drift as
a bug. Do not weaken an implementation to match stale prose.

## Version Scope and Reference Sources

This plan is written against the committed pins in
[`stack.versions.env`](../../stack.versions.env). Re-audit all cited symbols
when any pin or local override changes. Line numbers are deliberately omitted:
symbols and behavior are more stable upgrade anchors than line positions.

| Component | Current version/ref | Relevant reference checkout and behavior to re-check |
| --- | --- | --- |
| Onyx | image `v4.2.5`; ref `b7482a59fb74503d5ec3dcde0ae5beac7b4905ff` | [`reference_repos/onyx`](../../reference_repos/onyx): `OnyxWebCrawler` in `backend/onyx/tools/tool_implementations/open_url/onyx_web_crawler.py`; provider selection in `backend/onyx/tools/tool_implementations/web_search/providers.py`; PDF detection in `backend/onyx/utils/web_content.py`; parsing in `backend/onyx/file_processing/extract_file_text.py`; local Chromium helper in `backend/onyx/utils/playwright_fetch.py`; Web UI provider cards in `web/src/lib/webSearch/utils.ts`. |
| Onyx code interpreter | image `0.4.4`; ref `8950eadc06567798ec61354f24260e5dc996684b` | [`reference_repos/python-sandbox`](../../reference_repos/python-sandbox). It is a regression target, not part of the new browser path. |
| SearXNG | image `2026.6.26-f8ffbf36f`; ref `f8ffbf36f9039ecb232dcfab5263b02b36fed9f5` | [`reference_repos/searxng`](../../reference_repos/searxng): online processors in `searx/search/processors/online.py`; direct-callback support in `searx/search/processors/offline.py`; example in `searx/engines/demo_offline.py`; exception/suspension behavior in the processor base and search orchestration. |
| CRW, removal baseline | image `ghcr.io/us/crw:0.23.0`; ref `dc497fdf35a6d1a941391cbade663601eedac1b6` | [`reference_repos/crw`](../../reference_repos/crw). Use it only to enumerate behavior being preserved or intentionally removed: scrape/search response mapping, PDF handling, CDP escalation, request waits, rate controls, and proxy assumptions. The target stack has no CRW runtime pin. |
| Obscura | image `h4ckf0r0day/obscura:0.1.10`; ref `50e66320b0842d2844ce298957a335a6bed95c4d` | [`reference_repos/obscura`](../../reference_repos/obscura): main-resource aliasing and body storage in `crates/obscura-browser/src/page.rs`; `Network.getResponseBody` in `crates/obscura-cdp/src/domains/network.rs`; `Fetch.takeResponseBodyAsStream` in `crates/obscura-cdp/src/domains/fetch.rs`; `IO.read`/`IO.close` in `crates/obscura-cdp/src/domains/io.rs`; navigation waits in `crates/obscura-cdp/src/domains/page.rs`; CLI dump behavior in `crates/obscura-cli/src/main.rs`; MCP result behavior in `crates/obscura-mcp/src/lib.rs`. |
| Playwright Python | `1.58.0` in the pinned Onyx source | [`reference_repos/onyx/pyproject.toml`](../../reference_repos/onyx/pyproject.toml). Pin this exact client version in the derived SearXNG image unless compatibility testing establishes a deliberate change. No browser binary is needed in that image for CDP-only operation. |
| Mysterium | `local/private-onyx-myst:20260713` | [`myst`](../../myst) plus the current routing docs. Preserve its data-plane health check, VPN reconnect, and autoheal contract. |
| Teep | ref `6413fe0547b449e67f7296986fe8b8ffbc9bbcd2` | [`teep`](../../teep). No functional change is planned. Include it in full-stack regressions. |
| Namespace/proxy support | `alpine:3.20`, `python:3.12-slim-bookworm`, `python:3.12-alpine`, `alpine/socat:1.8.0.3` | [`docker-compose.yaml`](../../docker-compose.yaml) and the version manifest. These own namespace holding, policy-proxy runtimes, and narrow gateways. Required Python dependencies must be locked into images at build time. |
| CDP shim, removal baseline | `local/private-onyx-cdp-shim:20260713` | [`crw/cdp_shim.py`](../../crw/cdp_shim.py) and its Dockerfile/lock. Its wait rewriting, context-proxy stripping, stealth-script stripping, cookie clearing, target selection, and trace behavior form an explicit obsolescence checklist. |
| Full-mode search/data support | `valkey/valkey:9-alpine`, `minio/minio:RELEASE.2025-07-23T15-54-02Z-cpuv1` | Valkey remains SearXNG's cache; MinIO remains Onyx object storage. Neither becomes a CDP or document-transfer intermediary. |
| Exposure/recovery support | `tailscale/tailscale:stable`, `willfarrell/autoheal:latest` | Optional Tailscale exposure is a regression target. Autoheal retains its narrow Myst recovery role. Because these are moving tags, record the tested image identity during implementation and re-test on every pull. |

The implementation must record any required Obscura fix as a new immutable
image version and source ref. Do not silently keep the `0.1.10` label while
running modified source.

## Goals

1. Replace CRW-backed Onyx content fetches with a single-navigation,
   PDF-capable direct Obscura client.
2. Replace CRW-backed SearXNG custom engines with direct Obscura rendering.
3. Remove CRW, the CRW validation DNS server, the CRW API gateway, the CRW
   prefetch bridge and policy instance, and the CDP shim.
4. Remove CRW-specific networks, secrets, images, configuration, build targets,
   health dependencies, documentation, and tests.
5. Replace browser, MCP-browser, Onyx-helper, and optional executor policy
   processes with one source-aware restricted-egress listener. All public
   hosts, including search engines, receive the same destination policy.
6. Retain only gateways and bridges that still establish a real network
   boundary. Do not retain separate policy processes merely to label callers.
7. Provide one user-facing PDF byte limit, defaulting to 50 MiB and allowing
   deliberate values above 50 MiB, and propagate it correctly to both Onyx and
   Obscura.
8. Keep actual byte counts authoritative when `Content-Length` is absent,
   false, duplicated, incompatible with `Transfer-Encoding`, or describes a
   compressed representation.
9. Preserve exact main-resource bytes for PDFs and supported non-HTML
   documents, without a second network hit.
10. Preserve search-result quality, engine retry/suspension behavior, waits,
   challenge visibility, and cross-client rate controls.
11. Preserve final-hop destination validation and resolver selection without
    sending user target hostnames to Docker's embedded resolver.
12. Make patch drift, missing dependencies, CDP incompatibility, oversized
    content, and unavailable bodies loud and diagnosable.
13. Leave full-mode local document ingestion unchanged. Preserve optional
    executor network isolation and explicit enablement while intentionally
    allowing its proxy path to reach public search engines.

## Non-Goals

- Do not route the main Onyx content-fetch path through Obscura MCP. MCP
  `browser_navigate` returns a tool-oriented summary, not a byte-preserving
  response body, and the MCP process is intentionally a separate agent-facing
  browser boundary.
- Do not use Obscura CLI `--dump original`. At the pinned ref that command uses
  a separate HTTP client fetch and would violate single-fetch semantics.
- Do not replace browser rendering with plain `requests`, `httpx`, `curl`, or
  SearXNG's normal online HTTP processor.
- Do not add a direct-network fallback when Obscura, its policy proxy, the VPN,
  or an upstream proxy is unavailable.
- Do not merge Obscura into the trusted Mysterium namespace. Its restricted
  control and egress networks remain security boundaries.
- Do not expose CDP on a host port or to executor, MCP, data, or general Onyx
  networks.
- Do not retain search-engine deny lists in the egress policy. Executor code,
  Onyx helpers, and `open_url` may contact a public search engine just like any
  other public destination. Direct executor/helper traffic does not receive
  Obscura's browser fingerprint or navigation rate scheduler, so 403/429
  responses remain visible to the caller rather than triggering a hidden
  fallback.
- Do not merge the Obscura, MCP, and executor component-side egress bridges or
  networks. Their fixed-port bridges prevent restricted components from seeing
  the trusted routing namespace or each other; identical destination rules do
  not make those network boundaries obsolete.
- Do not make the PDF byte limit unlimited. Values above 50 MiB are supported,
  but the value must remain a positive finite integer.
- Do not claim that increasing Obscura's retained-body limit bounds its initial
  network allocation. At the pinned ref Obscura reads the full HTTP body before
  deciding whether to retain it.
- Do not change the full-mode local-document Web connector, doc-drop server,
  embedding shim, or PDF freshness patch as part of this migration.
- Do not remove Onyx helper **egress routing** merely because the
  `OnyxWebCrawler` no longer uses local Chromium. Other Onyx helper downloads,
  the Web connector, Highspot, and upstream Playwright paths still use the
  shared policy through loopback; only the dedicated helper proxy process is
  removed.
- Do not preserve obsolete CRW names as compatibility aliases.

## Current State and Why It Is Too Complex

The deployed request chain intentionally layered CRW around Obscura to gain
rendering, PDF handling, prefetch filtering, and search-engine integration.
Restricted egress subsequently added a gateway or bridge at every trust
boundary. The result is secure but operationally dense.

The current Onyx content path can involve:

```text
Onyx -> CRW service gateway -> CRW
     -> CRW prefetch bridge -> prefetch-blocking policy proxy
     -> final hop
     -> CDP shim -> Obscura -> browser egress bridge
     -> browser policy proxy -> final hop
```

The current SearXNG path adds the SearXNG service gateway in front of the same
CRW chain. CRW's validation resolver, CRW API authentication, CDP shim request
rewrites, separate prefetch policy, multiple internal networks, and readiness
edges exist largely to make those layers safe.

Obscura already exposes the CDP primitives needed to collapse the chain:

- rendered DOM through a normal page target;
- main-document response events and loader/request identity;
- main-resource body lookup;
- `Fetch.takeResponseBodyAsStream` plus `IO.read` and `IO.close`; and
- explicit server-side navigation wait modes.

The migration is not a mechanical URL replacement. The following behaviors
currently reside in different layers and must be deliberately re-homed:

- PDF versus HTML dispatch;
- exact-body retention and content limits;
- wait-until selection;
- cookie clearing;
- browser fingerprint and proxy ownership;
- search-host serialization and minimum start interval;
- status, redirect, CAPTCHA, and empty-content error mapping;
- query/body redaction in diagnostics;
- URL syntax and final-hop destination validation;
- service readiness and VPN liveness dependencies; and
- patch source-shape validation.

## Required Obscura Capability Gate

Do not cut over production traffic solely because the current CDP methods
exist. First add an integration test against the exact pinned Obscura image and
prove all of the following:

1. A normal HTML main resource produces a rendered DOM and does not require a
   second HTTP fetch.
2. A PDF served as `application/pdf` is retrievable byte-for-byte through the
   main navigation's body handle.
3. Inline and `Content-Disposition: attachment` PDFs are both retrievable
   without a download-manager refetch, `HEAD`, or follow-up range request.
4. A PDF served as `application/octet-stream`, without a useful extension, is
   byte-for-byte retrievable and detectable by `%PDF-` magic.
5. A PDF served with a misleading text content type remains byte-exact. At the
   current ref, content-type-based text classification may perform lossy UTF-8
   conversion. If the test fails, fix Obscura before cutover.
6. Missing retained body, deliberately discarded oversized body, empty body,
   and true zero-length body are distinguishable states.
7. The completed response exposes an authoritative decoded entity-body byte
   count even when `Content-Length` is absent or wrong.
8. `IO.read` can drain the body in bounded chunks, returns base64 metadata
   correctly, and `IO.close` releases the entry.

The preferred fix is an upstream Obscura release. If that is unavailable, add
a narrowly scoped source build in this repository, pin its exact source ref,
document it in the version manifest and upgrade guide, and fail image build or
startup if the expected source shape changes. The minimum Obscura behavior is:

- retain the main response as original bytes independent of a misleading MIME
  classification;
- derive CDP text/base64 presentation at the protocol boundary rather than
  irreversibly converting stored bytes;
- report an explicit body-retention outcome and actual body length; and
- retain one canonical body object for loader/request aliases rather than
  silently making an avoidable full-body clone.

`reference_repos/obscura` remains audit-only. A temporary local build should
use an `obscura/` Dockerfile plus committed patch files, fetch/checkout the
immutable `OBSCURA_REF`, run `git apply --check` before applying, build the
release binary in a build stage, and copy only runtime artifacts into the final
image. Add `OBSCURA_REF` and a distinct local `OBSCURA_IMAGE` tag to
`stack.versions.env`. Use the existing build proxy arguments/host build route;
never make the running Obscura container install compilers or packages.

True incremental network-to-consumer streaming is desirable but not a cutover
requirement. Until Obscura supports it, the plan intentionally accepts higher
memory use for configured large documents and documents that residual risk.

## Target Topology

### Shared Rendering Instance

Use one non-MCP Obscura server for Onyx content fetches and SearXNG search
rendering. Keep the existing separate `obscura-mcp` instance for agent MCP
tools. Sharing the rendering instance preserves one browser fingerprint and
allows Obscura to coordinate all top-level search navigations, while separate
MCP remains a distinct trust and capacity boundary.

```text
                                      internal obscura-control network
                                     +--------------------------------+
Onyx API namespace                   |                                |
  direct CDP client                  |  Obscura CDP :9222             |
       |                             |     --stealth                  |
       +-> obscura-cdp-gateway ------+     --proxy=egress bridge      |
                                     |        |                       |
SearXNG                              |        +-> browser-egress       |
  direct CDP client -----------------+            bridge              |
                                     +----------------|---------------+
                                                      v
                                             shared restricted-
                                               egress policy
                                                      |
                                      Myst / upstream proxy / no-VPN

Onyx API -- private Unix socket + passed memfd --> Onyx PDF parser
                                                    network_mode: none
```

Onyx services currently share `network_mode: service:netns-holder` and cannot
be attached directly to a Docker bridge. Therefore retain one narrow raw TCP
gateway:

- `obscura-cdp-gateway` attaches to the Onyx ingress network and
  `obscura-control`;
- it forwards only the Obscura CDP port;
- it exposes no host port and no policy/configuration port; and
- its health check verifies `/json/version` through the forwarded port.

SearXNG may attach directly to `obscura-control`; it does not need a second
gateway. Neither client attaches to `browser-egress`.

The PDF parser is not attached to these networks. It receives only already
fetched, size-bounded bytes through the private Unix socket described below.

### Networks to Retain

- the restricted Obscura control network;
- the restricted Obscura browser-egress network;
- a narrow Onyx-to-CDP ingress network for the CDP gateway;
- the current SearXNG/Valkey and SearXNG service-ingress networks;
- distinct Obscura-renderer, Obscura-MCP, and optional executor egress
  networks and their narrow bridge containers;
- one shared internal policy-side upstream network used only by those fixed
  bridges and `netns-holder`;
- one shared restricted-egress policy service in the trusted namespace;
- the separate Obscura MCP control/egress/gateway networks;
- optional executor networks; and
- full-mode data and local-RAG networks.

The Onyx helper path no longer has its own proxy service or network: trusted
Onyx processes in the shared namespace use the shared listener through
`127.0.0.1`. Existing stack-owned `NO_PROXY` rules remain necessary for
trusted internal services.

Also add one private Unix-socket volume shared only by the Onyx API service and
the networkless PDF parser. It is an IPC boundary, not a Docker network or a
place to persist document bodies.

### Networks to Delete

Delete every CRW-only bridge after verifying no other service is attached:

- CRW API/control network;
- Onyx-to-CRW ingress network;
- CRW-to-SearXNG network;
- CRW-to-CDP-shim network;
- CRW prefetch-egress network; and
- prefetch-policy upstream network.

Also replace `browser-policy-upstream`, `mcp-browser-policy-upstream`, and the
optional `executor-policy-upstream` with the single
`restricted-policy-upstream` network. Preserve their component-side egress
networks.

Use the current names in [`docker-compose.yaml`](../../docker-compose.yaml) as
the deletion checklist; do not retain empty networks for compatibility.

## One Shared In-Process CDP Client

Create a neutral Python package at, for example,
`obscura/python/private_onyx_obscura/`. For Onyx, bind-mount that directory
read-only at `/opt/private-onyx/obscura-client` and add that exact directory to
the api server's base `PYTHONPATH` in both lite and full modes. For SearXNG,
copy the same package into the derived image at the same path during image
build and set its image-level/runtime `PYTHONPATH` explicitly. Do not copy a
second editable implementation under `searxng/` or an Onyx patch directory.
Both integrations must import this package rather than maintaining subtly
different navigation, body, validation, logging, and cleanup code.

The package is not a sidecar and does not listen on a socket. It uses
Playwright's Python CDP connection and a raw `CDPSession` for protocol details
that Playwright's high-level `page.goto()` does not expose reliably with
Obscura.

### Public Result Contract

Return a typed result with at least:

- requested URL;
- final main-document URL;
- main-document status code and status text when available;
- normalized response headers;
- parsed content type and charset;
- loader/request identity;
- authoritative actual body byte count;
- rendered HTML when requested;
- raw entity bytes when requested;
- navigation and body-read timing;
- challenge/blocked indicators when detectable; and
- a typed failure category.

Do not return raw bodies in exceptions or logs. The caller must be able to
distinguish at least:

- invalid or locally forbidden URL;
- final-hop private/internal-policy denial;
- navigation timeout;
- HTTP error status;
- rate-limit or CAPTCHA/challenge response;
- response body above the configured limit;
- body not retained/body unavailable;
- body stream decode/protocol failure;
- unsupported content type;
- empty/unparseable content; and
- CDP transport loss.

### Connection and Target Lifecycle

For each logical fetch:

1. Connect to the configured browser CDP endpoint.
2. Use the default browser context and create a fresh page/target.
3. Attach a raw CDP session to that target.
4. Enable the required `Network`, `Page`, and lifecycle events before
   navigation.
5. Register response/loading listeners before sending `Page.navigate`.
6. Send raw `Page.navigate` with an explicit Obscura-supported `waitUntil`
   value.
7. Identify the final main `Document` response by target frame and loader ID,
   updating it across redirects.
8. Wait for the requested readiness and record status, headers, final URL, and
   actual body length.
9. Read either rendered DOM or the main-resource byte stream, never both unless
   the caller explicitly needs both from the same navigation.
10. Close the IO handle, detach the CDP session, close the page/target, and
    disconnect the client in `finally` blocks.

Do not create a new incognito browser context per request. Current Obscura
fingerprint and cookie behavior is tied to its default context, and historical
CRW proxy-context options are exactly what the CDP shim had to strip. A fresh
target in the default context gives request isolation without reintroducing
those incompatible context settings.

### Explicit Wait Semantics

At the current Obscura ref, `Page.navigate` accepts an Obscura-specific
`waitUntil` value, while Playwright normally performs a client-side wait after
issuing its own navigation command. Use the raw CDP command so server-side wait
selection is unambiguous.

Define separate internal defaults for content pages and search pages, mapped to
the exact wait names accepted by the pinned Obscura source. Preserve current
behavior deliberately:

- content pages should normally wait for DOM content readiness, then allow a
  bounded post-DOM/lifecycle condition needed for rendered content;
- search engines should use the stronger current CRW/search wait where
  required by results populated after initial DOM readiness; and
- every wait must have a hard deadline and cancellation cleanup.

Do not replace readiness with a fixed sleep. Any small delay needed for a
specific engine must be engine-specific, justified, bounded, and tested.

### Main-Response Identification

Do not assume the first response event is the requested document. Subresources,
service workers, and redirects can race. Track the page's main frame and the
`Document` response/loader returned by navigation. Record each redirect only
to update final URL and response identity; never initiate an extra validation
fetch.

If Obscura aliases main-resource bodies by loader ID, use that documented
identity. Treat protocol changes as a strict incompatibility, not as a reason
to guess another response body.

### Raw Body Retrieval

For PDF and other raw formats:

1. Inspect a syntactically valid `Content-Length` only as an early rejection
   hint.
2. Never trust it as the final size or existence check.
3. Compare Obscura's completed actual body length to the configured limit.
4. Open the same navigation's body with
   `Fetch.takeResponseBodyAsStream`.
5. Loop over `IO.read` with a bounded requested chunk size.
6. Base64-decode only chunks marked as base64.
7. Increment the actual decoded entity-byte count before appending each chunk
   and stop with an oversized-content error as soon as the limit is exceeded.
8. Require the drained byte count to agree with the authoritative completed
   count when the protocol provides one.
9. Always call `IO.close`, including on timeout, cap rejection, decode failure,
   cancellation, and caller exception.

The limit applies to decoded HTTP entity bytes returned by Obscura, not wire
framing and not `Content-Length` alone. This is required for:

- HTTP/1.1 chunked responses;
- HTTP/2/3 responses that have no transfer-encoding header;
- absent, malformed, duplicated, or conflicting `Content-Length`;
- compressed responses whose decoded body differs from the advertised wire
  representation; and
- servers that lie about length.

Let Obscura's HTTP stack reject invalid HTTP request/response framing. The
client still enforces the count on bytes it receives. A `Content-Length` equal
to the cap is allowed; one byte over is rejected.

`Fetch.takeResponseBodyAsStream` in Obscura `0.1.10` streams from an already
buffered response, not directly from the network. Chunked CDP reads therefore
bound the client-side append loop but do not eliminate Obscura's peak body
allocation. Documentation and memory tests must say so plainly.

Content classification may require both a bounded raw prefix and a rendered
DOM from the same navigation. Open at most one body stream: read only the
prefix needed for magic detection, then either continue draining that same
stream for a raw document or close it before using the already-rendered DOM.
Never reopen the stream or navigate again merely because the MIME type was
misleading.

### Rendered HTML Retrieval

For HTML/XHTML/search pages, call Playwright `page.content()` after the explicit
navigation wait. Do not retrieve a second copy of the main resource merely to
classify it. Response headers and a small body prefix from the retained
navigation may be used if classification genuinely requires it, but must not
cause a second navigation or HTTP request.

Preserve Onyx's separate 20 MiB HTML safety limit from
`DEFAULT_MAX_HTML_SIZE_BYTES`. Enforce it against the actual main-response body
and against the UTF-8 size of the serialized rendered DOM, because script can
expand the DOM beyond the original body. The latter check necessarily occurs
after serialization with the current API; document that transient allocation.
The PDF setting below must not silently raise this HTML limit.

### URL Validation Without DNS Leakage

The shared client performs syntax and obvious-literal rejection only:

- allow only `https`, plus `http` when the explicit stack setting permits it;
- reject credentials in URLs;
- normalize IDNA and reject malformed host/port forms;
- reject loopback, unspecified, link-local, multicast, private, carrier-grade,
  documentation, benchmark, and other non-public IP literals;
- reject `localhost`, single-label hosts, Docker internal aliases/default host
  names, and the repository's canonical internal suffix/name blocklist; and
- strip the fragment from the network navigation while preserving it in the
  requested/final presentation as appropriate; fragments are browser-local and
  must not cause a second fetch.

Do **not** resolve the user target hostname in Onyx, SearXNG, a validation DNS
sidecar, or Docker's embedded resolver. The shared restricted-egress policy is
the authoritative destination validator for domain names and redirects. It
must perform resolution through the resolver appropriate to the selected
route, as documented in
[VPN routing and restricted egress](../vpn_routing_and_proxies.md):

- VPN/provider resolver when no upstream proxy is configured and VPN mode is
  enabled;
- remote target resolution through `socks5h` or equivalent proxy semantics
  when an upstream proxy is configured; and
- the explicitly selected system/no-VPN resolver only in explicit no-VPN mode.

Only the internal CDP service name is resolved through Docker DNS. User target
hostnames must not appear there.

The final-hop policy must repeat host/IP/internal-name checks after resolution
and for every CONNECT target. Obscura's own private-target protection is useful
defense in depth, not a replacement for the final-hop policy.

### Diagnostics and Redaction

Replace CDP-shim tracing with neutral direct-client tracing. Configuration may
include an internal trace level and destination, but safe defaults must:

- log scheme, normalized host, port, status, body size, timing, wait mode, and
  failure category;
- redact query values by default, because search terms and tokens occur there;
- never log bodies, cookies, authorization headers, proxy credentials, API
  keys, document contents, or CDP stream chunks;
- allow-list only explicitly safe query-key names if any key names are logged;
  and
- identify whether a failure occurred in connect, navigate, wait, body drain,
  parse, or final-hop policy without exposing private content.

## Onyx `OnyxWebCrawler` Design

### Provider Selection

The supported Onyx content provider becomes the built-in **Onyx Web Crawler**.
The Admin UI must not be configured with a Firecrawl URL for this stack.
Existing installations must be told to select the built-in provider after
upgrade; silently translating a saved Firecrawl configuration is not required
and would hide stale state.

The current provider construction and UI behavior must be re-audited in:

- [`reference_repos/onyx/backend/onyx/tools/tool_implementations/web_search/providers.py`](../../reference_repos/onyx/backend/onyx/tools/tool_implementations/web_search/providers.py); and
- [`reference_repos/onyx/web/src/lib/webSearch/utils.ts`](../../reference_repos/onyx/web/src/lib/webSearch/utils.ts).

The runtime patch must ensure the built-in provider is available in lite and
full mode and must fail startup if upstream symbols or signatures have drifted.

### Narrow Runtime Patch

Add the integration to the common strict patch layer in
[`onyx/patches/sitecustomize_base`](../../onyx/patches/sitecustomize_base), then
activate it from both lite and full bootstraps. Patch the narrowest stable
`OnyxWebCrawler` fetch boundary, currently `_fetch_url`, and any imported limit
constants/provider bindings required to make the configured values effective.

At startup the patch must verify:

- the expected module, class, method, and signature exist;
- the upstream method still has the requests-first/local-Playwright fallback
  shape the patch intends to replace;
- PDF/HTML parser helpers have the expected signatures;
- the configured CDP URL and byte limits are valid;
- the shared Playwright client version can connect to the pinned Obscura CDP
  protocol; and
- no stale CRW/Firecrawl wrapper path is selected for the built-in provider.

Strict mode remains the default. Failure must prevent API service readiness.

### Single-Fetch Rule

The patched `OnyxWebCrawler` must never call:

- `ssrf_safe_get` or another requests-based document fetch;
- upstream `fetch_rendered_html` or local Chromium as a fallback;
- CRW or a Firecrawl-compatible endpoint;
- Obscura MCP;
- Obscura CLI `--dump original`; or
- a second Obscura navigation after MIME or parser failure.

Retries that initiate another main-document navigation are disabled by default.
If a future retry policy is proposed, it is a user-visible semantic change and
must distinguish intentional retries from the single-fetch guarantee.

Preserve upstream `contents()` ordering and per-URL failure isolation: parallel
calls may complete out of order internally, but output entries remain aligned
with input URLs, and one failed URL does not discard successful peers.

### Content Classification and Dispatch

Classify from the final URL, normalized MIME type, and a bounded prefix of the
same navigation's exact bytes. Existing Onyx PDF detection in
[`backend/onyx/utils/web_content.py`](../../reference_repos/onyx/backend/onyx/utils/web_content.py)
uses URL, MIME, and `%PDF` magic; retain all three signals.

Dispatch as follows:

| Content | Source from the one navigation | Onyx processing |
| --- | --- | --- |
| HTML/XHTML | rendered `page.content()` | existing `web_html_cleanup` / `_parse_html_to_web_content` behavior, metadata and link handling |
| PDF | exact entity bytes | existing PDF text/title/metadata extraction inside the hardened parser boundary below |
| plain text, JSON, XML, YAML, and recognized source-code MIME types | exact entity bytes | charset-aware decoding with the existing Onyx/BeautifulSoup utilities where appropriate; preserve meaningful whitespace |
| supported office/document formats already accepted by an existing Onyx byte/file parser | exact entity bytes | call that existing parser only after explicit compatibility tests |
| unsupported binary | exact bytes are not emitted to the LLM | return an explicit failed `WebContent`/typed error naming the unsupported MIME without a second fetch |
| empty or image-only document | same-navigation result | preserve current explicit empty/unparseable behavior; do not invent OCR |

Do not interpret arbitrary binary bytes as UTF-8 HTML. Do not treat a
`text/html` header as authoritative when a `%PDF-` signature proves otherwise.
Conversely, do not label an HTML error page as a PDF solely because the
requested URL ended in `.pdf`; status, MIME, magic, and parse outcome all
contribute to the error message.

Challenge, 401/403, 404, 429, 5xx, timeout, and empty-content results should
remain informative to the agent. Remove stale messages that tell operators to
configure Firecrawl or CRW.

### PDF Parser Safety Boundary

CRW currently supplied PDF-specific controls that disappear with CRW. The
built-in Onyx parser at the pinned ref uses an isolated PDFium subprocess with
a timeout but can fall back to in-process `pypdf`. Do not move untrusted large
PDF parsing into the API process or merely a child that inherits the API
server's broad network namespace. That would weaken the isolation of the
current recommended CRW path.

Add one narrowly scoped `onyx-open-url-pdf-parser` service. It uses the exact
pinned Onyx backend image and existing Onyx PDF parser, but runs with:

- `network_mode: none`;
- a read-only root filesystem;
- all capabilities dropped and `no-new-privileges`;
- no application secrets, proxy variables, or external service URLs;
- a private writable `tmpfs` only where the parser requires it;
- container PID/memory/CPU limits sized from validated stack settings; and
- no host ports or Docker networks.

Place its versioned client/server/protocol code in a repository package such
as `onyx/open_url_pdf_parser/private_onyx_pdf_parser/`. Bind-mount that package
read-only at `/opt/private-onyx/pdf-parser` in the API and parser services and
append it to their `PYTHONPATH`; start the sidecar with an explicit
`python -m private_onyx_pdf_parser.server`. This is source code shipped by the
wrapper, not a runtime-installed dependency.

The API server and parser share only a private Unix-domain-socket directory.
Use the same non-root UID/GID or a dedicated narrow group, socket mode `0660`,
and `SO_PEERCRED` verification. No background, executor, MCP, SearXNG, or other
container receives the socket mount.

Avoid copying a large private PDF into JSON, stdout, or a persistent volume.
On Linux the API process should create an anonymous `memfd`, write the already
bounded bytes, rewind it, seal it read-only where supported, and pass the file
descriptor with `SCM_RIGHTS` over the Unix socket. The parser must `fstat` the
received descriptor and independently reject sizes above the configured cap
before parsing. Treat platforms where descriptor passing or sealing does not
work as a failed capability check; do not silently fall back to a network
listener or persistent shared file.

Use a small versioned request envelope for URL/title hints, byte length, parser
options, deadline, and request ID. Return a length-prefixed, size-bounded JSON
result containing only extracted text/metadata or a typed error. Reject unknown
protocol versions, extra file descriptors, oversized control/result frames,
and mismatched lengths. Never use Python pickle across the container boundary.

Inside the parser service, wrap the complete Onyx parse, including PDFium and
`pypdf` fallbacks, in a dedicated worker subprocess with:

- a wall-clock timeout;
- a process-wide bounded number of concurrent parses;
- a Linux address-space/resource limit sized independently of the download
  limit;
- termination and reap on timeout, client disconnect, or cancellation;
- a bounded result channel; and
- explicit handling for encrypted, corrupt, decompression-bomb-like,
  image-only, and over-resource documents.

Proposed settings:

- `ONYX_OPEN_URL_MAX_CONCURRENT_PDF_PARSES`, default `2`;
- `ONYX_OPEN_URL_PDF_PARSE_TIMEOUT_SECONDS`, default `120`; and
- `ONYX_OPEN_URL_PDF_PARSE_MEMORY_MB`, default `512`.

These can remain advanced stack settings rather than routine UI options, but
they must be validated and documented. The memory limit must be high enough for
the selected PDF byte limit and parser overhead; increasing the document limit
without reviewing parser memory is an operator-visible warning. Size the
container limit for at least all permitted workers plus supervisor/result
overhead; do not set a per-worker 512 MiB limit while giving two workers a 512
MiB container ceiling.

The previous CRW decompressed-byte guard cannot be claimed as preserved merely
by limiting compressed input bytes. The networkless container plus worker
memory/CPU/time limits become the safety envelope. If implementation discovers
a reliable upstream page/decompressed-object budget, add it as defense in depth
and test it. Do not add a parser fallback in the API process or outside the
isolated service.

This one helper is intentionally included in the simplified design because it
preserves an untrusted-native-parser boundary that a normal subprocess cannot.
It does not perform downloads and therefore cannot create a second web hit.

## Configurable PDF and Body Limits

### One User-Facing PDF Limit

Add one user-facing setting:

```env
ONYX_OPEN_URL_MAX_PDF_SIZE_MB=50
```

Requirements:

- default to 50 MiB;
- accept positive integer values above 50;
- reject zero, negative, fractional, non-numeric, overflow, and empty values
  before Compose starts;
- interpret MiB as `1024 * 1024` bytes and state that explicitly;
- compute a canonical byte value once in the Makefile/startup environment;
- pass the exact byte value to the Onyx patch; and
- size Obscura's retained body/IO stream limits to at least that value.

Do not conflate this with:

- `ONYX_OPEN_URL_MAX_CHARS_PER_URL`;
- `ONYX_OPEN_URL_MAX_TOTAL_CHARS`;
- code-interpreter upload/output limits; or
- SearXNG HTML character limits.

The character budgets still limit LLM-facing extracted text after parsing. The
PDF byte limit controls network body retention and parser input.

### Propagation Into Obscura

At startup derive:

```text
OBSCURA_NETWORK_BODY_BUFFER_BYTES =
  max(ONYX_OPEN_URL_MAX_PDF_SIZE_BYTES, configured HTML/raw-body floor)

OBSCURA_IO_STREAM_MAX_BYTES >= OBSCURA_NETWORK_BODY_BUFFER_BYTES
```

Choose sufficient IO stream entries for the configured fetch concurrency and
fail validation when the settings are inconsistent. Do not use a hidden 50 MiB
ceiling in either Onyx or Obscura after the operator selects a larger value.

SearXNG does not need PDF support, but its search HTML must fit the common
retention floor. Give search HTML its own conservative internal maximum, no
larger than the preserved 20 MiB Onyx HTML default unless separately justified,
so a search result page cannot consume the entire large-PDF allowance without
reason. Enforce both main-response and rendered-DOM sizes.

### Memory Warning and Concurrency

Until Obscura performs true incremental buffering, a single response may exist
simultaneously as:

- the HTTP client's full body;
- the retained response/body alias;
- base64 or CDP protocol material;
- the Python client's accumulating bytes;
- the anonymous memfd/page-cache copy handed to the parser service;
- the parser worker's input representation; and
- parser-owned decoded objects.

The precise multiplier depends on content, protocol path, and allocator, but
peak memory can be several times the configured PDF limit. Documentation should
warn operators to plan for at least roughly 3–5x per concurrently handled
large PDF and potentially more for parser expansion, and say that this is an
estimate, not a bound.

Add or reuse a bounded `ONYX_OPEN_URL_MAX_CONCURRENT_FETCHES` setting. Large
limits should be paired with lower fetch and PDF-parse concurrency. An
oversized server response can still cause a temporary Obscura allocation before
the client rejects it because the current network read is fully buffered. This
is a residual memory-denial risk, not a reason to fall back to direct fetching.

## SearXNG-to-Obscura Design

### Use the Direct/Offline Engine Contract

SearXNG's normal online engine flow calls `request(params)` and then performs
its own HTTP request before calling `response()`. That contract cannot express
a Playwright/CDP navigation without awkward fake responses or double fetching.

Convert each custom engine to SearXNG's direct callback form using
`engine_type = "offline"` and `search(query, params)`, re-checking the exact
contract in:

- [`reference_repos/searxng/searx/search/processors/offline.py`](../../reference_repos/searxng/searx/search/processors/offline.py); and
- [`reference_repos/searxng/searx/engines/demo_offline.py`](../../reference_repos/searxng/searx/engines/demo_offline.py).

This does not mean the engine is network-free; it means the engine callback
owns its Obscura navigation instead of asking SearXNG's online processor to
issue an HTTP request.

Refactor [`searxng/engines/_crw.py`](../../searxng/engines/_crw.py) so that it
first directly imports and uses the Playwright/shared CDP client. In the same
atomic cutover, rename it to `_obscura.py` and update all custom engine imports.
The end state has no `_crw.py` module.

The current custom-engine set is
[`google2.py`](../../searxng/engines/google2.py),
[`bing2.py`](../../searxng/engines/bing2.py),
[`duckduckgo2.py`](../../searxng/engines/duckduckgo2.py),
[`brave2.py`](../../searxng/engines/brave2.py), and
[`startpage2.py`](../../searxng/engines/startpage2.py). Treat this as a
version-scoped inventory, not a forever-closed list: the upgrade audit must
enumerate all modules importing `_crw`/`_obscura` and all enabled custom engines
before each cutover or pin change.

### Per-Engine Flow

Each engine's `search()` must:

1. Construct exactly the current search URL, query encoding, locale, safe
   search, time-range, category, and pagination parameters.
2. Select the engine-specific wait mode and deadline.
3. Perform one Obscura main-document navigation.
4. Obtain the rendered DOM with `page.content()`.
5. Pass the DOM to a refactored pure parser helper.
6. Preserve current result URL normalization, title/content extraction,
   language/category metadata, no-results detection, and scoring inputs.
7. Map 403/429/CAPTCHA/timeout/empty DOM to the SearXNG exception class that
   drives the current engine suspension/retry policy.
8. Return `EngineResults` or the exact result-list form required by the pinned
   offline processor.

Remove CRW API URL/key handling, JSON envelope parsing, `enable_http: true`
settings used only for CRW, and fake HTTP response construction.

The current round-robin/retry patch and last-resort result scoring remain, but
must be revalidated against the offline processor. In particular, verify that:

- the per-attempt start time is reset as intended;
- engine suspension counters receive direct-client failures;
- a selected fallback engine has enough overall deadline for its own
  navigation;
- providers are not retried invisibly by Playwright or the shared client; and
- query text is not exposed in logs.

One browser navigation per engine attempt is the unit of accounting. Browser
redirects and subresource requests are part of that navigation and are not
"double hits." A deliberate round-robin attempt against a different search
provider is a distinct search attempt and should be visible in diagnostics.

### Derived SearXNG Image

The pinned SearXNG image does not currently ship the Playwright Python package.
Build a small derived image that:

- starts from the exact pinned SearXNG base image;
- installs `playwright==1.58.0` from a committed hashed lock file;
- does **not** install or download Chromium, because Obscura owns the browser;
- includes the shared CDP client and custom engines read-only;
- validates imports during image build; and
- performs no package installation at container startup.

Add `searxng/requirements.in`, a hashed `requirements.txt`, a derived-image
Dockerfile, a pinned local image identifier in `stack.versions.env`, and
Makefile image-ready/build/upgrade-python-deps support. Builds must use the
existing explicit build proxy arguments and host/build networking rules. They
must not depend on Myst runtime health or application egress proxies.

## Browser Fingerprint, Cookies, and Session State

Keep Obscura's `--stealth`/fingerprint configuration and explicit browser proxy
ownership. Direct clients must not pass per-request `proxyServer` settings and
must not inject CRW's historical stealth script. Those behaviors make two CDP
shim rewrites obsolete rather than moving them elsewhere.

Cookie clearing remains necessary because Onyx and SearXNG share Obscura's
default context. Move ownership into the Obscura server itself so clients do
not race to clear shared state. Add an upstream or locally pinned Obscura
setting such as:

```env
OBSCURA_BROWSER_CLEAR_COOKIES_INTERVAL_SECONDS=3600
```

Requirements:

- clear the shared default browser context at the configured interval;
- use `0` only as an explicit documented disable value, if disabling is
  retained at all;
- perform the first clear at a deterministic documented point;
- serialize clearing with target creation sufficiently to avoid corrupting an
  active navigation;
- log only success/failure and timing, never cookie data;
- expose repeated clear failures through health/readiness; and
- validate the feature at startup.

Do not create a new cookie-clear sidecar or elect one of the two clients as an
implicit leader. If the Obscura change cannot be made safely, keep the current
shim until it can; removing cookie ownership without replacement is not an
acceptable cutover.

## Top-Level Navigation Rate and Concurrency Control

CRW currently provides a per-registrable-domain (`eTLD+1`) limit for its
request surfaces: one concurrent fetch and approximately `0.33` starts/second,
with the documented crawl jitter. Direct Onyx and SearXNG clients run in
separate processes, so local semaphores alone cannot preserve that global
rule.

Do **not** try to implement navigation rate limiting by counting connections in
the shared restricted-egress policy. HTTPS CONNECT payloads are opaque there,
and Obscura's HTTP client may reuse or multiplex a tunnel. A tunnel count is
therefore neither a request count nor a reliable navigation-start clock.

Make the shared **Obscura server** the central top-level navigation scheduler
for both clients. Extend its navigation/fetch pipeline so that every top-frame
request, including a cross-site top-frame redirect, acquires the normalized
registrable-domain lease before the network request starts. IP literals use
their normalized address as the key. Use a maintained public-suffix
implementation; do not approximate `eTLD+1` with the last two labels.
Subresources are not independently charged as top-level navigations; they
remain subject to normal browser and final-hop destination policy.

The scheduler must provide:

- one concurrent top-level navigation per registrable domain;
- a configurable minimum start interval, preserving the current approximately
  `0.33` requests/second default;
- bounded, non-secret jitter only if current behavior needs it;
- cancellation-safe lock/semaphore release;
- explicit timeout rather than an unbounded queue;
- hostname normalization/IDNA/public-suffix processing before classification;
- process-wide state shared by every CDP client/target in this Obscura server;
- a lease that remains active until the configured navigation wait completes,
  fails, or times out; and
- no query logging.

Preserve the current defaults explicitly: maximum concurrency `1`, start rate
`0.33`/second (about three seconds), and crawl jitter factor `0.2` where the
current CRW path applies it. Characterization tests must decide whether search
and general crawl currently receive different jitter profiles; encode that
result rather than guessing during implementation. Add explicit Obscura
settings for concurrency, rate/interval, queue timeout, and jitter. These are
stack policy, not routine user-facing options.

Remove search-host classification from the egress proxy completely. The
Obscura scheduler's public-suffix rate key is unrelated to destination
authorization. Search engines are ordinary public targets for Obscura,
Obscura MCP, Onyx helpers, and network-enabled executors.

The shared final-hop policy must continue to:

- deny internal names and resolved non-public addresses for every caller;
- apply the configured plain-HTTP rule uniformly;
- enforce the allowed bridge-source client set;
- allow the exact local document-server destination only for a real loopback
  peer, never for a bridge peer; and
- preserve selected resolver, upstream-proxy, request-framing, TLS, logging,
  and credential-redaction behavior.

The policy proxy remains the security boundary for destination and resolver
selection. Obscura's scheduler is an operational/anti-blocking control and
must not be described as an SSRF boundary. Direct-client local semaphores may
also bound each caller's resource use, but must not be the only cross-client
per-host control.

The current generic proxy implementation lives under `crw/`. Move it to a
neutral path such as `egress/restricted_egress_proxy.py`, rename all legacy
`PREFETCH_*` environment vocabulary to `EGRESS_*`, and remove
`EGRESS_PROXY_POLICY`, `PREFETCH_BLOCK_HOSTS`, the search matcher, and every
named mode (`prefetch`, `executor`, `browser`, `onyx-helper`, and
`searxng-external`). Update imports, Compose, locks, tests, and documentation
atomically. Do not keep compatibility aliases.

Run exactly one `restricted-egress-proxy` process in the trusted namespace on
one port. Its base allowed-client list contains `obscura-egress-bridge` and
`obscura-mcp-egress-bridge`; the executor overlay appends
`executor-egress-bridge`. Loopback is accepted for trusted Onyx helpers and
health probes. Resolve bridge identities through Docker service discovery for
source-IP comparison, reject ambiguous identity matches, and re-resolve after
bridge replacement without ever treating those names as browsing targets.

Rename the helper's internal exception to an explicitly source-scoped setting,
for example `EGRESS_PROXY_LOOPBACK_TRUSTED_DESTINATIONS`. The implementation
must pass the actual socket peer classification into HTTP, CONNECT,
destination-validation, and cleartext-policy decisions. A bridge cannot obtain
the local-document exception with headers, target syntax, or proxy chaining.

Because an executor can deliberately open many tunnels, add bounded per-source
active-connection quotas and a global ceiling sized below the process/OS file
descriptor limit. Reserve independent capacity for loopback, renderer, MCP,
and executor identities, reject excess work promptly with a typed 503, and
release counters on every close/cancellation path. These limits protect shared
availability; they are stack-owned defaults, not a destination allowlist or a
user-facing search policy.

Keep each component's existing narrow bridge and component-side egress
network. Point every bridge at the one shared listener through one common
`restricted-policy-upstream` internal network. Bridge identity comes from its
unique peer IP/service name on that network. Delete the separate browser,
MCP-browser, and optional executor policy-upstream networks. This does not make
restricted components peers: only their fixed dual-homed bridge containers
share the policy side. Never attach a restricted component directly to that
network or to `netns-holder`. The executor override attaches its bridge to the
base shared upstream network and must not add another `netns-holder` network or
policy service.

## Readiness, Health, VPN Liveness, and Autoheal

### Required Dependency Graph

The target startup chain is:

```text
myst-client-vpn (VPN mode) or explicit no-VPN-ready namespace
  -> shared restricted-egress policy
  -> Obscura / MCP / optional executor egress bridges
  -> Obscura CDP server
  -> obscura-cdp-gateway
  -> Onyx API readiness

Obscura CDP server + SearXNG Valkey
  -> SearXNG readiness
  -> SearXNG service gateway
  -> Onyx API readiness

networkless Onyx PDF parser
  -> private Unix socket protocol health
  -> Onyx API readiness
```

Implement health checks as follows:

- Mysterium health remains data-plane-aware in VPN mode and immediately
  well-defined in explicit no-VPN mode. Re-test both forms.
- Shared restricted-egress policy health verifies its listener and selected
  final-hop routing prerequisites without resolving or fetching an arbitrary
  public user target.
- Each egress bridge verifies the same upstream policy listener from its own
  component boundary and receives the expected denial for a blocked local
  target.
- Obscura health verifies local `/json/version` and the expected cookie-clear
  capability/configuration, but does not make a public probe on every health
  interval.
- `obscura-cdp-gateway` health fetches `/json/version` through the gateway and
  validates the expected browser/product protocol shape.
- SearXNG health verifies its local HTTP API, Playwright/shared-client import,
  and local CDP connectivity without executing a public search.
- PDF parser health uses a bounded Unix-socket protocol ping and verifies the
  expected parser/protocol version without parsing an external document.
- Onyx API depends on the CDP gateway, SearXNG service gateway, shared
  restricted-egress listener, Obscura MCP gateway, networkless PDF parser, and
  its other existing required services. Remove all CRW and dedicated
  helper-policy health dependencies.

Compose `depends_on` establishes startup order, not permanent runtime
supervision. A failed downstream request after startup must remain a visible
typed failure. Do not add silent direct fallbacks.

### Autoheal and VPN Reconnection

The current autoheal behavior is primarily for Mysterium/VPN reconnection.
Preserve its labels, restart authority, grace periods, and no-VPN semantics.
Do not label every stateless bridge/gateway for autoheal and create a restart
storm during a VPN flap. The networkless parser is independent of VPN health
and must not restart merely because Myst reconnects.

Validate these sequences:

1. VPN enabled, initial connection succeeds.
2. VPN disabled, Myst health reaches the documented ready state without a
   nonexistent tunnel.
3. VPN enabled, tunnel drops after the stack is healthy, Myst becomes
   unhealthy, autoheal/reconnect occurs, and the shared policy does not permit
   direct egress during the gap.
4. Upstream proxy configured, VPN enabled or disabled as supported, and target
   DNS remains at the upstream proxy.
5. Obscura restarts while clients remain up; clients reconnect on the next
   request and do not reuse a stale body/target.
6. Shared policy is unavailable; Obscura and clients fail closed and report
   the dependency rather than switching routes.

Health scripts and shim containers must use preinstalled tools. No `apk add`,
`apt-get`, `pip install`, browser download, or other package/network operation
may occur in health checks, entrypoints, or runtime patch initialization.

## Security Model After Migration

### Trust Boundaries

- Onyx and SearXNG may issue powerful CDP commands only to the dedicated
  non-MCP Obscura instance.
- CDP is unauthenticated and therefore remains confined to internal networks
  and one narrow gateway; it is never host-published.
- Obscura cannot reach the broad trusted namespace. Its only internet route is
  its egress bridge to the shared restricted-egress listener.
- The bridge cannot select arbitrary trusted-namespace ports.
- The policy accepts only loopback or an expected bridge source, applies the
  loopback-only internal exception after peer classification, enforces
  per-source quotas, and applies one public-destination policy.
- The separate MCP Obscura instance and component-side bridge remain
  independent even though the final policy process is shared.
- Executors cannot reach CDP, SearXNG control, Obscura control, Docker socket,
  data stores, or host services. Their bridge reaches only the shared policy
  port.
- Untrusted PDF native parsing occurs in a networkless, read-only,
  resource-bounded service that can receive only the private Unix socket and
  an anonymous bounded body descriptor.

### Internal Hostname and Address Denial

Retain one canonical blocklist and test it in every relevant policy. It must
cover the explicit repository service aliases plus Docker/platform defaults,
including at least:

- `localhost`, `localhost.localdomain`, and local suffix forms;
- single-label Docker Compose service/container/network aliases;
- `host.docker.internal`, `gateway.docker.internal`, and
  `kubernetes.docker.internal`;
- `docker.for.mac.host.internal`, `docker.for.mac.localhost`, and documented
  legacy Docker Desktop aliases where resolvable;
- `host.containers.internal` and `gateway.containers.internal` for Podman;
- Docker embedded DNS address `127.0.0.11` and loopback aliases;
- cloud metadata names and link-local metadata addresses;
- `.local`, `.localhost`, `.internal`, `.home.arpa`, and other stack-blocked
  local-use suffixes; and
- every non-public IPv4 and IPv6 range after resolution, including IPv4-mapped
  IPv6 and alternate textual forms.

Allow the host-only upstream proxy exception only inside the trusted final-hop
proxy implementation and only for the configured proxy endpoint. Never add it
to the general browser destination allowlist.

The definitive list and resolver semantics belong in
[Internal network security](../internal_network_security.md), backed by unit
tests rather than duplicated divergent lists.

### Onyx SSRF Interaction

Onyx's upstream URL validation remains useful for URL syntax and UI paths, but
must not perform a target DNS lookup before the browser fetch. The direct
adapter either bypasses the DNS-resolving helper or patches its use at this
narrow boundary while retaining syntax/literal checks.

Saved Onyx SSRF settings are not authorization for Obscura to access private
targets. The shared final-hop policy remains authoritative for all hostnames
and redirects. `ONYX_AGENT_ALLOW_HTTP_URLS` must be enforced both before
navigation and at the final hop.

### Residual Risks

Document these honestly after implementation:

- Obscura CDP is powerful and relies on network isolation rather than protocol
  authentication.
- CONNECT policy sees the requested hostname/port but cannot inspect encrypted
  HTTP paths or response content.
- Obscura currently fully buffers a response before client-side size rejection;
  a hostile oversized response can cause temporary high memory use.
- Large PDF parsing has parser-specific CPU/memory risk despite networkless
  container and worker bounds.
- Browser subresources intentionally create additional requests; single-fetch
  means one main-document navigation, not one TCP/HTTP request total.
- Some sites may fingerprint Obscura or challenge shared browser state.
- Direct executor/helper search-engine requests do not use Obscura's
  fingerprint or per-domain scheduler and may receive or contribute to 403/429
  responses from the shared exit IP. This is accepted behavior, not a reason
  to restore a hidden search deny list or browser fallback.
- One policy process is a shared failure and resource-contention domain for
  browser, MCP, helper, and executor egress. Per-source quotas prevent a caller
  from consuming unbounded connections, but a process crash temporarily
  affects every path until the normal restart policy recovers it.
- Explicit no-VPN mode intentionally permits system-routed internet egress
  through the same destination policy.

## Complete Obsolescence Inventory

### Runtime Containers and Services to Remove

Remove these current services from Compose and all dependency graphs:

- `crw`;
- `crw-validation-dns`;
- `crw-service-gateway`;
- `crw-prefetch-bridge`;
- `prefetch-blocking-proxy`;
- `cdp-shim`;
- `mcp-browser-egress-proxy`;
- `onyx-helper-egress-proxy`; and
- optional `executor-egress-proxy` whenever executor networking is enabled.

Add `obscura-cdp-gateway` if the existing generic gateway cannot be
instantiated safely, plus the networkless `onyx-open-url-pdf-parser` security
boundary. Rename `browser-egress-proxy` to the one
`restricted-egress-proxy`. Relative to the current base stack, removing eight
services and adding two produces a net reduction of at least six always-on
containers; enabling executor networking no longer adds a policy container.
Do not add another fetch, validation, rate-limit, or body-conversion sidecar.

Do **not** remove:

- `obscura`;
- `obscura-egress-bridge`;
- `obscura-mcp-egress-bridge`;
- the renamed shared `restricted-egress-proxy`;
- `searxng-core`, Valkey, or `searxng-service-gateway`;
- `onyx-open-url-pdf-parser` after it is added;
- the separate Obscura MCP server/control/gateway;
- the optional executor egress bridge and component network; or
- full-mode local document services.

### Files and Patches to Delete or Move

Delete after their replacement tests pass:

- `crw/cdp_shim.py`;
- `crw/cdp-shim.Dockerfile`;
- `crw/cdp-shim-requirements.in` and its hashed lock;
- `crw/validation_dns.py`;
- `tests/test_crw_validation_dns.py`;
- CRW-specific Compose fragments, scripts, and generated examples; and
- any tests whose only purpose is validating removed CRW API envelopes.

Move and rename, rather than delete, the generic restricted-egress proxy and
its tests:

- `crw/prefetch_blocking_proxy.py` -> a neutral `egress/` module;
- its requirements/build context if still required by multiple proxy services;
- `tests/test_prefetch_blocking_proxy.py` or current equivalent ->
  `tests/test_restricted_egress_proxy.py`; and
- all `PREFETCH_*` env/config vocabulary -> `EGRESS_*`.

Delete the following shim behaviors because the new clients make them
unnecessary:

- CDP `waitUntil` injection: callers send explicit raw `Page.navigate`;
- CRW stealth-script stripping: CRW no longer injects it;
- per-context `proxyServer` stripping: callers use the default Obscura context;
- shim-owned periodic cookie clearing: Obscura owns it;
- shim-specific target selection/wait-for-search-host coordination: the shared
  client and Obscura scheduler own it; and
- CDP-shim trace configuration: neutral client diagnostics replace it.

Retain and update these Onyx/runtime patches because this migration does not
obsolete them:

- lite-mode `open_url` availability;
- `ONYX_OPEN_URL_MAX_CHARS_PER_URL` and total-character limit propagation;
- Onyx helper proxy routing for helper downloads and remaining local Chromium
  users, retargeted to the shared loopback listener;
- full-mode local-document PDF freshness/reindexing;
- SearXNG round-robin and last-resort scoring, adapted to offline engines;
- code-interpreter capability/network/upload patches; and
- Deep Research/tool-batch patches.

For completeness, the current patch inventory has this migration impact:

| Current patch or workaround | Migration disposition |
| --- | --- |
| CRW Firecrawl-compatible content/search adapter | Delete. Built-in `OnyxWebCrawler` and direct SearXNG engines replace it. |
| CRW validation DNS and Onyx validation workaround | Delete. The shared client performs non-resolving syntax/literal checks and the final hop resolves/validates names. |
| CDP shim wait injection, stealth/proxy stripping, cookie clearing, target selection, and tracing | Delete only after direct explicit waits, default-context use, Obscura cookie ownership, and neutral tracing pass their gates. |
| CRW prefetch-blocking policy instance | Delete. There is no raw search prefetch after every search engine navigates directly through Obscura. Move the generic final-hop implementation and run one shared instance. |
| Named egress modes and search-host deny list | Delete. Public search hosts use the same policy as every other public destination, including for executors. |
| Dedicated browser/MCP/helper/executor policy processes | Collapse into one source-aware listener. Retain separate component bridges and add per-source quotas plus a loopback-only internal exception. |
| Onyx helper HTTP/Playwright proxy patch | Retain. Remove `OnyxWebCrawler` reliance on its local-Chromium/requests path, but keep it for Web connector, Highspot, helper downloads, and other upstream callers; point it at the shared loopback listener. |
| Lite `open_url` availability | Retain and make it activate the direct crawler patch. |
| Open URL/web-search character budgets | Retain. Add the separate PDF byte cap; do not merge byte and character semantics. |
| SearXNG CRW-backed engine adapter | Replace with direct offline Playwright/CDP engines. |
| SearXNG round-robin and last-resort scoring | Retain and revalidate against offline processor timing/exceptions. |
| LLM context-window override | Retain unchanged. |
| Assistant reasoning preservation and chat-reminder placement | Retain unchanged. |
| Deep Research selected-chat-Agent tools and complete tool-batch execution | Retain unchanged. |
| Onyx LiteLLM interaction and GLM automatic tool-choice compatibility | Retain unchanged. |
| Coding-agent final-answer synthesis and saved tool-result preservation | Retain unchanged. |
| Coding-agent repository/code-interpreter upload alignment | Retain unchanged. |
| Internal-search content caps | Retain unchanged. |
| Code-interpreter capability descriptions and executor network/proxy patch | Retain the restricted executor network and bridge, but point it at the shared policy and remove claims that search hosts are blocked. Exercise as a routing regression. |
| Background Web-connector PDF freshness | Retain unchanged; it is not the LLM `open_url` PDF path. |
| Local embedding shim | Retain unchanged. |
| Compose wrapper, Podman, external proxy, and install/upgrade hooks | Modify only where service/image/network names change; preserve their existing semantics and strict validation. |

Update this table during implementation if the current inventory in
[Onyx patch information](../onyx_patch_info.md) has changed. An unclassified
runtime patch is a release blocker.

### Version, Secret, and Makefile Cleanup

Remove:

- `CRW_IMAGE` and `CDP_SHIM_IMAGE` pins;
- generated `CRW_ONYX_API_KEY` and every export/Compose requirement for it;
- CRW API, render, PDF, prefetch, validation DNS, and timeout variables;
- `OBSCURA_CDP_URL` values used only by the CDP shim;
- `CDP_SHIM_*`, `STRIP_PROXY_SERVER`, and shim trace variables;
- `EGRESS_PROXY_POLICY`, `PREFETCH_BLOCK_HOSTS`, and every search-blocking mode
  or message;
- dedicated MCP/helper/executor policy ports, service names, and health
  dependencies;
- `browser-policy-upstream`, `mcp-browser-policy-upstream`, and optional
  `executor-policy-upstream` in favor of one `restricted-policy-upstream`;
- `crw-image-ready`, `cdp-shim-image-ready`, and `cdp-shim-build` targets;
- CRW/CDP-shim prerequisites from `up-lite`, `up-full`, `upgrade`, and Python
  dependency upgrade flows; and
- `crw-service-gateway` from the stack-owned helper `NO_PROXY` list.

Add or rename:

- `ONYX_OBSCURA_CDP_URL=ws://obscura-cdp-gateway:9222/devtools/browser`;
- `SEARXNG_OBSCURA_CDP_URL=ws://obscura:9222/devtools/browser`;
- the PDF byte/parser settings above;
- neutral CDP wait/timeout/trace settings;
- stack-owned Obscura navigation scheduler settings for per-domain
  concurrency `1`, rate `0.33`, queue timeout, and the characterized jitter
  profile;
- neutral restricted-egress policy variables;
- one shared listener port, a base/optional-overlay bridge allowlist,
  peer-scoped connection quotas/global ceiling, and
  `EGRESS_PROXY_LOOPBACK_TRUSTED_DESTINATIONS`;
- Onyx helper and every component bridge URLs retargeted to that listener;
- Obscura cookie-clear ownership setting;
- the derived SearXNG image pin and build target; and
- any Obscura source ref required by the capability gate.

Service URLs are stack properties, not routine user-facing knobs. Keep them in
Compose/Makefile defaults rather than `.env.wrapper.example` unless an operator
has a legitimate supported need to override them.

## Implementation Workstreams

### Workstream 0: Lock Behavior With Tests

Before deleting code, add fixtures and characterization tests for:

- current custom search URL construction and DOM parsing;
- SearXNG retry/suspension/scoring behavior;
- Onyx HTML, PDF, text, status, and ordering outputs;
- final-hop internal-name/address denial and DNS routing;
- cookie clearing, wait selection, and search rate control; and
- current full-mode local PDF ingestion.

Record which CRW behaviors are intentionally not preserved, such as the
Firecrawl-compatible API itself.

### Workstream 1: Obscura Body and Cookie Prerequisites

1. Add the byte-exact/body-state integration tests.
2. Implement or consume the required Obscura body-retention fix.
3. Implement Obscura-owned periodic cookie clearing.
4. Implement the process-wide per-registrable-domain top-level navigation
   scheduler, including redirect handling.
5. Pin the exact image/ref and document source/build provenance.
6. Validate stealth, proxy, private-target, scheduling, and health behavior.

Do not proceed to production cutover if the mislabeled-PDF or unavailable-body
tests remain ambiguous.

### Workstream 2: Neutral Egress Proxy

1. Move the generic policy proxy out of `crw/`.
2. Rename configuration without compatibility aliases.
3. Delete search-host classification and all named modes.
4. Make the loopback internal exception depend on the actual socket peer.
5. Add unambiguous bridge identity mapping and bounded per-source/global
   connection accounting.
6. Preserve resolver selection, internal blocklists, request framing, upstream
   proxy handling, source allowlists, HTTP policy, and redaction.
7. Replace the dedicated browser/MCP/helper/executor services with one shared
   listener, one shared policy-side upstream network, and retarget each
   retained bridge/helper client.
8. Prove no removed service, port, policy env, or search-block message remains.

### Workstream 3: Shared CDP Client

1. Implement URL syntax/literal validation without target DNS.
2. Implement explicit navigation/event/body lifecycle.
3. Implement HTML and raw-body modes.
4. Implement actual-byte limits and cleanup.
5. Implement typed failures and safe diagnostics.
6. Test against a fake CDP protocol and the pinned live Obscura image.

### Workstream 4: Onyx Patch

1. Add strict source-shape checks.
2. Replace `OnyxWebCrawler` transport with the shared client.
3. Add content dispatch and byte-exact PDF handling.
4. Add the networkless Onyx parser service, Unix FD-passing protocol, worker
   isolation, and resource limits.
5. Propagate byte and character settings to every imported binding.
6. Activate in lite and full bootstraps.
7. Remove the local-Chromium fallback from this provider only.

### Workstream 5: SearXNG Direct Engines

1. Build the pinned Playwright-client-only SearXNG image.
2. Convert custom engines to the offline/direct contract.
3. Refactor pure DOM parsers and rename `_crw.py` to `_obscura.py`.
4. Preserve error/suspension/round-robin/scoring behavior.
5. Remove CRW API settings and outgoing HTTP assumptions.
6. Add CDP-aware readiness.

### Workstream 6: Atomic Compose Cutover

1. Add the Onyx CDP gateway/ingress network.
2. Attach SearXNG to Obscura control.
3. Add the networkless PDF parser and private Unix-socket volume to lite and
   full API services.
4. Add the shared policy listener, retarget the retained component bridges and
   Onyx helper URL, and remove dedicated policy services/ports.
5. Apply new health dependencies and optional-executor allowlist overlay.
6. Remove all CRW/shim services, networks, secrets, pins, and build targets.
7. Render and inspect effective lite/full Compose models.
8. Start each routing-mode matrix from a clean supported state.

### Workstream 7: Documentation and Cleanup

Complete every documentation edit below, delete obsolete files/text, run the
full test matrix, and move this plan to `docs/plans/implemented/` only after the
acceptance criteria are met.

## Documentation Update Plan

Documentation changes are part of implementation, not a follow-up.

### `README.md`

- Replace the CRW/Firecrawl architecture and setup instructions with built-in
  Onyx Web Crawler selection and direct Obscura behavior.
- Remove the Firecrawl API base URL/key procedure.
- Update lite/full component lists, diagrams, first-run steps, health guidance,
  image builds, and environment tables.
- Explain the 50 MiB default, values above 50 MiB, memory/concurrency tradeoff,
  and single-main-navigation guarantee.
- List the networkless Onyx PDF parser as the one new security sidecar and
  explain why it is not a downloader.
- Keep Obscura MCP configuration separate from Onyx content fetching.
- Explain that executors and helpers may contact public search engines directly,
  while Obscura-rendered search retains browser fingerprinting and scheduling.
- Preserve local-RAG, executor, upstream proxy, VPN, and Tailscale guidance.

### `docs/request_handling.md`

Rewrite the current CRW-centric diagrams and prose. Document:

- Onyx Web Crawler -> direct Obscura HTML/PDF/raw flow;
- SearXNG offline/direct custom engines;
- exact navigation/body lifecycle;
- MIME/URL/magic dispatch;
- Unix FD handoff to the networkless Onyx PDF parser and its error/resource
  semantics;
- waits, default context, stealth, cookie clearing, rate controls, and trace
  redaction;
- uniform public-destination policy with no search-host special case, including
  the distinction between Obscura-scheduled navigation and unscheduled direct
  executor/helper HTTP;
- byte versus character limits;
- expected failures and no-direct-fallback behavior;
- one main navigation versus redirects/subresources;
- separate MCP behavior; and
- unchanged full-mode local document fetch/parsing.

Delete CRW API, prefetch, validation DNS, Firecrawl configuration, and CDP-shim
sections rather than retaining a historical appendix.

### `docs/vpn_routing_and_proxies.md`

- Replace CRW/prefetch topology and service tables with the CDP gateway and
  direct SearXNG client.
- Update all network attachments, proxy policies, health URLs, readiness edges,
  and `NO_PROXY` ownership.
- Replace the policy-mode matrix with one source-aware policy, one loopback-only
  internal exception, per-source quotas, retained narrow bridges, and one
  shared policy-side upstream network.
- Delete search-engine destination blocking and document the accepted direct
  executor/helper behavior and shared-policy failure domain.
- Document the parser's `network_mode: none` and private Unix IPC readiness
  separately from VPN/egress dependencies.
- Preserve the full VPN/upstream/no-VPN routing and DNS matrix.
- Explain that only the internal Obscura service name uses Docker DNS; target
  DNS occurs at the selected final hop.
- Update autoheal/reconnection validation and failure examples.

### `docs/internal_network_security.md`

- Remove CRW and validation-DNS reachability sections.
- Document CDP client reachability, CDP gateway risk, Obscura isolation,
  SearXNG's control-network attachment, and the separate MCP boundary.
- Explain why policy processes are shared but component-side bridges/networks
  remain separate, including executor resource-quota behavior.
- Document parser socket peer checks, FD/size validation, container hardening,
  and its lack of network attachment.
- Update canonical Docker/Podman/internal hostname coverage.
- Document redirect resolution and the no-target-Docker-DNS property.
- Explain the narrow Onyx SSRF patch interaction and final-hop authority.
- Add the body-buffering and large-PDF residual memory risks.

### `docs/onyx_patch_info.md`

- Add the strict direct-Obscura `OnyxWebCrawler` patch, content dispatch, PDF
  parser boundary, and limit propagation.
- Describe the parser protocol/worker implementation and strict version check.
- Clarify that helper proxy routing remains for other helper downloads and
  local Chromium users, but uses the shared loopback policy listener rather
  than a dedicated helper service.
- Update the SearXNG patch section for offline direct engines and derived image.
- Update code-interpreter capability text: network-enabled executors may reach
  public search engines through the shared policy, while internal/private
  destinations and direct sockets remain unavailable.
- Delete CRW/CDP-shim patch and configuration descriptions.
- Retain and accurately scope every unaffected patch.

### `docs/onyx_patches_upgrade.md`

- Update the current version table and service/image map.
- Add source-shape audit anchors for `OnyxWebCrawler`, parser helpers,
  Playwright `1.58.0`, SearXNG offline processors, Obscura body/IO/navigation
  methods, and cookie clearing.
- Add the Unix parser protocol version, Onyx parser import/signature, container
  hardening, and FD-passing capability checks.
- Add byte-exact PDF/body and one-fetch upgrade tests.
- Replace CRW/CDP-shim/validation-DNS upgrade steps with deletion assertions so
  they cannot accidentally return.
- Cover the new SearXNG lock/image build and shared neutral egress proxy,
  including peer classification, loopback-only exceptions, quotas, and absence
  of named/search-block policy modes.
- Revalidate executor environment injection and LLM-facing capability text
  against the search-allowed shared policy.

### `docs/local_docs_rag_search.md`

- State explicitly that local document Web-connector ingestion and PDF
  freshness are unchanged, while helper/local-Chromium external routing keeps
  the same semantics through the shared listener.
- Prevent readers from assuming local doc-drop URLs are sent through Obscura.
- Retest and update only references made stale by component-name changes.

### `.env.wrapper.example`

- Add the PDF size and advanced parse/concurrency settings with memory warnings.
- Remove CRW, CDP-shim, search-block-host, and policy-mode user settings.
- Do not expose stack-owned CDP URLs or helper `NO_PROXY` values.
- Keep immutable image/source pins in `stack.versions.env`.

### `AGENTS.md`

- Replace CRW runtime shape, key locations, and invariants with direct
  Onyx/SearXNG -> Obscura paths.
- Add the new shared client, neutral egress proxy, and derived SearXNG image to
  key locations, along with the networkless parser service/protocol module.
- Replace instructions that require blocking executor search traffic with the
  uniform public-destination policy and retained bridge isolation invariant.
- Remove obsolete CRW/CDP-shim build commands.
- Add concise commands for running all unit tests and a single test module, and
  explain that new regression tests belong under `tests/` as
  `test_<subsystem>.py` using standard `unittest` discovery.
- Keep the privacy, fail-closed, build-time dependency, and full-stack test
  rules.

### Plans and Version Manifest

- Update `stack.versions.env` comments and pins.
- Mark superseded CRW-specific portions of the implemented restricted-egress
  plan as historical without rewriting its decision record.
- After deployment and validation, change this file's status to implemented,
  update its normative-doc links, and move it to
  `docs/plans/implemented/obscura_direct.md`.

## Test Plan

### Unit Test Layout

Add focused `unittest` modules under [`tests`](../../tests):

- `test_obscura_cdp_client.py`;
- `test_onyx_obscura_crawler_patch.py`;
- `test_onyx_open_url_pdf_parser.py`;
- `test_searxng_obscura_engines.py`;
- `test_obscura_direct_compose.py` or an equivalent structured Compose test;
- renamed/updated `test_restricted_egress_proxy.py`; and
- Obscura source/integration tests in its build context when a local source fix
  is required.

Run the repository suite with:

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Run one module while iterating with:

```sh
python3 -m unittest tests.test_obscura_cdp_client -v
```

### Shared Client Unit Cases

Use a deterministic fake CDP peer to test:

- event registration occurs before navigation;
- exact `Page.navigate` wait value and timeout;
- main-frame/document response selection amid subresources;
- redirect chain final URL/status/loader selection;
- base64 and plain `IO.read` chunks;
- multiple chunks and EOF;
- `IO.close` on success and every failure path;
- CDP disconnect/cancellation cleanup;
- actual byte count equality, one byte below, exactly at, and one byte above
  cap;
- absent, malformed, duplicated, too-small, too-large, and conflicting
  `Content-Length`;
- chunked and HTTP/2-style no-transfer-encoding cases;
- gzip/br compression where decoded body length differs from wire length;
- zero-length versus unavailable/discarded body;
- query/header/body redaction;
- URL/IDNA/IP-literal/Docker-internal-name rejection without DNS calls; and
- proof that no error path starts a second navigation.

### Onyx Patch Unit Cases

Test:

- strict success and expected-source-shape failures;
- full- and lite-bootstrap activation;
- default 50 MiB, values above 50 MiB, and invalid setting rejection;
- propagation to every imported Onyx constant/provider binding;
- HTML cleanup and metadata;
- PDF by final URL, MIME, and `%PDF-` magic independently;
- misleading PDF MIME and HTML error page at a `.pdf` URL;
- plain text/JSON/XML/source decoding and whitespace;
- unsupported binary, corrupt/encrypted/image-only PDF, timeout, and resource
  limit behavior;
- bounded PDF parse concurrency and subprocess reap;
- parser protocol versioning, `SO_PEERCRED`, single sealed memfd acceptance,
  independent `fstat` size enforcement, frame/output limits, and rejection of
  missing/extra descriptors;
- parser service timeout/disconnect cleanup and absence of an API-process
  parser fallback;
- per-URL failure isolation and stable output order;
- no requests/local-Chromium/Firecrawl/CRW/MCP/CLI fallback; and
- character budgets applied after document extraction.

### SearXNG Engine Unit Cases

For every custom engine, test:

- URL/query/locale/safe-search/time/page construction;
- direct/offline processor registration;
- engine-specific wait selection;
- current DOM selectors and result normalization using committed sanitized
  fixtures;
- no-results page;
- 403, 429, CAPTCHA, timeout, empty DOM, and CDP loss mapping;
- engine suspension/retry interaction;
- round-robin and last-resort scoring behavior;
- no normal SearXNG HTTP-client call;
- one main navigation per engine attempt; and
- no query value in logs.

Fixtures should be minimal and purpose-built, not copied full third-party pages.

### Restricted-Egress Proxy Unit Cases

Retain and extend tests for:

- canonical internal/Docker/Podman/cloud-metadata names;
- all non-public IPv4/IPv6 classes and mapped forms;
- IDNA and trailing-dot normalization;
- resolver selection in VPN, upstream-proxy, and explicit no-VPN modes;
- remote DNS for `socks5h` upstreams;
- source-client enforcement;
- HTTP port policy;
- successful public search-host CONNECT/forward behavior from simulated
  renderer, MCP, loopback-helper, and executor peers;
- absence of `EGRESS_PROXY_POLICY`, search-host lists, and search-specific
  denial messages;
- loopback-only trusted destination success and identical bridge-peer denial,
  including attempts to spoof identity with headers/targets;
- bridge hostname re-resolution after replacement and fail-closed rejection
  of missing or ambiguous peer identity;
- independent per-source connection quotas, global ceiling, typed 503,
  cancellation/close counter release, and reserved loopback/browser capacity
  under executor saturation;
- `Content-Length`/`Transfer-Encoding` request-smuggling defenses already
  present in the proxy; and
- no legacy prefetch mode/env names.

### Local Single-Fetch Integration Fixture

Create a test-only instrumented HTTP origin with counters and endpoints for:

- static HTML;
- JavaScript-populated HTML;
- redirect to HTML;
- valid PDF with correct MIME;
- PDF as `application/octet-stream`;
- inline and attachment-disposition PDF responses;
- PDF with a misleading text MIME;
- HTML error at a `.pdf` URL;
- plain text, JSON, and unsupported binary;
- 401/403/404/429/500;
- delayed/hung response;
- empty response;
- chunked response;
- compressed response;
- false/absent length where the actual body crosses the cap; and
- a response larger than 50 MiB for opt-in memory testing.

Run the fixture only in an isolated test topology that explicitly allows its
private address; never weaken production internal-destination policy. Count
main-document hits separately from redirects and browser subresources. Record
HTTP method and `Range` headers. Assert one main-document navigation, no
preflight `HEAD` or follow-up range fetch, and zero helper/CLI/direct-fetch
hits.

### Obscura Integration Cases

Against the exact image pin:

- run every required capability-gate case;
- verify byte hashes, not merely extracted text;
- verify rendered DOM after JS execution;
- verify explicit wait modes;
- verify cookie clear scheduling and active-navigation coordination;
- issue concurrent Onyx-like and SearXNG-like CDP navigations and verify one
  active top-level navigation per registrable domain;
- verify the minimum start interval, cross-site redirect scheduling, queue
  timeout, cancellation, and lease release without charging subresources as
  independent navigations;
- verify browser proxy enforcement and no direct route;
- verify restart/reconnect cleanup;
- measure RSS at 50 MiB and at one documented above-50 value with concurrency
  1 and the supported maximum; and
- record expected peak-memory guidance without turning a platform-specific RSS
  number into a brittle unit assertion.

### Compose and Static Security Tests

Parse effective Compose models structurally and assert:

- no CRW/CDP-shim service, image, secret, env, health dependency, network, or
  host port remains;
- only the expected clients can reach Obscura control/CDP;
- Obscura has no direct trusted-namespace or general external network;
- the CDP gateway exposes only the intended port and no host binding;
- SearXNG has Obscura-control and Valkey/service networks but no direct egress;
- Onyx has the gateway and helper paths required by each mode;
- exactly one `restricted-egress-proxy` service exists, with no dedicated
  browser/MCP/helper/executor policy services or legacy policy ports;
- all retained component bridges point only to the shared listener while
  renderer, MCP, and executor component-side networks remain distinct;
- bridge policy-side interfaces share only one
  `restricted-policy-upstream` network, with no restricted application
  attached to it and no legacy per-policy upstream networks;
- Onyx helper proxy variables point to the shared loopback listener and the
  trusted internal exception is configured as loopback-only;
- the optional executor overlay adds its bridge identity/quota and no policy
  container;
- no policy mode or search-host blocklist environment remains;
- the PDF parser has `network_mode: none`, no ports/networks/secrets/proxies,
  the expected hardening/resource limits, and a socket volume shared only with
  the API service;
- MCP and executor networks remain separate;
- internal networks remain `internal: true` where required;
- stack-owned `NO_PROXY` contains exact current service aliases and is not
  user-facing; and
- health/dependency conditions are present in both lite and full overlays.

### Runtime Matrix

Test at least:

| Stack | VPN | Upstream proxy | Required result |
| --- | --- | --- | --- |
| lite | enabled | none | Onyx/search browser traffic uses Myst route and Myst/provider DNS |
| lite | disabled | none | explicit no-VPN route works; no stalled Myst health |
| lite | enabled or documented supported form | `socks5h` | upstream proxy handles target DNS; no direct fallback |
| full | enabled | none | same browser route plus local RAG regression passes |
| full | disabled | none | explicit no-VPN route plus local RAG passes |
| full | supported | upstream proxy | browser/helper routes obey the shared policy; local services stay local |

For each practical matrix entry:

- run `make up-lite` or `make up-full`;
- inspect `make ps-lite` or `make ps-full`;
- exercise a real custom `web_search` query;
- exercise `open_url` on HTML, JS-rendered HTML, PDF, text, and an oversized
  response;
- exercise `open_url` on a public search-engine URL and verify any failure is
  the real rendered upstream status/challenge, not a local egress-policy 403;
- when executor networking is enabled, contact a public search engine from an
  executor and verify it reaches the selected route rather than receiving a
  local search-policy 403;
- verify simultaneous executor saturation cannot consume the reserved
  renderer/MCP/loopback proxy capacity;
- inspect SearXNG, Obscura, shared-policy, gateway, and Onyx logs;
- verify target DNS did not reach Docker's embedded resolver;
- verify public egress identity matches the chosen route;
- attempt representative internal/Docker/metadata destinations and redirects;
- interrupt/recover VPN and Obscura; and
- verify no package installation occurs after containers start.

### Regression Tests Outside the New Path

- Full-mode doc-drop crawling, PDF freshness/reindexing, embedding shim, and
  `internal_search`.
- Obscura MCP `open_url`/`browser_navigate` behavior and its separate SSRF
  policy.
- Code-interpreter network disabled/enabled, shared-policy routing, public
  search access, and matching proxy capability text.
- Remaining Onyx helper downloads and local Chromium consumers in lite and
  full modes.
- Teep inference, WebUI, auth, MinIO, and optional Tailscale exposure.
- Supported custom search engines individually, not only aggregate SearXNG.

### Static/Repository Validation

Run at minimum:

```sh
make help
python3 -m unittest discover -s tests -p 'test_*.py' -v
git diff --check
```

Use the Makefile's exact layering to render effective lite/full Compose models.
Search the repository for stale `crw`, `CRW_`, `cdp-shim`, `PREFETCH_`, and
Firecrawl setup references, manually classifying historical plan references
and upstream names that legitimately remain.

## Rollout and Migration

1. Land characterization tests and Obscura prerequisites first without
   changing the active provider.
2. Build/pin the derived SearXNG and any required Obscura image.
3. Implement shared client, Onyx patch, direct SearXNG engines, and neutral
   policy changes behind the atomic Compose branch.
4. Run unit, integration, static, memory, and routing matrix tests.
5. Tell existing operators to select **Onyx Web Crawler** instead of the saved
   Firecrawl provider before/at upgrade.
6. Stop the old stack and start the complete new topology. Do not run old CRW
   and new direct engines together.
7. Verify health, one-fetch counters, search quality, PDF hashes, DNS route,
   egress identity, and denial cases.
8. Remove old images/volumes only through normal documented cleanup after
   rollback confidence; no destructive cleanup is part of startup.
9. Complete documentation and move this plan to implemented status.

Rollback is the whole request-path change: restore the prior Compose, pins,
patches, and documented Firecrawl/CRW configuration together. Do not partially
restore only CRW or only the CDP shim, because health and policy assumptions
will no longer align.

## Acceptance Criteria

The plan is complete only when all of the following are true:

- Onyx built-in `OnyxWebCrawler` uses one Obscura main-document navigation for
  HTML, PDF, and supported raw content.
- Byte hashes prove PDF retrieval uses the same navigation and handles correct,
  generic, missing, and misleading MIME cases.
- The configurable PDF limit defaults to 50 MiB, accepts tested values above
  50 MiB in both Onyx and Obscura, and documents memory cost.
- Actual decoded body bytes, not `Content-Length`, enforce the limit for normal,
  chunked, compressed, absent-length, and false-length responses.
- The complete PDF parser runs only in the networkless hardened service, uses
  the versioned Unix FD-passing protocol, and is timed, memory-bounded, and
  concurrency-bounded with no API-process fallback.
- Every custom SearXNG engine uses Playwright/raw CDP directly through the
  offline callback and retains current parsing/retry/suspension/scoring
  behavior.
- Search-host rate/concurrency limits work across Onyx and SearXNG processes.
- No user target hostname is resolved by Onyx/SearXNG through Docker DNS;
  final-hop resolver selection matches VPN/upstream/no-VPN mode.
- Internal/Docker/Podman/metadata names and non-public resolved addresses are
  denied at the relevant policy layers, including redirects.
- CRW, validation DNS, CRW gateways/bridges/prefetch policy, and CDP shim are
  absent from runtime, builds, secrets, networks, health dependencies, and
  current documentation.
- The generic restricted-egress proxy is neutrally named and still protects
  browser, MCP, helper, and executor paths through one listener with separate
  source quotas and a loopback-only internal exception.
- Public search engines are not special-cased by egress policy; renderer, MCP,
  helper, and executor peers receive the same allow/deny result for the same
  public destination.
- Dedicated MCP/helper/executor policy services and named policy modes are
  absent, while their narrow component bridges/networks remain.
- Cookie clearing, fingerprint, explicit waits, diagnostics, and challenge
  visibility have named owners and passing tests.
- VPN enabled/disabled/upstream-proxy startup and reconnection work without a
  direct fallback or autoheal restart storm.
- No runtime patch, shim, entrypoint, or health check installs packages.
- Lite and full `open_url`, all supported search engines, full local RAG,
  Obscura MCP, executor modes, and remaining helper downloads pass regressions.
- README, AGENTS.md, every affected document, examples, version pins, and
  upgrade instructions describe only the deployed topology.

## Final Architecture Summary

The intended result removes CRW as an API, renderer coordinator, PDF fetcher,
validation resolver, and readiness hub. It also removes the CDP shim by moving
each necessary behavior to its natural owner:

| Behavior | Target owner |
| --- | --- |
| single navigation and response identity | shared in-process CDP client |
| rendered DOM | Obscura + shared client |
| byte-exact main-resource body | Obscura body store/IO protocol |
| PDF/content parsing and LLM character budgets | Onyx |
| PDF parser resource isolation | networkless Onyx parser service and bounded worker |
| search URL construction and DOM parsing | custom SearXNG engines |
| explicit navigation waits | direct client call sites |
| browser fingerprint/proxy | Obscura |
| shared cookie clearing | Obscura server |
| cross-client per-host/search rate control | Obscura top-level navigation scheduler |
| uniform public-destination policy, peer quotas, and loopback-only exception | one shared restricted-egress proxy |
| internal destination and redirect denial | shared final-hop policy, with Obscura defense in depth |
| target DNS routing | selected final hop |
| CDP reachability | internal networks and narrow gateway |
| strict upgrade/source validation | runtime patches, image builds, and upgrade tests |

That ownership model is simpler because it eliminates translation layers, not
because it discards behavior. The migration succeeds only when the smaller
topology has evidence for the same privacy, security, document, search, and
operational properties as the current stack.
