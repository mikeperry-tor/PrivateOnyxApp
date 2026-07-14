# VPN Routing And Restricted Egress

The wrapper separates Onyx applications from the trusted final-hop routing
namespace. Docker network placement is the enforcement boundary; proxy
configuration selects one of the routes available through that boundary.

See [Request handling](request_handling.md) for web and MCP paths and
[Internal network security](internal_network_security.md) for reachability and
residual risks.

## Compose layering

`make up-lite` and `make up-full` assemble the base stack, the selected Onyx
mode, optional Podman/Teep/Tailscale layers, the executor-network layer when
enabled, and `docker-compose.proxy.yml` when `EGRESS_UPSTREAM_PROXY_URL` is
non-empty. `EGRESS_ALLOW_HTTP_URLS` controls cleartext target URLs.

The Makefile generates different 256-bit credentials for the public and host
route brokers on every start. They are stack secrets, not wrapper settings.

## Network-enforced application isolation

`api_server`, `background`, `web_server`, `nginx`, code-interpreter control,
and local RAG helpers use only explicit `internal: true` networks. They do not
share `netns-holder`, any broker/policy-upstream network, or a Docker network
with a usable default egress route. A direct public or host-gateway socket from
an application therefore fails even if a client ignores proxy configuration.

Internal traffic uses service DNS names:

- Onyx to SearXNG: `searxng-service-gateway:8888`;
- Onyx to CRW: `crw-service-gateway:3010`;
- Onyx to code interpreter: `code-interpreter:8000`;
- Onyx to Teep: `teep:8337` in both ordinary and Myst-routed modes;
- full-mode Web crawl origin: `doc-drop-web:8091`; and
- full-mode embedding service: `local-embedding-shim:9101`.

The former bundled Obscura MCP service and its control/egress topology were
removed intentionally. Existing saved records that name it must be deleted.

## Public and host-capable routes

Environment-aware public helpers use `onyx-public-egress-bridge:3128`.
MCP/OAuth and configured Web Connector requests choose the public or host
bridge from the saved Onyx Admin SSRF level. Supported configured chat
inference endpoints and the embedding shim use the host bridge;
provider-default inference uses the public bridge. Runtime patches construct
explicit transports for clients that would otherwise ignore environment
proxy settings. The exact internal chat base `http://teep:8337/v1` is the one
configured-inference exception: it uses a direct `trust_env=false` client on
`onyx-teep` rather than either external route. Private configured image,
speech, arbitrary embedding/reranking, and Admin provider-discovery endpoints
are not supported; see [Onyx patches](onyx_patch_info.md).

Each bridge is a hardened numeric-nonroot TCP forwarder with exactly two
internal networks: one application-side network and one dedicated
policy-upstream network. Public and host request-policy processes have
separate namespaces and separate authenticated broker networks. They cannot
use each other's broker or credential. Browser and executor bridges are also
separate and cannot reach either Onyx policy or broker.

The minimal public and host route brokers run in `netns-holder`, repeat
destination validation, and make the authoritative DNS/upstream connection.
They accept only a bounded, versioned, authenticated request from their
matching policy address. Broker requests cannot select a resolver, upstream,
source address, or route class. Streams have connection, idle, total, and
concurrency limits.

## Destination classes

The public route rejects private, loopback, link-local, multicast, metadata,
unspecified/reserved addresses, Docker/Podman internal names, legacy host
aliases, and single-label service names. The host route adds only:

- exact `host.docker.internal`, resolved and pinned by the host broker; and
- RFC1918 literals or `.local`, `.internal`, and `.home.arpa` names whose
  complete answer set is RFC1918, only when `EGRESS_ALLOW_RFC1918=true`.

Mixed private/global answers fail closed. Loopback, link-local, multicast,
unspecified/reserved, other Docker aliases, and metadata remain blocked even
on the host route. Exact
`host.docker.internal` does not require the RFC1918 option and never traverses
an external upstream proxy. When `EGRESS_ALLOW_HTTP_URLS=false`, the host route
still permits plain HTTP to that exact host and to destinations whose complete
answer set is RFC1918 when `EGRESS_ALLOW_RFC1918=true`. The policy marks the
connection as cleartext and the broker enforces that classification after its
authoritative DNS validation, without enabling public HTTP.

New installs seed Onyx SSRF protection to Allow Private Network with loopback
disabled. A saved Admin Security Hardening value remains authoritative and is
read when a new MCP client or Web crawl is created. `open_url` remains strict.
The internal doc-drop origin uses the host policy and an exact fixed gateway;
it is not a direct crawl bypass.

## Final-hop routing and DNS

