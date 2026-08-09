# First-Party Cookie Isolation for `open_url()`

> **Status: deferred.** This document is an implementation plan, not a
> description of current behavior. Both supported `open_url()` transports
> currently start each navigation without cookies retained by an earlier
> navigation. SearXNG no longer belongs to this deferred design: it now uses
> provider-partitioned live browser sessions, as recorded in
> [SearXNG provider browser sessions](../implemented/searxng_provider_sessions.md).
> The normative current behavior remains documented in
> [Request handling](../../request_handling.md) until this plan is implemented,
> validated, documented, and moved to `docs/plans/implemented/`.
>
> Do not enable the Obscura part of this plan against the selected image. Its CDP
> cookie export/import path does not preserve whether a cookie is host-only,
> and its cookie-domain validation does not use a complete Public Suffix List.
> Re-importing an exported host-only cookie can therefore widen it to
> subdomains. The capability gate below must pass against a later pinned
> Obscura release before implementation is enabled.

## Goal

Give each `open_url()` transport a bounded, process-local cookie cache that:

- retains only cookies belonging to the first-party site of the requested
  navigation;
- never makes one first-party site's cookies available to another site;
- gives every retained site generation a hard maximum lifetime of one hour;
- preserves shorter cookie expiration and explicit cookie deletion;
- supports ten concurrent Obscura `open_url()` navigations without serializing
  browser work;
- keeps the stock Requests and Playwright cookie stores separate;
- does not alter URL validation, DNS ownership, egress routing, navigation
  count, retry behavior, or response limits; and
- does not retain cookies for Web Connector ingestion or unrelated Onyx
  Playwright users.

The intended state boundaries are:

| Caller and transport | Retained state | Sharing boundary |
| --- | --- | --- |
| Onyx `open_url()` through Obscura | One in-memory cookie store | Same API process, same schemeful first-party site, and same live one-hour generation |
| Stock `open_url()` Requests attempt | A separate in-memory cookie store | Same API process, same schemeful first-party site, Requests only |
| Stock `open_url()` Playwright fallback | A separate in-memory cookie store | Same API process, same schemeful first-party site, Playwright only |
| Web Connector, indexing, or another Playwright caller | None added by this work | Existing upstream behavior remains authoritative |

“Process-local” is deliberate. This plan does not promise a shared cookie
session across API replicas, process restarts, image replacement, or stack
restart. If the API later runs multiple worker processes, each
worker must have an independent store and one-hour lifecycle. Do not add a
database, broker, sidecar, shared file, or sticky load-balancing scheme to hide
that boundary.

This feature is site isolation, not user isolation. The current `open_url()`
call chain does not propagate a stable authenticated-user identifier to these
adapters. In a multi-user deployment, callers served by the same API process
could therefore reuse cookies for the same first-party site. Implementation
must remain deferred unless the deployment's single-user trust assumption is
accepted and documented, or a separate project first supplies an
authenticated principal namespace through the request chain. Never describe
this plan as per-user isolation.

## Scope and Non-Goals

This plan applies only to:

- the direct-Obscura replacement installed by
  `onyx/patches/sitecustomize_api_server/obscura_crawler_patch.py`; and
- the stock Onyx Requests/Playwright path wrapped by
  `onyx/patches/sitecustomize_api_server/onyx_crawler_egress_patch.py`.

It does not:

- persist local storage, IndexedDB, service workers, cache storage, HTTP
  cache, TLS sessions, browser history, permissions, or any browser state
  other than accepted HTTP cookies;
- preserve authentication indefinitely or promise that every site login works;
- share state between Requests and Playwright;
- share state between the stock and Obscura selections;
- apply cookie policy in an egress proxy;
- change Onyx indexed-result reuse, ingestion, or semantic retrieval;
- add extra prefetches, browser navigations, retry attempts, or fixed sleeps;
- use Obscura's `--storage-dir`; or
- retain compatibility with any experimental or earlier cookie shim.

Do not add a user-facing TTL setting. The retention ceiling is exactly 3,600
seconds. Do not add switches for sharing transports. If a
deployment-wide enable/disable switch is considered necessary during privacy
review, add at most one strictly parsed browser-cookie-continuity boolean and
keep every transport partition mandatory. The preferred implementation has no
new setting: this behavior becomes the supported behavior for both
`open_url()` selections once all gates pass.

## Version Scope

This plan is based on the committed pins in
[`stack.versions.env`](../../../stack.versions.env), the matching upstream
checkouts under `reference_repos/`, and the committed Python dependency locks.
Re-run the capability/source-shape audits and revise this plan when any row
changes; a matching version string alone does not replace the black-box gates
specified below.

| Component | Consulted version | Why it matters for this plan |
| --- | --- | --- |
| Obscura | Derived v0.2.0 image; `reference_repos/obscura` at `v0.2.0` | Owns per-WebSocket state isolation, the fifteen-connection cap, CDP cookie import/export, cookie-domain validation, target lifecycle, and optional storage persistence. Its lossy host-only round trip is the principal implementation blocker. |
| Onyx application | Current `ONYX_IMAGE_TAG`; matching `reference_repos/onyx` checkout | Owns `open_url()` orchestration, the stock Requests-first/Playwright-fallback flow, the five-worker stock crawler, the 120-second tool deadline, and the runtime symbols wrapped by both Onyx patches. |
| Onyx crawler libraries | Requests `2.33.0`, Playwright `1.58.0`, and `publicsuffix2` `2.20191221` in the Onyx `uv.lock` | Determine Requests cookie-jar metadata, Chromium context cookie conversion, and the parser available to runtime patches. The old parser package's implicit PSL data is not accepted as the shared current snapshot proposed here. |
| Egress identity components | `MYST_IMAGE=local/private-onyx-myst:2d6e87618f9f-20260719` and `TOR_BASE_IMAGE=docker.io/dockurr/tor:0.4.9.11@sha256:446881b3366cbc2cc5cf8d13a76e3104f60824b7c15343d14defe903ded18f0d` | Myst reconnects and Tor circuit/exit changes can separate a retained cookie from the public IP that established it. Neither currently supplies an authoritative route-generation signal to the cookie store, so this plan deliberately relies on the fixed one-hour ceiling instead of heuristic route coupling. |

