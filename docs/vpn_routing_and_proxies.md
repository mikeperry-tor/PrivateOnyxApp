# VPN Routing and Proxies

This document describes the current VPN and proxy implementation in the wrapper
stack. The main controls live in `.env.wrapper`, with defaults documented in
`.env.wrapper.example`. The Makefile reads those values and builds the effective
Compose stack by layering optional override files.

Related implementation docs:

- [Request handling](request_handling.md) describes the Onyx web-search and
  `open_url` request chains through SearXNG, CRW, the CDP shim, and obscura.
- [Onyx patch information](onyx_patch_info.md) describes the runtime
  `sitecustomize` patches used by the API server, background worker, and
  code-interpreter containers.
- [Internal network security](internal_network_security.md) describes
  localhost/private-network reachability, prefetch-proxy destination
  validation, Obscura/CRW blocking behavior, and code-interpreter networking
  risks.

## Compose Layering

The wrapper does not rewrite the base Compose file at startup. `make up-lite`
and `make up-full` assemble `COMPOSE_FILE` from:

- `docker-compose.yaml`
- either `docker-compose.lite.yml` or `docker-compose.full.yml`
- optional Podman overrides
- optional routing/proxy overrides:
  - `docker-compose.teep-vpn.yml` when `TEEP_ROUTE_THROUGH_MYST_VPN=true`
  - `docker-compose.tailscale-vpn.yml` when `TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN=true`
  - `docker-compose.code-interpreter-vpn.yml` when `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`
  - `docker-compose.proxy.yml` when `ONYX_AGENT_OUTBOUND_PROXY_URL` is non-empty

This means the default path stays small: if an optional switch is unset, its
override file is not part of the Compose model.

## Shared VPN Namespace

`docker-compose.yaml` creates a long-lived `netns-holder` container. Services
that should share VPN routing use:

```yaml
network_mode: "service:netns-holder"
```

`myst-client` also joins this namespace and owns the Mysterium WireGuard tunnel
and kill-switch rules. The stable namespace owner is separate from
`myst-client` so restarting the VPN daemon does not invalidate other services'
network namespace references.

The main VPN-routed services are:

- `myst-client`
- `searxng-core`
- `obscura`
- `obscura-mcp`
- `cdp-shim`
- `prefetch-blocking-proxy`
- `crw`
- Onyx app services such as `api_server`, `web_server`, `nginx`, and
  `code-interpreter`
- full-mode extras such as `background`, `doc-drop-web`, and
  `local-embedding-shim`

Host-facing access to services inside the namespace is provided by small
`alpine/socat` bridge containers on the normal Compose network:

- `host-web-proxy` maps the host `HOST_PORT_ONYX_WEBUI` to `nginx` in the shared namespace.
- `host-searxng-proxy` maps the host `HOST_PORT_SEARXNG` to SearXNG.
- `host-doc-drop-web-proxy` exists in full mode for the local doc-drop web view.
- `host-teep-proxy` is added only when `TEEP_ROUTE_THROUGH_MYST_VPN=true`.

## Mysterium Runtime Behavior

The Mysterium image is built from `myst/build/Dockerfile`, which compiles the
configured Mysterium node source and packages it with the wrapper entrypoint
mounted from `myst/myst-client-entrypoint.sh`.

At runtime, `myst-client`:

- starts the daemon in consumer mode
- creates or reuses the first identity in `docker-data/myst-data`
- unlocks the identity
- submits on-chain registration when needed
- checks balance and payment orders when funding is needed
- optionally waits for funds when `MYST_VPN_WAIT_FOR_FUNDS=true`
- connects to a provider when `MYST_AUTO_CONNECT=true`
- keeps the Mysterium kill-switch active while the daemon runs

Provider selection is controlled by `MYST_VPN_PREFERRED_PROVIDER_IDS`, `MYST_COUNTRY`,
`MYST_LOCATION_TYPE`, and `MYST_SERVICE_TYPE`. When `MYST_VPN_PREFERRED_PROVIDER_IDS` is set,
the entrypoint tries one pinned provider per retry cycle. Otherwise it lets
Mysterium select a provider using the configured country/location filters.

`MYST_VPN_WIREGUARD_MTU` is passed to the daemon and also enforced periodically on
the live `myst0` interface. `MYST_VPN_ALLOW_LAN_BYPASS=true` appends RFC 1918 LAN CIDRs
to route exemptions so local services can be reached through the Docker bridge
while non-exempt egress remains protected by the VPN kill-switch.

