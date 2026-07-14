# Onyx Application Network Isolation Plan

> **Status: implemented (2026-07-14), simplified to final-hop proxies on
> 2026-07-14.** This plan is the implementation record for moving Onyx
> applications out of the trusted routing namespace. The original two-stage
> request-policy/broker design was removed before completion because it added
> a custom protocol, credentials, counters, deadlines, services, and failure
> modes without strengthening the primary network boundary.
>
> Normative deployed behavior is documented in
> [VPN routing and restricted egress](../vpn_routing_and_proxies.md),
> [Internal network security](../internal_network_security.md), and
> [Request handling](../request_handling.md). This remains the prerequisite for
> [Direct Obscura request handling](obscura_direct.md).

## Decision

Onyx application containers use only explicit `internal: true` networks. They
do not share `netns-holder` and cannot obtain public, host-gateway, or LAN
connectivity by ignoring proxy configuration.

Outbound HTTP(S) follows one of two fixed paths:

```text
Onyx application
  -> public bridge -> public final-hop proxy (3132) -> selected final route

Onyx application explicitly authorized for private destinations
  -> host bridge -> host final-hop proxy (3133) -> selected final route
```

Each bridge is a fixed numeric-nonroot TCP forwarder with one caller network
and one dedicated policy-side network. `netns-holder` joins both policy-side
networks under the `myst-client` alias. The two final-hop proxy processes share
that trusted namespace but use different listeners, bridge source allowlists,
and immutable route-class configuration.

The final-hop proxy itself performs HTTP framing checks, destination policy,
authoritative target DNS, address pinning for direct connections, upstream
proxy selection, and streaming. There is no separate request-policy service,
route broker, broker credential, broker protocol, admission counter, or
broker-imposed connection lifetime.

## Why this boundary is sufficient

The security boundary is application network placement, not the number of
proxy processes between an application and the Internet. Applications cannot
address `netns-holder` directly; they can address only their fixed bridge. A
bridge cannot select a different listener. Each listener accepts only the
resolved address of its configured bridge peer and has a fixed public or host
route class.

The previous broker repeated policy after an isolated policy process, but both
components were custom Python parsers handling attacker-influenced streams.
That duplication increased attack surface and introduced authentication,
framing, capacity, timeout, cancellation, and half-close state. It did not
protect against compromise of the final trusted process and was not needed to
stop application direct sockets. Collapsing the stages removes those risks.

The deliberate residual tradeoff is that both Onyx final-hop proxies share
`netns-holder`. A process-level compromise of either proxy is therefore a
trusted-namespace compromise. Public-versus-host separation is configuration,
listener, and bridge reachability defense in depth; it is not a sandbox
against arbitrary code execution inside a final-hop proxy. The proxy remains
numeric-nonroot, read-only, capability-free, and `no-new-privileges`.

## Route classes

The public route rejects private, loopback, link-local, multicast,
unspecified/reserved, metadata, Docker/Podman internal, legacy host-alias, and
single-label service destinations.

The host route adds only:

- exact `host.docker.internal`, resolved and pinned by the final-hop proxy;
- exact stack-owned `doc-drop-web:8091` through its fixed gateway; and
- RFC1918 literals or `.local`, `.internal`, and `.home.arpa` names whose
  complete answer set is RFC1918, only when `EGRESS_ALLOW_RFC1918=true`.

Mixed private/global answers fail closed. Loopback, link-local, metadata, and
other container-internal names remain blocked. Plain HTTP remains disabled by
default; the exact host and fully validated opt-in RFC1918 destinations are
the narrow host-route exceptions.

MCP/OAuth and configured Web Connector clients choose public or host routing
from saved Onyx Admin SSRF policy when the client/crawl is created. Supported
configured chat providers and the embedding shim use the host route. The exact
internal Teep URL uses its explicit internal network and `trust_env=false`.
Generic helpers and provider-default inference use the public route. Unsupported
private provider endpoint types fail rather than acquiring a broad bypass.

## DNS and final routing

The final-hop proxy is authoritative for target DNS and connection choice:

| VPN | Upstream proxy | Final route |
| --- | --- | --- |
| enabled | absent | Myst provider DNS and Mysterium |
| enabled | present | upstream proxy reached through Mysterium |
| disabled | absent | system DNS and explicit direct namespace route |
| disabled | present | upstream proxy reached directly |

HTTP, HTTPS, `socks5`, and `socks5h` upstreams receive ordinary target names
without a preliminary local target lookup. Exact host and RFC1918 exceptions
never traverse an external upstream proxy. Operator-local system-DNS lookup is
limited to the three documented suffixes and requires the RFC1918 opt-in.

## Internal services and ingress

