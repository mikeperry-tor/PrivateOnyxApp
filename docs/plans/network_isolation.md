# Docker host-service isolation and code-interpreter controller separation

Status: proposed implementation; runtime changes and acceptance validation are
not complete. The existing security/RAG documents describe the current
rpcbind residual. This plan authorizes neither disabling a host service nor
changing the Podman support contract to match an unverified Docker assumption.

## Objective and scope

Harden the stack against code execution in Onyx API/background processes,
browser/search components, and executor children by:

1. On Docker Engine 28+, giving every selected internal bridge IPv4 and IPv6
   `isolated` gateway settings, removing ordinary host-side bridge addresses while
   preserving intended container-to-container traffic.
2. Moving the Docker code-interpreter controller off the shared backend onto
   a dedicated internal network whose only application peer is `api_server`.
3. Validating the resulting boundary under actual service privileges, rather
   than treating a missing default route or failed RPC response as sufficient
   proof of containment.

Apply controller separation on every supported Docker version and isolated
gateways by default on Docker 28+, independently of
`ONYX_CODE_INTERPRETER_ENABLE_NETWORK`. Older Docker versions that meet the
existing stack requirements remain supported: warn at startup and retain
ordinary internal bridges with their host-service residual. Keep executor
children networkless when that option is false/unset. Preserve existing route selection, proxy
destination policy, Tor/VPN failure behavior, mounts, and RAG protocols.

Keep both bots opt-in. Disable built-in Python, Bash, and Coding Agent tools
in `background` through an explicitly empty `CODE_INTERPRETER_BASE_URL`.
Slack retains ordinary chat/search and its existing `deep_research=false`
request behavior. Discord retains authenticated API-based chat, including
API-side code tools where configured, and its existing deep-research default
of false. No bot-specific runtime patch or additional bot service is needed.

This is primarily Compose/Makefile work with implementation-time validation.
No new Onyx runtime patches, executor-image changes, dependency upgrades, or embedding
shim behavior changes are expected. Unnecessary network capabilities may
need removal as described below. Do not expand this into a general sandbox
rewrite, socket-proxy product, authentication retrofit, or VM firewall project.

Podman remains supported with its explicitly documented existing boundary;
equivalent Podman host-service containment is not an assumed deliverable.
Assess and document its capability gap and run compatibility validation.
Do not silently disable Podman or present its existing `internal` networks as
providing the new Docker guarantee.

## Required orientation and implementation locations

Read repository `AGENTS.md` and these owning documents before editing:

- [Internal network security](../internal_network_security.md).
- [VPN routing and proxies](../vpn_routing_and_proxies.md).
- [Podman support](../podman_suport.md), including rootless Docker exceptions.
- [Native Tor support](../native_tor_support.md).
- [Local document RAG](../local_docs_rag_search.md).
- [Resource minimization](../resource_minimization.md).
- [Request handling](../request_handling.md).
- [Patch information](../onyx_patch_info.md) and
  [upgrade validation](../onyx_patches_upgrade.md).

Key code and test entry points:

- `Makefile`: `LITE_FILES`, `FULL_FILES`, matching down-file lists, engine
  selection, capability preflight, staged full startup, health inventory,
  and every command consuming the selected Compose model.
- `docker-compose.yaml`, `compose_overlays/`: network declarations,
  service attachments, optional feature and engine differences.
- `podman/startup_health.py`: existing engine/Compose capability checks and
  startup integration; reuse appropriate machinery without conflating engines.
- `onyx/helper-egress.env`: shared `NO_PROXY` entries; these bypass proxies,
  but do not grant connectivity or enforce access control.
- `onyx/background_entrypoint.py`: opt-in Slack/Discord process selection.
  Upstream Slack chat handling, Discord's API client, tool construction, and
  native tool availability checks define their different execution paths.
- `tests/test_onyx_network_isolation.py`,
  `tests/test_podman_startup_health.py`, `tests/test_tor_config.py`,
  `tests/test_code_interpreter_executor_env.py`,
  `tests/validate_code_interpreter_executor_network.py`.
- Read-only `reference_repos/python-sandbox/code-interpreter/app/` and
  `reference_repos/onyx/backend/onyx/` for call-path orientation. The selected
  pinned images are authoritative when a reference checkout differs.

Never patch `reference_repos/` or generated upstream deployment files.
Never read, print, shell-source, or modify private `.env.wrapper`, document
contents, or persistent data for this work without explicit authorization.
Use supported Make/Compose mechanisms for private configuration. Do not dump
complete resolved Compose models or container environments into reports.

## Evidence and its limits

The initial local reproduction used Docker Desktop Engine 29.7.2,
linux/arm64, controller image `onyxdotapp/code-interpreter:0.4.6`, and executor
image `local/private-onyx-python-executor:0.4.5-5d91a10c39a8`.
These identify evidence, not pins to force during implementation; re-read
`stack.versions.env` and Make-derived image identities.

