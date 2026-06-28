# Onyx patch information

Last updated for Onyx v4.1.7.

This document explains why this wrapper carries local Onyx patches, how those
patches modify Onyx at runtime or install time, and how the same behavior could
be turned into proper upstream merge requests. For line-oriented upgrade checks,
use the companion inventory in
[`docs/onyx_patches_upgrade.md`](onyx_patches_upgrade.md). For operator-facing
setup and troubleshooting of the local document RAG path, use
[`docs/local_docs_rag_search.md`](local_docs_rag_search.md).

Related implementation docs:

- [Local document RAG search](local_docs_rag_search.md) describes the
  `doc-drop-web` connector path, local embedding shim, optional MLX embedding
  server, and RAG-specific diagnostics.
- [Request handling](request_handling.md) describes how web search and
  `open_url` requests flow through SearXNG, CRW, the CDP shim, and obscura.
- [VPN routing and proxies](vpn_routing_and_proxies.md) describes the
  Compose-level VPN namespace, `ONYX_AGENT_OUTBOUND_PROXY_URL`, and optional teep, Tailscale, and
  code-interpreter routing modes.
- [Internal network security](internal_network_security.md) records the tested
  internal reachability of wrapper shims and services, including the
  prefetch-proxy direct CONNECT risk and code-interpreter executor networking
  gap.

Reference checkouts:

- `reference_repos/onyx` contains the Onyx source used for v4.1.7 review.
- `reference_repos/python-sandbox` contains the code-interpreter image source
  used by Onyx's `onyxdotapp/code-interpreter` image.

## Design goals

The wrapper patches are not meant to fork Onyx behavior broadly. They solve a
small set of local deployment requirements that Onyx v4.1.7 does not expose as
configuration:

- Let a private deployment tune tool limits without rebuilding Onyx images.
- Keep internal-search result count and content budgets small enough for local
  document RAG without relying on obscure upstream env names.
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

The wrapper's `ONYX_SECURITY_SSRF_*` env vars are not prerequisites for the
runtime patches themselves. They only seed Onyx's Admin -> Security Hardening
SSRF Protection level for URL-fetching paths such as Web connectors, MCP/OAuth
endpoints, and the fallback `OnyxWebCrawler` provider. The local embedding shim
uses explicit model-server routing instead, and CRW/Obscura browser traffic is
governed by the Compose network/proxy/VPN layout rather than those Onyx SSRF
settings.

## Modification summary

| Area | Onyx service or component | Local mechanism | Upstream shape |
| --- | --- | --- | --- |
| Open URL and web search character budgets | `api_server` | `sitecustomize` rewrites module constants and function defaults | Admin/env settings for per-URL and aggregate tool budgets |
| Internal search context limits | `api_server` | `sitecustomize` rewrites search defaults and wraps result formatting; full compose passes wrapper env aliases | Admin/env settings for candidate count, returned context count, and content budgets |
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
at interpreter startup. When `ONYX_OPEN_URL_MAX_CHARS_PER_URL` or
`ONYX_OPEN_URL_MAX_TOTAL_CHARS` is set, it:

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

- `ONYX_OPEN_URL_MAX_CHARS_PER_URL`
- `ONYX_OPEN_URL_MAX_TOTAL_CHARS`
- Equivalent admin preferences or per-assistant tool settings

The implementation should read the configured values at call time or pass them
through tool configuration, rather than relying on module constants captured as
default parameters. Tests should cover default behavior, custom limits, and the
"unlimited" value if that is accepted upstream.

## Internal search context limits

Local files:

- `onyx/patches/sitecustomize_base/wrapper_env_patches.py`
- `onyx/patches/sitecustomize_base/sitecustomize.py`
- `docker-compose.full.yml`
- `.env.wrapper.example`

Onyx source areas:

- `backend/onyx/configs/chat_configs.py`
- `backend/onyx/tools/models.py`
- `backend/onyx/tools/tool_implementations/search/search_tool.py`
- `backend/onyx/tools/tool_implementations/search/search_utils.py`
- `backend/onyx/tools/tool_implementations/utils.py`
- `backend/onyx/server/features/search/api.py`
- `backend/onyx/mcp_server/tools/search.py`

### Why this is needed

Onyx v4.1.7's internal search result payload is section/chunk content, not a
short excerpt. The agent-facing `internal_search` tool, the `/search` API, and
the MCP `search_indexed_documents` tool all ultimately forward the LLM-facing
search JSON. That JSON uses the selected section's `combined_content`, and a
section may contain merged adjacent chunks or chunks added by Onyx's context
expansion flow.

The upstream defaults are also easy to misunderstand. `NUM_RETURNED_HITS=50`
controls how many candidate hits/sections are kept before later selection, and
`MAX_CHUNKS_FED_TO_CHAT=25` is used as a rough pre-selection token budget and
as a final section-count limit. With 512-token chunks, those defaults can
produce tool responses large enough to crowd the answering model's context
window in a local document RAG workflow.

