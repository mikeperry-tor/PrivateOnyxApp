# Code Agent Instructions for Private Onyx

This repository is a Docker Compose wrapper for running Onyx with private verified inference, VPN/proxy-routed Obscura browser access, an optional public-proxied stock Onyx crawler mode, SearXNG search, local document RAG, and optional Tailscale exposure.

It is not the upstream Onyx project, nor is it a fork. Most changes here are deployment, Compose, Python sidecar, shell, SearXNG, or runtime-patch changes around upstream projects.

The core correctness property is privacy-preserving request handling. Preserve the current routing, anti-fingerprinting, rate-limiting, patch-upgrade, explicit configuration, and fail-closed behavior unless the user explicitly asks to change it.

## How to Use These Instructions

These instructions are an orientation layer. They tell you where to look and which repo-wide invariants not to violate; they do not replace the subsystem docs.

Before changing a subsystem, read the matching document below, then inspect the implementation. If the implementation and docs disagree, treat that as a bug to resolve. Do not paper over drift with vague wording.

- `README.md` - user-facing setup instructions, privacy properties, and option consequences. Keep deep implementation details out of this document.
- `docs/request_handling.md` - direct-Obscura `web_search`, selectable built-in `open_url` transport, lifecycle waits, body/DOM limits, cookies, and anti-bot behavior.
- `docs/vpn_routing_and_proxies.md` - trusted VPN namespace, restricted component networks, final-hop proxy policies, explicit no-VPN mode, and optional routing switches.
- `docs/internal_network_security.md` - restricted component reachability, destination validation, bridge boundaries, Onyx SSRF interaction, and residual risks.
- `docs/onyx_patch_info.md` - why local runtime patches exist.
- `docs/onyx_patches_upgrade.md` - Onyx/code-interpreter/SearXNG/Obscura/Teep upgrade checklist and patch validation, for use when image/source pins or runtime Python locks are updated.
- `docs/local_docs_rag_search.md` - full-mode local document RAG, local doc serving, PDF freshness, embedding shim, and diagnostics.
- `docs/resource_minimization.md` - implemented low-idle health cadence,
  background work, model lifecycle, search, and storage resource policy plus
  regression checks. Read it before changing periodic work, worker topology,
  health checks, MLX lifecycle, or resource settings.
- `docs/podman_suport.md` - Podman/macOS Compose overlays, startup-health
  translation, shared-storage ownership, bind-mount workarounds, known regressions, and
  the compatibility checklist. Read it before adding or validating any feature
  that changes Compose, mounts, container lifecycle, health checks, or
  socket-dependent behavior under Podman.

When a request touches more than one path, read each relevant doc first. Prefer small, doc-aligned changes over broad rewrites.

## Runtime Shape

At a high level:

- Users reach nginx through a hardened fixed host publisher or the optional fixed Tailscale frontend gateway; nginx stays internal-only.
- Nginx is the single WebUI health boundary: its local root check traverses the
  frontend, while API health remains a separate startup dependency. Do not add
  a duplicate periodic `web_server` health check.
- There are two main modes for the stack: lite and full. The full mode adds local document RAG through `doc-drop-web`, the Onyx Web connector, and `local-embedding-shim`.
- Full mode stages embedding readiness before replacing the API/background tier
  and follows the low-idle health, worker, model-lifecycle, search, and storage
  policy in `docs/resource_minimization.md`. Keep those controls static,
  explicit, and fail-closed rather than adding runtime administrative work.
- `embedserv/host_process_manager.py` provides shared lifecycle ownership for
  wrapper-managed host services. Keep service-specific peer, model, child, and
  content validation in the services; see `docs/local_docs_rag_search.md` and
  `docs/podman_suport.md` for selection and shutdown semantics.
- The recommended local-RAG Admin model name `nomic-ai/nomic-embed-text-v23` is intentionally synthetic: it preserves Onyx's `nomic-ai` feature gates while a strict runtime patch aliases only tokenizer construction to the bundled v1 tokenizer.
- Ordinary chat uses the standalone code-interpreter service. Its LLM-facing
  tool name is `run_python`, while its display name remains Code Interpreter.
  Onyx Craft is a separate OpenCode-based, per-user sandbox environment; this
  wrapper leaves it disabled because it has no supported Craft sandbox backend
  or privacy/resource lifecycle policy.
- Onyx sends LLM requests through the included Teep local inference service.
- The shared API runtime patches give nested Deep Research agents the tools selected for the current chat Agent and execute complete model-emitted tool batches with bounded concurrency.
- Onyx `web_search` uses only the wrapper's supported SearXNG engines, which
  render through Obscura; inherited stock engines are absent.
