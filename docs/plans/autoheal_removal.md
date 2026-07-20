# Autoheal removal and Myst self-recovery plan

## Purpose

Remove the third-party Docker-socket `autoheal` service and determine whether
its only use—recovering an established Mysterium VPN after sustained local
data-plane failure—can safely be replaced by a narrow, in-container mechanism
that works the same way under Docker and Podman. If Myst-only restart cannot
be proven reliable in the long-lived shared namespace, prefer visible failure
and full-stack operator recovery over preserving unsafe automatic restart.

This plan is intentionally self-contained. A new conversation should be able
to execute it after reading the repository `AGENTS.md` and the subsystem docs
listed below, without relying on earlier discussion.

## Status and scope

Status: planning complete; implementation has not started. The Myst-only
restart design is conditional on the blocking shared-namespace qualification
in Phase 0. Do not implement PID-1 self-termination before that gate passes.

Update this file in place while executing the work. Do not retain superseded
design alternatives merely for history; the final plan should describe the
implemented contract and record any validation that could not be completed.

This work does not change the Myst source revision, VPN provider-selection
policy, registration/funding flow, final-hop destination policy, route/MTU
reconciliation cadence, Compose startup deadline, or ordinary one-minute Myst
health cadence. It also does not remove the unrelated Docker-only code
interpreter socket. Removing an obsolete manifest pin does not authorize
deleting the already-downloaded autoheal image from the user's engine cache.

## Required reading before implementation

Read these files completely before editing:

- `AGENTS.md`
- `README.md`
- `docs/vpn_routing_and_proxies.md`
- `docs/internal_network_security.md`
- `docs/podman_suport.md`
- `docs/onyx_patches_upgrade.md`
- `docs/resource_minimization.md`
- `Makefile`
- `docker-compose.yaml`
- `docker-compose.vpn-autoheal.yml`
- `docker-compose.podman-vpn.yml`
- `myst/docker-compose.yaml`
- `myst/myst-client-entrypoint.sh`
- `myst/myst-readiness.sh`
- the Myst, network-isolation, health-inventory, pinning, and Podman tests under
  `tests/`

Start with `git status --short`. Preserve unrelated and untracked user files,
including private `.env.wrapper`, `docker-data/`, and document sources. The
private environment may be sourced for authorized live validation, but its
contents must not be printed, rewritten, or staged.

## Current behavior and reason for removal

Today, VPN-enabled Docker models add `docker-compose.vpn-autoheal.yml`. Its
`autoheal` container:

- mounts `/var/run/docker.sock` with engine-wide control authority;
- polls Docker health state once per minute;
- restarts only the `autoheal=true` Myst container after it becomes unhealthy;
- exists in both lite and full Docker VPN models;
- is omitted from explicit no-VPN models; and
- is rendered but hidden behind an inactive socket-only profile under Podman,
  where automatic Myst recovery is consequently unavailable.

The actual VPN predicate already lives in `myst/myst-readiness.sh`. In VPN
mode it checks only local state: exact top-level TequilAPI `Connected` status,
a global `myst0` IPv4 address, and a kernel route lookup that selects `myst0`
with the expected source. It performs no public DNS or HTTP probe and does not
log the potentially identifying API body.

`myst-client` already has `restart: unless-stopped`. Its network namespace is
owned by the stable `netns-holder`, so restarting Myst does not invalidate the
final-hop proxies, optional VPN-routed Teep/Tailscale processes, or fixed
bridges. Request-time proxy policy continues to fail closed while Myst is
unavailable.

The autoheal container therefore adds a privileged control-plane dependency,
an image pin, Compose overlays, tests, documentation, and a Docker/Podman
behavior difference without owning any unique health predicate.

### Known shared-namespace restart risk

This repository has previously observed that restarting only
`myst-client-vpn` while the rest of the stack remained running could leave Myst
unable to reconnect. The restarted entrypoint cycled through providers, while
a complete stack teardown/recreate recovered it. The root cause was not proven,
but the long-lived shared namespace is a credible cause.

`myst-client` uses `network_mode: service:netns-holder`. Restarting Myst does
not create a fresh network namespace; it rejoins the namespace still owned by
the unchanged holder. Kernel networking state belongs to that namespace, not
to the Myst process. Depending on how gracefully the old daemon cleaned up,
the following can survive or be left partially configured:

- the `myst0` interface and its address;
- the `0.0.0.0/1` and `128.0.0.0/1` split-default routes;
- provider/control-plane and wrapper route exemptions;
- policy rules, WireGuard/UAPI state, ipsets, and Myst firewall chains/rules;
- resolver/runtime files; and
- connection/session state persisted below the Myst data directory.

The proposed health wrapper changes who requests a restart, not what an
engine restart does. Signaling PID 1 and relying on `restart: unless-stopped`
would therefore be vulnerable to the same failure unless graceful cleanup and
same-namespace re-entry are explicitly qualified. The armed marker and
60-second failure window prevent startup restart churn; they do not normalize
the shared namespace.

## Target behavior

### Recovery state machine

Keep `myst-readiness.sh` as the pure, side-effect-free readiness predicate. Add
a small stdlib/shell-only wrapper, tentatively
`myst/myst-healthcheck.sh`, as the single Compose health command.

The PID-1 termination behavior below is the preferred design only if Phase 0
proves repeated Myst-only restart reliable and fail-closed. If that gate fails,
follow the fallback decision in Phase 0 rather than implementing this state
machine unchanged.

The wrapper must implement this state machine:

1. At every Myst entrypoint start, clear only the wrapper's two exact runtime
   state files. A Docker/Podman restart reuses the container writable layer, so
   this reset is mandatory; stale armed state must not survive a restart.
2. In explicit no-VPN mode, execute the existing readiness predicate and
   return its status without arming or requesting a restart.
3. In VPN mode, before the first successful readiness result in the current
   container process lifetime:
   - return readiness failures normally;
   - do not signal PID 1; and
   - do not create a hidden restart loop during registration, funding,
     provider selection, initial tunnel establishment, or a visible failed
     `make up-*`.
4. The first successful VPN readiness result atomically marks the current
   process lifetime as armed and clears any failure timestamp.
5. Every later successful result clears the failure timestamp.
6. The first failure after arming records the integer monotonic uptime from
   `/proc/uptime` and returns the readiness failure.
7. Further failures continue returning unhealthy. Only after failure has been
   continuous for at least 60 seconds may the wrapper emit one concise,
   non-secret diagnostic and send `SIGTERM` to container PID 1.
8. The entrypoint must reliably handle that `SIGTERM`, stop its route
   reconciliation child and Myst daemon, reap both, and exit. The existing
   `restart: unless-stopped` policy then restarts Myst under Docker or Podman.
9. A successful probe at any point resets the failure window, so a transient
   failure never contributes to a later unrelated failure.

Use elapsed monotonic time, not a count of invocations. Docker startup probes
run every five seconds, while steady probes run every minute; counting two
calls could otherwise turn the intended sustained runtime failure policy into
a ten-second restart immediately after startup. A 60-second continuous-failure
window gives approximately one to two minutes from an arbitrary disconnect to
restart, depending on when the next regular check begins.

The health wrapper should support a narrow test interface for an alternate
target PID and state directory, preferably explicit `reset` and `check`
subcommands with positional arguments. Production Compose must always use PID
1 and a fixed non-mounted runtime directory. Do not expose a new user-facing
`.env.wrapper` option for the target PID, state path, interval, or grace period.

### Visible startup contract

This design deliberately distinguishes startup from recovery:

- a daemon process crash is still handled by `restart: unless-stopped`;
- a live daemon that never becomes VPN-ready has never armed self-recovery;
- `make up-lite` or `make up-full` remains visibly blocked until its existing
  420-second Compose wait fails, leaving container health and logs available;
- registration/funding waits are not periodically destroyed and restarted;
- invalid providers or exhausted initial connect attempts remain visible
  startup failures rather than hidden restart churn; and
- only a VPN that proved fully ready once in the current process lifetime can
  self-terminate after later sustained failure.

Do not add a second watchdog loop that separately reruns readiness. The engine
healthcheck must remain the sole periodic predicate evaluation. The wrapper
adds state and enforcement around that one result; it does not duplicate it.

### Failure and security properties

Preserve these invariants:

- Onyx applications never gain a direct public route or join `netns-holder`.
- A failed VPN remains fail-closed at the final-hop proxy; restart is recovery,
  not an alternate route.
- No public probe, provider API body, identity, location, credentials, or
  private route details are added to logs.
- The replacement receives no Docker or Podman socket and no new capability.
- Only PID 1 in the same Myst container may be signaled in production.
- The health wrapper cannot signal before one complete successful VPN
  readiness result in that process lifetime.