## Current Behavior and Blockers

### Current Obscura capabilities that are useful

Obscura gives each CDP WebSocket connection an isolated live browser
context, cookie jar, HTTP client, target set, event thread, and V8 state. The
wrapper's client already opens one WebSocket per navigation, and the
direct-Obscura crawler permits up to ten concurrent `open_url()` fetches
against Obscura's fifteen-connection limit. This removes the need for the
wrapper to allocate browser-context IDs or maintain a worker pool to isolate
simultaneous navigations.

The relevant pinned source is under `reference_repos/obscura/`:

- `crates/obscura-cdp/src/server.rs` clones an immutable startup context for
  each accepted connection. One live connection does not inherit another live
  connection's mutations.
- `crates/obscura-cdp/src/domains/network.rs` implements
  `Network.getCookies`, `Network.setCookie`, `Network.setCookies`,
  `Network.deleteCookies`, and `Network.clearBrowserCookies`.
- `crates/obscura-cdp/src/domains/target.rs` implements browser contexts within
  one connection.

Those capabilities are enough to inject a cookie snapshot into one isolated
navigation and extract its final cookie state. They are not enough to persist
that state safely between connections.

### Blocking Obscura cookie defects

`reference_repos/obscura/crates/obscura-net/src/cookies.rs` tracks
`host_only` internally when it receives a `Set-Cookie` header or a JavaScript
cookie. Its exported `CookieInfo` does not carry that property.
`set_cookies_from_cdp()` consequently imports every cookie with
`host_only: false`. A host-only cookie exported from one connection and
imported into another can be sent to a subdomain.

The same file deliberately uses only an incomplete obvious-suffix check rather
than a complete Public Suffix List. Multi-label suffixes and private suffixes
such as `co.uk` and `github.io` are not comprehensively covered. A wrapper
filter can reject cookies before retention, but it cannot reconstruct lost
host-only metadata after CDP export. Guessing from a leading dot or treating
every cookie as a domain cookie is not acceptable. Treating every cookie as
host-only would be safe but would silently break legitimate domain cookies
and is not the intended browser-compatible feature.

Implementation is blocked until the selected Obscura image passes all of these
black-box requirements through its public CDP endpoint:

1. A host-only cookie set by `app.example.test` exports with unambiguous
   host-only metadata, survives export/import into a new connection, is sent
   to `app.example.test`, and is not sent to `sub.app.example.test`.
2. A valid `Domain=example.test` cookie remains distinguishable, survives
   export/import, and is sent to matching subdomains.
3. Cookie name, value, domain, path, `Secure`, `HttpOnly`, `SameSite`,
   expiration/session status, and deletion semantics survive a round trip.
4. Invalid supercookies for public and private suffixes, including multi-label
   cases, are rejected or can be rejected losslessly by the wrapper.
5. An expired cookie and `Max-Age=0` deletion cannot reappear after import.
6. `Network.setCookies` and `Network.getCookies` operate on the target's
   isolated connection context and do not mutate another simultaneous
   connection.

Add this as a focused selected-image capability test, not a source-version
allowlist. If the image fails, fail the proposed feature's startup gate or
leave it unimplemented; do not activate a reduced cookie model.

### Why Obscura persistence and long-lived connections are not substitutes

Do not enable Obscura `--storage-dir`. It is one browser-wide
persistence destination, not a first-party partition. Connection-close deltas
are merged into persistence state on disk, while newly accepted live
connections continue to clone the startup snapshot rather than another live
connection's current state. It would also require a writable browser mount and
would weaken the current read-only, disposable Obscura runtime contract.

Do not retain one WebSocket or browser context per first-party site. A
long-lived connection would retain cookies, but it would consume the
fifteen-connection limit, require eviction and worker-lifecycle management,
and serialize or complicate same-site concurrency. Browser contexts exist
only inside one WebSocket and disappear with it. Multiple Obscura processes or
profile directories have the same lifecycle and resource problems.

The intended `open_url()` design therefore keeps Obscura's one isolated
connection per navigation and retains only validated cookie snapshots in the
owning Onyx API process. SearXNG's fixed set of five providers permits a
separately implemented bounded live-session design; arbitrary crawler sites do
not.

### Stock Requests/Chromium complication

The stock crawler first uses a fresh `requests.Session` for a URL and its
manually validated redirects. When that response appears blocked, upstream
Onyx can retry the URL with a fresh Playwright Chromium browser context.

Requests and Chromium must not share a cookie bucket:

- Requests does not implement all Chromium cookie behavior, including the
  same browser enforcement of `SameSite`, partitioned cookies, cookie
  prefixes, and JavaScript interactions.
- Moving a clearance or session cookie between their distinct TLS, HTTP,
  JavaScript, and browser fingerprints can be rejected by a site or look like
  session theft.
- A Requests cookie jar and Playwright's cookie representation do not have a
  lossless common model for every current browser cookie attribute.
