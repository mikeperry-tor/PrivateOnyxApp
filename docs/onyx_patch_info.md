# Wrapper patch information

The wrapper uses runtime `sitecustomize` patches because several required
behaviors are not configurable in the pinned Onyx release. Patches are narrow,
and strict: required callable targets validate signatures plus source markers,
or exact structural text where a string/prompt is the target. A required patch
that no longer matches must stop startup rather than silently restore an unsafe
upstream path. Focused deterministic tests cover the wrapper-owned behavior;
the upgrade checklist also requires installation against the pinned images.

## Bootstrap ownership

`sitecustomize_api_server` is the only API bootstrap in both lite and full
modes, and `sitecustomize_background` owns full-mode background patches. The
code-interpreter service uses native configuration without a runtime patch. The
neutral `onyx/patches/shared/wrapper_env_patches.py` contains reusable helpers
but is not executable bootstrap code. Compose imports it explicitly through
`PYTHONPATH`.

The pinned background image's supervisor configuration resets worker
`PYTHONPATH` to `/app`, overriding the container-level Compose value. Full mode
therefore also mounts the background bootstrap as `/app/sitecustomize.py` and
the shared helper as `/app/wrapper_env_patches.py`. This makes the same strict
bootstrap visible to the real Celery workers and their spawn-based indexing
children; relying only on the wrapper directories in the container environment
would patch the supervisor process but leave document fetching unpatched.

The wrapper entrypoint and local Beat watchdog start with `python -S`, and the
background bootstrap also recognizes the exact `/usr/bin/supervisord` argv.
Those three control processes never execute Onyx application work, so they do
not install the heavy strict patch set. This exclusion is deliberately exact:
Beat, all Celery workers, and spawn-based indexing children still import and
validate the background bootstrap.

This split prevents a base bootstrap from running service-inappropriate code
and makes missing API-only behavior startup-visible.

Both Onyx bootstraps redirect their installation diagnostics to stderr. This is
part of the isolated-process protocol: Onyx reserves child stdout for the
pickled return value, so even a successful startup message on stdout corrupts
PDF extraction and other isolated results.

## Selectable built-in crawler

`ONYX_AGENT_USE_OBSCURA_BROWSER` accepts exactly `true` or `false` and defaults
to the stock Onyx path. The API bootstrap installs exactly one of the two
strict integrations below; an invalid setting or source-shape mismatch stops
startup. This switch does not affect the SearXNG direct-Obscura engines.

The stock path remains the default reliability-oriented choice. Re-run
comparable stock/direct batches on every Obscura upgrade and change the default
only when the direct browser path's stronger containment and one-navigation
behavior also meets the required reliability.

### Direct Obscura mode

`sitecustomize_api_server/obscura_crawler_patch.py` strictly replaces the
built-in `OnyxWebCrawler` URL-fetch path. It imports the single client in
`browser/obscura_client` and provides:

- one raw Obscura `Page.navigate` per accepted URL, with no requests,
  Playwright-local-browser, Firecrawl, CLI, generic-parser, or second-fetch
  fallback;
- one absolute 120-second monotonic invocation deadline/finalization state,
  passed through the outer and nested crawler executors;
- ten process-global blocking permits acquired only for remaining budget,
  finalization checks before setup and immediately before navigation, and
  permit retention through cleanup for already-sent requests;
- stable requested-URL ordering and failure/snippet correlation, with terminal
  URLs retained for successful content and citations after redirects;
- same-navigation PDF, HTML, and exact raw-text dispatch using pinned Onyx
  extractors;
- one positive finite configured byte limit applied independently to the
  main-response body, including HTML, and serialized rendered DOM; existing
  post-parse character budgets; and normal unsuccessful
  `WebContent` results for per-URL failures;
- strict source and result-cardinality validation.

The patch is scoped to the built-in crawler. Deliberately selected external
Onyx providers keep their upstream implementation and public egress route;
the wrapper neither migrates saved provider rows nor claims one-navigation or
provider data-policy guarantees for them.

### Default stock Onyx mode

