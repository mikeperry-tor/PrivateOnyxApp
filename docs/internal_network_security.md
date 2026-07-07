# Internal Network Security

This document describes the current internal-network security posture for
LLM-controlled network paths, including localhost, Docker service names,
internal service IPs, and stack API endpoints.

The main conclusions:

- Obscura and CRW block private/internal targets from the browser and Onyx
  `open_url()` agent tool paths.
- The prefetch-blocking proxy performs destination validation for `CONNECT`
  and HTTP forwarding. It blocks localhost, `host.docker.internal`,
  single-label Docker-style hostnames, non-global IP literals, and DNS names
  that resolve to blocked addresses when no explicit upstream proxy is
  configured. Code-interpreter executor HTTP clients that honor injected proxy
  variables use the same HTTP listener when an upstream proxy is configured or
  when `ONYX_AGENT_ALLOW_HTTP_URLS=false`, and therefore keep the same
  internal-target, search-engine, and cleartext-HTTP blocks.
- Onyx SSRF settings affect only Onyx-managed URL-fetching paths and startup
  defaults for the Admin Security Hardening policy; they are not firewall
  rules for CRW, Obscura, or code-interpreter.
- When code-interpreter networking is enabled, generated code can reach
  loopback, shared-namespace services, Docker service aliases, and
  `host.docker.internal` directly. Proxy environment variables do not close
  that gap.

## Version Scope

This document applies to the committed pins in `stack.versions.env`:

| Component | Version / image |
| --- | --- |
| Onyx | `onyxdotapp/onyx-backend:v4.1.7` and `onyxdotapp/onyx-web-server:v4.1.7` |
| Code interpreter | `onyxdotapp/code-interpreter:0.4.4` |
| CRW | `ghcr.io/us/crw:0.18.3` |
| Obscura | `h4ckf0r0day/obscura:0.1.9` |
| SearXNG | `searxng/searxng:2026.6.26-f8ffbf36f` |
| Teep | `13rac1/teep:cacfa5ab2a4e8cc52ec8a2020a763f7306ad3438` |
| Mysterium | `mysteriumnetwork/myst:docker_host_fixes_with_logs` |
| netns holder | `alpine:3.20` |
| Python sidecars | `python:3.12-slim-bookworm`, `python:3.12-alpine` |
| Tailscale | `tailscale/tailscale:stable` |
| MinIO | `minio/minio:RELEASE.2025-07-23T15-54-02Z-cpuv1` |
| Valkey | `docker.io/valkey/valkey:9-alpine` |

In full mode, the relevant browser, scraper, Onyx, RAG, and code-interpreter
services share the `netns-holder` network namespace.

Related implementation docs:

- [Request handling](request_handling.md) describes the `web_search` and
  `open_url` chains through SearXNG, CRW, the prefetch proxy, the CDP shim, and
  Obscura.
- [VPN routing and proxies](vpn_routing_and_proxies.md) describes the shared
  namespace, optional Mysterium/proxy routing, and code-interpreter networking
  switch.
- [Local document RAG search](local_docs_rag_search.md) describes the
  doc-drop and embedding-shim paths that motivate the default Onyx SSRF
  posture.
- [Onyx patch information](onyx_patch_info.md) and
  [Onyx wrapper patches](onyx_patches_upgrade.md) document the runtime patches,
  shims, and upgrade checks relevant to this posture.

## Namespace Shape

In full mode, these services share the network namespace owned by
`onyx-netns-holder-1`:

- `myst-client`
- `searxng-core`
- `obscura`
- `obscura-mcp`
- `cdp-shim`
- `prefetch-blocking-proxy`
- `crw`
- `api_server`
- `web_server`
- `nginx`
- `background`
- `doc-drop-web`
- `local-embedding-shim`
- `code-interpreter`

That namespace exposes many local listeners, including Onyx Web/API, CRW,
Obscura CDP/MCP, SearXNG, code-interpreter, doc-drop, the embedding shim, and
the prefetch proxy. Private and LAN routes exist through the Docker bridge,
while general egress uses the Mysterium tunnel when VPN mode is enabled.

The important security point: if an LLM-controlled process can make arbitrary
network connections from this namespace, there are useful internal targets to
reach.

## CRW And `open_url`

As documented in `docs/request_handling.md`, the recommended `open_url()` tool
request path is:

```text
Onyx FirecrawlClient -> CRW /v1/scrape -> CDP shim -> Obscura
```

CRW rejects direct scrape targets for local and internal forms such as:

- `http://127.0.0.1:9101/health`
- `http://localhost:9101/health`
- `http://172.18.0.8:9101/health`
- `http://api_server:8080/health`
- `http://myst-client:9101/health`
- `http://host.docker.internal:3000/`

