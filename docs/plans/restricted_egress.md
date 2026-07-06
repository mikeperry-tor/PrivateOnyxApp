# Restricted Egress Network Plan

## Goal

Move egress-capable stack components from broad shared-namespace or
Docker-bridge reachability to narrow, component-specific network placement.
Each component should be able to reach only the local proxy shim ports and
explicit peer-service ports required for its job. Those proxy shims should
enforce destination policy and preserve the operator's selected final outbound
path.

The intended end state:

- Every egress-capable component has an explicit final-hop contract:
  - `MYST_VPN_ENABLED=true` and no `ONYX_AGENT_OUTBOUND_PROXY_URL`: allowed
    external requests leave through the Mysterium namespace.
  - `MYST_VPN_ENABLED=true` and `ONYX_AGENT_OUTBOUND_PROXY_URL` set: allowed
    external requests connect to the configured upstream proxy from inside the
    Mysterium namespace.
  - `MYST_VPN_ENABLED=false` and no `ONYX_AGENT_OUTBOUND_PROXY_URL`: allowed
    external requests leave directly through Docker bridge egress by explicit
    no-VPN choice.
  - `MYST_VPN_ENABLED=false` and `ONYX_AGENT_OUTBOUND_PROXY_URL` set: allowed
    external requests connect to the configured upstream proxy directly,
    without the Mysterium hop.
- Every egress-capable component is unable to reach stack-internal API
  surfaces, host gateway addresses, `host.docker.internal`, LAN/private
  addresses, link-local metadata addresses, or other host-network targets
  except through a local proxy shim that applies the component's destination
  policy.
- Host-resident upstream proxies, especially Tor Browser at
  `socks5h://host.docker.internal:9150`, remain supported as an explicit
  `ONYX_AGENT_OUTBOUND_PROXY_URL` option. This is a final-hop exception for the
  proxy shim that dials the configured upstream proxy, not general host access
  for egress-capable application components.
- `ONYX_AGENT_OUTBOUND_PROXY_URL` continues to mean "optional upstream proxy
  for internet egress" rather than "executor network isolation."
- `MYST_VPN_ENABLED=true` and `MYST_VPN_ENABLED=false` keep their current
  explicit meanings for the final outbound hop.

The immediate implementation target is code-interpreter executor isolation:

- `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=false` remains easy to audit: executor
  pods use upstream `network none`, no executor egress bridge is started, and
  no proxy environment is injected into executors.
- `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true` gives generated Python and shell
  code a single network capability: connect to a local HTTP proxy service.
- The executor proxy service blocks search engine URLs and internal/private
  targets, then forwards allowed HTTP and HTTPS requests through the same
  final-hop contract as the other egress-capable components.

This document also compares follow-on options for isolating Obscura, CRW, and
SearXNG into restricted containers that can reach only their configured
proxies or explicitly allowed peer services.

## Non-Goals

- Do not add a transparent firewall or general-purpose network sandbox in this
  phase. The boundary should be Docker network placement plus a proxy-only
  reachable service.
- Do not preserve the old shared-namespace executor behavior as an implicit
  compatibility mode. If that behavior is ever needed for trusted debugging, it
  should require a separate explicit opt-in variable.
- Do not make `ONYX_AGENT_OUTBOUND_PROXY_URL` required. Network-enabled
  executors should use the local proxy endpoint whether or not a remote
  upstream proxy is configured.
- Do not give executor pods access to the shared `netns-holder` namespace,
  stack service aliases, Docker socket, host gateway, or Docker bridge by
  relying on `NO_PROXY` values. Proxy environment variables are only routing
  hints; network placement must be the boundary.

## Current State

The base stack has a stable shared namespace owned by `netns-holder`.
VPN-routed services join that namespace with:

```yaml
network_mode: "service:netns-holder"
```

`prefetch-blocking-proxy` also runs in that namespace. It listens on
`127.0.0.1:3128` from the point of view of CRW and other shared-namespace
services. CRW's HTTP prefetch path uses:

```env
HTTP_PROXY=http://127.0.0.1:3128
HTTPS_PROXY=http://127.0.0.1:3128
NO_PROXY=127.0.0.1,localhost,::1
```

The proxy blocks search-engine prefetches and internal/private targets, then
forwards allowed HTTP requests and HTTPS `CONNECT` tunnels either directly from
the shared namespace or through `ONYX_AGENT_OUTBOUND_PROXY_URL`.

Today, when `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`, the Makefile layers
`docker-compose.code-interpreter-vpn.yml`. That override sets:

```env
PYTHON_EXECUTOR_DOCKER_NETWORK=container:onyx-netns-holder-1
```

Executor pods then inherit the entire shared namespace. Generated code can use
raw sockets, ignore proxy variables, and reach internal services directly. That
is the security gap this plan closes.

## Target Architecture

Introduce a dedicated executor-only Docker network and a small egress proxy
bridge.

```text
executor pod
  |
  | HTTP_PROXY / HTTPS_PROXY / ALL_PROXY
  v
code-interpreter-egress-proxy
  |     attached to executor-only internal network
  |     attached to normal stack default network
  v
prefetch-blocking-proxy at myst-client:3128
  |
  | direct from netns-holder, or via ONYX_AGENT_OUTBOUND_PROXY_URL
  v
internet
```

Executor egress routing goal:

- Executor pods should not have direct internet egress.
- Their only internet-capable peer should be the local executor proxy shim,
  for example `http://code-interpreter-egress-proxy:3128`.
- The executor proxy shim should be a simple TCP forwarder to the shared
  `prefetch-blocking-proxy` at `myst-client:3128`.
- The shared proxy remains responsible for final egress through Mysterium,
  through `ONYX_AGENT_OUTBOUND_PROXY_URL`, through both, or through neither
  when no-VPN/no-proxy mode is explicitly selected.
- If `ONYX_AGENT_OUTBOUND_PROXY_URL` points at a host Docker proxy such as
  Tor Browser, executor pods still reach only the local executor proxy shim;
  the host-proxy connection is made by the final-hop shared proxy under the
  explicit host-proxy bypass policy.

Executor internal-access goal:

- Executor pods should attach only to the executor-only internal network.
- The executor-only network should expose only the executor proxy shim port.
- Executor pods should not be able to resolve or route to `api_server`, CRW,
  SearXNG, Obscura, Obscura MCP, CDP shim, `myst-client`, data stores,
  host-facing bridges, Docker bridge peers, host gateway addresses,
  `host.docker.internal`, LAN/private ranges, or link-local metadata
  addresses.
