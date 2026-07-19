# VPN routing and restricted egress

This stack keeps application containers off Internet-routed Docker networks.
Traffic crosses fixed-destination bridges to final-hop policy proxies in the
trusted `netns-holder` routing namespace. The route owner is Mysterium, a
configured upstream proxy, or the explicit no-VPN system route.

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
| Explicit no-VPN | system DNS and system default route in `netns-holder` |

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

## VPN and no-VPN lifecycle

`netns-holder` gives route-owning processes a stable namespace. Myst and the
final-hop proxies are trusted co-resident processes; optional Teep or
Tailscale routing switches deliberately promote those processes into the same
namespace. This is a routing boundary, not a sandbox between its residents.
Onyx applications never join it.

Repeated `make up-lite`/`make up-full` calls distinguish the standalone Myst
signup project from the integrated `onyx` Compose project. They stop the
former before stack startup but preserve an already-running integrated Myst
container; restarting Myst alone can leave a half-open `myst0` interface and
split default routes in the long-lived holder namespace. Recover such a state
with the matching full `make down-*` then `make up-*` cycle.

With `MYST_VPN_ENABLED=true`, health reads only Myst's loopback
`/connection` status plus the local `myst0` address and route. It requires a
single top-level `Connected` status and verifies that a reserved public test
address routes through `myst0` with that interface's source address. The check
does not perform public DNS or HTTP traffic and does not log the API response,
which can contain provider or identity details. VPN-only autoheal checks once
per minute and may restart Myst after a failing local data-plane check. This
lower cadence can leave a failed route visible for nearly a minute before
recovery begins. Request-time final-hop DNS and destination validation remain
authoritative and fail closed throughout that interval; there is no periodic
upstream-proxy probe or system-route fallback while VPN mode is selected.

With `MYST_VPN_ENABLED=false`, the Myst container idles as namespace owner,
requires no wallet, and readiness requires that no stale `myst0` remains plus
a usable IPv4 default route. The explicit no-VPN compose model omits autoheal
and its Docker socket. Switching a live namespace from VPN to no-VPN requires
tearing down the old stack so a stale interface cannot survive.

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
docker inspect onyx-api_server-1
docker inspect onyx-obscura-1
docker logs onyx-onyx-public-egress-proxy-1 --since 10m
docker logs myst-client-vpn --since 10m
make health-inventory
```

The inventory renders effective lite and full Compose models and reports each
retained check's command, startup cadence, steady cadence, and approximate
steady checks per hour. Retained checks use fast `start_interval` polling only
during their bounded startup period and a ten-minute steady cadence, except
Myst's one-minute recovery check. Origin services whose sole readiness value is
already represented by a fixed gateway or publisher have their duplicate check
disabled. Docker Engine 25.0+ and Compose 2.20.2+ are required; startup rejects
Podman until its health-startup behavior has an equivalent validated contract.

Check that application, browser, executor, and host-capable networks remain
distinct; the fixed bridges point at the intended listener; target DNS does
not appear at Docker's embedded resolver; private/internal/metadata targets
are rejected; and an interrupted VPN or proxy produces a visible failure with
no direct route. Also inspect the effective WebUI CSP and prove remote
resources, inline event-handler attributes, and eval are blocked while login
hydration, same-origin APIs, WebSockets, chat images, and local/blob previews
still work.