- Explicit no-VPN mode never arms VPN self-recovery.
- Manual `docker stop`, `podman stop`, and Compose down remain respected by
  `unless-stopped` and must not cause an unintended restart.
- `netns-holder` remains the namespace owner. Do not move namespace ownership
  back to Myst or recreate dependent services merely because Myst restarts.
- Exact route/MTU reconciliation remains the single existing 20-second,
  change-only loop. Self-recovery must not add another route repair owner.

If health-wrapper state is missing, treat the process as unarmed. If the
failure timestamp is malformed, log a generic local-state warning and replace
it with the current monotonic timestamp; do not signal immediately based on
corrupt state. Failure to create/update the private runtime state must make the
healthcheck fail loudly rather than silently disabling recovery.

## Implementation phases

### Phase 0: blocking same-namespace restart qualification

Run this phase before writing the self-heal wrapper. It is a release gate, not
optional live validation after the design has already been selected.

#### Establish the exact namespace lifecycle

On a healthy VPN-enabled Docker stack, record without exposing provider,
identity, or private document data:

- Myst and `netns-holder` container IDs and restart counts;
- `HostConfig.NetworkMode` for Myst;
- the network-namespace inode seen from Myst and `netns-holder`;
- interface names, address/prefix structure, route/rule structure, and counts;
- presence/counts—not unredacted contents—of Myst firewall chains, ipsets,
  WireGuard/UAPI sockets, and wrapper route exemptions;
- TequilAPI connection state and ordinary Myst health; and
- one representative routed request proving the VPN path works.

Store any raw snapshots containing endpoint addresses only in a private
temporary directory and remove them after comparison. Handoff output should
contain structural/redacted differences, not provider IPs or identity data.

Confirm directly that Myst and the holder see the same namespace inode and
that the inode and holder container remain unchanged through a Myst-only
restart. This proves the test exercises the risky lifecycle rather than a
fresh namespace.

#### Reproduce the exact proposed restart path

Do not begin with an abrupt kill. Exercise graceful `SIGTERM` delivery to
Myst's wrapper PID 1—the same path the proposed health wrapper would use—and
allow `restart: unless-stopped` to restart the container. Repeat at least three
times from a fully healthy state. For every cycle:

1. Confirm PID 1 forwards termination to the daemon and waits for shutdown.
2. Capture whether `myst0`, split routes, firewall/ipset state, UAPI sockets,
   and exemptions were removed or normalized before the new connection starts.
3. Confirm the restarted Myst process sees the same holder namespace inode.
4. Inspect logs for stale-interface, existing-connection, route, firewall,
   UAPI, and provider-attempt failures without printing provider identities.
5. Require readiness to recover within the configured connection-attempt
   budget without exhausting/sweeping the full provider set.
6. Require a real routed request to succeed after health returns.
7. Confirm `netns-holder`, policy proxies, bridges, and unrelated application
   containers were not recreated.

Also test one runtime disconnect followed by graceful Myst-only restart. An
abrupt daemon/PID-1 `SIGKILL` is a separate crash case: characterize it, but do
not claim the planned graceful recovery handles unclean kernel state unless it
also passes. Existing engine restart policy may restart a crashed process, but
the wrapper must not promise successful reconnection after cleanup was
impossible.

During every transition, continuously test fail-closed behavior for both the
ordinary direct-Myst final hop and, when configured, an upstream-proxy final
hop. The interval between old tunnel teardown and new readiness must never use
the container-engine bridge as a direct fallback. Do not assume the Myst kill
switch remains installed during graceful disconnect/shutdown; inspect and
test it.

#### Qualification pass criteria

The PID-1 restart design may proceed only if all of these hold across repeated
cycles:

- graceful shutdown completes within a documented bound;
- the old connection's interface, split routes, UAPI state, and session-owned
  rules are absent or are demonstrably and correctly adopted by the new daemon;
- wrapper exemptions contain exactly the intended current entries without
  duplication or stale provider routes;
- Myst reconnects without cycling through all providers;
- local readiness and a real routed request both recover;
- traffic remains fail-closed throughout;
- the holder namespace remains stable; and
- the behavior is repeatable rather than a single successful restart.

Repeat the same qualification under Podman before claiming engine parity. A
Docker pass does not prove Podman because signal delivery, restart policy,
health scheduling, and shared-namespace implementation can differ.

