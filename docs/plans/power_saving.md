# Power saving plan

## Current status (2026-07-20)

**Implementation state: the complete pre-measurement bundle is implemented.**
This includes the pinned component baseline, sleepy/local health and recovery
checks, aggregate gateway checks, staged embedding readiness, idle MLX unload
and cold reload, reduced Celery/Beat scheduling and worker overhead, removal of
disabled monitoring/Craft/bot activity, the 1 GiB OpenSearch heap with
Performance Analyzer disabled, MinIO's `slowest` scanner profile, and a strict
Podman-native startup-health layer. Tests and the user-facing and upgrade
documentation cover these changes.

Two follow-up low-risk reductions are also implemented. Exact background
control programs and SearXNG's exact multiprocessing resource tracker no
longer install application `sitecustomize` patches. Celery workers, Beat,
spawned indexing children, the SearXNG parent, and its request workers retain
their strict patches. Myst still reconciles every 20 seconds for the same
repair bound and DNS freshness, but an exact target/gateway/device match now
suppresses the route replacement and success log; drift is repaired and
logged as before.

**Validation state: deterministic and pinned-image validation passes.**
`make check` passed 235 tests (with five image-only skips), and
`make check-upgrade` passed the pinned-image contracts and five image-backed
parser tests. Live Docker checks covered startup/readiness, worker and Beat
behavior, OpenSearch, MinIO CRUD, SearXNG search, aggregate Obscura recovery,
and MLX cold load, shared concurrent startup, and 10-minute idle unload. Live
rootless Podman 5.8.1 checks covered lite and full startup, all native startup
health contracts, exact host-route preservation, shared PostgreSQL/OpenSearch
binds, MinIO CRUD, embedding readiness, WebUI, and a ten-result SearXNG
search. A clean-machine Podman full start directly pulled the required Onyx
images without invoking the Docker-oriented upstream bootstrap, started all 29
expected containers without code interpreter or socket autoheal. Podman full
mode serves the configured document source through a PID-tracked host-local
server and fixed hardened relay, with no document collection stored in the VM.
The default source remains `./doc-drop`; the mechanism also supports external
mounts, including WebDAV. Native
health timestamps confirmed five-second startup checks, a 600-second ordinary
steady interval, and Myst's one-minute interval. The bundled MLX child also
unloaded after its completed readiness request while the lightweight host
proxy remained listening.

The 2026-07-20 follow-up Docker validation recreated the full VPN stack and
left every service healthy. Exact control-process exclusions reduced
background supervisor PSS from about 197 MiB to 27 MiB and watchdog PSS from
about 192 MiB to 18 MiB; both had one thread and no NumPy/OpenBLAS/tokenizer or
Torch mappings afterward. The SearXNG resource tracker fell from about 59 MiB
to 10 MiB PSS while the parent and request worker retained all strict patch
diagnostics. A real API-to-SearXNG-gateway query returned ten results. Pinned
image validation and all five image-only parser tests passed.

The corrected Myst live run wrote the initial fixed-host route, learned four
distinct broker addresses as DNS rotated, corrected the tunnel MTU once, and
then emitted no duplicate route-change lines across subsequent 20-second
passes. A manual exact reconciliation was also silent. Live testing caught
and fixed an initial matcher error caused by Linux rendering host routes
without the `/32` suffix; deterministic coverage now mirrors that canonical
output.
Podman was then cleanly restarted against the initialized Docker bind data:
PostgreSQL exposed the existing application state (including 194 chat sessions,
1,326 chat messages, 15 connectors, three LLM providers, and six personas),
OpenSearch recovered 71 active primary shards, all 29 expected services became
healthy, and the WebUI returned HTTP 200. A subsequent full down/up cycle
validated the zero-copy host document relay and restored the same shared
database and index mounts. The relay had no mounts, only its dedicated host
uplink, and both published and exact-policy internal health returned HTTP 200.
The tracked server survived its launch session, reused the same PID on a
repeated start, stopped with bounded identity validation, and restored service
after restart. PostgreSQL retained the native five-second startup check and
600-second steady-state check after the shared-data workaround. The
working mappings are now unconditional parts of the two core Podman overlays;
there are no database-specific Compose layers, native-volume branch, or
storage opt-out flags.
The long controlled before/after power/resource measurement windows and the
remaining destructive or workload-specific fault/performance gates have not
yet been run. Fault injection is intentionally deferred until after health
frequency and resource-consumption evidence is collected. A complete
`make upgrade` dependency refresh was also not completed
because PyPI access timed out; no dependency-lock input changed, and the exact
Myst and Teep images were built and validated separately.

A non-controlled Docker full-stack snapshot on 2026-07-20 showed 16 retained
health checks: Myst contributes 60 local checks/hour and the other 15 checks
contribute six each, for 150 steady checks/hour. The corresponding lite model
had 13 retained checks and 132/hour under the selected optional overlays. One
stack-wide sample reported about 5.2 GiB container memory and 7.4% aggregate
container CPU; this is operational context, not a before/after power result,
because foreground/background work and Docker sampling noise were not isolated.