- Executor `NO_PROXY` should remain limited to loopback inside the executor
  pod itself, not stack service names.

The executor pods should be attached only to a new internal network, for
example:

```yaml
networks:
  code-interpreter-executor:
    internal: true
```

The code-interpreter patch should set executor proxy variables to a service DNS
name on that network, for example:

```env
HTTP_PROXY=http://code-interpreter-egress-proxy:3128
HTTPS_PROXY=http://code-interpreter-egress-proxy:3128
ALL_PROXY=http://code-interpreter-egress-proxy:3128
NO_PROXY=127.0.0.1,localhost,::1
```

The executor `NO_PROXY` value should be intentionally small. It must not reuse
the trusted-service `NO_PROXY_INTERNAL` list that includes `api_server`,
`myst-client`, `obscura`, `crw`, `searxng-core`, host proxy aliases, or other
stack-internal names.

## Recommended Implementation Shape

### 1. Replace the Shared-Namespace Executor Overlay

Replace the current meaning of `docker-compose.code-interpreter-vpn.yml`, or
introduce a renamed overlay such as:

```text
docker-compose.code-interpreter-network.yml
```

The existing filename is misleading once executor pods no longer join the VPN
namespace. A rename is clearer, but it should be handled carefully because the
Makefile and docs currently refer to the old name.

The new overlay should:

- keep `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true` on `code-interpreter`;
- keep the `sitecustomize_code_interpreter` patch mounted;
- keep `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true` on `api_server` so tool
  descriptions say network access is available;
- set `PYTHON_EXECUTOR_DOCKER_NETWORK` to the executor-only network name, not
  `container:onyx-netns-holder-1`;
- add the executor-only internal network;
- add the bridge service;
- set `ONYX_AGENT_EXECUTOR_HTTP_PROXY_URL` to the bridge URL.

The Makefile suffix should change from a VPN-specific variable name to a
network-specific one:

```make
CODE_INTERPRETER_NETWORK_SUFFIX :=
ifneq ($(filter true,$(ONYX_CODE_INTERPRETER_ENABLE_NETWORK)),)
CODE_INTERPRETER_NETWORK_SUFFIX :=:docker-compose.code-interpreter-network.yml
endif
```

### 2. Keep the No-Network Case Visibly Simple

The easiest state to audit is:

- no code-interpreter network overlay;
- no executor-only network;
- no bridge service;
- no executor proxy variables;
- upstream executor `--network none` remains intact.

To preserve that simplicity, proxy injection should be tied to
`ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`, not merely to
`ONYX_AGENT_OUTBOUND_PROXY_URL` being non-empty.

This means moving code-interpreter executor proxy settings out of
`docker-compose.proxy.yml` and into the code-interpreter network overlay, or
making the `sitecustomize_code_interpreter` patch no-op for executor proxy
injection unless `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`.

The stronger and clearer version is to split the concerns:

- `docker-compose.proxy.yml` configures trusted services such as obscura,
  SearXNG, and the shared-namespace `prefetch-blocking-proxy` for the optional
  upstream proxy.
- the code-interpreter network overlay configures executor network placement
  and executor proxy injection.

With that split, `ONYX_AGENT_OUTBOUND_PROXY_URL` alone never changes the
executor sandbox.

### 3. Add a Bridge Service

Add a service such as:

```yaml
code-interpreter-egress-proxy:
  ...
  networks:
    - default
    - code-interpreter-executor
```

It must not publish host ports. It should listen only for executor-network
clients and forward to the existing shared-namespace proxy at:

```text
http://myst-client:3128
```

`myst-client` is a default-network alias for the `netns-holder` namespace owner.
Connecting to `myst-client:3128` reaches `prefetch-blocking-proxy`, because that
proxy listens inside the shared namespace on port `3128`.

Security hardening for the bridge service:

- no host port publishing;
- no Docker socket mount;
- no writable host mounts;
- `read_only: true` where practical;
- `security_opt: ["no-new-privileges:true"]`;
- minimal image;
- clear healthcheck that verifies only local listen/readiness, not external
  internet reachability;
- fail closed if the upstream `myst-client:3128` proxy is unavailable.

## Bridge Strategy For Restricted Components

Separate two roles:

- **final-hop proxy shims** enforce destination policy and own the real
  outbound connection from the correct routing namespace;
- **bridge services** expose one final-hop proxy port to one restricted
  component network.

The preferred bridge service is a minimal raw TCP forwarder. It should not
parse HTTP, resolve target DNS, classify destinations, add proxy policy, or
rewrite requests. Its job is only:

```text
restricted component network :3128 -> selected final-hop proxy :3128
```

This works because the security boundary is the component's Docker network
placement plus the final-hop proxy's destination policy. Duplicating the same
proxy implementation in front of the final-hop proxy mostly adds configuration
and failure modes. It is useful only if the first proxy instance has a
different policy, materially better per-component audit logs, or a separate
rate-limit/accounting function.

### Why Not Move A Final-Hop Proxy To Both Networks?

Moving an existing final-hop proxy into restricted component networks is not
recommended.

The current `prefetch-blocking-proxy` service uses:

```yaml
network_mode: "service:netns-holder"
```

Compose services using `network_mode` cannot also declare ordinary
`networks:` attachments. So the existing service cannot simply stay inside the
shared namespace and also attach to an executor-only network.

Moving it out of `network_mode: service:netns-holder` would have broader
effects:

- CRW currently talks to the proxy through shared-namespace loopback
  `127.0.0.1:3128`; that would need to become a service DNS hop.
- The proxy's own direct egress would leave through its Docker network rather
  than from inside the Mysterium-controlled namespace unless an additional
  upstream hop back into `netns-holder` is added.
- The CRW prefetch path, executor egress path, and optional upstream proxy path
  would become coupled to a dual-homed service with more privilege and more
  reachability.
- The no-VPN and VPN fail-closed reasoning would become harder because the
  same service would be both a shared request-path component and an executor
  boundary component.

This option complicates compose layering and weakens the security story by
making one existing request-path service straddle trust boundaries.

### Simple TCP Forwarder

Use one TCP forwarder per restricted component egress network and final-hop
proxy target. It should not publish host ports.

Benefits:

- very small compose surface;
- no duplicate proxy configuration;
- all URL and destination policy lives in the final-hop proxy;
- fewer moving parts to reason about.

Costs:

- no component-specific URL validation or logs before the final-hop proxy;
- any bug or misconfiguration in the final-hop proxy is the application-level
  policy gate;
- the bridge is a generic tunnel to the proxy port, so its security relies on
  Docker network isolation and the final-hop proxy.

