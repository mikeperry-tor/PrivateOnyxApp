# Code Agent Instructions for Private Onyx

This repository is a Docker Compose wrapper for running Onyx with private verified inference, VPN/proxy-routed web access, Obscura/CRW browser scraping, SearXNG search, local document RAG, and optional Tailscale exposure.

It is not the upstream Onyx project, nor is it a fork. Most changes here are deployment, Compose, Python sidecar, shell, SearXNG, or runtime-patch changes around upstream projects.

The core correctness property is privacy-preserving request handling. Preserve the current routing, fingerprinting, rate-limit, patch-upgrade, explicit configuration, and fail-closed behavior unless the user explicitly asks to change it.

## How to Use These Instructions

These instructions are intentionally a thin orientation layer. They tell you where to look and which repo-wide invariants not to violate; these instructions do not replace the subsystem docs.

Before changing a subsystem, read the matching document below, then inspect the implementation. If the implementation and docs disagree, treat that as a bug to resolve. Do not paper over drift with vague wording.

- `README.md` - user setup, first-run flow, optional features, and host endpoints.
- `docs/request_handling.md` - `web_search`, `open_url`, CRW, Obscura, CDP shim, prefetch blocking, readiness, cookies, and anti-bot behavior.
- `docs/vpn_routing_and_proxies.md` - shared VPN namespace layout, Mysterium, explicit no-VPN mode, optional routing switches, `ONYX_AGENT_OUTBOUND_PROXY_URL`, and `NO_PROXY`.
- `docs/onyx_patch_info.md` - why local runtime patches exist.
- `docs/onyx_patches_upgrade.md` - Onyx/code-interpreter/SearXNG/CRW/Obscura/ Teep upgrade checklist and patch validation, for use when image/source pins or runtime Python locks are updated.
- `docs/local_docs_rag_search.md` - full-mode local document RAG, local doc serving, PDF freshness, embedding shim, and diagnostics.

When a request touches more than one path, read each relevant doc first. Prefer small, doc-aligned changes over broad rewrites.

## Runtime Shape

At a high level:

- Users reach the Onyx WebUI through the local host proxy or optional Tailscale Funnel.
- Onyx sends LLM requests through the included Teep local inference service.
- Onyx `web_search` calls SearXNG, which uses custom CRW-backed engines.
- CRW uses local proxy/CDP/Obscura components to render search and page content. These components exist to reduce 429 and 403 errors at search engines and web pages.
- Search/browser traffic normally egresses through the shared Mysterium VPN
  namespace; optional proxy routing is configured explicitly.
- There are two main modes for the stack: lite and full. The full mode adds local document RAG through `doc-drop-web`, the Onyx Web connector, and `local-embedding-shim`.

Those bullets are only a map. Read the docs above before changing any runtime path.

## Key Locations

- `Makefile` - source of truth for stack targets, compose layering, generated local secrets, image builds, upgrades, Myst flows, and embedserv flows.
- `stack.versions.env` - committed source of truth for stack image tags and source refs.
- `.env.wrapper.example` - user-facing configuration surface for local runtime options, not routine image pins.
- `docker-compose.yaml` - base wrapper stack.
- `docker-compose.full.yml` - full Onyx/RAG mode.
- `docker-compose.lite.yml` - lite mode.
- `docker-compose.*-vpn.yml`, `docker-compose.proxy.yml`, and Podman overrides
  - optional routing/proxy/container-engine layers selected by the Makefile.
- `crw/` - CDP shim and prefetch-blocking proxy around CRW/Obscura.
- `searxng/` - custom CRW-backed engines, minimal SearXNG overlay, and
  proxy-mode entrypoint.
- `myst/` - Mysterium image build file, entrypoint, signup compose, and helper CLI.
- `teep/` - Teep image build file and entrypoint.
- `onyx/patches/` - runtime `sitecustomize` patches applied inside Onyx and code-interpreter containers.
- `onyx/local_embedding_shim.py` - model-server-compatible bridge to an OpenAI-compatible embeddings endpoint. Exists to provide embedding query prefix strings and model name conversion.
- `onyx/doc_drop_webserver.py` - read-only local document HTTP server for the Web connector, to serve files for RAG indexing and embedding.
- `reference_repos/` - upstream checkouts for audits and upgrades, if present. Treat them as references only; do not patch them.

## Commands

Use the Makefile instead of hand-assembling compose commands unless you are
debugging the Makefile itself.