- Onyx `open_url()` is a chat-time read tool, not an ingestion path. Full mode
  may prefer an exact-ID indexed copy after concurrent crawler/indexed lookup;
  the default crawler remains the public-only stock requests/Chromium path.
- Lite mode keeps crawler-backed `open_url()` available through a strict runtime
  patch while indexed URL retrieval remains disabled.
- Setting `ONYX_AGENT_USE_OBSCURA_BROWSER=true` moves only the built-in crawler to the single-navigation Obscura path. At Obscura 0.1.10, testing found the stock crawler was blocked less often; re-evaluate this default on Obscura upgrades.

## Network Security

This stack controls egress and internal reachability through network-namespace
topology, explicit routes, and final-hop destination policy, as documented in
`docs/internal_network_security.md`. Safety must not depend on application-level
proxy settings or caller discipline alone.

- SearXNG, Obscura, and optional executor pods use narrow internal networks. Internet traffic crosses component bridges to final-hop policy proxies in the trusted Mysterium routing namespace.
- Direct callers validate URL syntax without resolving target names; authoritative destination DNS and address validation remain at the final-hop policy.
- Obscura's narrow network permits its mandatory bridge proxy but no direct
  Internet route; final-hop policy remains authoritative for target DNS and
  private-target rejection.
- Onyx applications use internal-only networks. Generic helpers use a fixed
  public bridge, while configured integrations, inference, and embeddings use
  separate route-class bridges. Direct sockets have no external route, and
  executors never inherit Onyx exceptions.
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
- Myst route/MTU reconciliation and post-readiness recovery each have one
  socket-free owner. Preserve their bounded, fail-closed behavior and the
  startup/no-VPN non-arming rules documented in
  `docs/vpn_routing_and_proxies.md` and `docs/resource_minimization.md`.
- Myst signup is a non-restarting, host-driven workflow separate from integrated
  startup. Never retry an ambiguous financial mutation; see
  `docs/onyx_patches_upgrade.md` when upgrading Myst.
- Optional Onyx telemetry, third-party analytics/tracing, cloud billing,
  CAPTCHA, and remote configuration/data-list fetches are disabled; intentional
  local administrative analytics and release-note behavior are documented in
  `docs/onyx_patches_upgrade.md`.
- Nginx adds a restrictive WebUI CSP because browser requests are outside the
  container routing boundary. Preserve the tested policy and its documented
  compatibility exception; do not describe it as complete XSS prevention.

Those bullets are only a map. Read the docs above before changing any runtime networking path. Keep the documentation up-to-date.

## Key Locations

- Build, versions, and configuration:
  - `Makefile` - source of truth for stack targets, Compose layering, generated
    local secrets, image builds, upgrades, Myst flows, and embedserv flows.
  - `stack.versions.env` - committed source of truth for image tags, source refs,
    and the derived SearXNG image repository. The Makefile includes a digest of
    every embedded SearXNG wrapper input in the derived tag.
  - `.env.wrapper.example` - user-facing runtime options. Keep descriptions
    focused on user-visible properties and consequences; do not expose
    developer/debugging controls or deep implementation detail here.
- Compose topology:
  - `docker-compose.yaml` - base wrapper stack, restricted control topology,
    policy proxies, fixed bridges, and service gateways.
  - `docker-compose.full.yml` and `docker-compose.lite.yml` - full RAG and lite
    mode overlays.
  - `docker-compose.code-interpreter-network.yml` - optional executor-only
    internal network and proxy bridge.
  - `docker-compose.*-vpn.yml`, `docker-compose.proxy.yml`, and Podman
    overrides - optional routing, proxy, and container-engine layers selected
    by the Makefile.
- Routing and component implementations:
  - `browser/obscura_client/` - shared direct-CDP client used by Onyx and
    SearXNG.
  - `egress/` - shared final-hop policy-proxy implementation.
  - `searxng/` - derived image, custom direct-Obscura offline engines, and
    SearXNG overlay.
  - `myst/` and `teep/` - component build files, entrypoints, and helper flows.
- Onyx wrapper code:
  - `onyx/patches/` - runtime `sitecustomize` patches applied inside Onyx and
    code-interpreter containers.
  - `onyx/nginx/` - tracked browser security headers, including the restrictive
    WebUI CSP mounted independently of generated upstream nginx configuration.
  - `onyx/helper-egress.env` - stack-owned `NO_PROXY` values for trusted Onyx
    backend dependencies; update it when Compose service names or aliases
    change.
  - `onyx/local_embedding_shim.py` - model-server-compatible bridge to an
    OpenAI-compatible embedding endpoint, including model-name conversion and
    query prefixes.
  - `onyx/doc_drop_webserver.py` - read-only local document server for Web
    connector indexing and embedding.
