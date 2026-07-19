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

The macOS client and Linux VM server can have different versions. The wrapper
requires:

- a Linux Podman server version 5.8.1 or later;
- a Compose provider version 2.20.2 or later; and
- the native startup-health flags checked by `podman update --help`.

The currently verified guest baseline is the official Podman machine-os 5.8.1
image. On this machine, official 5.8.2 and 5.8.5 machine images developed an
unusable overlay image store after restart, reporting
`readlink ... overlay: invalid argument`. This is a local observed regression,
not a claim about every Podman installation. The capability gate deliberately
runs `podman images` so a broken post-restart store fails before stack changes.
If that probe fails, report the machine as unusable. Never recreate a machine
that may contain user data without explicit approval: recreation deletes
Podman-native database volumes and the staged RAG cache.

Starting Podman Desktop or approving macOS access to a new volume does not
necessarily update an existing process or VM. Restart Podman Desktop after a
new approval when access still fails, then rerun the capability, image-store,
mount/staging, and startup-health checks. Do not assume a pre-restart result is
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
replaced. Re-add every required mount explicitly; for example,
`doc-drop-web` needs both the native RAG volume and the tracked
`onyx/doc_drop_webserver.py` read-only bind.

### Common Podman override

`docker-compose.podman.yml` owns these differences:

- `code-interpreter` is placed behind an inactive
  `requires-docker-socket` profile. The legacy executor requires Docker socket
  behavior that the rootless macOS Podman VM does not reliably provide.
- The optional Tailscale frontend gateway replaces Docker tmpfs `uid=`/`gid=`
  options with Podman's `U` option. This use is limited to newly created tmpfs
  mounts, not host data. The service remains non-root and retains its other
  hardening.
- PostgreSQL replaces its macOS host bind with the
  `podman-postgres-data` native volume.
- API startup waits for PostgreSQL to become healthy. Podman creates more of
  the graph concurrently and otherwise can expose database initialization
  races earlier than the Docker path.

### Full-mode override

`docker-compose.podman-full.yml` additionally:

- makes `background` wait for PostgreSQL health;
- replaces the OpenSearch data bind with the
  `podman-opensearch-data` native volume; and
- mounts the externally managed `onyx-podman-rag-docs` volume read-only at
  `/import`, while preserving the document-server script bind.

The RAG volume is marked `external: true` because the Makefile staging step
creates and owns it before Compose starts. Compose must not silently create a
project-scoped substitute or remove it as ordinary project state.

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
- `configure` discovers containers by Compose project label, validates each
  retained regular check, installs an exact native startup check on stopped
  containers, and reinspects the complete project.
- `stage-docs` implements the full-mode native RAG cache described below.

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
2. `podman compose create` creates stopped containers.
3. `startup_health.py configure` installs and verifies native startup checks.
4. `podman compose up -d --wait` starts the verified graph.

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

## macOS mounts, ownership, and attributes

Podman machine host shares use virtiofs. Linux container ownership expectations
do not reliably map to macOS bind mounts:

- mode-0700 PostgreSQL data appeared with unusable ownership and could not be
  safely changed by the image;
- OpenSearch uid/gid 1000 could not create its `nodes` tree and failed with
  `AccessDeniedException`/`failed to bind service`; and
- a user-mounted WebDAV source under `/Volumes` appeared empty or inaccessible
  inside the VM even when the exact directory was configured, macOS approved
  it, and Podman Desktop was restarted.

Do not broaden a share to `/`, all of `/Volumes`, or a symlinked parent as a
workaround. Aside from unnecessary exposure, a macOS-visible symlink does not
make an independently mounted user filesystem re-exportable by virtiofs.

The common suffixes solve different Linux problems:

- `:z`/`:Z` change SELinux sharing labels. They do not make an unshared macOS
  path visible, alter uid/gid ownership, or remove macOS attributes.
- `:U` recursively changes ownership of a source. On host binds this can mutate
  private data and was rejected by the tested virtiofs paths. Its only current
  use is on new disposable tmpfs mounts.

