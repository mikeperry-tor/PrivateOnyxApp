# Private Onyx.App Docker Compose Set

This stack gets you a private deep research agent with code sub-agents and RAG document search, via a responsive web interface that you can access from anywhere.

The stack is built around [Onyx](https://github.com/onyx-dot-app/onyx) using [teep](https://github.com/13rac1/teep) for private verified LLM inference.

Search traffic is rendered through [Obscura Browser](https://github.com/h4ckf0r0day/obscura) via customized [SearXNG](https://github.com/searxng/searxng) engines which round-robin to minimize search load and associated captchas.

Web browsing uses Onyx's stock requests/Playwright crawler by default, and can be switched to the Obscura Browser by an env preference. Fingerprints are stable in the default web crawler, but are varied per-navigation in Obscura. Cookies are cleared between every navigation. No other browser state or browser-based tracking information is preserved.

To further minimize captchas and reduce tracking, all agent internet traffic can use the selected [Mysterium](https://github.com/mysteriumnetwork/node), upstream-proxy, and/or explicit no-VPN routing mode. The stack employs docker/podman network namespace isolation to ensure that all agent traffic exits through the configured VPN and/or upstream proxy, and prohibits DNS leaks and other forms of proxy bypass.

[Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel) integration allows you to access the instance remotely from anywhere, without the need for that device to use the Tailscale VPN. Tailscale funnel is used in userland networking mode for the reverse proxy HTTPS service only: it does not create a Tailnet or use the Tailscale VPN.

## Private Deep Research, RAG, and Code Agent Support

The main reason I created this stack is because none of the private chat providers offer "Deep Research" (aka orchestrated multi-agent multi-round research report generation), and I didn't like going back to non-private chat providers when I needed this functionality.

Additionally, the full mode of Onyx provides RAG search results to the agent from local collection of PDFs and other documents, and has a Code Agent tool that allows the chat agent to spawn multiple sub-agents to clone and investigate git repositories. Onyx has many other connectors as well.

In this stack, I [patched Onyx](./docs/onyx_patch_info.md) to improve several limitations and poorly performing edge cases:

- Onyx telemetry, third-party analytics and error reporting, cloud billing, CAPTCHA, and automatic remote configuration/data-list downloads are explicitly disabled.
- A restrictive browser Content Security Policy blocks third-party scripts, connections, frames, media, fonts, workers, and remote images from bypassing the stack's VPN/proxy routing through the user's browser. Additionally, this blocks queries to a Google favicon service for all sourced URLs in chat and research reports; generic icons are used instead.
- Stock Onyx strips reasoning between tool calls for most open-weight LLMs. This causes needless repeated re-thinking and degrades final answer quality.
- Stock Onyx strips tool call results upon user follow-up questions, which often makes LLMs think that they hallucinated the previous turn tool results; this has been patched.
- Stock Onyx disables `open_url()` whenever its vector database is disabled, which leaves lite mode unable to open and read web pages. This stack keeps crawler-backed web browsing available in lite mode. Full mode may reuse a connector document already indexed under the requested URL during an `open_url()` call; this is exact-ID chat-time retrieval, not URL ingestion or `internal_search`, and remains unavailable in lite mode.
- The "Deep Research" mode has been patched to provide the research sub-agents with RAG access and all configured tools, rather than the Onyx default of only web search and url retrieval.
- The "Deep Research" mode now also supports longer, bounded research runs and executes all accepted tool calls when a research agent requests several different tools at once, rather than silently dropping some of them.
- The code sub-agent investigation summarization has been enhanced to summarize reasoning steps as well as output.
- Sub-agents are patched to choose whether to call another tool or finish, avoiding a forced-tool compatibility problem with vLLM for open weight models.
- RAG document re-indexing is patched to skip re-downloading and re-parsing unchanged local PDFs, making re-indexing substantially faster than stock Onyx.
- Onyx's idle background workload is reduced by running discovery and housekeeping less often, removing unused monitoring and disabled-feature work, keeping lightweight control processes out of application bootstraps, and keeping optional Slack/Discord bot processes off unless enabled. Stable Myst routes are validated without repeated route writes or success logs. Controlled before/after power and resource measurements remain future work.

I intend to merge these upstream at some point, once I stop finding new edge cases and the dust settles a bit.

If you do not need intense multi-agent deep research, code research subagents, and RAG functionality, your best option is [TinFoil](https://tinfoil.sh), which has an excellent [security architecture](https://tinfoil.sh/security-and-privacy-faq) and decent cross-device app support, with encrypted syncing of chats.

## Components

The Docker Compose files in this stack relies on the following components:

1. [Onyx](https://github.com/onyx-dot-app/onyx) provides a [top-ranking Deep Research Agent](https://huggingface.co/spaces/muset-ai/DeepResearch-Bench-Leaderboard), with a decent web interface and comprehensive connector and RAG-based local document search support. While other open source deep research agents rank slightly higher than Onyx, it is the only provider-neutral option with a complete user interface that works well with both mobile and desktop web browsers.

2. [Teep](https://github.com/13rac1/teep) provides private verified LLM inference via a local OpenAI-compatible proxy on port 8337. Teep supports [multiple private inference providers](https://github.com/13rac1/teep#supported-providers), and verifies attestation, encryption, and remote runtime properties before requests are allowed to proceed.

3. [Mysterium](https://github.com/mysteriumnetwork/node) is a Wireguard dVPN that accepts cryptocurrency payment and has a large pool of residential endpoints. The use of residential IP addresses reduces the rate of captchas and rate limiting by search engines and websites. Mysterium server-side code is open source and contains no centralized data retention. No comparable Zero Data Retention options are available to end-users. (Firecrawl, Exa, and Brave retain all user API activity and do not offer ZDR to consumers).

4. [Obscura Browser](https://github.com/h4ckf0r0day/obscura) provides all custom search engines, and optionally the built-in Onyx Web Crawler, with one headless-browser navigation per target. It supplies anti-fingerprinting defenses without an HTTP prefetch or local-browser fallback. Obscura and SearXNG run on narrow internal networks; browser traffic crosses a fixed bridge to a destination-validating final-hop proxy in the selected Myst/proxy/no-VPN routing namespace. By default, only the built-in crawler instead uses Onyx's stock HTTP fetch and local Chromium fallback through the fixed public Onyx bridge.

5. [SearXNG](https://github.com/searxng/searxng) is an open source meta-search engine. The wrapper-provided Google, Brave, DuckDuckGo, Startpage, and Bing offline engines navigate and parse rendered result pages through Obscura. SearXNG owns round-robin search provider scheduling, retries after unresponsive providers, and suspends providers after visible anti-bot failures or rate limits.

6. [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel) is a free service that creates a reverse proxy to access the Onyx web interface via HTTPS from any web browser, using your assigned ts.net service subdomain name. The TLS key is generated locally in this stack and is signed with Let's Encrypt. This means that Tailscale's infrastructure is unable to read the contents of your remote communications to the instance.

## Prerequisites

- Docker Engine 25.0 or later with Docker Compose 2.20.2 or later; or a
  rootless Podman 5.8.1 or later server with a Compose provider 2.20.2 or
  later. The Podman path installs and verifies Podman's separate native
  startup health checks before starting any created service. The currently
  verified macOS guest is the official 5.8.1 machine-os image; startup also
  probes the image store so a broken post-restart guest fails early.
- Internet access for image builds and provider APIs
- `make`
- `uv` for MLX embedding server installation and dependency-lock upgrades.

## Running the Stack

The stack comes in two flavors: lite and full. This specifies the mode of the Onyx app. Lite mode provides Chat, Web, and Research only. Full mode also provides RAG, external app connectors, and groupware. Lite mode uses significantly less RAM (~1GB vs ~10GB).

It is possible to switch between full and lite modes between restarts.

Docker and Podman persist PostgreSQL and OpenSearch in the same bind mounts
under `./docker-data`. For the configured RAG source—`./doc-drop` by default—
Podman uses a wrapper-managed host-local server rather than copying the
directory into its VM. This also supports external mounts that virtiofs cannot
re-export reliably.

The wrapper records which engine owns the shared database/index data before
starting Compose and refuses a start through the other engine until the
matching `make down-*` succeeds. Use `make shared-data-engine-status` to inspect
the claim. After updating an already-running installation, rerun its matching
`make up-*` once before switching engines so the claim is initialized.

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
  - Keep `CONTAINER_BIN=docker` for Docker Desktop, or set it to `podman` for a
    running Podman machine. Podman mode requires a clean `make down-*` when
    switching engines; it never falls back to Docker.
  - On rootless Podman for macOS, code interpreter and Docker-socket autoheal
    are unavailable. Routing remains fail-closed, but a failed Myst connection
    requires `podman restart myst-client-vpn` or a stack restart.
  - PostgreSQL and OpenSearch reuse Docker's initialized bind data through
    guarded Podman user mappings and a wrapper-managed engine claim. Never run
    both engines against it at once.
  - In full mode, `make up-full` starts a PID-tracked read-only document server
    on the Mac for `ONYX_RAG_DOC_SOURCE_DIR` (`./doc-drop` by default); a
    hardened fixed relay keeps the internal `doc-drop-web:8091` origin
    unchanged. `make down-full` stops only that identity-validated
    wrapper-owned process. No document collection is copied into the Podman
    VM. External mounts, including WebDAV mounts, use the same path.
- Teep LLM Provider/API config:
  - Set at least one teep key (for example `TEEP_NEARAI_API_KEY`, `TEEP_TINFOIL_API_KEY`)
- **VPN and Proxy Use**:
  - Set `MYST_VPN_ENABLED=false` to use the explicit no-VPN final-hop route.
  - You may use an upstream proxy with or without the Mysterium VPN enabled (`EGRESS_UPSTREAM_PROXY_URL`).
  - For the full routing matrix, namespace layout, and proxy behavior, see [`docs/vpn_routing_and_proxies.md`](docs/vpn_routing_and_proxies.md).
- **Optional LAN access**:
  - Set `ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true` to let explicitly configured MCP servers, Web Connectors, embedding servers, and inference providers reach services on your private LAN. MCP and Web Connector access also requires a compatible SSRF setting under **Admin → Security Hardening** that will be set correctly by default, but should not be changed.
  - LAN service destinations require an RFC1918 IP literal or a name ending in `.local`, `.internal`, or `.home.arpa`; failed, empty, or mixed public/private lookups are rejected.
  - This setting does **not** give agent web search, `open_url()`, browser activity, or generated code access to your host, LAN, private addresses, metadata endpoints, or stack-managed services. Those agent-controlled paths remain public-only (or have no network at all).
  - `host.docker.internal` is available by default to the explicitly configured integrations and model endpoints above, so services running on the Docker host do not require `ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true`. The Onyx agent itself never has access to `host.docker.internal` through any of its default tools.
  - If the Onyx application becomes fully compromised, an attacker may abuse local LAN access with this pref. See [`docs/internal_network_security.md`](docs/internal_network_security.md) for the implementation boundaries and residual risks of enabling this setting.

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

Once Mysterium VPN successfully connects (or the service starts with `MYST_VPN_ENABLED=false`), Onyx will need to be configured to use teep via its [Web-based Admin Interface](http://localhost:3000/admin/configuration/language-models).

For LLM inference, select the **OpenAI-Compatible** provider type for teep. This is important for GLM-5.2 and Kimi-K2.6 reasoning models: the wrapper's Onyx patches preserve active-turn assistant reasoning as OpenAI-compatible `reasoning_content`/`reasoning` fields by default, and the OpenAI-Compatible provider keeps teep's raw model IDs on that request path. The BiFrost provider is also compatible, but OpenAI-Compatible is the recommended teep selection. Other provider catalogs may spell GLM and Kimi model names with different dots, dashes, or compressed forms, and the OpenAI-Compatible path avoids LiteLLM native-provider remapping and ensures reasoning preservation.

Use `http://teep:8337/v1` as the OpenAI baseurl.

The models supported by your API key from `.env.wrapper` should then be listed if you refresh the dropdown. Use teep's exact model ID as listed in the model selection drowndrop.

Set `ONYX_AGENT_LLM_MAX_TOKENS` in `.env.wrapper` to the limit you want the stack to use (the default 900000 is good for GLM-5.2).

The wrapper recognizes exactly `http://teep:8337/v1` as its bundled private
inference endpoint. Other supported configured chat endpoints may be public,
may use `host.docker.internal` by default, or may use a private LAN address when
`ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true`. These inference permissions are
not inherited by agent browsing or generated code.

### Inference Provider Recommendations

The best privacy preserving providers supported by teep are currently `neardirect` and `tinfoil_v3_direct`, which are the direct completions version of [NearAI](https://cloud.near.ai) and [Tinfoil.sh](https://tinfoil.sh), respectively. NearAI is also useful in that it can be paid in cryptocurrency.

This stack can also use a local OpenAI-compatible, LM Studio, or Ollama chat
endpoint through `host.docker.internal` or an explicitly enabled RFC1918
IP address.

If your local provider is running on a private LAN address, set
`ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true` to permit that configured
endpoint. RFC1918 names must end in `.local`, `.internal`, or `.home.arpa`;
otherwise use literal IP addresses. An endpoint on `host.docker.internal` does
not require this setting.

### LLM recommendations

Verifiable private inference is only currently possible with Open Weight models. While it is [technically possible](https://www.anthropic.com/research/confidential-inference-trusted-vms) for closed weight models to support attestation-based verification, proprietary LLM labs [do not seem to be interested](https://www.anthropic.com/news/activating-asl3-protections) in offering privacy to end users.

For a research agent like Onyx, the primary desirable property is a low hallucination rate. The [Artificial Analysis Omniscience Index](https://artificialanalysis.ai/evaluations/omniscience#aa-omniscience-hallucination-rate) provides a [Hallucination Rate benchmark](https://artificialanalysis.ai/evaluations/omniscience#aa-omniscience-hallucination-rate) that is worth tracking for this purpose.

Among Open Weight models currently supported by NearAI and Tinfoil, GLM-5.2 is the best option for text, and Kimi-K2.6 is the best multimodal option. Configure these with Onyx's OpenAI-Compatible provider type so the stack's Onyx patches can preserve active-turn reasoning fields continue across tool-using turns.

### Search and Web Crawler Provider Configuration

Select SearXNG and the built-in **Onyx Web Crawler** in the [Web Search Admin Panel](http://localhost:3000/admin/configuration/web-search):

1. Go to **Admin Panel -> Web Search -> Web Crawler**.
2. Open **SearXNG** and click **Connect**
3. Set the **SearXNG Base URL** to `http://searxng-service-gateway:8888`.
4. Open **Onyx Web Crawler**, click **Connect**, then **Set as Default**.

The stock Onyx Web Crawler appears to be blocked less often than Obscura v0.1.10 by websites, but you can set `ONYX_AGENT_USE_OBSCURA_BROWSER=true` to cause the Onyx Web Crawler to use the Obscura Browser instead of Onyx's internal fetch + Chromium Playwrite fallback.

SearXNG always uses Obscura; `ONYX_AGENT_USE_OBSCURA_BROWSER` independently selects the built-in crawler transport.

In either case, egress is restricted to ensure usage of VPN and/or upstream proxy, through docker compose network namespace routing. This is the case for all search traffic as well. For the request flow, one-navigation contract, limits, and failure behavior, see [`docs/request_handling.md`](docs/request_handling.md).

Selecting Firecrawl or Exa for Web, or Brave, Serpa, Exa, or Google PSE for Search, is supported. Connections to these services will traverse via the VPN and/or upstream proxy, but these external providers perform their accesses from their own IP address space. None of these providers offer ZDR policies to consumer end users, so your API key and account on these services will be associated with your usage activity, and this data will be stored, trained on, and/or sold by these providewrs.

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

Disable by setting `TAILSCALE_FUNNEL_ENABLED=false` and restarting. The Tailscale process and its fixed frontend gateway are then omitted from the effective Compose model.

#### Optional: Network Access for the Code-Interpreter

By default, Onyx's code-interpreter (the `onyxdotapp/code-interpreter` image from [onyx-dot-app/python-sandbox](https://github.com/onyx-dot-app/python-sandbox)) hardcodes `--network none` on every executor pod it spawns. This means the Python tool and coding-agent bash sessions have **zero network access** — the LLM-generated code cannot make any outbound requests. This is a security isolation measure baked into the upstream image.

You can optionally give executor pods restricted proxy-only access by setting:

```bash
# Give code-interpreter executor pods network access
# Executors remain isolated from stack, host, LAN, and direct internet routes.
ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true
```

### Optional: Outbound Proxy (`EGRESS_UPSTREAM_PROXY_URL`)

Set `EGRESS_UPSTREAM_PROXY_URL` in `.env.wrapper` to use an upstream proxy. This
is orthogonal to Mysterium: if both are set, the upstream-proxy connection
crosses the VPN.

Supported schemes:

```bash
# HTTP proxy
EGRESS_UPSTREAM_PROXY_URL="http://user:pass@proxy.example.com:8080"

# HTTPS proxy
EGRESS_UPSTREAM_PROXY_URL="https://user:pass@proxy.example.com:8443"

# SOCKS5 proxy
EGRESS_UPSTREAM_PROXY_URL="socks5://proxy.example.com:1080"

# SOCKS5 alias; target names are still resolved by the proxy
EGRESS_UPSTREAM_PROXY_URL="socks5h://proxy.example.com:1080"
```

An exact host Tor proxy
(via `EGRESS_UPSTREAM_PROXY_URL=socks5h://host.docker.internal:9150`) does not
require `ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true`. Choosing a host- or
LAN-based upstream proxy only makes that proxy available as routing
infrastructure; it does not give agent browsing or generated code permission to
access other host or LAN destinations.

Invalid upstream proxy URLs fail policy-proxy startup.

The same destination permissions apply whether or not an upstream proxy is
configured. Agent browsing and generated code remain unable to target host,
LAN, metadata, or stack-managed service addresses. Explicitly configured MCP,
Web Connector, inference, and embedding endpoints retain only the local access
described under **Optional LAN access**. See the
[internal network policy](./docs/internal_network_security.md) for DNS and
remote-proxy limitations.

### Optional: Local Document RAG via Web Connector

The full version of Onyx supports search and retrieval (RAG) over PDF, DOC, EPUB, and other document types.

For implementation details, troubleshooting notes, and Onyx upgrade assumptions,
see [`docs/local_docs_rag_search.md`](docs/local_docs_rag_search.md).

Setup steps:

1. Put PDFs into `ONYX_RAG_DOC_SOURCE_DIR` (default `./doc-drop`).
2. Configure one of the embedding backends below (install the bundled MLX
   backend first if that is your choice).
3. Start or restart full stack: `make up-full`.
4. In Onyx Admin → Connectors → Web, create a connector.
5. Set Web connector type to **Recursive**.
6. Set URL to the internal crawl origin `http://doc-drop-web:8091/`.
7. Sync the connector.

Notes:

- Directory listing pages are crawlable; you can also target specific files
  directly, e.g. `http://doc-drop-web:8091/my-paper.pdf`.
- Later syncs skip downloading and parsing unchanged local PDFs, making routine
  document updates substantially faster than stock Onyx.
- Background discovery runs every five minutes. A newly uploaded
  project/assistant file, connector change, or deletion can therefore take up
  to five minutes to begin processing.
- Browser-visible result links are rewritten to the host display origin,
  `http://localhost:8091/` by default. This enables you to click on source links in a host browser and view them locally.

### Optional: Running a Local Embedding Model Server (Mac)

If you are on a Mac, the makefile has rules that can install
[Harrier-oss-v1-0.6b](https://huggingface.co/microsoft/harrier-oss-v1-0.6b)
(which is a [leading SOTA open weight embedding model](https://huggingface.co/spaces/mteb/leaderboard)), served via
[mlx-embeddings](https://github.com/Blaizzy/mlx-embeddings).


```sh
# Install mlx-openai-server and mlx-embeddings in ./embedserv using `uv`
make embedserv-install
# Verify the model was downloaded correctly
make embedserv-verify-model
# Start the full stack; this also launches the installed embedding server
make up-full
```

You can select a different model via `ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL` in
`.env.wrapper`, using the huggingface ID of any MLX-packaged embedding model.

After `make embedserv-install` has installed the selected model, `make up-full`
automatically launches a lightweight lifecycle proxy at the bundled default
endpoint, `http://host.docker.internal:3210/v1/embeddings`. It loads the MLX
model for the first embedding request and unloads it ten minutes after the last
request completes. A request after unload includes the measured cold-start
delay and still uses the shim's unchanged 30-second deadline. You can run
`make embedserv-serve` directly in the foreground. `make down-full` stops only
the wrapper-managed proxy and its identity-validated child; manual and custom
upstreams are untouched.

`make up-full` first starts the embedding shim and its routing dependencies,
then calls `/ready` exactly once before creating a fresh API/background tier.
Failure returns nonzero with that subset left running for diagnosis and no
automatic retry. On a repeated start, already-running API/background services
are neither stopped nor recreated by a failed validation.

Full mode favors low idle activity: connector discovery can take up to about
five minutes, and Slack and Discord bot processes are disabled by default.
Enable only a bot you use with `ONYX_SLACK_BOT_ENABLED=true` or
`ONYX_DISCORD_BOT_ENABLED=true` in `.env.wrapper`.

MLX embedding server installation and embedding model download run on the host before the embedding shim is ready; they are not routed through the stack VPN. Standard host `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` environment variables are honored by `uv` and
the download libraries when the host requires a build/download proxy.

The stack uses use `mlx-embeddings` because llama.cpp embeddings support is very buggy (including many subtle accuracy drift bugs, especially under concurrency load and batched embeddings). LM Studio's embedding support is non-existent.

### Optional: Using Teep for Embeddings

If you are not on a Mac, your best bet is to use `Qwen/Qwen3-Embedding-0.6B` with Teep's `neardirect` provider. Qwen3-Embedding is also highly ranked in the [emebdding leaderboards](https://huggingface.co/spaces/mteb/leaderboard).

Point the shim at Teep's host-published OpenAI endpoint and select Teep's provider-qualified
model in `.env.wrapper`:

```env
ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL="http://host.docker.internal:8337/v1/embeddings"
ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL="neardirect:Qwen/Qwen3-Embedding-0.6B"
```

Teep uses `TEEP_NEARAI_API_KEY` from the `.env.wrapper` file for the upstream
request. Do not set `ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL` to the Qwen model:
that variable only selects the model downloaded and served by the bundled Mac
MLX flow. `make up-full` sees the custom Teep URL and does not start MLX.

Use `host.docker.internal` here because this setting identifies an embedding
endpoint running on the Docker host; stack-managed service names are not a
user-configurable addressing surface for this option.

### Optional: Embedding Model Configuration in Onyx

Properly configuring an open-weight frontier embedding model for RAG is a minefield. Almost no one gets it right, including Onyx and LiteLLM. Do **not** configure embeddings through LiteLLM.

Frontier embedding models require an instruction prefix when generating queries, but unfortunately, Onyx has an issue with handling this prefix for generic LLM providers. To address this, the stack contains a local shim which allows you to set the query prefix via an environment instead. The prefixes in `.env.wrapper.example` should be good for either Harrier or Qwen3.

To configure embedding support in Onyx:

1. Go to [Onyx Admin Index Settings](http://localhost:3000/admin/configuration/index-settings)
2. Select your embedding model as **Self-Hosted / Custom Model** (local)
3. Enter `nomic-ai/nomic-embed-text-v23` as the model type. This synthetic name
   intentionally enables Onyx's hardcoded `nomic-ai` RAG features. The wrapper
   maps only its tokenizer to the bundled nomic v1 tokenizer; the embedding
   shim still sends requests to the configured real upstream model.
4. For both `Harrier-OSS-V1-0.6B` and `Qwen3-Embedding-0.6B`, the embedding dimension is 1024.

This Onyx configuration choices cause the stack's patches to route embedding requests to either the MLX embeddings or teep model that you configured above, using correctly formatted query and indexing prefixes.

### Optional: External MCP servers

You can add any MCP servers you operate or trust through Onyx Admin. Their streamable
HTTP, SSE, redirects, discovery, registration, OAuth, token, refresh, and tool
traffic is subject to the saved **Admin → Security Hardening** setting. Public
MCP servers may use nonstandard TCP ports. An MCP server on
`host.docker.internal` can be allowed without enabling general LAN access;
other RFC1918 destinations require
`ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true`. These MCP permissions do not
extend to agent browsing or generated code.

## Upgrading the Stack

This wrapper carries a few local Onyx runtime and install-time patches. For the rationale behind those patches, see [`docs/onyx_patch_info.md`](docs/onyx_patch_info.md).

Image tags, source refs, and runtime Python lock files are consolidated in
[`stack.versions.env`](stack.versions.env) plus the committed `requirements.in`
/ hashed `requirements.txt` files. `make upgrade` refreshes the Python locks
through `make upgrade-python-deps` before rebuilding/pulling the stack
components. The derived SearXNG image installs its complete pinned Python
dependency set from the generated hashed `searxng/requirements.txt` lock and
validates the shared Obscura client at image-build time. The Makefile derives
the local image tag from the upstream SearXNG pin and every embedded Dockerfile,
lock, shared-client, and engine input, so a source change selects a fresh image;
the runtime container never downloads packages or a browser. Myst and Teep
builds forward standard
`HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` build arguments when those
variables are present. Image builds and host-side `embedserv` installation
happen before stack readiness and therefore use the host/build network, not
the Mysterium namespace or `EGRESS_UPSTREAM_PROXY_URL`.

When changing these image pins or rebasing onto a new Onyx release, point a code agent at [`docs/onyx_patches_upgrade.md`](docs/onyx_patches_upgrade.md).

## Docker Host Endpoints

The following endpoints are exposed to your docker host:

- Onyx WebUI: [`http://localhost:3000`](http://localhost:3000)
- SearxNG WebUI: [`http://localhost:8080`](http://localhost:8080)
- teep human-readable stats page: [`http://localhost:8337`](http://localhost:8337)
- teep OpenAI API base: `http://localhost:8337/v1`
- teep health check: `http://localhost:8337/health`
- teep prometheus metrics: `http://localhost:8337/metrics`
- Full-mode local document display: [`http://localhost:8091`](http://localhost:8091)
- Wrapper-managed MLX embedding lifecycle proxy (when installed): `http://127.0.0.1:3210`

## Privacy of this stack

This stack should be regarded as a proof-of-concept. It will keep your LLM
queries and the resultant search activity out of the hands of AI companies, data
aggregators, and marketers, but single-hop VPN activity is not as strong as Tor
(which is supported, but not the default). Single-hop VPN activity may not even
be as strong as [Tinfoil's distributed trust architecture](https://tinfoil.sh/security-and-privacy-faq#web-search).

However, the [network security](./docs/internal_network_security.md) of this
stack is robust, and [restricted VPN+proxy egress is enforced](docs/vpn_routing_and_proxies.md) with docker compose network
namespaces, service isolation and least-priviledge capability configuration.

The network restrictions are designed to contain agent-controlled activity:
web search, `open_url()`, browser requests, and optionally network-enabled
generated code cannot reach host, LAN, metadata, or stack-managed service
addresses. Configured MCP/Web integrations and inference/embedding endpoints
can be granted narrower local access. This separation is not a sandbox for a
fully compromised Onyx application; such a compromise may abuse the local
destinations that Onyx is legitimately configured to use.

This stack uses the host OS VPN as the "first hop" before connecting to the
Mysterium endpoint. Additionally, all inference, search, and web traffic exiting
the Mysterium VPN uses TLS or https.

Obscura uses a stable browser vendor/version profile with some per-navigation fingerprint variation, while the stock Onyx Web Crawler uses a fixed browser configuration. Obscura-backed requests clear cookies before each navigation, while the default stock crawler does not retain cookies between requests. No other browser state or browser-based tracking information is preserved.

### Why not just use Tor by default?

Tor usage is supported by configuring `EGRESS_UPSTREAM_PROXY_URL="socks5h://host.docker.internal:9150"` in your .env.wrapper, and this [proxy usage](./docs/vpn_routing_and_proxies.md) is [strictly enforced](./docs/internal_network_security.md), but it is not the default.

It may seem strange that a [Tor Project](https://www.torproject.org) employee created a private inference stack that does not support Tor usage by default. This was a pragmatic choice to produce something that functioned.

The reality is that many websites subject Tor and datacenter VPNs to increased captchas and bans compared to [residential IP addresses](https://acid.vegas/blog/the-shady-world-of-ip-leasing/). The most egregious example is Google's move to update [ReCaptcha to require an official Google device, while exempting "official" AI scrapers](https://www.financialexpress.com/life/technology-google-qr-captcha-controversy-explained-why-internet-is-scared-of-this-4237640/).

Until this landscape changes, residential IP address leasing is the only reliable option for a self-hosted private research agent, and Mysterium was the best choice among those, since the server side is open source, and payment is made in cryptocurrency.

You can monitor SearXNG search-engine success statistics on the "Engines" tab of the [Preferences Pane](http://localhost:8080/preferences), if you want to test your success with Tor usage, other proxy providers, or your host VPN.
