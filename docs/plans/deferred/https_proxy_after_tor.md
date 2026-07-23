# HTTPS Proxy After Tor Plan

> **Status: deferred.** This is a potential enhancement, not current behavior
> or an implementation commitment. Native Tor egress and
> `EGRESS_UPSTREAM_PROXY_URL` remain mutually exclusive until this plan is
> deliberately implemented, validated, documented, and moved to
> `docs/plans/implemented/`.
>
> It is not clear that this feature would provide a practical privacy benefit.
> No suitable zero-data-retention (ZDR) HTTPS proxy service is currently known
> to this project. Adding a persistent proxy account after Tor can replace
> destination-visible Tor exits with a single party capable of correlating
> requests, so implementation should remain deferred until an operator has a
> concrete provider and threat model for which that trade is beneficial.

## Goal

Optionally compose the existing wrapper-owned native Tor egress with one
operator-configured HTTPS forward proxy:

```text
restricted application
  -> fixed final-hop policy proxy
  -> private Tor Unix SOCKS socket
  -> Tor circuit and exit
  -> verified HTTPS connection to configured proxy
  -> requested public target
```

When both `TOR_EGRESS_ENABLED=true` and a nonempty
`EGRESS_UPSTREAM_PROXY_URL=https://...` are configured, Tor should connect to
the HTTPS proxy and the HTTPS proxy should become the final public egress
identity. Tor should resolve and reach the proxy endpoint; the HTTPS proxy
should resolve and reach requested target names.

This feature must preserve the current destination policy, restricted bridge
topology, local-route exceptions, credential redaction, TLS verification, and
fail-closed behavior. Applications must not receive the Tor socket, upstream
proxy credentials, or any new network attachment.

Use this plan with the normative
[Native Tor support](../../native_tor_support.md),
[VPN routing and restricted egress](../../vpn_routing_and_proxies.md), and
[Internal network security](../../internal_network_security.md) documents.
Those documents describe current behavior until this plan is implemented.

## Decision Gate

Do not implement this enhancement merely because the transport composition is
possible. Before scheduling it:

1. Identify a concrete HTTPS proxy service or operator-managed deployment.
2. Document what “zero data retention” means for that service, including
   connection metadata, destination metadata, authentication records, DNS
   queries, abuse logs, billing records, and compelled logging.
3. Determine whether the claim is contractual, independently audited, or only
   marketing language.
4. Compare the provider's jurisdiction, account/payment linkage, and stable
   credentials with the operator's actual threat model.
5. Explain why a stable proxy identity after Tor is preferable to allowing
   destinations to observe changing Tor exits.
6. Confirm that the provider supports verified HTTPS proxy transport, the
   required authentication method, expected request volume, and both HTTP
   forwarding and `CONNECT`.

If no trustworthy provider or concrete use case is identified, leave the
current mutual-exclusion rule in place. A self-hosted proxy may be useful for
fixed egress allowlisting, but it does not by itself provide ZDR or anonymity
from its host and operator.

## Proposed Configuration Semantics

Do not add a second user-facing proxy URL. Give the existing combination one
explicit meaning:

| Tor egress | Upstream URL | Behavior |
| --- | --- | --- |
| off | empty | Existing Myst or explicit no-VPN direct final hop |
| off | `http://`, `https://`, `socks5://`, or `socks5h://` | Existing direct-to-proxy behavior |
| on | empty | Existing native Tor-to-target behavior |
| on | `https://` public proxy | Proposed Tor-to-HTTPS-proxy chain |
| on | `http://`, `socks5://`, or `socks5h://` | Reject during host preflight |
| on | host, LAN, stack-local, metadata, reserved, or `.onion` proxy endpoint | Reject during host preflight or endpoint validation |

`TOR_ONION_SERVICE_ENABLED` remains independent. Enabling onion ingress must
not route ingress through the HTTPS proxy or give the onion gateway access to
proxy configuration.

The chain continues to bypass Tor and the upstream proxy for the existing
exact internal destination, exact `host.docker.internal`, and explicitly
validated LAN integration exceptions. This enhancement changes only the
existing public/configured-external final-hop path.

`MYST_VPN_ENABLED` must not affect the chained public path. Tor retains its
direct `tor-uplink`; neither the Tor-to-proxy connection nor proxy-endpoint DNS
may traverse Myst. Existing non-Tor upstream-proxy behavior through Myst
remains unchanged.

## Privacy and Security Properties

### Information placement

The intended observation boundaries are:

| Party | Information available |
| --- | --- |
| Local application | Existing local policy-proxy address only |
| Final-hop policy proxy | Requested target and configured proxy, as it does today |
| Tor guard | Operator source address and Tor use, but not proxy or target |
| Tor exit | HTTPS proxy address and encrypted TLS traffic to it |
| HTTPS proxy | Stable account identity if authenticated, target names/addresses, timing, and traffic volume |
| Target | HTTPS proxy egress identity rather than a Tor exit |

