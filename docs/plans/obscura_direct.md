# Direct Obscura Request Handling Plan

> **Status: planned; its
> [Onyx application network isolation](onyx_network_isolation.md) prerequisite
> is implemented.** This is a
> standalone implementation plan for replacing
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
  -> fixed browser egress bridge
  -> public-only request-policy namespace
  -> public route broker
  -> VPN, configured upstream proxy, or explicit no-VPN route

SearXNG custom search engine
  -> direct Playwright/CDP client
  -> the same Obscura instance
  -> fixed browser egress bridge
  -> public-only request-policy namespace
  -> public route broker
  -> VPN, configured upstream proxy, or explicit no-VPN route
```

The Onyx path must perform exactly one browser navigation for each requested
URL and reuse that navigation's retained main-resource body or rendered DOM.
HTML is converted with Onyx's existing cleanup pipeline. Binary-classified PDF
documents are retrieved byte-for-byte from the same navigation and passed to
Onyx's existing parser; accepted raw text is semantic text, not an unconditional
original-byte guarantee. There must be no preliminary `HEAD`/`GET`, CRW
prefetch, requests-based fetch, local-Chromium retry, Obscura CLI
`--dump original` call, or other second content fetch.

The SearXNG path is HTML-only. Each custom engine must navigate its search URL
through Obscura, read the rendered DOM, and parse it with the engine's existing
selectors and result normalization. The current `_crw.py` integration must be
changed to use the Playwright Python library and a raw CDP session directly,
then renamed to `_obscura.py` as part of the same cutover so the deployed code
does not retain a misleading CRW abstraction.

`searxng/engines/_obscura.py` also owns search-provider serialization. Run
SearXNG with exactly one Granian worker and keep process-wide per-provider
state shared by its request threads. Each supported custom engine permits one
active navigation and approximately one start every three seconds by default.
This is intentionally a SearXNG-only anti-blocking policy: `open_url`, Onyx
helpers, and network-enabled executors may navigate the same public hosts
without participating in it.

This is a simplification project, but removal count is not the only success
measure. The result must preserve or strengthen:

- one-fetch document handling, including PDFs;
- search-engine rendering and anti-bot visibility;
- browser fingerprint consistency and explicit wait behavior;
- per-provider SearXNG search rate and concurrency control;
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

Complete `onyx_network_isolation.md` first. This plan assumes the Onyx
application tier is already on internal networks, public-only and host-capable
traffic already crosses distinct Onyx egress bridges and isolated
request-policy namespaces to distinct public-only and host-capable route
brokers, internal dependencies use service DNS rather than shared loopback,
and application services no longer depend directly on Myst. Do not implement
this plan against the old shared-namespace topology and then migrate it again.

Before changing a subsystem, read the corresponding current documentation:

- [Onyx application network isolation](onyx_network_isolation.md) for the
  prerequisite application networks, distinct public-only and host-capable
  Onyx egress paths, transport/DNS behavior, ingress, and internal service
  URLs.

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
| Obscura | image `h4ckf0r0day/obscura:0.1.10`; ref `50e66320b0842d2844ce298957a335a6bed95c4d` | [`reference_repos/obscura`](../../reference_repos/obscura): main-resource aliasing and body storage in `crates/obscura-browser/src/page.rs`; `Network.getResponseBody` in `crates/obscura-cdp/src/domains/network.rs`; `Fetch.takeResponseBodyAsStream` in `crates/obscura-cdp/src/domains/fetch.rs`; `IO.read`/`IO.close` in `crates/obscura-cdp/src/domains/io.rs`; navigation waits in `crates/obscura-cdp/src/domains/page.rs`; and browser-context behavior in `crates/obscura-cdp/src/domains/target.rs` and `dispatch.rs`. Use the image unmodified. |
| Playwright Python | `1.58.0` in the pinned Onyx source | [`reference_repos/onyx/pyproject.toml`](../../reference_repos/onyx/pyproject.toml). Pin this exact client version in the derived SearXNG image unless compatibility testing establishes a deliberate change. No browser binary is needed in that image for CDP-only operation. |
| Mysterium | `local/private-onyx-myst:20260713` | [`myst`](../../myst) plus the current routing docs. Preserve its data-plane health check, VPN reconnect, and autoheal contract. |
| Teep | ref `6413fe0547b449e67f7296986fe8b8ffbc9bbcd2` | [`teep`](../../teep). No functional change is planned. Include it in full-stack regressions. |
| Namespace/proxy support | `alpine:3.20`, `python:3.12-slim-bookworm`, `python:3.12-alpine`, `alpine/socat:1.8.0.3` | [`docker-compose.yaml`](../../docker-compose.yaml) and the version manifest. These own namespace holding, policy-proxy runtimes, and narrow service gateways. Build a distinct minimal fixed-bridge runtime from the pinned socat artifact (or an equivalently audited fixed forwarder) without a shell/package manager; do not use the general gateway image unchanged for a security-boundary bridge. Required dependencies are installed only at image build time. |
| CDP shim, removal baseline | `local/private-onyx-cdp-shim:20260713` | [`crw/cdp_shim.py`](../../crw/cdp_shim.py) and its Dockerfile/lock. Its wait rewriting, context-proxy stripping, stealth-script stripping, cookie clearing, target selection, and trace behavior form an explicit obsolescence checklist. |
| Full-mode data support | `minio/minio:RELEASE.2025-07-23T15-54-02Z-cpuv1` | MinIO remains Onyx object storage. The unused SearXNG Valkey and the two bypassed upstream Onyx model-server containers are removed. |
| Exposure/recovery support | `tailscale/tailscale:stable`, `willfarrell/autoheal:latest` | Tailscale exists only in its enabled Compose layer. Autoheal exists only when VPN mode is enabled and retains its narrow Myst recovery role. Because these are moving tags, record the tested image identity during implementation and re-test on every pull. |

Do not patch, fork, or locally rebuild Obscura for this plan. If the exact
unmodified image fails a required capability gate, cutover is blocked until a
compatible unmodified upstream release can be pinned and re-audited.

## Goals

1. Replace CRW-backed Onyx content fetches with a single-navigation,
   PDF-capable direct Obscura client.
2. Replace CRW-backed SearXNG custom engines with direct Obscura rendering.
3. Remove CRW, the CRW validation DNS server, the CRW API gateway, the CRW
   prefetch bridge and policy instance, and the CDP shim.
4. Remove CRW-specific networks, secrets, images, configuration, build targets,
   health dependencies, documentation, and tests.
5. Use one neutral public-only request-policy namespace for generic Onyx,
   browser, and optional executor listeners, while retaining the prerequisite's
   separate host-capable policy namespace for private-permitted MCP/Web
   Connector traffic, embedding, inference, and named narrow helpers. Each
   namespace reaches only its matching route broker; public versus host access
   is a network-namespace and broker boundary.
6. Retain separate fixed browser and optional executor egress bridges and
   component networks. They use separate listeners in the same public-only
   policy namespace, but neither bridge may attach to the
   other component network or either Onyx policy-side network.
7. Provide one user-facing document byte limit, defaulting to 50 MiB and allowing
   deliberate values above 50 MiB, and propagate it correctly to both Onyx and
   Obscura.
8. Keep actual byte counts authoritative when `Content-Length` is absent,
   false, duplicated, incompatible with `Transfer-Encoding`, or describes a
   compressed representation.
9. Preserve exact main-resource bytes for PDFs and supported non-HTML
   documents when the pinned Obscura body store classifies them as binary,
   without a second network hit. Characterize and document the pinned
   content-type-driven UTF conversion limitation for binary data mislabeled as
   text rather than claiming byte identity in that case.
10. Preserve search-result quality, engine retry/suspension behavior, waits,
    challenge visibility, and SearXNG-owned per-provider rate controls while
    explicitly accepting unscheduled `open_url` navigation.
11. Preserve final-hop destination validation and resolver selection without
    sending user target hostnames to Docker's embedded resolver.
12. Make patch drift, missing dependencies, CDP incompatibility, oversized
    content, and unavailable bodies loud and diagnosable.
13. Remove unused or structurally unnecessary services remaining after the
    isolation prerequisite: SearXNG Valkey, the bypassed Onyx
    inference/indexing model servers, the SearXNG and Teep host-only socat
    publishers.
14. Leave full-mode local document ingestion unchanged. Preserve optional
    executor network isolation and explicit enablement while intentionally
    allowing its proxy path to reach public search engines.

## Non-Goals

- Do not use Obscura CLI `--dump original`. At the pinned ref that command uses
  a separate HTTP client fetch and would violate single-fetch semantics.
- Do not replace browser rendering with plain `requests`, `httpx`, `curl`, or
  SearXNG's normal online HTTP processor.
- Do not add a direct-network fallback when Obscura, its policy proxy, the VPN,
  or an upstream proxy is unavailable.
- Do not merge Obscura into the trusted Mysterium namespace. Its restricted
  control and egress networks remain security boundaries.
- Do not expose CDP on a host port or to executor, data, or general Onyx
  networks.
- Do not retain search-engine deny lists in the egress policy. Executor code,
  Onyx helpers and `open_url` may contact a public search engine just like
  any other public destination. Only the custom SearXNG engines participate in
  the per-provider search scheduler, so direct traffic may receive or
  contribute to upstream 403/429 responses without a hidden fallback.
- Do not merge the renderer and executor component networks, bridges, or
  applications. Each bridge must remain a fixed forwarder with no
  client-selectable destination, control interface, shell, secrets, or packet
  forwarding.
- Do not merge either prerequisite Onyx egress bridge or policy-side network
  into the browser or executor bridge. Exact host/internal exceptions must
  remain unreachable from browsers and executors.
- Do not make the document byte limit unlimited. Values above 50 MiB are supported,
  but the value must remain a positive finite integer.
- Do not claim that increasing Obscura's retained-body limit bounds its initial
  network allocation. At the pinned ref Obscura reads the full HTTP body before
  deciding whether to retain it.
- Do not change the full-mode local-document Web connector, embedding shim, or
  PDF freshness patch as part of this migration. The isolation prerequisite
  already owns their network and host-publication changes; remove only the
  bypassed model-server containers here and retain `doc-drop-web` and
  embedding-shim functionality.
- Do not remove Onyx helper **egress routing** merely because the
  `OnyxWebCrawler` no longer uses local Chromium. Other Onyx helper downloads,
  the Web connector, Highspot, and upstream Playwright paths still use the
  appropriate public-only or host-capable Onyx bridge/listener established by
  the isolation prerequisite.
  Only the redundant policy implementation may be consolidated.
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
- SearXNG per-provider serialization and minimum start interval;
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
5. A PDF served with a misleading text content type characterizes the pinned
   limitation: Obscura decides binary versus text storage from `Content-Type`,
   and text-classified bodies pass through lossy UTF-8 conversion before CDP
   retrieval. Record whether the resulting byte count/hash changed and return a
   typed byte-identity-unavailable or unsupported-content result when exact raw
   bytes cannot be proven. At runtime, a text-classified body that is identified
   as PDF by URL, disposition, or retrieved magic is unconditionally in this
   typed rejection path; equality of its observed length is not evidence that
   conversion preserved it. Do not pass such bytes to the PDF parser or claim
   byte-exact handling for this case.
6. A PDF with no `Content-Type` follows the same typed rejection path because
   the pinned Obscura classifier treats a missing type as text-like. Do not
   claim that URL or magic detection can restore the original bytes.
7. A declared non-UTF-8 raw-text response demonstrates that the retained CDP
   body is not a charset-preserving raw-byte channel. Characterize the result
   and do not claim that downstream charset detection can undo an earlier
   lossy conversion.
8. Missing retained body, deliberately discarded oversized body, empty body,
   and true zero-length body are distinguishable states.
9. The completed response exposes an authoritative decoded entity-body byte
   count even when `Content-Length` is absent or wrong.
10. `IO.read` can drain the body in bounded chunks, returns base64 metadata
   correctly, and `IO.close` releases the entry.
11. `Network.clearBrowserCookies` clears the shared default jar and is callable
   from a browser-level CDP connection used by the state clearer.
12. `Target.createBrowserContext` still returns the default context and does
    not provide isolation; if this behavior changes in a later pin, stop and
    re-audit rather than accidentally mixing old clearing assumptions with new
    lifecycle semantics.

The minimum behavior required from an unmodified pinned upstream Obscura image
is:

- retain correctly identified binary main responses as original bytes;
- characterize the misleading-text conversion with authoritative fixture
  byte counts and hashes, and make text-classified PDF signals select a typed
  rejection path at runtime rather than attempting to infer identity from the
  converted bytes;
- characterize missing-type and non-UTF-8 text conversion, and describe
  accepted raw text as semantic UTF-8 content rather than original bytes;
- report an explicit body-retention outcome and actual body length; and
- retain one canonical body object for loader/request aliases rather than
  silently making an avoidable full-body clone.

`reference_repos/obscura` remains audit-only. Do not add an Obscura Dockerfile,
patch set, fork, or source build. If the current image fails, evaluate a later
unmodified upstream release as a normal pin upgrade and rerun the complete
capability, security, and compatibility gates before changing the manifest.

True incremental network-to-consumer streaming is desirable but not a cutover
requirement. Until Obscura supports it, the plan intentionally accepts higher
memory use for configured large documents and documents that residual risk.

## Target Topology

### Shared Rendering Instance

Use one Obscura server for Onyx content fetches and SearXNG search rendering.
Sharing the rendering instance preserves one renderer fingerprint. SearXNG,
not Obscura, coordinates custom search-engine starts.

This is an explicit trust decision, not a CDP isolation claim. Onyx, SearXNG,
and `obscura-state-clearer` are allowed to share the default browser state and
full unauthenticated CDP authority. Any of the three can enumerate or attach to
targets created by the others, inspect page bodies/cookies, execute CDP
commands, or disrupt an in-flight target. Network placement prevents other
stack components from joining this trust domain but does not separate these
three clients from one another. Document this shared confidentiality,
integrity, and availability domain in the runtime security docs.

```text
                                      internal obscura-control network
                                     +--------------------------------+
