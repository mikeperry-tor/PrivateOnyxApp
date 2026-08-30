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
The API CORS allowlist is the same single canonical origin; ordinary WebUI API
requests remain same-origin on every frontend, while other sites cannot read
even credentialless public API responses through wildcard CORS.
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
| `api_server` | Onyx frontend/backend/data services; API-only `obscura-cdp-gateway`; Teep | fixed public and host-capable Onyx bridges; reviewed adapters select the intended route, and the optional stock crawler uses only the public bridge |
| `background` | Onyx backend/data services; Teep | fixed public and host-capable Onyx bridges; reviewed connector adapters select the intended route |
| `searxng-core` | SearX service gateway; direct Obscura CDP control | none except browser activity performed by Obscura |
| `obscura-cdp-gateway` | API-side control network and Obscura control network | none |
| `obscura` | CDP control networks and its fixed browser bridge | shared public final-hop policy through `obscura-egress-bridge` |
| enabled executor | executor service/control networks and its fixed bridge | shared public final-hop policy through `executor-egress-bridge` |
| doc-drop and embedding components | their documented full-mode local networks; the macOS Podman relay has a dedicated host uplink; rootless Docker's exact stack-owned Teep embedding selection has only `onyx-backend` plus `onyx-teep` | only their explicit fixed host/public route where configured; the rootless Teep exception has no host-egress network |
| optional `tor` | `tor-ingress` only when onion ingress is enabled; private control tmpfs and optional SOCKS runtime volume are mounts, not networks | dedicated `tor-uplink`; applications never join it |
| optional `tor-frontend-gateway` | spans only `tor-ingress` and `onyx-frontend`, with fixed nginx forwarding | none |

CDP is powerful browser authority. Obscura v0.2.1 gives every WebSocket its
own browser context, HTTP client, cookie jar, targets, headers, User-Agent
state, thread, and V8 isolates. The API and SearXNG therefore do not share
browser state across their request connections, but they still share one
process, one CDP endpoint, one 15-connection resource cap, and one failure
domain. Connection isolation is not caller authentication. The narrow API
gateway prevents unrelated Onyx backend peers from gaining CDP reachability,
but it is not an authorization protocol between the two intended callers.
The selected-image gate also proves `Storage.clearCookies` cannot clear a
second connection. The stack does not invoke that command as an isolation
mechanism.

Each direct `open_url` request uses and closes a fresh connection and target.
SearXNG partitions one lazy connection and target per exact provider, reuses
both across clean homepage-first search attempts, parks the target on local
`about:blank` after terminal DOM capture, and closes the target and then the
connection after one hour idle. Parking destroys provider page execution
without creating a final-hop request. Provider generations retain their
native cookie jar, stable target fingerprint seed, selected profile, and
target-owned stealth HTTP client only within that provider boundary. There is
no cookie-clear race to claim as an isolation mechanism. A cleanup or
protocol ambiguity discards the affected generation and remains an
availability/resource event, not a safe retry signal. Clients do not
reconnect or replay an in-flight attempt.

Startpage's supported Anubis continuation does not widen CDP or network
authority. Its opaque token and validated challenge remain inside the SearXNG
process and are bound to the exact retained Startpage owner, target, session,
frame, loader, form data, and deadline. Hashing is local and networkless. The
pass request, redirect, and any restored form POST use only that target's
existing cookie jar, fingerprint, stealth client, connection pool, and selected
browser bridge. Token mismatch, interceptor mismatch, deadline expiry, or
continuation ambiguity closes the provider generation without another route.

## Destination validation

Client-side URL validation accepts explicit `https`, general `http` only when
`EGRESS_ALLOW_HTTP_URLS=true`, and `http` hosts ending in `.onion` only when
the Tor egress layer supplies its internal capability. It rejects credentials,
fragments, invalid IDNA, canonical internal names, and `file:`, and deliberately
does not perform target DNS. The final-hop proxy independently requires its
fixed Tor socket for the onion HTTP exception. Authoritative resolution and
address validation stay at the final hop so the connection uses the same
approved answer.

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

Onyx's SSRF setting remains an application-layer control. `VALIDATE_ALL` and
`VALIDATE_LLM` reject private, loopback, link-local, reserved, and similar
destinations for the operations they validate. `ALLOW_PRIVATE_NETWORK` permits
RFC1918 MCP/OAuth destinations while retaining the loopback and link-local
floor. `DISABLED` permits private and loopback destinations, but still rejects
link-local targets. Web Connector validates only at `VALIDATE_ALL`;
LLM-controlled `open_url` validates at every level except `DISABLED`, where it
retains the loopback/link-local floor. This validation does not replace
final-hop policy.

