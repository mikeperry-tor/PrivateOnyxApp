# Podman support

## Native Tor

Podman uses the same pinned image, config bind, and host state bind as Docker,
so an engine switch preserves the onion identity. Only Tor gets
`userns_mode: keep-id:uid=101,gid=102`; do not recursively rewrite its state or
move it to an engine-specific directory.

Native Docker runs Tor as the invoking host UID/GID, matching the owner of the
shared mode-0700 state bind. Its transient SOCKS socket uses the separate
host-owned `docker-data/tor/docker-runtime` bind on Linux. Podman and Docker
Desktop use engine-local named volumes, so switching engines requires neither
root ownership nor a privileged state rewrite.

Rootless Docker maps its container root to the invoking Linux user, so Tor runs
as `0:0` inside that namespace while retaining host-user ownership of the same
state and transient-runtime binds. The runtime initializer also uses `0:0` and
does not need a subordinate-ID assumption or persistent-state ownership rewrite.

Docker Desktop reports the shared state bind root as UID/GID `0:0`, so its
Docker Tor identity is `0:0`; native Docker uses the invoking host UID/GID.
Docker Desktop also uses the named runtime volume because its host-bind
transport cannot perform every Unix-socket operation Tor requires. Make
exports the matching identity and runtime source to the same platform-neutral
Docker overlays. This is Docker platform translation, not state created
incorrectly by Podman.
Docker initializes only that transient runtime mount with a networkless,
capability-limited one-shot container before Tor starts; persistent state is
never chowned by this path.
Rootless Podman continues to map the invoking machine user to Tor's `101:102`
and does not inherit the Docker process override.

The Podman SOCKS socket uses an engine-local named volume. Tor mounts it read-write
and the two policy containers mount it read-only; those containers need no
matching UID, supplemental group, shared user namespace, privilege, or engine
socket. The Podman overlay translates the private control tmpfs to native
`U,mode=0700` ownership, constrains `ping_group_range` to the mapped Tor group
102 for rootless crun, and translates the onion gateway tmpfs to the existing `:U` form.
Startup health uses `podman/startup_health.py`; Docker is never a fallback.

Qualification covers all four role models, `make tor-onion-address`,
cookie-authenticated control health, outbound Tor, simultaneous
localhost/Tailscale/onion sessions, restart and down/up identity persistence,
and an engine switch. Both engines must be down before adopting shared Onyx
data ownership; Tor identity state remains the common host bind.

This document is the compatibility and validation authority for running this
wrapper with rootless Podman on macOS or native Linux and with rootless Docker
on native Linux. Read it before adding a feature that
changes Compose layering, mounts, container lifecycle, health checks, image
preparation, network interfaces, or Docker-socket use.
The fresh-state, directed engine-handoff matrix is defined separately in
[`permissions_cross_validation.md`](permissions_cross_validation.md).

Podman is a separate runtime backed by a Linux virtual machine. It is not a
drop-in executable alias for Docker Desktop. The common Compose model remains
the source of application and network behavior, while narrowly scoped Podman
overlays and `podman/startup_health.py` handle engine differences. Do not weaken
privacy routing or add a Docker fallback to make a Podman incompatibility less
visible.

## Selection and version authority

Select Podman explicitly through `.env.wrapper` or the command environment:

```text
CONTAINER_BIN=podman
```

Use Makefile targets rather than assembling a Compose invocation manually.
The Makefile detects a Podman binary by its basename, exports
`CONTAINER_BIN`, and appends the Podman overlays to the effective
`COMPOSE_FILE`.

For rootless Docker on native Linux, leave `CONTAINER_BIN=docker` and select the
local rootless Docker context (or set its local `DOCKER_HOST`). Make classifies
the selected daemon from Docker's security options, resolves its Unix socket
for the code interpreter, and adds:

- `compose_overlays/docker-compose.docker-rootless.yml` in both modes; and
- `compose_overlays/docker-compose.docker-rootless-full.yml` in full mode.

The selected socket must be a live local Unix socket. Remote rootless contexts
are rejected because the code-interpreter container cannot safely mount a
remote engine endpoint as `/var/run/docker.sock`.

On Debian-family systems with Docker Engine already installed, first inspect
an existing rootless context with
`docker --context rootless info --format '{{json .SecurityOptions}}'`. If the
output contains `name=rootless`, select that context and skip installation.
Otherwise, install the RootlessKit prerequisite and create the per-user daemon
with:

```bash
sudo apt-get update
sudo apt-get install rootlesskit
dockerd-rootless-setuptool.sh install
systemctl --user enable --now docker.service
docker context use rootless
docker info --format '{{json .SecurityOptions}}'
```

The security-options output must contain `name=rootless`. The `docker.io`
package may install the setup tool under `/usr/share/docker.io/contrib`; add
that directory to `PATH` when it is not otherwise found. The setup tool can
refuse while a rootful system daemon is active. Use its `--force` option only
when that is the reported reason and coexistence is intentional. The rootful
daemon remains available through `docker context use default`. If the setup
tool does not create its context, create it once with `docker context create
rootless --docker "host=unix:///run/user/$(id -u)/docker.sock"`. The rootless
daemon then uses that per-user socket through the `rootless` context.
`sudo loginctl enable-linger "$USER"` additionally keeps the user service
available across logout and starts it at boot.

Rootless Podman and rootless Docker share two useful wrapper mechanisms: the
common Compose application/network model and one engine-aware resolver for
their per-user Unix API socket. They do not share a storage overlay or a
synthetic common UID. Podman's `keep-id` can map each image UID onto the
invoking user while retaining interoperable host binds. Rootless Docker maps
only container root to that user and maps non-root image UIDs into the host's
subordinate-ID range, so its ownership-sensitive stores must remain named
volumes. Podman also needs native startup-health translation and omits the
Docker-socket code interpreter, while rootless Docker retains Docker's native
health behavior and code-interpreter contract. Keeping those differences in
narrow overlays avoids assumptions about host or subordinate UID numbers.