Do not apply `chmod`, `chown`, `chattr`, `xattr`, `:U`, or broader VM sharing to
private database or RAG sources. Native volumes are the supported workaround
for PostgreSQL and OpenSearch. Those volumes live inside the Podman VM and are
not synchronized with Docker's `docker-data/postgres` or
`docker-data/opensearch` binds. Switching engines therefore switches database
and index state; it does not migrate it.

## RAG document staging workaround

Direct `podman cp` and ordinary macOS archive behavior may try to preserve
AppleDouble sidecars or extended attributes. The tested WebDAV source failed
with `operation not permitted` while handling a `._.DS_Store` attribute.
Full-mode startup avoids both the virtiofs re-export and metadata-preservation
paths.

`stage-podman-full-docs` ensures the pinned small Python/Alpine staging image is
present and calls `startup_health.py stage-docs`. The implementation:

1. Validates the source directory and a conservative native-volume name.
2. Computes a SHA-256 manifest over relative names, entry types, sizes,
   modification times, and symlink targets. It does not read file bodies or
   print names/content. AppleDouble entries and `.DS_Store` are excluded.
3. Reuses the existing cache immediately when `.source-manifest` matches.
4. Creates `.incoming` inside `onyx-podman-rag-docs` when a refresh is needed.
5. Runs host `tar` with `COPYFILE_DISABLE=1`, `--no-mac-metadata`,
   `--no-xattrs`, `--no-acls`, `--no-fflags`, and AppleDouble/`.DS_Store`
   exclusions.
6. Pipes that archive to an interactive `podman run` receiver with
   `--network=none` and `--pull=never`. The receiver extracts only into
   `.incoming` in the native volume.
7. Activates the completed tree as `docs`, atomically replaces the manifest,
   and removes `.previous`. Activation failure rolls the previous tree back.

A transfer failure leaves the previous active cache in place and makes
`make up-full` fail. This is intentionally not a silent stale-data fallback:
the old copy remains recoverable, but the operator is told that the requested
refresh did not occur. A transient host filesystem read failure requires a
later explicit retry.

The cache duplicates the selected source inside the Podman VM and persists
until its named volume or the machine is removed. Change detection is metadata
based; a source that changes bytes while preserving name, type, size, and
nanosecond mtime will not trigger a refresh. Preserve this explicit trade-off
unless replacing it with another privacy-preserving bounded inventory.

`doc-drop-web` sees `/import/docs` through a read-only volume mount. Always
validate the effective mount and a denied write probe after changing its
Compose definition. Never display document listings, names, or contents in
diagnostic output.

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
  store gates, regular/startup cadence contracts, stopped-container update,
  running-container fail-closed behavior, metadata-free offline document
  streaming, cache reuse, atomic failure preservation, and volume-name
  validation.
- `tests/test_myst_lifecycle_makefile.py`: capability prerequisites,
  create/configure/start ordering, staged full-mode documents, direct Onyx
  image pulls, and exclusion of the upstream installer/socket-only executor.
- `tests/test_onyx_network_isolation.py`: effective Podman overlay selection,
  optional VPN behavior, tmpfs options, native PostgreSQL/OpenSearch storage,
  database health dependencies, external RAG volume, script bind, and
  read-only mount semantics.
- `tests/test_myst_readiness.py`: interface/route parsing that must remain
  valid with Podman's network layout.

Run `make check` after every Podman script, Makefile, or overlay change. For
image/source pin or runtime-patch work, follow the repository's ordinary
`make check-upgrade` rules as well. `make health-inventory` validates the
effective lite/full health models, but it does not replace native live
inspection.

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
5. For full mode, exercise both a changed-source transfer and the unchanged
   manifest fast path when safe. Confirm the external native volume, retained
   script bind, `/import/docs` availability, and denied writes without printing
   private document names.
6. Validate native database volumes on both a fresh machine/volume and existing
   data where the change touches initialization, ownership, or dependencies.
   Confirm Docker bind data was not modified.
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