Onyx API on internal onyx-backend   |                                |
  direct CDP client                  |  Obscura CDP :9222             |
       |                             |     --stealth                  |
       +-> obscura-cdp-gateway ------+     --proxy=browser bridge     |
                                     |        |                       |
SearXNG                              |        +-> browser-egress       |
  direct CDP client -----------------+            bridge              |
                                     +----------------|---------------+
                                                      v
                                            fixed browser egress
                                                   bridge
                                                      |
                                                      v
                                            public request-policy
                                               namespace
                                                      |
                                                      v
                                            public route broker
                                                      |
                                      Myst / upstream proxy / no-VPN

optional executor ---- fixed executor egress bridge ----> separate
                       public-only policy listener in the same
                       request-policy namespace ----> public route broker
                                                   ----> selected final hop

Onyx API -- private Unix socket + passed memfd --> Onyx document parser
                                                    network_mode: none
```

The isolation prerequisite places Onyx on `onyx-backend` and keeps restricted
service control networks separate from the general application tier. Retain
one narrow raw TCP gateway:

- `obscura-cdp-gateway` attaches to `onyx-backend` and
  `obscura-control`;
- it forwards only the Obscura CDP port;
- it exposes no host port and no policy/configuration port; and
- its health check verifies `/json/version` through the forwarded port.

SearXNG may attach directly to `obscura-control`; it does not need a second
gateway. Neither client attaches to `browser-egress`.

The document parser is not attached to these networks. It receives only already
fetched, size-bounded bytes through the private Unix socket described below.

### Networks to Retain

- the restricted Obscura control network;
- the restricted Obscura browser-egress network;
- the prerequisite `onyx-backend` caller network for the CDP gateway;
- the SearXNG API/service-ingress networks;
- distinct renderer and optional executor egress networks, each attached only
  to its own fixed bridge;
- distinct browser and executor policy-side upstream networks, each used only
  by its matching bridge and the isolated public-only request-policy process;
- one public-only request-policy process in its own namespace with separate
  fixed generic-Onyx, browser, and executor listeners on distinct policy-side
  networks and only the prerequisite public route-broker network;
- one host-capable request-policy process in a separate namespace with only the
  prerequisite host listener, exception table, and host route-broker network;
- the prerequisite public-only and host-capable route brokers in
  `netns-holder`, each reachable only from its matching policy namespace;
- the prerequisite separate public-only and host-capable Onyx bridges and
  component networks;
- optional executor networks; and
- full-mode data and local-RAG networks.

The two Onyx egress paths retain their separate bridges, component networks,
policy-side upstreams, policy namespaces, and route brokers from the
prerequisite. The public-only Onyx listener moves into the neutral public-only
request-policy process; the host-capable listener remains in its own namespace.
Browser and executor bridges cannot attach to either Onyx policy-side network
or either broker network. Existing stack-owned `NO_PROXY` rules remain
necessary only for trusted service names on Onyx's internal networks.

Also add:

- one private Unix-socket volume shared only by the Onyx API service and the
  networkless document parser. It is an IPC boundary, not a Docker network or a
  place to persist document bodies; and
- one private atomic readiness-state volume writable only by the state clearer
  and mounted read-only by Onyx and SearXNG. It carries only the bounded clear
  epoch/result record, never cookies or document bodies.

### Networks to Delete

Delete every CRW-only bridge after verifying no other service is attached:

- CRW API/control network;
- Onyx-to-CRW ingress network;
- CRW-to-SearXNG network;
- CRW-to-CDP-shim network;
- CRW prefetch-egress network; and
- prefetch-policy upstream network.

Also delete the SearXNG Valkey network. Retain or rename
`browser-policy-upstream` and optional `executor-policy-upstream` as two
distinct networks. Each fixed bridge attaches only to its component network
and matching policy-side network.

Use the current names in [`docker-compose.yaml`](../../docker-compose.yaml) as
the deletion checklist; do not retain empty networks for compatibility.

### Remaining Host Publication and Conditional Service Layers

The isolation prerequisite already owns nginx/WebUI and doc-drop host
publication and removal of their obsolete publisher containers. Do not
recreate them or move Onyx services back into `netns-holder`. In this plan,
remove `host-teep-proxy` from the Teep-through-VPN layer only after validating
the prerequisite's fixed internal Teep gateway and a loopback-bound host
publication from the namespace owner. If that publication is not portable in
a supported engine, retain the narrow Teep host publisher rather than
weakening Teep or Onyx network placement.

Publish the optional SearXNG diagnostic host port directly from
`searxng-core:8888`, bound to `127.0.0.1`. Validate both Docker and Podman with
the SearXNG networks marked `internal: true`. If a supported engine cannot do
this safely, omit the host diagnostic endpoint for that engine rather than
restoring an always-on proxy; Onyx reaches SearXNG through its service gateway.
Retain service-level health checks because deleting a host publisher also
deletes only that publisher's redundant health state.

Make optional behavior structurally optional:

- preserve the prerequisite's conditional Tailscale frontend gateway and its
  omission from disabled models;
- preserve the prerequisite's VPN-only `autoheal` and its omission from
  explicit no-VPN models;
- executor enablement adds its component network, fixed executor bridge,
  policy-side network, and listener configuration without changing the browser
  bridge.

Remove SearXNG Valkey rather than retaining an unused cache dependency. The
pinned configuration has limiter/public-instance behavior disabled and no
Valkey URL/client; unit and runtime checks must prove that state. Retain the
existing local SQLite cache behavior used by the selected engines and prove
that removing Valkey does not change engine results or suspension behavior. In
full mode, remove both upstream Onyx model-server containers. `api_server` and
`background` already set inference and indexing model-server host/port to the
internal `local-embedding-shim` service URL; confirm the removed containers
receive no non-health traffic and run indexing, `internal_search`,
embedding-setting, and unsupported rerank/query-analysis regressions before
cutover. Do not delete existing host cache directories as part of startup or
migration.

## Shared In-Process CDP Client Library

Create a neutral Python package at, for example,
`browser/obscura_client/private_onyx_obscura/`. This is wrapper client code,
not part of the Obscura source tree or image. For Onyx, bind-mount that directory
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

### Async Core and Synchronous Callers

Implement one asynchronous client core. Do not share Playwright connection,
browser, page, session, task, or event-loop objects across threads. Provide a
small synchronous adapter for the pinned synchronous call sites:

- each Onyx `OnyxWebCrawler._fetch_url` worker owns one event loop for the
  duration of that logical fetch and runs exactly one top-level client task;
- each SearXNG offline-engine worker owns one event loop and registers that
  loop, top-level task, cleanup callback, and scheduler lease under the outer
  attempt token before navigation; and
- loop shutdown occurs only after target/session/connection cleanup and
  pending-task cancellation have completed.

The adapter must latch cancellation that arrives before loop/task
registration, reject nested use from an already running event loop, and never
fall back to a detached helper thread. Playwright objects are created and
destroyed on their owning loop/thread. Unit tests must prove successful reuse
where explicitly supported, deterministic loop closure, no cross-thread
object use, and no leaked task/thread when cancellation races connect,
navigation, body read, or teardown.

### Public Result Contract

Return a typed result with at least:

- requested URL;
- final main-document URL;
- main-document status code and status text when available;
- normalized response headers;
- parsed content type and charset;
- the mirrored Obscura body-storage classification and whether original-byte
  identity is guaranteed;
- loader/request identity;
- authoritative actual body byte count;
- rendered HTML when requested;
- exact raw entity bytes only for binary-classified requests, or semantic
  UTF-8 text for an accepted text-classified raw format;
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
- byte identity unavailable or unsupported charset/conversion;
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

Do not claim a fresh target provides cookie or first-party state isolation.
At the pinned image, `Target.createBrowserContext` is a compatibility stub: it
clears and returns the single default context, while `Target.createTarget` does
not select a distinct context. CDP therefore provides no usable per-first-party
context boundary. A fresh target isolates page/session runtime state, but all
targets still share the default cookie jar and server fingerprint.

Do not patch or fork Obscura to change this behavior. Use the stable configured
Obscura fingerprint and the periodic clearing design below. If a future
unmodified upstream release implements real browser contexts, treat adoption
as a separate pin upgrade and re-audit context selection, cookies, redirects,
third-party subresources, fingerprint stability, lifecycle, and capacity before
changing this plan.

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

The stream API does not separately expose Obscura's original `main_is_binary`
decision. Mirror the pinned `is_text_like_content_type` predicate exactly from
`crates/obscura-browser/src/page.rs`, including its rule that an absent or empty
`Content-Type` is text-like. Add a strict source-shape anchor and fixture table
for every recognized text type, suffix rule, parameters/case normalization,
missing type, and representative binary type. If a future pin changes that
predicate or exposes an authoritative classification field, stop and re-audit;
do not infer original-byte identity from valid UTF-8 output or equal lengths.

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

Content classification may require both a bounded retained-body prefix and a
rendered DOM from the same navigation. Only a body that Obscura classified and
stored as binary has a raw-byte guarantee. Open at most one body stream: read
only the prefix needed for magic detection, then either continue draining that
same stream for a binary-classified raw document or close it before using the
already-rendered DOM. If a text-classified body has PDF signals, close it and
return the typed byte-identity-unavailable result. Never reopen the stream or
navigate again merely because the MIME type was misleading.

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
sidecar, or Docker's embedded resolver. The matching restricted-egress policy is
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
- Obscura CLI `--dump original`; or
- a second Obscura navigation after MIME or parser failure.

Retries that initiate another main-document navigation are disabled by default.
If a future retry policy is proposed, it is a user-visible semantic change and
must distinguish intentional retries from the single-fetch guarantee.

Preserve upstream `contents()` ordering and per-URL failure isolation: parallel
calls may complete out of order internally, but output entries remain aligned
with input URLs, and one failed URL does not discard successful peers.

### Content Classification and Dispatch

Classify from the final URL, normalized MIME type, Obscura's binary/text body
classification, and a bounded prefix of the same navigation's retained body.
The prefix is exact only for a binary-classified body. Existing Onyx PDF detection in
[`backend/onyx/utils/web_content.py`](../../reference_repos/onyx/backend/onyx/utils/web_content.py)
uses URL, MIME, and `%PDF` magic; retain all three signals.

Dispatch as follows:

| Content | Source from the one navigation | Onyx processing |
| --- | --- | --- |
| HTML/XHTML | rendered `page.content()` | existing `web_html_cleanup` / `_parse_html_to_web_content` behavior, metadata and link handling |
| PDF, binary-classified by Obscura | exact entity bytes | existing PDF text/title/metadata extraction inside the hardened parser boundary below |
| PDF signal in a text-classified body | converted retained body, with no raw-byte identity guarantee | close the body handle and return typed `byte-identity-unavailable`; never invoke the PDF parser |
| accepted textual format, binary-classified by Obscura | exact entity bytes | bounded charset-aware decoding with existing safe utilities where appropriate; preserve meaningful whitespace |
| UTF-8/ASCII accepted textual format, text-classified by Obscura | retained semantic text; not an unconditional original-byte channel | UTF-8 validation with explicit conversion diagnostics; preserve meaningful whitespace |
| declared non-UTF-8 accepted textual format, text-classified by Obscura | retained content after Obscura's UTF-8-lossy storage step | return a typed unsupported-charset/conversion result unless a future audited upstream API exposes charset-preserving bytes; do not claim downstream decoding can recover the original |
| DOCX, PPTX, XLSX, EML, EPUB, images, and other binary formats | exact bytes are not emitted to the LLM | return an explicit unsupported-content error without a second fetch |
| unsupported binary | exact bytes are not emitted to the LLM | return an explicit failed `WebContent`/typed error naming the unsupported MIME without a second fetch |
| empty or image-only document | same-navigation result | preserve current explicit empty/unparseable behavior; do not invent OCR |

Do not interpret arbitrary binary bytes as UTF-8 HTML. A `%PDF-` signature in a
text-classified body proves that the response is PDF-like, not that Obscura's
converted bytes are safe to parse; select the typed rejection above.
Conversely, do not label an HTML error page as a PDF solely because the
requested URL ended in `.pdf`; status, MIME, magic, and parse outcome all
contribute to the error message.

For accepted UTF-8/ASCII raw text, compare the authoritative pre-conversion
body count with the drained UTF-8 byte count. A mismatch proves conversion and
must be a typed failure. Equality permits semantic text handling but is not a
cryptographic byte-identity claim. Record that limitation in runtime
documentation and diagnostics without logging body content.

Challenge, 401/403, 404, 429, 5xx, timeout, and empty-content results should
remain informative to the agent. Remove stale messages that tell operators to
configure Firecrawl or CRW.

The pinned `OnyxWebCrawler` currently has only two effective branches: PDF is
detected from URL/MIME/magic and parsed as PDF; every other response is decoded
and cleaned as HTML. Onyx's separate generic file-ingestion helper supports
DOCX, PPTX, XLSX, EML, and EPUB, but `open_url` does not call it. This migration
therefore adds explicit raw text/JSON/XML/YAML/source handling but does not
silently expand `open_url` into archive/office/email parsing. Any future format
addition must be implemented inside the isolated parser, with archive
expansion, resource-limit, malformed-input, and no-API-fallback tests.

Treat accepted raw formats as bounded text decoding, not active data loading:
do not resolve XML entities, follow schemas/includes, construct YAML objects,
execute source, or dereference embedded URLs. UTF-8 validation and optional
syntax validation must remain networkless and resource-bounded. Do not retain
the old claim that downstream charset detection can recover bytes already
converted by Obscura.

### Document Parser Safety Boundary

CRW currently supplied PDF-specific controls that disappear with CRW. The
built-in Onyx parser at the pinned ref uses an isolated PDFium subprocess with
a timeout but can fall back to in-process `pypdf`. Do not move untrusted large
PDF parsing into the API process or merely a child that inherits the API
server's broad network namespace. That would weaken the isolation of the
current recommended CRW path.

Add one narrowly scoped `onyx-open-url-document-parser` service. It uses the
exact pinned Onyx backend image and existing Onyx PDF parser plus bounded raw
text decoders, but runs with:

- `network_mode: none`;
- a read-only root filesystem;
- all capabilities dropped and `no-new-privileges`;
- no application secrets, proxy variables, or external service URLs;
- a private writable `tmpfs` only where the parser requires it;
- container PID/memory/CPU limits sized from validated stack settings; and
- no host ports or Docker networks.

Place its versioned client/server/protocol code in a repository package such
as `onyx/open_url_document_parser/private_onyx_document_parser/`. Bind-mount
that package read-only at `/opt/private-onyx/document-parser` in the API and parser services and
append it to their `PYTHONPATH`; start the sidecar with an explicit
`python -m private_onyx_document_parser.server`. This is source code shipped by the
wrapper, not a runtime-installed dependency.

The API server and parser share only a private Unix-domain-socket directory.
Use the same non-root UID/GID or a dedicated narrow group, socket mode `0660`,
and `SO_PEERCRED` verification. No background, executor, SearXNG, or other
container receives the socket mount.

Avoid copying a large private document into JSON, stdout, or a persistent volume.
On Linux the API process should create an anonymous `memfd`, write the already
bounded bytes, rewind it, and pass the file descriptor with `SCM_RIGHTS` over
the Unix socket. Descriptor passing is mandatory. Read-only memfd sealing is a
preferred hardening option pending Docker/Podman/Desktop compatibility tests,
not an unconditional cutover requirement. Where supported, create with
`MFD_ALLOW_SEALING`, apply `F_SEAL_WRITE`, `F_SEAL_GROW`, `F_SEAL_SHRINK`, and
`F_SEAL_SEAL`, and have the parser verify them with `F_GET_SEALS`.

The parser must `fstat` the received descriptor and independently reject sizes
above the configured cap before parsing. On a validated platform without seal
support, the sender closes its writable descriptor immediately after the
`SCM_RIGHTS` handoff and retains no intentional duplicate. The parser mounts no
writable shared file area, copies exactly the advertised bytes through a
strictly bounded reader into parser-owned anonymous storage before invoking any
format parser, requires EOF at that boundary, closes the received descriptor,
and re-checks size before and after the copy. Treat the copied contents as fully
untrusted because a compromised sender could retain a duplicate and race the
copy; the worker resource envelope remains authoritative, but parser execution
never reads from the still-shared descriptor. Record the compatibility result,
extra memory cost, and residual during-copy TOCTOU risk. Failure of
`SCM_RIGHTS` remains a failed capability check; do not fall back to a network
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

- `ONYX_OPEN_URL_MAX_CONCURRENT_DOCUMENT_PARSES`, default `2`;
- `ONYX_OPEN_URL_DOCUMENT_PARSE_TIMEOUT_SECONDS`, default `120`; and
- `ONYX_OPEN_URL_DOCUMENT_PARSE_MEMORY_MB`, default `512`.

These can remain advanced stack settings rather than routine UI options, but
they must be validated and documented. The memory limit must be high enough for
the selected document byte limit and parser overhead; increasing the document limit
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

## Configurable Document and Body Limits

### One User-Facing Document Limit

Add one user-facing setting:

```env
ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB=50
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
document byte limit controls retained-body acceptance for PDF and all accepted
non-HTML raw formats plus isolated parser input; with the
pinned Obscura path it does **not** cap the initial full network-body
allocation.

