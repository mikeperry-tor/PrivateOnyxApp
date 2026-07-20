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
- Full mode validates embedding readiness in a staged one-shot gate before a
  fresh API/background tier, uses an on-demand host MLX lifecycle proxy for the
  bundled backend with loopback-peer enforcement, bounded connection threads,
  tokenized launch/configuration identity plus strict child PID, port, and
  served-model ownership, visible unbounded cold-start readiness while the
  child remains alive, a five-minute proxy-to-child blocked-socket timeout,
  single-attempt shim forwarding, and
  applies the low-idle background/storage policy documented in
  `docs/resource_minimization.md`. OpenSearch uses static single-node settings:
  a 512 MiB heap, four configured processors, monthly body-free audit
  initialization, disabled Query Insights top-N collection, and zero replicas
  for new Onyx indices.
  There is no runtime OpenSearch migration or administrative sidecar. Exact
  background control processes stay outside the application
  `sitecustomize` bootstrap; Beat, workers, and indexing children remain
  strictly patched. Full-mode bot workers are explicit default-off options
  selected by `ONYX_AGENT_SLACK_BOT` and `ONYX_AGENT_DISCORD_BOT`; lite mode
  has no background supervisor or bot processes.
- The stdlib-only `embedserv/host_process_manager.py` owns common detached lifecycle,
  atomic PID/token/configuration records, readiness waits, and identity-checked
  stops for the bundled MLX proxy and Podman host document server. Keep
  service-specific child/model/peer/content validation in those services. Lite
  mode selects neither host service; Docker full mode selects no host document
  server and skips the manager for clean Teep/custom-embedding starts.
- The recommended local-RAG Admin model name `nomic-ai/nomic-embed-text-v23` is intentionally synthetic: it preserves Onyx's `nomic-ai` feature gates while a strict runtime patch aliases only tokenizer construction to the bundled v1 tokenizer.
- The wrapper uses the legacy code-interpreter service and explicitly disables unsupported Craft sandbox scheduling.
- Onyx sends LLM requests through the included Teep local inference service.
- The shared API runtime patches give nested Deep Research agents the tools selected for the current chat Agent and execute complete model-emitted tool batches with bounded concurrency.
- Onyx `web_search` calls SearXNG, whose explicit five-engine custom offline set
  navigates and parses rendered provider pages through Obscura. Inherited stock
  engines are absent so they cannot initialize or perform startup network work.
- Onyx `open_url()` is a chat-time read tool, not an ingestion path. For every
  requested URL it concurrently performs a fresh crawl and, in full mode,
  exact-ID retrieval of any document that a connector indexed previously; it
  prefers that indexed copy when present. The default crawler uses Python
  `requests` with a local Chromium fallback; both stages are restricted to
  public-only egress through the fixed Onyx bridge.
- Lite mode keeps crawler-backed `open_url()` available through a strict runtime
  patch while indexed URL retrieval remains disabled.
- Setting `ONYX_AGENT_USE_OBSCURA_BROWSER=true` moves only the built-in crawler to the single-navigation Obscura path. At Obscura 0.1.10, testing found the stock crawler was blocked less often; re-evaluate this default on Obscura upgrades.

## Network Security

This stack controls egress and internal reachability through network-namespace
topology, explicit routes, and final-hop destination policy. Safety must not
depend on application-level proxy settings or caller discipline alone.

- SearXNG, Obscura, and optional executor pods use narrow internal networks. Internet traffic crosses component bridges to final-hop policy proxies in the trusted Mysterium routing namespace.
- Direct callers validate URL syntax without resolving target names; authoritative destination DNS and address validation remain at the final-hop policy.
- Isolated Obscura allows private-address resolution only so its HTTP client can
  resolve the mandatory Docker egress-bridge proxy. Its narrow network prevents
  direct Internet egress, and the final-hop policy remains authoritative for
  target DNS and private-target rejection.
- Onyx applications use internal-only networks. Generic helpers use a fixed
  public bridge; saved-level MCP/Web Connector traffic and configured
  chat inference plus the dedicated embedding shim use separate public or
  host-capable bridges and route-class-specific final-hop policy listeners.
  Identical public policies share a proxy process but keep distinct bridges
  and caller networks. Direct sockets have no external route; executor pods
  never inherit Onyx exceptions. The exact internal Teep chat base is a
  startup-validated direct-service exception, and doc-drop Web Connector
  traffic uses an exact host final-hop gateway rather than a process-wide
  direct crawl.
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
- Myst retains a 20-second route/MTU reconciliation bound. Exact matching
  exemption routes are silent no-ops; writes and success logs occur only for
  missing or drifted target/gateway/device state. One immediate post-connect
  pass is followed by a single background reconciliation owner.
- Optional Onyx telemetry, third-party analytics/tracing, cloud billing,
  CAPTCHA, and automatic remote configuration/data-list fetches are explicitly
  disabled in Compose. Release-note polling and local administrative analytics
  intentionally remain enabled.
