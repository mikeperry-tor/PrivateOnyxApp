# Startpage Anubis Proof-of-Work Support

> **Status: deferred.** This is an implementation plan, not current behavior.
> Startpage Anubis pages are explicit CAPTCHA failures and enter the ordinary
> provider-suspension path. They are not solved. The normative current behavior
> is in [Request handling](../../request_handling.md).
> Do not describe any approach below as implemented until its capability gates,
> tests, live validation, and documentation phase all pass and this plan is
> moved to `docs/plans/implemented/`.
>
> **Decision rule:** implement exactly one execution approach. Prefer an
> upstream Obscura release with bounded real workers (Approach A). If the
> selected pin has no suitable implementation, prefer the narrow upstreamable
> Obscura worker patch (Approach B). Use the stack-specific solver (Approach C)
> only after explicitly accepting its Anubis protocol-maintenance obligation.
> Never ship worker execution and the stack solver as fallback paths for each
> other.

## Goal

Allow the custom `startpage2` SearXNG engine to complete a supported Startpage
Anubis proof-of-work challenge presented either before the homepage form or
after its search submission, retain the resulting authorization cookie in the
existing Startpage-only Obscura session, perform the original form POST once
after a homepage challenge or restore it at most once when a result challenge's
GET redirect loses its body, and return a validated result DOM without
weakening any routing, isolation, scheduling, deadline, logging, or failure
contract.

Completion must preserve these properties:

- the complete challenge and continuation remain one `startpage2` engine
  attempt under its existing provider reservation and lease;
- every origin request uses the retained Startpage target, target-owned stealth
  client, cookie jar, selected proxy route, TLS profile, fingerprint seed, and
  connection pool;
- no requests client, constructed result URL, direct-network path, alternate
  browser, or same-engine failure retry is introduced;
- all challenge work consumes only the existing absolute browser transaction
  deadline, capped to the engine window minus outcome-processing headroom;
- successful proof state remains partitioned to `startpage2` and is discarded
  with that provider generation;
- local runtime, protocol, resource, and timeout failures remain distinguishable
  from an explicit provider rejection or renewed challenge; and
- the selected implementation has one resource owner and one time-budget owner.

## Scope and Non-Goals

This plan applies only to:

- the `startpage2` interaction in `searxng/engines/_obscura.py`;
- the provider parser in `searxng/engines/startpage2.py`;
- the shared direct-CDP client in `browser/obscura_client/`;
- the derived Obscura image and patch series under
  `browser/obscura_image/` when Approach B is selected; and
- deterministic, selected-image, and live Startpage validation.

It does not:

- change `open_url`, the stock Onyx crawler, connectors, executors, or local
  document RAG;
- change SearXNG round-robin selection, provider scoring, the one-hour
  suspension duration, or last-resort Bing eligibility;
- turn Anubis into a generic anti-bot bypass framework;
- solve image CAPTCHAs, interaction challenges, fingerprint challenges, or
  Anubis algorithms that have not passed the gates below;
- persist or export cookies, worker state, challenge values, proofs, or tokens;
- add a user-facing timeout, difficulty, worker-count, or solver setting;
- add a fixed five-to-eight-second wait, a second browser deadline, or a fresh
  budget for proof calculation;
- enable the raster renderer or change the no-render `stealth` feature set
  unless a later audited Anubis requirement proves that unavoidable;
- permit repeated proof attempts, repeated search resubmissions, or a fallback
  from one implementation approach to another; or
- modify `reference_repos/`.

## Version and Protocol Scope

This plan targets:

- the derived Obscura v0.2.1 image and matching
  `reference_repos/obscura` checkout;
- SearXNG `2026.7.15-7b2199ecd` and the five custom offline engines;
- Startpage's Anubis v1.25.0 homepage challenge using algorithm `fast`,
  difficulty 4, a main ES module, and four direct same-origin classic workers;
  and
- an Anubis v1.26.2 fixture that prefetches one worker body and creates
  blob-backed workers.

The relevant upstream behavior is:

- the direct-worker profile creates `max(hardwareConcurrency / 2, 1)` workers
  from direct `sha256-webcrypto.mjs` or `sha256-purejs.mjs` URLs;
- the worker searches nonce lanes and reports either progress or a
  `{hash, data, difficulty, nonce}` result;
- the page navigates to
  `/.within.website/x/cmd/anubis/api/pass-challenge` with the challenge ID,
  response hash, nonce, redirect URL, and elapsed milliseconds;
- the server validates `SHA256(randomData + decimalNonce)` and the configured
  count of leading hexadecimal zeroes, sets an authorization cookie, and
  redirects; and
- the blob-worker profile fetches the worker source once, creates a JavaScript
  Blob URL, and falls back to direct worker URLs only when the prefetch/blob
  path fails.

The Startpage challenge is the homepage document itself. It has the exact
Anubis main-module path, version and challenge elements, and visible
`Verifying your request...` marker, but no form or query control. The shared
classifier requires the same-origin main module, both JSON elements, and the
visible `.sp-message` phrase before classifying it as CAPTCHA ahead of form
lookup. Any one of those signals alone is insufficient.

Proof support must use that admitted structure at both the homepage and result
boundaries before beginning continuation work. A missing, duplicate, or invalid
query control on a non-challenge homepage remains a sanitized protocol failure.

## Maintenance Model

The `fast` proof primitive is
`SHA256(randomData + decimalNonce)` with the configured count of leading
hexadecimal zeroes. Keep it independent of worker scheduling, WebCrypto versus
pure-JavaScript selection, challenge-page transport, worker-source layout,
direct versus Blob worker creation, retry and backoff, cleanup, and browser
compatibility.

Treat maintenance as two separate obligations:

1. the small proof primitive, protected by version-independent known vectors
   and expected to change only when an explicitly new algorithm is admitted;
2. exact fixture-backed protocol profiles covering challenge extraction,
   worker suppression or execution, pass fields/path, cookie and redirect
   behavior, and post-pass form/result continuation.

The proof primitive does not justify accepting unknown Anubis releases. Keep
the exact version allowlist and fail closed on a new version until its protocol
profile is audited. A version update requires a fixture and protocol-profile
review; it changes the proof loop only when an explicitly different algorithm
is admitted. Maintenance follows versions served by Startpage rather than
unrelated upstream tags.

Do not rely on the displayed version string alone. At implementation time,
capture sanitized fixture copies or independently generated equivalent pages
for every admitted version and verify the exact main module, worker creation,
proof fields, pass path, cookie behavior, and redirect transaction. Do not
commit a live Startpage response, challenge value, proof, cookie, query, or
provider identifier that is unique to an observed request.

## Current Failure and Why Readiness Alone Is Insufficient

Obscura v0.2.1 has `crypto.subtle.digest`, Blob objects, blob URL bookkeeping,
and a `Worker` compatibility object. Its Worker implementation fetches or reads
the source and evaluates it cooperatively in the page's V8 isolate. It does not
create an independently scheduled worker isolate.

The Startpage challenge launches four SHA-256 loops. Adding its marker as a
pending readiness selector leaves those loops on the page isolate, prevents the
isolate from servicing CDP inspection, and consumes the transaction deadline at
`result-readiness`. Therefore:

- do not implement support by adding only Startpage pending/terminal selectors;
- do not extend `OBSCURA_MODULE_BUDGET_MS` or the script deadline to mask the
  absence of independent worker scheduling;
- do not classify a blocked readiness command as successful proof work; and
- do not assume `networkidle2` covers proof completion or its navigation.

Anubis also creates a new main-document navigation through its pass endpoint.
The existing readiness helper is intentionally a same-document DOM hydration
wait and does not own new loader correlation. Finally, the original Startpage
search is a POST while Anubis redirects with GET. The redirect URL cannot
reconstruct the original request body. A successful proof may therefore need
one new form submission using the already retained query and fixed fields.

## Alternatives and Selection Gates

### Approach A: adopt upstream bounded dedicated workers

Upgrade to an Obscura release that implements genuine dedicated workers and
meets every capability and resource gate in this plan. This is the preferred
long-term result because worker fetch, isolate construction, messaging,
termination, navigation cleanup, and browser semantics stay component-owned.

An upstream version is acceptable only if black-box selected-image tests prove:

1. direct same-origin classic worker source loading;
2. blob-backed classic worker source loading and revocation behavior;
3. worker execution in an independently scheduled isolate so a worker hash loop
   cannot prevent a main-page CDP command from completing;
4. `postMessage`, `message`, `error`, and `terminate()` behavior sufficient for
   both admitted Anubis fixtures;
5. SHA-256 WebCrypto, `TextEncoder`, typed arrays, and timers in worker scope;
6. target/navigation teardown of every descendant worker;
7. target, connection, cookie, Blob URL, and worker-state isolation;
8. same-origin worker fetch through the target's selected stealth client and
   final-hop proxy with no direct fallback;
9. the exact resource ceilings specified below, either natively or through
   narrow wrapper configuration that is startup-validated; and
10. no raster-render feature dependency.

Do not carry a wrapper worker patch into a release that passes these gates.
Remove obsolete patches, tests that assert their source shape, and patch
documentation in the same change.

### Approach B: narrow wrapper-owned Obscura worker patch

If the selected Obscura pin lacks suitable workers, add one ordered patch under
`browser/obscura_image/patches/` and its `series` file. Apply it only through
the verified-source image build. It must be narrow, startup-identifiable,
tested in Rust and through public CDP, and suitable for upstream submission.

The patch implements a bounded dedicated-worker subset, not a source recognizer
or Anubis hash shortcut. The supported public surface is:

```text
new Worker(url)
new Worker(url, undefined)
new Worker(url, {})

worker.postMessage(value)
worker.onmessage
worker.onerror
worker.addEventListener("message" | "error", handler)
worker.removeEventListener("message" | "error", handler)
worker.terminate()

worker global:
  self, globalThis
  addEventListener("message", handler)
  removeEventListener("message", handler)
  onmessage
  postMessage(value)
  close()
  crypto.subtle.digest("SHA-256", BufferSource)
  TextEncoder, TextDecoder
  ArrayBuffer and typed arrays
  setTimeout/clearTimeout and performance.now
  console
```

Source rules:

- accept an absolute or relative same-origin `https:` URL resolved against the
  owning document, or a live Blob URL created in that target;
- treat `.mjs` as a URL suffix only: Anubis calls the classic-worker constructor
  without `{type: "module"}`, and its distributed worker body is bundled;
- reject cross-origin, `http:`, `file:`, `data:`, revoked/foreign Blob URLs,
  module workers, shared workers, service workers, nested workers, non-default
  credentials, and unsupported options with a visible browser-style exception;
- fetch a direct worker through the target's existing stealth client, cookie
  jar, proxy, redirect, TLS, and destination-policy path;
- require every redirect to remain same-origin HTTPS and reject a missing,
  oversized, or non-JavaScript response;
- retain blob bytes once per target and share immutable source bytes among that
  target's workers; never place Blob data in a process-global map; and
- never fall back from a failed Blob worker to a direct URL inside Obscura. The
  Anubis page may make its own documented fallback decision.

Messaging rules:

- support null, booleans, finite numbers, strings, arrays, plain objects,
  ArrayBuffer, and typed-array views;