RootlessKit normally starts slirp4netns with host-loopback access disabled.
Docker's `host-gateway` token is also daemon-global rather than
RootlessKit-aware and can resolve to an inactive rootful `docker0` bridge when
rootful and rootless daemons coexist. For rootless Docker, Make therefore maps
`host.docker.internal` explicitly to RootlessKit's stable `10.0.2.2` host
address. Host embeddings and operator integrations require the daemon to be
started with host loopback enabled, for example by setting
`DOCKERD_ROOTLESS_ROOTLESSKIT_DISABLE_HOST_LOOPBACK=false` in the rootless
Docker service environment and restarting that daemon. This deliberately
changes host reachability for every container on that daemon; keep host
services bound narrowly where possible, apply host firewall policy, and rely
on the stack's final-hop proxy to permit only configured ports and
destinations.

The exact stack-owned Teep embedding URL does not require host-loopback. When
rootless Docker selects `http://host.docker.internal:${HOST_PORT_TEEP}/v1/embeddings`,
the wrapper keeps that operator-facing setting but gives the shim only
`onyx-backend` plus `onyx-teep` and sends its runtime request directly to
`http://teep:8337/v1/embeddings`. If Teep is VPN-routed, its fixed internal
gateway owns the same `teep` alias. No other host URL or integration receives
this exception.

On a systemd installation, a typical opt-in drop-in is:

```ini
# ~/.config/systemd/user/docker.service.d/host-loopback.conf
[Service]
Environment=DOCKERD_ROOTLESS_ROOTLESSKIT_DISABLE_HOST_LOOPBACK=false
```

Run `systemctl --user daemon-reload` and restart `docker.service` only after
reviewing that daemon-wide consequence. Lite mode does not need this option
unless an enabled integration actually targets a host service. Full mode needs
it when its configured embedding endpoint is another host-local service; the
exact stack-owned Teep endpoint uses the internal exception above.

Docker's daemon-wide `userns-remap` mode is different from rootless Docker. It
remaps bind ownership while the daemon and its socket remain rootful, which is
incompatible with the stack's shared host binds and Docker-out-of-Docker
executor contract. The capability gate detects `name=userns` without
`name=rootless` and exits before the shared-data claim, host-directory
preparation, image work, or Compose mutation. There is no automatic
`--userns=host` bypass; use an ordinary or rootless Docker daemon.

`podman compose` is a frontend to an external Compose provider. It may report
`docker-compose` as that provider; the provider still submits operations to
the Podman API. This does not mean the Docker engine is being used. Do not run
`docker` commands as a fallback, and use `podman version` or `podman info` when
the selected engine must be proved.

All external image references owned by this wrapper include an explicit
`docker.io/` registry. Do not rely on Docker's implicit Docker Hub default:
Podman installations may intentionally have no unqualified-search registries,
and changing that host-wide policy is not a stack prerequisite.

Native rootless Podman resolves `host.docker.internal` to its fixed
`169.254.1.2` link-local engine gateway. The Podman overlay supplies that exact
address only to the host-capable final-hop policy. The exception remains
restricted to the exact logical host name and the configured embedding or
operator-selected port; all other link-local addresses remain blocked.

Docker Compose 5.3.1 may otherwise honor the host's selected Docker Desktop
context or Podman's SSH system connection. The Makefile therefore exports an
exact `DOCKER_HOST=unix://...` value from the selected Podman engine whenever
Podman is selected. It uses the machine's inspected forwarded socket on macOS
and the rootless remote socket reported by `podman info` on native Linux.
Preserve this pin: an SSH path can inherit host proxy/SOCKS behavior and fail
with `nc: connection failed`, while the Unix socket also proves that the
provider targets Podman's API rather than Docker Desktop.

The client and Linux Podman server can have different versions. The wrapper
uses these version and capability rules:

- a Linux Podman server, with 5.4.2 as the currently validated baseline;
- a Compose provider that preserves `gw_priority`, `!override`, and
  `start_interval` in a rendered probe model; and
- the native startup-health flags checked by `podman update --help`.

Podman Machine creates separate rootless and rootful system connections for
the same VM. Either connection may be selected explicitly, but rootless is the
qualification default because it exercises the ownership and user-namespace
constraints this wrapper must preserve. A rootful run is not a substitute for
the documented rootless compatibility matrix, and the capability gate does
not reject it merely for being rootful.

An older Podman server prints a warning but is not rejected based on its version
alone. It proceeds through the same image-store, Compose-provider, and native
startup-health capability probes, and any missing required capability still
fails before stack mutation.

The Compose probe is a hard interface check rather than a version gate.
`gw_priority` is routing-critical and was introduced in Docker Compose 2.33.1;
older providers can accept the file while silently omitting it. Repository
model tests also require Docker Compose 2.35.0+, where
`config --no-env-resolution` became available, so tests never load service
environment files. On native Linux, the Makefile obtains the rootless API
socket from `podman info`; on macOS it first uses the forwarded socket reported
by `podman machine inspect`.

On native systemd Linux, the external provider requires the rootless Podman API
socket:

```bash
systemctl --user enable --now podman.socket
```

For containers started from a remote or otherwise transient login to remain
running after the final session closes, an administrator must also enable
lingering for that rootless user, for example
`sudo loginctl enable-linger <user>`. Without lingering, logind can terminate
the detached rootless container processes at logout; this is host lifecycle
configuration rather than a container OOM or stack health failure.

