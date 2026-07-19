# Power saving plan

We want to save power for laptop users by reducing health check frequency and eliminating many pointless and expensive periodic tasks.

Make the sleepy behavior the default. No power-saving preference or user-facing switch is warranted.

## Target behavior

After startup, in an idle default stack:

- Among container health and recovery loops, only local VPN recovery continues
  more often than every 10 minutes, and it runs once per minute rather than
  every 15 seconds.
- Ordinary container health checks run every 10 minutes.
- No health check performs inference, consumes API tokens, or accesses a GPU.
- No health check performs DNS resolution, connects to a public target, or
  contacts a configured upstream proxy. Synthetic online probes disclose that
  the VPN endpoint is running this stack in addition to consuming power.
- Redundant observational checks are removed.
- Onyx background discovery work runs every 5 minutes, and functional
  housekeeping runs every 10 minutes or longer.
- Pull-only background analytics, Celery event heartbeats/gossip, and unused
  file-based liveness writes are absent.
- Disabled features have neither scheduled jobs nor dedicated idle workers.

Correctness work that is conditional on an active request/task or owns a live
distributed lease is not governed by those idle-observability targets. The
primary-worker lock renewal, active indexing heartbeats and memory guard,
broker connection health, task cancellation, chat streaming heartbeats, and
enabled Slack failover heartbeat retain their required cadences unless a
separate fault-injection test proves a safe change.

During startup:

- Required dependencies are checked every 3–5 seconds using `start_interval`.
- The first successful check immediately marks the service healthy.
- Expensive end-to-end validation happens at most once per stack start.

## Authority, prerequisites, and non-goals

Read the subsystem documentation before implementation:

- `docs/vpn_routing_and_proxies.md` for Myst lifecycle, route classes,
  autoheal, upstream proxies, and fail-closed behavior;
- `docs/internal_network_security.md` for bridge boundaries and authoritative
  destination validation;
- `docs/local_docs_rag_search.md` for embedding-shim readiness and full-mode
  startup;
- `docs/onyx_patch_info.md` for background-bootstrap ownership; and
- `docs/onyx_patches_upgrade.md` for strict runtime-patch and pinned-image
  validation requirements.

The implementation must preserve the privacy properties in those documents.
A less frequent or less comprehensive health status must not introduce a
direct-network fallback. Application requests continue to rely on the actual
route and final-hop policy and must fail closed immediately even if Docker
still reports a stale `healthy` state.

Docker's `start_interval` requires Docker Engine 25.0 or later. The Compose
specification can carry the field, but that alone does not prove that the
selected engine schedules it. Make Engine 25.0 the documented Docker minimum,
verify the rendered `StartInterval` in `docker inspect`, and verify the
effective Docker and Podman models selected by the Makefile. Podman exposes a
separate startup-health model (`HealthStartup*`); do not assume that accepting
the Compose key gives Docker-equivalent behavior. A supported Podman path must
either demonstrate the fast-start/slow-steady transition in a live contract
test or use a narrow Podman-specific startup layer. Otherwise fail before
starting the stack with an actionable version/capability error.

