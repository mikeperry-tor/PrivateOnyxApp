# Onyx Application Network Isolation Plan

> **Status: planned prerequisite.** This plan moves the Onyx application tier
> out of the trusted Mysterium routing namespace and makes its external and
> host-local HTTP(S) access depend on separate fixed public-only and
> host-capable egress bridges plus final-hop
> policy. Implement this plan before
> [Direct Obscura request handling](obscura_direct.md). Until implementation is
> complete, the normative deployed behavior remains documented in
> [VPN routing and restricted egress](../vpn_routing_and_proxies.md),
> [Internal network security](../internal_network_security.md), and
> [Request handling](../request_handling.md).

## Executive Decision

The current wrapper routes many environment-aware Onyx requests through a
loopback proxy, but `api_server`, `background`, `web_server`, `nginx`, and the
code-interpreter control service still share
`network_mode: service:netns-holder`. A client that ignores proxy settings can
therefore open a direct socket through the VPN namespace, or through Docker in
explicit no-VPN mode. Commit `71422cb477ca64ede56a01e3d67a03a9d16cbb5f`
improved routing coverage but did not create a network-enforced boundary.

Replace that topology with:

```text
host browser / optional Tailscale
             |
             v
      loopback publication or
      narrow frontend gateway
             |
             v
  internal Onyx application networks
    api_server / background / web / nginx
    code-interpreter control / trusted local helpers
       |                |                 |
       |                |                 +--> internal data/service peers
       |                +--> fixed request-path gateways
       |
       +--> fixed public-only bridge --> public Onyx policy listener -----+
       |                                                                 |
       +--> fixed host-capable bridge --> host-capable policy listener --+-->
                                                                         |
              Myst / configured upstream proxy / explicit no-VPN route <-+
              (the host-capable listener also has exact approved host
               exceptions; the public listener never does)
```

All Onyx application networks must be `internal: true`. Required internal
service traffic remains direct on explicitly selected internal networks.
Generic environment-aware external HTTP(S) uses the public-only bridge.
Remote MCP/OAuth and explicitly approved host-local MCP, embedding, and narrow
helper traffic use the host-capable bridge. Direct sockets that ignore proxy
configuration must fail closed because the application containers have no
externally routed network.

Both Onyx bridges must remain separate from the browser and executor bridges
planned by `obscura_direct.md`. The public-versus-host distinction is the
security boundary: only the host-capable Onyx listener may reach exact trusted
host-local destinations. Sharing that listener or bridge with a browser or
executor would create a path to the Docker host.

## Relationship to Other Plans

Implement this plan first. It establishes the application-side security
boundary and service URLs that the Direct Obscura migration must consume.
`obscura_direct.md` then removes CRW/CDP-shim services and consolidates the
browser-side policy and bridge topology without moving Onyx back into the
routing namespace.

Ownership is divided as follows:

| Concern | Owning plan |
| --- | --- |
| Onyx application networks and removal from `netns-holder` | this plan |
| Separate public-only and host-capable Onyx bridges/listeners | this plan |
| Onyx MCP HTTPX proxy correctness and target-DNS behavior | this plan |
| Internal service URL migration away from shared loopback | this plan |
| Host WebUI/doc publication and Tailscale-to-Onyx ingress | this plan |
| CRW removal and direct Onyx/SearXNG-to-Obscura fetching | `obscura_direct.md` |
| Separate renderer and executor public-only bridges | `obscura_direct.md` |
| SearXNG-owned search-provider scheduling | `obscura_direct.md` |
| General document single-fetch, size-cap, and parser isolation changes | `obscura_direct.md` |

Where implementation preparation overlaps, this plan owns the final network
shape. The Direct Obscura plan must use it rather than temporarily restoring
shared-loopback or shared-namespace assumptions.

## Required Reading and Version Scope

Before implementation, re-read:

- [VPN routing and restricted egress](../vpn_routing_and_proxies.md) for the
  current trusted namespace, DNS selection, upstream proxy, readiness, and
  autoheal behavior;
- [Internal network security](../internal_network_security.md) for the current
  bridge boundaries, Docker-internal hostname policy, Onyx SSRF interaction,
  and residual risks;
- [Request handling](../request_handling.md) for the current CRW, SearXNG,
  Obscura, MCP, `web_search`, and `open_url` request chains;
- [Onyx patch information](../onyx_patch_info.md) and
  [Onyx wrapper patch upgrades](../onyx_patches_upgrade.md) for strict runtime
  patch and source-shape requirements;
- [Local document RAG](../local_docs_rag_search.md) for doc-drop, embedding
  shim, source-link, freshness, and indexing behavior; and
- [Restricted egress implementation plan](implemented/restricted_egress.md)
  for the security properties that this migration must preserve.

This plan is written against [`stack.versions.env`](../../stack.versions.env):

| Component | Current pin/ref | Relevant audit locations |
| --- | --- | --- |
| Onyx | image `v4.2.5`; ref `b7482a59fb74503d5ec3dcde0ae5beac7b4905ff` | [`reference_repos/onyx`](../../reference_repos/onyx), especially `backend/onyx/tools/tool_implementations/mcp/mcp_ssrf.py`, `mcp_client.py`, `backend/onyx/utils/url.py`, connector clients, OAuth clients, Playwright helpers, and deployment Compose. |
| HTTPX | `0.28.1` in the pinned Onyx source | `reference_repos/onyx/pyproject.toml` and the installed client behavior. In this version, supplying an explicit transport disables environment-derived proxy mounts. |
| MCP Python SDK | `1.27.0` in the pinned Onyx source | streamable HTTP, SSE, `OAuthClientProvider`, redirects, discovery, registration, and token requests. |
| Code interpreter | image `0.4.4`; ref `8950eadc06567798ec61354f24260e5dc996684b` | [`reference_repos/python-sandbox`](../../reference_repos/python-sandbox), the control API, spawned executor network selection, and upload paths. |
| Mysterium | `local/private-onyx-myst:20260713` | [`myst`](../../myst), especially namespace ownership, host-route exemptions, kill switch, no-VPN readiness, and autoheal. |
| Compose topology | current branch | [`docker-compose.yaml`](../../docker-compose.yaml), mode and routing overrides, [`Makefile`](../../Makefile), and `onyx/helper-egress.env`. |

