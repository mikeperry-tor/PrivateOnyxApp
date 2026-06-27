# Code Agent Instructions for Private Onyx

This repository is a Docker Compose wrapper for running Onyx with private
verified inference, VPN/proxy-routed web access, Obscura/CRW browser scraping,
SearXNG search, local document RAG, and optional Tailscale exposure.

It is not the upstream Onyx project, nor is it a fork. Most
changes here are deployment, Compose, Python sidecar, shell, SearXNG, or
runtime-patch changes around upstream projects.

The core correctness property is privacy-preserving request handling. Agent
changes must preserve the current routing, fingerprinting, rate-limit,
patch-upgrade, and fail-closed behavior unless the user explicitly asks to
change it.

## Data Flow

The main runtime paths are:

- User opens Onyx UI through the local host proxy or optional Tailscale Funnel.
- Onyx sends LLM requests to the included Teep local inference service, which talks to configured private inference
  providers.
- Onyx `web_search` calls SearXNG, which uses custom CRW-backed engines.
- CRW uses a local prefetch-blocking proxy, the CDP shim, and Obscura to render
  search and page content with browser-like behavior.
- Search/browser traffic normally egresses through the shared Mysterium VPN
  namespace. Optional `PROXY_URL` adds an upstream proxy for selected services.
- Full mode adds local document RAG through `doc-drop-web`, the Onyx Web
  connector, and `local-embedding-shim`.

Before changing any of these paths, read the matching document in `docs/`.

## Key Files and Directories

- `Makefile` - source of truth for stack targets, image builds, compose
  layering, generated local secrets, Myst signup, upgrade, and embedserv flows.
- `.env.wrapper.example` - user-facing configuration surface.
- `docker-compose.yaml` - base wrapper around upstream Onyx services plus VPN,
  search, scraping, Teep, Tailscale, and host proxy sidecars.
- `docker-compose.full.yml` - full Onyx/RAG mode, OpenSearch/cache/MinIO,
  `doc-drop-web`, `host-doc-drop-web-proxy`, and `local-embedding-shim`.
- `docker-compose.lite.yml` - lite mode with vector DB disabled and Open URL
  runtime patching.
- `docker-compose.*-vpn.yml`, `docker-compose.proxy.yml`, and Podman overrides
  - optional routing/proxy/container-engine layers selected by the Makefile.
- `crw/` - CDP shim and prefetch-blocking proxy used by CRW/Obscura request
  handling.
- `searxng/` - custom CRW-backed search engines, minimal SearXNG settings
  overlay, and proxy-mode entrypoint.
- `myst/` - Mysterium image, entrypoint, standalone signup compose, and helper
  CLI.
- `teep/` - Teep build context when present; Teep source is pinned by
  `TEEP_REF`.
- `onyx/patches/` - runtime `sitecustomize` patches applied inside Onyx and
  code-interpreter containers.
- `onyx/local_embedding_shim.py` - model-server-compatible bridge to an
  OpenAI-compatible embeddings endpoint.
- `onyx/doc_drop_webserver.py` - read-only local document HTTP server for the
  Web connector.
- `docs/` - project behavior and upgrade documentation. Keep docs current and
  avoid stale historical clutter.
- `reference_repos/` - local upstream checkouts used for upgrade audits. Treat
  these as references, not primary implementation files or build inclusions. Do not patch them.

## Core Commands

Use the Makefile instead of hand-assembling compose commands unless you are
debugging the Makefile itself.

- `make help` - list supported targets and key overrides.
- `make up-lite` / `make up-full` - start the lite or full stack.
- `make down-lite` / `make down-full` - stop the matching stack.
- `make ps-lite` / `make ps-full` - inspect containers.
- `make logs-lite` / `make logs-full` - follow logs.
- `make upgrade` - rebuild Myst and Teep, pull sidecar images, and refresh
  Onyx deployment files.
- `make upgrade-onyx ONYX_CONFIG_REF=<tag>` - refresh upstream Onyx deployment
  files for a specific ref.
- `make onyx-build` - run the Onyx installer wrapper and ensure image tags.
- `make myst-build` / `make teep-build` - build pinned local images.
- `make vpn-signup-orderform`, `make vpn-signup-blockchain`,
  `make vpn-orderstatus`, and `make vpn-balance` - Myst identity, payment, and
  status flows.
- `make embedserv-install`, `make embedserv-verify-model`, and
  `make embedserv-serve` - optional local MLX embedding server flow.

`make up-lite` and `make up-full` generate ephemeral local secrets on every
start, including SearXNG, Onyx auth, CRW API, and MinIO credentials. Do not
move those secrets into `.env.wrapper`.

## Documentation Routing

Read and update these docs when changing the related subsystem:

- `README.md` - user setup, first-run flow, optional features, and host
  endpoints.
- `docs/request_handling.md` - `web_search` and `open_url` chains, CRW,
  Obscura, CDP shim behavior, 429 avoidance, prefetch proxying, wait strategy,
  fingerprint/cookie limits, and troubleshooting.
