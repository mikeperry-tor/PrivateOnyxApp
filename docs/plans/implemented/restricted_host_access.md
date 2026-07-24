# Restricted Docker-host access

> **Status: implemented (2026-07-24).** This is the implementation and
> acceptance record for restricted Docker-host access. Normative deployed
> behavior, diagnostics, and change guidance are documented in
> [Internal network security](../../internal_network_security.md),
> [VPN routing and restricted egress](../../vpn_routing_and_proxies.md),
> [Local document RAG](../../local_docs_rag_search.md), and
> [Podman support](../../podman_suport.md).

## Status and objective

This document records the implementation that replaced the host route's
unrestricted `host.docker.internal` destination exception with an explicit
port policy:

```env
ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS="none"
```

The option must accept:

- a comma-separated list of TCP ports;
- `all`, preserving the current behavior for operators who deliberately need
  unrestricted Docker-host port access; or
- `none`, denying `host.docker.internal` as an integration destination.

The user-facing default is `none`, not `all`. Port 3210 is authorized
automatically in a running full-stack configuration only when the embedding
shim's configured upstream URL is exactly the bundled macOS MLX
lifecycle-proxy URL
`http://host.docker.internal:3210/v1/embeddings`. That automatic grant is
stack-owned internal configuration, not an implied item in the user list.
Lite mode never receives it. Full mode also does not receive it when Teep or
any other alternate embedding endpoint is configured. `make up-full` applies
the selection, and the resulting policy remains active until that policy
container is recreated or removed; subsequent MLX process liveness does not
add or revoke it.

The existing `embedserv-start-if-installed` path already uses that exact URL
comparison: it starts the wrapper-owned MLX lifecycle proxy only for the
bundled URL, skips the launch for every custom upstream, and stops a previously
recorded wrapper-owned MLX process after a transition to a custom upstream.
Reuse this same deterministic selection in the Makefile. As part of this
change, defer transition cleanup until after the custom-mode policy has
successfully removed automatic 3210, as specified below. Do not inspect the
host process list, PID state, listener state, or network reachability to decide
whether port 3210 is allowed. PID-record and listener validation may remain
inside the separate start/stop lifecycle operations; their results must never
grant the port.

The current automatic start also passes `--allow-untracked-listener`, which
lets any pre-existing listener on 3210 satisfy the lifecycle prerequisite.
Remove that flag from `embedserv-start-if-installed`. A qualifying automatic
grant must follow either a newly launched wrapper-managed lifecycle proxy or an
already-running process with the matching ownership record, command
fingerprint, identity, and readiness. A listener already present without a
matching ownership record must fail full startup at the preflight check before
Compose applies automatic 3210.

This is deliberately not proof that the tracked PID owns every later listener
on port 3210. The shared manager checks tracked process identity and port
readiness separately, so a tracked process that stays alive after losing its
listener, or a bind race after preflight, is not cryptographically associated
with the socket. That limitation is acceptable for the present threat model:
the port policy restricts a compromised container, not a malicious local host,
and any allowed port authorizes whichever host process is listening. Do not
claim stronger listener authentication in tests, documentation, or completion
criteria.

Remove the standalone foreground `make embedserv-serve` target as well. It
creates no ownership record and therefore cannot participate in the automatic
full-stack lifecycle without restoring the ambiguous untracked-listener path
or adding a second ownership protocol. The supported flow becomes
`make embedserv-install`, optional `make embedserv-verify-model`, and
`make up-full`, which launches and owns the lifecycle proxy. Direct invocation
of implementation scripts remains an unsupported developer diagnostic, not a
documented deployment mode.

Because no supported caller then needs it, remove
`--allow-untracked-listener` from `embedserv/host_process_manager.py` entirely,
including its permissive branch, CLI argument, and positive acceptance test.
An occupied port without a valid matching ownership record always fails
closed. This simplifies the shared host-process contract for both current and
future services.

Port 8337 is Teep's default host publisher, but it is not allowed
automatically: an operator who deliberately points the embedding shim back at
Teep through the host must add the actual `HOST_PORT_TEEP` value explicitly.
Ollama, LM Studio, another inference server, an MCP server, a Web Connector
origin, or any other custom host service likewise requires its actual host
port to be selected explicitly.

This is a hardening change against arbitrary code execution in an Onyx
`api_server` or `background` process. Both services legitimately join
`onyx-host-egress` and can therefore submit arbitrary HTTP proxy requests to
`onyx-host-egress-bridge`. Today, a compromised process can issue `CONNECT
host.docker.internal:<any-port>` and the host final-hop proxy resolves and
connects to the Docker host directly, bypassing Myst, Tor, and a configured
upstream proxy. The new default must deny every ordinary Docker-host port,
except for the stack-owned port-3210 grant while the bundled MLX path is
selected for full-mode startup.

This option narrows the exact logical host alias; it is not service
authentication. An allowed port authorizes whichever host process is listening
on that port at request time. Operators should therefore list only ports whose
host listeners they trust, and should not assume that stopping the intended
service permanently reserves its port identity.

The implementation must not add compatibility aliases for older behavior.
Operators who require the old behavior can set the explicit value `all`.

## Exact policy semantics

### Accepted syntax

Parse `ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS` once when
`egress/final_hop_proxy.py` starts:

- An unset or empty value resolves to the default `none`. Compose must
  also supply this default explicitly to the host policy service.
- After trimming surrounding ASCII whitespace, exact lowercase `all` means
  every valid TCP port from 1 through 65535.
- Exact lowercase `none` adds no operator-selected ordinary
  `host.docker.internal` destination; the separate stack-owned automatic
  singleton may still apply as specified below.
- Otherwise, split on commas, trim ASCII whitespace from each item, require
  every item to contain only ASCII decimal digits, convert each to an integer,
  and require the result to be between 1 and 65535 inclusive.
- Repeated numeric ports are harmless and should be deduplicated by storing
  the parsed result as an immutable set. Leading zeroes need no special
  rejection because they do not change the selected integer port.
- Implement ASCII trimming and digit recognition explicitly. In Python, use
  `strip(" \t\r\n\v\f")` or an equivalent ASCII-only helper and recognize a
  numeric item with an ASCII `[0-9]+` full match. Do not use unqualified
  `str.strip()`, `str.isdigit()`, or `str.isdecimal()` as the grammar because
  they accept additional Unicode whitespace or numeric characters.
- Reject empty list items, signs, ranges, service names, mixed keywords such
  as `all,3210`, and any other text.
- Reject mixed-case keyword variants such as `ALL` or `None`. This is a new
  strict setting; do not add compatibility spellings.
- Invalid input must raise a non-secret `RuntimeError` before the proxy starts
  listening. Do not fall back to `all`, the default list, or `none`. The
  resulting host-policy failure must also make `make up-lite` and
  `make up-full` fail rather than reporting a usable stack; the stack-level
  mechanism is specified below.

The parser is deliberately limited to one exact hostname and TCP ports. Do not
generalize this option into a hostname, CIDR, URL, scheme, path, or integration
allowlist.

### Stack-owned bundled-MLX grant

Keep the operator policy separate from one internal boolean,
`EGRESS_PROXY_BUNDLED_MLX_HOST_ACCESS`:

- it is not user-facing and must not be added to `.env.wrapper.example`;
- the final-hop proxy accepts exactly lowercase `true` or `false` for this
  internal value, defaulting to `false` on unset or empty input and rejecting
  every other value before listening;
- lite startup always supplies `false`;
- full startup supplies `true` only when the effective
  `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL` is exactly
  `http://host.docker.internal:3210/v1/embeddings`, the same condition that
  selects the wrapper-managed MLX lifecycle proxy;
- full startup supplies `false` for Teep and every other custom embedding URL;
  and
- the effective exact-host permission is the union of the operator policy and
  `{3210}` only when this boolean is true. Thus an operator value of `none`
  still permits 3210 while the running policy container has the qualifying
  bundled-MLX full configuration.

The Makefile selects only the closed internal values `false` or `true`; it does
not parse, normalize, or merge the user port list. The final-hop proxy remains
the sole parser and enforcement authority for the user-facing syntax. Keep the
automatic boolean visible in the effective Compose model and proxy startup log
so the grant cannot be mistaken for an implicit fallback.

### Authoritative enforcement point

The host final-hop proxy is the sole destination-policy authority. Replace the
current host-only predicate `_is_exact_host_exception(host)` with one predicate
that accepts both `host` and `port` and returns true only when:

1. `EGRESS_ROUTE_CLASS` is exactly `host`;
2. the normalized hostname is exactly `host.docker.internal`; and
3. the parsed operator policy is `all`, the target port is in its numeric set,
   or the target port is in the validated stack-owned automatic set.

Use that same predicate everywhere the current exact-host predicate affects
behavior:

- `_validate_destination()`, where an unlisted host port must be rejected
  before DNS and before any connection attempt;
- `_plain_http_allowed()`, so cleartext HTTP remains an exception only for an
  allowed host port;
- `_connect_via_upstream()`, so an allowed exact-host destination continues
  to use the pinned direct connection rather than Myst, Tor, or
  `EGRESS_UPSTREAM_PROXY_URL`; and
- `_open_plain_http_forward_connection()`, for the equivalent direct
  plain-HTTP path.

Destination denial belongs only in `_validate_destination()`. The downstream
connection helpers should reuse the shared predicate solely to select the
already-validated direct path; they must not introduce a second parser or a
different allow decision. Client patches, the embedding shim, the TCP bridge,
Compose networks, and host services must not repeat this port check.

For the host route, an exact `host.docker.internal` request to an unlisted port
must return HTTP 403, perform no system/Docker DNS lookup, open no direct
socket, and never fall through to public DNS, Myst, native Tor, or
`EGRESS_UPSTREAM_PROXY_URL`. The public route must continue to reject the exact
host at every port regardless of this option.

An allowed port retains the current narrow host behavior:

- resolve only exact `host.docker.internal` with system/Docker DNS in the
  trusted routing namespace;
- reject empty resolution and loopback, link-local, multicast, unspecified,
  or reserved answers;
- pin the complete validated answer set used for the connection;
- permit both HTTPS `CONNECT` and plain HTTP even when
  `EGRESS_ALLOW_HTTP_URLS=false`; and
- connect directly over the engine host route, never through Myst, Tor, or an
  external upstream proxy.

Native Tor remains a transport, not a destination-policy authority. Do not
pass either host-access setting into the Tor service or add a Tor-specific
host-port rule. The host final-hop proxy must apply the single effective
exact-host predicate during validation and again in the shared post-validation
direct-route decision; an allowed port therefore bypasses the later
`EGRESS_TOR_SOCKS_UNIX_PATH` branch, while a denied port returns 403 before any
Tor socket is opened. The same rule applies to external upstream transport.

The option selects the TCP destination port, not the URL's implicit or
explicit syntax. For example, `http://host.docker.internal:3210/...` is allowed
only by the qualifying bundled-full automatic grant (or an explicit operator
grant); the operator default remains `none`.
`https://host.docker.internal/...` uses implicit port 443 and is denied unless
the operator explicitly selects 443.

### Policies that must remain independent

Do not apply `ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS` to any of the following:

1. **Configured upstream-proxy endpoint.** If the operator explicitly sets
   `EGRESS_UPSTREAM_PROXY_URL=socks5h://host.docker.internal:9150`, the
   final-hop policy processes may still connect to that one configured
   endpoint even when the host-port option is `none` or omits 9150. This is
   routing infrastructure selected by a separate explicit setting, not an
   integration destination. Keep `_resolve_upstream_proxy_endpoint()` and its
   exact-host validation independent. An Onyx process may cause otherwise
   permitted public traffic to use that SOCKS proxy, but it must not be able to
   submit `CONNECT host.docker.internal:9150` as an ordinary host destination
   unless 9150 is also in the new option.
2. **Stack-owned internal destination.**
   `EGRESS_PROXY_TRUSTED_INTERNAL_DESTINATIONS=doc-drop-web:8091` is an exact
   service authority and remains available to its current host route. It is
   not a Docker-host port and must continue to work when the option is `none`.
3. **Opt-in RFC1918 LAN endpoints.**
   `ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS` keeps its current independent
   meaning. It must neither add Docker-host ports nor be required for a port
   selected by the new option. Direct host-gateway IPs remain governed by the
   existing address policy; the new option recognizes only the exact logical
   hostname. This is an intentional residual: when the LAN option is true, a
   compromised host-route caller may be able to reach the Docker host through
   a known RFC1918 host-gateway IP literal without using
   `host.docker.internal`. Do not silently redefine or narrow the existing LAN
   opt-in in this change. The new default materially hardens the default
   `ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=false` posture; operators enabling
   broader LAN access continue to accept its documented compromise impact.
4. **Host publishers and container membership.** Do not remove
   `netns-holder`'s `host.docker.internal:host-gateway` mapping, alter
   `HOST_PORT_*`, add networks, or give application containers a host mapping.
   The final-hop proxy still needs the mapping to resolve allowed ports and an
   explicitly configured host upstream proxy.

This separation is the reason the option is named
`ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS`, rather than a global
`EGRESS_ALLOW_DOCKER_HOST_ACCESS`. It narrows destinations available to
host-capable Onyx integrations and compromised Onyx callers without changing
an explicitly selected routing endpoint.

### Unification boundary for exact Docker-host connections

The bundled MLX grant and a configured
`EGRESS_UPSTREAM_PROXY_URL=socks5h://host.docker.internal:9150` are the same
kind of network operation at the lowest layer: the final-hop proxy normalizes
the exact Docker-host identity, resolves it with system/Docker DNS, rejects a
forbidden or empty complete answer set, pins the accepted addresses, and opens
a direct engine-route connection without Tor, Myst, or another upstream.
Implement that network operation once.

They are not the same authorization:

- MLX port 3210 is an ordinary destination capability available to a
  host-route caller only when operator policy or the bundled-full boolean
  permits it.
- Port 9150 is a routing-infrastructure capability held by the final-hop proxy
  only because the operator configured that exact upstream authority. It is
  reached while establishing the selected transport for an otherwise
  permitted request, not while validating the caller's target.

Use a pure exact-host identity helper and one shared
resolve/validate/pin helper for both roles. Keep two explicit authorization
entry points, named by purpose, and never insert the configured upstream port
into the effective ordinary-destination set. A generic union such as
`{operator ports} | {3210 when bundled} | {configured upstream port}` would be
incorrect because it would turn `CONNECT host.docker.internal:9150` into an
ordinary allowed destination.

Do not introduce a general purpose-tagged capability framework or endpoint
registry for these two cases. Two small role-specific authorization checks
over shared exact-host mechanics are simpler and make the security distinction
visible at review time.

## Implementation changes

### Final-hop policy

Update `egress/final_hop_proxy.py`:

1. Add a small startup parser and immutable representation for
   `ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS`. Keep it adjacent to
   `ROUTE_CLASS` and `ALLOW_LAN_ENDPOINTS`.
2. Default the raw setting to `none` inside the process as defense against
   direct invocation outside Compose. Treat an explicitly empty string as the
   same documented default.
3. Parse `EGRESS_PROXY_BUNDLED_MLX_HOST_ACCESS` independently as exact
   lowercase `true` or `false`, defaulting empty/unset to `false`. Reject any
   other value before listening. Map true to the immutable singleton `{3210}`;
   do not reuse the user-list grammar for this stack-owned boolean.
4. Replace `_is_exact_host_exception(host)` with the port-aware shared
   predicate described above. Give it a name that makes the port decision
   visible, such as `_is_allowed_exact_host_destination(host, port)`.
5. In `_validate_destination()`, detect the exact normalized hostname on the
   host route before the ordinary blocked-hostname branch. If its port is not
   allowed, return a stable policy reason such as
   `host.docker.internal port is not allowed`. Do not resolve it first.
6. Update the four current exact-host call sites. Do not change trusted
   internal, RFC1918, upstream-proxy authorization, DNS ownership,
   HTTP-framing, bridge-peer, or connection-pinning semantics.
7. Remove the unused `_blocked_destination_reason()` compatibility wrapper.
   It has no production or test callers; `_validate_destination()` remains the
   single destination-policy entry point.
8. Fix explicit-zero port parsing at the same boundary. In
   `_parse_authority()`, absolute-form HTTP target parsing, and
   `_parse_proxy_url()`, apply a protocol default only when `parsed.port is
   None`; never use `parsed.port or default`. Preserve an explicit `0` so
   ordinary destination validation rejects it and configured upstream-proxy
   startup rejects it. This closes CONNECT, origin-form `Host`, absolute-form
   HTTP, and upstream-proxy variants consistently.
9. Add the canonical operator, automatic, and effective policies to the
   existing non-secret startup log. Do not log the raw invalid value when
   parser startup fails.
10. Update the module documentation and comments that currently describe
   `host.docker.internal` as always available or unrestricted.

Normalize the two sources once at startup into one effective immutable
representation: an `allow_all` flag plus the union of the operator numeric set
and `{3210}` when bundled-MLX access is true. Every ordinary-destination
decision must consume only that effective representation. Retain the source
values separately only for validation and diagnostics.

Factor only the narrow helpers needed to keep exact-host policy consistent:

- one pure exact-Docker-host identity predicate and one Docker-host
  resolver/address validator, shared by an allowed ordinary exact-host
  destination and the independently configured
  `EGRESS_UPSTREAM_PROXY_URL` endpoint. Callers retain distinct authorization,
  error context, and existing connection functions, but resolution,
  complete-answer pinning, and forbidden-address classification must not
  drift. Do not consolidate `_open_validated_direct_connection()` with
  `_open_http_proxy_connection()` in this change: doing so would unnecessarily
  alter public direct connections and HTTP, HTTPS, and SOCKS upstream setup,
  including HTTPS proxy TLS/SNI behavior; and
- one post-validation direct-route predicate shared by CONNECT and plain-HTTP
  connection opening. It should cover trusted internal destinations, an
  effectively allowed exact-host destination, and validated opt-in RFC1918
  targets before either helper considers native Tor or an external upstream.

Do not merge authorization for ordinary destinations with authorization for a
configured upstream-proxy endpoint. They may share resolution mechanics, but
the latter must remain usable independently when the operator port policy is
`none` and bundled-MLX access is false.

Do not create a second proxy, a port-filtering sidecar, application-layer
authentication, TLS interception, URL-path filtering, per-integration
listeners, or caller-specific policy in this change. Those would solve
different problems and would duplicate or expand the final-hop boundary.

### Compose

Update `docker-compose.yaml`:

- On `onyx-host-egress-proxy`, pass:

  ```yaml
  ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS: "${ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS:-none}"
  EGRESS_PROXY_BUNDLED_MLX_HOST_ACCESS: "${EGRESS_PROXY_BUNDLED_MLX_HOST_ACCESS:-false}"
  ```

- On `onyx-public-egress-proxy`, set the operator policy statically to `none`
  and the internal boolean statically to `false`, analogous to its static
  false LAN option:

  ```yaml
  ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS: "none"
  EGRESS_PROXY_BUNDLED_MLX_HOST_ACCESS: "false"
  ```

  The route class remains the primary reason the public policy cannot use the
  exception; the explicit values prevent effective-configuration inspection
  from suggesting otherwise.
- Do not pass the option to `api_server`, `background`,
  `local-embedding-shim`, bridges, Myst, Tor, Obscura, or executors. They are
  not enforcement points.
- On `onyx-host-egress-bridge`, keep
  `depends_on.onyx-host-egress-proxy.condition: service_started` and add
  `restart: true`. Docker Compose defines dependency restart propagation for
  an explicit Compose-managed dependency update, but this behavior must not be
  presumed for a Podman Compose provider. Whenever a supported Compose
  operation explicitly recreates or updates the host policy proxy, the bridge
  must also be restarted or recreated, have its health state reset, and make a
  fresh policy-generated 403 probe. Do not rely on the bridge's prior
  `healthy` state or ten-minute steady interval.
- Preserve identical policy under Docker and Podman. No Podman-specific
  policy overlay should be necessary. The Make-selected lifecycle may require
  an engine-specific orchestration command if a supported provider does not
  implement dependency restart propagation; that is an orchestration
  compatibility detail, not a different security policy.

Delete the empty
`compose_overlays/docker-compose.proxy.yml` selection marker. Remove
`PROXY_SUFFIX`, its conditional assignment, and its use in all four lite/full
start/down Compose file lists. `EGRESS_UPSTREAM_PROXY_URL` is already passed
to the two final-hop proxies by the base Compose model and to Myst by its
existing model; an empty layer contributes no configuration or security
boundary. Update structural tests to stop requiring this file or conditional
file-set change, and prove effective upstream-proxy environment values remain
unchanged with and without the URL.

Do not move the duplicated `api_server`/`background` proxy-routing environment
block into `onyx/helper-egress.env` in this change. That optional cleanup
changes configuration ownership and AGENTS.md semantics without simplifying
the enforcement work. Leave it for a separately reviewed change.

### Makefile selection

Extend the restricted shared wrapper-settings reader in
`tor/render_config.py` so `wrapper_setting` can read all three lifecycle
selection inputs, which are not currently in `SETTING_DEFAULTS` or the CLI's
accepted `--name` choices:

- `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL`;
- `ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL`; and
- `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL`.

Preserve the reader's existing later-file-definition and
environment/Make-command-line-over-file precedence, restricted one-line
grammar, literal-dollar behavior, and non-secret errors. Update its module and
function documentation to describe shared wrapper startup selection rather
than only Tor rendering; retaining the current file location is preferable to
an unrelated rename in this change.

Use that reader to compute one canonical set of effective lifecycle inputs:

- an unset or explicitly empty URL becomes the exact bundled URL;
- an unset or explicitly empty MLX model becomes
  `majentik/harrier-oss-v1-0.6b-MLX-8bit`; and
- an unset or explicitly empty upstream served model falls back to the
  canonical MLX model.

Export the canonical public values to Compose and pass the same canonical URL,
MLX model, and derived served model to every embedserv install, verification,
and automatic-start recipe that consumes them. Do not source
`.env.wrapper` again inside those recipes and do not independently recompute
defaults with shell parameter expansion: doing either would let a file value
overwrite an environment or Make command-line value after selection, or make
the lifecycle launch a different model from the embedding shim. Avoid
interpolating these values as shell program text; pass them through the
exported environment.

Make the supported standalone `embedserv-install` and
`embedserv-verify-model` targets depend on `wrapper-config-preflight` before
their recipes can mutate the venv, download a model, or inspect a defaulted
model path. `$(shell ...)` does not propagate the settings reader's exit status
reliably, so this explicit prerequisite is required even though `make up-full`
already has it. The configuration-independent `embedserv-stop-if-started`
target must not consume the current URL or model values and must remain usable
after a configuration parse error or model change.

Before any of those values participates in ordinary Make expansion, apply the
same origin-aware literal-dollar preservation pattern already used for
`TOR_EXIT_NODE_FINGERPRINTS`: for environment- or command-line-origin values,
freeze `$(value VARIABLE)` into a simple exported value; obtain file-origin
values only through the restricted parser. Then derive and export the
canonical values once. This requirement applies to all three inputs, not only
the URL. Add focused `$` cases from file, environment, and Make command-line
origins so neither Make, the shell, Compose, nor a recursive Make invocation
rewrites the value.

Derive one Make-level selection exactly once from that canonical URL:

- exact bundled URL: `EMBEDSERV_MODE=bundled`;
- every non-empty custom URL: `EMBEDSERV_MODE=custom`.

Derive both lifecycle target selection and the closed proxy boolean from that
one mode:

- full bundled: `EGRESS_PROXY_BUNDLED_MLX_HOST_ACCESS=true`;
- full custom: `EGRESS_PROXY_BUNDLED_MLX_HOST_ACCESS=false`; and
- lite: always `EGRESS_PROXY_BUNDLED_MLX_HOST_ACCESS=false`, regardless of
  `EMBEDSERV_MODE`.

Supply that value to every Compose create/configure/up command in the selected
`up-full` or `up-lite` recipe, including both Podman configuration passes and
the staged `local-embedding-shim` start. Make's internal value must override an
ambient shell value of the same name. Do not add it to `.env.wrapper.example`.
Direct hand-assembled Compose invocation defaults the operator policy to
`none` and bundled-MLX access to `false`; supported startup continues to use
the Make targets.

This is selection, not another port-policy parser: Make compares one effective
URL with one exact constant and emits only mode `bundled` or `custom`, from
which it derives exact boolean `true` or `false`. Keep the URL comparison
inside the host lifecycle targets only as a fail-closed cross-check. If the
Make-selected mode and the canonical exported URL received by a target
disagree, fail startup rather than launching, stopping, or continuing with a
different host permission. Do not reload the file for this cross-check. Do not
inspect processes, PID files, listeners, or health to compute the permission.

This is restart-time configuration, not a runtime administration surface.
The wrapper does not watch `.env.wrapper`, mutate the embedding selection in a
running container, or expose an API for changing it. A changed upstream URL is
applied only by a subsequent `make up-full`, which recreates the affected
Compose services. The staged `/ready` gate is part of normal startup and
operator-error containment, not support code for tests: it validates a real
embedding before replacing the API/background tier and leaves the diagnostic
subset available when validation fails. Tests can exercise the same selection
and ordering without requiring embedding configuration to be hot-swappable.

Do not describe this staging as guaranteeing that the previous full stack
remains completely functional after a failed reconfiguration. The embedding
shim and routing dependencies have already been updated, so previously running
API/background containers can remain unrecreated while embedding-dependent
operations no longer match their prior upstream. Also preserve the existing
documentation warning that changing the model, dimensionality, or prefix
policy after indexing can require an index rebuild and that mixed embeddings
are invalid.

Preserve safe transition ordering:

1. For the bundled URL, start or validate the wrapper-managed MLX lifecycle
   proxy before the staged Compose start. Only after that succeeds may Compose
   apply automatic 3210. A failed/missing installation therefore never creates
   a newly permitted port without its intended service. Remove the automatic
   target's `--allow-untracked-listener` argument so an unrelated listener
   cannot satisfy this prerequisite.