Re-audit these paths whenever their pins change. Do not copy assumptions from
this plan across an upgrade without source and runtime validation.

## Goals

1. Remove upstream Onyx application containers from the Mysterium namespace.
2. Make direct external sockets from Onyx application containers fail at the
   network layer in VPN, upstream-proxy, and explicit no-VPN modes.
3. Preserve environment-aware helper downloads, Playwright helpers, Web
   connectors, remote LLM/helper clients, and other supported outbound paths
   through the final-hop policy.
4. Route streamable HTTP and SSE MCP traffic, redirects, discovery, OAuth,
   registration, refresh, and token requests through the Onyx egress policy.
5. Preserve MCP access to exact `host.docker.internal` endpoints when the Onyx
   Admin SSRF setting permits private-network destinations.
6. Remove the bundled Obscura MCP service, its gateway, networks, policy
   routes, configuration, secrets, tests, and documentation rather than
   preserving a complexity-heavy feature that is no longer justified.
7. Preserve target-DNS confinement: public MCP/helper target names are resolved
   only by the selected final hop, except in explicit documented direct mode.
8. Preserve full-mode RAG, local embedding, doc-drop source links/freshness,
   WebUI access, Teep inference, and optional Tailscale behavior.
9. Preserve optional executor isolation; the code-interpreter control service
   may manage pods, but spawned executors retain their separate restricted
   network or `none`.
10. Simplify service names, networks, ports, dependencies, and health checks
    made unnecessary by shared namespace removal.
11. Keep VPN failure and reconnection fail closed without restarting unrelated
    application services or creating an autoheal storm.
12. Make every routing exception exact, stack-owned, startup-validated, and
    covered by deterministic tests.

## Non-Goals

- Do not remove Mysterium, `netns-holder`, final-hop policy, or the explicit
  no-VPN mode. They remain the selected final-hop substrate.
- Do not put `api_server`, `background`, code-interpreter control, or another
  application service directly on a policy-upstream network.
- Do not attach an Onyx application container to any non-internal Docker
  network merely to make `host.docker.internal` work.
- Do not rely on `HTTP_PROXY`, `HTTPS_PROXY`, or `NO_PROXY` as the security
  boundary. They select the intended route; internal network placement
  prevents bypass.
- Do not grant browser or executor bridges access to either Onyx listener, and
  never grant the public-only Onyx listener host/internal exceptions.
- Do not allow arbitrary Docker-internal names, the Docker gateway, LAN
  ranges, link-local metadata, or loopback merely because exact
  `host.docker.internal` MCP access is supported.
- Do not disable Onyx SSRF validation globally. Preserve the Admin control and
  its distinction between public, private-network, loopback, and link-local
  targets.
- Do not use an external upstream proxy for exact host-local destinations.
- Do not modify CRW, Obscura rendering, SearXNG parsing, PDF behavior, or
  search scheduling beyond the Compose caller-side attachments needed for
  this prerequisite.
- Do not add a transparent IP router, privileged firewall container, host
  networking, or container-level packet forwarding.
- Do not install packages in entrypoints, health checks, runtime patches, or
  bridge containers.

## Confirmed Current-State Findings

### Proxy Coverage Is Not Enforcement

`api_server` and full-mode `background` set uppercase and lowercase HTTP proxy
variables to `http://127.0.0.1:3132`, and the wrapper patches the shared
Playwright launcher. They nevertheless share `netns-holder`, so any explicit
socket or custom transport can use the namespace route directly.

The live pinned MCP factory supplies `_SSRFGuardAsyncTransport()` to
`httpx.AsyncClient`. HTTPX `0.28.1` sets `allow_env_proxies` only when
`transport is None`, so that factory has no environment-derived proxy mounts.
A live fixed request through the factory reached `https://example.com` while
the Onyx helper proxy logged no corresponding request. Remote MCP therefore
works today but bypasses restricted-egress policy; it leaves through Myst when
VPN mode is enabled or directly through Docker in no-VPN mode.

### Host MCP Works for the Wrong Structural Reason

`host.docker.internal` currently resolves and is reachable from the shared
namespace. Onyx permits it when the saved SSRF Protection level is
`ALLOW_PRIVATE_NETWORK` or `DISABLED`. It is listed in the generic helper
`NO_PROXY`, but MCP ignores that list because its custom transport ignores all
environment proxy selection. Host MCP succeeds by taking the same direct
namespace route as remote MCP.

An `internal: true` network does not provide that route. A live internal-only
SearXNG container had no default route and could not resolve
`host.docker.internal`. Adding an `/etc/hosts` entry alone would not create a
safe path to the host. The target must cross a policy-mediated gateway in the
trusted namespace.

### Shared Loopback Creates Unrelated Coupling

The common namespace currently forces or encourages:

- code-interpreter control on port `7000` because background metrics occupies
  `8000` in the same namespace;
- `CODE_INTERPRETER_BASE_URL=http://localhost:7000`;
- API/background model-server addresses at `127.0.0.1:9101`;
- doc-drop at `localhost:8091`;
- nginx/web/API loopback and namespace-IP health assumptions;
- host publisher containers that forward into `myst-client`; and
- direct `depends_on` edges from application services to Myst.

These are deployment artifacts rather than required Onyx semantics.

### Bundled Obscura MCP Is Removed, Not Re-Isolated

The bundled browser service currently adds a second Obscura process, a control
network, an egress network and bridge, a policy-side network and policy
service, an Onyx gateway, readiness edges, configuration, and documentation.
Preserving it after Onyx isolation would also require another exact trusted
route. Its limited benefit does not justify that topology or audit burden.

Delete the feature completely in this prerequisite. This includes
`obscura-mcp`, `obscura-mcp-gateway`, `obscura-mcp-egress-bridge`,
`mcp-browser-egress-proxy`, their networks/aliases/dependencies, related
environment and generated-secret handling, helper `NO_PROXY` entries, Make
targets/help, tests, endpoints, and setup/upgrade documentation. Do not retain
an off-by-default Compose profile, compatibility alias, saved setting, or
policy exception. Treat an obsolete enablement setting as a migration error
with a direct removal message rather than silently ignoring it.

## Target Network Model

