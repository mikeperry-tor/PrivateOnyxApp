# Internal Network Security

This document describes the network-enforced boundaries for attacker-influenced
and application outbound paths at the pins in `stack.versions.env`.

## Primary boundary

Every Onyx application, data, request-gateway, Teep caller, code-interpreter
control, and RAG network is `internal: true`. Onyx applications do not share
`netns-holder`; proxy variables are route selectors, not the sandbox. A client
that ignores them has no external or host-gateway route.

Only Myst, the two minimal Onyx route brokers, and explicitly selected
VPN-side services share `netns-holder`. Public and host request-policy
processes use separate namespaces and separate broker networks. Each hardened
TCP bridge has exactly one caller network and its matching policy network.

## Reachability

- API/background reach explicit backend/data/Teep networks and the public and
  host egress bridges. They cannot reach routing, broker, browser, executor, or
  restricted-service control networks.
- Web/nginx use only `onyx-frontend`; a fixed hardened publisher exposes nginx
  through a non-masqueraded host edge required by Docker Desktop.
- CRW, SearXNG, CDP shim, and Obscura retain narrow internal networks and fixed
  gateways. CRW uses the prefetch bridge; Obscura uses the browser bridge.
- Code-interpreter control is on `onyx-backend` and serves port 8000. Spawned
  executors use `none` or their dedicated internal network and executor bridge.
- Full-mode data services use `onyx-data`. `doc-drop-web` is reached directly
  on `onyx-backend`; a separate fixed display publisher uses a dedicated
  internal peer network. The embedding shim reaches its configured upstream
  only through the host bridge.
- Optional Tailscale reaches nginx only through a fixed HTTP gateway and never
  joins `onyx-frontend`. Myst-routed Teep similarly uses a fixed gateway.

The former bundled Obscura MCP process, gateway, policy, secrets, and networks
were removed. A saved Onyx MCP record that references it is invalid and should
be deleted.

## Onyx public and host egress

The public bridge accepts generic helper, provider-default inference, and
strict MCP/Web Connector traffic. The host bridge accepts saved-level-approved
MCP/OAuth and Web Connector traffic, configured inference endpoints, and the
embedding shim. Runtime patches create explicit proxy transports when HTTPX,
MCP SDK, Playwright, or provider SDK construction would ignore environment
proxy variables.

The policy processes validate HTTP framing and structural destinations but do
not resolve public targets. They send a bounded authenticated request to their
matching broker. The broker repeats validation, selects the fixed final route,
resolves where required, pins the approved address, and streams with idle,
total, and concurrency limits. Route class, resolver, upstream, and source
address are not caller-selectable.

Both routes always reject loopback, link-local, metadata, multicast,
unspecified/reserved addresses, Docker/Podman internal suffixes and legacy
aliases, and single-label service names. The host route alone permits:

- exact `host.docker.internal`, resolved and pinned by the host broker; and
- RFC1918 literals or all-RFC1918 DNS answers when
  `EGRESS_ALLOW_RFC1918=true`.

Mixed private/global DNS answers fail. Public-only, browser, and executor
policies never receive these exceptions. Exact host and RFC1918 traffic does
not traverse an external upstream proxy. Exact `host.docker.internal` and
opt-in, fully validated RFC1918 destinations are the cleartext exceptions when
`EGRESS_ALLOW_HTTP_URLS=false`. The host broker classifies DNS answers before
opening the connection, so these support local inference, embedding, Web
Connector, and MCP endpoints without permitting public HTTP targets.

## SSRF interaction

Compose seeds `OPEN_URL_VALIDATE_SSRF=true`,
`MCP_SERVER_ALLOW_PRIVATE_NETWORK=true`, and
`MCP_SERVER_ALLOW_LOOPBACK=false`. Saved Admin Security Hardening state takes
precedence and is read for each new MCP client or Web crawl. Strict levels
select the public route; levels that permit private networks may select the
host route. Onyx-side structural validation uses `resolve_dns=False` for
external requests so the broker remains the authoritative resolver.

The Web connector has one fixed direct exception for
`http://doc-drop-web:8091/`. It is exact, startup-validated, and internal; it
does not enable arbitrary private crawling. Display links are rewritten to the
configured host doc-drop origin only in returned search sections, while stored
document identity remains the internal crawl URL.

## Other restricted components

Executor, prefetch, and browser policies retain the same destination floor.
Prefetch and executor also reject search-engine hosts. Cleartext targets are
rejected unless `EGRESS_ALLOW_HTTP_URLS=true`; the host route additionally
allows exact `host.docker.internal` and RFC1918 destinations explicitly
enabled by `EGRESS_ALLOW_RFC1918`, as described above.
CONNECT on another allowed port is an opaque TCP stream and is not
application-protocol inspected.

CRW's mandatory URL-safety preflight uses `crw-validation-dns` on loopback.
The sidecar returns a synthetic global address for unresolved public
multi-label names and never forwards target names. CRW has no direct route;
authoritative destination DNS and validation happen at the final-hop policy.

Obscura permits private resolution only so its HTTP client can locate its
mandatory internal egress bridge. Its network has no external route, and the
browser final-hop policy remains authoritative for navigation targets.

## DNS and upstream proxies

Direct VPN mode uses the source-bound Myst provider resolver; explicit no-VPN
mode uses the trusted routing namespace's system DNS. HTTP, HTTPS, and
`socks5h` upstream proxies receive target hostnames. Plain `socks5` uses the
selected final-hop resolver and a validated pinned address.

Remote-DNS proxy schemes cannot locally prove what address an upstream will
choose. A malicious or misconfigured upstream can resolve a public-looking
name privately; upstream-side policy is required to eliminate that residual
risk. Proxy credentials and request secrets must not appear in logs.

## Bridge and control-plane risks

Bridges are read-only numeric-nonroot containers, drop all capabilities, use
`no-new-privileges`, disable IP forwarding, and have no Docker socket or host
port. They are TCP forwarders, not routers. A future incorrectly shared or
non-internal network can weaken the boundary and must be caught by effective
Compose tests.

The code-interpreter control service retains Docker socket access. Compromise
of that service is compromise of the Docker control plane; executor network
isolation does not make that socket safe. Likewise, the trusted route brokers
are security-critical because they own final-hop DNS and connectivity.

Failure is intentionally closed: stopping a policy, bridge, broker, Myst, or
upstream disables its route without cross-class or direct fallback. Only Myst
is autohealed, preventing dependency restart storms while internal application
health remains independent.