2. For a custom URL, the pre-Compose host-process prerequisite must not stop a
   previously recorded MLX process. First run the existing staged
   `local-embedding-shim` Compose start with the automatic boolean false; its
   healthy host bridge proves that the updated policy listener is active and
   forwarding.
3. Perform the custom embedding `/ready` request. If it fails, retain the old
   recorded MLX process for diagnosis; it remains unreachable through the
   now-revoked host policy.
4. Only after replacement readiness succeeds, run one mode-selected cleanup
   target that stops the previously recorded wrapper-owned MLX lifecycle proxy,
   then continue the existing API/background replacement sequence. A live
   proxy stops its in-memory `Popen` child during graceful shutdown. This
   cleanup may use the existing shared-manager identity/PID safeguards, but it
   cannot affect the selected policy.

This ordering prevents a bundled-to-custom transition from leaving the old
automatic-3210 policy active after the intended MLX process has stopped. If
the staged Compose update fails before policy health, retain the old recorded
process for diagnosis rather than creating that mismatch.

Remove the `embedserv-serve` recipe from the Makefile and `.PHONY` inventory.
Do not replace it with another foreground or unrecorded launch target. Keep
`embedserv-install`, `embedserv-verify-model`,
`embedserv-start-if-installed`, and `embedserv-stop-if-started`.

Retain `embedserv/host_process_manager.py` as the one shared cross-platform
owner for both the top-level MLX lifecycle proxy and the Podman host document
server. Do not add an MLX-only owned-child launcher, Unix control protocol,
second record format, platform process API, or service-specific ownership
branch to the manager. Apart from removing generic untracked-listener
acceptance, preserve its existing PID/random-token/configuration-fingerprint,
readiness, atomic-record, and bounded-stop contract for both services.
Accidental PID reuse does not reproduce the fresh 256-bit token in the live
command. The contract is not a defense against a malicious same-user host
process that can read and deliberately reproduce private lifecycle state; that
local-host threat remains out of scope.

Simplify MLX child ownership instead of trying to make it durable across a
proxy crash:

- while the lifecycle proxy is alive, its in-memory `Popen` object is the only
  child ownership authority;
- normal idle unload and graceful proxy shutdown continue to stop and wait for
  that exact child/process group;
- remove the child PID file, `--child-pid-file`,
  `--cleanup-recorded-child`, `embedserv-cleanup-recorded-child`, and every
  process-list-based orphan signaling path; and
- move the existing child-port availability check before the proxy binds or
  exposes port 3210. An occupied loopback child port 3211 therefore makes the
  top-level proxy start fail before the shared manager reports readiness and
  before Compose can apply automatic 3210.

If the lifecycle proxy is forcibly killed or crashes after starting MLX, its
child may remain on loopback port 3211. Do not guess ownership or signal it on
the next start. A later bundled start must fail visibly because 3211 is
occupied, without binding 3210 or applying a new automatic grant. A
bundled-to-custom transition may stop a live recorded proxy after replacement
readiness; if the recorded proxy is already gone, the shared manager retains
its existing non-signaling stale-record behavior. The stack-owned automatic
policy never grants child port 3211; an operator must not add that internal
implementation port to the ordinary host allowlist. Document manual host
diagnosis as the recovery for this exceptional crash case. This is an
intentional availability/resource tradeoff in favor of a small portable
ownership boundary, not a successful automatic cleanup claim.

`down-full` likewise calls only the shared manager's top-level proxy stop. A
live proxy performs graceful child cleanup. A missing, malformed, stale, or
identity-mismatched proxy record retains the shared manager's non-signaling
behavior; `down-full` must not fall back to a child PID file or process-list
guess.

Document and distinguish both exceptional manual-recovery states. A malformed
or identity-mismatched top-level record can leave the lifecycle proxy itself
alive, so the next bundled start reports an untracked listener on port 3210.
A crashed proxy can instead leave only its MLX child, so startup reports
occupied internal child port 3211 before binding 3210. Neither diagnostic may
recommend automatic PID signaling, deleting a record as proof of ownership, or
adding 3211 to the host-port allowlist.

The shared lifecycle implementation remains platform-neutral. The optional MLX
package itself is macOS-only, while the manager, document server, proxy policy,
Make selection, and full custom-embedding stack must run on Linux and macOS.
Any genuinely platform-specific capability test must detect its platform and
skip elsewhere; portable lifecycle tests must run on both.

Remove the generic `--allow-untracked-listener` option from
`host_process_manager.py`; the ordinary occupied-untracked-port failure becomes
the only behavior.

### Stack bring-up failure

Do not add a user-port parser to the Makefile, a caller, or a shell preflight.
The final-hop proxy startup parser remains the single syntactic authority; the
Makefile emits only the closed automatic boolean selected above.

Use the existing `onyx-host-egress-bridge` health boundary to propagate parser
failure to stack bring-up:

1. Invalid configuration makes `onyx-host-egress-proxy` exit before it
   listens. Its existing restart policy may restart it, but it must never
   become a usable listener with fallback policy.
2. The bridge's `depends_on` entry uses `restart: true`, so a supported Compose
   operation that recreates the host policy proxy also restarts the bridge.
   This discards any stale healthy status from a previous stack run and puts
   the bridge through startup health again; waiting up to ten minutes for its
   ordinary steady-state interval is not part of failure propagation.
3. The restarted `onyx-host-egress-bridge` cannot forward its existing health
   probe to the absent policy listener, so its fresh health check fails.
   Preserve the probe's requirement for an HTTP 403 response from the policy
   proxy; a TCP refusal, disconnect, or empty response is not healthy.
4. The Makefile's existing
   `docker compose up -d --wait --wait-timeout 420` path therefore exits
   nonzero instead of reporting successful lite/full startup. Full mode's
   staged `local-embedding-shim` startup also remains gated on the freshly
   evaluated host bridge.
5. Podman's native startup-health translation and dependency restart
   propagation must preserve the same result: the restarted bridge never
   reaches healthy and the bounded Compose wait fails.

Passing the fresh and warm failure test is a mandatory acceptance gate for
every supported Docker and Podman Compose provider. If a Podman provider does
not honor `depends_on.restart`, add an explicit Make-selected Podman startup
phase that recreates the host policy proxy and its stopped host bridge
together, before the ordinary bounded whole-stack start and startup-health
configuration. That phase must fail closed and must not first run an
unbounded/waiting start against the stale bridge. Do not add a second parser,
probe process liveness, or silently continue with provider-dependent stale
health. The Docker path may continue to use qualified dependency propagation;
both paths must produce the same externally observed policy and failure
contract.

Do not add a duplicate periodic health check to the final-hop proxy. The bridge
is already the externally consumed readiness boundary and proves both that the
policy listener exists and that forwarding reaches it. Do not change
`depends_on` from `service_started` to `service_healthy` for the proxy unless a
proxy health check is deliberately introduced for some separate reason.

Test both fresh startup and an already-running healthy stack. For the latter,
first prove the bridge healthy, change only the host-port policy to an invalid
value, and invoke the normal matching Make start. On Docker and Podman, assert
that Compose recreates the policy proxy, dependency propagation restarts the
bridge, its health state is freshly evaluated, and the bounded start exits
nonzero. A stale `healthy` bridge or a zero exit is a failure. Do not work
around this with a duplicate configuration parser.

### User-facing configuration

Add the setting to `.env.wrapper.example` in section 5, immediately before
`ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS`. Explain:

- the operator default is `none`;
- `all` restores the previous any-port behavior and increases the impact of a
  compromised Onyx backend;
- `none` blocks every operator-selected ordinary `host.docker.internal`
  integration target, while a qualifying bundled-MLX full start adds the
  visible stack-owned port-3210 grant;
- Teep, including its default host port 8337, and every other custom host
  service require their actual port in the list;
- lite mode and full mode with a custom embedding endpoint receive no automatic
  port; a running full stack receives automatic port 3210 only while its
  applied policy configuration selects the bundled MLX lifecycle proxy. The
  grant persists until policy recreation/removal and is not revoked merely by
  later MLX process failure;
- the option does not enable RFC1918 LAN endpoints;
- enabling the separate LAN option retains its broader documented compromise
  impact, including the possible host-gateway-IP residual;
- it does not control an exact host endpoint explicitly configured as
  `EGRESS_UPSTREAM_PROXY_URL`; and
- full-mode startup fails closed if its configured host embedding endpoint is
  not permitted.

Keep the option next to LAN destination policy, not in the inbound **Host
Endpoints** section.

## Deterministic tests

### `tests/test_tor_config.py`

