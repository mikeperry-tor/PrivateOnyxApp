# Private Onyx.App Docker Compose Set

Docker Compose wrapper for running [Onyx](https://github.com/onyx-dot-app/onyx) with the [teep](https://github.com/13rac1/teep) private verified LLM inference proxy and [SearXNG](https://github.com/searxng/searxng). All search, web, and inference traffic is sent over a [Mysterium](https://github.com/mysteriumnetwork/node) VPN connection.

This stack gets you a private deep research agent with a clean web interface.

## Components

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
- Provider/API config:
  - Set at least one teep key (for example `NEARAI_API_KEY`, `VENICE_API_KEY`, or `CHUTES_API_KEY`)
- Optional Myst provider pinning:
  - `MYST_PROVIDER_IDS`
- Optional Myst funding order auto-creation:
  - `MYST_ORDER_AMOUNT`
  - `MYST_ORDER_CURRENCY`
  - `MYST_ORDER_GATEWAY`
  - `MYST_ORDER_COUNTRY`
  - `MYST_ORDER_GATEWAY_DATA` (optional gateway metadata if required by provider)
  - `MYST_WAIT_FOR_FUNDS`

## Running the Stack

The stack comes in two flavors: lite and full. This specifies the mode of the Onyx app. Lite mode provides Chat, Web, and Research only. Full mode also provides RAG, external app connectors, and groupware. Lite mode uses significantly less RAM (~2GB vs ~20GB).

It is possible to switch between full and lite modes.

All persistent data is bind-mounted to subdirectories in `./docker-data`

If you use a VPN connection on your Docker Host, the Mysterium VPN will route *through* your host VPN, rather than along side it. If your host VPN does not support UDP (such as via Tails or TorVPN), it will not work.

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

1. Mysterium will create a new cryptographic identity, register it, and create an order URL on coingate for 100 $MYST (currently ~$20 USD). The order URL will be visible via `docker logs myst-client-vpn`.  This order URL can be paid in several different major cryptocurrencies, though an email is required. It may be possble to transfer $MYST directly to the VPN identity yourself, but I have not verified this.

2. Once Mysterium VPN successfully connects, Onyx will need to be configured to use teep via its Web-based Admin Interface. The BiFrost provider is compatible. Use `http://127.0.0.1:8337/v1`. The models supported by your API key from `.env.wraper` should then be listed if you refresh the dropdown.

3. Mysterium residential providers can be flaky. Once you find one that works well, you may want to pin its identity via `MYST_PROVIDER_IDS` in `.env.wrapper`. Multiple providers can be listed, separated by commas.

## Docker Host Endpoints

The following endpoints are exposed to your docker host:

- Onyx WebUI: [`http://localhost:3000`](http://localhost:3000)
- SearxNG WebUI: [`http://localhost:8080`](http://localhost:8080)
- teep human-readable stats page: [`http://localhost:8337`](http://localhost:8337)
- teep health check: `http://localhost:8337/health`
- teep prometheus metrics: `http://localhost:8337/metrics`