### Propagation Into Obscura

At startup derive:

```text
OBSCURA_NETWORK_BODY_BUFFER_BYTES =
  max(ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_BYTES, configured HTML/raw-body floor)

OBSCURA_IO_STREAM_MAX_BYTES >= OBSCURA_NETWORK_BODY_BUFFER_BYTES
```

Choose sufficient IO stream entries for the configured fetch concurrency and
fail validation when the settings are inconsistent. Do not use a hidden 50 MiB
ceiling in either Onyx or Obscura after the operator selects a larger value.

SearXNG does not need PDF support, but its search HTML must fit the common
retention floor. Give search HTML its own conservative internal maximum, no
larger than the preserved 20 MiB Onyx HTML default unless separately justified,
so a search result page cannot consume the entire large-document allowance without
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
peak memory can be several times the configured document limit. Documentation should
warn operators to plan for at least roughly 3–5x per concurrently handled
large document and potentially more for parser expansion, and say that this is an
estimate, not a bound.

Add or reuse a bounded `ONYX_OPEN_URL_MAX_CONCURRENT_FETCHES` setting. Large
limits should be paired with lower fetch and document-parse concurrency. An
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
single default context. Add one narrow `obscura-state-clearer` sidecar on
`obscura-control`; it has no egress network, proxy settings, application
secrets, host port, or general network relay behavior. Its CDP connection is
nevertheless full browser authority, including technical access to targets,
bodies, and cookies; that authority is accepted because the clearer belongs to
the explicitly shared CDP trust domain. Its implementation must invoke only
the existing CDP `Network.clearBrowserCookies` method and expose no
caller-selected command surface. Do not modify Obscura or rely on unsupported
browser-context commands.

Keep its small implementation under a wrapper-owned path such as
`browser/obscura_state_clearer/`, with a pinned build-time dependency lock and
a fixed CDP service URL. Run it as a numeric non-root user with a read-only
root filesystem, dropped capabilities, `no-new-privileges`, and only a small
tmpfs if its health state requires one. Its health endpoint, if any, is exposed
only on `obscura-control` and reports schedule/result metadata without URLs or
cookie values.

Use the existing stack setting, normalized to one name:

```env
OBSCURA_BROWSER_CLEAR_COOKIES_INTERVAL_SECONDS=3600
```

Requirements:

- clear the shared default cookie jar at the configured interval;
- use `0` only as an explicit documented disable value, if disabling is
  retained at all;
- perform the first clear after Obscura is ready and before Onyx/SearXNG
  clients become ready, then schedule later clears from monotonic time;
- use one clearer replica and a monotonic schedule so duplicate loops cannot
  race or shorten the interval;
- accept and document that an atomic cookie-jar clear may occur during an
  active navigation, matching the current periodic behavior; do not pretend
  CDP can reserve per-navigation or per-first-party state;
- log only success/failure and timing, never cookie data;
- expose any startup clear failure and repeated later failures through
  health/readiness; and
- validate the CDP method by requiring that first clear to succeed.

Both Onyx and SearXNG startup dependencies must require the clearer's successful
first-clear health state. SearXNG must not become ready directly from Obscura
health alone. Publish the clearer's last-success generation and monotonic age
as an atomically replaced, bounded state file on a private volume writable only
by the clearer and mounted read-only by Onyx and SearXNG. After startup, a
missed/repeated clear makes the clearer unhealthy and blocks **new**
direct-client navigations when that state is missing, malformed, or stale;
in-flight navigations may finish or observe the atomic clear. Recovery requires
a successful clear. Do not add a general control listener or let a permanently
failed clearer degrade silently into unbounded cookie retention.

Version the state record and include a strictly increasing generation, success
result, monotonic timestamp, interval, and kernel boot identity. At startup,
prove that clearer, Onyx, and SearXNG observe the same monotonic clock domain
and boot identity before comparing ages; fail readiness on clock-domain
mismatch, generation rollback, a future timestamp beyond a small scheduling
tolerance, unsafe ownership/mode, oversized content, or unknown fields/version.
Write and `fsync` a temporary file in the same volume, atomically replace the
record, and `fsync` the directory. Do not use wall-clock time alone for
freshness or accept a stale record merely because Obscura itself is reachable.

Do not issue `Target.createBrowserContext` as a clearing mechanism: at this pin
it clears the same global jar as a side effect and returns no isolation. Do not
claim localStorage/IndexedDB partitioning that the pinned browser does not
persist across page runtimes. The unavoidable residual is that different
first-party sites and their third-party subresources share cookies between
clear intervals; periodic clearing limits duration but does not create
first-party isolation.

## SearXNG-Owned Search Navigation Rate and Concurrency Control

CRW currently limits requests by registrable domain. The target deliberately
narrows this behavior to the supported custom SearXNG providers. Implement it
in `searxng/engines/_obscura.py`, where search URL construction, navigation,
error mapping, and provider identity are already known. Do not modify Obscura
to schedule general CDP traffic and do not infer navigation starts from proxy
connections.

Run `searxng-core` with `GRANIAN_WORKERS=1` explicitly. Granian may retain its
blocking worker threads, so unrelated searches and normal HTTP handling remain
concurrent. The shared `_obscura.py` module owns a process-wide scheduler keyed
by stable custom provider identity (`google2`, `brave2`, `duckduckgo2`,
`startpage2`, or `bing2`), not by query text. All custom engine modules import
that one scheduler; do not create one module-local lock per engine file.

For each provider, preserve these defaults:

- maximum active top-level search navigations: `1`;
- start rate: `0.33`/second, about one start every three seconds;
- jitter factor: `0.2` only where characterization proves the current search
  path applies it; and
- a finite stack-owned queue timeout aligned with the characterized SearXNG
  outer deadline, never an unbounded wait.

Use monotonic time and cancellation-safe `try/finally` release. Hold the lease
from immediately before the single `Page.navigate`/`page.goto` through its
configured readiness wait, failure, or timeout. A redirect remains charged to
the selected provider bucket for the whole attempt; the Python client cannot
acquire a new destination-domain lease before Obscura follows a redirect, and
the target design does not claim that stronger behavior. Subresources are not
separate scheduled attempts. Do not log queries or target URLs containing
query values.

Startup validation must fail if Granian is configured with more than one
worker, and Compose tests must prohibit scaling `searxng-core` above one
replica. Do not restore Valkey or add another coordination service. If future
scaling requires multiple SearXNG processes, redesign scheduling explicitly
rather than silently weakening the guarantee.

### SearXNG Outer-Deadline Cancellation

The pinned offline processor ignores its `timeout_limit`, and
`Search.search_multiple_requests` starts raw Python threads, joins them until
the outer deadline, then only records a timeout. It does not cancel the worker.
A timed-out direct engine could therefore keep a CDP navigation and provider
scheduler lease alive after SearXNG has returned.

Patch the pinned SearXNG orchestration narrowly in the derived image so the
existing outer engine deadline is authoritative. Do not maintain a separately
configured inner navigation timeout that must be kept in sync. The direct
client must:

1. create a unique attempt token before each custom offline-engine thread is
   started and retain the exact thread object rather than rediscovering it by
   name;
2. register the worker's asyncio loop, top-level navigation task, page/CDP
   cleanup callback, and scheduler lease against that token before navigating;
3. make cancellation latched and race-safe, so a timeout that occurs before
   registration immediately cancels a later registration;
4. on the SearXNG outer timeout, request cancellation with
   `loop.call_soon_threadsafe(task.cancel)`, close the page/session/connection
   in `finally`, and release the provider lease exactly once;
5. wait only a small fixed cleanup grace, which is not a second request
   timeout, and reject late result-container writes after the outer result has
   been finalized; and
6. make readiness unhealthy and fail loudly if a worker survives cleanup
   grace, because Python threads cannot be killed safely and a leaked browser
   target/lease cannot be treated as normal.

Apply the patch with strict source-shape and signature checks against the
pinned `offline.py` and search orchestrator. Do not use asynchronous exception
injection, detached daemon threads, a second HTTP fetch, or process-global
thread-name cancellation. Unit tests must cover timeout before task
registration, while queued for a lease, during navigation, during body/DOM
read, during cleanup, and concurrently with unrelated engines. After each
case, assert no live task, target, connection, thread, scheduler lease, or late
result mutation remains.

`open_url` is intentionally unscheduled. Its existing overall fetch
concurrency and timeout bounds still protect local resources, but it may
navigate a search provider concurrently with SearXNG and may contribute to an
upstream 403/429. The same accepted behavior applies to Onyx helpers and
network-enabled executors. Only custom SearXNG provider attempts
receive the anti-blocking schedule.

## Restricted Egress and Separate Bridges

Remove search-host classification from the egress policy completely. Search
engines are ordinary public targets for renderer Obscura, Onyx helpers, and
network-enabled executors. The final-hop policy
continues to deny internal names and resolved non-public addresses, apply the
plain-HTTP rule, select the VPN/upstream/no-VPN resolver path, enforce HTTP
framing, and redact credentials and sensitive request material.

The current generic implementation lives under `crw/`. Move it to a
neutral path such as `egress/restricted_egress_proxy.py`, rename legacy
`PREFETCH_*` vocabulary to `EGRESS_*`, and remove `EGRESS_PROXY_POLICY`,
`PREFETCH_BLOCK_HOSTS`, the search matcher, every named policy mode, and all
obsolete component-specific policy configuration. Preserve the isolation
prerequisite's host-capable namespace/listener, exact host and opt-in RFC1918
destination table, matching route broker, and allowed bridge peer. Extend the
prerequisite's existing public-only policy service with the browser and optional
executor listeners, retaining its public route broker; do not create a second
public policy process from the legacy browser service. Rename the prerequisite
service to the neutral final name in the same atomic cutover if needed. Update
imports, Compose, locks, tests, and documentation atomically. Do not keep
compatibility aliases.

Run exactly two request-policy processes in separate, non-trusted network
namespaces:

- one public-only `restricted-egress-policy` with three non-loopback listeners,
  each on its own policy-side network and reachable only through the fixed
  generic-Onyx, browser, or optional executor bridge, plus only the public
  route-broker network; and
- one host-capable policy process with only the prerequisite host
  listener/network and host route-broker network, reachable only through the
  fixed host-capable Onyx bridge.

Retain the prerequisite's two minimal route brokers in `netns-holder`. The
public broker repeats destination validation and accepts only public targets
from the public policy namespace. The host broker repeats validation and adds
only the exact host and `EGRESS_ALLOW_RFC1918`-gated RFC1918 behavior. A
policy process cannot address the other broker network, and neither broker
accepts raw client HTTP proxy traffic or an operator/client-selected upstream.

All three public listeners have identical public-destination policy and no
host/internal exception table. The separate bridges preserve caller-network
isolation; sharing the public-only process is accepted because the namespace
can reach only the public broker, which repeats the denial. A public caller or
a compromised public request-policy process cannot obtain a host exception
through headers, target syntax, proxy chaining, another network attachment, or
an alternate broker. The policy does not serialize or count requests by
caller. Loopback may remain available only for process-local health.

Retain a fixed `obscura-egress-bridge` between `browser-egress` and
`browser-policy-upstream`. Retain or add a separate optional
`executor-egress-bridge` between `executor-egress` and
`executor-policy-upstream`. Each exposes one fixed proxy port and forwards only
to its matching listener. Never attach either bridge to the other component
network or to any Onyx component/policy-side network. Static and runtime tests
must prove that renderer and executor clients cannot address one another or
either Onyx listener.

Each bridge must:

- use a minimal immutable forwarding image with no shell or package manager;
- run as a non-root numeric user with a read-only filesystem, all capabilities
  dropped, and `no-new-privileges`;
- mount no source, secrets, credentials, Docker socket, or writable volume;
- use a literal fixed command/configuration whose only upstream is the bridge
  listener; never accept a target host/port from a client or user-facing env;
- expose no admin, DNS, metrics, control, or host port;
- disable IPv4 and IPv6 packet forwarding and prove the container cannot route
  packets between component networks;
- bind only the one forwarding port;
- attach no restricted application directly to either policy-side network or
  `netns-holder`; and
- pass negative reachability tests proving renderer and executor remain unable
  to address one another through ordinary routing or either bridge listener.

Create the shared bridge image in a multi-stage build from a pinned input: copy only
the forwarder, a purpose-built end-to-end health-probe binary, and their
required runtime libraries into a scratch/distroless final stage. The probe
must construct the fixed blocked-local `CONNECT`, send it through the local
forwarding port, require the exact policy/broker denial, and expose no
caller-selected host, port, command, or arbitrary fetch mode. Invoke it through
an image-owned fixed `healthcheck` subcommand; do not rely on `sh`, `printf`,
`grep`, `wget`, or runtime-installed tools. Set the numeric user and fixed
entrypoint in the image, and pin the result separately in
`stack.versions.env`. Add dedicated Makefile build and image-readiness targets,
and inspect the built image in tests to verify that common shells and package
managers are absent. The running bridge must never install packages or accept
an operator-provided command override.

These controls prevent an accidental generic relay or IP router. A compromised
bridge reaches its one component network and its matching public-only policy
listener, but not the other component or any host-capable route. The shared
public-only policy namespace remains a public-egress failure and availability
domain; its only onward peer is the independently validating public route
broker. Document that residual without describing it as a host access path.

## Readiness, Health, VPN Liveness, and Autoheal