#### Decision if the gate fails

Do not paper over failure with blind deletion of `myst0`, broad route flushes,
iptables resets, extra provider retries, or repeated container restarts. Those
can either remove another process's state or briefly expose the direct bridge
route.

Use this decision order:

1. Diagnose graceful shutdown first. Verify the shell forwards `SIGTERM`, the
   pinned Myst daemon runs its supported shutdown/connection cleanup, and the
   wrapper waits for actual completion. A bounded cleanup improvement is
   acceptable only if failure remains visible and does not fall through to an
   unclean restart.
2. If supported `myst connection down` is considered before daemon shutdown,
   prove that its cleanup finishes and that the namespace remains fail-closed
   for the entire disconnected interval. Never assume the kill switch remains.
3. Add a pre-start validator that detects stale `myst0`, split routes, UAPI,
   or firewall state and stops before provider selection. Detection is
   valuable even when automatic cleanup is unsafe; it prevents another opaque
   full-provider sweep. Because `restart: unless-stopped` would turn a plain
   preflight exit into another restart loop, the failure path must remain
   alive-but-unready with one clear diagnostic (or use another explicitly
   proven non-looping mechanism) until the operator performs the documented
   full-stack recovery.
4. Only implement narrow stale-state cleanup if every artifact has exact
   ownership criteria, deterministic tests, and a separately maintained
   fail-closed guard covering the cleanup/reconnect window. Broad namespace or
   firewall cleanup is out of scope.
5. If reliable fail-closed Myst-only restart still cannot be proven, remove
   autoheal without replacement restart enforcement. Keep ordinary health
   visibility, document that recovery requires the matching full
   `make down-*` then `make up-*`, and retain the visible startup/runtime
   failure. This is the default safe fallback.

An in-process reconnect controller may be evaluated as a separate design
because it lets the existing daemon clean up state it owns, but do not add it
casually. It must serialize with startup/provider selection, reuse one
connection-attempt owner, preserve the kill switch, bound retries, and avoid a
second polling loop. If it cannot satisfy those properties more simply than
the fallback, defer it.

### Phase 1: deterministic Myst health supervisor

Proceed with this phase only after the Phase 0 restart gate passes. Add
`myst/myst-healthcheck.sh` and focused tests before removing autoheal. If Phase
0 selects the no-automatic-restart fallback, keep `myst-readiness.sh` as the
direct health command and omit the supervisor/state files entirely.

The script should:

- use strict shell error handling;
- delegate the complete predicate to `/usr/local/bin/myst-readiness.sh`;
- pass through the readiness exit status and existing non-secret diagnostics;
- keep an armed marker plus one first-failure monotonic timestamp under a fixed
  private directory in `/run` or `/var/run`;
- use exact file paths and reject unexpected state-file types/symlinks;
- make state transitions safe if an engine ever overlaps startup and regular
  probes, without adding a persistent lock process or a stale-lock failure
  mode;
- clear state through an explicit `reset` action called by the entrypoint;
- never persist state in `docker-data` or another bind mount;
- signal the explicit target PID only after the armed/grace checks; and
- return unhealthy even when the signal succeeds, so inspection during the
  restart window never reports a false healthy result.

Update `myst/myst-client-entrypoint.sh` to reset the state before either the
VPN or no-VPN branch begins. Make its `INT`/`TERM` handling unambiguous: disable
recursive traps, terminate the exact route-reconciliation and daemon children,
wait for both, and exit. Preserve all registration, funding, provider
selection, connect retry, MTU, route-exemption, and no-VPN behavior.

Add deterministic tests, preferably in a new `tests/test_myst_self_heal.py`,
covering:

- reset removes only the two exact known state files;
- VPN failure before first success returns unhealthy but does not signal;
- first success arms recovery;
- success clears a previous failure timestamp;
- one failure after arming does not signal;
- repeated probes inside 60 seconds do not signal, even at five-second startup
  cadence;
- continuous failure at or beyond 60 seconds signals only the supplied test
  process;
- a successful probe between failures starts a fresh grace window;
- no-VPN success/failure never arms or signals;
- malformed, missing, non-regular, and symlink state files fail safely as
  specified;
- readiness diagnostics do not expose a supplied mock API body; and
- signal/cleanup behavior leaves no test child process behind.