- The existing fallback already performs a second transport attempt only when
  the Requests result is classified as blocked. Cookie persistence must not
  add another attempt or migrate state at that boundary.

Use two cookie stores. A successful or blocked Requests attempt updates only
the Requests store. A Playwright fallback starts from only the Playwright
store and updates only that store. A site may therefore need one Playwright
navigation to establish browser-specific clearance even when Requests has
already obtained cookies. This limitation is preferable to ambiguous
cross-fingerprint sharing.

The Requests adapter does not currently own the crawler's later
blocked-response classification. It may therefore commit valid cookies from a
response that later causes the existing Playwright fallback. Do not spread a
commit callback through the stock crawler solely to change that behavior. The
one-hour cap and separate Playwright store bound its effect. If live evidence
shows this poisons the Requests path, first add one narrow classified-result
commit boundary and its pinned-source validation; never infer the Playwright
outcome inside `_proxied_get()`.

### Implemented SearXNG exception

SearXNG avoids the unsafe cookie export/import round trip entirely. Each of its
five exact providers owns at most one lazy Obscura connection, reuses fresh
targets within that native context, and closes it after one hour idle. That
implementation is isolated from every `open_url()` adapter and is fully
specified in
[SearXNG provider browser sessions](../implemented/searxng_provider_sessions.md).
It is not a partial implementation of this plan and must not be generalized to
arbitrary crawler sites.

## Enforcement Model

### Exactly one owner at each layer

Cookie enforcement must be divided as follows:

| Concern | Sole owner |
| --- | --- |
| Site-key calculation, one-hour generations, cache bounds, accepted-cookie filtering, and inter-navigation merge | New wrapper `FirstPartyCookieStore` |
| Cookies sent during an Obscura navigation and its redirects/subresources | Obscura's isolated connection cookie jar |
| Import/export CDP commands for one Obscura navigation | Shared Obscura client, only when its caller supplies an explicit cookie snapshot |
| Cookies sent during one stock Requests attempt and its redirects | That attempt's `requests.Session` |
| Cookies sent during one Playwright fallback and its redirects/subresources | That attempt's Playwright browser context |
| URL normalization, private-address denial, DNS, and final routing | Existing URL validators and final-hop policy proxies |

No other layer may clear, partition, copy, or merge cookies. In particular:

- do not add cookie logic to the final-hop proxies, nginx, Myst, Tor, SearXNG,
  or Compose health checks;
- do not call `Network.clearBrowserCookies` on a timer;
- do not combine the wrapper cache's one-hour expiration with a browser worker
  recycle policy;
- do not let both `obscura_crawler_patch.py` and the generic client merge the
  same result; and
- do not leave a second legacy timer or cache in either runtime patch.

Browser-native expiration and the wrapper's retention ceiling are
complementary, not duplicate enforcement. A cookie expires at the earlier of
its own expiry and its site's generation deadline.

### Store instances and namespaces

Create one shared, transport-independent store implementation under
`browser/obscura_client/private_onyx_obscura/` so the exact same key, expiry,
filtering, and merge rules are used by all three API adapters. Keep the CDP
client itself stateless. Instantiate the store exactly three times in the API
process:

```text
open-url-obscura
open-url-stock-requests
open-url-stock-playwright
```

The namespace is part of every key even when store instances are separate.
This prevents a future refactor into one map from collapsing transport
boundaries. Store globals are forbidden in the shared package:
`obscura_crawler_patch.py` and `onyx_crawler_egress_patch.py` own their
explicit instances. The shared client defines lossless cookie value objects
and explicit import/export parameters but owns no cache.

Commit one current Mozilla Public Suffix List snapshot, its provenance/license,
and a pinned checksum alongside the shared store. Use an audited parser in the
Onyx image and prohibit runtime list retrieval. The Onyx patch must
startup-validate that the parser and data work in its pinned image.

### Site key

Compute the key before network activity from the normalized requested URL, not
from DNS and not from the final redirect:

```text
(namespace, normalized scheme class, registrable-domain-or-exact-host)
```

Rules:

1. Normalize the URL through the existing public-only URL normalizer first.
   Cookie logic must not weaken, replace, repeat, or perform DNS for URL
   validation.
2. Canonicalize DNS names to lowercase ASCII IDNA without a trailing dot.
3. Use the registrable domain derived from the wrapper's committed Public
   Suffix List snapshot. Use the same pinned checksum and parser in both
   runtime images; never use a parser's implicit packaged data or a runtime
   network update. Add a startup self-test for known ICANN and private
   multi-label suffixes used by deterministic tests.
4. Use the exact normalized literal for IPv4 and IPv6 addresses. An IPv6
   address is stored without URL brackets.
5. Use the exact normalized host for `.onion` names and other names for which
   the PSL returns no registrable domain.
6. Partition cleartext and secure navigation. The scheme class is `secure`
   for `https`, and `cleartext` for permitted `http`. Do not inject an HTTP
   site's cookies into HTTPS or vice versa at the wrapper level. This is
   intentionally narrower than ordinary browser scheme behavior and avoids
   connecting cleartext/onion and HTTPS identities.
7. A normalization or PSL failure rejects cookie persistence for that
   `open_url()` or search result with a typed, sanitized error. It must not
   create a best-effort host key.

The initial key remains authoritative across redirects. Cookies set by a
same-site redirect may be retained. Cookies belonging to a cross-site redirect
target or third-party subresource may operate inside that one browser/session
attempt, but must be discarded at extraction rather than inserted into another
site bucket. A redirect may not seed a future cache for a site the caller did
not initially select.

### Accepted cookie model

The store must retain a lossless normalized representation containing at
least:

- name and value;
- canonical domain;
- an explicit `host_only` boolean;
- normalized path;
- `Secure` and `HttpOnly`;
- normalized `SameSite`;
- absolute wall-clock expiry or explicit session-cookie status; and
- any partition key or other isolation attribute that the selected Obscura
  and Playwright APIs require to round-trip a cookie safely.

If an adapter cannot represent an attribute without widening where or when a
cookie is sent, it must reject that cookie from persistence. It must not infer,
drop, or weaken a security attribute. Unsupported partitioned cookies should
remain usable inside their original browser attempt but should not be retained
until all adapters and the store have an explicit lossless model.

Before insertion, independently verify that:

- a host-only cookie's domain is exactly the origin host that created it;
- a domain cookie domain-matches the initial site and is not a public or
  private suffix;
- the cookie belongs to the initial site key;
- `Secure` cookies came from, and will only be injected into, a secure scheme;
- the path is absolute and valid;
- the cookie has not expired;
- prefix rules supported by the source browser, including `__Host-` and
  `__Secure-`, remain satisfied; and
- all size and count bounds below are satisfied.

Filtering is defense in depth around browser-native cookie handling. It is not
permission to repair malformed cookies. Drop a rejected cookie from retained
state, record only a reason counter or sanitized warning, and allow it to
remain usable inside the already isolated live attempt. Never log cookie
names, values, full URLs, query strings, or authentication state.

### One-hour generations

Use a monotonic clock for retention lifecycle and a wall clock only to compare
protocol cookie expiry timestamps.

The first accepted cookie inserted into an absent site bucket creates a
generation with:

```text
generation_id = an opaque monotonically increasing integer
created_at = monotonic_now
expires_at = created_at + 3600 seconds
```

The deadline is fixed. Reading, writing, receiving a new cookie, or completing
a navigation does not slide or extend it. At or after `expires_at`, the whole
bucket is atomically removed before it can be read or merged. A later accepted
cookie creates a new generation with a new identifier and deadline.

Every navigation receives an immutable snapshot plus its `generation_id`.
The snapshot also carries its monotonic generation deadline. Before handing
cookies to a transport, create an import view whose effective cookie expiry is
no later than that deadline: clamp persistent-cookie expiry and give session
cookies a temporary absolute transport expiry. This does not change the
store's lossless source value; it prevents a long setup, redirect, or
subresource phase from sending an old-generation cookie after the wrapper
deadline.

The adapter/client must check the snapshot deadline again immediately before
import. If the deadline has arrived, it imports no old cookies and continues
the already authorized navigation with an expired/empty snapshot. Expiration
is normal clearing, not an import failure and not a retry. The transport's
native expiry enforcement covers the narrow interval after that check. These
two steps enforce one store-owned deadline at the import boundary; they are not
separate retention policies.

When a navigation finishes:

- merge its delta only if that exact generation still exists and has not
  expired;
- if the bucket expired while the navigation was in flight, discard the
  entire delta so an old request cannot repopulate the new generation;
- if the navigation began without a bucket, create a generation only when it
  returns at least one accepted cookie; and
- if concurrent navigations both began without a bucket, the first successful
  locked merge creates it and the other merge may join only if its snapshot
  token represented the same explicit “absent generation” epoch. Use a
  per-key epoch counter so an expired/evicted absence cannot be confused with
  the original absence.

Merge by the browser cookie identity tuple:

```text
(canonical domain, host_only, path, name, partition key if supported)
```

Apply explicit deletions as deletions. For concurrent updates within the same
generation, completion order under the store lock wins for the same identity;
updates to unrelated cookie identities must not be lost. Do not hold the store
lock during CDP, HTTP, Playwright, parsing, or logging operations.

Use one daemon sweeper per `FirstPartyCookieStore`, waiting on a condition for
the nearest deadline. Access and merge paths must also expire due buckets
synchronously, so correctness does not depend on scheduler timing. Do not use
one timer or thread per site. Process exit clears all stores without a shutdown
write.

Three dormant sweepers in the API process are acceptable. They can be reduced
to one scheduler only if the store remains the sole lifecycle owner and the
change is demonstrably simpler. Do not add a service, health check, or
periodic wakeup when all stores are empty.

### Resource bounds

Use fixed code constants, validated by unit tests:

| Bound | Required value |
| --- | --- |
| Site generations per store | 128 |
| Cookies per site generation | 64 |
| Serialized bytes per cookie | 4,096 |
| Total serialized retained bytes per store | 1 MiB |
| Site generation lifetime | 3,600 seconds |

Count UTF-8 bytes of every retained field and attribute in the serialized-byte
calculation, not only the name and value. Reject an individual cookie that
exceeds the cookie bound. When a cookie update would exceed a site's count or
the store byte bound, evict whole least-recently-accessed site generations
other than the generation currently being merged. If the current generation
still cannot fit, reject only the excess retained cookie updates and emit one
rate-limited sanitized diagnostic. Site access may update eviction order but
must never extend its one-hour deadline.

Eviction is a privacy-preserving loss of continuity, not permission to retry a
navigation. It must be observable through aggregate counters or sanitized
logs. An invariant violation, serialization ambiguity, or adapter import
failure is different: treat it as a typed cookie-state failure as described
below.

### Error behavior

Cookie-state behavior must never silently become less strict:

- Invalid startup assumptions, missing cookie attributes, unexpected patched
  source shape, or a failed PSL self-test must stop patch/engine installation
  and prevent the affected API process from becoming ready.
- Failure to validate or import a retained snapshot must fail that URL before
  navigation. Do not navigate statelessly and do not add a retry.