### Trusted Routing Namespace

Retain `netns-holder` as the stable owner of the Myst namespace. It contains:

- `myst-client`;
- final-hop policy process or processes;
- exact routing/DNS readiness logic;
- VPN-only optional services that genuinely require namespace routing; and
- only the policy-side endpoints of fixed gateways.

It must not contain `api_server`, `background`, `web_server`, `nginx`,
code-interpreter control, local application helpers, or data services. Remove
the `api_server`, `background`, `web_server`, `nginx`, and `code-interpreter`
aliases currently assigned to `netns-holder`.

Give the routing namespace an explicitly named external-capable uplink network
rather than relying on accidental application use of Compose `default`.
Application and data services must not attach to that uplink.

### Internal Onyx Networks

Use the smallest practical set of explicit `internal: true` networks. A
recommended shape is:

| Network | Members and purpose |
| --- | --- |
| `onyx-frontend` | nginx, web server, API, and optional frontend ingress gateway; only HTTP application traffic. |
| `onyx-backend` | API, background, code-interpreter control, trusted wrapper helpers, and caller sides of fixed request-path gateways. |
| `onyx-data` | API/background and only their required PostgreSQL/cache/index/object-store peers. |
| `onyx-public-egress` | API/background generic environment-aware clients; only peer is the fixed public-only bridge. |
| `onyx-host-egress` | API MCP transport, local embedding shim, and explicitly approved narrow host-capable helpers; only peer is the fixed host-capable bridge. |
| existing restricted service networks | CRW, SearXNG, and later Obscura CDP remain behind fixed gateways; do not expose their control networks directly to general Onyx peers. |

Do not retain a broad default network attachment in addition to these
networks. Compose tests must assert that every application service has only
the expected internal networks and no `network_mode: service:netns-holder`.

The two retained caller-side networks `onyx-crw-ingress` and
`onyx-searxng-ingress` can be collapsed into `onyx-backend`: each gateway
remains a fixed-port forwarder whose service side stays on its separate
restricted control/API network. The common caller side does not merge CRW and
SearXNG service networks or make either gateway a generic relay. The later
Direct Obscura migration deletes the CRW gateway and adds the CDP gateway on
this same caller-side network.

### Separate Onyx Egress Bridges

Add two fixed bridges. Each has exactly two network attachments:

- `onyx-public-egress-bridge` attaches to `onyx-public-egress` and a dedicated
  internal `onyx-public-policy-upstream` shared only with `netns-holder`; and
- `onyx-host-egress-bridge` attaches to `onyx-host-egress` and a dedicated
  internal `onyx-host-policy-upstream` shared only with `netns-holder`.

Each bridge forwards one fixed proxy port to its matching fixed listener. Each
must:

- use a minimal immutable image;
- run as a numeric non-root user with a read-only filesystem, all capabilities
  dropped, and `no-new-privileges`;
- mount no source, secrets, Docker socket, or writable volumes;
- accept no client-selected upstream, operator command, or runtime package
  installation;
- expose no host, admin, metrics, DNS, or control port;
- disable IPv4 and IPv6 forwarding; and
- be tested as a TCP forwarder rather than an IP router.

Only generic public clients attach to `onyx-public-egress`. Only MCP,
embedding, and named narrow helpers attach to `onyx-host-egress`. Network
membership alone is not the request selector for API/background, which need
both classes: generic `HTTP_PROXY`/`HTTPS_PROXY` point to the public bridge,
while the strict MCP transport and each approved host helper use an explicit
host-bridge URL. Neither bridge may attach to browser, executor, data,
frontend, or restricted service control networks.

Use the same audited policy implementation for both classes, but run separate
processes/listeners with separate policy-side networks. A configuration or
process compromise in the public-only listener must not acquire the
host-capable route or exception table. Direct Obscura may later place the
generic-Onyx, browser, and executor public-only listeners in this same
public-only process, but it must preserve each listener's separate bridge and
policy-side network. It must keep the host-capable listener in its own process
with its distinct allowed bridge peer.

### Onyx Policy Listener Classes

Both Onyx listeners apply normal final-hop policy to public destinations:

- HTTP request-framing validation;
- URL/port/plain-HTTP policy;
- internal and Docker-name rejection;
- VPN provider DNS and address pinning when VPN is enabled without an upstream
  proxy;
- remote target DNS at the configured upstream proxy when one is set;
- explicit system/Docker DNS only in documented no-VPN direct mode; and
- credential-safe logging.

Only the host-capable listener additionally supports a small stack-owned
exact-destination table. At minimum, evaluate and test:

- `host.docker.internal:*` for trusted host-local MCP, OAuth, local model, and
  explicitly supported helper endpoints.

These exceptions must be exact host-and-port rules wherever the supported
feature has a fixed port. If arbitrary host MCP ports remain supported,
document that exact `host.docker.internal` intentionally exposes host TCP
services permitted by the Onyx Admin SSRF setting; continue to reject every
other Docker-internal name and link-local target.

Resolve exact host-local exceptions with the trusted namespace's system Docker
resolver, pin the selected address, and connect directly through the existing
host route exemption. Never send them through Myst provider DNS or an external
upstream proxy. On Linux, add and validate the required
`host.docker.internal:host-gateway` mapping on the namespace owner. Preserve
the Myst route exemption for `host.docker.internal` without enabling a broad
LAN bypass.

Policy exception matching must occur after canonical URL/hostname parsing and
before the normal blanket Docker-internal rejection. Reject credentials,
ambiguous IP syntax, trailing-dot tricks, IDNA confusables, redirects to
unapproved internal names, and link-local/cloud-metadata addresses. Preserve
an always-on denial floor for:

- `localhost`, `localhost.localdomain`, their subdomains, and loopback
  literals;
- every unapproved `*.docker.internal` and `*.containers.internal` name,
  including `gateway.docker.internal`, `vm.docker.internal`,
  `kubernetes.docker.internal`, `host.containers.internal`, and
  `gateway.containers.internal`;
- legacy Docker Desktop spellings including `docker.for.mac.*.internal` and
  `docker.for.win.*.internal`;
- single-label Docker service, container, and network-alias names; and
- configured deployment-local suffixes plus all non-public resolved address
  ranges.

