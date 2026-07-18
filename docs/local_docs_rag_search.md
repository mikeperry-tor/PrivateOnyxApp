# Local Document RAG Search

This document explains the local-document RAG path provided by the full Onyx
wrapper. It is the design and troubleshooting companion for the shorter setup
steps in `README.md` and the line-oriented upgrade checklist in
[`docs/onyx_patches_upgrade.md`](onyx_patches_upgrade.md).

Use this document for operator-facing setup, request flow, and diagnostics. For
the patch rationale and possible upstream shape, see
[`docs/onyx_patch_info.md`](onyx_patch_info.md), especially
[Other retained patches](onyx_patch_info.md#other-retained-patches), and
[Compose wrapper changes](onyx_patch_info.md#compose-wrapper-changes).
For line-numbered Onyx upgrade checks, use
[`docs/onyx_patches_upgrade.md`](onyx_patches_upgrade.md), especially
[Other patch regression audit](onyx_patches_upgrade.md#other-patch-regression-audit), and
[Routing and Compose audit](onyx_patches_upgrade.md#routing-and-compose-audit).
For the tested interaction between Onyx SSRF defaults, local doc-drop
reachability, Obscura private-network blocking, and code-interpreter
networking, see
[`docs/internal_network_security.md`](internal_network_security.md).

The local path is intentionally built out of wrapper services instead of an
Onyx fork:

- `doc-drop-web` exposes a local document directory as a small read-only HTTP
  site for Onyx's Web connector.
- A hardened fixed display publisher exposes `doc-drop-web` on the host while
  the server remains internal-only. The connector crawls
  `http://doc-drop-web:8091/`; returned source
  links are rewritten to `http://localhost:8091/` by default.
- `onyx/patches/sitecustomize_background/sitecustomize.py` adds internal-origin
  PDF freshness, exact crawl routing, saved-level external routing, and
  display-only source-link rewriting.
- The API `sitecustomize` bootstrap invokes the shared optional internal-search
  content-cap patch so selected chunks do not overwhelm the answering model.
- `local-embedding-shim` implements the subset of Onyx's model-server HTTP API
  that indexing and search need for embeddings, then forwards the actual
  embedding work to an OpenAI-compatible `/v1/embeddings` endpoint.
- The optional `make embedserv-*` targets install and run a local MLX
  OpenAI-compatible embedding server on the host.

These pieces exist because Onyx v4.2.5 has strong assumptions about where local
documents and embedding models live. The wrapper keeps the user-facing Onyx
configuration simple while preserving those assumptions at the service
boundary.

## End-To-End Flow

1. Files are placed under `ONYX_RAG_DOC_SOURCE_DIR`, defaulting to `./doc-drop`.
2. `doc-drop-web` mounts that directory read-only at `/import/docs` and serves
   it on dedicated internal route and publisher networks.
3. The host display origin is
   `http://localhost:${HOST_PORT_ONYX_RAG_DOC_WEB:-8091}/` by default.
4. In Onyx Admin -> Connectors -> Web, create a Recursive Web connector pointed
   at `http://doc-drop-web:8091/`.
5. The Onyx `background` worker crawls the directory listing and downloads
   documents through the Web connector. The exact stack-owned origin uses the
   host final-hop proxy and a fixed gateway into the
   dedicated doc-drop route network. User-defined connector targets use the
   public or host final-hop proxy selected from saved Admin SSRF state.
6. During indexing, `background` calls `MODEL_SERVER_HOST:MODEL_SERVER_PORT`.
   In full mode the wrapper points that to `local-embedding-shim:9101` on the
   internal backend network.
7. The shim converts Onyx model-server `EmbedRequest` payloads into
   OpenAI-compatible embedding requests and sends them to `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL`.
8. During chat/search, `api_server` uses the same shim path to embed the query
   before Onyx runs hybrid search against the indexed documents.
9. If configured, the wrapper applies per-result and aggregate character caps
   after Onyx has selected and serialized internal-search results for the
   answering model.

Stored document IDs and freshness requests retain the internal crawl origin.
Only links returned for display are rewritten to the host origin. Existing Web
connectors saved with `http://localhost:8091/` must be recreated with
`http://doc-drop-web:8091/` and reindexed; Compose cannot rewrite saved records.

## Web Connector Server

Implementation:

- Compose services: `doc-drop-web`, `doc-drop-route-gateway`, and
  `host-doc-display-publisher` in `docker-compose.full.yml`
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

Onyx v4.2.5 has SSRF protection for URL-fetching paths. Compose seeds:

```env
OPEN_URL_VALIDATE_SSRF=true
MCP_SERVER_ALLOW_PRIVATE_NETWORK=true
MCP_SERVER_ALLOW_LOOPBACK=false
```

This maps to Allow Private Network when no Admin value is saved. Saved state
selects the public or host route for external Web Connector/MCP requests;
loopback remains blocked. The exact internal doc-drop origin is a separate
stack-owned host final-hop gateway and does not depend on broad loopback access.

Once an admin saves Security Hardening settings in Onyx, the saved UI value is
the effective runtime policy and these env vars only act as startup defaults.
If generic private crawling suddenly fails after UI changes, check Admin ->
Security Hardening first. The exact stack-owned doc-drop route remains
available at the strict "Validate All" posture.

There is an intentional tradeoff here. The document source is local and trusted
by the operator, so the wrapper optimizes for making Onyx's Web connector see
that source. Do not point the doc-drop connector at arbitrary untrusted local
services just because the SSRF setting allows the wrapper's document server.

These settings do not govern the local embedding shim's upstream call and are
not firewall rules for Obscura browser traffic. The restricted Obscura
networks and browser final-hop policy provide that path's private-target
backstop. Browser same-origin/CORS behavior remains defense in depth, not a
stack-internal access-control boundary.

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

Onyx v4.2.5 intentionally avoids trusting `Last-Modified` for Web PDFs because
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

## Internal Search Content Caps

Implementation:

- Compose env: `docker-compose.full.yml`
- Runtime patch: `onyx/patches/shared/wrapper_env_patches.py`, installed by the
  API bootstrap
- User-facing env:
  `ONYX_RAG_INTERNAL_SEARCH_MAX_CONTENT_CHARS_PER_RESULT`, and
  `ONYX_RAG_INTERNAL_SEARCH_MAX_TOTAL_CONTENT_CHARS`

Onyx's internal search path is chunk-oriented, not excerpt-oriented. In v4.2.5,
the `internal_search` tool and the `/search` API serialize the selected
section's full `content` into the model-facing tool result. Onyx may also merge
nearby matching chunks and expand a selected section with adjacent chunks before
formatting the result. That is useful for answer quality, but local document
sets can produce enough text to fill small or medium model context windows.

Full mode therefore exposes optional wrapper-level character caps:

```env
ONYX_RAG_INTERNAL_SEARCH_MAX_CONTENT_CHARS_PER_RESULT=0
ONYX_RAG_INTERNAL_SEARCH_MAX_TOTAL_CONTENT_CHARS=0
```

Empty or `0` means no wrapper cap. Positive values cap the serialized result
text after Onyx's own retrieval, LLM section selection, context expansion, and
section merging have already run. The first value limits each result's
`content`; the second limits total returned `content` across all results.

Set these values when internal search is consuming too much of the model
window. Leave them empty or `0` when relying on a large, correctly configured
chat context window. Changes require recreating `api_server` because the base
`sitecustomize` patch runs at Python startup.

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

The shim listens on `0.0.0.0:9101` on its own container and joins the internal
`onyx-backend` network. Full mode sets these env vars for `api_server` and
`background`:

```env
MODEL_SERVER_HOST=local-embedding-shim
MODEL_SERVER_PORT=9101
INDEXING_MODEL_SERVER_HOST=local-embedding-shim
INDEXING_MODEL_SERVER_PORT=9101
```

That makes Onyx believe it is talking to its own model server. The shim exposes
the endpoints Onyx expects for embedding-related paths:

- `GET /health`
- `GET /ready`
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

`GET /health` is process liveness. Compose uses `GET /ready`, which sends the
fixed text `readiness` to the configured default embedding model and requires
one non-empty vector. Full-mode `api_server` and `background` therefore do not
start while the host/LAN embedding endpoint, its route, or its model is
unusable. The response and logs expose no API key or upstream response body.
The default plain-HTTP `host.docker.internal` endpoint uses the host route's
fixed exact-host exception even when public cleartext URLs are disabled;
arbitrary public HTTP destinations remain blocked. RFC1918 HTTP destinations
are available to the configured embedding integration only when
`ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true`. Use an RFC1918 literal or a
`.local`, `.internal`, or `.home.arpa` name whose complete DNS answer set
validates as RFC1918. Empty or failed lookups for those operator-local suffixes
fail closed without external DNS/proxy fallback. This setting does not give
agent browsing or generated code access to the embedding endpoint or LAN.

## Why The Shim Exists

The wrapper needs release-image-compatible local embeddings. Onyx v4.2.5 can
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

The `v23` name is intentionally synthetic. It must remain saved in Onyx so
the `nomic-ai` feature gates stay enabled. The API and background startup
patches map only tokenizer construction for that exact name to the tokenizer
already bundled as `nomic-ai/nomic-embed-text-v1`; they do not rewrite the
saved model name or the model name sent to the embedding shim. This avoids an
attempt to download the nonexistent `v23` tokenizer and preserves the same
tokenization Onyx previously reached through its fallback.

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

Once the selected model is installed, `make up-full` automatically launches
`make embedserv-serve` in the background when the shim uses the bundled default
URL. It skips this startup when `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL` selects
Teep or another custom service. If the default URL is selected without an
installed or already-running server, startup fails immediately with setup
guidance. Automatic startup waits for the listener and shows recent log output
directly if the process exits or times out.
The background server writes to `embedserv/serve.log`; direct
`make embedserv-serve` remains the foreground form. `make down-full` validates
the recorded process identity before stopping an automatically launched
server. Missing, stale, or reused PIDs are reported and ignored; manually
launched servers are never stopped by this lifecycle hook.

`make embedserv-install` installs from the hashed lock file with
`--require-hashes`. To upgrade package versions during a stack upgrade, edit
`embedserv/requirements.in` if needed and run `make upgrade-python-deps`.
This host-side installation and model download occur before stack startup and
do not depend on Myst readiness. They honor the standard host
`HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` environment when a download proxy is
required; `EGRESS_UPSTREAM_PROXY_URL` is intentionally only a runtime
final-hop policy setting.
Most embedserv requirements are intentionally unconstrained so that target can
float them forward. Two inputs are compatibility pins. `transformers<5.13`
avoids a `mlx-lm` tokenizer-registration failure during handler startup:
`'str' object has no attribute '__module__'`. `typer==0.20.0` avoids a
`sys.exit()` handler traceback in the local embedserv CLI path. Re-test the
matching behavior before unpinning either dependency.

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

To use Teep instead of the bundled MLX server, configure:

```env
ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL="http://host.docker.internal:8337/v1/embeddings"
ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL="neardirect:Qwen/Qwen3-Embedding-0.6B"
```

Use the configured `HOST_PORT_TEEP` in the URL if it is not `8337`.
`ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL` does not select a Teep model; it only
controls the bundled MLX download/server and serves as the shim's fallback
model when `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL` is unset. The shim reaches
Teep through its fixed host publisher, not the internal `teep` service name.

Exact `host.docker.internal` uses the narrow host exception without another
setting. If the embedding service instead uses an RFC1918 literal or a
`.local`, `.internal`, or `.home.arpa` LAN name, set:

```env
ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true
```

The shim itself has no direct route. It uses HTTP absolute-form or HTTPS
CONNECT through `onyx-host-egress-bridge`, verifies TLS, reuses pooled
connections, and disables ambient proxy discovery. Without the required host
policy permission, `/ready` remains unhealthy and full-stack startup fails
closed instead of accepting later embedding errors.

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
Do not replace the synthetic `nomic-ai/nomic-embed-text-v23` Admin value with
the real upstream model unless the corresponding Onyx feature gates have been
re-audited.

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

- The Web connector cannot crawl `http://doc-drop-web:8091/`: check that full
  mode, `doc-drop-web`, `doc-drop-route-gateway`, and the host egress
  final-hop proxy are healthy and that the connector was recreated after the
  network-isolation migration. Test the display link separately at
  `http://localhost:8091/`.
- Directory listings work but hidden files are missing: this is expected.
- Indexing starts but embedding fails with connection errors: confirm the host
  embedding server is running at `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL`,
  `onyx-host-egress-bridge` and its final-hop proxy are healthy,
  `ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true` is set only when the configured
  endpoint is on an RFC1918 LAN, and the URL uses a container-reachable host
  name.
- Search returns weak results after successful indexing: verify query prefix
  logs, embedding dimension, model identity, and whether old documents were
  indexed with a different model or prefix.
- Internal search returns relevant documents but fills the model context:
  set `ONYX_RAG_INTERNAL_SEARCH_MAX_CONTENT_CHARS_PER_RESULT` or
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
- The Security Hardening env-to-UI mapping matches the posture documented in
  [Internal network security](internal_network_security.md).
- The recommended Admin model type and embedding dimension still match the
  current Onyx UI behavior and the selected local model.
- The exact synthetic `nomic-ai/nomic-embed-text-v23` name still activates the
  intended Onyx RAG behavior, and `HuggingFaceTokenizer.__init__` still has the
  source shape validated by the exact v23-to-v1 tokenizer-only alias.

If Onyx gains a first-class OpenAI-compatible embedding provider that handles
query/document prefixes correctly in both indexing and query-time search, prefer
that over the shim. Until then, the shim is the narrow compatibility layer that
keeps local document RAG predictable.
