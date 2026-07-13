# Internal Network Security

This document describes the restricted topology for agent-controlled or
attacker-influenced network paths. The implementation is current for the pins
in `stack.versions.env`.

## Security Boundary

Docker network placement is the primary boundary. Proxy variables are not a
sandbox: generated code and browser processes can ignore them, so restricted
components are placed only on `internal: true` networks with their required
peers and narrow gateways.

The trusted `netns-holder` namespace still contains Onyx application services,
the Mysterium client, code-interpreter control plane, full-mode local RAG
services, and final-hop proxy policies. It no longer contains:

- CRW;
- SearXNG or its Valkey instance;
- Obscura CDP;
- Obscura MCP;
- CDP shim;
- network-enabled executor pods.

## Reachability By Component

### Executor pods

Disabled executor networking retains upstream Docker network `none`. When
enabled, pods attach only to the explicitly named internal
`onyx-code-interpreter-executor` network. Their only intended peer is
`executor-egress-bridge:3128`.

They cannot route directly to Onyx, CRW, SearXNG, either Obscura process, CDP,
Myst control ports, data stores, default-network services, the Docker host
gateway, LAN/private ranges, or link-local metadata. Direct internet sockets
also fail because the network is internal. The bridge forwards only to the
`executor` policy in the trusted routing namespace.

The code-interpreter control service remains trusted and retains Docker socket
access. A compromise of that service can create containers or alter network
placement; restricted executor networking does not make the Docker control
plane untrusted-safe.

### CRW

CRW attaches only to dedicated API, CDP, search, and prefetch-egress networks.
It reaches:

- Onyx callers through `crw-service-gateway:3010`;
- SearXNG directly on their shared internal search network;
- CDP shim at `cdp-shim:9224`;
- the prefetch policy through `crw-prefetch-bridge:3128`.

CRW 0.23 also requires a local DNS result during its own URL-safety
prevalidation, before it uses `HTTP_PROXY`/`HTTPS_PROXY`. The
`crw-validation-dns` process shares CRW's network namespace, listens only on
`127.0.0.1:53`, and never forwards queries. Docker's embedded resolver first
answers known service names with their real private bridge addresses, so CRW
rejects them. Unresolved public multi-label names are forwarded only to this
loopback process, which supplies a fixed global IPv4 address. Unknown
single-label and host-local/Docker-internal suffixes receive NXDOMAIN.

That synthetic answer is not used to open the target connection and is not an
authoritative private-address check. CRW has no direct internet route; its
prefetch goes through the final-hop policy, while rendered navigation goes
through Obscura and its final-hop policy. Those policies perform the real DNS
and destination checks described below. CRW's syntax/IP-literal checks,
Docker answers for known internal peers, and its network placement remain
defense in depth.

### SearXNG

SearXNG attaches only to its API network, the CRW search network, and its
Valkey network. The default CRW-backed engines post to the configurable
`CRW_SCRAPE_URL=http://crw:3010/v1/scrape`. SearXNG has no general internet
route, so accidentally enabled direct engines fail closed.

The host diagnostic endpoint and Onyx API access use separate narrow TCP
gateways. Valkey is reachable only from the SearXNG state network.

### Obscura CDP

The renderer attaches only to its control network with CDP shim and its browser
egress network. Its explicit `OBSCURA_PROXY` points to
`obscura-egress-bridge:3128`, which reaches a search-allowed `browser` policy.
Obscura's default private-network deny remains enabled as defense in depth.

The CDP shim is dual-homed only between CRW's CDP network and Obscura's control
network. It continues to strip conflicting stealth/proxy settings, inject
waitUntil, clear cookies, redact traces, and block cleartext navigation by
policy.

### Obscura MCP

The MCP process is separate from the CDP renderer and has separate control and
egress networks. Its unauthenticated listener is not host-published and is not
reachable from executors, CRW, SearXNG, CDP, data networks, or the default
network. Onyx reaches it only through `obscura-mcp-gateway:9223`.

Its browser uses a separate egress bridge and `browser` policy instance, so a
failure does not share the CDP renderer's bridge process. Browser state remains
shared across callers of that MCP instance but separate from the CRW-driven
renderer.

## Final-Hop Destination Validation

Every final-hop policy rejects:

- loopback, private/RFC1918, link-local, multicast, unspecified, reserved, and
  other non-global IP literals;
- legacy IPv4 shorthand after normalization;
- localhost names, every `*.docker.internal` name, known legacy Docker Desktop
  host/gateway names, configured additional internal names, their subdomains,
  and single-label Docker service/container/alias names;
- DNS names whose selected resolver answer contains any blocked address, when
  no upstream proxy is configured.

Hostname comparisons normalize case, brackets, and a trailing DNS root dot
before applying internal/search-host policy, so fully qualified spellings such
as `www.google.com.` cannot bypass the executor or prefetch search block.

`prefetch` and `executor` also block configured search hosts. `browser` allows
those hosts because SERP rendering is its intended function.

When `ONYX_AGENT_ALLOW_HTTP_URLS=false`, ordinary plain-HTTP requests and
CONNECT to destination port 80 receive 403. CONNECT on other allowed ports is
an opaque TCP tunnel; it can carry protocols other than HTTPS and is not
content-inspected.

### Direct-mode VPN DNS and pinning