Only the exact host-capable-listener exceptions named above can precede that
floor. The public-only listener has no exception table and always applies the
floor.
They do not create a suffix exception, so subdomains and newly introduced
Docker/Podman aliases remain denied by default.

## MCP, OAuth, and Target DNS

### Explicit MCP Transport

Patch the pinned Onyx MCP client narrowly and strictly. Introduce a
stack-owned `ONYX_MCP_HTTP_PROXY_URL` pointing to
`http://onyx-host-egress-bridge:3128`. It is not a user-facing routing knob.

The MCP factory must:

1. Validate the exact proxy URL and reject credentials, paths, query,
   fragments, unexpected hosts, or ports at startup.
2. Instantiate the SSRF-guarded `AsyncHTTPTransport` with that explicit HTTP
   proxy.
3. construct `httpx.AsyncClient(..., trust_env=False, transport=...)` so
   environment settings cannot silently replace or bypass the selected route.
4. Preserve the existing timeout, headers, authentication, redirect, SSE, and
   streamable-HTTP behavior.
5. Revalidate every request produced by the MCP SDK, including redirects,
   protected-resource discovery, authorization-server metadata, dynamic
   registration, token exchange, and refresh.
6. Fail startup in strict mode if the pinned HTTPX/MCP signatures or transport
   behavior no longer match.

Do not implement this by removing the custom SSRF transport and falling back
to generic environment handling. The guard exists because the MCP SDK creates
additional URLs from server responses.

### DNS-Safe MCP Validation

The current fetch-time guard calls `socket.getaddrinfo()` before sending the
request. That would fail or leak public target names through Docker's embedded
resolver after network isolation.

Split validation responsibilities:

- Onyx performs scheme, credential, syntax, normalized-hostname, IP-literal,
  always-blocked-name, Admin SSRF-level, and exact trusted-host checks without
  resolving arbitrary public names.
- The final-hop policy resolves and validates public names using the selected
  VPN/upstream/no-VPN mode and rechecks every CONNECT/forward request.
- Exact `host.docker.internal` destinations are resolved only by their
  authorized host-listener exception.

At strict SSRF levels, `host.docker.internal` must be rejected structurally
even without DNS. At `ALLOW_PRIVATE_NETWORK`, allow that exact non-loopback
host identity but retain loopback and link-local denial. At `DISABLED`, retain
the always-on link-local/cloud-metadata floor. Store-time and fetch-time
behavior must remain consistent, and redirects cannot downgrade the decision.

With a configured remote upstream proxy, a public-looking hostname that the
upstream resolves privately remains the documented upstream-side DNS residual
risk. Do not reintroduce a local lookup to mask it.

### Generic Helper and `NO_PROXY` Rules

Set uppercase and lowercase HTTP proxy variables on approved Onyx helpers to
the bridge URL rather than loopback. Update the Playwright patch to accept only
that exact stack-owned URL.

Keep `NO_PROXY` stack-owned and limited to service names reachable directly on
the explicit internal networks. Remove `host.docker.internal`; host traffic
must cross policy. Remove obsolete aliases and add only current service or
gateway names. Never propagate this list to executor pods.

Libraries with explicit transports, disabled environment discovery, custom
DNS, raw sockets, browser launch flags, or SDK-specific clients must be audited
individually. The network boundary makes a missed client fail closed, but every
supported feature still needs an intentional route and regression test.

## Internal Service URL Migration

Replace shared-loopback URLs with service DNS names on internal networks:

- restore code-interpreter control to its normal port `8000` and use
  `http://code-interpreter:8000` from API/background;
- point API/background model-server-compatible calls to
  `http://local-embedding-shim:9101` when the shim remains the selected
  implementation;
- use service names for API, web, nginx, Teep, database, cache, index, object
  store, and request-path gateways;
- keep health checks container-local where they check the service itself, but
  use service DNS for dependency checks; and
- remove comments, environment overrides, and tests that exist only because
  two unrelated services collided on one network namespace.

Do not expose data services to the egress network. API/background may attach
to both `onyx-backend` and `onyx-data`; each egress bridge may attach only to
its named caller network and policy-side network.

### Local Document RAG

Move `doc-drop-web` to an internal Onyx network, publish its diagnostic/source
port directly on the configured loopback host bind, and configure the Web
connector to crawl `http://doc-drop-web:8091/`. Remove the old policy-mediated
loopback fetch path.

The pinned Web connector uses the fetched URL as both document identity and
`TextSection.link`; it has no fetch/display URL split. Add a narrow, strict
background patch for this exact configured connector that retains the
internal URL for crawl identity but rewrites only returned display links to
the configured host-clickable base, preserving normalized paths and encoding.
The agent receives indexed excerpts and treats these URLs as display/citation
links; it does not retrieve them during `internal_search`. That makes the
display-only rewrite semantically safe, but does not eliminate the rewrite:
without it, users would receive container-only links.

Update freshness checks to use the exact `doc-drop-web` service host. Existing
connectors/documents whose identity used the old loopback URL must be recreated
and reindexed; do not attempt an ambiguous in-place ID rewrite. Test recursive
paths, URL encoding, clickability, PDF freshness, and absence of internal
service URLs from displayed citations.

### Local Embedding Upstream

Move `local-embedding-shim` to an internal network and make its
environment-aware HTTP client reach the configured upstream through the
host-capable Onyx bridge. The
default `host.docker.internal` upstream must use the exact host exception.
Public embedding upstreams use normal final-hop policy. API/background reach
only `local-embedding-shim:9101`, never its upstream directly.

If the shim cannot use an explicit proxy without changing request semantics,
patch its small owned implementation rather than attaching it to an external
network. Validate `/ready`, query/passage prefixes, model conversion, API-key
redaction, indexing, and `internal_search`.

## Ingress and Optional Exposure

### Host WebUI and Doc Ports

Once nginx and doc-drop no longer share the VPN namespace, publish their
supported host ports directly and bind them to configured loopback/host
addresses. Remove `host-web-proxy` and `host-doc-drop-web-proxy`. Direct host
publication is ingress; it
must not add a non-internal egress network to the service.

Validate Docker and Podman behavior. Preserve the current user-facing port
variables and defaults. Do not bind a previously loopback-only endpoint to all
interfaces by accident.