Extend the shared wrapper-settings tests to include
`ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL`,
`ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL`, and
`ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL` in the accepted setting inventory.
Prove their defaults, quoted file values, later-definition wins,
environment-over-file precedence, and Make-command-line-over-file precedence.
For each origin, include a representative literal `$` value and prove the
exact bytes survive Make evaluation and export. Add CLI `get` cases so a
regression in the `--name` choices cannot silently yield an empty Make
selection. Existing Tor, upstream-proxy, and canonical-origin validation must
remain unchanged.

### `tests/test_restricted_egress_proxy.py`

Extend the existing loader's explicit environment with the default
`ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS=none` and
`EGRESS_PROXY_BUNDLED_MLX_HOST_ACCESS=false`. Add focused cases covering:

1. Parser results for the default list, whitespace around numeric items,
   duplicate ports, exact lowercase `all`, and exact lowercase `none`.
2. Startup failure for representative invalid classes: an empty item,
   non-decimal text, a non-ASCII decimal digit, non-ASCII surrounding
   whitespace, a mixed keyword/list, mixed-case keywords, port 0, and port
   65536. One case per failure class is sufficient; do not build a
   combinatorial parser corpus.
3. On the host route with both defaults, 3210, 8337, and 9150 return the stable
   policy reason without calling the system, Myst, or upstream resolver.
4. `none` rejects a representative host port without DNS; `all` accepts a
   representative non-default valid port and preserves the current pinned
   direct connection behavior.
5. The internal automatic setting accepts only exact lowercase `false` and
   `true`; unset or empty becomes `false`, while mixed case, numeric values,
   surrounding whitespace, and arbitrary text fail startup.
6. With the operator policy `none` and bundled-MLX access true, port 3210
   validates and uses the pinned direct path while 8337 and 9150 remain denied
   before DNS. With the operator policy explicitly allowing another port, the
   effective union contains both that port and stack-owned 3210. A false
   bundled-MLX value adds nothing to `none`, a numeric list, or `all`.
7. The public route rejects `host.docker.internal` even if the operator option
   is `all` or bundled-MLX access is true.
8. Plain HTTP is allowed only for an operator-listed or automatically granted
   host port when global HTTP is disabled. An unlisted port must not acquire
   the HTTP exception.
9. With an external upstream or native Tor selected, an allowed ordinary host
   destination still connects directly. An unlisted destination is rejected
   before either route is attempted. Test one connection helper deeply and
   keep the existing focused plain-HTTP test for the other; do not duplicate
   every assertion across HTTP and CONNECT. Assert specifically that bundled
   MLX access true plus operator `none` reaches 3210 without opening the Tor
   Unix socket, while operator `none` plus bundled-MLX access false rejects
   3210 before that socket or any upstream connection is attempted.
10. Cover the configured Docker-host upstream-proxy exception positively and
   negatively with the integration option set to `none`:
   - with
     `EGRESS_UPSTREAM_PROXY_URL=socks5h://host.docker.internal:9150`, a
     permitted public target resolves the exact configured proxy endpoint with
     system/Docker DNS, connects only to its pinned validated address on port
     9150, completes the SOCKS handshake, and reaches the requested public
     target;
   - in that same configuration, an ordinary client target of
     `host.docker.internal:9150` returns 403 before system/Docker DNS or any
     connection attempt, proving that the configured routing endpoint does not
     become an integration destination; and
   - with `EGRESS_UPSTREAM_PROXY_URL` unset and the allowed-port option still
     `none`, the ordinary `host.docker.internal:9150` target remains denied and
     no upstream-endpoint resolver or connection path is invoked.
   Exercise the request/connection path, not only
   `_resolve_upstream_proxy_endpoint()` in isolation. This is the critical
   positive/negative non-interference regression.
11. `doc-drop-web:8091` remains allowed by the trusted-internal setting when the
   host-port option is `none`.
12. One existing RFC1918 allow/deny case remains green with the host-port
    option set to `none`, proving the two options are independent.
13. Handler-level coverage proves the policy is connected to proxy request
    parsing and HTTP status generation:
    - `_handle_connect("host.docker.internal:9150", ...)` returns 403 without
      resolving or opening a connection, both when the same authority is
      configured as `EGRESS_UPSTREAM_PROXY_URL` and when no upstream is set;
    - `_handle_connect("host.docker.internal:3210", ...)` reaches the
      validated direct path with operator `none` plus bundled-MLX access true,
      and returns 403 with operator `none` plus bundled-MLX access false;
    - absolute-form plain-HTTP requests to port 3210 and an unlisted port
      respectively reach the direct path and return 403;
    - `https://host.docker.internal/...` or an equivalent CONNECT authority
      with implicit/default port 443 is denied; and
    - explicit port zero in CONNECT authority, absolute-form HTTP, and an
      origin-form request's `Host` header remains zero through parsing and is
      rejected before DNS or connection, even when the corresponding protocol
      default port is allowed; and
    - normalized spellings such as `HOST.DOCKER.INTERNAL.` cannot bypass or
      lose the same port decision.
14. The startup diagnostic renders the operator, automatic, and effective
    policies canonically for both defaults, a deduplicated/sorted numeric
    override, `all`, and the automatic-3210 union, without exposing a raw
    invalid value in the fatal parser error.
15. Focused helper tests prove ordinary exact-host and configured-upstream
    resolution share complete-answer validation and pinning, while their
    authorization remains independent. Shared direct-route tests cover trusted
    internal, effective exact-host, RFC1918, Tor-selected, and
    external-upstream-selected cases without duplicating the full handler
    matrix. Assert that both allowed MLX 3210 and configured upstream 9150 call
    the same exact-Docker-host resolution/validation helper, but only 3210
    appears in the effective ordinary-destination set; ordinary 9150 remains
    denied unless the operator independently lists it.
16. Configured `http`, `https`, `socks5`, and `socks5h` upstream URLs with
    explicit port zero fail startup as invalid instead of aliasing their
    protocol defaults. Omitted ports retain the documented defaults.

Adapt existing exact-host tests rather than retaining duplicate tests for the
old unlimited default.

### `tests/test_onyx_network_isolation.py`

Extend effective-Compose assertions to prove:

- the host final-hop proxy receives operator `none` and bundled-MLX access
  `false` by default;
- an override such as `none`, `all`, or `3210,11434` reaches only the host
  final-hop proxy;
- the public final-hop proxy remains fixed at operator `none` and bundled-MLX
  access `false` despite wrapper or ambient overrides;
- no application, bridge, browser, executor, Myst, or Tor service receives the
  settings;
- Make-selected lite Docker/Podman models always render bundled-MLX access
  `false`;
- Make-selected full Docker/Podman models render bundled-MLX access `true` for
  the exact bundled URL and `false` for Teep and a representative custom URL;
- the operator value remains independent in every mode;
- the host bridge retains the health probe that requires a policy-generated
  403, declares dependency restart propagation from the host policy proxy, and
  the Makefile-selected lite/full starts retain bounded `--wait`;
- `compose_overlays/docker-compose.proxy.yml`, `PROXY_SUFFIX`, and the four
  file-list references are absent; and
- setting a representative upstream proxy changes the effective proxy/Myst
  environment as before without changing the selected Compose file list.

Use the existing matrix helpers, but do not exhaustively combine every option
value with every unrelated VPN, Tor, executor, and frontend overlay. The
settings change environment values and no topology.

### `tests/test_myst_lifecycle_makefile.py`

Extend the existing host-process selection tests to prove:

- the effective empty/unset embedding URL selects the bundled URL, launches
  `embedserv-start-if-installed`, and supplies bundled-MLX access `true` to
  every full Compose phase;
- Teep and representative custom URLs skip MLX launch, supply bundled-MLX
  access `false`, and defer the recorded proxy stop until after replacement
  embedding readiness;
- every lite Compose phase forces bundled-MLX access `false`, even when the
  full-mode embedding setting contains the bundled URL;
- an ambient or command-line `EGRESS_PROXY_BUNDLED_MLX_HOST_ACCESS` value
  cannot override the Make-selected closed value; and
- file, environment, and Make command-line embedding URL values obey the shared
  parser's documented precedence, with the same coverage for the MLX and
  served-model values; the one canonical URL/model/served-model set reaches
  Compose plus install, verification, automatic-start, and mode selection
  without any of those recipes sourcing the file again;
- malformed accepted-setting syntax makes direct `embedserv-install` and
  `embedserv-verify-model` stop at `wrapper-config-preflight` before their
  recipes execute; the test must not invoke `uv`, create a venv/model path, or
  fall back to the default model;
- `embedserv-stop-if-started` has no configuration-preflight prerequisite and
  consumes no canonical URL/model value, so a malformed current settings file
  cannot prevent identity-checked shutdown;
- literal-dollar values from file, environment, and command-line origins
  survive the origin-aware Make freeze, export, recursive Make calls, and
  lifecycle cross-check byte-for-byte;
