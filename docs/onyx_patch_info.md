# Wrapper patch information

The wrapper uses runtime `sitecustomize` patches because several required
behaviors are not configurable in the pinned Onyx release. Patches are narrow,
source-shape checked, and strict: a required patch that no longer matches must
stop startup rather than silently restore an unsafe upstream path.

## Bootstrap ownership

`sitecustomize_api_server` is the only API bootstrap in both lite and full
modes. `sitecustomize_background` owns full-mode background patches, and
`sitecustomize_code_interpreter` owns the code-interpreter service patch. The
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

The stock path is the default because parallel testing against Obscura 0.1.10
found it was blocked less often and was substantially more reliable. This is a
pin-specific operational observation. Re-run the comparison when Obscura is
upgraded and change the default if the direct browser path improves enough to
justify its stronger containment and one-navigation behavior.

### Direct Obscura mode

`sitecustomize_api_server/obscura_crawler_patch.py` strictly replaces the
built-in `OnyxWebCrawler` URL-fetch path. It imports the single client in
`browser/obscura_client` and provides:

- one raw Obscura `Page.navigate` per accepted URL, with no requests,
  Playwright-local-browser, Firecrawl, CLI, generic-parser, or second-fetch
  fallback;
- one absolute 120-second monotonic invocation deadline/finalization state,
  passed through the outer and nested crawler executors;
- five process-global blocking permits acquired only for remaining budget,
  finalization checks before setup and immediately before navigation, and
  permit retention through cleanup for already-sent requests;
- stable requested-URL ordering and failure/snippet correlation, with terminal
  URLs retained for successful content and citations after redirects;
- same-navigation PDF, HTML, and exact raw-text dispatch using pinned Onyx
  extractors;
- a positive finite configured main-response byte limit, including for HTML,
  a separate fixed 20 MiB serialized-DOM bound, existing post-parse character
  budgets, and normal unsuccessful
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

The stock path intentionally retains upstream behavior, including a
possible second origin request when Chromium follows a qualifying requests
failure and local Chromium's weaker containment inside `api_server`. Obscura
document/wait settings do not apply. Wrapper character limits and
mixed-success failure presentation remain separate retained patches.

`sitecustomize_api_server/open_url_failure_reporting_patch.py` is installed
before either transport is selected. It records the final post-fallback
per-URL failures and appends the pinned upstream sanitized failure message when
a batch also has rich successful results. It leaves all-success, all-failure,
and timeout-only response forms unchanged, so the behavior is identical in
stock and direct Obscura modes.

The shared CDP client validates URL syntax without public DNS, tracks the
terminal main-frame Document request, reads retained body streams with actual
byte accounting, obtains rendered DOM, returns typed warning-level failures,
redacts wrapper diagnostics, and cleans up streams, targets, sessions, and
connections on every path. Obscura 0.1.10 can evict a subresource-heavy page's
main body before creating its loader alias. The client maps that exact rejection
to `body-unavailable`; Onyx may continue only with same-navigation HTML/XHTML
DOM, while PDF/raw/binary paths remain strict. See
[Request handling](request_handling.md).

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
page session attachment is incompatible with Obscura 0.1.10's reused
flattened session identifier.

`google2`, `brave2`, `duckduckgo2`, `startpage2`, and `bing2` are offline
engines. `_obscura.py` owns one-navigation transport, exact terminal hosts,
challenge/status mapping, the provider lease, and an exact three-second start
interval. Each engine owns URL construction, sanitized DOM parsing, result
normalization, and explicit no-results detection. Parser mismatch is an
unresponsive failure, not empty success.

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

The WebUI executes in the user's browser, so a Markdown image or embedded
component would otherwise use the user's ordinary network and bypass every
container VPN/proxy control. The tracked `onyx/nginx/webui-csp.conf` adds a
second response policy:

```text
img-src 'self' blob: data:;
```

