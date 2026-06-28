# Local Document RAG Search

This document explains the local-document RAG path provided by the full Onyx
wrapper. It is the design and troubleshooting companion for the shorter setup
steps in `README.md` and the line-oriented upgrade checklist in
[`docs/onyx_patches_upgrade.md`](onyx_patches_upgrade.md).

Use this document for operator-facing setup, request flow, and diagnostics. For
the patch rationale and possible upstream shape, see
[`docs/onyx_patch_info.md`](onyx_patch_info.md), especially
[Background Web connector PDF freshness](onyx_patch_info.md#background-web-connector-pdf-freshness),
[Internal search context limits](onyx_patch_info.md#internal-search-context-limits),
[Local embedding shim](onyx_patch_info.md#local-embedding-shim), and
[Docker Compose wrapper modifications](onyx_patch_info.md#docker-compose-wrapper-modifications).
For line-numbered Onyx upgrade checks, use
[`docs/onyx_patches_upgrade.md`](onyx_patches_upgrade.md), especially
[Background Web connector PDF freshness patch](onyx_patches_upgrade.md#background-web-connector-pdf-freshness-patch),
[Local embedding shim](onyx_patches_upgrade.md#local-embedding-shim), and
[Full mode](onyx_patches_upgrade.md#full-mode-compose).

The local path is intentionally built out of wrapper services instead of an
Onyx fork:

- `doc-drop-web` exposes a local document directory as a small read-only HTTP
  site for Onyx's Web connector.
- `host-doc-drop-web-proxy` publishes that site back to the host on
  `HOST_PORT_ONYX_RAG_DOC_WEB`, so the connector URL and resulting document
  links use `http://localhost:8091/` by default.
- `onyx/patches/sitecustomize_background/sitecustomize.py` adds a local-host
  PDF freshness optimization for the Web connector.
- `onyx/patches/sitecustomize_base/sitecustomize.py` applies full-mode
  internal-search context limits so selected chunks do not overwhelm the
  answering model.
- `local-embedding-shim` implements the subset of Onyx's model-server HTTP API
  that indexing and search need for embeddings, then forwards the actual
  embedding work to an OpenAI-compatible `/v1/embeddings` endpoint.
- The optional `make embedserv-*` targets install and run a local MLX
  OpenAI-compatible embedding server on the host.

These pieces exist because Onyx v4.1.7 has strong assumptions about where local
documents and embedding models live. The wrapper keeps the user-facing Onyx
configuration simple while preserving those assumptions at the service
boundary.

## End-To-End Flow

1. Files are placed under `ONYX_RAG_DOC_SOURCE_DIR`, defaulting to `./doc-drop`.
2. `doc-drop-web` mounts that directory read-only at `/import/docs` and serves
   it from inside the shared `netns-holder` namespace.
3. `host-doc-drop-web-proxy` publishes the service to the host at
   `http://localhost:${HOST_PORT_ONYX_RAG_DOC_WEB:-8091}/`.
4. In Onyx Admin -> Connectors -> Web, create a Recursive Web connector pointed
   at `http://localhost:8091/`.
5. The Onyx `background` worker crawls the directory listing and downloads
   documents through the Web connector.
6. During indexing, `background` calls `MODEL_SERVER_HOST:MODEL_SERVER_PORT`.
   In full mode the wrapper points that to `127.0.0.1:9101`, which is the local
   embedding shim in the same network namespace.
7. The shim converts Onyx model-server `EmbedRequest` payloads into
   OpenAI-compatible embedding requests and sends them to `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL`.
8. During chat/search, `api_server` uses the same shim path to embed the query
   before Onyx runs hybrid search against the indexed documents.
9. Before internal-search results are returned to the answering model, the
   wrapper limits candidate sections, LLM-facing sections, and result content
   character budgets using the full-mode env settings below.

The important subtlety is that `127.0.0.1` and `localhost` are not always the
same machine in this stack. `MODEL_SERVER_HOST=127.0.0.1` means loopback inside
the shared Docker namespace. The Web connector URL `http://localhost:8091/`
means the crawler requests a URL that resolves as local from the Onyx container
and remains clickable from the host browser because the proxy publishes the same
port.

## Web Connector Server

Implementation:

- Compose services: `doc-drop-web` and `host-doc-drop-web-proxy` in
  `docker-compose.full.yml`
- Server script: `onyx/doc_drop_webserver.py`
- User-facing env: `ONYX_RAG_DOC_SOURCE_DIR` and `HOST_PORT_ONYX_RAG_DOC_WEB`

Upgrade-sensitive compose details for these services live in
[Full mode](onyx_patches_upgrade.md#full-mode-compose). The
broader reason the wrapper carries these sidecars is described in
[Docker Compose wrapper modifications](onyx_patch_info.md#docker-compose-wrapper-modifications).

`doc-drop-web` is a thin Python `http.server` wrapper. It deliberately does not
try to parse documents. Onyx's Web connector is responsible for downloading,
extracting, chunking, and indexing PDFs, Office documents, EPUBs, and other
supported formats.

The wrapper server adds a few behaviors that are useful for a document drop:

- Directory listings hide hidden entries such as `.DS_Store`, dotfiles,
  `__pycache__`, and any path component beginning with `.`.
- Direct requests for hidden paths return 404.
- Unreadable files return 403 instead of producing a server traceback and a
  broken connection.
- The mounted document directory is read-only in the container.

This server exists because Onyx's Web connector already knows how to crawl HTTP
URLs, but it does not have a first-class "local directory" connector in this
wrapper. Serving the directory over HTTP gives Onyx a normal connector source
without giving the Onyx containers write access to the user's document tree.

## SSRF And Security Hardening

Onyx v4.1+ has SSRF protection for URL-fetching paths. The wrapper seeds the
default Onyx Security Hardening posture with:

```env
ONYX_SECURITY_SSRF_VALIDATE_OPEN_URL=true
ONYX_SECURITY_SSRF_ALLOW_PRIVATE_NETWORK=true
ONYX_SECURITY_SSRF_ALLOW_LOOPBACK=false
```

In Onyx v4.1.7 this maps to "Allow Private Network" when no Security Hardening
setting has been saved in the Admin UI. That is the desired default for local
doc-drop crawling: the Web connector is allowed to crawl the local document
server, MCP/OAuth endpoints may use private LAN or `host.docker.internal`
addresses, and loopback MCP/OAuth endpoints such as `127.0.0.1` remain blocked.
Setting `ONYX_SECURITY_SSRF_ALLOW_LOOPBACK=true` seeds the broader "Disabled"
posture and should be reserved for cases that intentionally need loopback
MCP/OAuth access, such as the internal Obscura MCP server.

Once an admin saves Security Hardening settings in Onyx, the saved UI value is
the effective runtime policy and these env vars only act as startup defaults.
If doc-drop crawling suddenly fails after UI changes, check Admin -> Security
Hardening first. The strict "Validate All" posture can block the local Web
connector.

There is an intentional tradeoff here. The document source is local and trusted
by the operator, so the wrapper optimizes for making Onyx's Web connector see
that source. Do not point the doc-drop connector at arbitrary untrusted local
services just because the SSRF setting allows the wrapper's document server.

These settings do not govern the local embedding shim's upstream call and are
not firewall rules for CRW or Obscura browser traffic. In particular, they do
not stop JavaScript running in Obscura from attempting requests to internal
network addresses that are reachable from the browser namespace; browser
same-origin/CORS behavior is not a stack-internal access-control boundary.

## PDF Freshness Patch

Implementation:

- `onyx/patches/sitecustomize_background/sitecustomize.py`
- Internal environment:
  `ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_ENABLED=true` and
  `ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_HOSTS=localhost,127.0.0.1,::1`

This section keeps the operational behavior in one place. The detailed patch
rationale is in
[Background Web connector PDF freshness](onyx_patch_info.md#background-web-connector-pdf-freshness);
the upstream symbols and line references to re-check during an Onyx upgrade are
in
[Background Web connector PDF freshness patch](onyx_patches_upgrade.md#background-web-connector-pdf-freshness-patch).

Onyx v4.1.7 intentionally avoids trusting `Last-Modified` for Web PDFs because
public websites often emit unreliable validators. That is sensible for the
general internet, but wasteful for a local document-drop server where the file
metadata is trusted and stable.

The background patch changes that only for allowlisted local hosts. For PDFs
served from `localhost`, `127.0.0.1`, or `::1` by default, it uses HTTP `HEAD`
metadata such as `Last-Modified` and `Content-Length` to skip full download and
PDF parsing when the source has not changed. It stores wrapper metadata on the
Onyx document record so later syncs can make the same decision.

This patch is tied to Onyx internals. During a major Onyx upgrade, verify that
the Web connector still has the same scrape path, that connector `Document`
objects still expose `doc_updated_at`, `doc_metadata`, and `content_hash()`, and
that the database `Document` model still stores compatible fields. If Onyx adds
native incremental freshness for Web PDFs, prefer that and remove or narrow this
patch.

## Internal Search Context Limits

Implementation:

- Compose env: `docker-compose.full.yml`
- Runtime patch: `onyx/patches/sitecustomize_base/wrapper_env_patches.py`
- User-facing env:
  `ONYX_RAG_INTERNAL_SEARCH_MAX_CANDIDATE_SECTIONS`,
  `ONYX_RAG_INTERNAL_SEARCH_MAX_CONTEXT_SECTIONS`,
  `ONYX_RAG_INTERNAL_SEARCH_MAX_CONTENT_CHARS_PER_RESULT`, and
  `ONYX_RAG_INTERNAL_SEARCH_MAX_TOTAL_CONTENT_CHARS`

Onyx's internal search path is chunk-oriented, not excerpt-oriented. In v4.1.7,
the `internal_search` tool and the `/search` API serialize the selected
section's full `content` into the model-facing tool result. Onyx may also merge
nearby matching chunks and expand a selected section with adjacent chunks before
formatting the result. That is useful for answer quality, but local document
sets can produce enough text to fill small or medium model context windows.

Full mode therefore exposes wrapper-level names for the relevant budgets:

```env
ONYX_RAG_INTERNAL_SEARCH_MAX_CANDIDATE_SECTIONS=24
ONYX_RAG_INTERNAL_SEARCH_MAX_CONTEXT_SECTIONS=8
ONYX_RAG_INTERNAL_SEARCH_MAX_CONTENT_CHARS_PER_RESULT=6000
ONYX_RAG_INTERNAL_SEARCH_MAX_TOTAL_CONTENT_CHARS=30000
```

`ONYX_RAG_INTERNAL_SEARCH_MAX_CANDIDATE_SECTIONS` limits the ranked sections kept
before LLM document selection. `ONYX_RAG_INTERNAL_SEARCH_MAX_CONTEXT_SECTIONS`
limits how many selected sections are serialized back to the answering model;
the compose layer also passes it to upstream Onyx as `MAX_CHUNKS_FED_TO_CHAT`.
The two character budgets are applied after Onyx's context-expansion step, so a
single expanded result cannot dominate the final tool response.

Lower these values when internal search is consuming too much of the model
window. Raise them when search is missing necessary context even though the
right document is being found. Changes require recreating `api_server` because
the base `sitecustomize` patch runs at Python startup.

## Embedding Shim

Implementation:

- Compose service: `local-embedding-shim` in `docker-compose.full.yml`
- Shim script: `onyx/local_embedding_shim.py`
- Onyx services routed to it: `api_server` and `background`

This section focuses on how to run and diagnose the shim. The rationale and
upstreamable design are in
[Local embedding shim](onyx_patch_info.md#local-embedding-shim); the model-server
contract and upstream references to verify during upgrades are in
[Local embedding shim](onyx_patches_upgrade.md#local-embedding-shim).

The shim listens on `0.0.0.0:9101` inside the shared namespace. Full mode sets
these env vars for `api_server` and `background`:

```env
MODEL_SERVER_HOST=127.0.0.1
MODEL_SERVER_PORT=9101
INDEXING_MODEL_SERVER_HOST=127.0.0.1
INDEXING_MODEL_SERVER_PORT=9101
```

That makes Onyx believe it is talking to its own model server. The shim exposes
the endpoints Onyx expects for embedding-related paths:

- `GET /health`
- `GET /api/gpu-status`
- `POST /encoder/bi-encoder-embed`
- `POST /encoder/cross-encoder-scores`, as an intentional 501 stub
- `POST /custom/query-analysis`, as an intentional 501 stub

Only `/encoder/bi-encoder-embed` performs real work. It accepts Onyx
`EmbedRequest` JSON with fields such as `texts`, `model_name`, `text_type`,
`manual_query_prefix`, and `manual_passage_prefix`, then sends:

```json
{"model": "<model>", "input": ["<text>", "..."]}
```

to `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL`, which must be an OpenAI-compatible
`/v1/embeddings` endpoint. The shim returns Onyx's expected response shape:

```json
{"embeddings": [[0.1, 0.2]]}
```

The 501 stubs are deliberate. This shim is an embedding bridge, not a reranker
or query-analysis model server. If Onyx starts requiring reranking or
query-analysis for the workflows you use, route those calls to a real Onyx
model server or extend the shim with compatible implementations.

## Why The Shim Exists

The wrapper needs release-image-compatible local embeddings. Onyx v4.1.7 can
use self-hosted/custom embedding models, but the practical path is still shaped
by internal model-server assumptions:

- Indexing and search call Onyx's model-server endpoints, not a generic
  OpenAI-compatible embedding endpoint in every path.
- The `api_server` query path and `background` indexing path both need to use
  the same embedding model and vector dimensionality.
- Some high-quality asymmetric embedding models need different query and
  passage handling, but Onyx's generic provider configuration does not reliably
  provide the needed query prefix for all local/custom setups.
- Onyx has special hardcoded behavior for the `nomic-ai` local embedding model
  family, so the recommended UI setup uses `nomic-ai/nomic-embed-text-v23` as
  the model type while the shim maps requests to the actual upstream model.

The shim preserves Onyx's internal contract and moves the OpenAI-compatible
translation to a small local service that can be updated independently during
Onyx upgrades.

## Prefix Handling

The wrapper recommends setting:

```env
ONYX_RAG_EMBEDDING_QUERY_PREFIX="Instruct: Given a document query, retrieve the most relevant chunk.\nQuery: "
ONYX_RAG_EMBEDDING_PASSAGE_PREFIX=""
```

Compose passes these to the shim as `SHIM_QUERY_PREFIX` and
`SHIM_PASSAGE_PREFIX`. The shim also honors `manual_query_prefix` and
`manual_passage_prefix` if Onyx sends them. Manual prefixes from Onyx win; env
prefixes are the fallback.

The shim normalizes escaped `\n` sequences into real newlines because admin UIs
and env files often store prefixes that way. Logs include `prefix_source` and
`prefix_len` on every embedding request, which is the quickest way to verify
that query prefixing is actually active.

Prefixing matters because many modern embedding models are asymmetric: queries
and passages are not supposed to be embedded with identical raw text. If query
prefixing is missing, indexing may succeed but search quality can collapse in a
way that looks like a retrieval bug.

## Optional Local Embedding Server

Implementation:

- `make embedserv-install`
- `make embedserv-verify-model`
- `make embedserv-serve`
- `embedserv/requirements.in`
- `embedserv/requirements.txt` (hashed lock file)

On macOS, the Makefile can install and launch an MLX embedding server:

```sh
make embedserv-install
make embedserv-verify-model
make embedserv-serve
```

`make embedserv-install` installs from the hashed lock file with
`--require-hashes`. To upgrade package versions during a stack upgrade, edit
`embedserv/requirements.in` if needed and run `make upgrade-python-deps`.
Most embedserv requirements are intentionally unconstrained so that target can
float them forward. `typer==0.20.0` is a compatibility pin: newer Typer
releases currently trigger a `sys.exit()` handler traceback in the local
embedserv CLI path, so re-test that behavior before unpinning.

The default model is:

```env
ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL="majentik/harrier-oss-v1-0.6b-MLX-8bit"
ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL="http://host.docker.internal:3210/v1/embeddings"
```

`ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL` is optional. When unset, the shim
defaults it to `ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL` for the bundled MLX server
flow.

`make embedserv-serve` reads `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL`, requires an explicit port,
requires the path to end in `/v1/embeddings`, and binds to `0.0.0.0` when the
configured host is `host.docker.internal`. The Docker-side shim reaches that
host service through `host.docker.internal`.

If the host embedding service is on a LAN or host-local address, set:

```env
MYST_VPN_ALLOW_LAN_BYPASS=true
```

This lets local inference APIs bypass the Myst VPN firewall while preserving the
fail-closed VPN behavior for other traffic. Without it, the shim may be healthy
but embedding calls can fail with upstream connection errors.

The Makefile uses `mlx-embeddings` through `mlx-openai-server` because this
stack expects an OpenAI-compatible embedding endpoint with stable vector output.
Do not put LiteLLM between Onyx and embeddings for this path; it tends to hide
model-specific prefix and task behavior that RAG quality depends on.

## Onyx Admin Configuration

For the local/custom embedding path:

1. Open Onyx Admin -> Configuration -> Index Settings.
2. Select `Self-Hosted / Custom Model` for embeddings.
3. Enter `nomic-ai/nomic-embed-text-v23` as the model type.
4. Set the embedding dimension to `1024` for the recommended Harrier or
   Qwen3 0.6B models.
5. Keep the wrapper env query prefix configured unless your selected model
   explicitly does not require asymmetric query instructions.

The model name entered in Onyx is not necessarily the model served upstream.
When `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL` is set, the shim uses that value
for upstream requests regardless of the `model_name` sent by Onyx. When it is
unset, the shim uses `ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL`. This lets Onyx use
the UI/model-type behavior it needs while the shim targets the real local model.

Changing embedding model, dimensionality, or prefix policy after documents are
indexed can require rebuilding the index. Mixed embeddings from different
models or dimensions should be treated as invalid.

## Diagnostics

Useful container logs:

```sh
make logs-full
```

Look for these shim messages:

- `embed_request`: Onyx reached the shim. Check `text_type`, `requested_model`,
  `upstream_model`, `inputs`, `prefix_source`, and `prefix_len`.
- `embed_success`: upstream embeddings returned successfully. Check `dim` for
  the expected vector size.
- `embed_http_error`: the upstream embedding server returned an HTTP error.
- `embed_upstream_unreachable`: the shim could not connect to
  `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL`.
- `upstream_connection_retry`: a pooled keep-alive connection was stale and the
  shim retried once.
- `rerank_stub_called` or `query_analysis_stub_called`: Onyx used an endpoint
  this shim intentionally does not implement.

Common failure modes:

- The Web connector cannot crawl `http://localhost:8091/`: check that full mode
  is running, `doc-drop-web` is healthy, `host-doc-drop-web-proxy` published the
  port, and Onyx Security Hardening is not set to strict `Validate All`.
- Directory listings work but hidden files are missing: this is expected.
- Indexing starts but embedding fails with connection errors: confirm the host
  embedding server is running at `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL`,
  `MYST_VPN_ALLOW_LAN_BYPASS=true` is set when needed, and the URL uses the
  container-reachable host name.
- Search returns weak results after successful indexing: verify query prefix
  logs, embedding dimension, model identity, and whether old documents were
  indexed with a different model or prefix.
- Internal search returns relevant documents but fills the model context:
  lower `ONYX_RAG_INTERNAL_SEARCH_MAX_CONTEXT_SECTIONS`,
  `ONYX_RAG_INTERNAL_SEARCH_MAX_CONTENT_CHARS_PER_RESULT`, or
  `ONYX_RAG_INTERNAL_SEARCH_MAX_TOTAL_CONTENT_CHARS`, then recreate `api_server`.
- Onyx reports reranker or query-analysis failures: either disable those Onyx
  features for this local path or provide real implementations instead of the
  shim stubs.

## Major Upgrade Checklist

This is the short RAG-specific checklist. The authoritative line-oriented
inventory is
[`docs/onyx_patches_upgrade.md`](onyx_patches_upgrade.md), especially the
[background freshness](onyx_patches_upgrade.md#background-web-connector-pdf-freshness-patch),
[embedding shim](onyx_patches_upgrade.md#local-embedding-shim), and
[full-mode compose](onyx_patches_upgrade.md#full-mode-compose)
sections. Before upgrading Onyx across a major version, re-check these
assumptions:

- `background` and `api_server` still route both indexing and query-time
  embeddings through the model-server env vars pointed at the shim.
- Onyx's embedding request and response shape still matches the shim, including
  the fields used for query/passage prefixing.
- Reranking and query-analysis are still optional for this local path, or the
  shim has been extended to support them.
- The `internal_search` formatter still flows through the patched
  `SearchToolOverrideKwargs` defaults and
  `convert_inference_sections_to_llm_string()` helper, or the context-limit
  patch has been updated for the new search path.
- The Web connector scrape path and document model fields used by the PDF
  freshness patch still exist.
- The Security Hardening env-to-UI mapping has not changed.
- The recommended Admin model type and embedding dimension still match the
  current Onyx UI behavior and the selected local model.

If Onyx gains a first-class OpenAI-compatible embedding provider that handles
query/document prefixes correctly in both indexing and query-time search, prefer
that over the shim. Until then, the shim is the narrow compatibility layer that
keeps local document RAG predictable.
