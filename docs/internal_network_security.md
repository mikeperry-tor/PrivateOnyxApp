# Internal network security

The stack uses network separation and fixed-destination bridges to limit which
components can reach CDP, Onyx data services, host-capable destinations, and
the Internet. The exact routing policy is in
[VPN routing and restricted egress](vpn_routing_and_proxies.md).

## Reachability and trust boundaries

| Component | Internal reachability | Internet path |
| --- | --- | --- |
| `api_server` | Onyx backend/data services; API-only `obscura-cdp-gateway`; Teep | fixed public or saved-level host-capable Onyx bridge; optional stock crawler uses only the public bridge |
| `searxng-core` | SearX service gateway; direct Obscura CDP control | none except browser activity performed by Obscura |
| `obscura-cdp-gateway` | API-side control network and Obscura control network | none |
| `obscura` | CDP control networks and its fixed browser bridge | shared public final-hop policy through `obscura-egress-bridge` |
| enabled executor | executor service/control networks and its fixed bridge | shared public final-hop policy through `executor-egress-bridge` |
| doc-drop and embedding components | their documented full-mode local networks | only their explicit host/public route where configured |

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
private, mixed-answer, and rebinding destinations. Redirects and browser
subresources pass through the same policy. Internal service names use Docker
DNS, but public target names never need to.

A remote-DNS upstream proxy is an explicit exception to address observation:
the wrapper can validate the public-looking hostname but cannot see or pin the
upstream's answer. A malicious or misconfigured upstream can resolve it
privately unless that upstream enforces equivalent policy.

Onyx's own SSRF setting remains an application-layer control for MCP, Web
Connector, and configured provider choices. It does not replace final-hop
policy. Saved Admin settings select the public or host-capable route; they do
not grant browser or executor access to the host listener.

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
accounting, while rendered DOM and API body limits are separately enforced.
Obscura may allocate the full initial response before any of those retained
limits. None is a complete aggregate process-memory bound.

The public Onyx, browser, and executor bridges share a final-hop proxy process,
so that proxy is a failure/contention domain. Their networks and allowed-peer
checks remain separate. Host-capable policy is a different listener and
bridge. A compromised caller cannot select another bridge merely by choosing
a proxy address.

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
- Full-mode doc-drop remains a local path and does not gain browser/CDP access.