- `docs/vpn_routing_and_proxies.md` - shared namespace layout, Mysterium
  behavior, disabling VPN, optional Teep/Tailscale/code-interpreter VPN
  routing, and `PROXY_URL` behavior by service.
- `docs/onyx_patch_info.md` - why each wrapper patch exists and what a cleaner
  upstream implementation would look like.
- `docs/onyx_patches_upgrade.md` - line-oriented upgrade checklist for Onyx,
  code-interpreter, local patches, SearXNG, Compose, and install hooks.
- `docs/local_docs_rag_search.md` - full-mode local document RAG, PDF freshness,
  embedding shim, prefix handling, admin configuration, and diagnostics.

When implementation and docs disagree, treat it as a bug to resolve. Do not
paper over drift with vague wording.

## Repository Rules

This stack protects private research, document contents, browsing behavior,
inference traffic, local credentials, VPN identities, and proxy configuration.

Correctness means preserving privacy and explicit routing behavior, not keeping
every service limping along after validation fails.

### Fail Loudly and Fail Closed

- Do not add silent fallbacks, broad `except`/`catch` suppression, `|| true`,
  best-effort bypasses, or degraded direct-network behavior unless the user
  explicitly asks for that behavior.
- Missing required env values, failed image/tag resolution, failed patch
  application, invalid routing configuration, failed proxy setup, or failed
  validation must produce a clear non-secret error.
- Do not convert a hard failure into empty search results, empty document
  content, direct egress, or a weaker parser path just to make a workflow pass.
- Unsupported embedding shim endpoints should remain visible failures, such as
  intentional 501 stubs, unless a real compatible implementation is added.
- `WRAPPER_PATCH_STRICT=true` is the normal posture. Only relax it for temporary
  upgrade diagnosis when the user explicitly accepts the risk.
- Don't preserve compatibility shims for old behavior. This repository prefers current,
  explicit behavior over indefinite backwards compatibility.

### Preserve Request Handling and 429 Avoidance

- Keep SearXNG custom engines routed through CRW for the supported search
  engines. Do not re-enable stock direct Google, Brave, DuckDuckGo, or
  Startpage engines unless deliberately changing the architecture.
- Preserve CRW per-host serialization and rate limiting unless the user
  explicitly accepts higher 429/CAPTCHA risk.
- Do not add fixed `waitFor` sleeps to Firecrawl/CRW requests. The stack relies
  on CDP shim `waitUntil` injection plus CRW readiness heuristics.
- Keep search-engine HTTP prefetches blocked locally so engines see browser
  navigation through Obscura, not a bare reqwest prefetch followed by browser
  traffic.
- Keep CRW in auto render mode for this path. Forcing render-js or disabling JS
  rendering changes failure and PDF behavior.
- Map CRW anti-bot responses back to SearXNG engine exceptions so suspension
  and partial-result behavior remain visible.

### Preserve Obscura Fingerprinting and Cookie Behavior

- Do not replace Obscura browser rendering with plain HTTP fetching for search
  or recommended `open_url` paths.
- Keep the CDP shim behavior aligned with `docs/request_handling.md`: strip
  CRW's conflicting stealth JS, inject page readiness waits, strip
  per-context proxy fields as a safety net, and periodically clear cookies.
- Do not disable Obscura stealth, storage, tracker blocking, or browser-level
  fingerprinting without a documented reason and a docs update.
- Cookie persistence and clearing are part of the anti-tracking posture. Changes
  to default CDP context usage, storage directories, or clearing intervals must
  be tested against search/open_url behavior.

### Preserve VPN and Proxy No-Leak Behavior

- Services that are meant to share VPN egress must use
  `network_mode: "service:netns-holder"` and must not publish direct host ports.
  Host access should go through the explicit host proxy sidecars.
- `myst-client` owns the Mysterium tunnel and kill-switch inside the shared
  namespace. Do not bypass it with direct network attachments.
- `MYST_VPN_ENABLED=false` is the explicit no-VPN mode. Do not introduce
  automatic direct-egress fallback when Myst fails or is unfunded.
- Optional routing switches must remain explicit:
  `TEEP_VPN_ROUTED=true`, `TAILSCALE_VPN_ROUTED=true`, and
  `CODE_INTERPRETER_VPN_ROUTED=true`.
- Keep Teep and Tailscale outside the VPN by default to avoid linking inference
  provider or Tailscale identities to agent browsing traffic.
- Code-interpreter executor networking is high trust. Do not advertise network
  access to the LLM unless executor pods actually inherit the VPN namespace.
- `PROXY_URL` must be threaded consistently through Obscura, Obscura MCP,
  prefetch-blocking proxy upstream traffic, SearXNG settings, and
  code-interpreter proxy injection as documented.
- Preserve `NO_PROXY`/direct loopback behavior for internal service calls.
- When handling `host.docker.internal` for Obscura proxying, preserve the
  resolved-IP path so SOCKS connectors do not try to resolve Docker-internal
  names through the upstream proxy.