### Tailscale

Tailscale must reach nginx without requiring nginx to share its namespace.
Do not attach the Tailscale container directly to `onyx-frontend`: it needs an
external routing path of its own when it does not route through Myst and would
therefore become a dual-homed pivot into the application network.

Use a fixed HTTP frontend gateway with exactly one configured nginx upstream.
Its Onyx side attaches to `onyx-frontend`. Its caller side attaches to a narrow
Tailscale-only ingress network when Tailscale has its own routing uplink, or is
published into the Myst namespace when Tailscale itself must route through
Myst. The gateway must not be a CONNECT, SOCKS, generic TCP, or
client-selected reverse proxy, and it must expose no other Onyx service.
Tailscale targets that gateway rather than nginx directly. Do not attach nginx
or Tailscale itself to both a routed network and an Onyx application network.

Keep the gateway and Tailscale service structurally absent when Funnel is
disabled. Health checks must traverse the configured target path.

### Teep

Teep remains a separate inference component. Onyx reaches it by service name
on an internal caller network. If Teep is configured to route through Myst,
retain its namespace placement and expose a fixed internal gateway to Onyx;
do not move the Onyx application tier back into the namespace. Host Teep
publication and any `host-teep-proxy` removal remain coordinated with the
Direct Obscura service-reduction work unless this plan necessarily changes
them first.

## Service and Network Simplifications

Isolation enables these reductions independently of CRW/Obscura removal:

1. Remove `host-web-proxy`; publish nginx directly on the configured host bind.
2. Remove `host-doc-drop-web-proxy` after moving `doc-drop-web` out of the
   routing namespace and publishing it on the configured loopback bind.
3. Restore code-interpreter control port `8000`; remove the `7000` collision
   workaround and duplicate loopback URL overrides.
4. Remove Onyx service aliases from `netns-holder`.
5. Remove direct `netns-holder` and `myst-client` dependencies from API,
   background, web, nginx, code-interpreter control, and isolated helpers.
   Gate only actual egress clients on the complete bridge/policy readiness
   chain.
6. Collapse the retained CRW and SearXNG caller-side ingress networks into
   `onyx-backend` while keeping each gateway's restricted service-side network
   separate.
7. Replace shared-loopback dependency URLs with ordinary Compose service DNS,
   eliminating namespace-specific health scripts and interface-IP discovery.
8. Remove `host.docker.internal` from generic helper `NO_PROXY` and delete
   obsolete aliases as services move.
9. Put PostgreSQL, cache, index, MinIO, and other runtime data peers on explicit
   internal networks rather than an implicit external-capable default network,
   where their upstream images permit it.
10. Give the Myst namespace an explicit routing uplink so accidental use of
    Compose `default` cannot grant application egress.
11. Make Tailscale and its fixed frontend gateway conditional rather than
    retaining an always-on shared-namespace dependency or making Tailscale a
    dual-homed Onyx peer.
12. Remove the bundled Obscura MCP service and all of its gateway, network,
    policy-route, secret, environment, readiness, test, and documentation
    artifacts. Reject stale enablement variables during migration so an old
    configuration cannot appear to enable a removed feature.
13. Remove readiness checks and restart edges that exist only to coordinate
    unrelated services sharing one namespace.

The migration adds two Onyx egress bridges and may add a conditional Tailscale
frontend gateway. Count reductions and additions from effective Compose models
rather than claiming an unconditional net service count.

Do not use this plan to remove SearXNG Valkey, upstream model-server
containers, CRW, CDP shim, browser policy services, or renderer bridges. Those
items remain owned by `obscura_direct.md`, even if their eventual removal is
already known.

### Simplifications Explicitly Rejected

Do not reduce service count by erasing a security boundary:

- Do not combine either Onyx egress bridge with future browser or executor
  bridges. Browser and executor paths must never reach the host-capable
  listener, and their bridges remain separate from each other.
- Do not replace the fixed CRW/SearXNG/CDP gateways with one generic
  multiport or client-selected relay. Sharing `onyx-backend` on the caller side
  is safe only because each gateway still has one fixed destination on one
  separate service-side network.
- Do not merge frontend, backend, data, public-egress, host-egress, and
  routing-uplink
  networks merely to shorten Compose. Their membership defines materially
  different lateral reachability.
- Do not attach Tailscale directly to `onyx-frontend` while it also has an
  external or VPN uplink. Keep the fixed HTTP ingress gateway even though it
  costs one conditional service.
- Do not reuse either Onyx egress bridge as the Tailscale ingress gateway, an
  internal service mesh, or an executor gateway. Direction, protocol, trusted
  destinations, and compromise impact differ.

These rejected merges should be encoded as negative Compose tests so a later
service-count cleanup cannot silently recreate them.

## Readiness, VPN Recovery, and Autoheal

Use this dependency chain for egress-capable Onyx services:

```text
myst-client data-plane readiness (VPN mode)
  or explicit no-VPN namespace readiness
    -> both required Onyx policy listener readiness checks
    -> matching fixed Onyx egress bridge end-to-end readiness
    -> API/background/helper readiness
```

Policy readiness must retain the target-free checks from the restricted-egress
implementation: provider-DNS validation in direct VPN/no-VPN mode or
upstream-proxy endpoint bootstrap plus protocol/authentication handshake. The
bridge health check must traverse the bridge and receive the expected policy
denial for a blocked local target.

Application services no longer depend directly on `myst-client` merely because
they share its namespace. Egress-capable services depend on the bridge/policy;
pure internal services depend only on their real application/data peers. A
post-start policy failure remains a visible request failure; do not add direct
fallback.

Treat the graph above as startup ordering, not as a reason to make the API's
own health endpoint perform a transitive Internet/VPN probe. API, background,
web, code-interpreter control, and local-helper health checks should verify
their own local function. Their Compose startup dependencies may require the
bridge/policy to become healthy once, while the bridge and policy retain their
own continuously meaningful health. Do not set dependency-restart behavior
that restarts Onyx applications merely because Myst or the policy restarts.
This keeps a tunnel flap visible on egress requests without turning it into an
application restart cascade.