The startup capability target verifies that `DOCKER_SOCK_PATH` names an active
Unix socket before claiming shared data or creating containers. It does not
enable a persistent host service automatically. On macOS, the equivalent
socket is owned by the selected running Podman machine.

The currently verified guest baseline is the immutable official Podman
machine-os v5.8.1 release image. On Apple silicon with libkrun, initialize it
from:

```text
https://github.com/podman-container-tools/podman-machine-os/releases/download/v5.8.1/podman-machine.aarch64.applehv.raw.zst
```

Despite `applehv` in the artifact name, this is the published ARM64 raw disk
format accepted by libkrun. Do not replace it with the mutable
`quay.io/podman/machine-os:5.8` tag without first validating a new machine. In
July 2026 that tag produced a Fedora CoreOS 44 image whose first-boot Ignition
failed while creating the macOS uid 501 user (`useradd: cannot lock
/etc/group`) and entered emergency mode under both libkrun and AppleHV.

The immutable image retains package-owned container configuration under
`/usr/etc`. If `podman build` or `podman pull` reports that no `policy.json`
exists, first verify that `/usr/etc/containers/policy.json` is present and is
owned by `containers-common`, then restore that exact vendor file to
`/etc/containers/policy.json`. Do not invent a different trust policy or copy
one from another host. Repeat the runtime identity gates after this host
correction.

On this machine, official 5.8.2 and 5.8.5 machine images also developed an
unusable overlay image store after restart, reporting
`readlink ... overlay: invalid argument`. This is a local observed regression,
not a claim about every Podman installation. The capability gate deliberately
runs `podman images` so a broken post-restart store fails before stack changes.
If that probe fails, report the machine as unusable. Never recreate a machine
that may contain user data without explicit approval.

Starting Podman Desktop or approving macOS access to a new volume does not
necessarily update an existing process or VM. Restart Podman Desktop after a
new approval when access still fails, then rerun the capability, image-store,
mount, and startup-health checks. Do not assume a pre-restart result is
still valid.

## Compose overlay model

The Makefile assembles the ordinary base/mode/optional layers first and adds
the engine-specific layers when `CONTAINER_BIN` resolves to Podman:

- `compose_overlays/docker-compose.podman.yml` applies to lite and full modes;
- `compose_overlays/docker-compose.podman-full.yml` applies only to full mode;
  and
- `compose_overlays/docker-compose.podman-macos-full.yml` applies only to
  full mode on macOS.

Keep Docker and Podman behavior separated in those override files. Preserve
Compose `${VAR:?message}` checks and the Makefile-generated ephemeral secret
flow. When using `!override`, remember that the entire inherited sequence is
replaced.

### Common Podman override

`compose_overlays/docker-compose.podman.yml` owns these differences:

- `code-interpreter` is placed behind an inactive
  `requires-docker-socket` profile. The legacy executor requires Docker socket
  behavior that the rootless macOS Podman VM does not reliably provide.
- The Makefile also omits
  `compose_overlays/docker-compose.code-interpreter-network.yml` under
  Podman even if `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true` remains in a
  shared environment. This prevents an unused executor bridge, network, and
  health loop from running when the executor itself is unavailable.
- The optional Tailscale frontend gateway replaces Docker tmpfs `uid=`/`gid=`
  options with Podman's `U` option. This use is limited to newly created tmpfs
  mounts, not host data. The service remains non-root and retains its other
  hardening.
- PostgreSQL uses the same `docker-data/postgres` bind as Docker, with the
  required `keep-id` mapping and direct entrypoint. A missing or empty bind is
  initialized once through the pinned image in a uniquely named disposable
  Podman volume, copied into the empty bind, and removed before Compose creates
  the application graph. Native Linux Docker runs PostgreSQL as the invoking
  host uid/gid, including stock-entrypoint initialization of a fresh bind. This
  prevents Docker from changing the mode-0700 tree to image uid 70 before a
  switch to rootless Podman.
- API startup waits for PostgreSQL to become healthy. Podman creates more of
  the graph concurrently and otherwise can expose database initialization
  races earlier than the Docker path.

### Full-mode override

`compose_overlays/docker-compose.podman-full.yml` additionally:

- makes `background` wait for PostgreSQL health;
- always uses the same initialized `docker-data/opensearch` bind as Docker
  Desktop with the required `keep-id` mapping and a group-1000-only
  `ping_group_range` that rootless crun can apply inside that mapping; a missing
  or empty bind is initialized once through the pinned image in a uniquely
  named disposable Podman volume, copied into the empty bind, and removed
  before Compose creates the application graph.

On native Linux, full mode retains the ordinary read-only document-source bind
and containerized Python server from `docker-compose.full.yml`.

### macOS full-mode override

`compose_overlays/docker-compose.podman-macos-full.yml`:

- replaces container-side document serving with a hardened fixed relay to the
  wrapper-managed host document server. The default `doc-drop` source is
  created when absent; a custom configured source must already be a directory.

### Shared Docker data

The core engine overlays let Docker and Podman use the same stopped PostgreSQL
cluster, OpenSearch index, and full-mode MinIO object store in place. This is
unconditional: there are no marker-selected storage branches or opt-out flags.
On native Linux, the Docker-only overlay keeps PostgreSQL and MinIO owned by
the invoking host user so their persistent files remain writable after a
switch to rootless Podman. The stock PostgreSQL entrypoint initializes a fresh
bind as that user and cannot perform its root-only ownership change. Podman's
preflight initializes a genuinely missing or empty PostgreSQL bind and refuses
a nonempty bind without `PG_VERSION` as partial or unknown state. The same rule
applies to OpenSearch: a genuinely missing or empty bind is initialized, while
a nonempty bind without `nodes` is never overwritten.

