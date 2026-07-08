# Restricted Egress Network Plan

## Goal

Implement one restricted-egress model for every stack component that can give
the Onyx agent external or internal network access. The model should replace
broad shared-namespace reachability with narrow component networks and explicit
egress/service gateways.

This plan covers code-interpreter executors, Obscura, CRW, and SearXNG
together. Code-interpreter executors are the highest-risk first delivery
target because they run LLM-generated code, but the restricted-egress
architecture is stack-wide.

The intended end state:

- agent-controlled or attacker-influenced components do not share the broad
  `netns-holder` namespace;
- each component can reach only the local ports required for its job;
- all internet egress goes through an explicit final-hop proxy policy;
- the final-hop proxy policy preserves the operator's selected routing mode;
- `ONYX_AGENT_OUTBOUND_PROXY_URL` remains an optional upstream proxy for
  internet egress, not a sandbox switch;
- `MYST_VPN_ENABLED=true` and `MYST_VPN_ENABLED=false` keep their current
  explicit final-hop meanings;
- host-resident upstream proxies, such as
  `socks5h://host.docker.internal:9150`, remain supported only as a
  final-hop-proxy exception, not as general host access for application
  components.

## Planning Scope

Implement the stack-wide egress model as one architecture, even when runtime
implementation is staged. Every component should answer the same questions:

- what network is this component allowed to attach to?
- which local gateway port may it use for internet egress?
- which final-hop proxy policy handles destination validation?
- which peer service ports, if any, are reachable directly?
- how does the selected VPN/proxy/no-VPN routing matrix remain intact?

This does not require one giant patch. It requires one architecture, one naming
scheme, one proxy-policy vocabulary, and staged compose work that is easy to
validate independently.

Use this plan with the subsystem docs, not as a replacement for them. The
request path details live in [Request handling](../request_handling.md), the
VPN/proxy routing matrix lives in
[VPN routing and proxies](../vpn_routing_and_proxies.md), current internal
reachability risks live in
[Internal network security](../internal_network_security.md), runtime patch
behavior lives in [Onyx patch information](../onyx_patch_info.md), and upgrade
checks live in [Onyx wrapper patches](../onyx_patches_upgrade.md).

## Version Scope

This plan is current for the committed pins in
[`stack.versions.env`](../../stack.versions.env). Re-check the assumptions in
this file when any of these pins or their equivalent local overrides change.

| Component | Current pin | Why it matters for this plan |
| --- | --- | --- |
| Onyx application | `ONYX_IMAGE_TAG=v4.1.9` | Owns API/background/web service shape, tool prompts, SSRF settings, Firecrawl/Open URL wiring, and Compose references. |
| Code interpreter | `CODE_INTERPRETER_IMAGE_TAG=0.4.4` | Owns executor pod creation, Docker network selection, and proxy env injection points. |
| SearXNG | `SEARXNG_IMAGE_TAG=2026.6.26-f8ffbf36f` | Owns custom engine loading, outgoing proxy settings, and search app network needs. |
| CRW | `CRW_IMAGE=ghcr.io/us/crw:0.21.1` | Owns scrape/search endpoints, HTTP prefetch behavior, CDP renderer settings, and peer assumptions. |
| Obscura | `OBSCURA_IMAGE=h4ckf0r0day/obscura:0.1.9` | Owns browser rendering, `--proxy` behavior, private-target blocking, and CDP browser egress. |
| Teep | `TEEP_REF=6413fe0547b449e67f7296986fe8b8ffbc9bbcd2` | Out of scope for restricted egress unless provider routing or namespace placement changes. |
| Mysterium | `MYST_IMAGE=mysteriumnetwork/myst:docker_host_fixes_with_logs` | Owns the shared routing namespace, VPN connection, and kill-switch behavior behind final-hop policy. |
| Network/support images | `NETNS_HOLDER_IMAGE=alpine:3.20`, `PYTHON_SLIM_IMAGE=python:3.12-slim-bookworm`, `PYTHON_ALPINE_IMAGE=python:3.12-alpine`, `SOCAT_IMAGE=alpine/socat:1.8.0.3`, `TAILSCALE_IMAGE=tailscale/tailscale:stable`, `AUTOHEAL_IMAGE=willfarrell/autoheal:latest` | Shape namespace ownership, bridge/proxy implementation choices, host diagnostic bridges, optional Tailscale routing, and health behavior. |
| Full-mode data/search support | `MINIO_IMAGE=minio/minio:RELEASE.2025-07-23T15-54-02Z-cpuv1`, `VALKEY_IMAGE=docker.io/valkey/valkey:9-alpine` | Relevant when validating full-mode data services, SearXNG cache reachability, or local document RAG placement. |

## Non-Goals

- Do not add a transparent firewall or general-purpose network sandbox in this
  phase. The primary boundary is Docker network placement plus narrow gateways.
- Do not preserve broad shared-namespace executor networking as an implicit
  compatibility mode. If needed for trusted debugging, add a separate explicit
  opt-in with clear warnings.
