# WebUI Stream Reconnection Plan

> **Status: implementation and Docker live boundary validation complete;
> authenticated browser and unavailable engine/ingress validation pending.**
> The lasting behavior and maintenance contract are in the owning documents
> listed below. This plan remains the acceptance and outstanding-validation
> artifact until the practical browser/engine matrix is complete.

## Objective

Recover a recorded Onyx chat automatically when a browser loses its live chat
response stream while the backend continues generating. The primary target is
mobile and tablet browser suspension, including iOS backgrounding, but the
same mechanism must handle transient client networking failures and restored
pages from the back-forward cache.

Implement the recovery without rebuilding or rewriting the stock Onyx WebUI
image. A small wrapper-owned, same-origin script will be injected into HTML by
the existing nginx boundary. On browser wake it will reload the affected chat
once, allowing Onyx v4.6.5's stock recorded-chat hydration and durable stream
resume path to reattach to the backend run. It must never resend the original
chat POST.

Increase Onyx's native durable-stream limits at the same time:

| Onyx environment variable | Required value | Meaning |
| --- | ---: | --- |
| `CHAT_STREAM_BUFFER_TTL_S` | `14400` | Four-hour TTL assigned to each chunk when written and to metadata on each flush. |
| `CHAT_STREAM_BUFFER_DONE_TTL_S` | `3600` | One-hour retention after recorded-chat completion. |
| `CHAT_STREAM_BUFFER_MAX_BYTES` | `33554432` | 32 MiB compressed cap per run. |

These are stack-owned internal policy values. Set literal values in Compose;
do not expose them in `.env.wrapper.example`, add compatibility aliases, or
make them user-facing knobs.

## Upstream Contract and Scope

The implementation is deliberately a thin recovery trigger around the pinned
stock behavior. Before editing, re-read and validate these v4.6.5 sources in
`reference_repos/onyx`:

- `web/src/app/app/services/lib.tsx`: `sendMessage()` posts to
  `/api/chat/send-chat-message`; `resumeStream()` reads
  `/api/chat/chat-session/{session_id}/resume-stream?cursor=...`.
- `web/src/app/app/services/currentMessageFIFO.ts`:
  `updateCurrentMessageFIFO()` turns a browser-side stream exception into
  `CurrentMessageFIFO.error` and completes the FIFO.
- `web/src/hooks/useChatController.ts`: the stock active-send consumer turns
  that FIFO error into the current chat error state; explicit user cancellation
  calls the exact `stop-chat-session` endpoint, while route cleanup can abort a
  stream controller without expressing cancellation intent.
- `web/src/hooks/useChatSessionController.ts`:
  `resumeInFlightRun()` replays a recorded single-model run from cursor zero,
  tails it, aborts its controller in `finally` as local cleanup, and then
  refreshes the persisted session. It intentionally does not attach to a
  multi-model run because that run id identifies the user message, not an
  assistant node.
- `web/src/views/AppPage.tsx`: incognito sessions register a `pagehide`
  `sendBeacon()` teardown and are intentionally deleted when the page closes.
- `backend/onyx/server/query_and_chat/chat_backend.py`:
  `get_chat_session()` exposes `current_run`, `resume_chat_stream()` owns the
  replay/tail endpoint, and `end_incognito_session()` owns immediate incognito
  teardown.
- `backend/onyx/chat/chat_processing_checker.py` and
  `backend/onyx/cache/interface.py`: the processing-fence encoding and selected
  backend transient exceptions used by the wrapper recovery-status route.
- `backend/onyx/chat/stream_buffer.py`: buffers exact outbound NDJSON as
  compressed chunks, treats truncation/eviction as a gap, deletes completed
  incognito buffers, and gives recorded completed buffers the done TTL.
- `backend/onyx/configs/chat_configs.py`: the three required Compose settings
  are native Onyx environment variables.

Re-audit all named symbols and assumptions on every Onyx upgrade. If upstream
adds a supported browser reconnect implementation with equivalent recorded,
multi-model, cancellation, and incognito semantics, remove this injection
rather than carrying two recovery owners.

### Included behavior

- Recorded single-model chat whose original response stream fails after the
  POST has been accepted.
- Recorded chat that completes while its tab is suspended.
- A second suspension after a first successful reattachment.
- Transient offline/online and back-forward-cache restoration signals.
- Multi-model recorded chat through a completion-aware fallback described
  below; stock Onyx cannot render a live multi-model replay after reload.
- Localhost, fixed host publishing, Tailscale Funnel, and onion WebUI origins.
- Lite and full modes under both Docker and Podman.

### Explicit exclusions and boundaries

