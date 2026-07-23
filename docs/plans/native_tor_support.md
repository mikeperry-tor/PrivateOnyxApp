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
  state.
- Tor egress is an internally selected `socks5h` upstream for the existing
  final-hop policy proxies. Onyx, Obscura, SearXNG, and executor containers do
  not connect to Tor directly and gain no new network.
- Onion ingress reaches only a fixed, hardened frontend gateway, which reaches
  only nginx on `onyx-frontend`. Tor never joins `onyx-frontend`.
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
- `egress/final_hop_proxy.py`
- `podman/startup_health.py`
- the network-isolation, restricted-egress, health-inventory, image-pinning,
  Makefile lifecycle, and Podman tests under `tests/`

Start with `git status --short`. Do not read, rewrite, or stage the private
`.env.wrapper`, `docker-data/`, `doc-drop/`, or generated Onyx secret
configuration. The private environment may be sourced for authorized live
validation without printing it.

Before selecting the image pin, inspect the chosen `dockurr/tor` tag and its
source revision. The image currently documents SOCKS on port 9050, custom
`/etc/tor/torrc`, and persistent `/var/lib/tor`; its current Dockerfile also
runs as user `tor` and supplies an Internet-backed health check. Treat the
image behavior, packaged Tor version, UID/GID, GeoIP/GeoIPv6 file locations,
available local tools, entrypoint/CMD, supported architectures, and volume
ownership as contracts to verify, not assumptions from this plan. Record a
versioned, multi-architecture image reference with an immutable manifest
digest in `stack.versions.env`; never use `latest`.

Useful upstream references are:

- [`dockurr/tor` image documentation](https://hub.docker.com/r/dockurr/tor)
- [`dockur/tor` source](https://github.com/dockur/tor)
- [Tor Project onion-service setup](https://community.torproject.org/onion-services/setup/)
- [`torrc(5)` `ExitNodes` syntax](https://manpages.debian.org/unstable/tor/torrc.5.en.html)
- [Tor Control Protocol bootstrap status](https://spec.torproject.org/control-spec/commands.html)

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
- `TOR_EXIT_COUNTRY` is one ISO 3166-1 alpha-2 country code. Normalize it to
  lower case and render `ExitNodes {cc}`. Reject whitespace, braces, lists,
  `??`, `A1`, and any value other than exactly two ASCII letters.
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

Do not expose free-form Tor arguments or config text through environment
variables. Render only validated structured inputs. Do not put generated
onion keys, control credentials, or other secrets in `.env.wrapper.example`.

## Configuration and persistent state

Add a small, stdlib-only renderer, preferably `tor/render_config.py`, and
focused unit tests. It should accept explicit command-line arguments from the
Makefile, validate the complete option set, and atomically create:

```text
docker-data/tor/
├── config/
│   └── torrc
└── state/
    └── onion-service/       # generated by Tor only when onion ingress is on
```

Use bind mounts, subject to the Podman ownership qualification below:

```yaml
- ./docker-data/tor/config:/etc/tor:ro
- ./docker-data/tor/state:/var/lib/tor
```

If mounting all of `/etc/tor` hides image files Tor actually needs, bind the
single generated `torrc` file instead while keeping it below
`docker-data/tor/config`; document the reason. Do not copy image GeoIP data to
the host. Point `GeoIPFile` and `GeoIPv6File` at the paths verified inside the
pinned image so country selection cannot silently operate without GeoIP data.

The renderer must use a fixed template, write a temporary file in the same
directory, set non-secret config permissions, run the pinned image's
`tor --verify-config -f /etc/tor/torrc` against the exact mounts without
networking, and rename only after validation. It must not destroy or recreate
the state directory. A failed render or image-level config check leaves the
last valid config intact and stops startup.

The generated config should explicitly include, as applicable:

- `DataDirectory /var/lib/tor`
- `SocksPort 0.0.0.0:9050` when egress is enabled, otherwise `SocksPort 0`
- a local control port and cookie authentication used only for health, with
  the cookie kept under `/var/lib/tor`
- `ClientOnly 1`, no relay/OR/Dir port, and a rejecting exit policy
- `ExitNodes` only from the validated selector, with strict no-fallback
  behavior appropriate to the pinned Tor version
- `HiddenServiceDir /var/lib/tor/onion-service`
- `HiddenServiceVersion 3`
- `HiddenServicePort 80 tor-frontend-gateway:8080` only when onion ingress is
  enabled
- stdout logging at a non-sensitive level; never enable debug/control tracing
  by default

Verify config-file ownership and the state directory's required owner/mode for
the image's actual `tor` UID/GID. The onion-service directory contains the
service's private identity key. It must not be world-readable, included in a
build context, printed by tests, or exposed through another container. Add
`docker-data/tor` to any relevant private-data/build-context exclusions if the
existing `docker-data/` rule is not already sufficient.

The onion identity must survive `down`/`up`, engine restart, and warm Docker or
Podman restart. Do not add an automatic key-reset target. Documentation should
explain that loss of `docker-data/tor/state/onion-service` changes the address,
and that copying those files copies the service identity. Any future reset
must require an explicit, stopped-stack, narrowly targeted destructive action.

## Compose topology

Use one common Tor layer and narrowly scoped role/routing layers rather than
putting Tor permanently in `docker-compose.yaml`. Suggested files are:

- `docker-compose.tor.yml`: common `tor` service, hardening, state/config
  mounts, local health, Tor-only networks, optional-role profiles, and the
  frontend/socks gateway definitions;
- `docker-compose.tor-egress.yml`: final-hop SOCKS selection, exact internal
  upstream permission, Tor SOCKS bridge, and dependencies;
- `docker-compose.tor-vpn.yml`: optional placement of trusted Tor in the stable
  Myst namespace and the required network aliases/dependencies.

An onion-only layer may be added if Compose cannot express the frontend
gateway cleanly with profiles. Prefer profiles `tor-egress` and `tor-onion`:
the Tor service belongs to both and starts if either is active; the SOCKS
bridge belongs only to `tor-egress`; the frontend gateway belongs only to
`tor-onion`. The Makefile derives `COMPOSE_PROFILES` as a comma-separated union
with the existing `tailscale` profile. Do not overwrite one optional profile
when another is enabled. `down-lite` and `down-full` must activate all known
optional profiles so disabled or newly reconfigured Tor containers are
removed as orphans.

### Direct/default Tor placement

The default Tor container should have:

- one dedicated non-internal `tor-uplink` for Tor's own Internet traffic;
- one internal `tor-socks` network used only by Tor and a fixed SOCKS bridge;
- one internal `tor-ingress` network used only by Tor and the fixed frontend
  gateway;
- no published ports; and
- no Onyx backend, data, browser, executor, host-publish, or frontend network.

Use a fixed, non-root, read-only, capability-free `socat` bridge for egress:

```text
final-hop proxies in netns-holder
  -> tor-policy-upstream (internal)
  -> tor-socks-bridge:9050
  -> tor-socks (internal)
  -> tor:9050
  -> tor-uplink
  -> Tor network
  -> selected exit -> destination
```

This extra bridge is intentional. It prevents Tor from joining a network of
the trusted namespace and prevents the policy proxies from joining Tor's
network. The bridge has one fixed listen target and one fixed connect target.
Only `netns-holder` and the bridge join `tor-policy-upstream`. Tor cannot use
that network to reach policy listeners.

The current final-hop proxy deliberately rejects unknown internal upstream
proxy names. Extend it with a narrow, startup-validated exact internal
upstream permission for `tor-socks-bridge:9050`; do not loosen generic internal
name handling or reuse the LAN opt-in. Both public and host policy processes
receive the internally generated
`socks5h://tor-socks-bridge:9050` only when native Tor egress is enabled. Target
hostnames remain unresolved until Tor receives the SOCKS request. The
application-facing bridges and their destination/peer checks remain unchanged.

Because Tor is a remote-DNS upstream, the wrapper cannot inspect or pin the
target address Tor resolves. Preserve hostname-level denials, but document the
same residual already applicable to other remote-DNS upstreams: the wrapper
cannot prove that a public-looking name was not resolved to a private address
from the upstream/exit's network. Exact wrapper-trusted internal destinations
must continue to bypass the external upstream only under their existing route
class rules.

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
local root health check traverses nginx and must retain the repository's
five-second startup/ten-minute steady cadence. Tor depends on the gateway's
health when onion ingress is selected. Do not publish the gateway or Tor ports
to the host.

Onion traffic terminates Tor encryption at the Tor container and then travels
over isolated internal container networks. State this explicitly; do not call
the Tor-to-gateway hop TLS. Preserve Onyx authentication and the wrapper CSP.
Test the WebSocket/API paths, not only the login HTML.

### Tor health and startup

Override the image's built-in external `check.torproject.org` health check. A
periodic Internet request violates this repository's local-only health policy
and creates needless identifiable background traffic.

Add a small mounted health script that talks only to `127.0.0.1:9051`, performs
cookie authentication, requests `GETINFO status/bootstrap-phase`, and succeeds
only on the pinned Tor version's documented completed bootstrap state. It must
not print the authentication cookie, circuit paths, exit identities, onion
address, or control reply on success. On failure, emit only a concise local
reason. Use five-second startup checks, a ten-minute ordinary interval, one
retry, and a bounded start period/timeout consistent with Tor's realistic
bootstrap time and the existing 420-second outer Compose wait.

Config validation happens before Compose mutation. Runtime health proves Tor
bootstrap; it must not make a public request, create a test circuit, or verify
the selected exit on every cadence. The frontend and SOCKS bridges should use
local structural health checks. Egress usability and selector correctness are
explicit live validation steps, not periodic monitoring.

## Tor over Myst

### Required design

When `TOR_ROUTE_THROUGH_MYST_VPN=true`, apply a separate overlay that:

- requires `MYST_VPN_ENABLED=true` in Makefile/config validation;
- sets `tor` to `network_mode: service:netns-holder` and resets its ordinary
  network list;
- attaches `netns-holder` to `tor-socks` and/or `tor-ingress` only as needed,
  with an exact `tor` alias so the unchanged fixed gateways still work;
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
identity, and fixed ingress/egress gateways used by direct Tor placement. Only
the Tor process's network placement and the holder's exact internal aliases
change.

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

Add Makefile variables for all five user options and `TOR_IMAGE`. Validate
booleans exactly (`true`/`false`) and validate cross-option constraints before
creating directories, pulling images, claiming shared data, starting host
processes, or running Compose.

Add or update:

- `TOR_IMAGE` in `stack.versions.env`, pinned by version and immutable digest;
- `tor-image-ready`, which inspects or pulls exactly that image and never
  substitutes a mutable tag;
- `tor-config-ready`, which invokes the renderer and offline image config
  check only when a Tor role is enabled;
- Tor common/egress/VPN suffix selection in both `LITE_FILES` and `FULL_FILES`;
- profile union logic that preserves Tailscale plus Tor profiles;
- `tor-image-ready` and `tor-config-ready` in the applicable `up-lite` and
  `up-full` prerequisite chains;
- the Tor image in the `upgrade` pull workflow;
- all Tor layers/profiles in `down-lite` and `down-full`; and
- help text describing Tor enablement and the onion-address command.

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
5. trim and validate exactly one lower-case v3 hostname matching 56 base32
   characters followed by `.onion`; and
6. print only a short label and the validated address, never keys, directory
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

Treat Podman as a separate qualification target. Before choosing a mount
strategy, use the pinned image to determine the `tor` UID/GID and test a
disposable directory under `/private/tmp` through rootless Podman. Confirm
config readability, state creation, restrictive onion-key permissions, warm
reuse, and no ownership mutation outside the disposable target.

Prefer the common bind mounts. If rootless Podman cannot write
`docker-data/tor/state` as the image's user, add the narrowest
`docker-compose.podman.yml` override, likely a verified `keep-id` mapping for
the exact Tor UID/GID. Do not use recursive `:U`, broad `chmod`/`chown`, an
anonymous engine volume, privileged mode, or a host-side Tor daemon. Do not
duplicate the state into a Podman-only location because the onion identity
must remain stable when intentionally switching engines under the existing
exclusive ownership workflow.

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
- invalid booleans, country codes, fingerprint lengths/characters, duplicates,
  empty comma items, selector-without-egress, and both selectors together;
- no newline or directive injection;
- `SocksPort 0` for onion-only;
- hidden-service directives only when enabled;
- strict exit selection and verified GeoIP paths;
- atomic replacement and preservation of the prior file on validation failure;
- no deletion or rewriting of state/key files; and
- image-level `tor --verify-config` invocation with networking disabled.

### Compose and Makefile tests

Extend `tests/test_onyx_network_isolation.py`,
`tests/test_myst_lifecycle_makefile.py`, `tests/test_health_inventory.py`,
`tests/test_podman_startup_health.py`, and
`tests/test_immutable_component_pins.py` as appropriate. Assert:

- no Tor service/network/layer/profile in the default model;
- correct profile union for Tailscale plus either/both Tor roles;
- exact common, egress, and VPN layer selection for lite/full and Docker/Podman;
- invalid combinations fail before Compose and filesystem mutation;
- immutable `TOR_IMAGE` and no `latest` fallback;
- Tor alone has an Internet uplink in direct mode;
- applications receive no Tor network and remain off public networks;
- Tor does not join `onyx-frontend`; only the hardened gateway spans ingress
  and frontend;
- the SOCKS bridge is fixed-destination, hardened, and the only span between
  Tor and the namespace-facing internal network;
- final-hop proxies alone receive the internal SOCKS URL and exact internal
  upstream allowlist;
- generic internal proxy names remain rejected by `final_hop_proxy.py`;
- onion-only mode exposes no SOCKS listener and selects no egress override;
- native Tor egress conflicts with explicit upstream proxy configuration;
- direct mode does not join the Myst namespace;
- Tor-over-Myst joins only the trusted namespace, depends on healthy Myst,
  has no direct uplink, and leaves applications outside it;
- no Tor/control/frontend host ports are published;
- config/state mounts are exactly scoped and state is writable only by Tor;
- local-only health commands, five-second startup cadence, ten-minute steady
  cadence, and Podman's exact native startup-health model;
- down targets activate all Tor profiles and remove stale role containers; and
- `tor-onion-address` uses the selected engine/model, validates v3 output, and
  fails safely for disabled, stopped, missing, malformed, or multi-line state.

Add final-hop proxy unit cases for the exact Tor bridge permission, wrong port,
wrong name, feature-disabled behavior, and unchanged rejection of arbitrary
Docker service names. Test that target names are sent in SOCKS domain form and
are never resolved by Docker/system/Myst DNS when Tor egress is selected.

Run `make check` as the normal deterministic gate. Because this adds an image
pin but no runtime Python lock or Onyx patch, pull/inspect the new pinned image
and add a focused Tor image-contract test. Do not run a broad Onyx upgrade
merely to add Tor. If implementation changes an existing runtime patch or
dependency lock, follow the repository's ordinary `make check-upgrade` rules.

## Effective-model validation matrix

Render through the Makefile, never by hand-assembling Compose files. Automated
model tests should cover both lite and full, Docker and Podman, for:

| Egress | Onion | Myst route | Expected structural result |
| --- | --- | --- | --- |
| off | off | off | no Tor layer, profile, service, or network |
| on | off | off | Tor + SOCKS bridge, direct Tor uplink, no frontend gateway |
| off | on | off | Tor + frontend gateway, direct Tor uplink, no egress override |
| on | on | off | one Tor daemon, both fixed gateways, direct Tor uplink |
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
   `make up-full`, then inspect `make ps-*`, Tor, bridge, gateway, nginx, Myst
   sentinel, and final-hop logs.
3. Confirm Tor has the sole `tor-uplink`, no ports are published, applications
   have no Tor networks, and neither fixed gateway can select an arbitrary
   destination.
4. Inspect health. Confirm local control bootstrap reaches complete, the
   frontend gateway traverses nginx, and ordinary checks settle at ten-minute
   cadence. On Podman inspect `.Config.StartupHealthCheck` separately from
   `.Config.Healthcheck`.
5. Make a real `web_search` and a real `open_url` request. If executor network
   is supported/enabled for that engine, make one generated-code HTTPS
   request. Confirm the observed public IP is a Tor exit, target DNS is sent
   through SOCKS, and stopping Tor or the SOCKS bridge causes visible failure
   with no direct/VPN/system fallback.
6. Exercise one configured internal destination (for example local RAG in full
   mode) and confirm its existing exact direct exception still works and was
   not sent to Tor.
7. Run `make tor-onion-address`; validate only the returned v3 hostname. From a
   separate Tor Browser or isolated Tor client, load login, authenticate with a
   test account, make a same-origin API request, and verify a live WebSocket or
   streamed chat path. Confirm CSP and authentication match local access.
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
- onion-only through Myst on at least one engine, proving no unnecessary SOCKS
  bridge/profile.

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
  Tor-over-Myst consequences;
- `.env.wrapper.example`: user-visible options and conflicts only;
- `docs/vpn_routing_and_proxies.md`: Tor route ownership, remote-DNS residual,
  selector semantics, direct versus Myst placement, failure behavior, and
  verification commands;
- `docs/internal_network_security.md`: Tor trust/reachability rows, both fixed
  gateways, absence of application/direct networks, and onion ingress boundary;
- `docs/resource_minimization.md`: optional process/health cadence and absence
  when disabled;
- `docs/podman_suport.md`: bind ownership, overlay, startup-health translation,
  engine-switch persistence, and live checklist;
- `docs/request_handling.md`: only if supported request routing behavior changes;
- `docs/onyx_patches_upgrade.md`: add the Tor image/config contract to the
  component upgrade checklist; and
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
- Target DNS for Tor-routed requests is delegated to Tor; no direct DNS or
  transport fallback succeeds when Tor, its bridge, or its selected exit is
  unavailable.
- Country selection and fingerprint pinning validate strictly and live
  evidence proves the selected constraint; invalid/unavailable pins fail
  closed.
- Onion ingress publishes no host port, reaches Onyx only through the fixed
  hardened gateway, supports login/API/WebSocket behavior, and preserves CSP
  and Onyx authentication.
- `make tor-onion-address` works identically for Docker/Podman and lite/full,
  validates v3 output, and never exposes keys or unrelated state.
- Onion identity survives normal down/up, warm restart, and an explicitly
  validated engine switch; private key permissions remain restrictive.
- Tor config/state use `docker-data/tor` bind storage, or the documented
  single-file config-bind exception if the image requires it. No anonymous
  volume silently owns identity state.
- Image health is overridden with a local authenticated bootstrap check; no
  periodic health check performs Internet/DNS traffic or logs sensitive Tor
  state.
- Podman receives exact native startup-health translation and needs no Docker
  fallback, socket, privileged mode, recursive ownership mutation, or separate
  onion identity.
- Tor-over-Myst passes on both engines: aliases and mounts work in the shared
  namespace; initial Myst unavailability and later Myst loss have no direct
  fallback; qualified recovery restores Tor without a permanent wedge or
  identity change; and the design uses one unprivileged, socket-free Tor
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