Use temporary directories and disposable `sleep` processes. Never signal the
test runner or host PID 1.

### Phase 2: Compose integration and autoheal deletion

If Phase 0 passes, in `myst/docker-compose.yaml`:

- mount the new health wrapper read-only;
- change the health command from direct `myst-readiness.sh` execution to the
  wrapper's production `check` action targeting PID 1;
- keep the one-minute regular interval, five-second startup interval,
  ten-second timeout, two retries, and 420-second startup period unless live
  evidence requires a separately reviewed change; and
- preserve `restart: unless-stopped`.

If Phase 0 selects the safe fallback, leave the direct
`myst-readiness.sh` health command and its existing cadence intact. Do not
mount or invoke an unused health supervisor. `restart: unless-stopped` still
handles an ordinary daemon process exit, but documentation must not promise
that a Myst-only restart recovers a stale shared namespace.

In `docker-compose.yaml`, remove the `autoheal: "true"` label from
`myst-client`. Do not add a replacement label, socket, privileged helper,
sidecar, or network.

In the `Makefile`:

- remove `VPN_AUTOHEAL_OVERRIDE_FILE`;
- remove `VPN_AUTOHEAL_SUFFIX` and `PODMAN_VPN_COMPOSE_SUFFIX` selection;
- remove the associated Docker/Podman comments;
- remove both suffixes from `LITE_FILES` and `FULL_FILES`; and
- preserve all unrelated Podman, proxy, Teep, Tailscale, executor, secret, and
  stack-mode layering.

Delete:

- `docker-compose.vpn-autoheal.yml`
- `docker-compose.podman-vpn.yml`

Remove `AUTOHEAL_IMAGE` from `stack.versions.env`. This is deletion of an
unused support-image pin, not an image/source upgrade; do not run the broad
upgrade flow or pull a replacement image.

After rendering every mode, assert that no effective service mounts
`/var/run/docker.sock` except the existing Docker-only code interpreter path
where intentionally enabled. In particular, Myst recovery must introduce no
container-engine socket under either engine.

### Phase 3: replace and expand deterministic contracts

Update `tests/test_onyx_network_isolation.py`:

- replace overlay-selection tests with assertions that lite/full,
  VPN/no-VPN, and Docker/Podman Makefile models never mention either deleted
  autoheal overlay;
- assert every effective model has no `autoheal` service and Myst has no
  `autoheal` label;
- after a passing Phase 0, assert the effective Myst health command is the new
  wrapper with production PID/state arguments; under the fallback, assert it
  remains the pure readiness script and that no self-heal wrapper/state
  contract exists;
- assert `myst-client` retains `restart: unless-stopped`;
- retain the exact one-minute regular health cadence and startup-health
  contract;
- update aggregate health inventories so `autoheal` is not listed as a
  disabled service; and
- retain all routing, namespace, socket, and fail-closed assertions.

Update `tests/test_immutable_component_pins.py` so only still-used support
images are required. Add a negative assertion that `AUTOHEAL_IMAGE` does not
return to the manifest or Compose files.

Update Podman lifecycle/capability tests to assert there is no Podman VPN
socket-suppression overlay because there is no socket-dependent recovery
service to suppress. Keep code interpreter's independent Docker-socket
restriction intact.

Add repository-wide negative checks for:

- `docker-compose.vpn-autoheal.yml`
- `docker-compose.podman-vpn.yml`
- `AUTOHEAL_IMAGE`
- `autoheal=true` / the Compose label
- the `willfarrell/autoheal` image

Do not ban explanatory use of the word “autoheal” in this plan or concise
migration documentation until implementation is complete.

### Phase 4: active documentation updates

Update all active documentation in the same change:

- `README.md`: remove the Docker/Podman autoheal difference. State that both
  engines have the same behavior without a container-engine socket. Promise
  automatic post-readiness recovery only if both engines pass Phase 0;
  otherwise document visible failure and matching full-stack restart.
- `AGENTS.md`: remove `docker-compose.vpn-autoheal.yml` and the Podman VPN
  overlay from key locations. Document the selected recovery invariant and,
  only when Phase 0 passes, identify the new wrapper alongside
  `myst-readiness.sh`.
- `docs/vpn_routing_and_proxies.md`: replace the autoheal lifecycle text with
  the selected result: either the exact qualified state machine and recovery
  window, or the visible-failure/full-stack-restart fallback. In both cases
  document unchanged fail-closed routing and Docker/Podman parity.