When the preference is `false`,
`sitecustomize_api_server/onyx_crawler_egress_patch.py` retains the pinned
upstream crawler's requests fetch and Playwright Chromium fallback. It replaces
only the crawler's imported HTTP helper and scopes a structural validator to
its browser fallback. Both stages are public-only, perform no API-side target
DNS, and use the exact `onyx-public-egress-bridge`; the Admin SSRF private
allowance and requests environment/`NO_PROXY` behavior cannot widen this
LLM-controlled route. The shared Playwright launcher sets `<-loopback>` as its
proxy bypass value to disable Chromium's implicit direct loopback exception.
Redirects and subresources remain final-hop policy decisions.

The Tor egress Compose layer supplies `api_server` a strict internal capability
that lets both crawler transports accept `http://` when the normalized host
ends in `.onion`. The stock requests redirect validator and Playwright
validator use the same rule; the shared direct-Obscura client applies it to the
initial and terminal navigation URL. The final-hop policy separately requires
the fixed native-Tor Unix socket before allowing such HTTP traffic. No onion
address-format check is duplicated outside Tor.

The stock path intentionally retains upstream behavior, including a
possible second origin request when Chromium follows a qualifying requests
failure and local Chromium's weaker containment inside `api_server`. Obscura
wait settings do not apply. The startup patch strictly validates and overrides
the pinned crawler's separate 50 MiB PDF and 20 MiB HTML defaults so
`ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB` controls requests-fetched PDF/HTML and
UTF-8 encoded Chromium-rendered HTML alike. Those stock checks remain
post-materialization and are not complete download or peak-memory bounds.
Wrapper character limits and mixed-success failure presentation remain
separate retained patches.

`sitecustomize_api_server/open_url_failure_reporting_patch.py` is installed
before either transport is selected. It records the final post-fallback
per-URL failures and appends the pinned upstream sanitized failure message when
a batch also has rich successful results. It leaves all-success, all-failure,
and timeout-only response forms unchanged, so the behavior is identical in
stock and direct Obscura modes.

The shared CDP client validates URL syntax without public DNS, opens one
v0.1.11-isolated browser connection per request, tracks the terminal main-frame
Document request, reads retained body streams with actual byte accounting,
obtains rendered DOM, returns typed warning-level failures, redacts wrapper
diagnostics, and cleans up streams, targets, sessions, and connections on every
path. It relies on connection isolation instead of a non-atomic cookie-clear
command. The pinned server can evict a subresource-heavy page's main body before
creating its loader alias. The client maps that exact rejection to
`body-unavailable`; Onyx may continue only with same-navigation HTML/XHTML DOM,
while PDF/raw/binary paths remain strict. See
[Request handling](request_handling.md).

## Lite-mode `open_url` availability

Stock Onyx makes `OpenURLTool.is_available()` return false whenever
`DISABLE_VECTOR_DB=true`. That condition is too broad for this deployment's
lite mode: exact-ID reuse of a connector document already indexed under the
requested URL is unavailable without a vector database, but the independent
built-in crawler can still open and read public web pages. This reuse occurs
only while executing the chat-time `open_url` tool; it does not ingest the URL
or run semantic `internal_search`. If left unchanged, Onyx hides `open_url`
from user-visible built-in skills and skips it while constructing an Agent's
tools, leaving lite mode with search results but no tool for opening their
pages.

The lite overlay therefore sets the stack-owned
`ONYX_FORCE_OPEN_URL_AVAILABLE=true`. The API bootstrap installs
`sitecustomize_api_server/lite_open_url_availability_patch.py`, which overrides
only `OpenURLTool.is_available()` and only when `DISABLE_VECTOR_DB=true`. Full
mode does not set the switch and retains upstream availability behavior.

The installer strictly validates both sides of the contract before exposing
the tool: upstream must still disable it specifically because of
`DISABLE_VECTOR_DB`, and `OpenURLTool.run()` must still execute indexed
retrieval and crawling as failure-tolerant siblings while converting an index
failure into an empty indexed result. Lite mode consequently returns crawler
content while its `DisabledDocumentIndex` continues to fail loudly if called;
the patch does not restore RAG or indexed retrieval.

`ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB` controls only the built-in crawler. In
stock mode it applies to requests-fetched PDF/HTML and rendered Chromium HTML;
in direct Obscura mode it applies independently to the retained main-resource
body and rendered DOM. It is absent from the background RAG indexing service,
and exact-ID indexed retrieval bypasses it. Both indexed and crawled content
remain subject to the final `ONYX_OPEN_URL_MAX_TOTAL_CHARS` LLM-output budget
after merge. The Makefile's derived Obscura retention floors are shared browser
memory settings, not document-indexing limits, and SearXNG retains its separate
fixed 20 MiB search-DOM ceiling.

Remove this patch and its Compose switch when upstream makes crawler-backed
`open_url` available with the vector database disabled. Until then, every
Onyx upgrade must verify the availability gate, crawler/index failure
separation, user-visible skill listing, Agent tool construction, and a real
lite-mode `open_url` request.

## SearXNG overlay

The derived image is based on SearXNG `2026.7.15-7b2199ecd` and strictly
validates the upstream offline processor, search orchestration, result
container, timeout, and exception shapes used by its runtime patches. The
derived `searxng/Dockerfile` installs all build-time pinned Python
dependencies, including the audited Playwright 1.58 client, from the generated
hashed `searxng/requirements.txt` lock. It downloads neither a browser nor
packages at runtime. The Makefile derives the local image tag from the pinned
upstream tag and the complete set of embedded Dockerfile, lock, shared-client,
and engine inputs, preventing a stale manually tagged image from surviving a
source-only change. Compose explicitly exposes the wrapper patch directory,
the SearXNG application root, and the shared client directory to the embedded
Python interpreter so patch or client import failure remains startup-visible.
The runtime direct client uses pinned WebSockets because Playwright's public
page session attachment is incompatible with the pinned Obscura server's
reused flattened session identifier.

`google2`, `brave2`, `duckduckgo2`, `startpage2`, and `bing2` are offline
engines. `_obscura.py` owns one-navigation transport, exact terminal hosts,
challenge/status mapping, the provider lease, and an exact three-second start
interval. Each engine owns URL construction, sanitized DOM parsing, result
normalization, and explicit no-results detection. Parser mismatch is an
unresponsive failure, not empty success.

The overlay uses `use_default_settings.engines.keep_only: []` and explicitly
adds only those five engines. Unused stock engines are absent rather than merely
disabled, preventing their initialization and associated startup network work.

The SearXNG startup patch keeps the existing round-robin selection/retry and
last-resort scoring behavior. It atomically reserves a provider before thread
creation, passes its opaque exact reservation token to that engine attempt, and
does not fan out when every provider is busy, cooling, or suspended. That path
adds visible unresponsive-engine records without creating browser threads. It
extends the ordinary offline processor's suspension path to
CAPTCHA, rate-limit, and access-denied exceptions. SearXNG continues to own
timeouts, late results, unresponsive records, retry selection, and suspension.
Disabling round robin deliberately restores ordinary selected-engine fan-out.

## Network and helper routing patches

Environment-aware Onyx HTTP clients are routed through fixed public or
saved-level host-capable bridges. The tracked `onyx/helper-egress.env` owns
both `NO_PROXY` forms for trusted internal peers, including the exact
`obscura-cdp-gateway` name. Public targets must never be added there.

Saved MCP/OAuth and Web Connector choices, configured inference, and the
embedding shim retain their public/host route-class selection. The exact
internal Teep base is a startup-validated direct exception. Full-mode doc-drop
uses its exact local gateway rather than a process-wide direct crawl.
The Compose final-hop host policy separately restricts exact
`host.docker.internal` by TCP port with operator default `none`. Full mode
separately supplies only the exact configured embedding authority, and each
policy process separately holds only its configured upstream-proxy authority.
These are wrapper policies, not Onyx runtime patches.

Onyx Playwright consumers retain explicit helper proxy routing with Chromium's
implicit loopback bypass disabled. In direct Obscura mode this does not create
a crawler fallback; in the default stock mode it carries the intentionally
retained crawler fallback through the public bridge.

## Telemetry, automatic fetches, and WebUI egress

The wrapper explicitly overrides the pinned images' optional reporting and
tracing configuration rather than relying on unset values inherited from the
generated upstream `.env` file. `DISABLE_TELEMETRY=true` disables Onyx's
anonymous telemetry endpoint. Empty Sentry, PostHog, marketing PostHog,
HubSpot, Braintrust, and Langfuse credentials disable those clients; the
PostHog key, rather than its default host value, is the authoritative enable
condition. Cloud, paid-EE, Stripe, GTM, and reCAPTCHA configuration remains
disabled in both the backend and WebUI. The current reCAPTCHA Enterprise
project, API-key, site-key, and hostname settings are explicitly empty; the
obsolete `RECAPTCHA_SECRET_KEY` name is not treated as a current control.