- Do not retry, reconstruct, or resend `/api/chat/send-chat-message`. Only the
  original stock request may initiate the run.
- Do not implement an SSE parser or a second packet/state reducer. Stock Onyx
  remains the sole owner of replayed packet interpretation and React state.
- Do not patch immutable `.next` chunks or bind-mount modified compiled WebUI
  assets over their hashed paths.
- Do not register a service worker or attempt to keep JavaScript alive while
  the operating system suspends it.
- Do not suppress, delay, replace, or otherwise intercept the functional
  result of the incognito teardown beacon. Incognito remains intentionally
  non-recoverable after `pagehide`; its content-free completed buffer is still
  deleted. The companion may clear its own stale marker after observing an
  incognito teardown request, but must call the original browser API exactly
  once and return its exact result.
- A browser process killed before the companion records an accepted send may
  still require a manual reload. The implementation must not claim recovery
  for a request whose session id was never observed.
- Provider-side inference continuation and browser-to-Onyx reconnection remain
  separate failure domains. The existing LiteLLM continuation patch may keep
  the backend run alive; this plan only restores the browser view.

## Design

### 1. Wrapper-owned nginx integration

Add a tracked nginx integration under `onyx/nginx/`, with clear ownership and
no changes to generated files under `onyx/onyx_data/`:

- `webui-reconnect.js`: dependency-free browser companion.
- `webui-reconnect-http.conf`: directives valid at nginx `http` context,
  including an `Accept`-based upstream compression map if required.
- `webui-reconnect-server.inc`: directives included inside the generated
  server block. It owns the exact static-asset location and the HTML
  `sub_filter` rule.
- `run-nginx-wrapper.sh`: strict startup shim that copies the currently
  generated upstream templates, transforms only ephemeral copies of the
  template and runner, and executes that derived runner.

Resolve the common nginx service and selected-image gate from the same
`NGINX_IMAGE=docker.io/library/nginx:1.25.5-alpine` pin in
`stack.versions.env`; validation must fail if it is absent. Startup/image
validation must explicitly require
`--with-http_sub_module`; do not assume that all future nginx images contain
it.

The upstream Next server currently compresses HTML when the browser advertises
gzip. `sub_filter` cannot be trusted to modify an opaque compressed body. The
wrapper must therefore make HTML document requests reach the WebUI upstream
with identity encoding while retaining normal compression negotiation for
hashed JavaScript, CSS, fonts, images, and other non-HTML resources. Implement
this with an nginx `map` based on an `Accept` header containing `text/html`,
and inject the resulting `proxy_set_header Accept-Encoding ...` into the exact
upstream `location /` block. Do not strip compression globally from API/SSE or
static assets. Test navigation requests both with and without
`Sec-Fetch-Dest`; `Accept: text/html` is the authoritative selector.

The startup shim must:

1. use POSIX shell strict failure handling;
2. reproduce only the generated command's template-copy setup, not copy the
   generated nginx configuration into the repository;
3. require exactly one expected `server {` insertion site and exactly one
   ordinary `location / {` insertion site in the generated
   `app.conf.template`;
4. inject one server-level include and one HTML-only upstream encoding line
   into an ephemeral template;
5. verify the transformed template contains each wrapper marker exactly once;
6. copy the generated `run-nginx.sh` to a temporary runner, require exactly one
   pinned final nginx-start marker, and insert `nginx -t` after upstream has
   rendered `app.conf` and its optional MCP includes but before it starts the
   reload loop or foreground daemon;
7. leave every other generated runner statement, API proxy buffering setting,
   and timeout unchanged;
8. execute the temporary runner with the transformed template;
9. exit nonzero before nginx starts on missing modules, source drift, an
   unreadable asset, or a failed `nginx -t`; and
10. print only non-secret structural diagnostics.

Do not edit `onyx/onyx_data/data/nginx/app.conf.template` or
`run-nginx.sh`. They remain generated artifacts refreshed by the supported
Onyx upgrade flow.

Inside the server include:

- Serve the companion at one reserved same-origin path such as
  `/_private-onyx/webui-reconnect.js` from its read-only mount.
- Use an exact-match location so the asset can never fall through to the WebUI
  or API upstream.
- Set the JavaScript content type, `X-Content-Type-Options: nosniff`, and
  `Cache-Control: no-store`. Do not enable CORS.
- Inject exactly one external `<script defer>` element immediately before
  `</head>` for `text/html` responses. Give it a stable wrapper-owned data
  attribute that tests can count.
- Use `sub_filter_once on` and an exact closing-head source marker. A missing
  runtime marker must make the live validation fail; do not add a second
  fallback insertion pattern.