- structured-clone the message at `postMessage` time and preserve ArrayBuffer
  and view bytes;
- reject functions, symbols, DOM objects, cycles not supported by the selected
  serializer, transfer lists, and objects outside the admitted set with
  `DataCloneError`;
- deliver each accepted message exactly once and in sender order;
- convert uncaught worker exceptions into one sanitized error event without
  worker source, URL query, challenge data, or proof material in wrapper logs;
  and
- make `terminate()` and worker `close()` idempotent and prevent subsequent
  message delivery.

Each worker must have a distinct V8 isolate and event loop. Reusing the page
isolate, evaluating the worker once per incoming message, or converting each
digest into a main-page microtask does not satisfy this approach.

### Approach C: stack-specific Anubis solver

This is the smaller implementation but creates a permanent protocol audit. It
does not execute Anubis workers. Instead, the shared client returns a strictly
validated pending challenge to the synchronous `startpage2` engine thread; that
thread computes the admitted proof; the provider browser owner then resumes
the exact retained CDP target and performs the common continuation transaction.

Keep the proof loop independent of the exact-version protocol profiles. The
loop accepts only the already validated canonical `random_data`, difficulty,
and deadline inputs below; it does not branch on an Anubis version. Version
profiles own extraction, suppression markers, and continuation fields, and
their fixture audits prove that they produce the same canonical solver input.

The solver contract is:

```python
@dataclass(frozen=True)
class AnubisPowChallenge:
    continuation_token: str       # opaque, generation-bound, non-secret log ID forbidden
    version: str                  # exact allowlisted version
    challenge_id: str             # canonical UUID, never logged
    random_data: str              # exactly 128 lowercase hexadecimal characters
    algorithm: Literal["fast"]
    difficulty: int               # inclusive range 1..5

@dataclass(frozen=True)
class AnubisPowSolution:
    response: str                 # 64 lowercase hexadecimal characters
    nonce: int                    # non-negative decimal integer
    elapsed_milliseconds: int     # monotonic elapsed time, non-negative
```

The exact version allowlist is code-owned and fixture-backed. Begin with the
versions proven during implementation; do not use a broad semver range. A new
version must fail closed until its fixtures and protocol audit pass.

The solver:

- runs synchronously on the already allocated SearXNG engine thread, never on
  the shared provider event-loop thread and never in a new process or executor;
- computes `sha256((random_data + str(nonce)).encode("ascii"))` starting at
  nonce zero and accepts the first hash with `difficulty` leading hexadecimal
  zeroes;
- checks the shared browser deadline at least once per 4,096 candidates;
- examines at most 16,777,216 candidates, an exact ceiling of sixteen expected
  search spaces at admitted difficulty 5;
- uses constant-size working memory and emits no progress log containing
  challenge material, nonce, hash, query, pass URL, or cookie;
- returns `pow-exhausted` when the candidate ceiling is reached and the normal
  post-navigation timeout category when the shared deadline expires; and
- does not manufacture a shorter elapsed time or bypass the Anubis test-cookie
  requirement.

The provider browser owner stores the continuation state on its own event-loop
thread under a random opaque token bound to the exact owner generation,
session ID, target ID, frame ID, initial result loader, query, fixed fields,
and request deadline. Only one token may exist for `startpage2`. It is consumed
exactly once by either resume or abort. A token mismatch, generation change,
second resume, or expired deadline closes the generation and returns a typed
protocol failure.

The pending object crosses to the synchronous engine thread only after the
initial challenge fields and origin have been validated. The engine thread
must call resume or abort in `finally`; abandoning a token is a generation-
closing error. Do not expose a raw CDP session, WebSocket, cookie jar, or target
object to the engine module.

Approach C must not coexist with real-worker challenge execution. The client
must prevent the page's Anubis worker attempt from starting so it cannot starve
the page isolate before challenge extraction, race the stack solver, consume
duplicate CPU, or double-spend the challenge. Before the transaction's initial
homepage navigation, install one `Page.addScriptToEvaluateOnNewDocument`
preload owned by the Startpage transaction. Keep it installed through the
homepage classification and, when the homepage is ordinary, the original form
POST classification so it can suppress a challenge at either boundary. Remove
it before the admitted challenge's pass navigation, or immediately after the
terminal result classification when no challenge occurred. The preload wraps,
but does not delete, the native `Worker` constructor. It returns an inert
message-compatible worker only when:

- the resolved same-origin direct worker path is exactly an admitted Anubis
  `/.within.website/x/cmd/anubis/static/js/worker/sha256-*.mjs` path; or
- the constructor receives a target-local Blob URL while the current document
  contains the exact admitted Anubis main-module marker.

Every other Worker construction delegates to the original constructor with
unchanged arguments, prototype behavior, events, and exceptions. The inert
worker accepts the one Anubis start message, performs no hashing, emits no
progress/result, and implements idempotent `terminate()`; it must not expose
the message to Python or logs because the validated challenge JSON remains the
sole solver input. Terminate all inert instances on success, abort, timeout,
pass navigation, target close, or connection close. Failure to install, remove,
or acknowledge this interceptor closes the generation.

This interceptor is itself an Anubis protocol obligation and must be protected
by exact source-shape and black-box tests. If a version cannot be suppressed
without intercepting unrelated workers, do not add it to Approach C's version
allowlist. Do not rely on racing a termination command against an already
running cooperative hash loop.

## Common Provider API and Component Boundaries

Keep `startpage2.search()`'s public engine contract unchanged: it calls
`searxng.engines._obscura.submit_search(...)` once and receives one terminal
DOM or a typed exception. The two-phase objects required by Approach C are an
internal boundary between `_obscura.py`, its provider browser owner, and the
shared client; they never reach SearXNG's generic processor or the provider
parser.

