# Optional Stack Configuration

The stack has several optional features that can be enabled independently,
including native Tor, remote access via Tailscale Funnel, an outbound proxy,
Myst VPN, and RAG document search.

## Optional: Native Tor

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

### Optional Tor Onion Service Ingress

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

## Optional: Tailscale Funnel

You can publish the Onyx WebUI through Tailscale Funnel to access it remotely via any web browser. The WebUI is responsive and works fine on phones and tablets.

Tailscale Funnel prerequisites in your Tailscale account admin portal:

- MagicDNS enabled
- HTTPS certificates enabled for your tailnet
- Funnel node attribute enabled for your user/device in ACL policy

To set this up, in `.env.wrapper`, set `TAILSCALE_FUNNEL_ENABLED=true` and set `TAILSCALE_FUNNEL_AUTHKEY` using a free auth key created at [Tailscale Admin Settings Keys Page](https://login.tailscale.com/admin/settings/keys).

Bring the stack up as usual (`make up-lite` or `make up-full`).

Your Onyx WebUI will then be available publicly at `https://onyx.your-tailnet.ts.net`.

You should also make this Tailscale URL the authoritative WebUI URL by setting
`WEBUI_CANONICAL_ORIGIN=https://onyx.your-tailnet.ts.net`, using the actual
Funnel hostname. Onyx will use that URL for invitation, verification, and
password-reset links; generated absolute links; identity-provider and MCP OAuth
callbacks; and origin-checked voice WebSockets. Update any externally registered
MCP callback URLs after changing it.

> The HTTPS canonical origin marks authentication and CSRF cookies `Secure` globally. This protects the Tailscale cookies from downgraded HTTP connections, making your login credentials safer. Browsers will still allow login through `http://localhost:300`, and Tor Browser and Brave's Tor window will still support http onion logins, due to special handling of Secure cookies for onion domains. Voice WebSockets work only from the Tailscale hostname.

By default, the Tailscale service does not route through Mysterium VPN, to avoid linking your Tailscale account to your search activity at the Myst VPN exit server. To route Tailscale through the VPN namespace instead, set `TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN=true` in `.env.wrapper`.

Tailscale Funnel and Tor onion ingress can be enabled concurrently as separate entry points, but Tailscale itself cannot currently be routed through Tor.

### Optional: Network Access for the Code-Interpreter

By default, Onyx's code-interpreter (the `onyxdotapp/code-interpreter` image from [onyx-dot-app/python-sandbox](https://github.com/onyx-dot-app/python-sandbox)) selects Docker's `none` network for every executor pod it spawns. Each pod is temporary and least-priv sandboxed.

To let Python and coding tools fetch public information and download files, set:

```bash
ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true
```

Requests use your selected VPN, Tor, proxy, or no-VPN connection. Onion URLs
are available with `TOR_EGRESS_ENABLED=true`. This setting does not grant access
to your computer or LAN; the [platform limitations](#privacy-and-security-of-this-stack)
still apply.

## Optional: Outbound Stack Proxy

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

## Optional: Myst VPN Setup

Myst is disabled by default. Skip this section unless you have explicitly set `MYST_VPN_ENABLED=true` in `.env.wrapper`. In the default no-VPN mode, no Myst daemon, wallet, identity, registration, or payment is required; a lightweight sentinel only validates the shared namespace's direct route, and `make up-lite` / `make up-full` proceeds directly to starting the stack.

The Mysterium VPN requires a funded wallet (paid in cryptocurrency) before it can connect. The signup process is handled by a standalone container that creates a cryptographic identity and registers it on-chain (Mysterium sponsors the gas fees).

Note that because our usage of Mysterium is crypto-native, a normal Mysterium VPN app subscription won't work here. However, the good news is that crypto-native Mysterium is _considerably_ cheaper than the app subscription fee, especially since this agent does not use much data, and there is no monthly fee or funds expiration. I've used less than 10 $MYST ($2 USD) in actual VPN fees since I started this project.

There are two ways to fund the wallet:

- **Option A - Order page (CoinGate):** Pay via a crypto payment gateway. Easiest for first-time users, but requires email, name, and address.
- **Option B - Direct blockchain transfer:** Transfer $MYST directly on Polygon. Cheaper (no gateway fees), but requires acquiring $MYST yourself.

Both options use the same standalone container and produce the same identity/keystore. You only need to run one.

### Option A: CoinGate $MYST Order (Requires email, Name, and Address)

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

### Option B: Direct $MYST Transfer (Skip the Order Page)

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

## Optional: Local Document RAG via Web Connector

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

## Optional: Running a Local Embedding Model Server (Mac)

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
request completes. After a repository update changes the bundled dependency or
Python contract, `make up-full` atomically refreshes an existing installation
before launching it; first-time installation remains explicit.

Full mode skips MLX launch with a custom `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL` value.

> The stack uses `mlx-embeddings` because llama.cpp embeddings support is very buggy (including many subtle accuracy drift bugs, especially under concurrency load and batched embeddings). LM Studio's embedding support is similarly problematic, but worse. For some reason, embedding services recieve little attention from the open source community; `mlx-embeddings` is a rare standout. You are better off with teep than any other alternative option.

See the [Onyx Embedding Configuration](#optional-embedding-model-configuration-in-onyx) for
information on how to configure Onyx to use this endpoint; it is not exactly straight-forward.

## Optional: Using Teep for Embeddings

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

## Optional: Embedding Model Configuration in Onyx

Properly configuring an open-weight frontier embedding model for RAG is a minefield. Almost no one gets it right, including Onyx and LiteLLM. Do **not** configure embeddings through LiteLLM.

To configure embedding support in Onyx:

1. Go to [Onyx Admin Index Settings](http://localhost:3000/admin/configuration/index-settings)
2. Select your embedding model as **Self-Hosted / Custom Model** (local)
3. Enter `nomic-ai/nomic-embed-text-v23` as the model type. This synthetic name
   intentionally enables Onyx's hardcoded `nomic-ai` RAG features. The stack
   prepares and loads its bundled nomic v1 tokenizer offline; the embedding
   shim still sends requests to the configured real upstream model.
4. For both `Harrier-OSS-V1-0.6B` and `Qwen3-Embedding-0.6B`, the embedding dimension is 1024.
5. Enable **Normalize Embeddings** for the recommended models. The shim honors
   this setting even when the selected OpenAI-compatible endpoint returns
   unnormalized vectors.

These Onyx configuration choices cause the stack's patches to route embedding requests to either the MLX embeddings or teep model that you configured above, using correctly formatted query and indexing prefixes.

> Frontier embedding models require an instruction prefix when generating queries, but unfortunately, Onyx has an issue with handling this prefix for generic LLM providers. To address this, the stack contains a local shim which allows you to set the query prefix via an environment instead. The prefixes in `.env.wrapper.example` should be good for either Harrier or Qwen3.

## Optional: External MCP servers

You can add any MCP servers you operate or trust through Onyx Admin.

For an MCP server running on the same host as Docker or Podman:

1. Make the MCP server reachable through the container engine's host gateway:
   - If the server runs directly on a native Linux host, the portable choice is
     to make it listen on `0.0.0.0:<port>`. This includes the engine's host
     gateway interface; use host firewall rules to reject unwanted LAN or
     public access to that port. For a narrower listener, bind to the actual
     host-access address selected by the wrapper (often `172.17.0.1` for
     ordinary Docker and `10.0.2.2` for rootless Docker; do not hardcode either
     outside the wrapper). A host process bound only to the host's
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