Those costs are acceptable for the default design. The forwarder itself is
deliberately boring; the meaningful review surface is the final-hop proxy
policy and the Compose network graph.

### Component Applicability

The simple TCP-forwarder approach can work for all restricted components, with
two important caveats:

| Component | Forwarder target | Special case |
| --- | --- | --- |
| Code-interpreter executors | search-blocking `prefetch-blocking-proxy` | Works directly. Search-engine URLs should remain blocked so generated code uses the controlled SearXNG/CRW/Obscura search path. |
| CRW HTTP prefetch | search-blocking `prefetch-blocking-proxy` | Works directly. Search-engine prefetches must be blocked to force browser escalation and avoid raw HTTP SERP double-hits. |
| Obscura browser | search-allowed browser final-hop proxy | Cannot use the existing search-blocking prefetch proxy, because Obscura must render search-engine pages. It can still use a raw TCP forwarder, but the target proxy policy must allow configured search-engine hosts while blocking internal/private targets. |
| SearXNG default CRW engines | no internet egress forwarder | Even simpler: with only CRW-backed engines, SearXNG only needs CRW and Valkey networks. |
| SearXNG external engines | search-allowed or engine-policy final-hop proxy | Only needed if non-CRW engines, favicons, autocomplete, or similar external features are intentionally enabled. |

The final-hop proxy policy, not the bridge implementation, is what differs by
component. At minimum the stack needs two final-hop proxy policies:

- **prefetch/executor policy**: block internal/private targets, block
  configured search-engine hosts, and block plain HTTP unless explicitly
  allowed.
- **browser/search-allowed policy**: block internal/private targets and block
  plain HTTP unless explicitly allowed, but allow configured search-engine
  hosts.

A future SearXNG external-engine policy may be the same as browser/search
allowed, or it may become engine-allowlisted if the enabled engine set is small
enough to justify that extra configuration.

### When To Use A Second Proxy Instance

A second proxy instance is optional, not the default recommendation. Use it
only when it provides something the TCP forwarder cannot:

- different destination policy from the final-hop proxy;
- per-component request logs that are worth the extra service and config;
- per-component rate limits or counters;
- an intentionally separate policy failure domain.

Do not run the same proxy code twice with the same policy only to call it
defense in depth. That increases the amount of code and configuration that can
drift without creating an independent boundary.

### Recommendation

Do not move existing final-hop proxies out of `network_mode:
service:netns-holder`, and do not dual-home them into restricted component
networks.

Use simple TCP forwarders from restricted component networks to the appropriate
final-hop proxy port:

- executor and CRW prefetch traffic forward to the search-blocking prefetch
  proxy;
- Obscura browser traffic forwards to a search-allowed browser proxy;
- SearXNG uses no egress forwarder in the default CRW-backed configuration;
- optional SearXNG external egress forwards to a search-allowed or
  engine-policy proxy.

This keeps the bridge layer generic and makes the policy differences visible
where they belong: in the final-hop proxy configuration.

## Sitecustomize Patch Changes

The `onyx/patches/sitecustomize_code_interpreter/sitecustomize.py` patch should
be adjusted so it models three independent decisions:

1. whether executor network access is enabled;
2. which Docker network executor pods join;
3. which proxy environment variables executor pods receive.

Recommended behavior:

- if `ONYX_CODE_INTERPRETER_ENABLE_NETWORK` is not `"true"`, do not change the
  executor network and do not inject executor proxy variables;
- if enabled, require `PYTHON_EXECUTOR_DOCKER_NETWORK` to be non-empty and not
  `container:onyx-netns-holder-1`;