- Audits and validation:
  - `reference_repos/` - read-only upstream checkouts used during audits and
    upgrades; never patch them.
  - `tests/` - deterministic unit tests, image contract checks, and supporting
    validation scripts.

## Commands

Use the Makefile instead of hand-assembling compose commands unless you are debugging the Makefile itself.

- `make help` - list supported targets and key overrides.
- `make test` - run the deterministic Python suite without requiring images,
  credentials, a running stack, or the private `.env.wrapper` contents.
- `make check` - run `make test`, compile repository runtime/test Python paths,
  validate `make help`, and run `git diff --check`. Use this as the normal
  development pre-handoff check.
- `make test-images` - install strict runtime patches against the already-built
  pinned Onyx, code-interpreter, and derived SearXNG images, then run the
  SearXNG parser tests that need image dependencies. It does not pull or build
  missing images or permit validation-container networking; use the reported
  build target first.
- `make test-opensearch-image` - validate the pinned OpenSearch image in an
  isolated disposable environment.
- `make check-upgrade` - run `make check`, `make test-images`, and
  `make test-opensearch-image`. Run this after `make upgrade` and before the
  practical live validation matrix.
- `make integration-opensearch`, `make integration-opensearch-restart`, and
  `make integration-opensearch-onyx` - validate the running full-stack
  OpenSearch volume, restart recovery, and pinned Onyx integration.
- `make up-lite` / `make up-full` - start the selected stack; full mode also
  performs its documented staged embedding-readiness flow; `CONTAINER_BIN` selects
  between docker and podman runtimes for the stack.
- Shared-data directory access checks fail closed on Docker/Podman runtime
  ownership status. Use `make adopt-shared-data-engine` to reset ownership
  only after verifying both engines are down; see `docs/podman_suport.md`.
- `make health-inventory` - render the Makefile-selected engine/environment and
  optional overlays for lite/full healthcheck commands, startup/steady
  cadences, and approximate steady checks per hour.
- `make down-lite` / `make down-full` - stop the matching stack.
- `make ps-lite` / `make ps-full` - inspect containers.
- `make logs-lite` / `make logs-full` - follow logs.
- `make upgrade` - refresh hashed Python locks, rebuild local components and
  SearXNG, pull pinned support images, and refresh Onyx deployment files.
- `make upgrade-onyx ONYX_CONFIG_REF=<tag>` - refresh only the generated Onyx
  deployment files for the selected ref and synchronize the generated local
  Onyx environment tags; it does not pull images.
- `make upgrade-python-deps` - upgrade hashed Python lock files from the committed `requirements.in` inputs.
- `make onyx-build`, `make executor-build`, `make searxng-build`,
  `make myst-build`, and `make teep-build` - image builds. The Docker-only
  executor image is derived from its pinned upstream release plus the hashed
  `executor/requirements.txt` lock and includes SymPy.
- `make embedserv-install`, `make embedserv-verify-model`, and `make embedserv-serve` - optional local MLX embedding server flow for the full RAG stack.

`make up-lite` and `make up-full` generate ephemeral local secrets on every start, including SearXNG, Onyx auth, and MinIO credentials. Do not move those secrets (or any other new ephemeral secrets) into `.env.wrapper.example`.

Do not read or modify the custom `.env.wrapper` unless specifically asked to do so. You may source this file into your environment without reading the contents, to apply the environment to service restart and patch diagnosis.

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
  - Treat Podman as a separately validated runtime, not a Docker alias. Follow
    `docs/podman_suport.md` for its overlay, mount, startup-health, socket, and
    clean-machine/restart validation requirements.
- VPN/proxy routing:
  - Preserve explicit VPN/no-VPN behavior, separate public/host route classes,
    narrow host/LAN exceptions, final-hop DNS ownership, and documented proxy
    environment handling.
  - Onyx applications must never join `netns-holder` or gain direct fallback when VPN, policy-proxy, or bridge connectivity fails.
- Request handling:
  - Keep supported search engines on the shared direct-Obscura path and preserve their atomic pre-thread provider reservation.
  - Preserve `open_url`'s chat-time exact-ID reuse semantics: it must not ingest
    crawled pages, replace `internal_search`, or turn URL opening into semantic
    retrieval. Indexed and crawler work remain failure-tolerant siblings, with
    indexed content preferred only after both paths complete.
  - Keep the built-in crawler's default stock requests/local-Chromium transport
    public-only; switching it to Obscura must not alter SearXNG's path.
  - Keep `ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB` authoritative for the built-in
    crawler in both transports without extending it to indexed retrieval,
    external providers, background ingestion, or SearXNG search limits.
  - When explicitly switched to Obscura, preserve one navigation, event-based
    waits, anti-bot visibility, cleanup, and body/DOM limits. Re-test the
    default on Obscura upgrades.
  - Do not add other hidden retries, fallbacks, or fixed sleeps.