Only Myst retains the `autoheal=true` recovery role in VPN mode. Restarting
Myst leaves `netns-holder` stable, the policy recovers in place, and isolated
Onyx services remain running. This prerequisite makes explicit no-VPN models
omit autoheal; the Direct Obscura migration must preserve that layer boundary.
Do not autoheal application, bridge, or gateway containers in response to a
tunnel flap.

Validate:

1. VPN-enabled cold start and healthy target-free DNS probe.
2. Explicit no-VPN cold start with no nonexistent-tunnel wait.
3. VPN drop and reconnection with direct sockets still impossible.
4. Configured HTTP(S), SOCKS5, and SOCKS5h upstream proxies with no public
   target-DNS lookup by Onyx.
5. Policy or bridge failure after startup, producing fail-closed typed errors.
6. Host MCP during VPN operation without routing the host connection through
   Myst or the upstream proxy.
7. Myst restart without application restarts, stale namespace references, or
   autoheal storms.

## Security Model and Residual Risks

After migration:

- an Onyx application compromise can reach required application/data peers and
  its explicitly selected fixed proxy bridges, but has no direct Internet,
  host-gateway, routing
  namespace, browser-control, executor, or Docker-socket path except where the
  service explicitly requires one;
- code-interpreter control remains highly trusted because it owns the Docker
  socket, but its HTTP service no longer receives general namespace egress;
- the two fixed Onyx bridges are the only multi-homed exceptions between their
  Onyx egress classes and policy upstreams;
- the policy, not application DNS, is authoritative for public target
  resolution and private-address rejection;
- exact host exceptions intentionally expose approved host services to trusted
  Onyx callers when Admin SSRF policy permits them; and
- browser and executor peers cannot reach either Onyx listener.

Residual risks to document:

- a compromise or generic-relay misconfiguration in either Onyx bridge could
  pivot between its two attached networks;
- a compromise of the final-hop policy process has trusted namespace access;
- the code-interpreter control service's Docker socket remains a host-level
  capability independent of network isolation;
- an upstream proxy may resolve a public-looking name privately unless it
  enforces equivalent destination policy;
- permitting arbitrary ports on exact `host.docker.internal` exposes whatever
  host services listen on those ports to an SSRF-permitted Onyx MCP/helper
  request; and
- required internal peers remain laterally reachable according to the selected
  application/data network grouping.

Do not claim container networking protects against Docker daemon compromise or
an operator attaching new networks after startup.

## Implementation Workstreams

### Workstream 0: Characterize and Lock Current Behavior

1. Add deterministic tests proving the current MCP custom transport lacks
   environment proxy mounts.
2. Inventory every Onyx/connector SDK that disables environment proxies,
   supplies a custom transport, performs raw DNS, or opens raw sockets.
3. Inventory every current `localhost`, `127.0.0.1`, namespace-IP,
   `host.docker.internal`, and netns-holder alias dependency.
4. Record effective lite/full/VPN/no-VPN/proxy/Tailscale/executor Compose
   models and service counts.
5. Lock current MCP streamable HTTP, SSE, OAuth, redirect, host, connector,
   local RAG, and inference behavior with tests.
6. Inventory every bundled Obscura MCP artifact that must be deleted and add a
   negative test that rejects stale enablement configuration.

### Workstream 1: MCP and Policy Correctness

1. Add the strict explicit MCP proxy transport.
2. Split non-resolving Onyx validation from authoritative final-hop DNS.
3. Add exact host exception handling only to the host-capable Onyx policy
   listener, and prove the public-only listener rejects the same target.
4. Preserve Admin SSRF level semantics on initial URLs and every derived URL.
5. Add Linux host-gateway and Myst route-exemption validation.
6. Add focused unit tests before changing network placement.

### Workstream 2: Internal Application Networks

1. Add explicit frontend, backend, data, public-egress, and host-egress
   internal networks.
2. Move API/background first while retaining fixed gateways to the current
   CRW, SearXNG, Teep, and internal services.
3. Move web/nginx and replace shared-namespace upstream assumptions.
4. Move code-interpreter control, restore port `8000`, and revalidate executor
   creation and separate executor networking.
5. Move or explicitly classify local embedding/doc-drop helpers.
6. Remove application aliases and attachments from `netns-holder`.
7. Remove application attachment to implicit external-capable networks.

### Workstream 3: Separate Onyx Egress Bridges

1. Build or reuse an audited fixed-forwarder image with immutable provenance.
2. Add the public-only and host-capable component and policy-side networks.
3. Expose each policy listener only to its expected bridge peer and prove that
   the public listener has no host exception capability.
4. Retarget generic helper/Playwright proxy variables to the public bridge and
   MCP/embedding/narrow host helpers to the explicit host bridge URL.
5. Update stack-owned `NO_PROXY` for internal service DNS only.
6. Prove packet forwarding and client-selected destinations are impossible.
7. Remove the old loopback-only helper client assumption.

### Workstream 4: Ingress and Local Services

1. Publish nginx directly on the configured host bind and remove
   `host-web-proxy`.
2. Move doc-drop internally, add the strict display-link-only rewrite, reindex
   affected connectors, and remove `host-doc-drop-web-proxy`.
3. Add the conditional fixed Tailscale frontend gateway and its narrow caller
   network; keep Tailscale itself off `onyx-frontend`.
4. Route local embedding upstream through the host-capable Onyx policy.
5. Retarget Teep and internal model-service URLs without changing inference
   trust semantics.

### Workstream 5: Readiness and Compose Cleanup

1. Replace direct Myst dependencies with real bridge/policy or internal-service
   dependencies.
2. Remove namespace-collision ports, aliases, health scripts, and comments.
3. Render every supported Compose matrix and assert network membership.
4. Exercise Myst reconnection and no-VPN startup.
5. Delete every bundled Obscura MCP artifact and verify no effective Compose
   model or user-facing setting retains it.
6. Update documentation atomically and hand the resulting topology to
   `obscura_direct.md`.

## Documentation Updates

### `README.md`

- Explain that Onyx application containers have no direct route and that
  supported external traffic crosses restricted egress.
- Preserve Admin SSRF instructions for host MCP and explain that the connection
  is policy-mediated rather than a `NO_PROXY` bypass.
- Update host endpoints, local RAG URLs, Tailscale behavior, troubleshooting,
  and service counts.
