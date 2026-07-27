# Resource minimization

## Optional Tor lifecycle

When both roles are disabled, the effective model contains no Tor container,
networks, gateway, runtime mount, rendered Tor config, or Tor health activity.
The ordinary stack preflight still validates the disabled role booleans and
canonical origin, but creates no Tor file or runtime resource. Enabling either
role starts one shared process; combined egress and onion ingress intentionally
share guards, resource pressure, and failure fate.

Tor health is a local, cookie-authenticated `GETINFO status/bootstrap-phase`
query over its private Unix control socket. It runs every five seconds during
startup and every ten minutes after health is established. It performs no
periodic Internet or DNS probe and logs no cookie, onion key, or onion address.
The fixed onion gateway follows the same low-idle local health cadence.

## Purpose

This document describes the stack's implemented power and resource
minimization policy. It is the regression authority for health cadence,
background work, optional model lifecycle, search providers, storage settings,
and other low-idle behavior. Read it before changing those areas, and keep it
aligned with the implementation and deterministic contracts.

The objective is not minimum resource use at any cost. The stack must retain
visible startup, request correctness, privacy routing, indexing recovery, and
explicit operator control. Resource-saving changes must not introduce hidden
fallbacks, retries, migrations, or weaker ownership checks.

## Design principles

- Keep an idle private stack quiet enough for an always-on workstation.
- Preserve visible, fail-closed startup rather than hiding a wedged dependency.
- Preserve request correctness, privacy routing, indexing recovery, and explicit
  operator control.
- Reclaim large optional model memory after inactivity without imposing a short
  model-load deadline.
- Prefer component configuration and aggregate boundary checks over periodic
  work, runtime administration, or copied upstream implementations.

## Implemented controls

### Container health and startup

- Retained local health checks poll every five seconds during startup and every
  ten minutes in steady state. Myst remains once per minute because its same
  local result also triggers qualified post-readiness recovery.
- Fixed gateways and publishers validate their complete local boundary. The
  corresponding origin health check is disabled when it would only repeat the
  same path.
- Nginx is the WebUI health boundary. Its root request traverses the frontend,
  so `web_server` has no separate periodic health check. Nginx still waits for
  the API independently because a frontend root response does not prove API
  health.
- Health checks perform local-only work. They do not run inference, public DNS,
  Internet requests, or storage migrations.
- Docker requires Engine API 1.44+ for native `start_interval` support. The
  shared Compose probe also requires the model to retain `start_interval`,
  `!override`, and routing-critical `gw_priority`.
- Podman validates its engine/image-store/Compose capability once before any
  shared-data or host-process mutation, creates stopped containers, installs
  native five-second startup checks, verifies the resulting container model,
  and only then starts containers. Both full-mode staged configuration passes
  reuse the prerequisite result rather than repeating the probe.
- Each Compose health phase has a 420-second outer wait. A failed container is
  left available with its health history rather than being silently restarted
  by startup health.
- Full mode stages `local-embedding-shim`, performs one visible inference-backed
  `/ready` request, and only then starts a fresh API/background tier.
- Recreating the host policy restarts its bridge and forces a fresh
  policy-generated 403 startup probe. Podman recreates the stopped pair before
  native startup-health configuration and asserts the bridge is running and
  healthy after the external Compose provider returns.

`make health-inventory` is the source of truth for the selected engine,
environment, profiles, exact health set, and approximate steady checks per
hour. Do not copy fixed counts into documentation.

### Bundled MLX embeddings

- The lightweight host lifecycle proxy remains listening while its MLX child is
  loaded on demand.
- A completed request resets the ten-minute idle timer. When the timer expires,
  the proxy gracefully stops the owned MLX child and releases model memory.
- Cold model load has no wrapper deadline while the owned child remains alive.
  During `make up-full`, this wait stays in the foreground, prints the lifecycle
  log path, and can be cancelled with Ctrl-C.
- Once the child is ready, each proxy-to-child request has a five-minute blocked
  socket timeout. The embedding shim has its own 540-second upstream request
  timeout beneath Onyx's 600-second model-server read timeout and never retries
  a POST.
- A request arriving during idle unload waits for unload to finish and starts a
  new child. It is not dropped or replayed.
- Normal proxy shutdown stops accepting new connections, drains accepted
  requests with an active child, and then stops the child. An incomplete cold
  start may be cancelled during shutdown.
- Parent ownership uses an absolute command identity, random per-launch token,
  and configuration fingerprint. The live proxy's in-memory `Popen` object is
  the only child ownership authority; the child port and advertised model are
  validated without a durable child PID record.