- inject `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and lowercase variants from
  `ONYX_AGENT_EXECUTOR_HTTP_PROXY_URL`;
- inject `NO_PROXY` from a dedicated `ONYX_AGENT_EXECUTOR_NO_PROXY`, defaulting
  to `127.0.0.1,localhost,::1`;
- continue to pass `ONYX_AGENT_OUTBOUND_PROXY_URL` only as diagnostic/context
  if needed, not as the executor's direct proxy URL;
- fail closed in strict mode if proxy injection is expected but no executor
  proxy URL is configured.

The patch should not propagate trusted-service `NO_PROXY_INTERNAL` into
executor pods.

## Compose Layering Review

### Base `docker-compose.yaml`

No executor egress bridge should live in the base file. Base behavior should
remain the safest and easiest to inspect:

- code-interpreter service exists;
- executor pods default to upstream network isolation;
- `prefetch-blocking-proxy` serves CRW prefetch inside `netns-holder`;
- no executor-only network exists.

### `docker-compose.proxy.yml`

This overlay should continue to mean:

- route obscura browser egress through `ONYX_AGENT_OUTBOUND_PROXY_URL`;
- merge SearXNG outgoing proxy settings;
- configure the shared-namespace `prefetch-blocking-proxy` to use the upstream
  proxy.

It should stop being responsible for executor pod proxy injection. Otherwise,
`ONYX_AGENT_OUTBOUND_PROXY_URL` changes executor launch behavior even when
`ONYX_CODE_INTERPRETER_ENABLE_NETWORK=false`, which makes the no-network case
harder to audit.

### Code-Interpreter Network Overlay

This overlay should own all executor networking:

- executor-only network creation;
- bridge service creation;
- executor Docker network selection;
- executor proxy URL and executor `NO_PROXY`;
- code-interpreter patch mount;
- api_server prompt/tool-description enablement.

This makes the meaning of `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`
concentrated in one place.

### VPN Routing Overlays

`docker-compose.teep-vpn.yml` and `docker-compose.tailscale-vpn.yml` should be
unaffected. They move trusted services into the shared namespace. They should
not attach to the executor-only network.

### Podman Overlay

`docker-compose.podman.yml` currently disables code-interpreter by default.
The first implementation can keep restricted executor networking unsupported
under Podman unless it is explicitly validated.

If Podman support is later added, validation must confirm:

- the code-interpreter image can still spawn executor containers/pods with the
  selected network;
- the executor-only network is actually internal;
- the bridge service is the only reachable peer;
- no host gateway or stack aliases are reachable from generated code.

## `MYST_VPN_ENABLED` Complexity Review

### `MYST_VPN_ENABLED=true`

This is the normal private egress mode.

The final outbound hop should still occur from the shared namespace:

```text
executor -> bridge -> myst-client:3128 -> internet
```

`myst-client:3128` is the shared-namespace `prefetch-blocking-proxy`. When it
connects directly to allowed destinations, the connection is made from the
Mysterium-routed namespace and is subject to the existing kill-switch behavior.

If `ONYX_AGENT_OUTBOUND_PROXY_URL` is also set, the shared proxy connects to
that upstream proxy through the Mysterium tunnel. The remote site then sees the
upstream proxy's egress, while the upstream proxy provider sees a connection
from the Mysterium exit.

### `MYST_VPN_ENABLED=false`

This remains an explicit no-VPN mode. `myst-client` idles without arming the
VPN kill-switch or connecting the tunnel.

The executor isolation boundary should still hold:

```text
executor -> bridge -> myst-client:3128 -> Docker bridge/direct egress
```

Generated code can still only reach the bridge service, but allowed internet
requests leave directly unless `ONYX_AGENT_OUTBOUND_PROXY_URL` is configured.

This preserves the current no-VPN semantics: routing privacy is reduced by
explicit user choice, but executor pods still do not receive broad access to
the shared namespace.

## `ONYX_AGENT_OUTBOUND_PROXY_URL` Complexity Review

### Empty Upstream Proxy

When `ONYX_AGENT_OUTBOUND_PROXY_URL` is empty:

- `docker-compose.proxy.yml` is not applied;
- obscura and SearXNG keep their non-upstream-proxy behavior;
- the shared `prefetch-blocking-proxy` forwards allowed requests directly from
  the shared namespace;
- the code-interpreter network overlay, if enabled, still starts the executor
  bridge and points executors at it.

The bridge must therefore not depend on `docker-compose.proxy.yml`.

### Configured HTTP/HTTPS/SOCKS Upstream Proxy

When `ONYX_AGENT_OUTBOUND_PROXY_URL` is non-empty:

- trusted web/search services use the existing proxy override behavior;
- the shared `prefetch-blocking-proxy` uses the configured upstream proxy for
  allowed HTTP and HTTPS traffic;
- executor pods still point only at the local bridge URL, not at the upstream
  proxy URL;
- executor clients do not need PySocks, socksio, or scheme-specific transport
  support because they always see an ordinary HTTP proxy endpoint.

This keeps the SOCKS compatibility fix while avoiding executor access to the
shared namespace.

### Host Docker Proxy / Tor Upstream

Host-resident proxy endpoints must remain a supported upstream proxy shape,
because they make Tor Browser easy to use from the wrapper:

```env
ONYX_AGENT_OUTBOUND_PROXY_URL=socks5h://host.docker.internal:9150
MYST_VPN_ALLOW_LAN_BYPASS=true
```

The existing stack relies on `MYST_VPN_ALLOW_LAN_BYPASS=true` so the
shared-namespace proxy client can reach `host.docker.internal:9150` while other
egress remains controlled by the Mysterium namespace and proxy configuration.
Restricted component networks must preserve this capability without widening
application-component access to the host.

Required policy:

- application components and executor pods must not be able to connect to
  `host.docker.internal`, host gateway IPs, LAN/private ranges, or link-local
  metadata addresses directly;
- only the final-hop egress shim that owns `ONYX_AGENT_OUTBOUND_PROXY_URL`
  may connect to the configured upstream proxy endpoint when that endpoint is
  on the Docker host or LAN;
- this host-proxy allowance should be endpoint-scoped to the resolved upstream
  proxy host and port, not a general exemption for arbitrary host/LAN targets;
- if the final-hop shim remains in `netns-holder`, the current
  `MYST_VPN_ALLOW_LAN_BYPASS=true` requirement is acceptable and should be
  documented as still required for host Tor;
- if future browser, CRW, SearXNG, or executor shims perform the final
  upstream dial outside `netns-holder`, they need an equivalent shim-scoped
  host-proxy bypass such as an explicit allowlist for the configured upstream
  proxy endpoint;
- destination validation for target URLs remains unchanged. A request for
  `http://host.docker.internal:*` or a private/LAN target is still blocked;
  only the proxy shim's connection to the configured upstream proxy can use
  the host/LAN bypass.

This distinction keeps the user-facing Tor flow simple while preserving the
main isolation goal: components can reach only their local shim ports, and
host access is limited to the shim's final-hop connection to the configured
upstream proxy.

### DNS Classification

The final-hop proxy's current DNS behavior should remain:

- with no upstream proxy, it resolves targets itself and blocks names that
  resolve to non-global/internal addresses;
- with an upstream proxy, it avoids target DNS resolution to prevent leaking
  target DNS outside the configured proxy path.

TCP forwarder bridges should not perform target DNS resolution. If an optional
component-facing proxy instance is ever added for a distinct policy or logging
reason, configure it so the final-hop proxy remains the destination
classification point for target DNS.

## Search Engine Blocking

The executor proxy path should keep blocking search engine URLs.

This is acceptable and desirable for code-interpreter because search should
remain on the SearXNG/CRW/obscura path, where rate limits, anti-bot behavior,
and private-target validation are documented and controlled.

Document the user-visible consequence:

- generated code using `urllib`, `requests`, `curl`, `git`, or similar tools
  through the executor proxy should not expect direct access to Google, Brave
  Search, DuckDuckGo HTML, Startpage, or Bing search result pages;
- non-search HTTP and HTTPS URLs are allowed only after internal/private target
  validation.

## Agent-Facing Prompt And Tool Description Patches

There is already an API-server-side patch gated by
`ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`. It lives in
`onyx/patches/sitecustomize_base/wrapper_env_patches.py` and rewrites:

- `PythonTool.DESCRIPTION`;
- `PYTHON_TOOL_GUIDANCE`;
- `BashTool.DESCRIPTION`;
- coding-agent bash tool metadata;
- coding-agent system prompts.

Today that replacement text says executor pods have VPN-routed internet access.
It also advertises shared-namespace local services:

- Python tool text mentions CRW's Firecrawl-compatible API at
  `http://127.0.0.1:3010`, including `/v1/scrape`, `/v1/crawl`, `/v1/map`, and
  `/v1/search`.
- Coding-agent text mentions the same CRW API and direct CDP browser access at
  `ws://127.0.0.1:9222/devtools/browser`.
- It does not advertise SearXNG directly. Search is exposed indirectly through
  CRW's `/v1/search`, which is configured to call the bundled SearXNG sidecar.

That text must change with the restricted executor network. In the new model,
executor pods should not be told they share a namespace or can reach loopback
stack services. The baseline restricted-mode text should say:

```text
Network access is available through a restricted HTTP/HTTPS proxy. Direct
socket access to the internet and direct access to stack-internal services are
blocked. Internal/private network targets and direct search-engine URLs are
blocked by the proxy.
```

If we expose additional local web/search services to code-agent, the prompt
patch should advertise only the gateway endpoints that are actually reachable
from the executor-only network, not the shared-namespace loopback addresses.

