# Rootless Podman Code Tools Plan

> **Status: deferred.** This document specifies the evidence, implementation,
> and validation required before `run_python` and Onyx coding agents can be
> supported by this wrapper under rootless Podman. Current Podman deployments
> continue to omit the socket-dependent `code-interpreter` service until every
> applicable completion criterion in this plan passes on both native Linux and
> macOS Podman Machine.
>
> This is an implementation plan, not a description of current supported
> behavior. Canonical behavior must be moved into the owning documentation as
> part of implementation. Do not expose the engine socket paths as user-facing
> settings in `.env.wrapper.example`.

## Objective

Support the pinned Onyx code tools in a fully rootless Podman stack by mounting
the selected rootless Podman Docker-compatibility API socket into the pinned
`code-interpreter` controller at `/var/run/docker.sock`. The controller's
bundled Docker CLI will continue to use its upstream `docker` executor backend;
Podman's Docker-compatible API will create, inspect, execute in, and remove the
short-lived executor containers.

The completed feature must support both code-tool forms used by Onyx v4.6.5:

- `run_python`, including streamed execution, staged input files, generated
  files, download, deletion, timeouts, resource limits, and optional restricted
  network access; and
- `coding_agent`, including repository upload, long-lived session creation,
  repeated Bash commands in one workspace, explicit deletion, TTL expiry, and
  reaping after controller restart.

The implementation must preserve the privacy, isolation, resource, lifecycle,
and fail-closed contracts in:

- [Podman support](../../podman_suport.md);
- [Internal network security](../../internal_network_security.md);
- [VPN routing and restricted proxies](../../vpn_routing_and_proxies.md);
- [Wrapper patch information](../../onyx_patch_info.md);
- [Patch and component upgrades](../../onyx_patches_upgrade.md); and
- [Resource minimization](../../resource_minimization.md).

Rootless Podman remains a separately qualified runtime. A passing rootless
Docker test is not evidence that the Podman API, user namespace, network,
tmpfs, cgroup, or cleanup semantics are correct.

## Pinned Upstream Contract

The initial implementation targets this exact component set:

| Component | Pinned contract |
| --- | --- |
| Onyx | `v4.6.5` |
| code-interpreter API | `0.4.6` |
| upstream Python executor | `0.4.5`, pinned by digest |
| derived executor | the Makefile-derived tag from the upstream digest, `executor/Dockerfile`, and hashed dependency lock |

Onyx treats code-interpreter as an HTTP service selected through
`CODE_INTERPRETER_BASE_URL`. It does not manage the engine socket itself.
`run_python` calls the stateless execute/file endpoints. `coding_agent` uploads
a repository archive, creates a session, executes Bash in that session, and
deletes it when the agent finishes. Both paths therefore become available only
when the same controller is configured, enabled, and healthy.

The pinned code-interpreter has two executor backends: `docker` and
`kubernetes`. There is no Podman-specific backend. Its Docker backend invokes
the bundled `docker` CLI for all of the following operations:

- engine version and executor-image inspection;
- detached executor creation with `docker run`;
- tar streaming and Python/Bash execution with `docker exec`;
- forced cleanup with `docker kill` or `docker rm -f`; and
- label-filtered session discovery and TTL reaping with `docker ps -a`.

The stock generated Compose service now mounts
`${DOCKER_SOCK_PATH:-/var/run/docker.sock}` at `/var/run/docker.sock`. Treat
that variable as a generated Compose interface, not proof that an arbitrary
socket is usable. The wrapper must resolve, verify, and supply the correct
engine-visible path.

Re-audit this contract whenever Onyx, code-interpreter, the executor, the
Docker CLI embedded in the controller, or the validated Podman baseline
changes. If upstream adds a supported Podman backend, prefer it after a full
security and behavior comparison and remove compatibility code that it makes
obsolete.

## Scope and Non-Goals

Included:

- rootless Podman on native Linux;
- the selected rootless Podman Machine connection on macOS;
- lite and full wrapper modes;
- executor networking disabled and enabled through the existing restricted
  proxy-only overlay;
- clean and warm startup, restart, cancellation, timeout, TTL, and shutdown;
- image preparation and image-only validation in Podman's own image store;
- preservation of the current rootless Docker behavior while sharing safe
  socket-resolution and preflight code; and
