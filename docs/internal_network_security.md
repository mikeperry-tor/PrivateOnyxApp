# Internal network security

## Native Tor and onion ingress boundaries

Tor is a trusted route owner only when either role is enabled. Direct mode
gives Tor `tor-uplink`; no application joins it. Outbound access is admitted
solely by mounting Tor's Unix SOCKS volume read-only in the public and host
policy containers. Tor's control socket and cookie live in a mode-0700
Tor-only tmpfs, remain mode 0600, and are not mounted elsewhere. There is no
TCP SOCKS/control listener or published Tor port.

For onion ingress, Tor retains `tor-uplink`, additionally joins `tor-ingress`,
and forwards virtual port 80 to the fixed-address gateway. Tor encryption
terminates in the Tor container; traffic from Tor to the gateway and nginx is
plaintext HTTP on the isolated internal container networks, not TLS. Tor never
joins `onyx-frontend`. The hardened gateway alone spans `tor-ingress` and
`onyx-frontend` and forwards only to nginx, accepts only a syntactically valid
v3 onion `Host`, replaces client-supplied forwarding headers, preserves that
Host, and publishes no host port. Its fixed configuration is the destination
restriction; bridge attachment is not a sandbox after gateway compromise.

Localhost, Tailscale, and onion frontends may run together. Authentication,
CSP, and cookies remain application-owned and are not rewritten at an edge.
Browser state and host-only cookies are separate per hostname, so each needs
its own login and logout is independent. `WEBUI_CANONICAL_ORIGIN` maps to one
internal `WEB_DOMAIN`. OAuth/federated callbacks, generated links, voice
WebSockets, and other origin-sensitive behavior are guaranteed only there.
Its scheme chooses cookie security globally: an HTTPS canonical origin can
prevent login cookies from working over a secondary HTTP origin, while an HTTP
canonical origin leaves cookies without `Secure` even on a secondary HTTPS
Tailscale visit. Onion ingress is public to anyone who learns the address and
does not compensate for weak credentials.

The stack uses network separation and fixed-destination bridges to limit which
components can reach CDP, Onyx data services, host-capable destinations, and
the Internet. The exact routing policy is in
[VPN routing and restricted egress](vpn_routing_and_proxies.md).
The explicit no-VPN final hop is the default; Myst VPN and upstream-proxy
routing are optional. The same destination and no-direct-fallback boundaries
apply in every selected mode.

## Reachability and trust boundaries

| Component | Internal reachability | Internet path |
| --- | --- | --- |
| `api_server` | Onyx backend/data services; API-only `obscura-cdp-gateway`; Teep | fixed public or saved-level host-capable Onyx bridge; optional stock crawler uses only the public bridge |
| `searxng-core` | SearX service gateway; direct Obscura CDP control | none except browser activity performed by Obscura |
| `obscura-cdp-gateway` | API-side control network and Obscura control network | none |
| `obscura` | CDP control networks and its fixed browser bridge | shared public final-hop policy through `obscura-egress-bridge` |
| enabled executor | executor service/control networks and its fixed bridge | shared public final-hop policy through `executor-egress-bridge` |
| doc-drop and embedding components | their documented full-mode local networks; Podman relay has a dedicated host uplink | only their explicit fixed host/public route where configured |
| optional `tor` | `tor-ingress` only when onion ingress is enabled; private control tmpfs and optional SOCKS runtime volume are mounts, not networks | dedicated `tor-uplink`; applications never join it |
| optional `tor-frontend-gateway` | spans only `tor-ingress` and `onyx-frontend`, with fixed nginx forwarding | none |

CDP is powerful browser authority. The API and SearXNG are mutually
non-isolated with respect to the shared Obscura worker pool: either can create
targets on a worker and concurrent connections do not create a user security
boundary. The narrow API gateway prevents unrelated Onyx backend peers from
gaining CDP reachability, but it is not an authorization protocol between the
two intended callers.

Fresh targets and best-effort `Network.clearBrowserCookies` reduce accidental
carryover; they do not provide per-user isolation. At Obscura 0.1.10 a clear
may be deferred behind active work on a worker and is not atomic with the
following target creation across clients. Non-cookie browser state is
unverified and state is per worker. Capacity is fixed at five workers.

