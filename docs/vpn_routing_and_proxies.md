# VPN routing and restricted egress

This stack keeps application containers off Internet-routed Docker networks.
Traffic crosses fixed-destination bridges to final-hop policy proxies in the
trusted `netns-holder` routing namespace. The route owner is Mysterium, a
configured upstream proxy, or the explicit no-VPN system route.

## Route classes

| Class | Callers | Fixed bridge | Policy |
| --- | --- | --- | --- |
| Public Onyx | generic helpers, saved public MCP/Web Connector traffic | `onyx-public-egress-bridge` | public destinations only |
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
RFC1918 behavior. `EGRESS_ALLOW_RFC1918=true` accepts RFC1918 literals and
operator-local names ending in `.local`, `.internal`, or `.home.arpa` only
after complete-answer-set classification. Empty, failed, or mixed local
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

## DNS ownership

Docker DNS resolves only internal service names. Browser target names are
passed unresolved to the final hop:

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

## VPN and no-VPN lifecycle

`netns-holder` gives route-owning processes a stable namespace. Myst and the
final-hop proxies are trusted co-resident processes; optional Teep or
Tailscale routing switches deliberately promote those processes into the same
namespace. This is a routing boundary, not a sandbox between its residents.
Onyx applications never join it.

With `MYST_VPN_ENABLED=true`, health requires a connected Myst daemon, a usable
`myst0` address, route, and provider-resolver data path. VPN-only autoheal may
restart Myst after that data-plane health fails. The final-hop proxies wait for
route health and never expose a system-route fallback while VPN mode is
selected.

With `MYST_VPN_ENABLED=false`, the Myst container idles as namespace owner,
requires no wallet, and readiness requires that no stale `myst0` remains plus
a usable IPv4 default route. The explicit no-VPN compose model omits autoheal
and its Docker socket. Switching a live namespace from VPN to no-VPN requires
tearing down the old stack so a stale interface cannot survive.

Configured upstream failures, VPN disconnects, proxy DNS failures, rejected
destinations, and broken bridges all fail closed. No application or browser is
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
```

Check that application, browser, executor, and host-capable networks remain
distinct; the fixed bridges point at the intended listener; target DNS does
not appear at Docker's embedded resolver; private/internal/metadata targets
are rejected; and an interrupted VPN or proxy produces a visible failure with
no direct route.