- current fixed host, Tailscale, and onion WebUI entry points, which all reach
  the same Onyx API and therefore require no code-tool-specific browser route.

Not included:

- rootful Podman as qualification evidence for rootless support;
- a privileged controller or Docker-in-Docker;
- a TCP-published Podman API;
- a world-writable engine socket or host-wide socket permission change;
- mounting the engine socket into the Onyx API, background workers, executor
  children, or any service other than `code-interpreter`;
- weakening child resource, capability, tmpfs, or network restrictions to
  accommodate an unsupported Podman API behavior;
- treating Craft/Build sandboxes as the ordinary Onyx coding-agent path; or
- changing the generated Onyx deployment files by hand.

## Security and Authority Model

The controller's writable engine socket is an engine-control boundary. A
controller compromise can create arbitrary containers, mount engine-visible
paths, join other networks, and inspect other containers owned by the rootless
Podman user. Rootless execution limits the authority compared with a rootful
host engine, but it still exposes the complete Podman user account, this
stack's secrets, its containers, and engine-visible private data. Document
this residual plainly; do not describe the socket as harmless because it is
rootless.

Preserve these mandatory boundaries:

- only `code-interpreter` receives one read-write Unix-socket bind;
- the bind target remains exactly `/var/run/docker.sock`, which is the default
  endpoint used by the controller's bundled Docker CLI;
- no TCP listener, socket proxy, host network, `--privileged`, added
  controller capability, broad device access, or rootful Podman connection is
  permitted;
- executor children never receive the engine socket, host binds, controller
  filesystem, application secrets, or a shared container namespace;
- executor children retain `--pull never`, `--pids-limit`,
  `no-new-privileges`, `cap-drop=ALL`, the single `CHOWN` setup capability,
  bounded tmpfs workspaces, CPU and memory limits, and non-root execution as
  `65532:65532`;
- network-disabled children use the engine's `none` network and cannot resolve
  or reach stack, host, metadata, LAN, or public destinations;
- network-enabled children join only the named internal executor network and
  receive exactly the eight upper/lower-case proxy variables already validated
  by the wrapper;
- the dedicated executor bridge remains the only child route to the ordinary
  public final-hop policy; it must not acquire host/LAN destination exceptions;
- failure to resolve, mount, open, identify, or exercise the selected socket
  stops startup without trying another socket or relaxing isolation; and
- logs may name the engine, phase, and non-secret socket class, but must not
  print environment contents, file payloads, code, repository contents,
  credentials, or executor output beyond explicit test markers.

On SELinux systems, first test whether the rootless user socket can be bind
mounted with its existing label. Do not recursively relabel the user runtime
directory or mutate the live socket label. If a controller-only SELinux option
is required, document its exact authority and prove that it does not affect
other services. Reject the platform if the only working choices are a broad
host relabel, disabled host policy, or privileged execution.

## Socket Path Model

### Two distinct paths on macOS

Do not continue using one value for both of these roles:

1. **Client/Compose socket:** a Unix socket visible on the macOS host. The
   external Compose provider uses it through `DOCKER_HOST` to submit requests
   to the selected Podman Machine.
2. **Controller mount source:** the Podman API socket visible in the Linux
   server or Podman Machine guest. A bind source sent to the remote engine is
   resolved in that server filesystem and must be mountable into the
   controller.

These paths may be identical on native Linux. They are normally different on
macOS because Podman forwards the guest API to a host-side Unix socket. A
host-side forwarded path is not automatically a valid bind source inside the
guest, while a guest `/run/user/.../podman/podman.sock` path is not directly
openable by the macOS Compose client.

Refactor `podman/startup_health.py` to return an explicit immutable socket
contract rather than an ambiguous string. The logical result should contain:

```text
engine_kind
rootless_identity
client_socket_path
controller_mount_source
controller_mount_target=/var/run/docker.sock
remote_server
```

Names may differ in the implementation, but client and mount paths must remain
separate values with separate validation.

### Resolution rules

For native rootless Podman:

- obtain the selected rootless API path from `podman info` or the selected
  system connection;