### Required Dependency Graph

The target startup chain is:

```text
myst-client-vpn (VPN mode) or explicit no-VPN-ready namespace
  -> host route broker -> host-capable Onyx policy remains ready
  -> public route broker
    -> public-only policy (generic Onyx + browser + optional executor listeners)
      -> fixed browser egress bridge
      -> Obscura CDP server
      -> Obscura state clearer
      -> obscura-cdp-gateway
      -> Onyx API readiness

  -> optional fixed executor egress bridge/listener

Obscura CDP server
  -> Obscura state clearer first-clear readiness
  -> SearXNG readiness
  -> SearXNG service gateway
  -> Onyx API readiness

networkless Onyx document parser
  -> private Unix socket protocol health
  -> Onyx API readiness
```

Implement health checks as follows:

- Mysterium health remains data-plane-aware in VPN mode and immediately
  well-defined in explicit no-VPN mode. Re-test both forms.
- Each route broker verifies its selected final-hop routing prerequisites
  without resolving or fetching an arbitrary public user target. Each
  request-policy process verifies listener configuration, its fixed broker
  protocol, and the expected broker denial without exposing a listener to the
  wrong bridge.
- Each component bridge verifies its one fixed upstream listener and receives
  the expected denial for a blocked local target.
- Obscura health verifies local `/json/version` but does not make a public probe
  on every health interval. The state-clearer health separately verifies its
  schedule, most recent result, and configured CDP method.
- `obscura-cdp-gateway` health fetches `/json/version` through the gateway and
  validates the expected browser/product protocol shape.
- SearXNG health verifies its local HTTP API, Playwright/shared-client import,
  local CDP connectivity, and the clearer's successful current readiness
  without executing a public search.
- Document-parser health uses a bounded Unix-socket protocol ping and verifies the
  expected parser/protocol version without parsing an external document.
- Onyx API depends on the CDP gateway, SearXNG service gateway, both prerequisite
  Onyx bridges/policies/brokers, browser bridge, state clearer, networkless
  document parser, and its other existing required services. Background and
  other clients depend only on the prerequisite egress chain(s) they actually
  use. Remove all CRW, Valkey, and legacy policy-service health dependencies.

Compose `depends_on` establishes startup order, not permanent runtime
supervision. A failed downstream request after startup must remain a visible
typed failure. Keep the prerequisite's application-local health semantics:
the API health endpoint must not become a transitive VPN/public-network probe,
and Myst/policy restart must not restart the application tier. Do not add
silent direct fallbacks.

### Autoheal and VPN Reconnection

The current autoheal behavior is primarily for Mysterium/VPN reconnection.
Preserve its labels, restart authority, and grace periods when VPN mode is
enabled. Omit the `autoheal` service entirely from explicit no-VPN Compose
models. Do not label every stateless bridge/gateway for autoheal and create a
restart storm during a VPN flap. The networkless parser is independent of VPN
health and must not restart merely because Myst reconnects.

Validate these sequences:

1. VPN enabled, initial connection succeeds.
2. VPN disabled, Myst health reaches the documented ready state without a
   nonexistent tunnel and no autoheal container exists.
3. VPN enabled, tunnel drops after the stack is healthy, Myst becomes
   unhealthy, autoheal/reconnect occurs, and no request-policy/broker chain
   permits direct egress during the gap.
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

- Onyx, SearXNG, and the state clearer form one accepted CDP trust domain. They
  may issue powerful commands only to the dedicated Obscura instance, but are
  not isolated from one another's targets, bodies, cookies, or browser state.
- CDP is unauthenticated and therefore remains confined to internal networks
  and one narrow gateway; it is never host-published.
- Obscura cannot reach the broad trusted namespace. Its only internet route is
  the fixed browser bridge to the browser-only restricted-egress listener.
- The bridge cannot select arbitrary trusted-namespace ports and has no packet
  forwarding or control surface.
- The prerequisite host-capable Onyx namespace and route broker retain their
  exact host and opt-in RFC1918 exceptions. The generic Onyx, browser, and
  executor listeners in the public-only namespace have no such exception and
  can reach only the public route broker. Their policy-side networks and
  allowed bridge peers remain distinct, and the host-capable namespace remains
  a separate public-versus-host boundary.
- Executors cannot reach CDP, SearXNG control, Obscura control, Docker socket,
  data stores, or host services through normal routing. Their egress network
  reaches only the fixed executor bridge's matching public-only listener.
- Untrusted document parsing occurs in a networkless, read-only,
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

Retain the prerequisite's exact `host.docker.internal` exception only on the
host-capable Onyx policy listener and broker, and the existing host-only
upstream-proxy bootstrap exception only for the configured proxy endpoint.
Never add either to the public Onyx, browser, or executor destination policy.

The definitive list and resolver semantics belong in
[Internal network security](../internal_network_security.md), backed by unit
tests rather than duplicated divergent lists.

### Onyx SSRF Interaction

Onyx's upstream URL validation remains useful for URL syntax and UI paths, but
must not perform a target DNS lookup before the browser fetch. The direct
adapter either bypasses the DNS-resolving helper or patches its use at this
narrow boundary while retaining syntax/literal checks.

Saved Onyx SSRF settings are not authorization for Obscura or `open_url` to
access private targets. Direct `open_url` always uses the public bridge; the
prerequisite's saved-level public/host selection applies only to MCP/OAuth and
admin-configured Web Connectors. The request-policy and route-broker chain
remains authoritative for all hostnames and redirects. The prerequisite's
`EGRESS_ALLOW_HTTP_URLS` setting must be enforced both before navigation and at
the final hop.

### Residual Risks

Document these honestly after implementation:

- Obscura CDP is powerful and relies on network isolation rather than protocol
  authentication.
- CONNECT policy sees the requested hostname/port but cannot inspect encrypted
  HTTP paths or response content.
- Obscura currently reads the complete response into memory before the retained
  body store, CDP stream, or client-side limit can reject it. The bounded
  `Fetch.takeResponseBodyAsStream`/`IO.read` interface streams from an already
  buffered body; it does not bound the initial network allocation. A hostile
  response can therefore exceed the configured retention cap transiently, and
  accepted large bodies may coexist as multiple Obscura/CDP/Python/memfd
  representations. Values above 50 MiB deliberately trade memory safety margin
  for document support until Obscura implements true incremental network
  streaming.
- Large document parsing has format/parser-specific CPU/memory risk despite networkless
  container and worker bounds.
- Browser subresources intentionally create additional requests; single-fetch
  means one main-document navigation, not one TCP/HTTP request total.
- Some sites may fingerprint Obscura or challenge shared browser state. The
  pinned CDP cannot create distinct contexts, so different first-party sites
  and third-party subresources share cookies until periodic clearing.
- Onyx, SearXNG, and the state clearer have full shared CDP authority. A
  compromise of any one can inspect or disrupt targets and browser state owned
  by the others; periodic clearing limits cookie lifetime but is not an access
  boundary.
- `open_url`, helper, and executor search-engine requests do not use the
  SearXNG per-provider scheduler and may receive or contribute to 403/429
  responses from the shared exit IP. This is accepted behavior, not a reason
  to restore a hidden search deny list or browser fallback.
- The public-only request-policy namespace is a shared failure and
  resource-contention domain for generic Onyx, browser, and executor traffic.
  It reaches only the public route broker; the host-capable namespace and
  broker remain independent.
- Each component bridge is a residual pivot only between one component network
  and its matching public-only policy-side network; separate bridges prevent a
  bridge compromise from directly entering the other component network.
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
- legacy `browser-egress-proxy` after its public listener is moved into the
  prerequisite public-only policy service; and
- optional legacy `executor-egress-proxy` whenever executor networking is enabled.

Also remove or replace these structural services:

- retain `obscura-egress-bridge` as the fixed browser bridge and retain/add a
  separate optional `executor-egress-bridge`; rebuild both from the same
  hardened immutable image if necessary;
- remove unused `searxng-valkey`, its network/volume/dependency, and
  `VALKEY_IMAGE` pin;
- remove `host-searxng-proxy`; publish the diagnostics port directly from
  `searxng-core` on `127.0.0.1`, or omit the optional host diagnostic endpoint
  if the supported container engine cannot publish securely from its internal
  network;
- in full mode, remove the unused `inference_model_server` and
  `indexing_model_server` services and their log volumes because `api_server`
  and `background` already route both model-server endpoints to the internal
  embedding shim;
- remove `host-teep-proxy` from the Teep-through-VPN layer and publish its
  loopback-bound port from `netns-holder`;
- verify that the prerequisite's Tailscale and autoheal conditional layers are
  preserved without reintroducing either service into disabled/no-VPN models.

Add `obscura-cdp-gateway` if the existing generic gateway cannot be
instantiated safely, the networkless `onyx-open-url-document-parser` security
boundary, `obscura-state-clearer`, and the two separate component bridges.
Use or atomically rename the prerequisite public-only policy service as the
restricted-component `restricted-egress-policy` process; remove the legacy
browser/executor policy services after their listeners move into it. No
scheduler, fetch, validation, rate-limit, or body-conversion sidecar is added.

Relative to the completed isolation baseline, the request-path changes remove
six always-on CRW/shim containers plus the legacy browser policy service, and
add the document parser plus state clearer. Valkey and SearXNG host-publisher
removal therefore bring the expected lite reduction to at least seven
containers, assuming the current gateway is retargeted rather than duplicated.
An enabled executor layer removes its additional legacy policy service. Full
mode removes two more unused model servers. Teep-through-VPN may omit one
additional host proxy when the supported engine can publish it safely. Host WebUI/doc,
Tailscale, and no-VPN autoheal reductions belong to the prerequisite baseline
and must not be counted again. Treat all counts as configuration-dependent
assertions generated from effective pre/post Compose models.

Do **not** remove:

- `obscura`;
- the separate browser and optional executor bridges;
- the renamed public-only restricted-component `restricted-egress-proxy`;
- `searxng-core` or `searxng-service-gateway`;
- `onyx-open-url-document-parser` after it is added;
- `obscura-state-clearer` after it is added;
- the optional executor component network;
- `doc-drop-web`, `local-embedding-shim`, or other required full-mode data
  services;
- `autoheal` in VPN-enabled models; or
- `tailscale-funnel` when Funnel is enabled.

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
- shim-owned periodic cookie clearing: `obscura-state-clearer` owns it through
  the existing CDP command;
- shim-specific target selection/wait-for-search-host coordination: the shared
  client and SearXNG `_obscura.py` scheduler own the remaining search behavior;
- CDP-shim trace configuration: neutral client diagnostics replace it.

Retain and update these Onyx/runtime patches because this migration does not
obsolete them:

- lite-mode `open_url` availability;
- `ONYX_OPEN_URL_MAX_CHARS_PER_URL` and total-character limit propagation;
- Onyx helper proxy routing for helper downloads and remaining local Chromium
  users, retaining the prerequisite's public-only bridge/listener;
- full-mode local-document PDF freshness/reindexing;
- SearXNG round-robin and last-resort scoring, adapted to offline engines;
- code-interpreter capability/network/upload patches; and
- Deep Research/tool-batch patches.

For completeness, the current patch inventory has this migration impact:

| Current patch or workaround | Migration disposition |
| --- | --- |
| CRW Firecrawl-compatible content/search adapter | Delete. Built-in `OnyxWebCrawler` and direct SearXNG engines replace it. |
| CRW validation DNS and Onyx validation workaround | Delete. The shared client performs non-resolving syntax/literal checks and the final hop resolves/validates names. |
| CDP shim wait injection, stealth/proxy stripping, cookie clearing, target selection, and tracing | Delete only after direct explicit waits, default-context use, the narrow state clearer, and neutral tracing pass their gates. |
| CRW prefetch-blocking and legacy browser/executor policy instances | Delete. There is no raw search prefetch after every search engine navigates directly through Obscura. Move the generic implementation, retain the prerequisite host instance, and add browser/executor listeners to its existing public instance. |
| Named egress modes and search-host deny list | Delete. Public search hosts use the same policy as every other public destination, including for executors. |
| Dedicated component policy implementations | Share one neutral implementation. Consolidate generic Onyx, browser, and executor public listeners into one public-only policy namespace; retain the separate host-capable namespace, both route brokers, and all distinct fixed bridges/policy-side/broker networks. |
| Onyx helper HTTP/Playwright proxy patch | Retain. Remove `OnyxWebCrawler` reliance on its local-Chromium/requests path, but keep it for Web connector, Highspot, helper downloads, and other upstream callers; keep the prerequisite's fixed Onyx bridge URL. |
| Lite `open_url` availability | Retain and make it activate the direct crawler patch. |
| Open URL/web-search character budgets | Retain. Add the separate generalized document byte cap; do not merge byte and character semantics. |
| SearXNG CRW-backed engine adapter | Replace with direct offline Playwright/CDP engines. |
| SearXNG round-robin and last-resort scoring | Retain and revalidate against offline processor timing/exceptions. |
| LLM context-window override | Retain unchanged. |
| Assistant reasoning preservation and chat-reminder placement | Retain unchanged. |
| Deep Research selected-chat-Agent tools and complete tool-batch execution | Retain unchanged. |
| Onyx LiteLLM interaction and GLM automatic tool-choice compatibility | Retain unchanged. |
| Coding-agent final-answer synthesis and saved tool-result preservation | Retain unchanged. |
| Coding-agent repository/code-interpreter upload alignment | Retain unchanged. |
| Internal-search content caps | Retain unchanged. |
| Code-interpreter capability descriptions and executor network/proxy patch | Retain the restricted executor network and its separate bridge when enabled, and remove claims that search hosts are blocked. Exercise as a routing regression. |
| Background Web-connector PDF freshness | Retain unchanged; it is not the LLM `open_url` PDF path. |
| Local embedding shim | Retain the isolation prerequisite's explicit host-capable HTTP/HTTPS proxy implementation unchanged. |
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
- obsolete component-specific policy ports, service names, and health
  dependencies replaced by the neutral implementation;
- `VALKEY_IMAGE`, SearXNG Valkey service/network/volume/dependency;
- inference/indexing model-server services and log volumes from full mode;
- SearXNG and, where validated, Teep socat-publisher services; host WebUI/doc
  publishers are already absent in the prerequisite baseline;
- `crw-image-ready`, `cdp-shim-image-ready`, and `cdp-shim-build` targets;
- CRW/CDP-shim prerequisites from `up-lite`, `up-full`, `upgrade`, and Python
  dependency upgrade flows; and
- `crw-service-gateway` from the stack-owned helper `NO_PROXY` list.

Add or rename:

- `ONYX_OBSCURA_CDP_URL=ws://obscura-cdp-gateway:9222/devtools/browser`;
- `SEARXNG_OBSCURA_CDP_URL=ws://obscura:9222/devtools/browser`;
- the generalized document byte/parser settings above;
- stack-owned CDP wait/timeout/trace settings, not user-facing sample options;
- stack-owned SearXNG `_obscura.py` scheduler settings for per-provider
  concurrency `1`, rate `0.33`, queue timeout, and the characterized jitter
  profile, plus an explicit `GRANIAN_WORKERS=1`;
- neutral restricted-egress policy variables;
- one public-only policy namespace with generic Onyx, browser, and optional
  executor listeners, one separate host-capable Onyx policy namespace, and the
  prerequisite's distinct public and host route brokers;
- distinct fixed browser and optional executor bridge URLs and policy-side
  networks;
- direct loopback-bound SearXNG publication and the validated Teep publication
  in place of their remaining host publisher services;
- Makefile-selected Tailscale-enabled, VPN-autoheal, and Teep-routing layers;
- the state-clearer image/configuration and normalized cookie-clear setting;
- the derived SearXNG image pin and build target;
- an immutable fixed-bridge image pin, source/build provenance, build
  target, and image-readiness target; and
- the exact unmodified upstream Obscura image/ref that passed the capability
  gate.

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

### Workstream 1: Obscura Capability and State-Clearing Prerequisites

1. Add the byte-exact/body-state integration tests.
2. Test the exact unmodified pinned image; if it fails, block cutover until a
   compatible unmodified upstream release is available.
3. Implement the narrow CDP-based periodic state-clearer sidecar.
4. Pin the exact upstream image/ref that passed and document provenance.
5. Validate the lack of real browser contexts, stable fingerprint, periodic
   clearing, proxy, private-target, body-memory, and health behavior.

Do not proceed to production cutover if the mislabeled-PDF behavior is
ambiguous or can silently feed altered bytes to the parser, or if
unavailable-body states remain ambiguous. The accepted current limitation is a
loud rejection/lack of byte-exact support for misleading text MIME, not silent
corruption.

### Workstream 2: Neutral Egress Proxy

1. Move the generic policy proxy out of `crw/`.
2. Rename configuration without compatibility aliases.
3. Delete search-host classification and all named modes.
4. Preserve the prerequisite host-capable namespace/listener and host broker,
   extend or atomically rename the prerequisite public-only policy service with
   separate browser/executor listener networks, and retain its public broker so
   host/internal exceptions remain structurally unavailable. Remove the legacy
   browser/executor policy services rather than creating another public process.
5. Preserve resolver selection, internal blocklists, request framing, upstream
   proxy handling, HTTP policy, and redaction.
6. Use one public-only policy namespace with generic-Onyx/browser/executor
   listeners and one separate host-capable namespace; retain all four distinct
   bridges/upstreams and both route brokers.
7. Prove no removed service, port, policy env, or search-block message remains.

### Workstream 3: Shared CDP Client

1. Implement URL syntax/literal validation without target DNS.
2. Implement explicit navigation/event/body lifecycle.
3. Implement HTML and raw-body modes, including loud rejection when the pinned
   misleading-text MIME conversion prevents proof of byte identity.
4. Implement actual-byte limits and cleanup.
5. Implement typed failures and safe diagnostics.
6. Test against a fake CDP protocol and the pinned live Obscura image.

### Workstream 4: Onyx Patch

1. Add strict source-shape checks.
2. Replace `OnyxWebCrawler` transport with the shared client.
3. Add content dispatch, byte-exact PDF handling for binary-classified bodies,
   and typed rejection for unprovable misleading-text bodies.
4. Add the networkless Onyx parser service, Unix FD-passing protocol, worker
   isolation, and resource limits.
5. Propagate byte and character settings to every imported binding.
6. Activate in lite and full bootstraps.
7. Remove the local-Chromium fallback from this provider only.

### Workstream 5: SearXNG Direct Engines

1. Build the pinned Playwright-client-only SearXNG image.
2. Convert custom engines to the offline/direct contract.
3. Refactor pure DOM parsers and rename `_crw.py` to `_obscura.py`.
4. Add the process-wide per-provider scheduler, monotonic interval/jitter,
   bounded queue, and cancellation-safe lease release.
5. Patch the pinned SearXNG outer timeout for cooperative direct-engine
   cancellation and prove no target/thread/task/lease survives it.
6. Set and validate one Granian worker while retaining normal blocking-thread
   concurrency.
7. Preserve error/suspension/round-robin/scoring behavior.
8. Remove CRW API settings and outgoing HTTP assumptions.
9. Add CDP-, scheduler-, and cancellation-aware readiness.

### Workstream 6: Atomic Compose Cutover

1. Add the Onyx CDP gateway between prerequisite `onyx-backend` and
   `obscura-control`.
2. Attach SearXNG to Obscura control.
3. Add the networkless document parser, state clearer, private Unix-socket
   volume, and private atomic clearer-state volume to lite and full API and
   SearXNG services with the documented read/write modes.
4. Retain both prerequisite Onyx bridges and route brokers, consolidate the
   generic Onyx listener into the public-only policy namespace, preserve the
   separate host-capable namespace, and retain separate browser and optional
   executor bridges/listeners/upstreams.
5. Replace the remaining SearXNG and validated Teep host publisher containers
   with loopback-bound publications and validate Docker and Podman behavior.
6. Remove Valkey and the full-mode model-server containers; preserve the
   prerequisite's conditional Tailscale and VPN-autoheal layers.
7. Apply the revised health dependencies without reintroducing direct Myst or
   shared-namespace dependencies on Onyx application services.
8. Remove all CRW/shim services, networks, secrets, pins, and build targets.
9. Render and inspect effective lite/full Compose models and service counts
   relative to the completed isolation baseline.
10. Start each routing-mode matrix from a clean supported state.

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
- List the networkless Onyx document parser and narrow state clearer, and
  explain why neither is a downloader or egress path.
- Explain that only custom SearXNG engines receive per-provider scheduling;
  `open_url`, executors, and helpers may contact public search engines
  directly and unscheduled.
- Update service counts and optional-feature instructions for removed Valkey,
  model servers, remaining host publishers, and separate restricted-component
  egress bridges. Use the implemented isolation topology
  as the service-count baseline.
- Preserve local-RAG, executor, upstream proxy, VPN, and Tailscale guidance.

### `docs/request_handling.md`

Rewrite the current CRW-centric diagrams and prose. Document:

- Onyx Web Crawler -> direct Obscura HTML/PDF/raw flow;
- SearXNG offline/direct custom engines;
- exact navigation/body lifecycle;
- MIME/URL/magic dispatch;
- the pinned Obscura UTF-conversion limitation for text-classified bodies,
  including binary content mislabeled as text, absent `Content-Type`, and
  non-UTF-8 raw text; the resulting lack of byte-identity guarantee; and typed
  rejection where binary identity or charset-preserving bytes are required;
- Unix FD handoff to the networkless Onyx document parser and its error/resource
  semantics;
- waits, default context, stealth, cookie clearing, rate controls, trace
  redaction, and the accepted shared CDP authority of Onyx, SearXNG, and the
  state clearer;
- uniform public-destination policy with no search-host special case, including
  the distinction between SearXNG-scheduled provider navigation and
  unscheduled `open_url`/executor/helper traffic;
- byte versus character limits;
- expected failures and no-direct-fallback behavior;
- one main navigation versus redirects/subresources;
- unchanged full-mode local document fetch/parsing.

Delete CRW API, prefetch, validation DNS, Firecrawl configuration, and CDP-shim
sections rather than retaining a historical appendix.

### `docs/vpn_routing_and_proxies.md`

- Replace CRW/prefetch topology and service tables with the CDP gateway and
  direct SearXNG client.