**Deferred state: intentionally not implemented.** Every item under
[Deferred until after the measurement gate](#deferred-until-after-the-measurement-gate)
remains deferred: event-driven Myst route/MTU ownership beyond the implemented
change-only write path; OpenSearch plugin,
audit, refresh, and lower-memory experiments; the Kombu Redis blocking-pop
timeout patch; API metrics and connection-pool sizing; worker consolidation;
and supervisor log-relay or enabled-bot micro-tuning. These require comparable
before/after measurements and separate evidence-driven changes.

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
  per-worker file-based liveness writes are absent. Beat retains one consumed
  file heartbeat, but only once every 5 minutes.
- Disabled features have neither scheduled jobs nor dedicated idle workers.
- OpenSearch is no longer provisioned with an unconditional 2 GiB heap and
  does not start the supported Performance Analyzer agent CLI. Its plugin,
  Security/TLS, audit, refresh, and index behavior otherwise stays unchanged.
- MinIO keeps its supported scanner/healing behavior, but uses the supported
  `slowest` scanner profile instead of beginning a new background scan cycle
  every minute.
- The bundled host MLX embedding model unloads after a bounded idle period and
  reloads on the next real embedding request; a full-mode stack no longer
  holds roughly a gigabyte of model-handler memory indefinitely while idle.
- Retained Celery workers do not expose unused standalone Prometheus servers,
  and their concurrency is reduced only to values that pass representative
  full-mode workloads. API metrics, connection pools, and the six-worker
  functional topology stay unchanged until measurement justifies more work.

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

Docker's `start_interval` requires Docker Engine 25.0 or later, and Docker
Compose first supported the field in 2.20.2. The Compose specification can
carry the field, but that alone does not prove that the selected engine
schedules it. Make Engine 25.0 and Compose 2.20.2 the documented Docker
minimums, verify the rendered `StartInterval` in `docker inspect`, and verify
the effective Docker and Podman models selected by the Makefile. Podman
exposes a separate startup-health model (`HealthStartup*`), and live testing
confirmed that its Compose compatibility API accepts but drops
`start_interval`. The implemented Podman 5.8.1+ path therefore creates stopped
containers, copies each exact regular command and timeout into a native
five-second startup check, verifies the native and regular configuration, and
only then starts the services. It rejects running containers that lack that
configuration and never treats successful Compose rendering as proof. Full
mode also starts an identity-validated macOS document server before
stack creation. A fixed, capability-free relay preserves `doc-drop-web:8091`
without copying the configured collection into the VM, while application
containers retain no direct host route. This path applies to the default
`./doc-drop` directory and to configured local or external mounts.

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

The source audit is pinned-artifact-sensitive. Verified matches are Onyx
`b7482a59fb74503d5ec3dcde0ae5beac7b4905ff`, Obscura `v0.1.10`, and SearXNG
`7b2199ecd`. The local Teep reference worktree does not contain the committed
`TEEP_REF=6413fe0547b449e67f7296986fe8b8ffbc9bbcd2`, although the built image's
revision label does. The local MinIO reference also does not contain the
revision recorded on the running image. Synchronize those read-only reference
repositories, or extract and archive the exact image source, before relying on
implementation details from them.

Myst and two third-party support images are not currently reproducible enough
for an exact-source audit: the Myst Dockerfile clones the mutable
`docker_host_fixes_with_logs` branch at depth one and records no revision;
`willfarrell/autoheal:latest` and `tailscale/tailscale:stable` are mutable image
tags. Before implementation, pin Myst to a reviewed commit in
`stack.versions.env`, add the commit as an image label, and pin Autoheal plus
Tailscale to immutable tags or digests. Record the currently tested image
digests as audit evidence, but do not mistake a local image digest for a
source pin. Reconcile the Makefile's stale Teep fallback ref with the committed
source of truth or add an assertion that makes fallback drift impossible.

Those pin corrections are part of this plan, not incidental cleanup. They
trigger the pin-upgrade workflow in `docs/onyx_patches_upgrade.md`: refresh the
relevant sources/images through the Makefile, run `make check-upgrade`, and
perform the component-specific live matrix. Do not defer exact-source review
until after health and scheduler behavior has been changed.

This is a reproducibility correction, not authorization for opportunistic
component upgrades. Pin Autoheal and Tailscale to the exact currently tested
digests. For Myst, prove the selected source commit reproduces the required
current image/CLI/API/route behavior before replacing the local image; if the
running image's exact source cannot be reconstructed, keep that image for the
initial bundle and treat source repinning/rebuild as a separately validated
blocker rather than silently adopting a newer branch head.

## Delivery strategy and measurement gate

Implement all low-risk and manageable-risk work below as one coordinated
pre-measurement change. "One change" means one release/validation boundary,
not one undifferentiated patch: keep commits and tests grouped by subsystem so
a failing capability can be isolated or reverted without discarding the other
savings.

The pre-measurement scope is limited to:

- immutable component/source authority required to validate the work;
- offline and less frequent health checks, aggregate readiness, and removal of
  unconsumed leaf checks;
- staged one-shot embedding readiness and the narrow wrapper-owned MLX idle
  lifecycle proxy;
- OpenSearch heap reduction to 1 GiB and the supported Performance Analyzer
  disable flag, without changing the plugin set, Security configuration,
  refresh interval, or container memory limit;
- MinIO's supported `slowest` scanner profile;
- exact five-minute Onyx work discovery, removal of the self-hosted monitoring
  and disabled-Craft schedules/workers, the lightweight Beat watchdog, removal
  of unused worker event/file liveness traffic, default-off bot processes,
  conservative Celery concurrency, and disabled standalone worker metrics.

Initial-bundle release matrix:

| Item | Timeout/cadence contract | Stability and capability gate |
|---|---|---|
| Myst health and autoheal | Startup probe 5s; steady health 1m; probe timeout 10s; 2 failures; 420s start period; autoheal poll 60s; expected restart detection roughly 2–3m. | Local TequilAPI/interface/route only; no DNS/public/upstream probe; daemon disconnect and missing tunnel autoheal; the 20s route/MTU cadence and repair bound are unchanged, while exact matching exemptions avoid route writes and success logs. |
| Ordinary/aggregate health | Startup probe 5s; steady health 10m; 1 retry; bounded existing timeout; local start period 30–120s, API 240s. | `compose up --wait` fails on a missing origin/proxy path; rendered dependencies remain acyclic; real requests fail closed immediately; no health-triggered public traffic. |
| One-shot embedding startup | Exactly one `/ready` call with no retry; shim upstream request remains bounded at 30s; `/health` is inference-free. | Fresh API/background creation waits for success; repeated failure neither recreates nor stops running services; lite mode never calls it. |
| MLX idle lifecycle | 10m idle measured after request completion; cold start plus inference stays inside the unchanged 30s shim deadline; proposed child termination grace 15s. | Single child for concurrent cold calls; no ambiguous POST replay; active batches never unload; memory is released; custom/manual upstream ownership and repeated up/down remain correct. |
| OpenSearch | Fixed 1 GiB heap; no new container timeout or memory limit. | Full fresh/existing-volume ingestion, KNN/hybrid/search/reindex/restart workload passes without OOM, circuit-breaker, corruption, stuck recovery, or material tail-latency regression; all non-PA configuration is effectively unchanged. |
| MinIO scanner | Supported `slowest` profile; nominal scan cycle 30m; existing readiness timeout/startup semantics retained. | Object CRUD, multipart cleanup, lifecycle/healing, chat files, document ingestion, and restart pass; no object loss/corruption or unbounded cleanup delay. |
| Beat discovery and housekeeping | Discovery/reload 5m; functional housekeeping 10m or existing longer cadence; newly eligible document/connector begins within 5m. | Exact pinned schedule names/cadences; no disabled task materializes; active indexing, deletion, and cleanup complete correctly. |
| Beat watchdog | Beat file touch and watcher check 5m; stale/startup grace 20m; expected restart 20–25m after last good reload. | Started with `python -S`; stdlib/file-only watcher; no application bootstrap, Redis, or Onyx imports; hung Beat restarts once; healthy/busy workers cannot cause a false Beat restart. |
| Monitoring/Craft worker removal | No replacement polling interval. | No producer targets removed queues; task registry/routing remains complete; Craft stays absent only while explicitly disabled. |
| Celery event/file liveness removal | Broker health, revocation, leadership, and active index heartbeats retain pinned cadences. | Cancellation, remote control, kill/redelivery, stuck-index recovery, and clean shutdown pass; no task loss or unconsumed worker file writes. |
| Bots and worker sizing | Bots absent by default; enabled programs retain exact upstream timing; discovery remains the 5m outer bound. | Either opt-in restores original Slack/Discord behavior; representative multi-document/connector concurrency completes without queue starvation, timeout, or material throughput regression. |
| Standalone worker metrics | No listener or metrics thread; API metrics unchanged. | Every retained worker starts and registers the same tasks; operator diagnostics and functional logs remain available. |

Freeze representative workloads and acceptance budgets before implementation.
Unless a row defines an intentional new delay (five-minute discovery,
ten-minute MLX unload, or health-status staleness), use a default material
regression threshold of 20% for median and p95 task/request completion time and
zero new timeouts, errors, restarts, corruption, or lost work. If normal host
variance makes 20% indistinguishable from noise, collect more runs rather than
loosening the threshold after seeing the result. Existing hard application
timeouts remain authoritative even when the percentage budget would allow a
larger value.

Every item must retain the timeout/failure behavior stated in its subsystem
section. No capability may be weakened merely to complete the bundle: if one
item fails its focused stability or live workload test, leave that item out,
record the evidence, and continue validating the remaining independent work.
Privacy/routing failures, task loss, corrupt search/index results, or an
unbounded external/model retry are release blockers rather than acceptable
power trade-offs.

After the complete pre-measurement bundle passes deterministic, pinned-image,
and live validation, run the controlled before/after measurement described in
`Initial-bundle expected result and measurement`. Do not implement anything in
`Deferred until after the measurement gate` until those results identify a
remaining material cost and
the named promotion requirements are satisfied. This keeps dependency-internal
patches and capability-sensitive redesigns out of the initial change.

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
currently built daemon exposes `GET http://127.0.0.1:4050/connection`; this
reads the local connection manager and returns the current connection state.
The existing `myst connection info` command is a heavier CLI wrapper around
the same API call. Confirm that contract after Myst is pinned in phase 1. The
health check should consume the response without logging it and require
exactly `status=Connected`, because the full response contains provider,
location, session, and identity metadata.

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

The separate 20-second Myst route/MTU maintenance cadence remains unchanged.
It owns routing correctness across daemon-driven reconnects, so each pass still
resolves configured exemption hosts and validates exact routes. A matching
target, gateway, and device now produces no route write or success log; only
missing or drifted routes are replaced and logged. Replacing the loop with a
netlink state machine is too invasive without stronger evidence, and remains
explicitly deferred below.

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

- Egress bridges
- Obscura readiness through its CDP gateway
- SearXNG readiness through its API gateway
- API server
- Web server
- Nginx
- Teep
- PostgreSQL
- MinIO
- Doc-drop route gateway
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
   do not newly create or restart API/background against an unvalidated
   embedding backend.
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

Define repeated-start behavior explicitly. On a fresh stack, API and
background must not exist before `/ready` succeeds. On a repeated `make
up-full`, those services may already be running from the previous successful
start; a later failed `/ready` must return nonzero without recreating,
restarting, or stopping them. The helper cannot truthfully promise that they
were never running, and it must not turn a validation failure into an implicit
outage of an otherwise healthy existing stack.

This removes periodic inference and API spend, but it does not by itself stop
the bundled host server from keeping its model resident. The following
lifecycle work is required for the default local MLX path. Subsequent real
embedding failures still surface through the actual indexing or search
operation.

### Bundled MLX server: unload the model when idle

The pinned `mlx-openai-server` 1.8.1 configuration has no idle-unload option
and eagerly spawns its model handler. In the running stack the lightweight
server parent is roughly 100 MiB RSS and the idle embedding handler roughly
1.36 GiB RSS. Unified-memory accounting varies on macOS, but this is plainly a
larger savings opportunity than the health request itself.

For the wrapper-managed default MLX upstream only, replace the always-warm
host process with a tracked lightweight lifecycle proxy:

1. Keep the shim-facing address and request contract unchanged at port 3210.
   Bind a small tracked proxy there and launch the heavy MLX server only on
   loopback at a separate fixed port. Preserve the existing host-exposure and
   routing policy; do not make the model backend itself reachable from Docker.
2. On the first bounded `/v1/embeddings` request, serialize startup, launch the
   exact verified command/process group, wait for model readiness within the
   existing startup budget, and forward the original request once. Concurrent
   callers wait on the same startup rather than spawning duplicate models.
3. After 10 minutes with no in-flight or newly arrived embedding request,
   gracefully stop the identity-validated child process group, verify exit,
   and release MLX model memory. Reset the timer after request completion, not
   request start, so long batches cannot be killed. Do not expose a generic
   user cadence knob or unload between requests in an active indexing burst.
4. If startup or the child fails, return one clear local upstream error. Never
   replay a POST whose delivery/completion is ambiguous, hide failure with an
   empty vector, or fall through to a network embedding provider.
5. `make down-full` stops the wrapper-owned proxy and any child using the same
   PID/executable/ancestry validation as the current lifecycle. Custom
   upstreams and manually launched listeners remain untouched.

Prefer a small stdlib implementation with strict URL/path/body limits and no
general forwarding capability. Place it under `embedserv/` and keep it
independent of Onyx imports (for example,
`embedserv/idle_embedding_proxy.py`, with focused tests rather than a new
package or service framework). Pin and inspect the installed MLX server CLI and
readiness contract; fail startup on drift. The shim's upstream deadline is
currently 30 seconds, so cold model start plus forwarding and inference must
complete within that unchanged end-to-end deadline under the accepted
document/query batch workload. Give child readiness its own shorter bounded
deadline selected from measurement; do not extend or retry the outer 30-second
request merely to hide a slow cold start. If that requirement cannot be met
reliably, omit idle unload from the bundle rather than degrading embedding
reliability.

The one-shot full startup `/ready` intentionally loads the model once and the
idle timer unloads it about ten minutes later. Use `time.monotonic()` for idle
accounting. Graceful child termination must also have a fixed timeout
(proposed 15 seconds), after which only the already identity-validated child
process group may be force-stopped; proxy shutdown must never target a reused
or merely port-matching process. A later search/index operation may incur the
measured cold-start delay, which must be documented.

Test first request, concurrent cold requests, an indexing burst, the exact
idle boundary, a request arriving during shutdown, child crash, proxy crash,
stale/reused PID files, repeated `make up-full`, `make down-full`, custom and
manual upstreams, and query/index correctness after multiple unload/reload
cycles. Measure host RSS/unified GPU memory and idle package power before load,
while warm, and after unload; verify that no timer sends inference or network
traffic merely to keep/readiness-check the model.

### Final-hop proxy probes

The current proxy health command starts Python and resolves `example.com`.
Remove the proxy health check entirely rather than replacing it with another
observer. Each proxy is already exercised by one or more egress-bridge health
checks: a local `CONNECT` that receives the expected policy rejection proves
that the bridge can reach the final-hop listener and that the process is
serving its validated configuration. Preserve a separate aggregate bridge
check for every distinct public, host, Obscura, and executor path; do not
collapse checks across network boundaries merely because several bridges
share a proxy process.

- Change bridge dependencies on the final-hop proxy to `service_started`.
- Keep strict main-process startup validation of static configuration,
  listener binding, route-class policy, and upstream-proxy syntax. Invalid
  configuration must make the process exit and prevent aggregate readiness.
- Do not periodically connect to a configured upstream HTTP/SOCKS proxy merely
  to prove that it accepts a connection or credentials.
- Do not use a public canary target during startup. The first real request is
  the appropriate end-to-end validation of DNS, VPN, and configured-upstream
  behavior, and it already fails closed if that path is unavailable.
- Keep Myst responsible only for the local, offline VPN state checks described
  above.
- Remove `--check-ready` and readiness-only probe helpers if no production path
  uses them after the dependency rewrite; retain real request-time DNS and
  destination-validation helpers.

The aggregate bridge check must fail when the proxy is absent, not listening,
misconfigured, or unreachable from that bridge. No health or readiness check
should produce DNS or upstream-network traffic.

### Exact Compose disposition

Audit the effective lite and full models, including VPN/no-VPN, configured
upstream proxy, Teep-through-VPN, Tailscale-through-VPN, executor networking,
and Tailscale profiles. The intended steady-state disposition is:

| Service or service class | Steady interval | Action |
|---|---:|---|
| `myst-client` | 1m | Replace with offline TequilAPI/interface/route probe; use two failures before unhealthy. |
| `autoheal` loop | 1m | Set `AUTOHEAL_INTERVAL=60`; disable the image's inherited 5s `pgrep` health check with `healthcheck: {disable: true}`. |
| Public/host final-hop proxies | none | Remove the redundant probe; strict process startup plus each bridge's aggregate local 403 check owns readiness. |
| Obscura/public/host/executor egress bridges | 10m | Keep the local policy-rejection check; it must never leave the namespace. |
| Obscura | none | Remove the expensive in-container `fetch` probe; aggregate readiness moves to the CDP gateway. |
| Obscura CDP gateway | 10m | Replace socket-open-only health with a local `/json/version` request through the gateway. |
| SearXNG core | none | Remove its duplicate Python health process; aggregate readiness moves to the service gateway. |
| SearXNG service gateway | 10m | Keep one end-to-end local HTTP check through the gateway. |
| API, Web, and nginx | 10m | Keep local HTTP checks needed by startup dependencies. |
| Teep | 10m | Keep its local `/health` check; audit the Teep-VPN gateway overlay too. |
| PostgreSQL | 10m | Override the inherited upstream 10s check in `docker-compose.yaml`. |
| Redis cache | none | Preserve `service_started`; broker/cache clients already perform functional startup checks and connection health. |
| OpenSearch | none | Preserve `service_started`; Onyx performs functional initialization, and a periodic JVM health probe would add observation without recovery. |
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
- `egress/final_hop_proxy.py` for removal of readiness-only probe code after
  bridge dependencies are rewired.

For Myst, feed the loopback TequilAPI response directly into a status matcher.
Do not print, persist, or interpolate the full JSON into an error message. Unit
tests must provide fake `wget` and `ip` executables and prove that no `dig`,
resolver, or other online command is invoked. Keep the existing no-VPN
stale-`myst0` and default-route behavior.

The currently tested Myst image has BusyBox `wget` with explicit proxy
control. Reconfirm this in the newly pinned image, then use the equivalent of:

```sh
wget -Y off -q -T 5 -O - http://127.0.0.1:4050/connection
```

Pipe it directly to a matcher anchored at the beginning of the top-level JSON
object, equivalent to
`^[[:space:]]*\{[[:space:]]*"status"[[:space:]]*:[[:space:]]*"Connected"[[:space:]]*[,}]`.
The currently tested Go DTO serializes `status` first; assert that exact
contract against the newly pinned Myst source/image. A loose search for any
nested `status` field is
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
attached to the interface. Characterize the newly pinned Myst route output
first and add a different local-only invariant only if the supported provider
route shape demonstrably cannot satisfy this one.

For the final-hop proxy, remove readiness logic without weakening destination
validation used by real requests. Tests must prove that no effective health
command invokes `_resolve_target_host`, `_probe_http_proxy_endpoint`, or
`_probe_socks5_proxy_endpoint`, and that the retained bridge check reaches the
right listener while making no DNS or upstream connection.

## Full-mode storage and search plan

The original proposal concentrated on check frequency, but the live full
stack shows that always-resident storage/search services are a larger power
and memory opportunity. A one-shot diagnostic sample found the background
container at roughly 2.9 GiB/212 PIDs and OpenSearch at roughly 2.8 GiB/125
PIDs; MinIO was roughly 230 MiB. These figures are not acceptance thresholds:
repeat cgroup memory, CPU, wakeup, disk-I/O, thread, and process measurements
over a controlled idle window before and after each phase.

### OpenSearch heap and supported agent disable

The generated deployment fixes `OPENSEARCH_JAVA_OPTS=-Xms2g -Xmx2g`. The
running JVM confirms that 2 GiB heap. Its logs also show Performance Analyzer
startup that falls back to disabled after doing agent work. The initial bundle
changes only two supported wrapper settings:

1. Override the generated heap in tracked wrapper Compose with
   `-Xms1g -Xmx1g`. Do not test or adopt 512 MiB in this phase, even though the
   official [OpenSearch Docker
   example](https://docs.opensearch.org/latest/install-and-configure/install-opensearch/docker/)
   uses it. Do not add a cgroup memory limit: JVM heap is not the process's
   complete native/direct/file-cache budget.
2. Set `DISABLE_PERFORMANCE_ANALYZER_AGENT_CLI=true` using the supported
   setting so the bundled agent does not start merely to disable itself. Do
   not remove the plugin or alter any other plugin, audit, Security/TLS, index,
   refresh, shard, or scheduler configuration before the measurement gate.
   The authoritative behavior is documented in the [OpenSearch Performance
   Analyzer
   documentation](https://docs.opensearch.org/latest/monitoring-your-cluster/pa/index/).

The 1 GiB heap is accepted only if fresh and existing-volume startup,
create/index/search/delete, KNN vector search, hybrid normalization pipelines,
multi-document PDF ingestion, connector reindex, restart recovery, and the
Security/TLS client path all pass. Run the largest representative ingestion
and simultaneous search workload long enough to force normal old-generation
behavior; record heap occupancy, GC pause/time, native/direct memory, cgroup
peak, latency, rejected requests, circuit breakers, and OOM/restart events.
No OOM, corruption, stuck recovery, material tail-latency regression, or
inability to complete the workload is acceptable. If 1 GiB fails, select the
smallest larger fixed heap that passes rather than weakening the workload or
changing more OpenSearch controls at once.

### MinIO scanner cadence

MinIO documents a one-minute default scanner cycle. Confirm that behavior in
the exact image source synchronized in phase 1, then set
`MINIO_SCANNER_SPEED=slowest` in the full-mode MinIO service. The supported
profile keeps scanner, healing, lifecycle, and usage behavior but changes its
cycle to 30 minutes and uses the service's built-in throttling. Use the
[documented MinIO scanner setting](https://min.io/docs/minio/linux/reference/minio-server/settings/core.html);
do not use the undocumented `_MINIO_SCANNER=off` escape hatch.

Validate bucket creation, chat/file upload and download, multipart/object
cleanup, document ingestion, restart, and any configured lifecycle/usage
behavior. Compare disk I/O and scanner logs across an idle window longer than
30 minutes. Also verify that license call-home is absent in this unlicensed
deployment; classify it rather than adding an unnecessary blocking shim.

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

The trade-off is that unattended work may take up to five minutes to begin.
Chat over existing indexed data is unaffected, but a newly uploaded
project/assistant file, connector change, or deletion can be visibly delayed;
those are interactive workflows and must be documented as such. For a local
laptop stack, that is a reasonable default only if the live acceptance test
confirms the stated five-minute bound.

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

### Functional housekeeping at 10 minutes or longer; local Beat recovery

| Exact schedule name | Pinned upstream cadence | New cadence |
|---|---:|---:|
| `celery-beat-heartbeat` | 1m | remove |
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
an informational log even when unchanged, and touches Beat's liveness file.
Set the strictly validated self-hosted reload interval to 5 minutes and keep
that file touch: unlike the eight worker files below, it will become the sole
input to the replacement watchdog. The discovery delay contract is the greater
of schedule activation and reload timing; tests must prove a newly
materialized eligible task is dispatched within the documented bound.

Remove the queued `celery-beat-heartbeat` task, its Redis key/TTL writes, and
the current Redis-aware watchdog. A heartbeat routed through the primary
Celery queue cannot distinguish a hung Beat process from a busy/delayed worker,
and the current watchdog imports enough of Onyx's Redis stack to consume about
222 MiB RSS in the observed container. Replace it with a tiny tracked,
stdlib-only supervisor program that watches only Beat's local liveness-file
mtime:

- Beat touches the file after each successful five-minute scheduler reload.
- The watcher checks once every 5 minutes with monotonic elapsed time.
- Use a 20-minute stale threshold and an equivalent startup grace; require two
  observations for a missing file so startup ordering cannot cause a restart.
- Treat a missing, non-regular, wrong-owner, or future-dated file as invalid
  without reading/logging file contents. Pin the exact safe path and reject
  symlinks.
- On confirmed staleness, restart only Beat through the existing supervised
  control path and log one non-secret reason. A watcher failure must be visible
  to supervisor rather than silently disabling recovery.

The expected stuck-Beat recovery bound is roughly 20–25 minutes after the
last successful reload, with no Redis or task-queue dependency. State the
measured bound only after killing/hanging Beat in a live fault-injection test.
Keep the replacement narrow and startup-validated, and document its ownership
in `docs/onyx_patch_info.md` and `docs/onyx_patches_upgrade.md`.

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

Leave retained program log routing and `log-redirect-handler` unchanged in the
initial bundle. Removing that low-cost relay broadens the strict supervisor
rewrite and risks losing diagnostics for separately produced MCP logs; it is
deferred until measurement demonstrates a material I/O or wakeup cost.

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

Do not patch enabled-bot acquisition, cache, heartbeat, metrics, or database
pool internals in the initial bundle. An explicitly enabled bot retains the
pinned upstream behavior and full capability. Only the wrapper's default-off
process policy changes; tests must prove that setting either flag true restores
the exact original program command, log inputs, database-managed credentials,
Admin configuration, message handling, and active failover behavior.

Finally, set conservative wrapper-owned full-mode Celery concurrency defaults
appropriate to one laptop user (proposed: primary 2, light 4, heavy 2,
docprocessing 2, user-file 1, docfetching 1) instead of upstream's primary 4,
light 24, heavy 4, docprocessing 6, user-file 2, and docfetching 1. Keep these
as explicit Compose values rather than hidden patches. Before finalizing the
numbers, run representative multi-document PDF ingestion and connector sync,
measure throughput/memory, and verify that no task's correctness assumes the
upstream pool size. Apply the release matrix's zero-timeout/error and 20%
median/p95 completion-time budget; if a proposed value fails, increase only
that worker's concurrency to the smallest passing value. Exercise simultaneous
queue classes so an idle single-queue test cannot hide starvation. The two
removed monitoring/scheduled-task workers must not have concurrency settings
left behind.

### Disable unused standalone worker metrics

The retained primary, light, heavy, document-processing, and document-fetching
worker apps each call `start_metrics_server`. Set
`PROMETHEUS_METRICS_ENABLED=false` explicitly on the background container and
assert that no worker metrics listener/thread is created. This complements
removal of the monitoring worker; it does not remove operator-invoked logs or
diagnostic commands. If a future bundled scraper is added, it must be an
explicit feature with a documented cost rather than silently re-enabling all
listeners.

The setting must be scoped to `background`; do not disable the API `/metrics`
route or its request/pool instrumentation in this phase. For every retained
worker type, assert that no metrics listener socket or server thread exists and
that worker startup/task registration is otherwise identical. The environment
flag is an upstream-supported branch, so no metrics module monkeypatch is
allowed in the initial bundle.

### Recurring work audited and deliberately retained

Do not treat every interval-looking constant as an idle timer. The source
audit found the following recurring or polling paths, but they should remain
unless their stated scope changes:

- Redis's `health_check_interval=60` is checked opportunistically before a
  connection command after an idle period; it does not create a background
  once-per-minute task by itself. Keep it as broker/cache connection safety.
- `REDIS_POOL_MAX_CONNECTIONS=128` is a lazy cap rather than 128 preopened
  sockets. Include actual Redis client/socket peaks in the connection-budget
  trace, but do not lower the cap merely to make a configuration number look
  smaller; the scheduled/monitoring removals are what should eliminate idle
  Redis commands.
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
- The globally mounted WebUI health banner performs request/focus-driven SWR
  reads, not an interval refresh; the ten-minute auth token refresh is
  visibility-gated and correctness-sensitive. The one-minute `useNightTime`
  hook has no call sites in the pinned WebUI. Do not patch any of these unless
  an image/browser trace contradicts that classification.

Add an idle assertion for each conditional item above: no index heartbeat or
memory observer without an active attempt, no chat/task polling without an
active operation, and no bot or upload loop after its owner is absent. This
prevents a future upgrade from turning a deliberately retained conditional
loop into unclassified idle work.

## Deferred until after the measurement gate

None of the items in this section belongs to the initial implementation. Each
requires the completed pre-measurement bundle, comparable before/after data,
and evidence that the named source is still a material share of remaining idle
power, memory, I/O, or wakeups. Promote one item at a time in a separate change
with its own rollback and validation; do not combine deferred experiments with
one another or with an Onyx/component upgrade.

### Event-driven Myst route and MTU ownership

The current entrypoint resolves exemption hosts and validates MTU/routes every
20 seconds. It performs route replacement and success logging only when the
exact target, gateway, or device is missing or differs. A future design may
replace the remaining polling/DNS work with a blocking, debounced netlink
watcher plus a slow DNS reconciliation and an exact wrapper-owned route set.
Promote it only if measurement attributes material stable-state cost to the
remaining loop.

This is high risk because it owns fail-closed routes across daemon-driven
reconnects. Before promotion, characterize every pinned Myst link/address/route
event and prove that filtering the helper's own /32 updates cannot self-loop or
miss a reconnect. Required tests include immediate repair after link, address,
default-route, and daemon-owned reconnect changes; stale DNS-address removal;
literal host/LAN/upstream-proxy routes; helper death causing visible container
failure; and uninterrupted route-class isolation. The event repair bound must
be measured and no slower than the current 20-second reconciliation bound;
hostname refresh needs an explicit bounded interval and DNS-failure policy.

### OpenSearch plugin, audit, refresh, and lower-memory experiments

The pinned image loads scheduler-heavy plugins and the observed logs include
five-/15-minute and hourly work. Do not build a derived image or alter the
plugin set before measurement. If OpenSearch remains material afterward,
inventory `bin/opensearch-plugin list`, plugin descriptor dependencies, exact
Onyx mappings/APIs, and startup logs. `opensearch-knn`,
`opensearch-neural-search`, their dependencies, and the current Security/TLS
path are known capability boundaries.

Separately gated candidates are removal of confirmed-unused top-level plugins,
supported disabling of unused Security audit events, a 512 MiB heap, a cgroup
limit, and a longer refresh interval. Never remove Security/TLS as a power
shortcut or install/remove plugins at runtime. A derived image, if justified,
must pin its base digest/plugin set and hash every build input. Each candidate
requires fresh and existing-volume startup, KNN/hybrid/index/delete/reindex,
multi-document ingestion, concurrent search, restart/recovery, TLS/auth, GC,
OOM/circuit-breaker, and read-after-index freshness tests. Keep the same
workload and latency/error budgets used for the 1 GiB baseline; failure means
the candidate is rejected, not that the workload is weakened.

### Kombu Redis blocking-pop timeout

The installed Kombu Redis transport reissues `_brpop_start(timeout=1)` and has
no public option for that timeout. Lengthening it could reduce Redis wakeups,
but patching this dependency-internal method is upgrade-fragile. Promote a
strict 10-second patch only if post-change command deltas show empty `BRPOP`
remains material.

Required validation is an exact Kombu version/signature/source contract plus
per-worker Redis deltas, immediate task delivery on every queue, remote control
and revocation, graceful shutdown, Redis restart, and a dropped/stalled broker
connection. Normal publish-to-start latency must not regress because Redis
should wake a blocking pop immediately; shutdown and broker-failure detection
bounds must be explicitly measured and remain within existing operational
timeouts. Do not add a post-pop sleep or retry a task ambiguously.

### API metrics and connection-pool sizing

The API unconditionally installs Prometheus middleware, `/metrics`, request
histograms, endpoint context, and SQLAlchemy pool collectors/listeners, but no
wrapper scraper consumes them. Removing that path needs an Onyx-internal patch
and may affect pool-timeout or tenant context, so defer it unless profiling
shows meaningful request CPU/RSS cost. Any future gate must preserve the
functional pool-timeout exception handler and all non-metrics tenant context,
then test ordinary/error/streaming requests and deliberate pool exhaustion.

Also defer changes to `CELERY_BROKER_POOL_LIMIT`, the API's sync/async/read-only
SQLAlchemy pools, Discord's pool, Redis's lazy connection cap, and PostgreSQL
`max_connections`. These primarily affect capacity and failure behavior, not
proven idle power. Promotion requires peak connection/socket measurements for
chat, ingestion, Admin, bots, migrations, connector concurrency, reconnect,
and failure recovery, followed by a written budget with recovery headroom. No
pool-wait/timeout regression or exhausted database is acceptable.

### Worker consolidation

The six retained functional Celery workers import substantial Python runtimes,
but merging light, heavy, and user-file queues can cause missing task
registration, head-of-line blocking, and changed acknowledgement/failure
isolation. Consider consolidation only if post-change proportional-set-size
measurement still makes those duplicate runtimes a major cost.

Before promotion, derive the exact union of task modules and compare queue
routing, `acks_late`, prefetch, time limits, pool type, signals/init hooks,
memory limits, cancellation, and failure isolation. Task-registry/routing
contracts plus simultaneous chat, deletion, user-file, connector, heavy work,
and worker-kill/redelivery must pass. Publish-to-start and completion latency
for each class must stay within the six-worker baseline; otherwise retain six.

### Supervisor log relay and enabled-bot micro-tuning

Direct-to-stdout supervisor logging could remove one `tail -F` process and
intermediary writes, but it has small expected value and risks losing the
separately listed MCP logs. Promote it only if disk-I/O/wakeup measurement is
material and every log producer is mapped. Preserve startup/failure diagnostics,
Docker-layer rotation, ordering expectations, and bounded log storage.

Default-off Slack/Discord processes are in the initial bundle; enabled-bot
cadence, Slack metrics, no-tenant heartbeat, and Discord pool changes are not.
They save nothing in the default stack and modify optional capability internals.
Only revisit them using an enabled-bot benchmark, preserving dynamic database
configuration, command-driven refresh, message latency, reconnect, and Slack's
15-second owner heartbeat/60-second failover contract.

## Initial-bundle expected result and measurement

Approximate steady-state changes:

| Source | Current | Proposed |
|---|---:|---:|
| Container health/recovery checks | ~73/min | ~4/min |
| Non-VPN container checks | ~65/min | ~2/min |
| Myst route/MTU maintenance | DNS, two route writes, and two success logs every 20s in the observed configuration | DNS and exact route validation every 20s; route writes and success logs only on missing/drifted state |
| Onyx Celery scheduled jobs | ~32/min | ~2/min |
| Embedding health inferences | 120/hour | 0/hour |
| Bundled MLX model residency | handler ~1.36 GiB RSS indefinitely after start | unload after 10m idle; reload on demand |
| Craft jobs while disabled | 3/min | 0 |
| Self-hosted monitoring jobs | ~6.4/min plus scrape-triggered work | 0 |
| Queue-observability Redis operations | at least ~136/min, plus uncached Prometheus scrapes | 0 |
| Empty Celery Redis `BRPOP` reissues | about one/second per idle consumer | unchanged pending measurement |
| Celery worker event heartbeats | about 240/min across eight pinned workers | 0 |
| Per-worker liveness-file writes | about 32/min across eight pinned workers | 0 |
| Beat liveness/queued heartbeat | file touch on 1m reload plus queued Redis heartbeat every 1m | one consumed local file touch every 5m; no Redis heartbeat task |
| Dormant bot database/configuration polls | Discord 12/min plus Slack 1/min | 0 by default |
| Worker Prometheus listeners | one per retained app that calls `start_metrics_server` | 0 by default |
| API Prometheus path | middleware, `/metrics`, histograms, DB collectors/listeners on every run | unchanged pending measurement |
| Supervisor log relay | one `tail -F` plus duplicate rotating per-program writes | unchanged pending measurement |
| Supervisor Celery workers | 8 | 6 |
| Unpatched background control processes | none | entrypoint and watchdog use `python -S`; exact supervisor argv skips the background bootstrap |
| SearXNG multiprocessing resource tracker | imported the complete SearXNG patch bootstrap | exact resource-tracker command skips application patches |
| OpenSearch JVM heap | fixed 2 GiB | 1 GiB if the full workload passes; otherwise smallest larger passing value |
| OpenSearch plugin schedulers | five-/15-minute and hourly activity observed | plugin set unchanged; disable only the Performance Analyzer agent CLI |
| MinIO scanner cycle | 1m default | 30m supported `slowest` profile |

About two of the remaining container checks per minute would be the local Myst
health and autoheal loops. Everything else would wake approximately once per
ten minutes. None of these checks should emit public DNS or upstream traffic.

The container-health and Celery rate rows are provisional estimates and must not be used
as acceptance criteria until generated from every effective Compose model.
Add a deterministic inventory that lists each effective health command,
steady/start interval, profile, and checks per minute, and have the plan's
final measured table come from that artifact. Count disabled profiles and
duplicate aggregate/core probes correctly. Likewise, confirm Celery event
counts from Redis command/event deltas on the exact pinned image rather than
assuming the nominal two-second default survived Onyx configuration.

Rows marked unchanged are deliberate controls, not unfinished initial work.
Use their post-change measurements to decide whether any deferred proposal is
worth its added patch surface or capability risk. The initial release is
successful without touching them.

The pre-change one-shot live sample (background ~2.9 GiB and 212 PIDs;
OpenSearch ~2.8 GiB and 125 PIDs; API ~688 MiB; MinIO ~230 MiB; SearXNG ~220
MiB) is diagnostic context, not a trustworthy baseline by itself. Capture at
least three comparable pre-change idle windows before implementation. After
the entire initial bundle—not after a selectively favorable subset—capture at
least three matching post-change windows using cgroup memory/CPU, host package
power, process proportional set size where available, disk I/O, network/DNS
traffic, and wakeup/thread counts. Use the same warm-up, stack mode, workload,
observation duration, host power state, and external upstream availability.
Each idle window must exceed 35 minutes so it includes MLX unload, multiple
ten-minute health cycles, and a complete slow MinIO scanner cycle; include one
matched window longer than 65 minutes to classify retained hourly work. Report
median and range plus image digests. Avoid adding child RSS figures as though
shared pages were unique memory.

The 2026-07-20 focused baseline identified application imports in processes
that never execute application work. In the background container, proportional
set size was approximately 197 MiB for supervisor and 192 MiB for the local
watchdog; the watchdog had 16 threads and mapped NumPy/OpenBLAS/tokenizer
libraries. SearXNG's multiprocessing resource tracker used approximately 59
MiB PSS. Myst emitted 30 route-exemption success lines in five minutes and
performed the corresponding two replacements every 20 seconds. After the
change, supervisor/watchdog PSS was approximately 27/18 MiB and the SearXNG
resource tracker was approximately 10 MiB PSS: about 384 MiB combined PSS
removed from these three non-application processes. Myst made only the
initial/new-DNS-address route changes described in Current status. These are
focused diagnostic comparisons and do not replace the controlled stack-wide
measurement gate above.

Promote a deferred high-fragility item only when measurement and a bounded
prototype support a meaningful remaining benefit: normally at least 10% of
median idle package power beyond run-to-run noise, at least 500 MiB unique/PSS
memory, or a dominant sustained source of wakeups/I/O with a demonstrated host
power effect. A large command count alone is not sufficient. Record a decision
for every deferred item—promote with evidence or leave deferred—so the gate
does not gradually become an unchecked backlog.

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
  expect `example.com` DNS or upstream-proxy authentication probes. Assert no
  final-hop proxy health command exists, each distinct bridge still performs
  its local aggregate 403 check, and patched DNS plus upstream-connect helpers
  fail the test if any health path calls them.
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
  one `/ready` request with no retry loop, and on a fresh stack creates
  API/background only after success. Exercise failure and repeated `make
  up-full` calls so an old successful result cannot be reused and existing
  API/background services are neither recreated nor stopped on failure.
- Add host embedding lifecycle tests for strict proxy paths/body bounds,
  serialized cold start, single forwarding, no ambiguous POST retry, ten-minute
  post-completion idle unload, request/shutdown race, process identity, crash,
  repeated up/down, manual/custom upstream ownership, and reload correctness.
  Record host/model memory release and cold-start latency against shim/Onyx
  timeouts.
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
  one consumed Beat liveness-file write and no per-worker file writes, absence
  of the queued Beat heartbeat, disabled Craft and monitoring entries, exactly
  six retained Celery workers, optional bot sections, and the chosen
  concurrency values. Source-text matching alone is insufficient. Assert the
  existing log relay and all retained log inputs remain intact.
- Add deterministic bot tests for both feature flags, enabled/disabled
  supervisor output, exact restoration of the unmodified upstream program/log
  definitions when enabled, DB-managed configuration, message handling,
  reconnect, and active heartbeat preservation.
- Add an idle live-stack observation after startup: snapshot Redis
  `INFO commandstats` and relevant queue/pubsub state before and after at least
  11 minutes without running `MONITOR` continuously. There must be no
  monitoring-task dispatch, queue/process/Redis metrics collection, Celery
  worker event heartbeat/gossip traffic, per-worker liveness-file timestamp
  changes, or dormant-bot polling. The single Beat file should advance only at
  its five-minute reload; the replacement watcher must cause no Redis traffic.
  Record functional queue reads separately; active indexing, deletion, primary
  leadership, and recovery flows are expected traffic and are not failures.
- Add live task cancellation/revocation, worker-kill/redelivery, and active
  index-attempt heartbeat recovery tests to prove removal of the Celery event
  plane did not remove a correctness mechanism.
- Add a focused watchdog contract covering Beat file cadence, safe path/type,
  ownership, symlink rejection, check interval, stale threshold, startup
  grace, missing/future-mtime states, supervisor restart failure, and the
  calculated restart bound. Assert it imports no Onyx, Redis, Celery, or
  SQLAlchemy modules.
- Keep Docker Engine/Compose and Podman capability tests that inspect the
  runtime health configuration and observe fast startup checks followed by a
  slow steady interval. Merely accepting/rendering `start_interval` is not a
  pass; the Podman contract test must inspect `StartupHealthCheck` and the
  ordinary 10-minute or Myst one-minute interval separately.
- Add effective-Compose and live OpenSearch tests for the 1 GiB heap and
  disabled Performance Analyzer agent CLI. Assert the exact plugin list,
  Security/TLS settings, refresh/index settings, and image remain unchanged;
  run the fresh/existing-volume KNN/hybrid/ingestion/restart workload and the
  GC/native/cgroup stability observation described above.
- Add MinIO effective-Compose and live object-flow tests for
  `MINIO_SCANNER_SPEED=slowest`, retained readiness, lifecycle/healing
  behavior, and the observed 30-minute scan cadence.
- Assert standalone background worker Prometheus listeners are disabled while
  the API metrics route/middleware/collectors remain unchanged. Record, but do
  not tune, broker/database connection and Redis `BRPOP` baselines for the
  post-measurement decision.

Update behavior documentation in the same change:

- `docs/vpn_routing_and_proxies.md`: replace its claim that health validates
  the provider-resolver data path; document offline TequilAPI/local-route
  health, one-minute autoheal timing, stale health limitations, and the fact
  that real requests remain fail-closed. Explicitly state that the existing
  20-second route/MTU correctness cadence and repair bound are unchanged while
  exact matching routes no longer cause writes or success logs.
- `docs/local_docs_rag_search.md`: change Compose readiness from repeated
  `/ready` to local `/health`, and document the staged shim-only startup,
  single pre-API/background `make up-full` `/ready` request, and failure
  behavior. Also document the selected OpenSearch heap, unchanged plugin image,
  and MinIO scanner profile, including user-visible performance/freshness
  trade-offs.
  Document the wrapper-managed MLX idle unload, ten-minute residency window,
  cold-start delay, failure behavior, and custom/manual-upstream exclusions.
- `docs/onyx_patch_info.md`: document exact schedule rewrites, removal of all
  self-hosted monitoring jobs/worker/collectors, unused file probes, Celery
  event heartbeat/gossip, the corrected Craft removal, bot policy, concurrency
  defaults, standalone worker metrics disable, and watchdog ownership. Clearly
  distinguish removed observability from retained functional queue inspection,
  broker health, primary leadership, task revocation, and index-attempt
  heartbeats.
- `docs/onyx_patches_upgrade.md` and `stack.versions.env`: add every schedule
  name/upstream cadence; the pinned Beat scheduler, app bootstep, monitoring
  modules, Celery worker commands, supervisor sections/log tails, bot loops,
  and watchdog constants; immutable Myst/Autoheal/Tailscale pins; and the exact
  Teep/MinIO source audit. Require effective versioned-Beat/derived-supervisor
  validation and add or update Makefile targets rather than hand-building
  component images.
- `README.md`: mention the five-minute maximum background discovery delay and
  staged one-shot full-mode embedding validation, default-off Slack/Discord
  processes, Docker Engine 25 plus Compose 2.20.2 minimums, and the delayed
  processing of newly uploaded project/assistant files as user-visible
  consequences; keep implementation details in the subsystem docs.
- `.env.wrapper.example`: add only documented default-off Slack and Discord
  feature switches; do not add credentials, cadence knobs, or a generic power
  mode.
- `AGENTS.md`: update the runtime map and validation invariants if the final
  health or background behavior changes its current claims.

Do not update behavior docs as if a deferred item were implemented. Deferred
requirements remain only in this plan until a post-measurement promotion is
approved and completed.

`make check` is the deterministic pre-handoff gate. This plan deliberately
changes component pins, so follow the documented `make upgrade` flow for those
phases and run `make check-upgrade` against the newly produced images.
Runtime-patch-only iterations may use `make check-upgrade` against the current
pins, but the final validation cannot claim that no pin changed.

## Implementation order

Implement and validate in dependency-safe phases:

1. Establish reproducible authority: pin the exact tested Autoheal/Tailscale
   artifacts; establish a behavior-equivalent Myst source pin or preserve the
   tested image digest without rebuilding it; synchronize exact Teep and MinIO
   sources; reconcile the Teep fallback; and capture repeatable idle/workload
   baselines before changing behavior.
2. Replace Myst's online DNS readiness with direct local TequilAPI plus
   interface/route validation. Move Myst health and autoheal polling to one
   minute, disable autoheal's inherited health, update deterministic tests,
   and live-test reconnect plus autoheal before changing other probes. Do not
   change the route/MTU loop.
3. Remove final-hop proxy health and rewire every distinct bridge as the
   aggregate local readiness owner. Prove that neither direct nor
   configured-upstream modes produce synthetic health traffic.
4. Apply the exact Compose disposition table: remove leaf checks and move all
   retained ordinary checks to 10 minutes with service-specific fast-start
   grace. Collapse the Obscura, SearXNG, and doc-drop duplicate probes and
   verify every effective mode and optional overlay.
5. Replace embedding health with `/health`, add the staged shim-only startup
   and single pre-API/background `up-full` `/ready` helper, then install the
   wrapper-managed MLX lifecycle proxy and prove idle unload/reload with zero
   keep-warm inference traffic.
6. Override OpenSearch to a 1 GiB heap, set only the supported Performance
   Analyzer agent-disable flag, and apply MinIO's supported `slowest` scanner
   profile. Assert the OpenSearch image, plugin, Security/TLS, refresh, index,
   and audit configuration are otherwise unchanged.
7. Apply the strict Beat rewrite: five-minute discovery/reload, remove all
   self-hosted monitoring and queued Beat-heartbeat tasks, remove
   disabled-Craft tasks, and install the local-file watchdog. Validate the
   effective versioned Beat schedule against the actual pinned image.
8. Install the single derived-supervisor entrypoint: remove monitoring and
   disabled-Craft workers/log tails, disable Celery heartbeat/gossip, remove
   unused worker file probes, and apply explicit default-off bot policy. Test
   task cancellation/redelivery and bot opt-in before right-sizing worker
   concurrency and disabling standalone worker metrics. Leave API metrics,
   connection pools, Redis transport internals, log routing, enabled-bot
   internals, and the six-worker topology unchanged.
9. Complete all cross-referenced behavior and upgrade documentation, then run
   deterministic, pinned-image, upgrade, and live validation. Capture the
   controlled post-change measurement before considering any deferred item.

Validation should prove:

- Lite and full startup remain fast.
- A fresh `make up-full` validates the embedding backend exactly once before
  creating API/background and makes no later idle requests. On a repeated
  invocation, failure neither restarts nor stops already-running services.
- The wrapper-managed MLX handler exits and releases measured model memory
  about ten minutes after the last completed request, reloads once for
  concurrent cold callers, and never takes ownership of a manual/custom
  upstream.
- A daemon-reported VPN disconnect or missing local tunnel/interface becomes
  unhealthy and autoheals within the documented multi-minute bound.
- Packet capture and resolver/proxy logs show no DNS query, public connection,
  or configured-upstream connection caused by any health check during an idle
  observation window longer than the ten-minute health interval.
- Myst health consumes only loopback TequilAPI state and local interface/route
  information, and does not log the sensitive remainder of the TequilAPI
  connection response.
- The existing Myst route/MTU loop and its reconnect/exemption behavior are
  unchanged; only health and autoheal probing changes.
- Broken egress continues to fail closed before its next health check.
- No Craft tasks are scheduled when Craft is disabled.
- No self-hosted monitoring task, worker, monitoring metrics endpoint, queue
  scan, process-memory scan, Redis-health scrape, or worker-event receiver
  remains.
- API Prometheus instrumentation, pool collectors, and connection-pool sizing
  remain unchanged; retained standalone workers expose no metrics listener.
- Retained workers emit no Celery monitoring heartbeat/gossip events and write
  no per-worker liveness files; the sole Beat liveness file advances once per
  reload and is consumed by the stdlib watcher. Broker health, revocation,
  redelivery, primary leadership, and active indexing heartbeats still work.
- Slack and Discord processes are absent by default; when explicitly enabled,
  their program definitions, configuration delays, message handling, and active
  heartbeat/failover behavior match the unchanged pinned upstream behavior.
- Idle scheduled-discovery and housekeeping logs show only the classified
  five- or ten-minute cadence; hourly/daily cleanup remains at its pinned
  cadence.
- A new document or connector begins processing within five minutes.
- `make up-lite` performs no embedding validation; `make up-full` performs
  exactly one before API/background start and returns nonzero without an
  automatic retry when it fails.
- Effective Docker Compose models retain fast startup checks and 10-minute
  steady checks; Podman installs the equivalent native startup check before
  start and fails clearly on any command, timeout, or cadence mismatch.
- Removed leaf checks are absent from the effective model, not merely shadowed
  in one Compose source file.
- The Beat watchdog restarts a deliberately hung Beat process within the
  documented bound and performs no Redis, SQL, Onyx, or Celery polling.
- OpenSearch passes KNN/hybrid/TLS and ingestion tests at the selected heap;
  its image, plugin set, Security/TLS, audit, refresh, and index configuration
  are unchanged, and the Performance Analyzer agent CLI stays disabled.
- MinIO object/document flows pass with the supported slowest scanner profile,
  and idle disk activity reflects the documented 30-minute cadence.
- Broker/database pool sizes, Redis `BRPOP` behavior, six-worker task routing,
  enabled-bot internals, and supervisor log relay remain unchanged and are
  captured only as post-change baselines.
- Exact pinned-reference checks pass for every component; no Teep or MinIO
  conclusion is based on a mismatched reference worktree, and no Myst,
  Autoheal, or Tailscale behavior is attributed to a mutable tag/branch.