With Myst enabled, direct mode derives the provider DNS address from the
`myst0` IPv4 subnet and sends DNS packets directly to that literal address.
The DNS sockets are source-bound to the IPv4 address assigned to `myst0`, and
the provider resolver is on that interface's directly connected subnet.
Failure to identify or bind that address fails the request closed without
granting the proxy `CAP_NET_RAW`. Docker's `127.0.0.11` resolver is not used by
the final-hop policy for browsing targets. CRW's separate synthetic preflight
is described above and does not leave CRW's namespace. Validation queries A
records because IPv6 is disabled in the trusted routing namespace, then
returns the exact approved address set.
Connection code retries only those addresses and never passes the hostname to
a second resolver call. HTTP Host and TLS SNI retain the original hostname. The unit test
`tests/test_restricted_egress_proxy.py` verifies resolver selection and
connection pinning.

When `MYST_VPN_ENABLED=false`, direct mode deliberately uses system/Docker DNS
along with direct Docker egress. Docker DNS also remains in use for internal
service discovery, CRW's non-forwarding synthetic preflight, and an explicitly
configured internal upstream proxy such as `host.docker.internal`.
Any VPN-switch value other than the exact strings `true` and `false` fails
policy-proxy startup.

The built-in Docker and loopback hostname floor cannot be removed with
`PREFETCH_BLOCK_INTERNAL_HOSTS`; that variable only adds deployment-specific
multi-label names. All current Compose service names, container-style names,
and explicit network aliases are single-label and are rejected independently.
Hostname classification applies IDNA normalization first, including Unicode
characters that DNS treats as dot separators.
If a future override introduces a dotted internal alias outside
`*.docker.internal`, add it through `PREFETCH_BLOCK_INTERNAL_HOSTS`.

### Upstream-proxy DNS residual risk

When `ONYX_AGENT_OUTBOUND_PROXY_URL` is set, the local policy intentionally
does not resolve arbitrary target names, avoiding a target-DNS leak outside the
configured proxy route. Syntactic private targets are still blocked locally,
but a public-looking hostname may be resolved to a private address by the
upstream proxy. Upstream-side policy, DNS-over-proxy classification, or an
allowlist is required to eliminate that residual risk.

For HTTP CONNECT, SOCKS5/SOCKS5h, and absolute-form HTTP forwarding, the target
hostname is sent to the upstream proxy without a local target lookup. A public
upstream-proxy hostname is itself resolved through Myst DNS when VPN mode is
active; this bootstrap reveals the proxy endpoint but not browsing targets.

## Bridge Properties And Risks

Component bridges have no host publishing, Docker socket, or writable host
mount. They are read-only, drop Linux capabilities, and use
`no-new-privileges`. Each egress bridge has a dedicated upstream network shared
only with `netns-holder`; service gateways attach only to their caller ingress
and restricted service network. They are TCP forwarders, not IP routers.

Final-hop listeners permit loopback health checks and only the resolved peer
address of their configured bridge. This application-level source check is
required because the policy processes share `netns-holder` and bind across its
interfaces; unrelated default-network or ingress-network containers are
rejected rather than receiving an undocumented proxy path.

The loopback policy health client validates the configured egress substrate,
not an arbitrary destination: provider-DNS resolution in direct mode, or a
target-free upstream-proxy protocol/authentication handshake in proxy mode.
Bridge health sends a localhost CONNECT that policy must reject with 403, so a
listening but disconnected socat process is not considered ready. Service and
host gateways similarly make an HTTP request through their forwarding path.
These probes do not grant another client or network access to a policy port.

The HTTP proxy rejects ambiguous request framing: conflicting or malformed
`Content-Length`, combined `Content-Length`/`Transfer-Encoding`, and transfer
coding lists that do not end in exactly one `chunked` coding. Valid fixed and
chunked bodies, extensions, and trailers are streamed without a wrapper size
cap.

Remaining bridge risks:

- a future non-internal or wrongly shared Docker network can reintroduce a
  route;
- a listener bound on an unintended attachment can expose a policy port;
- TCP bridges rely on the selected final-hop policy being correct;
- final-hop policy availability is required, so failure is intentionally
  closed rather than falling back to direct access.

## Onyx SSRF Settings

`ONYX_SECURITY_SSRF_VALIDATE_OPEN_URL`,
`ONYX_SECURITY_SSRF_ALLOW_PRIVATE_NETWORK`, and
`ONYX_SECURITY_SSRF_ALLOW_LOOPBACK` seed Onyx Admin security settings. Saved UI
settings take precedence. These values govern Onyx-managed URL paths; they are
not firewall rules for restricted components or final-hop proxies.

The bundled Obscura MCP endpoint now uses the narrow non-loopback gateway
`http://obscura-mcp-gateway:9223/mcp`. It no longer requires globally disabling
Onyx SSRF protection solely to access shared loopback. Full-mode local document
RAG retains its documented private-network needs.

## Host Proxy Exception

Host Tor configurations require `MYST_VPN_ALLOW_LAN_BYPASS=true` so the trusted
final-hop proxy can reach `host.docker.internal:9150`. Restricted components do
not receive that route, but the Myst exemption itself covers RFC1918 LAN ranges
inside the trusted routing namespace. It remains broader than the desired
endpoint-scoped exception.