- require an absolute Unix path owned by the selected rootless user;
- require the local path to be a live socket; and
- use the same verified path for the Compose client and controller bind only
  when both checks prove that identity.

For macOS Podman Machine:

- obtain the host-forwarded client socket from the selected machine/connection
  inspection used by the current Compose capability gate;
- obtain the guest socket path from the selected connection's server endpoint
  or a structured Podman inspection field;
- require the selected connection to point to the same running machine and to
  a rootless guest socket;
- verify the guest path is an absolute live Unix socket from inside that exact
  machine before any Compose create;
- submit the guest path as the controller bind source; and
- never hardcode the macOS UID, guest UID, `/run/user` number, SSH port,
  machine name, or temporary forwarded-socket directory.

For rootless Docker:

- preserve the existing requirement for an absolute live local Unix socket
  from the selected rootless Docker context;
- populate both logical paths with that same verified socket; and
- continue rejecting TCP, SSH, remote, missing, rootful `userns-remap`, or
  daemon-mode drift.

`DOCKER_HOST` should use only the client/Compose socket. The generated
`DOCKER_SOCK_PATH` interpolation should use only the controller mount source.
If a second Make variable is necessary, keep it stack-owned and internal. Do
not add either path to `.env.wrapper.example` or require ordinary users to copy
machine-specific paths.

Any explicit developer override must pass the same engine-identity, absolute
path, socket-type, rootless-owner, and server-visibility checks. An override is
not permission to bypass discovery or point at a rootful engine.

## Feasibility Gate

Complete an isolated capability spike before changing the supported Compose
topology. Run it against both required rootless platforms using the exact
pinned controller and derived executor images already present in the selected
Podman image store. The probe must never pull an image implicitly.

### Controller-to-Podman API probe

Launch a disposable, networkless `code-interpreter` controller probe with only
the candidate guest/server socket mounted at `/var/run/docker.sock`. Require
the bundled Docker CLI to complete all of these operations against Podman's
compatibility API:

- `docker version --format` with a valid server response;
- an engine identity check that proves the server is the selected Podman
  instance rather than another local socket;
- `docker image inspect` of the exact derived executor reference;
- `docker ps -a` with the exact label and format expressions used by the
  session reaper; and
- clean controller exit and removal without a leaked probe container.

Do not accept a probe that works only after enabling Docker-in-Docker, changing
socket permissions, mounting a rootful socket, or disabling the rootless user
namespace.

### Exact child-command probe

Use the pinned `DockerExecutor._build_run_command()` to generate the command;
do not hand-author a simpler Podman command and treat it as equivalent. Execute
the exact command through the controller's Docker CLI and validate every
required option:

- detached `--rm` container and `--pull never`;
- exact named or `none` network;
- `--cgroupns host`;
- PID limit;
- no-new-privileges and capability drop/add set;
- work directory and both tmpfs mounts, including workspace UID/GID;
- Python environment variables;
- CPU ulimit;
- memory and memory-swap equality;
- optional proxy environment; and
- exact derived executor image and bounded sleep command.

After creation, inspect the child with native `podman inspect`, not only the
Docker CLI, and prove the effective OCI configuration matches the requested
contract. Execute Python as `65532:65532`, copy a tar archive into the
workspace, copy a result archive out, kill the child, and require no residual
container, mount, or anonymous volume.

If Podman rejects or silently drops a required option, stop. First determine
whether a current supported Podman API fixes the problem. If not, the only
acceptable code change is a narrow, strictly validated upstream contribution
or wrapper-owned command adaptation with equivalent or stronger semantics.
Do not delete a security or resource flag merely to pass the probe. Any runtime
adaptation must be documented as an Onyx/code-interpreter patch, fail on source
drift, and be exercised inside the pinned image.

### Image-reference probe

Prove that the exact derived local image reference resolves identically
through:

- native `podman image inspect`;
- the Docker CLI through Podman's API;
- code-interpreter startup's image check; and
- the child `run --pull never` command.

Do not allow an unqualified name to trigger registry search or a pull. If
Docker and Podman assign different implicit local registries, adopt one
explicit, engine-neutral local reference, update the build/tag derivation and
tests, and require that exact reference everywhere. Do not add opportunistic
retagging or a mutable fallback.

### Feasibility pass conditions