Myst must run as container root under native rootful Docker so it can configure
its tunnel namespace and firewall. After the Myst writer stops, each tracked
`make down-*` and `make vpn-signup-stop` path runs a networkless, read-only-root
helper from the already-selected Myst image with only `CAP_CHOWN`. The helper
returns the persistent `docker-data/myst-data` tree to the invoking host UID/GID
and verifies the result before releasing the shared-data claim. Rootless
Docker and Podman already map container root to the invoking user, while Docker
Desktop translates the bind through its VM; those paths do not run the helper.
This narrow shutdown operation is part of the engine handoff contract, not an
operator repair command.

Full-mode startup also uses the shared `docker-data/model-cache` bind for the
small wrapper-owned nomic tokenizer file. The Makefile extracts that file from
the selected pinned Onyx image with networking disabled and writes it
atomically as the invoking host user before either engine creates the
application containers. This avoids Docker named-volume copy-up semantics,
works the same under rootless Podman, and never overwrites unrelated cache
entries. A missing tokenizer in the pinned image is a startup failure rather
than permission to fetch it at runtime.

The ownership guard inspects both native engines without the Podman
`DOCKER_HOST` compatibility override used by the external Compose provider, so
repeated Podman claims cannot misclassify Podman writers as Docker writers.
Both engines must be down before switching because shared storage does not make
concurrent database or index writers safe.

Before either `make up-*` or an optional Myst signup recipe reaches Compose, an
atomic marker under `docker-data/host-services` claims the shared
database/index/wallet data
for Docker or Podman. Repeated starts by the same engine are allowed; the other engine fails
closed until the owner's matching `make down-*` completes successfully. Inspect
the marker with `make shared-data-engine-status`. On the first claim, the guard
queries every installed Docker/Podman command (including `CONTAINER_BIN`) for
running Onyx PostgreSQL/OpenSearch writers and any integrated or standalone
`myst-client-vpn`. Same-engine claims repeat this inspection so an out-of-band
other-engine Myst daemon cannot be hidden by an existing marker. A writer owned by the other
engine, or an installed engine that cannot be inspected, stops startup before
the marker is created. A Docker client or compatibility shim is not treated as
an installed Docker engine when its selected endpoint is one absolute local
Unix socket and that socket does not exist. The other narrow exception is an
unselected Podman command whose default machine positively reports the
`stopped` state; its expected API connection failure cannot conceal a running
Podman writer and does not block a Docker start. Other failed, remote, malformed,
or existing-socket endpoints remain ambiguous and fail closed. This closes the
upgrade window in which a running pre-marker Docker stack could otherwise be
claimed by Podman without requiring an unused client or Podman VM to have a
running engine.

A failed start deliberately retains its claim for a safe retry. If both engines
are verifiably down but an ambiguous unavailable engine prevents first-use
inspection,
`make adopt-shared-data-engine` is the explicit operator override;
it creates an absent marker or atomically replaces a stale marker for the
selected `CONTAINER_BIN` without contacting either engine. This makes recovery
possible when the former Podman machine or socket is no longer running. Before
using it, verify every reachable engine has no running Onyx PostgreSQL,
OpenSearch, or Myst container and independently establish that an unreachable
former engine is stopped. Do not use it to bypass a reported writer. After a
crash leaves a stale Podman claim and Docker is selected, use
`make adopt-shared-data-engine CONTAINER_BIN=docker`, confirm
`make shared-data-engine-status` reports the selected Docker flavor, and then
run the matching `make up-*` target.

The two stack-start targets are `.NOTPARALLEL` Make targets. Their ownership
claim, shared-data preparation, host-side services, and Compose launch therefore
cannot become peer jobs under `make -j`; a rejected claim stops the serial
prerequisite chain before any shared-data or host-process mutation.

When Myst VPN is explicitly enabled, standalone signup uses the fixed
`private-onyx-myst-signup` Compose project,
`MYST_SETUP_ONLY=true`, and `restart: "no"` on both engines. The preflight
rejects an integrated or wrong-mode same-name container rather than executing
financial commands in it. `make vpn-signup-stop` releases the selected-engine
claim after stopping that exact project; `make up-*` instead adopts the same
claim, stops the standalone project, and starts integrated Myst with the
preserved wallet bind.

PostgreSQL requires four linked settings:

- `userns_mode: keep-id:uid=70,gid=70` maps the Podman machine user to the
  Alpine image's PostgreSQL account;
- `net.ipv4.ping_group_range=70 70` keeps rootless crun's namespace sysctl
  inside that mapped group, without granting another group raw ICMP;
- `user: "70:70"` starts the database with that account; and
- `entrypoint: ["postgres"]` bypasses the stock image entrypoint only for the
  already-initialized shared cluster.

The external Compose provider can submit independent `keep-id` container
creates concurrently through Podman's compatibility API. On macOS, that can
leave one container with only its requested one-ID mapping and make its
`devpts` mount fail. Podman startup therefore creates PostgreSQL, full-mode
OpenSearch, and optional Tor containers serially before creating the remaining
graph. Keep these targeted `up --no-start --no-deps` operations ordered
whenever another Podman-only service needs a distinct `keep-id` mapping.

The entrypoint bypass is essential. Its unconditional `chmod 0700` on
`PGDATA` makes macOS virtiofs create a root-owned
`user.containers.override_stat` on the mount root. Subsequent lookup then
fails even though Docker's `com.docker.grpcfuse.ownership` metadata correctly
records uid/gid 70. `prepare-podman-postgres-data` validates `PG_VERSION` and
removes only that mount-root Podman override before Compose create. It leaves
Docker's attribute and valid per-file Podman runtime attributes intact.

