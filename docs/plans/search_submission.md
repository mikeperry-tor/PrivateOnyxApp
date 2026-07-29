# Homepage-First SearXNG Search Submission Plan

> **Status: planned.** This document specifies intended behavior, not current
> runtime behavior. The five custom SearXNG engines currently navigate directly
> to provider result URLs. Current normative behavior remains in
> [Request handling](../request_handling.md) until this plan is implemented,
> validated, documented there, and moved to `docs/plans/implemented/`.
>
> This plan is written as an implementation handoff. An implementer must verify
> the committed component pins and the matching `reference_repos/` source
> identities before changing code. If a pinned provider page or Obscura CDP
> capability differs from the contracts below, stop and revise the plan rather
> than adding a fallback or weakening an assertion. This plan deliberately
> carries two narrow build-time patches against pinned Obscura v0.1.11; any
> source-shape drift is a build failure and an upgrade-review gate.

## Decision

Convert all five wrapper-owned search providers—Google, Brave, DuckDuckGo
No-AI, Startpage, and Bing—from direct result-URL navigation to one
homepage-first browser transaction:

```text
retained target in the provider's retained Obscura connection
  -> navigate the provider homepage
  -> validate the completed homepage document
  -> populate the homepage's search control
  -> submit that control's owning form
  -> validate the completed result document
  -> return the bounded rendered result DOM to the provider parser
  -> retain the target until provider-session expiry
```

This is one SearXNG provider attempt containing exactly two main-document
navigation stages. It is not a retry and must never fall back to the former
direct result URL.

Homepage-first submission is mandatory for every provider after cutover.
There is no direct-versus-homepage provider strategy setting. Query text entry
has two implementations:

- **instant entry**, the default, assigns the complete query through the
  control's native value setter and emits the minimum input events required by
  the page; and
- **timed key entry**, explicitly selected per provider through
  `SEARXNG_TIMED_TYPING_PROVIDERS`.

Navigation, form discovery, fixed-field application, submission, lifecycle
observation, and result extraction are identical for the two entry modes.
Timed entry must therefore be independently selectable without creating a
second navigation implementation.

Do not call timed key entry “human,” “human-like,” or an anti-bot bypass in
code, configuration, logs, or normative documentation. CDP can generate timed
key events, but providers can classify the connection using IP reputation,
TLS and browser fingerprints, cookies, JavaScript behavior, request history,
and other signals. Timed entry is an experimental request-shape option.

## Motivation and live baseline

A live full-stack investigation on 2026-07-29 established that the pinned
Obscura release accepts the raw CDP commands needed for this design:
`Runtime.evaluate`, `Input.dispatchKeyEvent`, and
`Input.dispatchMouseEvent`. The successful prototype did not require
Playwright or a second CDP attachment.

For the query `private onyx obscura browser`, the existing direct engines and a
temporary homepage-first timed-entry probe produced:

| Provider | Direct result-URL behavior | Homepage-first observed behavior |
| --- | --- | --- |
| Google | HTTP 429 | Google `/sorry/` CAPTCHA / unusual-traffic flow |
| Brave | 20 result cards | 20 result cards |
| DuckDuckGo No-AI | unfinished verification preload | the same unfinished verification preload |
| Startpage | CAPTCHA | 10 result cards |
| Bing | 10 broad results dominated by the first query term | 2 narrower result cards, including an Onyx browser result |

These are capability and design signals, not reliability claims. In
particular:

- Google reaching `/sorry/` is a distinct provider classification from the
  persistent direct HTTP 429, but is still a blocking result.
- Startpage's improvement may have come from provider-generated homepage
  state, cookies, or hidden form values rather than timed key cadence.
- Bing's homepage form used provider defaults in the prototype, whereas the
  deployed engine explicitly sets `adlt=off` and `setlang=en`. The
  implementation must preserve those current engine parameters before drawing
  a quality comparison.
- DuckDuckGo's deep preload remained a typed CAPTCHA condition.

The prototype also exposed failure modes that this plan must handle rather
than encode as one-off provider workarounds: a textarea accepted Enter as a
newline, Startpage replaced a control after a mouse interaction, one
submission acknowledgement became ambiguous while navigation proceeded, and
Obscura logged V8 watchdog overruns on some pages.

### Pinned Obscura state-lifetime audit

The implementation must distinguish four lifetimes. They are not
interchangeable:

| State | Pinned Obscura v0.1.11 scope | Required search scope |
| --- | --- | --- |
| CDP WebSocket and isolated `BrowserContext` | One context per WebSocket | One retained owner per provider |
| Cookie jar and ordinary `ObscuraHttpClient` | Browser context / WebSocket | Retain until the provider owner is cleared |
| Browser profile selection | Selected when the browser context is constructed | Stable for the provider owner |
| Stealth `wreq::Client` and its connection pool | One `Page`, therefore one CDP target | Retain the same target for the provider owner |

Consequently, retaining only the CDP WebSocket while creating and closing one
target per query preserves cookies and the context profile, but destroys the
stealth HTTP connection pool after every query. That is not sufficient for this
plan. Each provider owner must retain one WebSocket **and one target** across
clean attempts. The two stages of a query and later queries for that provider
reuse that target. A new main-document navigation resets Obscura's JavaScript
realm; it does not require a replacement target.

The current one-hour clearing mark is a sliding **idle** deadline measured from
completion of the last provider attempt, not an absolute one-hour maximum age.
At that deadline, close the retained target and then the WebSocket. The next
attempt constructs a fresh WebSocket/context/target generation. A transport,
protocol, submission, event-accounting, DOM, or cleanup ambiguity invalidates
and closes the generation earlier; retaining ambiguous browser state is not
allowed merely to improve continuity.

The current shared client sets WebSocket `ping_interval=None`, and pinned
Obscura has no application-level CDP idle deadline. On the direct internal
Compose bridge, a quiet provider WebSocket therefore remains open until the
owner closes it or a real transport/service failure occurs. Preserve that
behavior: do not add per-query disconnects, target detach/reattach, periodic
CDP traffic, or a new intermediary idle policy. If the WebSocket dies
naturally, discard the generation; never reconnect and replay the in-flight
query.

The selected Compose model enables `--stealth` and does not set
`OBSCURA_ROTATE_PROFILE` or `OBSCURA_PROFILE`. Obscura therefore uses its stable
default profile. This plan must not add `OBSCURA_ROTATE_PROFILE`, per-query user
agent changes, `Network.setUserAgentOverride`, or other fingerprint rotation.
The JavaScript realm can be recreated on navigation while the advertised
profile, TLS emulation, cookie jar, and owning stealth client remain stable.

Pinned Obscura nevertheless reseeds its broader JavaScript fingerprint on every
navigation from wall-clock time and `Math.random()`. Screen dimensions, GPU,
canvas/audio values, hardware concurrency, device memory, battery, and other
seed-derived surfaces can therefore change between the homepage and submitted
result in one query. This plan patches the seed to target scope. Generate one
unpredictable nonzero 32-bit seed when `Page::new` creates a target, store it
privately on that `Page`, and inject the same value into every new JavaScript
realm created by navigation on that target. A replacement target gets a new
seed. Do not persist, configure, expose through CDP, or log the seed.

Target scope is intentional. The provider target, cookie-bearing WebSocket
context, and target-owned stealth client are retained together until the
one-hour idle clear, while request-scoped `open_url` targets continue to get
independent short-lived identities. Context scope would unnecessarily couple
multiple targets, and process scope would share one JavaScript identity across
providers and `open_url`.