## Optional Code-Agent Access To Search/Scrape Services

The strictest restricted-network design gives executor pods access only to the
egress proxy. That is the cleanest interpretation of "proxy service only" and
should be the default.

It is still possible to provide selected local search/scrape capabilities to
the code agent without returning executors to the shared namespace. Treat this
as a separate opt-in service-access profile, not as part of baseline network
enablement.

The safest version is:

- expose CRW's Firecrawl-compatible API through the executor bridge at a new
  executor-network hostname/port;
- prefer CRW `/v1/search` over exposing SearXNG directly, so the code-agent gets
  the existing wrapper search path without learning stack-internal SearXNG
  topology;
- rely on CRW's existing target validation and Obscura's private-network block
  for scrape targets;
- do not expose direct CDP or Obscura MCP in restricted mode.

Direct CDP access is a much larger capability than HTTP search/scrape. It gives
generated code browser automation control over a long-lived internal browser
service and bypasses the CRW API's narrower request model. Even though Obscura
blocks private-network navigation by default, exposing CDP would make the
executor bridge a browser-control bridge rather than a narrow egress/search
bridge.

### Using Option C For Local Service Access

The raw TCP forwarder option can be extended from one listener:

```text
executor :3128 -> myst-client:3128
```

to a small explicit allowlist:

```text
executor :3128 -> myst-client:3128   # HTTP/HTTPS proxy
executor :3010 -> myst-client:3010   # CRW Firecrawl-compatible API
```

Optionally, it could also expose:

```text
executor :8888 -> myst-client:8888   # SearXNG JSON API/UI
```

However, exposing SearXNG directly adds another application surface and may
make prompts and debugging less clear. Prefer exposing CRW `/v1/search` first.

If we use raw forwarding for CRW, the bridge has no HTTP path-level policy; it
exposes whatever CRW exposes on that port. That is simple and auditable at the
network layer, but all application policy then belongs to CRW. If we want a
path-level allowlist, the bridge needs to become a tiny HTTP gateway rather
than a pure TCP forwarder.

Recommended staged approach:

1. Implement proxy-only executor networking first.
2. Update prompt/tool text to describe only restricted proxy access.
3. Add an explicit opt-in such as
   `ONYX_CODE_INTERPRETER_ALLOW_LOCAL_WEB_SERVICES=true` only if code-agent
   workflows need local CRW search/scrape access.
4. If that opt-in is enabled, expose CRW through the bridge and advertise the
   bridge URL in code-agent prompts.
5. Keep CDP and Obscura MCP unexposed unless there is a separate, reviewed
   design for browser-control access.

## Restricted Request-Path Components

The code-interpreter executor is the highest-risk untrusted-code boundary, but
the same proxy-only network pattern can also be applied to the web request
path. The useful question is different for each component:

- Obscura runs untrusted page JavaScript and should ideally have no direct
  egress except a browser egress proxy.
- CRW accepts URLs from Onyx and SearXNG, makes HTTP prefetch decisions, and
  controls the browser through CDP. It should ideally reach only its configured
  prefetch proxy, CDP endpoint, search backend, and callers.
- SearXNG should ideally reach only CRW and Valkey in the default wrapper
  configuration, because the enabled web engines are CRW-backed stubs rather
  than direct external engines.

The reference checkouts under `reference_repos/` support this direction:
Obscura exposes explicit `--proxy` / `OBSCURA_PROXY` controls and does not
honor `HTTP_PROXY` / `HTTPS_PROXY` as a routing boundary; CRW has explicit
proxy and CDP configuration surfaces but can also use environment proxy
variables for its reqwest HTTP prefetch; SearXNG uses `outgoing.proxies` and
per-engine `network` settings rather than relying on process-level proxy
environment variables.

The common implementation principle should remain the same as for
code-interpreter executors: proxy settings are routing hints, while container
network placement is the boundary.

Each restricted component section below must answer two questions:

1. Which local shim port is the component allowed to use for internet egress,
   and how does that shim preserve the selected VPN/proxy/no-VPN final hop?
2. Which Docker networks prevent the component from reaching internal APIs,
   host gateway addresses, `host.docker.internal`, LAN/private ranges, and
   link-local metadata addresses directly?

### Obscura Browser Restricted Container

Obscura is the best candidate for additional isolation because it renders
attacker-controlled web pages. Obscura already blocks private-network targets
by default, and the wrapper does not pass `--allow-private-network`, but it
still currently runs in the shared namespace. If Obscura has a bug in target
validation, JS fetch handling, URL rewriting, or proxy threading, the shared
namespace gives that bug nearby internal listeners to probe.

Target shape:

```text
cdp-shim
  |
  | CDP WebSocket
  v
obscura on an obscura-control network
  |
  | --proxy http://obscura-egress-proxy:3128
  v
obscura-egress bridge/proxy
  |
  v
search-allowed egress proxy inside netns-holder
  |
  v
internet through Mysterium or ONYX_AGENT_OUTBOUND_PROXY_URL
```

Egress routing goal:

- Obscura should not have direct Docker-bridge or shared-namespace egress.
- Its only internet-capable outbound path should be
  `http://obscura-egress-proxy:3128`, passed via `--proxy` or
  `OBSCURA_PROXY`.
- `obscura-egress-proxy` should forward only to a search-allowed proxy shim in
  `netns-holder`, so the final outbound hop follows the same four-state
  `MYST_VPN_ENABLED` / `ONYX_AGENT_OUTBOUND_PROXY_URL` matrix as the rest of
  the stack.
- If `ONYX_AGENT_OUTBOUND_PROXY_URL` points at a host Docker proxy, Obscura
  still reaches only `obscura-egress-proxy`; the search-allowed final-hop shim
  is the only piece that may use the host-proxy bypass.
- The Obscura container should not receive `HTTP_PROXY`, `HTTPS_PROXY`, or
  broad `NO_PROXY` values as the enforcement mechanism; Obscura's own proxy
  flag is the routing input, and the isolated network is the boundary.

Internal-access goal:

- Obscura should be attached only to an Obscura control network and an
  Obscura egress network.
- The control network should contain only the CDP shim and the Obscura CDP
  listener needed for browser control; it must not include `api_server`, CRW,
  SearXNG, `myst-client`, host-facing bridges, or data services.
- The egress network should contain only Obscura and the
  `obscura-egress-proxy` listener on port `3128`.
- The browser egress proxy must block internal/private targets,
  `host.docker.internal`, single-label Docker service names, host gateway
  addresses, and link-local metadata ranges. Search-engine hosts are the
  intentional exception for this mode.

