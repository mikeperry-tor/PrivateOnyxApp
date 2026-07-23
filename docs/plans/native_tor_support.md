# Optional native Tor support plan

## Purpose

Add optional, first-class Tor support in two independently selectable roles:

1. route the stack's existing restricted public egress through a wrapper-owned
   Tor SOCKS proxy; and
2. expose the Onyx WebUI as a persistent v3 onion service without publishing
   another host port.

Use the [`dockurr/tor`](https://hub.docker.com/r/dockurr/tor) image and keep Tor
structurally absent when neither role is enabled. The implementation should
follow the existing Tailscale pattern: Makefile-selected Compose layers,
profiles for optional services, a fixed frontend gateway, pinned persistent
state, no application container in a route-owning namespace, and explicit
Docker/Podman validation.

## Status and principal decisions

Status: proposed.

The implementation includes Tor-over-Myst as a supported, separately opt-in
route. It is a small topological extension of the existing
Tailscale-over-Myst pattern and does not require changes to Myst itself. It
must fail configuration validation unless Myst is enabled. All Tor-over-Myst
topology, lifecycle, failure, Docker, and Podman checks are required for
completion.

The following are deliberate design decisions:

- Outbound Tor and onion ingress are independent switches. Enabling either
  starts one Tor process; enabling both reuses that process and persistent
  state. This is also a privacy and resource decision: two local Tor clients
  would create a more distinctive set of guard/directory connections, consume
  more memory, and make the two enabled roles easier for a local observer to
  distinguish. The shared process means the roles share guard state, resource
  pressure, and failure fate; document that tradeoff without presenting the
  roles as independently anonymous Tor clients.
- Tor egress is an internal Unix-domain SOCKS upstream for the existing
  final-hop policy proxies. An explicit engine-managed runtime volume is
  mounted only into Tor and those two policy containers; there is no TCP SOCKS
  listener or SOCKS bridge service.
  Onyx, Obscura, SearXNG, executor, ingress, Myst, Teep, and Tailscale
  containers do not receive the socket mount or gain a new network.
- Tor exposes no TCP control listener. Its health check uses a Tor-only Unix
  control socket on an ephemeral private tmpfs and performs a complete
  SAFECOOKIE exchange; even trusted Myst-namespace peers receive neither the
  socket nor the authentication cookie.
- Onion ingress reaches a fixed, hardened frontend gateway whose configuration
  forwards only to nginx on `onyx-frontend`. As with the existing Tailscale
  gateway, joining `onyx-frontend` gives the gateway network reachability to
  the other peers on that bridge; the fixed nginx configuration is the
  destination restriction, not a claim of network-level isolation after a
  gateway compromise. Tor never joins `onyx-frontend`.
- Tor does not replace the existing explicit upstream-proxy setting. Native
  Tor egress and a non-empty `EGRESS_UPSTREAM_PROXY_URL` are mutually exclusive
  and startup fails rather than choosing one silently.
- Country selection and relay-fingerprint pinning are mutually exclusive.
  Tor's `ExitNodes` list is a union, not an intersection, so accepting both
  would not mean "this fingerprint in this country." A pinned relay already
  fixes the exit more precisely than country selection.
- Exit selection affects only clearnet exit circuits. Onion-service ingress
  uses introduction and rendezvous circuits and does not use a Tor exit.
- Onion client authorization, vanity addresses, bridges/pluggable transports,
  relay operation, arbitrary operator `torrc` fragments, a control-port host
  publication, and automatic onion-key backup are out of scope for the first
  implementation.
- This plan concerns clearnet egress through Tor and inbound access to Onyx at
  its onion address. Do not broaden URL policy or promise arbitrary outbound
  `.onion` browsing as part of this work. If it already works through the
  selected SOCKS path for an otherwise allowed URL, preserve that behavior,
  but test and document it separately before calling it supported.
- Generic configured-upstream endpoint classification and SOCKS framing
  hardening are a prerequisite phase with their own tests/review boundary.
  The Tor transport is added only after that generic behavior passes.
- The existing Tailscale and new onion frontends share one multi-ingress
  policy. `WEB_DOMAIN` remains one canonical callback/link origin; ordinary
  same-origin UI operation and the voice WebSocket support each validated
  runtime frontend without wildcard origins or trusted client forwarding
  headers.

## Required reading and initial audit

Before editing, read these files completely:

- `AGENTS.md`
- `README.md`
- `docs/request_handling.md`
- `docs/vpn_routing_and_proxies.md`
- `docs/internal_network_security.md`
- `docs/resource_minimization.md`
- `docs/podman_suport.md`
- `docs/onyx_patches_upgrade.md`
- `Makefile`
- `stack.versions.env`
- `.env.wrapper.example`
- `docker-compose.yaml`
- `docker-compose.proxy.yml`
- `docker-compose.tailscale-vpn.yml`
- `docker-compose.podman.yml`
- `tailscale/entrypoint.sh`
- `tailscale/frontend-gateway.conf`
- `egress/final_hop_proxy.py`
- `podman/startup_health.py`
- every `WEB_DOMAIN` consumer and the voice WebSocket authentication path in
  the pinned `reference_repos/onyx`
- the network-isolation, restricted-egress, health-inventory, image-pinning,
  Makefile lifecycle, and Podman tests under `tests/`

Start with `git status --short`. Do not read, rewrite, or stage the private
`.env.wrapper`, `docker-data/`, `doc-drop/`, or generated Onyx secret
configuration. The private environment may be sourced for authorized live
validation without printing it.

Before selecting the image pin, inspect the chosen `dockurr/tor` tag and its
source revision. Treat image behavior, packaged Tor version, UID/GID,
GeoIP/GeoIPv6 paths, tools, entrypoint/CMD, architectures, and volume ownership
as contracts rather than assumptions. Record a versioned, multi-architecture
image reference with an immutable manifest digest in `stack.versions.env`;
never use `latest`.

Useful upstream references are:

- [`dockurr/tor` image documentation](https://hub.docker.com/r/dockurr/tor)
- [`dockur/tor` source](https://github.com/dockur/tor)
- [Tor Project onion-service setup](https://community.torproject.org/onion-services/setup/)
- [`torrc(5)` `ExitNodes` syntax](https://manpages.debian.org/unstable/tor/torrc.5.en.html)
- [Tor Control Protocol bootstrap status](https://spec.torproject.org/control-spec/commands.html)
- the pinned source checkout at `reference_repos/tor`, especially
  `doc/man/tor.1.txt`, `src/feature/nodelist/routerset.c`, and the control-auth
  implementation

The source audit used `reference_repos/tor` commit
`f3d28b2e0978ca075ec324834bec077673478ded`. Recheck the same semantics against
the source corresponding to the selected runtime image during implementation;
the reference checkout is evidence for behavior, not a substitute for matching
the packaged Tor version.

The currently audited image candidate is
`docker.io/dockurr/tor:0.4.9.11@sha256:446881b3366cbc2cc5cf8d13a76e3104f60824b7c15343d14defe903ded18f0d`.
It is an OCI index for `linux/amd64`, `linux/arm64`, and `linux/arm/v7`, reports
source revision `6823d1099c7f5e9f51b75cf6b7fdf94a627f11f2`, and contains Tor
0.4.9.11. Reconfirm these contracts when implementation begins rather than
silently advancing the tag or digest.

The audited base image has no configured image user. Its root entrypoint recursively
changes ownership and modes in `/var/lib/tor`, generates a hashed password from
a default `PASSWORD=password`, and supplies default network TCP SOCKS and HTTP
tunnel listeners unless overridden. Do not run that entrypoint.

Build a minimal wrapper image from the pinned base in `tor/Dockerfile`. Its
only filesystem change is to create `/run/tor-egress` as `0755`, owned by the
audited `tor` account UID/GID `101:102`; it must not copy config, install
packages, declare another `VOLUME`, or add an entrypoint. This image-owned
directory initializes the explicit `tor-runtime` named volume with correct
Linux ownership on Docker Desktop and Podman. Execute
`tor -f /etc/tor/torrc` directly as `101:102`, with a read-only root
filesystem, all capabilities dropped, and `no-new-privileges`. The image
stores GeoIP data at `/usr/share/tor/geoip`
and `/usr/share/tor/geoip6`; it includes Bash, `nc`, `od`, `curl`, `python`,
and `python3`, but not `socat`. Python is therefore an audited image contract
used by the SAFECOOKIE health script below, not an incidental implementation
detail. The image declares `/var/lib/tor` as a volume. Explicitly bind it so
no anonymous volume owns state. Pin the upstream as
`TOR_BASE_IMAGE`; derive `TOR_IMAGE` deterministically from the base digest and
the wrapper Dockerfile content, following the repository's existing derived-
image pattern.

## User-visible configuration

Add a dedicated Tor section to `.env.wrapper.example` with these names and
defaults:

```dotenv
TOR_EGRESS_ENABLED=false
TOR_ONION_SERVICE_ENABLED=false
TOR_ROUTE_THROUGH_MYST_VPN=false
TOR_EXIT_COUNTRY=""
TOR_EXIT_NODE_FINGERPRINTS=""
```

Semantics:

- `TOR_EGRESS_ENABLED=true` routes the existing public and configured-external
  final-hop paths through the wrapper Tor SOCKS endpoint. Existing exact
  internal destinations, `host.docker.internal`, and explicitly permitted LAN
  integration routes retain the final-hop proxy's current direct exception
  semantics; Tor must not break local RAG or local inference.
- `TOR_ONION_SERVICE_ENABLED=true` creates or reuses one v3 onion identity and
  maps virtual port 80 to the fixed Tor frontend gateway. The service is
  publicly reachable by anyone who knows or discovers the address; no Tor
  client authorization is provided initially.
- `TOR_ROUTE_THROUGH_MYST_VPN=true` routes the Tor daemon's guard, directory,
  exit, introduction, and rendezvous traffic through the Myst namespace. It
  does not mean "choose a Tor exit in the Myst country." It requires
  `MYST_VPN_ENABLED=true` and at least one Tor role.
- `TOR_EXIT_COUNTRY` is one Tor country selector. Tor documents country
  selectors as case-insensitive, two-letter ISO 3166-1 alpha-2 codes wrapped
  in braces.
  Normalize the wrapper input to lower case and render `ExitNodes {cc}`.
  Reject whitespace, braces, lists, Tor's special pseudo-country selectors
  `??` (unknown) and `A1` (anonymous proxy), and any value other than exactly
  two ASCII letters. Do not maintain a second ISO country database in the
  wrapper: the pinned Tor GeoIP data is authoritative for whether a
  syntactically valid code is known at runtime.
- `TOR_EXIT_NODE_FINGERPRINTS` is a comma-separated list of one or more Tor
  relay identity fingerprints. Accept an optional leading `$`, normalize to
  uppercase, require exactly 40 hexadecimal characters per item, reject empty
  items and duplicates, and impose a documented small upper bound such as 16.
  Render a single `ExitNodes $FP1,$FP2` directive.
- If either exit selector is non-empty while `TOR_EGRESS_ENABLED=false`, fail
  validation. Exit selection has no meaning for onion-only service circuits.
- If both exit selectors are non-empty, fail validation.
- A selected country or fingerprint is strict: unavailable or unsuitable
  exits cause Tor requests to fail; there is no unrestricted-exit fallback.

The pinned Tor source treats `ExitNodes` as the allowed exit set;
`StrictNodes` applies only to `ExcludeNodes`, and `StrictExitNodes` is obsolete.
Do not render either as a supposed strict-exit control. A syntactically valid
but unknown country is accepted during config parsing, logged as unrecognized
after GeoIP is loaded, selects no usable exits, and leaves clearnet requests
unable to obtain a qualifying exit. Tests must prove that runtime failure and
the absence of unrestricted fallback. `tor --verify-config` alone cannot prove
that a country exists in the runtime consensus/GeoIP combination.

Do not expose free-form Tor arguments or config text through environment
variables. Render only validated structured inputs. Do not put generated
onion keys, control credentials, or other secrets in `.env.wrapper.example`.

Because this work also closes the existing Tailscale multi-ingress gap, add one
general WebUI setting outside the Tor-only section:

```dotenv
ONYX_WEB_CANONICAL_ORIGIN="http://localhost:3000"
```

Validate it as exactly one absolute `http` or `https` origin with an ASCII/IDNA
host and optional valid port, but no userinfo, path other than `/`, query,
fragment, control character, or comma/list syntax. Pass it explicitly to both
the pinned backend and web-server containers as their `WEB_DOMAIN`; do not ask
users to edit the generated Onyx deployment environment. The default preserves
current local behavior. An operator who relies on OAuth, email links, or other
absolute URLs from a remote browser should deliberately select the stable
Tailscale or onion origin they want those features to return to. A generated
Tailscale/onion address may require one initial start to discover and then a
normal stack restart after selecting it as canonical. Changing the canonical
origin can require OAuth/OIDC/SAML provider callback re-registration and does
not merge browser cookies or local storage between hostnames.

## Configuration and persistent state

Add a small, stdlib-only renderer, preferably `tor/render_config.py`, and
focused unit tests. It should accept explicit command-line arguments from the
Makefile, validate the complete option set, and atomically create:

```text
docker-data/tor/
├── config/
│   └── torrc
└── state/
    └── onion-service/          # generated by Tor only when onion ingress is on
```

Use host binds only for config and persistent state:

```yaml
- ./docker-data/tor/config/torrc:/etc/tor/torrc:ro
- ./docker-data/tor/state:/var/lib/tor
```

The common Tor service also receives one Tor-only ephemeral tmpfs at
`/run/tor-control`, owned by `101:102` with mode `0700`. It contains only the
private Unix control socket used by the in-container health check. It is never
mounted into another container, never host-bound, and disappears with the Tor
container. Use the existing Docker/Podman-specific tmpfs ownership pattern;
do not place the control socket on the macOS state bind. A disposable Docker
Desktop probe showed that virtiofs allowed creation there but refused Tor's
required private-mode change, causing Tor to fail closed with
`Unable to make ... private`.

When egress is enabled, its overlay additionally mounts an explicitly named
top-level `tor-runtime` volume read-write at `/run/tor-egress` in Tor and
read-only at the same path in the two final-hop policy containers. Do not mark
it external, do not declare it in the image, and do not mount it in onion-only
mode. It contains only a transient socket and may be engine-local; the host
state bind remains the sole owner of the portable onion identity.

This separation is required on macOS, not cosmetic. In a disposable Docker
Desktop test, Tor created a socket on a `/private/tmp` bind but virtiofs refused
Tor's required `chmod(0660)`, so Tor failed closed with "Unable to make ...
group-writable." With the minimal derived image and a fresh explicit named
volume, the mount-only design instead initialized the directory as
`0755 101:102`; `WorldWritable RelaxDirModeCheck` made Tor create the socket as
`0666 101:102`, and a UID/GID `65534:65534` Python policy container with only
the read-only volume mount completed a `0500` SOCKS5 greeting. "World writable"
is scoped inside containers that explicitly receive this otherwise private
volume; it does not publish a host path or grant any unmounted service access.
The wrapper image must not declare the runtime path as a `VOLUME`: the egress
Compose layer is the sole creator/mounter, so onion-only mode gains neither a
named nor anonymous runtime volume.

Use the single-file config bind: the audited image needs no additional file
from `/etc/tor`, while its GeoIP databases live under `/usr/share/tor`. This
also avoids hiding future image-owned files unnecessarily. Do not copy image
GeoIP data to the host. Point `GeoIPFile` and `GeoIPv6File` at the verified
image paths so country selection cannot silently operate without GeoIP data.

The renderer must use a fixed template, write a temporary file in the same
directory, and set non-secret config permissions. Bind that candidate file
directly at `/etc/tor/torrc:ro` in a network-disabled validation container and
run the pinned image's `tor --verify-config -f /etc/tor/torrc` directly as
`101:102`, bypassing the image entrypoint. Give that validation container a
disposable correctly owned data directory and the same private control-tmpfs
path/mode expected at runtime; never mount live onion keys merely to parse
config. Rename the candidate over the final file only after validation. The
renderer must not destroy or recreate the state directory. A failed render or
image-level check leaves the last valid config intact and stops startup.

The generated config should explicitly include, as applicable:

- `DataDirectory /var/lib/tor`
- `SocksPort unix:/run/tor-egress/socks WorldWritable RelaxDirModeCheck` when
  egress is enabled, otherwise `SocksPort 0`; never open a TCP SOCKS listener.
  The flags make mount topology the sole admission boundary; they do not expose
  the socket outside containers that receive the volume
- `DNSPort 0`, `HTTPTunnelPort 0`, `TransPort 0`, and `NATDPort 0`, so the
  wrapper exposes no alternate Tor DNS, HTTP-tunnel, transparent-proxy, or NATD
  client interface
- `ControlPort 0`, `ControlSocket /run/tor-control/control.sock`,
  `ControlSocketsGroupWritable 0`, `CookieAuthentication 1`, an explicit
  `CookieAuthFile /var/lib/tor/control_auth_cookie`, and
  `CookieAuthFileGroupReadable 0`; do not configure a default or operator-known
  control password or any TCP control listener
- `ClientOnly 1`, `ORPort 0`, `DirPort 0`, `ExtORPort 0`, and a rejecting exit
  policy; keep every relay and auxiliary network listener explicitly disabled
- `ExitNodes` only from the validated selector; do not add obsolete
  `StrictExitNodes` or unrelated `StrictNodes`
- `HiddenServiceDir /var/lib/tor/onion-service`
- `HiddenServiceVersion 3`
- `HiddenServicePort 80 tor-frontend-gateway:8080` only when onion ingress is
  enabled
- stdout logging at a non-sensitive level; never enable debug/control tracing
  by default

Verify config-file ownership and the state directory's required owner/mode for
UID/GID `101:102`. The derived image initializes the explicit runtime volume's
directory as `0755`; Tor must create its socket as `0666`. Tor alone receives
that volume read-write; the public and host final-hop policy proxies receive it
read-only without a shared user namespace or supplementary group. A read-only
volume still permits
connecting to an existing Unix socket, but not creating or replacing it. No
other container receives the volume, and it contains no control cookie or
state. The separate control tmpfs and its mode-`0600` socket are visible only
inside Tor; the mode-`0600` authentication cookie remains in Tor's state bind.
The onion-service
directory contains the service's private identity key. It must not be
world-readable, included in a build context, printed by tests, or exposed
through another container. Add `docker-data/tor` to any relevant private-data/
build-context exclusions if the existing `docker-data/` rule is not sufficient.

The onion identity must survive `down`/`up`, engine restart, and warm Docker or
Podman restart. Do not add an automatic key-reset target. Documentation should
explain that loss of `docker-data/tor/state/onion-service` changes the address,
and that copying those files copies the service identity. Any future reset
must require an explicit, stopped-stack, narrowly targeted destructive action.

## Compose topology

Use one common Tor layer and narrowly scoped role/routing layers rather than
putting Tor permanently in `docker-compose.yaml`. Required files are:

- `docker-compose.tor.yml`: common `tor` service, hardening, state/config
  mounts, direct uplink, local health, and both optional-role profiles;
- `docker-compose.tor-egress.yml`: Unix SOCKS volume mounts, internal final-hop
  selection, and Tor egress configuration;
- `docker-compose.tor-onion.yml`: the `tor-ingress` network, fixed frontend
  gateway, Tor attachment to that network, and onion-only dependency;
- `docker-compose.tor-vpn.yml`: optional placement of trusted Tor in the stable
  Myst namespace and the required network aliases/dependencies.

The role overlays are required, not merely a health-ordering convenience.
Compose profiles enable or exclude whole services; they cannot conditionally
add a network attachment, mount, or `depends_on` entry within one service, and
top-level networks are not removed by an inactive profile. Putting the onion
gateway dependency in the common Tor service makes the egress-only model
invalid when the gateway's profile is inactive. Putting both role networks and
mounts in the common service also leaks role-specific topology into the other
mode. The common service therefore belongs to both `tor-egress` and
`tor-onion`, while the Makefile includes the matching role overlay(s) that add
only that role's service fields and resources. The frontend gateway belongs
only to `tor-onion`; egress adds no service. The Makefile derives
`COMPOSE_PROFILES` as a comma-separated union with the existing `tailscale`
profile. Do not overwrite one optional profile when another is enabled.
`down-lite` and `down-full` must activate all known optional profiles so
disabled or newly reconfigured Tor containers are removed as orphans.

### Direct/default Tor placement

The default Tor container should have:

- one dedicated non-internal `tor-uplink` for Tor's own Internet traffic;
- `tor-ingress` only when onion ingress is enabled;
- no published ports; and
- no Onyx backend, data, browser, executor, host-publish, or frontend network.

Use the dedicated Unix socket for egress:

```text
application fixed bridges
  -> final-hop policy proxies in netns-holder
  -> /run/tor-egress/socks (read-only named volume; SOCKS domain request)
  -> Tor process
  -> tor-uplink
  -> Tor network
  -> selected exit -> destination
```

Extend `final_hop_proxy.py` with one internal-only Unix SOCKS setting fixed by
the stack to `/run/tor-egress/socks`; do not expose it in the user-facing proxy
URL. This does not require another SOCKS implementation or dependency. The
egress proxy already implements the SOCKS5 greeting, optional username/password
exchange, domain-form `CONNECT`, and bounded reply framing. Refactor that
protocol exchange to operate on an already-open reader/writer pair: configured
TCP SOCKS proxies retain the validated TCP endpoint opener, while native Tor
uses `asyncio.open_unix_connection()` and then the identical state machine.
Do not fork a Tor-specific SOCKS implementation.

The Tor egress overlay gives the setting and read-only socket volume only to
the public and host policy processes. There is no upstream endpoint DNS,
internal service-name exception, TCP listener, or additional service. The
application-facing bridges and their destination/peer checks remain unchanged.

The SOCKS protocol remains unauthenticated, as Tor documents. Admission is the
explicit volume-mount topology: only the two policy containers can see the
socket. Socket/directory modes and the fixed path are functional configuration
contracts, not a second authorization mechanism. Do not add SOCKS credentials,
a duplicate Tor-only destination policy, or an application-level socket/port
allowlist. Myst, Teep, and Tailscale are trusted route-namespace peers but
receive no socket mount. In combined mode the onion gateway can reach Tor only
at its onion target interface; it cannot use a TCP SOCKS port because none
exists. The same remains true when Tor shares the Myst namespace.

### Generic upstream-proxy DNS-result invariant

Keep one invariant in `final_hop_proxy.py` for every configured upstream proxy,
independent of HTTP versus SOCKS and TCP versus Unix transport:

- validate the requested hostname syntactically and against hostname policy,
  but do not resolve an ordinary external target with system, Docker, or Myst
  DNS before proxying it;
- send the original hostname to the upstream using HTTP authority/absolute
  form or SOCKS5 address type `DOMAINNAME` (`ATYP=0x03`);
- permit only the SOCKS5 `CONNECT` command (`CMD=0x01`); never implement Tor's
  nonstandard hostname-resolution commands, SOCKS `BIND`, or `UDP ASSOCIATE`;
- consume the SOCKS success reply's `BND.ADDR` and `BND.PORT` strictly as
  framing and discard them whether encoded as IPv4, IPv6, or a domain name;
  never return, log, cache, classify, or add that value to `validated_ips`;
- reject an invalid SOCKS version, reserved byte, unoffered authentication
  method, unknown address type, truncated/oversized reply, or trailing framing
  ambiguity before exposing the tunnel; and
- treat HTTP proxy status/headers only as tunnel/forwarding protocol metadata;
  never interpret a proxy response header or body as a DNS answer or feed it
  into destination validation or a later direct connection.

The current egress proxy already has the essential generic behavior:
`_validate_destination()` returns no target IPs when any supported upstream is
selected, both accepted SOCKS URL spellings send a domain-form `CONNECT`, and
the SOCKS reply address is read and discarded. Preserve that behavior while
factoring the Unix transport, and tighten the reply parser as above. Resolving
the configured *proxy endpoint* itself for a TCP HTTP/SOCKS proxy is separate
from resolving the requested target and remains governed by the common
endpoint classifier below. Native Tor's Unix endpoint requires no DNS at all.

Because Tor is a remote-DNS upstream, the wrapper cannot inspect or pin the
target address Tor resolves. Preserve hostname-level denials, but document the
same residual already applicable to other remote-DNS upstreams: the wrapper
cannot prove that a public-looking name was not resolved to a private address
from the upstream/exit's network. Exact wrapper-trusted internal destinations
must continue to bypass the external upstream only under their existing route
class rules.

Be precise about web content. Initial requests, redirects, frames,
subresources, scripts, media, WebSockets, and worker fetches performed inside
the wrapper's crawler/browser paths must use the same public final-hop listener;
Chromium's loopback bypass remains disabled. Literal loopback/private/special
addresses, canonical `localhost`/Docker-host names, single-label service names,
and their built-in suffixes are rejected before upstream proxying. When the
wrapper owns DNS, it classifies the complete answer set, pins the approved
address used for the connection, and therefore also blocks DNS rebinding.

Remote-DNS mode cannot provide that last address-level proof. With native Tor,
any privately resolved address is relative to the remote exit's network, not
the wrapper's Docker/Podman or host network, so it does not make local
`127.0.0.1`, RFC1918 services, or `host.docker.internal` reachable. A different
operator-configured remote proxy located on the host or LAN can have materially
more dangerous private reach and must enforce equivalent destination policy.
Do not claim that hostname checks alone protect the upstream proxy's own local
network.

Content bytes that happen to contain an IP address remain inert data to the
egress layer. An allowed public server may itself act as a fetch/translation
service and return data obtained elsewhere, but the wrapper opens only the
validated tunnel to that public server and must never convert response content
into another connection. Separately, content rendered in the user's WebUI is
outside container egress; preserve the restrictive WebUI CSP, and document
that explicit user navigation and a browser/XSS compromise are not controlled
by final-hop routing.

### Existing configured upstream proxy validation

Treat the generic proxy work in this section and the preceding
`Generic upstream-proxy DNS-result invariant` section as a prerequisite
hardening phase, not as an inseparable Tor implementation change. Complete its
focused unit tests, documentation, and review boundary before adding the native
Unix transport or any Tor Compose layer. The Tor phase may depend on that
validated generic behavior but must not obscure regressions in it. If commits
are requested, keep this prerequisite phase in a separate commit.

Fix the existing no-VPN upstream-endpoint classification in that prerequisite
phase, even though native Tor no longer needs an internal TCP-name exception.
Today, `_open_http_proxy_connection()` sends any non-literal upstream hostname to
system/Docker DNS first when `MYST_VPN_ENABLED=false`; the single-label/internal
hostname rejection is reached only in the VPN branch. No-VPN resolution also
does not classify the returned upstream addresses before connecting. That is
inconsistent with the documented proxy endpoint classes.

Refactor one common validator/resolver used in VPN and no-VPN modes:

- allow a global IP literal;
- allow an RFC1918 IPv4 literal as deliberate operator-selected routing
  infrastructure, but continue to reject loopback, link-local, metadata,
  multicast, unspecified, reserved, and other special-use literals;
- allow exact `host.docker.internal` through its existing host-route exception;
- allow an operator-local `.local`, `.internal`, or `.home.arpa` hostname only
  with `ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true` and only when every system
  answer is RFC1918;
- allow an ordinary public hostname only when the selected system or Myst
  resolver returns a nonempty, entirely global answer set; and
- reject single-label/Docker service names before either resolver is called.

Empty, mixed, or disallowed answer sets fail closed. Keep the configured proxy
URL operator-controlled, credential-redacted, and independent of target-route
permissions. Add symmetric VPN/no-VPN unit cases for every endpoint class and
prove that an arbitrary service name on any holder network is never contacted.

### Onion ingress

Use a separate hardened nginx gateway, modeled on
`tailscale-frontend-gateway`:

```text
Tor client
  -> Tor rendezvous circuit
  -> tor onion service virtual port 80
  -> tor-frontend-gateway:8080 on tor-ingress
  -> nginx:80 on onyx-frontend
  -> Onyx WebUI/API
```

The gateway alone joins `tor-ingress` and `onyx-frontend`. It runs as a fixed
non-root UID/GID, read-only, with all capabilities dropped,
`no-new-privileges`, Podman-compatible tmpfs mounts, a tracked minimal nginx
configuration, request-size/time bounds aligned with the existing frontend,
and no access log that records private paths or onion client activity. Its
fixed configuration forwards only to `nginx:80`, matching the Tailscale
gateway's process-level restriction; do not describe the shared
`onyx-frontend` bridge as destination isolation against gateway compromise. Its
local root health check traverses nginx and must retain the repository's
five-second startup/ten-minute steady cadence. Tor depends on the gateway's
health when onion ingress is selected. Do not publish the gateway or Tor ports
to the host.

Onion traffic terminates Tor encryption at the Tor container and then travels
over isolated internal container networks. State this explicitly; do not call
the Tor-to-gateway hop TLS. Preserve Onyx authentication and the wrapper CSP.
Preserve the client `Host` header for same-origin application behavior, but do
not treat `Host` or any client-supplied forwarded header as proof of a trusted
origin. The gateway must discard incoming `Forwarded` and `X-Forwarded-*`
values before setting its own fixed forwarding metadata. Set
`X-Forwarded-Proto http` for the virtual port-80 onion origin; do not copy
Tailscale's unconditional `X-Forwarded-Proto https`. Validate an onion request
host as a syntactically valid v3 `.onion` name and, when `Origin` is present,
require exact normalized host/port agreement. The Tor rendezvous already
selects the actual service identity; do not create a circular startup
dependency by mounting onion keys/state into the gateway merely to compare the
header with the generated hostname. Never use this syntactic check to generate
canonical redirects. Test forged `Host`, `Origin`, `Forwarded`, and
`X-Forwarded-*` values as well as login, API, streaming, and relevant WebSocket
paths, not only the login HTML.

### Onyx public-origin compatibility

Treat this as one existing multi-ingress problem covering localhost, the
runtime-assigned Tailscale Funnel origin, and the generated onion origin. Do
not add a Tor-only origin bypass. Tailscale Funnel does not accept an arbitrary
operator domain in this wrapper: containerboot expands `${TS_CERT_DOMAIN}` to
the node's assigned `*.ts.net` certificate name, which can change when the
tailnet or configured node hostname changes. The implementation must not
hard-code the current name or require it to equal the single canonical
`WEB_DOMAIN`. Tailscale's TLS/Funnel endpoint authenticates the actual assigned
name before forwarding on the private ingress network; the gateway may enforce
the `*.ts.net` syntax and same-request Host/Origin agreement, but must not treat
that suffix as permission to create redirects, callbacks, or other absolute
URLs.

Keep `WEB_DOMAIN` as one explicit canonical public origin. It remains the
trusted source for redirects and generated links that must not use an
attacker-controlled request host. Do not redefine it as a wildcard or
comma-separated allowlist. Inventory every pinned consumer and classify it in
a checked table under the request/frontend documentation:

- **ordinary same-origin operation**: relative hydration, basic login,
  same-origin APIs, chat streaming, and ordinary downloads should work at
  localhost, the active Tailscale name, and the onion origin;
- **request-origin security check**: the voice WebSocket must compare the
  browser-controlled `Origin` with the normalized origin of that same request,
  rather than with canonical `WEB_DOMAIN`;
- **canonical-origin feature**: OAuth/OIDC/SAML and connector/MCP OAuth
  callbacks, email verification/reset links, Slack/bot links, OpenAPI URLs,
  Build URLs, and other generated absolute links continue to use
  `WEB_DOMAIN`; and
- **cookie policy**: the current auth and CSRF cookie `Secure` decisions are
  derived globally from `WEB_DOMAIN`, which is not accurate when simultaneous
  HTTP localhost, HTTPS Tailscale, and HTTP-scheme onion frontends are enabled.

Implement the narrow multi-origin correction for the voice WebSocket for both
Tailscale and Tor. Parse and normalize the browser `Origin`; require its
host and effective port to equal the request `Host`; accept only the
documented `http`/`https` scheme for that ingress; reject missing, opaque,
multi-valued, malformed, userinfo-bearing, or path/query/fragment-bearing
origins. This retains the CSWSH property: a hostile web page cannot choose its
browser-generated `Origin`, even though a non-browser client can choose both
headers. Do not rewrite `Origin`, accept a suffix/wildcard origin, or use
client-supplied `Forwarded`/`X-Forwarded-*` as authorization. Preserve the
single-use WebSocket token check as an independent second control.

Do not leave cookie transport security dependent on whether the one canonical
origin happens to start with `https`. Normalize the wrapper's internal/local
nginx response to the HTTP-local baseline by removing only the `Secure` flag
from proxied cookies, then let each fixed external edge apply its actual
browser-facing transport contract. Harden the existing Tailscale gateway in
the same implementation phase: overwrite forwarding headers, require a
syntactically valid `*.ts.net` Funnel host, require WebSocket `Origin`
host/port agreement at the edge as defense in depth, and add `Secure` to every
auth/CSRF cookie on its externally HTTPS responses. This also keeps local HTTP
login working when canonical `WEB_DOMAIN` is an HTTPS Tailscale origin.
Preserve the cookie name, value, host-only scope, path, expiry, `HttpOnly`, and
`SameSite` attributes; test login, refresh, logout/deletion, and OAuth CSRF/PKCE
cookies rather than inspecting only one login response. The browser still
receives host-only cookies separately for each frontend; logging in on one
hostname does not log the browser in on another.
For the HTTP-scheme onion origin, do not assume that every supported Tor
Browser treats a `Secure` cookie identically merely because onion transport is
end-to-end encrypted and the origin may be considered potentially trustworthy.
Live-test the selected Tor Browser: add `Secure` at the onion gateway only if
the cookie is accepted and returned there; otherwise keep the normalized
host-only, `HttpOnly`, `SameSite` cookie without `Secure` for that origin and
document the browser-visible limitation. Never weaken the HTTPS Tailscale
cookie to match onion behavior, and never remove `Secure` at an externally
HTTPS edge.

Consequences that must be user-visible:

- choosing `WEB_DOMAIN` selects the one canonical callback/link identity; an
  OAuth provider generally cannot infer or accept every simultaneous frontend,
  and a flow started on another frontend may deliberately return to the
  canonical one;
- email, connector, MCP, SAML, bot, Build, and other absolute links may open
  the canonical frontend rather than the frontend on which the action began;
- without the WebSocket correction, voice fails on every non-canonical
  Tailscale or onion origin even when login and chat appear healthy;
- without per-ingress cookie correction, HTTPS Tailscale sessions inherit the
  canonical origin's potentially non-`Secure` cookie decision; and
- host-only cookies, local storage, and other browser state are isolated by
  hostname, so each frontend can require a separate login and can have
  independent client state.

Any required Onyx runtime patch must be narrow, startup-validated against the
pinned signature/body, covered by forged-origin and valid-local/Tailscale/onion
tests, and documented in both `docs/onyx_patch_info.md` and
`docs/onyx_patches_upgrade.md`. Do not characterize the whole WebUI as broken
merely because a canonical-origin feature intentionally returns to
`WEB_DOMAIN`.

### Tor health and startup

Override the image health check. The audited entrypoint writes its own password
health configuration, and the packaged wrapper can optionally call
`check.torproject.org`; neither contract is appropriate after bypassing the
entrypoint. Periodic Internet health traffic remains prohibited.

Add a small mounted stdlib-only Python health script that connects only to
`/run/tor-control/control.sock`, completes the Tor Control Protocol
`SAFECOOKIE` challenge/response, requests
`GETINFO status/bootstrap-phase`, and succeeds only on the pinned Tor version's
documented completed bootstrap state. It must verify the server-to-controller
HMAC before sending the controller-to-server HMAC; never send the raw cookie
with ordinary `COOKIE` authentication. Bound the socket, line, field, and total
reply lengths, require exact hexadecimal nonce/hash lengths and unambiguous
`250` framing, and reject unexpected authentication methods or duplicate
fields. It must not print the authentication cookie, nonces, hashes, control
reply, circuit paths, exit identities, or onion address on success. On failure,
emit only a concise local reason. Use five-second startup checks, a ten-minute
ordinary interval, one retry, and a bounded start period/timeout consistent
with Tor's realistic bootstrap time and the existing 420-second outer Compose
wait.

The control socket must remain on Tor's private ephemeral tmpfs and must always
require explicit cookie authentication. Tor's default `CookieAuthentication`
is off, and a control listener with no cookie or hashed password accepts
unauthenticated control. The generated config therefore disables TCP control,
sets the private socket and cookie directives above explicitly, and configures
no default password. Myst, Teep, Tailscale, final-hop proxies, gateways, and
applications receive neither the control tmpfs nor the state bind, so
Tor-over-Myst does not expose a loopback control plane to trusted namespace
co-residents.

Startup/image tests must issue `PROTOCOLINFO` over the Unix socket and require
exactly `COOKIE,SAFECOOKIE` with no `NULL` or password method, issue an
unauthenticated command and require `514 Authentication required`, complete a
real SAFECOOKIE exchange, verify the server hash, and retrieve bootstrap state.
The audited Tor 0.4.9.11 image created the control tmpfs as `0700 101:102`, the
control socket and cookie as `0600 101:102`, and passed that exchange under
both Docker and rootless Podman keep-id. A second Docker Desktop probe proved
that placing the socket on the macOS state bind fails Tor's private-mode
change; keep the tmpfs separation as a tested portability and security
contract.

Config validation happens before Compose mutation. Runtime health proves Tor
bootstrap; it must not make a public request, create a test circuit, or verify
the selected exit on every cadence. The frontend gateway has a local structural
health check. When egress is enabled, Tor reaching bootstrap also proves the
configured Unix listener was accepted, while end-to-end SOCKS usability
remains an explicit live test.
Selector correctness is not periodic monitoring.

## Tor over Myst

### Required design

When `TOR_ROUTE_THROUGH_MYST_VPN=true`, apply a separate overlay that:

- requires `MYST_VPN_ENABLED=true` in Makefile/config validation;
- sets `tor` to `network_mode: service:netns-holder` and resets its ordinary
  network list;
- attaches `netns-holder` to `tor-ingress` only when onion ingress is enabled,
  with an exact `tor` alias so the unchanged fixed frontend gateway works;
- makes Tor depend on healthy Myst and the selected frontend gateway; and
- leaves Onyx applications outside the trusted namespace.

Tor then shares Myst's loopback, interfaces, routes, DNS context, and policy
listeners. This is not a sandbox boundary: Tor becomes a trusted co-resident
process just like VPN-routed Tailscale or Teep. The Myst kill switch and
readiness must remain authoritative, so a Myst outage cannot make Tor fall
back to the namespace's ordinary route. Verify restart behavior when Myst
reconnects in the stable holder namespace.

This mode hides Tor guard/directory connections from the host ISP behind Myst,
at the cost of linking Tor activity to the selected Myst provider/exit and
adding a provider that can observe the host-to-Tor traffic pattern. It does not
add anonymity "hops" inside Tor, does not change onion end-to-end properties,
and can reduce reliability or throughput.

### Required implementation and validation contract

Tor-over-Myst adds one small overlay and no new daemon. The implementation must
retain the same single Tor process, config, state, health predicate, onion
identity, fixed ingress gateway, and Unix egress socket used by direct Tor
placement. Only the Tor process's network placement and the holder's exact
onion-network alias change.

The following are mandatory final-validation requirements:

- Docker and Podman must both preserve the required `tor` aliases and give the
  shared-namespace Tor process access to the same read-only config and writable
  state mounts used in direct mode.
- Tor must bootstrap, serve onion ingress, and provide SOCKS egress without a
  direct uplink while placed in the Myst namespace.
- Initial Myst unavailability, later Myst loss, and qualified Myst recovery
  must never give Tor a direct-route fallback. Recovery must leave Tor usable
  without changing its onion identity or permanently wedging it in the stable
  holder namespace.
- The design must not add an application network, engine socket, privileged
  mode, extra capability, engine-specific control daemon, or second Tor
  process.
- Docker and Podman must expose the same config, state, health, restart, and
  failure semantics. An engine-specific Compose mount/UID correction is
  acceptable only under the narrow Podman rules below; it must not create a
  separate identity or lifecycle.

If one fails during implementation, the work is incomplete and the exact
blocker must be reported to the user before changing scope.

Do not multiply every selector, role, mode, engine, proxy, Tailscale, Teep, and
executor switch into an exhaustive live Cartesian product. Deterministically
render all structural Tor combinations, then use the pairwise live matrix
below. Country/fingerprint behavior is Tor configuration, not a different
network topology.

## Makefile and lifecycle

Add Makefile variables for all five Tor options, the general
`ONYX_WEB_CANONICAL_ORIGIN`, `TOR_BASE_IMAGE`, and the derived `TOR_IMAGE`.
Split readiness into two ordered stages:

1. a pure host-side semantic preflight validates booleans exactly
   (`true`/`false`), selector syntax, the canonical-origin syntax, and every
   cross-option constraint before creating directories, pulling/building
   images, claiming shared data, starting host processes, or running Compose;
   and
2. after `tor-image-ready`, `tor-config-ready` renders a same-directory
   candidate, runs the offline image-level `tor --verify-config`, and only then
   atomically replaces the last valid config.

This resolves the otherwise contradictory requirement to use the image for
syntax verification while also rejecting invalid user input before image or
filesystem mutation.

Add or update:

- `TOR_BASE_IMAGE` in `stack.versions.env`, pinned by version and immutable
  digest, plus a content-derived local `TOR_IMAGE` tag;
- `tor-build`/`tor-image-ready`, which pull exactly the base image and build or
  inspect the minimal wrapper without substituting a mutable tag;
- a mutation-free Tor semantic preflight plus `tor-config-ready`, which invokes
  the renderer and offline image config check only when a Tor role is enabled;
- Tor common/egress/onion/VPN suffix selection in both `LITE_FILES` and
  `FULL_FILES`;
- profile union logic that preserves Tailscale plus Tor profiles;
- `tor-image-ready` and `tor-config-ready` in the applicable `up-lite` and
  `up-full` prerequisite chains;
- the Tor base image audit/pull and derived-image rebuild in the future
  steady-state `upgrade` workflow, together with revalidation of any Onyx patch
  that depends on the pinned Tor/frontend behavior;
- all Tor layers/profiles in `down-lite` and `down-full`; and
- help text describing Tor enablement and the onion-address command.

Adding Tor does not itself require or authorize refreshing Onyx deployment
artifacts or Python dependency locks. During this implementation, validate the
new pin and derived image directly rather than running a broad Onyx upgrade
merely to add Tor. After integration, future intentional `make upgrade` runs
must include Tor and any dependent Onyx multi-ingress patch in the normal
component audit.

Add this required target:

```text
make tor-onion-address
```

The target must:

1. fail clearly unless `TOR_ONION_SERVICE_ENABLED=true`;
2. use the Makefile-selected engine, environment files, Compose layers, and
   profiles;
3. require an existing running/healthy Tor service, without starting or
   restarting the stack;
4. read `/var/lib/tor/onion-service/hostname` inside the Tor container rather
   than reading private state directly on the host;
5. treat the hostname written by the pinned Tor process as authoritative rather
   than implementing a second onion-address format/checksum validator; require
   only one nonempty line so malformed state cannot inject terminal output; and
6. print only a short label and that Tor-produced address, never keys, directory
   listings, config, or control data.

It must behave the same for Docker and Podman and for lite/full. If the address
has not yet been generated, report that Tor is not ready and point to the
selected `make ps-*`/logs command.

Tor state is not application database state and should not participate in the
PostgreSQL/OpenSearch shared-data engine ownership marker unless live testing
proves concurrent Docker/Podman access can corrupt it. Starting both engines
against the same Tor bind is nevertheless unsafe. The normal Onyx engine claim
already excludes concurrent stacks; add deterministic coverage proving Tor
startup remains behind that claim rather than creating a second ownership
system.

## Podman-specific implementation

Treat Podman as a separate qualification target. A preliminary qualification
on the repository's macOS/rootless baseline used client 5.8.5, server 5.8.1,
and the exact audited arm64 manifest.
Podman reported the same empty image user, root entrypoint, state-volume
declaration, `101:102` Tor account, GeoIP paths, and Tor version as Docker.
Its OCI image inspection exposed no usable inherited health check (and Podman
warned that OCI output ignores `HEALTHCHECK` while building the disposable
derived image), reinforcing the requirement to install and inspect the
Compose-owned native startup/regular checks rather than rely on image metadata.
With disposable `/private/tmp` binds,
`userns_mode: keep-id:uid=101,gid=102`, and container user `101:102`, Tor ran
read-only/capability-free, saw both binds as `101:102`, created state as `0700`
and the control cookie as `0600`, and reused the same state on a warm container
replacement. The macOS paths remained owned by host UID 501/GID 0; Podman did
not recursively change host ownership. Repeat this evidence against the real
repository paths during implementation, including actual onion-key creation
and an engine/VM restart.

The selected mount-only named-volume design was also tested with the minimal
derived image. Rootless Podman initialized the explicit volume directory as
`0755 101:102`, Tor created the socket as `0666 101:102`, and Tor remained
non-root/read-only/capability-free. A policy container in Podman's default user
namespace, running as `65534:65534` with no supplementary group or matching
user-namespace mapping, completed a `0500` SOCKS5 greeting when—and only
because—it received the explicit read-only volume mount. This removes the
earlier shared-UID/group design and makes the effective Compose mount list the
single SOCKS admission boundary to audit.

Use the common state/config bind mounts and add the verified
`userns_mode: keep-id:uid=101,gid=102` correction only to Tor in the Podman
layers; the policy containers do not need it.
Do not use recursive `:U` on a host bind, broad `chmod`/`chown`, an anonymous
runtime volume, privileged mode, or a host-side Tor daemon. Do not duplicate
the state into a Podman-only location because the onion identity must remain
stable when intentionally switching engines under the existing exclusive
ownership workflow.

Give `/run/tor-control` an engine-specific tmpfs ownership expression: Docker
uses its verified `uid=101,gid=102,mode=0700` syntax, while Podman must use the
narrow keep-id/`:U` form proven to produce `0700 101:102` without mutating a
host bind. Inspect the actual control socket as `0600 101:102` on both engines.
If the common nginx tmpfs syntax used by `tor-frontend-gateway` is rejected by
Podman, mirror the existing Tailscale gateway's `:U,mode=0755` override. Extend
`podman/startup_health.py` expectations so the Tor and gateway checks receive
the exact native startup-health translation before containers start. Rendered
Compose `start_interval` alone is not acceptance evidence.

Do not use Docker commands as Podman fallback. Use `CONTAINER_BIN=podman` on
every Make invocation and direct `podman` inspection for evidence.

## Deterministic implementation tests

Add focused tests before or with the implementation.

### Config renderer tests

Cover:

- both roles disabled (renderer refuses invocation or emits no runtime config);
- egress-only, onion-only, and combined config;
- normalized country and fingerprint output;
- invalid booleans, country-selector syntax/special pseudo-country values,
  fingerprint lengths/characters, duplicates, empty comma items,
  selector-without-egress, and both selectors together;
- no newline or directive injection;
- `SocksPort 0` for onion-only;
- the exact Unix `SocksPort` flags and explicit absence of TCP SOCKS, DNS,
  HTTP-tunnel, transparent-proxy, and NATD listeners for egress;
- `ControlPort 0`, the exact private Unix control socket, mandatory cookie
  authentication, private socket/cookie modes, no default password, and
  rejection of unauthenticated control commands;
- explicit `ORPort 0`, `DirPort 0`, and `ExtORPort 0` plus the absence of every
  unintended relay, metrics, control, and auxiliary TCP/UDP listener;
- hidden-service directives only when enabled;
- `ExitNodes` no-fallback behavior, absence of obsolete strict directives, and
  verified GeoIP paths;
- atomic replacement and preservation of the prior file on validation failure;
- no deletion or rewriting of state/key files; and
- image-level `tor --verify-config` invocation with networking disabled.

### Image contract tests

Inspect the pinned base and derived image on both engines. Assert the audited
Tor version, architectures, source label, UID/GID, GeoIP paths, base entrypoint
and tools, including both Python command names and the absence of `socat`;
assert that Compose bypasses that entrypoint and overrides the image health
check. Assert the derived filesystem change is limited to the owned
`0755 /run/tor-egress` directory and that it does not declare that directory as
a `VOLUME`. With a fresh explicit runtime volume, require directory
`0755 101:102`, socket `0666 101:102`, non-root Tor startup with no capability,
and successful access from an otherwise unprivileged policy container only
when the volume is mounted. Also prove every Tor model explicitly binds
`/var/lib/tor`, overriding the base image's declared volume so identity state
never enters an anonymous volume.

### Compose and Makefile tests

Extend `tests/test_onyx_network_isolation.py`,
`tests/test_myst_lifecycle_makefile.py`, `tests/test_health_inventory.py`,
`tests/test_podman_startup_health.py`, and
`tests/test_immutable_component_pins.py` as appropriate. Assert:

- no Tor service/network/layer/profile in the default model;
- correct profile union for Tailscale plus either/both Tor roles;
- exact common, egress, onion, and VPN layer selection for lite/full and
  Docker/Podman;
- invalid combinations fail in the host semantic preflight before image,
  Compose, shared-data, host-process, or filesystem mutation; image-level
  verification occurs only after that preflight and preserves the prior config
  on failure;
- canonical origin rejects lists, wildcard/suffix forms, userinfo, unsafe
  schemes, malformed IDNA/ports, and non-root paths/query/fragments, and is
  passed identically as `WEB_DOMAIN` to the backend and web server;
- immutable `TOR_BASE_IMAGE`, content-derived `TOR_IMAGE`, and no `latest`
  fallback;
- Tor alone has an Internet uplink in direct mode;
- applications receive no Tor network and remain off public networks;
- Tor does not join `onyx-frontend`; only the hardened gateway spans ingress
  and frontend;
- there is no SOCKS bridge service or TCP SOCKS listener;
- final-hop proxies alone receive the read-only Unix socket volume and exact
  internal socket setting; no application, ingress, Myst, Teep, or Tailscale
  service receives it;
- generic internal proxy names are rejected before DNS in both VPN and no-VPN
  modes, while documented public/host/RFC1918 endpoint classes retain their
  exact behavior;
- onion-only mode exposes no SOCKS listener and selects no egress override;
- native Tor egress conflicts with explicit upstream proxy configuration;
- direct mode does not join the Myst namespace;
- Tor-over-Myst joins only the trusted namespace, depends on healthy Myst,
  has no direct uplink, and leaves applications outside it;
- no Tor/control/frontend host ports are published and no TCP control listener
  exists even in the shared Myst namespace;
- config/state mounts are exactly scoped and state is writable only by Tor;
- the private control tmpfs is mounted only by Tor, the socket and cookie are
  `0600 101:102`, a complete SAFECOOKIE exchange succeeds, ordinary COOKIE is
  never used by health, and no namespace peer receives either filesystem;
- the explicit runtime volume is writable only by Tor, mounted read-only in
  policy containers, absent elsewhere, and not created in onion-only mode;
- Docker and Podman policy containers need no Tor group, matching UID, or
  shared user namespace; the effective volume-mount list is the sole SOCKS
  admission rule;
- local-only health commands, five-second startup cadence, ten-minute steady
  cadence, and Podman's exact native startup-health model;
- down targets activate all Tor profiles and remove stale role containers; and
- `tor-onion-address` uses the selected engine/model, trusts Tor's hostname
  format, and fails safely for disabled, stopped, missing, empty, or multi-line
  state.

Add focused frontend tests shared by Tailscale and Tor. Inventory every pinned
`WEB_DOMAIN` consumer; prove valid local, runtime Tailscale, and v3-onion
same-origin WebSockets; reject cross-origin, missing, opaque, malformed,
multi-valued, userinfo, path/query/fragment, host/port-mismatch, suffix-only,
and forged-forwarding cases. Prove Tailscale responses add `Secure` to auth and
CSRF/PKCE cookies after the internal/local nginx normalization, without
changing their host-only/HttpOnly/SameSite/path/expiry properties; prove local
HTTP login/logout still works when the canonical origin is HTTPS; and record
the validated Tor Browser cookie behavior for the HTTP-scheme onion origin.
Canonical OAuth/OIDC/SAML/connector/MCP callbacks and generated absolute links
must still use `WEB_DOMAIN`, not a request header.

Add final-hop proxy unit cases for Unix connection failure and the shared
SOCKS protocol state machine over TCP and Unix transports. For every HTTP,
HTTPS, SOCKS5, and SOCKS5h upstream, assert that ordinary target names never
reach Docker/system/Myst DNS and that no upstream response can populate
`validated_ips` or trigger a later direct connection. Assert the SOCKS command
is always `CONNECT` with `ATYP=DOMAINNAME`; feed successful IPv4, IPv6, and
domain-form bound-address replies and prove their values are consumed but
unobservable, then cover every malformed reply class listed above. Add the
symmetric configured-upstream endpoint-classification cases specified above
for VPN and no-VPN modes. Do not add a duplicate Tor-path/port authorization
test: effective-model mount assertions are authoritative for admission.

Run `make check` as the normal deterministic gate. Pull/inspect the new pinned
Tor image and add a focused Tor image-contract test. Do not run a broad Onyx
upgrade merely to add Tor. If the pinned source audit confirms that the
multi-ingress WebSocket correction requires a new or changed runtime patch,
run `make check-upgrade` against the current pinned Onyx images as required for
a runtime-patch-only change; do not refresh Onyx deployment artifacts first.
If a dependency lock changes, follow the repository's ordinary lock upgrade
and `make check-upgrade` rules.

## Effective-model validation matrix

Render through the Makefile, never by hand-assembling Compose files. Automated
model tests should cover both lite and full, Docker and Podman, for:

| Egress | Onion | Myst route | Expected structural result |
| --- | --- | --- | --- |
| off | off | off | no Tor layer, profile, service, or network |
| on | off | off | Tor + Unix SOCKS volume, direct Tor uplink, no frontend gateway/network |
| off | on | off | Tor + frontend gateway, direct Tor uplink, no egress override |
| on | on | off | one Tor daemon, Unix SOCKS volume + frontend gateway, direct Tor uplink |
| on | off | on | one Tor daemon in healthy Myst namespace, SOCKS path, no direct uplink |
| off | on | on | one Tor daemon in healthy Myst namespace, frontend path, no direct uplink |
| on | on | on | one Tor daemon in healthy Myst namespace, both paths, no direct uplink |

Also render and reject: Myst route with Myst disabled, Myst route with both Tor
roles disabled, selector without egress, country plus fingerprint, Tor egress
plus `EGRESS_UPSTREAM_PROXY_URL`, and invalid boolean values. Include at least
one model with Tailscale, Teep-over-Myst, and network-enabled executor options
to prove profile/layer union and unchanged application isolation; it need not
be a live Cartesian matrix.

## Live validation procedures

Use non-sensitive test URLs/accounts and do not print private `.env.wrapper`
contents, onion keys, cookies, queries, or document names. Record exact image
digest, engine server/client/provider versions, mode, switches, and omitted
rows. A public Tor-check request is acceptable as an explicit operator-run
validation step; it must never become a health check.

### Common direct-mode procedure

For Docker, then Podman:

1. Start from the matching stack down, with the other engine confirmed not to
   own shared data. Preserve existing Tor state.
2. Select combined direct mode and one stack mode, run `make up-lite` or
   `make up-full`, then inspect `make ps-*`, Tor, the Unix socket mounts,
   gateway, nginx, Myst sentinel, and final-hop logs.
3. Confirm Tor has the sole `tor-uplink`, no ports are published, applications
   have no Tor networks or socket mount, only the two policy containers can see
   the socket, and the frontend gateway's fixed configuration selects nginx.
4. Inspect health. Confirm local control bootstrap reaches complete, the
   frontend gateway traverses nginx, and ordinary checks settle at ten-minute
   cadence. On Podman inspect `.Config.StartupHealthCheck` separately from
   `.Config.Healthcheck`.
5. Make a real `web_search` and a real `open_url` request. If executor network
   is supported/enabled for that engine, make one generated-code HTTPS
   request. Confirm the observed public IP is a Tor exit, target DNS is sent
   through SOCKS, and stopping Tor or making the socket unavailable causes
   visible failure with no direct/VPN/system fallback.
6. Exercise one configured internal destination (for example local RAG in full
   mode) and confirm its existing exact direct exception still works and was
   not sent to Tor.
7. Run `make tor-onion-address`; use the Tor-produced hostname without a second
   address-format validator. From a separate Tor Browser or isolated Tor
   client, load login, authenticate with a test account, make a same-origin API
   request, and verify chat streaming. Exercise the pinned voice WebSocket and
   every other audited `WEB_DOMAIN` consumer separately; confirm the safe
   multi-frontend behavior or the explicitly documented canonical-origin
   limitation. Confirm CSP and authentication match local access and record
   whether the selected Tor Browser accepts and returns a `Secure` cookie on
   the HTTP-scheme onion origin.
   In one representative model, enable Tailscale too, obtain its actual
   runtime-assigned Funnel name without hard-coding it, and repeat login,
   same-origin API, voice WebSocket, forged-Origin rejection, canonical-link
   behavior, and cookie inspection. Require `Secure` on the externally HTTPS
   Tailscale auth/CSRF cookies while local and onion cookies retain their
   separately validated behavior.
8. Run the address target again and prove the address is unchanged after
   `down`/`up`. Restart Docker Desktop or the Podman VM when practical and
   repeat address/state and health checks.
9. Disable only egress, restart, and prove onion access remains while clearnet
   egress returns to the selected ordinary route. Disable only onion ingress
   and prove its gateway is absent while Tor egress remains. Disable both and
   prove all Tor services/networks are absent after `down`/`up`.

Run the procedure at least once in lite and once in full on each engine. The
full-mode repetition may focus on model equivalence, local RAG preservation,
onion login/API, and one Tor-routed request rather than duplicating every
failure injection.

### Exit-country and exit-pinning validation

Use direct egress for one country-selector test and one fingerprint-selector
test across the two engines; swap engines if regional availability requires
it. For each:

1. render and inspect the normalized config without exposing unrelated state;
2. start Tor and perform multiple fresh clearnet requests/circuits;
3. use a one-time explicit Tor Control Protocol inspection to obtain the exit
   relay identities for those circuits without adding a periodic monitor;
4. for fingerprint pinning, require every clearnet exit circuit to end at one
   of the configured fingerprints;
5. for country selection, resolve each observed exit identity/address against
   Tor's loaded consensus/GeoIP data or a reputable explicit validation source
   and require the selected country; and
6. select one deliberately unavailable/non-exit test fingerprint and prove
   requests fail rather than using an unrestricted exit.

Do not log full circuit paths or retain more identity/IP evidence than needed
for the validation report. Note that a pinned relay may not allow every target
port and can materially reduce reliability and anonymity-set diversity.

### Tor-over-Myst pairwise procedure

Fund/configure Myst through the repository's normal workflow and run these
representative rows on both Docker and Podman:

- combined Tor roles in lite mode through Myst;
- Tor egress through Myst in full mode, including local RAG preservation; and
- onion-only through Myst on at least one engine, proving no unnecessary Unix
  socket mount or egress overlay.

For each row:

1. inspect the Tor process namespace, default/split routes, and interfaces;
2. inspect the effective network aliases and config/state mounts, then prove
   they match the direct-mode identity and ownership contracts on both engines;
3. prove Tor has no direct `tor-uplink` and its guard/directory connection
   leaves through `myst0`;
4. prove the clearnet destination sees a Tor exit rather than the Myst exit;
5. prove onion ingress and `make tor-onion-address` still work;
6. interrupt Myst after it has become healthy and confirm Tor egress/onion
   reachability fail with no direct fallback;
7. observe the existing qualified Myst recovery, then confirm Tor reboots or
   resumes cleanly as designed and retains the same onion identity; and
8. stop/recreate the matching stack and repeat route, alias, mount, health,
   identity, and usability inspection.

Also start with Myst unavailable before Tor has ever become healthy. Confirm
Tor remains unready, opens no successful clearnet or onion path, and does not
fall back to a direct route or enter hidden restart churn. After Myst becomes
ready, confirm the common local control health reaches completed bootstrap.

Inspect the final Compose/container security model on both engines and prove
there is still one Tor process, no engine socket, no privileged mode or added
capability, no application attachment to Tor/Myst networks, and no
engine-specific control service.

Also prove the direct-default Tor mode remains on `tor-uplink` when Myst is
enabled but `TOR_ROUTE_THROUGH_MYST_VPN=false`; a global Myst switch must not
implicitly reroute Tor.

## Documentation updates during implementation

Update behavior in place rather than leaving the plan as the only authority:

- `README.md`: concise user setup, privacy/reliability tradeoffs, onion address
  command, public onion exposure, persistent-key warning, exit selectors, and
  Tor-over-Myst consequences; also explain the one canonical `WEB_DOMAIN`,
  separate frontend logins/state, and which flows return to that canonical
  origin;
- `.env.wrapper.example`: user-visible options and conflicts only;
- `docs/vpn_routing_and_proxies.md`: Tor route ownership, remote-DNS residual,
  Unix SOCKS ownership, the generic prohibition on local target DNS and
  proxy-returned address reuse, configured-upstream validation in VPN/no-VPN
  modes, selector semantics, direct versus Myst placement, failure behavior,
  and verification commands;
- `docs/internal_network_security.md`: Tor trust/reachability rows, Unix-socket
  mount boundary, private control tmpfs, fixed frontend gateway, absence of
  application/direct networks, onion ingress boundary, and the shared
  Tailscale/Tor Host/Origin validation model;
- `docs/resource_minimization.md`: optional process/health cadence and absence
  when disabled;
- `docs/podman_suport.md`: state-bind ownership, named-volume ownership,
  Tor-only keep-id mapping, mount-only SOCKS admission, overlay,
  startup-health translation, engine-switch persistence, and live checklist;
- `docs/request_handling.md`: Tor as an optional remote-DNS clearnet route,
  Unix socket/failure ownership, the generic no-local-target-DNS/no-returned-IP
  proxy invariant, unchanged URL/HTTP policy, explicit unsupported status of
  general outbound `.onion` browsing, and any separately tested incidental
  `.onion` behavior;
- `docs/onyx_patch_info.md`: if the pinned Onyx runtime needs the narrow
  request-origin WebSocket correction, document its purpose, exact target, and
  startup validation;
- `docs/onyx_patches_upgrade.md`: add the Tor image/config contract, the
  `WEB_DOMAIN` consumer inventory, Tailscale/Tor gateway behavior, and any
  dependent Onyx patch to the component upgrade checklist; and
- `AGENTS.md`: short orientation, new layer/target locations, and invariants.

Document that Tor exits are commonly blocked or challenged, fixed-country and
especially fixed-relay selection reduce availability and can reduce anonymity,
and onion ingress does not make weak Onyx credentials safe. Do not describe Tor
as eliminating traffic correlation or the browser CSP as full XSS prevention.

## Acceptance criteria

Implementation is complete only when all of the following are true:

- Default lite/full models under Docker and Podman contain no Tor artifacts and
  preserve current routing behavior.
- Each Tor role can be enabled independently; combined mode uses exactly one
  pinned `dockurr/tor` container.
- Tor egress covers the existing final-hop public paths without giving
  applications a Tor or public network and without weakening destination,
  bridge-peer, host/LAN, HTTP, or fail-closed policy.
- Tor opens no TCP SOCKS listener. Only the two final-hop policy containers can
  see its Unix socket through the explicit read-only runtime volume; no extra
  bridge service, socket credential, group-based admission rule, or internal
  upstream-name exception exists.
- Target DNS for Tor-routed requests is delegated to Tor; no direct DNS or
  transport fallback succeeds when Tor, its socket, or its selected exit is
  unavailable.
- Every upstream-proxy scheme keeps ordinary target names out of local/Myst
  DNS, uses tunnel/forwarding protocols only, and discards all proxy-returned
  addressing metadata without allowing it to become a validated address or a
  direct-connection input. SOCKS resolution commands are unsupported.
- Configured non-Tor proxy endpoints use the same explicit endpoint classes in
  VPN and no-VPN modes; internal/single-label names are rejected before DNS and
  all resolved public answer sets are classified.
- Country/fingerprint syntax is injection-safe and live evidence proves the
  selected constraint; Tor-unknown countries and unavailable/unsuitable pins
  fail closed without an unrestricted exit.
- Onion ingress publishes no host port, reaches Onyx only through the fixed
  hardened gateway configuration, supports login/API/streaming, and preserves
  CSP and Onyx authentication. Every audited `WEB_DOMAIN` consumer has either
  safe tested multi-frontend behavior or a precise canonical-origin limitation.
  The same request-origin WebSocket rule and forwarding-header hardening cover
  existing Tailscale ingress; arbitrary Origin/Host/forwarded-header trust is
  never introduced, and HTTPS Tailscale cookies retain `Secure`.
- `make tor-onion-address` works identically for Docker/Podman and lite/full,
  treats Tor's output as authoritative, rejects only unsafe empty/multi-line
  state, and never exposes keys or unrelated state.
- Onion identity survives normal down/up, warm restart, and an explicitly
  validated engine switch; private key permissions remain restrictive.
- Tor config and state use the documented `docker-data/tor` bind storage;
  config is a single-file read-only bind, identity state never falls into an
  anonymous volume, the SOCKS socket alone uses the explicit engine-local
  `tor-runtime` volume, and the private control socket alone uses the Tor-only
  ephemeral control tmpfs.
- Image health is overridden with a local authenticated bootstrap check; no
  periodic health check performs Internet/DNS traffic or logs sensitive Tor
  state.
- The Tor image entrypoint is bypassed; Tor runs directly as audited UID/GID
  `101:102`, read-only, capability-free, and no-new-privileges. It has no TCP
  control listener; its mode-`0600` Tor-only Unix control socket explicitly
  requires a private cookie, advertises no `NULL` or default-password
  authentication, and passes a server-verified SAFECOOKIE exchange without
  exposing the raw cookie to namespace peers.
- Podman receives exact native startup-health translation and needs no Docker
  fallback, engine socket, privileged mode, recursive ownership mutation, or
  separate onion identity.
- Tor-over-Myst passes on both engines: aliases and mounts work in the shared
  namespace; initial Myst unavailability and later Myst loss have no direct
  fallback; qualified recovery restores Tor without a permanent wedge or
  identity change; and the design uses one unprivileged, engine-socket-free Tor
  process with no application network expansion.
- `make check`, the focused pinned-image contract test, all effective-model
  rows, and the proportionate live matrix pass. Any row that cannot be run is
  named with its reason and is not represented as validated.
- README, subsystem docs, Podman guidance, upgrade guidance, configuration
  example, tests, and `AGENTS.md` agree with the implemented behavior.

## Handoff record

When implementing, update this section rather than appending a dated journal.
Record:

- selected image tag, immutable digest, source revision, Tor version,
  architectures, UID/GID, GeoIP paths, and verified image contract;
- final Compose layers, profiles, mount strategy, and Tor-over-Myst namespace
  and alias implementation;
- deterministic and live commands run, with engine/mode/role coverage;
- selector and failure-injection evidence in non-sensitive summary form;
- Docker/Podman versions and restart cases exercised;
- any omitted validation and exact reason; and
- final running stack/engine state without exposing the onion address unless
  the operator explicitly wants it recorded.