On native Linux, Docker's ordinary root entrypoint instead changes the bind to
host uid 70, which a rootless user namespace cannot represent or traverse.
`docker-compose.docker-linux.yml` therefore runs both the stock entrypoint and
the server as the invoking host uid/gid. The full-mode-only
`docker-compose.docker-linux-full.yml` does the same for MinIO so it does not
leave root-owned object-store files that rootless Podman cannot rename. Before
either engine creates a container, the common startup preflight creates every
selected mode's host bind root as the invoking user; native Docker must not
create a missing PostgreSQL source as root. The entrypoint can then initialize
the fresh host-owned bind but cannot perform its root-only ownership change, so
Docker does not undo Podman's compatible ownership. These overlays are never
selected on macOS, where Docker Desktop ownership metadata and the existing
Podman xattr handling remain unchanged.

Rootless Docker cannot reproduce Podman's `keep-id:uid=70,gid=70` mapping:
container root maps to the invoking host user, while PostgreSQL must run as a
nonzero in-container UID. Assigning image UID 70 or OpenSearch UID 1000 to a
host bind would make the files belong to host subordinate IDs and would break
the ordinary-Docker/rootless-Podman interchange contract. The rootless Docker
overlays therefore use engine-managed named volumes for PostgreSQL and SearXNG
cache, plus full-mode OpenSearch and MinIO. Image entrypoints retain their
native ownership setup inside those volumes. No host UID, subordinate UID/GID
range, or daemon-assigned volume UID is assumed.

These four stores belong to the selected rootless Docker daemon and are not the
same data as `docker-data/postgres`, `docker-data/opensearch`, or
`docker-data/minio`. `make down-*` preserves them, as it preserves all named
volumes. Other host binds—including Myst identity, Tor state, Onyx file-system
data, model cache, and document source—remain host-user-compatible because
their writers run as container root or are read-only. Always stop the active
stack before changing Docker contexts or container engines; do not treat the
rootless volume isolation as permission for concurrent stacks.
The shared-data marker distinguishes `docker-rootful`, `docker-rootless`, and
`podman`; switching between either Docker daemon therefore requires the active
flavor's matching `make down-*`, just like a Docker/Podman switch. A legacy
`docker` marker is upgraded to the selected Docker flavor on its next claim.

OpenSearch uses `keep-id:uid=1000,gid=1000` and its ordinary image entrypoint.
The Podman-only network namespace sets `net.ipv4.ping_group_range=1000 1000`:
rootless crun applies this sysctl at container creation, and every group in the
range must exist in the `keep-id` mapping. The setting grants raw ICMP only to
the already mapped OpenSearch group and avoids adding an unmapped host group.
The tracked read-only `audit.yml` bind and all resource/Query Insights settings
are applied directly to that service; there is no administrative sidecar or
successful-exit dependency to translate under Podman.
The tested image can create and reuse its normal per-file Podman attributes;
do not recursively delete them. `prepare-podman-opensearch-data` requires its
initialized `nodes` directory before a full-mode create, initializing a
genuinely empty bind through the pinned service image when necessary.

On the verified rootless 5.8.1 server, Podman clamps the inherited unlimited
OpenSearch memlock request to an 8 MiB soft/hard limit. OpenSearch consequently
logs that JVM memory is not locked. The guest reports no configured swap, and
both clean and shared-volume 2,000-document/768-dimensional KNN, hybrid,
concurrent indexing, reindex, and restart validation passed without breaker
trips, queue rejections, or OOM evidence. Treat this as an explicit rootless VM
capability difference. Do not broaden VM privileges or disable the common
memory-lock request merely to silence the warning; re-evaluate the limit,
guest swap state, mapped-index residency, and failure behavior when changing
the Podman machine or OpenSearch resource policy.

Rootless Docker's OCI runtime cannot raise the inherited unlimited memlock
rlimit and otherwise refuses to create the OpenSearch container. Its full-mode
overlay sets the same 8 MiB soft/hard limit explicitly. OpenSearch emits the
same memory-not-locked warning and continues; this is a rootless runtime
constraint, not permission to reduce the JVM heap or other memory safeguards.

### VPN recovery and socket limitations

Myst uses the same in-container health supervisor under Podman and Docker. It
has no engine socket, sidecar, added capability, or Podman-specific overlay.
Only a VPN that became ready once in the current container lifetime is armed;
60 seconds of continuous later readiness failure requests graceful PID-1
termination, after which `restart: unless-stopped` restarts Myst in the stable
holder namespace. Initial and explicit no-VPN failures remain visible without
self-restart. The Docker-socket limitation therefore applies only to the
unrelated standalone code-interpreter service used by ordinary chat.
In no-VPN mode on either engine, `netns-holder` still owns the namespace and
the Myst container runs only its inert readiness sentinel; the daemon and
route-reconciliation loop are absent.

## Startup-health translation

The Compose provider accepts and renders `healthcheck.start_interval`, but the
tested Podman compatibility API drops it when creating containers. A rendered
Compose model is therefore necessary but not sufficient evidence.

`podman/startup_health.py` provides strict startup-health and storage actions:

- `check` validates that the selected binary is Podman, inspects the Linux
  server version, warns below the validated baseline, probes the image store,
  checks the Compose provider, and verifies the required native `podman update`
  flags.
- `configure` renders the Makefile-selected effective Compose model, discovers
  all created containers by Compose project label, requires the complete
  retained-health set to match, validates each command, timeout, retry count,
  start period, and regular cadence, installs an exact native startup check on
  stopped containers, and reinspects the complete project.