Implementation may proceed only if both platforms prove:

- correct rootless engine identity;
- a mountable private Unix socket without host permission changes;
- exact child security and resource semantics;
- reliable named-network attachment;
- stateless and session executor lifecycle operations;
- label-based TTL reaping;
- deterministic cleanup after controller termination; and
- no need for privilege, rootful Podman, direct egress, or weakened policy.

Record a blocked platform as unsupported. Do not partially enable the service
for only one rootless Podman platform while the documentation claims general
Podman support.

## Implementation Design

### 1. Engine-aware socket resolution

Refactor `podman/startup_health.py` and the Makefile so socket discovery has
separate actions or structured fields for the Compose client and controller
mount source. Preserve the existing early Compose and image-store capability
checks.

Add strict checks for:

- selected client and server identity;
- rootless rather than rootful Podman connection;
- absolute, non-symlink socket paths;
- socket liveness on the relevant side of the machine boundary;
- a controller mount source belonging to the selected server;
- mismatch between selection time and pre-create time; and
- clear, non-secret failure messages for stopped machines and disabled native
  Linux `podman.socket` units.

The Makefile should export:

- the client socket only as the external provider's `DOCKER_HOST`; and
- the controller mount source as the generated Compose
  `DOCKER_SOCK_PATH` value.

Re-resolve or revalidate immediately before Compose create so a machine or
connection switch cannot redirect the controller after the initial capability
gate. Do not silently fall back to the default Podman connection.

### 2. Image preparation

Make Podman startup require the exact pinned code-interpreter image and derived
executor image in Podman's selected image store:

- include `CODE_INTERPRETER_IMAGE` in the Podman required-image set;
- make `executor-image-ready` an ordinary Podman lite/full prerequisite;
- build the executor with `CONTAINER_BIN=podman`, the pinned upstream digest,
  the tracked Dockerfile, and the hashed dependency lock;
- retain explicit proxy build arguments only when they are present in the
  supported host build environment;
- prohibit startup-time dependency installation and implicit controller pulls;
  and
- verify SymPy, ReportLab, svglib, and the rest of the advertised package
  contract with networking disabled.

The code-interpreter startup currently attempts a pull when its configured
executor image is absent. The wrapper precondition must make that path
unreachable. Add a live negative test in an isolated store or disposable
image name and require wrapper startup to fail before the application graph
rather than relying on that upstream pull behavior.

### 3. Compose topology

After the feasibility gate passes:

- remove the inactive `requires-docker-socket` profile override from the common
  Podman layer;
- retain the generated service's root user only for controller operation and
  socket access; do not add privileges or capabilities;
- mount exactly the validated `DOCKER_SOCK_PATH` source at
  `/var/run/docker.sock` read-write;
- keep the controller only on `onyx-backend`;
- preserve the empty wrapper-owned `depends_on` reset unless an enabled
  executor route adds the existing healthy bridge dependency;
- select `docker-compose.code-interpreter-network.yml` under Podman whenever
  `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`;
- preserve `PYTHON_EXECUTOR_DOCKER_NETWORK=none` when network access is
  disabled;
- retain the exact named `onyx-code-interpreter-executor` internal network and
  proxy environment when enabled; and
- ensure no Podman overlay resets the socket bind, network set, health check,
  limits, or exact derived image.

Do not duplicate the complete code-interpreter service in an engine overlay.
Keep common Onyx/API settings in the base and limit Podman overrides to actual
engine differences. If macOS and native Linux require different socket-source
syntax, put only that translation in the OS-specific Podman layer or in the
Makefile-derived value.

Render the complete Makefile-selected model rather than invoking Compose with
an ad hoc file list. The root base must remain first so generated deployment
paths and wrapper-relative mounts resolve correctly.

### 4. Startup-health lifecycle

Once code-interpreter joins the Podman graph, it must participate in the
existing create/configure/start sequence:

1. resolve and validate both socket roles;
2. validate the Podman client/server/provider and image store;
3. prepare the exact controller and executor images;
4. create the stopped Compose graph;
5. install and re-inspect native Podman startup health for every retained
   health check, including code-interpreter;
6. start the graph with the existing bounded wait; and
7. assert the controller is healthy and reports version `0.4.6`.

