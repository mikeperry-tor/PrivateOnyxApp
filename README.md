# Private Onyx.App Docker Compose Set

This stack gets you a private deep research agent with code sub-agents and RAG document search, via a responsive web interface that you can access from anywhere.

The stack is built around [Onyx](https://github.com/onyx-dot-app/onyx) using [teep](https://github.com/13rac1/teep) for private verified LLM inference.

Search traffic starts at provider homepages and submits their search forms through [Obscura Browser](https://github.com/h4ckf0r0day/obscura) via customized [SearXNG](https://github.com/searxng/searxng) engines which round-robin to minimize search load and associated captchas.

Web browsing uses Onyx's stock requests/Playwright crawler by default, and can be switched to the Obscura Browser by an env preference. Direct `open_url` browser state remains request-scoped. Each SearXNG provider retains its own isolated browser session and target for up to one idle hour so cookies, the selected browser profile, and its target fingerprint remain stable within that provider session.

Users can optionally enable [Tor](https://www.torproject.org/) for the Agent's internet access. Tor onion service access is supported in the Tor egress mode. Alternatively, [Mysterium VPN](https://github.com/mysteriumnetwork/node) can be used for this purpose, or an upstream proxy, or both. In every mode, Docker/Podman network-namespace isolation forces traffic through the selected final-hop route to prevent host/LAN access, proxy bypass, and DNS leaks.

[Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel) integration allows you to access the instance remotely from anywhere, without the need for your client device to use the Tailscale VPN. Tailscale funnel is used in userland networking mode for the reverse proxy HTTPS service only: it does not create a Tailnet or use the Tailscale VPN itself, either.

A v3 Tor onion service can also be created for the stack. Onion access and public Tailscale access can be enabled simultaneously.

## Private Deep Research, RAG, and Code Agent Support

The main reason I created this stack is because none of the private chat providers offer "Deep Research" (aka orchestrated multi-agent multi-round research report generation), and I didn't like going back to non-private chat providers when I needed this functionality.

Additionally, the full mode of Onyx provides RAG search results to the agent from local collection of PDFs and other documents, and has a Code Agent tool that allows the chat agent to spawn multiple sub-agents to clone and investigate git repositories. Onyx has many other connectors as well.

If you do not need intense multi-agent deep research, code research subagents, and RAG functionality, your best option is [TinFoil](https://tinfoil.sh), which has an excellent [security architecture](https://tinfoil.sh/security-and-privacy-faq) and decent cross-device app support, with encrypted syncing of chats.

## Key Patches to Stock Onyx

In this stack, I [patched Onyx](./docs/onyx_patch_info.md) to improve several limitations and poorly performing edge cases:

- Onyx telemetry, third-party analytics and error reporting, cloud billing, CAPTCHA, and remote configuration are explicitly disabled.
- A more restrictive browser Content Security Policy now blocks third-party scripts, connections, frames, media, fonts, workers, and remote images from bypassing the stack's selected Tor/VPN/proxy via the user's browser. Additionally, this policy blocks Onyx WebUI queries to a Google favicon service for all sourced URLs in chat and research reports; generic icons are used instead.
- Stock Onyx strips agent reasoning between tool calls for most open-weight LLMs. This causes needless repeated re-thinking and degrades final answer quality. This has been patched.
- Stock Onyx strips tool call results upon user follow-up questions, which often makes LLMs think that they hallucinated the previous turn tool results. This has been patched.
- Stock Onyx removes query strings (`?`) and fragments (`#`) from web URLs, which prevents the agent from reading Hacker News `item?id=...` posts, YouTube `watch?v=...` video and comments, signed links, and all other query-addressed pages. This stack preserves complete URL through search, crawling, citations, and document matching.
- The "Deep Research" mode has been patched to provide the research sub-agents with RAG access and all configured tools, rather than the Onyx default of only web search and url retrieval.
- The "Deep Research" mode now also supports much longer research runs, and has been patched to execute all accepted tool calls when a research agent requests several different tools at once, rather than silently dropping some of them like stock Onyx does.
- The code sub-agent investigation summarization has been enhanced to summarize reasoning steps as well as output.
- Sub-agents are patched to choose whether to call another tool or finish, avoiding a forced-tool compatibility problem with vLLM for open weight models.
- RAG document re-indexing is patched to skip re-downloading and re-parsing unchanged local files, making re-indexing substantially faster than stock Onyx.
- Onyx's idle background CPU workload is reduced by running discovery and housekeeping less often, removing unused monitoring and disabled-feature work, keeping lightweight control processes out of application bootstraps, and keeping optional Slack/Discord bot processes off unless enabled with `ONYX_AGENT_SLACK_BOT` or `ONYX_AGENT_DISCORD_BOT`.
- Onyx Agent tool descriptions have been patched to describe an additional SymPy package, reinforce correct image link creation, and describe network access in coding environments when it is enabled.
- The Onyx installation process and the wider stack lifecycle are adapted to additionally support rootless Podman, including selected-engine image preparation, Compose routing, startup-health handling, and shared-data safeguards when switching between Docker and Podman.

I intend to merge these upstream at some point, once I stop finding new edge cases and the dust settles a bit.

## Core Components

The Docker Compose files in this stack relies on the following components:

1. [Onyx](https://github.com/onyx-dot-app/onyx) provides a [top-ranking Deep Research Agent](https://huggingface.co/spaces/muset-ai/DeepResearch-Bench-Leaderboard), with a decent web interface and comprehensive connector and RAG-based local document search support. While other open source deep research agents rank slightly higher than Onyx, it is the only provider-neutral option with a complete user interface that works well with both mobile and desktop web browsers.

2. [Teep](https://github.com/13rac1/teep) provides private verified LLM inference via a local OpenAI-compatible proxy on port 8337. Teep supports [multiple private inference providers](https://github.com/13rac1/teep#supported-providers), and verifies attestation, encryption, and remote runtime properties before requests are allowed to proceed.

3. [Tor](https://hub.docker.com/r/dockurr/tor) can be optionally used to route agent Internet traffic through native Tor egress, expose the WebUI as a persistent v3 onion service, or provide both roles concurrently. When Tor is enabled, the Agent can also access onion service URLs.

4. [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel) is a free service that creates a reverse proxy to access the Onyx web interface via HTTPS from any web browser, using your assigned public `ts.net` service subdomain name. The TLS key is generated locally in this stack and is signed with Let's Encrypt. This means that Tailscale's infrastructure is unable to read the contents of your remote communications to the instance.

5. [Mysterium](https://github.com/mysteriumnetwork/node) is an optional WireGuard dVPN that accepts cryptocurrency payment and has a large pool of residential endpoints. It is disabled by default. Enabling it can reduce captchas and rate limiting by search engines and websites through residential exit addresses. Mysterium server-side code is open source and contains no centralized data retention. No comparable Zero Data Retention options are available to end-users to reduce captcha and ban frequency. (Firecrawl, Exa, and Brave retain all user API activity and do not offer ZDR to consumers).

6. [Obscura Browser](https://github.com/h4ckf0r0day/obscura) provides all custom search engines, and optionally the built-in Onyx Web Crawler. Search uses a homepage navigation followed by that page's native form submission; Bing may repeat that flow once for page two when its first page has fewer than five valid results. `open_url` retains its single-navigation contract. Obscura supplies anti-fingerprinting defenses without an HTTP prefetch or local-browser fallback. Obscura and SearXNG run on narrow internal networks; browser traffic crosses a fixed bridge to a destination-validating final-hop proxy that ensures public internet access.

7. [SearXNG](https://github.com/searxng/searxng) is an open source meta-search engine. It is patched to issue queries in round-robin fashion to Google, Brave, DuckDuckGo No-AI, Startpage, and Bing, accessed through Obscura Browser. If an attempt produces no usable result, SearXNG may continue sequentially with a different provider; it never retries a failed provider within that search. Bing's bounded page-two fetch is part of one successful provider attempt. Providers are suspended after visible anti-bot failures or rate-limit responses.

8. [mlx-embeddings](https://github.com/Blaizzy/mlx-embeddings) is optionally installed for local embeddings on MacOS, for RAG document search. Other local embedding providers are supported but not recommended due to accuracy and API issues. Teep can also be used for private embeddings on non-Mac hosts.

## Prerequisites

- Docker Engine API 1.44+ (Engine 25.0+) or rootless Podman. Podman 5.4.2 is
  the currently validated baseline; older versions may work when the startup
  checks pass.
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

Before the first start, copy `.env.wrapper.example` to `.env.wrapper`.

Edit `.env.wrapper` as needed, based on the `.env.wrapper.example` template.

Mandatory configuration:

- **Teep LLM Provider/API config**:
  - Set at least one real teep key to use teep in `.env.wrapper`. [Tinfoil](https://tinfoil.sh/) and/or [NearAI](https://cloud.near.ai/) are recommended. Both have excellent TEE attestation coverage.
  - You can [configure Onyx](#onyx-admin-ui-configuration) to use another inference provider other than teep, but one of these teep API keys must have a non-empty value for the stack to start. This value can be a placeholder (which is the default).

Other key variables you may want to change:

- **Container engine selection**:
  - Set `CONTAINER_BIN=podman` to use Podman instead of Docker.
  - You must perform a clean `make down-*` when switching engines; this is enforced
    in the stack's startup code to prevent shared database corruption in the
    bind-mounted `docker-data` directory. Otherwise, switching between engines is safe
    to do at a later point.
  - On rootless Podman, the code interpreter tool and the code subagent are unavailable,
    due to docker-from-docker launch compatibility issues.
- **Tor, VPN, and Proxy Use**:
  - Set `TOR_EGRESS_ENABLED=true` to route public agent Internet traffic through native Tor, and/or set `TOR_ONION_SERVICE_ENABLED=true` to publish the WebUI as a v3 onion service. `TOR_EXIT_COUNTRY` or `TOR_EXIT_NODE_FINGERPRINTS` may optionally constrain clearnet exits.
  - Set `MYST_VPN_ENABLED=true` to enable the optional Myst VPN, then complete the [Myst VPN Setup](#optional-myst-vpn-setup) below before starting the stack.
  - Set `EGRESS_UPSTREAM_PROXY_URL` to use an upstream proxy with or without the Mysterium VPN enabled. You can use your host Tor Browser SOCKS port here instead of launching the built-in Tor container.
  - Native `TOR_EGRESS_ENABLED=true` Tor egress [cannot currently](./docs/plans/deferred/https_proxy_after_tor.md) be combined with `EGRESS_UPSTREAM_PROXY_URL`, but it can run alongside Myst.
  - Use `TEEP_ROUTE_THROUGH_MYST_VPN=true` and/or `TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN=true` to route teep or tailscale through Myst.
    - Tor egress does not currently support routing Teep provider traffic or Tailscale traffic through Tor. Both tools currently lack comprehensive proxy support.
- **Docker-host integration ports and optional LAN access**:
  - `ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS` selects which TCP ports configured integrations may reach at exact `host.docker.internal`.
    - Ollama, LM Studio, MCP servers, and other custom host services require their actual port in the list when used for anything other than `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL`.
  - Set `ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true` to let [Onyx MCP servers](#optional-external-mcp-servers) and [Onyx LLM inference](#onyx-llm-configuration) reach endpoints on your private LAN.
  - MCP access to either kind of private endpoint also requires the saved Onyx **Admin → Security Hardening** setting described in the [MCP instructions](#optional-external-mcp-servers).
  - **The agent's tools still cannot access your host or LAN**, regardless of any host or LAN configuration option value. This includes the code agent and code interpreter tool; even if you [grant network access to coding tools](#optional-network-access-for-the-code-interpreter). This is enforced through this stack's docker service network isolation, not Onyx code.
    - Even if the Onyx application code itself becomes fully compromised, it can reach only explicitly configured or allowed host endpoints.
  - **Enabling LAN access additionally will allow a compromised Onyx service to access anything on your LAN.**

## Onyx Admin UI Configuration

Once the stack starts, configure Onyx via its [Web-based Admin Interface](http://localhost:3000/admin/configuration/language-models).

### Onyx LLM Configuration

For LLM inference, select the **OpenAI-Compatible** provider type for teep.

> This stack's Onyx patches preserve active-turn assistant reasoning as OpenAI-compatible `reasoning_content`/`reasoning` fields by default, and the OpenAI-Compatible provider keeps teep's raw model IDs on that request path, preventing LiteLLM "fixups".

Use `http://teep:8337/v1` as the OpenAI baseurl.

The models supported by your API key from `.env.wrapper` should then be listed if you refresh the dropdown.

### Inference Provider Recommendations

The best privacy preserving provider aliases in teep are currently `neardirect` and `tinfoil_v3_direct`, which are the direct-connection versions of [NearAI](https://cloud.near.ai) and [Tinfoil.sh](https://tinfoil.sh), respectively.

This stack can also use a local OpenAI-compatible, LM Studio, or Ollama chat endpoint through `host.docker.internal` or an explicitly enabled RFC1918 IP address. For local inference: in `.env.wrapper` set `ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS` for host inference ports; set`ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true` for LAN inference. RFC1918 names must end in `.local`, `.internal`, or `.home.arpa`; otherwise use
literal IP addresses. An endpoint on `host.docker.internal` does not require the
LAN setting, but its port must be selected.

### LLM recommendations

Verifiable private inference is only currently possible with Open Weight models. While it is [technically possible](https://www.anthropic.com/research/confidential-inference-trusted-vms) for closed weight models to support attestation-based verification, proprietary LLM labs [do not seem to be interested](https://www.anthropic.com/news/activating-asl3-protections) in offering privacy to end users.

Among Open Weight models currently supported by NearAI and Tinfoil, GLM-5.2 is the best option for text, and Kimi-K2.6 is the best option for text+images.

> For a research agent like Onyx, the primary desirable property is a low hallucination rate. The [Artificial Analysis Omniscience Index](https://artificialanalysis.ai/evaluations/omniscience#aa-omniscience-hallucination-rate) provides a [Hallucination Rate benchmark](https://artificialanalysis.ai/evaluations/omniscience#aa-omniscience-hallucination-rate) that is worth tracking for this purpose.

Set `ONYX_AGENT_LLM_MAX_TOKENS` in `.env.wrapper` to the limit you want the stack to use (the default 900000 is good for GLM-5.2; 250000 is good for Kimi K2.6).

### Onyx Search and Web Crawler Provider Configuration

Select SearXNG and the built-in **Onyx Web Crawler** in the [Web Search Admin Panel](http://localhost:3000/admin/configuration/web-search):

1. Go to **Admin Panel -> Web Search -> Web Crawler**.
2. Open **SearXNG** and click **Connect**
3. Set the **SearXNG Base URL** to `http://searxng-service-gateway:8888`.
4. Open **Onyx Web Crawler**, click **Connect**, then **Set as Default**.

The stock Onyx Web Crawler appears to be blocked less often than Obscura v0.1.10 by websites, but you can set `ONYX_AGENT_USE_OBSCURA_BROWSER=true` to cause the Onyx Web Crawler to use the Obscura Browser instead of Onyx's internal fetch + Chromium Playwrite fallback.

SearXNG always uses Obscura. Each search provider keeps its own browser session
and target for up to one hour after its last query, preserving provider cookies,
profile/fingerprint state, and connection continuity without sharing state with
another provider. `SEARXNG_TIMED_TYPING_PROVIDERS` can opt selected providers
into experimental timed key entry; it adds latency and can disclose successive
query prefixes through provider autocomplete.

In either case, Docker Compose network-namespace routing restricts egress to the selected final hop (Tor exit, VPN, or proxy). This is the case for all search traffic as well. For the request flows, distinct browser navigation contracts, limits, and failure behavior, see [`docs/request_handling.md`](docs/request_handling.md).

Selecting Firecrawl or Exa for Web, or Brave, Serpa, Exa, or Google PSE for Search, is supported. Connections to these services use the selected Tor/VPN/proxy route, but these external providers perform their accesses from their own IP address space. None of these providers offer ZDR policies to consumer end users, so your API key and account on these services will be associated with your usage activity, and this data will be stored, trained on, and/or sold by these providers. A nice rant about this situation can be found at the [end of this README](#the-anti-bot-landscape-is-also-anti-privacy).

## Optional Configurations

The following sections detail optional feature configuration, including native Tor, remote access via Tailscale Funnel, an outbound proxy, Myst VPN, and RAG document search.

### Optional: Native Tor

The stack can optionally start one pinned Tor client for either or both egress and onion service ingress.

For egress:

```dotenv
TOR_EGRESS_ENABLED=true
TOR_EXIT_COUNTRY=""
TOR_EXIT_NODE_FINGERPRINTS=""
```

`TOR_EGRESS_ENABLED=true` routes the existing public final-hop policy paths
through a private Unix SOCKS socket. Target DNS is owned by Tor, and a stopped
Tor daemon or unavailable selected exit fails closed. Native Tor egress permits
`http://` destinations whose host ends in `.onion` without enabling general
clearnet HTTP.

`TOR_EXIT_COUNTRY` accepts one two-letter country selector.
`TOR_EXIT_NODE_FINGERPRINTS` accepts up to 16 comma-separated 40-hex relay
identities; it cannot be combined with the country selector. Both require Tor
egress. Selection is strict: no matching usable exit means requests fail rather
than falling back. Country and especially relay pinning can reduce availability
and anonymity-set diversity.

Native Tor egress currently covers agent-controlled and configured-external
final-hop policy paths only. It does not route Teep provider connections or
Tailscale traffic, and there are no Teep-through-Tor or Tailscale-through-Tor
options. Both components use their direct routes by default or may be routed
through Myst with their component-specific settings.

#### Optional Tor Onion Service Ingress

To publish an onion service for access to the Onyx WebUI, set `.env.wrapper`
value `TOR_ONION_SERVICE_ENABLED=true`. This can be set independently from
`TOR_EGRESS_ENABLED=true`; you can access the stack via an onion service
without the Agent using Tor for its own internet access.

Onion ingress creates a v3 onion service upon first use. Retrieve the created
address while the stack is running with:

```bash
make tor-onion-address
```

The identity is stored under `docker-data/tor/state`; back it up deliberately
and protect it like a server credential. Do not delete it unless you intend to
replace the onion address. Copying the onion-service files copies the service
identity and allows the holder to operate that same onion address. Docker and
Podman share this host state; switching between them will preserve it.

To make the onion service the authoritative WebUI URL, first retrieve its
address, then set `WEBUI_CANONICAL_ORIGIN=http://your-address.onion` in
`.env.wrapper` and restart the stack. Onyx will use that onion URL for
invitation, verification, password-reset links, and generated absolute links.
This URL will also be used for identity-provider and MCP OAuth callbacks; and
origin-checked voice WebSockets, though neither of these have been verified to
work (they likely will not).

> The onion URL uses HTTP because Tor provides the authenticated and encrypted connection. Selecting it as canonical leaves authentication and CSRF cookies without the `Secure` attribute on every ingress, including HTTPS Tailscale.

Tailscale, onion, and localhost host-side access can be used simultaneously, but
each hostname has separate browser cookies, storage, and login sessions. Logout
on one hostname does not log out the others.

### Optional: Tailscale Funnel

You can publish the Onyx WebUI through Tailscale Funnel to access it remotely via any web browser. The WebUI is responsive and works fine on phones and tablets.

Tailscale Funnel prerequisites in your Tailscale account admin portal:

- MagicDNS enabled
- HTTPS certificates enabled for your tailnet
- Funnel node attribute enabled for your user/device in ACL policy

To set this up, in `.env.wrapper`, set `TAILSCALE_FUNNEL_ENABLED=true` and set `TAILSCALE_FUNNEL_AUTHKEY` using a free auth key created at [Tailscale Admin Settings Keys Page](https://login.tailscale.com/admin/settings/keys).

Bring the stack up as usual (`make up-lite` or `make up-full`).

Your Onyx WebUI will then be available publicly at `https://onyx.your-tailnet.ts.net`.

To make this Tailscale URL the authoritative WebUI URL, also set
`WEBUI_CANONICAL_ORIGIN=https://onyx.your-tailnet.ts.net`, using the actual
Funnel hostname. Onyx will use that URL for invitation, verification, and
password-reset links; generated absolute links; identity-provider and MCP OAuth
callbacks; and origin-checked voice WebSockets. Update any externally registered
MCP callback URLs after changing it.

> The HTTPS canonical origin marks authentication and CSRF cookies `Secure` globally. This protects the Tailscale cookies from downgraded HTTP connections, but prevents login through the HTTP onion URL and `http://localhost:3000`. Voice WebSockets work only from the Tailscale hostname, and absolute links opened from another hostname lead to Tailscale and its separate browser session.

By default, the Tailscale service does not route through Mysterium VPN, to avoid linking your Tailscale account to your search activity at the Myst VPN exit server. To route Tailscale through the VPN namespace instead, set `TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN=true` in `.env.wrapper`.

Tailscale Funnel and Tor onion ingress can be enabled concurrently as separate entry points, but Tailscale itself cannot currently be routed through Tor.

#### Optional: Network Access for the Code-Interpreter

By default, Onyx's code-interpreter (the `onyxdotapp/code-interpreter` image from [onyx-dot-app/python-sandbox](https://github.com/onyx-dot-app/python-sandbox)) selects Docker's `none` network for every executor pod it spawns. Each pod is temporary and
least-priv sandboxed.

You can optionally give these executor pods public internet access by setting:

```bash
# Give code-interpreter executor pods network access
# Executors remain isolated from stack, host, LAN, and direct internet routes.
ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true
```

As noted previously, network access is still restricted to public internet endpoints and routed through the Myst VPN/Tor/Proxy. Tor onion access is allowed with `TOR_EGRESS_ENABLED=true`. This tool is still prevented from accessing the host or LAN, regardless of any configuration setting.

### Optional: Outbound Stack Proxy

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

A configured proxy at exact `host.docker.internal`, an RFC1918 literal, or an
operator-local `.local`, `.internal`, or `.home.arpa` name does not require a
host integration port or `ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true`.
Invalid upstream proxy URLs fail policy-proxy startup.

> Choosing a host- or LAN-based upstream proxy does not give agent browsing or generated code permission to access other host or LAN destinations, unless that upstream proxy can route to them (for example, a proxy on your LAN that allows LAN access).

### Optional: Myst VPN Setup

Myst is disabled by default. Skip this section unless you have explicitly set `MYST_VPN_ENABLED=true` in `.env.wrapper`. In the default no-VPN mode, no Myst daemon, wallet, identity, registration, or payment is required; a lightweight sentinel only validates the shared namespace's direct route, and `make up-lite` / `make up-full` proceeds directly to starting the stack.

The Mysterium VPN requires a funded wallet (paid in cryptocurrency) before it can connect. The signup process is handled by a standalone container that creates a cryptographic identity and registers it on-chain (Mysterium sponsors the gas fees).

Note that because our usage of Mysterium is crypto-native, a normal Mysterium VPN app subscription won't work here. However, the good news is that crypto-native Mysterium is _considerably_ cheaper than the app subscription fee, especially since this agent does not use much data, and there is no monthly fee or funds expiration. I've used less than 10 $MYST ($2 USD) in actual VPN fees since I started this project.

There are two ways to fund the wallet:

- **Option A - Order page (CoinGate):** Pay via a crypto payment gateway. Easiest for first-time users, but requires email, name, and address.
- **Option B - Direct blockchain transfer:** Transfer $MYST directly on Polygon. Cheaper (no gateway fees), but requires acquiring $MYST yourself.

Both options use the same standalone container and produce the same identity/keystore. You only need to run one.

#### Option A: CoinGate $MYST Order (Requires email, Name, and Address)

**Step 1: Run the signup process**

```bash
make vpn-signup-orderform
```

This launches a dedicated non-restarting Myst setup container, creates or selects one exact identity, submits at most one registration request, and creates one verified CoinGate payment order. The container can remain running while you complete payment; it does not retry registration or order creation in the background. The payment URL is displayed in a banner:

```
═══════════════════════════════════════════════════════════
PAYMENT URL: https://coingate.com/pay/invoice/abc123...
═══════════════════════════════════════════════════════════
```

The default order is for 100 $MYST, payable via CoinGate in several major cryptocurrencies. An email is required by the payment gateway. You can customize the order amount, currency, and gateway via `MYST_VPN_ORDER_*` variables in `.env.wrapper`. The command validates those values against Myst's current gateway list and will not silently choose another gateway or currency.

**Step 2: Pay at the URL**

Open the payment URL in a browser and complete the cryptocurrency payment.

**Step 3: Check payment status**

```bash
make vpn-orderstatus
```

This refreshes and shows your identity balance, registration status, and all orders. A payment URL is displayed only for `initial` or `new` orders. A `paid` order with a zero balance is reported as settlement pending and never causes another order to be created. Repeat until your balance is non-zero. For a quick refreshed balance check:

```bash
make vpn-balance
```

**Step 4: Start the full stack**

```bash
make up-lite   # or make up-full
```

With `MYST_VPN_ENABLED=true`, this automatically stops the standalone signup container (your wallet data is preserved) and starts the full stack. If no Myst identity is found, it will tell you to run `make vpn-signup-orderform` or `make vpn-signup-blockchain` first. This identity check does not apply in the default no-VPN mode.

**Notes:**

- If the payment order fails or expires before you pay, run `make vpn-signup-orderform` again explicitly. It reuses your selected identity and creates a replacement only after an authoritative order/balance check.
- If more than one identity exists, set `MYST_VPN_IDENTITY` in `.env.wrapper`; signup, status checks, and integrated startup all refuse to guess which wallet to use.
- Run `make vpn-signup-stop` if you want to stop the standalone setup container without starting the stack.
- Image preparation may take some time on the first start or after an update changes component versions or stack build inputs. `make up-lite` and `make up-full` reuse the exact selected images when they are already present.
- Mysterium residential providers can be flaky. While connected, run `make vpn-connection-info` to display the active provider identity. Once you find one that works well, you may want to pin its identity via `MYST_VPN_PREFERRED_PROVIDER_IDS` in `.env.wrapper`. Multiple providers can be listed, separated by commas.
- Registration or order failures are printed by the command and do not restart the setup container. Ambiguous order results are never retried automatically; run `make vpn-orderstatus` before deciding whether to try again.

#### Option B: Direct $MYST Transfer (Skip the Order Page)

You can fund your wallet by transferring $MYST tokens directly on-chain, bypassing the CoinGate order page entirely. This is cheaper (no gateway fees) and works with any wallet or exchange that supports Polygon ERC-20 transfers.

**Important: Do NOT send $MYST to your identity address.** The Mysterium node tracks balance on a deterministic **consumer channel** contract, not the raw ERC-20 balance of your identity. Sending tokens to the identity address will not credit your balance and the funds will be stuck.

**Chain:** Polygon Mainnet (Chain ID 137). The default mainnet chain is Polygon, as defined in the Mysterium node metadata (`DefaultChainID: 137`). Ethereum Mainnet (Chain ID 1) is also supported by the node, but the default consumer flow uses Polygon.

**Step 1: Run the blockchain signup**

```bash
make vpn-signup-blockchain
```

This launches the same standalone Myst container as `make vpn-signup-orderform`, creates a new identity (or reuses an existing one), registers it on-chain (Mysterium sponsors the gas fees), and prints your **channel address** - the address you must send $MYST to. No payment order is created.

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

The node polls the on-chain channel balance and will reflect the transfer once the Polygon block confirms. Both `make vpn-balance` and `make vpn-orderstatus` explicitly refresh the balance. If it remains zero, leave the setup container running and try the read-only status command again later; do not submit another transfer merely because settlement is delayed.

### Optional: Local Document RAG via Web Connector

The full version of Onyx supports search and retrieval (RAG) over PDF, DOC, EPUB, and other document types.

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
# Install mlx-openai-server and mlx-embeddings, download the model, and verify it
make embedserv-install
# Start full mode; this also launches the installed MLX lifecycle proxy
make up-full
```

To re-run model integrity verification independently later, use
`make embedserv-verify-model`.

You can select a different model via `ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL` in
`.env.wrapper`, using the huggingface ID of any MLX-packaged embedding model.

After `make embedserv-install` has installed the selected model, `make up-full`
automatically launches a lightweight lifecycle proxy at the bundled default
endpoint, `http://host.docker.internal:3210/v1/embeddings`. It loads the MLX
model for the first embedding request and unloads it ten minutes after the last
request completes.

Full mode skips MLX launch with a custom `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL` value.

> The stack uses `mlx-embeddings` because llama.cpp embeddings support is very buggy (including many subtle accuracy drift bugs, especially under concurrency load and batched embeddings). LM Studio's embedding support is similarly problematic, but worse. For some reason, embedding services recieve little attention from the open source community; `mlx-embeddings` is a rare standout. You are better off with teep than any other alternative option.

See the [Onyx Embedding Configuration](#optional-embedding-model-configuration-in-onyx) for
information on how to configure Onyx to use this endpoint; it is not exactly straight-forward.

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
user-configurable addressing surface for this option. Full mode permits only
this configured authority automatically, including the actual
`HOST_PORT_TEEP` value in the URL.

### Optional: Embedding Model Configuration in Onyx

Properly configuring an open-weight frontier embedding model for RAG is a minefield. Almost no one gets it right, including Onyx and LiteLLM. Do **not** configure embeddings through LiteLLM.

To configure embedding support in Onyx:

1. Go to [Onyx Admin Index Settings](http://localhost:3000/admin/configuration/index-settings)
2. Select your embedding model as **Self-Hosted / Custom Model** (local)
3. Enter `nomic-ai/nomic-embed-text-v23` as the model type. This synthetic name
   intentionally enables Onyx's hardcoded `nomic-ai` RAG features. The stack
   maps only its tokenizer to the bundled nomic v1 tokenizer; the embedding
   shim still sends requests to the configured real upstream model.
4. For both `Harrier-OSS-V1-0.6B` and `Qwen3-Embedding-0.6B`, the embedding dimension is 1024.

This Onyx configuration choices cause the stack's patches to route embedding requests to either the MLX embeddings or teep model that you configured above, using correctly formatted query and indexing prefixes.

> Frontier embedding models require an instruction prefix when generating queries, but unfortunately, Onyx has an issue with handling this prefix for generic LLM providers. To address this, the stack contains a local shim which allows you to set the query prefix via an environment instead. The prefixes in `.env.wrapper.example` should be good for either Harrier or Qwen3.

### Optional: External MCP servers

You can add any MCP servers you operate or trust through Onyx Admin.

For an MCP server running on the same host as Docker or Podman:

1. Make the MCP server reachable through the container engine's host gateway:
   - If the server runs directly on a native Linux host, the portable choice is
     to make it listen on `0.0.0.0:<port>`. This includes the engine's host
     gateway interface; use host firewall rules to reject unwanted LAN or
     public access to that port. For a narrower listener, bind to the actual
     host-gateway address reported by the selected engine (often `172.17.0.1`
     but do not hardcode this: distribution Docker daemon configuration can
     use a different address). A host process bound only to the host's
     `127.0.0.1` is not reachable through `host.docker.internal` on native
     Linux.
   - If the server runs in a separate Docker or Podman container, make it
     listen on that container's network interface (commonly `0.0.0.0`) and
     publish its MCP port on a host address reachable through the engine
     gateway. Publishing on `0.0.0.0:<host-port>` is portable, but requires
     firewall or container-publication rules that prevent unwanted LAN or
     public access. `127.0.0.1` inside the MCP container refers only to that
     container; on native Linux, publishing only on the host's `127.0.0.1` is
     also not reachable through `host.docker.internal`. Use the published host
     port below, not the container's private address or name.
2. Add its port to `ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS` in `.env.wrapper`,
   restart the stack, and configure its Onyx URL as
   `http://host.docker.internal:<port>/...`. Do not use `localhost`: from Onyx,
   that refers to the Onyx container itself.
3. In **Admin → Security Hardening**, save `Allow Private Network` (preferred)
   or `Disabled`.

For an MCP server on another private-LAN machine:

1. Set `ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true` in `.env.wrapper` and
   restart the stack. This broad opt-in is not needed for exact
   `host.docker.internal` access.
2. Configure the Onyx MCP URL with the server's RFC1918 address, or with an
   operator-local name ending in `.local`, `.internal`, or `.home.arpa`.
3. In **Admin → Security Hardening**, save `Allow Private Network` (preferred)
   or `Disabled`.

The Onyx security setting is required in both cases: it selects the
host-capable MCP route. `Validate All` and `Validate LLM` select the
public-only route, so an allowed host port or the LAN opt-in alone is not
enough.

The URL hostname is also the HTTP `Host` authority sent to the MCP server.
Consequently, a host-local server configured in Onyx as
`http://host.docker.internal:<port>/...` must accept
`host.docker.internal:<port>` (or that hostname, according to the server's
configuration) in any allowed-host/DNS-rebinding checks. A LAN server must
accept the LAN name or address used in its Onyx URL. Prefer adding the exact
authority to the server's allowlist over disabling Host validation globally.

> These MCP permissions do not extend to agent browsing or generated code. Onyx SSRF protections are redundant to this stack's network topology isolation, and less comprehensive as well.

## Upgrading the Stack

When you pull an update for this repository, ensure the stack is down first, and then
start the same stack mode normally:

```bash
make down-lite   # or make down-full
git pull
make up-lite     # or make up-full
```

That is all an end user needs to do.

The start command prepares any images selected by the updated
[`stack.versions.env`](stack.versions.env) or changed content-addressed build
inputs, reuses images that already match, and recreates affected services.
Persistent application data is retained. This works with either container engine
selected by `CONTAINER_BIN`: Podman mode prepares images in Podman's image
store, and Docker mode prepares images in Docker's image store.

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

If the host OS routes container-engine traffic through its own VPN, that route
acts as a first hop before connecting to Tor, the Myst VPN, or stack's proxy. By
default, the stack uses no VPN, no Tor, and no proxy. The stack's VPN, Tor, and
proxy settings must be configured in `.env.wrapper`.

With respect to web browser privacy, Obscura uses a stable browser
vendor/version profile. The wrapper keeps its seed-derived JavaScript
fingerprint stable for the lifetime of each retained search-provider target,
while the stack's default Onyx Web Crawler uses a fixed Chromium MacOS browser
configuration profile. `open_url()` transports remain request-scoped and do
not preserve browser state between calls. SearXNG is the narrow exception: it
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