- `embedserv/host_process_manager.py` supplies the shared detached start/readiness/record/
  stop mechanics for this proxy and the Podman document server. It never signals
  a PID based only on a record number. Service-specific peer, model, child, and
  content-policy checks remain in the services themselves.
- Lite mode selects neither host process. Docker full mode uses the manager only
  for the bundled default MLX endpoint. A custom transition first applies only
  the replacement configured embedding authority, proves bridge and replacement
  readiness, and only then stops a recorded live proxy. The host document
  server is Podman full-only.
- An untracked top-level listener on 3210 and an orphaned child on loopback
  3211 are distinct manual-recovery failures. Neither authorizes PID guessing
  or automatic signaling; an occupied 3211 fails before 3210 binds.

### Onyx background work

- Connector, user-file, and secondary-index port-migration discovery schedules
  run every five minutes. Newly eligible work can therefore wait roughly five
  minutes before discovery.
- Housekeeping remains explicit: checkpoint cleanup hourly, index-attempt
  cleanup every 30 minutes, and hierarchy fetching hourly.
- Monitoring, periodic process-memory logging, Redis Beat-heartbeat, Craft
  cleanup/dispatch, and unsupported scheduled-task producers are absent. The
  separate indexing-child memory observer and indexing allocation tracer remain
  request-scoped upstream diagnostics and are no-ops under the wrapper's
  default zero-valued limits.
- The monitoring and scheduled-task workers are absent because no supported
  retained task targets those queues. Slack and Discord workers are default-off
  explicit options.
- Retained worker concurrency is bounded: primary 2, light 4, heavy 2,
  doc-processing 2, user-file-processing 1, and document-fetching 1. The
  doc-processing worker consumes both the live `docprocessing` queue and the
  secondary-index `port` queue.
- Only one secondary-index port attempt may run concurrently. This preserves
  one of the two doc-processing slots for ordinary indexing while a migration
  is active; the discovery task does not create a second simultaneous attempt.
- Retained Celery workers run without event heartbeat or gossip. Their upstream
  file-liveness bootstep is disabled.
- Beat reloads the materialized schedule every five minutes. The upstream
  `DynamicTenantScheduler.tick()` implementation is not replaced: its local
  marker represents event-loop liveness, while schedule-refresh failures remain
  logged application errors.
- A stdlib-only watchdog checks Beat's marker every five minutes and restarts
  only Beat after the 20-minute startup grace and two missing observations or a
  stale marker. It does not enqueue Redis heartbeat work or import the Onyx
  application bootstrap.
- Exact supervisor and watchdog control processes and SearXNG's exact
  multiprocessing resource tracker skip application `sitecustomize` imports.
  Beat, workers, indexing children, the SearXNG parent, and request workers keep
  their strict patches.

The supervisor transformation and schedule transformation are both retained:
one controls consumers/processes and the other controls task production. They
are not duplicate enforcement.

### Onyx API

- The synchronous and asynchronous API database engines each use a base pool
  of five with up to 15 overflow connections. This still accommodates Onyx's
  20-connection startup warmup per engine, while overflow connections close
  after warmup instead of leaving 40 idle connections resident.
- The read-only database engine retains two base connections and its upstream
  burst overflow behavior.
- AnyIO's API thread pool is capped at 12 workers. This bounds synchronous
  endpoint and streaming-generator concurrency without changing request
  semantics or adding a queue outside AnyIO's normal limiter.

### Onyx Craft

- Craft remains explicitly disabled with `ENABLE_CRAFT=false`. Ordinary chat's
  `run_python` tool uses the standalone code-interpreter service; Craft is a
  separate OpenCode-based environment with persistent per-user sandboxes.
- Enabling the Docker Craft backend would add an always-running sandbox proxy,
  Docker-socket access for the API/background/proxy tier, a dedicated scheduled-
  task worker, periodic dispatch and sandbox-cleanup work, persistent sandbox
  volumes, and one sandbox container for each active user environment.
- A Docker Craft sandbox defaults to a one-CPU limit and a 2 GiB memory limit
  and remains eligible for reuse until the one-hour idle timeout. Limits are not
  reservations or proof of steady consumption, but multiple retained user
  sandboxes make Craft's potential CPU, memory, storage, and background-work
  footprint materially larger than the request-scoped Python executor.
- Craft is not a supported wrapper option. Enabling it requires a deliberate
  design and validation of its Docker/Kubernetes sandbox backend, proxy and
  credential boundary, VPN/egress topology, per-user concurrency and storage,
  cleanup/failure recovery, Docker and Podman behavior, and explicit resource
  policy. It must not be enabled by setting the environment flag alone.