- Failure to export or validate state after navigation must discard the
  proposed merge and mark that URL fetch unsuccessful with a sanitized
  cookie-state reason. The network request has already happened; do not repeat
  it.
- A deliberately rejected cookie or bounded-cache eviction does not fail page
  content. It is a defined retention decision, recorded without secrets.
- Expiration is normal behavior and produces no warning.
- An expired-generation merge is silently discarded as the intended race
  result, with an aggregate testable counter if diagnostics exist.

Add a `COOKIE_STATE` failure category to the shared Obscura client only if the
client itself detects the protocol failure. The Onyx patch maps it to a stable
user-safe failure reason. Do not include CDP payloads or cookie material in
exceptions.

## Transport Integration

### Shared Obscura client

Extend `browser/obscura_client/private_onyx_obscura/client.py` and its exported
models with explicit, optional cookie transfer:

```text
fetch(..., cookie_snapshot: CookieSnapshot | None = None,
      return_cookies: bool = False)
```

The exact names may follow the existing client style, but both controls must
default to disabled. The client must not create or access a cache and must not
derive a site key. `CookieSnapshot` contains immutable cookies, the generation
token, and the monotonic deadline needed for the immediate pre-import check;
the client returns the token unchanged and never interprets store epochs.

For an opted-in call:

1. Open the ordinary new CDP WebSocket and create/attach the target exactly as
   today.
2. Enable the required Network domain.
3. Recheck the snapshot's monotonic deadline, derive the expiry-clamped import
   view, and import it with one bounded `Network.setCookies` command after
   target/session creation and before the existing `pre_navigation_guard` and
   `Page.navigate`. Skip the command when the snapshot is empty or has expired.
4. Perform exactly one existing navigation.
5. After response/body/DOM collection and before target or WebSocket teardown,
   issue one `Network.getCookies` command for the same isolated target.
6. Return the lossless final snapshot with the existing fetch result.
7. Include both cookie commands in the existing per-fetch deadline. Do not
   extend Onyx's 120-second tool deadline or the 105-second browser-attempt
   budget.

Cleanup must still close the target and WebSocket on every path. Cookie export
must not suppress the primary protocol failure. Tests must prove that a
default caller which supplies neither option sends no cookie import/export
command. State exists only in the explicit Onyx adapters.

The shared client remains an input to the derived SearXNG image tag even
though SearXNG does not opt into cookie snapshots. Rebuild and validate that
image, prove its provider-session path still issues no cookie import/export
commands, and keep Onyx-specific patch imports out of it.

### Direct-Obscura `open_url()`

In `obscura_crawler_patch.py`, create the
`open-url-obscura` store at patch installation and make `_direct_fetch()` the
sole adapter:

1. Normalize the requested URL using the existing shared client policy.
2. Acquire the existing `ACTIVE_FETCHES` permit and confirm the invocation can
   still navigate.
3. Calculate the site key, take a store snapshot as late as practical, and
   pass only that snapshot to `fetch_sync()`. The client still performs the
   deadline check and expiry clamp immediately before CDP import.
4. After a successful lossless export, filter the returned cookies against the
   original site key and merge the resulting delta with the snapshot token.
5. Continue existing content classification and parsing without another
   navigation.

The store lock must not wrap `ACTIVE_FETCHES`. The current bounded semaphore
of ten and the ten-worker `open_url()` executor remain the only Obscura
capacity controls. Same-site calls may run concurrently; do not add a per-site
lock around browser work.

The invocation deadline remains authoritative. If result collection finalizes
an invocation while a worker is completing, that worker may close its
connection but must not merge cookies after the invocation has been marked
finalized. This is in addition to the store generation check and prevents a
timed-out tool call from altering future state.

### Stock Requests path

In `onyx_crawler_egress_patch.py`, keep `_proxied_get()` as the sole Requests
adapter:

1. Normalize the initial URL using the existing wrapper policy.
2. Calculate the `open-url-stock-requests` key and snapshot.
3. Construct the existing fresh `requests.Session`, set `trust_env = False`,
   recheck the snapshot deadline, and import its expiry-clamped view into that
   session's cookie jar immediately before its first `GET`.
4. Preserve the existing manually validated redirect loop, proxy mapping,
   timeouts, headers, and maximum of ten redirects.
5. Before `session.close()` on every normal response path, export the jar,
   filter it to the original site, and merge its delta.
6. On an exception, export/merge only if the adapter can distinguish a valid
   post-response cookie state. Otherwise discard the delta, close the session,
   and preserve the original exception.

Refactor the repeated `session.close()` branches into one controlled cleanup
path only if doing so keeps response ownership correct. Do not close or consume
the returned `requests.Response` prematurely. Cookie work must not change the
final-hop proxy, URL validation on every redirect, or proxy-owned DNS.

Use `requests.cookies.Cookie` metadata to retain host-only/domain/path
distinctions and validate it independently with the store's PSL rules.
Do not use a module-global `requests.Session`.

### Stock Playwright fallback

The current patch scopes crawler-only URL validation with a `ContextVar` while
calling upstream `fetch_rendered_html()`. Extend this narrow scope rather than
patching every Onyx Playwright caller:

1. Add a crawler-only context value containing the
   `open-url-stock-playwright` key, snapshot, and store token.
2. Startup-validate the pinned `onyx.utils.playwright_fetch` symbols and source
   shape that create the fresh browser context.
3. Wrap the module's `playwright_session` context manager, or the narrowest
   equivalent context-construction symbol in the pinned image. When and only
   when the crawler context value is active, recheck its deadline, add the
   expiry-clamped snapshot with `browser_context.add_cookies()` before
   navigation, and export with `browser_context.cookies()` before context
   close.