- Do not make `ONYX_AGENT_OUTBOUND_PROXY_URL` required.
- Do not rely on `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, or `NO_PROXY` as a
  security boundary. They are routing hints.
- Do not expose direct CDP, Obscura MCP, data stores, Docker socket access, or
  host gateway access to generated code through the restricted networks.
- Do not weaken the existing request-path properties: search-engine prefetch
  blocking, private-target blocking, anti-bot visibility, rate limiting,
  explicit no-VPN mode, and fail-closed behavior must remain intact.

## Current State

The base stack has a stable shared namespace owned by `netns-holder`.
VPN-routed services join it with:

```yaml
network_mode: "service:netns-holder"
```

In that namespace, many useful internal listeners share loopback and Docker
service aliases: Onyx API/Web, CRW, SearXNG, Obscura CDP/MCP, CDP shim,
doc-drop, embedding shim, code-interpreter, and Mysterium control surfaces.
For the current namespace topology and optional routing layers, see
[VPN routing and proxies](../vpn_routing_and_proxies.md). For tested
reachability and the current security gaps, see
[Internal network security](../internal_network_security.md).

The current web request path is intentionally routed through wrapper shims:

- SearXNG custom engines POST to CRW instead of fetching search engines
  directly.
- CRW sends raw HTTP prefetch through `prefetch-blocking-proxy` on
  `127.0.0.1:3128`.
- CRW reaches that proxy today with process proxy variables similar to:

  ```env
  HTTP_PROXY=http://127.0.0.1:3128
  HTTPS_PROXY=http://127.0.0.1:3128
  NO_PROXY=127.0.0.1,localhost,::1
  ```

- `prefetch-blocking-proxy` blocks search-engine prefetches, private/internal
  targets, and plain HTTP unless explicitly allowed.
- CRW escalates search-engine pages to CDP/Obscura.
- Obscura blocks private-network targets by default.

The full `web_search` and `open_url` chains, including CRW, SearXNG,
prefetch-blocking proxy, CDP shim, Obscura, rate limiting, and anti-bot
behavior, are documented in [Request handling](../request_handling.md).

The largest current gap is code-interpreter network enablement. When
`ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`, the Makefile layers
`docker-compose.code-interpreter-vpn.yml`, which sets:

```text
PYTHON_EXECUTOR_DOCKER_NETWORK=container:onyx-netns-holder-1
```

Executor pods then inherit the entire shared namespace. Generated code can
use raw sockets, ignore proxy variables, and reach internal services directly.

The broader request-path components also have unnecessary reachability today:
Obscura, CRW, and SearXNG all run in the shared namespace even though each
needs only a small set of peers and egress paths.

Current component configuration surfaces matter for the implementation:

- Obscura exposes explicit `--proxy` / `OBSCURA_PROXY` controls; do not treat
  `HTTP_PROXY` / `HTTPS_PROXY` as the browser enforcement mechanism.
- CRW has explicit proxy, CDP, and search URL settings, and its reqwest HTTP
  prefetch path can also use proxy environment variables.
- SearXNG should use native `outgoing.proxies` and per-engine `network`
  settings for intentional external features rather than process-level proxy
  environment variables.

The common principle is the same for every component: proxy settings are
routing inputs, while Docker network placement is the boundary.

## Target Model

Separate three roles and keep them named consistently.

### Restricted Component

A component that can be influenced by the agent, generated code, untrusted web
content, or URL inputs.

In scope:

- code-interpreter executor pods;
- Obscura browser renderer;
- CRW scraper/API;
- SearXNG search app.

Out of scope for this plan unless they become agent-facing network paths:

- Teep provider routing;
- Tailscale Funnel;
- Mysterium signup/funding flows;
- data stores and trusted control-plane services.

Full-mode local document RAG has its own doc-drop, Web connector, PDF
freshness, and embedding-shim constraints. If restricted-network work changes
doc-drop or embedding-shim placement, read
[Local docs RAG search](../local_docs_rag_search.md) first.

### Component Bridge

A small gateway on a restricted component network. It exposes one allowed local
port to one restricted component or component class.

Default bridge behavior should be boring:

```text
restricted component network :3128 -> selected final-hop proxy :3128
```

A bridge should not parse URLs, resolve target DNS, duplicate destination
classification, or implement proxy policy unless a component genuinely needs
component-specific logs, rate limits, path allowlists, or a distinct policy
failure domain.

For executor and CRW prefetch traffic, the expected upstream target is
`myst-client:3128`. `myst-client` is the default-network alias for the
`netns-holder` namespace owner, and connecting to that port reaches the
shared-namespace final-hop proxy. This bridge shape must not depend on
`docker-compose.proxy.yml`, because executor networking must work both with
and without an upstream proxy URL.

Bridge hardening:

- no host port publishing;
- no Docker socket;
- no writable host mounts;
- `read_only: true` where practical;
- `security_opt: ["no-new-privileges:true"]`;
- source-network filtering or interface binding so unrelated networks cannot
  use the listener;
- healthchecks that verify local readiness, not external internet reachability;
- fail closed if the upstream final-hop proxy is unavailable.

### Final-Hop Proxy Policy

A proxy inside the routing namespace that validates destinations and performs
the real outbound connection. This is where final egress routing belongs.

At minimum the stack needs these policy modes:

| Policy | Search hosts | Internal/private targets | Plain HTTP | Intended users |
| --- | --- | --- | --- | --- |
| `prefetch` | blocked | blocked | controlled by `ONYX_AGENT_ALLOW_HTTP_URLS` | CRW HTTP prefetch |
| `executor` | blocked | blocked | controlled by `ONYX_AGENT_ALLOW_HTTP_URLS` | code-interpreter executors |
| `browser` | allowed | blocked | controlled by `ONYX_AGENT_ALLOW_HTTP_URLS` | Obscura browser |
| `searxng-external` | allowed or allowlisted | blocked | controlled by `ONYX_AGENT_ALLOW_HTTP_URLS` | optional non-CRW SearXNG features |

The existing `prefetch-blocking-proxy` already implements most of the
`prefetch` and `executor` policy. Prefer extending that implementation with
explicit modes over copying similar proxy code into separate files.

Do not move final-hop proxies out of `network_mode: service:netns-holder` just
to attach them to restricted networks. Compose services using `network_mode`
cannot also declare ordinary `networks:` attachments, and moving the proxy out
would blur the final-hop routing boundary. Keep final-hop proxies in the
routing namespace and expose them through narrow bridges.

## Policy Preference Surface

The restricted-egress implementation must preserve the current user-facing
preference surface from `.env.wrapper.example`,
[Request handling](../request_handling.md), and
[VPN routing and proxies](../vpn_routing_and_proxies.md). Do not replace these
values with hardcoded per-mode defaults or new names unless the documentation
and example env file are updated in the same change.

Policy-enforcement preferences:

- `ONYX_AGENT_ALLOW_HTTP_URLS=false` is the stack-wide cleartext target URL
  default. When false, plain `http://` target URLs fail closed in the final-hop
  proxy policies and in the CDP shim's browser navigation path. When true,
  non-search plain HTTP targets may be forwarded through the selected
  final-hop policy after the same internal/private destination validation.
