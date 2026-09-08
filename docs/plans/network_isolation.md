# Docker host-service isolation and controller separation

Status: implementation and deterministic validation complete; platform
acceptance remains partial with the outstanding gates below.
Full platform security qualification is not complete. No automatic commit is
requested. Frozen records under `implemented/` are unaffected.

## Accepted implementation

The lasting specification is [internal network security](../internal_network_security.md#docker-gateway-and-controller-boundary),
including the access matrix, controller/socket trust, retained host-service
paths, privilege restrictions, RAG consequences, migration residual, and
qualification limits. [Resource minimization](../resource_minimization.md#onyx-background-work)
owns native bot availability and [executor cleanup](../resource_minimization.md#executor-lifetime-and-cleanup).
[Podman support](../podman_suport.md#host-service-boundary) owns its separate
engine contract; [native Tor](../native_tor_support.md#configuration-state-and-process-contract)
owns SOCKS runtime initialization.

- Every supported Docker server selects the dedicated API/controller network.
  Docker 28+ additionally selects the gateway-only overlay last. Older supported
  Docker warns once at startup and keeps ordinary internal bridges.
- Background explicitly disables its native controller URL on both engines.
  Slack keeps chat/search without built-in execution; Discord retains its
  authenticated API execution path. Both remain opt-in with research mode off.
- Application/data services drop `NET_RAW` and `NET_ADMIN`; existing capability
  drops and legitimate route-owner privileges remain. Children retain their
  native networkless default or the exact dedicated proxy network.
- Controller URL and duplicate optional socket-mount configuration are removed.
  The inherited common socket mount retains default/custom socket selection.
- The ordinary matching down/update/up workflow recreates selected networks.
  There is no stale-network gate, daemon-wide child sweep, reaper, engine
  restart, host firewall mutation, rpcbind shutdown, or persistent-data deletion.
- Tor's own entrypoint exclusively removes stale SOCKS sockets. Dependency
  initialization and Make preparation preserve live sockets across full-mode
  stages and repeated startup.
- The document HTTP listener binds without reverse hostname lookup; startup
  remains local when host DNS is unavailable, preserving the existing
  readiness deadline and peer/path restrictions.

## Consolidated acceptance evidence

Evidence paths below are local test outputs under `/tmp`. Network JSON reports
contain qualification facts and test-owned payloads; lifecycle logs remain
local and may contain upstream diagnostics. No resolved private environment
model is retained. Reusable validation code lives in `tests/`.

| Check | Platform/selection | Privilege/firewall configuration | Result | Evidence | Limitation |
| --- | --- | --- | --- | --- | --- |
| Make-selected Compose models | Docker ordinary/isolated; Podman; lite/full; native/rootless/macOS fixtures; executor unset/off/on; VPN/Teep/Tailscale route layers; Tor roles and combined topology; down models | Public fixture environment | Passing expanded matrix and final suite | `tests/test_docker_gateway_isolation.py`, `/tmp/private-onyx-model-check.log` | Rendering is not live platform qualification |
| Server-version selection | Docker below 28 and 28+ mocks | Server-only version query; unchanged mode/API capability checks | Passing | `tests/test_podman_startup_health.py` | Native Docker 26 launch still needs its server |
| Deterministic checks | Local macOS; public fixtures | No private configuration or Internet required | Passing: 651 tests, 20 image-dependent skips; compile/help/diff checks pass | `/tmp/private-onyx-network-check.log` | Image-dependent parser checks pass in the separate image gate |
| Native tools and bot requests | Selected Onyx v4.6.5 / controller 0.4.6 images | Networkless validation; complete wrapper bootstrap; mocked external I/O | Passing | `/tmp/private-onyx-network-images.log`, `tests/validate_native_bot_tools.py` | No Slack/Discord messages sent |
| Tor image/runtime | Docker Desktop and rootless macOS Podman | Selected local Tor image; networkless fixtures | Passing on both engines | `/tmp/private-onyx-network-tor-image.log`, `/tmp/private-onyx-network-podman-tor-image.log` | Image gate does not prove live egress |
| Positive/negative host delivery | Docker Desktop Engine 29.7.2, Linux arm64; ordinary and isolated dual-network fixtures with IPv6 | Rootful; iptables; firewalld absent; UID/GID 65534 and root, NET_RAW/NET_ADMIN removed, no-new-privileges | Passing tested destination set: ordinary host TCP/UDP delivery; zero isolated delivery; internal DNS and IPv4/IPv6 TCP/UDP work; raw sockets denied | `/tmp/private-onyx-host-isolation.json`, `tests/validate_docker_host_isolation.py` | Not comprehensive IPv6 link-local/multicast or crafted-frame qualification |
| Actual Python application host probes | Docker full; API/background/embedding/SearXNG | Normal and container-root credentials; effective/bounding network capabilities removed | Zero host-receiver deliveries; raw sockets denied | `/tmp/private-onyx-host-isolation.json` | Actual-service receiver probes cover IPv4; other service privilege/IPv6 combinations remain pending |
| Controller caller boundary | Docker full, networking enabled | 15 application/data/gateway/policy network namespaces, capability-free probe subjects | Name and literal-IP connections denied; API client succeeds | `/tmp/private-onyx-controller-denial.json` | Namespace probes do not prove every original process's privilege boundary |
| Recreated selected networks | Docker full with VPN and native Tor egress | 18 active internal bridges | Both isolation options and no IPAM gateway verified | `/tmp/private-onyx-running-networks.json` | Unused old project networks remain; successful startup does not convert all daemon networks |
| Controller client/artifacts | Docker full, executor networking enabled | Actual API client; native child launch settings | Python, SSE, upload/download, text/PNG output, Bash session, file/session cleanup pass; child cannot reach controller | `/tmp/private-onyx-controller-integration.log`, `tests/validate_controller_integration.py` | Authenticated Onyx chat tool/UI artifact flow remains separate |
| Executor egress failure | Docker full, native Tor + VPN | Dedicated executor network, capability drop ALL plus native CHOWN, no socket/bind mounts | Public HTTPS works; direct TCP/UDP and host targets denied; stopped bridge fails closed and restored | `/tmp/private-onyx-executor-egress.log` | Test-owned session only |
| Full startup and warm startup | Docker Desktop, VPN enabled, native Tor egress | New capability drops; original readiness cadence | Passing staged MLX readiness and API/background/WebUI health; warm startup preserves Tor socket | `/tmp/private-onyx-network-up-full.log`, `/tmp/private-onyx-network-warm-full.log`, `/tmp/private-onyx-network-health.txt` | Readiness does not prove complete RAG ingestion/recovery |
| Public route/search | Docker full, native Tor | Existing fixed policy/browser bridges | HTTPS and DuckDuckGo search pass (10 results, no unresponsive engines); local WebUI HTTP 200 | `/tmp/private-onyx-tor-https.log`, `/tmp/private-onyx-search-check.log` | Other providers, stock/direct crawler matrix, login/chat/replay not qualified here |
| Networking-disabled execution and lite lifecycle | Docker Desktop, native Tor, no VPN | Actual child uses network none, no proxy variables/mounts | Passing lite startup, native client, PNG/text artifacts, sessions, literal-IP denial | `/tmp/private-onyx-network-up-lite.log`, `/tmp/private-onyx-controller-networkless.log` | Authenticated chat UI remains separate |
| Podman compatibility | macOS Podman server 5.8.1, Netavark 1.17.2 / aardvark-dns 1.17.0 | Rootless; separate existing boundary | Capability/image gates and DNS-free host-document readiness pass; full startup and normal down blocked by overlay image-store error | `/tmp/private-onyx-network-podman-full.log`, `/tmp/private-onyx-network-podman-down.log`, `/tmp/private-onyx-podman-platform.json` | `readlink ... storage/overlay: invalid argument`; no machine recreation or storage repair performed |
| Native Linux Docker/Podman | `ssh debvm`, reported Docker 26 | Not inspected: agent SSH fails with `No route to host`; operator confirms Terminal access to the same address | Blocked | Connection attempts | No older-Docker or native/rootless live pass claimed |

## Remaining acceptance gates

Implementation completion and platform qualification remain separate. Before
marking this plan fully accepted:

- Resume local Podman full/lite compatibility once its image store is repaired
  by the operator. Deterministic checks, the Tor socket regression, and Docker
  networking-disabled execution pass.
- Qualify native rootful/rootless Docker and native rootless Podman when the VM
  is reachable, including older-Docker warning/launch and Docker 28+ recreation
  across the threshold. Qualify native Docker with firewalld independently.
- Complete the bounded host/VM destination inventory, IPv6 unicast/link-local/
  multicast and broadcast receiver checks, bridge-address inspection, actual
  service and achievable-root privileges, and raw/crafted packet feasibility.
  No reply alone is insufficient; a demonstrated bypass blocks the affected
  claim until the control or supported contract is revised.
- Complete authenticated `run_python`/coding-agent chat, repository staging,
  WebUI login/stream/replay/artifacts, configured MCP/Web connector route
  selection, and full disposable-document ingestion, query/internal search,
  source download, freshness, and restart recovery. Private documents are not
  test fixtures. Readiness and native-client checks do not replace these flows.
- Validate opt-in Slack/Discord conversations only with credentials and explicit
  messaging authorization. Deterministic request/tool checks remain mandatory.
- Qualify optional Tailscale/onion ingress and remaining route-failure cases
  without changing operator identities, accounts, funding, keys, or host
  firewall policy. Engine restart and leftover cleanup are operator recovery
  actions, not automatic test or startup actions.

No new Onyx runtime patch, executor-image change, dependency upgrade, host
firewall scheme, or Podman addressless bridge emulation is part of this design.

## Handoff state

Docker's tested full and lite stacks are stopped. Podman has no running stack
containers; three created containers remain because its storage-driver failure
also prevents normal Compose shutdown. Test-owned host document and MLX
processes were stopped through Make and the shared-data guard released Podman
ownership. No machine, engine, persistent store, or unknown endpoint was
force-deleted. The operator's `.env.wrapper` settings were not rewritten.