4. Filter and merge against the original site after a successful export.
5. Restore the `ContextVar` in `finally` blocks so exceptions cannot leak
   crawler state into another async task or Playwright caller.

Do not put cookie injection into the browser-launch proxy patch. Browser proxy
selection and cookie persistence have different owners. Do not modify Web
Connector or indexing Playwright contexts. Preserve Playwright's fresh
browser/context lifecycle per stock fallback.

The Requests-to-Playwright fallback imports no Requests cookies and exports no
Playwright cookies to Requests. Add an assertion at the adapter boundary so a
future refactor cannot accidentally pass one store's snapshot into the other.

### SearXNG non-interaction

SearXNG must continue using only its implemented live provider sessions. It
must not instantiate `FirstPartyCookieStore`, supply an `open_url()` snapshot,
or receive exported cookies through the future optional client interface.
Tests must keep proving that no state crosses between SearXNG and either
`open_url()` selection. Its effective Compose model must also retain exactly
one Granian request worker; otherwise separate worker processes would create
independent per-provider owners and invalidate the implemented capacity and
serialization contract.

## Implementation Sequence

Do the work in these bounded phases. Stop if any gate fails.

1. **Capability gate**
   - Extend `tests/validate_obscura_runtime.py` and the selected-image wrapper
     to exercise lossless host-only/domain cookie round trips and full PSL
     rejection.
   - Run it against the selected Obscura image.
   - If it fails, leave the feature deferred. An upstream Obscura change is
     required; do not patch `reference_repos/obscura/`.
2. **Store**
   - Add the one authoritative shared store module, pinned PSL data/parser,
     value model, fake-clock seams, bounds, generation tokens, delta merge, and
     diagnostics.
   - Audit the Onyx runtime image for the parser.
   - Add deterministic tests before connecting it to transports.
3. **Obscura adapter**
   - Add stateless explicit cookie import/export to the shared client.
   - Opt in only from direct `open_url()`.
   - Rebuild the derived SearXNG image because it contains the shared client,
     and prove SearXNG remains on its independent live-session behavior.
4. **Stock adapters**
   - Add the Requests store boundary.
   - Add the crawler-scoped Playwright boundary.
   - Keep their stores distinct and retain strict pinned-source validation.
5. **Integration and documentation**
   - Run deterministic, selected-image, concurrency, routing, Docker, and
     Podman checks below.
   - Replace obsolete normative documentation rather than preserving a
     history of prior behavior.
   - Move this plan to `docs/plans/implemented/` only after every completion
     criterion is met.

Expected implementation files are:

- `browser/obscura_client/private_onyx_obscura/client.py`;
- new shared store/value modules and pinned PSL data under
  `browser/obscura_client/private_onyx_obscura/`;
- `browser/obscura_client/private_onyx_obscura/__init__.py` and the owning
  model module if cookie values are separated;
- `onyx/patches/sitecustomize_api_server/obscura_crawler_patch.py`;
- `onyx/patches/sitecustomize_api_server/onyx_crawler_egress_patch.py`;
- focused tests under `tests/`;
- the existing Makefile-derived SearXNG input hash; and
- the documentation listed below.

Do not rely on the selected base images accidentally containing the same PSL
data. The data snapshot is a committed shared input. Add a parser dependency
only where an audited selected image lacks it, update the committed input, and
regenerate the hashed lock through `make upgrade-python-deps`; runtime
installation and runtime PSL download are forbidden.

## Test Criteria

The test pyramid is intentionally divided by owner. Test store semantics once,
then test only each adapter's boundary. Do not repeat the complete TTL matrix
through every browser.

### Deterministic store tests

Add focused unit tests with injected monotonic and wall clocks for:

- registrable-domain keys for ordinary domains, subdomains, `co.uk`,
  `github.io`, uppercase/trailing-dot input, and IDNA;
- exact-host keys for IPv4, IPv6, and `.onion`;
- separate secure and cleartext keys;
- host-only versus domain-cookie matching;
- rejection of public/private-suffix supercookies and cookies outside the
  initial site;
- preservation of path, `Secure`, `HttpOnly`, `SameSite`, expiry/session state,
  and deletion;
- natural cookie expiry before the one-hour ceiling;
- a fixed, non-sliding deadline at 3,600 seconds, including exact-boundary
  behavior;
- expiry-clamped import views for persistent and session cookies without
  mutating the stored source values;
- synchronous expiration when a delayed sweeper has not run;
- physical removal by the single sweeper without a per-site timer;
- generation-token rejection when an old request completes after expiry,
  eviction, or replacement;
- two simultaneous first inserts into an absent bucket;
- deterministic completion-order conflict resolution for the same cookie and
  preservation of unrelated concurrent updates;
- all count/byte bounds, whole-site LRU eviction, and no TTL extension on LRU
  access;
- sanitized diagnostics containing no cookie name, value, full URL, query, or
  header; and
- clean process/store shutdown behavior without persistence writes.

Add one barrier-based stress test with ten simultaneous snapshots and merges
for the same site and another for ten different sites. Assert no deadlock, no
lost unrelated update, no stale-generation resurrection, and no lock held
during simulated network work. Larger concurrency or long soak tests are not
required for the store.

### Shared Obscura client tests

Extend the existing fake-CDP tests to prove:

- one opted-in fetch sends exactly one `Network.setCookies` before
  `Page.navigate` and one `Network.getCookies` before teardown;
- the same target/session receives import, navigation, and export;
- an empty snapshot has defined behavior without an unnecessary import
  command;
