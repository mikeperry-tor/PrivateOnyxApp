# Onyx patch information

Last updated for Onyx v4.1.7.

This document explains why this wrapper carries local Onyx patches, how those
patches modify Onyx at runtime or install time, and how the same behavior could
be turned into proper upstream merge requests. For line-oriented upgrade checks,
use the companion inventory in `docs/onyx_patches_upgrade.md`.

Reference checkouts:

- `reference_repos/onyx` contains the Onyx source used for v4.1.7 review.
- `reference_repos/python-sandbox` contains the code-interpreter image source
  used by Onyx's `onyxdotapp/code-interpreter` image.

## Design goals

The wrapper patches are not meant to fork Onyx behavior broadly. They solve a
small set of local deployment requirements that Onyx v4.1.7 does not expose as
configuration:

- Let a private deployment tune tool limits without rebuilding Onyx images.
- Keep generated tool descriptions accurate when executor capabilities differ
  from upstream defaults.
- Support trusted, VPN-routed code-interpreter execution when explicitly
  enabled.
- Use a local OpenAI-compatible embedding server while preserving Onyx's
  model-server HTTP contract.
- Run useful Web and Open URL workflows in lite mode.
- Avoid repeated local PDF downloads and parsing when trusted HTTP validators
  prove the source document has not changed.
- Make Onyx's Docker Compose install and runtime fit the wrapper's container
  engine, proxy, VPN, and sidecar topology.

Upstreamable versions of these changes should keep current Onyx defaults
unchanged. Riskier behavior, especially code-interpreter network access and
trusted HTTP freshness, should remain explicit opt-in configuration.

## Modification summary

| Area | Onyx service or component | Local mechanism | Upstream shape |
| --- | --- | --- | --- |
| Open URL and web search character budgets | `api_server` | `sitecustomize` rewrites module constants and function defaults | Admin/env settings for per-URL and aggregate tool budgets |
| Firecrawl scrape payload | `api_server` | `sitecustomize` replaces `FirecrawlClient._get_webpage_content` | Configurable Firecrawl request options |
| Code-interpreter capability text | `api_server` | `sitecustomize` rewrites tool descriptions and prompt constants | Capability-driven tool descriptions generated from actual executor config |
| Lite Open URL availability | Lite `api_server` | `sitecustomize` forces `OpenURLTool.is_available` true | Separate Open URL availability from vector DB availability |
| Web connector PDF freshness | `background` | `sitecustomize` wraps `WebConnector._do_scrape` | Trusted-host HTTP validator freshness policy |
| Code-interpreter executor networking and proxying | `code-interpreter` and executor pods | `sitecustomize` mutates `DockerExecutor._build_run_command` | Supported executor network/proxy configuration in `python-sandbox` |
| Local embedding bridge | `api_server`, `background` | Shim service implements selected model-server endpoints | First-class OpenAI-compatible embedding provider |
| Compose wrapper | Runtime services | Compose `extends`, overrides, sidecars, network namespace | Official compose extension points and documented env knobs |
| Install hooks | Install/upgrade flow | Makefile plus installer wrapper scripts | Installer flags for engine, image tag, config ref, and noninteractive setup |

## Open URL and web search character budgets

Local files:

- `onyx/patches/sitecustomize_base/wrapper_env_patches.py`
- `onyx/patches/sitecustomize_base/sitecustomize.py`
- `docker-compose.yaml`

Onyx source areas:

- `backend/onyx/tools/tool_implementations/web_search/utils.py`
- `backend/onyx/tools/tool_implementations/open_url/open_url_tool.py`

### Why this is needed

Onyx v4.1.7 hardcodes the amount of page text that `web_search` and `open_url`
can return to the LLM. Those defaults are reasonable for many hosted
deployments, but they are too small for local research tasks that intentionally
feed longer documents, local manuals, or source references through Onyx tools.

The wrapper needs to raise or effectively remove those limits from environment
configuration, without rebuilding the upstream Onyx backend image.

### How it modifies Onyx

