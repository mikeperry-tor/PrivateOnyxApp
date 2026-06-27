# Private Onyx.App Docker Compose Set

Docker Compose wrapper for running [Onyx](https://github.com/onyx-dot-app/onyx) with the [teep](https://github.com/13rac1/teep) private verified LLM inference proxy and [SearXNG](https://github.com/searxng/searxng). All search and web traffic is fetched using [Obscura](https://github.com/h4ckf0r0day/obscura) over a [Mysterium](https://github.com/mysteriumnetwork/node) VPN connection. [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel) integration allows you to access the instance remotely.

This stack gets you a private deep research agent and RAG document searching via a responsive web interface that you can access from anywhere.

## Deep Research, RAG, and Code Agent Support

The main reason I created this stack is because none of the private chat providers offer a "Deep Research Mode" (aka multi-agent multi-round research report generation), and I didn't like going back to non-private chat providers when I needed this functionality.

Additionally, the full mode of Onyx provides RAG search results to the agent from local collection of PDFs and other documents, and has a Code Agent tool that allows the chat agent to spawn multiple sub-agents to clone and investigate git repositories. Onyx has many other connectors as well.

If you do not need intense multi-agent deep research and RAG functionality, your best option is [TinFoil](https://tinfoil.sh), which has an excellent [security architecture](https://tinfoil.sh/security-and-privacy-faq) and decent cross-device app support, with encrypted syncing of chats.

## Components

The Docker Compose files in this stack relies on the following components:

1. [Onyx](https://github.com/onyx-dot-app/onyx) provides a [top-ranking Deep Research Agent](https://huggingface.co/spaces/muset-ai/DeepResearch-Bench-Leaderboard), with a decent web interface and comprehensive connector and RAG-based local document search support. While other open source deep research agents rank slightly higher than Onyx, it is the only provider-neutral option with a complete user interface.

2. [Teep](https://github.com/13rac1/teep) provides private verified LLM inference via a local OpenAI-compatible proxy on port 8337. Teep supports [multiple private inference providers](https://github.com/13rac1/teep#supported-providers), and verifies attestation, encryption, and remote runtime properties before requests are allowed to proceed.

3. [Mysterium](https://github.com/mysteriumnetwork/node) is a Wireguard dVPN that accepts cryptocurrency payment and has a large pool of residential endpoints. The use of residential IP addresses reduces the rate of captchas and rate limiting by search engines and websites. Mysterium server-side code is open source and contains no centralized data retention. No comparable Zero Data Retention options are available to end-users. (Firecrawl, Exa, and Brave retain all user API activity and do not offer ZDR to consumers).

4. [Obscura](https://github.com/h4ckf0r0day/obscura) is combined with [crw](https://github.com/us/crw) to provide the Onyx agent with an actual headless browser with anti-fingerprinting defenses, as a Firecrawl-compatible API endpoint. This helps reduce fingerprint-based bans by websites. Both run inside the shared Myst namespace so scrape/crawl traffic egresses through the VPN endpoint IP.

5. [SearXNG](https://github.com/searxng/searxng) is an open source meta-search engine that provides API search for multiple back ends. The DuckDuckGo, Brave, and Google engines of SearXNG have been [monkey-patched](https://github.com/searxng/searxng/discussions/5651) to use the Obscura+crw instance to fetch their search results, which significantly reduces captchas and bans by these search engines.

6. [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel) is a free service that creates a reverse proxy to the Onyx web interface. The TLS key is generated locally and signed with Let's Encrypt. This means that Tailscale's infrastructure is unable to read the contents of your remote communications to the instance.

## Prerequisites

- Docker or Podman
- Internet access for image builds and provider APIs
- `make`

## Running the Stack

The stack comes in two flavors: lite and full. This specifies the mode of the Onyx app. Lite mode provides Chat, Web, and Research only. Full mode also provides RAG, external app connectors, and groupware. Lite mode uses significantly less RAM (~1GB vs ~20GB).

It is possible to switch between full and lite modes.

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
  - Examples: `CONTAINER_BIN=docker` or `CONTAINER_BIN=/opt/homebrew/bin/podman`
  - The bundled installer shim exposes that executable to the upstream Onyx installer as `docker`, so local updates to `onyx/install.sh` do not need to be re-patched.
  - In podman mode, the wrapper also applies `docker-compose.podman.yml`, which disables `code-interpreter` and `autoheal` by default because they require a functional Docker daemon socket inside containers.
- Teep LLM Provider/API config:
  - Set at least one teep key (for example `NEARAI_API_KEY`, `VENICE_API_KEY`, or `CHUTES_API_KEY`)
- Optional Tailscale Funnel exposure (public HTTPS 443 -> Onyx UI):
  - Set `TAILSCALE_FUNNEL_ENABLED=true`
  - Set `TAILSCALE_AUTHKEY` using a free auth key created at [Tailscale Keys Settings](https://login.tailscale.com/admin/settings/keys).
  - Optional overrides: `TAILSCALE_HOSTNAME`, `TAILSCALE_EXTRA_ARGS`
- **Master VPN switch**:
  - Set `MYST_VPN_ENABLED=false` to run the entire stack without the Mysterium VPN. The `myst-client` container still starts and joins the shared `netns-holder` namespace (so all service wiring stays identical), but it idles the daemon without arming the kill-switch or attempting to connect. Traffic egresses directly via the Docker bridge. This skips the Myst wallet/funding requirement entirely. See [Optional: Disabling the Mysterium VPN](#optional-disabling-the-mysterium-vpn) below.
- **VPN routing for teep, tailscale, and code-interpreter**:
  - By default, teep and tailscale run on the default Docker network (no Myst VPN). Their traffic egresses directly via the docker host's networking stack (or host VPN). This is done so that your tailscale account and inference provider account are not linkable to the agent's web activity by IP address. The code interpreter has no network access as a sandboxing measure. To change this:
    - Set `TEEP_VPN_ROUTED=true` to route teep LLM API traffic through the Mysterium VPN namespace.
    - Set `TAILSCALE_VPN_ROUTED=true` to route Tailscale Funnel traffic through the VPN namespace.
    - Set `CODE_INTERPRETER_VPN_ROUTED=true` to give Onyx's Python tool and coding-agent bash sessions outbound internet access through the VPN. **Security:** this removes the code-interpreter's network isolation — the LLM can make arbitrary outbound requests from generated code.
- **Optional LAN access** (for local inference APIs):
  - Set `ALLOW_LAN_ACCESS=true` to allow access to local network addresses (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) without routing through the VPN. Useful for accessing LLMs, embedding servers, or MCP servers running on your host or LAN while maintaining fail-closed behavior for all other traffic. Default: `false`
- Optional Onyx SSRF defaults (for MCP servers and local doc-drop crawling):
  - If you want both MCP servers on `host.docker.internal` and the doc-drop connector at `http://localhost:8091/`, use `OPEN_URL_VALIDATE_SSRF=true`, `MCP_SERVER_ALLOW_PRIVATE_NETWORK=true`, and `MCP_SERVER_ALLOW_LOOPBACK=false`. That yields the `Allow Private Network` posture by default.
    - `OPEN_URL_VALIDATE_SSRF`, `MCP_SERVER_ALLOW_PRIVATE_NETWORK`, and `MCP_SERVER_ALLOW_LOOPBACK` seed Onyx's default SSRF Protection level at startup.
    - After you save a value in Onyx Admin -> Security Hardening, the saved UI setting becomes the effective runtime policy and overrides these defaults.

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

The default order is for 100 $MYST (currently ~$20 USD), payable via CoinGate in several major cryptocurrencies. An email is required by the payment gateway. You can customize the order amount, currency, and gateway via `MYST_ORDER_*` variables in `.env.wrapper`.

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
- Mysterium residential providers can be flaky. Once you find one that works well, you may want to pin its identity via `MYST_PROVIDER_IDS` in `.env.wrapper`. Multiple providers can be listed, separated by commas.
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

Once Mysterium VPN successfully connects, Onyx will need to be configured to use teep via its [Web-based Admin Interface](http://localhost:3000/admin/configuration/language-models). The BiFrost provider is compatible. The URL depends on your `TEEP_VPN_ROUTED` setting:

- **Default (`TEEP_VPN_ROUTED=false`):** Use `http://teep:8337/v1` (Docker DNS resolves the teep service on the default network).
- **VPN-routed (`TEEP_VPN_ROUTED=true`):** Use `http://127.0.0.1:8337/v1` (shared loopback in the VPN namespace).

The models supported by your API key from `.env.wrapper` should then be listed if you refresh the dropdown.

### Inference Provider Recommendations

The best privacy preserving providers supported by teep are currently `neardirect` and `tinfoil_v3_direct`, which are the direct completions version of [NearAI](https://cloud.near.ai) and [Tinfoil.sh](https://tinfoil.sh), respectively. NearAI is also useful in that it can be paid in cryptocurrency.

This stack can also be used with LMStudio or any other local LLM provider.  Simply use `host.docker.internal` to connect to your localhost instance, using the Onyx Admin UI configuration.

If the local provider is running on a private/LAN address, you will usually also want `ALLOW_LAN_ACCESS=true` so traffic can bypass the Myst VPN firewall to reach your host or LAN service.

### LLM recommendations

Verifiable private inference is only currently possible with Open Weight models. While it is [technically possible](https://www.anthropic.com/research/confidential-inference-trusted-vms) for closed weight models to support attestation-based verification, proprietary LLM labs [do not seem to be interested](https://www.anthropic.com/news/activating-asl3-protections) in offering privacy to end users.

For a research agent like Onyx, the primary desirable property is a low hallucination rate. The [Artificial Analysis Omniscience Index](https://artificialanalysis.ai/evaluations/omniscience#aa-omniscience-hallucination-rate) provides a [Hallucination Rate benchmark](https://artificialanalysis.ai/evaluations/omniscience#aa-omniscience-hallucination-rate) that is worth tracking for this purpose.

Among Open Weight models currently supported by NearAI and Tinfoil, GLM-5.2 is the best option for text, and Kimi-2.6 is the best multimodal option.

### Web Search Provider Configuration

To make use of the provided crw and obscura anti-fingerprinting web search tools, you will need to select SearXNG and Firecrawl from the [Web Search Admin Panel](http://localhost:3000/admin/configuration/web-search):

1. Go to **Admin Panel -> Web Search -> Web Crawler**.
2. Open **SearXNG** and click **Connect**
3. Set the **SearXNG Base URL** to `http://localhost:8080`.
4. Open **Firecrawl** and click **Connect**.
5. Set **API Base URL** to `http://localhost:3010/v1/scrape`.
6. Set **API Key** to the value of `CRW_ONYX_API_KEY` (default: `local-crw`).
7. Click **Connect**, then **Set as Default** on the Firecrawl card.

## Optional Configurations

The following sections detail additional optional feature configuration, including remote access via TailScale Funnel, and RAG document search.

### Optional: Tailscale Funnel

You can publish the Onyx WebUI through Tailscale Funnel to access it remotely.

- Public endpoint: `https://onyx.your-tailnet.ts.net` on port 443
- By default, the tailscale service does not route through Mysterium VPN, to avoid linking your tailscale account to your search actvity at the Myst VPN exit server.
- Tailscale uses the userspace networking mode, so no VPN activity is involved.
- To route Tailscale through the VPN namespace instead, set `TAILSCALE_VPN_ROUTED=true` in `.env.wrapper`. **Warning:** this links your Tailscale identity to the VPN exit IP.

Tailscale Funnel prerequisites:

- MagicDNS enabled
- HTTPS certificates enabled for your tailnet
- Funnel node attribute enabled for your user/device in ACL policy

Enable it in `.env.wrapper`:

```bash
TAILSCALE_FUNNEL_ENABLED=true
TAILSCALE_AUTHKEY=tskey-client-...
```

Bring the stack up as usual (`make up-lite` or `make up-full`).

Disable by setting `TAILSCALE_FUNNEL_ENABLED=false` and restarting. The service will remain idle and will not publish Funnel routes.

### Optional: VPN Routing for Teep, Tailscale, and Code-Interpreter

By default, **teep** and **tailscale** run on the default Docker network without VPN routing. This means:

- Teep LLM API traffic egresses directly (not through Mysterium).
- Tailscale Funnel traffic egresses directly, keeping your Tailscale identity separate from the VPN exit IP.

You can optionally route either or both services through the Mysterium VPN namespace by setting these variables in `.env.wrapper`:

```bash
# Route teep LLM proxy traffic through the VPN
TEEP_VPN_ROUTED=true

# Route Tailscale Funnel through the VPN
# WARNING: This links your Tailscale identity to the VPN exit IP
TAILSCALE_VPN_ROUTED=true
```

The Makefile conditionally applies `docker-compose.teep-vpn.yml` and/or `docker-compose.tailscale-vpn.yml` override files when these are set to `true`. These override files properly adjust host and port mappings of teep and tailscale for the VPN interface. Restart the stack after changing these settings.

#### Optional: VPN Routing for the Code-Interpreter

By default, Onyx's code-interpreter (the `onyxdotapp/code-interpreter` image from [onyx-dot-app/python-sandbox](https://github.com/onyx-dot-app/python-sandbox)) hardcodes `--network none` on every executor pod it spawns. This means the Python tool and coding-agent bash sessions have **zero network access** — the LLM-generated code cannot make any outbound requests. This is a security isolation measure baked into the upstream image.

You can optionally give executor pods outbound internet access through the Mysterium VPN by setting:

```bash
# Give code-interpreter executor pods VPN-routed internet access
# SECURITY: This removes the code-interpreter's network isolation.
CODE_INTERPRETER_VPN_ROUTED=true
```

The code tool descriptions and code agent prompts are updated to mention internet access, and include service hints for the in-namespace scraping/browser services that executor pods can reach via localhost (since they inherit the netns-holder namespace).

Restart the stack after changing this setting (`make down-lite && make up-lite`, or the full-mode equivalents).

### Optional: Outbound Proxy (`PROXY_URL`)

Set `PROXY_URL` in `.env.wrapper` to route internet egress from **crw**, **obscura** (both the CDP `serve` instance and the MCP server), **SearXNG**, the **code-interpreter**, and the **code agent** through a single upstream proxy. This is orthogonal to the Mysterium VPN: if both are set, traffic is proxied **through** the VPN tunnel (the proxy connection itself egresses over the VPN).

Accepts any scheme:

```bash
# HTTP / HTTPS proxy
PROXY_URL="http://user:pass@proxy.example.com:8080"

# SOCKS5 proxy
PROXY_URL="socks5://proxy.example.com:1080"

# SOCKS5 with remote DNS resolution
PROXY_URL="socks5h://proxy.example.com:1080"
```

> **NOTE** The proxy setup has not been audited for leaks. You still likely want the Myst VPN, or at least a host VPN, in case of proxy bypass. In particular, Obscura/Chrome does not support SOCKS5 usernames and passwords, and will bypass the proxy entirely if these are set. Additionally, the python code interpreter tool will bypass socks proxies when used with urllib, since urllib does not honor ALL_PROXY or support SOCKS.

**SearXNG crw-engine loopback exclusion:** The crw-backed SearXNG engines (`google2`, `brave2`, `duckduckgo2`, `startpage2`) POST their search queries to the local crw scraper at `http://127.0.0.1:3010/v1/scrape` (loopback inside the `netns-holder` namespace). The `searxng-proxy-entrypoint.sh` wrapper defines a `direct` network (`proxies: {}`) and assigns these engines to it, so their loopback requests to crw bypass the upstream proxy. Without this, the `all://` proxy pattern would catch the loopback request and the proxy would reject it as a private address. All other SearXNG engines (which fetch external URLs directly) use the default network and egress through the proxy.

### Optional: Local Document RAG via Web Connector

The full version of Onyx supports search and retrieval (RAG) over PDF, DOC, EPUB, and other document types.

For implementation details, troubleshooting notes, and Onyx upgrade assumptions, see [`docs/local_docs_rag_search.md`](docs/local_docs_rag_search.md).

Setup steps:

1. Put PDFs into `DOC_DROP_DIR` (default `./doc-drop`).
2. Start or restart full stack: `make up-full`.
3. In Onyx Admin → Connectors → Web, create a connector.
4. Set Web connector type to **Recursive**.
5. Set URL to `http://localhost:8091/` (or `http://localhost:<DOC_DROP_WEB_PORT>/`).
6. Sync the connector.

Notes:

- Directory listing pages are crawlable; you can also target specific files
  directly, e.g. `http://localhost:8091/my-paper.pdf`.
- Onyx v4.1+ has SSRF Protection that can block this service if you save a
  Security Hardening override in the Admin UI.
- The defaults in `.env.wrapper.example` seed the `Allow Private Network` posture
  in the Security Hardening UI, which allows this localhost connector while still keeping
  loopback/link-local protections on LLM-initiated fetch paths.
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

You must also set `ALLOW_LAN_ACCESS=true` in `.env.wrapper`, so traffic can bypass the Myst VPN firewall to reach this embedding service.

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

The MCP server runs in the netns-holder VPN namespace, so all browser traffic egresses through the Mysterium VPN tunnel with stealth anti-fingerprinting enabled. It listens on `127.0.0.1:${OBSCURA_MCP_PORT:-9223}` inside the namespace (default port 9223). Configure the port via `OBSCURA_MCP_PORT` in `.env.wrapper`.

**Step 1: Configure SSRF Protection to allow loopback**

The obscura MCP server listens on `127.0.0.1` inside the netns-holder namespace. The api_server (which runs in the same namespace) reaches it via localhost, but Onyx's SSRF protection blocks loopback addresses by default — even at the `Allow Private Network` level (the wrapper's default via `MCP_SERVER_ALLOW_PRIVATE_NETWORK=true`, `MCP_SERVER_ALLOW_LOOPBACK=false`).

To allow the MCP client to reach `127.0.0.1`, set SSRF Protection to **Disabled**:

1. Go to **Admin Panel -> Security Hardening** ([http://localhost:3000/admin/security](http://localhost:3000/admin/security)).
2. Set **SSRF Protection** to **Disabled**.
3. Click **Save**.

This allows loopback and private-network targets for MCP servers, open_url, and OAuth endpoints. Cloud-metadata and link-local addresses (169.254.0.0/16) remain blocked as an always-on floor. This is safe in the wrapper deployment because all services run inside the VPN namespace — there are no external clients that could exploit the relaxed SSRF policy.

> **Note:** If you prefer not to disable SSRF protection globally, you can alternatively run the obscura-mcp service on a non-loopback address by giving the netns-holder namespace a dedicated bridge IP. However, the simplest path is the Disabled setting above, since the wrapper's threat model already assumes all services are co-located in the VPN namespace.

**Step 2: Configure the MCP server in Onyx**

1. Go to **Admin Panel -> MCP Servers** ([http://localhost:3000/admin/mcp-servers](http://localhost:3000/admin/mcp-servers)).
2. Click **Add MCP Server**.
3. Set **Name** to `obscura` (or any name you prefer).
4. Set **Server URL** to `http://127.0.0.1:9223/mcp` (or your configured `OBSCURA_MCP_PORT`).
   - The api_server runs in the same netns-holder namespace, so it reaches the MCP server via localhost.
5. Set **Auth Type** to **None** (the MCP HTTP transport has no built-in auth; it's only reachable within the VPN namespace).
6. Click **Save**, then click **Discover Tools** to verify the connection.

**Step 3: Assign the MCP tools to an Assistant**

0. **RELOAD THE ONYX Admin WebUI**. After MCP config change, the Admin WebUI needs to be updated with the proper set of tool lists.
1. Go to **Admin Panel -> Assistants** and edit an assistant (or create a new one).
2. Under **MCP Servers**, select the `obscura` server.
3. Select which tools to expose (or select all).
4. Save the assistant.

Chat agents using that assistant can now drive a stealth browser to navigate, read, and interact with web pages — with all traffic egressing through the VPN.

**Security notes:**

- The MCP HTTP transport has no built-in auth. It binds `0.0.0.0` inside the netns-holder namespace, so it's only reachable by other services in that namespace (and the host-web-proxy bridge). For additional origin restrictions, set `OBSCURA_MCP_ALLOWED_ORIGINS` in `.env.wrapper` to a comma-separated list of allowed Origin values.
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

## Privacy of this stack

This stack should be regarded as a proof-of-concept. It will keep your LLM queries and the resultant search
activity out of the hands of AI companies, data aggregators, and marketers,
but single-hop VPN activity is not as strong as Tor, and may not even be as
strong as [Tinfoil's distributed trust
architecture](https://tinfoil.sh/security-and-privacy-faq#web-search).

As noted previously, this stack will use the host OS VPN as the "first hop"
before connecting to the Mysterium endpoint. Additionally, all inference,
search, and web traffic exiting the Mysterium VPN uses TLS or https.

### Why not just use Tor?

It may seem strange that a [Tor Project](https://www.torproject.org) employee created a private inference stack that does not support Tor usage. This was a pragmatic choice to produce something that functioned.

The reality is that many websites subject Tor and datacenter VPNs to increased captchas and bans compared to [residential IP addresses](https://acid.vegas/blog/the-shady-world-of-ip-leasing/). The most egregious example is Google's move to update [ReCaptcha to require an official Google device, while exempting "official" AI scrapers](https://www.financialexpress.com/life/technology-google-qr-captcha-controversy-explained-why-internet-is-scared-of-this-4237640/).

Until this landscape changes, residential IP address leasing is the only reliable option for a self-hosted private research agent, and Mysterium was the best choice among those, since the server side is open source, and payment is made in cryptocurrency.

If you want to see the difference, you can use set `PROXY_URL=socks5h://host.docker.internal:9150` and `ALLOW_LAN_ACCESS=true` in `.env.wrapper` to use the host Tor Browser proxy. SearXNG provides search engine success statistics on the "Engines" of the [Preferences Pane](http://localhost:8080/preferences), which is available on your host. Again, be aware that the `PROXY_URL` config is not audited for proxy bypass leaks.
