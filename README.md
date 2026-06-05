# Private Onyx.App Docker Compose Set

Docker Compose wrapper for running [Onyx](https://github.com/onyx-dot-app/onyx) with the [teep](https://github.com/13rac1/teep) private verified LLM inference proxy and [SearXNG](https://github.com/searxng/searxng). All search, web, and inference traffic is sent over a [Mysterium](https://github.com/mysteriumnetwork/node) VPN connection.

This stack gets you a private deep research agent with a clean web interface.

## Deep Research Mode Support

The main reason I created this stack is because none of the private chat providers offer a "Deep Research Mode" (aka multi-agent multi-round research report generation), and I didn't like going back to non-private chat providers when I needed this functionality.

The Deep Research Mode in Onyx is optional, but if you do not need intense multi-agent deep research functionality at all, your best option is [TinFoil](https://tinfoil.sh), which has an excellent [security architecture](https://tinfoil.sh/security-and-privacy-faq) and decent cross-device app support, with encrypted syncing of chats.

## Components

The Docker Compose files in this stack rely on four components:

1. [Onyx](https://github.com/onyx-dot-app/onyx) provides a [top-ranking Deep Research Agent](https://huggingface.co/spaces/muset-ai/DeepResearch-Bench-Leaderboard), with a decent web interface and comprehensive connector and RAG support. While other open source deep research agents rank slightly higher than Onyx, it is the only provider-neutral option with a complete user interface.

2. [Teep](https://github.com/13rac1/teep) provides private verified LLM inference via a local OpenAI-compatible proxy on port 8337. Teep supports [multiple private inference providers](https://github.com/13rac1/teep#supported-providers), and verifies attestation, encryption, and remote runtime properties before requests are allowed to proceed.

3. [SearXNG](https://github.com/searxng/searxng) is an open source meta-search engine that provides API search for multiple back ends (Bing, DuckDuckGo, Brave, and Google are enabled by default).

4. [Mysterium](https://github.com/mysteriumnetwork/node) is a Wireguard dVPN that accepts cryptocurrency payment and has a large pool of residential endpoints. The use of residential IP addresses reduces the rate of captchas and rate limiting by search engines and websites. Mysterium server-side code is open source and contains no centralized data retention. No comparable Zero Data Retention options are available to end-users. (Firecrawl, Exa, and Brave retain all user API activity and do not offer ZDR to consumers).

## Prerequisites

- Docker or Podman
- Internet access for image builds and provider APIs
- `make`

## Configure Environment

Edit `.env.wrapper` as needed:

- Host ports:
  - `HOST_PORT` (Onyx UI bridge)
  - `SEARXNG_PORT`
  - `TEEP_PORT`
  - `DOC_DROP_WEB_PORT` (local docs bridge for Web connector)
- Provider/API config:
  - Set at least one teep key (for example `NEARAI_API_KEY`, `VENICE_API_KEY`, or `CHUTES_API_KEY`)
- Optional local documents directory for Web connector indexing:
  - `DOC_DROP_DIR` (host directory to expose read-only, default `./doc-drop`)
- Optional Myst provider pinning:
  - `MYST_PROVIDER_IDS`
- **Optional LAN access** (for local inference APIs):
  - Set `ALLOW_LAN_ACCESS=true` to allow access to local network addresses (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) without routing through the VPN. Useful for accessing LLMs running locally (e.g., LMStudio) while maintaining fail-closed behavior for all other traffic. Default: `false`
- Optional Myst funding order auto-creation:
  - `MYST_ORDER_AMOUNT`
  - `MYST_ORDER_CURRENCY`
  - `MYST_ORDER_GATEWAY`
  - `MYST_ORDER_COUNTRY`
  - `MYST_WAIT_FOR_FUNDS`

## Running the Stack

The stack comes in two flavors: lite and full. This specifies the mode of the Onyx app. Lite mode provides Chat, Web, and Research only. Full mode also provides RAG, external app connectors, and groupware. Lite mode uses significantly less RAM (~2GB vs ~20GB).

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

2. Once Mysterium VPN successfully connects, Onyx will need to be configured to use teep via its Web-based Admin Interface. The BiFrost provider is compatible. Use `http://127.0.0.1:8337/v1`. The models supported by your API key from `.env.wraper` should then be listed if you refresh the dropdown.

3. Mysterium residential providers can be flaky. Once you find one that works well, you may want to pin its identity via `MYST_PROVIDER_IDS` in `.env.wrapper`. Multiple providers can be listed, separated by commas.

### Local Documents via Web Connector

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
- In this wrapper, `WEB_CONNECTOR_VALIDATE_URLS` is set empty for Onyx backend
  containers so localhost crawling is allowed for this local-only use case.

#### Local Embedding Shim (release images)

This shim is enabled in **full mode only** (`make up-full`). It is not started
for lite mode (`make up-lite`).

If you want query/passage prefix control for embeddings while running stock
Onyx release images, use the local embedding shim path.

Why: Onyx's LiteLLM/cloud embedding path rejects manual prefix strings. The shim
keeps Onyx on the local model-server code path and forwards to a local
OpenAI-compatible `/v1/embeddings` API.

Required `.env.wrapper` settings:

```bash
# Query/passage prefixes used by the shim
ONYX_EMBEDDING_QUERY_PREFIX="Instruct: Given a document query, retrieve the most relevant chunk.\nQuery: "
ONYX_EMBEDDING_PASSAGE_PREFIX=""

# Local endpoint (adjust host/port to your embedding server)
LOCAL_EMBEDDINGS_URL="http://host.docker.internal:12340/v1/embeddings"

# Optional
#LOCAL_EMBEDDING_API_KEY=""
#LOCAL_EMBEDDING_MODEL="text-embedding-qwen3-embedding-4b"
```

Restart services after editing:

```bash
make up-full
```

Index Settings configuration in Onyx Admin:

1. Go to **Index Settings**.
2. Stage/select your embedding model as **Self-Hosted / Custom Model** (local)
  so Onyx uses the local model-server path.
3. Use the same model name as your local embedding model (for example
  `text-embedding-qwen3-embedding-4b`).
4. Do **not** configure embeddings through LiteLLM for this flow.

Quick verification:

```bash
# Shim health
curl -sSf http://localhost:9101/health

# Live debug logs (text type, prefix source, vector dims)
docker compose --env-file .env.wrapper logs -f local-embedding-shim
```

## Inference Provider Recommendations

The best privacy preserving provider supported by teep is currently `neardirect`, which is the direct completions version of [NearAI](https://cloud.near.ai). NearAI is also useful in that it can be used with cryptocurrency.

Teep will also soon add support for [Tinfoil](https://tinfoil.sh), which also has an excellent security architecture, as well as excellent mobile and web apps.

This stack can also be used with LMStudio or any other local LLM provider. Simply use `host.docker.internal` to connect to your localhost instance, using the Onyx Admin UI configuration. The OpenAI Compatible endpoint in Onyx works the best.

## LLM recommendations

Verifiable private inference is only currently possible with Open Weight models. While it is [technically possible](https://www.anthropic.com/research/confidential-inference-trusted-vms) for closed weight models to support attestation-based verification, proprietary LLM labs [do not seem to be interested](https://www.anthropic.com/news/activating-asl3-protections) in offering privacy to end users.

For a research agent like Onyx, the primary desirable property is a low hallucination rate. The [Artificial Analysis Omniscience Index](https://artificialanalysis.ai/evaluations/omniscience#aa-omniscience-hallucination-rate) provides a [Hallucination Rate benchmark](https://artificialanalysis.ai/evaluations/omniscience#aa-omniscience-hallucination-rate) that is worth tracking for this purpose.

Among Open Weight models currently supported by NearAI and Tinfoil, GLM-5.1 is the best option for text, and Kimi-2.6 is the best multimodal option.

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