Pinned Obscura constructs `StealthHttpClient` with a 30-second request timeout
but no explicit pool-idle setting. Its pinned `wreq` dependency defaults idle
pooled sockets to 90 seconds and exposes a builder-level
`pool_idle_timeout`, including `None` to disable it. Obscura exposes no CLI
flag, environment variable, or CDP command for that builder option. Do not
confuse the request timeout, `OBSCURA_NAV_TIMEOUT_MS`, script deadline, dynamic
settle time, TCP keepalive, or the CDP WebSocket lifetime with HTTP pool idle
retention. Natural provider, proxy, kernel, or `wreq` idle expiry may close a
socket before one hour; preserving the target ensures this stack does not
destroy the pool earlier. A later request may establish a new socket through
the same retained client without rotating provider browser state.

There is one transport-consistency defect. In pinned v0.1.11, GET main
navigation uses the target-owned stealth client, while native POST navigation
uses the context-owned ordinary client. The planned Startpage form is POST.
This plan patches native form POST into the same target-owned stealth client,
cookie jar, proxy, TLS-emulation profile, connection pool, redirect loop, and
tracker policy as GET. Do not work around the mismatch by changing the form
method, synthesizing a result URL, using script `fetch`, disabling stealth, or
weakening the gate.

## Required outcomes

- Every custom engine begins a query at its declared provider homepage and
  reaches results only through that page's form submission.
- Every provider uses one retained target in its provider-owned retained
  Obscura connection. Clean attempts preserve the provider cookie jar, selected
  profile, browser context, stealth HTTP client, and connection pool until the
  one-hour idle clearing mark.
- Every navigation on one retained target receives the same seed-derived
  JavaScript fingerprint bundle. Only creation of a replacement target rotates
  that bundle.
- The shared client performs exactly two completed main-document stages:
  homepage and submitted results. Redirects and subresources within a stage do
  not increase that count.
- The former direct result URL is not constructed, navigated, retried, or used
  as a fallback.
- Provider admission, reservation, one-provider lease, cooldown, sequential
  next-provider rotation, suspension, and last-resort scoring retain their
  existing SearXNG ownership and semantics.
- The provider lease covers setup, both document stages, text entry,
  submission, cleanup, provider parsing, and suspension recording.
- The three-second provider cooldown starts immediately before homepage
  navigation, because that is the first provider-origin request.
- Homepage and submitted-result documents independently receive bounded HTTP,
  origin, challenge, lifecycle, and DOM validation.
- The result parser receives only the submitted result DOM. It never receives
  or parses the homepage DOM.
- The existing shared-client `fetch()` API and one-navigation contract remain
  unchanged for `open_url` and any non-search caller.
- Instant entry is the default for all providers.
- Timed key entry is selected only through the strict
  `SEARXNG_TIMED_TYPING_PROVIDERS` setting and shares all other code paths with
  instant entry.
- No query, query prefix, result URL, page text, markup, cookies, provider
  tokens, or form values are written to logs.
- Both entry modes remain on the existing Obscura browser route and public
  final-hop policy. No application container receives direct egress or a new
  network.
- Blocking, timeout, protocol, parser, cleanup, and policy failures remain
  visible typed failures. They must not become empty-result success, a retry,
  or a direct-network fallback.

## Non-goals

- Do not retain a direct-search mode, compatibility flag, or per-provider
  navigation strategy.
- Do not add a same-provider retry or try both entry modes for one query.
- Do not submit by synthesizing a result URL after loading the homepage.
- Do not add provider APIs, ordinary HTTP clients, Playwright, a browser
  binary, or a second CDP attachment.
- Do not create a general Obscura fork. Carry exactly the two reviewable
  build-time source patches specified below. Do not add provider logic,
  SearXNG configuration, navigation policy, compatibility fallbacks, or
  unrelated upstream cleanup to those patches.
- Do not simulate mistakes, corrections, pointer wandering, clicks, scrolling,
  focus changes, or other behavioral theatre.
- Do not expose timing distributions, per-character delays, submission
  methods, selectors, or navigation deadlines as user settings.
- Do not claim timed entry defeats bot detection.
- Do not add telemetry, experiment databases, query sampling, or result-quality
  storage.
- Do not change provider ordering, round-robin behavior, Bing's last-resort
  role, result scoring, parser selectors, URL unwrapping, or query-coherence
  checks except where a submitted result DOM demonstrably requires a narrow
  parser update.
- Do not change Obscura's fifteen-connection cap, five provider connection
  owners, one-hour provider-session idle lifetime, or one-hour blocking
  suspensions.
- Do not broaden terminal hosts, permit a form to submit to a different
  origin, or allow non-HTTPS provider navigation.
- Do not change VPN, upstream-proxy, Tor, DNS, bridge, or final-hop policy
  ownership.
- Do not increase timeouts as an incidental part of implementation. The
  timeout decision and amendment gate are specified below.

## Component boundaries and sole ownership

The following table is normative. A concern must not be re-enforced in a
second layer “for safety”; duplicate enforcement creates drift and conflicting
failure categories. Tests may observe an enforcement point but must not
reimplement it.

| Concern | Sole owner | Required boundary |
| --- | --- | --- |
| Browser contexts, page execution, network clients and pools, cookie jar, fingerprint implementation, JavaScript runtime, and CDP command implementation | Pinned Obscura source plus the two wrapper-owned build-time patches | Obscura receives CDP commands and returns protocol events. The patches route native POST through the target stealth client and make the existing JavaScript fingerprint seed target-scoped. Obscura knows no provider names, selectors, leases, or SearXNG settings. |
| Source acquisition, exact patch application, locked stealth compilation, patchset identity, and derived-image tagging | `browser/obscura_image/`, `stack.versions.env`, and `Makefile` | The image build verifies one immutable upstream source archive, applies exactly the ordered patch set without fuzz or fallback, builds with the upstream lock and `stealth` feature, and copies only the built binaries into the hardened runtime image. Runtime containers never patch source or install build tools. |
| CDP connection/target/session-generation lifecycle, staged event accounting, generic HTTP/challenge classification, bounded DOM capture, text-entry mechanics, native form submission, deadline accounting, and cleanup | `browser/obscura_client/private_onyx_obscura` | The client retains one target with one connection, never rotates or clears browser state, and executes a trusted declarative search specification. It owns no provider selection, suspension, cooldown, parser, or environment parsing. |
| The single provider-interaction registry, terminal-origin and form-action policy values, one-hour idle clearing policy, provider browser owners, strict timed-provider setting parsing, entry-mode selection, pre-navigation guard, failure mapping, and returned-DOM validation | `searxng/engines/_obscura.py` | `_obscura.py` owns one opaque shared-client search session per provider and its idle timer. It supplies one immutable specification selected by exact engine name and never sends raw CDP commands or manipulates target IDs. |
| Dynamic provider request options derived from SearXNG `RequestParams`, plus provider-specific result parsing and result-URL normalization | `google2.py`, `brave2.py`, `duckduckgo2.py`, `startpage2.py`, and `bing2.py` | An engine supplies a tuple of fixed form fields and parses only the returned result DOM. It does not read environment settings, create URLs, inspect sessions, or select entry mode. |
| Provider reservation, lease lifetime around `engine.search()`, busy/cooling admission, suspension, next-provider rotation, and last-resort scoring | Existing SearXNG orchestration and strict wrapper patches | Search submission must use the existing reservation token and pre-navigation guard. The client and engines cannot select or retry providers. |
| Public/private destination enforcement, authoritative DNS, proxy/VPN/Tor route selection, redirects, and subresource egress | Existing final-hop policy and routing components | This work adds no route, DNS resolution, HTTP proxy, network, or destination fallback. |
| User-facing configuration transport | `.env.wrapper.example` and Compose environment entries | Compose passes a string and supplies the default `none`; it does not parse, normalize, or validate the provider set. |