- Do not inject into API responses, RSC payloads, static assets, downloads, or
  non-HTML errors. Injection into another ordinary Onyx HTML page is harmless:
  the companion must remain inert unless it observes an exact chat send.

Mount the asset, include files, and startup shim read-only on the common nginx
service in `docker-compose.yaml`, and replace only that service's command with
the wrapper shim. Because the common base is used by Docker/Podman and
lite/full modes, do not duplicate these mounts or command changes in engine or
mode overlays. Inspect every effective model to prove the Podman overlays do
not reset them.

The existing tracked CSP already allows external same-origin scripts through
`script-src 'self'` and browser API requests through `connect-src 'self'`.
Do not add a remote source, `data:` script source, `unsafe-eval`, worker source,
or broader connection source. Continue emitting both upstream Onyx CSP and the
tracked restrictive CSP.

### 2. Browser companion state machine

Keep the companion small, readable, dependency-free, and independent of DOM
class names, React internals, Next chunk names, Zustand stores, or rendered
English text. Its only integration points are stable browser APIs, exact API
paths, the `chatId` URL search parameter, and the pinned request/response
shapes listed above.

Use a versioned `sessionStorage` record scoped to the tab. Store only:

- schema version;
- random request token generated in the browser;
- recorded chat session UUID;
- send start timestamp;
- whether at least two `llm_overrides` made the request multi-model;
- hidden timestamp, if the active request was backgrounded;
- last recovery timestamp; and
- bounded `single`/`multi` recovery phase and backoff state.

Never store the prompt, response packets, filenames, model/provider names,
credentials, headers, error bodies, or exception strings. Validate parsed
storage strictly and discard malformed, unknown-version, expired, or
cross-session values. Bound record lifetime to the four-hour live-buffer TTL.

Wrap `window.fetch` once, preserving its receiver, arguments, returned promise,
and errors. Only instrument exact same-origin requests:

- For a stock `POST /api/chat/send-chat-message`, parse the current JSON body
  only far enough to obtain `chat_session_id` and whether `llm_overrides` is a
  two-or-more-entry array. Cancel recovery work for an older marker, then set a
  new marker before calling the original fetch.
- If the original fetch throws synchronously, clear only that new marker and
  rethrow the exact error.
- Never issue a replacement POST after synchronous failure, HTTP failure,
  response-stream failure, or wake-up.
- Clear the matching token on a non-success response or a signal that was
  already aborted before the send was invoked. An abort after invocation is
  ambiguous: stock route cleanup and resumed-stream `finally` cleanup use the
  same signal mechanism, so it must retain the marker unless an exact stop
  request proves user intent.
- For a successful send or exact
  `GET /api/chat/chat-session/{session_id}/resume-stream?cursor={integer}`,
  pass the original bytes through one transparent `TransformStream`. Clear
  only the matching token when the body reaches clean EOF. A visible body
  failure enters recovery for that token. Preserve body errors and
  cancellation unchanged. Do not clone or tee the stream, buffer packets,
  parse SSE, or change chunk timing.
- Reconstructing a `Response` is permitted only for these exact successful
  streaming endpoints and must preserve status, status text, and headers.
  Before accepting this technique, confirm the pinned send/resume consumers
  use no lost `Response` properties such as `url`, `redirected`, or `type`.
  Treat a changed upstream consumer as an upgrade failure requiring redesign.
- Leave ordinary `GET /api/chat/get-chat-session/{session_id}` requests
  untouched. Recovery owns its own bounded status request instead of coupling
  correctness to an upstream fetch's timing, promise, or response body.
- Observe exact same-origin
  `POST /api/chat/stop-chat-session/{session_id}` as the authoritative explicit
  user-cancellation signal. Invoke the original operation exactly once, then
  clear only the matching marker while preserving its result or exception.
- Observe exact incognito end-session fetch/beacon calls only to clear a
  matching companion marker after invoking the original operation. Never
  block, alter, defer, retry, or claim success for the teardown operation.

Register lifecycle listeners once:

- `visibilitychange`: record the hidden time only while a marker is active;
  when visible again, schedule recovery.
- `pagehide`: record state, but do not prevent navigation and do not interfere
  with stock incognito teardown.
- `pageshow`: recover when returning from the back-forward cache or when a
  previous hidden marker exists.
- `online`: retry pending recovery after connectivity returns, but do not
  synthesize a new interruption, status check, or reload while a successful
  stock single-model resume body owns completion.