- `initialize-postgres` initializes a missing or empty PostgreSQL bind through
  the pinned service image and a disposable engine volume, or validates an
  existing cluster, then performs the narrow mount-root xattr cleanup described
  above. It removes the staging volume on every outcome and refuses nonempty
  bind data without `PG_VERSION`. The narrow mount-root xattr cleanup runs only
  on macOS; native Linux has no Docker Desktop ownership xattr to remove.
- `initialize-opensearch` initializes a missing or empty OpenSearch bind through
  the pinned service image and a disposable engine volume, waits for the
  temporary node to become ready, explicitly loads and verifies the tracked
  audit policy, stops it cleanly, copies the initialized files into the shared
  bind, and removes its temporary container and volume on every outcome. It
  refuses nonempty bind data without `nodes`.
- `prepare-shared-data` validates an initialized database/index path and is
  retained as the common post-initialization check and for focused diagnostics.

For each retained health check, `configure` converts the regular command to an
equivalent shell-quoted command and installs it with the same timeout in
Podman's `StartupHealthCheck`. This is required because Podman 5.4's
`podman update --health-startup-cmd` accepts one shell command rather than a
Docker-style JSON exec array. The helper reinspects the exact transformed
command, sets its interval to five seconds and success threshold to one, and
disables startup-health-driven restart. It separately requires a ten-minute
ordinary interval, except Myst's one-minute interval. A running container
without the exact native startup configuration is rejected instead of being
modified in place.

The authoritative inspect fields are separate:

```text
.Config.StartupHealthCheck
.Config.Healthcheck
```

The Podman Makefile lifecycle is consequently create/configure/start:

1. `check-container-health-capability` runs the Podman capability gate once,
   before shared-data or host-process mutation. Docker uses the same target for
   its separate native engine/Compose version check, rootless socket check, and
   daemon-wide `userns-remap` rejection.
2. The mode-appropriate database preflights initialize genuinely empty
   PostgreSQL and full-mode OpenSearch binds, validate existing shared binds,
   and remove only the unsafe PostgreSQL mount-root override when present. Full
   On macOS, full mode also starts or validates the PID-tracked host document
   server.
3. `podman compose create` creates stopped containers.
4. `startup_health.py configure` installs and verifies native startup checks.
5. `podman compose up -d --wait --wait-timeout 420` starts the verified graph.
6. The wrapper inspects the host bridge after the provider returns and requires
   it to be running and healthy. Its probe requires a policy-generated 403, so
   this closes a verified provider gap where the external Compose provider can
   return zero after its wait expires while that native-startup-health
   container remains `starting`.

Podman's native startup check intentionally uses zero startup retries so it
does not restart a slow or broken service. Podman can consequently leave such
a container in `starting` indefinitely. The explicit Compose wait timeout is
the fail-closed outer bound for each container-health phase: that phase returns
nonzero within 420 seconds rather than blocking forever, while preserving the
failed container and its health history for diagnosis.

Full mode performs that sequence first for `local-embedding-shim`, makes the
single `/ready` request, then repeats create/configure/start for the complete
graph. Both configuration passes reuse that prerequisite result rather than
probing the engine and image store again. Do not collapse this into a single
`compose up`: that would start
containers before the native health contract is installed.
The intervening inference-backed `/ready` is different from a container-health
phase: it prints the MLX log path and waits without a short wrapper deadline
while a live bundled child loads. This preserves a visible startup contract for
machines with slow MLX load times; Ctrl-C is the operator escape. Once ready,
an individual proxy-to-child request has the same five-minute blocked-socket
timeout as normal embedding traffic.

The host MLX environment preparation is engine-independent. Before either
Docker or Podman full-mode startup launches an existing bundled installation,
the Makefile checks its dependency/runtime fingerprint and atomically refreshes
a stale environment. It performs no such mutation for a custom endpoint or an
installation that has never been set up. A refresh failure restores the old
environment and aborts startup before Compose begins.

## Image preparation

The Podman `onyx-build` branch pulls the exact Makefile-selected Onyx images
directly. Do not invoke the upstream Onyx bootstrap installer on this path. On
a clean Podman VM, its noninteractive default selected the wrong stack flavor,
started a socket-dependent code-interpreter container, and left bootstrap
containers with incompatible health configuration. The Docker installer flow
remains separate and unchanged.

Local component images still use their documented Makefile image targets.
Never substitute an unpinned image because the Podman store is empty, and do
not let validation containers pull or use network access implicitly.
The derived Python executor and its `executor-image-ready` prerequisite remain
Docker-only because rootless Podman omits the socket-dependent code-interpreter
service entirely; Podman startup and image validation do not build or store it.

Root-context Podman builds use `.containerignore`; Docker uses the matching
`.dockerignore`. They exclude private bind data, the document source, generated
Onyx deployment state, local model/cache trees, logs, and reference checkouts.
Keep the two files identical. Preserve those exclusions: without them, a
container client can archive large private host trees into a local or remote
build context before the Dockerfile runs, causing long silent starts and
unnecessary data exposure. Components that do not need the shared root context,
such as the Tor wrapper, use their own narrow component directory instead.

## macOS mounts, ownership, and attributes

Podman machine host shares use virtiofs. Linux container ownership expectations
do not map directly to macOS bind mounts:

- mode-0700 PostgreSQL data becomes inaccessible when the stock image
  entrypoint creates a root-owned Podman override on the mount root;
- PostgreSQL is shareable with the guarded direct-entrypoint overlay above,
  while OpenSearch is shareable with uid/gid 1000 `keep-id`; and
- some externally mounted sources may appear empty or inaccessible inside the
  VM even when the exact directory is configured and macOS access is approved;
  WebDAV mounts are one supported example.

Do not broaden a share to `/`, all of `/Volumes`, or a symlinked parent as a
workaround. Aside from unnecessary exposure, a macOS-visible symlink does not
make an independently mounted user filesystem re-exportable by virtiofs.