Patch enforcement must occur exactly once:

- Obscura's patched navigation layer is the sole selector of stealth GET/POST
  transport. The shared client validates the provider form method but never
  chooses an HTTP client, inspects TLS, or retries through another path.
- Obscura's `Page` is the sole fingerprint-seed owner. Compose, SearXNG, the
  shared client, and provider engines neither supply nor verify a seed value.
- The image build is the sole patch-presence enforcement point. Runtime code
  may rely on the selected image contract; it must not parse source, compare
  version strings per request, or implement a compatibility branch.
- Focused patched-source tests prove internal method/seed mechanics once.
  Selected-image tests prove the compiled artifact once. Provider tests prove
  only the externally required form transaction and must not reproduce TLS or
  fingerprint implementation assertions five times.

### Why the interaction registry belongs in `_obscura.py`

The engine modules must not each invent a browser transaction. The single
registry in `_obscura.py` owns:

- homepage URL;
- exact allowed homepage and result hosts;
- expected form action path and method;
- query-control selector and expected form field name; and
- the provider's permitted dynamic fixed-field names.

This keeps network/interaction policy adjacent to the retained provider
session and terminal-origin policy already owned by `_obscura.py`. Individual
engines retain only request-parameter mapping and result parsing. Do not copy
registry values into engine constants or the shared client.

The shared client is the sole enforcement mechanism for the specification
values. `_obscura.py` constructs and selects the policy; it must not repeat
post-return hostname or form validation after the client has successfully
enforced the staged contract. It may check that a returned DOM is nonempty and
map typed client failures to SearXNG exceptions.

## Public and internal API surface

### Existing API retained unchanged

Keep the current exported asynchronous `fetch()` function and
`ObscuraSession` behavior unchanged. `fetch()` continues to mean exactly one
`Page.navigate` and remains the API used by `open_url`. Do not add optional
search arguments to `fetch()` or allow its one-shot target to become retained.

### New shared-client types

Add these immutable public types to
`private_onyx_obscura` and export them from its `__init__.py`:

```python
TextEntryMode = Literal["instant", "timed"]

@dataclass(frozen=True)
class SearchInteractionSpec:
    homepage_url: str
    allowed_homepage_hosts: frozenset[str]
    allowed_result_hosts: frozenset[str]
    query_selector: str
    query_field_name: str
    form_action_path: str
    form_method: Literal["get", "post"]
    allowed_fixed_field_names: frozenset[str]

@dataclass(frozen=True)
class SearchSubmissionResult:
    final_url: str
    status: int
    headers: Mapping[str, str]
    rendered_html: str
    challenge: FetchFailure | None
    homepage_navigation_seconds: float
    submission_navigation_seconds: float
```

Also add a separate opaque `SearchBrowserSession` owner. It encapsulates exactly
one `ObscuraSession` plus the optional retained target/session/main-frame
identifiers for one generation. Its public surface is construction and
idempotent asynchronous `close()` only; callers cannot set, clear, or inspect
target identifiers. `submit_search()` is the sole creator and user of its
retained target. This prevents the `open_url` one-shot target lifecycle and the
search retained-target lifecycle from being mixed.

The production implementation may use an existing immutable header/result
representation rather than introduce a duplicate mapping type, but the
observable fields and separation of the two stage timings are required.
Neither result type may contain homepage HTML, the query, fixed form fields, or
provider-generated form tokens.

`SearchInteractionSpec` instances are trusted code constants. Validate every
instance when `_obscura.py` imports:

- `homepage_url` must normalize as HTTPS, contain no credentials, use the
  default HTTPS port, and have a host in `allowed_homepage_hosts`;
- all hosts must already be lowercase canonical DNS names without a trailing
  dot, wildcard, port, path, or IP literal;
- `allowed_homepage_hosts` and `allowed_result_hosts` must be nonempty;
- selectors, field names, and the action path must be nonempty;
- `form_action_path` must be an absolute path beginning with one `/`, with no
  scheme, authority, query, fragment, backslash, or encoded path separator;
- the query field name cannot appear in `allowed_fixed_field_names`; and
- fixed-field names must be unique, nonempty ASCII form names.

Invalid trusted constants are startup failures, not request-time parser
failures.

### New shared-client function

Add a separate asynchronous API:

```python
async def submit_search(
    query: str,
    *,
    spec: SearchInteractionSpec,
    fixed_fields: tuple[tuple[str, str], ...],
    text_entry_mode: TextEntryMode,
    cdp_url: str,
    wait_until: str,
    dom_limit: int,
    pre_navigation_guard: Callable[[], bool],
    pre_navigation_timeout_seconds: float,
    cleanup_command_timeout_seconds: float,
    request_timeout_seconds: float,
    session_owner: SearchBrowserSession,
) -> SearchSubmissionResult:
    ...
```

Search always supplies an explicit retained `session_owner`; reject a missing
owner. On its first clean attempt, `submit_search()` connects and creates one
target; on later clean attempts it attaches to and navigates that same target.
It must never create a second live target in one owner. Search requires a
callable `pre_navigation_guard`; reject a missing guard before creating or
reusing a target.

Validate `query` as a string without rewriting, trimming, truncating, Unicode
normalization, or logging it. SearXNG remains authoritative for whether an
empty query is accepted. Validate `fixed_fields` structurally:

- every name must be in `spec.allowed_fixed_field_names`;
- each permitted name may occur at most once;
- names and values must be strings;
- neither names nor values are interpolated into JavaScript source; and
- query content must be supplied only through the `query` argument, never as a
  fixed field.

Do not add a synchronous shared-client API. The existing provider event-loop
owner in `_obscura.py` supplies the synchronous SearXNG bridge, just as it does
for `fetch()`.

### SearXNG adapter API

Replace engine calls to `_obscura.navigate()` with:

```python
def submit_search(
    engine_name: str,
    query: str,
    fixed_fields: tuple[tuple[str, str], ...],
    pre_navigation_guard,
) -> str:
    """Return the bounded submitted-result DOM for one leased provider attempt."""
```

This function:

1. resolves `engine_name` through the exact provider registry;
2. selects `instant` or `timed` from the already parsed provider set;
3. invokes the matching retained `_ProviderBrowserSession`;
4. maps shared-client typed failures to existing SearXNG exception classes;
5. maps generic result challenge classifications exactly as current
   `_obscura.navigate()` does;
6. rejects HTTP 404 and HTTP 5xx result documents;
7. rejects an empty returned DOM; and
8. returns the result DOM without parsing it.

Remove the search-engine-facing `_obscura.navigate()` API after all five
engines migrate. Do not retain an alias or compatibility route. The shared
client's unrelated `fetch()` remains.

## Provider interaction registry

The initial registry must contain exactly these five entries. Selectors are
intentionally semantic and narrow; generated CSS classes must not be used.
Before implementation, confirm method, action, ownership, visibility, and
editability against the pinned live pages through Obscura. If a row has
changed, update this plan and its provider tests before implementation.

| Engine | Homepage | Allowed final hosts | Query selector / field | Required form action | Method |
| --- | --- | --- | --- | --- | --- |
| `google2` | `https://www.google.com/` | `www.google.com`, `google.com` | `textarea[name="q"]` / `q` | `/search` | GET |
| `brave2` | `https://search.brave.com/` | `search.brave.com` | `textarea[name="q"]` / `q` | `/search` | GET |
| `duckduckgo2` | `https://noai.duckduckgo.com/` | `noai.duckduckgo.com` | `input[name="q"]:not([type="hidden"])` / `q` | `/` | GET |
| `startpage2` | `https://www.startpage.com/` | `www.startpage.com`, `startpage.com` | `input#q` / `query` | `/sp/search` | POST |
| `bing2` | `https://www.bing.com/` | `www.bing.com`, `bing.com` | `textarea[name="q"]` / `q` | `/search` | GET |