For HTTPS, upstream Onyx validates one DNS answer and then lets the HTTP client
resolve the hostname again, leaving a DNS time-of-check/time-of-use window.
The wrapper's direct-DNS final hops close that window by resolving, validating,
and pinning the address used for the connection. Both MCP HTTP factories,
including the synthetic OAuth-challenge transport used by automatic discovery,
delegate redirect, discovery, registration, token, and request destination
authority to that same selected final hop rather than relying on an upstream
transport's earlier DNS result. Startup validation fails if either pinned
factory or the challenge state machine drifts.

`ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true` adds validated RFC1918 LAN
destinations only to the host-capable route used by configured MCP/Web
integrations and inference providers. Exact
`host.docker.internal` is governed independently by
`ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS`, whose operator default is `none`.
Full mode separately supplies the exact
`ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL` authority to the host policy; the
configured `EGRESS_UPSTREAM_PROXY_URL` authority is separately held by each
policy process. Either configured authority may use exact
`host.docker.internal`, an RFC1918 literal, or a supported operator-local name
whose complete system-DNS answer set is RFC1918, without enabling either broad
option. Other denied ports and LAN targets have no Tor, VPN, upstream-proxy, or
direct fallback. Allowed ports authorize whichever host process is listening,
not a service identity. Enabling broad LAN access retains a possible known
RFC1918 host-gateway path.

Native rootless Podman maps its exact engine-host alias to the fixed
link-local gateway `169.254.1.2`. The Podman overlay exposes only that one
address to the host-capable policy's exact-host resolver. It remains usable
only for `host.docker.internal` and an operator-selected host port or the exact
configured embedding authority. Every other link-local address, including
metadata ranges, remains denied; public policies receive no such exception.

Saved Admin SSRF levels remain an independent route-selection layer. MCP uses
the public route for `VALIDATE_ALL` and `VALIDATE_LLM`, and the host route for
`ALLOW_PRIVATE_NETWORK` and `DISABLED`. Web Connector uses the public route
only for `VALIDATE_ALL`; its other three levels use the host route. The exact
stack-owned `doc-drop-web:8091` authority remains separate. A host-port grant
does not itself select either host-capable route.

That selection is an adapter contract, not process isolation. `api_server` and
`background` intentionally join both the public and host-capable caller
networks. Compromise of either process can address either bridge directly,
irrespective of the saved SSRF level. Obscura, SearXNG, and executor callers
remain single-homed and cannot acquire that choice.

The two Onyx processes also join `onyx-data` directly and carry stack-owned
`NO_PROXY` entries for trusted backend dependencies. A request path that
ignores the reviewed proxy adapters, or that accepts one of those internal
service names, is not mediated by the final-hop proxy. Application
authorization and URL validation therefore remain required; process
compromise already crosses the data-service boundary.

macOS Podman full mode has one additional, narrow host boundary for local documents.
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

Onyx's document-push endpoint and API key are explicitly blank in both API and
background services. This prevents the optional all-editions feature from
copying the complete text and metadata of each newly indexed public document
to an external endpoint.

IdP profile enrichment is explicitly disabled in both Onyx processes. Enabling
it would add login-time bearer-authenticated requests to an OIDC userinfo URL
or Microsoft Graph and retain the returned claims snapshot in Redis. This
wrapper does not expose that feature as an operator option.

Paid enterprise hooks are inactive because paid-EE and license enforcement are
explicitly disabled. Their delivery path is not a supported route: it validates
an HTTPS endpoint when the hook is configured, then uses a direct HTTP client
without connection-time DNS pinning. Enabling paid-EE therefore requires a new
network adapter and SSRF/DNS-rebinding audit before query, ingestion, or
document hooks can be supported.

Environment-configured Braintrust and Langfuse tracing is also blank, but a
full administrator can configure and enable tracing in the database. An
enabled provider can export LLM inputs, outputs, reasoning, tool calls, and
associated metadata; `DISABLE_TELEMETRY` does not disable this deliberate
trace export. Image, voice, connector, OAuth, and inference providers likewise
receive the data inherent in their explicitly configured functions. Custom
provider and tracing clients do not all use Onyx's SSRF validator or the
wrapper's reviewed adapters. On an internal-only application network, clients
without a working proxy fail closed for public Internet access, but they can
still address reachable internal service names. Full administrators are
therefore trusted data-export and network-configuration principals.

That trust includes Onyx's single-tenant LLM custom-configuration setting.
Unsupported provider keys may be installed temporarily as process environment
variables around a LiteLLM call. Onyx serializes those calls and restores the
environment afterward, but values such as provider endpoints, credentials, or
transport settings can still influence the selected provider. Only a trusted
full administrator may configure LLM providers; the final-hop restrictions and
explicit configured-inference adapter remain authoritative where applicable.
Portkey model discovery plus its OpenAI- and Anthropic-compatible inference
modes use explicit environment-independent clients on the host-capable bridge;
their API keys do not authorize another route.