The common suffixes solve different Linux problems:

- `:z`/`:Z` change SELinux sharing labels. They do not make an unshared macOS
  path visible, alter uid/gid ownership, or remove macOS attributes.
- `:U` recursively changes ownership of a source. On host binds this can mutate
  private data and was rejected by the tested virtiofs paths. Its only current
  use is on new disposable tmpfs mounts.

Do not apply manual recursive `chmod`, `chown`, `chattr`, `xattr`, `:U`, or
broader VM sharing to private database or RAG sources. The tracked preflight's
single mount-root xattr removal is the supported exception. Database state is
not duplicated into Podman-native volumes.

## Host document server

For every macOS Podman full-mode source, including the default `./doc-drop`,
the wrapper serves `ONYX_RAG_DOC_SOURCE_DIR` without mounting or copying it
into the VM. This uniform path also supports externally mounted sources,
including WebDAV mounts, that are fully readable on macOS yet absent at the
Podman VM's `statfs` boundary. Ownership mappings, SELinux suffixes, and xattr
cleanup cannot repair a path that virtiofs did not expose. Native Linux
instead uses the ordinary read-only bind-mounted container server: its bridge
connections do not arrive at a host process as loopback peers, and it does not
need the macOS re-export workaround.
Native Linux full-mode startup also runs the identity-checked host-server stop
target so an older wrapper version cannot leave the obsolete macOS-style
process listening after the containerized server takes ownership.

`podman-doc-server-start` validates the source, rejects an unrelated listener
on fixed host port 18091, and starts
`onyx/doc_drop_webserver.py` with a private log and an ownership record under
`docker-data/host-services`. The record contains the PID, a random per-launch
ownership token, and a fingerprint of every launch-defining argument. A
tracked process is reused only when the token is present in its live command,
the configuration fingerprint matches, and `/_health` succeeds. Configuration
changes restart only the token-matched process. Malformed, stale, or reused PID
records never authorize signaling an unrelated process.

The same stdlib-only `embedserv/host_process_manager.py` used for the optional
MLX proxy owns the common detached-launch, atomic record, readiness, and stop
mechanics. Its location reflects the primary MLX use, but this document-server
use is deliberately supported and tested. Only macOS Podman full mode selects
this host document target; native Linux Podman and Docker use the containerized
document server instead.
The document server still owns its document-root confinement, loopback-peer
restriction, HTTP handling, and connection bounds.

The macOS Podman `doc-drop-web` service is a non-root, read-only, capability-free
`socat` relay. It keeps the existing internal route and display networks but
has no document mounts. Its only additional network is a dedicated
non-internal host uplink, and its fixed command connects only
`host.containers.internal:18091`. Podman's userspace gateway reaches the
macOS listener as a loopback peer; the server rejects non-loopback peers before
HTTP parsing or thread creation, caps active connection threads, and gives
accepted sockets a 30-second idle timeout. The wildcard bind required by the
gateway is therefore not an unauthenticated LAN document endpoint: direct
non-loopback sockets are closed without parsing enough HTTP to return a
response. This
restriction is enabled only by the host process's
`--loopback-peers-only` flag; Docker's internal document server does not use
it. Application containers do not join that uplink. The existing
`doc-drop-route-gateway` and exact host final-hop policy remain authoritative
for Web Connector access.

The host server resolves its configured document root once, rejects every
request that escapes it, and neither lists nor follows symbolic links. Point
`ONYX_RAG_DOC_SOURCE_DIR` at the real collection directory instead of using a
symlink. This keeps a symlink placed in the document tree from exposing other
host files through the relay.

The request-time check is not an OS sandbox against another trusted local
process deliberately replacing path components in the narrow interval before
the standard-library handler opens a file. Treat the configured document tree
as trusted local input. A future stronger boundary would require descriptor-
relative traversal and serving on every supported macOS filesystem; that work
is deferred and should retain the current listing and Web Connector behavior.

`make down-full` first removes the Compose graph and then calls
`podman-doc-server-stop-if-started`. The stop path validates command identity,
sends SIGTERM only to the recorded wrapper-owned server, waits for bounded
exit, and removes the PID record. Docker and native Linux Podman full mode
retain the ordinary read-only bind-mounted `doc-drop-web` container and never
start the host process. Request logging is suppressed because URLs can contain
private file names; the server log contains lifecycle diagnostics only.

## Networking differences

Do not assume Podman assigns Docker-style interface names or a fixed field
order in `ip route get` output. Myst readiness must identify the selected
`via`, `dev`, and `src` values by token and compare them with the actual
interface/address state. Preserve the tested exact host-route and final-hop
policy behavior; a Podman workaround must not give applications direct egress
or join them to the trusted Myst namespace.

`host.docker.internal` remains the configured logical host alias only for
operator-selected ports plus the exact full-mode configured embedding
authority and configured upstream-proxy authority. Validate the actual resolved
address and route inside the relevant container rather than assuming Docker
Desktop's address or interface. Compatibility checks cover configured-authority
allow, ordinary deny, and fresh/warm invalid-policy starts. The warm case
recreates the policy/bridge pair and must fail bounded startup rather than reuse
stale healthy state. The post-wait assertion makes that failure authoritative
even if the external Compose provider reports success after leaving the bridge
in `starting`.

## Deterministic coverage

The primary Podman-specific tests are:

- `tests/test_podman_startup_health.py`: binary refusal, capability and image
  store gates, exact effective-model health-set and field contracts,
  stopped-container update, running-container fail-closed behavior, and
  shared-data preflight.
- `tests/test_shared_data_engine.py`: same-engine claim reuse, cross-engine
  exclusion, explicit stale-claim adoption, matching release, and corrupt-marker
  failure.