Treat a companion-owned reload as a committed navigation for its token before
calling the browser API. From that point, the outgoing document may cancel
local timers and requests but must not let stream EOF/abort/failure,
`visibilitychange`, or `pagehide`, in any order, clear the persisted phase or
record a new interruption. If the same JavaScript realm is restored through
`pageshow`, release the in-memory navigation guard and resume the persisted
phase; a replacement document starts without that guard and self-schedules the
phase normally.

Wrap successful History API `pushState` and `replaceState` calls and observe
`popstate` so a retained recovery is re-evaluated when client-side navigation
returns to its `chatId`. Preserve receivers, arguments, results, and exceptions;
schedule no timer when no marker exists, and never redirect or select a chat.

Debounce simultaneous events. At most one token-correlated status request,
poll timer, or reload decision may be active. The request has its own
`AbortController`; hiding cancels it, and its result can mutate only the token
that created it. After the awaited response body, revalidate the token,
selected `chatId`, visibility, and connectivity before any state mutation or
reload; navigation, hiding, or loss of connectivity while the request is in
flight must leave the marker retained and inert. Temporary network, non-success
HTTP, or malformed JSON responses retain state and retry with bounded backoff. Use a minimum recovery
interval and a persisted recovery phase to prevent reload loops. Script startup
self-schedules any persisted phase, so correctness does not depend on whether
the injected deferred script ran before a stock Next.js session request.

Before reloading, require that the current `chatId` URL parameter equals the
marker session. If the user intentionally navigated to another chat, retain
the bounded marker for a later return to its chat but do not reload or redirect
the current view. Never change the selected chat automatically.

Install one API runtime route at
`GET /api/chat/reconnect-status/{session_id}`. It uses the stock `READ_CHAT`
dependency and a narrow session lookup, returns only `incognito` and
`current_run`, and does not load or translate message history. It must not
replace or wrap the ordinary session-detail endpoint. If the selected cache
raises a declared transient exception, or a processing fence exists without a
usable positive run ID, return `503`; absence is authoritative completion only
after those checks succeed. Startup must reject an upstream route collision or
drift in the stock cache-error behavior that makes this patch unnecessary or
unsafe.

#### Recorded single-model recovery

When the marked chat becomes visible after suspension, first make the owned
status request. Clear an incognito or missing session without reload. For a
recorded in-flight session, persist the `single` phase and perform one initial
reconciliation reload. The new stock WebUI fetches the session, sees
`current_run`, and invokes
`resumeInFlightRun()` from cursor zero; if the run completed while hidden, the
reload renders the persisted result instead. The new document performs bounded
settling checks until the companion observes a successful stock resume response;
that body's clean EOF then owns completion even if status reports that the run
has ended while bytes are still draining. If no resume owner appears, checks
continue while the run is active and completion performs one final hydration
reload. A later body failure or genuine suspension can re-enter recovery for
the same token.

If another hide/show cycle interrupts the resumed stock stream, permit another
bounded recovery for the same token. The phase and minimum interval prohibit
immediate reload churn without a new lifecycle/network transition.

#### Recorded multi-model recovery

Do not claim live packet continuation for multi-model chat. The pinned stock
hydrator cannot attach its `current_run` to assistant panels. After the owned
pre-reload status request proves the session is recorded:

1. reload once to reconcile the current session and remove the active-send
   error view;
2. while the tab is visible, query the bounded recovery-status endpoint until
   `current_run` is absent; and
3. reload once more to render every persisted model response and clear the
   marker.

Status checks exist only for an interrupted, visible recovery. Single-model
recovery uses an initial check and post-reload settling only until stock resume
ownership is observed; multi-model recovery continues polling. Use backoff of
2, 5, 15, 30, and at most 60 seconds,
abort immediately when hidden, pause while offline, retry temporary failures,
and expire at the four-hour marker TTL. Do not create an idle global poller.
Show at most a small
wrapper-owned, accessible same-origin recovery notice; do not imitate or
modify Onyx message nodes. Remove the fallback if upstream gains proper multi-
model live resume.

#### Incognito behavior

The owned pre-reload status request classifies the marked session. If it is
incognito or missing, clear the marker and do not reload; this prevents the
companion's own reload from firing stock `pagehide` teardown. An exact stock
end-session fetch/beacon also clears the matching marker only after the
original operation is invoked. Preserve its call count, result/error, and
immediate teardown. Automatic reconnect is for recorded chats; incognito
retains its stronger teardown contract.

### 3. Durable-stream Compose policy

Add the three literal values from the objective to `api_server.environment` in
the common base Compose file. They apply to both lite and full mode because the
API process writes and resumes chat streams in both. Do not add them to
`background`, `web_server`, nginx, or any Podman overlay.

Document their resource meaning accurately:

- The 32 MiB limit is compressed data per active recorded run, not a reserved
  allocation and not a whole-cache limit.
- Each new chunk receives its own four-hour live TTL, and each flush refreshes
  the metadata TTL. Later writes do not refresh existing chunks, so a run that
  remains active beyond four hours can lose early chunks and produce a gap.
- Completion changes each recorded chunk and metadata key to the one-hour done
  TTL.
- Incognito completion still deletes its buffer rather than retaining it for
  the done TTL.
- Full mode uses the shared Redis cache; lite mode can use Onyx's PostgreSQL
  cache backend. Capacity and cleanup tests must cover the actual selected
  backend rather than assuming Redis-only semantics.
- Eviction, truncation, corruption, or an expired chunk remains a replay gap;
  Onyx falls back to the persisted recorded message instead of replaying an
  incomplete sequence.

Do not change Redis eviction policy, cache persistence, PostgreSQL storage, API
worker count, nginx SSE buffering, or proxy timeouts as part of this work.

## Implementation Sequence

1. Add deterministic tests for the desired Compose, nginx-transform, browser
   state-machine, CSP, and generated-artifact contracts. Confirm they fail for
   the missing implementation.
2. Implement the nginx asset, HTTP/server snippets, and strict startup shim.
3. Implement the browser companion and its dependency-free test hooks.
4. Add the three native Onyx environment settings to common Compose.
5. Render Docker and Podman lite/full models and fix any overlay reset or mount
   incompatibility rather than duplicating common configuration.
6. Update canonical documentation and the README key-patch list.
7. Run deterministic, image, lifecycle, and real-browser validation below.
8. Re-read the final implementation against the exclusions, privacy boundary,
   resource limits, and upgrade-removal checkpoint. Remove any unused fallback
   or compatibility branch.

## Deterministic Test Requirements

Extend existing tests rather than relying on prose assertions.

### Compose and policy tests

Update `tests/test_onyx_network_isolation.py` and/or
`tests/test_onyx_privacy_config.py` to parse effective Compose models and prove:

- Docker lite, Docker full, Podman lite, and the complete Podman macOS full
  overlay set all retain the exact nginx command and read-only mounts.
- The script/config mounts resolve from the repository root in every model.
- No new network, host port, capability, writable mount, socket, secret, or
  environment authority is added to nginx.
- Only `api_server` receives the three exact stream-buffer values in lite and
  full models; `background`, `web_server`, and nginx do not.
- `.env.wrapper.example` contains none of the three internal variables.
- Podman overlays do not reset the common nginx integration or TTL values.
- Existing CSP still includes same-origin script/connect permission and still
  excludes remote script/connect origins, `unsafe-eval`, and `data:` scripts.
- The upstream-generated files under `onyx/onyx_data/` are not tracked as
  wrapper modifications by the implementation.

### Nginx transform and image-contract tests

Add focused tests for `run-nginx-wrapper.sh` using temporary fixture templates:

- exact current template transforms successfully;
- zero or two `server`/`location /`/runner-start markers fail closed;
- a missing script/include/module fails before startup;
- injected directives occur exactly once;
- API and static locations are not modified;
- filenames containing whitespace or shell metacharacters cannot become code;
- repeated preparation does not accumulate another injection; and
- syntax/transform errors propagate as nonzero exits without starting nginx.

Extend `make test-patch-images` or the smallest appropriate pinned-image gate
to verify the selected nginx image exposes `http_sub_module`, render the
transformed configuration with disposable upstream names, run `nginx -t`, and
serve a fixture through it. The fixture must prove:

- gzip-advertising HTML receives one injected tag and is returned correctly;
- non-HTML JavaScript/CSS bytes are unchanged and retain compression
  negotiation;
- API-like JSON, SSE, RSC, downloads, and error responses receive no tag;
- the exact asset path returns the tracked bytes with correct headers;
- an unknown wrapper-asset path does not proxy to another service; and
- CSP headers remain present on normal and error responses.

The image gate must use the selected local image without silently pulling or
substituting another nginx image.

### Companion JavaScript tests

Keep state transitions separable from browser side effects and test the actual
tracked JavaScript, not a Python reimplementation. A small Node harness may run
against the pinned Onyx WebUI image's existing Node runtime so `make test` does
not gain an undocumented host Node dependency. Use fake `window`, `document`,
`location`, `sessionStorage`, lifecycle events, `fetch`, streams, abort
signals, timers, and beacon behavior.

Cover at least:

- unrelated and cross-origin fetches remain byte/identity transparent;
- one exact send creates one sanitized marker and calls the original once;
- prompts, headers, packets, and errors never enter storage or logs;
- clean EOF, HTTP failure, and a pre-aborted send clear only the matching token;
- an in-flight send abort retains an ambiguously accepted marker, while an
  exact stop-session POST calls the original once and clears that marker;
- stream failure retains the marker and propagates the original failure;
- a resumed-body failure followed by stock's `finally` controller abort retains
  the marker and schedules another recovery;
- the stream is forwarded incrementally without cloning, teeing, buffering,
  parsing, reordering, or combining chunks;
- a later send cannot be cleared by completion of an earlier token;
- hidden/visible, persisted `pageshow`, and `online` coalesce to one recovery;
- every ordering of outgoing stream EOF/abort/failure, `visibilitychange`, and
  `pagehide` after an intentional reload leaves its persisted phase unchanged,
  and a same-realm `pageshow` resumes settling without another reload;
- visible-without-hidden and hidden-without-active-send do nothing;
- reload state cannot loop on the next document's initial `pageshow`;
- a second genuine suspension permits another bounded recovery for the token;
- a different current `chatId` is never reloaded or redirected;
- a status response resolved after navigation to another `chatId` cannot
  mutate state or reload that chat, while returning to the marker chat resumes
  the retained recovery;
- `pushState`, `replaceState`, and `popstate` preserve native call behavior,
  schedule nothing without a marker, and resume a retained multi-model recovery
  after client-side navigation returns to the marked `chatId`;
- malformed/expired/version-mismatched storage is discarded;
- persisted recovery self-starts without observing a stock session GET;
- status ownership is token-correlated, aborts on hide, never overlaps, and a
  stale result cannot clear a later send;
- transient HTTP/network/JSON status failures retain state and retry;
- single-model settling stops after a successful stock resume response, while
  no-owner and multi-model polling back off, pause while hidden/offline, stop
  on completion, and expire after four hours;
- a visible send/resume stream failure schedules recovery;
- a successful single-model resume response leaves clean EOF as the completion
  owner even when status reports completion; without that owner, completion
  performs one hydration reload, and a later body failure starts only one
  recovery;
- an `online` event while that successful resume body owns completion schedules
  no status request or reload, and clean EOF remains the completion owner;
- incognito is classified and cleared before any companion reload;
- incognito teardown calls the original fetch/beacon exactly once, preserves
  its return/error behavior, and never schedules recovery; and
- unavailable `TransformStream` or storage failures degrade to a visible/manual
  reload requirement without breaking stock chat or causing duplicate sends.

If executing these tests inside an image is incompatible with the deterministic
`make test` contract, add a focused image target and make `make check-upgrade`
run it. Do not silently skip the companion's executable behavior.

Run `make check` after the deterministic implementation is complete.

## Live and Browser Validation

### Stack matrix

Render and inspect the exact Makefile-selected effective Compose model for:

- Docker lite and full;
- Podman lite and full;
- Podman macOS full overlay composition; and
- enabled Tailscale and onion ingress layers, because all frontend gateways
  must still terminate at the same nginx service.

Perform live startup at minimum with Docker lite and Podman lite. Also run one
live full-mode stack on an available engine to prove the full overlay and cache
backend do not alter frontend recovery. For each live row:

1. start through `make up-lite` or `make up-full` with `CONTAINER_BIN` selected;
2. inspect `make ps-*` and targeted nginx/API/cache logs;
3. run `nginx -t` inside the live nginx container;
4. fetch the document with `Accept: text/html` and gzip advertised, proving one
   injected script and both CSP headers;
5. fetch the companion directly and confirm content type, `nosniff`, and
   `no-store`;
6. verify API SSE responses contain no injected bytes and still have proxy
   buffering disabled; and
7. stop the matching stack through the Makefile.

Podman validation must additionally prove the read-only file mounts work under
rootless UID mapping and macOS bind-mount handling, native startup-health
translation still succeeds, and no Podman-only overlay is required. If one is
actually required, add the narrowest Podman override and document why rather
than changing the Docker model.

### Real-browser scenarios

Use a real Chromium browser plus Safari/iOS when available. Browser automation
may emulate lifecycle/network conditions, but one practical iOS/iPadOS test is
required before claiming that mobile suspension is fixed.

For every scenario, assert the number of send POSTs and backend/provider run
starts. Recovery must never increase either count.

1. **Control:** complete a normal recorded chat. The companion does not reload,
   alter chunks, change rendering, or leave a marker.
2. **No active run:** background and restore the tab. No reload or API polling
   occurs.