`consent.google.com` is not an allowed homepage or result host. A Google
`/sorry/` document on an allowed Google host is admitted as the terminal
document and then classified as a CAPTCHA by generic challenge handling. Do
not misclassify it as an origin-policy denial.

The permitted dynamic fixed fields and their engine-owned mapping are:

| Engine | Always supplied | Conditionally supplied |
| --- | --- | --- |
| `google2` | `hl=en`, `udm=14` | `start=(pageno-1)*10` when nonzero; `tbs=qdr:d|w|m|y` for the supported time range |
| `brave2` | none | `tf=pd|pw|pm|py` for the supported time range; `offset=(pageno-1)*10` after page one |
| `duckduckgo2` | `ia=web` | none |
| `startpage2` | `cat=web` | `page=pageno` after page one |
| `bing2` | `adlt=off`, `setlang=en` | `first=(pageno-1)*10+1` after page one |

The registry's `allowed_fixed_field_names` must exactly match the union of the
names in its row. Engines own value calculation and omission rules. The
adapter/client own rejection of unknown or duplicate fields.

Preserve provider-generated successful controls that are already in the
homepage form, such as source, funnel, experiment, or session fields, unless
their name is one of the explicit fixed fields above. For an explicit fixed
field:

- if no same-name successful control exists in the selected form, create one
  hidden control in that form;
- if exactly one hidden same-name control exists, replace only its value;
- if more than one same-name control exists, or an existing control is not
  hidden, fail with a provider-form protocol error; and
- verify the final form contains exactly one successful control carrying the
  requested exact value.

This is the sole fixed-field enforcement. Engines must not edit DOM, and the
client must not know provider-specific field names beyond the supplied
specification.

## Transaction contract

### Setup and first-origin boundary

The transaction uses the existing absolute setup and attempt deadlines. On a
new generation, CDP connection, target creation, attachment, required-domain
enablement, and frame discovery occur before any provider-origin request. On a
retained generation, validate that the recorded target/session/main-frame still
exist and re-enable or verify required domains as the pinned protocol requires;
do not detach, close, or recreate the target merely to start another attempt.

Call `pre_navigation_guard()` exactly once, immediately before the homepage
`Page.navigate`. A false result produces the existing finalized failure and
sends no provider-origin request. A true result records the provider cooldown
through the existing SearXNG guard. Do not call the guard again before form
submission; one attempt has one cooldown start.

Clear only completely classified events from the prior attempt in the local
CDP receive buffer before navigating the retained target. Never clear
unclassified events belonging to an active stage. Do not use a CDP cache,
cookie, storage, network-state, service-worker, or history clearing command at
the attempt boundary. Loader and main-frame identity, rather than an empty
target, separate attempts.

### Homepage stage

1. Normalize the configured homepage URL without resolving it.
2. Issue exactly one raw `Page.navigate` using the configured search
   `waitUntil`.
3. Identify the homepage stage by its main frame and loader ID. Redirects are
   part of this stage.
4. Require one complete terminal main-document event set for that loader.
5. Require HTTPS, default port, no credentials, and an exact terminal host in
   `allowed_homepage_hosts`.
6. Apply generic HTTP and challenge classification to a bounded rendered
   homepage DOM.
7. Treat HTTP 429, 401/402/403, generic CAPTCHA, 404, and 5xx as terminal
   attempt failures. Do not locate or edit a form on such a page.
8. Discard the bounded homepage DOM after form validation and submission. It
   must not appear in the result object, parser, or logs.

The 20 MiB search DOM limit applies independently while serializing each
stage. Do not retain both serialized DOM strings after submission begins.

### Form and control enforcement

All page interaction occurs through `Runtime.callFunctionOn` or another CDP
operation that passes selector, query, names, and values as protocol
arguments. Never construct JavaScript by interpolating query or form data into
source text.

Immediately before editing:

- require the current main-frame URL still satisfies the homepage policy;
- require `query_selector` resolves to exactly one connected, visible,
  enabled, editable `input` or `textarea`;
- require its exact `name` equals `query_field_name`;
- require it has one owning `HTMLFormElement`;
- resolve the form action against the current homepage URL and require HTTPS,
  default port, no credentials, an allowed result host, and exact
  `form_action_path`;
- require the form method exactly matches the registry method;
- require form target is absent, empty, or `_self`;
- require `application/x-www-form-urlencoded` submission; and
- reject a query control or form inside a child frame.

Do not click the query control or submit button. The prototype showed that a
provider can replace its control after pointer interaction, and pointer
simulation is outside this plan. Focus the verified control directly for timed
entry. Submit through the owning form's native
`HTMLFormElement.requestSubmit()` with no submitter; never press Enter, because
provider query controls can be textareas.

### Instant entry

Instant entry must:

1. obtain the native value setter from the control's appropriate prototype;
2. assign the complete query through that setter;
3. dispatch one bubbling `input` event and one bubbling `change` event;
4. re-resolve the declared control and owning form after page handlers run;
5. repeat the form/action/method policy checks; and
6. require the control value exactly equals the original query before
   submission.

Do not assign `element.value` through a page-overridden setter without first
selecting the native prototype setter. Do not use `innerHTML`, URL
concatenation, clipboard APIs, or `Input.insertText` for instant entry.

### Timed key entry

Timed entry must:

1. require the verified control's initial value is empty;
2. focus it without a pointer event and prove it is `document.activeElement`;
3. send one bounded CDP key-down/text and key-up pair for each query Unicode
   code point, in order;
4. wait an asynchronous random interval in the fixed inclusive range 45–135
   milliseconds after each completed code point except the last;
5. count command time and delay time against the one absolute attempt
   deadline;
6. re-resolve and revalidate the control and form; and
7. require the final control value exactly equals the query before submission.

Use a process-local system random source in production. Tests inject a
deterministic random source and nonblocking sleeper. Do not seed production
timing from the query, engine name, process start, wall clock, or a fixed
constant. Do not add mistakes, backspaces, variable pauses, or a maximum query
length. A long query may exhaust the existing attempt deadline and fail
visibly; it must never be truncated or silently switched to instant entry.

Timed key events may activate provider autocomplete and disclose successive
query prefixes to the same selected provider. This is an intentional,
documented consequence of opting a provider into timed entry. Do not suppress
suggestion requests by patching page JavaScript; that would create a third
interaction mode.

### Submission and result stage

Submission can begin a document navigation before the
`Runtime.callFunctionOn` acknowledgement arrives. The raw transport has one
WebSocket reader, so do not race an ordinary command waiter against a second
event waiter. Add one internal, generic CDP exchange primitive that:

- sends the submission command;
- consumes its acknowledgement and lifecycle/network events through one
  receive loop;
- partitions result events by the main frame and a loader ID distinct from the
  homepage loader;
- accepts either acknowledgement-before-navigation or
  navigation-before-acknowledgement ordering;
- completes only when both the command acknowledgement and a complete result
  main-document event set have arrived; and
- fails at the absolute attempt deadline without repeating submission.

An acknowledgement error is a protocol failure even if navigation started. A
complete result document without a command acknowledgement by the deadline is
an ambiguous submission failure. Either case invalidates and closes the whole
provider target/connection generation; it is never accepted heuristically and
never retried.