The important wrinkle is search engines. Obscura must be allowed to navigate
to Google, Brave Search, DuckDuckGo HTML, Startpage, and Bing because the
whole search path intentionally escalates SERPs to the browser renderer. It
therefore cannot use the existing `prefetch-blocking-proxy` as its final
upstream in the default configuration: that proxy deliberately returns `403`
for search-engine hosts to prevent CRW's raw HTTP prefetch from double-hitting
SERPs. This is the chicken-and-egg problem. If the Obscura browser egress path
forwards to `myst-client:3128`, the browser renderer would also be blocked
from the search engines it is supposed to render.

Viable options:

1. Add a search-allowed browser egress proxy in the shared namespace.
   - Reuse the same proxy implementation type as `prefetch-blocking-proxy`,
     but run it with search-host blocking disabled and internal/private-target
     blocking enabled.
   - It must perform the final outbound hop from `netns-holder`, or through
     `ONYX_AGENT_OUTBOUND_PROXY_URL` from that namespace.
   - Obscura cannot attach directly to a service that uses
     `network_mode: service:netns-holder`, so an internal-network bridge or
     dual-proxy shape is still needed.

2. Put Obscura on an internal network with a tiny TCP bridge to a
   search-allowed proxy listener in `netns-holder`.
   - The bridge exposes only the proxy port to Obscura.
   - The search-allowed proxy listener enforces destination policy and final
     routing.
   - This preserves the current VPN/no-VPN semantics because final egress
     still originates in the shared namespace.

3. Use a firewall-restricted browser namespace instead of proxy-only egress.
   - This would allow Obscura to keep ordinary browser networking while rules
     deny internal/private destinations and permit only the proxy/VPN path.
   - It is less attractive in Compose because Docker subnet allocation, DNS,
     IPv6, and host-gateway behavior all need continuous validation.

A unified proxy implementation would simplify this if it supports explicit
modes, for example:

```text
mode=prefetch   block_search=true   block_internal=true   allow_http=false
mode=browser    block_search=false  block_internal=true   allow_http=false
mode=executor   block_search=true   block_internal=true   allow_http=false
mode=searxng    block_search=false  block_internal=true   allow_http=false
```

The implementation can stay one code path, but the mode names make intent
visible in Compose and logs. The browser mode is not a weaker version of the
prefetch mode; it exists because search-engine access is required for the
Obscura renderer.

Cost/benefit:

- Benefit: high. It creates a network backstop behind Obscura's private-target
  block for the highest-risk renderer process.
- Complexity: high. It requires changing CDP addressing, adding a
  search-allowed egress path, preserving VPN final-hop behavior, and validating
  browser search success.
- Recommended priority: high, after code-interpreter executor isolation. Do
  not attempt it by pointing Obscura at the current search-blocking
  `prefetch-blocking-proxy`.

### CRW Restricted Container

CRW is the orchestration point between Onyx/SearXNG, the HTTP prefetch proxy,
the CDP shim, and SearXNG-backed `/v1/search`. It is less directly exposed to
hostile browser JavaScript than Obscura, but it accepts untrusted target URLs
and contains the logic that decides whether to satisfy a request from HTTP
prefetch or escalate to CDP.

Target shape:

```text
Onyx / SearXNG callers
  |
  v
crw on a crw-api network
  |
  | HTTP_PROXY / HTTPS_PROXY
  v
prefetch egress bridge/proxy
  |
  v
prefetch-blocking-proxy inside netns-holder

crw
  |
  | WebSocket
  v
cdp-shim on a crw-cdp network

crw
  |
  | /v1/search only
  v
SearXNG on a search-internal network
```

Egress routing goal:

- CRW should not have direct internet egress.
- Its HTTP prefetch path should reach only a local prefetch proxy shim port,
  for example `http://crw-prefetch-egress-proxy:3128`.
- That CRW-side shim should be a simple TCP forwarder whose only upstream is
  the shared `prefetch-blocking-proxy` in `netns-holder` at
  `myst-client:3128`.
- The shared `prefetch-blocking-proxy` remains responsible for the final
  outbound hop: through Mysterium, through `ONYX_AGENT_OUTBOUND_PROXY_URL`,
  through both, or through neither when no-VPN/no-proxy mode is explicitly
  selected.
- If `ONYX_AGENT_OUTBOUND_PROXY_URL` points at a host Docker proxy, CRW still
  reaches only its prefetch proxy shim; the shared final-hop proxy is the only
  piece that may use the host-proxy bypass.
- CRW should not be configured with a broad `NO_PROXY`; the only direct peers
  should be the CDP shim, SearXNG search endpoint, and caller-facing API
  networks described below.

Internal-access goal:

- CRW should be split onto narrow internal networks instead of the shared
  namespace or general default network.
- The CRW prefetch-egress network should expose only the proxy shim port.
- The CRW CDP network should expose only the CDP shim WebSocket endpoint.
- The CRW search network should expose only SearXNG's search API endpoint if
  CRW `/v1/search` remains enabled.
- The CRW API network should expose only CRW's Firecrawl-compatible API to
  Onyx, SearXNG custom engines, or an explicit host diagnostic bridge.
- CRW should have no route to `api_server` health endpoints, Obscura CDP/MCP
  directly, `myst-client` administrative ports, data stores, host gateway
  addresses, `host.docker.internal`, LAN/private ranges, or link-local
  metadata addresses except through the prefetch proxy shim's destination
  policy.

The strict version gives CRW no default-network attachment and no shared
namespace. It can only reach:

- the prefetch proxy URL in `HTTP_PROXY` / `HTTPS_PROXY`;
- the CDP shim URL in `CRW_RENDERER__CHROME__WS_URL`;
- SearXNG for `CRW_SEARCH__SEARXNG_URL`, if `/v1/search` is enabled;
- callers that need the Firecrawl-compatible API.

That requires replacing all current loopback assumptions:

- `CRW_RENDERER__CHROME__WS_URL` changes from
  `ws://127.0.0.1:9224/devtools/browser` to a DNS name on the CDP network;
- `CRW_SEARCH__SEARXNG_URL` changes from `http://127.0.0.1:8888` to a DNS name
  on a search network;
- SearXNG's CRW-backed engines change `CRW_SCRAPE_URL` from loopback to a
  service DNS name, preferably via a wrapper env var rather than a hardcoded
  constant;
- the healthcheck stops assuming CRW can scrape its own loopback health URL
  through the same namespace shape.