### SearXNG and browser search

- The default search set contains only the five supported custom offline
  engines: Google, Brave, DuckDuckGo, Startpage, and Bing through the shared
  direct Obscura path.
- Inherited stock engines are removed rather than merely disabled one by one.
- Optional SearXNG plugins are omitted, preventing unused hooks and mutable
  ClearURLs rule retrieval at cache startup.
- Provider admission remains atomic before worker dispatch, with the existing
  per-provider concurrency/cooldown policy.
- Obscura uses one process with isolated per-WebSocket browser state instead of
  a process worker pool. Fifteen live connections cover ten direct `open_url`
  attempts plus five independently leased search providers; excess connections
  fail with HTTP 503 rather than queueing. Each connection has one response
  stream slot.
- Search DOM limits and the configured built-in `open_url` document limit remain
  separate because they bound different request paths.

### Storage and indexing

- OpenSearch uses a fixed 512 MiB heap, four configured processors, disabled
  Performance Analyzer, disabled Query Insights top-N collection, monthly
  body-free audit initialization, and zero replicas for newly created Onyx
  indices.
- These are static startup/current-volume settings. There is no administrative
  sidecar, runtime cluster mutation, or automatic existing-volume migration.
- MinIO uses the `slowest` scanner profile.
- API and background file logging is disabled. Their application logs use
  container stdout/stderr and the existing bounded engine log policy instead
  of also accumulating overlapping rotating files in `/var/log/onyx`.
- The embedding shim `/health` is process-only and never loads a model. The
  staged `/ready` request owns startup inference validation.
- Web Connector discovery may be delayed by the five-minute background cadence,
  but an active indexing operation retains Onyx's normal connector/indexing
  failure reporting and task-recovery behavior.

### Optional Myst routing

- Myst VPN is disabled by default. In explicit no-VPN mode `netns-holder`
  continues to own the stable routing namespace, while the Myst container runs
  only an inert readiness sentinel. It starts no Myst daemon or route-
  reconciliation loop, requires no wallet or tunnel, and never arms VPN
  recovery.

- Route exemptions and WireGuard MTU are reconciled every 20 seconds, preserving
  hostname-DNS refresh and the repair bound.
- Exact target/gateway/device and MTU matches are silent no-ops. Writes and
  success logs occur only for missing or drifted state.
- A successful connection performs one immediate reconciliation. The background
  loop is the single ongoing owner; status polling and provider-selection
  branches do not repeat the same operations.
- Docker and Podman use the same socket-free Myst health supervisor. It stays
  unarmed until the first complete VPN readiness success in the current
  container lifetime, resets its monotonic failure window on every success,
  and requests one graceful PID-1 restart only after 60 seconds of continuous
  later failure. Explicit no-VPN mode and initial connection failures never arm
  recovery. The engine healthcheck remains the only periodic readiness owner.

### Podman shared state and host documents

- A persistent engine marker prevents Docker and Podman from concurrently
  writing shared PostgreSQL, OpenSearch, MinIO, Redis, or file-system state.
  Live engine inspection separately protects first-use or unclaimed state.
  An unselected Podman API failure is skipped only when its default machine
  positively reports that it is stopped; ambiguous inspection failures still
  fail closed.
- Full-mode embedding startup remains two-stage so a failed readiness request
  cannot replace a working API/background tier.
- macOS Podman serves the configured document source from a wrapper-owned host process
  through a fixed, capability-free relay. The source is not copied into the VM.
- The host server validates document-root confinement, refuses symlinks, rejects
  non-loopback peers before HTTP parsing, and bounds connections. The shared
  host manager handles only its generic lifecycle and ownership record.

## Implementation map

- Health cadence and aggregation: `docker-compose.yaml`,
  `compose_overlays/` mode/engine layers, `Makefile`,
  `podman/startup_health.py`, and `tests/health_inventory.py`.
- MLX lifecycle and host-process ownership:
  `embedserv/idle_embedding_proxy.py`,
  `embedserv/host_process_manager.py`, `onyx/local_embedding_shim.py`, and
  their focused tests.
- Background schedules, workers, and Beat liveness:
  `onyx/background_entrypoint.py`, `onyx/beat_liveness_watchdog.py`,
  `onyx/patches/sitecustomize_background/`, and
  `tests/validate_pinned_background.py`.
- API database/thread pools and API/background file-log suppression:
  `docker-compose.yaml`, `compose_overlays/docker-compose.full.yml`, and the
  effective Compose resource tests.
