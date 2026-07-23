# VPN routing and restricted egress

This stack keeps application containers off Internet-routed Docker networks.
Traffic crosses fixed-destination bridges to final-hop policy proxies in the
trusted `netns-holder` routing namespace. The route owner is Mysterium, a
configured upstream proxy, or the explicit no-VPN system route.
`MYST_VPN_ENABLED=false` is the default: it selects the explicit no-VPN route
without requiring a Myst wallet or setup. Mysterium is an opt-in route selected
with `MYST_VPN_ENABLED=true`; an upstream proxy can be used in either mode.

## Route classes

| Class | Callers | Fixed bridge | Policy |
| --- | --- | --- | --- |
| Public Onyx | generic helpers, saved public MCP/Web Connector traffic, optional stock `open_url` requests/local Chromium | `onyx-public-egress-bridge` | public destinations only |
| Browser | Obscura workers | `obscura-egress-bridge` | same public listener and destination policy |
| Executor | enabled code-interpreter pods | `executor-egress-bridge` | same public listener and destination policy |
| Host-capable Onyx | explicitly selected saved-level host routes, configured inference, embedding shim | `onyx-host-egress-bridge` | public plus exact documented host/RFC1918 exceptions |

The three public callers share one final-hop proxy process and listener policy,
but retain distinct caller networks, fixed bridges, and peer-source checks.
They cannot select the host listener or route through one another. Executor
search-host denial was deliberately removed: enabled executors may receive the
real public search-engine response or challenge. No named search-host blocking
mode remains.

The host-capable listener alone owns exact `host.docker.internal` and opt-in
LAN behavior. `ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true` lets configured
Onyx integrations use RFC1918 literals and operator-local names ending in
`.local`, `.internal`, or `.home.arpa`, only after complete-answer-set
classification. It does not extend this capability to public helpers,
`open_url`, browser activity, or executors. Empty, failed, or mixed local
answers fail closed. Loopback, link-local metadata, Docker service names, and
other special-use ranges remain denied.

## Direct Obscura path

```text
api_server -> onyx-obscura-control -> obscura-cdp-gateway
            -> obscura-control -> obscura

searxng-core ---------------------> obscura-control -> obscura

obscura -> browser-egress -> obscura-egress-bridge
         -> browser-policy-upstream -> shared public final-hop proxy
```

Only `api_server` and `obscura-cdp-gateway` join
`onyx-obscura-control`. The gateway is not on `onyx-backend`. SearXNG reaches
Obscura directly on the browser control network. CDP is never host-published.
Obscura and SearXNG have no direct Internet route.

Both `NO_PROXY` and `no_proxy` in `onyx/helper-egress.env` contain the exact
internal name `obscura-cdp-gateway`. Removed browser intermediaries have no
compatibility aliases.

By default, `ONYX_AGENT_USE_OBSCURA_BROWSER=false` makes built-in-crawler
`open_url` use its stock requests fetch and conditional local Chromium fallback
run in `api_server` and use `onyx-public-egress-bridge`; SearXNG continues to
use Obscura. The requests adapter sets `trust_env=False` and supplies the
bridge explicitly, so `NO_PROXY` cannot bypass it. Chromium is launched with
the same fixed proxy and `<-loopback>` bypass rule, which means loopback is
forced through the proxy rather than using Chromium's implicit direct
exception. Initial URLs and requests redirects receive public-only structural
validation without local target DNS; browser redirects and subresources cross
the same public final-hop policy. This mode cannot select the host-capable
listener and a broken bridge/proxy has no direct fallback.

The stock route is the default because parallel testing found it was blocked
less often than the direct crawler on Obscura 0.1.10. That observation does
not change either route's egress policy. Re-run the comparison on Obscura
upgrades and monitor whether the browser route becomes more reliable.

## DNS ownership

Docker DNS resolves only internal service names. Browser target names, and
stock-crawler target names when its optional mode is selected, are passed
unresolved to the final hop:

| Mode | Target DNS and route |
| --- | --- |
| VPN | selected Myst provider DNS and the `myst0` route |
| Configured remote-DNS upstream | upstream proxy owns target resolution and the final route |
| No VPN (default) | system DNS and system default route in `netns-holder` |

When the wrapper resolves destinations itself, it validates the complete
answer set, pins approved addresses, and revalidates redirects/subresources.
When the operator deliberately delegates DNS to a remote upstream proxy, that
proxy can resolve a public-looking name to a private address outside the
wrapper's observation. Address-level private-target denial is therefore not a
wrapper guarantee in remote-DNS mode; the upstream must enforce equivalent
policy.

Named public upstream proxies are resolved by provider DNS and use the VPN
route in VPN mode. Exact host and RFC1918-literal proxy endpoints use only the
documented narrow route exceptions. Named operator-local proxies require the
RFC1918 opt-in and never fall back to external DNS when local lookup is empty
or fails.

## WebUI browser egress

The route classes above govern container traffic. They cannot transparently
route requests made by the WebUI after JavaScript and rendered content reach a
user's browser: without another control, injected script, browser fetches,
WebSockets, frames, media, fonts, workers, and remote Markdown images would use
the user's ordinary DNS and network, outside Myst and the final-hop proxies.

Nginx therefore adds a second restrictive CSP. It permits external scripts
only from the WebUI origin, denies inline event-handler attributes and eval,
and restricts connections, frames, media, fonts, workers, manifests, and
default resources to the WebUI origin. The stock Next.js image requires inline
bootstrap and React stream script blocks for hydration, so the no-rebuild
policy retains `'unsafe-inline'` for those blocks.
Images additionally allow only local `blob:` and `data:` previews. The browser
intersects this with Onyx's own policy and refuses disallowed loads before
opening a connection. Code-interpreter image links are recognized by their
`/api/chat/file/` path and rewritten to a relative same-origin source, so no
`http://localhost:3000` exception is present.

This closes the listed automatic browser resource bypasses and reduces XSS
impact, but it is not full XSS prevention: an injection sink that creates an
executable inline script block remains allowed until a rebuilt WebUI can use a
per-response nonce. The policy does not send browser traffic through the VPN.
User-opened external links and top-level navigation remain possible.
Server-side connectors, inference, search, release-note refresh, and other
configured operations continue to use their documented container route class.

## Optional VPN and default no-VPN lifecycle

`netns-holder` owns the stable namespace used by route-owning processes. Myst
and the final-hop proxies are trusted co-resident processes; optional Teep or
Tailscale routing switches deliberately promote those processes into the same
namespace. This is a routing boundary, not a sandbox between its residents.
Onyx applications never join it.

Repeated `make up-lite`/`make up-full` calls distinguish the standalone Myst
signup project from the integrated `onyx` Compose project. They stop the
former before stack startup but preserve an already-running integrated Myst
container and its routing namespace.

The standalone project uses `MYST_SETUP_ONLY=true` and `restart: "no"`. Its
entrypoint starts TequilAPI and waits without creating identities, registering,
or placing orders. The host helper is the single mutation owner, validates the
exact identity and live gateway configuration, verifies registration/order
postconditions, and never automatically retries an ambiguous financial
result. `MYST_VPN_IDENTITY` is required for both setup and integrated startup
when the wallet contains multiple identities. Setup health checks only local
TequilAPI and bypasses the recovery
supervisor, so hours or days spent completing payment neither arm PID-1
termination nor create restart churn. `make vpn-signup-stop` explicitly stops
the setup service. Integrated Myst startup performs no signup/order mutation.

