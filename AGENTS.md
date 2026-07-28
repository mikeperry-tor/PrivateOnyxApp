# Code Agent Instructions for Private Onyx

This repository is a Docker Compose wrapper for running Onyx with private verified inference, default explicit no-VPN or optional VPN/proxy/Tor-routed Obscura browser access, an optional public-proxied stock Onyx crawler mode, SearXNG search, local document RAG, and optional Tailscale or v3 onion exposure.

It is not the upstream Onyx project, nor is it a fork. Most changes here are deployment, Compose, Python sidecar, shell, SearXNG, or runtime-patch changes around upstream projects.

The core correctness property is privacy-preserving request handling. Preserve
the documented routing, anti-fingerprinting, rate-limiting, patch-upgrade,
explicit-configuration, and fail-closed properties. When a requested change
affects one, identify the consequence and update its implementation, tests, and
owning documentation together.

## How to Use These Instructions

These instructions are an orientation layer. They tell you where to look and which repo-wide invariants not to violate; they do not replace the subsystem docs.

Before changing a subsystem, read its owning document below, then inspect the
implementation. If the implementation and docs disagree, treat that as a bug
to resolve or report. Do not paper over drift with vague wording; do not allow
unrelated drift to persist without reporting it to the user.

- `README.md` - user-facing setup instructions, privacy properties, and option consequences. Keep deep implementation details out of this document.
- `docs/request_handling.md` - `web_search`, the built-in crawler and
  `open_url`, selectable browser transport, lifecycle waits, body/DOM limits,
  cookies, and anti-bot behavior.
- `docs/native_tor_support.md` - Tor roles, Compose layers, storage, process
  contract, health, latency considerations, diagnostics, canonical-origin
  behavior, and change validation.
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
  the compatibility checklist. Read it before changes that could affect Podman
  compatibility, including Compose, mounts, container lifecycle, health
  checks, or socket-dependent behavior.

When a request touches more than one path, read each relevant doc first. Prefer small, doc-aligned changes over broad rewrites.

## Runtime Shape

At a high level:

- Users reach nginx through a hardened fixed host publisher or optional fixed Tailscale/onion frontend gateways; nginx stays internal-only.
- There are two main modes for the stack: lite and full. Full mode adds the
  local-document ingestion, embedding, and `internal_search` path documented
  in `docs/local_docs_rag_search.md`.
- Onyx sends LLM requests through the included Teep local inference service.
- `web_search`, the built-in crawler, and `open_url` are distinct from local
  document RAG. Their transport, retrieval, and failure contracts are
  documented in `docs/request_handling.md`.
- Optional Tor can add substantial connection and circuit latency. Before
  changing any timeout, deadline, lifecycle wait, or startup allowance on a
  path that can use Tor, read `docs/native_tor_support.md` and the affected
  request or routing document and preserve enough time for that route.

## Network Security

Network topology and final-hop policy are privacy boundaries, not ordinary
deployment details. Before changing egress, reachability, DNS, proxying,
browser routing, or WebUI network controls, read
`docs/internal_network_security.md` and `docs/vpn_routing_and_proxies.md`, plus
`docs/native_tor_support.md` when either Tor role may be affected.

Preserve the documented route separation, final-hop destination authority, and
fail-closed behavior: a failed selected bridge, proxy, VPN, or Tor path must
not give an application, browser, or executor direct fallback egress. The
owning documents define the exact trust boundaries, exceptions, lifecycle,
residual risks, and validation requirements.

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
  - `compose_overlays/` - full/lite mode, optional routing/proxy/executor,
    Docker/Podman, and native Tor Compose layers selected by the Makefile.
    The root base remains first so every merged layer resolves relative paths
    from the repository root.
  - `compose_overlays/docker-compose.tor*.yml` and `tor/` - optional native
    Tor roles, strict config renderer, local authenticated health, and fixed
    onion gateway.
- Routing and component implementations:
  - `browser/obscura_client/` - shared direct-CDP client used by Onyx and
    SearXNG.
  - `browser/obscura_image/` - verified multi-architecture wrapper that replaces
    the upstream lean binaries with the matching official stealth release.
  - `egress/` - shared final-hop policy-proxy implementation.
  - `searxng/` - derived image, custom direct-Obscura offline engines, and
    SearXNG overlay.
  - `myst/` and `teep/` - component build files, entrypoints, and helper flows.
