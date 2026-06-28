# Internal Network Security Investigation

This document records the June 28, 2026 investigation into whether
LLM-controlled network paths can reach localhost, Docker service names,
internal service IPs, or stack API endpoints.

The main conclusion is mixed:

- Obscura and CRW currently block private/internal targets from the tested
  browser and Onyx `open_url()` agent tool.
- Onyx SSRF settings affect only Onyx-managed URL-fetching paths and startup
  defaults for the Admin Security Hardening policy; they are not firewall
  rules for CRW, Obscura, or code-interpreter.
- When code-interpreter networking is enabled, generated code can reach
  loopback, shared-namespace services, Docker service aliases, and
  `host.docker.internal` directly. Proxy environment variables do not close
  that gap.

## Version Scope

The investigation was conducted against the committed pins in
`stack.versions.env`:

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

The live stack was running in full mode. The relevant browser, scraper, Onyx,
RAG, and code-interpreter services shared the `netns-holder` network namespace.

## Live Namespace Shape

In the inspected full stack, these services shared the network namespace owned
by `onyx-netns-holder-1`:

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

That namespace exposed many local listeners, including Onyx Web/API, CRW,
Obscura CDP/MCP, SearXNG, code-interpreter, doc-drop, the embedding shim, and
the prefetch proxy. Private and LAN routes were also present through the Docker
bridge, while general egress used the Mysterium tunnel.

The important security point: if an LLM-controlled process can make arbitrary
network connections from this namespace, there are useful internal targets to
reach.

## CRW And `open_url`

As documented in `docs/request_handling.md`, the recommended `open_url()` tool
request path is:

```text
Onyx FirecrawlClient -> CRW /v1/scrape -> CDP shim -> Obscura
```

CRW rejected direct scrape targets for the tested local and internal forms:

- `http://127.0.0.1:9101/health`
- `http://localhost:9101/health`
- `http://172.18.0.8:9101/health`
- `http://api_server:8080/health`
- `http://myst-client:9101/health`
- `http://host.docker.internal:3000/`

The observed response was a `400` invalid request with messages such as
`Access to 127.0.0.1 is not allowed`, `host is not allowed`, or
`Access to 172.18.0.8 is not allowed`.

This means the CRW-backed `open_url` main-document path currently blocks
loopback, Docker bridge IPs, Docker DNS aliases that resolve to private
addresses, and `host.docker.internal`.

This protection belongs to CRW/Obscura, not to Onyx's upstream
`ssrf_safe_get()` path. In the FirecrawlClient configuration, Onyx posts the
target URL inside JSON to CRW; Onyx's direct URL fetch validator is not the
component deciding whether that target is allowed.

## Obscura

Obscura `v0.1.9` has a private-network deny behavior by default. The CLI help
documents `--allow-private-network` as the opt-in that permits loopback,
RFC1918, and link-local fetches.

The investigation confirmed that this block applies after DNS resolution:

- `http://host.docker.internal:3000/` failed by default and succeeded with
  `--allow-private-network`.
- `http://api_server:8080/health` failed by default and succeeded with
  `--allow-private-network`.
- `http://myst-client:9101/health` failed by default and succeeded with
  `--allow-private-network`.

Rendered-page probes were also tested. A temporary HTTP listener was bound in
the shared namespace, and an Obscura-rendered page attempted to reach it with
`fetch()`, `sendBeacon()`, image, script, and iframe subresources. With the
default Obscura configuration, no loopback or service-DNS request reached the
listener. With `--allow-private-network`, the control request did reach the
listener.

The running `obscura` service command did not include
`--allow-private-network`, and its environment did not set
`OBSCURA_ALLOW_PRIVATE_NETWORK`.

Do not rely on browser same-origin policy or CORS as the stack-internal access
control boundary. The meaningful boundary here is Obscura's private-network
block. If that block is disabled, a hostile page can at least attempt
connections to reachable internal services, even when the browser cannot read
every response.

## SearXNG DNS Note

`searxng-core` did not behave like the shared-namespace aliases in the Obscura
DNS-name probe. It failed even with `--allow-private-network`. That appears to
be a Docker DNS artifact of services using `network_mode: service:netns-holder`:
the stable aliases on `netns-holder`, such as `api_server` and `myst-client`,
are the meaningful service-DNS names for this namespace.

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

Executor pods then inherit the shared stack namespace. In the live stack, direct
probes from code-interpreter reached:

- `http://127.0.0.1:9101/health`
- `http://172.18.0.8:9101/health`
- `http://api_server:8080/health`
- `http://myst-client:9101/health`
- `http://host.docker.internal:3000/`

This is expected from the current override and is the largest remaining gap.
Generated code can use raw sockets or tools that ignore proxy variables.
Therefore `ONYX_AGENT_OUTBOUND_PROXY_URL`, `HTTP_PROXY`, `HTTPS_PROXY`,
`ALL_PROXY`, and `NO_PROXY` are routing hints, not a security boundary.

The current proxy override also intentionally sets `NO_PROXY` for internal
loopback and Docker DNS names so normal stack-internal service calls stay off
the upstream proxy. That is appropriate for trusted service code, but it is the
opposite of what an untrusted network-enabled executor needs.

## Onyx SSRF Protection Interaction

The wrapper seeds Onyx Admin -> Security Hardening with:

```env
ONYX_SECURITY_SSRF_VALIDATE_OPEN_URL=true
ONYX_SECURITY_SSRF_ALLOW_PRIVATE_NETWORK=true
ONYX_SECURITY_SSRF_ALLOW_LOOPBACK=false
```

For Onyx `v4.1.7`, that maps to the "Allow Private Network" posture when no
Admin UI value has been saved yet. The intended reason is full-mode local RAG:
the Web connector must be able to crawl the trusted local doc-drop server, and
some MCP/OAuth use cases may need private LAN or `host.docker.internal`
addresses. Loopback is still blocked in that default posture.

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