- Documentation:
  - Update docs and AGENTS.md when behavior, defaults, commands, routing, or optional feature semantics change.
  - AGENTS.md is for orientation material; specifics belong in the docs directory.
  - README.md is for user-facing deployment properties, not implementation details.
  - Remove/replace obsolete text instead of keeping historical sections or adding dated journal entries.
- Patch upgrades:
  - Before changing Onyx, code-interpreter, SearXNG, Obscura, or Teep pins, or runtime Python lock inputs, read `docs/onyx_patches_upgrade.md`.
  - Runtime patches should remain narrow, startup-validated, strict by default, and documented.
  - Runtime startup must not install packages, regenerate dependency locks, or
    download browsers. Change dependency inputs deliberately, regenerate hashed
    locks with `make upgrade-python-deps`, and commit the resulting lock files.
- Local RAG compatibility:
  - Preserve the exact fake-nomic saved model name and tokenizer-only alias unless Onyx's feature gates are deliberately reworked.
  - Do not enable Craft or its cleanup schedule without adding and documenting a supported sandbox backend.
  - The Onyx PDF parser operates by producing parsing results on stdout. Onyx bootstrap diagnostics must stay on stderr because isolated child stdout carries a pickled result.
  - Bootstrap or process-isolation changes require a real PDF extraction test.
- Untracked stack files:
  - Treat `.env.wrapper`, `docker-data/`, and `doc-drop/` as local private data.
    Do not read, stage, or rewrite them unless the user explicitly asks.
  - Treat `onyx/onyx_data/deployment/.env` as potentially secret local runtime
    configuration. Do not read or stage it unless the user explicitly asks.
  - Other files under `onyx/onyx_data/` are generated upstream deployment
    artifacts. Inspect them when an upgrade or Compose audit requires it, but
    update them only through the Makefile upgrade flow and do not stage them.

### Testing and Validation

- Baseline workflow:
  - Use `make check` as the normal deterministic development and pre-handoff
    validation.
  - After changing an image/source pin or dependency lock, run `make upgrade`,
    then `make check-upgrade` against the newly produced images.
  - After a runtime-patch-only change, run `make check-upgrade` against the
    current pinned images; do not run the broader upgrade flow unnecessarily.
  - `make test-images` must validate the selected local images without silently
    pulling, rebuilding, or substituting another artifact.
  - Keep live stack, browser, VPN, and RAG checks explicit. They can require
    credentials, funding, private configuration, external services, or an
    already-running stack.
- Deterministic test coverage:
  - Use `make test` for the complete Python suite. Use the underlying unittest
    command only when focused debugging is useful.
  - Add focused cases under `tests/` for proxy destination/DNS policy, HTTP
    framing, bridge-peer authentication, route-class enforcement, executor
    network selection, and injected executor environment changes.
  - Runtime-limit patches need cases covering signature drift, configured
    values, and invalid values.
  - Deterministic tests must not require live Internet access, VPN credentials,
    or the private `.env.wrapper`.
- Compose and lifecycle changes:
  - For Compose or Makefile changes, run `make help` and inspect the effective
    Compose model for every affected mode using the Makefile's layering.
  - For startup changes, run the relevant `make up-lite` or `make up-full`, then
    inspect `make ps-*` and targeted service logs when practical.
- Request and routing changes:
  - For request-path changes, exercise a real `web_search` query and a real
    `open_url` request. Inspect SearXNG, Obscura, CDP gateway, API, bridge, and
    final-hop logs as needed.
  - For VPN/proxy changes, verify namespace membership, egress path,
    `NO_PROXY` behavior, and the absence of direct host-port or direct-network
    bypasses.
- Runtime patch and component changes:
  - For Onyx patches, confirm strict-mode startup success diagnostics and
    exercise the patched behavior.
  - For Python executor dependency changes, rebuild the derived executor,
    verify its exact package version without network access, and exercise a
    real code-interpreter call. Keep package descriptions aligned with the
    validated executor contents.
  - For full-mode RAG, test doc-drop crawling, PDF freshness/reindexing,
    embedding-shim health, and `internal_search`.
  - For SearXNG engines, test every affected custom engine with a real query.
  - For code-interpreter routing/proxy changes, test disabled and enabled modes
    and confirm that LLM-facing capability text matches executor networking.
- Incomplete validation:
  - If a relevant check cannot be run safely, state exactly what was omitted
    and why.

### Git Workflow

- Stage only files you intentionally changed. Do not use `git add .` or `git add -A`.
- Do not revert user changes or generated local state unless explicitly asked.
- For multi-phase plans, use one commit per phase when the user asks you to commit.