Two pinned backend defaults make third-party requests independently of
`DISABLE_TELEMETRY`. The wrapper sets both to an explicit empty value:

- `AUTO_LLM_CONFIG_URL` disables the periodic recommended-model configuration
  download from the Onyx GitHub repository. Model configuration is therefore
  local/operator-managed.
- `DISPOSABLE_EMAIL_DOMAINS_URL` disables the public disposable-email domain
  list download. Disposable-address filtering is consequently unavailable;
  ordinary authentication and the configured valid-domain controls are
  unchanged.

Release-note refreshes remain enabled by design. They fetch the Onyx
documentation changelog through the fixed public Onyx egress route and cache
the result. Local administrative usage/query analytics also remain available:
they are stored and rendered by this deployment and are not the optional
PostHog, Sentry, or custom-script integrations described above. Operator-added
connectors, MCP servers, inference providers, web-search providers, OAuth
flows, and similar configured integrations retain their intended outbound
behavior through the documented route class.

The WebUI executes in the user's browser, so injected script, a Markdown image,
or an embedded component would otherwise use the user's ordinary network and
bypass every container VPN/proxy control. The tracked
`onyx/nginx/webui-csp.conf` adds a second response policy with these effective
source classes:

```text
default-src 'self';
script-src 'self' 'unsafe-inline';
script-src-attr 'none';
style-src 'self' 'unsafe-inline';
img-src 'self' blob: data:;
font-src 'self';
connect-src 'self';
media-src 'self' blob:;
frame-src 'self' blob:;
worker-src 'self' blob:;
manifest-src 'self';
```

Browsers enforce that policy together with Onyx's existing object, base, form,
and frame-ancestor restrictions. It blocks third-party scripts, browser
connections, frames, media, fonts, workers, manifests, arbitrary remote
Markdown images, and the Web-result Google favicon request before a network
request is made. It also blocks inline event-handler attributes. Same-origin
API calls, packaged assets, backgrounds, logos, uploaded images, and
`/api/chat/file/{id}` remain available. `blob:` keeps local image/audio/PDF and
worker URLs working, and `data:` is limited to images so embedded DOCX preview
images work without permitting a general data-document source.

The official image's dynamic Next.js HTML contains inline bootstrap and React
stream scripts, so a static `script-src 'self'` policy breaks hydration. A
runtime nginx response-filter prototype successfully added nonces to the HTML
but broke the current Next chunk/preload path with `strict-dynamic`. Passing a
CSP nonce into the pinned Next 16.2.6 renderer also failed because the compiled
Onyx Proxy does not preserve that request header through to rendering. Browser
testing produced a complete response but a blank, unhydrated DOM in both strict
variants.

The compatible no-rebuild policy consequently allows inline script blocks but
not inline event-handler attributes or `eval`, and permits external scripts
only from the WebUI origin. This is weaker than a source-level nonce policy: an
XSS sink capable of creating an executable inline script block can still run
code. The policy nevertheless blocks common event-handler injection, remote
script loading, and the network/resource classes normally used for automatic
browser-side exfiltration. A future rebuilt WebUI should implement a real
per-response nonce in the Onyx/Next source and remove `'unsafe-inline'` only
after login, React streaming, and lazy chunk loading pass in a real browser.

Code-interpreter output does not need a `localhost` CSP exception. An
unconditional strict API patch changes generated-file links to relative
same-origin `/api/chat/file/{id}` values, adds a ready-to-copy
`response_markdown` ordinary link to each LLM-facing generated-file result, and
requires every user-requested file in the Python function description, Python
guidance, and post-execution reminder. For an image filename, the WebUI extracts
the ID from the ordinary link and renders the relative endpoint. Accidental
Markdown image syntax also remains same-origin, although prompts require the
supplied ordinary link so Onyx can consistently recognize and retain the
reference. Custom prompts should copy `response_markdown` rather than construct
a file URL.