## Disabling VPN Egress

`MYST_VPN_ENABLED=false` keeps the topology intact but starts `myst-client` in
idle mode:

- no kill-switch firewall is armed
- no VPN connection is attempted
- no identity, registration, funding, or provider flow runs
- the healthcheck is based on TequilAPI reachability instead of connected VPN
  status

Services join `netns-holder`, so internal addressing remains the same.
External traffic leaves directly through the Docker bridge instead of through
Mysterium.

## Optional Service VPN Routing

### Teep

By default, `teep` runs on the normal Compose network and publishes
`HOST_PORT_TEEP` directly. Its provider API traffic does not use the Mysterium
namespace. Onyx reaches it through Docker DNS at:

```text
http://teep:8337/v1
```

When `TEEP_ROUTE_THROUGH_MYST_VPN=true`, the Makefile adds
`docker-compose.teep-vpn.yml`. That override moves `teep` into
`netns-holder`, removes direct port publishing, and adds `host-teep-proxy` for
host access. In this mode, services in the shared namespace reach teep on
loopback:

```text
http://127.0.0.1:8337/v1
```

The teep image is built by `teep/build/Dockerfile`. `make teep-build` pins the
source checkout with `TEEP_REF`, which defaults to a commit SHA, and derives the
default image tag from that pin. To upgrade teep, change `TEEP_REF` to the new
commit; the wrapper will build and run a distinct image tag. Its entrypoint
defaults to `teep serve` and appends `TEEP_SERVE_ARGS` for serve mode, allowing
wrapper flags such as `--offline` without rebuilding the image.

### Tailscale Funnel

By default, `tailscale-funnel` runs on the normal Compose network, outside the
Mysterium namespace. Its entrypoint idles unless `TAILSCALE_FUNNEL_ENABLED` is
true and `TAILSCALE_FUNNEL_AUTHKEY` is set. When enabled, it uses Tailscale userspace
networking and generates a serve/funnel config that proxies HTTPS traffic to:

```text
http://host-web-proxy:3000
```

When `TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN=true`, the Makefile adds
`docker-compose.tailscale-vpn.yml`. That override moves `tailscale-funnel` into
`netns-holder` and retargets the local proxy to:

```text
http://127.0.0.1:80
```

That routes Tailscale traffic through the Mysterium namespace. It also links
the Tailscale identity's control/data-plane traffic to the VPN exit IP, so the
default is intentionally not VPN-routed.

### Code Interpreter

The `code-interpreter` service itself runs in the shared namespace by default,
but upstream executor pods default to Docker network `none`. That means the
Python tool and coding-agent bash sessions have no network access unless
explicitly enabled.

When `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`, the Makefile adds
`docker-compose.code-interpreter-vpn.yml`. That mounts
`onyx/patches/sitecustomize_code_interpreter` into the code-interpreter image
and sets `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`. The override also sets:

```text
PYTHON_EXECUTOR_DOCKER_NETWORK=container:onyx-netns-holder-1
```

The patch monkey-patches `DockerExecutor._build_run_command` only to propagate
proxy settings into executor containers.

Because the code-interpreter container already shares `netns-holder`, executor
pods inherit the shared namespace. With `MYST_VPN_ENABLED=true`, egress goes
through Mysterium; with VPN explicitly disabled, egress leaves through the
Docker bridge. This intentionally removes the upstream network isolation for
LLM-generated code.