### Preserve Patch Upgrade Correctness

- Before changing `ONYX_IMAGE_TAG`, `CODE_INTERPRETER_IMAGE_TAG`,
  SearXNG tags, CRW, Obscura, or Teep pins, read
  `docs/onyx_patches_upgrade.md`.
- Refresh or inspect the relevant `reference_repos/` checkout before deciding
  that a patch still applies.
- Runtime patches must check exact upstream symbols/strings when they depend on
  them and must fail startup in strict mode when assumptions drift.
- Prefer removing a wrapper patch when upstream provides an equivalent explicit
  setting. Do not keep stale patches for historical comfort.
- After an upgrade, verify both import-time patch success and behavior:
  search, open_url, lite Open URL availability, full-mode internal search,
  PDF freshness, embedding shim calls, code-interpreter routing when enabled,
  and proxy mode when enabled.

### Preserve Compose Layering

- The Makefile assembles `COMPOSE_FILE`; do not flatten override files into the
  base compose file unless the architecture intentionally changes.
- Defaults should stay small. Optional override files should only apply when
  their env switch is set.
- Keep SearXNG config as a minimal overlay using `use_default_settings`. Do not
  copy the full upstream settings file into `searxng/core-config/`.
- Keep Docker and Podman behavior separated in the Podman override files. Do
  not assume Docker socket semantics work in rootless Podman on macOS.
- Preserve generated local secret flow in the Makefile and the
  `${VAR:?message}` checks in Compose where they enforce required values.

### Protect Sensitive Data

- Never log or print API keys, auth cookies, Tailscale auth keys, Myst identity
  secrets, payment details beyond intended public status fields, document
  contents, inference prompts, inference responses, or proxy credentials.
- Redact secrets in examples and diagnostics.
- Treat `.env.wrapper`, `docker-data/`, `doc-drop/`, logs, and generated model
  caches as local private data. Do not stage or rewrite them unless the user
  explicitly asks.
- Keep local document serving read-only and hidden-path filtering intact.

### Documentation Hygiene

- Update docs in the same change when behavior, defaults, commands, routing, or
  optional feature semantics change.
- Remove obsolete documentation instead of preserving long "old behavior"
  sections. Historical drift belongs in Git history, not the user docs.
- Avoid vague compatibility language. Name the current behavior, the current
  env vars, and the current failure mode.
- Keep README concise and operator-facing; put deep implementation and upgrade
  detail in the relevant `docs/` file.

## Testing and Validation

There is no single test framework for this wrapper. Choose checks
based on what changed:

- Compose or Makefile changes: run `make help` and inspect the effective compose
  model for the affected mode, for example with the same `COMPOSE_FILE` layering
  the Makefile would use.
- Stack startup changes: run the relevant `make up-lite` or `make up-full`,
  then `make ps-*` and targeted logs.
- Request path changes: test a real `web_search` query and a real `open_url`
  request; inspect SearXNG, CRW, CDP shim, and Obscura logs as needed.
- VPN/proxy changes: verify effective namespace membership, egress path, and
  `NO_PROXY` behavior. Confirm no direct host port or direct-network bypass was
  introduced.
- Onyx patch changes: confirm startup logs show patch success in strict mode and
  exercise the patched behavior.
- Full-mode RAG changes: test doc-drop crawling, unchanged-PDF freshness,
  modified-PDF reindexing, embedding shim health, and `internal_search`.
- SearXNG engine changes: test each custom engine with a real query and inspect
  rendered HTML if results drop to zero.
- Code-interpreter routing/proxy changes: test both disabled and enabled modes;
  confirm LLM-facing capability text matches executor network reality.

Do not run live network, payment, VPN, or full-stack destructive operations
casually. If a check needs credentials, funding, external services, or long
runtime, explain what was not run and why.

## Git Workflow

- This repository is managed by Git, but many local data and reference files may
  be untracked. Do not stage broad paths.
- Stage only files you intentionally changed. Do not use `git add .` or
  `git add -A`.
- Do not revert user changes or generated local state unless explicitly asked.
- For multi-phase plans, use one commit per phase when the user asks you to
  commit.
- Do not mention audit identifiers in code, docs, or commit messages.

## Implementation Style

- Prefer small, explicit changes over broad rewrites.
- Prefer structured parsers or compose-aware inspection over ad hoc text hacks
  when changing configuration formats.
- Shell scripts should use strict error handling where practical and should not
  hide failing commands that affect privacy, routing, or validation.
- Python sidecars should return clear HTTP errors and log non-secret reasons.
- Runtime patches should be narrow, startup-validated, and documented in
  `docs/onyx_patch_info.md` and `docs/onyx_patches_upgrade.md`.
- Do not add new long-lived mutable global state unless it is protected and
  appropriate for threaded sidecars.
- Keep optional features opt-in and visibly configured.