- Onyx wrapper code:
  - `onyx/patches/` - runtime `sitecustomize` patches applied inside Onyx
    application containers. Code-interpreter networking uses native settings.
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

- `make help` is a user-facing command index. Add targets to it only for
  user-facing workflows or key bug-report diagnostics; keep internal
  development and component-scoped targets in this file or the owning
  subsystem document.
- `make test` - run the deterministic Python suite without requiring images,
  credentials, a running stack, or the private `.env.wrapper` contents.
- `make check` - run `make test`, compile repository runtime/test Python paths,
  validate `make help`, and run `git diff --check`. Use this as the normal
  pre-handoff check when implementation, configuration, tests, or build/runtime
  tooling changes.
- `make test-patch-images` - install strict runtime patches against the
  already-built pinned Onyx, code-interpreter, and derived SearXNG images,
  then run the SearXNG parser tests that need image dependencies. Use it for a
  focused Onyx runtime patch, code-interpreter/executor, or SearXNG image/parser
  change; do not invoke the Tor or OpenSearch image gates. It does not pull or
  build missing images or permit validation-container networking; use the
  reported build target first.
- `make test-tor-image` - validate the selected local Tor base/derived image
  contract, hardened runtime, Unix sockets, volume ownership, and authenticated
  control path. Use it only for Tor image, config, mount, ownership, health, or
  control changes.
- `make test-opensearch-image` - validate the pinned OpenSearch image in an
  isolated disposable environment. Use it only for the OpenSearch pin,
  configuration, audit policy, or image-validation workload.
- `make test-all-images` - run `make test-patch-images`,
  `make test-tor-image`, and `make test-opensearch-image`. Reserve it for
  changes spanning multiple image families or broad release validation; do not
  use it for unrelated focused work.
- `make check-upgrade` - run `make check` followed by
  `make test-all-images`. Use it after the broad `make upgrade` flow or as a
  release-wide image gate before the practical live validation matrix.
- `make integration-opensearch`, `make integration-opensearch-restart`, and
  `make integration-opensearch-onyx` - validate the running full-stack
  OpenSearch volume, restart recovery, and pinned Onyx integration.
- `make up-lite` / `make up-full` - start the selected stack; full mode also
  performs its documented staged embedding-readiness flow; `CONTAINER_BIN` selects
  between docker and podman runtimes for the stack.
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
  `make obscura-build`,
  `make myst-build`, and `make teep-build` - image builds. The Docker-only
  executor image is derived from its pinned upstream release plus the hashed
  `executor/requirements.txt` lock.

## Repository Rules

This stack protects private research, document contents, browsing behavior, inference traffic, local credentials, VPN identities, and proxy configuration.

### Implementation Style

- Prefer small, explicit changes over broad rewrites.
- When necessary, runtime patches should be narrow, startup-validated, covered by tests, and documented in `docs/onyx_patch_info.md` and `docs/onyx_patches_upgrade.md`.
- Prefer structured parsers or compose-aware inspection over ad hoc text hacks when changing configuration formats.
- Prefer component-configurable behavior over shims and patches.
- Remove shims and patches when component configuration options or other updates are discovered that could provide the desired functionality.
- Don't preserve compatibility for old behavior unless the user specifically
  requests it. Prefer current, explicit behavior over backwards compatibility.
- Keep optional features opt-in and visibly configured.
- Preserve the documented Docker/Podman and macOS/Linux support matrix. Do not
  assume that documented engine-specific behavior is interchangeable, and
  avoid introducing new platform dependencies.

### General Failure Handling

- Fail loudly and fail closed, especially for patch application and patch error handling.
- Do not add silent fallbacks, broad error suppression, `|| true`, direct-network bypasses, empty-result substitutes, or weaker parser paths unless the user explicitly asks for that behavior.
- Shell scripts should use strict error handling where practical and should not hide failing commands that affect privacy, routing, or validation.
- Python sidecars should return clear protocol-appropriate errors and log
  non-secret reasons.
- Privacy-, routing-, and runtime-patch-critical assumptions should be
  startup-validated and halt service health or stack launch when they do not
  hold.
- Keep image/tag resolution, patch application, routing setup, proxy setup, and validation checks as visible non-secret failures.

### Component-Specific Rules

- Compose layering:
  - The Makefile assembles `COMPOSE_FILE`. Keep optional behavior in override files, keep Docker and Podman behavior separated, and preserve generated local secret flow plus Compose `${VAR:?message}` checks.
  - Treat Podman as a separately validated runtime, not a Docker alias. Read
    `docs/podman_suport.md` before changes that could affect its overlays,
    mounts, lifecycle, startup health, socket use, or compatibility matrix.