The same override sets `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true` on `api_server`.
`onyx/patches/sitecustomize_base/wrapper_env_patches.py` then updates the
Python tool, Bash tool, and coding-agent prompt text so the model is told that
network access is available through the VPN. The patch mechanics are described
in [Onyx patch information](onyx_patch_info.md#code-interpreter-executor-networking-and-proxying).

## ONYX_AGENT_OUTBOUND_PROXY_URL

`ONYX_AGENT_OUTBOUND_PROXY_URL` is an optional upstream proxy, independent of Mysterium routing.
It accepts HTTP, HTTPS, SOCKS5, and SOCKS5h URLs, for example:

```text
ONYX_AGENT_OUTBOUND_PROXY_URL="http://user:pass@proxy.example.com:8080"
ONYX_AGENT_OUTBOUND_PROXY_URL="socks5://proxy.example.com:1080"
ONYX_AGENT_OUTBOUND_PROXY_URL="socks5h://host.docker.internal:9150"
```

When `ONYX_AGENT_OUTBOUND_PROXY_URL` is empty, `docker-compose.proxy.yml` is not applied. When it
is non-empty, the override threads the proxy through services that perform
external fetches. If those services are also in the Mysterium namespace, their
connection to the upstream proxy leaves through the VPN tunnel.

The Makefile also derives `ONYX_AGENT_OUTBOUND_PROXY_URL_RESOLVED`. If `ONYX_AGENT_OUTBOUND_PROXY_URL` contains
`host.docker.internal`, the Makefile resolves it to an IP address and passes
that resolved URL to obscura. This avoids SOCKS connectors trying to resolve
the Docker-internal hostname through the upstream SOCKS proxy.

Internal service traffic is excluded with `NO_PROXY` values covering loopback
and Docker DNS names such as `myst-client`, `api_server`, `nginx`,
`code-interpreter`, `obscura`, `crw`, and `searxng-core`.

## How ONYX_AGENT_OUTBOUND_PROXY_URL Applies by Service

### Obscura CDP Browser

`docker-compose.proxy.yml` replaces the `obscura` command with the normal
`serve` command plus:

```text
--proxy ${ONYX_AGENT_OUTBOUND_PROXY_URL_RESOLVED:-${ONYX_AGENT_OUTBOUND_PROXY_URL}}
```

This applies the upstream proxy to the stealth browser traffic used by CRW's
CDP renderer. The request-chain details for the search and `open_url` paths
are covered in [Request handling](request_handling.md).

### Obscura MCP

The same override adds the same `--proxy` flag to `obscura-mcp`. Browser
automation requests made through the MCP server therefore use the configured
upstream proxy as well.

### CRW and the Prefetch-Blocking Proxy

CRW's base environment points raw HTTP prefetch traffic at the local
`prefetch-blocking-proxy` on `127.0.0.1:3128` using `HTTP_PROXY` and
`HTTPS_PROXY`. It does not point CRW directly at `ONYX_AGENT_OUTBOUND_PROXY_URL`.

The local proxy in `crw/prefetch_blocking_proxy.py` handles CRW's prefetch
step:

- known search engine hosts receive an immediate 403 with no upstream request
- internal/private destinations receive 403 without opening any `CONNECT`,
  `HEAD`, or PDF forwarding path
- non-search URLs receive a HEAD request to detect PDFs
- PDFs are tunneled back to CRW for its PDF extraction path
- non-PDF pages receive 403 so CRW escalates to obscura/CDP

When `ONYX_AGENT_OUTBOUND_PROXY_URL` is set, `prefetch-blocking-proxy` uses it for its own HEAD
requests and PDF tunnels. That keeps any unavoidable non-browser prefetch
traffic on the same proxy path as obscura.

Destination validation applies to literal IP addresses, localhost,
`host.docker.internal`, and single-label Docker-style names without opening an
upstream connection. When `ONYX_AGENT_OUTBOUND_PROXY_URL` is empty, the proxy
also resolves target DNS names locally and blocks any name that resolves to
loopback, private/RFC1918, link-local, reserved, or otherwise non-global
addresses. When `ONYX_AGENT_OUTBOUND_PROXY_URL` is set, the proxy skips that
target DNS resolution check so target DNS is not leaked outside the configured
upstream proxy path.

The CDP shim in `crw/cdp_shim.py` sits between CRW and obscura. Among its
runtime behaviors, it strips CRW's `proxyServer` field from
`Target.createBrowserContext`, so the CDP path uses obscura's own `--proxy`
setting instead of CRW attempting to configure a per-context proxy.

### SearXNG

SearXNG does not reliably honor `HTTP_PROXY`/`HTTPS_PROXY` for its engine
requests because it builds explicit httpx transport mounts from
`outgoing.proxies` in `settings.yml`.

When `ONYX_AGENT_OUTBOUND_PROXY_URL` is set, `docker-compose.proxy.yml` replaces the SearXNG
entrypoint with `searxng/searxng-proxy-entrypoint.sh`. The wrapper copies the
mounted SearXNG config to `/tmp/searxng-proxy`, edits the copy, sets
`SEARXNG_SETTINGS_PATH` to that copy, and then execs the image's original
entrypoint. The host-side `settings.yml` bind mount is not modified.

The generated settings add:

- `outgoing.proxies.all://` pointing at `ONYX_AGENT_OUTBOUND_PROXY_URL`
- `extra_proxy_timeout: 20`
- a `direct` outgoing network with `proxies: {}`
- `network: direct` on the local CRW-backed engines (`google2`, `brave2`,
  `duckduckgo2`, `startpage2`, and `bing2`)

The direct network is needed because those engines call the local CRW API at
`http://127.0.0.1:3010`. Those loopback calls must not be sent to the upstream
proxy.

### Code Interpreter Executor Pods

When `ONYX_AGENT_OUTBOUND_PROXY_URL` is set, `docker-compose.proxy.yml` mounts the same
`sitecustomize_code_interpreter` patch into `code-interpreter` and sets
`ONYX_AGENT_OUTBOUND_PROXY_URL`, `ALL_PROXY`, and `NO_PROXY` on the service.

The patch injects proxy environment variables into every executor pod's
`docker run` command:

- for HTTP/HTTPS proxies, it injects `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`,
  lowercase variants, and `NO_PROXY`
- for SOCKS proxies, it injects `ALL_PROXY` and lowercase variants, but avoids
  `HTTP_PROXY`/`HTTPS_PROXY` because Python's `urllib` treats those as HTTP
  CONNECT proxies
- for SOCKS proxies, it creates the Docker volume `onyx-proxy-libs`, installs
  the hashed `PySocks` and `socksio` lock from
  `onyx/patches/sitecustomize_code_interpreter/proxy-libs-requirements.txt`
  into it using `PROXY_LIBS_INSTALL_IMAGE`, mounts it into executor pods at
  `/tmp/proxy-libs`, and injects `PYTHONPATH` so `requests` and `httpx` can use
  SOCKS transports

Proxy injection and VPN routing are separate code paths. With `ONYX_AGENT_OUTBOUND_PROXY_URL`
alone, executor pods receive proxy environment variables but remain
network-isolated. With both `ONYX_AGENT_OUTBOUND_PROXY_URL` and
`ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`, executor pods inherit the shared
namespace and then use the configured upstream proxy for supported tools.

## VPN Signup and Funding Flows

The Makefile exposes four Myst helper targets:

- `make vpn-signup-orderform`
- `make vpn-signup-blockchain`
- `make vpn-orderstatus`
- `make vpn-balance`

These targets operate on the standalone Myst Compose file at
`myst/docker-compose.yaml` and the persistent data directory
`docker-data/myst-data`.

### Order Form Flow

`make vpn-signup-orderform` ensures the Mysterium image exists, starts the
standalone `myst-client-vpn` container if needed, and disables automatic VPN
connection while signing up:

```text
MYST_AUTO_CONNECT=false
MYST_VPN_WAIT_FOR_FUNDS=false
```

It then runs:

```text
myst/myst-vpn-cli.sh signup
```

The helper waits for TequilAPI, creates or reuses an identity, unlocks it,
checks registration and balance, reuses an existing unpaid order if present,
or creates a new order using:

- `MYST_VPN_ORDER_AMOUNT`
- `MYST_VPN_ORDER_CURRENCY`
- `MYST_VPN_ORDER_GATEWAY`
- `MYST_VPN_ORDER_COUNTRY`
- `MYST_VPN_ORDER_GATEWAY_DATA`

The payment URL is extracted from the Myst CLI order response and printed in a
prominent banner.

### Direct Blockchain Flow

`make vpn-signup-blockchain` starts the same standalone container but also sets:

```text
MYST_SKIP_ORDER_CREATION=true
```

It runs:

```text
myst/myst-vpn-cli.sh blockchain
```

The helper creates or reuses the identity, submits registration when needed,
and prints the consumer channel address from `myst cli identities get`. The
user sends Polygon MYST to that channel address, not to the identity address.
No payment order is created in this flow.

### Status and Balance

`make vpn-orderstatus` prints the active identity, balance, registration
status, all known orders, and any payment URLs it can extract from unpaid
orders.

`make vpn-balance` prints a compact identity, balance, and registration summary.

### Starting the Main Stack

`make up-lite` and `make up-full` run `ensure-myst-funded` before starting the
main wrapper stack. If `MYST_VPN_ENABLED=false`, the check is skipped. Otherwise
the target:

- stops the standalone signup container if it is running
- preserves wallet data in `docker-data/myst-data`
- verifies that `docker-data/myst-data/keystore` contains an identity
- asks the user to run one of the signup flows if no identity exists

Funding is not destructively managed by the wrapper. The Myst daemon and helper
commands read the persisted identity and on-chain/channel balance from the
Mysterium node state.