With `MYST_VPN_ENABLED=true`, health reads only Myst's loopback
`/connection` status plus the local `myst0` address and route. It requires a
single top-level `Connected` status and verifies that a reserved public test
address routes through `myst0` with that interface's source address. The check
does not perform public DNS or HTTP traffic and does not log the API response,
which can contain provider or identity details. A socket-free supervisor wraps
that same predicate at the existing one-minute steady cadence. It clears its
private process-lifetime state at entrypoint start and arms only after the first
complete readiness success. A later first failure records monotonic uptime;
every success clears it; and continuous failure for at least 60 seconds changes
the armed marker to a one-shot signaled state and sends `SIGTERM` to container
PID 1. The entrypoint stops and reaps the daemon and route-reconciliation child
before exiting, and `restart: unless-stopped` then restarts Myst. Missing or
incomplete registration/funding, provider selection, and an initial tunnel
failure remain visibly unready without restart churn. Explicit no-VPN and
standalone setup modes never arm recovery.

The one-minute steady cadence means automatic restart normally begins between
roughly one and two minutes after a disconnect. Docker and Podman use the same
health command and no container-engine socket. Repeated qualification on both
engines showed graceful cleanup removing the old `myst0` and split routes,
one-attempt reconnection in the unchanged holder namespace, and no successful
application-path requests during the unready window. Request-time final-hop DNS
and destination validation remain authoritative and fail closed throughout;
there is no periodic public probe, upstream-proxy probe, or system-route
fallback while VPN mode is selected.

The separate reconciliation loop retains its 20-second repair and hostname-DNS
refresh bound. Each pass compares the exact exemption target's gateway and
device with the required bridge route. A complete match is a silent no-op;
only a missing or drifted route invokes `ip route replace` and emits the
change log. MTU updates were already change-only. A successful connection gets
one immediate MTU/route reconciliation; the background loop is the single
ongoing owner. Connection polling and provider-selection branches do not
repeat the same operations. This reduces stable-state commands, netlink writes,
and logs without weakening reconnect repair or changing route ownership; an
event-driven replacement remains deferred.

With the default `MYST_VPN_ENABLED=false`, the Myst daemon and its route-
reconciliation loop are not started. The Myst container retains only an inert
process so its existing healthcheck can act as a readiness sentinel for the
namespace owned by `netns-holder`: no wallet is required, no stale `myst0` may
remain, and a usable IPv4 default route must exist. The health supervisor
delegates that predicate without creating recovery state or signaling PID 1.
Switching a live namespace from VPN to no-VPN requires tearing down the old
stack so a stale interface cannot survive.

Configured upstream failures, VPN disconnects, proxy DNS failures, rejected
destinations, and broken bridges all fail closed. Policy-rejected destinations
return HTTP 403; proxy-side NXDOMAIN, no-address, and resolver failures return
HTTP 502. No application or browser is
attached to a direct public network as a fallback. Long-lived CONNECT streams
use connection/setup and idle/framing controls but no arbitrary total tunnel
lifetime.

## Validation

Use Make targets so the correct compose overlays are selected:

```sh
make ps-lite
make ps-full
make vpn-connection-info
docker inspect onyx-api_server-1
docker inspect onyx-obscura-1
docker logs onyx-onyx-public-egress-proxy-1 --since 10m
docker logs myst-client-vpn --since 10m
make health-inventory
```

`make vpn-connection-info` runs `myst connection info` in the running
`myst-client-vpn` container and prints its connection details, including the
active provider identity. It works with either the integrated stack or the
standalone signup container.

