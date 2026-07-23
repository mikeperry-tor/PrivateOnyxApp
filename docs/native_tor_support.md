# Native Tor support

## Scope and roles

The wrapper can run one pinned Tor client for two independently selected roles:

- `TOR_EGRESS_ENABLED=true` routes the existing public and configured-external
  final-hop policies through Tor.
- `TOR_ONION_SERVICE_ENABLED=true` exposes the WebUI as a persistent public v3
  onion service.

Enabling either role starts the same Tor process. Combined mode deliberately
shares guard state, resource pressure, and failure fate rather than creating
two distinguishable Tor clients. Tor runs directly on `tor-uplink`; routing Tor
through Myst is not implemented. General outbound `.onion` browsing, bridges,
pluggable transports, relay operation, onion client authorization, arbitrary
torrc fragments, and automatic identity backup are unsupported.

`TOR_EXIT_COUNTRY` and `TOR_EXIT_NODE_FINGERPRINTS` optionally constrain
clearnet exits. They are mutually exclusive, require Tor egress, and do not
affect onion-service circuits. Native Tor egress conflicts with
`EGRESS_UPSTREAM_PROXY_URL`. The host-side renderer validates these constraints
before Compose or shared-data mutation.

## Compose and request paths

The Makefile selects narrowly scoped layers:

- `docker-compose.tor.yml` defines the common Tor process, direct uplink,
  persistent binds, private control tmpfs, and local health check.
- `docker-compose.tor-egress.yml` adds the engine-local `tor-runtime` volume
  and mounts it read-only into the public and host final-hop policy containers.
- `docker-compose.tor-onion.yml` adds `tor-ingress` and the fixed
  `tor-frontend-gateway`.
- `docker-compose.tor-podman.yml` and
  `docker-compose.tor-onion-podman.yml` contain only Podman ownership, sysctl,
  and tmpfs translations.

No Tor layer is selected when both roles are disabled. Combined mode still has
one Tor service.

The egress path is:

```text
restricted application network
  -> fixed bridge
  -> public or host final-hop policy in netns-holder
  -> /run/tor-egress/socks
  -> Tor on tor-uplink
  -> Tor network and selected exit
```

The socket is a Unix-domain SOCKS listener; no TCP SOCKS port or bridge service
exists. Tor mounts the named runtime volume read-write. Only the two policy
containers mount it read-only. Applications, browsers, executors, ingress
gateways, Myst, Teep, and Tailscale receive neither the volume nor a Tor
network. Ordinary target names remain unresolved until the shared SOCKS state
machine sends a domain-form `CONNECT`; socket, circuit, selector, and protocol
failures have no direct fallback. Exact internal, host, and explicitly allowed
LAN integration routes retain their existing direct exception semantics.

The onion ingress path is:

```text
Tor client
  -> v3 onion rendezvous
  -> Tor virtual port 80
  -> tor-frontend-gateway:8080 on tor-ingress
  -> nginx:80 on onyx-frontend
```

Tor retains `tor-uplink` and joins `tor-ingress`, but never joins
`onyx-frontend`. The fixed gateway alone spans `tor-ingress` and
`onyx-frontend`. Tor encryption terminates in the Tor container; the isolated
internal hops use plaintext HTTP. The gateway accepts a syntactically valid v3
onion `Host`, scrubs client forwarding headers, preserves the Host for
same-origin behavior, and forwards only to nginx. It publishes no host port.

## Configuration, state, and process contract

`tor/render_config.py` uses the same restricted settings parser as Make layer
selection and atomically renders:

```text
docker-data/tor/
├── config/
│   └── torrc
└── state/
    └── onion-service/
```

The config is mounted as one read-only file at `/etc/tor/torrc`; state is
mounted at `/var/lib/tor`. The onion-service directory contains the persistent
private identity. Deleting it changes the onion address. Copying it gives the
recipient the ability to operate the same onion identity. Do not print, stage,
or casually inspect its keys.

The transient SOCKS socket exists only in the engine-local `tor-runtime` named
volume. The control socket and authentication cookie exist only on Tor's
ephemeral `/run/tor-control` tmpfs and are not shared with another container.