Controller health must prove both engine connectivity and exact executor-image
availability. A socket file existing is not sufficient. The startup flow must
also reject a running controller that was created with the wrong socket or
without the exact native startup-health configuration rather than modifying it
in place.

Full mode must retain its staged embedding readiness sequence. Adding the
controller must not move it before an earlier fail-closed socket/image gate or
collapse Podman's create/configure/start phases.

### 5. Runtime cleanup and ownership

Validate and, if necessary, narrowly adapt cleanup around Podman's API response
text without broad exception matching:

- stateless `code-exec-*` children are killed on success, Python failure,
  timeout, client disconnect, and controller shutdown;
- session `code-session-*` children retain labels and self-expiring sleep;
- explicit session deletion removes the exact prefixed container;
- the 30-second reaper finds and removes only expired, correctly labelled
  sessions;
- restart reaps an expired session left by a terminated controller;
- malformed labels and unrelated containers are ignored;
- `make down-lite` and `make down-full` leave no executor children; and
- cleanup failures remain visible without deleting unrelated containers.

If Compose down cannot order controller shutdown and child cleanup reliably,
add a bounded wrapper-owned pre-down cleanup that filters exact labels and
name prefixes through the selected rootless Podman engine. It must verify
engine identity, refuse malformed matches, never use broad name globs, and be
covered by unit and live failure tests. Prefer upstream TTL/self-removal when
it is sufficient.

### 6. Rootless Docker simplification

Use the socket work to remove ambiguity from rootless Docker without merging
the runtimes' behavior:

- use the same structured client-versus-mount socket contract, with both paths
  equal only after local rootless Docker verification;
- centralize absolute-path, Unix-socket, selected-engine, and pre-create drift
  checks;
- use one common generated Compose mount interface for the controller;
- share exact executor-image preparation and controller health postconditions;
- share command-source and package image gates where engine semantics are the
  same; and
- keep rootless Docker's native health behavior, storage volumes,
  RootlessKit host gateway, daemon-mode detection, and `userns-remap`
  rejection separate.

Do not make rootless Docker depend on Podman commands or Podman Machine fields.
Conversely, do not treat the presence of a Docker-compatible API as proof that
the server is Docker. Tests must prove the expected engine identity for each
branch.

This refactor should eliminate the current conceptual overload in which
`DOCKER_SOCK_PATH` is both a client endpoint and a container bind source. It
should not add a user-facing socket knob or change ordinary rootful Docker's
default `/var/run/docker.sock` behavior.

## Deterministic Test Requirements

### Socket discovery and capability tests

Extend `tests/test_podman_startup_health.py` with exact fixtures for:

- native Linux rootless Podman where client and mount paths are equal;
- macOS Podman where host-forwarded and guest paths differ;
- selected non-default rootless machine/connection;
- stopped machine;
- missing native Linux user socket;
- rootful connection rejection;
- malformed, relative, symlinked, regular-file, directory, and TCP endpoints;
- disagreement between machine inspection, system connection, and server
  identity;
- guest socket absent even though the host-forwarded client socket is live;
- connection or machine switch between resolution and pre-create validation;
- rootless Docker's equal local paths;
- remote/rootful Docker and daemon-wide `userns-remap` rejection; and
- error messages that do not disclose credentials or unrelated environment.

Mocked tests must assert exact commands and parsed fields. Add parser fixtures
from both supported Podman platforms without embedding private host paths or
SSH keys.

### Makefile and image tests

Update `tests/test_myst_lifecycle_makefile.py`,
`tests/test_python_executor_image.py`, and
`tests/test_validation_makefile.py` to require:

- Podman includes the pinned code-interpreter in its required image set;
- Podman runs `executor-image-ready` before Compose creation;
- the executor build uses the selected `CONTAINER_BIN` and exact digest;
- Compose `DOCKER_HOST` receives only the client socket;
- generated `DOCKER_SOCK_PATH` receives only the controller mount source;
- both values are revalidated before create;
- Podman image validation no longer skips the code-interpreter contract;
- validation never pulls or substitutes images; and
- rootless Docker retains its image and socket prerequisites.

### Compose model tests

Extend `tests/test_onyx_network_isolation.py` to render at least:

| Engine/platform | Mode | Executor network |
| --- | --- | --- |
| native Linux rootless Podman | lite | disabled |
| native Linux rootless Podman | lite | enabled |
| native Linux rootless Podman | full | disabled |
| native Linux rootless Podman | full | enabled |
| macOS rootless Podman | lite | disabled/enabled |
| macOS rootless Podman | full | disabled/enabled |
| native Linux rootless Docker | lite/full | disabled/enabled |

For every model assert:

- code-interpreter is active without a hidden profile;
- only code-interpreter mounts the engine socket;
- the mount source and target are exact and there is no TCP socket;
- the API points only to `http://code-interpreter:8000` on `onyx-backend`;
- the controller has no published port, host network, privilege, added
  capability, or application-egress network;
- network-disabled children select `none` and create no unused executor
  bridge/network;
- network-enabled models include exactly the dedicated internal executor
  network, bridge, upstream policy network, eight proxy variables, and healthy
  dependency;
- no application service joins the executor network;
- Podman storage, keep-id, macOS document relay, Tor, and startup-health
  translations remain unchanged; and
- rootless Docker retains its distinct storage overlays and host gateway.

Also render representative VPN, native Tor, Tailscale, and onion combinations
to prove optional layer ordering does not replace the socket bind, attach the
controller or child to an unintended network, or give the executor host-route
exceptions.

### Code-interpreter contract tests

Retain and expand the pinned-image checks so both Docker and Podman validate:

- the exact `DockerExecutor._build_run_command()` signature and source markers;
- the complete child command and required security/resource flags;
- exact `none` and named-network selection;
- exact restricted proxy environment parsing;
- controller health's engine and image checks;
- stateless execute, streaming, upload, download, deletion, and file snapshot
  schemas;
- session create, repeated Bash, delete, labels, TTL, and reaper schemas;
- Python package capabilities and real offline functionality; and
- Onyx's `run_python`, `bash`, and `coding_agent` availability/version gates.

Tests should fail on a newly added upstream Docker command, flag, API call,
session label, cleanup heuristic, or backend. Every such change requires a new
Podman compatibility audit rather than an optimistic pass.

## Selected-Image and Isolated Runtime Validation

Add a focused Podman socket validation target rather than hiding live engine
authority inside ordinary deterministic tests. It must use only selected local
images and disposable uniquely named containers/networks, and it must remove
them on success and failure.

The target must:

1. prove rootless Podman identity and socket-path separation;
2. mount the exact controller source into the pinned API image;
3. run controller health without application networks;
4. execute the exact stateless child lifecycle;
5. exercise a long-lived session, labels, repeated Bash, explicit deletion,
   expiry, and reaping;
6. inspect child OCI state natively through Podman;
7. verify no child receives the engine socket or a host bind;
8. verify network-disabled isolation;
9. repeat with one disposable internal executor network and fixed local proxy
   fixture, without Internet access;
10. terminate the controller during stateless and session work and verify the
    documented cleanup/TTL result; and
11. report and preserve diagnostics on failure without leaving an active child.

Keep the normal image-only gate usable without a live socket. A suitable final
validation split is:

```sh
CONTAINER_BIN=podman make test-patch-images
CONTAINER_BIN=podman make test-code-interpreter-socket
```

The exact target name may change, but image structure and live socket authority
must remain separate, visible checks.

## Live Stack Validation

Run the complete matrix on native Linux rootless Podman and macOS rootless
Podman Machine. Use `CONTAINER_BIN=podman` for every Make invocation and native
`podman` inspection for all evidence. Do not use another engine's status or
image store as evidence.

### Lifecycle matrix

For lite and full modes, with executor networking disabled and enabled:

1. Start from a clean matching-stack down state and a healthy selected
   rootless Podman API socket.
2. Run the matching `make up-lite` or `make up-full`.
3. Verify the complete Compose graph, native startup health, controller image,
   executor image, socket source/target, and rootless engine identity.
4. Confirm Onyx reports code-interpreter enabled and healthy.
5. Repeat the same start without downing the stack and require idempotence.
6. Restart the controller and verify health, stateless execution, and session
   reaping recover.
7. Restart the Podman service or Podman Machine, rerun the supported startup
   flow, and repeat the code-tool checks.