Require a distinct second main-document loader. Same-document history changes,
XHR-only results, popups, new targets, downloads, and child-frame results are
unsupported and fail visibly. Do not add generalized SPA automation until a
provider in the fixed set requires it and this plan is revised.

For the result stage:

1. validate the final URL as HTTPS, default-port, credential-free, and on an
   exact `allowed_result_hosts` member;
2. classify generic HTTP and challenge conditions;
3. require a complete terminal main-document event set;
4. reject 404 and 5xx;
5. serialize only the final main-frame DOM under the search DOM limit; and
6. return the result to `_obscura.py`.

Provider-specific CAPTCHA and parser-shape checks remain in the engine parser
and run after shared-client transaction completion while the provider lease
remains held. They do not manipulate the retained target.

### Cleanup and retained session

Use the existing five-second per-command cleanup bound. After an unambiguous
completed transaction, including a cleanly classified HTTP or CAPTCHA result,
retain both the target and its provider WebSocket. Do not navigate to
`about:blank`, detach, close the target, clear cookies, clear cache/storage,
reset headers, replace the client, or issue any other post-attempt browser
cleanup. Local serialized homepage/result DOM values and client-side event
copies must still be released once no longer needed; Obscura owns any native
page/history state that necessarily remains with the retained target.

After transport, protocol, submission, event-accounting, DOM, or cleanup
ambiguity, invalidate the whole `SearchBrowserSession` generation and close the
target followed by the WebSocket within the cleanup bound. Do the same at the
one-hour idle deadline and explicit process shutdown. If target close fails,
still close the WebSocket; the connection is the final generation boundary.
The next attempt may create one replacement generation but may not retry the
failed query.

The existing one-hour idle expiry and one-hour SearXNG blocking suspension
remain aligned. The adapter owns the idle policy and calls the opaque owner's
`close()`; only the shared client owns target/connection close ordering.

## Failure taxonomy

Reuse existing `FetchFailure` categories where their meaning is unchanged.
Add narrow search-stage metadata or categories only where needed to preserve
actionable, URL-free diagnostics:

| Failure | Required classification |
| --- | --- |
| Homepage or result 429 | `RATE_LIMITED` |
| Homepage or result 401/402/403 | `ACCESS_DENIED` |
| Generic homepage or result challenge, including Google `/sorry/` | `CAPTCHA` |
| Origin, scheme, port, credentials, form action, method, target, or enctype violation | `POLICY_DENIED` |
| Missing/duplicate/replaced/noneditable control, invalid form ownership, fixed-field conflict, no distinct result loader, or incomplete event set | `PROTOCOL` with a sanitized search stage |
| Existing pre-navigation setup deadline | `PRE_NAVIGATION_TIMEOUT` |
| Homepage navigation deadline | `NAVIGATION_TIMEOUT`, stage `homepage-navigation` |
| Text-entry or submission/result deadline | `POST_NAVIGATION_TIMEOUT` with exact sanitized stage |
| Oversized homepage or result DOM | `OVERSIZE` with exact stage |
| Failed target/connection cleanup | existing cleanup failure handling; connection discarded |

Logs may contain an opaque request ID, engine name at the SearXNG adapter,
entry mode (`instant` or `timed`), sanitized stage, typed category, elapsed
times, status class, bounded byte counts, challenge signal, and cleanup
outcome. They must not contain URLs because result URLs include the query.

## Configuration contract

Add this user-facing setting:

```text
SEARXNG_TIMED_TYPING_PROVIDERS=none
```

Its grammar is strict and case-sensitive:

- `none` selects no timed providers and is the default;
- `all` selects all five providers;
- otherwise the value is a comma-separated list containing one or more exact
  names from `google2,brave2,duckduckgo2,startpage2,bing2`;
- whitespace, empty elements, duplicate names, unknown names, uppercase
  aliases, trailing commas, and combining `none` or `all` with another element
  are invalid; and
- an empty string is invalid. Compose supplies `none` when the variable is
  absent.

Examples:

```text
SEARXNG_TIMED_TYPING_PROVIDERS=none
SEARXNG_TIMED_TYPING_PROVIDERS=google2,bing2
SEARXNG_TIMED_TYPING_PROVIDERS=all
```

`searxng/engines/_obscura.py` is the sole parser and selection owner. Parse
once at module import into an immutable provider set. Invalid input must abort
SearXNG worker startup. Compose, the Makefile, individual engines, the shared
client, and the SearXNG patch must not parse or normalize it.

Pass the value with default `none` in both:

- the root `docker-compose.yaml` `searxng-core` environment; and
- `searxng/docker-compose.yml` for the component-scoped model.

Document the setting in `.env.wrapper.example` beside
`SEARXNG_ROUND_ROBIN`, including the query-prefix/autocomplete consequence.
Do not add a Makefile selection variable or a separate `all` boolean.

Because `.env.wrapper.example` is user-facing configuration, add a concise
README reference to the optional timed-entry behavior and its privacy/latency
consequence. Keep selectors, CDP commands, and internal timing constants out
of README.

## Timeout and resource contract

Keep the current limits for the initial implementation:

- one 45-second absolute pre-navigation setup deadline;
- one 50-second absolute SearXNG browser-attempt deadline shared by setup,
  homepage navigation, entry delays, submission, result navigation, and DOM
  retrieval;
- separate five-second cleanup-command bounds; and
- SearXNG's existing 60-second engine window.

Do not give each stage a fresh 50 seconds. Provider-capacity waiting remains
outside the engine window as currently documented.

Homepage-first mode adds one main-document request, its subresources, one
bounded homepage DOM serialization, form JavaScript, and possibly autocomplete
requests. Update the resource documentation accordingly. It does not add a
WebSocket, provider owner, target, worker, event-loop thread, periodic task,
health check, or persistent store, but it changes the existing search target
from per-attempt to provider-session lifetime and therefore retains the current
page's native in-memory state until idle expiry or generation invalidation.

Optional native Tor can add substantial latency. Completion requires the live
Tor check specified below. If the two-stage transaction cannot reliably reach
a terminal provider outcome inside 50 seconds over a healthy Tor route, stop
and amend this plan with one coordinated bounded timeout decision covering the
shared client, SearXNG engine window, Onyx SearXNG request timeout, tests, and
owning documentation. Do not opportunistically increase one inner timeout or
add a provider-specific timeout.

## Implementation work map

### Obscura pinned-source build and patch set

The current derived image downloads digest-verified upstream stealth release
binaries. Replace that mechanism with a reproducible build from v0.1.11 commit
`e78b5e60261599a850c053eaecc2de92625496d7`, already represented by
`reference_repos/obscura`.
`reference_repos/` remains read-only and is never a build input.

Add these tracked inputs:

```text
browser/obscura_image/fetch_source.py
browser/obscura_image/patches/series
browser/obscura_image/patches/0001-stealth-native-post.patch
browser/obscura_image/patches/0002-target-fingerprint-seed.patch
```

`stack.versions.env` must replace the architecture-specific release-archive
digests with:

- the exact upstream commit as `OBSCURA_SOURCE_REF`;
- one SHA-256 for the source archive addressed by that commit; and
- a digest-pinned, multi-architecture Rust/Debian builder image sufficient for
  the pinned lock and V8/BoringSSL build.

Retain the digest-pinned upstream Obscura image solely as the hardened final
runtime base unless an audit shows the source-built binaries require a
different runtime. `fetch_source.py` must reject a digest mismatch, traversal,
links, devices, duplicate paths, unexpected archive root, or overwrite. It
must not use a branch, moving tag, Git checkout, or the local reference
repository.