The VM had PID 1 `initd`, a process named `rpcbind`, and TCP/UDP listeners on
all IPv4 and IPv6 addresses at port 111. Disposable probes used the executor
image as UID/GID 65532 with all capabilities dropped and no-new-privileges.

| Probe | Ordinary internal bridge | Isolated internal bridge | Network none |
| --- | --- | --- | --- |
| IPv4 TCP RPC NULL to bridge gateway | Valid RPC reply | No RPC reply | Network unreachable |
| IPv4 UDP RPC NULL to bridge gateway | Valid RPC reply | No RPC reply | Network unreachable |
| Subnet-directed UDP broadcast RPC NULL | Valid RPC reply from host gateway | No reply within two seconds | Not separately tested |
| Other probed off-subnet VM address | Network unreachable | Network unreachable | Network unreachable |

The isolated bridge had no gateway in IPAM inspection. Its subnet's `.1`
address must not be treated as a host address: a container can receive that
address when Docker no longer reserves it as the gateway. The isolated
unicast refusal alone is therefore not evidence about host packet delivery.

Full application startup, RAG, native Linux/rootless comparison, IPv6 packet
probes, and privileged/raw-packet containment remain unvalidated. A UDP timeout
proves absence of a response within the bound, not absence of delivery or
one-way effects. Disposable reproduction resources are not retained.

Current design evidence, separate from live acceptance:

| Check | Result | Evidence and limitation |
| --- | --- | --- |
| Static Docker overlay with all applicable internal-network option entries and API/controller attachments | Compose rendering passes for lite, full, and combined executor/Teep-VPN/Tor-onion selections; unused optional networks are pruned | Public-fixture rendering only; complete Make-selected matrix and live creation remain required |
| Native background tool disablement | Explicit empty controller URL makes Python and Bash unavailable; Coding Agent delegates to Bash availability | Availability methods extracted from pinned Onyx `v4.6.5` return false without database/controller dependencies; full patched bootstrap and tool-construction validation remain required |
| Bot request paths | Slack executes chat in background with research disabled; Discord submits authenticated API chat with research defaulting to false | Pinned Onyx source inspection; live opt-in bot flows remain required |
| Session ownership | Controller `0.4.6` uses generic session labels and a daemon-wide expiry reaper | Pinned-image inspection; labels do not prove stack ownership |
| Socket mount cleanup | Base and executor-enabled rendering select identical socket mounts, including a custom socket path | Public-fixture rendering; complete engine/mode matrix remains required |

To reproduce a harmless request, send an ONC RPC v2 call to program 100000,
program version 2, procedure 0 (NULL), with AUTH_NULL credential/verifier and
a unique transaction ID. The ten big-endian 32-bit words are:
`xid, 0, 2, 100000, 2, 0, 0, 0, 0, 0`.
TCP adds the final-fragment record marker `0x80000000 | payload_length`.
Validate transaction ID, reply type, acceptance status, and TCP framing;
do not classify arbitrary bytes or successful UDP `sendto` as RPC success.
Use `SO_BROADCAST` for directed broadcasts, explicit short bounded timeouts,
and discovered subnets rather than hardcoded reproduction addresses.

## Security model and residual authority

### What isolated gateways change

Ordinary Docker `internal: true` networks lack ordinary external routing but
retain host-side bridge addresses. Host services bound to those addresses or
wildcard addresses can be reached without using HTTP proxies. Docker's
`isolated` gateway mode removes those bridge addresses. It is a per-network
property, not a per-container switch and not `network_mode: none`.
The upstream implementation disables IPv6 on the bridge to prevent a host
link-local address. Address removal is not an independently specified
host-input firewall; stronger packet-level claims require the feasibility
gate below.

Every attached network of a protected service must be covered. One remaining
ordinary bridge or uplink can preserve a host-service path. Container DNS,
same-network application sockets, and the fixed application proxies must
continue working. Do not infer denial from a DNS failure: test literal IPs.

The benefit extends beyond rpcbind to other current or future host/VM
listeners reachable through the removed addresses. Disabling rpcbind removes
one daemon's exposure, including to uplink containers, but does not establish
this broader boundary. Conversely, isolated internal networks do not remove
rpcbind or protect containers that retain an uplink. These controls have
different coverage.

`systemctl disable --now rpcbind` is not a portable Docker Desktop operation;
the inspected VM is not systemd-managed. Host-wide shutdown can also affect
operator RPC/NFS dependencies. Do not add service shutdown, socket-unit
manipulation, privileged VM mutation, or rpcbind restarts to stack startup.

RAG has no application-level rpcbind dependency: local documents use bind
mounts/HTTP, embeddings use HTTP, and data services use their own protocols.
Host storage backed by NFS, especially NFSv3, can have independent RPC
dependencies. Such host-side storage dependencies do not require executors
or Onyx processes to reach host rpcbind. Preserve intended host embedding
access through the existing final-hop policy.