8. Run the matching `make down-*` and require no controller, executor child,
   anonymous volume, or dedicated executor network when it should be absent.

Full mode must additionally prove local embedding readiness, OpenSearch,
MinIO, document ingestion/search, and the macOS document relay or native Linux
document bind remain functional. Code-tool support must not disturb shared
data ownership or engine-handoff guards.

### Real `run_python` validation

Exercise the tool through a real authenticated Onyx chat, not only by calling
the controller directly. Require:

- stdout and a final interactive expression;
- stderr plus a nonzero exit without controller failure;
- a bounded timeout and subsequent successful request;
- uploaded text and binary input files;
- generated PDF and image files, same-origin links, byte-correct download,
  and explicit deletion;
- SymPy, ReportLab, svglib, and representative scientific package use;
- streamed output with multiple chunks;
- output and file-size limit behavior;
- multiple sequential executions and bounded concurrent executions;
- browser rendering through the fixed local publisher and one configured
  remote WebUI entry point; and
- no residual `code-exec-*` container after each result, error, timeout,
  cancellation, or browser disconnect.

For network-disabled execution, attempt stack names, host aliases, metadata,
LAN, and public destinations and require denial. For network-enabled execution,
require a permitted public request through the executor bridge, deny host/LAN/
metadata/private targets, inspect the final-hop policy logs without printing
request content, and prove there is no direct fallback when the bridge or
policy process is stopped.

### Real coding-agent validation

Use a small controlled repository and a real Onyx `coding_agent` invocation.
Require:

- repository download by the Onyx/API path and upload into code-interpreter;
- one long-lived `code-session-*` child with the exact labels and expiry;
- archive extraction into the session workspace;
- repeated Bash commands observing persistent workspace changes;
- streamed coding-agent start/thinking/final packets in the WebUI;
- a correct final answer based on repository contents;
- explicit session deletion after success and agent failure;
- cancellation behavior with bounded cleanup;
- TTL self-removal and reaper removal after deliberately interrupted cleanup;
- controller restart while an unexpired session exists;
- controller restart after expiry; and
- no repository contents, token, command output, or credentials in wrapper,
  proxy, controller, or engine diagnostics beyond intended application output.

Repeat a direct session-protocol test without an LLM so executor lifecycle
failures can be distinguished from model/tool-selection variability.

### Resource and stability validation

Measure both idle and active behavior:

- controller idle RSS/CPU and the 30-second session-reaper engine calls;
- no executor child while idle;
- child CPU, memory, swap, PID, tmpfs, and timeout limits as reported by native
  Podman inspection and an in-child negative workload;
- bounded concurrent child count under simultaneous `run_python` calls;
- one-hour coding-agent session behavior without unbounded engine metadata,
  logs, mounts, or exited containers;
- repeated creation/deletion cycles sufficient to expose leaked conmon/crun
  processes, network namespaces, tmpfs mounts, or storage; and
- controller/engine behavior under memory pressure and process-limit failure.

Document any new steady-state cost in `docs/resource_minimization.md`. Do not
reduce safety limits, extend polling, or retain warm executor children merely
to improve benchmark results.

### Failure matrix

Exercise at least:

- missing/stopped rootless socket;
- wrong machine or connection selected;
- rootful socket selected;
- controller cannot read the socket;
- Docker CLI/API incompatibility;
- executor image absent;
- named executor network absent;
- required run flag rejected or silently dropped;
- child creation, tar upload, exec, snapshot, kill, and removal failure;
- controller crash before and during a request;
- Podman service or VM restart during stateless and session execution;
- policy bridge and final-hop proxy failure in network-enabled mode;
- timeout, cancellation, output overflow, file overflow, and malformed file
  path; and
- TTL reaper list or removal failure.

Every failure must be visible and bounded. None may select a different socket,
rootful connection, direct network, mutable image, privilege, unconfined
workspace, or empty-success response.

## Required Validation Commands

At implementation handoff, run and report:

```sh
make check
CONTAINER_BIN=podman make executor-build
CONTAINER_BIN=podman make test-patch-images
CONTAINER_BIN=podman make test-code-interpreter-socket
CONTAINER_BIN=podman make up-lite
CONTAINER_BIN=podman make ps-lite
CONTAINER_BIN=podman make down-lite
CONTAINER_BIN=podman make up-full
CONTAINER_BIN=podman make ps-full
CONTAINER_BIN=podman make down-full
```

