# Local Document RAG Search

This document explains the local-document RAG path provided by the full Onyx
wrapper. It is the design and troubleshooting companion for the shorter setup
steps in `README.md` and the line-oriented upgrade checklist in
[`docs/onyx_patches_upgrade.md`](onyx_patches_upgrade.md).

Use this document for operator-facing setup, request flow, and diagnostics. For
the patch rationale and possible upstream shape, see
[`docs/onyx_patch_info.md`](onyx_patch_info.md), especially
[Other retained wrapper behavior](onyx_patch_info.md#other-retained-wrapper-behavior), and
[Compose wrapper changes](onyx_patch_info.md#compose-wrapper-changes).
For line-numbered Onyx upgrade checks, use
[`docs/onyx_patches_upgrade.md`](onyx_patches_upgrade.md), especially
[Runtime patch contract audit](onyx_patches_upgrade.md#runtime-patch-contract-audit), and
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
  content-cap patch so configured limits bound selected model-facing chunks.
- `local-embedding-shim` implements the subset of Onyx's model-server HTTP API
  that indexing and search need for embeddings, then forwards the actual
  embedding work to an OpenAI-compatible `/v1/embeddings` endpoint.
- The optional `make embedserv-*` targets install and run a local MLX
  OpenAI-compatible embedding server on the host.

These pieces adapt Onyx's fixed assumptions about where local documents and
embedding models live. The wrapper keeps the user-facing Onyx
configuration simple while preserving those assumptions at the service
boundary.

## End-To-End Flow

1. Files are placed under `ONYX_RAG_DOC_SOURCE_DIR`, defaulting to `./doc-drop`.
   Full-mode startup creates that default directory when it is absent. A
   configured custom path must already exist so a typo cannot silently create
   and serve an unintended empty directory.
   Docker and native Linux Podman bind-mount this host directory directly.
   Podman on macOS instead starts the same read-only Python server on the host
   without making a VM copy. The process is PID tracked and identity validated
   by the Makefile. This applies equally to the default directory and another
   configured local directory; it also permits external mounts such as WebDAV
   when virtiofs cannot re-export them.
   The Podman host server resolves the configured root and rejects symbolic
   links rather than following them; configure the real collection directory
   and use ordinary files/directories below it.
2. In Docker and native Linux Podman modes, `doc-drop-web` serves its read-only
   bind directly. In macOS Podman mode, a capability-free fixed `socat` relay
   forwards only the internal origin to the host-local document server. It has
   no source bind or persistent document volume, so source changes are visible
   immediately.
3. `doc-drop-web` serves the files on dedicated internal route and publisher
   networks. The host display origin is
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
9. When a positive cap is configured, the wrapper limits per-result or combined
   internal-search content after Onyx selects and serializes the results.

Stored document IDs and freshness requests retain the internal crawl origin.
Only links returned for display are rewritten to the host origin. Existing Web
connectors saved with `http://localhost:8091/` must be recreated with
`http://doc-drop-web:8091/` and reindexed; Compose cannot rewrite saved records.

## Web Connector Server

Implementation:

- Compose services: `doc-drop-web`, `doc-drop-route-gateway`, and
  `host-doc-display-publisher` in
  `compose_overlays/docker-compose.full.yml`
- Server script: `onyx/doc_drop_webserver.py`
- User-facing env: `ONYX_RAG_DOC_SOURCE_DIR` and `HOST_PORT_ONYX_RAG_DOC_WEB`

Upgrade-sensitive compose details for these services live in
[Routing and Compose audit](onyx_patches_upgrade.md#routing-and-compose-audit). The
broader reason the wrapper carries these sidecars is described in
[Compose wrapper changes](onyx_patch_info.md#compose-wrapper-changes).

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

Onyx has SSRF protection for URL-fetching paths. Compose seeds:

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
  `ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_HOSTS=doc-drop-web`

This section keeps the operational behavior in one place. The detailed patch
rationale is in
[Background Web connector PDF freshness](onyx_patch_info.md#background-web-connector-pdf-freshness);
the upstream symbols and line references to re-check during an Onyx upgrade are
in
[Runtime patch contract audit](onyx_patches_upgrade.md#runtime-patch-contract-audit).

Onyx intentionally avoids trusting `Last-Modified` for Web PDFs because
public websites often emit unreliable validators. That is sensible for the
general internet, but wasteful for a local document-drop server where the file
metadata is trusted and stable.

The background patch changes that only for the allowlisted internal
`doc-drop-web` origin. It uses HTTP `HEAD` metadata such as `Last-Modified` and
`Content-Length` to skip full download and PDF parsing when the source has not
changed. It stores wrapper metadata on the Onyx document record so later syncs
can make the same decision.

This is a pre-download and pre-parse optimization. Onyx independently computes
a content hash after parsing and skips chunking, embedding, and vector writes
when indexed content is unchanged. Removing this wrapper patch would therefore
restore repeated PDF download and parsing, not repeated embedding of every
unchanged document.

The empty document used to carry an unchanged decision is never allowed into
indexing unless its validators still match the database record. A concurrent
database change or malformed sentinel fails that indexing attempt so a later
crawl performs a full scrape; it cannot replace indexed content with an empty
placeholder.

The trusted validator is only as strong as the metadata supplied by
`doc-drop-web`. A same-size replacement with an unchanged second-resolution
modification time cannot be distinguished by this fast path. Files should be
replaced with an updated modification time; a crawl with changed validators
falls through to normal parsing and native content-hash handling.

This patch is tied to Onyx internals. During a major Onyx upgrade, verify that
the Web connector still has the same scrape path, that connector `Document`
objects still expose `doc_updated_at`, `doc_metadata`, and `content_hash()`, and
that the database `Document` model still stores compatible fields. If Onyx adds
native incremental freshness for Web PDFs, prefer that and remove or narrow this
patch.

## Internal Search Content Caps

Implementation:

- Compose env: `compose_overlays/docker-compose.full.yml`
- Runtime patch: `onyx/patches/shared/wrapper_env_patches.py`, installed by the
  API bootstrap
- User-facing env:
  `ONYX_RAG_INTERNAL_SEARCH_MAX_CONTENT_CHARS_PER_RESULT` and
  `ONYX_RAG_INTERNAL_SEARCH_MAX_TOTAL_CONTENT_CHARS`

Onyx's internal search path is chunk-oriented, not excerpt-oriented. The
`internal_search` tool and the `/search` API serialize each selected section's
full `content` into the model-facing result. Nearby matching chunks may also be
merged or expanded before formatting, which can consume a substantial model
context window.

Full mode therefore supports optional wrapper-level character caps:

```env
ONYX_RAG_INTERNAL_SEARCH_MAX_CONTENT_CHARS_PER_RESULT=0
ONYX_RAG_INTERNAL_SEARCH_MAX_TOTAL_CONTENT_CHARS=0
```

Empty or `0` disables the corresponding cap; in that state the patch is
installed but makes no change. A positive first value limits each result's
`content`, while a positive second value limits combined `content` across all
results. Both apply after Onyx retrieval, section selection, context expansion,
and merging, without limiting ingestion or retrieval itself.

Set a positive value when internal search is consuming too much of the answer
model's context window. Recreate `api_server` after changing either value
because the patch reads them at Python startup.

## Embedding Shim

Implementation:

- Compose service: `local-embedding-shim` in
  `compose_overlays/docker-compose.full.yml`
- Shim script: `onyx/local_embedding_shim.py`
- Onyx services routed to it: `api_server` and `background`

This section focuses on how to run and diagnose the shim. The rationale and
upstreamable design are described below, with a summary in
[Other retained wrapper behavior](onyx_patch_info.md#other-retained-wrapper-behavior). The
model-server contract and upstream references to verify during upgrades are in
[Runtime patch contract audit](onyx_patches_upgrade.md#runtime-patch-contract-audit).

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

The shim also honors Onyx's `normalize_embeddings` request field. When enabled,
each validated upstream vector is L2-normalized before it is returned; a zero
or otherwise non-normalizable vector is rejected. This preserves the local
model-server contract independently of whether a selected OpenAI-compatible
upstream normalizes its own output.

The OpenAI-compatible response must contain exactly one indexed vector for
every input. The shim restores input order from the response indices and
rejects missing or duplicate indices, empty vectors, non-numeric or non-finite
values, and inconsistent dimensions within a response. It deliberately does
not require a particular dimension; Onyx's saved dimension remains an
operator-selected property of the configured embedding model.

Inbound request bodies, upstream response bodies, active handler threads,
upstream-pool waits, and idle request sockets are bounded. Upstream HTTP error
bodies and transport details are not returned to Onyx or written to shim logs.
The shim forwards an accepted embedding request exactly once and does not retry
a POST.

The 501 stubs are deliberate. This shim is an embedding bridge, not a reranker
or query-analysis model server. If Onyx starts requiring reranking or
query-analysis for the workflows you use, route those calls to a real Onyx
model server or extend the shim with compatible implementations.

`GET /health` is process liveness and is the shim's low-frequency Compose
healthcheck. `GET /ready` sends the fixed text `readiness` to the configured
default embedding model and requires one non-empty vector. `make up-full`
starts only the shim and its routing dependencies, calls `/ready` exactly once,
and proceeds to create the API/background tier only after that succeeds. A
failure is returned without retry and leaves the diagnostic subset running;
already-running API/background services are not recreated by a later failed
validation. The response and logs expose no API key or upstream response body.
Full mode gives the host policy only the exact authority parsed from
`ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL`, even when the operator host-port list is
`none` and broad LAN access is disabled. This covers exact
`host.docker.internal`, RFC1918 literals, and `.local`, `.internal`, or
`.home.arpa` names whose complete system-DNS answer set validates as RFC1918.
Empty, failed, non-RFC1918, or mixed local answers fail closed without
external DNS/proxy fallback. The configured local embedding authority connects
directly rather than traversing `EGRESS_UPSTREAM_PROXY_URL`; other local
authorities remain denied unless separately permitted. Arbitrary public HTTP
destinations remain blocked. This endpoint-specific permission does not give
agent browsing or generated code access to the embedding endpoint or LAN.

## Why The Shim Exists

The wrapper needs release-image-compatible local embeddings. Onyx can
use self-hosted/custom embedding models, but the practical path is still shaped
by internal model-server assumptions:

- Indexing and search call Onyx's model-server endpoints, not a generic
  OpenAI-compatible embedding endpoint in every path.
- The `api_server` query path and `background` indexing path both need to use
  the same embedding model and vector dimensionality.
- Some high-quality asymmetric embedding models need different query and
  passage handling, but Onyx's generic provider configuration does not reliably
  provide the needed query prefix for all local/custom setups.
- The saved model name gives Onyx an offline tokenizer identity while the shim
  maps requests to the actual upstream model.

The `v23` name is intentionally synthetic. Before full-mode Compose starts, the
Makefile extracts the `nomic-ai/nomic-embed-text-v1` tokenizer already bundled
in the pinned Onyx image, with container networking disabled, and atomically
installs it in the shared model-cache bind. API and background startup require
that generated file and map tokenizer construction for the exact bundled v1
name and synthetic v23 name to `Tokenizer.from_file`; they do not call Hugging
Face, rewrite the saved model name, or change the model name sent to the
embedding shim. Other tokenizer model names retain stock Onyx behavior. In
Onyx, the `nomic-ai` name
prefix additionally permits large chunks only when multipass indexing is
enabled; it has no name-based retrieval effect while multipass is disabled.

This bootstrap is part of `make up-full`, not runtime package installation or
a model download. It fails before the application graph starts if the selected
pinned image no longer contains a valid tokenizer, making an image/cache
contract change visible during upgrades instead of turning a later file upload
or token-budget request into an outbound dependency.

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
- `embedserv/requirements.in`
- `embedserv/requirements.txt` (hashed lock file)

Model verification uses the pinned Hugging Face verifier's exit status rather
than matching its human-readable output. Checksum mismatches, missing remote
files, and failures to obtain the remote manifest remain fatal; warnings for
local cache/metadata files that are not part of the remote repository do not
invalidate an otherwise successful verification. `make embedserv-install`
always performs this verification after downloading the model. The separate
`make embedserv-verify-model` target rechecks an existing installation and
directs the operator to run the install target when its model or verifier is
absent.

On macOS, install the MLX embedding server, then let full startup own it:

```sh
make embedserv-install
make up-full
```

Use `make embedserv-verify-model` whenever an independent integrity recheck is
needed after installation.

Once the selected model is installed, `make up-full` automatically launches a
small host lifecycle proxy when the shim uses the bundled default URL. It skips
this startup when `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL` selects Teep or another
custom service. A clean custom-upstream start does not execute the host process
manager. During a bundled-to-custom transition, startup first applies policy
with only the new configured embedding authority and proves the host bridge,
then validates the custom embedding endpoint, and only then uses the ownership
record to stop a live old wrapper-owned proxy. Either failure retains it for
diagnosis. Lite mode never selects either host service. The proxy accepts only
bounded `POST /v1/embeddings` requests,
starts the pinned `mlx-openai-server` child on the first request, and unloads
the child ten minutes after the last active request completes. Concurrent cold
requests share one startup, and a request is forwarded exactly once: a child
crash is returned as an error instead of replaying an embedding batch. Cold
startup has no wrapper deadline while the owned child remains alive. After the
child advertises the exact expected model, the one proxy-to-MLX request uses a
five-minute blocked-socket timeout. This is not a five-minute end-to-end
deadline: request parsing, lifecycle admission, and cold loading occur outside
it, and a response that continues making socket progress may take longer. The
shim independently limits an upstream socket operation to 540 seconds and
performs no POST retry.

Docker Desktop's host gateway requires the lifecycle proxy to listen on the
host wildcard address, but gateway connections arrive at the macOS listener as
loopback peers. The proxy rejects every non-loopback socket peer before request
parsing or thread creation, caps active connection threads, and gives accepted
sockets a 30-second idle timeout.
This is a narrow trust boundary in Docker Desktop's userspace gateway, analogous
to the macOS Podman host document relay; it is not application-layer client
authentication. Rejected direct LAN sockets are closed without parsing enough
HTTP to return an application response.

The macOS Podman overlay maps the exact `host.docker.internal` alias on the
host-capable policy path to libkrun/gvproxy's fixed `192.168.127.254` macOS
host-loopback endpoint. The embedding request then traverses the same
exact-authority host policy; the mapping is not present on the shim or other
application containers and does not grant them unrestricted host access.

Idle unload starts only after ten minutes since the last request completed and
never stops an active batch. A request that races an idle child stop simply
waits for the owned process to exit and then launches one new child; it is not
dropped or replayed. During proxy termination, the listener stops accepting
new connections, every already accepted request with an active child drains
subject to the five-minute blocked-socket timeout, and only then is the
lifecycle closed and its child stopped. A cold startup can be cancelled by
proxy shutdown. Connections initiated after listener shutdown are necessarily
refused by the operating system.

Before binding the top-level listener and before every child launch, the proxy
requires loopback child port 3211 to be unoccupied. The live proxy's in-memory
`Popen` object is the only child ownership authority, and `/v1/models` must
advertise the configured served model. A forced proxy crash can leave an
orphaned child on 3211; the next bundled start fails before port 3210 binds and
requires manual host diagnosis. It never inspects or signals that listener.

If the default URL is selected without an installed server/model, startup fails
immediately with setup guidance. Automatic startup waits only for the lifecycle
proxy listener; the staged `/ready` request proves the actual model separately.
That single readiness request is deliberately visible and has no short wrapper
timeout. `make up-full` prints that it is waiting; when bundled MLX is selected,
it also identifies the cold-start behavior and lifecycle log path. Custom
upstreams receive only the transport-neutral readiness message. Ctrl-C remains
the operator escape when an upstream never becomes ready. A definite child
exit, occupied child port, invalid configuration, or failed readiness inference
still fails startup rather than being hidden.

The staged full-mode start brings up Teep before this request, including its
fixed host publisher when Teep is routed through Myst. This makes the
documented `host.docker.internal:8337` Teep embedding endpoint available to the
shim during readiness rather than only after the rest of the application graph
starts. Teep is part of the final stack in every embedding mode; this changes
only its startup order.

The proxy writes to `embedserv/serve.log`. Automatic launch uses the absolute proxy-script path and
an explicit detached process session so it survives the initiating shell. Its
record contains a random per-launch ownership token and a fingerprint of every
launch-defining argument plus the proxy script contents. Repeated startup
reuses it only when the live command contains that token and the fingerprint
still matches; a configuration or implementation change restarts only that
identity-validated proxy. `make down-full` likewise requires
the token in the live command before signaling it. Graceful shutdown
signals its in-memory owned child group. A malformed or identity-mismatched
top-level record is removed without signaling; if its proxy remains on 3210,
the next start reports an untracked top-level listener. This is distinct from
the orphaned-child 3211 diagnostic. Both normal paths wait a bounded grace
period and signal only the identity-validated live proxy; that proxy owns its
child in memory. Missing records are harmless; malformed or reused PIDs and unowned
listeners fail closed and are never signaled. Manually launched and custom
servers remain untouched.

The wrapper's shared stdlib-only `embedserv/host_process_manager.py` lives with
the bundled embedding service but also owns the equivalent Podman full-mode
document-server lifecycle. Docker keeps the document server in a container, so
it does not select that host target. The manager
atomically writes the common PID/token/configuration record, validates command
identity before signaling, and performs the listener/readiness wait. The
service implementations retain their narrower peer, child, port, and model
validation; those are not duplicated by the host manager.

`make embedserv-install` installs from the hashed lock file with
`--require-hashes`, downloads the selected model, and verifies its integrity
before reporting it ready. It creates the host environment with Python 3.12,
the supported runtime for the pinned server set. To upgrade package versions
during a stack upgrade, edit the exact direct pins in
`embedserv/requirements.in` and run `make upgrade-python-deps`.
This host-side installation and model download occur before stack startup and
do not depend on Myst readiness. They honor the standard host
`HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` environment when a download proxy is
required; `EGRESS_UPSTREAM_PROXY_URL` is intentionally only a runtime
final-hop policy setting.
The direct MLX requirements are one exact audited compatibility set:
`mlx-openai-server==1.8.1`, `mlx-embeddings==0.0.5`, `mlx-lm==0.31.3`,
`huggingface_hub==1.16.1`, and `transformers==5.14.1`. Server 1.8.1 requires
`mlx-embeddings<0.1` and Click below 8.4, which excludes the currently
incompatible newer embedding and Hugging Face Hub lines. Transformers 5.14.1
fixes the string-first tokenizer-registration regression seen with 5.13 and
`mlx-lm` 0.31.3. `typer==0.25.0` is also direct: the Hugging Face CLI still uses
Typer, and 0.26 through 0.27 emit an exit-handler traceback even for successful
help paths. Version 0.25 is the newest audited clean release, so the former
0.20 pin can move forward but cannot yet be removed. Follow the joint
candidate-environment and live-model policy in
[`docs/onyx_patches_upgrade.md`](onyx_patches_upgrade.md#python-dependency-and-host-install-policy)
before changing this set.

An existing bundled installation carries a fingerprint of the lock, Python
runtime, and synchronization implementation. Before `make up-full` starts the
MLX lifecycle proxy, it leaves a matching environment untouched or atomically
replaces a stale one and runs the locked dependency/Python checks. A failed
refresh restores the previous environment and fails startup. This pre-start
host preparation is skipped for custom embedding endpoints and machines where
the bundled environment/model has never been installed; initial setup remains
the explicit `make embedserv-install` operation.

The default model is:

```env
ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL="majentik/harrier-oss-v1-0.6b-MLX-8bit"
ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL="http://host.docker.internal:3210/v1/embeddings"
```

`ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL` is optional. When unset, the shim
defaults it to `ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL` for the bundled MLX server
flow.

`make up-full` owns the bundled default URL and binds its lifecycle proxy to
host port 3210; its MLX child binds only to `127.0.0.1:3211`. The Docker-side
shim reaches it through the exact configured embedding authority installed in
the full-mode host policy. That authority is part of the applied policy until
recreation/removal, not a liveness monitor.

To use Teep instead of the bundled MLX server, configure:

```env
ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL="http://host.docker.internal:8337/v1/embeddings"
ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL="neardirect:Qwen/Qwen3-Embedding-0.6B"
```

Use the configured `HOST_PORT_TEEP` in the URL if it is not `8337`.
`ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL` does not select a Teep model; it only
controls the bundled MLX download/server and serves as the shim's fallback
model when `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL` is unset.
The startup preflight rejects that fallback when the URL is the wrapper's Teep
endpoint and reports the required explicit upstream-model setting before
containers are created.
Ordinary Docker and Podman reach Teep through its fixed host publisher, not the
internal `teep` service name. Rootless Docker applies a narrow exception for
this exact stack-owned Teep URL: the shim joins `onyx-teep`, rewrites only its
runtime upstream to `http://teep:8337/v1/embeddings`, and drops its host-egress
network and proxy. This avoids RootlessKit's disabled host-loopback path while
preserving the operator-facing configured authority and Teep routing choice.

Teep port 8337 (or the actual `HOST_PORT_TEEP`) is permitted automatically only
because it is the exact configured embedding authority. An RFC1918 literal or
a `.local`, `.internal`, or `.home.arpa` LAN name receives the same
endpoint-specific treatment without enabling broad LAN access; names must
resolve entirely to RFC1918 addresses.

Except for that exact internal rootless-Docker Teep path, the shim itself has
no direct route. It uses HTTP absolute-form or HTTPS
CONNECT through `onyx-host-egress-bridge`, verifies TLS, reuses pooled
connections, and disables ambient proxy discovery. Without the required host
policy permission, the one-shot `/ready` validation fails and full-stack
startup stops before creating a new API/background tier.

## Full-Mode Idle Storage Policy

Full mode trades some background responsiveness for lower idle CPU and memory:

- OpenSearch uses a fixed 512 MiB initial/maximum JVM heap, sizes its
  processor-derived pools from `node.processors=4`, and disables the
  performance-analyzer agent. The processor setting does not impose a CPU
  quota or change the JVM's GC-worker view by itself.
- OpenSearch stores retained failure/TLS audit events in monthly rather than
  daily indices and omits request bodies. It keeps audit categories,
  Security/TLS, and existing historical audit indices unchanged.
- The unused Query Insights latency/CPU/memory top-N collectors are disabled,
  so they do not retain query source or create new `top_queries-*` indices.
- Onyx indices use zero replicas because this wrapper intentionally deploys
  one OpenSearch data node; an unassigned replica was not an additional data
  copy. This is an index-creation default; there is no existing-index migration.
- An existing index that rejects a mapping refresh because OpenSearch applied a
  write block remains readable. Onyx logs the blocked refresh and allows the API
  to serve search while retrying verification on later index construction;
  indexing remains unavailable until the operator clears the block. A missing
  index or blocked index creation is still a startup error rather than a
  degraded-read exception.
- MinIO uses `MINIO_SCANNER_SPEED=slowest`; object healing, lifecycle cleanup,
  and scanner-driven maintenance can therefore take longer. Its retained
  healthcheck uses the common slow steady cadence.
- Redis and OpenSearch origin checks are disabled because dependent local
  consumers surface failures and their duplicate polling added no route
  assurance.
- Background discovery schedules are materialized every five minutes, queue
  monitoring producers/workers are removed, worker heartbeats/gossip are
  disabled, and worker event metrics are off. New or changed connector work can
  wait up to roughly five minutes before discovery.
- Full-mode Slack and Discord bot processes are disabled by default. Enable
  only the needed process with `ONYX_AGENT_SLACK_BOT` or
  `ONYX_AGENT_DISCORD_BOT`; these settings have no effect in lite mode.

All OpenSearch resource and low-idle values are startup configuration. The
tracked `onyx/opensearch/audit.yml` is the clean-volume Security audit seed;
the remaining values are Compose environment settings on the OpenSearch or
Onyx application services. There is no administrative sidecar, runtime API
mutation, or existing-volume migration. Persistent cluster settings take
precedence over startup values, and an already initialized Security index
retains its stored audit configuration; this private stack intentionally
defines only the clean/current-volume contract.

The retained worker pool is explicitly bounded: primary 2, light 4, heavy 2,
doc-processing 2, user-file-processing 1, and document-fetching 1. Beat writes
its upstream liveness marker when its five-minute scheduler reload tick runs;
schedule-update errors remain logged application failures rather than being
reclassified as process hangs. A local watchdog checks that marker without
Redis and restarts only `celery_beat` after two
missing observations or a stale interval. Startup validates the pinned
supervisor and schedule shapes; drift is fatal rather than silently restoring
the higher-frequency upstream configuration.

The wrapper entrypoint and file-only watchdog run with Python site imports
disabled, and the exact supervisor control process skips the background
application bootstrap. Beat, Celery workers, and their spawned indexing
children still install the strict background patches; the optimization does
not change connector discovery, ingestion, or PDF extraction behavior.

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
5. Enable **Normalize Embeddings** for the recommended models.
6. Keep the wrapper env query prefix configured unless your selected model
   explicitly does not require asymmetric query instructions.

The model name entered in Onyx is not necessarily the model served upstream.
When `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL` is set, the shim uses that value
for upstream requests regardless of the `model_name` sent by Onyx. When it is
unset, the shim uses `ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL`. This lets Onyx use
the UI/model-type behavior it needs while the shim targets the real local model.
Do not replace the synthetic `nomic-ai/nomic-embed-text-v23` Admin value with
the real upstream model unless its offline tokenizer availability and the
resulting index migration have been validated.

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
- `embed_invalid_upstream_response`: the service returned malformed,
  misordered, non-finite, empty, or inconsistent embedding data.
- `embed_upstream_unreachable`: the shim could not connect to
  `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL`.
- `rerank_stub_called` or `query_analysis_stub_called`: Onyx used an endpoint
  this shim intentionally does not implement.

Onyx applies a 30-second model-server connect timeout and a 600-second read
timeout to each request. The shim permits at most 30 seconds waiting for one of
its upstream slots and bounds a silent upstream socket wait at 540 seconds, so
it releases capacity before the caller's corresponding timeout. Passage
embedding retries qualifying request/HTTP failures up to three times with five
seconds between attempts; query embedding does not retry. The bundled lifecycle
proxy retains its separate five-minute blocked-socket limit once a request
reaches the MLX child.

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
  the configured local name resolves entirely to RFC1918 addresses, and the URL
  uses a container-reachable host name.
- Search returns weak results after successful indexing: verify query prefix
  logs, embedding dimension, model identity, and whether old documents were
  indexed with a different model or prefix.
- Internal search returns relevant documents but fills the model context:
  configure a positive `ONYX_RAG_INTERNAL_SEARCH_MAX_CONTENT_CHARS_PER_RESULT`
  or `ONYX_RAG_INTERNAL_SEARCH_MAX_TOTAL_CONTENT_CHARS`, then recreate
  `api_server`.
- Onyx reports reranker or query-analysis failures: either disable those Onyx
  features for this local path or provide real implementations instead of the
  shim stubs.

## Major Upgrade Checklist

This is the short RAG-specific checklist. The authoritative line-oriented
inventory is
[`docs/onyx_patches_upgrade.md`](onyx_patches_upgrade.md), especially
[Runtime patch contract audit](onyx_patches_upgrade.md#runtime-patch-contract-audit)
for background freshness and the embedding shim, and
[Routing and Compose audit](onyx_patches_upgrade.md#routing-and-compose-audit)
for full-mode Compose. Before upgrading Onyx across a major version, re-check
these assumptions:

- `background` and `api_server` still route both indexing and query-time
  embeddings through the model-server env vars pointed at the shim.
- Onyx's embedding request and response shape still matches the shim, including
  the fields used for query/passage prefixing.
- The internal-search formatter signature and result/content JSON construction
  still match the strict optional content-cap patch.
- OpenAI-compatible responses still provide one indexed vector per input; test
  ordering, count, numeric finiteness, consistent arbitrary dimensions, body
  limits, timeouts, and scrubbed errors.
- Reranking and query-analysis are still optional for this local path, or the
  shim has been extended to support them.
- The Web connector scrape path and document model fields used by the PDF
  freshness patch still exist.
- The Security Hardening env-to-UI mapping matches the posture documented in
  [Internal network security](internal_network_security.md).
- The recommended Admin model type and embedding dimension still match the
  current Onyx UI behavior and the selected local model.
- `HuggingFaceTokenizer.__init__` still has the source shape validated by the
  exact v1/v23-to-offline-v1 tokenizer-only mapping; the pinned image bootstrap
  succeeds with networking disabled; API and background load the generated
  file without a Hugging Face request; every other model retains stock loading;
  and any intended large-chunk behavior is tested with multipass indexing
  enabled.

If Onyx gains a first-class OpenAI-compatible embedding provider that handles
query/document prefixes correctly in both indexing and query-time search, prefer
that over the shim. Until then, the shim is the narrow compatibility layer that
keeps local document RAG predictable.