### Remaining rpcbind and host-service access

| Component/path after implementation | Required interpretation |
| --- | --- |
| API/background, internal-only browser/search/data/RAG services, enabled executor children on Docker 28+ | Ordinary direct bridge-host access should be removed on recreated isolated networks; actual privileges and packet-level denial require qualification. Stale-network reuse is an accepted migration risk below. |
| The same components on supported Docker below 28 | Ordinary internal bridges retain direct bridge-host access; startup warns and continues. Controller separation still applies. |
| Executor children with networking disabled | Retain `--network none`, including coding-agent sessions. |
| `netns-holder`, Myst, and final-hop proxies sharing its namespace | Retain route-owner authority and an uplink; isolated internal attachments do not make this namespace host-isolated. |
| Default Teep, optional Tor, optional Tailscale | Retain dedicated uplinks or explicitly selected shared routing namespaces; host/VM listener access remains a residual unless independently constrained. |
| Fixed host publishers, including WebUI/document/Teep publishers | Retain required non-internal publication networks; compromise can exceed the fixed forwarding behavior and may reach host listeners. |
| Podman and its macOS document relay | Retain separately documented engine/host paths; no unverified parity claim. |
| Operator, other containers on the daemon, host/VM processes | Outside the new application-network restriction. |
| Explicit host/LAN grants through the host policy | Retain only the configured allowances. Review whether a grant could authorize a TCP tunnel to port 111; do not claim port 111 is globally forbidden. The HTTP proxy is not a general UDP relay. |

The exact accessible addresses depend on engine, namespace, VPN rules, and
operator settings. Report tested reachability rather than asserting that
every exception necessarily reaches every VM address.

Isolation does not prevent same-network lateral movement, reading mounted
data or process credentials, application-authorized exports, public egress
through the allowed proxy, or kernel/container-runtime exploitation. API and
background remain dual-homed to public and host-policy caller networks:
compromise can select either bridge directly, bypassing application-level
SSRF route selection but not an uncompromised final-hop policy.

The normal stock crawler runs inside the API container and shares that
container's compromise consequences. The hardened Obscura path remains a
separate process boundary. Preserve that documented distinction.

Inventory effective UID, capabilities, privilege flags, sysctls, and namespaces
for the actual protected services. Do not extrapolate from the initial
capability-free executor probe to root application processes with `NET_RAW`.
Remove unnecessary `NET_RAW`/`NET_ADMIN` capability authority, preserve
existing capability drops, and validate affected entrypoints. Keep legitimate
VPN/TUN route-owner privileges separate. Do not apply blanket `cap_drop: ALL`
to services that need initialization capabilities without proving startup.
If raw Ethernet, IPv6 link-local, crafted routing, or one-way UDP can still
reach a host listener under the retained application privileges, the stronger
containment claim is blocked until a reviewed control addresses it. Do not
silently substitute a timeout-based success criterion or widen this plan
into host firewall mutation.

Retain fixed bridges, separate caller networks, public/host final-hop policies,
proxy peer checks, SSRF adapters, and the networkless executor default. Shared
destination policy does not make caller networks interchangeable. Policy
processes share the route-owner namespace, so peer checks still restrict
listener access. Isolated gateway settings do not replace these controls.

### Controller authority before and after migration

The controller currently joins `onyx-backend`, alongside `api_server`,
`searxng-service-gateway`, and, in full mode, `background` and
`local-embedding-shim`. The pinned HTTP API has no caller authentication.
Any compromised peer can invoke execution, file, and session APIs. Normal
gateway forwarding does not give SearXNG arbitrary access to that network.

The controller owns a writable Docker socket and creates/manages children;
children receive controller-selected images, network modes, launch arguments,
and staged workspace files, not the socket. HTTP API access is therefore
not automatically arbitrary Docker API access. A controller vulnerability,
container escape, or another route to control of its process can cross that
boundary. Rootful socket compromise grants daemon-host authority (the Linux
VM on Docker Desktop, with consequences for host-shared files); rootless
socket compromise grants the rootless daemon owner's authority. Do not
equate rootless Docker with unrestricted host root.

After migration, the only direct application-network caller is `api_server`.
Background, embedding shim, SearXNG gateway, browsers, frontend/data services, executor
children, and policy proxies must not reach the controller by name or IP.
The API still has execution authority and access to the unauthenticated
controller attack surface. Same-network isolation does not authenticate users
or sessions. Controller compromise still exposes the socket regardless of
its network settings. A malicious daemon administrator is out of scope.
Discord can still request API-side execution through its authenticated chat
API; removing background's direct controller reachability does not remove
that authorized application path. Do not describe controller separation as
preventing all indirect execution requests from background.

## Implementation design

### Separate controller topology from version-dependent gateway options

Add two small tracked Docker-only overlays with distinct responsibilities:

- `compose_overlays/docker-compose.docker-controller.yml` defines the internal
  `onyx-code-interpreter-control` network and minimal API/controller attachment
  changes. Select it on every supported Docker version.
- `compose_overlays/docker-compose.docker-isolation.yml` adds only the isolated
  gateway options to every applicable internal network, including the control
  network. Select it on Docker Engine 28+ and append it last.

Keep the root base first and select these through the existing Makefile file
lists after the relevant feature/platform layers. Do not duplicate controller
attachments in version-specific files or introduce a generator, generated-file
cache, selection identities, or feature-specific copies. Compose prunes unused
networks, so listing optional network option entries does not select services.
For each applicable network, the gateway overlay adds:

```yaml
driver_opts:
  com.docker.network.bridge.gateway_mode_ipv4: isolated
  com.docker.network.bridge.gateway_mode_ipv6: isolated
```

Preserve existing driver options, names, IPAM/static addresses, IPv6 enablement,
aliases, and attachment metadata. The IPv6 option must not independently enable
IPv6 or change address allocation. Keep optional network `internal`, IPAM, and
service definitions in their owning layers. Validate the effective model using
structured JSON/YAML; do not add a second merge interpreter or reject
lower-layer values intentionally overridden by the final gateway overlay.

On Docker 28+, deterministic tests must detect every selected internal network
and reject missing isolation options. On older supported Docker, assert that
only the gateway overlay is omitted and controller separation remains intact.
On Podman, select neither Docker overlay and preserve the controller's inactive
`requires-docker-socket` profile, executor-network omission, and absence of an
unused control network. Test optional-network pruning and both Docker version
branches across the supported feature combinations.

All Make consumers must use consistent selection: up, staged full readiness,
down (including Tor cleanup variants), ps/logs, health inventory, image/Compose
validation, and diagnostics. Reuse engine discovery and inspect the server
version, not the client version. Keep selection free of parse-time mutations.
Public fixtures must support deterministic rendering without a daemon or
private configuration. Never persist complete secret-bearing resolved models
or echo private values in errors. Keep cleanup and diagnostics usable without
passing startup capability checks; no new network inspection gate is required.

### Controller network migration

Audit all legitimate controller callers in the selected Onyx image before
restricting the network. Cover `run_python`, ordinary Bash/code-agent tools,
session creation/execution/deletion, uploads/downloads, repository staging,
generated artifact handling, and any background cleanup tasks. Relevant source
includes `onyx/tools/tool_implementations/python/code_interpreter_client.py`,
`python_tool.py`, `onyx/tools/fake_tools/coding_agent.py`, and their call sites.

Keep service name `code-interpreter`, port 8000, and API base URL stable.
Give the controller only `onyx-code-interpreter-control`; the API keeps all
existing attachments plus that network. Use an actual replacement for the
controller's network list, not a merge that leaves `onyx-backend` attached.
Do not attach the controller to the executor-egress network: it assigns that
network to children through Docker, not through its own network membership.
Do not add published ports, host networking, broad proxies, or socket mounts
to any caller or child.

Retain `code-interpreter` in the API's direct-service
`NO_PROXY` contract; a shared unused entry in background is not a route grant.
If names/aliases change, update the owning environment file and tests. Prefer
no client or runtime-patch changes. Audit indirect tool callers as well as
direct client imports; the accepted Slack/Discord behavior is specified below.
Resolve any additional caller explicitly rather than attaching all backend
services to the control network.

### Native bot behavior and configuration cleanup

In `compose_overlays/docker-compose.full.yml`, replace background's controller
URL with `CODE_INTERPRETER_BASE_URL: ""`. Do not merely delete it: the pinned
Onyx configuration otherwise defaults to `http://localhost:8000`. Apply this
native setting to full mode on both engines, while preserving Podman's
controller omission and both bots' default-off selection.

`PythonTool.is_available` and `BashTool.is_available` return false immediately
for an empty URL; `CodingAgentTool.is_available` delegates to Bash. Native
tool construction excludes unavailable tools even if present on the persona.
This disables built-in execution throughout background, including Slack,
without a tool-ID denylist, synthetic health failure, or new runtime patch.
The network boundary independently denies direct controller access after
background compromise. Explicit external MCP tools retain their own configured
capabilities; this is not a blanket ban on remote execution services.

Slack's handler invokes chat processing locally and explicitly requests
`deep_research=False`. Preserve ordinary chat, local/web search, persona
selection, user ACLs, and channel context. A natural-language research request
may use ordinary search tools but does not enable the deep-research mode.
Discord's client submits authenticated chat to `api_server`; preserve that
path and API-side code tools. Its request model defaults research to false.
Do not add Discord persona restrictions or treat client-supplied message
origin as an authenticated security identity.