- Remove shared-namespace and port-7000 instructions.

### `docs/vpn_routing_and_proxies.md`

- Restrict the trusted namespace inventory to Myst, final-hop policy, and
  genuine VPN-side services.
- Add both Onyx bridges and policy-side networks, their distinct peer sets,
  the host-only exception table, and the DNS matrix.
- Explain explicit MCP transport handling and why environment variables alone
  were insufficient.
- Update readiness, autoheal, upstream proxy, Teep, Tailscale, and no-VPN
  diagrams.

### `docs/internal_network_security.md`

- Make Onyx application networks part of the enforced boundary rather than a
  trusted shared-namespace exception.
- Document permitted peers per network, both Onyx bridges, the exact
  host-listener exception, Docker-internal denial, and residual pivot risk.
- Update reachability tables and negative-path evidence.

### `docs/request_handling.md`

- Update Onyx helper, MCP/OAuth, Web connector, local Chromium, and gateway
  request chains.
- Distinguish internal service DNS from public target DNS.
- Preserve current CRW/Obscura behavior until Direct Obscura is implemented.
- Remove the bundled Obscura MCP request chain and all setup, endpoint,
  troubleshooting, routing, and security claims for that removed service.

### `docs/onyx_patch_info.md` and `docs/onyx_patches_upgrade.md`

- Document the strict MCP proxy/DNS patch and revised Playwright proxy URL
  validation.
- Add source-shape anchors for HTTPX transport construction, MCP SDK clients,
  redirects, OAuth, and SSRF settings.
- Add upgrade tests that fail if environment proxy behavior or SDK factory
  signatures change.
- Update code-interpreter, port, and internal service URL assumptions.

### `docs/local_docs_rag_search.md`

- Document the internal doc-drop crawl URL, display-only link rewrite, direct
  host publication, and mandatory connector recreation/reindex migration.
- Update embedding-shim service URL, host upstream routing, readiness, and
  failure diagnostics.
- Revalidate source-link clickability, freshness, PDF reindexing, and
  `internal_search`.

### `.env.wrapper.example`, `AGENTS.md`, and Plans

- Do not make stack-owned bridge URLs or `NO_PROXY` values user-facing.
- Preserve user-facing SSRF, host port, VPN, upstream-proxy, and local embedding
  settings with accurate semantics.
- Update `AGENTS.md` runtime shape, key locations, invariants, and test guidance.
- Make `obscura_direct.md` depend on this plan and remove its obsolete shared
  namespace assumptions.
- Remove bundled Obscura MCP settings, examples, secrets, endpoints, routing
  diagrams, service counts, upgrade steps, and residual-risk text from
  `README.md`, `.env.wrapper.example`, `AGENTS.md`, request/routing/security
  docs, patch/upgrade docs, Make help, and every current plan. Document the
  removal as an intentional unsupported-feature migration, not a disabled
  optional mode.
- Use a repository-wide removal checklist covering at least
  `docker-compose.yaml`, both mode overlays, `onyx/helper-egress.env`,
  `README.md`, `.env.wrapper.example`, `docs/request_handling.md`,
  `docs/vpn_routing_and_proxies.md`, `docs/internal_network_security.md`,
  `docs/local_docs_rag_search.md`, `docs/onyx_patches_upgrade.md`,
  `plans/proxy-support.md`, and `plans/env_wrapper_reorg.md`. For the historical
  implemented restricted-egress decision record, add a clear supersession note
  to affected sections rather than rewriting what was implemented at that
  time. Repository searches must leave occurrences only in that marked
  historical record, upstream reference checkouts, and this removal plan.

## Test Plan

### Deterministic Unit Tests

Add focused cases under `tests/` for:

- exact MCP proxy URL validation and invalid values;
- HTTPX custom transport construction with the explicit proxy and
  `trust_env=False`;
- streamable HTTP and SSE factory use;
- initial URL, redirect, OAuth metadata, registration, authorization, token,
  and refresh validation;
- no public target DNS call in Onyx when the final hop is authoritative;
- SSRF levels for public, RFC1918, loopback, link-local, exact
  `host.docker.internal`, Docker/Podman/legacy aliases and
  suffixes, single-label services, trailing dots, IDNA, and IP literals;
- exact policy exception matching, system resolver selection, address pinning,
  and no external-upstream use for host targets;
- normal VPN/upstream/no-VPN public resolver selection;
- HTTP framing and credential redaction regression;
- generic-helper failure when the public bridge/policy is unavailable, MCP
  and embedding failure when the host bridge/policy is unavailable, and no
  cross-class fallback;
- internal doc-drop identity/display-link rewriting, path normalization,
  encoding, and strict source-shape failure;
- rejection of every removed bundled Obscura MCP setting or route; and
- stack-owned `NO_PROXY` content and executor non-propagation.

Use fake DNS, proxy, and HTTP transports. Unit tests must not require Internet,
VPN credentials, the Docker host, or `.env.wrapper`.

### Compose and Static Security Tests

Parse effective models structurally and assert:

- no Onyx application service uses `network_mode: service:netns-holder`;
- only Myst, final-hop policy, and explicitly justified VPN-side services share
  the namespace;
- every Onyx application/data network is `internal: true`;
- no Onyx application service attaches to the routing uplink, policy upstream,
  browser, executor, or restricted service control networks;
- each Onyx bridge has exactly its two networks and hardening;
- browser and executor bridges are separate and cannot reach either Onyx
  listener or policy-side network;
- the public-only listener cannot route any host/private destination and has
  no host-exception configuration;
- caller-side request gateways attach to `onyx-backend` and exactly one
  restricted service network, expose one fixed destination, and cannot relay
  to another gateway's service;
- `host.docker.internal` is absent from generic helper `NO_PROXY`;
- code-interpreter control uses service port `8000`, while executor networking
  remains separately selected and restricted;
- host WebUI/doc ports bind only as configured and obsolete host publishers are
  absent;
- Tailscale/frontend gateways exist only in enabled layers; Tailscale never
  attaches directly to `onyx-frontend`, and the fixed HTTP gateway has no
  egress/helper/data network;
- direct Myst dependencies are absent from pure application services; and
- no bundled Obscura MCP service, gateway, network, route, environment value,
  secret, health check, or conditional profile exists; and