- the launch target fails closed if its canonical URL and the Make-selected
  mode/boolean disagree. A missing/invalid bundled installation must fail
  before any Compose create/up command can apply automatic 3210 to a new proxy;
- the automatic start no longer passes `--allow-untracked-listener`; a
  pre-existing unrecorded listener on 3210 fails before Compose, while a
  matching recorded/ready wrapper-managed process remains reusable;
- `embedserv-serve` is absent from Make recipes and `.PHONY`, while install,
  verify, automatic start, and the shared-manager proxy-stop target remain;
- `embedserv-cleanup-recorded-child`, the child PID file, and every
  process-list-based child cleanup invocation are absent; and
- Make help, prerequisites, and lifecycle tests contain no foreground/untracked
  launch path.

Also assert exact transition ordering. Bundled mode must start/validate MLX
before the staged Compose command applies automatic 3210. Custom mode must not
stop a recorded MLX process in the pre-Compose prerequisite; it must first
complete the staged Compose wait with bundled-MLX access `false`, then pass
embedding `/ready`, then invoke the shared manager's identity-validated
proxy-stop target. Simulate failed staged Compose and failed
replacement-readiness cases and prove the stop is not invoked in either case.

### `tests/test_idle_embedding_proxy.py`

Remove child-record/orphan-cleanup tests with the deleted implementation.
Retain the portable in-memory lifecycle tests for exact `Popen` ownership,
concurrent cold start, model validation, active-request drain, idle unload,
graceful shutdown, bounded TERM/KILL of the exact child group, and an occupied
child port.

Add ordering coverage proving the proxy checks loopback port 3211 before
binding port 3210. Simulate a proxy crash that leaves 3211 occupied and prove a
subsequent startup fails without inspecting or signaling the listener and
without making 3210 ready. Also prove normal shared-manager shutdown of a live
proxy invokes the proxy's graceful child cleanup, while a stale/mismatched
top-level record never authorizes child signaling. Do not claim automatic
orphan cleanup after a parent crash. Require distinct non-secret diagnostics
for an untracked top-level listener on 3210 and an occupied child port 3211.

Any additional test that genuinely probes a macOS-only dependency or Docker
Desktop behavior must use explicit platform detection such as
`sys.platform == "darwin"` and skip on other hosts. Linux must still run all
portable ownership, policy, lifecycle, and full-stack custom-embedding tests;
do not use a macOS skip to hide a portable failure.

### `tests/test_host_process_manager.py`

Remove the untracked-listener acceptance case and assert the simplified
contract instead: an occupied port without a valid matching ownership record
always raises `ContractError`, the parser exposes no
`--allow-untracked-listener` option, and matching recorded ownership still
reuses a ready service. Preserve and explicitly test the shared stop contract:
malformed or identity-mismatched records are diagnosed and removed without
signaling or failing shutdown, including for the Podman document-server use.
Retain the same record/parser/start/stop implementation for the MLX proxy and
Podman document server; do not add a second MLX record or ownership helper.
Do not claim that the separate tracked-PID and listener-readiness checks prove
socket ownership.

### Existing MCP and Web Connector routing tests

Extend `tests/test_mcp_egress_patch.py` to cover all four saved Admin levels:
`VALIDATE_ALL` and `VALIDATE_LLM` select the public bridge, while
`ALLOW_PRIVATE_NETWORK` and `DISABLED` select the host bridge. Extend
`tests/test_web_connector_egress_patch.py` to prove that only `VALIDATE_ALL`
selects the public bridge and that `VALIDATE_LLM`,
`ALLOW_PRIVATE_NETWORK`, and `DISABLED` select the host bridge. Preserve the
exact internal `doc-drop-web:8091` host-route exception independently. These
are focused table-driven cases over the existing patch helpers, not a new
route-selection abstraction.

No image-contract test is required. The proxy script is bind-mounted, and this
change does not alter an image, runtime patch, parser dependency, Tor image, or
OpenSearch image.

## Validation criteria

### Required deterministic validation

Run:

```sh
make check
```

This is the required pre-handoff gate. It covers the complete Python suite,
Python compilation, `make help`, and `git diff --check`. Do not run
`make test-patch-images`, `make test-tor-image`,
`make test-opensearch-image`, or `make test-all-images`; none of their image
families changes.

Render and inspect the Makefile-selected effective Compose model for:

- lite Docker;
- full Docker;
- lite Podman; and
- full Podman.

Confirm only the two final-hop services contain the settings, the public
operator value is `none`, the public automatic boolean is `false`, the host
operator default/override is correct, the Make-selected bundled-MLX boolean
matches the lite/bundled-full/custom-full matrix, and service networks and
bridge destinations are unchanged. Use the repository's existing Compose test
helpers or Makefile-selected rendering rather than hand-assembling a different
layer order.

Add or retain focused structural tests that tie invalid-policy propagation to
the actual start contract: the host bridge health probe must fail without a
policy-generated 403, its host-policy dependency must set `restart: true`, and
both `up-lite` and the full-mode staged/final starts must use bounded Compose
`--wait`. Test the selected Docker and Podman effective models. If a supported
Podman provider requires the explicit Make fallback, structurally assert that
the policy proxy and stopped bridge are recreated before the bounded
whole-stack start and before startup-health configuration. These checks
complement parser unit tests without duplicating the parser in test-only
startup code; the stateful stale-health regression remains a live engine test.

### Focused live validation

Because this changes a request-path security boundary, deterministic tests
alone are insufficient for final release validation. When Docker host access
and temporary local listeners are available:

1. Start lite mode with the operator default `none` and explicit no-VPN mode.
   From `api_server`, issue HTTP proxy requests through
   `onyx-host-egress-bridge`, not direct host requests. Confirm ports 3210 and
   8337 both receive 403 before Docker-host DNS and that disposable listeners,
   when safely available on those ports, record no connection.
2. In full mode with the bundled MLX URL and operator `none`, confirm the
   wrapper launches the MLX lifecycle proxy, the effective model and startup
   log show bundled-MLX access true and effective port 3210, staged embedding
   readiness succeeds,
   and a representative unlisted port remains denied without a connection.
3. Change full mode to Teep or another custom embedding endpoint. Confirm the
   staged Compose start first makes bundled-MLX access false and proves the host
   bridge healthy, custom embedding readiness succeeds, and only then the
   shared manager stops the recorded live wrapper-owned MLX proxy, whose
   graceful shutdown stops its in-memory child. Confirm port 3210 is denied
   after the stop. If a disposable listener can then
   be bound safely to 3210, prove that it records no connection from the denied
   request. Select the custom endpoint's actual host port explicitly when that
   endpoint itself must remain reachable. Also force the staged Compose update
   and custom readiness to fail separately; neither failure may clean up the old
   recorded MLX process.
4. Repeat a representative request with operator `all`: it permits the
   otherwise unlisted port in lite and full mode. Also test an explicit numeric
   list with bundled-MLX access true in full mode, proving the effective union
   permits both without widening any other port. Restart/recreate the host
   final-hop proxy as required so startup configuration is actually reloaded.
5. Set one representative invalid value such as `3210,not-a-port` and run the
   normal Makefile start twice for each supported engine/provider: once from a
   clean stopped stack and once after first establishing a healthy matching
   stack.
   In the warm case, confirm the policy proxy is recreated, dependency
   propagation or the explicit qualified Make fallback restarts/recreates the
   previously healthy host bridge, and a fresh bridge probe fails. Both starts
   must exit nonzero within the bounded wait; the host policy must never listen
   under fallback policy, and logs must contain the non-secret configuration
   error. Exercise every supported Docker and Podman provider; failure blocks
   release for that provider. Do not treat a stale healthy bridge, restarting
   policy container, or partially running Compose project as successful
   bring-up.
6. Separately, when validating the documented Teep embedding path, set the
   operator list to include the actual `HOST_PORT_TEEP` value as well as
   changing the upstream URL and model; port 8337 must fail under operator
   `none` and succeed only after it is selected explicitly. If the required
   private model/service is unavailable, record this exact omission rather
   than substituting an unrelated external endpoint.
7. If a host SOCKS proxy is available, set
   `EGRESS_UPSTREAM_PROXY_URL=socks5h://host.docker.internal:<proxy-port>` to a
   port omitted from the integration list and set
   `ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS=none`. Prove a permitted public request
   uses that upstream successfully while an ordinary host-route `CONNECT` to
   the same host port receives 403 and the proxy listener records no second
   connection attributable to that denied ordinary target. Then unset
   `EGRESS_UPSTREAM_PROXY_URL`, retain `none`, and prove the ordinary target
   remains denied without contacting the former upstream listener.