- a snapshot expiring during CDP setup imports no old cookie, while a snapshot
  crossing its deadline after import is bounded by transport cookie expiry;
- a default call sends neither cookie command;
- the existing pre-navigation guard runs after successful cookie import;
- import failure performs no navigation and returns a typed failure;
- export failure performs no retry, returns a typed failure, and still tears
  down the target and WebSocket;
- cookie commands consume the existing request deadline;
- primary navigation failures are not hidden by export/cleanup failures; and
- cookie payloads cannot appear in exception or log text.

Add a default-caller assertion that no state is retained unless an adapter
explicitly requests cookie transfer. Do not duplicate the store's PSL, TTL, or
concurrency matrix in client tests.

### SearXNG non-interaction tests

Extend the shared-client and SearXNG session tests only as needed to prove
that adding the optional snapshot interface does not make a provider call
issue cookie import/export commands, instantiate an `open_url()` store, alter
its live-session lifecycle, or share state with direct Obscura `open_url()`.
The implemented provider-session plan owns the rest of the search validation
matrix.

### Onyx patch tests

For direct Obscura, extend `tests/test_onyx_obscura_crawler_patch.py` to cover:

- one snapshot and merge per URL;
- the original navigation site's key across same-site and cross-site
  redirects;
- no merge after invocation finalization;
- ten `open_url()` URLs proceed concurrently under the existing semaphore and
  executor;
- the eleventh browser use cannot exceed the ten-navigation wrapper cap;
- cookie-store locks do not serialize ten same-site browser calls;
- cookie-state failures produce stable unsuccessful `WebContent` results with
  no hidden retry; and
- pinned-source validation and the 120/105-second deadline relationship remain
  intact.

For the stock path, extend `tests/test_onyx_crawler_egress_patch.py` to cover:

- Requests snapshot injection before the first `GET`;
- Requests and Playwright skip an expired snapshot and clamp imported cookie
  expiry to the generation deadline;
- same-session redirects and a merge before session close;
- cross-site redirect cookies discarded from retention;
- response ownership and close behavior on terminal, missing-location,
  redirect-limit, and exception paths;
- unchanged proxy arguments, `trust_env = False`, URL validation, and
  proxy-owned DNS;
- Playwright injection/export only while the crawler `ContextVar` is active;
- unrelated Playwright use receives no cookies and no export callback;
- context cleanup and `ContextVar` reset on every exception;
- distinct Requests and Playwright namespaces, with no copy during fallback;
- no extra Requests or Playwright attempt introduced by cookies; and
- startup refusal when the pinned Playwright context-construction source shape
  changes.

Do not test upstream Requests or Chromium cookie algorithms exhaustively.
Adapter tests establish correct conversion and ownership; the tagged-image
integration tests establish the supported runtime contract.

### Selected-image and offline integration tests

Use an isolated, engine-local HTTPS fixture with controlled host aliases and
endpoints that:

- set and reflect host-only and domain cookies;
- set path, secure, HTTP-only, same-site, session, short-expiry, and deletion
  variants;
- perform same-site and cross-site redirects;
- load a third-party subresource;
- delay completion behind a barrier; and
- report only boolean receipt assertions, never secrets.

The selected Obscura image test must first enforce the blocking capability
gate. Then exercise two separate CDP connections to prove same-site continuity,
cross-site isolation, host-only subdomain denial, valid domain-cookie subdomain
delivery, third-party/cross-redirect non-retention, and simultaneous
connection isolation.

The derived SearXNG image must prove its existing provider continuity and
provider/`open_url()` isolation still hold and that it does not use the new
snapshot interface.

The pinned Onyx patch-image tests must exercise Requests and Playwright
separately with the same semantic fixture. They must prove continuity within
each store and no continuity across stores. Use a fake store clock or direct
store harness for the exact one-hour boundary; do not add a production TTL
override and do not make an image test sleep for an hour.

Run:

```text
make check
make test-obscura-image
make searxng-build
make test-patch-images
```

Use the Makefile-selected container engine for every focused image test. These
targets must validate exact local selected images without an implicit pull or
substitution.

### Routing and failure regression tests

Cookie tests do not replace the existing privacy-routing tests. Run the
affected deterministic network-isolation suite and prove:

- no application receives a new network, direct egress route, Obscura storage
  mount, browser profile mount, or proxy credential;
- URL validation still occurs on the initial URL and every stock redirect;
- final-hop DNS and private-address rejection remain authoritative;
- stopping the selected policy bridge produces a bounded fetch failure without
  direct, alternate-transport, or cookie-bypassing retry;
- Obscura CDP failure does not fall back to stock Requests/Playwright;
- Requests or Playwright failure does not switch to Obscura; and
- cache eviction, expiry, or cookie rejection never triggers a network retry.

## Live Validation Criteria

Live validation confirms wiring and resource behavior, not every deterministic
cookie rule. Use a controlled public HTTPS test origin with at least two
registrable sites or delegated subdomains for `open_url()`. Its response must
expose only test-specific nonce receipt, never production cookies. Search
provider live-session semantics remain outside this deferred plan.

Validate these cases:

1. Start lite mode with the configured Myst route and
   `ONYX_AGENT_USE_OBSCURA_BROWSER=true`.
2. Issue one `open_url()` that sets a test cookie and a second for the same
   site that requires it. Confirm a different first-party site does not receive
   it.
3. Run ten concurrent `open_url()` targets for the same site and then for
   different sites. Confirm all ten start within the existing cap, the API
   remains responsive, Obscura reports no connection-state crossover, and
   memory/thread growth remains within the fixed store design.