3. **Single-model active run:** wait for answer or reasoning packets, suspend
   or terminate only the browser connection while leaving the backend alive,
   restore the tab, and observe one reload followed by stock replay/tailing.
   The conversation must not clear or remain an error node.
4. **Completion while hidden:** allow the backend to finish before restoring
   the browser. One reload renders the persisted complete answer and clears the
   marker.
5. **Repeated suspension:** interrupt the resumed stream a second time. One
   additional lifecycle transition causes one additional recovery, with no
   reload loop or duplicate content.
6. **Offline/online:** take only the client offline, restore connectivity, and
   confirm the online event recovers the same run without a duplicate POST.
7. **Back-forward cache:** exercise `pagehide`/persisted `pageshow`; recovery is
   exactly once and ordinary browser navigation remains functional.
8. **Intentional navigation:** switch to another chat while the marked run is
   active. The companion does not redirect or reload the other chat. Returning
   to the original chat permits reconciliation.
9. **Explicit stop:** cancel from the stock UI. The exact stop-session POST
   clears recovery state, is issued once, and does not become an automatic
   reconnect; later local stream cleanup cannot change that outcome.
10. **Multi-model:** interrupt a recorded multi-model run. The companion does
    not claim live panel replay, uses only recovery-scoped backed-off status
    checks, reloads after completion, and renders every persisted panel once.
11. **Inference plus browser failure:** induce one retryable provider-stream
    interruption handled by the existing inference continuation patch, then
    suspend the browser. The recovered UI shows the same warning/continued
    answer as persisted state and does not halt or clear the conversation.
12. **Incognito:** background and close an incognito tab. The existing teardown
    beacon and server cleanup still run; the companion neither blocks them nor
    resurrects the session. A missing session produces no reload loop.
13. **TTL boundaries:** prove recorded replay remains available beyond the
    upstream-default one-hour live and ten-minute completed windows, then
    expires at the required values using isolated/fake-clock backend tests
    where waiting in real time is impractical.
14. **Capacity boundary:** generate or fixture a buffer above 16 MiB compressed
    but below 32 MiB and prove it remains resumable; above 32 MiB prove Onyx
    marks it truncated, does not exceed the cap, and falls back to persisted
    recorded state.
15. **Ingress origins:** smoke-test localhost and every configured remote
    frontend type available in the environment. The script URL must remain
    same-origin and CSP-clean under Tailscale and onion access.

Inspect console, nginx, API, and cache logs for errors. Logs and storage must
not contain prompts, output chunks, credentials, full request bodies, or
incognito content introduced by the companion.

Run `make test-patch-images` after the nginx/runtime-contract change. Because
this work spans Compose, a pinned support image contract, Onyx behavior, and
live topology, run `make check-upgrade` before handoff. State exactly which
live Safari/iOS, Tailscale, onion, Docker, Podman, lite, or full rows could not
be executed and why.

## Documentation Updates Required During Implementation

Update current behavior in place; do not add release notes, prior-version
catalogs, or an investigation narrative.

- `docs/onyx_patch_info.md`
  - Add a **WebUI recorded-stream recovery** section near the inference retry
    and CSP sections.
  - Document ownership split: provider continuation, backend durable buffer,
    nginx companion injection, and stock WebUI hydration.
  - State single-model, completed-while-hidden, multi-model fallback, explicit
    cancellation, and incognito behavior.
  - Document the strict generated-template transform, same-origin asset, CSP
    interaction, compression selector, storage minimization, and upstream
    removal checkpoint.
  - Add the companion and nginx shim to the maintained wrapper patch inventory.
- `docs/onyx_patches_upgrade.md`
  - Add an Onyx WebUI/nginx reconnect audit checklist naming the upstream files
    and symbols in this plan.
  - Require checking send payload shape, response consumer properties,
    `chatId`, FIFO error behavior, recorded hydration, `current_run`, resume
    cursor/endpoint, multi-model run ids, incognito `pagehide`, CSP, HTML
    compression, generated nginx markers, and nginx module availability.
  - Require executable companion tests, selected-image nginx tests, effective
    Docker/Podman models, and the live interruption matrix on upgrades.
  - State that native upstream reconnect support is a patch-removal gate.
- `docs/resource_minimization.md`
  - Document the per-chunk 4-hour live TTL, 1-hour completed TTL, 32 MiB
    compressed per-run cap, cache-backend distinction, non-reserved/worst-case
    nature, and incognito deletion exception.
  - Document that the companion adds no idle poller; single-model recovery uses
    bounded initial/settling checks until stock resume ownership is observed,
    while multi-model recovery continues polling.