The minimal derived image bypasses the upstream entrypoint and runs the pinned
Tor binary directly as UID/GID `101:102`, with a read-only root filesystem, all
capabilities dropped, and `no-new-privileges`. `ClientOnly 1` is the single
relay-mode control. The pinned Tor manpage states that it prevents relay and
directory service even if `ORPort`, `ExtORPort`, or `DirPort` is configured,
and the pinned source makes `server_mode()` return false whenever it is set.
Do not duplicate it with relay-port zeros, `ExitPolicy`, or other relay-only
settings.

Client-facing interfaces remain explicit: the selected Unix `SocksPort` or
`SocksPort 0`, `DNSPort 0`, `HTTPTunnelPort 0`, `TransPort 0`, and `NATDPort
0`. TCP control is disabled with `ControlPort 0`. Tor exposes only the private
cookie-authenticated Unix control socket.

## Health, lifecycle, and diagnostics

Tor health authenticates with the private cookie and requests
`GETINFO status/bootstrap-phase`. It performs no periodic public DNS or HTTP
request. Startup checks run every five seconds; established health runs every
ten minutes. The onion gateway uses the same startup/steady cadence and checks
the complete local gateway-to-nginx path.

Use Make targets so diagnostics use the selected engine, environment, and
Compose layers:

```sh
make ps-lite
make ps-full
make health-inventory
make tor-onion-address
make test-tor-image
```

For targeted runtime inspection, select the configured engine explicitly:

```sh
docker inspect onyx-tor-1
docker logs onyx-tor-1 --since 10m
docker logs onyx-onyx-public-egress-proxy-1 --since 10m
docker logs onyx-onyx-host-egress-proxy-1 --since 10m
docker logs onyx-tor-frontend-gateway-1 --since 10m
```

Replace `docker` with `podman` when `CONTAINER_BIN=podman`; never use Docker as
an implicit fallback. Do not print the control cookie, onion keys, circuit
paths, exit identities, or private request details while diagnosing.

Common failure boundaries are:

- Tor unhealthy: inspect bootstrap notices and selector availability.
- Egress proxy healthy but requests fail: inspect Tor health, socket mounts,
  selector suitability, and final-hop protocol errors; do not add a direct
  fallback.
- Onion address unavailable: confirm onion mode is selected and Tor is
  running, then use `make tor-onion-address`; do not read keys on the host.
- Gateway unhealthy: inspect `tor-frontend-gateway`, nginx, and the fixed
  `tor-ingress`/`onyx-frontend` attachments.
- Engine switch failure: ensure both stacks are down, preserve
  `docker-data/tor/state`, and follow `docs/podman_suport.md`.

## Canonical origin and browser sessions

`ONYX_WEB_CANONICAL_ORIGIN` supplies one explicit `WEB_DOMAIN` to the backend
and web server. Localhost, Tailscale, and onion ingress may coexist, but each
hostname has separate browser cookies, storage, login, and logout state.
OAuth/federated callbacks, email and Build links, voice WebSockets, and other
absolute-origin behavior are guaranteed only at the selected canonical origin.
The gateways do not rewrite application cookie attributes.

For a feature-complete onion frontend, obtain the generated address, set an
HTTP onion canonical origin, and restart normally. Changing the canonical
origin can require external identity-provider callback changes.

## Change and validation map

Before changing Tor behavior, read:

- `docs/vpn_routing_and_proxies.md` for egress ownership, DNS, selectors, and
  configured-upstream behavior;
- `docs/internal_network_security.md` for reachability and ingress boundaries;
- `docs/resource_minimization.md` for optional lifecycle and health cadence;
- `docs/podman_suport.md` for keep-id, tmpfs, startup health, and engine
  switching;
- `docs/request_handling.md` for crawler/search behavior through remote DNS;
  and
- `docs/onyx_patches_upgrade.md` before changing the image pin or audited
  runtime contract.

Keep `tor/render_config.py`, the Tor Compose layers, Make selection, docs, and
the focused tests aligned. Run `make check` for ordinary changes. Run
`make test-tor-image` with both engines for image, config, mount, ownership, or
control changes. Pin or dependency upgrades require `make check-upgrade` plus
the documented Docker/Podman live matrix, including real Tor egress, onion
ingress, selector failure, identity persistence, and simultaneous frontend
behavior.