The shared-client API becomes conceptually:

```python
AnubisExecutionMode = Literal["browser-workers", "stack-solver"]

@dataclass(frozen=True)
class PendingAnubisPow:
    challenge: AnubisPowChallenge

SearchTransactionOutcome = SearchSubmissionResult | PendingAnubisPow

async def submit_search(
    query: str,
    *,
    session_owner: SearchBrowserSession,
    spec: SearchInteractionSpec,
    anubis_mode: AnubisExecutionMode,
    # existing policy/deadline arguments remain
) -> SearchTransactionOutcome: ...

async def resume_anubis(
    *,
    session_owner: SearchBrowserSession,
    continuation_token: str,
    solution: AnubisPowSolution,
) -> SearchSubmissionResult: ...

async def abort_anubis(
    *,
    session_owner: SearchBrowserSession,
    continuation_token: str,
) -> None: ...
```

`anubis_mode` is a build-selected constant with exactly one accepted value in
an installed image, not an environment/user setting. Under A/B,
`submit_search` runs proof and continuation internally and never returns
`PendingAnubisPow`; `resume_anubis` and `abort_anubis` are unreachable and may
be omitted from that build. Under C, `_ProviderBrowser` exposes matching
thread-safe `begin_sync`, `resume_sync`, and `abort_sync` operations that submit
these coroutines to its existing event-loop thread. `_obscura.submit_search`
performs begin, synchronous engine-thread proof calculation, and resume under
one outer `try/finally`, then returns only the final DOM. Do not add these
operations to the provider engine module or expose them over HTTP/CDP.

`SearchSubmissionResult` must carry the actual terminal loader's URL, status,
headers, rendered DOM, challenge classification, and cumulative stage timings.
Add structured timings for proof and continuation rather than redefining the
existing submission duration. Timing fields are monotonic diagnostic values;
none grants or enforces a budget.

Refactor the interaction specification so same-document hydration and
multi-document verification cannot be confused:

```python
@dataclass(frozen=True)
class DomReadinessSpec:
    pending_selector: str
    terminal_selector: str

@dataclass(frozen=True)
class AnubisVerificationSpec:
    marker_selector: str
    visible_selector: str
    visible_text: str
    version_element_id: str
    challenge_element_id: str
    main_script_path_prefix: str
    pass_path: str
    admitted_algorithms: frozenset[str]
    result_terminal_selector: str
    no_results_selector: str

@dataclass(frozen=True)
class SearchInteractionSpec:
    # existing homepage/form/origin fields remain
    dom_readiness: DomReadinessSpec | None = None
    anubis_verification: AnubisVerificationSpec | None = None
```

`duckduckgo2` owns one `DomReadinessSpec`. `startpage2` owns one
`AnubisVerificationSpec`. No provider may configure both without a separate
review and a deterministic precedence contract.

The Startpage specification must require:

- exact HTTPS/default-port host `www.startpage.com` or `startpage.com` under the
  existing host policy;
- a script source whose decoded path begins with
  `/.within.website/x/cmd/anubis/static/js/main.mjs`;
- visible `.sp-message` text equal to the normalized case-insensitive phrase
  `Verifying your request`;
- bounded JSON values from the exact `anubis_version` and `anubis_challenge`
  elements;
- pass path exactly
  `/.within.website/x/cmd/anubis/api/pass-challenge`;
- the existing organic-result, explicit-no-results, legacy CAPTCHA, and Anubis
  selectors as terminal classifications; and
- one original search form POST containing `query`, `cat=web`, and optional
  `page`, with the existing duplicate/conflict rules.

The shared client owns structural detection before returning a result because
it alone can continue the retained target. `startpage2.py` retains the same
provider-specific Anubis detector as defense in depth for any explicit
verification DOM returned after continuation. Do not make both layers perform
proof work or suspension recording: the client owns transaction execution;
the engine parser owns final provider-specific classification; the SearXNG
offline processor remains the only suspension owner.

## Common Transaction State Machine

The ordinary Startpage transaction remains:

```text
H0  homepage GET and completed validated document
R0  original form POST and completed validated result document
```

When `H0` is an admitted Anubis page, continue within the same request deadline
before looking for a form:

```text
H0 challenge
  -> proof work
  -> C0 JavaScript or client navigation to exact pass path
     (server redirect chain remains in C0's loader)
  -> one validated Startpage homepage form
  -> R0 one original authorized form POST
  -> terminal result/no-results/challenge DOM
```

When an ordinary `H0` supplies the validated form but `R0` is an admitted
Anubis page, continue within the same request deadline:

```text
H0 -> R0 challenge
       -> proof work
       -> C0 JavaScript or client navigation to exact pass path
          (server redirect chain remains in C0's loader)
       -> either terminal result DOM
          or one validated Startpage form page
             -> R1 one authorized restoration POST
             -> terminal result/no-results/challenge DOM
```

Only one challenge may be solved in one engine attempt. A homepage challenge
uses at most `H0`, `C0`, and `R0`; a result challenge uses at most `H0`, `R0`,
`C0`, and `R1`. Redirect responses within a loader do not increase this count.
Any additional challenge, loader beyond the boundary-specific maximum, popup,
child-frame result, same-document substitute, cross-origin navigation, or
second search submission is a terminal explicit challenge or protocol failure
as specified below and never starts a second proof.

For every loader the shared client must correlate main-frame
`Network.responseReceived`, frame/loader identity, completion, terminal URL,
status, headers, and bounded DOM. It must validate HTTPS/default-port/exact
allowed host at each terminal response. `C0` must begin at the exact pass path;
its redirect chain must remain on an allowed Startpage host. Query strings are
never used to admit a route.

After `C0` for a homepage challenge:

- require exactly one form satisfying the original Startpage homepage form
  contract and no result or challenge terminal;
- submit the retained original query and fixed fields once through the same
  native setter/event/requestSubmit path to create `R0`; and
- accept terminal results/no-results, but return a renewed challenge without a
  second proof or submission.

After `C0` for a result challenge:

- if the bounded DOM contains organic results or the explicit no-results
  marker, return it without resubmission;
- if it contains exactly one form satisfying the original Startpage form
  contract and no result/challenge terminal, restore the original query and
  fixed fields through the same native setter/event/requestSubmit path and
  create `R1`;
- if it contains another explicit challenge, return that challenge for final
  provider classification without another proof attempt;
- otherwise return a typed parser/protocol failure; and
- if `R1` contains another challenge, return it as the terminal explicit
  challenge and do not solve, resubmit, or navigate again.

The original query and fixed fields remain request-local client state. Do not
derive them from `redir`, history, challenge JSON, a constructed result URL, or
the post-challenge page. The first POST after a homepage challenge is the
original search submission; the restoration POST after a result challenge is
challenge continuation, not a failure retry. Update canonical documentation to
say that a successful Startpage challenge can add one pass navigation and,
depending on its boundary, one original or restoration POST while every other
provider keeps its current stage count.

## Exact Ownership and Enforcement Model

| Concern | Sole owner | Required behavior |
| --- | --- | --- |
| Provider reservation, same-provider serialization, round-robin rotation, cooldown admission, suspension recording | Existing SearXNG wrapper patch and offline processor | Hold the existing `startpage2` lease across proof, continuation, parser outcome, and suspension. Do not add these concepts to the shared client or Obscura. |
| Engine window | SearXNG engine configuration | Keep the existing 60-second window. Rotation gives another provider its own normal window; Anubis does not. |
| Browser transaction deadline | `searxng/engines/_obscura.py` computes it; shared client enforces it | Keep nominal 50 seconds capped to remaining engine time minus exactly 1 second of outcome-processing headroom. H0, R0, proof, C0, optional R1, all DOM/event commands, and worker termination consume this one deadline. |
| Setup sub-deadline | Shared client | Keep the existing cumulative 45-second ceiling inside the browser deadline. Challenge continuation creates no new setup phase. |
| Cleanup command bounds | Shared client | Keep the existing five-second command and WebSocket-close bounds outside the transaction deadline. A failed worker/continuation cleanup taints and closes the generation. |
| Challenge field and protocol validation | Shared client using the provider's immutable `AnubisVerificationSpec` | Admit proof work only after exact structural, origin, path, type, size, algorithm, and selected-approach checks. |
| Worker source, isolate, message, and concurrency limits for A/B | Obscura | Enforce the exact worker limits below. Python and SearXNG must not add duplicate worker counters or timers. |
| Solver candidate and difficulty limits for C | Shared solver called on the SearXNG engine thread | Enforce the exact solver limits below. Obscura must not also solve or meter the proof. |
| Cookie acceptance and delivery | Existing retained Obscura target cookie jar | Do not export, inspect, copy, clear, or separately validate the authorization cookie. A renewed challenge is the behavioral failure signal. |
| Target/generation lifecycle | Shared client `SearchBrowserSession` | Reuse on unambiguous terminal success/block; close immediately on ambiguous protocol, worker teardown, event, or transport failure; retain the existing one-hour idle expiry otherwise. |
| Final provider DOM parsing | `startpage2.py` | Parse results/no-results and defensively classify explicit Startpage/Anubis challenge DOM. It never runs proof work. |
| CAPTCHA/access/rate-limit suspension | SearXNG offline processor | Preserve the existing 3,600-second values and record only typed blocking outcomes returned by the engine. |
| Destination DNS and routing | Existing Obscura target client, bridges, and final-hop proxy | Worker source, pass, redirect, and restoration requests follow the selected no-VPN/VPN/proxy/Tor route with no fallback. |

### Worker resource limits for Approaches A and B

These are fixed reviewed implementation values, not environment variables:

- at most **4 live dedicated workers per target**;
- at most **4 live dedicated workers in the Obscura process**;
- no worker-capacity queue: a constructor exceeding either cap throws a
  browser-visible `QuotaExceededError` immediately;
- at most **1 MiB decoded source bytes per worker source**;
- at most **64 KiB serialized bytes per `postMessage` payload** in either
  direction;
- at most **32 MiB V8 old-generation heap per worker isolate** using the
  runtime's hard isolate heap limit;
- no nested workers and no transfer-list ownership movement; and
- all four process permits are released synchronously as part of acknowledged
  terminate, worker close, owning-realm navigation, target close, or connection
  close.

The four-worker ceiling matches the admitted Startpage page under Obscura's
current stable `hardwareConcurrency=8` profile. Do not lower the advertised
profile merely to reduce proof work; that would change the target fingerprint.
Do not add a separate worker wall timer: the shared browser transaction
deadline is the only proof wall-time authority. On its expiry, the client
closes the target generation, and Obscura's target teardown must interrupt and
join every worker before acknowledging closure.

The heap limit bounds V8-managed old-generation memory, not total worker RSS,
Rust allocations, stacks, or networking. Documentation and tests must not claim
an aggregate byte-perfect process-memory bound. Selected-image stress tests
must nevertheless demonstrate that four admitted workers and repeated teardown
do not grow live isolate/thread counts or retained source/message state.

### Solver resource limits for Approach C

Approach C has exactly one proof loop because the existing `startpage2` lease
allows one engine attempt. It adds no executor, process, thread pool, semaphore,
or worker counter. Its only resource ceilings are difficulty 5, 16,777,216
candidates, 4,096-candidate deadline checks, fixed-size challenge fields, and
the existing browser deadline. Do not apply the four-worker or V8 heap limits
to this approach.