- health dependencies follow the target graph in lite and full modes.

Inspect container routes at runtime: application containers must have only
connected internal routes and no default route capable of reaching the host or
Internet.

### Runtime Matrix

Test at least:

| Mode | Required checks |
| --- | --- |
| lite, VPN | remote MCP, helper download, Playwright helper, search/open URL, Teep, direct-socket denial, Myst reconnect |
| lite, no VPN | same functional paths through explicit no-VPN policy; no stalled Myst readiness or direct application route |
| lite, upstream HTTP/HTTPS proxy | remote MCP/helper target DNS stays at upstream; exact host MCP remains local |
| lite, upstream SOCKS5/SOCKS5h | target DNS behavior matches scheme and no direct fallback occurs |
| full, VPN/no VPN | all lite checks plus doc-drop crawl, source links, PDF freshness, embeddings, indexing, and `internal_search` |
| MCP strict SSRF | host MCP rejected; remote public MCP succeeds through policy |
| MCP allow-private | exact host MCP succeeds; loopback/link-local/other Docker names remain denied |
| executor network off/on | control plane works; pods remain `none` or on the dedicated executor network and never inherit Onyx exceptions |
| Tailscale off/on and routed/unrouted | disabled services absent; enabled ingress reaches nginx without adding application egress |

For a live remote MCP test, use a controlled fixture or test server and prove
the Onyx policy logged the request while the application container had no
direct route. For host MCP, run a fixture bound only on the Docker host and
prove the policy used the exact local exception. Test remote-to-host and
host-to-link-local redirects under every SSRF level.

### Regression and Failure Tests

- Stop each Onyx policy independently and prove only its expected class fails,
  with no cross-class fallback.
- Stop each bridge independently and prove its expected clients fail while
  internal service calls and the other egress class remain healthy.
- Drop Myst and prove policy readiness fails, direct sockets remain impossible,
  and internal chat/data health is not needlessly restarted.
- Change an MCP/HTTPX signature in a fixture and prove strict startup fails.
- Verify local model, GitHub repository download, Web connector, Highspot,
  OAuth refresh, LLM provider, and remaining helper clients use their intended
  path.
- Verify host and service DNS do not leak public MCP/helper names.
- Verify logs contain no query values, credentials, OAuth tokens, proxy
  passwords, MCP headers, or document contents.

Run the repository suite:

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Also run `make help`, render every affected Makefile-selected Compose model,
and exercise `make up-lite` and `make up-full` where credentials and runtime
permit.

## Rollout and Migration

1. Land deterministic MCP/policy tests and the explicit transport patch while
   the current namespace still provides a fallback route; verify the actual
   request uses policy, not the fallback.
2. Add internal networks, both fixed bridges, and both final-hop listeners with
   strict readiness.
3. Move API/background and retarget all internal URLs and gateways.
4. Move web/nginx/code-interpreter control and change host ingress.
5. Move or explicitly classify doc-drop and embedding helpers.
6. Remove shared namespace aliases, application attachments, direct Myst
   dependencies, loopback workarounds, and obsolete publisher services.
7. Exercise every mode from a clean Compose state and verify direct-socket
   denial before declaring cutover.
8. Update all current documentation atomically.
9. Mark this plan implemented and only then begin the Direct Obscura cutover.

Do not maintain a supported hybrid where some API/background replicas share
`netns-holder` and others use the isolated networks. That would make failures
and bypass behavior replica-dependent.

Existing saved MCP, Web connector, LLM, and local embedding URLs are application
data. Provide explicit migration instructions or a narrowly validated migration
for any URL that must change; Compose cannot rewrite stored records.

## Acceptance Criteria

- No upstream Onyx application container shares `netns-holder` or an
  external-capable Docker network.
- A direct public IP socket and a direct host-gateway socket fail from API and
  background in every supported mode.
- Environment-aware helpers and patched Playwright work through the fixed
  public-only Onyx bridge and final-hop policy.
- Remote MCP streamable HTTP, SSE, redirects, discovery, OAuth, registration,
  token, and refresh traffic crosses policy with no application-side public
  target DNS lookup.
- Exact `host.docker.internal` MCP works only at the intended SSRF levels,
  stays local, and never traverses the external upstream proxy.
- The bundled Obscura MCP service and all of its configuration, secrets,
  routes, networks, tests, and documentation are absent; stale enablement
  configuration is rejected explicitly.
- Loopback, link-local metadata, other Docker-internal names, private IPs at
  strict levels, and redirect bypasses remain denied.
- Browser and executor bridges are separate and cannot reach either Onyx
  listener, the host exception, application networks, or data networks.
- Internal Onyx, data, Teep, request-gateway, code-interpreter-control, and RAG
  traffic uses explicit service networks and DNS names.
- The code-interpreter port collision workaround and netns-holder application
  aliases are absent.
- Host WebUI/doc and optional Tailscale exposure work without restoring
  application egress.
- VPN/no-VPN/upstream-proxy startup and Myst reconnection pass without direct
  fallback, stale namespace references, or autoheal storms.
- No runtime patch, health check, bridge, or shim installs packages.
- Every listed documentation file describes the deployed topology and no
  current section claims that Onyx applications share the VPN namespace.
- `obscura_direct.md` consumes this topology as a completed prerequisite.

## Final Ownership Summary

| Behavior | Owner after isolation |
| --- | --- |
| Onyx application/data reachability | explicit internal Compose networks |
| generic Onyx public HTTP(S) route | fixed public-only Onyx bridge and listener |
| remote MCP/OAuth transport | explicit SSRF-guarded HTTPX proxy transport |
| public target DNS and address validation | selected final-hop policy |
| exact host MCP/embedding/narrow-helper route | fixed host-capable Onyx bridge and exact policy exception in trusted namespace |
| browser and executor public routes | separate bridges owned by Direct Obscura |
| VPN readiness and recovery | Myst plus target-free policy readiness and Myst-only autoheal |
| internal service bypasses | stack-owned `NO_PROXY` plus internal networks, never host/public names |
| direct-socket denial | absence of an external/default route from application containers |

The core result is not merely that supported clients use a proxy. It is that
unsupported or compromised clients cannot obtain another route.