Incognito chat uses the same LLM, MCP, web-search, `open_url`, and code-tool
network routes as ordinary chat. It suppresses supported external tracing and
adds provider-specific no-retention controls where the provider implements
them, while keeping live context in Redis with a one-hour sliding lifetime. It
does not make an enabled provider or tool local, erase final-hop/provider logs,
or add a retention guarantee for OpenAI-compatible providers that ignore those
controls. Treat incognito as a chat-persistence policy, not as a separate
network-isolation boundary.

Authenticated skill preview/import accepts GitHub repository syntax and fetches
only fixed `api.github.com` and `codeload.github.com` authorities through the
public route. Redirects receive the normal SSRF-safe validation, archive
credentials are not forwarded, and archive member/count/size bounds apply.
Imported instructions and code subsequently inherit the existing code
interpreter boundary; Craft remains disabled. The unauthenticated MCP OAuth
client-metadata endpoint publishes only the canonical client identifier, name,
and redirect URIs and contains no client secret. Usage and cost APIs remain
authenticated local-database views and create no outbound callback.

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

The configured default treats the stock crawler as the reliability-oriented
choice. Re-evaluate reliability and containment with comparable stock/direct
batches on every Obscura upgrade.

The browser's main-response retention limit applies per entry, not per
process. Multiple retained entries, base64 representation, and request/loader
aliases can multiply memory use. Each isolated connection has a distinct
one-entry IO stream store. The API enforces the same configured ceiling
independently on the rendered DOM and main body; SearXNG has its own fixed DOM
ceiling.
Obscura may allocate the full initial response before any of those retained
limits. None is a complete aggregate process-memory bound.

The public Onyx, browser, and executor bridges share a final-hop proxy process,
so that proxy is a failure/contention domain. Their networks and allowed-peer
checks remain separate. Host-capable policy is a different listener and
bridge. Single-homed Obscura and executor callers cannot select another bridge
merely by choosing a proxy address; the dual-homed API and background
processes can, as described above.

The Docker code-interpreter controller has a writable engine socket so it can
create short-lived executor containers. Those children receive the fixed
executor network and do not receive the socket, but compromise of the
controller grants container-engine and therefore host-level authority. The
Podman topology omits this socket-dependent service.

Myst, the final-hop policy processes, and `netns-holder` are trusted
network-namespace peers when the selected routing mode co-locates them.
Caller gateways constrain application ingress; they do not isolate those
co-resident trusted processes from one another.

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

The Startpage proof path additionally excludes challenge JSON, random data,
challenge IDs, nonces, hashes, pass URLs, continuation tokens, and cookies from
wrapper diagnostics. Only the sanitized request ID, entry mode, status class,
stage/category, navigation timings, and solved/not-solved state are emitted.

Provider browser state is process-local but not principal-local. All searches
handled by one SearXNG process can contribute to the same provider's retained
session. It never crosses providers or reaches Onyx applications, but it can
correlate users of a multi-user deployment. The supported private deployment
accepts this only under its single-user trust assumption.

## Verification checklist

- CDP has no host port and `obscura-cdp-gateway` is not on `onyx-backend`.
- Only API/gateway join `onyx-obscura-control`; SearXNG joins browser control.
- Onyx, browser, executor, and host-capable bridges and caller networks remain
  distinct and have fixed destinations.
- Only API and background are dual-homed public/host route selectors; their
  direct data-network and stack-owned `NO_PROXY` trust is explicit.
- Obscura and SearXNG have no direct Internet route.
- Initial, redirected, subresource, internal, metadata, and mixed-answer
  destinations fail according to the selected DNS mode.
- Proxy, VPN, gateway, or bridge failure cannot produce direct application
  egress.
- WebUI responses include the wrapper CSP; remote resources, inline event
  handler attributes, and eval are blocked while login hydration, same-origin
  APIs/WebSockets, and local previews work.
- Full-mode doc-drop remains a local path and does not gain browser/CDP access.
- Document push remains explicitly blank. Tracing has no enabled provider
  unless a full administrator deliberately configures that sensitive export.
- OpenAPI/docs remain unregistered, and tracing, provider, voice, Craft,
  mobile/SSO, and other expanded routes retain their intended authentication
  and feature gates.
- Docker executor children receive neither the engine socket nor an alternate
  route; the socket-bearing controller is absent from the Podman topology.
- Exact Docker-host allow/deny tests cover the default, numeric, `all`, and
  configured embedding/proxy authorities. Recreating the host policy restarts
  the dependent bridge so a warm invalid setting cannot reuse stale healthy
  state.
- Under macOS Podman, only the fixed doc-drop relay joins the host uplink; the
  host server rejects non-loopback peers before thread creation, caps and
  socket-bounds active connections, and the relay has no source mount. Native
  Linux Podman retains the internal read-only bind-mounted server.
- The bundled Docker Desktop embedding listener rejects non-loopback peers
  before thread creation, caps and socket-bounds active connections, and drains
  accepted requests before lifecycle shutdown.
