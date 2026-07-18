# Code Agent Instructions for Private Onyx

This repository is a Docker Compose wrapper for running Onyx with private verified inference, VPN/proxy-routed Obscura browser access, an optional public-proxied stock Onyx crawler mode, SearXNG search, local document RAG, and optional Tailscale exposure.

It is not the upstream Onyx project, nor is it a fork. Most changes here are deployment, Compose, Python sidecar, shell, SearXNG, or runtime-patch changes around upstream projects.

The core correctness property is privacy-preserving request handling. Preserve the current routing, fingerprinting, rate-limit, patch-upgrade, explicit configuration, and fail-closed behavior unless the user explicitly asks to change it.

## How to Use These Instructions

These instructions are intentionally a thin orientation layer. They tell you where to look and which repo-wide invariants not to violate; these instructions do not replace the subsystem docs.

Before changing a subsystem, read the matching document below, then inspect the implementation. If the implementation and docs disagree, treat that as a bug to resolve. Do not paper over drift with vague wording.

- `README.md` - user-facing setup instructions, privacy properties, and option consequences. Keep deep implementation details out of this document.
- `docs/request_handling.md` - direct-Obscura `web_search`, selectable built-in `open_url` transport, lifecycle waits, body/DOM limits, cookies, and anti-bot behavior.
- `docs/vpn_routing_and_proxies.md` - trusted VPN namespace, restricted component networks, final-hop proxy policies, explicit no-VPN mode, and optional routing switches.
- `docs/internal_network_security.md` - restricted component reachability, destination validation, bridge boundaries, Onyx SSRF interaction, and residual risks.
- `docs/onyx_patch_info.md` - why local runtime patches exist.
- `docs/onyx_patches_upgrade.md` - Onyx/code-interpreter/SearXNG/Obscura/Teep upgrade checklist and patch validation, for use when image/source pins or runtime Python locks are updated.
- `docs/local_docs_rag_search.md` - full-mode local document RAG, local doc serving, PDF freshness, embedding shim, and diagnostics.

When a request touches more than one path, read each relevant doc first. Prefer small, doc-aligned changes over broad rewrites.

Keep relevant documentation up to date when you perform changes, including this AGENTS.md file.

## Runtime Shape

At a high level:

- Users reach nginx through a hardened fixed host publisher or the optional fixed Tailscale frontend gateway; nginx stays internal-only.
- There are two main modes for the stack: lite and full. The full mode adds local document RAG through `doc-drop-web`, the Onyx Web connector, and `local-embedding-shim`.
- The recommended local-RAG Admin model name `nomic-ai/nomic-embed-text-v23` is intentionally synthetic: it preserves Onyx's `nomic-ai` feature gates while a strict runtime patch aliases only tokenizer construction to the bundled v1 tokenizer.
- The wrapper uses the legacy code-interpreter service and explicitly disables unsupported Craft sandbox scheduling.
- Onyx sends LLM requests through the included Teep local inference service.
- The base `sitecustomize` patch gives nested Deep Research agents the tools selected for the current chat Agent and executes complete model-emitted tool batches with bounded concurrency.
- Onyx `web_search` calls SearXNG, whose custom offline engines navigate and parse rendered provider pages through Obscura.
- Onyx `open_url()` tool calls use the Onyx Web Crawler with python requests and its local Chromium fallback; both stages are restricted to public-only
egress through the fixed Onyx bridge.
- Setting `ONYX_AGENT_USE_OBSCURA_BROWSER=true` moves only the built-in crawler to the single-navigation Obscura path. At Obscura 0.1.10, testing found the stock crawler was blocked less often; re-evaluate this default on Obscura upgrades.

## Network Security

This stack has hardened the Onyx networking usage to ensure that both egress and internal network access is controlled via network namespace topology and explicit routing, rather than code.