Run the enabled-network variants through the supported wrapper setting and
inspect the Makefile-selected effective model. Run the equivalent affected
lite/full and network-disabled/enabled checks against native Linux rootless
Docker to prove the shared resolver refactor did not regress it. Use the
component-scoped image and socket targets during development; reserve
`make check-upgrade` for the final broad release gate if the implementation
also changes an Onyx or support-image pin.

The implementation report must identify platform, host OS, Podman client and
server versions, Compose provider version, selected connection class, exact
image tags/digests, mode, network setting, checks run, skips, and remaining
limitations. Never include private socket forwarding credentials, document
names, repository contents, prompts, or environment values.

## Documentation Updates During Implementation

Replace obsolete current-behavior text rather than adding a history section.
Update:

- `docs/podman_suport.md`: supported code-tool topology, two-path socket model,
  rootless platform matrix, image preparation, startup health, cleanup,
  deterministic coverage, and live checklist;
- `docs/internal_network_security.md`: controller engine authority, rootless
  boundary, child isolation, executor routing, and residual compromise impact;
- `docs/onyx_patch_info.md`: exact code-interpreter compatibility mechanism,
  any strict runtime adaptation, package and prompt contracts, and removal
  condition;
- `docs/onyx_patches_upgrade.md`: generated socket interface, Docker command/API
  drift, Podman compatibility, image-store, stateless/session cleanup, and live
  `run_python`/`coding_agent` upgrade checks;
- `docs/resource_minimization.md`: controller/reaper idle cost, child limits,
  cleanup, and resource regression checks;
- `docs/vpn_routing_and_proxies.md`: enabled executor route behavior under
  rootless Podman, if no longer already engine-neutral;
- `README.md`: concise user-visible statement that rootless Podman supports
  `run_python` and coding agents, plus any platform prerequisite that users
  must act on;
- `AGENTS.md`: only the supported runtime matrix or repository-wide validation
  guidance that changes; and
- the key stock-Onyx patch/shim list in the owning patch documentation if a
  code-interpreter source adaptation is required.

Do not expose `DOCKER_SOCK_PATH`, the Compose client socket, the guest socket,
or a controller-engine selector in `.env.wrapper.example`. Discovery and
validation are wrapper-owned. Do not preserve the current unsupported-Podman
wording as historical background after support is complete.

After implementation is accepted, move this plan to
`docs/plans/implemented/rootless_podman_codetools.md`. Keep the implemented
record aligned with the accepted design, while the owning documents become the
canonical maintenance contract.

## Completion Criteria

Rootless Podman code-tool support is complete only when:

- the client/Compose socket and controller mount source are independently
  resolved and verified;
- both native Linux and macOS Podman Machine pass the rootless feasibility and
  live matrices;
- the controller uses the selected rootless Podman Docker-compatible API
  without privilege, TCP exposure, socket permission changes, or fallback;
- every upstream Docker child flag has equivalent effective Podman semantics;
- exact pinned controller and derived executor images are prepared and used
  without implicit pulls;
- `run_python` passes real Onyx execution, streaming, file, package, timeout,
  concurrency, network, and cleanup checks;
- `coding_agent` passes real repository, persistent Bash session, packet,
  final-answer, deletion, expiry, restart, and reaper checks;
- only the controller receives the socket and children retain all isolation
  and resource limits;
- disabled networking has no route and enabled networking has only the fixed
  proxy route with no private-target or direct fallback;
- Podman native startup health includes the controller and all lifecycle
  failures are bounded and visible;
- clean, warm, restart, cancellation, timeout, and down paths leave no leaked
  children, mounts, namespaces, or anonymous storage;
- rootless Docker passes its complete affected regression matrix using the
  simplified shared socket contract;
- deterministic, selected-image, isolated-socket, and live-stack gates pass;
  and
- all owning documentation describes the resulting current behavior without
  obsolete unsupported-state text.

Until every applicable criterion passes, keep the common Podman overlay's
inactive `requires-docker-socket` profile and the Podman image/route exclusions
in place.