Remove the controller service's own unused `CODE_INTERPRETER_BASE_URL` from
`docker-compose.yaml`, after confirming the selected controller does not
consume it. Remove the duplicate socket-volume replacement from
`docker-compose.code-interpreter-network.yml` after proving the common
controller configuration preserves the selected socket for default and custom
paths, rootful/rootless Docker, both modes, and networking off/on. Socket
selection must not depend on child networking. If inherited mount behavior
drifts, keep one explicit common controller mount rather than an optional copy.

### Security feasibility gate

During implementation, run the packet and positive-connectivity checks below
on disposable isolated networks under the intended protected-service privilege
sets. Record effective/bounding
capabilities and whether achievable container-root authority can regain raw
network access. The rendered API, background, SearXNG, and several data
services currently lack explicit capability drops; image users alone do not
establish their full privilege boundary.

Identify the exact qualified privilege sets and any necessary capability
removals. If the selected environment cannot establish the stronger boundary,
leave qualification pending; if it demonstrates a bypass, revise the control
or claim before adopting the broader design. Address removal alone cannot
satisfy this gate, and missing platform access does not count as approval to
claim protection there.

### Docker version selection and ordinary lifecycle

Preserve the stack's existing Docker/Compose minimum requirements, health API,
engine-mode, rootless socket, and userns-remap checks. Docker 28 is a gateway
feature threshold, not a new minimum supported version. Reuse existing version
discovery for overlay selection rather than adding a parallel capability gate.
A supported server below 28 omits only the isolated-gateway overlay, emits one
clear warning per top-level startup, and continues normal launch. For example:

> Docker Engine <version> does not support isolated bridge gateways. Starting
> with ordinary internal networks: containers may reach host/VM services on
> bridge addresses. Controller separation remains enabled. Upgrade to Docker
> 28+ and recreate the stack networks to enable gateway isolation.

Failure to determine the server version is a startup error, not evidence of an
older engine. On Docker 28+, surface isolation-option creation failures without
retrying with ordinary gateways. Preserve ordinary error handling for failures
unrelated to this explicitly supported older-engine branch.

Actual network options, bridge addresses, packet delivery, service privileges,
and controller reachability are implementation and platform-qualification
checks, not checks at every startup. Keep the existing Docker Compose startup
flow and full-mode embedding readiness prerequisite before API/background
replacement or start. Preserve Podman's existing create/configure/start flow
for its native startup-health requirement. Add no Docker create/inspect/start
lifecycle, network scanner, qualification cache, privileged helper, or health loop.

An existing prerequisite failure before mutation leaves the current deployment
state intact; it cannot restore services already removed by `make down-*`.
Once Compose creation/replacement begins, failure can leave services stopped,
replaced, or partially running. Retain persistent data and visible errors;
do not promise transactional rollback or add rollback machinery.

Daemon-driven restarts use persisted configuration without Make preflight.
Qualify restart behavior during implementation; add no restart interception or
repeated packet checks. Engine/firewall/network changes can change the scope
of recorded qualification. Version selection establishes feature availability,
not packet-level protection of every running or previously created network.

### Ordinary upgrade and accepted stale-executor risk

Keep the README upgrade contract: `make down-lite`, `git pull`, then
`make up-lite`, or the matching full-mode commands. A successful down removes
old networks, and up creates the selected topology. Older supported Docker
launches with the warning; Docker 28+ creates isolated internal networks.
The same down/up workflow is required when an engine upgrade crosses the
Docker 28 threshold. A warm up or engine restart alone does not convert an
existing ordinary bridge. No separate migration command, drain protocol,
endpoint inventory, or user-run qualification procedure is required.

The first upgrade's down runs the old revision's Makefile. Validate the normal
upgrade from that revision during implementation, rather than assuming new
shutdown behavior is already installed.

Executor children are outside Compose's service inventory and may outlive
controller shutdown. A remaining child can retain its workspace, resources,
and ordinary bridge-host access and prevent network removal. Depending on
Compose version and network metadata, subsequent startup may fail or reuse
the old network; if executor networking is no longer selected, the child and
old network may remain separately. This is an accepted operational and
security residual, not a migration gate to implement or exhaustively test.
Do not claim all running networks are upgraded merely because startup succeeds.

Note this exception briefly in upgrade troubleshooting: the operator may need
to restart the container engine and retry the matching down/up workflow. Engine
restart is a recovery step, not a guarantee of endpoint cleanup or network
conversion; persistent leftovers may require manual resolution. Never restart
the engine automatically, sweep children daemon-wide, force-delete unknown
endpoints, or delete persistent data. Add no reaper, registry, controller patch,
mandatory drain wait, or stale-network startup scan for this case.

Controller 0.4.6 uses `sleep_seconds=(timeout_ms * 1000) + 10` for transient
children and relies on normal controller cleanup; interrupted cleanup can leave
very long-lived children. Sessions allow TTLs up to 24 hours. Its session reaper
uses generic daemon-wide labels and deletion accepts a generic name prefix;
neither proves stack ownership. Retain this limitation in the owning docs,
without making lifetime probes or interrupted-cleanup tests acceptance gates
for this network change.