### How it modifies Onyx

Full mode passes clearer wrapper settings to `api_server`:

- `ONYX_RAG_INTERNAL_SEARCH_MAX_CANDIDATE_SECTIONS`
- `ONYX_RAG_INTERNAL_SEARCH_MAX_CONTEXT_SECTIONS`
- `ONYX_RAG_INTERNAL_SEARCH_MAX_CONTENT_CHARS_PER_RESULT`
- `ONYX_RAG_INTERNAL_SEARCH_MAX_TOTAL_CONTENT_CHARS`

The compose layer also maps
`ONYX_RAG_INTERNAL_SEARCH_MAX_CONTEXT_SECTIONS` to upstream
`MAX_CHUNKS_FED_TO_CHAT` so Onyx's own config import sees the lower value.

The base `sitecustomize` patch then:

- Replaces `chat_configs.NUM_RETURNED_HITS`.
- Replaces `chat_configs.MAX_CHUNKS_FED_TO_CHAT`.
- Updates the captured Pydantic defaults on
  `SearchToolOverrideKwargs.num_hits` and
  `SearchToolOverrideKwargs.max_llm_chunks`.
- Wraps `convert_inference_sections_to_llm_string()` so each result's
  `content` field and the aggregate returned content can be capped after Onyx
  has merged and expanded sections.
- Replaces the formatter reference imported into `search_tool.py`, since that
  module imports the helper by name.

The wrapper defaults are intentionally lower than upstream:

```env
ONYX_RAG_INTERNAL_SEARCH_MAX_CANDIDATE_SECTIONS=24
ONYX_RAG_INTERNAL_SEARCH_MAX_CONTEXT_SECTIONS=8
ONYX_RAG_INTERNAL_SEARCH_MAX_CONTENT_CHARS_PER_RESULT=6000
ONYX_RAG_INTERNAL_SEARCH_MAX_TOTAL_CONTENT_CHARS=30000
```

### Upstream merge request shape

This should become first-class Onyx configuration, ideally with names that
distinguish candidate retrieval count, final context section count, per-result
content budget, and aggregate tool-response budget. The final budget should be
applied after context expansion, because expansion is where a selected section
can grow past the nominal chunk count.

Tests should cover the chat `internal_search` tool, `/search`, and MCP
`search_indexed_documents`, because all three paths can expose the same
oversized content.

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

The base API-server `sitecustomize` path calls this helper. Lite mode loads the
lite `sitecustomize` first and currently imports selected base helpers for
character limits and code-interpreter capability text, but not this defensive
Firecrawl helper. For Onyx v4.1.7 that does not change the live request shape,
because upstream already sends the same no-`waitFor` payload.

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

Onyx describes the Python tool, Bash tool, and coding agent as running without
network access when `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=false`. That matches
code-interpreter 0.4.4's default executor network, `none`.

The wrapper can explicitly enable executor networking through the shared stack
namespace. When that is enabled, the upstream descriptions become actively
harmful: the LLM is told not to use network operations even though network
access is available and expected. The inverse would also be dangerous, so the
text must track actual executor capabilities.

### How it modifies Onyx

When `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true` is present in `api_server`, the base
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

The patch checks exact upstream string matches before claiming success. The
wrapper runs these patches in strict mode, so missing expected strings or
changed helper signatures fail startup instead of silently leaving stale tool
text in place.

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

The lite `sitecustomize` patch imports selected base helpers for environment
driven character limits and code-interpreter capability text, then forces
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