| `MYST_VPN_ENABLED` | `EGRESS_UPSTREAM_PROXY_URL` | Broker final route |
| --- | --- | --- |
| `true` | empty | Mysterium |
| `true` | set | upstream proxy reached through Mysterium |
| `false` | empty | explicit direct Docker route in `netns-holder` |
| `false` | set | upstream proxy reached directly |

No-VPN mode changes only the broker's selected final route; application
isolation is identical. Myst readiness requires a working `myst0` route and a
source-bound provider-DNS probe. No-VPN readiness requires no stale `myst0`
and a usable non-Myst default route. In VPN-enabled models only `myst-client`
is autohealed; explicit no-VPN models omit autoheal and its Docker socket.

With an upstream proxy, HTTP, HTTPS, `socks5`, and `socks5h` all send ordinary
target names directly to that proxy without a preliminary system or Myst DNS
lookup. Without an upstream proxy, public names are resolved and pinned at the
broker through Myst provider DNS or explicit no-VPN system DNS. Upstream
credentials are never logged.

The upstream proxy endpoint is bootstrap routing, not a target-policy
exception. In VPN mode, public proxy names are resolved with the Myst provider
resolver and public proxy addresses follow the Myst route. Exact
`host.docker.internal` uses its narrow host route. A configured RFC1918 IPv4
literal receives only an exact `/32` proxy-endpoint route, without enabling
general RFC1918 target access. System-DNS resolution of `.local`, `.internal`,
or `.home.arpa` proxy names requires `EGRESS_ALLOW_RFC1918=true`.

An upstream that resolves a public-looking hostname to a private address is a
residual risk for remote-DNS schemes; enforce equivalent policy at that
upstream. Exact host and RFC1918 exceptions are never sent through it.

When `EGRESS_ALLOW_RFC1918=true`, only names ending in `.local`, `.internal`,
or `.home.arpa` are queried through system/Docker DNS for RFC1918
classification. All-global answers are discarded and follow the normal
selected final hop; mixed private/global answers fail closed. All other names
remain private from the system resolver: they go directly to the configured
upstream proxy, or through Myst provider DNS when no upstream is set. Users
must use one of these operator-local suffixes or an RFC1918 IP literal for LAN
destinations.

The suffix allowlist follows `.local` from
[RFC 6762](https://www.rfc-editor.org/rfc/rfc6762), `.home.arpa` from
[RFC 8375](https://www.rfc-editor.org/rfc/rfc8375), and the current IETF DNSOP
[`.internal` work](https://datatracker.ietf.org/doc/draft-ietf-dnsop-internal/).
It is a routing/privacy allowlist, not a claim that all three namespaces have
identical DNS resolution semantics.

When `EGRESS_ALLOW_RFC1918=false`, target-related system/Docker resolution is
limited to exact stack-owned internal destinations and
`host.docker.internal`. Ordinary names go to the configured proxy without
local resolution, use Myst provider DNS without a proxy, or use system DNS
only in explicit no-VPN mode. Docker service-name resolution used to
authenticate fixed bridge/policy/broker peers is control-plane resolution, not
user-target resolution.

## Readiness and failure behavior

Policy readiness checks the listener and authenticated broker denial of a
fixed blocked destination. Broker readiness checks its listener plus the
selected DNS/proxy substrate without opening a user target. Bridges require a
policy denial response, proving the full hop. Loss of Myst, a broker, policy,
or bridge makes only the corresponding route fail; there is no public/host
cross-class or direct fallback.

CRW retains its loopback-only synthetic validation DNS sidecar. That sidecar
never forwards target names; authoritative browser destination validation is
still performed by the browser final-hop policy.

## Host publication and optional routes

Docker Desktop does not activate direct published ports on internal-only
networks. Hardened fixed-destination WebUI and full-mode doc-display publishers
therefore join one non-masqueraded host edge and a narrow service-side network;
nginx and doc-drop remain internal. Optional Tailscale uses a profiled, fixed
HTTP frontend gateway; the Tailscale process never joins
`onyx-frontend`. Ordinary Teep uses `onyx-teep` plus its selected uplink;
Myst-routed Teep is exposed through a fixed internal gateway instead of
sharing the Onyx caller network with `netns-holder`.

`autoheal` is selected as a VPN-only Compose layer and has only the narrow Myst
recovery role. Explicit no-VPN models omit the service and Docker socket mount.

Executor pods remain `none` by default. When enabled, they use their own
internal network and executor bridge/policy, receive no Onyx broker credential
or trusted-service bypass, and cannot use host/RFC1918 exceptions.

For a host Tor proxy, use:

```env
EGRESS_UPSTREAM_PROXY_URL=socks5h://host.docker.internal:9150
```

The exact host exception is sufficient; `EGRESS_ALLOW_RFC1918` is needed only
for a distinct RFC1918 endpoint.