The HTTPS requirement is essential. Plain HTTP and SOCKS proxy protocols would
expose proxy authentication and target metadata to the Tor exit after Tor
encryption terminates. They are intentionally out of scope for the combined
mode even though the wrapper continues to support them when Tor egress is off.

Origin TLS remains end-to-end between the local policy proxy/client flow and
the requested HTTPS origin through the proxy tunnel. Proxy TLS is a separate
outer TLS session between the final-hop policy proxy and the configured HTTPS
proxy.

### DNS ownership

The chain has two distinct remote resolutions:

1. The proxy endpoint hostname is sent in domain form through the private Tor
   SOCKS connection. Tor owns its resolution and connection.
2. The requested target hostname is sent through HTTPS proxy forwarding or
   `CONNECT`. The configured proxy owns target resolution.

The wrapper therefore cannot inspect the resolved address set for either
public hostname. It must still reject malformed names, forbidden literal
addresses, loopback/internal naming patterns, Docker host aliases, metadata
names, single-label service names, operator-local suffixes, and `.onion`
endpoints before opening the chain. Documentation must retain the existing
warning that a remote-DNS proxy can resolve a public-looking target to a
private address outside the wrapper's observation.

Do not introduce local DNS as a validation step for either public hostname;
that would leak names and would not pin the addresses actually used by Tor or
the remote proxy.

### Failure behavior

Any of the following must fail the request without attempting a direct, Myst,
or non-Tor proxy connection:

- missing or unhealthy Tor socket;
- Tor bootstrap, circuit, resolution, or SOCKS failure;
- proxy TLS verification, hostname, TLS-version, or handshake failure;
- proxy authentication rejection;
- malformed, oversized, incomplete, or timed-out proxy response;
- non-successful `CONNECT`;
- unsupported runtime behavior for nested TLS;
- target connection or origin TLS failure.

Logs must identify the failing layer without printing proxy credentials,
complete request URLs, cookies, control material, onion keys, or Tor circuit
details.

## Implementation Design

### Host validation and Compose selection

Update `tor/render_config.py` and its tests so the current blanket conflict
becomes the HTTPS-only matrix above. Validation should parse the URL using the
same strict proxy grammar as runtime validation, or move the grammar into a
small shared module rather than allowing the host and container rules to
drift. Error messages should state the invalid combination and the accepted
`https://` form without echoing credentials.

Keep `wrapper-config-preflight` as the first `make up-lite` and `make up-full`
prerequisite so invalid combinations fail before shared-data or Compose
mutation.

The existing Tor and proxy Compose layers may both be selected. Confirm the
effective full/lite Docker and Podman models rather than assuming layer merge
order. In chained mode:

- only the two final-hop policy containers receive both the read-only Tor
  runtime volume and `EGRESS_UPSTREAM_PROXY_URL`;
- Tor receives neither the URL nor proxy credentials;
- applications and gateways receive neither;
- `myst-client` must not resolve, classify, or install a route for the proxy
  endpoint.

Prefer an explicit Tor-egress overlay override that leaves
`EGRESS_UPSTREAM_PROXY_URL` empty for `myst-client` while retaining it in the
two policy containers. Do not weaken or bypass the existing Myst route logic
globally, because non-Tor proxy modes still depend on it.

### Layered transport

Refactor `egress/final_hop_proxy.py` around composable bounded stream
operations:

1. Open the fixed `/run/tor-egress/socks` Unix socket.
2. Run the existing SOCKS5 state machine with the HTTPS proxy hostname and
   port as the destination.
3. Upgrade that stream with the existing verified HTTPS-proxy TLS context and
   explicit proxy hostname SNI.
4. For an HTTPS target or inbound `CONNECT`, send the existing bounded
   authenticated proxy `CONNECT` request over the TLS-protected proxy stream.
5. For an explicitly permitted plaintext HTTP request, forward absolute-form
   HTTP over the TLS-protected proxy stream and retain strict request framing.
6. For wrapper-originated HTTPS forwarding, establish origin TLS inside the
   proxy tunnel without terminating or weakening the outer proxy TLS layer.

Extract helpers with transport-specific names rather than adding conditionals
throughout request parsing. A representative shape is:

```text
open_native_tor_stream(proxy_host, proxy_port)
  -> TLS upgrade for verified HTTPS proxy
  -> HTTP proxy CONNECT or absolute-form forwarding
  -> optional inner origin TLS
```

Continue to use one timeout budget per bounded handshake/read operation.
Close the writer on every partial failure. Do not add retries, alternate
addresses resolved outside Tor, or a direct fallback.

The current Python/OpenSSL runtime must be tested for TLS-over-Tor followed by
origin TLS through an HTTPS proxy tunnel. In particular, confirm that the
runtime supports the required TLS-in-TLS stream behavior for
wrapper-originated HTTPS forwarding. If it does not, stop implementation and
choose an explicit, reviewed transport abstraction; do not disable proxy TLS,
origin verification, or TLS 1.3 policy as a workaround.

### Runtime validation

Retain defense-in-depth validation inside each final-hop policy container.
Runtime startup should reject:

- a Tor socket combined with any non-HTTPS proxy scheme;
- a forbidden proxy endpoint form;
- malformed credentials or URL components;
- an unexpected Tor socket path;
- a configuration in which the intended layered transport is ambiguous.

Startup validation need not make a public probe. The existing local health
checks should remain socket-free and low-idle; real connection failures are
reported on requests. Tor health remains responsible for authenticated local
bootstrap status.

## Testing Plan

### Deterministic configuration tests

Extend `tests/test_tor_config.py` to cover:

- Tor plus an empty proxy URL;
- Tor plus a valid public `https://` proxy, with and without credentials;
- clear rejection of `http://`, `socks5://`, and `socks5h://` in combined
  mode;
- rejection of host, LAN, metadata, stack-local, reserved, and `.onion` proxy
  endpoints in combined mode;
- unchanged acceptance of all currently supported schemes when Tor is off;
- errors that never include proxy passwords;
- environment, command-line, and settings-file precedence.

Add a Make-level test that exercises `wrapper-config-preflight` and checks its
nonzero result and user-facing error for invalid combinations.

### Final-hop transport tests

Use deterministic fake streams and resolvers to prove:

- the first SOCKS destination is the configured proxy, not the requested
  target;
- proxy endpoint DNS is not requested from system or Myst resolvers;
- the HTTPS proxy TLS upgrade uses certificate verification, explicit SNI,
  and the configured minimum TLS policy;
- proxy authentication is sent only inside proxy TLS and is redacted from
  logs and exceptions;
- target `CONNECT` occurs only after Tor SOCKS and proxy TLS succeed;
- permitted plaintext HTTP uses absolute-form forwarding over proxy TLS;
- target DNS remains delegated to the HTTPS proxy;
- local/internal validated exceptions retain their current direct path;
- Tor, SOCKS, TLS, authentication, timeout, malformed response, non-200
  response, and origin TLS failures have no alternate route;
- closing or cancellation at each stage releases the complete layered stream;
- both public and host route-class policy instances behave identically for
  external targets.

Exercise the pinned runtime's real TLS-in-TLS behavior in an isolated image
test with local fake Tor SOCKS and HTTPS proxy endpoints. The test must not use
the Internet and must use generated test-only certificate authority material.

### Compose and lifecycle tests

Render effective Compose models for lite/full and Docker/Podman with:

- neither Tor nor proxy;
- Tor only;
- HTTPS proxy only;
- Tor plus HTTPS proxy;
- Tor plus onion ingress plus HTTPS proxy;
- the representative combination with Tailscale and network-enabled executor
  overlays.

Assert that only the final-hop policy containers receive both chain inputs,
Tor remains on `tor-uplink`, Myst does not receive an actionable proxy route
in chained mode, applications remain isolated, and Podman ownership/tmpfs
translations remain intact.

Run:

```sh
make check
make test-images
make test-tor-image
make test-opensearch-image
```

If implementation proceeds, perform one live Docker and one live Podman
request through a controlled HTTPS proxy and inspect Tor plus both final-hop
proxy logs. Validate a real `web_search`, `open_url`, network-enabled executor
request, configured external integration, Tor failure, proxy failure, and
restart recovery. Do not claim implementation complete without a real
end-to-end proxy test; if no suitable proxy is available, leave the feature
deferred.

## Documentation Changes If Implemented

Replace the current conflict description consistently in:

- `AGENTS.md`;
- `.env.wrapper.example`;
- `README.md`;
- `docs/native_tor_support.md`;
- `docs/vpn_routing_and_proxies.md`;
- `docs/internal_network_security.md`;
- `docs/resource_minimization.md` if health or periodic work changes;
- `docs/podman_suport.md` for the validated Compose/lifecycle matrix;
- `docs/onyx_patches_upgrade.md` for the image and live validation checklist.

Document the exact observation table, DNS ownership, local-route exceptions,
HTTPS-only restriction, provider trust caveat, ZDR uncertainty, credential
linkability, TLS requirements, failure diagnostics, and absence of fallback.
Do not describe Tor plus a proxy as automatically more anonymous or private.

Once implemented, move this file to `docs/plans/implemented/` and add a status
banner linking back to the normative documents. Remove obsolete conflict text
rather than retaining contradictory historical guidance.

## Completion Criteria

This enhancement is complete only when:

- the decision gate identifies a concrete beneficial use case and acceptable
  HTTPS proxy;
- both configuration and runtime enforce HTTPS-only chained mode;
- public proxy endpoint DNS travels only through Tor;
- requested target DNS travels only through the HTTPS proxy;
- proxy TLS and origin TLS remain independently verified;
- proxy credentials are never exposed to the Tor exit or logs;
- local route exceptions and application isolation are unchanged;
- all failure stages are bounded and have no direct fallback;
- deterministic, image, Compose, Docker, and Podman validation passes;
- normative documentation replaces the current mutual-exclusion contract.

Until every criterion is met, the supported behavior remains the current
fail-fast conflict between native Tor egress and
`EGRESS_UPSTREAM_PROXY_URL`.