4. Restart with `ONYX_AGENT_USE_OBSCURA_BROWSER=false`. Repeat continuity for
   the Requests path. Trigger the existing blocked-response classification to
   exercise Playwright, then repeat within Playwright and prove the Requests
   cookie was not shared into it.
5. Stop the public egress bridge and then the Obscura CDP path in their
   applicable modes. Confirm fail-closed behavior and no fallback/retry.
6. Inspect API, Obscura, and policy-proxy logs for bounded errors,
   correct adapter selection, absence of cookie data, and absence of new
   routing warnings.
7. Stop the stack with the matching `make down-lite` and report its final
   state.

Repeat the affected lite-mode matrix under Docker and rootless Podman. Use
`CONTAINER_BIN=podman` consistently for Podman and follow
[Podman support](../../podman_suport.md); do not mix Docker inspection into
Podman evidence. Full mode uses the same API request path, so render its
effective Compose model and run a startup smoke test, but do not repeat the
cookie semantic matrix unless full-mode wiring differs.

Do not wait an hour during live validation. Exact expiry, clearing, and
in-flight expiry races belong to fake-clock deterministic tests. A process
restart may additionally demonstrate that caches are non-persistent, but it
does not substitute for the one-hour tests.

Record:

- selected image IDs/tags and container engine versions;
- whether Myst, Tor, upstream proxy, or explicit no-VPN mode carried the live
  checks;
- peak API and Obscura thread counts and approximate memory before and during
  ten-way `open_url()`;
- the exact deterministic, image, and live commands run;
- any omitted case and its reason; and
- the explicit final stack state.

## Documentation Required at Implementation

Update every applicable normative document in the implementation change.
Replace obsolete statements; do not retain a history of the stateless
behavior or prior Obscura releases.

- [README.md](../../../README.md): briefly state the user-visible per-site,
  per-transport, process-local one-hour cookie behavior, the stock
  Requests/Playwright separation, and the lack of per-user isolation. Keep
  implementation details out.
- [Request handling](../../request_handling.md): become the complete normative
  authority for keys, scheme separation, accepted cookie state, fixed
  generation lifetime, concurrency, redirect handling, transport partitions,
  failure behavior, and process/user limitations.
- [Onyx patch information](../../onyx_patch_info.md): document why the
  `FirstPartyCookieStore`, direct-Obscura adapter, Requests adapter, and
  crawler-scoped Playwright context hook remain necessary, including strict
  startup source validation and sole ownership boundaries.
- [Onyx patches upgrade](../../onyx_patches_upgrade.md): add the Obscura
  lossless-cookie capability audit, pinned Onyx Requests/Playwright symbol and
  source-shape audit, pinned PSL data/parser audit, selected-image commands,
  and ten-concurrent-call regression.
- [Internal network security](../../internal_network_security.md): document
  cookies as bounded process-local inter-request state, not a routing control;
  state that the cache is service-global rather than per-user and cannot grant
  LAN/private access or override final-hop validation.
- [VPN routing and restricted egress](../../vpn_routing_and_proxies.md):
  explicitly state that cookies do not change route selection, DNS ownership,
  proxy authentication, destination validation, or fail-closed behavior, and
  that no proxy enforces cookie policy.
- [Resource minimization](../../resource_minimization.md): document the three
  API stores, fixed byte/site/cookie limits, dormant condition waiters, no
  per-domain workers/timers, no persistent storage, and the ten-call resource
  validation.
- [Podman support](../../podman_suport.md): add the affected selected-image and
  live compatibility checks and state that no Podman mount, volume,
  capability, socket, or lifecycle override is introduced.
- `.env.wrapper.example`: update only the existing
  `ONYX_AGENT_USE_OBSCURA_BROWSER` explanation to describe the two resulting
  `open_url()` cookie transports. Do not add a TTL or transport-sharing
  setting. If privacy review instead requires one enable boolean, document
  that single setting here and in the request-handling document.
- `AGENTS.md`: update the repository-wide request-handling invariant only if
  maintainers want this boundary enforced for future work. The invariant
  should say that `open_url()` retains bounded first-party cookies only in its
  owning adapters, with separate stores for every transport and no per-user
  claim.

When implementation is complete, move this file to
`docs/plans/implemented/cookie_isolation.md`. Keep it as an implementation
record, but do not append a progress journal or preserve superseded normative
claims in the documents above.

## Completion Criteria

This plan is complete only when all of the following are true:

- the selected Obscura image passes the lossless host-only/domain/PSL
  capability gate;
- privacy review explicitly accepts process-local, service-global rather than
  per-user continuity;
- one authoritative store implementation enforces the exact site key,
  accepted-cookie model, bounds, generation semantics, and sanitized errors;
- Obscura `open_url()`, Requests, and Playwright use three distinct namespaces
  and no other `open_url()` layer performs cookie persistence;
- the shared Obscura client remains stateless by default while only direct
  `open_url()` explicitly opts into transfer;
- all three store instances retain same-site cookies for no more than 3,600
  seconds and discard stale in-flight merges;
- Requests and Playwright never exchange cookies;
- SearXNG remains on its independent implemented provider-session design and
  never exchanges state with `open_url()`;
- ten concurrent Obscura `open_url()` calls pass without new per-site
  serialization or a substantial unbounded resource increase;
- no extra navigation, retry, fixed sleep, browser worker, persistent mount,
  sidecar, or routing path was added;
- deterministic, selected-image, routing, Docker, Podman, and live criteria
  pass or every omission is explicitly reported;
- all listed documentation describes only the implemented behavior
  consistently; and
- the matching stack is left in an explicit, reported state.