- `PREFETCH_BLOCK_HOSTS` is the configured search-host block list for the
  prefetch and executor policies. The browser policy must not use this list as
  a block list; it should use the corresponding host set only for search-aware
  browser behavior such as wait selection.
- `PREFETCH_BLOCK_INTERNAL_HOSTS` remains part of destination validation for
  final-hop proxy policies. It complements, rather than replaces, IP literal,
  single-label hostname, localhost, host-gateway, private-range, link-local,
  reserved, and non-global address checks.
- `ONYX_AGENT_OUTBOUND_PROXY_URL` controls only upstream final-hop proxying.
  Restricted components and executor pods should receive local bridge/proxy
  URLs, not this upstream URL directly.
- `ONYX_AGENT_HTTPS_PROXY_REQUIRE_TLS13` continues to apply to `https://`
  upstream proxy legs for proxy implementations that support it.

Executor-specific preferences:

- `ONYX_CODE_INTERPRETER_ENABLE_NETWORK` remains the user-visible switch that
  enables executor network access.
- `ONYX_AGENT_EXECUTOR_HTTP_PROXY_URL` and
  `ONYX_AGENT_EXECUTOR_NO_PROXY` are the executor injection inputs when
  executor networking is enabled. They must describe the restricted bridge
  endpoint and executor-local bypasses, not trusted stack service bypasses.

Browser-path preferences that must survive the network split:

- `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH`,
  `OBSCURA_BROWSER_WAIT_UNTIL_WEB`, and
  `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH_HOSTS` remain CDP shim controls for
  Obscura navigation timing. Do not bury them inside proxy policy.
- `OBSCURA_BROWSER_CLEAR_COOKIES_INTERVAL` remains the cookie retention
  control for the browser path.

SearXNG and Onyx application preferences remain separate from final-hop
destination policy:

- `SEARXNG_ROUND_ROBIN` controls CRW-backed provider scheduling, not network
  egress policy.
- `ONYX_SECURITY_SSRF_VALIDATE_OPEN_URL`,
  `ONYX_SECURITY_SSRF_ALLOW_PRIVATE_NETWORK`, and
  `ONYX_SECURITY_SSRF_ALLOW_LOOPBACK` seed Onyx's Admin security posture. They
  are not firewall rules for CRW, Obscura, SearXNG, executor pods, or the
  final-hop proxy policies.

## Routing Matrix

All policies must preserve the same four final-hop outcomes.

| `MYST_VPN_ENABLED` | `ONYX_AGENT_OUTBOUND_PROXY_URL` | Final outbound behavior |
| --- | --- | --- |
| `true` | empty | allowed requests leave from the Mysterium namespace |
| `true` | set | final-hop proxy connects to the upstream proxy through Mysterium |
| `false` | empty | allowed requests leave directly through Docker bridge egress by explicit no-VPN choice |
| `false` | set | final-hop proxy connects to the upstream proxy directly, without Mysterium |

Restricted components should not know this matrix directly. They should always
talk to their local bridge URL. The final-hop proxy decides whether its own
upstream connection goes through Mysterium, through `ONYX_AGENT_OUTBOUND_PROXY_URL`,
through both, or through neither.

`ONYX_AGENT_OUTBOUND_PROXY_URL` may be HTTP, HTTPS, or SOCKS. Restricted
components and executor pods should still see an ordinary local HTTP proxy
endpoint, so they do not need PySocks, socksio, or upstream-proxy-scheme
support.

For the current service-by-service `ONYX_AGENT_OUTBOUND_PROXY_URL` behavior,
the explicit no-VPN mode, and the optional service routing switches, see
[VPN routing and proxies](../vpn_routing_and_proxies.md).

## Host-Resident Upstream Proxies

Host proxy examples such as Tor Browser must remain supported:

```env
ONYX_AGENT_OUTBOUND_PROXY_URL=socks5h://host.docker.internal:9150
MYST_VPN_ALLOW_LAN_BYPASS=true
```

Required distinction:

- restricted components must not be able to connect to
  `host.docker.internal`, host gateway IPs, LAN/private ranges, or link-local
  metadata addresses directly;
- only the final-hop proxy that owns `ONYX_AGENT_OUTBOUND_PROXY_URL` may
  connect to the configured upstream proxy endpoint;
- target URL validation is unchanged: requests for host, LAN, private, or
  link-local targets are blocked;
- the current `MYST_VPN_ALLOW_LAN_BYPASS=true` mechanism is broader than the
  desired endpoint-scoped host-proxy allowance and should remain documented as
  a residual risk until replaced.

The desired end state is an endpoint-scoped exception for the configured
upstream proxy host and port. Until then, docs must make clear that
`MYST_VPN_ALLOW_LAN_BYPASS=true` is a broader LAN route exemption used for
host-resident upstream proxies, not general permission for restricted
components to reach host or LAN targets.