- SearXNG, Obscura, and optional executor pods use narrow internal networks. Internet traffic crosses component bridges to final-hop policy proxies in the trusted Mysterium routing namespace.
- Direct callers validate URL syntax without resolving target names; authoritative destination DNS and address validation remain at the final-hop policy.
- Isolated Obscura allows private-address resolution only so its HTTP client
  can resolve the mandatory Docker egress-bridge proxy.
  Their narrow networks prevent direct Internet egress, and final-hop policy
  proxies remain authoritative for target DNS and private-target rejection.
- Onyx applications use internal-only networks. Generic helpers use a fixed
  public bridge; saved-level MCP/Web Connector traffic and configured
  chat inference plus the dedicated embedding shim use separate public or
  host-capable bridges and route-class-specific final-hop policy listeners.
  Identical public policies share a proxy process but keep distinct bridges
  and caller networks. Direct
  sockets have no external route; executor pods never inherit
  Onyx exceptions. The exact internal Teep chat base is a startup-validated
  direct-service exception, and doc-drop Web Connector traffic uses an exact
  host final-hop gateway rather than a process-wide direct crawl.
- `ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS` is the user-facing opt-in for
  validated RFC1918 endpoints used by configured MCP/Web integrations,
  inference providers, and the embedding shim. It must never grant LAN access
  to generic helpers, `open_url`, browser activity, or executors. Exact
  `host.docker.internal` remains a separate default host-route exception.
- Myst and the final-hop proxies are trusted routing-namespace processes.
  Enabling Myst routing for Teep or Tailscale deliberately promotes that
  trusted component into the same namespace, including its loopback,
  interfaces, routes, and policy listeners. Their fixed gateways constrain
  application ingress; they are not a sandbox between co-resident processes.

Those bullets are only a map. Read the docs above before changing any runtime networking path. Keep the documentation up-to-date.

## Key Locations

- `Makefile` - source of truth for stack targets, compose layering, generated local secrets, image builds, upgrades, Myst flows, and embedserv flows.
- `stack.versions.env` - committed source of truth for stack image tags, source refs, and the derived SearXNG image repository. The Makefile adds a digest of every embedded SearXNG wrapper input to that derived image's tag.
- `.env.wrapper.example` - user-facing configuration surface for local runtime options; do not expose developer or debugging configuration here. Keep option descriptions user-facing, without describing full implementation details. Only describe properties and consequences that are directly relevant to the user's decision.
- `docker-compose.yaml` - base wrapper stack, including the restricted SearXNG/Obscura control topology, policy proxies, fixed bridges, and service gateways.
- `docker-compose.full.yml` - full Onyx/RAG mode.
- `docker-compose.lite.yml` - lite mode.
- `docker-compose.code-interpreter-network.yml` - optional executor-only internal network and proxy bridge.
- `docker-compose.*-vpn.yml`, `docker-compose.vpn-autoheal.yml`,
  `docker-compose.proxy.yml`, and Podman overrides
  - optional routing/proxy/container-engine layers selected by the Makefile.
- `browser/obscura_client/` - shared direct-CDP client used by Onyx and SearXNG.
- `egress/` - shared final-hop policy-proxy implementation.
- `searxng/` - derived image, custom direct-Obscura offline engines, and SearXNG overlay.
- `myst/` - Mysterium image build file, entrypoint, signup compose, and helper CLI.
- `teep/` - Teep image build file and entrypoint.
- `onyx/patches/` - runtime `sitecustomize` patches applied inside Onyx and code-interpreter containers.
- `onyx/helper-egress.env` - stack-owned `NO_PROXY` set for trusted Onyx
  backend dependencies; update it when Compose service names or aliases change.
- `onyx/local_embedding_shim.py` - model-server-compatible bridge to an OpenAI-compatible embeddings endpoint. Exists to provide embedding query prefix strings and model name conversion.
- `onyx/doc_drop_webserver.py` - read-only local document HTTP server for the Web connector, to serve files for RAG indexing and embedding.
- `reference_repos/` - upstream checkouts for audits and upgrades, if present. Treat them as references only; do not patch them.
- `tests/` - Python unittests and image-based validation tests. Run these upon changes and keep them up to date.