The prefetch egress path can reuse the code-interpreter bridge design: a raw
TCP forwarder from a CRW-only internal network to `myst-client:3128`.

Unlike Obscura, CRW's prefetch path should keep search-engine blocking.
Search-engine URLs should still receive local `403` at prefetch time so CRW
escalates to Obscura without sending the raw reqwest request to the provider.

Cost/benefit:

- Benefit: medium to high. It protects against CRW bugs that attempt direct
  internal access outside the documented proxy/CDP/search channels.
- Complexity: high. It touches the most loopback assumptions and spans Onyx,
  SearXNG, CDP shim, healthchecks, and host-facing bridges.
- Recommended priority: medium. It is valuable isolation work, but doing it
  before Obscura isolation risks spending complexity on plumbing while leaving
  the renderer in the broadest namespace.

### SearXNG Restricted Container

SearXNG is the easiest component to reason about in the default wrapper
configuration. The stock direct search engines are removed, and the enabled
web engines are `google2`, `brave2`, `duckduckgo2`, `startpage2`, and `bing2`;
those custom engines POST to CRW rather than fetching search providers
directly. In that mode SearXNG does not need general internet egress for web
search. It needs:

- inbound HTTP from Onyx and optional host diagnostics;
- outbound HTTP to CRW's `/v1/scrape`;
- access to `searxng-valkey`;
- optional outbound access for any non-CRW engines, autocomplete, favicons, or
  future features that are explicitly enabled.

Target shape for the default CRW-only engine set:

```text
Onyx / host-searxng-proxy
  |
  v
searxng-core on search-api/search-internal networks
  |
  +--> crw /v1/scrape
  +--> searxng-valkey
```

Egress routing goal:

- In the default CRW-backed engine set, SearXNG should have no general
  internet egress at all.
- Its only outbound HTTP path for web search should be the CRW scrape endpoint
  on an internal search/CRW API network.
- If future non-CRW engines, favicons, autocomplete, or other external
  features are intentionally enabled, they should use SearXNG's
  `outgoing.proxies` through a local SearXNG egress proxy shim port, for
  example `http://searxng-egress-proxy:3128`.
- That shim should forward to a suitable shared-namespace egress proxy so the
  final outbound hop follows the selected Mysterium/proxy/no-VPN matrix.
- If `ONYX_AGENT_OUTBOUND_PROXY_URL` points at a host Docker proxy, SearXNG
  should still reach only the local SearXNG egress shim; the final-hop shim is
  the only piece that may use the host-proxy bypass.

Internal-access goal:

- SearXNG should be attached only to the search API network, the CRW scrape
  network, and the Valkey network needed for its own cache/state.
- It should not share a namespace or network with `api_server`, Obscura,
  Obscura MCP, CDP shim, `myst-client` administrative ports, data stores,
  host-facing bridges, or host gateway aliases.
- If an external-engine egress shim is added, the SearXNG egress network
  should expose only that proxy shim port and should block internal/private
  targets, `host.docker.internal`, single-label service names, host gateway
  addresses, and link-local metadata ranges.

If SearXNG remains limited to CRW-backed web engines, it can be placed on an
internal search network without any internet egress proxy at all. That is the
cleanest version and would make accidental direct-engine traffic fail closed.
The `docker-compose.proxy.yml` SearXNG entrypoint would then only be needed if
non-CRW external engines are intentionally enabled.

If future SearXNG features need internet egress, use SearXNG's native
`outgoing.proxies` and per-engine `network` support rather than environment
proxy variables. In that model:

- the CRW-backed engines use a `direct` SearXNG network that means "direct to
  local CRW on the search-internal Docker network," not direct internet;
- external engines use a proxy-backed SearXNG outgoing network;
- the container network still prevents bypassing those settings with arbitrary
  direct sockets.

Cost/benefit:

- Benefit: medium. It narrows a Python web app and plugin surface, and it
  makes future accidental direct engines obvious.
- Complexity: low to medium if done before CRW isolation, medium if combined
  with CRW because both sides need DNS-name rewiring.
- Recommended priority: medium to high. This is likely the cheapest isolation
  win, especially if we first make `CRW_SCRAPE_URL` configurable in the custom
  engines.

### Cross-Component Cost/Benefit Decision Matrix

| Component | Main risk reduced | Isolation value | Complexity | Recommended order |
| --- | --- | --- | --- | --- |
| Code-interpreter executors | LLM-generated code bypassing proxy variables and reaching internal services | Very high | Medium to high | 1 |
| Obscura browser | Untrusted page JS or browser/proxy bugs reaching internal services or bypassing configured egress | High | High | 2 |
| SearXNG | Accidental direct engines or plugin/network features bypassing CRW | Medium | Low to medium | 3 |
| CRW | Scraper orchestration bugs or direct fetch paths bypassing configured proxy/CDP/search channels | Medium to high | High | 4 |

This order is not purely by security value. It also accounts for implementation
sequencing:

- executor isolation removes the largest already-documented arbitrary-code
  gap;
- Obscura isolation adds a backstop behind the untrusted browser renderer;
- SearXNG isolation is comparatively cheap once CRW's URL is configurable;
- CRW isolation is worthwhile, but it benefits from having the Obscura and
  SearXNG endpoints already moved off loopback and onto explicit internal
  networks.

The decision point for each component should be whether the extra network
surface remains smaller and clearer than the shared namespace it replaces. If
an isolation design requires a broad dual-homed gateway that can reach many
stack services, it has probably lost the benefit. Prefer several narrow,
named bridges over one general internal router.

## Security Properties After Executor Implementation

Expected improvements:

- executor pods no longer share loopback with `api_server`, `crw`, obscura,
  SearXNG, `myst-client`, or other shared-namespace services;
- executor pods cannot reach stack service DNS aliases through normal Docker
  networking;
- executor pods cannot bypass the proxy with raw sockets unless Docker network
  isolation is misconfigured;
- executor `NO_PROXY` no longer carves out stack-internal names;
- the existing prefetch/search path stays unchanged;
- `ONYX_AGENT_OUTBOUND_PROXY_URL` remains optional and no longer implies any
  executor sandbox changes by itself.

Remaining risks:

- the code-interpreter container still has Docker socket access in order to
  spawn executor pods; that trusted service remains part of the control plane;
- if the executor-only network is not truly internal or the wrong network name
  is injected, generated code could regain broader egress;
- with TCP forwarder bridges, all destination policy depends on the final-hop
  proxy policy being correct for that component;
- optional second proxy instances add value only when they have a distinct
  policy, log, rate-limit, or accounting role;
- environment variables are not a boundary. The boundary is the executor-only
  Docker network plus the absence of any reachable peer except the bridge.