- Update all network attachments, proxy policies, health URLs, readiness edges,
  and `NO_PROXY` ownership.
- Replace the policy-mode matrix with one public-only policy namespace for
  generic Onyx, browser, and executor listeners plus one host-capable policy
  namespace and the prerequisite's two route brokers; document all separate
  caller bridges, broker links, and upstreams.
- Delete search-engine destination blocking and document the accepted direct
  executor/helper behavior and the restricted-component policy failure domain.
- Document the parser's `network_mode: none` and private Unix IPC readiness
  separately from VPN/egress dependencies.
- Preserve the full VPN/upstream/no-VPN routing and DNS matrix.
- Explain that only the internal Obscura service name uses Docker DNS; target
  DNS occurs at the selected final hop.
- Update autoheal/reconnection validation and failure examples, including the
  absence of an autoheal service in no-VPN mode.
- Preserve the prerequisite's isolated Onyx ingress/publication and conditional
  Tailscale design; document only the remaining Teep layer changes.

### `docs/internal_network_security.md`

- Remove CRW and validation-DNS reachability sections.
- Document CDP client reachability, CDP gateway risk, Obscura isolation,
  SearXNG's control-network attachment, and the explicit lack of isolation
  among Onyx, SearXNG, and the state clearer inside their shared CDP trust
  domain.
- Document separate browser and executor fixed bridges alongside the two
  prerequisite Onyx bridges, including their exact network pairs and residual
  pivot/failure domains.
- Document parser socket peer checks, FD/size validation, container hardening,
  and its lack of network attachment.
- Update canonical Docker/Podman/internal hostname coverage.
- Document redirect resolution and the no-target-Docker-DNS property.
- Explain the narrow Onyx SSRF patch interaction and final-hop authority.
- Add the body-buffering and large-document residual memory risks.
- Update reachability tables for removed Valkey, host publishers, model
  servers, and conditional Tailscale/autoheal services.

### `docs/onyx_patch_info.md`

- Add the strict direct-Obscura `OnyxWebCrawler` patch, content dispatch, document
  parser boundary, and limit propagation.
- Describe the parser protocol/worker implementation and strict version check.
- Clarify that helper proxy routing remains for other helper downloads and
  local Chromium users through the prerequisite's public-only Onyx bridge and
  listener.
- Update the SearXNG patch section for offline direct engines and derived image.
- Update code-interpreter capability text: network-enabled executors may reach
  public search engines through the restricted-component policy, while internal/private
  destinations and direct sockets remain unavailable.
- Delete CRW/CDP-shim patch and configuration descriptions.
- Retain and accurately scope every unaffected patch.

### `docs/onyx_patches_upgrade.md`

- Update the current version table and service/image map.
- Add source-shape audit anchors for `OnyxWebCrawler`, parser helpers,
  Playwright `1.58.0`, SearXNG offline processors, Obscura body/IO/navigation
  methods, the exact `is_text_like_content_type` predicate, and cookie
  clearing.
- Add the Unix parser protocol version, Onyx parser import/signature, container
  hardening, and FD-passing capability checks.
- Add binary-classified byte-exact PDF/body, text-classification limitation,
  and one-fetch upgrade tests.
- Replace CRW/CDP-shim/validation-DNS upgrade steps with deletion assertions so
  they cannot accidentally return.
- Cover the new single-worker SearXNG scheduler/cancellation patch and image
  build, neutral egress implementation with two isolated policy namespaces and
  two route-broker classes, separate bridge hardening, and absence of
  named-policy/search-block modes.
- Revalidate clearer-state schema/clock-domain assumptions and optional memfd
  sealing across supported Docker, Podman, and Desktop platforms.
- Revalidate executor environment injection and LLM-facing capability text
  against the search-allowed restricted-component policy.

### `docs/local_docs_rag_search.md`

- State explicitly that local document Web-connector ingestion and PDF
  freshness are unchanged, while helper/local-Chromium external routing keeps
  the same semantics through the public-only Onyx listener.
- Preserve the prerequisite's selected doc-drop fetch/display and host
  publication design, and remove the unused upstream model-server services
  from the documented full-mode component inventory.
- Prevent readers from assuming local doc-drop URLs are sent through Obscura.
- Retest and update only references made stale by component-name changes.

### `.env.wrapper.example`

- Add only `ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB` for the new document controls,
  with the memory warning. Keep parser concurrency, timeout, memory, CDP URLs,
  waits, traces, scheduler internals, and bridge/policy settings as validated
  stack-owned defaults rather than expanding the sample surface.
- Rename the cookie interval to
  `OBSCURA_BROWSER_CLEAR_COOKIES_INTERVAL_SECONDS`, document the lack of
  first-party isolation and the periodic-clear tradeoff, and do not retain a
  compatibility alias for the old name. Remove the old name's consumer; a stale
  value is simply ignored.
- Remove `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH` and
  `OBSCURA_BROWSER_WAIT_UNTIL_WEB`; the direct clients own explicit,
  call-site-specific waits and the CDP-shim-wide overrides no longer have a
  coherent meaning. Remove their consumers; stale values are simply ignored.
- Retain `SEARXNG_ROUND_ROBIN`, but replace its CRW wording with the direct
  Obscura provider-selection semantics; it remains a real operator choice, not
  an egress-policy mode.
- Retain `ONYX_CODE_INTERPRETER_ENABLE_NETWORK`, but state that enabled
  executors may reach public search engines while host/private targets remain
  denied.
- Retain the SearXNG/Teep host-port and Teep routing switches, but replace
  legacy socat/proxy-bridge descriptions with the final direct-publication and
  fixed-gateway topology.
- Remove CRW, CDP-shim, search-block-host, and policy-mode user settings.
- Consume the prerequisite's `EGRESS_UPSTREAM_PROXY_URL` and
  `EGRESS_ALLOW_HTTP_URLS` names, its `EGRESS_ALLOW_RFC1918` option, and its
  fixed Onyx SSRF default; do not rename, alias, or re-expose the removed SSRF
  seed flags here.
- Do not expose stack-owned CDP URLs or helper `NO_PROXY` values.
- Keep immutable image/source pins in `stack.versions.env`.

### `AGENTS.md`

- Replace CRW runtime shape, key locations, and invariants with direct
  Onyx/SearXNG -> Obscura paths.
- Add the new shared client, neutral egress proxy, and derived SearXNG image to
  key locations, along with the networkless parser service/protocol module.
- Replace instructions that require blocking executor search traffic with the
  uniform public-destination policy, SearXNG-only scheduler, and separate
  browser/executor bridge invariants.
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
- Update the implemented Onyx network-isolation plan with a supersession note
  for its transitional CRW topology and point its final ownership sections to
  the deployed Direct Obscura architecture.
- After deployment and validation, change this file's status to implemented,
  update its normative-doc links, and move it to
  `docs/plans/implemented/obscura_direct.md`.

## Test Plan

### Unit Test Layout

Add focused `unittest` modules under [`tests`](../../tests):

- `test_obscura_cdp_client.py`;
- `test_onyx_obscura_crawler_patch.py`;
- `test_onyx_open_url_document_parser.py`;
- `test_searxng_obscura_engines.py`;
- `test_obscura_direct_compose.py` or an equivalent structured Compose test;
- renamed/updated `test_restricted_egress_proxy.py`; and
- integration tests that run against the exact unmodified Obscura image pin.

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
- synchronous Onyx and SearXNG adapters, per-thread event-loop ownership,
  cancellation before/after task registration, and deterministic loop closure;
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
- PDF detection by final URL, MIME, and `%PDF-` magic independently, while
  parsing only binary-classified bodies and rejecting text-classified or
  missing-type PDF signals as byte-identity unavailable;
- misleading PDF MIME produces a characterized byte-identity-unavailable or
  unsupported result and never sends altered bytes to the PDF parser; HTML
  error pages at a `.pdf` URL remain HTML/status failures;
- plain text/JSON/XML/source decoding and whitespace;
- the same document cap at one byte below, exactly at, and one byte above for
  PDF and every accepted raw-text format;
- explicit DOCX/PPTX/XLSX/EML/EPUB/image rejection with no generic-file-parser
  or API-process fallback;
- unsupported binary, corrupt/encrypted/image-only PDF, timeout, and resource
  limit behavior;
- bounded document parse concurrency and subprocess reap;
- parser protocol versioning, `SO_PEERCRED`, sealed-memfd verification where
  supported, the validated bounded-unsealed copy path where not supported,
  sender descriptor closure, independent pre/post-copy `fstat` and EOF/size
  enforcement, proof that parsing never reads the shared descriptor,
  frame/output limits, and rejection of missing/extra descriptors;
- parser service timeout/disconnect cleanup and absence of an API-process
  parser fallback;
- per-URL failure isolation and stable output order;
- no requests/local-Chromium/Firecrawl/CRW/CLI fallback; and
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
- process-wide scheduler sharing across all engine modules;
- concurrent same-provider attempts never overlap and observe the configured
  minimum start interval;
- different providers may navigate concurrently;
- queue timeout plus release after success, exception, timeout, and caller
  cancellation;
- outer-timeout cancellation before task registration, while queued, during
  navigation/DOM read, and during cleanup, with no surviving thread, task,
  target, connection, lease, or late result mutation;
- cleanup-grace breach marks readiness unhealthy and fails loudly;
- redirect attempts remain charged to the selected provider bucket;
- startup rejection of `GRANIAN_WORKERS` other than `1`;
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
- HTTP port policy;
- successful public search-host CONNECT/forward behavior through public Onyx,
  browser, and executor listeners;
- absence of `EGRESS_PROXY_POLICY`, search-host lists, and search-specific
  denial messages;
- host-capable Onyx trusted-destination success and every public-listener denial,
  including attempts to obtain the exception with headers, targets, or proxy
  chaining;
- separation of the public and host policy namespaces and broker networks,
  repeated broker validation, and `EGRESS_ALLOW_RFC1918` behavior inherited
  from the isolation prerequisite;
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
- PDF with no `Content-Type`;
- UTF-8 and declared non-UTF-8 text-classified raw responses;
- HTML error at a `.pdf` URL;
- plain text, JSON, XML, YAML, representative source text, and unsupported
  office/archive/image/binary bodies;
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
- verify byte hashes for binary-classified bodies and characterize/reject the
  pinned misleading-text UTF conversion without claiming byte identity;
- verify missing-type PDF rejection and the supported/unsupported raw-text
  conversion cases without claiming charset recovery after storage;
- verify rendered DOM after JS execution;
- verify explicit wait modes;
- verify cookie clear scheduling, one-clearer ownership, and the documented
  mid-navigation clear behavior;
- verify atomic state publication, permissions, monotonic clock-domain/boot-ID
  agreement, generation rollback/future/stale/malformed rejection, and recovery
  only after a later successful clear;
- verify concurrent CDP navigation remains supported without a per-domain
  scheduler or false browser-context isolation claim;
- verify browser proxy enforcement and no direct route;
- verify restart/reconnect cleanup;
- measure RSS at 50 MiB and at one documented above-50 value with concurrency
  1 and the supported maximum;
- demonstrate and record that the initial full-response allocation can exceed
  the retained-body/client cap before rejection, while CDP chunking only bounds
  the downstream read loop; and