## Commands

Use the Makefile instead of hand-assembling compose commands unless you are debugging the Makefile itself.

- `make help` - list supported targets and key overrides.
- `make up-lite` / `make up-full` - start the lite or full stack. Full mode
  also starts the bundled host MLX embedding server when its selected model is
  already installed and the shim still uses the bundled default endpoint;
  `make down-full` stops only that identity-validated automatically launched
  process. Custom upstreams and manually launched servers are not touched.
- `make down-lite` / `make down-full` - stop the matching stack.
- `make ps-lite` / `make ps-full` - inspect containers.
- `make logs-lite` / `make logs-full` - follow logs.
- `make upgrade` and `make upgrade-onyx ONYX_CONFIG_REF=<tag>` - upgrade flows.
- `make upgrade-python-deps` - upgrade hashed Python lock files from the committed `requirements.in` inputs.
- `make onyx-build`, `make myst-build`, and `make teep-build` - image builds.
- `make embedserv-install`, `make embedserv-verify-model`, and `make embedserv-serve` - optional local MLX embedding server flow for the full RAG stack.

`make up-lite` and `make up-full` generate ephemeral local secrets on every start, including SearXNG, Onyx auth, and MinIO credentials. Do not move those secrets (or any other new ephemeral secrets) into `.env.wrapper.example`.

Do not read or modify the custom `.env.wrapper` unless specifically asked to do so. You may source this file into your environment without reading the contents, to apply the environment to service restart and patch diagnosis.

If a check needs additional credentials, funding, external services, or long runtime, explain what was not run and why.

## Repository Rules

This stack protects private research, document contents, browsing behavior, inference traffic, local credentials, VPN identities, and proxy configuration.

### Implementation Style

- Prefer small, explicit changes over broad rewrites.
- When necessary, runtime patches should be narrow, startup-validated, covered by tests, and documented in `docs/onyx_patch_info.md` and `docs/onyx_patches_upgrade.md`.
- Prefer structured parsers or compose-aware inspection over ad hoc text hacks when changing configuration formats.
- Prefer component-configurable behavior over shims and patches.
- Remove shims and patches when component configuration options or other updates are discovered that could provide the desired functionality.
- Don't preserve compatibility for old behavior. Prefer current, explicit behavior over indefinite backwards compatibility.
- Keep optional features opt-in and visibly configured.

### General Failure Handling

- Fail loudly and fail closed, especially for patch application and patch error handling.
- Do not add silent fallbacks, broad error suppression, `|| true`, direct-network bypasses, empty-result substitutes, or weaker parser paths unless the user explicitly asks for that behavior.
- Shell scripts should use strict error handling where practical and should not hide failing commands that affect privacy, routing, or validation.
- Python sidecars should return clear HTTP errors and log non-secret reasons.
- Patches and overlay modifications should self-validate their application and halt service health or stack launch otherwise.
- Keep image/tag resolution, patch application, routing setup, proxy setup, and validation checks as visible non-secret failures.

### Component-Specific Rules

- Compose layering:
  - The Makefile assembles `COMPOSE_FILE`. Keep optional behavior in override files, keep Docker and Podman behavior separated, and preserve generated local secret flow plus Compose `${VAR:?message}` checks.
- VPN/proxy routing:
  - Preserve explicit VPN/no-VPN behavior, the separate public/host Onyx route classes, exact host and opt-in `ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS` policy, operator-local `.local`/`.internal`/`.home.arpa` DNS restriction, optional routing switches, and `EGRESS_UPSTREAM_PROXY_URL`/`NO_PROXY` handling.
  - Public upstream-proxy names and addresses must use provider DNS and the VPN route in VPN mode; exact host and RFC1918-literal proxy endpoints use only their documented narrow route exceptions, while named operator-local proxies require `ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true`.
  - Empty or failed operator-local target lookups fail closed without external fallback; only non-empty all-global answers return to the selected public final hop.
  - Onyx applications must never join `netns-holder` or gain direct fallback when VPN, policy-proxy, or bridge connectivity fails.