- `docs/internal_network_security.md`
  - Record that the companion is a tracked same-origin asset served directly
    by nginx, makes only same-origin Onyx API requests, adds no route/network,
    and remains constrained by both CSP policies.
  - State that it neither moves browser traffic through container egress nor
    expands SSRF, API, private-network, or final-hop authority.
- `docs/podman_suport.md`
  - Add the read-only nginx asset/config/startup-shim mounts and their rootless
    Podman validation to the compatibility checklist.
  - State whether the common model is sufficient; do not imply Docker evidence
    qualifies Podman.
- `README.md`
  - Add one concise bullet to **Key Patches to Stock Onyx**: recorded chats
    automatically reload and reattach after a suspended/mobile browser loses
    its response stream, without resending the prompt; multi-model recovery
    reconciles after completion, while incognito teardown remains immediate.
  - Keep TTL values and nginx implementation detail out of the README.
- `AGENTS.md`
  - Update only if the implementation introduces a new contributor command,
    validation target, key location, or repository-wide invariant. Otherwise
    leave it unchanged.

Do not make `docs/request_handling.md` the owner of chat-stream reconnection;
that document owns crawler and search request handling. Add only a narrowly
relevant cross-reference there if implementation reveals an actual shared
browser lifecycle invariant.

## Completion Criteria

This plan is complete only when all of the following are true:

- stock WebUI image bytes and hashed chunks remain untouched;
- nginx injects exactly one tracked same-origin companion into HTML and fails
  startup on structural drift;
- the companion never duplicates a send or model run;
- recorded single-model recovery works after real client suspension;
- recorded multi-model recovery settles without pretending to support live
  panel replay;
- incognito teardown and explicit user cancellation are unchanged;
- the three native stream-buffer limits are exact in all effective models and
  absent from `.env.wrapper.example`;
- CSP, ingress, network isolation, and Docker/Podman topology remain intact;
- deterministic, selected-image, Compose, live-stack, and browser tests pass;
  and
- canonical docs and the README key-patch list describe only the resulting
  current behavior.

## Current validation status

`make check` passes all 639 deterministic tests, compile checks, help
validation, and diff checks. `make test-patch-images` passes the selected Onyx
v4.6.5 backend and WebUI, nginx 1.25.5, executor, and derived SearXNG gates. The
reconnect gate includes the executable browser companion's stream, lifecycle,
incognito, startup-bootstrap, retry, stale-token, and overlapping-request
contracts. It also covers the two-model threshold, marker-free History API
transparency, multi-model recovery after client-side route return, the
single-model resume-owner handoff/no-owner fallback, and retryable status
uncertainty. In-flight status completion revalidates route eligibility, and an
`online` event cannot displace an active stock resume owner. The pinned API
bootstrap proves the authenticated recovery-status route is installed exactly
once and ordinary session requests remain untouched. Native stream-buffer
per-chunk TTL/cap/gap/incognito fixtures, nginx module/configuration/serving
behavior, HTML-only compression selection, CSP, and excluded-response checks
pass.
Effective Docker and Podman lite/full models,
including the macOS Podman full overlay, retain the exact common nginx image,
command/read-only mounts, and API-only stream settings. Generated files under
`onyx/onyx_data/` are unchanged.

Docker lite and full start healthy through their Makefile targets and stop
cleanly. The live lite nginx uses the exact selected
`docker.io/library/nginx:1.25.5-alpine` image, passes `nginx -t`, injects one
companion tag into HTML with both CSP policies, and serves the v2 companion
with JavaScript, `nosniff`, and `no-store` headers. The API resume request
remains uninjected and the effective API proxy locations retain
`proxy_buffering off`. `make integration-chat-stream-cache-lite` and
`make integration-chat-stream-cache-full` pass against the selected PostgreSQL
and Redis implementations. They prove native expiry, that later writes do not
refresh older chunk TTLs, completion expiry reset, exact replay, and compressed
capacity gap behavior. The live API process receives the exact `14400`, `3600`,
and `33554432` policy values. Targeted nginx, API, and cache startup logs contain
no reconnect integration error. No matching stack is left running.

Authenticated real-chat interruption scenarios remain unexecuted because this
environment provides neither an authenticated browser session nor repository
browser automation capable of driving the private WebUI without private user
credentials and provider-backed chat activity. This includes the control,
single- and multi-model suspension, completion-while-hidden, repeated
suspension, offline/online, back-forward-cache, intentional-navigation,
explicit-stop, provider-plus-browser interruption, and incognito scenarios.
No Safari/iOS runtime is available for the required mobile test. Podman is not
the selected available engine, and configured Tailscale and onion frontends
are not available for live ingress smoke tests; their effective Compose
models remain covered deterministically.