The pinned server can retain a hidden blank target/session if concurrent
connection arrival assigns work around an occupied or failed child. Cleanup
is attempted by the client, but unreachable server-owned state can require a
worker restart. This is an availability/resource limitation, not a safe retry
signal; clients do not reconnect or navigate a second time.

## Destination validation

Client-side URL validation accepts only explicit `https`, or `http` when
`EGRESS_ALLOW_HTTP_URLS=true`; rejects credentials, fragments, invalid IDNA,
canonical internal names, and `file:`; and deliberately does not perform
target DNS. Authoritative resolution and address validation stay at the final
hop so the connection uses the same approved answer.

For wrapper-resolved routes, the final-hop proxy denies loopback, link-local,
metadata, Docker/Podman internal names, multicast, unspecified, reserved,
private, and mixed-answer destinations. It prevents DNS rebinding from
bypassing this policy by validating every resolution and pinning the approved
addresses used for the connection. Redirects and browser subresources pass
through the same policy. Internal service names use Docker DNS, but public
target names never need to.

A remote-DNS upstream proxy is an explicit exception to address observation:
the wrapper can validate the public-looking hostname but cannot see or pin the
upstream's answer. A malicious or misconfigured upstream can resolve it
privately unless that upstream enforces equivalent policy.

Onyx's own SSRF setting remains an application-layer control for MCP, Web
Connector, and configured provider choices. It does not replace final-hop
policy. Saved Admin settings select the public or host-capable route; they do
not grant browser or executor access to the host listener.

`ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true` adds validated RFC1918 LAN
destinations only to the host-capable route used by configured MCP/Web
integrations, inference providers, and the embedding shim. Exact
`host.docker.internal` is governed independently by
`ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS`, whose operator default is `none`.
Bundled-MLX full mode adds only stack-owned port 3210; lite and custom
embedding modes add none. Denied ports are rejected before Docker DNS and have
no Tor, VPN, upstream-proxy, or direct fallback. Allowed ports authorize
whichever host process is listening, not a service identity. Enabling LAN
access retains a possible known RFC1918 host-gateway path.

Saved Admin SSRF levels remain an independent route-selection layer. MCP uses
the public route for `VALIDATE_ALL` and `VALIDATE_LLM`, and the host route for
`ALLOW_PRIVATE_NETWORK` and `DISABLED`. Web Connector uses the public route
only for `VALIDATE_ALL`; its other three levels use the host route. The exact
stack-owned `doc-drop-web:8091` authority remains separate. A host-port grant
does not itself select either host-capable route.

Podman full mode has one additional, narrow host boundary for local documents.
The capability-free `doc-drop-web` relay alone joins a dedicated non-internal
host uplink and can connect only to `host.containers.internal:18091`. The
wrapper-managed macOS server accepts loopback peers, serves a resolved trusted
document root read-only, rejects hidden paths and symlinks, and suppresses
request logging. Application containers do not join this uplink; the existing
exact `doc-drop-route-gateway` and host final-hop listener remain the only Onyx
ingress path. Loopback peer validation is trust in Podman's userspace gateway,
not client authentication. The request-time path check also does not prevent a
trusted local process from racing path-component replacement before open.

The bundled macOS MLX lifecycle proxy uses the corresponding Docker Desktop
gateway boundary. Its fixed host path requires a wildcard listener, but it
rejects non-loopback socket peers in the server admission hook, before HTTP
request parsing or thread creation. The Podman document server applies the same
pre-thread check in host mode. Both host HTTP servers cap active connection
threads and apply a 30-second idle socket timeout, so a loopback peer cannot
hold a bounded slot indefinitely without making progress. These controls trust
the container engine's userspace gateway to present relay traffic as loopback;
they do not authenticate individual application callers behind that gateway.
The MLX proxy's separate five-minute child-request timeout begins only after
the owned child is ready and limits a blocked operation toward that child; it
does not weaken the 30-second untrusted-client socket bound or place a deadline
on cold model loading.

Full mode performs no runtime OpenSearch administration. The monthly audit
pattern and Query Insights controls are static node settings, the tracked
body-free `audit.yml` seeds clean Security configuration, and Onyx receives a
zero-replica index-creation default. No administrative certificate, password,
or additional OpenSearch network authority is granted to another service.