- `docs/podman_suport.md`: remove the Podman VPN overlay and socket limitation
  section for autoheal. Document the qualified common recovery behavior, or
  the common manual full-stack recovery fallback. Keep code interpreter's
  distinct socket limitation.
- `docs/onyx_patches_upgrade.md`: remove autoheal from immutable support-pin
  validation and add the selected Myst lifecycle, shared-namespace preflight,
  and fault checks to the Myst upgrade checklist.
- `docs/resource_minimization.md`: update the current Myst health/recovery
  control and its regression checks to match the selected qualified recovery
  or visible-failure fallback.
- `docs/internal_network_security.md`: update only if its socket-authority or
  residual-risk inventory changes after inspection.

Audit `docs/plans/implemented/` with `rg -i autoheal`. These documents describe
older implementation phases, but unqualified present-tense claims must not
contradict the repository. Remove obsolete autoheal-specific operational text
or add a concise local supersession note pointing to this plan/current VPN
documentation. Do not expand them with another long history.

Do not edit untracked files under a top-level `plans/` directory unless the
user separately places them in scope. They are not repository documentation
when `git ls-files` does not list them.

Finish with repository-wide searches for `autoheal`, `AUTOHEAL`, both deleted
filenames, and `/var/run/docker.sock`. Every remaining match must be either
this plan, an explicitly marked historical statement, or an intentional
Docker-only code-interpreter socket reference.

### Phase 5: deterministic validation

Run:

```sh
make check
make health-inventory
```

Also render the Makefile-selected effective Compose model for this complete
matrix without reading or printing private values:

| Engine | Mode | VPN | Required result |
| --- | --- | --- | --- |
| Docker | lite | enabled | qualified wrapper recovery, or direct health fallback; no autoheal/socket |
| Docker | full | enabled | same selected contract; full services unchanged |
| Docker | lite | disabled | no self-recovery arming; direct-route readiness |
| Docker | full | disabled | same; full services unchanged |
| Podman | lite | enabled | same selected contract as Docker |
| Podman | full | enabled | same selected contract plus Podman host document server |
| Podman | lite | disabled | no self-recovery arming |
| Podman | full | disabled | no self-recovery arming plus host document server |

For each model verify:

- no `autoheal` service, label, image, profile, dependency, or socket mount;
- Myst remains in `network_mode: service:netns-holder`;
- Myst retains `restart: unless-stopped` and the exact selected health command;
- VPN/no-VPN environment selection is unchanged;
- retained healthcheck commands and startup/steady cadences match the health
  inventory; and
- Podman's native startup-health translation accepts the new command exactly.

Run `make test-images` only if the implementation changes runtime patches or
image contracts beyond the bind-mounted Myst scripts. Do not run `make
upgrade` merely because the unused autoheal pin was removed.

### Phase 6: live Docker validation

Use the running stack only after deterministic checks pass. Source
`.env.wrapper` without printing it. Start from a clean matching mode when
practical and record the initial Myst and `netns-holder` container IDs and
Myst restart count.

Validate both lite and full if time permits; at minimum validate the mode
affected by the current environment and render the other one deterministically.

Required healthy-path checks:

1. `make up-*` completes with Myst healthy.
2. The effective stack contains no autoheal container.
3. No recovery component mounts the Docker socket.
4. The first successful healthcheck creates armed state only inside Myst.
5. Stable health checks do not rewrite/log recovery state continuously beyond
   the necessary success reset.
6. Representative SearXNG and configured inference/embedding traffic still
   uses the expected VPN/final-hop route.

Required runtime-disconnect fault:

1. After Myst has been healthy and armed, induce a supported disconnect (for
   example the Myst CLI `connection down`) without stopping `netns-holder`.
2. Confirm requests fail closed during the outage—no system-route or direct
   fallback.
3. Under the qualified wrapper design, confirm one failure does not immediately
   restart Myst, continuous failure for at least 60 seconds causes the clear
   diagnostic and qualified graceful restart path, and Myst reconnects without
   provider sweep while `netns-holder` remains unchanged.
4. Under the fallback, confirm Myst remains visibly unhealthy and does not
   enter an automatic restart/provider loop. Then use the documented matching
   full `make down-*`/`make up-*` recovery and confirm service restoration.
5. Re-run a representative routed request after the selected recovery path.