The response is a `400` invalid request with messages such as
`Access to 127.0.0.1 is not allowed`, `host is not allowed`, or
`Access to 172.18.0.8 is not allowed`.

The CRW-backed `open_url` main-document path blocks loopback, Docker bridge
IPs, Docker DNS aliases that resolve to private addresses, and
`host.docker.internal`.

This protection belongs to CRW/Obscura, not to Onyx's upstream
`ssrf_safe_get()` path. In the FirecrawlClient configuration, Onyx posts the
target URL inside JSON to CRW; Onyx's direct URL fetch validator is not the
component deciding whether that target is allowed.

## Obscura

Obscura `v0.1.9` has a private-network deny behavior by default. The CLI help
documents `--allow-private-network` as the opt-in that permits loopback,
RFC1918, and link-local fetches.

This block applies after DNS resolution:

- `http://host.docker.internal:3000/` is blocked by default and allowed with
  `--allow-private-network`.
- `http://api_server:8080/health` is blocked by default and allowed with
  `--allow-private-network`.
- `http://myst-client:9101/health` is blocked by default and allowed with
  `--allow-private-network`.

Rendered pages cannot reach loopback or service-DNS targets with `fetch()`,
`sendBeacon()`, image, script, or iframe subresources under the default
Obscura configuration. The `--allow-private-network` flag permits these
private-network attempts.

The `obscura` service command excludes `--allow-private-network`, and its
environment leaves `OBSCURA_ALLOW_PRIVATE_NETWORK` unset.

Do not rely on browser same-origin policy or CORS as the stack-internal access
control boundary. The meaningful boundary here is Obscura's private-network
block. If that block is disabled, a hostile page can at least attempt
connections to reachable internal services, even when the browser cannot read
every response.

## SearXNG DNS Note

`searxng-core` does not behave like the shared-namespace aliases in Obscura
DNS-name checks. Docker DNS aliases on `netns-holder`, such as `api_server`
and `myst-client`, are the meaningful service-DNS names for this namespace.

## Onyx SSRF Protection Interaction

The wrapper seeds Onyx Admin -> Security Hardening with:

```env
ONYX_SECURITY_SSRF_VALIDATE_OPEN_URL=true
ONYX_SECURITY_SSRF_ALLOW_PRIVATE_NETWORK=true
ONYX_SECURITY_SSRF_ALLOW_LOOPBACK=false
```

For Onyx `v4.1.7`, that maps to the "Allow Private Network" posture when no
Admin UI value has been saved. The intended reason is full-mode local RAG:
the Web connector must be able to crawl the trusted local doc-drop server, and
some MCP/OAuth use cases may need private LAN or `host.docker.internal`
addresses. Loopback is blocked in that default posture.

Important boundaries:

- These env vars are startup defaults only. Once an admin saves Security
  Hardening settings in the UI, the saved UI value is authoritative.
- They apply to Onyx-managed URL-fetching paths such as the fallback
  `OnyxWebCrawler` path that uses `ssrf_safe_get()`.
- They do not apply to CRW's own target validation.
- They do not apply to Obscura's browser network stack.
- They do not apply to code-interpreter executor pods or arbitrary generated
  code.
- They do not govern the local embedding shim's upstream call.

If the fallback `OnyxWebCrawler` is active instead of the recommended CRW
Firecrawl provider, Onyx SSRF policy becomes the main protection for
LLM-controlled `open_url` fetches. A stricter "Validate All" posture may block
full-mode doc-drop crawling. A broader "Disabled" posture, or
`ONYX_SECURITY_SSRF_ALLOW_LOOPBACK=true` during first-run seeding, can allow
loopback access on Onyx-managed fetch paths and should be avoided unless a
specific trusted local integration requires it.

## Prefetch-Blocking Proxy

The wrapper's `crw/prefetch_blocking_proxy.py` exists to shape CRW's HTTP
prefetch behavior:

- known search-engine hosts receive `403` without an upstream request;
- non-search plain HTTP requests receive `403` by default with a message
  telling the caller to use HTTPS, unless `ONYX_AGENT_ALLOW_HTTP_URLS=true`;
- non-search `CONNECT` requests are tunneled.

The proxy listens on `0.0.0.0:3128` inside the shared namespace so CRW can use
`HTTP_PROXY`/`HTTPS_PROXY=http://127.0.0.1:3128`. It also acts as a destination
validator and rejects blocked targets without opening an upstream connection:

- literal loopback, private/RFC1918, link-local, multicast, unspecified,
  reserved, and other non-global IP addresses are blocked;