For the current host-proxy and LAN-bypass behavior, see
[VPN routing and proxies](../vpn_routing_and_proxies.md). For why host,
private, link-local, and internal targets remain security-sensitive even when
an upstream proxy is configured, see
[Internal network security](../internal_network_security.md).

## DNS Classification

Final-hop proxy destination classification should keep the current behavior:

- with no upstream proxy, resolve target hostnames locally and block any name
  that resolves to loopback, private/RFC1918, link-local, reserved, or other
  non-global addresses;
- with an upstream proxy, avoid local target DNS resolution so target DNS does
  not leak outside the configured proxy path.

That creates a deliberate residual risk in upstream-proxy mode: a
public-looking hostname might resolve to a private address at the upstream
proxy. The local final-hop proxy can still block IP literals, localhost names,
`host.docker.internal`, single-label Docker-style names, and other syntactic
private-target forms without opening a connection. Eliminating the remaining
risk requires upstream-proxy-side policy, DNS-over-proxy classification, or
explicit allowlists, and is out of scope for the first implementation.

Component bridges should not perform target DNS classification. Keep that
logic centralized in the final-hop proxy policy.

The current prefetch-proxy DNS behavior and its upstream-proxy residual risk
are described in [Request handling](../request_handling.md) and
[Internal network security](../internal_network_security.md).

## Component Targets

### Code-Interpreter Executors

Default disabled behavior should stay visibly simple:

- no executor network overlay;
- no executor-only network;
- no executor bridge;
- no executor proxy injection;
- upstream executor network isolation remains intact.

When `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`, generated Python and shell
code should get one network capability: connect to a local HTTP/HTTPS proxy
on the executor-only network.

Target shape:

```text
executor pod
  |
  | HTTP_PROXY / HTTPS_PROXY / ALL_PROXY
  v
executor-egress-bridge
  |
  v
executor/prefetch final-hop proxy in netns-holder
  |
  v
internet via selected routing matrix
```

Executor requirements:

- executor pods attach only to a named internal Docker network, for example
  `onyx-code-interpreter-executor`;
- `PYTHON_EXECUTOR_DOCKER_NETWORK` points at that concrete network name, never
  `container:onyx-netns-holder-1`;
- executor proxy variables point at the bridge URL, not at
  `ONYX_AGENT_OUTBOUND_PROXY_URL`;
- executor `NO_PROXY` is limited to loopback inside the executor pod, for
  example `127.0.0.1,localhost,::1`;
- generated code cannot resolve or route to `api_server`, CRW, SearXNG,
  Obscura, Obscura MCP, CDP shim, `myst-client`, host bridges, data stores,
  host gateway addresses, `host.docker.internal`, LAN/private ranges, or
  link-local metadata addresses;
- search-engine URLs stay blocked so generated code uses the controlled
  SearXNG/CRW/Obscura path instead of direct SERP scraping.

For the current executor gap and recommended proxy-only direction, see
[Internal network security](../internal_network_security.md). For the existing
runtime patch mechanics and upgrade checks, see
[Onyx patch information](../onyx_patch_info.md) and
[Onyx wrapper patches](../onyx_patches_upgrade.md).

The code-interpreter `sitecustomize` patch should model three independent
decisions:

1. whether executor network access is enabled;
2. which Docker network executor pods join;
3. which proxy variables executor pods receive.

Recommended patch behavior:

- if `ONYX_CODE_INTERPRETER_ENABLE_NETWORK` is not `true`, do not change the
  executor network and do not inject executor proxy variables;
- if enabled, require `PYTHON_EXECUTOR_DOCKER_NETWORK` and reject
  `container:onyx-netns-holder-1`;
- require `ONYX_AGENT_EXECUTOR_HTTP_PROXY_URL`;
- inject `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and lowercase variants from
  `ONYX_AGENT_EXECUTOR_HTTP_PROXY_URL`;
- inject `NO_PROXY` from `ONYX_AGENT_EXECUTOR_NO_PROXY`, defaulting to
  `127.0.0.1,localhost,::1`;
- pass `ONYX_AGENT_OUTBOUND_PROXY_URL` only as diagnostic/context if needed,
  never as the executor's direct proxy URL;
- never propagate trusted-service `NO_PROXY_INTERNAL` into executor pods.

The API-server prompt/capability patch in
`onyx/patches/sitecustomize_base/wrapper_env_patches.py` must change alongside
this. It currently rewrites Python, Bash, and coding-agent tool descriptions
when `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`; after isolation, those
descriptions must not advertise shared-namespace CRW, SearXNG, or CDP loopback
endpoints.

Prompt/tool-description patches should stop advertising shared-namespace local
services. Use wording closer to:

```text
Network access is available through a restricted HTTP/HTTPS proxy. Direct
socket access to the internet and direct access to stack-internal services are
blocked. Internal/private network targets and direct search-engine URLs are
blocked by the proxy.
```

Optional local CRW search/scrape access for the code agent should be a separate
opt-in profile, not part of baseline network enablement. If added, expose only
a reviewed CRW gateway URL on the executor network. Do not expose direct CDP or
Obscura MCP.

The staged shape for that opt-in should be:

1. implement proxy-only executor networking first;
2. advertise only restricted proxy access in prompt/tool text;
3. add `ONYX_CODE_INTERPRETER_ALLOW_LOCAL_WEB_SERVICES=true` only if code-agent
   workflows need local CRW search/scrape;
4. expose CRW's Firecrawl-compatible API through an executor-network gateway,
   preferably before exposing SearXNG directly;
5. keep direct CDP and Obscura MCP unexposed unless a separate browser-control
   design is reviewed.

A raw TCP gateway for local CRW access would expose all of CRW on that port. If
only specific Firecrawl paths should be reachable, use a tiny HTTP gateway with
path-level allowlisting instead of a pure TCP forwarder. Direct CDP access is
a much larger capability than HTTP search/scrape because it gives generated
code browser automation control over a long-lived internal browser service.

### Obscura Browser

Obscura renders attacker-controlled pages and should not live in the broad
shared namespace. Its egress is different from CRW prefetch and executor
egress because the browser must be allowed to visit configured search-engine
hosts.

Target shape:

```text
cdp-shim
  |
  | CDP WebSocket
  v