The pinned Python tool renderer has an unsafe error fallback: if highlight.js
throws, it passes raw model-emitted Python text to `dangerouslySetInnerHTML`.
The official runtime image contains only compiled chunks, and modifying those
chunks in place would retain their immutable cache URLs, so the wrapper does
not apply a misleading partial bundle rewrite. The CSP blocks event-handler
attributes and remote resource exfiltration from this path, and scripts inserted
through `innerHTML` are normally inert, but `'unsafe-inline'` means CSP is not a
complete containment claim for this or another executable script sink. Replace
the fallback with escaped text in the next source/image build, or drop the
checkpoint if upstream fixes it first.

This is a browser resource-load boundary, not a claim that CSP routes traffic
through Myst. External links and top-level user navigation remain possible,
and the backend route policy remains authoritative for server-side traffic.
The policy intentionally makes Stripe Elements, GTM, reCAPTCHA, third-party
Sentry/PostHog clients, remote Craft previews/media, and the custom injected
analytics-script feature inoperable even if accidentally configured. Local
administrative analytics do not depend on those clients. The generated
upstream environment template mentions `WEB_STRICT_CSP_ENABLED`, but the
pinned WebUI does not read it; the wrapper does not rely on that dead switch.

## Background Web connector PDF freshness

Full mode narrows Onyx's Web connector PDF freshness behavior for trusted
local document origins. For allowlisted local hosts, the background patch uses
stable HTTP metadata such as `Last-Modified` and `Content-Length` to skip a full
download and PDF parse when a document has not changed. It stores the wrapper
freshness metadata on the Onyx document record for later syncs.

The behavior is deliberately limited to configured local origins; ordinary
external Web connector PDFs retain upstream Onyx behavior. Before changing
either method, patch installation validates the exact signatures and critical
source operations of `WebConnector._do_scrape()` and
`indexing_pipeline.get_docs_to_update()`. It also validates the connector
`Document` fields, database document attributes, and `ScrapeResult` shape.
Only an unreadable sentinel or an unchanged sentinel whose HTTP validators
still match the database record is removed before indexing. A stale or
malformed sentinel falls through to normal indexing, including forced reindex.