Required service traffic uses explicit internal networks and service DNS:

- SearXNG through `searxng-service-gateway:8888`;
- CRW through `crw-service-gateway:3010`;
- code interpreter control at `code-interpreter:8000`;
- Teep at `teep:8337`;
- full-mode doc-drop identity at `doc-drop-web:8091`; and
- local embedding shim at `local-embedding-shim:9101`.

Docker Desktop does not activate published ports on containers attached only
to internal networks. Nginx and doc-drop therefore remain internal and use
hardened fixed-destination publishers on a non-masqueraded host edge. Optional
Tailscale reaches nginx only through a fixed frontend gateway. Myst-routed
Teep likewise uses a fixed gateway rather than sharing a caller network with
`netns-holder`.

The Web Connector sends the stable `http://doc-drop-web:8091/` identity through
the host proxy. A fixed gateway with that alias joins only the host policy-side
network and the doc-drop origin network. Redirects and subresources therefore
remain policy-mediated without giving the crawler a direct backend route.

## Connection behavior

The final-hop proxy has no global admission semaphore and no fixed total
lifetime for CONNECT tunnels. Long-lived active streams, including Tailscale
or other keepalive-driven protocols if deliberately sent through an HTTP
CONNECT route, remain open until a peer closes or I/O fails.

Current request/setup bounds are:

- target/provider DNS operation: 10 seconds;
- TCP connection to a direct target or configured upstream proxy: 15 seconds;
- initial client proxy request line: 30 seconds;
- each client proxy header line: 10 seconds.

Framed request bodies, chunk-size lines, trailers, and established tunnel
relay have no proxy-imposed deadline. Framing validation still rejects
ambiguous, malformed, or prematurely closed bodies.

The 15-second timer covers reaching the configured proxy endpoint, not the
remote circuit construction performed after a connection to a local Tor
proxy. SOCKS/HTTP proxy negotiation and established tunnel relay currently
have no separate fixed total deadline. Endpoint and client timeouts therefore
remain authoritative for slow and long-lived protocols.

## Failure behavior

Final-hop readiness validates configuration and the selected DNS/upstream
substrate without opening an arbitrary user target. Bridge health traverses
the bridge and expects the proxy to deny a fixed forbidden target. Stopping a
bridge, matching proxy, Myst, or configured upstream disables only that route.
There is no cross-class or direct fallback. Only Myst is autohealed in VPN
models; explicit no-VPN models omit autoheal and its Docker socket.

## Removed scope

The implementation also removed the bundled Obscura MCP service and its
gateway, policy, secrets, networks, and settings. Existing saved MCP records
that reference it are invalid. Executor pods remain networkless by default;
when enabled, they use their own restricted bridge and final-hop policy and do
not inherit Onyx host/RFC1918 exceptions.

## Validation and acceptance

The implementation is accepted only while tests prove:

- application services have only the documented internal networks and no
  `network_mode: service:netns-holder`;
- public and host bridges have distinct caller/policy-side networks and fixed
  destination ports;
- final-hop proxies share only the trusted namespace, run hardened, enforce
  distinct bridge source allowlists and route classes, and expose no host port;
- application, browser, and executor networks cannot reach alternate route
  listeners or trusted namespace networks;
- public/private destination classification, mixed-answer rejection, exact
  host behavior, RFC1918 opt-in, upstream modes, and HTTP framing fail closed;
- every restricted listener declares its public or host route class explicitly,
  and exact trusted internal authorities fail startup outside the host route;
- VPN and no-VPN upstream-proxy bootstrap use their documented resolvers, and
  all-global operator-local answers return to the selected final-hop resolver;
- doc-drop uses only its exact host proxy gateway;
- VPN/no-VPN Compose models and the combined upstream-proxy, executor-network,
  Myst-routed Teep, and Myst-routed Tailscale overlays preserve route isolation;
- Makefile selection adds each optional network layer only when its documented
  switch is enabled;
- every optional executor proxy/bridge is numeric-nonroot, read-only,
  capability-free, source-restricted, public-only, and packet-forwarding-disabled;
- framed request bodies and chunks retain strict structural validation without
  a proxy-imposed read deadline;
- removal of broker files, credentials, services, protocol tests, and networks
  is enforced; and
- lite/full stack startup plus representative helper, MCP, Web Connector,
  `open_url`, search, inference, embedding, RAG, and executor paths are tested
  when their external dependencies are available.

## Documentation contract

`README.md`, `AGENTS.md`, `.env.wrapper.example`, and all documents under
`docs/` must describe the final-hop proxy topology. Historical plans may record
older milestones only when clearly labeled historical; they must not present a
broker as part of the current or planned runtime.
