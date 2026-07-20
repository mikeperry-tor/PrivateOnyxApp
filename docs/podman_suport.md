# Podman support

This document is the compatibility and validation authority for running this
wrapper with rootless Podman on macOS. Read it before adding a feature that
changes Compose layering, mounts, container lifecycle, health checks, image
preparation, network interfaces, or Docker-socket use.

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

`podman compose` is a frontend to an external Compose provider. It may report
`docker-compose` as that provider; the provider still submits operations to
the Podman API. This does not mean the Docker engine is being used. Do not run
`docker` commands as a fallback, and use `podman version` or `podman info` when
the selected engine must be proved.

Docker Compose 5.3.1 may otherwise honor the host's selected Docker Desktop
context or Podman's SSH system connection. The Makefile therefore exports an
exact `DOCKER_HOST=unix://...` value from the current machine's inspected
forwarded socket whenever Podman is selected. Preserve this pin: the SSH path
can inherit host proxy/SOCKS behavior and fail with `nc: connection failed`,
while the Unix socket also proves that the provider targets Podman's API rather
than Docker Desktop.

The macOS client and Linux VM server can have different versions. The wrapper
requires:

- a Linux Podman server version 5.8.1 or later;
- a Compose provider version 2.20.2 or later; and
- the native startup-health flags checked by `podman update --help`.

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

- `docker-compose.podman.yml` applies to lite and full modes;
- `docker-compose.podman-full.yml` applies only to full mode; and
- `docker-compose.podman-vpn.yml` applies when the VPN/autoheal layer is
  otherwise selected.

Keep Docker and Podman behavior separated in those override files. Preserve
Compose `${VAR:?message}` checks and the Makefile-generated ephemeral secret
flow. When using `!override`, remember that the entire inherited sequence is
replaced.

### Common Podman override

`docker-compose.podman.yml` owns these differences:

- `code-interpreter` is placed behind an inactive
  `requires-docker-socket` profile. The legacy executor requires Docker socket
  behavior that the rootless macOS Podman VM does not reliably provide.
- The optional Tailscale frontend gateway replaces Docker tmpfs `uid=`/`gid=`
  options with Podman's `U` option. This use is limited to newly created tmpfs
  mounts, not host data. The service remains non-root and retains its other
  hardening.
- PostgreSQL always uses the same initialized `docker-data/postgres` bind as
  Docker Desktop, with the required `keep-id` mapping and direct entrypoint.
- API startup waits for PostgreSQL to become healthy. Podman creates more of
  the graph concurrently and otherwise can expose database initialization
  races earlier than the Docker path.

### Full-mode override

`docker-compose.podman-full.yml` additionally:

- makes `background` wait for PostgreSQL health;
- always uses the same initialized `docker-data/opensearch` bind as Docker
  Desktop with the required `keep-id` mapping; and
- replaces container-side document serving with a hardened fixed relay to the
  wrapper-managed macOS document server.

### Shared Docker data

The two core Podman overlays let Docker Desktop and Podman use the same stopped
PostgreSQL cluster and OpenSearch index in place. This is unconditional: there
are no additional database overlays, marker-selected storage branches, or
opt-out flags. The startup preflight requires the expected initialization
markers and does not inspect chats, tables, index names, or document contents.
Both engines must be down before switching because shared storage does not make
concurrent database or index writers safe.

Before either `make up-*` recipe reaches Compose, an atomic marker under
`docker-data/host-services` claims the shared database/index data for Docker or
Podman. Repeated starts by the same engine are allowed; the other engine fails
closed until the owner's matching `make down-*` completes successfully. Inspect
the marker with `make shared-data-engine-status`. A failed start deliberately
retains its claim for a safe retry. After upgrading an already-running checkout,
run its matching `make up-*` once to seed the marker before switching engines.
If a machine failure leaves a stale claim, verify both engines have no Onyx
containers before removing the marker manually.

PostgreSQL requires three linked settings:

- `userns_mode: keep-id:uid=70,gid=70` maps the Podman machine user to the
  Alpine image's PostgreSQL account;