For local document-drop setup and troubleshooting, see
[Local Document RAG Search](local_docs_rag_search.md#local-document-rag-search).
For the line-oriented upgrade inventory, see
[Background Web connector PDF freshness patch](onyx_patches_upgrade.md#background-web-connector-pdf-freshness-patch).

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
- Treats HTTP 401, 403, and 404 from the trusted preflight as terminal
  unreadable/missing PDF states and returns a wrapper skip sentinel instead of
  letting Onyx parse the error body as a PDF. Upstream Web connector code uses
  4xx/5xx page responses as the "skip this URL" signal; the wrapper applies
  that same intent to the direct PDF download path.
- Compares those validators to metadata stored on the DB document.
- If the validators and `doc_updated_at` match, returns an empty-section
  `ScrapeResult` that tells Onyx the document is unchanged.
- Marks unchanged and unreadable sentinels with wrapper `doc_metadata` and
  patches Onyx's document update gate so forced/targeted reindex paths do not
  accidentally index those empty sentinels as empty documents.
- If the PDF is scraped normally and its content hash matches the existing DB
  document, seeds freshness metadata so future runs can skip the download.
- If the parsed content hash differs, allows Onyx's normal re-index path.

Full-mode Compose sets the internal freshness allowlist to localhost addresses.
The patch still reads `ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_HOSTS` and
`ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_ENABLED` for upgrade/debug overrides, but
those are not part of the user-facing `.env.wrapper` surface.

The patch stores wrapper metadata keys:

- `_wrapper_http_freshness_version`
- `_wrapper_http_last_modified`
- `_wrapper_http_content_length`
- `_wrapper_http_freshness_source`
- `_wrapper_http_freshness_unchanged` on pre-download skip sentinels only
- `_wrapper_http_freshness_unreadable` on terminal HTTP-status skip sentinels
  only
- `_wrapper_http_status` on terminal HTTP-status skip sentinels only

By default, the patch logs startup status and one-time warnings for unexpected
conditions such as missing validators, HEAD failures, sentinel mismatches, or
indexing-patch failures. The internal
`ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_DEBUG=true` override enables per-document hit
and miss details while validating patch behavior.

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

Onyx's code-interpreter source, from `python-sandbox`, defaults executor pods to
Docker network `none`. That is a strong and sensible hosted default.

This wrapper also supports a different deployment mode: trusted, single-tenant
local execution where generated Python and bash should be able to reach the
internet through the shared VPN namespace, optionally using the configured
upstream proxy. This is useful for research, package inspection, fetching
public data, and coding-agent workflows where network commands are expected.

The wrapper uses code-interpreter 0.4.4's
`PYTHON_EXECUTOR_DOCKER_NETWORK` setting to choose the executor container
network before command construction.

### How it modifies Onyx

When `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`, `docker-compose.code-interpreter-vpn.yml`
sets `PYTHON_EXECUTOR_DOCKER_NETWORK=container:onyx-netns-holder-1`, so
code-interpreter starts executor containers directly in the shared network
namespace. The code-interpreter container also loads a `sitecustomize` patch
that:

- Patches `DockerExecutor._build_run_command`.
- Injects proxy env vars and optional SOCKS support into executor pod commands.

The wrapper compose already runs code-interpreter in the shared
`netns-holder` namespace. Inheriting that namespace gives executor pods the
same egress path: Mysterium when `MYST_VPN_ENABLED=true`, or direct bridge
egress when VPN is explicitly disabled.

When `ONYX_AGENT_OUTBOUND_PROXY_URL` is set, the same patch injects proxy environment variables
into executor pod commands. This proxy injection is independent from the
executor network setting: with `ONYX_AGENT_OUTBOUND_PROXY_URL` alone, executor pods remain
network-isolated. With both `ONYX_AGENT_OUTBOUND_PROXY_URL` and
`ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`, executor pods inherit the shared
namespace and supported tools use the configured upstream proxy.

For SOCKS proxies, the patch also:

- Creates a Docker volume named `onyx-proxy-libs`.
- Synchronously installs the hashed `PySocks` and `socksio` lock from
  `onyx/patches/sitecustomize_code_interpreter/proxy-libs-requirements.txt`
  into that volume using `PROXY_LIBS_INSTALL_IMAGE` during code-interpreter
  startup.
- Mounts the volume read-only into executor pods at `/tmp/proxy-libs` only if
  setup succeeds.
- Prepends that directory to `PYTHONPATH` only if setup succeeds.

This is intentionally high trust. It removes the upstream executor network
isolation when `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true` and lets generated code make
outbound network requests. See
[VPN routing and proxies](vpn_routing_and_proxies.md#code-interpreter-executor-pods)
for the service-level routing behavior.

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

For local/custom embedding setup, query prefix behavior, and diagnostics, see
[Local Document RAG Search](local_docs_rag_search.md#embedding-shim). For
upgrade checks against Onyx's model-server contract, see
[Local embedding shim](onyx_patches_upgrade.md#local-embedding-shim).

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

Onyx's agent-facing `internal_search` tool uses the normal Onyx search
pipeline, not a separate Web connector HTTP path. In v4.1.7,
`SearchTool._run_search_for_query()` calls `search_pipeline()`, which calls
`search_chunks()`, which embeds each query through
`get_query_embedding()`/`EmbeddingModel.encode()`. Because full mode points
`MODEL_SERVER_HOST`/`MODEL_SERVER_PORT` at the shim, a failed query embedding
surfaces to the agent as an `internal_search` tool failure even when the
document-drop HTTP server and indexed Web connector documents are healthy.

The shim keeps a small pool of upstream HTTP connections to the local
OpenAI-compatible embedding server. Some local embedding servers close idle
keep-alive connections without the client knowing. Reusing one of those stale
sockets raises a low-level transport error such as `Remote end closed
connection without response`; Onyx then wraps the resulting 502 as
`HTTP error occurred - response is None.` To avoid making transient stale
connection reuse visible to `internal_search`, the shim closes and replaces a
connection on `OSError`, `TimeoutError`, or `http.client.HTTPException`, logs
`upstream_connection_retry`, and retries the embedding request once on a fresh
socket. HTTP status errors from the upstream server are not retried; they remain
visible.

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

The RAG-specific sidecars described here are covered operationally in
[Local Document RAG Search](local_docs_rag_search.md#web-connector-server), and
line-oriented full-mode compose checks live in
[Full mode](onyx_patches_upgrade.md#full-mode-compose).

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

Full mode adds model-server routing through the local embedding shim, internal
search context-limit env aliases for `api_server`, background worker patches,
OpenSearch/cache/MinIO storage, and local document-drop services.

The local `doc-drop-web` service runs `onyx/doc_drop_webserver.py` instead of the
stdlib `python -m http.server` entrypoint directly. It keeps normal static-file
behavior for readable documents, hides hidden filesystem entries such as
`.git`, `._*`, `.DS_Store`, and `__pycache__` from directory listings, returns
HTTP 404 for direct requests to hidden paths, and converts unreadable file
requests into HTTP 403 responses so crawlers receive a proper status instead of
a closed connection.

Lite mode removes full-mode dependencies, uses Postgres-backed storage options,
sets `DISABLE_VECTOR_DB=true`, and loads the lite Open URL patch.

Podman overrides disable or profile services that depend on Docker socket
semantics that rootless Podman on macOS does not reliably provide.

Proxy and VPN override files thread optional egress configuration through Onyx
services and wrapper sidecars. The routing matrix is documented in
[VPN routing and proxies](vpn_routing_and_proxies.md).

### SearXNG overlay

The SearXNG sidecar uses `searxng/core-config/settings.yml` as a minimal
`use_default_settings` overlay on top of the image defaults. The wrapper owns
only the settings needed for the Onyx web-search path:

- enable `json` output while keeping `html` diagnostics;
- set `server.secret_key` from the ephemeral `SEARXNG_SECRET` generated by the
  Makefile;
- set the non-custom default `outgoing.request_timeout`;
- remove stock direct Google, Brave, DuckDuckGo, and Startpage engines;
- add the CRW-backed `google2`, `brave2`, `duckduckgo2`, and `startpage2`
  engines.

The Makefile exports `SEARXNG_SECRET` for wrapper starts so startup does not
rely on the overlay placeholder. Other SearXNG env-overridden defaults, such
as `SEARXNG_PORT`, `SEARXNG_BIND_ADDRESS`, `SEARXNG_BASE_URL`,
`SEARXNG_LIMITER`, `SEARXNG_PUBLIC_INSTANCE`, `SEARXNG_IMAGE_PROXY`,
`SEARXNG_METHOD`, and `SEARXNG_VALKEY_URL`, stay inherited from the image
defaults rather than being duplicated in the overlay.

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
- `stack.versions.env`
- `onyx/install.sh`
- `onyx/install-with-container-bin.sh`

Onyx source area:

- `reference_repos/onyx/deployment/docker_compose/install.sh`
- `reference_repos/onyx/deployment/docker_compose/env.template`

### Why this is needed

The upstream install script is optimized for directly installing Onyx's Docker
Compose deployment. The wrapper needs a more deterministic and automatable
workflow:

- Require wrapper image tags and source refs from `stack.versions.env`, with
  `.env.wrapper` and make CLI values available as explicit local overrides.
- Generate local stack auth material (`SEARXNG_SECRET`, `USER_AUTH_SECRET`,
  `CRW_ONYX_API_KEY`, and MinIO/S3 credentials) ephemerally for each Makefile
  invocation.
- Support Docker or Podman through `CONTAINER_BIN`.
- Refresh upstream deployment files for a chosen config ref.
- Initialize and sync Onyx's `.env` noninteractively.
- Avoid operator-managed local stack secrets.
- Start full or lite wrapper Compose stacks with the correct override layers.

### How it modifies Onyx

The Makefile orchestrates upgrade and runtime flow:

- `upgrade-onyx` downloads upstream compose, lite compose, env template, README,
  and nginx files for `ONYX_CONFIG_REF`.
- `init-onyx-env` runs the Onyx installer through the local wrapper.
- `sync-onyx-env` pins `IMAGE_TAG` and `CODE_INTERPRETER_IMAGE_TAG`.
- `onyx-build` uses the installer path to prepare or pull required Onyx images.
- `upgrade-python-deps` upgrades the hashed runtime Python locks for
  `embedserv`, `cdp-shim`, and code-interpreter SOCKS proxy support from their
  `requirements.in` files. Most package inputs are unconstrained so this target
  can move them forward; `embedserv/requirements.in` keeps `typer==0.20.0`
  pinned because newer Typer releases trigger a `sys.exit()` handler traceback
  in the local embedserv CLI path.

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
- Runtime secret injection without editing env files.
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