obscura on a browser-control network
  |
  | --proxy http://obscura-egress-bridge:3128
  v
obscura-egress-bridge
  |
  v
browser final-hop proxy in netns-holder
  |
  v
internet via selected routing matrix
```

Obscura requirements:

- Obscura attaches only to a browser-control network and a browser-egress
  network;
- the browser-control network contains only CDP shim and Obscura's CDP
  listener;
- the browser-egress network exposes only the proxy bridge;
- Obscura receives routing through `--proxy` or `OBSCURA_PROXY`, not broad
  process-wide proxy env as the enforcement mechanism;
- the browser final-hop policy blocks internal/private targets, host gateway
  addresses, `host.docker.internal`, single-label service names, and
  link-local metadata ranges;
- the browser final-hop policy allows configured search-engine hosts.

Do not point Obscura at the existing search-blocking
`prefetch-blocking-proxy`. That would break the intentional SERP rendering
path.

The browser final-hop policy should reuse the same proxy implementation with a
distinct mode rather than forking proxy code. For example:

```text
mode=prefetch   block_search=true   block_internal=true   allow_http=false
mode=executor   block_search=true   block_internal=true   allow_http=false
mode=browser    block_search=false  block_internal=true   allow_http=false
mode=searxng-external block_search=false  block_internal=true   allow_http=false
```

The browser mode is not a weaker accidental variant of prefetch mode; it exists
because Obscura must render search-engine pages while still blocking
internal/private targets.

In the real implementation, `allow_http` should be derived from
`ONYX_AGENT_ALLOW_HTTP_URLS` for every mode above. The default remains false,
but the existing explicit opt-in must continue to apply to both the proxy
policy and the CDP shim so HTTP browser navigations cannot bypass the
prefetch/executor block.

For the current CRW-to-CDP-to-Obscura browser chain, wait strategy, cookie
clearing, and CDP shim behavior, see
[Request handling](../request_handling.md).

### CRW

CRW accepts untrusted target URLs, performs HTTP prefetch, controls the browser
through CDP, and optionally calls SearXNG for `/v1/search`. It should have
only those explicit peers.

Target shape:

```text
Onyx / SearXNG callers
  |
  v
crw on a crw-api network
  |
  | HTTP_PROXY / HTTPS_PROXY
  v
crw-prefetch-bridge
  |
  v
prefetch final-hop proxy in netns-holder

crw -> cdp-shim on a crw-cdp network
crw -> searxng on a search network, if CRW /v1/search remains enabled
```

CRW requirements:

- no direct internet egress;
- no broad `NO_PROXY` list;
- HTTP prefetch reaches only the CRW prefetch bridge;
- the prefetch final-hop policy keeps search-engine blocking so CRW escalates
  SERPs to Obscura without raw HTTP double-hits;
- CDP access goes only to the CDP shim endpoint;
- SearXNG access goes only to the intended search endpoint;
- caller access to CRW's Firecrawl-compatible API uses an explicit API
  network or host diagnostic bridge;
- CRW has no route to Onyx internals, Obscura CDP/MCP directly, Mysterium
  admin ports, data stores, host gateway addresses, `host.docker.internal`,
  LAN/private ranges, or link-local metadata addresses.

This requires replacing current loopback assumptions:

- `CRW_RENDERER__CHROME__WS_URL` must change from shared loopback, currently
  shaped like `ws://127.0.0.1:9224/devtools/browser`, to a DNS name on the CDP
  network;
- `CRW_SEARCH__SEARXNG_URL` must change from shared loopback, currently
  shaped like `http://127.0.0.1:8888`, to a DNS name on the search network;
- SearXNG custom engines need configurable `CRW_SCRAPE_URL` instead of
  assuming `http://127.0.0.1:3010`;
- healthchecks must stop assuming CRW can use shared loopback for all peers.

For CRW's Firecrawl-compatible scrape path, HTTP prefetch behavior,
PDF/content-type handling, and `open_url` fallback caveats, see
[Request handling](../request_handling.md).

### SearXNG

In the default wrapper configuration, SearXNG's enabled web engines are
CRW-backed stubs. It does not need general internet egress for web search.
The known CRW-backed engines include `google2`, `brave2`, `duckduckgo2`,
`startpage2`, and `bing2`.

Target shape:

```text
Onyx / host diagnostics
  |
  v
searxng-core on search-api/search-internal networks
  |
  +--> crw /v1/scrape
  +--> searxng-valkey
```

SearXNG requirements:

- default CRW-backed engine mode has no internet egress proxy at all;
- web search outbound HTTP goes only to CRW's scrape endpoint;
- SearXNG reaches only Valkey for its own cache/state;
- it does not share a namespace or general network with Onyx internals,
  Obscura, CDP shim, Mysterium admin ports, data stores, host bridges, or host
  gateway aliases;
- accidental direct engines fail closed by network placement.

If future non-CRW engines, favicons, autocomplete, or other external features
are intentionally enabled, add a SearXNG egress bridge and configure SearXNG's
native `outgoing.proxies` / per-engine `network` settings to use it. Do not
rely on process-level proxy environment variables.