- Nginx adds a restrictive WebUI CSP because browser requests are outside the
  container routing boundary. Preserve its same-origin resource policy,
  locally generated chat-file images, and local preview schemes. The stock
  Next.js image requires inline bootstrap/stream scripts, so the current
  `'unsafe-inline'` exception is a documented XSS residual, not a claim of
  complete XSS prevention.

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
  - `docker-compose.*-vpn.yml`, `docker-compose.vpn-autoheal.yml`,
    `docker-compose.proxy.yml`, and Podman overrides - optional routing, proxy,
    recovery, and container-engine layers selected by the Makefile.
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
- `make test-opensearch-image` - run the pinned OpenSearch image with an
  engine-managed disposable volume and no external network, then validate the
  512 MiB/four-processor runtime, plugins, audit policy, indexing, KNN/hybrid
  search, reindexing, concurrent work, failure counters, and restart recovery.
- `make check-upgrade` - run `make check`, `make test-images`, and
  `make test-opensearch-image`. Run this after `make upgrade` and before the
  practical live validation matrix.
- `make integration-opensearch` - run the same disposable-index workload
  against the current full-stack OpenSearch volume without restarting it.
- `make integration-opensearch-restart` - add an OpenSearch-only restart and
  recovery check. `make integration-opensearch-onyx` separately exercises the
  exact pinned Onyx schema, client, normalization pipeline, and hybrid query.
  All three use `CONTAINER_BIN` and support the Docker/Podman command surface.
- `make up-lite` / `make up-full` - start the lite or full stack. Full mode
  also starts the bundled host MLX lifecycle proxy when its selected model is
  already installed and the shim still uses the bundled default endpoint, then
  validates `/ready` once before creating a new API/background tier. That
  foreground readiness wait has no short wrapper deadline and prints the MLX
  log path so a slow or stuck load remains visible and interruptible;
  `make down-full` stops only that identity-validated proxy and child group,
  including a strictly recorded orphan left by a proxy crash.
  Custom upstreams and manually launched servers are not touched.
- The first Docker/Podman shared-data ownership claim inspects installed engine
  commands for running Onyx PostgreSQL/OpenSearch writers and fails closed on
  conflicting writers or an inspection failure. `make adopt-shared-data-engine`
  is only for an absent marker after the operator verifies both engines are down.
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
- `make onyx-build`, `make searxng-build`, `make myst-build`, and
  `make teep-build` - image builds.
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
  - Preserve explicit VPN/no-VPN behavior, the separate public/host Onyx route classes, exact host and opt-in `ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS` policy, operator-local `.local`/`.internal`/`.home.arpa` DNS restriction, optional routing switches, and `EGRESS_UPSTREAM_PROXY_URL`/`NO_PROXY` handling.
  - Public upstream-proxy names and addresses must use provider DNS and the VPN route in VPN mode; exact host and RFC1918-literal proxy endpoints use only their documented narrow route exceptions, while named operator-local proxies require `ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true`.
  - Empty or failed operator-local target lookups fail closed without external fallback; only non-empty all-global answers return to the selected public final hop.
  - Onyx applications must never join `netns-holder` or gain direct fallback when VPN, policy-proxy, or bridge connectivity fails.
- Request handling:
  - Keep supported search engines on the shared direct-Obscura path and preserve their atomic pre-thread provider reservation.
  - Preserve `open_url`'s chat-time exact-ID reuse semantics: it must not ingest
    crawled pages, replace `internal_search`, or turn URL opening into semantic
    retrieval. Indexed and crawler work remain failure-tolerant siblings, with
    indexed content preferred only after both paths complete.
  - The built-in Onyx Web Crawler defaults to the stock requests/local-Chromium mode because it was blocked less often in Obscura 0.1.10 testing; preserve its public-only fixed-proxy adapter, no local target DNS, disabled environment/loopback bypasses, and unchanged SearXNG path.
  - Keep `ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB` authoritative for the built-in
    crawler in both modes: stock requests PDF/HTML bytes, stock Chromium
    rendered-HTML bytes, and the direct-Obscura main body and rendered DOM. It
    remains a per-representation post-materialization check on the stock path,
    not a complete download or peak-memory bound. It must not affect external
    content providers, background RAG ingestion, indexed document retrieval, or
    SearXNG's independent fixed search-DOM limit.
  - When explicitly switched to Obscura, preserve one navigation, event-based
    waits, anti-bot visibility, complete cleanup, and the common configured
    limit on both main-response body and rendered DOM. Re-test the default on
    Obscura upgrades.
  - Do not add other hidden retries, fallbacks, or fixed sleeps.
- Documentation:
  - Update docs and AGENTS.md when behavior, defaults, commands, routing, or optional feature semantics change.
  - Remove obsolete text instead of keeping long historical sections.
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
