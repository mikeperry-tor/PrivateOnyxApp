# Private Onyx.App Docker Compose Set

This stack gets you a private deep research agent with code sub-agents and RAG document search, via a responsive web interface that you can access from anywhere.

The stack is built around [Onyx](https://github.com/onyx-dot-app/onyx) using [teep](https://github.com/13rac1/teep) for private verified LLM inference.

Search traffic starts at provider homepages and submits their search forms through [Obscura Browser](https://github.com/h4ckf0r0day/obscura) via customized [SearXNG](https://github.com/searxng/searxng) engines which round-robin to minimize search load and associated captchas. Each SearXNG provider retains its own isolated browser session and target for up to one idle hour so cookies, the selected browser profile, and its target fingerprint remain stable within that provider session.

Web browsing uses Onyx's stock requests/Playwright crawler by default, and can be switched to the Obscura Browser by an env preference. Direct `open_url` browser state remains request-scoped and cleared for every request.

Users can optionally enable [Tor](https://www.torproject.org/) for the Agent's internet access. Tor onion service access is supported in the Tor egress mode. Alternatively, [Mysterium VPN](https://github.com/mysteriumnetwork/node) can be used for this purpose, or an upstream proxy, or both. Network network-namespace isolation prevents host/LAN access, proxy bypass, and DNS leaks. See [Privacy and Security](#privacy-and-security-of-this-stack) for more detail on these protections and their limitations.

[Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel) integration allows you to access the instance remotely from anywhere, without the need for your client device to use the Tailscale VPN. Tailscale funnel is used in userland networking mode for the reverse proxy HTTPS service only: it does not create a Tailnet or use the Tailscale VPN itself, either.

A v3 Tor onion service can also be created for the stack. Onion access and public Tailscale access can be enabled simultaneously.

## Improved Private Deep Research, RAG, and Code Agent Support

I created this stack because none of the private chat providers offer "Deep Research" (aka orchestrated multi-agent multi-round research report generation), and I didn't like going back to non-private chat providers when I needed this functionality.

In particular, Onyx has a Code SubAgent tool that allows the chat agent to spawn multiple sub-agents to clone and investigate multiple git repositories in parallel.

The ["full" mode](#full-mode) also provides RAG search results to the agent from a local collection of PDFs and other documents. The full mode of Onyx has many other connectors as well.

I have [extensively patched Onyx](./README_PATCHES.md) to improve many edge cases that degraded agent capability and performance, especially for generic OpenAI provider API endpoints.

## Core Components

The Docker Compose files in this stack relies on the following components:

1. [Onyx](https://github.com/onyx-dot-app/onyx) provides a [top-ranking Deep Research Agent](https://huggingface.co/spaces/muset-ai/DeepResearch-Bench-Leaderboard), with a decent web interface and comprehensive connector and RAG-based local document search support. While other open source deep research agents rank slightly higher than Onyx, it is the only provider-neutral option with a complete user interface that works well with both mobile and desktop web browsers.

2. [Teep](https://github.com/13rac1/teep) provides private verified LLM inference via a local OpenAI-compatible proxy on port 8337. Teep supports [multiple private inference providers](https://github.com/13rac1/teep#supported-providers), and verifies attestation, encryption, and remote runtime properties before requests are allowed to proceed.

3. [Tor](https://hub.docker.com/r/dockurr/tor) can be optionally used to route agent Internet traffic through native Tor egress, expose the WebUI as a persistent v3 onion service, or provide both roles concurrently. When Tor is enabled, the Agent can also access onion service URLs.

4. [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel) is a free service that creates a reverse proxy to access the Onyx web interface via HTTPS from any web browser, using your assigned public `ts.net` service subdomain name. The TLS key is generated locally in this stack and the certificate is signed with Let's Encrypt, without transmitting the private key to either Let's Encrypt or Tailscale. This means that Tailscale's infrastructure is unable to read the contents of your remote communications to the instance.

5. [Mysterium](https://github.com/mysteriumnetwork/node) is an optional open-source WireGuard dVPN that accepts cryptocurrency payment and has a large pool of residential endpoints. It is disabled by default. Enabling it can reduce captchas and rate limiting by search engines and websites through residential exit addresses. Mysterium server-side code is open source and collects VPN start/end times and aggregate byte counts, but does not collect exit outbound connection activity. No comparable Zero Data Retention options are available to end-users to reduce captcha and ban frequency. (Firecrawl, Exa, and Brave retain all user API activity and do not offer ZDR to consumers).

6. [Obscura Browser](https://github.com/h4ckf0r0day/obscura) provides all custom search engines, and optionally the built-in Onyx Web Crawler. Obscura supplies anti-fingerprinting defenses without an HTTP prefetch or local-browser fallback. Obscura and SearXNG run on narrow internal networks; browser traffic crosses a fixed bridge to a destination-validating final-hop proxy that ensures public internet access.

7. [SearXNG](https://github.com/searxng/searxng) is an open source meta-search engine. It is patched to issue queries in round-robin fashion to Google, Brave, DuckDuckGo, and Startpage, accessed through Obscura Browser. If an attempt produces no usable result, SearXNG will continue sequentially with a different provider. Providers are suspended after visible anti-bot failures or rate-limit responses. Bing is used as a last resort if all providers are blocked.

8. [mlx-embeddings](https://github.com/Blaizzy/mlx-embeddings) is optionally installed for local embeddings on MacOS, for RAG document search. Other local embedding providers are supported but not recommended due to accuracy and API issues. Teep can also be used for private embeddings on non-Mac hosts.

## Prerequisites

- Docker Engine API 1.44+ (Engine 25.0+), including native-Linux rootless
  Docker, or rootless Podman. Podman 5.4.2 is the currently validated baseline;
  older versions may work when the startup checks pass. Docker's daemon-wide
  `userns-remap` mode is detected and rejected; use ordinary or rootless Docker
  instead.
- Docker Compose 2.35.0 or later. This is required with both Docker and Podman.
- `make` and `python`
- `uv` when using the optional local MLX embedding server on macOS.

The Docker Compose version is most important. Many Linux distributions ship
with a docker-compose that is too old, and podman's podman-compose lacks key
features required by this stack.

Podman-only host does not need all of Docker installed, but it does
need an official Docker docker-compose binary v2.35.0 or later.

Check the version selected for your container engine:

```bash
# Docker
docker compose version

# Podman
podman compose version
```

If the selected version is older than 2.35.0, install or update the
[Docker Compose plugin](https://docs.docker.com/compose/install/linux/).
The manual per-user installation places the binary at
`~/.docker/cli-plugins/docker-compose`, where it can be used by either Docker
or Podman.

If you use podman on Linux, ensure that you enable Podman's rootless API
socket before starting the stack:

```bash
systemctl --user enable --now podman.socket
```

For rootless Docker on Debian or Ubuntu, first try
`docker --context rootless info --format '{{json .SecurityOptions}}'`. If it
includes `name=rootless`, select that context and skip installation. Otherwise,
install RootlessKit, then install and enable the per-user Docker service
(Docker Engine must already be installed):

```bash
sudo apt-get update
sudo apt-get install rootlesskit
dockerd-rootless-setuptool.sh install
systemctl --user enable --now docker.service
docker context use rootless
docker info --format '{{json .SecurityOptions}}'
```

The final output must include `name=rootless`. Debian's `docker.io` package may
place the setup tool in `/usr/share/docker.io/contrib`; add that directory to
`PATH` if the command is not found. If installation stops only because a
rootful daemon is already running and you intentionally want both daemons,
rerun the setup command with `--force`. The setup tool normally creates the
`rootless` context; if it does not, create it once with
`docker context create rootless --docker
"host=unix:///run/user/$(id -u)/docker.sock"`. Switch between the daemons with
`docker context use rootless` and `docker context use default`. To keep the
per-user daemon running across logout and start it at boot, also run
`sudo loginctl enable-linger "$USER"`.

## Running the Stack

The stack comes in two flavors: lite and full. This specifies the mode of the Onyx app. Lite mode provides Chat, Web, and Research only. Full mode also provides RAG, external app connectors, and groupware. Lite mode uses significantly less RAM (~1GB vs ~10GB).

It is possible to switch between full and lite modes between restarts.

### Lite Mode

Build and run lite mode:

```bash
make up-lite
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

### Makefile Commands

The stack consists of [several docker-compose layers](./compose_overlays/) that get applied depending upon your configuration options.

All docker/podman invocations should use the Makefile rules rather than direct `docker compose` cli usage, to avoid misapplying compose layers or misapplying `.env.wrapper` values.

For a synopsis of user-facing `make` rules, run:

```bash
make help
```

## First-run configuration

Before the first start, copy [`.env.wrapper.example`](./.env.wrapper.example) to `.env.wrapper`.

**Mandatory configuration**:

- Set at least one real teep key to use teep in `.env.wrapper`. [NearAI](https://cloud.near.ai/) is currently the only recommended provider, until Tinfoil resolves [security issues](https://github.com/tinfoilsh/cvmimage/pull/336#issuecomment-5331607827) that are [related to billing enforcement](https://github.com/tinfoilsh/cvmimage/issues/337).
- You can [configure Onyx](#onyx-admin-ui-configuration) to use another inference provider other than teep, but one of these teep API keys must have a non-empty value for the stack to start. This value can be a placeholder (which is the default).
- Set `CONTAINER_BIN=podman` to use Podman instead of Docker, but note that podman does not support the code agent sandboxing required by this stack. Rootless Docker 28+ is actually your most secure option.

You can read [`.env.wrapper.example`](./.env.wrapper.example) and [README\_OPTIONAL.md](./README_OPTIONAL.md) for additional options, but you likely want to get the basic stack running first.

## Onyx Admin UI Configuration

Once the stack starts, configure Onyx via its [Web-based Admin Interface](http://localhost:3000/admin/configuration/language-models).

### Onyx LLM Configuration

For LLM inference, select the **OpenAI-Compatible** provider type for teep.

> This stack's Onyx patches preserve active-turn assistant reasoning as OpenAI-compatible `reasoning_content`/`reasoning` fields by default, and the OpenAI-Compatible provider keeps teep's raw model IDs on that request path, preventing LiteLLM "fixups".

Use `http://teep:8337/v1` as the OpenAI baseurl.

The models supported by your API key from `.env.wrapper` should then be listed if you refresh the dropdown.

### Inference Provider Recommendations

The best privacy preserving provider aliases in teep are currently `neardirect` and `tinfoil_v3_direct`, which are the direct-connection versions of [NearAI](https://cloud.near.ai) and [Tinfoil.sh](https://tinfoil.sh), respectively.

> Unfortunately, Tinfoil has recently blocked usage of the direct provider connections due to a [billing issue](https://github.com/tinfoilsh/cvmimage/issues/337), and the `tinfoil_v3_cloud` router is [not safe to use](https://github.com/tinfoilsh/cvmimage/pull/336#issuecomment-5331607827) with the sandboxed code agents in Onyx.

This stack can also use a local OpenAI-compatible, LM Studio, or oMLX chat endpoint through `host.docker.internal` or an explicitly enabled RFC1918 IP address.

For local inference, in `.env.wrapper` set `ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS` to the host inference API port. For LAN inference, set`ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true`. Local network hostnames must end in `.local`, `.internal`, or `.home.arpa`; otherwise use literal RFC1918 IP addresses.

### LLM recommendations

> For a research agent like Onyx, the primary desirable property is a low hallucination rate. The [Artificial Analysis Omniscience Index](https://artificialanalysis.ai/evaluations/omniscience#aa-omniscience-hallucination-rate) provides a [Hallucination Rate benchmark](https://artificialanalysis.ai/evaluations/omniscience#aa-omniscience-hallucination-rate) that is worth tracking for this purpose.

Verifiable private inference is only currently possible with Open Weight models. While it is [technically possible](https://www.anthropic.com/research/confidential-inference-trusted-vms) for closed weight models to support attestation-based verification, proprietary LLM labs [do not seem to be interested](https://www.anthropic.com/news/activating-asl3-protections) in offering privacy to end users.

Among Open Weight models currently supported by NearAI and Tinfoil, [GLM-5.3-Flash](https://artificialanalysis.ai/models/glm-5-3-flash) currently is the best option by far. It is very fast, supports both text and images, and has the lowest hallucination rate in its capability class (even lower than [GLM-5.3](https://artificialanalysis.ai/models/glm-5-3)).

For low-RAM local inference, your best bet is [Qwen3.8-27B](https://artificialanalysis.ai/models/releases/qwen3-8-27b). With slighly more RAM, [Qwen3.8-Flash-Next](https://artificialanalysis.ai/models/qwen3-8-flash-next)
is a better choice. If you are interested in an uncensored version of either of these, [abliterlitics.dev provides a comprehensive comparison](https://abliterlitics.dev/models/qwen38-27b/#the-optimal-tradeoff).

Set `ONYX_AGENT_LLM_MAX_TOKENS` in `.env.wrapper` to the limit you want the stack to use (the default 900000 is good for GLM-5.3-Flash; 250000 is good for Qwen3.8 series).

### Onyx Search and Web Crawler Provider Configuration

Select SearXNG and the built-in **Onyx Web Crawler** in the [Web Search Admin Panel](http://localhost:3000/admin/configuration/web-search):

1. Go to **Admin Panel -> Web Search -> Web Crawler**.
2. Open **SearXNG** and click **Connect**
3. Set the **SearXNG Base URL** to `http://searxng-service-gateway:8888`.
4. Open **Onyx Web Crawler**, click **Connect**, then **Set as Default**.

The stock Onyx Web Crawler is the default reliability-oriented path, but you can set `ONYX_AGENT_USE_OBSCURA_BROWSER=true` to cause the Onyx Web Crawler to use the more isolated Obscura Browser instead of Onyx's internal fetch plus Chromium Playwright fallback.

SearXNG always uses Obscura. Each search provider keeps its own browser session
for up to one hour after its last query, preserving provider cookies,
profile/fingerprint state, and connection continuity without sharing state with
another provider. `SEARXNG_TIMED_TYPING_PROVIDERS` can opt selected providers
into experimental timed key-entry simulation of search input.

In either case, Docker Compose network-namespace routing restricts egress to the selected final hop (Tor exit, VPN, or proxy). This is the case for all search traffic as well. For the request flows, distinct browser navigation contracts, limits, and failure behavior, see [`docs/request_handling.md`](docs/request_handling.md).

Selecting Firecrawl or Exa for Web, or Brave, Serpa, Exa, or Google PSE for Search, is supported. Connections to these services use the selected Tor/VPN/proxy route, but these external providers perform their accesses from their own IP address space. None of these providers offer ZDR policies to consumer end users, so your API key and account on these services will be associated with your usage activity, and this data will be stored, trained on, and/or sold by these providers. A nice rant about this situation can be found at the [end of this README](#the-anti-bot-landscape-is-also-anti-privacy).

## Additional Optional Configuration

See [README\_OPTIONAL.md](./README_OPTIONAL.md) for instructions on configuring
native Tor support, remote access via Tailscale Funnel, an outbound proxy,
Myst VPN, and RAG document search.

## Upgrading the Stack

When you pull an update for this repository, ensure the stack is down first, and then
start the same stack mode normally:

```bash
make down-lite   # or make down-full
git pull
make up-lite     # or make up-full
```

Persistent application and user data is retained. After upgrading Docker to
version 28 or newer, also use this stop/start workflow to apply the stronger
host-access protection. Restarting Docker alone is insufficient.

If shutdown or startup fails, see the [network recovery guidance](docs/internal_network_security.md#docker-gateway-and-controller-boundary).

The start command prepares any images selected by the updated
[`stack.versions.env`](stack.versions.env) or changed content-addressed build
inputs, refreshes an existing bundled MLX environment when its committed
dependency/runtime fingerprint changes, reuses artifacts that already match,
and recreates affected services.

Published security refreshes also rebuild the affected executor,
code-interpreter, and Tailscale images. Their OS packages are updated during
the build; service startup does not install packages. Reload open browser tabs
after upgrading to pick up WebUI changes.

This works with either container engine selected by `CONTAINER_BIN`: Podman
mode compares and prepares images using Podman's image store, and Docker mode
compares and prepares images in Docker's image store.

Repository updates that change `stack.versions.env` have already performed
the required patch review, upgrade validation, and compatibility checks.
You do not need to run `make upgrade`, `make check-upgrade`, or any other
checks or tests.

If you edit `stack.versions.env` yourself, the version change is development
work rather than an ordinary stack update. Instruct a code agent to read
[`docs/onyx_patches_upgrade.md`](docs/onyx_patches_upgrade.md) to update the
stack for you. It will then review every affected patch and shim, perform the
upgrade, and complete the validation described there. The maintainers follow
that same process before committing any version-manifest update.

## Docker Host Endpoints

The following endpoints are exposed to your docker host:

- Onyx WebUI: [`http://localhost:3000`](http://localhost:3000)
- SearxNG WebUI: [`http://localhost:8080`](http://localhost:8080)
- teep human-readable stats page: [`http://localhost:8337`](http://localhost:8337)
- teep OpenAI API base: `http://localhost:8337/v1`
- teep health check: `http://localhost:8337/health`
- teep prometheus metrics: `http://localhost:8337/metrics`
- Full-mode local document display: [`http://localhost:8091`](http://localhost:8091)

## Privacy and Security of this stack

Teep's private verified inference keeps LLM query contents from inference providers,
while self-hosted browsing avoids sending search activity to commercial search
APIs.

The [network security](./docs/internal_network_security.md) of this stack is
robust, and [restricted selected-route egress](docs/vpn_routing_and_proxies.md)
is enforced with Docker Compose network namespaces, service isolation, and
least-privilege capability configuration.

These network restrictions are designed to contain agent-controlled activity:
web search, `open_url()`, browser requests, and optionally network-enabled
generated code cannot reach host, LAN, metadata, or stack-managed service
addresses. Unencrypted public HTTP url access is blocked by default; agents
may only access HTTPS urls, and Tor Onion services (if `TOR_EGRESS_ENABLED=true`).

These protections have platform limits. Docker 28+ provides the strongest
network protection, by further isolating the agent and browser network from the
Docker VM IP address and rpcbind port. Docker versions below 28 and Podman may
leave services on your computer or its container VM accessible if the Onyx core
API server is compromised, but agent generated code has no network path to this
service unless it exploits Docker's rpcbind service (which is available to all
docker containers when Docker 28+ network isolation is not in use). See the
[network security documentation](docs/internal_network_security.md#docker-gateway-and-controller-boundary)
for the tested protections and remaining risks.

If the host OS routes container-engine traffic through its own VPN, that route
acts as a first hop before connecting to Tor, the Myst VPN, or stack's proxy. By
default, the stack uses no VPN, no Tor, and no proxy. The stack's VPN, Tor, and
proxy settings must be configured in `.env.wrapper`.

With respect to web browser privacy, Obscura uses a stable browser
vendor/version profile. The wrapper keeps the randomized Obscura JavaScript
fingerprint stable for the lifetime of each retained search-provider target,
while the stack's default Onyx Web Crawler uses a fixed Chromium MacOS browser
configuration profile.  `open_url()` transports remain request-scoped and do not
preserve browser state between calls. SearXNG is the narrow exception: it
retains one native browser connection and target per search provider for up to
one hour after the last query, without sharing that state with other providers
or browser paths.

### The Anti-Bot Landscape is also Anti-Privacy

It may seem strange that a [Tor Project](https://www.torproject.org) employee created a private inference stack that provides a non-Tor dVPN option. This was a pragmatic choice to produce something that functioned well.

The reality is that many websites subject Tor and datacenter VPNs to increased captchas and bans compared to [residential IP addresses](https://acid.vegas/blog/the-shady-world-of-ip-leasing/). The most egregious example is Google's move to update [ReCaptcha to require an official Google device, while exempting "official" AI scrapers](https://www.financialexpress.com/life/technology-google-qr-captcha-controversy-explained-why-internet-is-scared-of-this-4237640/).

Clouldflare has "come to the rescue" with their [web bot auth](https://blog.cloudflare.com/web-bot-auth/) program and their [monetization gateway](https://blog.cloudflare.com/monetization-gateway/), but these systems do not natively support privacy of any kind. Web Bot Auth is basically "papers please" gated-registration for commercial entities, and current x402 micropayment specs are just another form of web tracking, except you're additionally publishing your browsing wallet activity on public blockchains. As a selling point, I guess, this browsing activity can be conveniently and publicly associated with any other purchases you may have made with that wallet. [We Live in Public](https://en.wikipedia.org/wiki/We_Live_in_Public) now, apprently. (Spoiler: that movie did not end well).

Personally, I do actually like micropayments as a concept. They would be vastly better than endless captchas, gated approval whitelists for big tech, and IP address bans for  self-hosted plebs and privacy-exiles. In fact, privacy-enhancing x402 micropayment middleware _do_ exist in [various](https://github.com/betterclever/zimppy/) [stages](https://github.com/DVB-ANRS/SecretPay) of [prototype](https://github.com/Micopay/micopay-protocol), but even x402 payments themselves do not yet have widespread adoption.

In the meantime, for users who need a residential IP address exit, Mysterium is the primary supported optional choice because its server side is open source and payment can be made in cryptocurrency.

An upstream proxy currently can also be configured with or without Myst VPN, but this proxy access is [not yet supported](./docs/plans/deferred/https_proxy_after_tor.md) via Tor. If you know of any proxy providers that suport ZDR, please [file a ticket](https://github.com/mikeperry-tor/PrivateOnyxApp/issues/new).

You can monitor SearXNG search-engine success statistics on the "Engines" tab of the [Preferences Pane](http://localhost:8080/preferences), if you want to test your success with Tor usage, other proxy providers, or your host VPN.