The inventory renders effective lite and full Compose models and reports each
retained check's command, startup cadence, steady cadence, and approximate
steady checks per hour. It uses the engine, environment, optional overlays, and
profiles selected by the Makefile rather than a fixed base model. Retained
checks use fast `start_interval` polling only
during their bounded startup period and a ten-minute steady cadence, except
Myst's one-minute recovery check. Origin services whose sole readiness value is
already represented by a fixed gateway or publisher have their duplicate check
disabled. Nginx is likewise the single WebUI boundary: its root check traverses
`web_server`, while its separate API dependency preserves API startup
validation. Docker Engine 25.0+ and Compose 2.20.2+ are required. With a Podman
5.8.1+ server and Compose provider 2.20.2+, the Makefile first creates stopped
containers, copies each regular command and timeout into Podman's native
five-second startup health check, strictly inspects both cadences, and only then
starts the services. A running container without that native configuration is
rejected rather than modified in place. Podman Compose accepting or rendering
`start_interval` alone is not treated as support because its compatibility API
drops that field. Every Compose wait has an explicit 420-second outer timeout,
so a service that remains in Podman's startup state fails stack launch instead
of blocking indefinitely.

Rootless Podman on macOS cannot reliably expose the Docker-compatible socket to
stack containers, so its overlays omit code interpreter. Myst recovery does
not require that socket and is identical on both engines. The Makefile also
omits the executor network overlay under Podman even when a shared environment
leaves its option enabled; no unused executor bridge or health loop is created.
Request routing remains fail closed when Myst is unhealthy. The Podman layer
also replaces Docker's unsupported tmpfs `uid`/`gid` options on the optional
Tailscale frontend gateway with Podman's user-owned `U` mount option; the
gateway continues to run as uid/gid 101 with its other hardening unchanged.
PostgreSQL and OpenSearch use the same initialized `docker-data/postgres` and
`docker-data/opensearch` binds under Docker and Podman. Guarded Podman `keep-id`
mappings plus a narrow PostgreSQL mount-root preflight preserve their expected
ownership. An atomic wrapper marker prevents the two engines from starting
against those shared writers concurrently and is released only after the
owning engine's matching stack is down. Podman API and background startup also
wait for PostgreSQL's native startup health check so database migrations cannot
race initialization.

The Podman volume suffixes solve different Linux-side problems. `:z`/`:Z`
relabel a source for SELinux sharing/isolation and do not change ownership or
make an unshared macOS path visible to the VM. `:U` recursively changes the
source ownership for the container user, which mutates the source and is
rejected with `lchown ... operation not permitted` on the tested macOS
virtiofs share. A disposable live probe found the same uid/gid and write denial
with and without `:Z`; changing only ordinary mode bits permitted the write.
Do not broaden permissions or rewrite attributes on the private Docker data
directories as a workaround; the guarded `keep-id` mappings and PostgreSQL
mount-root preflight avoid that mutation.

In Podman full mode, the wrapper serves `ONYX_RAG_DOC_SOURCE_DIR` directly from
a PID-tracked macOS process. A capability-free fixed relay is the only
container on its dedicated non-internal host uplink and forwards only
`doc-drop-web:8091` to that fixed host port. The server accepts HTTP only when
the peer address is loopback; Podman's userspace gateway presents its relayed
connection that way, while LAN peers are rejected. Application containers remain
on their internal networks, and the Web Connector continues through the exact
doc-drop final-hop policy. This avoids both a VM document copy and any need to
share `/Volumes`; in particular, do not share it merely to follow the
`/Volumes/Macintosh HD` symlink back to the system volume.

Myst discovers the pre-tunnel bridge gateway by parsing the tokens following
`via` and `dev`, not fixed `ip route` field positions. This matters on Podman,
whose device-filtered route output omits the redundant `dev eth0` tokens. The
exact `host.docker.internal` route therefore remains on the engine bridge for
host-capable policy traffic such as the bundled embedding server, while the
VPN default route remains authoritative for public traffic.

Check that application, browser, executor, and host-capable networks remain
distinct; the fixed bridges point at the intended listener; target DNS does
not appear at Docker's embedded resolver; private/internal/metadata targets
are rejected; and an interrupted VPN or proxy produces a visible failure with
no direct route. Also inspect the effective WebUI CSP and prove remote
resources, inline event-handler attributes, and eval are blocked while login
hydration, same-origin APIs, WebSockets, chat images, and local/blob previews
still work.