By default, `ONYX_AGENT_USE_OBSCURA_BROWSER=false` means the LLM-controlled
stock crawler does not inherit that Admin private-network allowance. Its
requests adapter and scoped Playwright validation accept public URL shapes
only, avoid local target DNS, and force the fixed public bridge. Requests
environment proxy and `NO_PROXY` selection are disabled; Chromium's implicit
loopback bypass is explicitly disabled. Redirects and subresources remain
under the
public final-hop policy, and the stock mode cannot reach the
host-capable listener.

## Browser containment and residuals

Obscura runs read-only as UID/GID 65534 with all capabilities dropped,
`no-new-privileges`, no private data/secret mounts, no persistent storage,
and without `--allow-file-access`. These controls limit impact but do not make
the browser untrusted-safe. The pinned upstream ES-module path has an accepted
local-file-read limitation, so no sensitive host/container files are mounted
into it.

The optional stock crawler's Chromium process is materially less isolated: it
runs inside the trusted `api_server` container with that service's filesystem
and process privileges. The fixed public proxy limits network destinations but
is not a browser sandbox and does not limit damage from a Chromium compromise
inside the container. This is the default because current reliability testing
favored upstream requests/Playwright despite that containment tradeoff;
operators can select the more isolated direct Obscura crawler, and search
continues in hardened Obscura either way.

This containment tradeoff is currently accepted as the default because the
stock crawler was blocked less often in parallel tests against Obscura 0.1.10.
Treat that result as version-specific and re-evaluate both reliability and
containment when Obscura is upgraded.

The browser's main-response retention limit applies per entry, not per
process. Multiple retained entries, base64 representation, and request/loader
aliases can multiply memory use. The distinct IO stream store has aggregate
accounting. The API enforces the same configured ceiling independently on the
rendered DOM and main body; SearXNG has its own fixed DOM ceiling.
Obscura may allocate the full initial response before any of those retained
limits. None is a complete aggregate process-memory bound.

The public Onyx, browser, and executor bridges share a final-hop proxy process,
so that proxy is a failure/contention domain. Their networks and allowed-peer
checks remain separate. Host-capable policy is a different listener and
bridge. A compromised caller cannot select another bridge merely by choosing
a proxy address.

The end-user browser is outside these container networks. As defense in depth,
nginx supplies a separate restrictive CSP that limits external scripts to
same-origin chunks, denies inline event-handler attributes and eval, and
restricts browser connections, frames, media, fonts, workers, manifests, and
images to the documented same-origin/local schemes. It preserves the inline
Next.js bootstrap/React stream blocks required by the stock image, same-origin
APIs and WebSockets, chat-file images, and local previews. This is a browser
resource-load boundary with a documented inline-script XSS residual; it neither
places the browser in the trusted routing namespace nor changes server-side
destination policy.

Wrapper logs redact query strings, bodies, cookies, credentials, and response
content. Upstream Obscura does not provide the same guarantee and may expose
full URLs in some logging modes; its logs are private data. Multi-worker child
logging can also be incomplete.

## Verification checklist

- CDP has no host port and `obscura-cdp-gateway` is not on `onyx-backend`.
- Only API/gateway join `onyx-obscura-control`; SearXNG joins browser control.
- Onyx, browser, executor, and host-capable bridges and caller networks remain
  distinct and have fixed destinations.
- Obscura and SearXNG have no direct Internet route.
- Initial, redirected, subresource, internal, metadata, and mixed-answer
  destinations fail according to the selected DNS mode.
- Proxy, VPN, gateway, or bridge failure cannot produce direct application
  egress.
- WebUI responses include the wrapper CSP; remote resources, inline event
  handler attributes, and eval are blocked while login hydration, same-origin
  APIs/WebSockets, and local previews work.
- Full-mode doc-drop remains a local path and does not gain browser/CDP access.
- Exact Docker-host allow/deny tests cover the default, numeric, `all`, and
  bundled-3210 policies. Recreating the host policy restarts the dependent
  bridge so a warm invalid setting cannot reuse stale healthy state.
- Under Podman, only the fixed doc-drop relay joins the host uplink; the host
  server rejects non-loopback peers before thread creation, caps and
  socket-bounds active connections, and the relay has no source mount.
- The bundled Docker Desktop embedding listener rejects non-loopback peers
  before thread creation, caps and socket-bounds active connections, and drains
  accepted requests before lifecycle shutdown.