## Podman boundary

Podman documentation describes `--internal` as disabling bridge forwarding
and default routes, and `isolate=true/strict` as inter-bridge isolation. These
are not Docker's addressless internal bridge contract. Do not claim Docker
option compatibility merely because a compatibility API accepts a key.

Inspect the installed Podman/Netavark versions and document what can be
established without changing the VM. Validate supported native/rootless and
macOS-machine configurations independently. A rootless namespace's gateway
is not automatically the physical host's gateway. Keep macOS document relay,
host embeddings, and engine-host aliases working. Leave equivalent host-input
filtering or addressless Podman bridge management as an explicit separate
design if native support cannot provide this boundary. No new Podman overlay
is required for the Docker-only change itself.

## Validation and acceptance

### Deterministic configuration and lifecycle tests

Use public example configuration and synthetic fixtures; no private env or
live credentials. Extend existing tests rather than replacing their coverage.

- Every selected Docker 28+ internal network has both isolated gateway options;
  no selected external/publication network receives them. Detect new internal
  networks automatically and validate all protected service attachments.
- Aliases, fixed addresses, IPAM, existing driver options, mounts, route-owner
  membership, and disabled-feature absence remain correct after merging.
- Controller has exactly one network; its two peers are exactly controller
  and API. No child gets that network/socket. Enabled children retain the
  exact executor network and eight proxy variables; disabled children retain
  `none`. Validate unset, false, and true separately.
- Podman models contain no Docker isolated gateway settings, active controller,
  unused controller control network, or executor-egress additions.
- Cover root-base-first ordering, controller overlay selection, the gateway
  overlay last when selected, unused-network
  pruning, malformed/unexpected models, secret exclusion, wrong drivers, and
  incompatible effective options. A newly introduced internal network without
  options on Docker 28+ must fail validation rather than escape a hardcoded
  test inventory.
- Mock supported Docker below 28 and Docker 28+ server versions. The older
  branch warns once and launches with controller separation but no isolated
  options; the newer branch selects both overlays. Test server/client version
  disagreement, malformed/unavailable server version failure, retained existing
  minimum requirements, and no retry without isolation on newer-engine errors.
- Preserve the Docker startup flow and both full-mode stages. Cover cleanup
  and diagnostics on both version branches and partial Compose/readiness
  failures. Add no per-start network inspection, packet probes, or dedicated
  stale-executor migration tests.
- Assert background's controller URL is explicitly empty in Docker and Podman
  full models, while Docker API configuration stays usable. Cover Slack and
  Discord independently enabled and together, preserving default-off behavior.
- Add selected-image checks for actual native Python/Bash/Coding Agent
  availability and exclusion from constructed Slack tools even when attached
  to the persona. Prove no controller call occurs with the empty background
  URL and that API tools remain available with a healthy controller fixture.
  Validate the full wrapper bootstrap, not only extracted method bodies.
- Verify Slack requests explicitly disable deep research and Discord's actual
  request serialization keeps it false. Preserve Discord's authenticated API
  path and its access to configured API-side code tools. Test executable
  request behavior, not prompt wording or a generic message-origin filter.
- Verify socket mount equivalence with default/custom socket paths across the
  supported Docker modes and executor-network settings before removing the
  duplicate mount. Confirm the selected controller has no dependency on its
  own removed client URL setting.
- Preserve startup/steady health cadence and the full staged readiness order.

Render through the Makefile for Docker Desktop, native rootful Docker,
rootless Docker, native rootless Podman, and macOS Podman; both lite/full.
Include older supported Docker and Docker 28+ version fixtures. Cover executor
off/on, VPN off/on, Teep and Tailscale route switches, Tor
off/egress/onion/both, and rootless exact-Teep embedding selection. Use
parameterized coverage of every network-introducing layer and valid combined
cases; confirm existing invalid combinations still fail. Include the maximal
valid combined topology and down-file variants. No hand-assembled alternate
Compose recipe may become the only tested path.

### Live packet and host-service boundary tests

Run these checks during implementation and explicit platform qualification,
never as ordinary startup prerequisites. The isolated-gateway denial claim
applies to Docker 28+ with recreated networks; older Docker retains the stated
residual and needs compatibility validation, not a passing host-denial result.
Build a reusable opt-in test harness
with bounded timeouts and explicit resource ownership/cleanup. Use already-selected local images; do not pull
or install tools at container startup. Collect only non-secret network facts
and test-owned payloads. Record engine/OS/rootless/image/feature identities,
daemon firewall backend, firewalld status, and relevant IPv4/IPv6 configuration.

1. Establish positive controls on a disposable ordinary bridge using the
   harmless RPC NULL probe and/or a disposable host-namespace TCP/UDP listener
   on unused test ports. Prove the probes can observe real traffic. Keep test
   listeners away from private interfaces/data and remove them after testing.