- legacy IPv4 shorthand forms such as `2130706433` and `0177.0.0.1` are
  normalized and classified during validation;
- `localhost`, `host.docker.internal`, subdomains of those names, and
  single-label Docker-style hostnames such as `api_server` and `myst-client`
  are blocked by name;
- when `ONYX_AGENT_OUTBOUND_PROXY_URL` is empty, DNS names are resolved locally
  and the proxy blocks the request if any returned address is internal or
  otherwise non-global;
- when `ONYX_AGENT_OUTBOUND_PROXY_URL` is set, the proxy intentionally does not
  perform target DNS resolution for this validation step, avoiding target DNS
  leakage outside the configured upstream proxy path;
- blocked destination attempts are logged at warning level with the method,
  host, port, peer, and block reason;
- blocked direct proxy calls return `403` without opening a tunnel or HTTP
  forwarding path.

This proxy validation aligns with the CRW and Obscura protections:

- CRW rejects internal `open_url` scrape targets at target validation.
- Obscura's private-network block prevents rendered pages from reaching
  `myst-client:3128` by default.
- The CDP shim applies the same `ONYX_AGENT_ALLOW_HTTP_URLS=false` default to
  `Page.navigate` and `Target.createTarget`, so cleartext HTTP browser
  navigations are also rejected before reaching Obscura.

However, any network-enabled process already running inside the shared
namespace can call the proxy directly. That includes network-enabled
code-interpreter executor pods. In that mode the proxy is an additional
internal-access path, not a boundary.

The code-interpreter patch points `HTTP_PROXY` and `HTTPS_PROXY` at
`http://127.0.0.1:3128` for executor pods when `ONYX_AGENT_OUTBOUND_PROXY_URL`
is configured, or when `ONYX_AGENT_ALLOW_HTTP_URLS=false`. That gives Python
`urllib` an ordinary HTTP proxy endpoint while the sidecar adapts upstream
egress to HTTP, HTTPS, SOCKS5, or SOCKS5h if an upstream proxy is configured.
The same destination validation applies, and configured search-engine hosts
still receive `403`. Plain HTTP URLs still receive the HTTPS guidance error
unless `ONYX_AGENT_ALLOW_HTTP_URLS=true`. If the configured upstream is an
`https://` proxy, this adapter verifies the proxy certificate, uses SNI, and
requires TLS 1.3 by default when the Python/OpenSSL runtime supports it.

This proxy should not be treated as the only boundary for a namespace shared
with untrusted code. If code-interpreter networking is enabled, executor pods
can reach other shared-namespace listeners directly unless their network
placement prevents it.

## Other Wrapper Shims And Sidecars

Other wrapper-managed listeners are also reachable inside the shared namespace:

- `cdp-shim` listens on `0.0.0.0:9224` and transparently forwards CDP traffic
  to Obscura on `127.0.0.1:9222`, while rewriting selected methods for
  wait/load behavior, stealth-script stripping, proxy-field stripping, and
  cookie clearing.
- `local-embedding-shim` listens on `0.0.0.0:9101` and forwards embedding
  requests to the configured OpenAI-compatible upstream.
- `doc-drop-web` listens on `0.0.0.0:8091` and serves the mounted local document
  directory read-only.
- Obscura's own CDP and MCP services listen on `0.0.0.0:9222` and
  `0.0.0.0:9223`.

These services are intended to be internal stack APIs, not public endpoints.
Obscura's private-network block protects rendered browser traffic from these
listeners by default. Any process with network access inside the shared
namespace can call these APIs directly. Network-enabled code-interpreter
executors therefore should be treated as able to call these APIs unless their
network placement prevents it.

For this reason, shim hardening should combine service-level validation with
namespace isolation. Binding an internal shim to loopback is useful only when
the only loopback peers in that namespace are trusted. Once untrusted executor
pods inherit the same namespace, loopback listeners become reachable by that
untrusted code.

## Code-Interpreter Gap

The default documented value is:

```env
ONYX_CODE_INTERPRETER_ENABLE_NETWORK=false
```

When it is false, upstream executor pods keep network isolation and generated
Python or shell code cannot make arbitrary network requests.

When `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`, the Makefile layers
`docker-compose.code-interpreter-vpn.yml`. That sets:

```env
PYTHON_EXECUTOR_DOCKER_NETWORK=container:onyx-netns-holder-1
```

Executor pods then inherit the shared stack namespace. In network-enabled mode,
generated code can reach internal targets such as:

- `http://127.0.0.1:9101/health`
- `http://172.18.0.8:9101/health`
- `http://api_server:8080/health`
- `http://myst-client:9101/health`
- `http://host.docker.internal:3000/`