The builder stage may install only the pinned release build's required
toolchain packages—CA certificates, C/C++ build tools, CMake, libclang/Clang,
Git for exact patch application, Make, Perl, pkg-config, and Python when the
pinned build requires them. Cargo registry and `rusty_v8` downloads occur only
during this explicit image build and remain checksum/lock constrained. Preserve
the Makefile's scoped build-proxy forwarding. Build natively for Docker's
selected `TARGETARCH`; do not introduce QEMU, a host Rust dependency, or a
runtime download.

The Dockerfile must:

1. fetch and verify the pinned source in a builder stage;
2. require `patches/series` to list exactly the two patch files once, in the
   order above, with no unlisted `*.patch` file;
3. run `git apply --check` and then `git apply` for each patch against the clean
   extracted source; offset/fuzz, rejected hunks, already-applied patches, and
   source-shape drift are fatal;
4. build both `obscura` and `obscura-worker` with the upstream `Cargo.lock`,
   `cargo build --release --locked --features stealth -p obscura-cli --bin
   obscura --bin obscura-worker`, and an explicit wrapper patchset version
   suffix;
5. run the focused Rust tests named below before copying artifacts;
6. copy only the two binaries into the digest-pinned hardened runtime image;
   and
7. contain no compiler, Cargo cache, source, patch file, shell, or package
   manager in the final image beyond what its audited runtime base already
   provides.

Update `OBSCURA_WRAPPER_BUILD_INPUTS` and its content-derived tag to include the
source ref/digest, builder and runtime image identities, source fetcher,
Dockerfile, patch series, and both patches. A patch edit must select a new
local image without changing a mutable tag. Remove `fetch_release.py` and the
obsolete release-archive digest contract; do not retain dual binary/source
build strategies.

#### Patch 0001: stealth native POST

The POST patch is owned entirely by Obscura's generic navigation layer:

- refactor `StealthHttpClient::fetch()` to delegate to one internal
  method-aware navigation function;
- add a form-POST entry point using the same `wreq::Client`, cookie jar, proxy,
  tracker policy, extra headers, in-flight counter, redirect limit, and
  response/cookie handling as stealth GET;
- set `Content-Type: application/x-www-form-urlencoded` only when carrying the
  native form body;
- match the existing ordinary navigation redirect contract: 301, 302, and 303
  convert POST to GET and clear the body; 307 and 308 preserve method and body;
- make `Page::navigate_single()` select the target's stealth client for both
  GET and POST whenever stealth is active, retaining the ordinary client for
  both methods only when stealth is inactive;
- record the actual initial document method instead of the current hard-coded
  `GET`; and
- leave scripted fetch/XHR, provider form discovery, destination enforcement,
  retries, and SearXNG ownership unchanged.

Do not construct a second `wreq::Client`, copy cookies between clients, or fall
back to the ordinary client after any stealth error.

#### Patch 0002: target-scoped fingerprint seed

The fingerprint patch is likewise provider-agnostic:

- add a private `fingerprint_seed: u32` to `Page`;
- generate it once with operating-system-backed randomness in `Page::new`;
  adding the already workspace-pinned `uuid` dependency to `obscura-browser`
  and deriving the seed from `Uuid::new_v4()` is the preferred narrow change;
  regenerate while the derived 32-bit value is zero, and include the
  `Cargo.toml` plus `Cargo.lock` dependency-list changes in the patch so
  `--locked` remains authoritative;
- provide a Rust-to-JavaScript runtime setter that installs the seed before
  `run_page_init()` on every navigation, including `about:blank`;
- add the seed global to Obscura's hidden-internal list;
- replace the per-navigation `Date.now() ^ Math.random()` assignment with the
  injected target seed while retaining the existing deterministic salted
  derivation and per-realm cache; and
- keep normal time, `Math.random()`, WebCrypto randomness, and non-fingerprint
  page behavior unchanged.

The seed is non-secret but must not enter CDP results, logs, DOM, cookies,
storage, configuration, or image labels. Do not add a seed environment
variable or deterministic provider-name/query derivation.

Both patches must include focused upstream Rust/JavaScript tests in their
diffs. Add a constant patchset marker to the Obscura version/startup diagnostic
so selected-image validation can reject an unpatched stealth binary; the marker
must reveal only a static patchset version, never the seed.

The pool idle timeout itself is not a required new setting: natural idle socket
expiry is allowed. Do not add a wrapper environment variable that Obscura does
not natively consume. If a future selected release exposes such a setting,
leave its upstream default unchanged unless this plan is separately amended
with resource, proxy/Tor, and stale-socket validation.

### Shared client

Modify:

- `browser/obscura_client/private_onyx_obscura/client.py`
- `browser/obscura_client/private_onyx_obscura/__init__.py`

Required work:

- add the immutable search types and `submit_search()` API;
- add the opaque `SearchBrowserSession` that owns one connection/target
  generation and implements target-then-WebSocket close;
- factor reusable private single-stage navigation/event/DOM helpers from
  `fetch()` without changing `fetch()` behavior;
- implement trusted-argument Runtime calls, instant entry, timed entry, form
  enforcement, fixed fields, and native submission;
- implement the one-reader submission acknowledgement/result-event exchange;
- partition homepage and result documents by loader and main frame;
- preserve absolute deadlines, generic challenge classification, URL-free
  diagnostics, retained-target validation, and strict generation cleanup; and
- keep search-only code out of the `open_url` call path.

Do not add a new Python dependency. The existing SearXNG wrapper source hash
already covers every Python file directly under the shared client directory;
if implementation introduces a subpackage, extend
`SEARXNG_WRAPPER_BUILD_INPUTS` so every copied input affects the derived image
tag.

### SearXNG shared adapter

Modify `searxng/engines/_obscura.py`:

- define and startup-validate the exact five-entry interaction registry;
- strictly parse `SEARXNG_TIMED_TYPING_PROVIDERS`;
- select the entry mode by exact engine name;
- adapt `_ProviderBrowserSession` to own one `SearchBrowserSession` and invoke
  shared-client `submit_search()` on the existing provider event loop;
- replace the engine-facing `navigate()` with the specified adapter
  `submit_search()`;
- preserve reservation, lease, cooldown, idle expiry, failure mapping, and
  generation-discard behavior; and
- remove terminal-host enforcement now duplicated by neither layer: policy
  values remain in the registry and the shared client becomes their sole
  enforcement mechanism.

### Provider engines

Modify all five files under `searxng/engines/`:

- remove direct result-URL construction;
- compute only the fixed-field tuples defined above;
- call `_obscura.submit_search()` once;
- preserve existing result parsing, challenge checks, URL normalization,
  paging declarations, time-range declarations, and Bing query coherence; and
- remove imports used only for direct target URL construction.

Do not retain dead base URLs, query URL helpers, or comments describing direct
navigation.

### Configuration and image contract

Modify:

- `stack.versions.env`
- `Makefile`
- `browser/obscura_image/Dockerfile`
- replace `browser/obscura_image/fetch_release.py` with the source fetcher and
  patch inputs specified above
- `docker-compose.yaml`
- `searxng/docker-compose.yml`
- `.env.wrapper.example`
- `tests/validate_obscura_image.py` and the existing selected-image fixture at
  its owning location
- affected deterministic tests listed below

The root and component Compose models must pass the exact string unchanged.
No new runtime network, volume, capability, service, health check, package,
browser binary, or secret is required. Rust, V8/BoringSSL build dependencies,
source, and patches exist only in image-builder stages.

## Deterministic test criteria