2. Repeat on isolated networks, including a dual-homed internal service shape.
   Inspect bridge addresses and use a test-owned socket receiver/counter to
   distinguish no delivery from no reply. Packet capture explains traversal;
   a frame observed at a host interface does not by itself prove delivery to
   a host socket. Do not turn off the real rpcbind service or send
   state-changing RPC calls. Prove internal DNS and same-network TCP/UDP
   application traffic still succeed, including IPv6 where enabled, so a
   broken network cannot pass as secure.
3. Test TCP, UDP unicast, directed/limited broadcasts where routable, IPv6
   unicast/link-local/multicast, and host/gateway aliases and literal addresses.
   Discover current/old bridge, VM, default-bridge and engine-host addresses;
   do not assume only the former gateway is relevant. Bound destination sets
   to this host/VM; do not scan unrelated LANs.
4. Repeat under actual API/background/container UIDs and capabilities, with
   proxies unset/ignored. Check raw-packet paths when effective privileges
   permit them. Test both normal process credentials and the container-root
   authority achievable under the retained security settings. Do not grant
   extra capabilities to a protected test subject and call that its baseline.
5. Verify direct host-service access is denied for API, background, browser,
   SearXNG, enabled executor children, embedding shim and representative data
   and internal gateway containers. Test exceptions separately and record
   which uplink/namespace processes still reach rpcbind or the test listener.
6. Where the real host has no rpcbind, the test-owned listener provides the
   positive control. Absence of a daemon cannot count as network protection.