In that future shape, CRW-backed engines can use a SearXNG network whose
meaning is "direct to local CRW on the internal Docker network," not direct
internet. External engines should use a separate proxy-backed outgoing
network, and container placement should still prevent arbitrary direct sockets
from bypassing those SearXNG settings.

For the current custom CRW-backed engines, provider scheduling, parser
assumptions, and SearXNG proxy overlay behavior, see
[Request handling](../request_handling.md) and
[Onyx patch information](../onyx_patch_info.md).

## Compose Layering

Use network-focused names for restricted-egress overlays.

Recommended files and responsibilities:

- `docker-compose.restricted-egress.yml`: common final-hop proxy modes,
  bridge image/service definitions, and shared restricted-network primitives
  where Compose permits reuse;
- `docker-compose.code-interpreter-network.yml`: executor-only network,
  executor bridge, executor Docker network selection, executor proxy URL,
  executor `NO_PROXY`, patch mount, and API-server prompt enablement;
- `docker-compose.browser-network.yml`: Obscura/CDP restricted networks and
  browser egress bridge;
- `docker-compose.search-network.yml`: SearXNG/Valkey/CRW search networks and
  CRW scrape URL wiring;
- `docker-compose.crw-network.yml`: CRW API/CDP/search/prefetch network
  placement.

If separate files become too noisy, combine the request-path components into
one `docker-compose.request-egress.yml`. Executor networking should live in
`docker-compose.code-interpreter-network.yml`, because executors attach to a
restricted executor network rather than to the VPN namespace.

`docker-compose.proxy.yml` should keep its narrow meaning:

- configure final-hop proxies to use `ONYX_AGENT_OUTBOUND_PROXY_URL`;
- configure trusted-service upstream proxy behavior for services that remain
  trusted shared-namespace components, including the current Obscura and
  shared-namespace `prefetch-blocking-proxy` behavior until those components
  move to restricted networks;
- configure SearXNG external proxy settings only when external SearXNG egress
  is intentionally enabled.

It should not inject executor proxy variables. `ONYX_AGENT_OUTBOUND_PROXY_URL`
alone must never change the executor sandbox.

Additional layering edge cases:

- No executor bridge or executor-only network should live in the base compose
  file. Base behavior should keep upstream executor network isolation.
- `docker-compose.teep-vpn.yml` and `docker-compose.tailscale-vpn.yml` move
  trusted services into the shared namespace; they should not attach to the
  executor-only network.
- The Podman overlay currently disables code-interpreter by default. Treat
  restricted executor networking under Podman as unsupported until explicitly
  validated.
- If Podman support is added later, confirm spawned executor containers join
  only the intended internal network, the bridge is their only reachable peer,
  and no host gateway or stack aliases are reachable.

For the existing Compose layering and routing overrides, see
[VPN routing and proxies](../vpn_routing_and_proxies.md). For the upgrade
checklist that covers Compose, SearXNG, Onyx, and code-interpreter patch
surfaces, see [Onyx wrapper patches](../onyx_patches_upgrade.md).

The Makefile should use network-specific suffix names, for example:

```make
CODE_INTERPRETER_NETWORK_SUFFIX :=
ifneq ($(filter true,$(ONYX_CODE_INTERPRETER_ENABLE_NETWORK)),)
CODE_INTERPRETER_NETWORK_SUFFIX :=:docker-compose.code-interpreter-network.yml
endif
```

Add separate opt-in variables only when needed, for example:

```env
ONYX_RESTRICT_OBSCURA_NETWORK=true
ONYX_RESTRICT_SEARXNG_NETWORK=true
ONYX_RESTRICT_CRW_NETWORK=true
ONYX_CODE_INTERPRETER_ALLOW_LOCAL_WEB_SERVICES=false
```

Prefer enabling the full restricted request path as a single documented mode
after validation, rather than leaving many half-overlapping profiles as
long-term user-facing options.

## Design Rules

- Use `docker-compose.code-interpreter-network.yml` for executor networking.
- `ONYX_AGENT_OUTBOUND_PROXY_URL` configures final-hop upstream proxying; it
  does not trigger executor proxy injection.
- `ONYX_AGENT_ALLOW_HTTP_URLS` remains the single cleartext target URL
  preference for proxy policy modes and CDP navigation blocking.
- Preserve the documented policy preference surface; do not silently drop
  `PREFETCH_BLOCK_HOSTS`, `PREFETCH_BLOCK_INTERNAL_HOSTS`,
  `ONYX_AGENT_HTTPS_PROXY_REQUIRE_TLS13`, executor proxy injection variables,
  Obscura navigation/cookie variables, SearXNG scheduling, or Onyx SSRF seed
  variables when moving components onto restricted networks.
- Untrusted executors receive only the dedicated executor `NO_PROXY`, not
  trusted-service `NO_PROXY_INTERNAL`.
- Prompt text advertises only reachable restricted-network capabilities.
- Destination policy lives in one proxy implementation with explicit modes.
- Raw TCP bridges are the default bridge type when no distinct policy,
  logging, rate-limit, or accounting role is needed.
- SearXNG custom engines use a configurable CRW endpoint.
- Runtime topology uses named API, CDP, search, prefetch, browser-egress, and
  executor-egress networks instead of broad shared-namespace assumptions.
- Host-proxy LAN bypass and upstream-proxy DNS classification are documented
  residual risks for the relevant routing modes.

## Implementation Phases

### Phase 1: Shared Primitives

1. Add explicit proxy policy modes to the existing prefetch proxy
   implementation or an adjacent shared module:
   - `prefetch`;
   - `executor`;
   - `browser`;
   - optional `searxng-external`.