Tests must prove executable boundaries without duplicating the same assertion
at every layer.

### Obscura source-build and patch tests

Replace `tests/test_obscura_release_fetch.py` with focused source-fetch tests
covering the exact archive URL/ref, digest success/failure, one expected root,
regular files, traversal, links/devices, duplicates, overwrite, and cleanup
after failure. Do not test the standard library tar implementation.

Extend `tests/test_obscura_direct_compose.py` or add one narrowly named
image-contract module to prove:

- the source ref, source digest, builder image, runtime image, patch series,
  both patches, source fetcher, and Dockerfile all affect
  `OBSCURA_WRAPPER_SOURCE_HASH`;
- the old release-archive variables, fetcher, and binary-copy strategy are
  absent;
- the Dockerfile uses `--locked`, explicitly enables `stealth`, builds exactly
  the two binaries, runs the focused patch tests, and copies only those
  artifacts into the runtime stage;
- the series file and directory contain exactly the same two ordered patches;
  and
- Compose still runs the resulting image with `--stealth` and does not receive
  any fingerprint-seed or POST-path setting.

The patches themselves must add focused upstream tests:

- stealth GET and form POST use the same client instance and cookie jar;
- POST body/content type plus 301/302/303 versus 307/308 redirect semantics;
- a stealth error never invokes the ordinary client;
- the recorded main-document method is POST for an initial POST;
- two realms initialized on one injected target seed produce identical
  screen/GPU/canvas/audio/hardware/device-memory values; and
- two injected target seeds produce their corresponding distinct deterministic
  bundles without consulting wall clock or `Math.random()`.

Run only those named upstream tests in the builder. Existing selected-image
coverage remains the regression authority for general Obscura behavior; do not
run the entire upstream suite at every wrapper build.

### Shared-client unit tests

Extend `tests/test_obscura_cdp_client.py` with protocol fixtures that cover:

- unchanged legacy `fetch()` one-navigation behavior;
- one representative GET form and one representative POST form;
- homepage and result loader/event partitioning, including redirects;
- result events arriving before the submission command acknowledgement;
- missing acknowledgement, command rejection, no second loader, popup, child
  frame, and same-document result failure without retry;
- exact homepage, form-action, and result-origin enforcement;
- rejection before interaction for blocked/challenged homepage documents;
- control uniqueness, visibility, editability, ownership, action, method,
  target, enctype, and fixed-field enforcement;
- user query and form values passed as CDP arguments rather than interpolated
  JavaScript;
- instant entry's native setter, exact input/change event count, and final
  value check;
- timed entry's exact character order, focus proof, bounded injected delays,
  final value check, and deadline behavior using an injected deterministic
  clock/random source;
- no mode fallback, query truncation, resubmission, or direct result
  navigation;
- independent homepage/result DOM size enforcement and homepage DOM release;
- one target creation across two clean attempts on the same owner, with distinct
  loader accounting and no detach/close/clear commands between attempts;
- target and connection retention after clean classified responses;
- target-then-WebSocket close exactly once at owner close or after
  protocol/submission/cleanup ambiguity;
- connection close even when target close fails; and
- no failed-query retry when a replacement generation is later created.

Use local protocol/fixture data only. Do not create five copies of generic
client tests or require Internet access.

### Provider and adapter tests

Extend `tests/test_searxng_obscura_engines.py` to prove once per provider:

- exact registry values from the provider table;
- exact fixed fields for page one, later pages, and supported time ranges;
- the engine calls `_obscura.submit_search()` exactly once;
- no engine constructs or calls a direct result URL; and
- existing parser and result normalization fixtures remain valid.

Test configuration parsing in the adapter's owning test module:

- absent/default `none`;
- exact `none`, `all`, each individual provider, and one valid subset;
- rejection of empty, whitespace, empty items, duplicates, unknown names,
  mixed `all`/`none`, and case variants; and
- selection of `timed` only for expanded set membership.

Extend `tests/test_searxng_obscura_scheduling.py` only for scheduling
integration:

- the reservation token is consumed once;
- the guard/cooldown is stamped once immediately before homepage navigation;
- the lease remains held through client completion, parser completion, and
  suspension recording;
- one failed provider attempt advances only to a different provider; and
- no scheduling test reimplements form or CDP mechanics.

### Compose and build-input tests

Extend `tests/test_obscura_direct_compose.py` to prove:

- root and component Compose pass
  `SEARXNG_TIMED_TYPING_PROVIDERS` with default `none`;
- no Compose layer sets `OBSCURA_ROTATE_PROFILE`, `OBSCURA_PROFILE`, or a
  wrapper-owned HTTP-pool timeout or fingerprint seed;
- SearXNG retains only its existing API and Obscura-control networks;
- no new browser package, binary, port, volume, or capability appears; and
- every new shared-client source file remains included in the derived SearXNG
  image hash.

Do not test README or documentation wording.

## Selected-image validation criteria

Extend the focused Obscura/SearXNG selected-image fixtures rather than adding a
new broad image target. `make test-obscura-image` owns the Obscura binary and
runtime assertions below; `make test-patch-images` owns the SearXNG
client/engine assertions. Together they must prove against the locally built
selected images:

- the pinned Obscura public CDP endpoint supports the exact Runtime, Input,
  form-submission, lifecycle, DOM, and target-cleanup commands used;
- the static patchset marker and full TLS-impersonation stealth startup
  diagnostic are both present, and the tracker-blocking-only binary is
  rejected;
- a fixture homepage GET form and POST form each produce exactly one distinct
  result document;
- instant and timed entry share the same navigation/submission transaction;
- two fixture searches through one `SearchBrowserSession` create one target,
  retain cookies, retain the stable advertised profile and seed-derived
  JavaScript fingerprint bundle across homepage/result and later-query
  navigations, and do not issue state-clearing CDP commands between attempts;
- in an isolated `--network none` validation container, run Obscura and a
  loopback-only TLS fixture in the same container with test-only
  `--allow-private-network`; require consecutive GET navigation, native POST
  form navigation, and another GET on one retained target to present the same
  expected stealth client profile and to reuse an HTTP connection when the
  fixture keeps it alive and the interval is below the library idle timeout;
- the loopback fixture records only method, connection identity, and
  fingerprint-shape assertions, never query or cookie values;
- a failed interactive transaction discards only the affected provider
  target/connection generation;
- explicit owner close and idle expiry leave no completed target behind;
- the fifteen-WebSocket mixed capacity contract remains unchanged; and
- installed pinned SearXNG symbols still match the strict offline-processor,
  scheduler, timeout, result-container, and suspension patches.

The image gate must not pull, rebuild, or substitute a missing selected image.
Build the deliberately selected artifacts first with `make obscura-build` and
`make searxng-build`.

## Live validation criteria

Run live checks with sanitized output. Do not capture full DOM, cookies,
provider-generated tokens, or private `.env.wrapper` contents.

### Required default-mode validation

With the supported full stack and
`SEARXNG_TIMED_TYPING_PROVIDERS=none`:

1. Rebuild the derived image and recreate SearXNG through the supported
   Makefile workflow.
2. Verify all five engines initialize and the strict setting parser reports no
   startup failure.
3. Query each engine explicitly with at least two ordinary, non-sensitive
   queries.
4. Accept provider results, CAPTCHA, access denial, 429, or parser mismatch as
   provider outcomes; require logs to prove homepage and submission stages
   where submission was reached.
5. Prove no direct result URL navigation and no same-provider retry occurred.
6. For a successful result, prove the engine parser consumed only the
   submitted result DOM and retained complete result URLs.