The IPv6/packet/privilege checks are required to establish the claimed
boundary, not optional enhancements to a successful IPv4 TCP test. If the
environment cannot support them, report incomplete validation explicitly and
do not label the complete isolation claim verified.
For IPv6-disabled deployments, verify the disabled/addressless behavior
separately from an IPv6-enabled disposable fixture. Include native Docker with
firewalld and the selected firewall backend in qualification: upstream
[Moby issue #49680](https://github.com/moby/moby/issues/49680) reports IPv6
connectivity failure on isolated networks under firewalld. Do not enable bridge
addresses, weaken host firewall policy, or silently disable IPv6 to pass.

### Controller and application integration

- From API, exercise controller health, Python execution/streaming, session
  creation, Bash command execution, file upload/download/list/delete and
  session deletion using disposable data. Exercise the actual Onyx
  `run_python` and coding-agent flows, including repository staging and
  generated file/image artifacts, with networking off and on.
- With authorized disposable bot conversations, verify Slack ordinary
  chat/search and persona/ACL behavior while Python, Bash, and Coding Agent
  are absent, and verify Discord's ordinary chat and configured code tools
  execute through the API. Neither bot selects deep-research mode. Record
  credential-dependent bot checks as pending when unavailable; deterministic
  request/tool tests remain mandatory and do not require sending messages.
- From background, embedding shim, SearXNG gateway, SearXNG, Obscura,
  frontend/data services and executor children, attempt direct connections to
  the controller's current IP as well as its name. Connection must fail; DNS
  failure alone is insufficient. Controller has no published port or proxy
  route that restores direct access. Review permitted host/LAN grants
  separately. Authenticated Discord-to-API chat remains an intentional
  indirect execution path, not a failed controller-network check.
- Inspect actual executor children: network memberships, socket/mount absence,
  configured image, privilege settings and proxy variables. Confirm changing
  the controller's own network does not change child launch behavior.
- Confirm controller remains engine-capable through its Unix socket, and
  document that this is retained trusted authority, not an isolation failure.
- Test enabled executor public HTTPS through the proxy; direct public TCP/UDP
  and private/host destinations fail. Stop the selected bridge/proxy and
  verify no direct fallback; restore only resources owned by this test.
- Exercise WebUI login/streamed chat/replay/artifacts, `web_search`, both stock
  and direct browser retrieval where configured, and allowed/denied MCP/Web
  connector host/public route selection.
- Run full-mode RAG on a disposable authorized document: initial ingestion,
  query embedding/internal search, source display download, changed-document
  refresh, and restart recovery. Verify both normal host embedding and the
  exact rootless Teep internal exception where supported. Do not use private
  documents merely as test fixtures.
- Exercise optional localhost/Tailscale/onion ingress and selected VPN/Tor
  egress. Preserve Tor startup/request allowances; do not shorten timeouts to
  make this matrix faster. Perform route-failure checks without mutating
  operator accounts, identities, funding, or persistent keys.

### Required commands and evidence

- Run `make check` after implementation/tooling changes.
- Run `make test-patch-images` because the code-interpreter runtime/network
  contract is affected; it must use selected locally built images.
- Run relevant `make up-lite`/`make up-full`, `make ps-*`, targeted non-secret
  logs, and `make health-inventory` for exercised combinations.
- Tor network-only changes need live Tor/ingress validation. Run
  `make test-tor-image` if implementation also changes its image, config,
  mounts, ownership, health, or control contract. Do not run aggregate image
  gates or OpenSearch image gates for unrelated network-only edits.
- Verify the README down/pull/up path from the old revision, repeat startup,
  engine restart, and feature toggles without deleting persistent data. Cover
  older supported Docker warning/launch, Docker 28+ fresh network creation,
  and recreation after crossing that version threshold. Inspect options,
  addresses, and memberships during qualification. Cover partial Compose and
  full-mode readiness failures; exhaustive stale-child recovery is out of scope.
- For unavailable platforms, credentials, images, or routing services, record
  the exact omitted checks and reason. Mocked Compose coverage is not live
  validation. Do not mark an untested platform guarantee complete.

Maintain a consolidated acceptance table with check, platform/selection,
privilege/firewall configuration, result, evidence location, and limitation.
Keep implementation status separate from platform security qualification.
Pending platform gates remain visible even when implementation work is done;
only platforms with all applicable positive and negative checks may be called
qualified for their stated boundary. Older-Docker compatibility does not imply
isolated-gateway protection, and stale-network reuse remains an accepted
exception to the recreated-network claim. A demonstrated bypass blocks the affected security claim and must
be resolved or explicitly reflected in a revised supported contract.
Do not journal the sequence of investigation or retain obsolete conclusions.
New deterministic tests must
test behavior/contracts, not freeze documentation wording.

## Documentation completion and handoff

Before implementation is complete, **fold the analysis, resulting access
matrix, controller trust boundary, remaining rpcbind paths, privilege caveats,
RAG implications, platform differences, and validation limits into
`docs/internal_network_security.md`**. Rewrite the existing candidate/current
residual text in place to the implemented truth. That document is the lasting
canonical specification; this plan must link to it rather than remain a
parallel permanent security specification.

Correct the security document's reachability table: enabled executor children
join only the dedicated executor network, not service/controller networks.
The controller owns the separate control connection and engine socket.

Update routing, Podman, and RAG documents where their actual contracts change.
Keep network and controller authority canonical in the security document;
put native bot availability and executor lifetime/cleanup limitations in the
resource-policy document's relevant sections. Link to those contracts from the
pinned-image upgrade checklist and patch information where relevant, without
copying the specification or describing native configuration as a new patch.
Include selected-image bot checks in the upgrade checklist; link to the
accepted cleanup residual without adding a mandatory lifetime test suite.

Explicitly update `README.md` and `.env.wrapper.example`: their existing broad
claims that agent tools cannot reach the host/LAN, including network-enabled
executors, conflict with the current rpcbind residual and retained Podman
boundary. Replace them with qualified descriptions of intended tool routes,
compromised-process boundaries, qualified Docker 28+ protection, and the
retained older-Docker and Podman limitations. Keep user-facing detail limited
to operator consequences:
the older-Docker startup warning, normal down/pull/up workflow and brief
stale-executor recovery note, bot capabilities, engine differences, and the high-level privacy boundary. Describe any retained
runtime validation accurately in patch information; do not invent a runtime
patch to describe a Compose change. Update `AGENTS.md` only if repository-wide
workflow or key-location instructions actually change.

Implementation completion requires the two focused Docker overlays, native bot
setting and cleanups, version-based gateway selection with an older-engine
warning, the ordinary README upgrade workflow, the controller caller audit, deterministic and applicable image checks, and
canonical documentation. It does not establish platform qualification by itself.
Full acceptance of the security guarantee additionally requires the feasibility
gate and all applicable live platform checks; unavailable checks leave that
qualification pending, not passed. Do not mark the plan fully accepted while
required gates remain outstanding. No automatic Git commit is requested.
Stage only intentional changes if later instructed to commit.
Do not modify frozen records under
`docs/plans/implemented/`.

## Primary references

- [Docker gateway modes](https://docs.docker.com/engine/network/port-publishing/#gateway-modes):
  ordinary internal bridge host address versus isolated mode.
- [Docker Engine 28 release notes](https://docs.docker.com/engine/release-notes/28/):
  introduction of isolated gateway mode.
- [Moby isolated gateway implementation](https://github.com/moby/moby/pull/49262):
  address removal and prevention of a host IPv6 link-local address.
- [Moby isolated IPv6/firewalld issue](https://github.com/moby/moby/issues/49680):
  positive-connectivity qualification requirement.
- [Podman network creation](https://docs.podman.io/en/v6.0.2/markdown/podman-network-create.1.html):
  internal, inter-bridge isolate, and managed/unmanaged bridge semantics.
- [Docker Desktop settings](https://docs.docker.com/desktop/settings-and-maintenance/settings/):
  file-sharing mechanisms.
- [Red Hat NFS documentation](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/7/html/storage_administration_guide/ch-nfs):
  RPC/NFS version distinctions.

Recheck upstream references and selected component implementations when
implementing; they do not replace the live supported-engine acceptance matrix.