This is expected from the network-enabled override and is the largest code
execution security gap. Generated code can use raw sockets or tools that ignore
proxy variables.
Therefore `ONYX_AGENT_OUTBOUND_PROXY_URL`, `HTTP_PROXY`, `HTTPS_PROXY`,
`ALL_PROXY`, and `NO_PROXY` are routing hints, not a security boundary.
The local HTTP proxy adapter closes the urllib/SOCKS compatibility gap for
ordinary HTTP/HTTPS clients and gives the executor path a single proxy URL for
all upstream proxy schemes. It also applies the default
`ONYX_AGENT_ALLOW_HTTP_URLS=false` plain-HTTP block to ordinary executor HTTP
clients that honor proxy variables, even when no upstream proxy is configured.
Generated code can still bypass those environment variables with raw sockets,
explicit no-proxy options, or tools that ignore proxy settings.

The proxy override intentionally sets `NO_PROXY` for internal
loopback and Docker DNS names so normal stack-internal service calls stay off
the upstream proxy. That is appropriate for trusted service code, but it is the
opposite of what an untrusted network-enabled executor needs.

## Options To Close Code-Interpreter Gaps

The conservative option is to leave:

```env
ONYX_CODE_INTERPRETER_ENABLE_NETWORK=false
```

That preserves upstream executor network isolation and avoids giving
LLM-generated code direct access to the shared namespace.

If networked code execution is required, the safer designs all avoid placing
executor pods directly in `container:onyx-netns-holder-1`.

### Option 1: Proxy-Only Executor Network

Create a dedicated internal Docker network for executor pods and attach only a
small egress proxy/gateway to it. Executor pods can reach the proxy, but cannot
route directly to the shared stack namespace, Docker bridge, host gateway,
RFC1918 networks, loopback outside the pod, or link-local metadata addresses.

The proxy/gateway should:

- resolve destination hostnames itself;
- reject loopback, RFC1918, Docker bridge, link-local, multicast, and
  `host.docker.internal` destinations after DNS resolution;
- reject DNS rebinding by validating every resolved address;
- deny CONNECT requests to internal addresses;
- fail closed if destination classification fails;
- optionally route allowed egress through Mysterium or the configured upstream
  proxy.

This is the cleanest model because it gives untrusted executor pods exactly one
network capability: talk to the controlled egress proxy.

The current proxy simplification is a useful prerequisite for this model:
executor tools no longer need direct SOCKS support or per-client transport
libraries. They can all be pointed at one ordinary HTTP proxy URL. To make that
a security boundary, however, executor pods must stop using
`container:onyx-netns-holder-1`; the proxy endpoint must be reachable from the
dedicated executor network, and other stack services must not be reachable from
that network.

### Option 2: Dedicated Executor VPN Namespace With Firewall Deny Rules

Use a separate namespace for code-interpreter executors rather than
`netns-holder`. That namespace may have its own VPN/proxy routing, but it
should install firewall rules before executors start.

Minimum deny list:

- `127.0.0.0/8`
- `::1/128`
- `10.0.0.0/8`
- `172.16.0.0/12`
- `192.168.0.0/16`
- Docker bridge subnets used by the stack
- `169.254.0.0/16`
- IPv6 unique-local and link-local ranges
- the host gateway and `host.docker.internal`
- any explicit stack service IPs or aliases

This is harder to keep correct than the proxy-only model because Docker subnet
allocation and host gateway addresses can vary. It also needs careful ordering:
the namespace must fail closed if firewall setup fails.

### Option 3: Runtime Executor Command Hardening

Patch the code-interpreter executor launch path to add Docker-level network
controls instead of using `container:onyx-netns-holder-1`.

Potential controls:

- put executor pods on a dedicated `internal: true` Docker network;
- remove stack aliases and shared namespace access;
- inject only the proxy endpoint needed for egress;
- avoid `NO_PROXY` entries for stack services in untrusted executor pods;
- block access to the Docker socket from executor pods;
- keep resource limits and timeouts independent of network policy.

This option fits the existing `sitecustomize_code_interpreter` patch point, but
it should not rely only on environment variables. The generated code can bypass
language-level proxy handling.

## Recommended Policy

Keep these as the default safety posture:

- `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=false`
- Obscura without `--allow-private-network`
- CRW private-target validation enabled
- Onyx Security Hardening at "Allow Private Network" only when full-mode
  doc-drop crawling or trusted private integrations require it
- avoid saving the broader "Disabled" Onyx Security Hardening posture unless
  the deployment intentionally permits loopback fetches

If code-interpreter networking is enabled for a trusted single-tenant session,
assume generated code can reach internal stack APIs until one of the network
isolation designs above is implemented and verified.