- `user: "70:70"` starts the database with that account; and
- `entrypoint: ["postgres"]` bypasses the stock image entrypoint only for the
  already-initialized shared cluster.

The entrypoint bypass is essential. Its unconditional `chmod 0700` on
`PGDATA` makes macOS virtiofs create a root-owned
`user.containers.override_stat` on the mount root. Subsequent lookup then
fails even though Docker's `com.docker.grpcfuse.ownership` metadata correctly
records uid/gid 70. `prepare-podman-postgres-data` validates `PG_VERSION` and
removes only that mount-root Podman override before Compose create. It leaves
Docker's attribute and valid per-file Podman runtime attributes intact.

OpenSearch uses `keep-id:uid=1000,gid=1000` and its ordinary image entrypoint.
The tested image can create and reuse its normal per-file Podman attributes;
do not recursively delete them. `prepare-podman-opensearch-data` requires its
initialized `nodes` directory before a full-mode create.

### VPN override and socket limitations

`docker-compose.podman-vpn.yml` puts `autoheal` behind the inactive
`requires-docker-socket` profile. Rootless Podman on macOS cannot provide the
container-visible Docker socket semantics expected by the stock autoheal
image. Routing remains fail-closed, but automatic Myst restart is unavailable.
Diagnose the readiness failure, then use `podman restart myst-client-vpn` or
restart the matching stack.

Do not expose a broader or privileged Podman socket merely to restore these
features. A future replacement must be designed and validated as a distinct
Podman control path with appropriately narrow authority.

## Startup-health translation

The Compose provider accepts and renders `healthcheck.start_interval`, but the
tested Podman compatibility API drops it when creating containers. A rendered
Compose model is therefore necessary but not sufficient evidence.

`podman/startup_health.py` provides three strict actions:

- `check` validates that the selected binary is Podman, inspects the Linux
  server version, probes the image store, checks the Compose provider, and
  verifies the required native `podman update` flags.
- `configure` renders the Makefile-selected effective Compose model, discovers
  all created containers by Compose project label, requires the complete
  retained-health set to match, validates each command, timeout, retry count,
  start period, and regular cadence, installs an exact native startup check on
  stopped containers, and reinspects the complete project.
- `prepare-shared-data` validates an initialized Docker database/index path
  and performs the narrow PostgreSQL mount-root xattr cleanup described above.

For each retained health check, `configure` copies the exact regular command
and timeout into Podman's `StartupHealthCheck`, sets its interval to five
seconds and success threshold to one, and disables startup-health-driven
restart. It separately requires a ten-minute ordinary interval, except Myst's
one-minute interval. A running container without the exact native startup
configuration is rejected instead of being modified in place.

The authoritative inspect fields are separate:

```text
.Config.StartupHealthCheck
.Config.Healthcheck
```

The Podman Makefile lifecycle is consequently create/configure/start:

1. `check-container-health-capability` runs the capability gate.
2. The mode-appropriate database preflights validate the shared binds and
   remove only the unsafe PostgreSQL mount-root override when present. Full
   mode also starts or validates the PID-tracked host document server.
3. `podman compose create` creates stopped containers.
4. `startup_health.py configure` installs and verifies native startup checks.
5. `podman compose up -d --wait` starts the verified graph.

Full mode performs that sequence first for `local-embedding-shim`, makes the
single `/ready` request, then repeats create/configure/start for the complete
graph. Do not collapse this into a single `compose up`: that would start
containers before the native health contract is installed.

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

Root-context Podman builds use `.containerignore`. It excludes private bind
data, the document source, generated Onyx deployment state, local model/cache
trees, logs, and reference checkouts. Preserve those exclusions: without them,
Podman archives large private host trees into the remote build context before
the Dockerfile runs, causing long silent starts and unnecessary data exposure.

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

For every Podman full-mode source, including the default `./doc-drop`, the
wrapper serves `ONYX_RAG_DOC_SOURCE_DIR` without mounting or copying it into
the VM. This uniform path also supports externally mounted sources, including
WebDAV mounts, that are fully readable on macOS yet absent at the Podman VM's
`statfs` boundary. Ownership mappings, SELinux suffixes, and xattr cleanup
cannot repair a path that virtiofs did not expose.