The base `sitecustomize` module imports Onyx's web-search and open-url modules
at interpreter startup. When `OPEN_URL_MAX_CHARS_PER_URL` or
`OPEN_URL_MAX_CHARS_ACROSS_URLS` is set, it:

- Replaces `web_search.utils.MAX_CHARS_PER_URL`.
- Rewrites default arguments on truncation helper functions that captured the
  old constant at function definition time.
- Replaces `open_url_tool.MAX_CHARS_ACROSS_URLS`.
- Rewrites the default argument on the Open URL section-to-LLM formatting
  helper.

Setting a limit to `0` means "effectively unlimited" and is implemented as a
very large integer budget.

### Upstream merge request shape

This should become regular Onyx configuration instead of a monkey patch.
Possible options:

- `OPEN_URL_MAX_CHARS_PER_URL`
- `OPEN_URL_MAX_CHARS_ACROSS_URLS`
- Equivalent admin preferences or per-assistant tool settings

The implementation should read the configured values at call time or pass them
through tool configuration, rather than relying on module constants captured as
default parameters. Tests should cover default behavior, custom limits, and the
"unlimited" value if that is accepted upstream.

## Firecrawl scrape payload control

Local files:

- `onyx/patches/sitecustomize_base/wrapper_env_patches.py`
- `onyx/patches/sitecustomize_base/sitecustomize.py`

Onyx source area:

- `backend/onyx/tools/tool_implementations/open_url/firecrawl.py`

### Why this is needed

The wrapper can route scraping through CRW, Obscura, and a browser readiness
path outside Onyx. In that setup, a fixed Firecrawl `waitFor` delay is a poor
fit: it can waste time on pages that are ready quickly and still fail on pages
that need adaptive browser handling.

Onyx v4.1.7 already sends only `url` and `formats` for the scrape payload, so
this patch is mostly defensive for this release. It preserves the wrapper's
desired contract if upstream later adds a hardcoded wait.

### How it modifies Onyx

The patch replaces `FirecrawlClient._get_webpage_content` and sends only:

- `url`
- `formats: ["markdown"]`

It preserves Onyx's response parsing path and the existing behavior that treats
some client-side scrape failures as empty content instead of fatal errors.

### Upstream merge request shape

Onyx could expose Firecrawl scrape options as configuration:

- Disable fixed wait entirely.
- Set a wait duration when a deployment wants one.
- Allow a controlled allowlist of additional Firecrawl payload fields.

The default should match current upstream behavior. A merge request should also
include tests that verify the constructed scrape payload.

## Code-interpreter capability descriptions

Local files:

- `onyx/patches/sitecustomize_base/wrapper_env_patches.py`
- `docker-compose.yaml`
- `docker-compose.code-interpreter-vpn.yml`

Onyx source areas:

- `backend/onyx/tools/tool_implementations/python/python_tool.py`
- `backend/onyx/prompts/tool_prompts.py`
- `backend/onyx/tools/tool_implementations/bash/bash_tool.py`
- `backend/onyx/coding_agent/mock_tools.py`
- `backend/onyx/prompts/coding_agent/coding_agent.py`
- `backend/onyx/tools/fake_tools/coding_agent.py`

### Why this is needed

Upstream Onyx describes the Python tool, Bash tool, and coding agent as running
without network access. That is correct for the default code-interpreter image,
which starts executor pods with `--network none`.

The wrapper can explicitly enable VPN-routed executor networking. When that is
enabled, the upstream descriptions become actively harmful: the LLM is told not
to use network operations even though network access is available and expected.
The inverse would also be dangerous, so the text must track actual executor
capabilities.

### How it modifies Onyx

When `CODE_INTERPRETER_VPN_ROUTED=true` is present in `api_server`, the base
patch rewrites LLM-facing text for:

- `PythonTool.DESCRIPTION`
- `PYTHON_TOOL_GUIDANCE`
- `BashTool.DESCRIPTION`
- Coding-agent bash tool metadata
- Coding-agent system prompts