## Failure and Suspension Semantics

Use existing typed shared-client failures where they fit. Add a sanitized
non-blocking parser/runtime reason only if the current types cannot distinguish
`pow-exhausted` or unsupported local continuation state without exposing
details.

| Outcome | Classification | Generation | Suspension |
| --- | --- | --- | --- |
| Initial page matches exact supported Anubis structure and completes successfully | Continue internally | Retain | None |
| Pass endpoint rejects the submitted proof, authorization cookie is ineffective, or terminal page is another explicit challenge | CAPTCHA | Retain until ordinary one-hour blocked-session expiry ordering | 3,600 seconds by existing processor |
| Explicit Anubis page uses unsupported version, algorithm, difficulty, or required browser feature | CAPTCHA/unsupported verification | Retain under ordinary blocked ordering | 3,600 seconds |
| Worker constructor/runtime/error event, worker quota, source-size/message-size rejection, or acknowledged local worker failure | Protocol/runtime failure, not CAPTCHA | Close immediately as ambiguous | None |
| Solver reaches its candidate ceiling | `pow-exhausted` parser/runtime failure, not CAPTCHA | Close immediately | None |
| Existing browser deadline expires during proof or continuation | Existing post-navigation timeout with exact stage | Close immediately | None |
| Cross-origin/path violation, extra loader, second restoration, token mismatch, or malformed continuation fields after initial admission | Protocol/policy failure | Close immediately | None unless the returned DOM independently proves an explicit challenge |
| Valid terminal result/no-results DOM | Success/no-results | Reusable | None |

Do not convert local implementation failures into CAPTCHA merely because an
explicit challenge was the trigger. Conversely, do not call a renewed or
server-rejected explicit verification page a parser mismatch to avoid normal
provider suspension.

## Security and Privacy Requirements

- Challenge ID, random data, proof hash, nonce, elapsed time, pass URL query,
  test cookie, authorization cookie, original query, form fields, page text,
  worker source, and response body are secret request data. Never include them
  in wrapper logs, exceptions, metrics, test snapshots, or committed fixtures.
- Logs may include only engine name, opaque existing request correlation ID,
  selected approach, sanitized stage, admitted version, numeric difficulty,
  worker count, status class, and elapsed duration. Do not log the opaque
  continuation token from Approach C.
- Challenge JSON and worker source are attacker-controlled. Apply size/type
  validation before allocation, parsing, hashing, isolate creation, or URL
  construction.
- Blob URLs and worker registries are target-local. A provider, target,
  connection, or `open_url` caller must never resolve another target's Blob URL
  or receive its messages.
- Worker network fetches must use the owning target's client and destination
  policy. A worker runtime receives no file access, environment variables,
  host sockets, private mounts, direct DNS, or alternate HTTP client.
- The pass and following original/restoration submission stages must reuse the
  same target so the server sees the same User-Agent, public route identity,
  fingerprint, cookies, and HTTP client state that received the challenge.
- Do not export/import cookies or copy the proof into a fresh browser context.
- A route failure, Tor circuit failure, VPN failure, bridge failure, worker
  source fetch failure, or Obscura failure remains closed. There is no switch
  to the stack solver, Chromium, requests, or direct egress.
- Preserve the current unprivileged, read-only, capability-free Obscura
  container and no-private-mount contract.

## Implementation Phases

### Phase 0: refresh capability evidence and select one approach

1. Record current pins from `stack.versions.env` and inspect the exact Obscura
   and Anubis sources.
2. Repeat a sanitized live Startpage-only probe from an allowed test route and
   record only version, algorithm, difficulty, source mode, stage timings, and
   terminal classification.
3. Test the selected Obscura image for independently scheduled direct/blob
   workers and the exact resource gates.
4. Choose A, B, or C in the implementation review. Update the status block with
   the accepted decision before changing behavior.
5. Do not implement or retain code/tests for an unselected alternative.

### Phase 1: common interaction and transaction model

1. Separate `DomReadinessSpec` from `AnubisVerificationSpec` and migrate the
   existing DuckDuckGo selectors without behavior change.
2. Add Startpage's immutable verification specification and strict constructor
   validation.
3. Generalize event accounting to admit the exact homepage-challenge
   `H0 -> C0 -> R0` or result-challenge `H0 -> R0 -> C0 -> optional R1`
   state machine while preserving two stages for ordinary providers and four
   for Bing's existing pagination transaction.
4. Make returned URL, status, headers, loader, and DOM come from the actual
   terminal result loader, never the initial challenge loader.
5. Add one form-submission operation that reuses the original query/fixed
   fields and can be invoked only after a successful admitted `C0` transition,
   as the original POST after a homepage challenge or restoration after a
   result challenge.
6. Keep the request deadline object immutable and shared through every stage.
7. Taint and close on event ambiguity or failed continuation cleanup.

### Phase 2A: upstream worker adoption

1. Upgrade Obscura through the documented component-upgrade flow.
2. Remove any now-obsolete wrapper compatibility code only after focused
   capability tests pass.
3. Configure or narrowly patch the exact fixed worker resource ceilings if the
   upstream release does not provide equivalent fail-fast limits.
4. Add the selected-image worker fixtures and transaction integration.

### Phase 2B: wrapper worker patch

1. Add Rust worker registry, isolate lifecycle, target-local Blob source
   registry, bounded message serializer, and target/client fetch bridge.
2. Thread worker ownership through page realm, navigation replacement, target
   close, and connection close.
3. Add a static patchset marker/version and startup validation.
4. Add Rust unit/integration coverage before wiring Startpage continuation.
5. Apply the patch with `git apply --check` in the existing verified-source
   builder and keep build tools out of the runtime image.