2. Wire policy modes to the existing preference names, including
   `ONYX_AGENT_ALLOW_HTTP_URLS`, `PREFETCH_BLOCK_HOSTS`,
   `PREFETCH_BLOCK_INTERNAL_HOSTS`, `ONYX_AGENT_OUTBOUND_PROXY_URL`, and
   `ONYX_AGENT_HTTPS_PROXY_REQUIRE_TLS13`.
3. Keep destination validation strict and fail closed.
4. Add a minimal bridge service pattern that can expose one restricted-network
   port to one final-hop proxy port without host publishing.
5. Add compose naming conventions and Makefile suffix names for restricted
   egress.

### Phase 2: Code-Interpreter Executors

1. Add `docker-compose.code-interpreter-network.yml` for executor networking.
2. Add the executor-only internal network with an explicit Docker network
   name.
3. Add the executor egress bridge to the executor network and the required
   upstream side.
4. Set `PYTHON_EXECUTOR_DOCKER_NETWORK` to the concrete executor network name.
5. Keep executor proxy injection in the code-interpreter network overlay only.
6. Tighten `sitecustomize_code_interpreter` as described above.
7. Update API-server prompt/tool-description patches.

### Phase 3: Obscura

1. Add a browser final-hop proxy policy that allows search hosts but blocks
   internal/private targets and plain HTTP by default unless
   `ONYX_AGENT_ALLOW_HTTP_URLS=true`.
2. Move Obscura onto browser-control and browser-egress networks.
3. Retarget CDP shim to Obscura by DNS name on the control network.
4. Point Obscura at the browser egress bridge through `--proxy` or
   `OBSCURA_PROXY`.
5. Preserve CDP shim preferences for waitUntil selection, cookie clearing,
   trace redaction, proxy-server stripping, and HTTP URL blocking.
6. Verify every custom SearXNG engine can still render SERPs through CRW.

### Phase 4: SearXNG

1. Make the custom engine CRW scrape URL configurable.
2. Move SearXNG and Valkey onto explicit search networks.
3. Keep the default CRW-backed engine set without general internet egress.
4. Keep `docker-compose.proxy.yml` SearXNG proxy mutation only for intentional
   external-engine modes.

### Phase 5: CRW

1. Move CRW onto explicit API, CDP, search, and prefetch-egress networks.
2. Retarget `CRW_RENDERER__CHROME__WS_URL` to the CDP shim DNS endpoint.
3. Retarget `CRW_SEARCH__SEARXNG_URL` to the SearXNG DNS endpoint if
   `/v1/search` remains enabled.
4. Point CRW `HTTP_PROXY` and `HTTPS_PROXY` at the CRW prefetch bridge.
5. Update healthchecks and host diagnostic bridges.
6. Re-test `web_search`, `open_url`, CRW `/v1/search`, PDF prefetch behavior,
   and failure modes.

This order is intentional:

| Component | Main risk reduced | Isolation value | Complexity | Order |
| --- | --- | --- | --- | --- |
| Code-interpreter executors | LLM-generated code bypassing proxy variables and reaching internal services | Very high | Medium to high | 1 |
| Obscura browser | Untrusted page JS or browser/proxy bugs reaching internal services or bypassing configured egress | High | High | 2 |
| SearXNG | Accidental direct engines or plugin/network features bypassing CRW | Medium | Low to medium | 3 |
| CRW | Scraper orchestration bugs or direct fetch paths bypassing configured proxy/CDP/search channels | Medium to high | High | 4 |

The decision point for each component should be whether the added network
surface remains smaller and clearer than the shared namespace it replaces.
Prefer several narrow, named bridges over one broad dual-homed internal router.

## Documentation Updates

Update docs in the same implementation phase as the related behavior.

- `.env.wrapper.example`: describe the new meaning of
  `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true` and any restricted request-path
  toggles. Keep `ONYX_AGENT_ALLOW_HTTP_URLS` documented as the cleartext URL
  control for CRW prefetch, executor proxy use, and Obscura/CDP navigation.
- `README.md`: update user-facing code-interpreter networking, upstream proxy,
  host Tor, and caveat sections.
- `.env.wrapper.example` and `README.md`: continue to tell host-Tor users that
  `MYST_VPN_ALLOW_LAN_BYPASS=true` is required for
  `socks5h://host.docker.internal:9150`, while also saying this is a broad LAN
  route exemption in the current implementation.
- `.env.wrapper.example` and `README.md`: mention the upstream-proxy DNS
  classification caveat near the upstream proxy examples so operators do not
  read `ONYX_AGENT_OUTBOUND_PROXY_URL` as a complete private-network
  enforcement layer.
- [VPN routing and proxies](../vpn_routing_and_proxies.md): replace
  shared-namespace executor routing text; document the final-hop matrix,
  upstream-proxy DNS caveat, and host-proxy LAN bypass distinction.
- [Internal network security](../internal_network_security.md): move the
  current shared-namespace executor gap to historical/background context after
  it is fixed; document remaining bridge, DNS, and host-proxy risks.
- [Request handling](../request_handling.md): describe restricted CRW, SearXNG,
  Obscura, and executor paths as they land, including search-engine blocking
  and the difference between no-upstream-proxy DNS classification and
  upstream-proxy mode.
- [Onyx patch information](../onyx_patch_info.md) and
  [Onyx wrapper patches](../onyx_patches_upgrade.md): update code-interpreter
  prompt/proxy patch mechanics and upgrade checks.
- [Local docs RAG search](../local_docs_rag_search.md): update only if the
  restricted network work changes doc-drop, Web connector, PDF freshness, or
  embedding-shim placement.

## Validation Plan

Use `ENV_FILE=/dev/null` and explicit environment values so validation does
not read local private `.env.wrapper`.

Static checks:

```text
ENV_FILE=/dev/null make help
python -m py_compile crw/prefetch_blocking_proxy.py onyx/patches/sitecustomize_code_interpreter/sitecustomize.py
git diff --check
```

Compose shape checks:

1. Network disabled, upstream proxy empty.
   - No executor-only network.
   - No executor bridge.
   - No executor proxy injection.
   - Executor network remains upstream `none`.

2. Network disabled, upstream proxy set.
   - `docker-compose.proxy.yml` applies only to trusted/final-hop proxy
     behavior.
   - No executor-only network.
   - No executor bridge.
   - No executor proxy injection.

3. Executor network enabled, upstream proxy empty, `MYST_VPN_ENABLED=true`.
   - Executor-only internal network exists with explicit concrete name.
   - `PYTHON_EXECUTOR_DOCKER_NETWORK` points at that concrete name.
   - Executor bridge exists and has no host-published port.
   - Bridge listener accepts executor-network clients only, not arbitrary
     default-network clients.
   - Executor proxy URL points at the bridge.
   - Final-hop proxy has no upstream proxy and remains in the routing
     namespace.

4. Executor network enabled, upstream proxy set, `MYST_VPN_ENABLED=true`.
   - Executor shape is unchanged.
   - Final-hop proxy receives `ONYX_AGENT_OUTBOUND_PROXY_URL`.
   - Executor pods point at the local bridge, not the upstream proxy URL.
   - Host proxy configurations require `MYST_VPN_ALLOW_LAN_BYPASS=true` or a
     future endpoint-scoped equivalent.

5. Executor network enabled, `MYST_VPN_ENABLED=false`.
   - Executor isolation still depends on the executor network and bridge, not
     on Myst.
   - No shared-namespace VPN connection is expected.

6. Restricted request path enabled.
   - Obscura has only browser-control and browser-egress networks.
   - CRW has only API, CDP, search, and prefetch-egress networks.
   - SearXNG has only search API/internal and Valkey networks by default.
   - No restricted component has a route to broad default-network peers,
     host gateway aliases, or `netns-holder` service aliases except through
     the intended bridge.
   - Proxy policy services and the CDP shim receive
     `ONYX_AGENT_ALLOW_HTTP_URLS`; the default false value is visible in the
     effective compose model.

Runtime checks when safe:

- from an executor pod, `curl --noproxy '*' https://example.com` fails;
- from an executor pod, `curl https://example.com` succeeds through the proxy
  when egress is available;
- with `ONYX_AGENT_ALLOW_HTTP_URLS=false`, proxied executor and CRW prefetch
  requests for `http://example.com` return the documented proxy `403`, and CDP
  HTTP navigations are rejected by the shim;
- with `ONYX_AGENT_ALLOW_HTTP_URLS=true`, non-search `http://example.com`
  requests are allowed only through the selected final-hop policy and still
  block internal/private targets;
- executor route table and Docker network attachments show only the executor
  internal network;
- non-executor containers cannot use the executor bridge listener;
- executor direct requests to examples such as `http://api_server:8080/health`,
  `http://myst-client:9101/health`, `http://crw:3010/health`, Obscura,
  SearXNG, `host.docker.internal`, LAN/private ranges, and link-local metadata
  fail;
- executor proxied requests to internal/private targets return proxy `403`;
- executor proxied requests to configured search-engine hosts return proxy
  `403`;
- executor prompts/tool descriptions mention restricted proxy access but do not
  advertise shared-namespace loopback CRW, SearXNG, or CDP endpoints;
- Obscura can still render each configured search provider;
- CRW still blocks search prefetches and escalates to Obscura;
- `open_url` still handles non-search HTTPS, PDF, and JS-required pages as
  documented;
- SearXNG default CRW-backed engines still work and accidental direct external
  engine traffic fails closed;
- upstream-proxy mode includes a documented manual review or test note for the
  hostname-to-private-IP residual risk.

## Security Properties

Expected improvements:

- generated code does not share loopback or service aliases with the stack;
- Obscura has a network backstop behind its private-network block;
- CRW can reach only its documented prefetch, CDP, search, and caller peers;
- SearXNG default search has no general internet egress;
- direct socket bypasses fail because restricted components have no route to
  the broader stack or internet;
- `NO_PROXY` lists do not create internal carve-outs for untrusted executors;
- the existing VPN/proxy/no-VPN final-hop semantics remain visible and
  centralized.

Remaining risks:

- code-interpreter service still has Docker socket access to spawn executor
  pods and remains trusted control plane;
- a wrongly named or non-internal Docker network can reintroduce reachability;
- a bridge that accepts clients on the wrong interface can create an
  undocumented proxy path;
- TCP bridges rely on the selected final-hop proxy policy being correct;
- upstream-proxy mode intentionally skips local target DNS resolution, leaving
  the hostname-to-private-IP residual risk described above;
- host-resident upstream proxies currently rely on
  `MYST_VPN_ALLOW_LAN_BYPASS=true`, which is broader than endpoint-scoped host
  proxy access;
- optional local CRW access for generated code, if enabled, expands the
  executor capability surface and must be documented as such.

## Decision Summary

This plan covers restricted egress for all agent-relevant network components
together. The architecture is:

```text
restricted component network
  -> narrow component bridge
  -> named final-hop proxy policy in the routing namespace
  -> selected Mysterium/proxy/no-VPN final hop
```

Use the search-blocking `prefetch`/`executor` policy for code-interpreter
executors and CRW HTTP prefetch. Add a separate search-allowed `browser`
policy for Obscura. Give SearXNG no internet egress in the default CRW-backed
configuration, and add `searxng-external` only for intentional external
features.

Implement in phases, but design the names, proxy modes, network boundaries,
and docs as one comprehensive restricted-egress model from the start.