8. Repeat the allowed/denied direct-host check with Myst enabled if funded
   credentials are available, confirming that an allowed host port stays on
   the engine bridge and a denied port has no VPN fallback. This is a
   conditional routing check, not a release blocker when credentials are
   unavailable.
9. Under Podman, validate bundled-full automatic allow, lite/custom-full
   automatic deny, and one explicit operator allow using the actual resolved
   `host.docker.internal` address and route. Do not assume a Docker Desktop
   gateway address or interface name. Confirm the Podman doc-drop host relay
   remains unaffected. Also exercise fresh and warm invalid values and confirm
   the normal bounded Podman bring-up fails for every supported provider. If
   dependency restart propagation did not qualify, exercise the explicit
   Make-selected bridge-recreation phase instead.

Run portable deterministic coverage and the custom-embedding full-stack path
on Linux as well as macOS. Any validation that actually depends on MLX,
Docker Desktop, or another macOS-only external capability must detect
`sys.platform == "darwin"` (and the required capability) before running and
must report a skip on other hosts. Such a skip must not suppress portable
proxy, ownership-launcher, Make/Compose, Podman, or Linux custom-embedding
coverage.

Do not test all 65,535 ports, every URL path, every HTTP method, or a Cartesian
product of VPN/upstream/Tor and option values. The parser tests establish set
membership; the focused live checks establish that one allow and one deny
cross the real boundary.

## Documentation updates required with implementation

Update all current documentation that describes exact
`host.docker.internal` as available without another setting:

1. `.env.wrapper.example`
   - add the new option and complete user-facing semantics in section 5;
   - revise the upstream-proxy comments to distinguish the independently
     configured proxy endpoint from ordinary integration destinations;
   - revise the Security Hardening comments that currently imply unconditional
     host access; and
   - annotate the embedding URL examples with the required selected port.
2. `README.md`
   Perform a section-by-section pass rather than updating only the first
   `host.docker.internal` explanation:

   - **Components**: review Teep's description as a local proxy on port 8337.
     It may remain an inbound component description, but must not imply that
     every container can reach arbitrary host-published ports.
   - **First-run configuration → Configure Environment → WebUI canonical
     origin**: review the `http://localhost:3000` guidance. This is host-browser
     ingress and is not controlled by the new option; retain it without
     conflating it with container-to-host egress.
   - **First-run configuration → Configure Environment → Tor, VPN, and Proxy
     Use**: when recommending a host Tor Browser SOCKS port, point to the
     independently configured `EGRESS_UPSTREAM_PROXY_URL` exception. Do not
     tell users to add that port to the integration list unless the same port
     must also be an ordinary integration destination.
   - **First-run configuration → Configure Environment → Optional LAN
     access**: introduce the new host-port option before the separate LAN
     option; replace the unconditional-host statement; explain operator
     default `none`, `all`, custom host ports including Teep 8337, the
     automatic bundled-full-only 3210 grant, the compromised-backend rationale,
     and the independently broader RFC1918 opt-in.
   - **Onyx UI Configuration**: update the statement that other configured
     chat endpoints “may use `host.docker.internal` by default.” State that
     their actual port must be selected. Keep the exact internal
     `http://teep:8337/v1` service path separate because it does not traverse
     the Docker-host exception.
   - **Inference Provider Recommendations**: update local OpenAI-compatible,
     LM Studio, and Ollama instructions so their actual host ports must appear
     in `ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS`. Preserve the separate
     `ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS` requirement for RFC1918
     destinations.
   - **Search and Web Crawler Provider Configuration**: review the
     `http://localhost:3000/...` Admin link and the public-route guarantees.
     The browser-side link remains unchanged; search and crawler traffic must
     remain unable to use the host exception.
   - **Optional: Native Tor**: review the statements about onion ingress not
     publishing another host port and simultaneous localhost/onion/Tailscale
     browser access. They describe inbound WebUI access and should remain
     distinct from the selected host integration ports.
   - **Optional: Tailscale Funnel**: review its host/browser-access wording;
     Tailscale ingress is unaffected by this option.
   - **Optional: Network Access for the Code-Interpreter**: retain and verify
     the statement that executors cannot target the host or LAN. The new option
     must not be described as extending to executors.
   - **Optional: Outbound Proxy (`EGRESS_UPSTREAM_PROXY_URL`)**: state that an
     exact configured host proxy endpoint such as port 9150 is independently
     allowed as routing infrastructure even when omitted from the integration
     list or when the list is `none`. Contrast that with an ordinary
     host-route `CONNECT`, which requires the port in the list.
   - **Optional: Local Document RAG via Web Connector**: review the browser
     display origin `http://localhost:8091/`. It is a host-published,
     user-clicked display endpoint, not a reason to add 8091 to the integration
     list; the internal crawl path remains the exact stack-owned
     `doc-drop-web:8091` exception.
   - **Optional: Running a Local Embedding Model Server (Mac)**: state that the
     documented `http://host.docker.internal:3210/v1/embeddings` path receives
     a stack-owned automatic port grant while the running full-stack policy
     configuration selected by `make up-full` uses bundled MLX. State that the
     grant lasts until policy recreation/removal rather than tracking process
     liveness. Remove the foreground
     `make embedserv-serve` instructions and describe install, optional
     verification, and automatic full-stack lifecycle as the only supported
     flow. Retain the browser/host-download distinctions.
   - **Optional: Using Teep for Embeddings**: state that port 8337 is not in
     the default list. Require the actual `HOST_PORT_TEEP` value to be added
     alongside the matching URL change, including when it retains the default
     value 8337.
   - **Optional: Embedding Model Configuration in Onyx**: review the
     `http://localhost:3000/...` Admin link as browser ingress; it is not
     controlled by the new option.
   - **Optional: External MCP servers**: replace “can be allowed without
     enabling general LAN access” with the precise three-layer rule:
     1. the saved Onyx Admin SSRF level must select the host-capable MCP
        transport (`Allow Private Network` or `Disabled` in the pinned Onyx
        contract);
     2. the MCP server's exact Docker-host port must be selected; and
     3. the separate LAN option is unnecessary for exact
        `host.docker.internal`, but remains required for supported RFC1918 or
        operator-local destinations.

     Make clear that selecting a host port does not override Onyx's
     application-layer route choice and that users should not weaken the
     global Admin SSRF level merely to compensate for an omitted port.
   - **Docker Host Endpoints**: explicitly distinguish the inventory of ports
     published for host-browser/operator use from ports that Onyx containers
     may reach through the host final-hop policy. Do not imply that published
     WebUI, SearXNG, Teep diagnostic, or document-display ports are
     automatically container-accessible. Note that 3000, 8080, 8091, and Teep
     port 8337 are not selected merely because they are published. Port 3210 is
     automatically reachable while the running full-stack policy selects
     bundled MLX and is not a general browser endpoint in this list.
   - **Privacy and Security of this stack**: replace the claim that a fully
     compromised Onyx application receives access to ports generally listening
     on the Docker host's localhost interface. State that the operator default
     is `none`; bundled-MLX full mode automatically authorizes only port 3210;
     lite and custom-embedding full mode add no automatic port; `all`
     deliberately restores the old any-port exposure; allowed ports authorize
     whatever process occupies them; and enabling the separate RFC1918 LAN
     option retains the documented host-gateway-IP residual.
     Preserve the stronger statement that agent-controlled public tools and
     executors cannot select this host route.
   - **Privacy and Security → The Anti-Bot Landscape is also Anti-Privacy**:
     review the localhost SearXNG preferences link and host-VPN terminology.
     These refer to operator browser access and the host's own route, not
     integration host-port permission.

   Also review incidental `localhost` links in these sections when editing so
   they remain clearly browser/operator-facing. Do not mechanically replace
   `localhost` with `host.docker.internal`: the two names describe different
   sides of the boundary. Before completing the README edit, inventory every
   case-insensitive README occurrence of `host.docker.internal`, `localhost`,
   `Docker host`, `host access`, `host endpoint`, `host port`, `private LAN`,
   and `RFC1918`; account for each hit as either updated policy text or
   intentionally unchanged inbound/operator guidance.
3. `docs/internal_network_security.md`
   - replace the “separate default exception” description with the restricted
     port policy;
   - explain the compromised-backend boundary and the remaining allowed-port
     authority; and
   - retain the independent Onyx Admin SSRF route-selection layer: a host port
     grant does not itself select the host-capable route;
   - state the two distinct pinned mappings exactly: MCP uses the public route
     for `VALIDATE_ALL` and `VALIDATE_LLM` and the host route for
     `ALLOW_PRIVATE_NETWORK` and `DISABLED`; Web Connector uses the public
     route only for `VALIDATE_ALL` and the host route for `VALIDATE_LLM`,
     `ALLOW_PRIVATE_NETWORK`, and `DISABLED`, with the exact internal doc-drop
     route remaining separately stack-owned; and
   - add the host-port setting and warm-reconfiguration bridge-health check to
     the verification checklist.