- `make help` - list supported targets and key overrides.
- `make up-lite` / `make up-full` - start the lite or full stack.
- `make down-lite` / `make down-full` - stop the matching stack.
- `make ps-lite` / `make ps-full` - inspect containers.
- `make logs-lite` / `make logs-full` - follow logs.
- `make upgrade` and `make upgrade-onyx ONYX_CONFIG_REF=<tag>` - upgrade flows.
- `make upgrade-python-deps` - upgrade hashed Python lock files from the committed `requirements.in` inputs.
- `make onyx-build`, `make myst-build`, and `make teep-build` - image builds.
- `make vpn-signup-orderform`, `make vpn-signup-blockchain`,
  `make vpn-orderstatus`, and `make vpn-balance` - Myst account/payment flows.
- `make embedserv-install`, `make embedserv-verify-model`, and `make embedserv-serve` - optional local MLX embedding server flow for the full RAG stack.

`make up-lite` and `make up-full` generate ephemeral local secrets on every start, including SearXNG, Onyx auth, CRW API, and MinIO credentials. Do not move those secrets (or any other new ephemeral secrets) into `.env.wrapper.example`.

Do not run live network, payment, VPN, or full-stack destructive operations casually. If a check needs credentials, funding, external services, or long runtime, explain what was not run and why.

Do not read or modify the custom `.env.wrapper` unless specifically asked to do so.

## Repository Rules

This stack protects private research, document contents, browsing behavior, inference traffic, local credentials, VPN identities, and proxy configuration.

### Implementation Style

- Prefer small, explicit changes over broad rewrites.
- Prefer structured parsers or compose-aware inspection over ad hoc text hacks when changing configuration formats.
- Prefer component-configurable behavior over shims. Remove shims when component configurations are discovered.
- When necessary, runtime patches should be narrow, startup-validated, and documented in `docs/onyx_patch_info.md` and `docs/onyx_patches_upgrade.md`.
- Don't preserve compatibility for old behavior. Prefer current, explicit behavior over indefinite backwards compatibility.
- Keep optional features opt-in and visibly configured.

### General Failure Handling

- Fail loudly and fail closed. Do not add silent fallbacks, broad error suppression, `|| true`, direct-network bypasses, empty-result substitutes, or weaker parser paths unless the user explicitly asks for that behavior.
- Shell scripts should use strict error handling where practical and should not hide failing commands that affect privacy, routing, or validation.
- Python sidecars should return clear HTTP errors and log non-secret reasons.
- Patches and overlay modifications should self-validate their application and halt service health or stack launch otherwise.
- Keep required env values, image/tag resolution, patch application, routing setup, proxy setup, and validation checks as visible non-secret failures.

### Component-Specific Rules

- Compose layering: the Makefile assembles `COMPOSE_FILE`. Keep optional behavior in override files, keep Docker and Podman behavior separated, and preserve generated local secret flow plus Compose `${VAR:?message}` checks.
- VPN/proxy routing: preserve explicit VPN/no-VPN behavior, host-proxy access, shared namespace membership where documented, optional routing switches, and `ONYX_AGENT_OUTBOUND_PROXY_URL`/`NO_PROXY` handling. Do not introduce automatic direct-egress fallback when VPN or proxy connectivity fails.
- Request handling: keep supported search engines routed through the custom CRW/SearXNG path; preserve CRW/Obscura rendering, prefetch blocking, per-host rate control, anti-bot visibility, and the documented CDP shim behavior. Do not replace this path with plain HTTP fetching or fixed sleeps.
- Documentation: update docs and AGENTS.md when behavior, defaults, commands, routing, or optional feature semantics change. Remove obsolete text instead of keeping long historical sections.
- Patch upgrades: before changing Onyx, code-interpreter, SearXNG, CRW, Obscura, or Teep pins, or runtime Python lock inputs, read `docs/onyx_patches_upgrade.md`. Runtime patches should remain narrow, startup-validated, strict by default, and documented.
- Untracked stack files: Treat `.env.wrapper`, `docker-data/`, and `doc-drop/` as local private data. Do not read, stage, or rewrite them unless the user explicitly asks.

### Testing and Validation

There is no single test framework for this wrapper. Choose checks based on what changed:

- Compose or Makefile changes: run `make help` and inspect the effective compose model for the affected mode using the Makefile's layering.
- Stack startup changes: run the relevant `make up-lite` or `make up-full`, then `make ps-*` and targeted logs when practical.
- Request path changes: test a real `web_search` query and a real `open_url` request; inspect SearXNG, CRW, CDP shim, and Obscura logs as needed.
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