`tests/test_web_connector_egress_patch.py` covers installed unchanged, changed,
missing-validator, terminal-error, and non-allowlisted HEAD paths; matching,
unreadable, stale, and ordinary sentinel behavior; and callable/model drift.
The upgrade audit
installs the patch against the pinned backend image and then exercises a real
doc-drop PDF crawl. Remove the freshness and sentinel patches together if
upstream gains an equivalent trusted-origin pre-download validator and forced-
reindex-safe skip mechanism. See
[Local document RAG search](local_docs_rag_search.md#pdf-freshness-patch).

## Deep Research tools, batches, and tool choice

The shared API patch passes every tool selected for the chat Agent into nested
Deep Research instead of reducing the set to search and URL tools. Exact source
replacement checks guard both the orchestrator and nested research loop. The
patch also:

- rejects a model-emitted batch before execution when it exceeds the configured
  maximum;
- prevents control tools such as `think_tool` and `generate_report` from being
  mixed with another call;
- preserves every accepted merged call, gives it a distinct nested UI
  placement, and bounds worker concurrency independently of batch size;
- updates both cycle-limit prompts and constants together; and
- changes only the four known forced-tool call sites to automatic tool choice,
  avoiding the pinned vLLM structured-decoding failure while retaining each
  loop's existing no-tool completion branch.

The installer source-checks every rewritten call site. Focused tests in
`tests/test_shared_agent_patch_contracts.py` cover batch rejection, control-
tool isolation, placement mapping, and all four forced-to-auto wrappers; strict
installation is also tested against the pinned Onyx image. Remove individual
rewrites when upstream preserves selected tools, mixed batches, bounded
concurrency, placement, and compatible automatic tool choice natively.

## Reasoning, tool history, and coding finalization

The reasoning patch family carries assistant `reasoning_content` across Onyx's
structured-message, reconstructed-history, and LiteLLM serialization
boundaries. Exact source checks protect every rebuilt loop and serializer. The
native-reasoning override now additionally validates the pinned detector's
two-argument signature and its model-map/LiteLLM fallback source before
replacing it. The saved-tool-result patch validates both the upstream response
helper and complete `convert_chat_history()` signature before retaining stored
responses and recomputing their token counts.

Coding-agent final synthesis receives a plain-text transcript containing tool
requests, tool output, and retained reasoning. If only final synthesis fails,
the fallback returns sanitized, bounded tool output without disclosing the
exception message; setup and execution failures retain their normal failure
semantics. The relevant upstream final-answer source is checked before
wrapping.

Focused tests cover reasoning attachment/serialization, native-detector drift,
saved response selection/token recounting, and the bounded final-answer
fallback. Strict installation of the complete composed reasoning and Deep
Research rewrites is tested against the pinned backend image. Remove each patch
only after the corresponding upstream model types and reconstruction paths
preserve reasoning/tool results and finalization failures no longer discard
successful tool evidence.

## Context and result-size patches

The configured LLM context override validates both upstream token-limit lookup
functions before making `GEN_AI_MAX_TOKENS` authoritative. The internal-search
patch validates the complete formatter signature and its result/content JSON
construction before applying per-result and aggregate character caps. The
`open_url`/web-search patch validates the positional defaults it changes, and
the repository-download patch validates the downloader signature and defaults
before aligning it with the code-interpreter upload receiver.

The per-URL `open_url` character cap applies while crawler output is converted
to an inference section. Exact-ID indexed sections bypass it. The aggregate cap
is applied after indexed/crawled merge and therefore limits both representations
in the LLM-facing string without limiting retrieval or ingestion itself.

Focused tests cover each configured value and drift boundary. They also cover
small internal-search budgets: if the human-readable truncation notice cannot
fit, raw content is cut at the exact cap instead of allowing the notice itself
to exceed the configured total. Remove these patches when upstream exposes
equivalent settings at the same preflight/post-format boundaries.

## Code-interpreter executor networking

Python execution uses a wrapper-derived executor image rather than the
code-interpreter API image. Its local tag is coupled to the pinned upstream
`python-executor-sci` release, `executor/Dockerfile`, and the generated hashed
`executor/requirements.txt` lock. The derived image adds SymPy 1.14.0 and its
locked `mpmath` dependency to the existing scientific environment. Docker-mode
startup builds the exact selected image before the API starts, and Compose sets
`PYTHON_EXECUTOR_DOCKER_IMAGE` explicitly; the upstream bare reference cannot
resolve to or pull mutable `latest` during API startup.

Unconditional strict API patches advertise the wrapper-maintained package set,
including SymPy, in both `PythonTool.DESCRIPTION` and
`PYTHON_TOOL_GUIDANCE`, and put generated-file response requirements in the
actual function description as well as the broader guidance. Each generated
file uses a relative same-origin link and includes exact `response_markdown`
for the final answer. This behavior is independent of optional executor
networking. Source drift in the upstream description, guidance, helper import,
or `PythonTool.run` signature stops API startup; malformed generated-file
results fail visibly at execution. The LLM-facing tool name is `run_python`;
`Code Interpreter` remains the display name. The wrapper does not alias or
accept another LLM-facing name. Pinned-image validation confirms the class,
constant, built-in map, saved-row remapping, prompt, relative-link helper, and
run wrapper after every patch is installed.

When optional executor networking is enabled, Compose uses upstream's native
`PYTHON_EXECUTOR_DOCKER_NETWORK` and `PYTHON_EXECUTOR_DOCKER_RUN_ARGS` settings.
The code-interpreter service has no wrapper runtime patch. Deterministic tests
parse the configured run arguments and reject extra or mismatched values. Image
validation inspects the exact pinned `DockerExecutor._build_run_command()`
signature and critical source layout, constructs a harmless sample command, and
confirms that it selects the dedicated network and preserves exactly the eight
upper/lower-case restricted proxy variables.

The API-side network capability patch uses exact upstream text replacements
for the Python tool, Bash tool, coding-agent mock tool, Python guidance, and
both coding-agent prompts. It tells the model about restricted proxy-only access
without claiming direct sockets, private targets, or direct search-engine URLs
are reachable. The separate file-link prompt patch is unconditional because
artifact rendering is independent of executor networking.
`tests/test_code_interpreter_executor_env.py` exercises the native run-argument
contract, malformed configuration, generated output, and upstream drift;
`tests/test_shared_agent_patch_contracts.py` covers every capability string and
strict text drift. The native command contract is additionally checked inside
the pinned code-interpreter image without installing a runtime patch.

Remove the API-side capability patch when Onyx derives accurate capability text
from the native executor settings.

## Other retained wrapper behavior

Other retained behavior has its own focused tests and upgrade checks:

- lite-mode `open_url` availability;
- local doc-drop behavior;
- local embedding shim model-name/query-prefix behavior, including the exact
  fake-nomic v23-to-v1 tokenizer-only alias that preserves Onyx feature gates;
- bundled host embedding lifecycle ownership, pre-thread non-loopback peer
  rejection, socket-bounded connection threads, unbounded live-child cold
  startup, five-minute proxy-to-child blocked-socket timeout, single-attempt
  shim forwarding, and accepted-request shutdown draining;
- static single-node OpenSearch policy: 512 MiB fixed heap,
  `node.processors=4`, disabled Performance Analyzer and Query Insights top-N
  collection, monthly body-free audit initialization, and zero replicas for
  newly created Onyx indices, without a runtime migration service;
- host publisher, Tailscale, MinIO, authentication, and Teep integration.

The wrapper explicitly disables Onyx Craft. Ordinary chat uses the standalone
code-interpreter service for `run_python`; Craft is a separate OpenCode-based,
per-user environment that requires a Kubernetes or Docker sandbox backend,
additional egress and credential boundaries, and its own resource lifecycle.
Those requirements are not implemented by this wrapper. The resource
consequences of keeping Craft absent are documented in
`docs/resource_minimization.md`.
The strict background bootstrap materializes seven connector-discovery
schedules at five minutes, removes their one-minute templates, removes the
three Craft cleanup schedules, and removes the queue/process/memory monitoring
producers. It also raises Beat's schedule reload interval to five minutes and
removes worker liveness bootsteps. It deliberately leaves the upstream Beat
`tick()` method intact: that method publishes local process liveness when the
reload tick runs and logs schedule-update failures independently. The wrapper
watchdog therefore detects a stopped Beat loop without turning a persistent
schedule or database error into a restart loop.

`onyx/background_entrypoint.py` derives a supervisor file only after validating
the pinned eight-worker, beat, watchdog, bot, and log-tail shapes. It retains
six bounded workers without Celery heartbeat/gossip, drops monitoring workers,
selects bot programs from two default-off Compose booleans, and replaces the
Redis-based watchdog with a local liveness-file watchdog that can restart only
Beat. Any source or configuration drift is a startup failure. These behaviors
are power policy, not compatibility fallbacks, and should be removed if Onyx
gains equivalent explicit configuration.

The bot booleans are the wrapper-owned, full-mode-only
`ONYX_AGENT_SLACK_BOT` and `ONYX_AGENT_DISCORD_BOT`. Lite mode has no
background supervisor, so neither setting creates a bot process there.

## Compose wrapper changes

The base wrapper adds the hardened single-process Obscura service, direct
control networks, API-only CDP gateway, derived SearXNG service, distinct fixed
egress bridges, and shared public/host final-hop policies. Obscura v0.1.11
isolates every live WebSocket browser context and rejects connections above the
aggregate capacity of 15. It runs read-only as 65534:65534, without
capabilities, storage, private mounts, or file-access permission. Application
containers do not join the trusted VPN namespace or a direct public network.

Lite and full overlays mount the same named API bootstrap. Full mode adds local
RAG services; lite mode does not install an anonymous substitute bootstrap.
The optional code-interpreter network overlay adds only the executor network
and bridge selected by the strict runtime patch.

The nginx service additionally mounts the tracked restrictive CSP fragment
directly into `/etc/nginx/conf.d`. The fragment is independent of the generated
upstream nginx template and survives regeneration of `onyx/onyx_data`; nginx
startup remains fatal if the fragment is syntactically invalid.

## Maintenance rule

Every pin or source-shape change must follow
[the upgrade checklist](onyx_patches_upgrade.md). Prefer removing a patch when
upstream exposes an equivalent strict configuration. Never retain a silent
compatibility branch, direct fallback, or broad exception suppression.