- record expected peak-memory guidance without turning a platform-specific RSS
  number into a brittle unit assertion.

### Compose and Static Security Tests

Parse effective Compose models structurally and assert:

- `.env.wrapper.example` exposes the document-size and renamed cookie controls,
  not the removed CDP-shim wait names or stack-owned parser/CDP/scheduler
  internals;
- no CRW/CDP-shim service, image, secret, env, health dependency, network, or
  host port remains;
- only the expected clients can reach Obscura control/CDP;
- Obscura has no direct trusted-namespace or general external network;
- the CDP gateway exposes only the intended port and no host binding;
- SearXNG has Obscura-control and service networks but no Valkey or direct
  egress, sets `GRANIAN_WORKERS=1`, and is not scaled above one replica;
- Onyx has the gateway and helper paths required by each mode;
- exactly one public-only policy service/namespace with
  generic-Onyx/browser/optional-executor listeners, one separate host-capable
  policy service/namespace, and the two prerequisite route brokers exist, with
  no legacy component policy services or ports;
- neither policy service uses `network_mode: service:netns-holder`; each has
  only its listener networks and matching broker network, while each broker
  accepts only its expected policy peer and repeats destination validation;
- separate fixed browser and optional executor bridges exist and neither is
  attached to the other's component or policy-side network;
- both prerequisite Onyx bridges still exist on their own client/upstream
  networks and are not attached to or reachable through either restricted-
  component bridge;
- each component bridge has only the fixed forward command, its component
  network, and its matching policy-side network; it has no shell,
  secrets, mounts, host ports, capabilities, writable filesystem, packet
  forwarding, or control listener;
- the fixed-bridge image has the expected immutable pin and build
  provenance, image inspection finds no common shell or package manager, and
  its fixed health-probe subcommand proves the complete bridge-policy-broker
  denial path without accepting an arbitrary target;
- renderer and executor component networks remain distinct, no restricted
  application is attached to a policy-side network, and negative tests show
  applications cannot route to one another through either bridge;
- generic Onyx proxy variables point to the public-only Onyx bridge, explicit
  host-capable clients point to the host bridge, and exact host/opt-in RFC1918
  exceptions exist only in the host-capable policy namespace and broker;
- the optional executor overlay adds its component network, fixed bridge,
  policy-side network, and listener configuration;
- no policy mode or search-host blocklist environment remains;
- the document parser has `network_mode: none`, no ports/networks/secrets/proxies,
  the expected hardening/resource limits, and a socket volume shared only with
  the API service;
- the state clearer has only `obscura-control`, no egress/proxy/secret/host
  attachment, exactly one replica, and the normalized interval setting; its
  private state volume is writable only by the clearer and read-only in Onyx
  and SearXNG, and stale/malformed state blocks new navigations;
- Valkey, inference/indexing model servers, and the remaining SearXNG/validated
  Teep host publisher services are absent; SearXNG and any supported Teep
  diagnostic ports use the exact publication design specified above, while
  prerequisite WebUI/doc publication remains unchanged;
- disabled Tailscale and no-VPN models omit Tailscale and autoheal services,
  respectively;
- effective service-count reductions match the inventory above;
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
| full | supported | upstream proxy | browser and helper routes obey their respective public-only policies; local services stay local |

For each practical matrix entry:

- run `make up-lite` or `make up-full`;
- inspect `make ps-lite` or `make ps-full`;
- exercise a real custom `web_search` query;
- exercise `open_url` on HTML, JS-rendered HTML, PDF, text, and an oversized
  response;
- exercise `open_url` on a public search-engine URL and verify any failure is
  the real rendered upstream status/challenge, not a local egress-policy 403;
- issue concurrent `web_search` requests for one provider and prove the
  SearXNG scheduler serializes them; concurrently issue `open_url` to the same
  provider and prove it is intentionally outside that scheduler;
- when executor networking is enabled, contact a public search engine from an
  executor and verify it reaches the selected route rather than receiving a
  local search-policy 403;
- inspect SearXNG, Obscura, the relevant policy processes, gateway, and Onyx logs;
- verify target DNS did not reach Docker's embedded resolver;
- verify public egress identity matches the chosen route;
- attempt representative internal/Docker/metadata destinations and redirects;
- interrupt/recover VPN and Obscura; and
- verify no package installation occurs after containers start.

### Regression Tests Outside the New Path

- Full-mode doc-drop crawling, PDF freshness/reindexing, embedding shim, and
  `internal_search`.
- Code-interpreter network disabled/enabled, restricted-component policy routing, public
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

1. Verify that `onyx_network_isolation.md` is implemented and its acceptance
   suite passes. Do not begin this migration on the shared-namespace topology.
2. Land characterization tests and Obscura prerequisites first without
   changing the active provider.
3. Build/pin the derived SearXNG image and verify the exact unmodified Obscura
   pin.
4. Implement shared client, Onyx patch, direct SearXNG engines, and neutral
   policy changes behind the atomic Compose branch.
5. Run unit, integration, static, memory, and routing matrix tests.
6. Tell existing operators to select **Onyx Web Crawler** instead of the saved
   Firecrawl provider before/at upgrade.
7. Stop the old stack and start the complete new topology. Do not run old CRW
   and new direct engines together.
8. Verify health, one-fetch counters, search quality, PDF hashes, DNS route,
   egress identity, and denial cases.
9. Remove old images/volumes only through normal documented cleanup after
   rollback confidence; no destructive cleanup is part of startup.
10. Complete documentation and move this plan to implemented status.

Rollback is the whole Direct Obscura request-path change: restore its prior
Compose, pins, patches, and documented Firecrawl/CRW configuration together,
but retain the already-completed Onyx application isolation topology. Do not
partially restore only CRW or only the CDP shim, and do not move Onyx back into
the Myst namespace as part of request-path rollback.

## Acceptance Criteria

The plan is complete only when all of the following are true:

- Onyx built-in `OnyxWebCrawler` uses one Obscura main-document navigation for
  HTML, PDF, and supported raw content.
- Byte hashes prove PDF retrieval uses the same navigation for correctly typed
  and generic binary MIME cases. Misleading text and missing MIME are
  characterized as pinned text-classification/UTF-conversion limitations,
  never claimed byte-exact, and rejected whenever PDF byte identity cannot be
  proven. Non-UTF-8 text-classified raw content is likewise not described as a
  recoverable original-byte stream.
- The configurable document limit defaults to 50 MiB, applies equally to PDF
  and every accepted non-HTML raw format, accepts tested values above 50 MiB
  in both Onyx and Obscura, and documents memory cost.
- Documentation and memory tests state that the pinned Obscura network path
  fully buffers a response before retained-body/client rejection; CDP chunking
  does not make the initial allocation streaming or bounded by the configured
  cap.
- Actual decoded body bytes, not `Content-Length`, enforce the limit for normal,
  chunked, compressed, absent-length, and false-length responses.
- The complete document parser runs only in the networkless hardened service, uses
  the versioned Unix FD-passing protocol, and is timed, memory-bounded, and
  concurrency-bounded with no API-process fallback.
- Every custom SearXNG engine uses Playwright/raw CDP directly through the
  offline callback and retains current parsing/retry/suspension/scoring
  behavior.
- One explicitly configured Granian process enforces one active navigation and
  the minimum start interval per custom SearXNG provider across its request
  threads. The patched SearXNG outer deadline cooperatively cancels timed-out
  direct engines and leaves no target, task, thread, result write, or scheduler
  lease. `open_url`, helpers, and executors are documented and tested as
  intentionally outside that schedule.
- No user target hostname is resolved by Onyx/SearXNG through Docker DNS;
  final-hop resolver selection matches VPN/upstream/no-VPN mode.
- Internal/Docker/Podman/metadata names and non-public resolved addresses are
  denied at the relevant policy layers, including redirects.
- CRW, validation DNS, CRW gateways/bridges/prefetch policy, and CDP shim are
  absent from runtime, builds, secrets, networks, health dependencies, and
  current documentation.
- The generic restricted-egress implementation is neutrally named and runs as
  one public-only policy namespace with generic-Onyx/browser/executor listeners
  plus one host-capable policy namespace and two distinct route brokers. Exact
  host/opt-in RFC1918 exceptions exist only in the host-capable namespace and
  broker; neither policy process shares `netns-holder`.
- Public search engines are not special-cased by egress policy; renderer,
  helper, and executor peers receive the same allow/deny result for the same
  public destination.
- Legacy component-specific policy services and named policy modes are absent.
  Browser and executor use separate hardened fixed bridges and cannot reach
  one another or either prerequisite Onyx bridge/listener.
- SearXNG Valkey, full-mode inference/indexing model servers, and the remaining
  SearXNG/validated Teep publisher containers are absent. WebUI/doc publisher
  removal belongs to the prerequisite baseline. Disabled Tailscale and no-VPN
  models omit their Tailscale/autoheal services, respectively.
- Periodic cookie clearing, stable global fingerprint, explicit waits,
  diagnostics, and challenge visibility have named owners and passing tests;
  documentation explicitly states that CDP provides no first-party context
  isolation and that Onyx, SearXNG, and the state clearer intentionally share
  full CDP authority at the pinned version. A stale clearer state blocks new
  navigation until a successful clear recovers it.
- VPN enabled/disabled/upstream-proxy startup and reconnection work without a
  direct fallback or autoheal restart storm.
- No runtime patch, shim, entrypoint, or health check installs packages.
- Lite and full `open_url`, all supported search engines, full local RAG,
  executor modes, and remaining helper
  downloads pass regressions.
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
| byte-exact binary-classified main-resource body | Obscura body store/IO protocol; misleading/missing text classification and non-UTF-8 text conversion are explicit documented limitations with typed rejection where identity is required |
| document parsing and LLM character budgets | Onyx |
| document parser resource isolation | networkless Onyx parser service and bounded worker |
| search URL construction and DOM parsing | custom SearXNG engines |
| explicit navigation waits | direct client call sites |
| browser fingerprint/proxy | Obscura |
| periodic shared-cookie clearing | CDP state-clearer sidecar in the accepted shared CDP trust domain plus private readiness-state volume |
| per-provider custom-search rate control | single-process SearXNG `_obscura.py` scheduler |
| SearXNG timeout cancellation | patched outer orchestrator plus direct-client attempt registry |
| unscheduled `open_url`/helper/executor navigation | accepted caller behavior with visible upstream responses |
| uniform public policy and host/opt-in RFC1918 exceptions | neutral implementation deployed as separate public-only and host-capable policy namespaces plus matching route brokers |
| renderer/executor fixed egress forwarding | separate hardened bridges to separate public-only listeners |
| Onyx public/host-capable forwarding | prerequisite separate public-only and host-capable bridges |
| internal destination and redirect denial | request-policy plus matching authoritative route broker, with Obscura defense in depth |
| target DNS routing | selected route broker/final route, with documented host-capable LAN-mode system-DNS tradeoff |
| CDP reachability | internal networks and narrow gateway |
| strict upgrade/source validation | runtime patches, image builds, and upgrade tests |

That ownership model is simpler because it eliminates translation layers, not
because it discards behavior. The migration succeeds only when the smaller
topology has evidence for the same privacy, security, document, search, and
operational properties as the current stack.