### Phase 2C: stack-specific solver

1. Add strict challenge extraction and the generation-bound pending/resume API.
2. Install the scoped pre-document Anubis Worker interceptor before `H0`, keep
   it through at most `R0`, then remove it and acknowledge inert-instance
   cleanup after challenge or terminal classification.
3. Implement the single-thread solver and exact limits in the shared package.
4. Resume only through the provider browser owner's event loop and consume the
   token once.
5. Add the same common pass/redirect/restoration transaction; only proof
   execution differs from A/B.

### Phase 3: classification, cleanup, and scheduling integration

1. Preserve the provider lease through final parser classification and
   suspension recording.
2. Map local worker/solver failures according to the table above.
3. Keep renewed/rejected Anubis pages in `startpage2.py`'s explicit CAPTCHA
   detector.
4. Verify the one-hour provider target is reusable after success and expires
   before readmission after a blocking outcome.
5. Confirm the three-second provider-start cooldown has one owner. It applies
   to the attempt's initial homepage authorization and to Bing's separately
   documented page-two transaction; it is not re-stamped for Anubis's internal
   pass navigation or the one restoration POST.

### Phase 4: canonical documentation and plan disposition

Complete every required documentation update listed below, remove obsolete
current-behavior language, record validation evidence in the implementation
review, and move this file unchanged in substance to
`docs/plans/implemented/anubis_pow_support.md`. Do not append a progress diary.

## Deterministic Test Criteria

All deterministic tests must be networkless and contain generated challenge
values, not captured live secrets.

### Common tests required for every selected approach

- strict `AnubisVerificationSpec` construction, selectors, exact host/path,
  JSON type/size checks, and rejection of malformed or ambiguous pages;
- the exact admitted structure and visible phrase classify an Anubis homepage
  before form lookup, while either signal alone does not;
- DuckDuckGo same-document readiness remains behaviorally unchanged;
- ordinary Startpage success/no-results retains `H0 -> R0` only;
- a homepage challenge follows `H0 -> C0 -> R0`, never attempts form lookup
  before proof, and never performs a restoration POST;
- supported challenge result directly after `C0` and result after exactly one
  `R1` restoration;
- original POST query and fixed fields are submitted after a homepage challenge
  or restored after a result challenge exactly once without using the redirect
  URL as their source;
- updated terminal URL/status/headers/loader/DOM come from the real terminal
  loader;
- cross-origin redirect, non-default port, wrong pass path, popup, child frame,
  fifth loader, second restoration, and challenge recurrence;
- expiry before proof, during C0, and during R1 uses the one existing absolute
  deadline and leaves outcome headroom computation unchanged;
- no fixed readiness allowance or reset deadline appears;
- provider lease held through continuation, parse, and suspension; another
  Startpage attempt cannot overlap, while another provider can progress;
- successful cookie continuity in the same synthetic target and no cookie
  export, clear, or cross-provider sharing;
- local runtime failures are non-blocking typed failures; renewed/rejected
  explicit challenge maps to CAPTCHA and the existing one-hour suspension;
- all errors and logs omit query, challenge, proof, nonce, pass query, cookies,
  worker source, page text, and markup; and
- ambiguous cleanup closes and discards the retained generation without
  replaying the query.

### Additional tests only for selected Approach A or B

- direct and blob classic workers execute outside the main isolate while CDP
  remains responsive during a long hash loop;
- relative URL resolution, same-origin redirects, target-client cookie/proxy
  use, MIME/source limit, revoked/foreign Blob rejection, and no direct fetch;
- four workers succeed; the fifth per-target or process-global worker fails
  immediately without a queue;
- 1 MiB source, 64 KiB message, and 32 MiB old-generation heap limits at their
  boundary and one unit beyond it;
- message clone timing, ordering, accepted data types, rejected types, error
  events, idempotent close/terminate, and no delivery after termination;
- navigation, target close, connection close, timeout, and winning-worker
  cleanup release every process permit and join every isolate/thread;
- repeated challenges do not increase live thread/isolate/source/blob/message
  counts; and
- no raster renderer or file-access capability is introduced.

Approach A may satisfy Rust-level coverage in upstream, but this repository
still requires black-box selected-image tests for every boundary it relies on.
Do not copy upstream's complete test suite.

### Additional tests only for selected Approach C

- exact known SHA-256 vectors and first-valid-nonce behavior;
- difficulty 1 through 5, malformed data, uppercase/odd/non-hex data,
  unsupported algorithm/version/difficulty, and noncanonical UUID;
- deadline checks no less often than every 4,096 candidates;
- exact 16,777,216-candidate exhaustion without an off-by-one;
- proof execution occurs on the engine thread while another provider's CDP
  coroutine progresses on the shared event loop;
- one pending token, exact generation binding, one resume/abort, rejection of
  replay/mismatch/expiry, and mandatory finally cleanup;
- the pre-document interceptor suppresses admitted direct and Blob Anubis
  workers, delegates unrelated workers unchanged, is removed before `C0`, and
  cannot race or double-spend the proof; and
- no worker-patch tests, worker resource settings, or alternate solver process
  are added.

## Selected-Image and Build Criteria

For Approach A or B, extend `make test-obscura-image` so the selected local
image proves the worker capability and fixed resource contract without network,
pulling, or image substitution. The test must use public CDP behavior and an
isolated fixture origin reached through the normal test network. For Approach B
it must also verify exact patch-series application and the updated patchset
startup marker.

For every approach, extend `make test-patch-images` only for the SearXNG/shared-
client/parser behavior that actually needs pinned image dependencies. Keep
pure state-machine and solver tests in `make test`. Do not add Approach A/B
image tests when C is selected, and do not run Tor or OpenSearch image gates for
this focused change.