- Craft absence and its removed workers/schedules: `docker-compose.yaml`, the
  background bootstrap and pinned-image validation, and the Compose/background
  resource tests.
- Search-engine and bootstrap reduction: `searxng/core-config/settings.yml`,
  `searxng/patches/`, custom engines under `searxng/engines/`, and the
  SearXNG bootstrap/parser/scheduling tests.
- OpenSearch, MinIO, and full-mode storage settings:
  `compose_overlays/docker-compose.full.yml`, `docker-compose.yaml`, and the
  OpenSearch runtime validation suite.
- Myst reconciliation and recovery ownership: `myst/myst-client-entrypoint.sh`,
  `myst/route-reconciliation.sh`, `myst/myst-readiness.sh`, effective Compose
  health configuration, and the Myst readiness/reconciliation tests.
- Myst signup/payment ownership: the non-restarting setup mode,
  `myst/vpn_cli.py`, `myst/signup_guard.py`, and their deterministic
  identity/order/failure/long-pause tests. The integrated entrypoint performs
  no signup or order mutation.
- Podman shared-state and macOS host-document controls: Podman Compose overlays,
  `podman/shared_data_engine.py`, `podman/startup_health.py`, the shared host
  manager, and their deterministic tests.

When one of these controls changes, update this document and its focused tests
in the same change. Remove obsolete enforcement rather than retaining two
independent owners for the same periodic work or lifecycle decision.

## Regression protection

### Deterministic contracts

Run:

```sh
make check
```

The suite must cover:

- exact effective Compose health/dependency contracts;
- Podman capability placement, native startup-health translation, and staged
  configuration;
- Beat schedule cadence, absence of disabled producers/workers, retention of
  upstream `tick()`, and watchdog restart thresholds;
- shared host-process atomic records, ownership-token validation,
  configuration changes, readiness failure, untracked listeners, malformed
  records, and PID reuse;
- MLX idle unload, concurrent cold start, request-during-unload, five-minute
  socket timeout, normal live-proxy child cleanup, and fail-closed orphan
  refusal after a proxy crash;
- Myst no-op and drifted-route reconciliation;
- SearXNG exact engine/plugin configuration and all custom parsers;
- OpenSearch configuration and validation helpers.

### Pinned-image contracts

Run:

```sh
make test-patch-images
```

Pinned background validation lives in
`tests/validate_pinned_background.py`, not an embedded shell one-liner. It must
produce useful Python assertion locations for schedule, supervisor, liveness,
and Web Connector freshness drift. Continue to require the selected local
images; never pull or substitute an image from the validation target.

For an Onyx, code-interpreter, executor, or SearXNG image/source pin,
dependency lock, or runtime-patch change, run `make check` and
`make test-patch-images`. Tor-only and OpenSearch-only work instead adds its
corresponding focused image target. Use `make test-all-images`, or
`make check-upgrade` when `make check` is also required, only for changes
spanning multiple image families or broad release validation.

### Lifecycle and integration changes

For affected lifecycle changes, validate both engines where supported:

1. Render the selected lite and full models and run `make health-inventory`.
2. Start lite and full modes with the Makefile, inspect `make ps-*`, and verify
   nginx is the externally consumed WebUI health boundary.
3. Confirm Podman native startup checks use five-second startup intervals and
   retained regular checks use their documented steady intervals.
4. Exercise a real SearXNG query through every affected custom engine.
5. In full mode, index the doc-drop collection, extract a PDF, and run
   `internal_search`.
6. Exercise MLX cold load, concurrent requests, ten-minute idle unload, a request
   arriving during unload, and a down/up ownership cycle.
7. Fault the Obscura origin, a fixed gateway, Myst routing, Beat, and the MLX
   child; verify the documented aggregate health or recovery owner responds.
8. Confirm no direct-network, host-port, proxy, or shared-data-engine bypass was
   introduced.
9. For Myst, verify initial failure remains unarmed, a transient failure clears
   its timestamp, sustained post-readiness failure waits at least 60 seconds,
   and Docker/Podman both preserve the holder namespace and fail-closed bridges
   across the graceful restart.

## Resource-safety guardrails

- No hidden retries, fallback embeddings, empty-result substitutes, or direct
  egress paths.
- No inference in periodic health checks.
- No automatic OpenSearch migration or live administrative tuning.
- No MLX unload while requests are active.
- No short deadline for a live cold model load.
- No weakening of VPN, proxy, bridge, peer, PID, port, model, or shared-storage
  ownership validation in exchange for lower idle usage.