Browsers enforce that policy together with Onyx's existing CSP. It blocks
arbitrary remote Markdown images and the Web-result Google favicon request
before a network request is made. `'self'` preserves packaged images,
backgrounds, logos, uploaded images, and `/api/chat/file/{id}`. `blob:` keeps
local preview object URLs working, and `data:` keeps embedded DOCX preview
images working; neither permits an HTTP(S) image destination.

Code-interpreter output does not need a `localhost` CSP exception. The pinned
prompt tells the model to emit `[filename](file_link)`. For an image filename,
the WebUI extracts the ID from the `/api/chat/file/` path and renders a new
relative `/api/chat/file/{id}` URL, even if the original backend link used the
default absolute `http://localhost:3000` origin. Custom prompts should retain
that ordinary-link form and should not emit a hard-coded Markdown image URL.

This is an image-egress control, not a claim that CSP routes browser traffic
through Myst. External links can still be opened by the user, and the backend
route policy remains authoritative for server-side traffic. The generated
upstream environment template mentions `WEB_STRICT_CSP_ENABLED`, but the
pinned WebUI does not read it; the wrapper does not rely on that dead switch.

## Background Web connector PDF freshness

Full mode narrows Onyx's Web connector PDF freshness behavior for trusted
local document origins. For allowlisted local hosts, the background patch uses
stable HTTP metadata such as `Last-Modified` and `Content-Length` to skip a full
download and PDF parse when a document has not changed. It stores the wrapper
freshness metadata on the Onyx document record for later syncs.

The behavior is deliberately limited to configured local origins; ordinary
external Web connector PDFs retain upstream Onyx behavior. Patch installation
validates the pinned connector, document, and database shapes and fails visibly
if an Onyx upgrade changes them. See
[Local document RAG search](local_docs_rag_search.md#pdf-freshness-patch).

## Other retained patches

The migration does not change the following independently tested behavior:

- LLM context-window override and reasoning-content preservation;
- Deep Research selected-Agent tools, bounded tool batches, and cycle limit;
- automatic tool choice for GLM/reasoning and coding/research agents;
- coding-agent final-answer and saved tool-result preservation;
- `open_url` and `web_search` post-fetch character budgets;
- coding-agent repository/upload byte-limit alignment;
- internal-search content caps;
- lite-mode `open_url` availability;
- local doc-drop behavior;
- optional code-interpreter executor networking, proxy injection, and
  capability descriptions;
- local embedding shim model-name/query-prefix behavior, including the exact
  fake-nomic v23-to-v1 tokenizer-only alias that preserves Onyx feature gates;
- host publisher, Tailscale, MinIO, authentication, and Teep integration.

The wrapper explicitly disables Onyx Craft because it provides the legacy
code-interpreter service, not a Craft Kubernetes or Docker sandbox backend.
The background bootstrap removes the otherwise unconditional
`cleanup-idle-sandboxes` beat template when Craft is disabled. The removal is
strictly shape-checked so an Onyx scheduling change fails visibly instead of
causing periodic attempts to initialize an unconfigured Kubernetes backend.

## Compose wrapper changes

The base wrapper adds the hardened five-worker Obscura service, direct control
networks, API-only CDP gateway, derived SearXNG service, distinct fixed egress
bridges, and shared public/host final-hop policies. Obscura runs read-only as
65534:65534, without capabilities, storage, private mounts, or file-access
permission. Application containers do not join the trusted VPN namespace or a
direct public network.

Lite and full overlays mount the same named API bootstrap. Full mode adds local
RAG services; lite mode does not install an anonymous substitute bootstrap.
The optional code-interpreter network overlay adds only the executor network
and bridge selected by the strict runtime patch.

The nginx service additionally mounts the tracked image-source CSP fragment
directly into `/etc/nginx/conf.d`. The fragment is independent of the generated
upstream nginx template and survives regeneration of `onyx/onyx_data`; nginx
startup remains fatal if the fragment is syntactically invalid.

## Maintenance rule

Every pin or source-shape change must follow
[the upgrade checklist](onyx_patches_upgrade.md). Prefer removing a patch when
upstream exposes an equivalent strict configuration. Never retain a silent
compatibility branch, direct fallback, or broad exception suppression.