4. `docs/vpn_routing_and_proxies.md`
   - update native-Tor direct exceptions, the host route-class policy, DNS
     ownership, validation steps, and Docker/Podman host-route text;
   - state that allowed ordinary host ports remain direct while denied ports
     never fall through; and
   - preserve the distinction from the configured upstream-proxy endpoint;
     and
   - expand the description of the restricted shared settings reader to
     include restart-time embedding endpoint selection while keeping Tor
     validation and rendering responsibilities distinct; and
   - document that host-policy recreation restarts the dependent bridge so a
     prior healthy state cannot satisfy a new bounded start.
5. `docs/local_docs_rag_search.md`
   - document why and exactly when the stack-owned automatic 3210 grant
     preserves the bundled MLX path;
   - confirm the automatic grant is absent when the existing exact-URL
     selection skips MLX for a custom upstream;
   - document the safe bundled-to-custom transition ordering: revoke automatic
     3210, prove bridge policy health, validate the custom embedding endpoint,
     and only then stop the recorded live MLX lifecycle proxy;
   - remove `make embedserv-serve` and every supported-foreground/untracked
     listener description; document install, optional verification, and
     automatic `make up-full` ownership as the supported flow;
   - document that the existing shared host manager owns the top-level MLX
     proxy and Podman document server with the same portable
     PID/token/configuration contract, while the live proxy alone owns its MLX
     child through `Popen`;
   - remove durable child-record/crash-cleanup claims and document the explicit
     failure mode: an orphaned loopback child makes the next bundled start fail
     before port 3210 binds and requires manual host diagnosis;
   - separately document an untracked live top-level proxy on port 3210 after
     malformed/mismatched record handling, and keep its manual recovery
     diagnostic distinct from the occupied-child-port-3211 case;
   - require port 8337 or the configured custom Teep/embedding host port to be
     added explicitly for the Teep path;
   - document the `none` startup failure behavior; and
   - update diagnostics to check the new setting before suggesting LAN access.
6. `docs/podman_suport.md`
   - replace unconditional exact-host wording with the selected-port policy;
   - retain the requirement to validate the actual resolved address and route;
   - preserve the shared manager's non-signaling, successful stop behavior for
     malformed or mismatched parent records; and
   - include allow/deny behavior and warm invalid-policy dependency-restart
     failure in deterministic/live compatibility checks.
7. `docs/native_tor_support.md`
   - update any statement that lists `host.docker.internal` as an unconditional
     direct exception. Allowed ports remain direct and denied ports have no Tor
     fallback.
8. `docs/onyx_patch_info.md`
   - update the Compose-wrapper/network-policy description. This is not a new
     Onyx runtime patch.
9. `docs/onyx_patches_upgrade.md`
   - add the setting and its upstream-proxy independence to the routing and
     Compose audit checklist so a future proxy or Onyx upgrade cannot restore
     unrestricted host access.
10. `docs/resource_minimization.md`
    - remove the foreground/operator-listener lifecycle description;
    - document automatic bundled-full ownership, custom transition ordering,
      one shared cross-platform manager for the MLX proxy and Podman document
      server, in-memory-only MLX child ownership, and the fail-closed orphan
      port behavior;
    - distinguish manual recovery for an untracked top-level proxy on 3210
      from an orphaned child on 3211;
    - document the host bridge's dependency restart and fresh startup-health
      evaluation when the policy proxy is recreated; and
    - update deterministic lifecycle coverage to include exact-host policy,
      normal live-proxy child cleanup, and refusal to signal an orphan after a
      proxy crash.
11. `AGENTS.md`
    - update the orientation bullets that currently describe exact
      `host.docker.internal` as a default exception, and mention operator
      default `none` plus the bundled-full automatic 3210 exception without
      moving detailed semantics out of the subsystem docs.
    - remove `make embedserv-serve` from the command inventory and retain only
      install, verification, and automatic `make up-full` lifecycle guidance.

After editing, search active documentation and configuration for every
`host.docker.internal` occurrence and revise claims affected by this policy.
Historical implemented/deferred plan documents should remain historical unless
they are presented as current operational authority; do not mechanically
rewrite archived design records.

## Completion criteria

Implementation is complete only when:

- the operator default is exactly `none`;
- lite mode and custom-embedding full mode have no automatic host port;
- bundled-MLX full mode automatically and visibly adds only port 3210, using
  the same exact-URL selection that launches the wrapper-owned lifecycle proxy;
- automatic 3210 remains a property of the running policy configuration until
  that container is recreated or removed and is not described as following
  later MLX process liveness;
- a listener already present without a matching ownership record is rejected
  at bundled-start preflight before automatic 3210 reaches a newly created
  policy proxy, without claiming that PID identity plus port readiness
  authenticates later socket ownership;
- `make embedserv-serve` and generic untracked-listener acceptance are removed;
  the supported bundled flow is install, optional verification, and
  wrapper-owned automatic full startup;
- the shared settings reader accepts the embedding URL, MLX model, and served
  model with their documented precedence and literal-dollar preservation; one
  canonical effective set drives Compose, install, verification, automatic
  start, mode, and automatic-policy selection without recipes re-sourcing the
  file, while top-level stop remains configuration-independent;
- malformed accepted settings prevent direct `embedserv-install` and
  `embedserv-verify-model` recipes from executing through the shared preflight;
- bundled-to-custom transition tests prove automatic 3210 is revoked through a
  healthy staged policy path and replacement readiness succeeds before
  the shared manager stops the recorded live MLX proxy, while either failure
  leaves the old proxy intact;
- the same cross-platform `host_process_manager.py`
  PID/token/configuration/readiness contract owns both the top-level MLX proxy
  and Podman document server, with no MLX-specific ownership protocol;
- a live MLX proxy owns and stops only its in-memory `Popen` child, while an
  orphaned loopback child after a proxy crash is never identified or signaled
  automatically and makes the next bundled start fail before port 3210 binds;
- diagnostics and documentation distinguish an untracked top-level listener
  on 3210 from an orphaned child on 3211 without recommending automatic
  signaling;
- `all`, `none`, and valid numeric lists behave exactly as specified;
- keywords require exact lowercase spelling, while numeric lists accept only
  the documented ASCII whitespace and digit grammar;
- invalid configuration prevents the final-hop proxy from listening and makes
  normal lite/full stack bring-up exit nonzero through dependency restart,
  freshly evaluated bridge health, and the bounded-wait contract, including
  when the prior bridge was already healthy;
- a compromised host-route caller cannot make the proxy resolve or connect to
  an unlisted port through the exact `host.docker.internal` destination
  exception;
- denied host ports have no upstream, VPN, Tor, DNS, or direct fallback;
- allowed host ports retain current pinned direct routing;
- CONNECT and absolute-form HTTP handler tests prove that normalized hostnames,
  explicit and implicit ports, status mapping, and the shared allow decision
  remain connected;
- explicit port zero is preserved and rejected for CONNECT, origin-form
  `Host`, absolute-form HTTP, and every supported configured upstream-proxy
  scheme, while omitted ports still receive protocol defaults;
- startup logs render the effective parsed policy canonically without echoing
  raw invalid input;
- positive and negative request-path tests prove that an explicitly configured
  `EGRESS_UPSTREAM_PROXY_URL=socks5h://host.docker.internal:9150` remains
  usable as routing infrastructure when the allowed-port policy is `none`,
  while an ordinary target of the same authority is denied with and without
  that upstream setting;
- doc-drop and RFC1918 policies remain independent;
- the exact saved-SSRF-level matrices for MCP and Web Connector routing remain
  distinct, documented, and covered across all four levels;
- documentation clearly states that the separate RFC1918 opt-in can retain a
  host-gateway-IP path and that allowed ports do not authenticate a host
  service;
- public/browser/executor routes gain no host destination access;
- Docker and Podman effective models preserve the existing topology, every
  supported provider passes the fresh and warm invalid-policy gate, and a
  provider lacking dependency restart propagation uses the explicit
  Make-selected bridge-recreation phase;
- the empty upstream-proxy Compose overlay and its Make suffix are removed
  without changing effective final-hop or Myst proxy configuration, and the
  unused `_blocked_destination_reason()` wrapper is absent;
- shared implementation remains portable across Linux and macOS, the full
  custom-embedding stack is validated on Linux, the optional MLX server remains
  the sole Mac-only exception, and genuinely platform-specific compatibility
  tests are explicitly platform-gated rather than embedded in enforcement or
  lifecycle logic;
- required deterministic and feasible live validation passes, with any
  credential-, model-, or engine-dependent omission stated precisely; and
- all current user-facing, security, routing, RAG, Podman, upgrade, and agent
  orientation documentation agrees with the implemented behavior.