## Executor Implementation Steps

1. Rename or replace `docker-compose.code-interpreter-vpn.yml`.
   - New recommended name: `docker-compose.code-interpreter-network.yml`.
   - Update Makefile suffix naming and docs references.

2. Add the executor-only network and bridge service.
   - Prefer a simple TCP forwarder to the shared `prefetch-blocking-proxy`.
   - Keep the existing `prefetch-blocking-proxy` unchanged inside
     `netns-holder`.
   - Do not publish bridge ports to the host.

3. Move executor proxy injection out of `docker-compose.proxy.yml`.
   - Keep trusted-service proxy routing in `docker-compose.proxy.yml`.
   - Put executor proxy URL and executor `NO_PROXY` in the code-interpreter
     network overlay.

4. Tighten `sitecustomize_code_interpreter`.
   - No-op when `ONYX_CODE_INTERPRETER_ENABLE_NETWORK` is not true.
   - Fail closed if network-enabled mode lacks an executor network or proxy
     URL.
   - Reject or warn loudly on `container:onyx-netns-holder-1`.
   - Inject only the dedicated executor proxy and narrow executor `NO_PROXY`.

5. Update docs.
   - `.env.wrapper.example`: describe the new meaning of
     `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`.
   - `docs/vpn_routing_and_proxies.md`: replace shared-namespace executor
     routing text with proxy-only routing.
   - `docs/internal_network_security.md`: move the shared-namespace gap from
     current behavior to historical/background context, then document the new
     remaining risks.
   - `docs/request_handling.md`: describe the executor proxy path and search
     engine blocking behavior.
   - `docs/onyx_patch_info.md` and `docs/onyx_patches_upgrade.md`: update the
     code-interpreter patch mechanics and upgrade checklist.

6. Remove obsolete wording and names.
   - Replace "code-interpreter-vpn" wording where it no longer matches.
   - Remove statements saying network-enabled executors can reach internal
     stack endpoints, except where retained as pre-change threat context.

## Follow-On Component Decision Steps

After executor isolation is implemented and validated, decide which request
path components are worth isolating. Suggested order:

1. Obscura browser.
   - Design a search-allowed egress proxy mode before moving the container.
   - Verify SERP rendering still works for every custom SearXNG engine.

2. SearXNG.
   - First make the CRW scrape URL configurable in the custom engines.
   - Then move SearXNG and Valkey onto explicit search networks with no
     general internet egress for the default CRW-backed engine set.

3. CRW.
   - Move CRW only after its Obscura, SearXNG, caller, and prefetch-proxy
     dependencies have stable DNS-name endpoints.
   - Re-test `web_search`, `open_url`, CRW `/v1/search`, PDF prefetch
     behavior, and healthchecks after the loopback assumptions are removed.

## Validation Plan

Use `ENV_FILE=/dev/null` and explicit environment values so validation does not
read local private `.env.wrapper`.

### Static Checks

- `ENV_FILE=/dev/null make help`
- `python -m py_compile crw/prefetch_blocking_proxy.py onyx/patches/sitecustomize_code_interpreter/sitecustomize.py`
- `git diff --check`

### Compose Shape Checks

Inspect effective compose output for these permutations:

1. Network disabled, upstream proxy empty.
   - No executor-only network.
   - No bridge service.
   - No executor proxy env injection.
   - Executor network remains upstream `none`.

2. Network disabled, upstream proxy set.
   - `docker-compose.proxy.yml` applies only to trusted services.
   - No executor-only network.
   - No bridge service.
   - No executor proxy env injection.

3. Network enabled, upstream proxy empty, `MYST_VPN_ENABLED=true`.
   - Executor-only internal network exists.
   - Bridge service exists.
   - Executor network is the executor-only network.
   - Executor proxy URL points at the bridge.
   - Shared proxy has no upstream proxy and remains in `netns-holder`.

4. Network enabled, upstream proxy set, `MYST_VPN_ENABLED=true`.
   - Same executor-only network and bridge shape.
   - Shared proxy receives `ONYX_AGENT_OUTBOUND_PROXY_URL`.
   - Executor pods still point at the local bridge URL, not the upstream URL.
   - For `ONYX_AGENT_OUTBOUND_PROXY_URL=socks5h://host.docker.internal:9150`,
     confirm the final-hop proxy shim can reach the host proxy only when
     `MYST_VPN_ALLOW_LAN_BYPASS=true` or an equivalent shim-scoped host-proxy
     allowlist is enabled.

5. Network enabled, `MYST_VPN_ENABLED=false`.
   - Same executor-only network and bridge shape.
   - No shared-namespace VPN connection is expected.
   - Executor isolation still depends on the executor-only network and bridge,
     not on Myst.

### Runtime Checks When Safe

With a running stack and network-enabled code-interpreter:

- from an executor pod, `curl --noproxy '*' https://example.com` should fail;
- from an executor pod, `curl https://example.com` should succeed through the
  proxy if egress is available;
- from an executor pod, direct requests to `http://api_server:8080/health`,
  `http://myst-client:9101/health`, `http://crw:3010/health`, and
  `http://host.docker.internal:*` should fail;
- from an executor pod, proxied requests to internal/private targets should
  return proxy `403`;
- from an executor pod, proxied requests to configured search engines should
  return proxy `403`;
- proxy logs should identify executor traffic distinctly if using the second
  proxy instance;
- CRW search and `open_url` behavior should remain unchanged.

### Prompt/Capability Checks

Confirm the LLM-facing tool descriptions still say network access is available
only when `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`, but avoid implying broad
network access. Preferred wording should be closer to:

```text
Network access is available through a restricted HTTP/HTTPS proxy. Direct
socket access and internal/private network targets are blocked.
```

## Decision Summary

Use restricted component networks and local proxy shim ports, not broad access
to the shared `netns-holder` namespace.

Do not dual-home final-hop proxies into restricted component networks. Keep
final-hop proxies in the routing namespace where their outbound behavior is
clear, and expose them to restricted components through simple TCP forwarders.

Use the search-blocking `prefetch-blocking-proxy` as the final-hop policy for
code-interpreter executors and CRW HTTP prefetch. Add a separate
search-allowed browser final-hop proxy for Obscura, because Obscura must render
search-engine pages. Give SearXNG no internet egress in the default CRW-backed
engine configuration; add a SearXNG egress forwarder only if external SearXNG
features are intentionally enabled.

A second proxy instance is not the default bridge. Add one only when it has a
distinct policy, logging, rate-limit, or accounting role that a TCP forwarder
cannot provide.