The replacement text says that network access is available through the VPN,
that Python tool package installation is limited, and that the coding agent is
better for package installation or multi-step coding workflows. It also names
local scraping and browser services available in the shared namespace.

The patch checks exact upstream string matches before claiming success.
`WRAPPER_PATCH_STRICT=true` is the default, so missing expected strings or
changed helper signatures fail startup instead of silently leaving stale tool
text in place. Set `WRAPPER_PATCH_STRICT=false` only for temporary diagnosis.

### Upstream merge request shape

Onyx should generate tool descriptions from a capability model rather than
hardcoded assumptions. Useful capability fields include:

- Executor network mode: disabled, enabled, VPN-routed, or proxied.
- Whether Python package installation is supported.
- Which package managers or network commands are expected to work.
- Optional local service hints for deployments that expose internal tools.

The API server and code-interpreter service should share the same source of
truth. A merge request could start with static env-driven capabilities, then
later grow into a code-interpreter capability endpoint.

## Lite Open URL availability

Local files:

- `onyx/patches/sitecustomize/sitecustomize.py`
- `docker-compose.lite.yml`

Onyx source areas:

- `backend/onyx/tools/tool_implementations/open_url/open_url_tool.py`
- `backend/onyx/tools/tool_constructor.py`
- `backend/onyx/server/query_and_chat/session_loading.py`

### Why this is needed

Lite mode disables the vector DB and removes full-mode dependencies. The wrapper
still wants live web and Open URL workflows in lite mode, especially for chat
and research against external or local web pages.

In Onyx v4.1.7, `OpenURLTool.is_available` is coupled to vector DB availability.
That disables a tool that can still be useful as a live fetch and summarization
tool.

### How it modifies Onyx

The lite `sitecustomize` patch imports the base patches and then forces
`OpenURLTool.is_available` to return `True`.

This changes tool exposure only. It does not add vector DB functionality or make
indexed retrieval available in lite mode.

### Upstream merge request shape

Onyx should separate:

- Whether live URL fetching is configured and allowed.
- Whether indexed document retrieval is available.
- Whether vector DB backed workflows are available.

`OpenURLTool` could degrade gracefully when vector DB is disabled, or Onyx could
expose a lite-mode tool allowlist. Tests should cover lite mode with Open URL
enabled and no vector DB.

## Background Web connector PDF freshness

Local files:

- `onyx/patches/sitecustomize_background/sitecustomize.py`
- `docker-compose.full.yml`

Onyx source areas:

- `backend/onyx/connectors/web/connector.py`
- `backend/onyx/connectors/models.py`
- `backend/onyx/db/models.py`

### Why this is needed

The wrapper exposes a local, read-only document drop over HTTP so Onyx's Web
connector can ingest local PDFs. These PDFs are often large and mostly static.
Re-downloading and re-parsing every unchanged file wastes time and can make
local indexing feel broken or noisy.

Onyx v4.1.7 intentionally does not trust `Last-Modified` for web PDFs, which is
a good default for arbitrary public web pages. The wrapper has a narrower case:
trusted local hosts where `Last-Modified` and `Content-Length` are stable
validators from a controlled file server.

### How it modifies Onyx

The background `sitecustomize` patch wraps `WebConnector._do_scrape`.

For allowlisted hosts only, it:

- Performs a HEAD request before scraping a PDF.
- Reads `Last-Modified` and `Content-Length`.
- Compares those validators to metadata stored on the DB document.
- If the validators and `doc_updated_at` match, returns an empty-section
  `ScrapeResult` that tells Onyx the document is unchanged.
- Marks unchanged sentinels with wrapper `doc_metadata` and patches Onyx's
  document update gate so forced/targeted reindex paths do not accidentally
  index those empty sentinels as empty documents.
- If the PDF is scraped normally and its content hash matches the existing DB
  document, seeds freshness metadata so future runs can skip the download.
- If the parsed content hash differs, allows Onyx's normal re-index path.

The allowlist is configured by `ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_HOSTS` and
defaults to localhost addresses. The patch can be disabled with
`ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_ENABLED=false`.

The patch stores wrapper metadata keys:

- `_wrapper_http_freshness_version`
- `_wrapper_http_last_modified`
- `_wrapper_http_content_length`
- `_wrapper_http_freshness_source`
- `_wrapper_http_freshness_unchanged` on pre-download skip sentinels only

By default, the patch logs startup status and one-time warnings for unexpected
conditions such as missing validators, HEAD failures, sentinel mismatches, or
indexing-patch failures. Per-document hit and miss details are available with
`ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_DEBUG=true`.

### Upstream merge request shape

This should become a Web connector freshness policy, not a wrapper metadata
hack. The important upstream distinction is trust:

- Public web default: keep current cautious behavior.
- Trusted hosts or connectors: allow HTTP validator based freshness.

Useful options:

- Trusted-host allowlist.
- Validator set: `ETag`, `Last-Modified`, `Content-Length`.
- Whether validator matches can skip download before parsing.
- Observability logs for hit, miss, and fallback reasons.

A merge request should include tests with a local static HTTP server:

- First ingest indexes the PDF.
- Second ingest with unchanged validators skips download or parsing.
- Changed content with changed validators re-indexes.
- Missing validators fall back to current behavior.

## Code-interpreter executor networking and proxying

Local files:

- `onyx/patches/sitecustomize_code_interpreter/sitecustomize.py`
- `docker-compose.code-interpreter-vpn.yml`
- `docker-compose.proxy.yml`
- `docker-compose.yaml`

Python-sandbox source area:

- `reference_repos/python-sandbox/code-interpreter/app/services/executor_docker.py`

### Why this is needed

Onyx's code-interpreter source, from `python-sandbox`, hardcodes executor pods
with `--network none`. That is a strong and sensible hosted default.

This wrapper also supports a different deployment mode: trusted, single-tenant
local execution where generated Python and bash should be able to reach the
internet through a VPN or proxy. This is useful for research, package
inspection, fetching public data, and coding-agent workflows where network
commands are expected.

Because upstream appends custom Docker run args after its isolation flags,
setting an extra `--network` flag is not enough; Docker rejects conflicting
network options. The wrapper has to replace the built-in `--network none`.

### How it modifies Onyx

When `CODE_INTERPRETER_VPN_ROUTED=true`, the code-interpreter container loads a
`sitecustomize` patch that:

- Resolves the code-interpreter container's own Docker container ID.
- Patches `DockerExecutor._build_run_command`.
- Replaces `--network none` with `--network container:<self>`.
- Causes executor pods to inherit the code-interpreter container's network
  namespace.

The wrapper compose already runs code-interpreter in the shared
`netns-holder` namespace. Inheriting that namespace gives executor pods the
same VPN-routed egress.

When `PROXY_URL` is set, the same patch injects proxy environment variables
into executor pods. For SOCKS proxies, it also:

- Creates a Docker volume named `onyx-proxy-libs`.
- Synchronously installs `PySocks` and `socksio` into that volume using
  `python:3.11-slim` during code-interpreter startup.
- Mounts the volume read-only into executor pods at `/tmp/proxy-libs` only if
  setup succeeds.
- Prepends that directory to `PYTHONPATH` only if setup succeeds.

This is intentionally high trust. It removes the upstream executor network
isolation and lets generated code make outbound network requests.

### Upstream merge request shape

The right upstream home is probably `python-sandbox`, with Onyx consuming the
resulting capabilities.

Potential configuration:

- `EXECUTOR_NETWORK_MODE=none|bridge|container:<id>|service:<name>`
- `EXECUTOR_EXTRA_DOCKER_RUN_ARGS`
- `EXECUTOR_PROXY_ENV_ALLOWLIST`
- `EXECUTOR_PROXY_URL`
- `EXECUTOR_MOUNT_PROXY_LIBS=true`

The implementation should avoid conflicting Docker flags by making the built-in
network setting configurable before command construction. It should also expose
the effective executor capabilities to Onyx so tool descriptions stay accurate.

Security documentation is part of the feature. Defaults should remain network
disabled.

## Local embedding shim

Local files:

- `onyx/local_embedding_shim.py`
- `docker-compose.full.yml`
- `Makefile`

Onyx source areas:

- `backend/model_server/*`
- `backend/shared_configs/model_server_models.py`
- `backend/onyx/natural_language_processing/search_nlp_models.py`
- `backend/onyx/indexing/embedder.py`
- `backend/onyx/utils/gpu_utils.py`

### Why this is needed

Onyx expects embedding calls to go through its model-server API, especially
`/encoder/bi-encoder-embed`. Many local embedding servers, including
OpenAI-compatible MLX servers, expose `/v1/embeddings` instead.

The wrapper wants to use local embeddings, often on Apple Silicon, without
pretending that the local server implements the full Onyx model-server API and
without rebuilding Onyx.

Older notes may refer to `local_embedding_sim.py`; the checked-in file is
`onyx/local_embedding_shim.py`.

### How it modifies Onyx

The shim does not import or patch Onyx directly. Instead, full-mode compose
points Onyx's model-server environment variables at the shim:

- `MODEL_SERVER_HOST=127.0.0.1`
- `MODEL_SERVER_PORT=9101`
- `INDEXING_MODEL_SERVER_HOST=127.0.0.1`
- `INDEXING_MODEL_SERVER_PORT=9101`

The shim runs in the shared network namespace, so `127.0.0.1:9101` resolves for
both `api_server` and `background`.

The shim implements the endpoints Onyx needs for local embedding startup and
embedding requests:

- `GET /health`
- `GET /api/gpu-status`
- `POST /encoder/bi-encoder-embed`

It translates Onyx embedding payloads into OpenAI-compatible embedding requests:

- Onyx `texts` becomes OpenAI `input`.
- Onyx or wrapper model names become OpenAI `model`.
- Query and passage prefixes come from Onyx request fields or wrapper env.
- Responses are translated back into `{"embeddings": ...}`.

It intentionally returns 501 for model-server features it does not implement:

- `POST /encoder/cross-encoder-scores`
- `POST /custom/query-analysis`

That keeps unsupported rerank and query-analysis calls visible instead of
silently producing fake results.

### Upstream merge request shape

Onyx could support OpenAI-compatible embedding providers directly. Useful
configuration:

- Embedding provider type: Onyx model server or OpenAI-compatible.
- Base URL and API key.
- Served model name override.
- Separate query and passage prefixes.
- Optional normalization and dimensions settings if supported.
- Separate routing for embeddings, reranking, and query analysis.

The key design point is that embeddings should not require a server to mimic
the entire Onyx model-server surface. Reranking and query analysis should be
independently configurable or explicitly disabled.

## Docker Compose wrapper modifications

Local files:

- `docker-compose.yaml`
- `docker-compose.full.yml`
- `docker-compose.lite.yml`
- `docker-compose.podman.yml`
- `docker-compose.podman-full.yml`
- `docker-compose.code-interpreter-vpn.yml`
- `docker-compose.proxy.yml`

Onyx source area:

- `reference_repos/onyx/deployment/docker_compose/docker-compose.yml`
- `reference_repos/onyx/deployment/docker_compose/docker-compose.onyx-lite.yml`

### Why this is needed

The wrapper runs Onyx as part of a larger local system with:

- A shared network namespace.
- VPN and proxy egress.
- Search and browser sidecars.
- Optional Tailscale and Teep routing.
- Local document-drop services.
- Local embedding services.
- Docker and Podman compatibility layers.
- Full and lite runtime profiles.

Upstream Compose files are a deployment product, not a library API. The wrapper
therefore uses Compose `extends` and override layers to keep close to upstream
while changing the pieces needed for the local topology.

### How it modifies Onyx

The base compose wrapper changes the runtime shape of core Onyx services:

- `api_server` joins the shared namespace, receives wrapper env, mounts
  `sitecustomize` patches, disables telemetry/cloud flags, and points
  code-interpreter calls at localhost.
- `web_server` joins the shared namespace and disables analytics/cloud UI flags.
- `nginx` no longer publishes ports directly; host access goes through a
  wrapper proxy.