Required startup-visibility checks should use safe disposable configuration or
a dedicated fault harness, not the user's funded identity/state:

- unregistered identity;
- registration pending funding;
- `MYST_VPN_WAIT_FOR_FUNDS=true`;
- invalid/unavailable preferred provider;
- transient `connection up` failure followed by success; and
- all configured initial connection attempts exhausted while the daemon stays
  alive.

For each, confirm recovery is unarmed until a complete readiness success. A
live-but-unready startup must remain inspectable and cause the outer Compose
wait to fail rather than cycling invisibly. A daemon crash may still restart
through `unless-stopped`.

Finally validate a clean `make down-*`: intentional stop must not restart Myst,
and no health state or helper process may remain on the host.

### Phase 7: live Podman validation

Treat Podman as a separate runtime. Do not infer it from Docker results.

When a usable supported Podman machine is available:

1. Run the capability gate and confirm its image store is healthy.
2. Start the relevant lite/full VPN stack through the Makefile.
3. Inspect `.Config.StartupHealthCheck` and `.Config.Healthcheck` separately;
   both must contain the selected command with the intended startup and regular
   cadences.
4. Repeat the runtime-disconnect test and confirm either the already-qualified
   graceful restart path or the visible fallback, without any API/socket helper.
5. Confirm `netns-holder`, policy proxies, and (in full mode) the Podman host
   document server retain their expected identity and behavior.
6. Validate explicit no-VPN mode does not arm or self-terminate.
7. Restart Podman Desktop/VM when practical, then repeat a warm start to catch
   persisted-container health-command or runtime-state mistakes.

If Podman is unavailable, report that exact omission. Deterministic Podman
model and startup-health tests are required regardless.

## Acceptance criteria

The work is complete only when all of the following are true:

- The autoheal Compose service, both autoheal-related overlays, label, image
  pin, Makefile selectors, and Podman suppression path are gone.
- No new container-engine socket, privileged service, network, capability, or
  public dependency replaces them.
- One periodic local readiness predicate supplies health visibility; if
  automatic enforcement qualifies, it consumes that same result and adds no
  duplicate polling daemon.
- The shared-namespace restart gate is recorded with repeatable evidence. A
  known-bad Myst-only restart is not reintroduced under a different trigger.
- Under automatic recovery, startup remains visibly unready until the VPN has
  proved ready once, and a later failure is continuous for at least 60 seconds
  before the qualified graceful path begins.
- Under fallback recovery, a sustained failure remains visibly unhealthy and
  requires the documented full-stack teardown/restart.
- Docker and Podman use the same selected recovery contract and preserve the
  stable `netns-holder` namespace during normal operation.
- Explicit no-VPN mode never arms VPN recovery.
- Runtime outages remain fail-closed and recover without recreating unrelated
  application services.
- Deterministic tests cover the state machine and full eight-case Compose
  matrix.
- `make check` and `make health-inventory` pass.
- Live Docker recovery passes; live Podman recovery passes when a usable Podman
  runtime is available, or the omission is reported precisely.
- README, AGENTS.md, active subsystem docs, upgrade guidance, resource
  minimization policy, and conflicting present-tense implemented-plan text
  match the final behavior.

## Expected resource and operational consequences

Removing autoheal eliminates one always-running container, its process and
periodic Docker API polling, the mounted Docker control socket, one external
image/pin, and the Docker/Podman feature mismatch. It does not remove Myst's
one-minute regular healthcheck because that check remains the user-visible
readiness predicate and, only under the qualified design, the recovery trigger.

Initial live-but-unready VPN setup does not self-restart. It remains visible
until corrected or explicitly restarted. If Phase 0 qualifies Myst-only
restart, a later sustained failure uses that exact tested graceful path. If it
does not, runtime failure also remains visible until the operator recreates the
matching stack and its namespace. In either result, request-time policy must
continue rejecting traffic rather than bypassing the VPN.

## Handoff record

At implementation handoff, report:

- files added, deleted, and materially changed;
- the exact state-machine timing used;
- Phase 0 namespace snapshots/results and the selected recovery branch;
- deterministic test totals and skipped tests;
- effective Docker and Podman model results;
- live Docker fault-injection/recovery observations;
- live Podman observations or the precise reason they were unavailable;
- final running stack mode and health; and
- any remaining `autoheal`, Docker-socket, or obsolete-documentation matches
  with an explanation for each.