- Request handling:
  - Keep supported search engines on the shared direct-Obscura path and preserve their atomic pre-thread provider reservation.
  - The built-in Onyx Web Crawler defaults to the stock requests/local-Chromium mode because it was blocked less often in Obscura 0.1.10 testing; preserve its public-only fixed-proxy adapter, no local target DNS, disabled environment/loopback bypasses, and unchanged SearXNG path.
  - When explicitly switched to Obscura, preserve one navigation, event-based waits, the configured main-response byte limit (including HTML), the separate fixed DOM limit, anti-bot visibility, and complete cleanup. Re-test the default on Obscura upgrades.
  - Do not add other hidden retries, fallbacks, or fixed sleeps.
- Documentation:
  - Update docs and AGENTS.md when behavior, defaults, commands, routing, or optional feature semantics change.
  - Remove obsolete text instead of keeping long historical sections.
- Patch upgrades:
  - Before changing Onyx, code-interpreter, SearXNG, Obscura, or Teep pins, or runtime Python lock inputs, read `docs/onyx_patches_upgrade.md`.
  - Runtime patches should remain narrow, startup-validated, strict by default, and documented.
- Local RAG compatibility:
  - Preserve the exact fake-nomic saved model name and tokenizer-only alias unless Onyx's feature gates are deliberately reworked.
  - Do not enable Craft or its cleanup schedule without adding and documenting a supported sandbox backend.
  - The Onyx PDF parser operates by producing parsing results on stdout. Onyx bootstrap diagnostics must stay on stderr because isolated child stdout carries a pickled result.
  - Bootstrap or process-isolation changes require a real PDF extraction test.
- Untracked stack files:
  - Treat `.env.wrapper`, `docker-data/`, and `doc-drop/` as local private data. Do not read, stage, or rewrite them unless the user explicitly asks.

### Testing and Validation

There is no single test framework for this wrapper. Choose checks based on what changed:

- Restricted-egress unit tests: run
  `python3 -m unittest discover -s tests -p 'test_*.py' -v`. Add focused cases
  under `tests/` when changing proxy destination/DNS policy, HTTP request
  framing, bridge-peer authentication and route-class enforcement, executor
  network selection, or injected executor environment. Runtime-limit patches should get focused signature,
  configured-value, and invalid-value cases. Tests must be deterministic and
  must not require live internet, VPN credentials, or the private
  `.env.wrapper`.

- Compose or Makefile changes: run `make help` and inspect the effective compose model for the affected mode using the Makefile's layering.
- Stack startup changes: run the relevant `make up-lite` or `make up-full`, then `make ps-*` and targeted logs when practical.
- Request path changes: test a real `web_search` query and a real `open_url` request; inspect SearXNG, Obscura, CDP gateway, API, bridge, and final-hop logs as needed.
- VPN/proxy changes: verify namespace membership, egress path, `NO_PROXY` behavior, and absence of direct host-port or direct-network bypasses.
- Onyx patch changes: confirm startup logs show patch success in strict mode and exercise the patched behavior.
- Full-mode RAG changes: test doc-drop crawling, PDF freshness/reindexing, embedding shim health, and `internal_search`.
- SearXNG engine changes: test each affected custom engine with a real query.
- Code-interpreter routing/proxy changes: test disabled and enabled modes, and confirm LLM-facing capability text matches executor networking.

If you cannot safely run a relevant check, say why.

### Git Workflow

- Stage only files you intentionally changed. Do not use `git add .` or `git add -A`.
- Do not revert user changes or generated local state unless explicitly asked.
- For multi-phase plans, use one commit per phase when the user asks you to commit.
