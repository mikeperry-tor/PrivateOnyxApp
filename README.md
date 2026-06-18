# Private Onyx.App Docker Compose Set

Docker Compose wrapper for running [Onyx](https://github.com/onyx-dot-app/onyx) with the [teep](https://github.com/13rac1/teep) private verified LLM inference proxy and [SearXNG](https://github.com/searxng/searxng). All search, web, and inference traffic is sent over a [Mysterium](https://github.com/mysteriumnetwork/node) VPN connection. [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel) integration allows you to access the instance remotely.

This stack gets you a private deep research agent and RAG document searching via a responsive web interface that you can access from anywhere.

## Deep Research Mode Support

The main reason I created this stack is because none of the private chat providers offer a "Deep Research Mode" (aka multi-agent multi-round research report generation), and I didn't like going back to non-private chat providers when I needed this functionality.

The Deep Research Mode in Onyx is optional, but if you do not need intense multi-agent deep research functionality at all, your best option is [TinFoil](https://tinfoil.sh), which has an excellent [security architecture](https://tinfoil.sh/security-and-privacy-faq) and decent cross-device app support, with encrypted syncing of chats.

## Components

The Docker Compose files in this stack rely on four components:

1. [Onyx](https://github.com/onyx-dot-app/onyx) provides a [top-ranking Deep Research Agent](https://huggingface.co/spaces/muset-ai/DeepResearch-Bench-Leaderboard), with a decent web interface and comprehensive connector and RAG-based local document search support. While other open source deep research agents rank slightly higher than Onyx, it is the only provider-neutral option with a complete user interface.

2. [Teep](https://github.com/13rac1/teep) provides private verified LLM inference via a local OpenAI-compatible proxy on port 8337. Teep supports [multiple private inference providers](https://github.com/13rac1/teep#supported-providers), and verifies attestation, encryption, and remote runtime properties before requests are allowed to proceed.

3. [Mysterium](https://github.com/mysteriumnetwork/node) is a Wireguard dVPN that accepts cryptocurrency payment and has a large pool of residential endpoints. The use of residential IP addresses reduces the rate of captchas and rate limiting by search engines and websites. Mysterium server-side code is open source and contains no centralized data retention. No comparable Zero Data Retention options are available to end-users. (Firecrawl, Exa, and Brave retain all user API activity and do not offer ZDR to consumers).

4. [Obscura](https://github.com/h4ckf0r0day/obscura) is combined with [crw](https://github.com/us/crw) to provide the Onyx agent with an actual headless browser with anti-fingerprinting defenses, as a Firecrawl-compatible API endpoint. This helps reduce fingerprint-based bans by websites. Both run inside the shared Myst namespace so scrape/crawl traffic egresses through the VPN endpoint IP.

5. [SearXNG](https://github.com/searxng/searxng) is an open source meta-search engine that provides API search for multiple back ends (DuckDuckGo, Brave, Startpage, and Google are enabled by default). Obscura+crw are used to fetch search results from DuckDuckGo, Brave, and Google, which significantly reduces captchas and bans by these search engines.

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

Status/logs:

```bash
make ps-lite
make logs-lite
```

Stop all lite containers:

```bash
make down-lite
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

0. The container build process may take some time to build all components on first run. Makefile dependency checks are used by `make up-lite` (or `make up-full`) build images on first run, but not after the images exist.

1. Mysterium will create a new cryptographic identity, register it, and create an order URL on coingate for 100 $MYST (currently ~$20 USD). The order URL will be visible via `docker logs myst-client-vpn`.  This order URL can be paid in several different major cryptocurrencies, though an email is required. It may be possible to transfer $MYST directly to the VPN identity yourself, but I have not verified this.

3. Mysterium residential providers can be flaky. Once you find one that works well, you may want to pin its identity via `MYST_PROVIDER_IDS` in `.env.wrapper`. Multiple providers can be listed, separated by commas.

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
- **Optional LAN access** (for local inference APIs):
  - Set `ALLOW_LAN_ACCESS=true` to allow access to local network addresses (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) without routing through the VPN. Useful for accessing LLMs, embedding servers, or MCP servers running on your host or LAN while maintaining fail-closed behavior for all other traffic. Default: `false`
- Optional Onyx SSRF defaults (for MCP servers and local doc-drop crawling):
  - `OPEN_URL_VALIDATE_SSRF`, `MCP_SERVER_ALLOW_PRIVATE_NETWORK`, and `MCP_SERVER_ALLOW_LOOPBACK` seed Onyx's default SSRF Protection level at startup.
  - After you save a value in Onyx Admin -> Security Hardening, the saved UI setting becomes the effective runtime policy and overrides these defaults.
  - If you want both MCP servers on `host.docker.internal` and the doc-drop connector at `http://localhost:8091/`, use `OPEN_URL_VALIDATE_SSRF=true`, `MCP_SERVER_ALLOW_PRIVATE_NETWORK=true`, and `MCP_SERVER_ALLOW_LOOPBACK=false`. That yields the `Allow Private Network` posture by default.

## Onyx UI Configuration

Once Mysterium VPN successfully connects, Onyx will need to be configured to use teep via its [Web-based Admin Interface](http://localhost:3000/admin/configuration/language-models). The BiFrost provider is compatible. Use `http://127.0.0.1:8337/v1`. The models supported by your API key from `.env.wraper` should then be listed if you refresh the dropdown.

### Inference Provider Recommendations

The best privacy preserving provider supported by teep is currently `neardirect`, which is the direct completions version of [NearAI](https://cloud.near.ai). NearAI is also useful in that it can be used with cryptocurrency.

Teep will also soon add support for [Tinfoil](https://tinfoil.sh), which also has an excellent security architecture, as well as excellent mobile and web apps.

This stack can also be used with LMStudio or any other local LLM provider.  Simply use `host.docker.internal` to connect to your localhost instance, using the Onyx Admin UI configuration.

If the local provider is running on a private/LAN address, you will usually also want `ALLOW_LAN_ACCESS=true` so traffic can bypass the Myst VPN firewall to reach your host or LAN service.

### LLM recommendations

Verifiable private inference is only currently possible with Open Weight models. While it is [technically possible](https://www.anthropic.com/research/confidential-inference-trusted-vms) for closed weight models to support attestation-based verification, proprietary LLM labs [do not seem to be interested](https://www.anthropic.com/news/activating-asl3-protections) in offering privacy to end users.

For a research agent like Onyx, the primary desirable property is a low hallucination rate. The [Artificial Analysis Omniscience Index](https://artificialanalysis.ai/evaluations/omniscience#aa-omniscience-hallucination-rate) provides a [Hallucination Rate benchmark](https://artificialanalysis.ai/evaluations/omniscience#aa-omniscience-hallucination-rate) that is worth tracking for this purpose.

Among Open Weight models currently supported by NearAI and Tinfoil, GLM-5.1 is the best option for text, and Kimi-2.6 is the best multimodal option.

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
- The tailscale service does not route through Mysterium VPN, to avoid linking your tailscale account to your search actvity at the Myst VPN exit server.
- Tailscale uses the userspace networking mode, so no VPN activity is involved.

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

### Optional: Local Document RAG via Web Connector

The full version of Onyx supports search and retrieval (RAG) over PDF, DOC, EPUB, and other document types.

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

### Using fastCRW (crw) Firecrawl-compatible Scraper with Obscura

Architecture:

- `obscura` runs from the published `h4ckf0r0day/obscura` Docker Hub image
  (release builds include the stealth feature). It serves a Chrome DevTools
  Protocol (CDP) WebSocket on port 9222 with `--stealth` enabled.
- `crw` runs from the published `ghcr.io/us/crw` image (built with the `cdp`
  cargo feature upstream) and is configured entirely via `CRW_*` environment
  variables — no local build or config file mount needed.
- crw connects to obscura's CDP endpoint at `ws://obscura:9222/devtools/browser`
  (a direct `/devtools/` URL, so crw skips `/json/version` discovery and
  connects straight to the browser socket).
- crw exposes Firecrawl-compatible `/v1/scrape`, `/v1/crawl`, `/v1/map`, and
  `/v1/search` endpoints on port 3010 (3000 is taken by the Onyx web server
  in the shared netns). `/v1/search` is backed by the wrapper's bundled
  SearXNG sidecar.

Both images are pulled automatically by `make up-lite` (or `make up-full`)
and refreshed by `make upgrade` — no manual build step is required.

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