7. Repeat one provider query on the same provider session and prove the same
   target and WebSocket generation were reused, cookies were retained, and no
   browser-state clearing command occurred.
8. Run at least four concurrent SearXNG requests and prove different providers
   can still progress concurrently while same-provider admission remains
   serialized.
9. Verify `make ps-full` and targeted SearXNG/Obscura logs after the run.

Do not require a live provider socket to remain open for one hour: providers,
proxies, kernels, and the HTTP library may expire it naturally. The deterministic
loopback test proves that this stack does not close an otherwise reusable
connection at the per-query boundary. The live check proves only retained
target/WebSocket generation and successful reconnect through the same client
when an upstream socket has expired.

### Required timed-entry validation

Recreate SearXNG with a two-provider subset, initially
`google2,bing2`, then with `all`:

- prove subset expansion selects timed entry only for the named providers;
- prove `all` selects timed entry for every provider;
- issue one explicit query per selected provider;
- prove each query is submitted once, is not truncated, and does not fall back
  to instant entry;
- record only entry mode, sanitized stage, category/status class, timing, and
  result count; and
- return the deployment to its intended operator configuration after testing.

Quality observations may inform later configuration choices but are not pass
or fail criteria. The pass criterion is correct bounded execution and visible
provider outcomes.

### Required routing and latency validation

- Run the five-provider default instant-entry check through the explicit
  no-VPN route.
- Run at least Google and one provider that returns results through a healthy
  native Tor egress route. A CAPTCHA or rate limit is acceptable if both
  staged routing and timeout behavior are visible.
- Prove loss of the selected browser bridge/final hop fails closed and does
  not create a direct request or second attempt. Reuse an existing focused
  lifecycle diagnostic where possible; do not rerun the entire routing matrix.

Myst and configured-upstream-proxy live matrices are not required solely for
this change because network topology and final-hop enforcement are unchanged.
If implementation changes any routing, DNS, proxy, or Compose network
behavior, this exemption ends and the complete owning-document matrix becomes
required.

### Docker and Podman scope

Render and inspect the affected lite/full effective Compose models for Docker
and Podman to prove identical SearXNG environment and network semantics. A
second full live provider matrix under Podman is not required solely for this
client/configuration change because there is no engine-specific mount,
lifecycle, socket, health, or network behavior. If implementation adds any
such behavior, read and execute the full `docs/podman_suport.md` compatibility
matrix before completion.

Because the Obscura image changes from binary replacement to a source-builder
stage, run `make obscura-build` and `make test-obscura-image` with Docker and
Podman when both are available on a supported host. This is the complete
engine-specific addition; do not duplicate the five-provider live matrix under
both engines unless effective runtime lifecycle or networking also changes.

## Completion commands

At minimum:

```sh
make check
make obscura-build
make searxng-build
make test-obscura-image
make test-patch-images
```

Also inspect:

```sh
git diff --check
make ps-full
docker logs searxng-core --since 10m
docker logs onyx-obscura-1 --since 10m
docker logs onyx-onyx-public-egress-proxy-1 --since 10m
```

Use the selected container engine in commands and diagnostics. Do not use
Docker as a fallback when Podman is selected.

Any omitted live or image check must be reported with the exact reason. This
plan is not complete if the deterministic suite, selected-image gate, default
no-VPN five-provider matrix, or required Tor latency check is omitted.

## Required documentation updates

Implementation is incomplete until current behavior is documented
canonically. Update:

- `docs/request_handling.md`
  - separate the unchanged one-navigation `open_url` contract from the new
    two-stage SearXNG search transaction;
  - document homepage/form/result validation, cooldown placement, no retry,
    retained provider WebSocket/context/target generations, cookie and
    target-seeded fingerprint continuity, stealth GET/POST continuity, natural
    HTTP-pool idle expiry, instant/timed entry, failure mapping, and timeout
    accounting;
  - replace every statement that a search engine builds one provider result
    URL or sends one `Page.navigate`; and
  - document provider-specific homepage submission parameters and the
    autocomplete/query-prefix consequence.
- `docs/resource_minimization.md`
  - record the added homepage document, bounded homepage DOM, form execution,
    optional key delays/autocomplete traffic, unchanged connection/target
    counts, changed target lifetime, retained native page memory, and unchanged
    worker, health, and persistence counts.
- `docs/onyx_patch_info.md`
  - replace the unmodified-release-binary description with the pinned-source
    stealth build, exact two-patch inventory, strict build-time application,
    target-scoped seed lifetime, native POST path, and removal criteria;
  - replace fresh-target-per-query statements with the retained provider
    target generation.
- `docs/onyx_patches_upgrade.md`
  - add source-ref/archive/builder/patch-series verification and require
    rebasing each patch against the exact candidate source with no fuzz;
  - add separate removal gates: delete the POST patch only when upstream routes
    native POST through the same target stealth client with equivalent redirect
    semantics; delete the seed patch only when upstream provides target- or
    context-stable fingerprint state whose lifetime matches this plan;
  - add the retained target/client scope, GET/POST stealth path,
    target-fingerprint, stable-profile, and pool-idle selected-image validation;
  - require rechecking all five homepage form contracts on Obscura/SearXNG
    upgrades.
- `.env.wrapper.example`
  - add the exact `SEARXNG_TIMED_TYPING_PROVIDERS` grammar, default, examples,
    and privacy/latency consequence beside the existing SearXNG scheduling
    option.
- `README.md`
  - add only a concise user-facing statement that searches start at provider
    homepages and that timed per-provider entry is optional and can disclose
    query prefixes through suggestions.

Review but do not duplicate behavior into these documents unless their owned
contract actually changes:

- `docs/vpn_routing_and_proxies.md`
- `docs/internal_network_security.md`
- `docs/native_tor_support.md`
- `docs/podman_suport.md`

Update `docs/native_tor_support.md` only if timeout or Tor validation behavior
changes. Update `docs/podman_suport.md` only if engine-specific lifecycle,
mount, health, socket, or network behavior changes. Update patch information
as required above for these build-time patches. Do not add duplicate search
transaction descriptions merely to mention that other boundaries were
reviewed.

`AGENTS.md` does not require an update: no repository-wide documentation
routing, supported workflow, or key location changes.

After implementation review and validation:

1. move this file to `docs/plans/implemented/search_submission.md`;
2. mark it implemented with the completion date and concise validation
   evidence;
3. link to the canonical `docs/request_handling.md` behavior; and
4. preserve the implemented plan as a historical record. Later behavior
   changes must update canonical documentation rather than appending a progress
   diary here.

## Implementation order

Implement in this order so failures remain attributable:

1. Replace the release-binary wrapper with the pinned-source build, apply the
   two strict patches, and pass focused Rust plus selected-image Obscura tests.
2. Audit live provider form contracts against that exact patched image; revise
   this plan if any normative row
   differs.
3. Refactor shared-client private stage helpers while preserving every
   existing `fetch()` test and runtime contract.
4. Add the retained `SearchBrowserSession` and two-stage `submit_search()`
   state machine, policy enforcement,
   instant entry, cleanup, and generic fixture tests.
5. Add the single SearXNG provider registry and adapter API.
6. Convert all five engines atomically; do not leave a mixed deployed set.
7. Add strict timed-provider configuration with default `none`.
8. Add timed key entry behind the already tested common transaction.
9. Update focused scheduling, provider, Compose, and image tests.
10. Update all mandatory canonical/user documentation.
11. Run deterministic, image, live default, timed subset/`all`, concurrency,
    fail-closed, and Tor validation.
12. Move the plan to `implemented/` only after all required evidence is
    available.

Do not commit unless the user explicitly requests a commit.