- Documentation:
  - Update AGENTS.md only when repository-wide invariants, documentation
    routing, supported workflows, or key locations change. Put subsystem
    behavior and validation details in the owning document.
  - README.md is for user-facing deployment properties, not implementation details.
  - In normative README and subsystem documentation, remove or replace obsolete
    text instead of retaining historical sections.
  - Keep implementation plans accurate through implementation and review,
    including their status, accepted decisions, and validation evidence. Plans
    are review artifacts, not long-term documentation: document current behavior
    canonically in the appropriate owning `docs/` document before implementation
    is complete, and link to that document from the plan instead of duplicating
    the lasting specification across plans or multiple subsystem documents.
  - Files under `docs/plans/implemented/` are historical implementation records.
    Preserve them after review/merge, but do not revise them for later behavior
    changes or append progress journals and post-implementation fix diaries;
    update the canonical owning document instead.
- Patch upgrades:
  - Before changing Onyx, code-interpreter, SearXNG, Obscura, or Teep pins, or runtime Python lock inputs, read `docs/onyx_patches_upgrade.md`.
  - Runtime patches should remain narrow, startup-validated, strict by default, and documented.
  - Runtime startup must not install packages, regenerate dependency locks, or
    download browsers. Change dependency inputs deliberately, regenerate hashed
    locks with `make upgrade-python-deps`, and include the resulting lock files
    in the change. Create a Git commit only when the user requests one.
- Untracked stack files:
  - Stack startup generates ephemeral local secrets, including SearXNG, Onyx
    auth, and MinIO credentials. Do not move them or other ephemeral secrets
    into `.env.wrapper.example`.
  - Treat `.env.wrapper`, `docker-data/`, and `doc-drop/` as local private data.
    Do not display, inspect, modify, stage, rewrite, or shell-source them unless
    the user explicitly asks. Pass `.env.wrapper` through the supported Make
    and Compose mechanisms when its configuration is needed.
  - Treat `onyx/onyx_data/deployment/.env` as potentially secret local runtime
    configuration. Do not read or stage it unless the user explicitly asks.
  - Other files under `onyx/onyx_data/` are generated upstream deployment
    artifacts. Inspect them when an upgrade or Compose audit requires it, but
    update them only through the Makefile upgrade flow and do not stage them.

### Testing and Validation

- Baseline workflow:
  - Use `make check` as the normal deterministic pre-handoff validation when
    implementation, configuration, tests, or build/runtime tooling changes.
  - For changes limited to documentation, AGENTS.md, or plans, inspect the diff
    and run `git diff --check`; `make check` is not required.
  - Use the component-scoped image target documented in **Commands** for a
    focused image or runtime-contract change. Use aggregate image targets only
    for multi-family or broad release work.
  - Every focused image target must validate the selected local images without
    silently pulling, rebuilding, or substituting another artifact.
  - Keep live stack, browser, VPN, and RAG checks explicit. They can require
    credentials, funding, private configuration, external services, or an
    already-running stack.
  - All tests that probe OS/platform-specific capability should detect the
    current OS/platform prior to running the test.
- Deterministic test coverage:
  - Use `make test` for the complete Python suite. Use the underlying unittest
    command only when focused debugging is useful.
  - Do not test prose merely to freeze its wording. Test executable behavior,
    machine-consumed contracts, generated examples, and executable
    documentation at their source.
  - Deterministic tests must not require live Internet access, VPN credentials,
    or the private `.env.wrapper`.
- Compose and lifecycle changes:
  - For Compose or Makefile changes, inspect the effective Compose model for the
    affected Makefile-selected engine/mode/feature combinations identified by
    the owning subsystem's validation matrix.
  - For startup changes, run the relevant `make up-lite` or `make up-full`, then
    inspect `make ps-*` and targeted service logs when practical.
  - Run the deterministic, image, lifecycle, and live checks required by each
    affected subsystem document.
- Incomplete validation:
  - If a relevant check cannot be run safely, state exactly what was omitted
    and why.

### Git Workflow

- Stage only files you intentionally changed. Do not use `git add .` or `git add -A`.
- Do not revert user changes or generated local state unless explicitly asked.
- For multi-phase plans, use one commit per phase when the user asks you to commit.