Use the official [Dockerfile `HEALTHCHECK`
reference](https://docs.docker.com/reference/dockerfile/), [Compose
specification](https://compose-spec.github.io/compose-spec/spec.html), and
[Podman health/startup-health
reference](https://docs.podman.io/en/latest/markdown/podman-run.1.html) as the
engine-semantics authorities. Pin tests to supported runtime versions rather
than copying behavior from an incidental local engine.

`start_interval` applies only during `start_period`. A successful startup
probe changes Docker health from `starting` to `healthy`, after which the
ordinary interval applies; failures during the remaining nominal grace are no
longer free. Conversely, a permanently broken service is not reported
unhealthy until its start period expires. Do not give every service a blanket
10-minute grace. Preserve the existing service-specific grace where it is
already sufficient, and explicitly assign a short 30-120 second grace to fast
local listeners/gateways that currently have none. Retain the longer API and
Myst budgets only where their bounded startup paths justify them.

This plan does not add a user-facing power preference, read or edit the private
`.env.wrapper`, or change request routing. It may add explicit default-off
Slack and Discord feature switches to `.env.wrapper.example`; those select
optional functionality rather than a power profile, and contain no
credentials. Do not modify generated files under
`onyx/onyx_data/` by hand. Generated upstream files may only be refreshed by
the documented Makefile upgrade flow; wrapper behavior belongs in tracked
Compose layers, scripts, or strict runtime patches.

The source audit is pinned-artifact-sensitive. The local Onyx reference
worktree is at the wrapper's audited commit
`b7482a59fb74503d5ec3dcde0ae5beac7b4905ff`, but the local Teep reference
worktree is not at `TEEP_REF=6413fe0547b449e67f7296986fe8b8ffbc9bbcd2`
and does not currently contain that object. Before implementing or changing a
Teep health assumption, synchronize the read-only reference repository to the
committed pin (or inspect the exact built image/source archive) and record the
pin in the audit. Never infer pinned Teep behavior from the current reference
worktree. Apply the same exact-pin check to Myst, Obscura, SearXNG, autoheal,
and the generated Onyx deployment inputs.

## Container health-check plan

### VPN recovery: local state only, once per minute

Myst VPN readiness and autohealing remain more responsive than ordinary
health checks, but neither may generate Internet traffic:

- Myst health check: 1 minute
- Autoheal poll: 1 minute
- Remove the provider-DNS query and its synthetic `example.com` target.
- Disable autoheal’s separate 5-second `pgrep` health check. Docker already
  restarts the container if its main process exits, so that probe adds no
  recovery value.

Prefer a direct local TequilAPI request over launching the Myst CLI. The
pinned daemon exposes `GET http://127.0.0.1:4050/connection`; this reads the
local connection manager and returns the current connection state. The
existing `myst connection info` command is a heavier CLI wrapper around the
same API call. The health check should consume the response without logging
it and require exactly `status=Connected`, because the full response contains
provider, location, session, and identity metadata.

The complete recurring VPN probe should use only:

- the loopback TequilAPI connection status;
- existence of `myst0` with a global IPv4 address;
- the expected local `myst0` route shape; and
- the existing no-VPN local checks when `MYST_VPN_ENABLED=false`.

It must not query provider DNS, resolve a public hostname, fetch an IP-check
service, open a synthetic tunnel, or otherwise emit traffic through the VPN.
This deliberately means that autoheal detects daemon-reported disconnects and
local interface/route loss, but does not manufacture coverless traffic merely
to discover a remote black hole. A real request encountering such a failure
still fails closed. Investigate whether Myst already transitions its local
connection state after its own protocol keepalive or provider-session failure;
do not add wrapper-generated traffic if it does not.

With a one-minute health interval and one-minute autoheal poll, recovery may
take several minutes depending on the retry threshold. That is acceptable for
a background laptop stack and avoids continually advertising stack activity
from the selected VPN endpoint.

Use this Myst timing contract:

```yaml
interval: 1m
start_interval: 5s
timeout: 10s
retries: 2
start_period: 420s
```

Keep the existing 420-second startup grace because it is derived from the
bounded connection-attempt budget. After startup, two failed local probes plus
the one-minute autoheal poll give a roughly two-to-three-minute detection and
restart bound. Keep `AUTOHEAL_START_PERIOD=300`; it is independent of the
Docker health start period and prevents recycling Myst during initial setup.

### Keep, but make sleepy

For every retained ordinary health check, set the steady-state contract to:

```yaml
interval: 10m
start_interval: 5s
retries: 1
```

Retain a bounded service-appropriate timeout and assign a service-specific
`start_period`. Use 30-120 seconds for local listeners, bridges, databases, and
small HTTP services; retain the API server's effective wrapper-owned
240-second allowance and Myst's separately specified 420-second allowance.
Do not accidentally restore the generated upstream API definition's
600-second grace through Compose merge changes. One steady-state retry is
intentional: these checks do not trigger recovery, so waiting another 10 or 20
minutes to report an observational failure has no benefit. A first success
must switch to the 10-minute interval. A permanently failing fast service must
become unhealthy within its short documented startup grace, not after a
blanket 10-minute wait.

- Myst-dependent policy proxies
- Egress bridges
- Obscura readiness through its CDP gateway
- SearXNG readiness through its API gateway
- API server
- Web server
- Nginx
- Teep
- PostgreSQL
- MinIO
- Doc-drop origin and route gateway
- Tailscale frontend gateway
- Executor egress bridge

A ten-minute interval means runtime health status may be stale for roughly ten
minutes plus the probe timeout. That is acceptable for these services because
health status does not repair them or protect routing after startup. Actual
requests still succeed or fail immediately, and the routing paths remain
fail-closed.

Do not keep two recurring probes for the same local path merely because both
services currently define one:

- Disable the expensive in-container Obscura `fetch` health command. Change
  `obscura-cdp-gateway` to depend on Obscura being started and make the
  gateway's health request `/json/version` through its own listener. This one
  check covers the gateway, the Obscura listener, and the expected local HTTP
  response without spawning a second Obscura/V8-capable binary. Make
  SearXNG's startup depend on this aggregate readiness if ordering is required.
- Disable the separate SearXNG-core Python health process. Change
  `searxng-service-gateway` to depend on the core being started and retain its
  end-to-end local HTTP check as the sole SearXNG readiness check. Repoint any
  startup-only dependency that formerly waited on core health to the service
  gateway; leaf host publishing still does not need health.
- For full-mode doc-drop, change `doc-drop-route-gateway` to depend on
  `doc-drop-web` being started and let the gateway's HTTP check be the sole
  recurring readiness probe for the origin path. The host display publisher
  remains a leaf with no health check.

These dependency rewrites must not create a cycle. Validate the rendered
startup graph and prove that a missing or nonresponsive underlying process
keeps the aggregate gateway unhealthy during `compose up --wait`.

### Remove entirely

Remove health checks from leaf publishers or self-managing clients whose health is not consumed by `depends_on`, autoheal, or another recovery mechanism:

- Host WebUI publisher
- Host SearXNG publisher
- Host doc-display publisher
- Tailscale Funnel client

If their main process exits, `restart: unless-stopped` already handles it. If they remain alive but unusable, their current health status triggers no action.

### Embedding shim: eliminate periodic inference

Change its Docker health check permanently to the local, non-inference endpoint:

```text
GET /health
```

The existing `/ready` endpoint must never be called by Docker health monitoring.

For full-stack startup:

1. Start only `local-embedding-shim` and its Compose dependencies, gating the
   shim itself on local `/health`.
2. Before starting `api_server` or `background`, have a named `make up-full`
   helper invoke `/ready` exactly once from inside the shim with the current
   bounded timeout.
3. Only after that succeeds, run the ordinary full-stack
   `docker compose up -d --wait`.
4. Fail visibly if the one end-to-end embedding request fails, leave the
   already-started shim/routing dependency subset running for diagnosis, and
   do not start API/background against an unvalidated embedding backend.
5. Do not retry it periodically or cache a retry loop inside the container.

The ordering is essential. Calling `/ready` after the full `compose up` would
allow API and background initialization to race or fail against an embedding
backend that the current health dependency guarantees is ready first.
Implement the staged startup with the same effective full-mode Compose file
set and env-file arguments for both Compose invocations. Confirm that a
targeted `compose up ... local-embedding-shim` starts only the shim and its
declared dependencies. The outer `up-full` target must generate ephemeral
secrets, resolve images, and start any bundled host embedding server only once;
the second Compose phase must not recurse through prerequisites that repeat
those side effects. Invoke `/ready` once with `compose exec -T`; do not use
`compose run` or a persistent init-service container whose previously exited
success could be reused on a later stack start. `up-lite` must never invoke the
helper. Do not add a restarting init container or shell retry loop, because
either can turn a failed paid endpoint into unbounded repeated spend.

This gives startup validation without keeping the model warm, waking the GPU, or spending API tokens indefinitely. Subsequent real embedding failures will surface through the actual indexing or search operation.

### Final-hop proxy probes

The current proxy health command starts Python and resolves `example.com`.
Remove that behavior completely rather than merely making it less frequent.
Both recurring proxy health and startup readiness must avoid synthetic online
traffic:

- Add a cheap listener-only health command for Docker’s recurring check.
- Validate static configuration, listener availability, and local namespace
  preconditions without resolving a public hostname.
- Do not periodically connect to a configured upstream HTTP/SOCKS proxy merely
  to prove that it accepts a connection or credentials.
- Do not use a public canary target during startup. The first real request is
  the appropriate end-to-end validation of DNS, VPN, and configured-upstream
  behavior, and it already fails closed if that path is unavailable.
- Keep Myst responsible only for the local, offline VPN state checks described
  above.

The recurring proxy check should prove only that the final-hop process is
alive, its listener accepts a local connection, and its startup-validated
configuration remains loaded. Bridge checks may continue proving that a
policy-rejected local `CONNECT` receives the expected 403 because that request
does not leave the Docker/VPN namespace. No health or readiness check should
produce DNS or upstream-network traffic.

### Exact Compose disposition

Audit the effective lite and full models, including VPN/no-VPN, configured
upstream proxy, Teep-through-VPN, Tailscale-through-VPN, executor networking,
and Tailscale profiles. The intended steady-state disposition is:

| Service or service class | Steady interval | Action |
|---|---:|---|
| `myst-client` | 1m | Replace with offline TequilAPI/interface/route probe; use two failures before unhealthy. |
| `autoheal` loop | 1m | Set `AUTOHEAL_INTERVAL=60`; disable the image's inherited 5s `pgrep` health check with `healthcheck: {disable: true}`. |
| Public/host final-hop proxies | 10m | Replace `--check-ready` with a local-only listener/configuration check. |
| Obscura/public/host/executor egress bridges | 10m | Keep the local policy-rejection check; it must never leave the namespace. |
| Obscura | none | Remove the expensive in-container `fetch` probe; aggregate readiness moves to the CDP gateway. |
| Obscura CDP gateway | 10m | Replace socket-open-only health with a local `/json/version` request through the gateway. |
| SearXNG core | none | Remove its duplicate Python health process; aggregate readiness moves to the service gateway. |
| SearXNG service gateway | 10m | Keep one end-to-end local HTTP check through the gateway. |
| API, Web, and nginx | 10m | Keep local HTTP checks needed by startup dependencies. |
| Teep | 10m | Keep its local `/health` check; audit the Teep-VPN gateway overlay too. |
| PostgreSQL | 10m | Override the inherited upstream 10s check in `docker-compose.yaml`. |
| MinIO | 10m | Keep its local readiness check in the full overlay. |
| Doc-drop origin | none | Remove duplicate origin health; aggregate readiness moves to its route gateway. |
| Doc-drop route gateway | 10m | Keep one end-to-end local HTTP check through the gateway. |
| Local embedding shim | 10m | Use only `/health`; never `/ready`. |
| Tailscale frontend gateway | 10m | Keep because Funnel startup depends on it. |
| Host WebUI, host SearXNG, host doc, host Teep publishers | none | Remove health checks; process exit remains covered by restart policy. |
| Tailscale Funnel | none | Remove observational health; Tailscale manages its own reconnection. |

Use this as a completeness checklist, not merely the service list in the base
file. Health checks also exist in `docker-compose.full.yml`,
`docker-compose.code-interpreter-network.yml`, `docker-compose.teep-vpn.yml`,
`docker-compose.tailscale-vpn.yml`, `docker-compose.vpn-autoheal.yml`, and the
extended Myst and upstream Onyx definitions.

The main implementation locations are:

- `docker-compose.yaml` for base services and inherited-health overrides;
- `docker-compose.full.yml` for RAG, MinIO, doc-drop, and embedding;
- the optional Compose overlays named above;
- `myst/docker-compose.yaml`, `myst/myst-readiness.sh`, and
  `docker-compose.vpn-autoheal.yml` for VPN recovery; and
- `egress/final_hop_proxy.py` for the proxy's local-only health command.

For Myst, feed the loopback TequilAPI response directly into a status matcher.
Do not print, persist, or interpolate the full JSON into an error message. Unit
tests must provide fake `wget` and `ip` executables and prove that no `dig`,
resolver, or other online command is invoked. Keep the existing no-VPN
stale-`myst0` and default-route behavior.

The pinned Myst image has BusyBox `wget` with explicit proxy control. Use the
equivalent of:

```sh
wget -Y off -q -T 5 -O - http://127.0.0.1:4050/connection
```

Pipe it directly to a matcher anchored at the beginning of the top-level JSON
object, equivalent to
`^[[:space:]]*\{[[:space:]]*"status"[[:space:]]*:[[:space:]]*"Connected"[[:space:]]*[,}]`.
The pinned Go DTO serializes `status` first; assert that exact pinned contract
against the Myst source/image. A loose search for any nested `status` field is
not sufficient. `-Y off` is required
even for loopback so an inherited proxy environment can never turn health into
an upstream request. Do not hold the response in a shell variable. Treat an
unavailable API or any response that does not contain the exact expected
`Connected` status field as unhealthy without echoing the response. Because
the endpoint is a trusted loopback API and the runtime image does not include
a JSON parser, keep the matcher narrow and cover malformed/truncated responses
with negative tests rather than installing another periodic-process
dependency solely for health.

Replace the vague route-presence test with a deterministic kernel-only route
lookup. After reading the first global `myst0` address, require `ip -4 route
get 198.51.100.1` to select `dev myst0` and the same `src` address. This TEST-NET
lookup sends no packet and performs no DNS, while proving that an ordinary
public route selects the tunnel rather than merely proving that some route is
attached to the interface. Characterize the pinned Myst route output first and
add a different local-only invariant only if the supported provider route
shape demonstrably cannot satisfy this one.

For the final-hop proxy, split the existing readiness logic rather than
weakening destination validation used by real requests. The Docker health
entry point should import and validate configuration, connect only to its own
loopback listener, and exit. Remove readiness-only calls to
`_resolve_target_host`, `_probe_http_proxy_endpoint`, and
`_probe_socks5_proxy_endpoint`; those helpers may remain if real request paths
need them. Tests must patch DNS and upstream socket-open functions to raise if
a local health invocation reaches them.

## Onyx background plan

The current self-hosted schedules should be rewritten by the strict background bootstrap. This should be the wrapper’s default, not configurable.

Audit the exact pinned image and these matching reference sources together;
the reference paths are evidence, not substitutes for image-contract tests:

- `backend/onyx/background/celery/tasks/beat_schedule.py` and
  `backend/onyx/background/celery/apps/beat.py` for materialized schedules and
  scheduler reload;
- `backend/onyx/background/celery/apps/app_base.py`, `apps/primary.py`, and
  `memory_monitoring.py` for worker bootsteps, startup gates, leadership, and
  active indexing safeguards;
- `backend/onyx/background/celery/tasks/monitoring/tasks.py` and
  `backend/onyx/server/metrics/indexing_pipeline*.py` for the monitoring
  producer, collectors, and event receiver;
- `backend/supervisord.conf` and
  `backend/onyx/utils/supervisord_watchdog.py` for process topology and Beat
  recovery; and
- `backend/onyx/onyxbot/{slack,discord}/` for dormant and active bot loops.

### Work discovery: every 5 minutes

Change these from 15–20 seconds to 5 minutes:

- Indexing discovery
- User-file processing
- User-file project synchronization
- User-file deletion
- Connector deletion
- Vespa/OpenSearch metadata synchronization
- Connector pruning

The trade-off is that unattended work may take up to five minutes to begin. Interactive chat is unaffected. For a local laptop stack, that is a much better default than waking PostgreSQL, Redis, Celery Beat, and several workers every few seconds.

A future improvement would enqueue work directly when an API action creates it, leaving these schedules only as slow reconciliation jobs. Until then, five minutes is the simple reliable choice.

Implement this as a generalized strict sleepy-schedule function in
`onyx/patches/sitecustomize_background/sitecustomize.py`, replacing the
current cleanup-only function rather than adding an independent loose patch.
It must match schedules by exact name in Onyx's `beat_schedule.py` and replace
only their `timedelta` values:

| Exact schedule name | Pinned upstream cadence | New cadence |
|---|---:|---:|
| `check-for-user-file-processing` | 20s | 5m |
| `check-for-user-file-project-sync` | 20s | 5m |
| `check-for-user-file-delete` | 20s | 5m |
| `check-for-indexing` | 15s | 5m |
| `check-for-connector-deletion` | 20s | 5m |
| `check-for-vespa-sync` | 20s | 5m |
| `check-for-pruning` | 20s | 5m |

Fail startup if an expected name is absent, duplicated, or no longer has the
expected upstream cadence. This makes an Onyx upgrade surface schedule drift
instead of silently restoring aggressive polling.

### Remove the self-hosted monitoring pipeline

The pinned self-hosted monitoring pipeline is entirely observational in this
Compose wrapper and has no bundled Prometheus scraper or exposed monitoring
port. It must be removed as a unit rather than retaining expensive pieces that
only become visible in logs or optional telemetry (which the wrapper disables):

1. `monitor-celery-queues` runs every 10 seconds. Its helper performs 20
   queue-length reads plus two scans of unacknowledged tasks and writes one
   large log line.
2. `monitor-background-processes` runs every 5 minutes. It scans every mapped
   queue, queries recent connector/index-attempt and sync state from
   PostgreSQL, reads/writes Redis emission markers, logs every metric, and then
   calls disabled optional telemetry.
3. `monitor-process-memory` runs every 5 minutes. It scans supervisor-managed
   processes and calls `psutil.Process.cpu_percent(interval=0.1)` for each,
   deliberately blocking for 100 ms per sampled process before writing local
   analytics logs.
4. The monitoring worker starts a Prometheus server and registers
   `QueueDepthCollector`, `RedisHealthCollector`, and `WorkerHealthCollector`.
   Uncached Redis-health scrapes issue `INFO memory` and `INFO clients`; queue
   scrapes inspect all queue lengths, unacknowledged sets, and oldest messages.
5. Independently of scrapes, `WorkerHeartbeatMonitor` holds a Celery event
   receiver open. The ordinary Celery workers emit event heartbeats for that
   monitoring plane by default.

Delete the three self-hosted Beat entries from the already materialized
`beat_schedule.tasks_to_schedule` list. Require exactly one match for each
name/task ID and its pinned cadence (`10s`, `5m`, and `5m` respectively), then
prove none remains. Remove the `celery_worker_monitoring` supervisor program
and its log-tail input in the same strictly validated derived-supervisor
configuration used for disabled Craft. Do not import or patch the monitoring
task/collector modules merely to make them no-ops: with no scheduled producer,
no monitoring worker, no scraper, and no published endpoint, they are dead
code in this deployment and importing them adds avoidable startup work.

The removal must be based on the self-hosted `MULTI_TENANT=false` contract and
must fail if another functional task is routed to `OnyxCeleryQueues.MONITORING`
in the pinned image. Cloud-only task templates are not running here, but audit
them during upgrades so a renamed self-hosted task cannot evade the check.
Keep the underlying queue primitives unchanged; task dispatch, fence
validation, stuck-work recovery, and other correctness paths use them.

Explicit queue, Redis, process-memory, and worker-status inspection may remain
available as operator-invoked diagnostics. It must run only on demand: do not
add a replacement timer, dashboard refresh, background thread, health check,
or user-facing preference that resumes the monitoring pipeline.

### Functional housekeeping at 10 minutes or longer; Beat recovery at 5 minutes

| Exact schedule name | Pinned upstream cadence | New cadence |
|---|---:|---:|
| `celery-beat-heartbeat` | 1m | 5m |
| `check-for-checkpoint-cleanup` | 1h | unchanged |
| `check-for-index-attempt-cleanup` | 30m | unchanged |
| `check-for-hierarchy-fetching` | 1h | unchanged |

The source audit also found conditional sub-five-minute templates that are
absent under this wrapper's committed configuration. Validate their absence
rather than silently ignoring them:

| Conditional schedule | Pinned cadence | Required wrapper state |
|---|---:|---|
| `check-for-doc-permissions-sync` | 30s | absent because paid EE/license enforcement is disabled |
| `check-for-external-group-sync` | 20s | absent because paid EE/license enforcement is disabled |
| `check-for-auto-llm-update` | configured seconds | absent because `AUTO_LLM_CONFIG_URL=""` |
| `migrate-chunks-from-vespa-to-opensearch` | 2m | absent because the effective pinned background configuration has `ONYX_DISABLE_VESPA=true` |

Fail startup if one unexpectedly materializes. Leave other daily/hourly
functional cleanup unchanged unless the exact pinned-source audit finds
another sub-five-minute self-hosted task. Any newly discovered task must be
explicitly classified before implementation; do not apply an indiscriminate
global multiplier.

`DynamicTenantScheduler.RELOAD_INTERVAL` is a separate one-minute schedule
poll that the original plan missed. On each reload it queries tenant state,
reads the Beat multiplier, rebuilds and compares the entire schedule, writes
an informational log even when unchanged, and touches a liveness file. Set the
strictly validated self-hosted reload interval to 5 minutes. The discovery
delay contract is therefore the greater of schedule activation and reload
timing; tests must prove a newly materialized eligible task is dispatched
within the documented bound. Remove the Beat liveness-file touch because the
Compose deployment has no consumer for that file.

The beat heartbeat and watchdog must be changed as one contract:

- Heartbeat: every 5 minutes
- Redis value: the heartbeat's Unix timestamp rather than the current constant
  `1`
- Redis key TTL: 30 minutes, longer than the stale threshold so a newly
  restarted watchdog can still measure the last actual heartbeat
- Watchdog check: every 5 minutes
- Watchdog stale threshold: 20 minutes
- Missing or malformed key: require two consecutive observations and apply the
  same 20-minute startup/last-valid-heartbeat grace; Redis connection errors do
  not count as proof that Beat is stale

The current existence-only key cannot implement a 20-minute age contract: it
expires after 10 minutes, while the watchdog measures from the last time it
observed the key rather than from the last heartbeat. Store and strictly parse
the actual timestamp so the expected stuck-Beat restart bound is roughly
20–25 minutes after the last successful heartbeat, plus task-queue delay if
the primary queue is already busy. Reject nonnumeric and implausibly future
values without logging the raw value. State the measured bound rather than
claiming it until the live fault-injection test establishes it.

The upstream watchdog constants are currently hard-coded in
`onyx/utils/supervisord_watchdog.py`, and the heartbeat value/TTL is hard-coded
in the Celery task. Do not claim these are environment-configurable. Use a
narrow, startup-validated wrapper replacement or patch, cover it with
pinned-image installation tests, and document the chosen ownership in
`docs/onyx_patch_info.md` and `docs/onyx_patches_upgrade.md`.

### Remove unused Celery event and file-liveness traffic

The Compose background container does not define or consume per-worker file
probes, but every Celery worker installs Onyx's `LivenessProbe` bootstep and
touches a file every 15 seconds. With the pinned eight-worker supervisor this
is 32 filesystem writes per minute before any work runs. Remove that bootstep
for this wrapper's Compose background runtime after strictly validating that
`get_bootsteps()` contains only the pinned `LivenessProbe`. Do not change the
upstream Kubernetes charts or their probe behavior.

Celery also sends a `worker-heartbeat` event at its default two-second cadence.
This is event-monitoring traffic, not the broker protocol keepalive and not the
index-attempt heartbeat used for stuck-work recovery. Once the monitoring
worker and `WorkerHeartbeatMonitor` are removed, add `--without-heartbeat` and
`--without-gossip` to every retained Celery worker command in the derived
supervisor configuration. This removes roughly 30 emitted heartbeat events per
worker per minute plus cross-worker gossip consumption. Keep broker connection
health, remote-control/pidbox behavior needed for task revocation, the primary
worker leadership lock, Beat's separate watchdog heartbeat, and active
index-attempt database heartbeats unchanged.

Use Celery's official [event-heartbeat
reference](https://docs.celeryq.dev/en/v5.5.2/reference/celery.worker.consumer.heart.html)
to preserve that distinction, but verify the CLI flags against the exact
Celery version installed in the Onyx image.

Validate this distinction against the Celery version installed in the pinned
Onyx image. A live cancellation/revocation test and worker-restart/redelivery
test are required before accepting the flags; no task-loss or stuck-attempt
recovery behavior may depend on the removed event plane.

### Completely remove disabled Craft work

When `ENABLE_CRAFT=false`:

- Remove `cleanup-idle-sandboxes`
- Remove `dispatch-due-scheduled-tasks`
- Remove `cleanup-stuck-scheduled-runs`
- Do not launch the dedicated scheduled-tasks Celery worker

The current patch bug must be fixed by deleting Craft entries from the already-materialized `tasks_to_schedule` list, not only from `beat_task_templates`. It should strictly verify that all expected entries existed and that none remain.

The patch must inspect both lists after importing the module because Onyx
constructs `tasks_to_schedule` during import. Require exactly one copy of each
Craft template upstream, remove the three names from both lists where present,
and verify the effective result returned by `get_tasks_to_schedule()`. Extend
the pinned-image test to instantiate the actual versioned Beat application;
the current live failure occurred even though the bootstrap printed a success
message, so a source-text assertion is insufficient.

Supervisor-level handling is shared by the Craft, monitoring, Celery-event,
and bot changes. Add one tracked wrapper background entrypoint and mount it only
on the full-mode `background` service. It should use Python's
non-interpolating INI parser to read the image's
`/etc/supervisor/conf.d/supervisord.conf`, require the exact pinned program and
log-tail sections, make only the classified transformations, write a temporary
derived configuration, and `exec` supervisord with that file. It must:

- remove `program:celery_worker_scheduled_tasks` and its log tail when Craft is
  disabled;
- remove `program:celery_worker_monitoring` and its log tail unconditionally
  for this self-hosted wrapper after the effective schedule proves the
  monitoring queue has no producers;
- add `--without-heartbeat --without-gossip` exactly once to every retained
  Celery worker command; and
- apply the explicit bot policy below.

Fail startup on missing, duplicate, or structurally changed sections or
commands. Do not use `sed`, rewrite the generated repository copy, or maintain
an unchecked full copy of the upstream supervisor file. Preserve the upstream
background command's custom-CA installation step and process/signal semantics
when replacing its entrypoint; add an image-contract assertion for those
commands so an upstream bootstrap change fails visibly.

### Remove dormant bot polling and right-size worker pools

The pinned supervisor launches eight Celery workers, not nine, plus Slack and
Discord processes. The dormant bots are active pollers even with no bot
configured: Discord queries PostgreSQL every 5 seconds for a token, while
Slack scans tenants/configuration every minute and runs a 15-second Redis
heartbeat loop (the latter has no tenants to write when dormant but still
wakes). Credentials for both can be stored in PostgreSQL, so absence of an env
token is not a valid disable signal.

Add explicit `ONYX_SLACK_BOT_ENABLED=false` and
`ONYX_DISCORD_BOT_ENABLED=false` wrapper feature switches to
`.env.wrapper.example` and full-mode Compose. When false, the derived
supervisor configuration removes the corresponding program and log-tail
entry, so the default has no bot interpreter or polling thread. When true,
preserve database-managed credentials and Admin UI configuration. Document
that an existing bot user must opt in after this change; do not inspect the
private database from the entrypoint and do not infer future configuration by
polling.

For explicitly enabled bots, reduce configuration/cache discovery without
weakening active connection ownership:

- Discord missing-token checks: 10 minutes instead of 5 seconds.
- Discord active cache refresh: 5 minutes instead of 1 minute; command-driven
  single-guild refresh remains immediate.
- Slack tenant/token acquisition: 10 minutes instead of 1 minute.
- Slack's 15-second heartbeat remains unchanged only while it owns at least
  one active tenant because its 60-second expiration is a failover contract.
  Change the loop so the no-tenant case waits on the slower acquisition event
  and performs no Redis heartbeat work.

Apply these as exact, startup-validated constant/control-flow patches only
when the corresponding bot feature is enabled. Test dynamic database token
addition/removal with the documented delay and verify active Slack/Discord
message handling.

Finally, set conservative wrapper-owned full-mode Celery concurrency defaults
appropriate to one laptop user (proposed: primary 2, light 4, heavy 2,
docprocessing 2, user-file 1, docfetching 1) instead of upstream's primary 4,
light 24, heavy 4, docprocessing 6, user-file 2, and docfetching 1. Keep these
as explicit Compose values rather than hidden patches. Before finalizing the
numbers, run representative multi-document PDF ingestion and connector sync,
measure throughput/memory, and verify that no task's correctness assumes the
upstream pool size. The two removed monitoring/scheduled-task workers must not
have concurrency settings left behind.

### Recurring work audited and deliberately retained

Do not treat every interval-looking constant as an idle timer. The source
audit found the following recurring or polling paths, but they should remain
unless their stated scope changes:

- Redis's `health_check_interval=60` is checked opportunistically before a
  connection command after an idle period; it does not create a background
  once-per-minute task by itself. Keep it as broker/cache connection safety.
- The primary-worker lock renews every 15 seconds while the primary worker is
  alive. It is leadership correctness, not observability; slowing it changes
  failover and split-brain bounds.
- The 30-second index-attempt heartbeat and 15-second near-limit memory
  observer exist only inside active spawned indexing work. Keep the heartbeat
  for stuck-attempt recovery and the memory observer for the configured worker
  memory limit. Neither should run in a genuinely idle stack.
- Task cancellation/fence polling, chat and image-generation streaming
  heartbeats, and sandbox readiness polling exist only for an active request
  or operation. Preserve them.
- Browser polling found in connector/indexing/credential admin pages and the
  three-second Projects upload loop is page- or progress-scoped. SWR does not
  refresh hidden tabs by default, the upload loop stops when its tracked set is
  empty, and authentication refresh is already visibility-gated. Do not patch
  generated WebUI code in this wrapper merely to change those interactive
  cadences. Re-audit if an effective pinned build shows one of these loops is
  globally mounted, continues while hidden, or continues after work becomes
  terminal; that would be a separate reduction candidate.
- Release-note/health reads are request- or page-driven in the pinned WebUI,
  not a server-side Beat job. Retain the repository's documented local
  administrative analytics and release-note behavior.

Add an idle assertion for each conditional item above: no index heartbeat or
memory observer without an active attempt, no chat/task polling without an
active operation, and no bot or upload loop after its owner is absent. This
prevents a future upgrade from turning a deliberately retained conditional
loop into unclassified idle work.

## Expected result

Approximate steady-state changes:

| Source | Current | Proposed |
|---|---:|---:|
| Container health/recovery checks | ~73/min | ~4/min |
| Non-VPN container checks | ~65/min | ~2/min |
| Onyx Celery scheduled jobs | ~32/min | ~2/min |
| Embedding health inferences | 120/hour | 0/hour |
| Craft jobs while disabled | 3/min | 0 |
| Self-hosted monitoring jobs | ~6.4/min plus scrape-triggered work | 0 |
| Queue-observability Redis operations | at least ~136/min, plus uncached Prometheus scrapes | 0 |
| Celery worker event heartbeats | about 240/min across eight pinned workers | 0 |
| Unconsumed Celery liveness-file writes | about 32/min across eight pinned workers, plus Beat | 0 |
| Dormant bot database/configuration polls | Discord 12/min plus Slack 1/min | 0 by default |
| Supervisor Celery workers | 8 | 6 |

About two of the remaining container checks per minute would be the local Myst
health and autoheal loops. Everything else would wake approximately once per
ten minutes. None of these checks should emit public DNS or upstream traffic.

The two container-health rows are provisional estimates and must not be used
as acceptance criteria until generated from every effective Compose model.
Add a deterministic inventory that lists each effective health command,
steady/start interval, profile, and checks per minute, and have the plan's
final measured table come from that artifact. Count disabled profiles and
duplicate aggregate/core probes correctly. Likewise, confirm Celery event
counts from Redis command/event deltas on the exact pinned image rather than
assuming the nominal two-second default survived Onyx configuration.

## Deterministic tests and documentation updates

Add or extend deterministic coverage before live validation:

- `tests/test_myst_readiness.py`: add VPN-mode cases with fake `wget` and `ip`
  commands for connected, disconnected, missing-interface, missing-address,
  route-device/source mismatch, malformed/duplicate/nested status fields, and
  missing-route states. Put failing fake `dig`, resolver, and socket tools on
  `PATH` and assert they are never invoked. Assert stdout/stderr never contains
  provider, identity, session, or location fields returned by the fake
  TequilAPI.
- `tests/test_restricted_egress_proxy.py`: replace the tests that currently
  expect `example.com` DNS or upstream-proxy authentication probes. Assert the
  local readiness path opens only `127.0.0.1:LISTEN_PORT`, and make patched DNS
  plus upstream-connect helpers fail the test if called.
- `tests/test_onyx_network_isolation.py`: use its existing effective Compose
  model helper to assert intervals, `start_interval`, retry counts, removed
  and aggregate health checks, rewired acyclic dependencies, the inherited
  PostgreSQL override, autoheal health disable, staged embedding target, and
  every optional overlay. Add a generated health inventory with computed
  steady-state check rates. Do not rely only on source-text matching.
- `tests/test_local_embedding_shim.py`: prove `/health` never calls
  `request_local_embeddings`; retain `/ready` tests because the endpoint still
  serves the one-shot startup validation.
- Add a Makefile contract test, or extend
  `tests/test_myst_lifecycle_makefile.py`, to prove only `up-full` invokes the
  one-shot embedding helper, starts only the shim/dependencies first, invokes
  one `/ready` request with no retry loop, and starts API/background only after
  success. Exercise failure and repeated `make up-full` calls so an old
  successful result cannot be reused.
- Replace the Craft source-text assertion in
  `tests/test_obscura_direct_compose.py` with focused background-patch tests.
  Also extend `tests/validate_pinned_patch_images.sh` so the actual pinned
  image's versioned Beat application contains the expected sleepy schedule
  and no disabled-Craft entries.
- In focused background tests, assert that all three self-hosted monitoring
  schedule names/task IDs are absent, no task remains routed to the monitoring
  queue, the monitoring supervisor section/log tail are absent, and the
  monitoring application/collector modules are not imported during normal
  background startup. Assert that every retained worker command has exactly
  one `--without-heartbeat` and `--without-gossip` and that no unclassified
  worker command changed.
- Extend pinned-image validation to instantiate the actual versioned Beat
  application and parse the actual supervisor config under the derived
  wrapper. Assert exact schedule cadences, five-minute scheduler reload,
  absence of Beat/worker liveness-file writes, absence of disabled Craft and
  monitoring entries, six retained Celery workers, optional bot sections, and
  the chosen concurrency values. Source-text matching alone is insufficient.
- Add deterministic bot tests for both feature flags, enabled/disabled
  supervisor output, missing-token/acquisition/cache cadence, no-tenant Slack
  wake behavior, DB-managed configuration, and active heartbeat preservation.
- Add an idle live-stack observation after startup: snapshot Redis
  `INFO commandstats` and relevant queue/pubsub state before and after at least
  11 minutes without running `MONITOR` continuously. There must be no
  monitoring-task dispatch, queue/process/Redis metrics collection, Celery
  worker event heartbeat/gossip traffic, liveness-file timestamp changes, or
  dormant-bot polling. Record functional queue reads separately; active
  indexing, deletion, Beat watchdog, primary leadership, and recovery flows
  are expected traffic and are not failures.
- Add live task cancellation/revocation, worker-kill/redelivery, and active
  index-attempt heartbeat recovery tests to prove removal of the Celery event
  plane did not remove a correctness mechanism.
- Add a focused watchdog contract covering heartbeat cadence, key TTL,
  timestamp encoding/parsing, watchdog check interval, stale threshold,
  missing/malformed/future values, Redis lookup errors, and the calculated
  restart bound.
- Add Docker Engine/Compose and Podman capability tests that inspect the
  runtime health configuration and observe fast startup checks followed by a
  slow steady interval. Merely accepting/rendering `start_interval` is not a
  pass.

Update behavior documentation in the same change:

- `docs/vpn_routing_and_proxies.md`: replace its claim that health validates
  the provider-resolver data path; document offline TequilAPI/local-route
  health, one-minute autoheal timing, stale health limitations, and the fact
  that real requests remain fail-closed.
- `docs/local_docs_rag_search.md`: change Compose readiness from repeated
  `/ready` to local `/health`, and document the staged shim-only startup,
  single pre-API/background `make up-full` `/ready` request, and failure
  behavior.
- `docs/onyx_patch_info.md`: document exact schedule rewrites, removal of all
  self-hosted monitoring jobs/worker/collectors, unused file probes, Celery
  event heartbeat/gossip, the corrected Craft removal, bot policy, concurrency
  defaults, and watchdog ownership. Clearly distinguish removed observability
  from retained functional queue inspection, broker health, primary
  leadership, task revocation, and index-attempt heartbeats.
- `docs/onyx_patches_upgrade.md`: add every schedule name/upstream cadence and
  the pinned Beat scheduler, app bootstep, monitoring modules, Celery worker
  commands, supervisor sections/log tails, bot loops, and watchdog constants
  to the patch audit. Require effective versioned-Beat and derived-supervisor
  validation.
- `README.md`: mention the five-minute maximum background discovery delay and
  staged one-shot full-mode embedding validation, default-off Slack/Discord
  processes, and Docker Engine 25 minimum as user-visible consequences; keep
  implementation details in the subsystem docs.
- `.env.wrapper.example`: add only documented default-off Slack and Discord
  feature switches; do not add credentials, cadence knobs, or a generic power
  mode.
- `AGENTS.md`: update the runtime map and validation invariants if the final
  health or background behavior changes its current claims.

`make check` is the deterministic pre-handoff gate. Because this changes a
runtime patch, also run `make check-upgrade` against the pinned images. Do not
run `make upgrade` merely for this work because no pin or dependency input is
changing.

## Implementation order

Implement and validate in dependency-safe phases:

1. Replace Myst's online DNS readiness with direct local TequilAPI plus
   interface/route validation. Move Myst health and autoheal polling to one
   minute, disable autoheal's inherited health, update deterministic tests,
   and live-test autoheal before changing other probes.
2. Replace final-hop online readiness with local listener/configuration health
   and prove that neither direct nor configured-upstream modes produce
   synthetic traffic.
3. Apply the exact Compose disposition table: remove leaf checks and move all
   retained ordinary checks to 10 minutes with service-specific fast-start
   grace. Collapse the Obscura, SearXNG, and doc-drop duplicate probes and
   verify every effective mode and optional overlay.
4. Replace embedding health with `/health`, add the staged shim-only startup
   and single pre-API/background `up-full` `/ready` helper, and prove zero
   later inference requests during idle observation.
5. Apply the strict Beat rewrite: five-minute discovery/reload, remove all
   self-hosted monitoring tasks, remove disabled-Craft tasks, and update the
   coupled heartbeat/watchdog contract. Validate the effective versioned Beat
   schedule against the actual pinned image.
6. Install the single derived-supervisor entrypoint: remove monitoring and
   disabled-Craft workers/log tails, disable Celery heartbeat/gossip, remove
   unused worker file probes, and apply explicit default-off bot policy. Test
   task cancellation/redelivery and bot opt-in before right-sizing worker
   concurrency.
7. Complete all cross-referenced behavior and upgrade documentation, then run
   deterministic and live validation.

Validation should prove:

- Lite and full startup remain fast.
- The embedding backend receives exactly one startup validation and no later idle requests.
- A daemon-reported VPN disconnect or missing local tunnel/interface becomes
  unhealthy and autoheals within the documented multi-minute bound.
- Packet capture and resolver/proxy logs show no DNS query, public connection,
  or configured-upstream connection caused by any health check during an idle
  observation window longer than the ten-minute health interval.
- Myst health consumes only loopback TequilAPI state and local interface/route
  information, and does not log the sensitive remainder of the TequilAPI
  connection response.
- Broken egress continues to fail closed before its next health check.
- No Craft tasks are scheduled when Craft is disabled.
- No self-hosted monitoring task, worker, metrics endpoint, queue scan,
  process-memory scan, Redis-health scrape, or worker-event receiver remains.
- Retained workers emit no Celery monitoring heartbeat/gossip events and write
  no unconsumed liveness files, while broker health, revocation, redelivery,
  primary leadership, and active indexing heartbeats still work.
- Slack and Discord processes are absent by default; when explicitly enabled,
  their configuration delays and active heartbeat behavior match the plan.
- Idle scheduled-discovery and housekeeping logs show only the classified
  five- or ten-minute cadence; hourly/daily cleanup remains at its pinned
  cadence.
- A new document or connector begins processing within five minutes.
- `make up-lite` performs no embedding validation; `make up-full` performs
  exactly one before API/background start and returns nonzero without an
  automatic retry when it fails.
- Effective Docker Compose models retain fast startup checks and 10-minute
  steady checks; supported Podman models either do the same or fail clearly.
- Removed leaf checks are absent from the effective model, not merely shadowed
  in one Compose source file.
- The beat watchdog restarts a deliberately hung Beat process within the
  documented bound and does not poll Redis more frequently than planned.
- Exact pinned-reference checks pass; no Teep conclusion is based on the
  currently mismatched reference worktree.
