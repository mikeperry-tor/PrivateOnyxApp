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
through Myst is not implemented. Outbound onion destinations are supported
through the egress role, including `http://` hosts ending in `.onion` without
the general cleartext-HTTP opt-in. Bridges, pluggable transports, relay
operation, onion client authorization, arbitrary torrc fragments, and
automatic identity backup are unsupported.

`TOR_EXIT_COUNTRY` and `TOR_EXIT_NODE_FINGERPRINTS` optionally constrain
clearnet exits. They are mutually exclusive, require Tor egress, and do not
affect onion-service circuits. Native Tor egress conflicts with
`EGRESS_UPSTREAM_PROXY_URL`. The host-side renderer validates these constraints
before Compose or shared-data mutation.

## Compose and request paths

The Makefile selects narrowly scoped layers:

- `compose_overlays/docker-compose.tor.yml` defines the common Tor process,
  direct uplink, persistent binds, private control tmpfs, and local health
  check.
- `compose_overlays/docker-compose.tor-egress.yml` adds the engine-local
  `tor-runtime` volume and mounts it read-only into the public and host
  final-hop policy containers.
- `compose_overlays/docker-compose.tor-onion.yml` adds `tor-ingress` and the
  fixed `tor-frontend-gateway`.
- `compose_overlays/docker-compose.tor-docker-macos.yml` translates only the
  Tor process and private control tmpfs to UID/GID `0:0` for Docker Desktop's
  bind-ownership model.
- `compose_overlays/docker-compose.tor-egress-docker-macos.yml` disables image
  copy-up for the engine-local SOCKS volume so that its fresh mount root uses
  the same Docker Desktop UID/GID.
- `compose_overlays/docker-compose.tor-docker-linux.yml` maps Tor to the
  invoking host UID/GID so the shared mode-0700 state bind remains directly
  usable across native Docker and rootless Podman.
- `compose_overlays/docker-compose.tor-egress-docker-linux.yml` replaces the
  Docker named SOCKS volume with a host-owned, Docker-specific transient bind;
  this keeps Tor non-root without changing shared state ownership.
- `compose_overlays/docker-compose.tor-podman.yml` and
  `compose_overlays/docker-compose.tor-onion-podman.yml` contain only Podman
  ownership, sysctl, and tmpfs translations.

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
exists. Tor mounts the selected runtime path read-write. Only the two policy
containers mount it read-only. Applications, browsers, executors, ingress
gateways, Myst, Teep, and Tailscale receive neither the volume nor a Tor
network. Ordinary target names remain unresolved until the shared SOCKS state
machine sends a domain-form `CONNECT`; socket, circuit, selector, and protocol
failures have no direct fallback. Exact internal destinations, permitted host
ports, the exact configured local embedding authority, and explicitly allowed
LAN integration routes retain direct semantics. An unlisted
`host.docker.internal` port is denied before DNS and never falls through to
Tor.

The egress layer gives `api_server` one internal capability allowing the stock
and direct-Obscura `open_url` validators to accept `http://` whenever the
normalized host ends in `.onion`. Initial URLs and existing redirect/final-URL
checks use the same rule. The final-hop policy independently permits that
cleartext scheme only when its fixed native-Tor Unix socket is configured.
Other remote-DNS upstream proxies do not receive the exception, and ordinary
clearnet HTTP remains controlled by `EGRESS_ALLOW_HTTP_URLS`. The wrapper does
not validate the onion name beyond its suffix; the complete hostname,
including any subdomain, is sent to Tor for authoritative validation.

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

The transient SOCKS socket exists in the engine-local `tor-runtime` named
volume for Podman and Docker Desktop. Native Docker uses
`docker-data/tor/docker-runtime`, which startup initializes as a host-owned
mode-0755 directory and clears of a stale socket before launch. It contains no
persistent Tor identity. The control socket and authentication cookie exist
only on Tor's ephemeral `/run/tor-control` tmpfs and are not shared with
another container.

The common Compose layer explicitly replaces the upstream entrypoint with the
pinned `/usr/bin/tor` binary; an empty entrypoint override is insufficient on
native Podman. It normally runs directly as UID/GID `101:102`, with a read-only
root filesystem, all capabilities dropped, and `no-new-privileges`. Native Docker
on Linux runs it as the invoking host UID/GID because Docker lacks Podman's
keep-id mapping; the state bind and Docker-specific socket bind therefore need
no privileged ownership rewrite and remain compatible with rootless Podman.
Docker Desktop on macOS is the narrow exception: its bind transport reports a
host bind root as UID/GID `0:0` regardless of host ownership or a container-side
`chown`, and
Tor strictly rejects a data directory not owned by its effective UID. The
Docker-macOS overlay therefore runs Tor as capability-free UID/GID `0:0` and
uses a root-owned mode-0700 control tmpfs. Its writable paths remain limited to
the state bind and optional SOCKS runtime volume. The egress translation uses
`volume.nocopy` so Docker does not populate that fresh volume from the image's
`101:102` directory. Podman retains the non-root keep-id mapping.

`ClientOnly 1` is the single relay-mode control. The pinned Tor manpage states
that it prevents relay and directory service even if `ORPort`, `ExtORPort`, or
`DirPort` is configured, and the pinned source makes `server_mode()` return
false whenever it is set. Do not duplicate it with relay-port zeros,
`ExitPolicy`, or other relay-only settings.

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
- Docker Desktop reports `/var/lib/tor` as root-owned: confirm the selected
  Compose model includes `docker-compose.tor-docker-macos.yml`; clearing the
  bind does not change Docker Desktop's mount-root ownership.

## Canonical origin and browser sessions

`WEBUI_CANONICAL_ORIGIN` supplies one explicit `WEB_DOMAIN` to the backend
and web server. Localhost, Tailscale, and onion ingress may coexist, but each
hostname has separate browser cookies, storage, login, and logout state.
Onyx builds invitation, verification, and password-reset links, OAuth/OIDC/SAML
login callbacks, connector and MCP OAuth callbacks, Build and other absolute
links, and origin-checked voice WebSockets from this value. The gateways do not
rewrite those URLs or application cookie attributes.

The canonical scheme selects cookie security globally rather than per ingress.
An HTTPS canonical origin makes authentication and CSRF cookies `Secure`, so an
HTTP secondary origin may be unable to establish or return its login cookie. An
HTTP canonical origin permits login on both HTTP onion and HTTPS Tailscale
origins, but the Tailscale session cookie lacks the `Secure` attribute. A
non-canonical origin also cannot use voice WebSockets because Onyx compares the
browser's `Origin` header with the canonical origin. Ordinary relative API and
chat requests can still work from either hostname. Absolute links opened from
a secondary hostname lead to the canonical hostname and therefore its separate
browser session.

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
control changes, including Tor-only pin or dependency upgrades. Use
`make check-upgrade` only when the change also spans the patch-image or
OpenSearch image families, or for a broad release gate. Complete the documented
Docker/Podman live matrix for Tor runtime changes, including real Tor egress,
onion ingress, selector failure, identity persistence, and simultaneous
frontend behavior.
