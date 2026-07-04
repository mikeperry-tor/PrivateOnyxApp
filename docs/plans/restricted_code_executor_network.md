# Restricted Code-Interpreter Executor Network Plan

## Goal

Change `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true` from "executor pods join
the shared stack/VPN namespace" to "executor pods can reach only a controlled
HTTP proxy endpoint."

The intended end state:

- `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=false` remains easy to audit: executor
  pods use upstream `network none`, no executor egress bridge is started, and
  no proxy environment is injected into executors.
- `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true` gives generated Python and shell
  code a single network capability: connect to a local HTTP proxy service.
- The proxy service blocks search engine URLs and internal/private targets, and
  then forwards allowed HTTP and HTTPS requests through the same egress path as
  the existing CRW prefetch path.
- `ONYX_AGENT_OUTBOUND_PROXY_URL` continues to mean "optional upstream proxy
  for internet egress" rather than "executor network isolation."
- `MYST_VPN_ENABLED=true` and `MYST_VPN_ENABLED=false` keep their current
  explicit meanings for the final outbound hop.

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

## Can We Use `prefetch-blocking-proxy` Itself As The Bridge?

There are three possible interpretations.

### Option A: Move The Existing Service To Both Networks

This is not recommended.

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

### Option B: Run A Second Instance Of `prefetch-blocking-proxy`

This is viable and is the best defense-in-depth bridge if we accept one extra
service instance.

The second instance would be named something like
`code-interpreter-egress-proxy`. It would attach to both the executor-only
network and the default network, but not to `network_mode:
service:netns-holder`.

It would run the same `crw/prefetch_blocking_proxy.py` code with:

```env
PREFETCH_PROXY_HOST=0.0.0.0
PREFETCH_PROXY_PORT=3128
ONYX_AGENT_OUTBOUND_PROXY_URL=http://myst-client:3128
PREFETCH_BLOCK_HOSTS=...
```

Then each executor request is validated twice:

1. the executor-facing instance blocks obvious search-engine and internal
   destinations before forwarding;
2. the shared-namespace instance at `myst-client:3128` repeats the block and
   performs the final egress.

This has useful properties:

- executor pods still only see one ordinary HTTP proxy;
- bridge-side validation logs are specific to code-interpreter traffic;
- the final outbound hop remains inside the existing shared namespace;
- the CRW prefetch path remains unchanged;
- search-engine blocking remains consistent with the current proxy behavior.

The main caveat is DNS validation. When the executor-facing instance is
configured with `ONYX_AGENT_OUTBOUND_PROXY_URL=http://myst-client:3128`, it
will treat the shared proxy as an upstream proxy. In the current proxy design,
that means it should avoid resolving arbitrary target DNS itself to avoid DNS
leaks through the wrong path. The shared-namespace proxy remains responsible
for target DNS classification when no final upstream proxy is configured.

That is acceptable because the executor-facing instance still blocks literal
private IPs, localhost names, `host.docker.internal`, single-label Docker-style
names, and configured search-engine hosts without target DNS. The final proxy
then enforces the full policy.

### Option C: Use A Raw TCP Forwarder

This is the simplest bridge. A tiny service forwards:

```text
executor network :3128 -> myst-client:3128
```

Benefits:

- very small compose surface;
- no duplicate proxy configuration;
- all policy lives in the existing `prefetch-blocking-proxy`;
- fewer moving parts to reason about.

Costs:

- no executor-specific validation or logs before the shared proxy;
- any bug or misconfiguration in the shared proxy is the only application-level
  policy gate;
- the bridge is a generic tunnel from executor pods to the proxy port, so its
  security relies entirely on Docker network isolation and the shared proxy.

This is acceptable if the priority is minimal implementation complexity. The
second-instance proxy is better if the priority is defense in depth and
diagnostics.

### Recommendation

Do not move the existing `prefetch-blocking-proxy`.

Keep the existing service in `network_mode: service:netns-holder` so CRW,
obscura-related behavior, Mysterium routing, and optional upstream proxy
semantics stay unchanged.

For the executor bridge, prefer a second instance of
`prefetch_blocking_proxy.py` if we want redundant destination checks and
executor-specific logs. Prefer a raw TCP forwarder only if we want the smallest
possible overlay and are comfortable relying on the shared proxy as the sole
application-level enforcement point.

In either case, the bridge should be introduced only by the
code-interpreter-network overlay, not by the general proxy overlay.

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

### DNS Classification

The shared proxy's current DNS behavior should remain:

- with no upstream proxy, it resolves targets itself and blocks names that
  resolve to non-global/internal addresses;
- with an upstream proxy, it avoids target DNS resolution to prevent leaking
  target DNS outside the configured proxy path.

If the executor bridge is a second proxy instance, configure it so the shared
proxy remains the final destination-classification point. The executor-facing
instance should still block literal internal targets and configured search
engine hostnames before forwarding.

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

## Security Properties After Implementation

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
- if the bridge is a raw TCP forwarder, all destination policy depends on the
  shared proxy;
- if the bridge is a second proxy instance, policy is stronger but compose
  layering and logs are more complex;
- environment variables are not a boundary. The boundary is the executor-only
  Docker network plus the absence of any reachable peer except the bridge.

## Implementation Steps

1. Rename or replace `docker-compose.code-interpreter-vpn.yml`.
   - New recommended name: `docker-compose.code-interpreter-network.yml`.
   - Update Makefile suffix naming and docs references.

2. Add the executor-only network and bridge service.
   - Prefer the second `prefetch_blocking_proxy.py` instance for redundant
     validation and executor-specific logs.
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

Use a proxy-only executor network, not the shared `netns-holder` namespace.

Do not dual-home the existing `prefetch-blocking-proxy`; keep it in the shared
namespace for CRW and final egress. Add a separate executor bridge only when
`ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`.

For maximum simplicity, the bridge can be a raw TCP forwarder to
`myst-client:3128`. For stronger defense in depth and clearer executor logs,
run a second instance of `prefetch_blocking_proxy.py` as the bridge and point
its upstream at `http://myst-client:3128`. The second-instance proxy is the
recommended implementation unless the extra service proves too noisy in
compose layering.
