# VPN Routing And Restricted Egress

The wrapper separates trusted routing services from components influenced by
generated code, URL input, or untrusted pages. Docker network placement is the
security boundary; proxy environment variables are routing hints inside that
boundary.

See [Request handling](request_handling.md) for the web paths and
[Internal network security](internal_network_security.md) for the resulting
reachability and residual risks.

## Compose Layering

`make up-lite` and `make up-full` assemble:

- `docker-compose.yaml`, including the atomic restricted request-path topology;
- the lite or full Onyx mode;
- optional Podman, Teep-VPN, and Tailscale-VPN layers;
- `docker-compose.code-interpreter-network.yml` only when
  `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`;
- `docker-compose.proxy.yml` only when `ONYX_AGENT_OUTBOUND_PROXY_URL` is set.

The proxy layer is intentionally only a routing-mode marker. Final-hop policy
proxies read the upstream URL directly; it never attaches executor pods to a
network or gives restricted components the upstream URL.

## Trusted Routing Namespace

`netns-holder` remains the stable owner of the Mysterium routing namespace.
Trusted Onyx services, the Mysterium client, and final-hop policy proxies use
`network_mode: service:netns-holder`. Restarting Myst therefore does not
invalidate their namespace references.

CRW, SearXNG, Obscura CDP, Obscura MCP, and executor pods do not share this
namespace. Narrow internal networks and TCP gateways expose only the required
ports:

| Restricted component | Direct peers | Internet path |
| --- | --- | --- |
| CRW | CRW API callers, CDP shim, SearXNG | `crw-prefetch-bridge` to `prefetch` policy |
| SearXNG | CRW and Valkey | none in the default CRW-backed engine profile |
| Obscura CDP | CDP shim and its egress bridge | `obscura-egress-bridge` to `browser` policy |
| Obscura MCP | Onyx MCP gateway and its egress bridge | separate bridge to `browser` policy |
| Executor pods | `executor-egress-bridge` only | `executor` policy |

The final-hop proxies bind distinct ports in `netns-holder`. Their bridges use
dedicated internal upstream networks, have no host ports, Docker socket, or
writable mounts, and fail closed when the policy proxy is unavailable. Each
proxy accepts non-loopback clients only when the peer address currently
resolves to its configured bridge service, so listeners bound across the
shared namespace are not usable from unrelated network attachments.

## Final-Hop Routing Matrix

Restricted components always use the same local bridge URL. The selected
operator preferences change only the final connection:

| `MYST_VPN_ENABLED` | `ONYX_AGENT_OUTBOUND_PROXY_URL` | Result |
| --- | --- | --- |
| `true` | empty | allowed requests leave through Mysterium |
| `true` | set | policy proxy connects to the upstream proxy through Mysterium |
| `false` | empty | allowed requests leave directly through Docker by explicit choice |
| `false` | set | policy proxy connects directly to the upstream proxy |

When `MYST_VPN_ENABLED=false`, Myst idles without arming its kill switch,
funding, registering, or connecting. Restricted-network isolation is unchanged;
only the trusted namespace's final route changes.

## Proxy Policies

`crw/prefetch_blocking_proxy.py` implements explicit modes:

| Mode | Search hosts | Private/internal targets | Cleartext target URLs |
| --- | --- | --- | --- |
| `prefetch` | blocked | blocked | controlled by `ONYX_AGENT_ALLOW_HTTP_URLS` |
| `executor` | blocked | blocked | controlled by `ONYX_AGENT_ALLOW_HTTP_URLS` |
| `browser` | allowed | blocked | controlled by `ONYX_AGENT_ALLOW_HTTP_URLS` |
| `searxng-external` | allowed | blocked | reserved for intentional external features |

When `ONYX_AGENT_ALLOW_HTTP_URLS=false`, ordinary HTTP requests and CONNECT to
destination port 80 are rejected. Other CONNECT ports are opaque TCP tunnels;
the policy does not inspect their application protocol.

Without an upstream proxy, VPN mode sends A queries directly to the Mysterium
provider resolver at the first usable address of the `myst0` consumer subnet.
The UDP and TCP DNS sockets are source-bound to the IPv4 address on `myst0`,
and the resolver is on that interface's directly connected subnet. An absent
or unusable device fails the lookup closed. This needs no additional container
capability and does not use Docker's embedded resolver. The policy queries A
records because the routing namespace disables
IPv6. It rejects the entire answer if any address is non-global
and connects only to an address from that validated set. It does not resolve
the hostname again between classification and connection. The original
hostname remains available for HTTP Host and TLS SNI. Explicit
`MYST_VPN_ENABLED=false` mode instead uses system/Docker DNS as part of the
operator's direct-routing choice. The policy proxies accept only the exact
values `true` and `false`; other values fail startup rather than selecting a
resolver ambiguously.

With an upstream proxy, target DNS stays remote to avoid local DNS leakage.
The policy still rejects private IP literals, localhost names, the current and
legacy Docker Desktop internal-name families, single-label service/container
names, and configured additional internal names. A public-looking
hostname that the upstream proxy resolves to a private address remains a
residual risk; eliminating it requires upstream-side policy or allowlisting.

Public upstream-proxy hostnames themselves are bootstrapped through the Myst
provider resolver when VPN mode is active, then connected by validated IP
while retaining TLS SNI. Literal proxy IPs need no lookup. Explicit internal
proxy endpoints such as `host.docker.internal`, and all proxy bootstrap in
no-VPN mode, use the system resolver. The final-hop policy never sends
arbitrary browsing target names to Docker DNS in upstream-proxy mode.