`podman-doc-server-start` validates the source, rejects an unrelated listener
on fixed host port 18091, and starts
`onyx/doc_drop_webserver.py` with a PID and private log under
`docker-data/host-services`. A tracked process is reused only when its command
identity and `/_health` response both match. Malformed, stale, or reused PID
records never authorize signaling an unrelated process.

The Podman `doc-drop-web` service is a non-root, read-only, capability-free
`socat` relay. It keeps the existing internal route and display networks but
has no document mounts. Its only additional network is a dedicated
non-internal host uplink, and its fixed command connects only
`host.containers.internal:18091`. Podman's userspace gateway reaches the
macOS listener as a loopback peer; the server rejects non-loopback peers before
serving any URL. This restriction is enabled only by the host process's
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
exit, and removes the PID record. Docker full mode retains its ordinary
read-only bind-mounted `doc-drop-web` container and never starts the host
process. Request logging is suppressed because URLs can contain private file
names; the server log contains lifecycle diagnostics only.

## Networking differences

Do not assume Podman assigns Docker-style interface names or a fixed field
order in `ip route get` output. Myst readiness must identify the selected
`via`, `dev`, and `src` values by token and compare them with the actual
interface/address state. Preserve the tested exact host-route and final-hop
policy behavior; a Podman workaround must not give applications direct egress
or join them to the trusted Myst namespace.

`host.docker.internal` is still the configured logical host alias. Validate
the actual resolved address and route inside the relevant container rather
than assuming Docker Desktop's address or interface.

## Deterministic coverage

The primary Podman-specific tests are:

- `tests/test_podman_startup_health.py`: binary refusal, capability and image
  store gates, exact effective-model health-set and field contracts,
  stopped-container update, running-container fail-closed behavior, and
  shared-data preflight.
- `tests/test_shared_data_engine.py`: same-engine claim reuse, cross-engine
  exclusion, matching release, and corrupt-marker failure.
- `tests/test_myst_lifecycle_makefile.py`: capability prerequisites,
  create/configure/start ordering, host document-server PID lifecycle, direct
  Onyx image pulls, and exclusion of the upstream installer/socket-only
  executor.
- `tests/test_onyx_network_isolation.py`: effective Podman overlay selection,
  optional VPN behavior, tmpfs options, unconditional shared Docker
  PostgreSQL/OpenSearch storage, database health dependencies, and the fixed
  hardened host document relay.
- `tests/test_doc_drop_webserver.py`: the non-indexed host readiness endpoint
  and document-root/symlink confinement.
- `tests/test_myst_readiness.py`: interface/route parsing that must remain
  valid with Podman's network layout.

Run `make check` after every Podman script, Makefile, or overlay change. For
image/source pin or runtime-patch work, follow the repository's ordinary
`make check-upgrade` rules as well. `make health-inventory` renders the actual
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
5. For full mode, confirm the host document server PID identity and readiness,
   the absence of document mounts/volumes on the relay, internal and published
   document access, immediate source visibility, and identity-safe shutdown.
   Never print private document names or contents.
6. Validate the shared Docker binds. Confirm the core Podman overlays,
   PostgreSQL direct entrypoint, `keep-id` mappings, initialization checks,
   mount-root xattr preflight, clean shutdown, and engine exclusivity.
7. Verify socket-only code interpreter and autoheal remain absent. If a future
   feature needs control-plane access, review its authority rather than
   exposing the rootless socket broadly.
8. Exercise local WebUI, document server, embedding readiness, MinIO/OpenSearch,
   SearXNG, and route/fail-closed behavior in proportion to the changed feature.
9. Recheck after a VM/Desktop restart when the change depends on mounts,
   storage, startup health, or generated containers. Clean first-start and
   warm repeated-start behavior are both required lifecycle cases.

Record exactly what could not be exercised and why. Leave the matching Podman
stack state explicit at handoff, and never stop or recreate a user-owned VM or
delete a native volume without authorization.
