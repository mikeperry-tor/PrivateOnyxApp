# Onyx Application Network Isolation Plan

> **Status: implemented (2026-07-14).** This plan moved the Onyx application tier
> out of the trusted Mysterium routing namespace and makes its external and
> host-local HTTP(S) access depend on separate fixed public-only and
> host-capable egress bridges plus final-hop
> policy. This prerequisite is complete for
> [Direct Obscura request handling](obscura_direct.md). Normative deployed
> behavior is documented in
> [VPN routing and restricted egress](../vpn_routing_and_proxies.md),
> [Internal network security](../internal_network_security.md), and
> [Request handling](../request_handling.md).
>
> **Docker Desktop implementation note:** Docker records but does not activate
> published ports for containers attached only to `internal: true` networks.
> The implementation therefore keeps nginx and doc-drop internal and uses
> hardened, fixed-destination host publishers on a non-masqueraded edge
> network. This is a narrower platform-required deviation from workstream 4's
> direct-publication wording; it does not restore the old routing namespace or
> grant application containers an egress route.
>
> **Web Connector implementation note:** the deployed stack sends doc-drop
> through the host request policy and one exact fixed gateway instead of using
> a process-wide direct crawl exception. This keeps requests, redirects, and
> browser subresources policy-mediated while preserving the stable
> `http://doc-drop-web:8091/` document identity.
>
> **Configured-provider implementation note:** the deployed minimal patch
> covers configured chat clients for the supported LiteLLM provider set. The
> exact internal Teep base uses a direct, environment-independent client;
> other configured chat bases use the host policy. Private configured image,
> speech, arbitrary embedding/reranking, and Admin discovery endpoints remain
> unsupported rather than relying on broad or fragile SDK patches. Current
> support bounds are documented in
> [Onyx patches](../onyx_patch_info.md#mcpoauth-web-connector-and-configured-inference).

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
       +--> fixed public-only bridge --> public request-policy namespace
       |                                      |
       |                                      v
       |                              public-only route broker -----------+
       |                                                                  |
       +--> fixed host-capable bridge --> host request-policy namespace   |
                                              |                           |
                                              v                           v
                                      host-capable route broker --> selected
                                        (exact host and optional        final
                                         RFC1918 exceptions only)       route
```

All Onyx application networks must be `internal: true`. Required internal
service traffic remains direct on explicitly selected internal networks.
Generic environment-aware external HTTP(S) uses the public-only bridge.
MCP/OAuth and admin-configured Web Connector traffic selects the public or
host-capable bridge from the saved Admin SSRF level. Supported
user-configured chat inference base URLs and explicitly approved host-local
embedding/narrow-helper traffic use the host-capable bridge. Provider-default
inference endpoints remain on the public-only bridge.
Direct sockets that ignore proxy configuration must fail closed because the
application containers have no externally routed network.

Both Onyx bridges must remain separate from the browser and executor bridges
planned by `obscura_direct.md`. The public-versus-host distinction is the
security boundary: the public and host request-policy processes run in
different network namespaces with different route-broker networks, and only
the host route broker may use exact host-local or explicitly enabled RFC1918
destinations. Those host-route exceptions also permit plain HTTP when general
cleartext URLs are disabled, but only after the broker authoritatively
validates the exact host or complete RFC1918 answer set. Sharing that listener,
namespace, route broker, or bridge with a browser or executor would create a
path to the Docker host or LAN.

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
| Separate public-only and host-capable Onyx bridges, policy namespaces, and route brokers | this plan |
| Onyx MCP, configured Web Connector/inference proxy correctness, SSRF defaults, and target-DNS behavior | this plan |
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
   connectors, provider-default and configured inference clients, remote
   helper clients, and other supported outbound paths through the final-hop
   policy.
4. Route streamable HTTP and SSE MCP traffic, redirects, discovery, OAuth,
   registration, refresh, and token requests through the Onyx egress policy.
5. Preserve MCP access to exact `host.docker.internal` endpoints when the Onyx
   Admin SSRF setting permits private-network destinations.
6. Preserve opt-in RFC1918 MCP, admin-configured Web Connector, embedding, and
   inference endpoints behind one generalized network-policy option.
7. Remove the bundled Obscura MCP service, its gateway, networks, policy
   routes, configuration, secrets, tests, and documentation rather than
   preserving a complexity-heavy feature that is no longer justified.
8. Preserve target-DNS confinement: public MCP/helper target names are resolved
   only by the selected final hop, except in explicit documented direct mode.
9. Preserve full-mode RAG, local embedding, doc-drop source links/freshness,
   WebUI access, Teep inference, and optional Tailscale behavior.
10. Preserve optional executor isolation; the code-interpreter control service
   may manage pods, but spawned executors retain their separate restricted
   network or `none`.
11. Simplify service names, networks, ports, dependencies, and health checks
    made unnecessary by shared namespace removal.
12. Keep VPN failure and reconnection fail closed without restarting unrelated
    application services or creating an autoheal storm.
13. Make every routing exception exact, stack-owned, startup-validated, and
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
- Do not grant browser or executor bridges access to either Onyx listener or
  route broker, and never grant the public-only Onyx namespace or route broker
  host/internal exceptions.
- Do not allow arbitrary Docker-internal names, the Docker gateway, link-local
  metadata, or loopback merely because host/private MCP access is supported.
  RFC1918 access is a separate explicit opt-in described below and exists only
  on the host-capable route.
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
policy exception. Remove every consumer of obsolete enablement settings; stale
values are simply unused and receive no compatibility or validation handling.

## Target Network Model

### Trusted Routing Namespace

Retain `netns-holder` as the stable owner of the Myst namespace. It contains:

- `myst-client`;
- minimal public-only and host-capable route-broker processes that perform the
  authoritative final connection, DNS selection, address pinning, and repeated
  destination validation;
- exact routing/DNS readiness logic;
- VPN-only optional services that genuinely require namespace routing; and
- only the trusted-namespace endpoints of fixed route gateways.

It must not contain `api_server`, `background`, `web_server`, `nginx`,
code-interpreter control, local application helpers, or data services. Remove
the `api_server`, `background`, `web_server`, `nginx`, and `code-interpreter`
aliases currently assigned to `netns-holder`.

Give the routing namespace an explicitly named external-capable uplink network
rather than relying on accidental application use of Compose `default`.
Application and data services must not attach to that uplink.

The request-policy processes do **not** use
`network_mode: service:netns-holder`. Run the public-only and host-capable
processes in separate ordinary container network namespaces. Neither receives
an external-capable network, the other's route network, or a direct
host-gateway route. Each can reach only its caller bridge and its matching
fixed route broker in `netns-holder`.

The route brokers are deliberately smaller than the request-policy processes.
They accept one versioned stack-internal protocol from their one allowed policy
peer, repeat canonical host/address/port validation, and open the final
connection through Myst, the configured upstream proxy, or explicit no-VPN
mode. The public broker accepts only public destinations. The host broker adds
only the exact host and opt-in RFC1918 behavior below. They must not accept raw
HTTP proxy traffic from Onyx, browsers, executors, or a caller bridge. A
compromised public request-policy process can therefore request arbitrary
public egress but still cannot obtain a host/LAN route from its broker.

The configured upstream proxy endpoint is operator-selected routing
infrastructure, not a target-policy grant. In VPN mode, public proxy names use
provider DNS and public addresses follow the VPN route. Exact
`host.docker.internal` keeps its narrow route; an RFC1918 IPv4 literal receives
only an exact `/32` proxy-endpoint route; and named operator-local proxies use
system DNS only with `EGRESS_ALLOW_RFC1918=true`.

Attach `netns-holder` to two distinct internal broker networks, each with only
the matching isolated policy service as its other member. Each broker binds a
fixed port only on its matching namespace interface, authenticates a distinct
per-start stack-generated policy credential, and rejects traffic arriving on
the wrong interface or with the wrong protocol/version. Keep IPv4/IPv6
forwarding disabled so these attachments cannot become routed bridges.

The broker request carries only a canonical destination host, port, route
class, a required `cleartext` or `opaque` transport classification, and bounded
connection metadata; it cannot select an upstream proxy, resolver, source
address, or alternate broker. The broker rejects missing or unknown transport
classifications. After validation and pinned resolution, it applies the
plain-HTTP rule to `cleartext` requests and returns a bounded duplex byte stream
for that one connection. Specify framing, maximum field/frame sizes,
connect/idle/total deadlines, half-close and cancellation behavior,
concurrency limits, and credential-safe errors. Upstream proxy credentials and
provider-DNS settings remain broker-side. Malformed frames, peer loss, DNS
disagreement, unavailable routes, and partial upstream handshakes fail closed
without a direct retry.

Classification is based on explicit proxy request semantics, not on waiting
for a TLS handshake to fail. Absolute-form `http://` requests and CONNECT to
port 80 are `cleartext`; HTTPS forwarding and normal TLS CONNECT tunnels are
`opaque`. CONNECT on other ports remains an opaque byte stream because the
policy does not inspect its application protocol.

### Internal Onyx Networks

Use the smallest practical set of explicit `internal: true` networks. A
recommended shape is:

| Network | Members and purpose |
| --- | --- |
| `onyx-frontend` | nginx, web server, API, and optional frontend ingress gateway; only HTTP application traffic. |
| `onyx-backend` | API, background, code-interpreter control, trusted wrapper helpers, and caller sides of fixed request-path gateways. |
| `onyx-data` | API/background and only their required PostgreSQL/cache/index/object-store peers. |
| `onyx-public-egress` | API/background generic environment-aware clients; only peer is the fixed public-only bridge. |
| `onyx-host-egress` | API/background MCP, configured Web Connector and inference transports, local embedding shim, and explicitly approved narrow host-capable helpers; only peer is the fixed host-capable bridge. |
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
  internal `onyx-public-policy-upstream` shared only with the public
  request-policy process; and
- `onyx-host-egress-bridge` attaches to `onyx-host-egress` and a dedicated
  internal `onyx-host-policy-upstream` shared only with the host-capable
  request-policy process.

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
configured Web Connector/inference, embedding, and named narrow helpers attach
to `onyx-host-egress`. Network membership alone is not the request selector for
API/background, which need both classes: generic `HTTP_PROXY`/`HTTPS_PROXY`
point to the public bridge; MCP and configured Web Connector transports select
an explicit public/host bridge URL from the saved SSRF level; and configured-
inference plus approved host-helper transports use the explicit host URL.
Neither bridge may attach to browser, executor, data, frontend, or restricted
service control networks.

Use the same audited request-policy implementation for both classes, but run
separate processes in separate network namespaces with separate listener and
route-broker networks. The public namespace reaches only the public route
broker; the host namespace reaches only the host route broker. Both brokers
repeat destination validation, and neither broker protocol accepts a
client-selected alternate broker or upstream. A configuration or process
compromise in the public-only request-policy listener must therefore remain
unable to acquire the host-capable route or exception table.

Direct Obscura may later place the generic-Onyx, browser, and executor
public-only listeners in the same public request-policy process, but it must
preserve each listener's separate bridge and peer allowlist. It must keep the
host-capable listener in its own namespace with its distinct bridge peer and
route broker.

### Onyx Policy Listener Classes

Both Onyx request-policy listeners apply normal request/framing policy, and
their route brokers repeat all destination-sensitive checks before making the
authoritative final connection:

- HTTP request-framing validation;
- URL/port/plain-HTTP policy, including an explicit `cleartext` marker passed
  to the broker for authoritative final-hop enforcement;
- internal and Docker-name rejection;
- VPN provider DNS and address pinning in the route broker when VPN is enabled
  without an upstream proxy;
- upstream-proxy target DNS remains remote for HTTP, HTTPS, `socks5`, and
  `socks5h`;
- explicit system/Docker target DNS only for fixed stack identities,
  operator-local RFC1918 classification, or documented no-VPN direct mode; and
- credential-safe logging.

Only the host-capable request-policy namespace and route broker additionally
support a small stack-owned exact-destination table. At minimum, evaluate and
test:

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
the Myst route exemption for `host.docker.internal` without enabling the broad
RFC1918 option. Exact `host.docker.internal` is also permitted for plain HTTP
when `EGRESS_ALLOW_HTTP_URLS=false`; the host broker enforces that exception
after resolving and pinning the exact host identity.

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

Only the exact host-capable exceptions named above and the explicit RFC1918
mode below can precede that floor. The public-only namespace and broker have no
exception table and always apply the floor.
They do not create a suffix exception, so subdomains and newly introduced
Docker/Podman aliases remain denied by default.

### Opt-In RFC1918 Configured Endpoints

Rename the overly Myst-specific `MYST_VPN_ALLOW_LAN_BYPASS` option to the
stack-wide `EGRESS_ALLOW_RFC1918`, default `false`, with no compatibility alias.
It has two coordinated effects: Myst installs the documented RFC1918 route
exemptions when Myst is enabled, and the host-capable request-policy
namespace/broker permits RFC1918 destinations in every routing mode for MCP,
admin-configured Web Connector, embedding, and inference clients deliberately
routed to the host-capable bridge. The public-only namespace, browser,
executor, `open_url`, and generic helper path never receive this capability.
The opt-in includes plain HTTP to the permitted RFC1918 destinations even when
`EGRESS_ALLOW_HTTP_URLS=false`; it does not enable plain HTTP to public targets.

When disabled, RFC1918 literals and names resolving only to RFC1918 addresses
fail closed. When enabled, named RFC1918 targets must end in `.local`,
`.internal`, or `.home.arpa`; other private targets must use an IP literal.
Additionally:

- Onyx MCP still applies the saved Admin SSRF level on every initial and
  derived URL; strict levels reject private destinations before proxying.
- Admin-configured Web Connector fetches use an explicit saved-level-selected
  public/host HTTP and Playwright transport, applying the connector SSRF posture
  to every initial, discovered, and redirected URL. The exact stack-owned
  doc-drop connector uses one exact host-policy gateway and never receives a
  process-wide direct crawl context.
- The local embedding shim and each supported Onyx client for a user-configured
  inference base URL use an explicit host-capable proxy transport rather than
  generic environment routing. Default provider endpoints with no configured
  base URL may remain on the generic public path.
- The host route broker resolves only `.local`, `.internal`, and `.home.arpa`
  private-capable names with the trusted system resolver, requires every
  selected address to be RFC1918, pins the connection, and never sends it
  through Myst provider DNS or an external upstream proxy. For requests
  classified as `cleartext`, it applies the HTTP exemption only after this
  complete-answer validation. All-global and mixed answers remain ineligible
  for the RFC1918 cleartext exemption.
- Loopback, link-local, metadata, multicast, unspecified, Docker-internal names
  other than the exact host exception, and IPv4-mapped bypass forms remain
  blocked even when the switch is enabled.

The suffix restriction prevents arbitrary public target names from reaching
system/Docker DNS merely because the opt-in is enabled. A qualifying
all-RFC1918 answer may use the pinned private route; a mixed private/global
answer is rejected; and an all-global answer is discarded and follows the
normal selected public-target route. A compromised API/background process
that can use the host-capable bridge can still probe RFC1918 services by IP or
through these suffixes while the switch is enabled, so this remains a broad
operator-approved LAN capability rather than an endpoint-scoped sandbox
guarantee.

## MCP, OAuth, and Target DNS

### One Onyx SSRF Default, No Wrapper Flags

Remove `ONYX_SECURITY_SSRF_VALIDATE_OPEN_URL`,
`ONYX_SECURITY_SSRF_ALLOW_PRIVATE_NETWORK`, and
`ONYX_SECURITY_SSRF_ALLOW_LOOPBACK` from `.env.wrapper.example` and stop reading
them. They are three legacy inputs to one saved Onyx
`SSRFProtectionLevel`, not independent network-policy controls.

Seed Onyx to `ALLOW_PRIVATE_NETWORK` on a new installation, with open-URL
validation on and loopback off. Until upstream exposes a direct enum default,
set its three legacy container inputs internally to `true`, `true`, and `false`;
do not expose them as wrapper options. This preserves the current useful
default: admin-configured Web Connectors may use private targets, MCP/OAuth may
use exact host or RFC1918 targets, `open_url` remains private-target guarded,
and loopback remains blocked.

Do not condition this seed on `EGRESS_ALLOW_RFC1918`. Environment values only
seed Onyx when no saved Admin override exists, so conditional seeding would be
stateful and misleading after the Admin setting had been saved. Instead, keep
two explicit layers whose effective permission is their intersection:

- the saved Admin SSRF level selects application permission and whether MCP,
  OAuth, and Web Connector traffic uses the public-only or host-capable bridge;
- `EGRESS_ALLOW_RFC1918` is the network upper bound for RFC1918 routing at the
  host broker and Myst firewall; and
- exact `host.docker.internal` is a separate narrow host-broker exception,
  while loopback/link-local/metadata remain unavailable on every broker.

For MCP/OAuth, `VALIDATE_ALL` and `VALIDATE_LLM` select the public bridge;
`ALLOW_PRIVATE_NETWORK` and `DISABLED` select the host bridge. For Web
Connectors, only `VALIDATE_ALL` selects the public bridge; the other levels
select the host bridge, matching upstream connector semantics. RFC1918 still
fails unless `EGRESS_ALLOW_RFC1918=true`, and `DISABLED` still cannot create a
broker route to loopback/link-local/metadata. Read the saved level at
client/crawl creation. The selected egress policy and broker validate every
initial or SDK-derived destination; a second MCP-only fetch-time policy in
Onyx would be duplicate enforcement. A saved Admin override remains
authoritative and may tighten the default without changing Compose.

### Explicit MCP Transport

Patch the pinned Onyx MCP client narrowly and strictly. Introduce stack-owned
`ONYX_MCP_PUBLIC_HTTP_PROXY_URL` and `ONYX_MCP_HOST_HTTP_PROXY_URL` values
pointing to the two fixed bridges. They are not user-facing routing knobs.

The MCP factory must:

1. Validate both exact proxy URLs and reject credentials, paths, query,
   fragments, unexpected hosts, or ports at startup.
2. Instantiate a plain explicit `AsyncHTTPTransport` with the public or host
   proxy selected from the saved Admin level above; never select from the
   destination text alone.
3. construct `httpx.AsyncClient(..., trust_env=False, transport=...)` so
   environment settings cannot silently replace or bypass the selected route.
4. Preserve the existing timeout, headers, authentication, redirect, SSE, and
   streamable-HTTP behavior.
5. Let the selected policy/broker validate every request produced by the MCP
   SDK, including redirects, protected-resource discovery,
   authorization-server metadata, dynamic registration, token exchange, and
   refresh.
6. Fail startup in strict mode if the pinned HTTPX/MCP signatures or transport
   behavior no longer match.

Do not fall back to generic environment handling. The explicit transport is
required so the saved Admin level selects exactly one route and every URL the
MCP SDK creates crosses that route.

### DNS-Safe MCP Routing

The current fetch-time guard calls `socket.getaddrinfo()` before sending the
request. That would fail or leak public target names through Docker's embedded
resolver after network isolation.

Split validation responsibilities without duplicating destination policy:

- Onyx retains its upstream configuration/store-time checks and uses the saved
  Admin SSRF level only to choose the public or host transport at client
  creation. The wrapper adds no second per-request MCP URL validator.
- The selected policy and matching route broker validate every
  CONNECT/forward request, including SDK-derived destinations. The broker
  resolves public names using the selected VPN/upstream/no-VPN mode.
- Exact `host.docker.internal` destinations are resolved only by their
  authorized host-broker exception.
- When `EGRESS_ALLOW_RFC1918=true`, the host-capable path follows the
  explicit IP-literal or `.local`/`.internal`/`.home.arpa` resolution and
  pinning rules above; the public path remains unchanged.

At strict SSRF levels, the public route rejects `host.docker.internal`. At
`ALLOW_PRIVATE_NETWORK` or `DISABLED`, the host route can admit that exact
non-loopback identity but retains the broker's loopback, link-local, and
metadata floor. Redirects and other derived requests cannot bypass the
selected route.

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

### Configured Web Connector Upstreams

Route every admin-configured Web Connector crawl other than the exact
stack-owned doc-drop connector through an explicit proxy transport. Select the
public or host bridge from the saved SSRF level as described above; never use a
direct socket, environment-only proxy selection, or `NO_PROXY` for a
user-configured target.

Patch both the connector's HTTP session and Playwright launcher. Disable
environment discovery, preserve authentication/headers/timeouts/redirect and
rendering behavior, and apply URL syntax plus saved-level validation to the
initial URL, discovered crawl URLs, redirects, sitemap entries, and browser
navigations. Public connector targets work through either selected broker's
normal public route. RFC1918 targets require both an Admin level that permits
connector-private access and `EGRESS_ALLOW_RFC1918=true`; named targets must
use `.local`, `.internal`, or `.home.arpa`. Loopback, link-local/metadata, and
unapproved Docker names remain blocked.

The exact configured local doc-drop connector keeps the fixed internal
`http://doc-drop-web:8091/` service identity, with the narrow identity/display-
link patch below. The implementation sends it through the host bridge and an
exact fixed broker gateway rather than granting a direct crawl context. Do not
generalize that internal destination to other saved Web Connector URLs.

### Configured Onyx Inference Upstreams

Preserve RFC1918 inference endpoints configured through Onyx provider
`api_base` or equivalent fields by adding one stack-owned
`ONYX_CONFIGURED_INFERENCE_HTTP_PROXY_URL` that points to the same fixed
host-capable bridge used by private-capable MCP and embedding. It is not a
user-selectable proxy.
Apply it only to requests derived from a user-configured inference base URL;
provider-default public endpoints continue to use the public bridge.

Inventory every synchronous and asynchronous path that consumes a configured
LLM, embedding, reranking, image-generation, speech, or provider-discovery base
URL at the pinned Onyx/LiteLLM versions. Supply an explicit proxy-capable client or
transport with environment discovery disabled, preserving streaming, TLS
verification/SNI, authentication, timeouts, retries, and connection pooling.
If a pinned SDK cannot accept a per-client proxy without changing global
traffic, add a narrow startup-validated factory patch and block cutover for
that configured-provider path until it is covered; do not use `NO_PROXY`, a
direct socket, or a process-global proxy switch that silently moves default
providers onto the host-capable path.

Validate the configured endpoint URL before client construction and on every
SDK-derived redirect or discovery URL where the SDK permits them. The route
broker remains authoritative for DNS/address selection: RFC1918 succeeds only
with `EGRESS_ALLOW_RFC1918=true` and either an IP literal or a supported
operator-local suffix, while public configured endpoints use the normal public
final route through the host broker. Startup/source-shape checks must fail
loudly if an SDK upgrade stops using the injected client.

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

Move `doc-drop-web` to a dedicated internal route network, publish its
diagnostic/source port through a fixed hardened publisher on the configured
host bind, and configure the Web connector to crawl
`http://doc-drop-web:8091/`. Send that exact origin through the host policy and
a fixed gateway into the route network; do not grant the background process a
direct doc-drop network path.

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

Move `local-embedding-shim` to an internal network and patch its owned
`http.client` connection pool to use an explicit, strictly validated
host-capable Onyx proxy URL. The current client is not environment-proxy aware,
so this is a required implementation change rather than an optional fallback. The
default `host.docker.internal` upstream must use the exact host exception.
RFC1918 upstreams require `EGRESS_ALLOW_RFC1918=true` and either an IP literal
or `.local`/`.internal`/`.home.arpa`; public embedding upstreams use normal
final-hop policy. API/background reach only `local-embedding-shim:9101`, never
its upstream directly.

Both the exact host default and opt-in RFC1918 embedding upstreams may use
plain HTTP when general cleartext URLs are disabled. The shim sends
absolute-form HTTP through the host bridge, which marks the broker request as
`cleartext`; the broker resolves, validates, and pins the target before opening
the upstream connection. Public HTTP embedding endpoints still require
`EGRESS_ALLOW_HTTP_URLS=true`.

Implement HTTP absolute-form forwarding and HTTPS `CONNECT` with ordinary TLS
certificate verification and target SNI after the tunnel is established.
Preserve pooling and one bounded reconnect, keep proxy and upstream credentials
out of logs, and ensure public target DNS is not resolved by the shim. Validate
HTTP and HTTPS upstreams, `/ready`, query/passage prefixes, model conversion,
API-key redaction, indexing, and `internal_search`.

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

Teep remains a separate inference component. To minimize additional services
without making it a pivot into `onyx-backend`, use a dedicated internal
`onyx-teep` network containing only Teep and the exact Onyx callers that need
it when Teep uses its normal external-capable namespace. Teep may be dual-homed
between that narrow caller network and its selected external uplink; it must
not attach to frontend, backend, data, public-egress, or host-egress networks.

When Teep is configured to route through Myst, retain its namespace placement
and expose a fixed single-destination Teep gateway on `onyx-teep`; do not attach
`netns-holder` itself to the Onyx caller network and do not move the Onyx
application tier back into the namespace. This adds a gateway only in the mode
that cannot safely use the direct narrow network. If host Teep publication is
retained, `host-teep-proxy` must be a hardened fixed-destination publisher:
numeric nonroot, read-only, all capabilities dropped, `no-new-privileges`, and
forwarding disabled. Any later removal remains coordinated with the Direct
Obscura service-reduction work.

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
    artifacts, including every consumer of its obsolete enablement variables.
13. Remove readiness checks and restart edges that exist only to coordinate
    unrelated services sharing one namespace.

The migration adds two Onyx egress bridges, two isolated request-policy
services, and two minimal route brokers, and may add a conditional Tailscale
frontend gateway. These replace the current loopback policy arrangement rather
than supplementing it indefinitely. Count reductions and additions from the
effective lite/full Compose models, including optional executor, Tailscale, and
Teep layers, rather than claiming an unconditional net service count.

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
    -> both trusted route-broker readiness checks
    -> matching isolated Onyx request-policy readiness checks
    -> matching fixed Onyx egress bridge end-to-end readiness
    -> API/background/helper readiness
```

Route-broker readiness must retain the target-free checks from the
restricted-egress implementation: provider-DNS validation in direct VPN/no-VPN
mode or upstream-proxy endpoint bootstrap plus protocol/authentication
handshake. Request-policy readiness must verify its fixed broker protocol and
the broker's expected denial without opening an arbitrary public target. The
bridge health check must traverse the bridge, request policy, and broker and
receive the expected denial for a blocked local target.

Application services no longer depend directly on `myst-client` merely because
they share its namespace. Egress-capable services depend on the complete
bridge/request-policy/route-broker chain;
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
Myst leaves `netns-holder` stable, the route brokers and policies recover in place, and isolated
Onyx services remain running. This prerequisite makes explicit no-VPN models
omit autoheal; the Direct Obscura migration must preserve that layer boundary.
Do not autoheal application, request-policy, route-broker, bridge, or gateway
containers in response to a tunnel flap.

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
- the two request-policy namespaces and their fixed route-broker links preserve
  the public-versus-host boundary without placing either request-policy process
  in `netns-holder`;
- the route broker, not application DNS, is authoritative for public target
  resolution and private-address rejection;
- exact host exceptions intentionally expose approved host services to trusted
  Onyx callers when Admin SSRF policy permits them; and
- browser and executor peers cannot reach either Onyx listener.

Residual risks to document:

- a compromise or generic-relay misconfiguration in either Onyx bridge could
  pivot between its two attached networks;
- a compromise of either minimal route broker has trusted namespace access;
- a compromise of the public request-policy process can obtain arbitrary
  policy-permitted public egress but remains behind the public broker's
  private/host denial;
- the code-interpreter control service's Docker socket remains a host-level
  capability independent of network isolation;
- an upstream proxy may resolve a public-looking name privately unless it
  enforces equivalent destination policy;
- permitting arbitrary ports on exact `host.docker.internal` exposes whatever
  host services listen on those ports to an SSRF-permitted Onyx MCP/helper
  request; and
- enabling `EGRESS_ALLOW_RFC1918` intentionally gives host-capable Onyx
  callers broad RFC1918 reach by IP and through the three operator-local
  suffixes;
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
6. Inventory every bundled Obscura MCP artifact that must be deleted.

### Workstream 1: MCP and Policy Correctness

1. Add the strict saved-level-selected MCP proxy transport and the explicit
   configured Web Connector transports.
2. Keep route selection in Onyx and authoritative request validation/DNS at
   the selected final hop, without a duplicate MCP fetch-time validator.
3. Add separate public and host request-policy namespaces plus their distinct
   public-only and host-capable route brokers in `netns-holder`.
4. Add exact host and opt-in RFC1918 handling only to the host-capable
   namespace/broker, and prove the public broker rejects the same target.
5. Define the bounded authenticated policy-to-broker protocol and generate a
   distinct ephemeral credential for each broker on every stack start. Require
   its versioned `cleartext`/`opaque` classification and enforce the final-hop
   HTTP decision at the broker after destination validation.
6. Remove the three wrapper SSRF seed flags, set the fixed
   `ALLOW_PRIVATE_NETWORK` default, and preserve saved Admin SSRF semantics on
   initial and derived URLs through public/host bridge selection.
7. Add Linux host-gateway, RFC1918 opt-in, and Myst route-exemption validation.
8. Add focused unit tests before changing network placement.

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
2. Add the public-only and host-capable component, policy, and route-broker
   networks without attaching either request-policy process to `netns-holder`.
3. Expose each request-policy listener only to its expected bridge peer and
   each broker only to its expected policy peer; prove the public broker has no
   host or RFC1918 exception capability.
4. Retarget generic helper/Playwright proxy variables to the public bridge;
   give MCP and configured Web Connector clients both saved-level-selected
   bridge URLs; and give configured-inference/embedding/narrow host helpers
   explicit host-bridge URLs.
5. Update stack-owned `NO_PROXY` for internal service DNS only.
6. Prove packet forwarding, cross-broker access, and client-selected alternate
   upstreams are impossible.
7. Remove the old loopback-only helper client assumption.

### Workstream 4: Ingress and Local Services

1. Publish nginx directly on the configured host bind and remove
   `host-web-proxy`.
2. Move doc-drop internally, add the strict display-link-only rewrite, reindex
   affected connectors, and remove `host-doc-drop-web-proxy`.
3. Add the conditional fixed Tailscale frontend gateway and its narrow caller
   network; keep Tailscale itself off `onyx-frontend`.
4. Add explicit HTTP/HTTPS proxy support to the local embedding shim and route
   its upstream through the host-capable Onyx policy.
5. Add and validate the explicit configured-inference transport for every
   supported pinned SDK path without changing provider-default routing.
6. Put ordinary Teep on the dedicated narrow `onyx-teep` network; add a fixed
   Teep gateway only in the Myst-routed mode.
7. Retarget internal model-service URLs without changing inference trust
   semantics.

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
- Remove the three legacy wrapper SSRF flags; document the fixed
  `ALLOW_PRIVATE_NETWORK` seed, saved Admin override, and network-policy ceiling.
- Explain that `EGRESS_ALLOW_RFC1918` gates RFC1918 MCP, configured Web
  Connector, embedding, and inference targets; named targets must use
  `.local`, `.internal`, or `.home.arpa`, while provider-default inference,
  `open_url`, and all browser/executor traffic remain public-only.
- Update host endpoints, local RAG URLs, Tailscale behavior, troubleshooting,
  and service counts.
- Remove shared-namespace and port-7000 instructions.

### `docs/vpn_routing_and_proxies.md`

- Restrict the trusted namespace inventory to Myst, minimal route brokers, and
  genuine VPN-side services; document that request-policy processes use
  separate namespaces.
- Add both Onyx bridges, isolated policy namespaces, route-broker networks,
  their distinct peer sets, the host/RFC1918 exception table, and the DNS
  matrix.
- Explain explicit MCP transport handling and why environment variables alone
  were insufficient.
- Document saved-level public/host selection for MCP/OAuth and configured Web
  Connectors, plus the fixed internal doc-drop exception.
- Update readiness, autoheal, upstream proxy, Teep, Tailscale, and no-VPN
  diagrams.

### `docs/internal_network_security.md`

- Make Onyx application networks part of the enforced boundary rather than a
  trusted shared-namespace exception.
- Document permitted peers per network, both Onyx bridges and policy
  namespaces, both route brokers, the exact host/RFC1918 exceptions,
  Docker-internal denial, and residual pivot risk.
- Explain the intersection of saved SSRF level, public/host bridge selection,
  and `EGRESS_ALLOW_RFC1918`, including the always-blocked loopback floor.
- Update reachability tables and negative-path evidence.

### `docs/request_handling.md`

- Update Onyx helper, MCP/OAuth, Web connector, local Chromium, and gateway
  request chains.
- Add configured-inference versus provider-default proxy selection, including
  RFC1918 opt-in, mixed-answer rejection, and fail-closed SDK coverage.
- Replace the three legacy SSRF seed flags with the fixed
  `ALLOW_PRIVATE_NETWORK` default and explain that the saved Admin level and
  `EGRESS_ALLOW_RFC1918` form independent, intersecting controls.
- Distinguish internal service DNS from public target DNS.
- Preserve current CRW/Obscura behavior until Direct Obscura is implemented.
- Remove the bundled Obscura MCP request chain and all setup, endpoint,
  troubleshooting, routing, and security claims for that removed service.

### `docs/onyx_patch_info.md` and `docs/onyx_patches_upgrade.md`

- Document the strict MCP and Web Connector proxy/DNS patches, saved-level
  bridge selection, and revised Playwright proxy URL validation.
- Document fixed SSRF seeding and removal/rejection of the three wrapper flags.
- Document the configured-inference proxy selection patch, covered provider
  clients, and strict source-shape validation.
- Add source-shape anchors for HTTPX transport construction, MCP SDK clients,
  redirects, OAuth, and SSRF settings.
- Add upgrade tests that fail if environment proxy behavior or SDK factory
  signatures change.
- Update code-interpreter, port, and internal service URL assumptions.

### `docs/local_docs_rag_search.md`

- Document the internal doc-drop crawl URL, display-only link rewrite, exact
  host-policy gateway, direct host display publication, and mandatory
  connector recreation/reindex migration.
- Update embedding-shim service URL, explicit proxy implementation, host/LAN
  upstream routing, readiness, and failure diagnostics.
- Revalidate source-link clickability, freshness, PDF reindexing, and
  `internal_search`.

### `.env.wrapper.example`, `AGENTS.md`, and Plans

- Rename `ONYX_AGENT_OUTBOUND_PROXY_URL` to `EGRESS_UPSTREAM_PROXY_URL` and
  `ONYX_AGENT_ALLOW_HTTP_URLS` to `EGRESS_ALLOW_HTTP_URLS`, with no
  compatibility aliases. Update Make/Compose selection, policy/broker inputs,
  executor injection, tests, help, examples, and current documentation
  atomically. Remove all consumers of the old names; stale values are ignored.
- Rename `MYST_VPN_ALLOW_LAN_BYPASS` to `EGRESS_ALLOW_RFC1918` as the single
  explicit RFC1918 opt-in, with no consumer or validation for the old name. Its
  example text must cover
  MCP, configured Web Connector, embedding, and inference endpoints; it is not
  required for an exact `host.docker.internal` upstream proxy or other exact
  host exception. Explain that both exact host and opt-in, broker-validated
  RFC1918 destinations may use plain HTTP when general cleartext URLs are
  disabled, without enabling public HTTP.
- Remove all three `ONYX_SECURITY_SSRF_*` options. Internally seed Onyx with
  `OPEN_URL_VALIDATE_SSRF=true`, `MCP_SERVER_ALLOW_PRIVATE_NETWORK=true`, and
  `MCP_SERVER_ALLOW_LOOPBACK=false`; do not add a replacement wrapper option.
- Do not make stack-owned bridge/broker URLs, credentials, listener ports, or
  `NO_PROXY` values user-facing.
- Generate the two distinct broker authentication credentials through the
  existing ephemeral stack-secret flow on every start; do not add them to
  `.env.wrapper.example` or persist them in application data.
- Preserve the saved Admin SSRF control and the remaining host-port, VPN, Teep,
  Tailscale, code-interpreter, model-behavior, and local-RAG wrapper settings;
  update descriptions only where routing or publisher ownership changed.
- Replace the SSRF seed comments with the fixed default, saved Admin override,
  public/host bridge selection, exact host/RFC1918 policy, and fixed internal
  doc-drop exception. Remove the obsolete bundled-Obscura-MCP rationale.
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

- `EGRESS_UPSTREAM_PROXY_URL`/`EGRESS_ALLOW_HTTP_URLS` propagation;
- `EGRESS_ALLOW_RFC1918` propagation;
- fixed `ALLOW_PRIVATE_NETWORK` seeding only when no Admin override is saved;
- exact public/host MCP and Web Connector proxy URL validation and invalid
  values;
- HTTPX custom transport construction with the explicit proxy and
  `trust_env=False`;
- streamable HTTP and SSE factory use;
- policy/broker validation of the initial URL, redirects, OAuth metadata,
  registration, authorization, token, and refresh requests;
- saved-level bridge selection for MCP/OAuth and Web Connectors at every SSRF
  level, including derived URLs, sitemaps, browser fallback, and a saved-level
  change between new clients/crawls;
- no public target DNS call in Onyx when the final hop is authoritative;
- SSRF levels for public, RFC1918, loopback, link-local, exact
  `host.docker.internal`, Docker/Podman/legacy aliases and
  suffixes, single-label services, trailing dots, IDNA, and IP literals;
- exact policy exception matching, system resolver selection, address pinning,
  and no external-upstream use for host targets;
- broker protocol version/authentication/framing limits, wrong-interface and
  cross-credential denial, deadlines/cancellation/half-close, concurrency, and
  no caller-selected resolver/upstream/source address;
- required broker `cleartext`/`opaque` classification, with missing, unknown,
  or malformed classifications rejected before a destination connection;
- `EGRESS_ALLOW_RFC1918` disabled/enabled behavior, RFC1918-only answer
  validation for `.local`/`.internal`/`.home.arpa`, arbitrary-name non-use of
  system DNS, mixed-answer rejection, all-global public-route handoff, and the
  unchanged public-only denial;
- plain HTTP exact-host success without the RFC1918 option; plain HTTP
  RFC1918-literal or supported-suffix all-RFC1918 success only on the host
  route with `EGRESS_ALLOW_RFC1918=true`; and public or mixed-answer cleartext
  denial when general HTTP is disabled;
- normal VPN/upstream/no-VPN public resolver selection;
- HTTP framing and credential redaction regression;
- generic-helper failure when the public bridge/policy is unavailable, MCP
  configured-inference, and embedding failure when the host bridge/policy is
  unavailable, and no cross-class fallback;
- local embedding shim HTTP absolute-form and HTTPS CONNECT proxying, TLS SNI,
  connection reuse/retry, no shim-side public DNS, and credential redaction;
- Web Connector HTTP/Playwright explicit proxying, public and RFC1918 targets,
  loopback/link-local/metadata denial, and the exact doc-drop host gateway;
- configured-inference URL selection for every supported synchronous and
  asynchronous SDK path, explicit client injection, provider-default public
  routing, RFC1918 opt-in, mixed-answer rejection, redirect/discovery handling,
  and strict failure when an SDK bypasses the injected transport;
- internal doc-drop identity/display-link rewriting, path normalization,
  encoding, and strict source-shape failure;
- absence of every removed bundled Obscura MCP route; and
- stack-owned `NO_PROXY` content and executor non-propagation.

Use fake DNS, proxy, and HTTP transports. Unit tests must not require Internet,
VPN credentials, the Docker host, or `.env.wrapper`.

### Compose and Static Security Tests

Parse effective models structurally and assert:

- no Onyx application service uses `network_mode: service:netns-holder`;
- only Myst, the minimal route brokers, and explicitly justified VPN-side
  services share `netns-holder`; request-policy processes use separate
  namespaces and cannot reach the other broker network;
- every Onyx application/data network is `internal: true`;
- no Onyx application service attaches to the routing uplink, policy upstream,
  route-broker, browser, executor, or restricted service control networks;
- each Onyx bridge has exactly its two networks and hardening;
- each broker network contains only `netns-holder` and its matching policy;
  each broker binds only its matching interface, uses a distinct generated
  credential, and namespace IP forwarding remains disabled;
- browser and executor bridges are separate and cannot reach either Onyx
  listener or policy-side network;
- the public-only listener and route broker cannot route any host/private
  destination and have no host/RFC1918-exception configuration;
- caller-side request gateways attach to `onyx-backend` and exactly one
  restricted service network, expose one fixed destination, and cannot relay
  to another gateway's service;
- `host.docker.internal` is absent from generic helper `NO_PROXY`;
- `.env.wrapper.example` omits all three legacy SSRF flags and Compose supplies
  the fixed internal seed values;
- code-interpreter control uses service port `8000`, while executor networking
  remains separately selected and restricted;
- host WebUI/doc ports bind only as configured; obsolete broad publishers are
  absent while the Docker Desktop implementation note's hardened fixed
  publishers remain;
- Tailscale/frontend gateways exist only in enabled layers; Tailscale never
  attaches directly to `onyx-frontend`, and the fixed HTTP gateway has no
  egress/helper/data network;
- ordinary Teep attaches only to `onyx-teep` plus its selected uplink, while
  Myst-routed Teep uses a fixed gateway and never exposes `netns-holder` to the
  Onyx caller network;
- direct Myst dependencies are absent from pure application services;
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
| lite, upstream SOCKS5/SOCKS5h | target names remain at the upstream proxy and no direct fallback occurs |
| full, VPN/no VPN | all lite checks plus doc-drop crawl, source links, PDF freshness, embeddings, indexing, and `internal_search` |
| MCP strict SSRF | host MCP rejected; remote public MCP succeeds through policy |
| MCP allow-private, RFC1918 off | exact host MCP, including plain HTTP, succeeds; RFC1918, loopback, link-local, and other Docker names remain denied |
| MCP allow-private, RFC1918 on | exact host, RFC1918 literals, and all-RFC1918 `.local`/`.internal`/`.home.arpa` MCP, including plain HTTP, succeed through the host route; public HTTP still requires `EGRESS_ALLOW_HTTP_URLS=true`; mixed answers and loopback/link-local/other Docker names remain denied |
| Web Connector, strict/allow-private | strict connector traffic uses the public bridge; permitted RFC1918 literals and `.local`/`.internal`/`.home.arpa` names use the host bridge and succeed only with `EGRESS_ALLOW_RFC1918`; configured doc-drop remains internal |
| supported configured chat inference, RFC1918 off/on | configured public endpoints use the host bridge and normal public final route; RFC1918 literals and supported operator-local names fail when off and use the pinned private route when on; public HTTP requires the general HTTP option; provider-default endpoints remain on the public bridge |
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
- Verify local model, configured
  LLM/embedding/reranking/image/speech/provider-discovery endpoints,
  provider-default LLM endpoints, GitHub repository download, Web connector,
  Highspot, OAuth refresh, and remaining helper clients use their intended
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
5. Move or explicitly classify doc-drop and embedding helpers, and enable the
   saved-level Web Connector plus configured-inference transports.
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
  stays local, never traverses the external upstream proxy, and may use plain
  HTTP when general cleartext URLs are disabled.
- New installs seed Onyx `ALLOW_PRIVATE_NETWORK` with loopback disabled; saved
  Admin overrides still select public/host routing, and no legacy wrapper SSRF
  flag remains consumed.
- RFC1918 MCP, configured Web Connector, embedding, and inference endpoints
  work only when `EGRESS_ALLOW_RFC1918=true`, use IP literals or
  `.local`/`.internal`/`.home.arpa` names, traverse only the host-capable
  namespace and broker, and never become reachable from `open_url`, generic
  public, browser, or executor paths. Their plain-HTTP forms receive the same
  exception only after broker validation; public or mixed answers remain
  denied when general HTTP is disabled.
- Every policy-to-broker request carries a valid `cleartext` or `opaque`
  classification. Both brokers reject missing or invalid classifications, and
  the host broker makes the final cleartext exception decision only after
  authoritative destination validation.
- The bundled Obscura MCP service and all of its configuration, secrets,
  routes, networks, tests, and documentation are absent.
- Loopback, link-local metadata, other Docker-internal names, private IPs at
  strict levels, and redirect bypasses remain denied.
- Browser and executor bridges are separate and cannot reach either Onyx
  listener or route broker, the host/RFC1918 exceptions, application networks,
  or data networks.
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
| generic Onyx public HTTP(S) route | fixed public-only Onyx bridge, isolated request-policy namespace, and public-only route broker |
| remote MCP/OAuth transport | saved-level-selected explicit HTTPX proxy transport; selected egress policy/broker is authoritative |
| admin-configured Web Connector transport | saved-level-selected explicit HTTP/Playwright proxy; exact doc-drop connector stays internal |
| public target DNS and address validation | selected route broker in the trusted routing namespace |
| exact host and opt-in RFC1918 MCP/Web Connector/embedding/inference route | fixed host-capable Onyx bridge, isolated host policy namespace, and host route broker; plain HTTP is allowed for these destinations only after broker validation |
| browser and executor public routes | separate bridges owned by Direct Obscura |
| VPN readiness and recovery | Myst plus target-free policy readiness and Myst-only autoheal |
| internal service bypasses | stack-owned `NO_PROXY` plus internal networks, never host/public names |
| direct-socket denial | absence of an external/default route from application containers |

The core result is not merely that supported clients use a proxy. It is that
unsupported or compromised clients cannot obtain another route.
