# Private Onyx.App Docker Compose Set

This stack gets you a private deep research agent with code sub-agents and RAG document search, via a responsive web interface that you can access from anywhere.

The stack is built around [Onyx](https://github.com/onyx-dot-app/onyx) using [teep](https://github.com/13rac1/teep) for private verified LLM inference.

Search and web traffic is fetched via customized [SearXNG](https://github.com/searxng/searxng) and [fastCRW](https://github.com/us/crw) services connected to [Obscura Browser](https://github.com/h4ckf0r0day/obscura).

Agent web traffic uses the selected [Mysterium](https://github.com/mysteriumnetwork/node), upstream-proxy, or explicit no-VPN routing mode. Proxy support is available either in addition to Mysterium or instead of it.

[Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel) integration allows you to access the instance remotely from anywhere.

## Private Deep Research, RAG, and Code Agent Support

The main reason I created this stack is because none of the private chat providers offer a "Deep Research Mode" (aka multi-agent multi-round research report generation), and I didn't like going back to non-private chat providers when I needed this functionality.

Additionally, the full mode of Onyx provides RAG search results to the agent from local collection of PDFs and other documents, and has a Code Agent tool that allows the chat agent to spawn multiple sub-agents to clone and investigate git repositories. Onyx has many other connectors as well.

If you do not need intense multi-agent deep research and RAG functionality, your best option is [TinFoil](https://tinfoil.sh), which has an excellent [security architecture](https://tinfoil.sh/security-and-privacy-faq) and decent cross-device app support, with encrypted syncing of chats.

## Components

The Docker Compose files in this stack relies on the following components:

1. [Onyx](https://github.com/onyx-dot-app/onyx) provides a [top-ranking Deep Research Agent](https://huggingface.co/spaces/muset-ai/DeepResearch-Bench-Leaderboard), with a decent web interface and comprehensive connector and RAG-based local document search support. While other open source deep research agents rank slightly higher than Onyx, it is the only provider-neutral option with a complete user interface.

2. [Teep](https://github.com/13rac1/teep) provides private verified LLM inference via a local OpenAI-compatible proxy on port 8337. Teep supports [multiple private inference providers](https://github.com/13rac1/teep#supported-providers), and verifies attestation, encryption, and remote runtime properties before requests are allowed to proceed.

3. [Mysterium](https://github.com/mysteriumnetwork/node) is a Wireguard dVPN that accepts cryptocurrency payment and has a large pool of residential endpoints. The use of residential IP addresses reduces the rate of captchas and rate limiting by search engines and websites. Mysterium server-side code is open source and contains no centralized data retention. No comparable Zero Data Retention options are available to end-users. (Firecrawl, Exa, and Brave retain all user API activity and do not offer ZDR to consumers).

4. [Obscura](https://github.com/h4ckf0r0day/obscura) is combined with [crw](https://github.com/us/crw) to provide the Onyx agent with an actual headless browser with anti-fingerprinting defenses, as a Firecrawl-compatible API endpoint. This helps reduce fingerprint-based bans by search engines and pages that CRW renders through the browser. Ordinary non-search `open_url` pages may be returned from CRW's HTTP prefetch without Obscura when the HTTP result is usable; see [`docs/request_handling.md`](docs/request_handling.md#known-limitations). CRW, SearXNG, and both Obscura processes run on narrow internal networks; their internet traffic crosses local bridges to destination-validating final-hop proxies in the selected Myst/proxy/no-VPN routing namespace.

5. [SearXNG](https://github.com/searxng/searxng) is an open source meta-search engine that provides API search for multiple back ends. The Google, Brave, DuckDuckGo, Startpage, and Bing web engines are wrapper-provided CRW-backed variants that use the Obscura+crw instance to fetch their search results, which significantly reduces captchas and bans by these search engines.

6. [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel) is a free service that creates a reverse proxy to the Onyx web interface. The TLS key is generated locally and signed with Let's Encrypt. This means that Tailscale's infrastructure is unable to read the contents of your remote communications to the instance.

## Prerequisites

- Docker or Podman
- Internet access for image builds and provider APIs
- `make`

## Running the Stack

The stack comes in two flavors: lite and full. This specifies the mode of the Onyx app. Lite mode provides Chat, Web, and Research only. Full mode also provides RAG, external app connectors, and groupware. Lite mode uses significantly less RAM (~1GB vs ~20GB).

It is possible to switch between full and lite modes between restarts.

All persistent data is bind-mounted to subdirectories in `./docker-data`

### Lite Mode

Build and run lite mode:

```bash
make up-lite
```

Stop all lite containers:

```bash
make down-lite
```

Status/logs:

```bash
make ps-lite
make logs-lite
```

### Full Mode

Build and run full mode:

```bash
make up-full
```

Stop all full containers:

```bash
make down-full
```

Status/logs:

```bash
make ps-full
make logs-full
```

## First-run configuration

The first time you run the stack, you need to do some configuration of the .env and of the Myst VPN, before the Onyx WebUI will start.

### Configure Environment

Edit `.env.wrapper` as needed, based on the `.env.wrapper.example` template.

Most likely variables you want to change:

- Container engine selection:
  - Set `CONTAINER_BIN` to the docker or podman executable you want the wrapper to use.
  - In podman mode, the wrapper also applies `docker-compose.podman.yml`, which disables `code-interpreter` and `autoheal` by default because they require a functional Docker daemon socket inside containers.
- Teep LLM Provider/API config:
  - Set at least one teep key (for example `TEEP_NEARAI_API_KEY`, `TEEP_TINFOIL_API_KEY`)
- **Master VPN switch**:
  - Set `MYST_VPN_ENABLED=false` to run the entire stack without the Mysterium VPN. The `myst-client` container still starts and joins the shared `netns-holder` namespace (so all service wiring stays identical), but it idles the daemon without arming the kill-switch or attempting to connect. Traffic egresses directly via the Docker bridge. This skips the Myst wallet/funding requirement entirely.
  - For the full routing matrix, namespace layout, and proxy behavior, see [`docs/vpn_routing_and_proxies.md`](docs/vpn_routing_and_proxies.md).
- **Optional LAN access** (for local inference APIs):
  - Set `MYST_VPN_ALLOW_LAN_BYPASS=true` to allow access to local network addresses (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) without routing through the VPN. Useful for accessing LLMs, embedding servers, or MCP servers running on your host or LAN while maintaining fail-closed behavior for all other traffic. Default: `false`
  - If you want MCP servers on `host.docker.internal` and the doc-drop connector at `http://localhost:8091/`, use `ONYX_SECURITY_SSRF_VALIDATE_OPEN_URL=true`, `ONYX_SECURITY_SSRF_ALLOW_PRIVATE_NETWORK=true`, and `ONYX_SECURITY_SSRF_ALLOW_LOOPBACK=false`. That yields `Allow Private Network`: Web connectors can crawl local/private targets, and MCP/OAuth endpoints can use private LAN or `host.docker.internal` addresses, while loopback MCP/OAuth targets such as `127.0.0.1` remain blocked.
  - Set `ONYX_SECURITY_SSRF_ALLOW_LOOPBACK=true` only when you intentionally need loopback MCP/OAuth access. In Onyx v4.2.5 this seeds SSRF `Disabled`, which is broader than the wrapper default.

### Initial VPN Connection (Myst Payment)

> **Skip this section entirely if `MYST_VPN_ENABLED=false`** in your `.env.wrapper`. When the VPN is disabled, no wallet, identity, or payment is required — `make up-lite` / `make up-full` will proceed directly to starting the stack.

The Mysterium VPN requires a funded wallet (paid in cryptocurrency) before it can connect. The signup process is handled by a standalone container that creates a cryptographic identity and registers it on-chain (Mysterium sponsors the gas fees).

Note that because our usage of Mysterium is crypto-native, a normal Mysterium VPN app subscription won't work here. However, the good news is that crypto-native Mysterium is *considerably* cheaper than the app subscription fee, especially since this agent does not use much data, and there is no monthly fee or funds expiration. I've used less than 10 $MYST ($2 USD) in actual VPN fees since I started this project.

There are two ways to fund the wallet:

- **Option A — Order page (CoinGate):** Pay via a crypto payment gateway. Easiest for first-time users, but requires email, name, and address.
- **Option B — Direct blockchain transfer:** Transfer $MYST directly on Polygon. Cheaper (no gateway fees), but requires acquiring $MYST yourself.

Both options use the same standalone container and produce the same identity/keystore. You only need to run one.

#### Option A: CoinGate $MYST Order (Requires email, Name, and Address)

**Step 1: Run the signup process**

```bash
make vpn-signup-orderform
```

This launches a standalone Myst container, creates a new identity, registers it, and creates a CoinGate payment order. The payment URL is displayed in a banner:

```
═══════════════════════════════════════════════════════════
PAYMENT URL: https://coingate.com/pay/invoice/abc123...
═══════════════════════════════════════════════════════════
```

The default order is for 100 $MYST (currently ~$20 USD), payable via CoinGate in several major cryptocurrencies. An email is required by the payment gateway. You can customize the order amount, currency, and gateway via `MYST_VPN_ORDER_*` variables in `.env.wrapper`.

**Step 2: Pay at the URL**

Open the payment URL in a browser and complete the cryptocurrency payment.

**Step 3: Check payment status**

```bash
make vpn-orderstatus
```

This shows your identity, balance, registration status, and all orders with their payment status. For unpaid orders, the payment URL is displayed again. Repeat until your balance is non-zero. For a quick balance check:

```bash
make vpn-balance
```

**Step 4: Start the full stack**

```bash
make up-lite   # or make up-full
```

This automatically stops the standalone signup container (your wallet data is preserved) and starts the full stack. If no Myst identity is found, it will tell you to run `make vpn-signup-orderform` or `make vpn-signup-blockchain` first.

**Notes:**

- If the payment order expires before you pay, just run `make vpn-signup-orderform` again. It reuses your existing identity and creates a new order.
- The container build process may take some time to build all components on first run. Makefile dependency checks are used by `make up-lite` (or `make up-full`) to build images on first run, but not after the images exist.
- Mysterium residential providers can be flaky. Once you find one that works well, you may want to pin its identity via `MYST_VPN_PREFERRED_PROVIDER_IDS` in `.env.wrapper`. Multiple providers can be listed, separated by commas.
- The payment URL is also printed in the container logs as a fallback: `docker logs myst-client-vpn 2>&1 | grep PAYMENT_URL`

#### Option B: Direct $MYST Transfer (Skip the Order Page)

You can fund your wallet by transferring $MYST tokens directly on-chain, bypassing the CoinGate order page entirely. This is cheaper (no gateway fees) and works with any wallet or exchange that supports Polygon ERC-20 transfers.

**Important: Do NOT send $MYST to your identity address.** The Mysterium node tracks balance on a deterministic **consumer channel** contract, not the raw ERC-20 balance of your identity. Sending tokens to the identity address will not credit your balance and the funds will be stuck.

**Chain:** Polygon Mainnet (Chain ID 137). The default mainnet chain is Polygon, as defined in the Mysterium node metadata (`DefaultChainID: 137`). Ethereum Mainnet (Chain ID 1) is also supported by the node, but the default consumer flow uses Polygon.

**Step 1: Run the blockchain signup**

```bash
make vpn-signup-blockchain
```

This launches the same standalone Myst container as `make vpn-signup-orderform`, creates a new identity (or reuses an existing one), registers it on-chain (Mysterium sponsors the gas fees), and prints your **channel address** — the address you must send $MYST to. No payment order is created.

The output will look like:

```
═══════════════════════════════════════════════════════════
DIRECT TRANSFER INSTRUCTIONS
═══════════════════════════════════════════════════════════

  Chain:           Polygon Mainnet (Chain ID 137)
  MYST token:      0x1379e8886a944d2d9d440b3d88df536aea08d9f3
  Send $MYST to:   0x<your-channel-address>

  ⚠  Do NOT send to your identity address (0x<your-identity>).
     The node tracks balance on the channel contract, not the identity.
     Sending to the identity address will lose your funds.
═══════════════════════════════════════════════════════════
```

The channel address is a CREATE2-derived proxy contract address, computed from your identity, the active Hermes address, the registry, and the channel implementation contract. You can also retrieve it later with:

```bash
make vpn-balance
```

Look for the `Channel Address` field.

**Step 2: Transfer $MYST on Polygon**

From any wallet or exchange that supports Polygon, send $MYST (ERC-20, contract `0x1379e8886a944d2d9d440b3d88df536aea08d9f3`) to your **channel address** (not your identity address). You will also need a small amount of $POL (formerly $MATIC) for gas if sending from your own wallet (exchange withdrawals handle gas on their end).

$MYST can be acquired on:

- QuickSwap (Polygon)
- Uniswap V3 (Ethereum, then bridge to Polygon)
- PancakeSwap (Binance Smart Chain, then bridge to Polygon)
- MEXC, HitBTC (centralized exchanges)

If you hold $MYST on Ethereum, use the [Mysterium bridge](https://help.mystnodes.com/en/articles/8004220-bridge-your-myst-tokens-from-ethereum-to-polygon) to move it to Polygon first.

**Step 3: Verify the balance**

```bash
make vpn-balance
```

The node polls the on-chain channel balance and will reflect the transfer once the Polygon block confirms. If the balance does not update, run `make vpn-orderstatus` to trigger a resync, or restart the container with `make vpn-signup-blockchain` (which reuses your existing identity).

## Onyx UI Configuration

Once Mysterium VPN successfully connects, Onyx will need to be configured to use teep via its [Web-based Admin Interface](http://localhost:3000/admin/configuration/language-models). Select the **OpenAI-Compatible** provider type for teep. This is important for GLM-5.2 and Kimi-K2.6 reasoning models: the wrapper's Onyx patches preserve active-turn assistant reasoning as OpenAI-compatible `reasoning_content`/`reasoning` fields by default, and the OpenAI-Compatible provider keeps teep's raw model IDs on that request path. Set `ONYX_AGENT_PRESERVE_ALL_REASONING=true` only if you want to carry reasoning from older turns too. The BiFrost provider is also compatible, but OpenAI-Compatible is the recommended teep selection. The URL depends on your `TEEP_ROUTE_THROUGH_MYST_VPN` setting:

- **Default (`TEEP_ROUTE_THROUGH_MYST_VPN=false`):** Use `http://teep:8337/v1` (Docker DNS resolves the teep service on the default network).
- **VPN-routed (`TEEP_ROUTE_THROUGH_MYST_VPN=true`):** Use `http://127.0.0.1:8337/v1` (shared loopback in the VPN namespace).

The models supported by your API key from `.env.wrapper` should then be listed if you refresh the dropdown. Use teep's exact model ID as listed; provider catalogs may spell GLM and Kimi model names with different dots, dashes, or compressed forms, and the OpenAI-Compatible path avoids LiteLLM native-provider remapping.

### Inference Provider Recommendations

The best privacy preserving providers supported by teep are currently `neardirect` and `tinfoil_v3_direct`, which are the direct completions version of [NearAI](https://cloud.near.ai) and [Tinfoil.sh](https://tinfoil.sh), respectively. NearAI is also useful in that it can be paid in cryptocurrency.

This stack can also be used with LMStudio or any other local LLM provider.  Simply use `host.docker.internal` to connect to your localhost instance, using the Onyx Admin UI configuration.

If the local provider is running on a private/LAN address, you will usually also want `MYST_VPN_ALLOW_LAN_BYPASS=true` so traffic can bypass the Myst VPN firewall to reach your host or LAN service.

### LLM recommendations

Verifiable private inference is only currently possible with Open Weight models. While it is [technically possible](https://www.anthropic.com/research/confidential-inference-trusted-vms) for closed weight models to support attestation-based verification, proprietary LLM labs [do not seem to be interested](https://www.anthropic.com/news/activating-asl3-protections) in offering privacy to end users.

For a research agent like Onyx, the primary desirable property is a low hallucination rate. The [Artificial Analysis Omniscience Index](https://artificialanalysis.ai/evaluations/omniscience#aa-omniscience-hallucination-rate) provides a [Hallucination Rate benchmark](https://artificialanalysis.ai/evaluations/omniscience#aa-omniscience-hallucination-rate) that is worth tracking for this purpose.

Among Open Weight models currently supported by NearAI and Tinfoil, GLM-5.2 is the best option for text, and Kimi-K2.6 is the best multimodal option. Configure these through teep with Onyx's OpenAI-Compatible provider type so active-turn reasoning fields continue across tool-using turns.

### Web Search Provider Configuration

To make use of the provided crw and obscura anti-fingerprinting web search tools, you will need to select SearXNG and Firecrawl from the [Web Search Admin Panel](http://localhost:3000/admin/configuration/web-search):

For a detailed request-flow map of Web Search, `open_url`, SearXNG, CRW, the CDP shim, and Obscura, see [`docs/request_handling.md`](docs/request_handling.md).

1. Go to **Admin Panel -> Web Search -> Web Crawler**.
2. Open **SearXNG** and click **Connect**
3. Set the **SearXNG Base URL** to `http://searxng-service-gateway:8888`.
4. Open **Firecrawl** and click **Connect**.
5. Set **API Base URL** to `http://crw-service-gateway:3010/v1/scrape`.
6. Set **API Key** to any non-empty placeholder value.
7. Click **Connect**, then **Set as Default** on the Firecrawl card.

Existing deployments must replace previously saved localhost URLs with these
gateway URLs after upgrading; saved Onyx Admin values are not rewritten by
Compose.

## Optional Configurations

The following sections detail additional optional feature configuration, including remote access via TailScale Funnel, and RAG document search.

### Optional: Tailscale Funnel

You can publish the Onyx WebUI through Tailscale Funnel to access it remotely via any web browser. The WebUI is responsive and works fine on phones and tablets.

To set this up, in `.env.wrapper`, set `TAILSCALE_FUNNEL_ENABLED=true` and set `TAILSCALE_FUNNEL_AUTHKEY` using a free auth key created at [Tailscale Keys Settings](https://login.tailscale.com/admin/settings/keys).

- Public endpoint: `https://onyx.your-tailnet.ts.net` on port 443
- By default, the tailscale service does not route through Mysterium VPN, to avoid linking your tailscale account to your search actvity at the Myst VPN exit server.
- Tailscale uses the userspace networking mode, so no VPN activity is involved.
- To route Tailscale through the VPN namespace instead, set `TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN=true` in `.env.wrapper`. **Warning:** this links your Tailscale identity to the VPN exit IP.

Tailscale Funnel prerequisites:

- MagicDNS enabled
- HTTPS certificates enabled for your tailnet
- Funnel node attribute enabled for your user/device in ACL policy

Bring the stack up as usual (`make up-lite` or `make up-full`).

Disable by setting `TAILSCALE_FUNNEL_ENABLED=false` and restarting. The service will remain idle and will not publish Funnel routes.

### Optional: VPN Routing for Teep, Tailscale, and Code-Interpreter

By default, **teep** and **tailscale** run on the default Docker network without VPN routing. This means:

- Teep LLM API traffic egresses directly (not through Mysterium).
- Tailscale Funnel traffic egresses directly, keeping your Tailscale identity separate from the VPN exit IP.

You can optionally route either or both services through the Mysterium VPN namespace by setting these variables in `.env.wrapper`:

```bash
# Route teep LLM proxy traffic through the VPN
TEEP_ROUTE_THROUGH_MYST_VPN=true

# Route Tailscale Funnel through the VPN
# WARNING: This links your Tailscale identity to the VPN exit IP
TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN=true
```

The Makefile conditionally applies `docker-compose.teep-vpn.yml` and/or `docker-compose.tailscale-vpn.yml` override files when these are set to `true`. These override files properly adjust host and port mappings of teep and tailscale for the VPN interface. Restart the stack after changing these settings.

#### Optional: Network Access for the Code-Interpreter

By default, Onyx's code-interpreter (the `onyxdotapp/code-interpreter` image from [onyx-dot-app/python-sandbox](https://github.com/onyx-dot-app/python-sandbox)) hardcodes `--network none` on every executor pod it spawns. This means the Python tool and coding-agent bash sessions have **zero network access** — the LLM-generated code cannot make any outbound requests. This is a security isolation measure baked into the upstream image.

You can optionally give executor pods restricted proxy-only access by setting:

```bash
# Give code-interpreter executor pods network access
# Executors remain isolated from stack, host, LAN, and direct internet routes.
ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true
```

Executor pods join only the internal `onyx-code-interpreter-executor` network.
Their sole peer is an HTTP proxy bridge; raw direct connections have no route
to the internet, Onyx services, CRW, SearXNG, Obscura, Docker host gateway,
LAN/private ranges, or metadata addresses. The final-hop proxy blocks direct
search-engine URLs and preserves the selected VPN/upstream-proxy/no-VPN mode.
Tool descriptions advertise this restricted capability and do not expose
stack-local service endpoints.

Restart the stack after changing this setting (`make down-lite && make up-lite`, or the full-mode equivalents).

### Optional: Outbound Proxy (`ONYX_AGENT_OUTBOUND_PROXY_URL`)

Set `ONYX_AGENT_OUTBOUND_PROXY_URL` in `.env.wrapper` to give the final-hop policy proxies an upstream proxy. Restricted components continue to use local bridge URLs. This is orthogonal to Mysterium: if both are set, the upstream-proxy connection crosses the VPN.

Supported schemes:

```bash
# HTTP proxy
ONYX_AGENT_OUTBOUND_PROXY_URL="http://user:pass@proxy.example.com:8080"

# HTTPS proxy
ONYX_AGENT_OUTBOUND_PROXY_URL="https://user:pass@proxy.example.com:8443"

# SOCKS5 proxy
ONYX_AGENT_OUTBOUND_PROXY_URL="socks5://proxy.example.com:1080"

# SOCKS5 with remote DNS resolution
ONYX_AGENT_OUTBOUND_PROXY_URL="socks5h://proxy.example.com:1080"
```

The local policy proxies block private/internal literals, all
`*.docker.internal` names, known legacy Docker Desktop host/gateway names, and
single-label Docker service/container names. These built-in blocks cannot be
removed by configuration. With an upstream proxy, target DNS remains remote to
avoid DNS leakage, so a public-looking hostname that the upstream resolves to a
private address is a residual risk. Host Tor
(`socks5h://host.docker.internal:9150`) also currently requires
`MYST_VPN_ALLOW_LAN_BYPASS=true`; that is a broad LAN route exemption for the
trusted final-hop proxy, not general LAN access for restricted components.

Without an upstream proxy, Myst mode queries the provider's DNS resolver
directly through the tunnel and pins connections to the validated answer;
Docker's embedded resolver does not receive browsing target names. Explicit
no-VPN mode uses system/Docker DNS. With an upstream proxy, target hostnames
are sent only through HTTP CONNECT, SOCKS, or absolute-form proxy requests.
Only the upstream proxy endpoint itself may need bootstrap resolution.

Invalid upstream proxy URLs fail policy-proxy startup. Startup logs redact
userinfo and show only the configured scheme, host, and port.

The default SearXNG engines (`google2`, `brave2`, `duckduckgo2`, `startpage2`, `bing2`) POST only to `http://crw:3010/v1/scrape` on the internal search network. SearXNG has no general internet route. Intentionally enabling external engines requires a separately reviewed `searxng-external` proxy policy.

### Optional: Local Document RAG via Web Connector

The full version of Onyx supports search and retrieval (RAG) over PDF, DOC, EPUB, and other document types.

For implementation details, troubleshooting notes, and Onyx upgrade assumptions, see [`docs/local_docs_rag_search.md`](docs/local_docs_rag_search.md).

Setup steps:

1. Put PDFs into `ONYX_RAG_DOC_SOURCE_DIR` (default `./doc-drop`).
2. Start or restart full stack: `make up-full`.
3. In Onyx Admin → Connectors → Web, create a connector.
4. Set Web connector type to **Recursive**.
5. Set URL to `http://localhost:8091/` (or `http://localhost:<HOST_PORT_ONYX_RAG_DOC_WEB>/`).
6. Sync the connector.

Notes:

- Directory listing pages are crawlable; you can also target specific files
  directly, e.g. `http://localhost:8091/my-paper.pdf`.
- Onyx v4.2.5 has SSRF Protection that can block this service if you save a
  Security Hardening override in the Admin UI.
- The defaults in `.env.wrapper.example` seed the `Allow Private Network` posture
  in the Security Hardening UI. That is enough for this Web connector because
  Onyx only enforces Web connector SSRF checks at strict `Validate All`.
- If you already saved a different value in Onyx Admin -> Security Hardening,
  that saved setting takes precedence. For doc-drop crawling, avoid the strict
  `Validate All` setting.

### Optional: Running a Local Embedding Model Server (Mac)

If you are on a Mac, the makefile has rules that can install [Harrier-oss-v1-0.6b](https://huggingface.co/microsoft/harrier-oss-v1-0.6b), which is a SOTA open weight embedding model:

```sh
# Install mlx-openai-server and mlx-embeddings in ./embedserv using `uv`
make embedserv-install
# Verify the model was downloaded correctly
make embedserv-verify-model
# Launch the embedding server
make embedserv-serve
```

These rules use `mlx-embeddings` because llama.cpp embeddings support is very buggy (including many subtle accuracy drift bugs), and LM Studio's is non-existent.

You must also set `MYST_VPN_ALLOW_LAN_BYPASS=true` in `.env.wrapper`, so traffic can bypass the Myst VPN firewall to reach this embedding service.

### Optional: Using Teep for Embeddings

If you are not on a Mac, your best bet is to use `Qwen/Qwen3-Embedding-0.6B` with the `neardirect` provider.

TODO: Document this in more detail

### Optional: Local/Custom Embedding Model Configuration

Properly configuring an open-weight frontier embedding model for RAG is a minefield. Almost no one gets it right, including Onyx and LiteLLM. Do **not** configure embeddings through LiteLLM.

Frontier embedding models require an instruction prefix when generating queries, but unfortunately, Onyx has an issue with handling this prefix for generic LLM providers. To address this, the stack contains a local shim which allows you to set the query prefix via an environment instead. The prefixes in `.env.wrapper.example` should be good for either Harrier or Qwen3.

To use this shim:

1. Go to [Onyx Admin Index Settings](http://localhost:3000/admin/configuration/index-settings)
2. Select your embedding model as **Self-Hosted / Custom Model** (local)
3. Enter `nomic-ai/nomic-embed-text-v23` as the model type (Onyx has special hardcoded features for nomic-ai...)
4. For both `Harrier-OSS-V1-0.6B` and `Qwen3-Embedding-0.6B`, the embedding dimension is 1024.

### Optional: Obscura MCP Server for Chat Agent Browser Automation

In addition to the stealth Obscura browser that the crw Firecrawl API uses, a second obscura instance runs as a [Model Context Protocol (MCP)](https://modelcontextprotocol.io) HTTP server by default. This exposes obscura's stealth browser automation tools directly to Onyx chat agents — they can navigate pages, take snapshots, click elements, fill forms, extract content, and more, all through the MCP tool interface.

The MCP server has separate control and egress networks. Onyx reaches its
unauthenticated listener only through `obscura-mcp-gateway`; browser traffic
uses a search-allowed, private-target-blocking final-hop policy. The bundled
server does not require disabling Onyx SSRF protection globally.

**Step 1: Configure the MCP server in Onyx**

1. Go to **Admin Panel -> MCP Servers** ([http://localhost:3000/admin/mcp-servers](http://localhost:3000/admin/mcp-servers)).
2. Click **Add MCP Server**.
3. Set **Name** to `obscura` (or any name you prefer).
4. Set **Server URL** to `http://obscura-mcp-gateway:9223/mcp`.
5. Set **Auth Type** to **None** (the gateway is reachable only from the Onyx-side ingress network).
6. Click **Save**, then click **Discover Tools** to verify the connection.

**Step 2: Assign the MCP tools to an Assistant**

0. **RELOAD THE ONYX Admin WebUI**. After MCP config change, the Admin WebUI needs to be updated with the proper set of tool lists.
1. Go to **Admin Panel -> Assistants** and edit an assistant (or create a new one).
2. Under **MCP Servers**, select the `obscura` server.
3. Select which tools to expose (or select all).
4. Save the assistant.

Chat agents using that assistant can drive a stealth browser to navigate, read,
and interact with web pages. Browser traffic follows the configured
Mysterium/upstream-proxy/no-VPN routing matrix.

**Security notes:**

- The MCP HTTP transport has no built-in auth. Only the narrow Onyx-side gateway reaches its control network; it is not host-published or executor-accessible.
- The browser session is shared across all tool calls within a single MCP server instance. Multiple concurrent chat sessions share the same browser state.
- See the [obscura MCP documentation](https://github.com/h4ckf0r0day/obscura/blob/main/docs/Use-the-MCP-server.md) for full details.

## Docker Host Endpoints

The following endpoints are exposed to your docker host:

- Onyx WebUI: [`http://localhost:3000`](http://localhost:3000)
- SearxNG WebUI: [`http://localhost:8080`](http://localhost:8080)
- teep human-readable stats page: [`http://localhost:8337`](http://localhost:8337)
- teep OpenAI API base: `http://localhost:8337/v1`
- teep health check: `http://localhost:8337/health`
- teep prometheus metrics: `http://localhost:8337/metrics`

## Upgrading the Stack

This wrapper carries a few local Onyx runtime and install-time patches. For the rationale behind those patches, see [`docs/onyx_patch_info.md`](docs/onyx_patch_info.md).

Image tags, source refs, and runtime Python lock files are consolidated in [`stack.versions.env`](stack.versions.env) plus the committed `requirements.in` / hashed `requirements.txt` files. `make upgrade` refreshes the Python locks through `make upgrade-python-deps` before rebuilding/pulling the stack components. When changing those pins or rebasing onto a new Onyx release, use [`docs/onyx_patches_upgrade.md`](docs/onyx_patches_upgrade.md).

## Privacy of this stack

This stack should be regarded as a proof-of-concept. It will keep your LLM queries and the resultant search
activity out of the hands of AI companies, data aggregators, and marketers,
but single-hop VPN activity is not as strong as Tor, and may not even be as
strong as [Tinfoil's distributed trust
architecture](https://tinfoil.sh/security-and-privacy-faq#web-search).

This stack uses the host OS VPN as the "first hop"
before connecting to the Mysterium endpoint. Additionally, all inference,
search, and web traffic exiting the Mysterium VPN uses TLS or https.

### Why not just use Tor?

It may seem strange that a [Tor Project](https://www.torproject.org) employee created a private inference stack that does not support Tor usage. This was a pragmatic choice to produce something that functioned.

The reality is that many websites subject Tor and datacenter VPNs to increased captchas and bans compared to [residential IP addresses](https://acid.vegas/blog/the-shady-world-of-ip-leasing/). The most egregious example is Google's move to update [ReCaptcha to require an official Google device, while exempting "official" AI scrapers](https://www.financialexpress.com/life/technology-google-qr-captcha-controversy-explained-why-internet-is-scared-of-this-4237640/).

Until this landscape changes, residential IP address leasing is the only reliable option for a self-hosted private research agent, and Mysterium was the best choice among those, since the server side is open source, and payment is made in cryptocurrency.

To compare routing, set `ONYX_AGENT_OUTBOUND_PROXY_URL=socks5h://host.docker.internal:9150` and `MYST_VPN_ALLOW_LAN_BYPASS=true` in `.env.wrapper` to use the host Tor Browser proxy. SearXNG provides search-engine success statistics on the "Engines" tab of the [Preferences Pane](http://localhost:8080/preferences). Remember that the Myst LAN bypass is broader than the single host-proxy endpoint and upstream-proxy DNS classification has the residual risk documented above.