- `tests/test_myst_lifecycle_makefile.py`: capability placement,
  serialized prerequisites, bounded create/configure/start ordering, host
  document-server lifecycle, direct Onyx image pulls, and exclusion of the
  upstream installer/socket-only executor and its unused network overlay.
- `tests/test_host_process_manager.py`: atomic ownership records, malformed
  record refusal, explicit operator-listener reuse, readiness enforcement, and
  reused-PID non-signaling.
- `tests/test_onyx_network_isolation.py`: effective OS-specific Podman overlay
  selection, optional VPN behavior, tmpfs options, unconditional shared Docker
  PostgreSQL/OpenSearch storage, database health dependencies, native-Linux
  document binds, and the fixed hardened macOS host document relay.
- `tests/validate_tor_image.py`: the selected Tor image, one fresh engine-local
  SOCKS volume, keep-id/tmpfs modes, explicit state bind, authenticated private
  control path, and unprivileged read-only policy-container socket access.
- `tests/validate_obscura_image.py`: the selected Obscura image on an
  engine-local internal fixture network, including isolated connection state,
  the live-connection cap, ten-way navigation concurrency, retained-body
  behavior, full stealth-feature startup, and hardened runtime settings. The
  same `make obscura-build CONTAINER_BIN=podman` workflow derives this image
  from the exact digest-verified source revision and strict patch series
  without Docker-specific mounts or sockets.
- `tests/test_doc_drop_webserver.py`: the non-indexed host readiness endpoint
  and document-root/symlink confinement.
- `tests/test_myst_readiness.py`: interface/route parsing that must remain
  valid with Podman's network layout.

Run `make check` after every Podman script, Makefile, or overlay change. For
image/source pin or runtime-patch work, add only the focused image target for
the affected family: `make test-patch-images`, `make test-obscura-image`,
`make test-tor-image`, or `make test-opensearch-image`. Use
`make test-all-images` (or
`make check-upgrade`, which also runs `make check`) only for changes spanning
multiple image families or broad release validation. Under Podman,
`make test-patch-images`, `make test-all-images`, and `make check-upgrade` skip
the unsupported socket-dependent code-interpreter image contract; Docker
continues to require and validate that image. The pinned Onyx and derived
SearXNG image contracts remain mandatory on both engines.
`make health-inventory` renders the actual
Makefile-selected engine, private environment, optional overlays, and active
profiles for lite and full mode and totals their steady checks per hour. It
does not replace native live inspection.

## Live compatibility checklist

When a new feature affects Podman, validate at least the affected entries
below. Use `CONTAINER_BIN=podman` on every Make invocation and use `podman` for
direct inspection; do not mix Docker engine results into the evidence.

1. Confirm client/server/provider versions and that `podman images` succeeds,
   including after a Podman Desktop restart when practical.
2. Render every affected lite/full/profile/optional-overlay model through the
   Makefile's layering. Check for lost mounts caused by `!override`, accidental
   socket services, Docker-only tmpfs syntax, and unintended networks.
3. Start from stopped/downed matching-stack state. Run `make up-lite` and/or
   `make up-full`, then inspect `make ps-*` and targeted logs.
4. Inspect `.Config.StartupHealthCheck` and `.Config.Healthcheck` separately.
   Observe fast startup probes followed by a ten-minute ordinary interval or
   Myst's one-minute interval; Compose rendering alone is not a pass.
5. For macOS full mode, confirm the host document server PID identity and
   readiness, the absence of document mounts/volumes on the relay, internal and
   published document access, immediate source visibility, and identity-safe
   shutdown. On native Linux, confirm the read-only source bind and
   containerized server instead. Never print private document names or
   contents.
6. Validate the shared Docker binds. Confirm the core Podman overlays, the
   native-Linux Docker ownership overlays, Podman's PostgreSQL direct
   entrypoint, `keep-id` mappings, initialization checks, tracked OpenSearch
   audit-policy bootstrap, macOS mount-root xattr preflight, clean shutdown,
   and engine exclusivity. On native Linux, exercise clean creation and
   Docker-to-Podman plus Podman-to-Docker switching with persistent
   PostgreSQL, OpenSearch, and MinIO records. Require the PostgreSQL and MinIO
   trees to remain owned by the invoking host uid/gid after every Docker start.
   When permissions or engine interchange changes, run the complete macOS and
   Linux lite/full directed matrix in
   [`permissions_cross_validation.md`](permissions_cross_validation.md),
   including rootless Docker's separate named-volume storage model.
7. Verify socket-only code interpreter remains absent and Myst recovery has no
   socket mount or engine-specific overlay. If a future feature needs
   control-plane access, review its authority rather than exposing the rootless
   socket broadly.
8. Exercise local WebUI, document server, embedding readiness, MinIO/OpenSearch,
   SearXNG, and route/fail-closed behavior in proportion to the changed feature.
9. Recheck after a VM/Desktop restart when the change depends on mounts,
   storage, startup health, or generated containers. Clean first-start and
   warm repeated-start behavior are both required lifecycle cases.

For a rootless Docker change, run the same affected lite/full live checks with
the rootless context selected. Confirm `docker info` reports `name=rootless`,
the code interpreter mounts that context's socket, the four ownership-sensitive
stores resolve to named volumes, `host.docker.internal` reaches a configured
host service without selecting a coexisting rootful bridge, Tor state remains
owned by the invoking host user, and both clean and warm starts succeed. Also exercise the capability gate
against a disposable daemon with `userns-remap` and require it to exit before a
Compose container or shared-data marker is created.

Record exactly what could not be exercised and why. Leave the matching Podman
stack state explicit at handoff, and never stop or recreate a user-owned VM or
delete a native volume without authorization.