The derived SearXNG image tag must change when shared-client or engine inputs
change through the existing Makefile digest mechanism. Update
`stack.versions.env` only if a selected component pin, source revision,
archive hash, builder image, or committed version value changes; do not edit it
merely because a wrapper patch was added.

## Live Validation Criteria

Use benign queries and sanitized logs. Run at least:

1. `make check`;
2. `make test-obscura-image` for A/B, or omit it for C unless another Obscura
   image contract changed;
3. `make test-patch-images`;
4. effective Docker and Podman lite/full Compose rendering, confirming no new
   network, mount, capability, replica, worker-process, or user-facing setting;
5. `make up-lite` with explicit no-VPN, then health and targeted SearXNG,
   Obscura, gateway, bridge, and final-hop logs;
6. a live Startpage challenge at the served homepage or result boundary that
   completes proof, obtains results, and reuses the same provider session on a
   subsequent query;
7. a forced unsupported/rejected challenge that suspends Startpage and rotates
   only through existing SearXNG behavior;
8. a local proof/worker failure that is unresponsive but does not suspend the
   provider;
9. concurrent Startpage and another-provider requests proving same-provider
   serialization and different-provider progress;
10. for A/B, four-worker saturation, fifth-worker fail-fast behavior, main-page
    CDP responsiveness, and complete worker cleanup after success and timeout;
11. a route interruption during worker fetch/pass/restoration proving no
    direct fallback and generation invalidation; and
12. one high-latency supported route—native Tor preferred, otherwise the
    configured VPN/upstream route—proving the challenge uses only the remaining
    engine window and preserves outcome-processing headroom.

Full-mode startup need not repeat document ingestion, embedding, OpenSearch,
or RAG tests because this change does not touch those paths. It must still
render cleanly and preserve the same SearXNG/Obscura topology. Run a real
Podman stack only if implementation changes Compose, mounts, health, lifecycle,
or engine-specific behavior; otherwise deterministic Podman rendering is
sufficient under the existing compatibility policy.

Record the observed Anubis version, algorithm, difficulty, challenge boundary,
selected approach, route class, stage timings, terminal classification, and
whether restoration was required. Do not record the query, challenge ID/data,
proof, nonce, cookie, pass URL, exit IP, or response body. If Startpage does not
offer a challenge on an available route, record the omitted live row; do not
weaken deterministic fixtures or manufacture a production challenge endpoint.

## Required Documentation Updates at Completion

The implementation is incomplete until these canonical documents are updated:

- `docs/request_handling.md`: replace the current unsupported-flow paragraph
  with the selected execution owner, exact `H0/R0/C0/R1` transaction, deadline,
  classification, cookie continuity, stage count, and no-fallback behavior.
- `docs/onyx_patch_info.md`: document an adopted upstream worker capability or
  the exact new Obscura patch; document the shared-client continuation API and
  any new strict startup markers. Approach C must instead document its solver
  and pending/resume boundary without implying browser Worker support.
- `docs/onyx_patches_upgrade.md`: add an explicit removal/re-audit checkpoint
  for the selected approach, every admitted direct/blob fixture family, any
  explicitly unsupported family, resource limits, transaction restoration,
  failure mapping, and the live Startpage check.
- `docs/resource_minimization.md`: document either the four-isolate global and
  per-target worker ceilings plus lifecycle cleanup (A/B), or the one in-thread
  bounded solver and absence of new workers/processes (C).
- `docs/internal_network_security.md`: record worker-source/pass/restoration
  destination authority, target-local state, and absence of a direct or
  cross-context path.
- `docs/vpn_routing_and_proxies.md`: state that every Anubis subrequest uses the
  selected final-hop route and fails closed. Do not duplicate implementation
  or time-budget detail there.
- `README.md`: update only the user-visible Startpage/search reliability and
  blocking behavior. Keep worker/protocol internals in subsystem docs.

Conditionally update:

- `stack.versions.env` when Approach A changes the Obscura pin/source/build
  metadata or another committed pin changes;
- `docs/native_tor_support.md` only if the implementation changes Tor-specific
  timeouts, lifecycle, routing, or diagnostics rather than merely validating
  the existing route;
- `docs/podman_suport.md` only if Compose, mounts, lifecycle, health, or
  engine-specific compatibility changes; and
- `.env.wrapper.example` only if a separately reviewed user-facing control is
  introduced. This plan specifies none.

Do not revise unrelated historical files under `docs/plans/implemented/`.
After completion, move this plan to `docs/plans/implemented/` and ensure its
status and selected-approach decision accurately describe the reviewed result;
lasting behavior must remain canonical in the documents above.

## Completion Criteria

This plan is complete only when:

- exactly one approach is present and no runtime fallback can invoke another;
- every common and selected-approach test passes;
- the selected local images pass their focused gates without pulling or
  substitution;
- live Startpage proof succeeds when the provider offers an admitted challenge;
- POST restoration is bounded to one and event-accounted;
- the one existing browser deadline and one SearXNG suspension owner remain
  authoritative;
- resource limits are enforced at their specified sole owner;
- local implementation failures do not become provider suspensions;
- renewed/rejected explicit verification does become the ordinary blocking
  outcome;
- cookies, workers, Blobs, proof state, and browser state remain provider/
  target/connection isolated;
- route interruption remains fail-closed;
- diagnostics contain none of the prohibited values; and
- all required documentation has been updated and this plan has moved to the
  implemented directory.

If any selected approach requires a second deadline owner, a second provider
retry owner, an unbounded worker/process pool, cookie export/import, direct
network access, or a fallback implementation, stop and revise the design
before implementation.