- `code-interpreter` joins the shared namespace, moves to port 7000, and can be
  patched for VPN-routed executor pods.
- `relational_db` uses wrapper-managed persistent storage.

Full mode adds model-server routing through the local embedding shim, background
worker patches, OpenSearch/cache/MinIO storage, and local document-drop
services.

Lite mode removes full-mode dependencies, uses Postgres-backed storage options,
sets `DISABLE_VECTOR_DB=true`, and loads the lite Open URL patch.

Podman overrides disable or profile services that depend on Docker socket
semantics that rootless Podman on macOS does not reliably provide.

Proxy and VPN override files thread optional egress configuration through Onyx
services and wrapper sidecars.

### Upstream merge request shape

Some wrapper sidecars are deployment-specific and do not belong in upstream
Onyx. The upstreamable pieces are extension points:

- Documented env vars for external code-interpreter URL and port.
- Documented env vars for external model-server or embedding-provider routing.
- A supported way to disable telemetry and hosted-cloud assumptions for local
  deployments.
- A maintained lite profile that keeps live web tools available when safe.
- Compose snippets or documentation for Podman limitations.
- Clear support for running Onyx behind a reverse proxy or shared network
  namespace.

## Install and upgrade hooks

Local files:

- `Makefile`
- `onyx/install.sh`
- `onyx/install-with-container-bin.sh`

Onyx source area:

- `reference_repos/onyx/deployment/docker_compose/install.sh`
- `reference_repos/onyx/deployment/docker_compose/env.template`

### Why this is needed

The upstream install script is optimized for directly installing Onyx's Docker
Compose deployment. The wrapper needs a more deterministic and automatable
workflow:

- Use `ONYX_IMAGE_TAG` from `.env.wrapper` as the image source of truth.
- Support Docker or Podman through `CONTAINER_BIN`.
- Refresh upstream deployment files for a chosen config ref.
- Initialize and sync Onyx's `.env` noninteractively.
- Generate missing secrets and avoid default MinIO credentials.
- Start full or lite wrapper Compose stacks with the correct override layers.

### How it modifies Onyx

The Makefile orchestrates upgrade and runtime flow:

- `upgrade-onyx` downloads upstream compose, lite compose, env template, README,
  and nginx files for `ONYX_CONFIG_REF`.
- `init-onyx-env` runs the Onyx installer through the local wrapper.
- `sync-onyx-env` pins `IMAGE_TAG`, generates `USER_AUTH_SECRET` when missing,
  and replaces default MinIO/S3 credentials.
- `onyx-build` uses the installer path to prepare or pull required Onyx images.

`install-with-container-bin.sh` wraps the upstream install script so it can run
through the selected container engine instead of assuming `docker`. The local
`onyx/install.sh` is kept as a patched installer entrypoint for the wrapper's
current flow.

### Upstream merge request shape

Onyx's installer could expose flags for the behaviors the wrapper currently has
to patch around:

- Container engine or compose command.
- Desired image tag.
- Config ref to download.
- Noninteractive env initialization.
- No-start or prepare-images-only mode.
- Lite or full install selection.
- Secret generation without starting the stack.
- Port remapping without editing compose by script.

The goal would be to let downstream deployments automate Onyx upgrades without
sed-based install wrappers.

## Upstreaming priorities

The smallest high-value merge requests are:

1. Env-configurable Open URL and web search character limits.
2. Firecrawl scrape payload options, including no fixed wait.
3. Open URL availability independent of vector DB availability.
4. Code-interpreter capability descriptions driven by executor configuration.
5. `python-sandbox` executor network and proxy configuration.
6. OpenAI-compatible embedding provider support with independent rerank and
   query-analysis routing.
7. Trusted-host HTTP validator freshness for the Web connector.
8. Installer flags for container engine, image tag, config ref, and
   noninteractive setup.

For each upstream change, preserve current Onyx behavior as the default, add
tests for both default and enabled behavior, and document security implications
where the option changes network or scraping trust boundaries.