CRW has a separate, mandatory local URL-safety lookup before it invokes its
proxy. Docker's embedded resolver answers known internal peers normally and
forwards unresolved public multi-label names to `crw-validation-dns`, which
listens on loopback in CRW's network namespace. It returns a fixed global A
record and never forwards the query. This synthetic answer only permits CRW
to continue to the proxy; it is never used by the final-hop connection.
Unknown single-label and host-local/Docker-internal suffixes receive NXDOMAIN.

Upstream proxy URLs are validated before a policy listener starts. Supported
schemes are `http`, `https`, `socks5`, and `socks5h`; malformed ports,
incomplete credentials, and path/query/fragment components fail startup.
Logs redact credentials.

## Readiness And VPN Recovery

In VPN mode, `myst-client` is healthy only when the daemon reports Connected,
`myst0` has a non-/32 global IPv4 subnet and a route, and a fixed `example.com`
A query succeeds against the derived provider resolver while source-bound to
the `myst0` address. The fixed probe contains no user browsing hostname. It
also ensures a stale control-plane Connected state makes the container
unhealthy. In explicit no-VPN mode, readiness instead requires no `myst0` and
a direct default route on `eth0`.

Only `myst-client` has the `autoheal=true` label. Restarting it leaves the
`netns-holder` namespace—and therefore every dependent service's namespace
reference—intact. Policy proxies and bridges deliberately are not autohealed:
their end-to-end health checks turn red while DNS, an upstream proxy, or Myst
is unavailable and recover in place after Myst reconnects. Restarting those
containers would not repair the tunnel and could create a restart storm.

Policy readiness first checks its listener. Without an upstream proxy it also
queries the fixed name through the selected Myst provider resolver (or system
DNS in explicit no-VPN mode) and rejects non-global answers. With an upstream
proxy it performs only endpoint bootstrap plus a target-free protocol/auth
handshake (`OPTIONS *` for HTTP(S), greeting/auth for SOCKS5); no browsing
target is locally resolved or opened. Egress bridges issue a blocked localhost
CONNECT and require the policy's 403, proving the entire bridge-to-policy hop
rather than merely checking that socat owns a port.

CRW health additionally requires `crw-validation.test` to resolve through the
loopback validation sidecar to its expected synthetic address, then checks the
CRW API. `crw-service-gateway` waits for that combined health state. A missing
or failed validation resolver therefore blocks Onyx's Firecrawl path instead
of producing a nominally healthy CRW that rejects every public URL.

Compose `depends_on: condition: service_healthy` gates initial startup only.
It does not recursively change a running consumer's Docker health when a
dependency later fails. Operators should inspect the specific Myst, policy,
bridge, gateway, CRW, and Onyx health states when diagnosing a live outage.

## Code Interpreter

The trusted `code-interpreter` control service remains in `netns-holder`
because it must serve Onyx and use the Docker socket to spawn pods. Its
executor pods default to upstream Docker network `none`.

When `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`:

- the Makefile adds `docker-compose.code-interpreter-network.yml`;
- `PYTHON_EXECUTOR_DOCKER_NETWORK=onyx-code-interpreter-executor` selects an
  explicitly named internal network;
- the runtime patch rejects `container:*` and `host` network values;
- executor `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and lowercase variants
  point to `http://executor-egress-bridge:3128`;
- executor `NO_PROXY` is only `127.0.0.1,localhost,::1`;
- the upstream proxy URL and trusted-service bypass list are not propagated.

Direct sockets therefore have no route to the internet or stack. Search hosts,
private/internal targets, and cleartext URL policy are enforced at the
`executor` final-hop proxy.

Podman keeps code-interpreter disabled by default; restricted executor
networking is unsupported there until its spawned-container network behavior
is explicitly validated.

## Request-Path Service URLs

The restricted topology replaces shared loopback endpoints:

- Onyx SearXNG base URL: `http://searxng-service-gateway:8888`;
- Onyx Firecrawl/CRW URL: `http://crw-service-gateway:3010/v1/scrape`;
- bundled Obscura MCP URL:
  `http://obscura-mcp-gateway.docker.internal:9223/mcp` (a dotted alias scoped
  to `onyx-mcp-ingress` for Onyx frontend URL validation);
- CRW CDP URL: `ws://cdp-shim:9224/devtools/browser`;
- CRW SearXNG URL: `http://searxng-core:8888`;
- SearXNG CRW scrape URL: `http://crw:3010/v1/scrape`;
- CDP shim Obscura URL: `ws://obscura:9222/devtools/browser`.

The bundled MCP gateway uses a non-loopback address, so operators do not need
to disable Onyx SSRF protection globally merely to configure it.
Existing saved Web Search/content-provider/MCP records are application data and
are not rewritten by Compose; update old localhost URLs in Onyx Admin during
the restricted-egress upgrade.

## Host-Resident Upstream Proxies

Tor Browser remains supported with:

```env
ONYX_AGENT_OUTBOUND_PROXY_URL=socks5h://host.docker.internal:9150
MYST_VPN_ALLOW_LAN_BYPASS=true
```

Only final-hop policy proxies receive this URL. Restricted components cannot
route to the host gateway. `MYST_VPN_ALLOW_LAN_BYPASS=true` is nevertheless a
broad RFC1918 route exemption inside the trusted routing namespace, not an
endpoint-scoped host-proxy exception. Treat that as a residual risk.

## Other Optional Routing

Teep and Tailscale retain their existing independent switches. Their VPN
overrides move trusted services into `netns-holder`; neither attaches to any
restricted component network. Host WebUI, SearXNG diagnostics, doc-drop, and
optional Teep access continue through explicit host-facing bridge containers.
