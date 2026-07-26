# First-Party Cookie Isolation for Browser Search and `open_url()`

> **Status: deferred.** This document is an implementation plan, not a
> description of current behavior. Both supported `open_url()` transports
> currently start each navigation without cookies retained by an earlier
> navigation. SearXNG browser searches also start without cookies from an
> earlier search.
> The normative behavior remains documented in
> [Request handling](../../request_handling.md) until this plan is implemented,
> validated, documented, and moved to `docs/plans/implemented/`.
>
> Do not enable the Obscura part of this plan against Obscura v0.1.11. Its CDP
> cookie export/import path does not preserve whether a cookie is host-only,
> and its cookie-domain validation does not use a complete Public Suffix List.
> Re-importing an exported host-only cookie can therefore widen it to
> subdomains. The capability gate below must pass against a later pinned
> Obscura release before implementation is enabled.

## Goal

Give SearXNG browser search and each `open_url()` transport a bounded,
process-local cookie cache that:

- retains only cookies belonging to the first-party site of the requested
  navigation;
- never makes one first-party site's cookies available to another site;
- gives every retained site generation a hard maximum lifetime of one hour;
- preserves shorter cookie expiration and explicit cookie deletion;
- supports ten concurrent Obscura `open_url()` navigations without serializing
  browser work;
- keeps the stock Requests and Playwright cookie stores separate;
- retains SearXNG cookies only within the same custom provider and first-party
  site;
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
| SearXNG through Obscura | One in-memory cookie store | Same SearXNG process, same custom provider, same schemeful first-party site, and same live one-hour generation |
| Web Connector, indexing, or another Playwright caller | None added by this work | Existing upstream behavior remains authoritative |

“Process-local” is deliberate. This plan does not promise a shared cookie
session across API or SearXNG replicas, process restarts, image replacement, or
stack restart. If either deployment later runs multiple worker processes, each
worker must have an independent store and one-hour lifecycle. Do not add a
database, broker, sidecar, shared file, or sticky load-balancing scheme to hide
that boundary.

This feature is site/provider isolation, not user isolation. Neither the
current `open_url()` call chain nor the Onyx-to-SearXNG search request
propagates a stable authenticated-user identifier to these adapters. In a
multi-user deployment, callers served by the same API or SearXNG process could
therefore reuse cookies for the same first-party site/provider. For search,
that gives a provider a stable identifier with which it can correlate all
users' queries that reach that provider during the generation. Implementation
must remain deferred unless the deployment's single-user trust assumption is
accepted and documented, or a separate project first supplies an
authenticated principal namespace through both request chains. Never describe
this plan as per-user isolation.

## Scope and Non-Goals

This plan applies only to:

- the five custom SearXNG offline engines through
  `searxng/engines/_obscura.py`;
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
- change SearXNG provider scheduling, round-robin behavior, reservations,
  cooldowns, suspension, retries, or last-resort scoring;
- share search cookies between Google, Brave, DuckDuckGo, Startpage, and Bing;
- retain cookies from a search response classified as rate-limited, CAPTCHA,
  access-denied, a consent terminal, or an unexpected terminal origin;
- change Onyx indexed-result reuse, ingestion, or semantic retrieval;
- add extra prefetches, browser navigations, retry attempts, or fixed sleeps;
- use Obscura's `--storage-dir`; or
- retain compatibility with any experimental or earlier cookie shim.

Do not add a user-facing TTL setting. The retention ceiling is exactly 3,600
seconds. Do not add switches for sharing transports or search providers. If a
deployment-wide enable/disable switch is considered necessary during privacy
review, add at most one strictly parsed browser-cookie-continuity boolean and
keep every partition mandatory. Do not create separate tuning switches for
search and `open_url()`. The preferred implementation has no new setting:
this behavior becomes the supported behavior for browser search and both
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
| Obscura | `OBSCURA_IMAGE=docker.io/h4ckf0r0day/obscura:0.1.11`; `reference_repos/obscura` at `v0.1.11` | Owns per-WebSocket state isolation, the fifteen-connection cap, CDP cookie import/export, cookie-domain validation, target lifecycle, and optional storage persistence. Its lossy host-only round trip is the principal implementation blocker. |
| Onyx application | `ONYX_IMAGE_TAG=v4.2.8`; `reference_repos/onyx` at `v4.2.8` | Owns `open_url()` orchestration, the stock Requests-first/Playwright-fallback flow, the five-worker stock crawler, the 120-second tool deadline, and the runtime symbols wrapped by both Onyx patches. |
| Onyx crawler libraries | Requests `2.33.0`, Playwright `1.58.0`, and `publicsuffix2` `2.20191221` in the Onyx `uv.lock` | Determine Requests cookie-jar metadata, Chromium context cookie conversion, and the parser available to runtime patches. The old parser package's implicit PSL data is not accepted as the shared current snapshot proposed here. |
| SearXNG | `SEARXNG_IMAGE_TAG=2026.7.15-7b2199ecd`, with the wrapper image derived from the committed SearXNG inputs | Owns offline-engine loading, one-process provider state, round-robin/fan-out orchestration, provider failure suspension, and retry/scoring behavior that cookie persistence must not duplicate or influence. |
| Shared SearXNG browser dependencies | Playwright `1.58.0` and websockets `15.0.1` from `searxng/requirements.in` and its hashed lock | Shape the derived image's browser/CDP runtime and dependency audit. Any PSL parser added for the shared store must be pinned and locked by the same workflow. |
| Egress identity components | `MYST_IMAGE=local/private-onyx-myst:2d6e87618f9f-20260719` and `TOR_BASE_IMAGE=docker.io/dockurr/tor:0.4.9.11@sha256:446881b3366cbc2cc5cf8d13a76e3104f60824b7c15343d14defe903ded18f0d` | Myst reconnects and Tor circuit/exit changes can separate a retained cookie from the public IP that established it. Neither currently supplies an authoritative route-generation signal to the cookie store, so this plan deliberately relies on the fixed one-hour ceiling instead of heuristic route coupling. |

## Current Behavior and Blockers

### Obscura v0.1.11 improvements that are useful

Obscura v0.1.11 gives each CDP WebSocket connection an isolated live browser
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
that state safely between connections in v0.1.11.

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

Do not enable Obscura `--storage-dir`. In v0.1.11 it is one browser-wide
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

The intended design therefore keeps Obscura's one isolated connection per
navigation and retains only validated cookie snapshots in the owning Onyx API
or SearXNG process.

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

### SearXNG benefits and complications

Persisting ordinary cookies from accepted result pages can stabilize provider
locale/preferences and retain short-lived anti-abuse state that tells a
provider consecutive searches belong to one browser session. It may therefore
reduce some 429s and CAPTCHAs. This is an expected benefit to measure, not a
guarantee: provider defenses also use exit IP, TLS/HTTP fingerprints, query
rate, behavior, and longer-lived server-side identifiers. A consent-terminal
response is not accepted and cannot establish retained consent under this
plan, because the wrapper must not make a consent choice on the user's behalf.

The implementation has these complications:

- The same Obscura v0.1.11 host-only/PSL export defect blocks safe SearXNG
  persistence. Search cannot use a reduced or inferred cookie representation.
- One SearXNG process serves every Onyx user without a user identity in the
  engine call. A provider cookie would correlate queries across users during
  its generation. Provider partitioning prevents cross-provider disclosure but
  does not solve this cross-user boundary.
- A persisted anti-abuse cookie can also preserve a challenged or throttled
  browser identity. Never commit cookies from a navigation explicitly
  classified as 429, CAPTCHA, access-denied, consent-only, or an unexpected
  terminal origin. This does not guarantee that every harmful cookie is
  recognizable.
- Myst reconnects, Tor circuit/exit changes, and upstream-proxy changes can
  leave a cookie associated with an earlier public IP. The request path has no
  authoritative route-generation event to consume. Do not add heuristic VPN
  log watching or couple cookie lifecycle to the scheduler. The fixed
  one-hour ceiling and process restart are the only clearing mechanisms in
  this scope.
- Search queries rotate among five providers by default. Each provider will
  accumulate useful continuity only when selected again within its current
  generation. Last-resort Bing state remains separate even when Bing confirms
  another provider's results.
- SearXNG's custom engine module and the Onyx runtime do not currently share a
  committed, current Public Suffix List data file. A single pinned PSL snapshot
  and parser must be available in both images without runtime download.
- SearXNG has one Granian process today. If its worker topology changes, each
  process will have separate provider cookies unless a separately reviewed
  user-aware state service is designed. This plan does not add one.

Cookie state must remain outside provider scheduling. A cookie-cache hit or
miss cannot change provider availability, round-robin cursor position,
cooldown, suspension duration, or scoring. A cookie-state failure is reported
as an ordinary engine response failure; the existing round-robin orchestration
may then try a different untried provider exactly as it does for another
unresponsive result. Cookie code must not initiate that retry itself.

## Enforcement Model

### Exactly one owner at each layer

Cookie enforcement must be divided as follows:

| Concern | Sole owner |
| --- | --- |
| Site-key calculation, one-hour generations, cache bounds, accepted-cookie filtering, and inter-navigation merge | New wrapper `FirstPartyCookieStore` |
| Cookies sent during an Obscura navigation and its redirects/subresources | Obscura's isolated connection cookie jar |
| Import/export CDP commands for one Obscura navigation | Shared Obscura client, only when its caller supplies an explicit cookie snapshot |
| Search provider success/block classification and merge decision | `searxng/engines/_obscura.py` |
| Cookies sent during one stock Requests attempt and its redirects | That attempt's `requests.Session` |
| Cookies sent during one Playwright fallback and its redirects/subresources | That attempt's Playwright browser context |
| URL normalization, private-address denial, DNS, and final routing | Existing URL validators and final-hop policy proxies |

No other layer may clear, partition, copy, or merge cookies. In particular:

- do not add cookie logic to the final-hop proxies, nginx, Myst, Tor, SearXNG
  scheduler patch, or Compose health checks;
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
filtering, and merge rules are used in both derived images. Keep the CDP client
itself stateless. Instantiate the store exactly three times in the API process:

```text
open-url-obscura
open-url-stock-requests
open-url-stock-playwright
```

Instantiate it once more in the SearXNG process. Its namespace is
`searxng-search:<engine_name>`, where `engine_name` must be one of the exact
keys in `TERMINAL_HOSTS`. The provider name is part of the namespace even
though the current providers use different registrable domains. This prevents
a future alias, redirect, or provider-domain consolidation from sharing state.

The namespace is part of every key even when store instances are separate.
This prevents a future refactor into one map from collapsing transport or
provider boundaries. Store globals are forbidden in the shared package:
`obscura_crawler_patch.py`, `onyx_crawler_egress_patch.py`, and
`searxng/engines/_obscura.py` own their explicit instances. The shared client
defines lossless cookie value objects and explicit import/export parameters
but owns no cache.

Commit one current Mozilla Public Suffix List snapshot, its provenance/license,
and a pinned checksum alongside the shared store. Use one audited parser in
both images and prohibit runtime list retrieval. If the selected SearXNG image
does not already contain the parser, add its exact dependency to
`searxng/requirements.in`, regenerate `searxng/requirements.txt`, and validate
the derived image. The Onyx patch must startup-validate that the same parser
and data work in its pinned image. Do not allow the two images to derive site
keys from different PSL snapshots.

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

Three dormant sweepers in the API process and one in the SearXNG process are
acceptable. They can be reduced to one scheduler per process only if the store
remains the sole lifecycle owner and the change is demonstrably simpler. Do
not add a service, health check, or periodic wakeup when all stores are empty.

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
  and prevent the affected API or SearXNG process from becoming ready.
- Failure to validate or import a retained snapshot must fail that URL before
  navigation. Do not navigate statelessly and do not add a retry. SearXNG's
  existing orchestration may select another untried provider after receiving
  the typed engine failure.
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
user-safe failure reason; SearXNG maps it to an ordinary unsuspended engine
response failure rather than a provider rate-limit/block suspension. Do not
include CDP payloads or cookie material in exceptions.

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
command. State exists only in the explicit Onyx and SearXNG adapters.

Because the shared client and store are inputs to the derived SearXNG image
tag, rebuild and validate that image. Keep Onyx-specific patch imports out of
the SearXNG image.

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

### SearXNG custom engines

Make `searxng/engines/_obscura.py` the sole search adapter and instantiate one
store when the module loads. Do not add cookie code to the five provider parser
modules or `searxng/patches/sitecustomize.py`.

Inside the existing `_lease(engine_name, reservation_token)`:

1. Validate `engine_name` against `TERMINAL_HOSTS`.
2. Normalize `target_url`, form namespace
   `searxng-search:<engine_name>`, calculate its site key, and take a snapshot.
3. Pass the snapshot and request final cookies through the shared client's
   explicit cookie interface.
4. Retain the provider lease through cookie export and store decision so the
   existing one-active-navigation-per-provider rule remains authoritative.
5. Perform the existing terminal-host, status, challenge, DOM-presence, and
   block-marker classification.
6. If the result is accepted by those checks, filter exported cookies against
   the original site and merge the delta before returning the DOM.
7. If it is classified as 429, CAPTCHA, access denied, consent terminal,
   unexpected terminal origin, HTTP failure, empty DOM, or cookie-state
   failure, discard the delta.

An engine-specific parser mismatch happens after `_obscura.navigate()` returns
and is not a reliable cookie-block signal; the shared adapter may already have
committed cookies from a terminal host with no block markers. Do not add a
transaction callback through all five parser modules solely to defer that
merge. If live evidence shows parser-mismatch pages poison provider state,
revisit this decision with a single explicit adapter result/commit interface
rather than five independent cookie implementations.

Provider rotation does not delete or move cookies. Google state remains in its
own generation while Brave is selected, and is available if Google is selected
again before that fixed generation expires. Round-robin disabled fan-out may
use up to five provider namespaces concurrently, but the existing per-provider
lease still prevents concurrent navigation to the same provider.

The scheduler remains unaware of cache contents. Existing typed provider
failures, suspensions, unresponsive-engine reporting, fallback to an untried
provider, cooldowns, and last-resort scoring are unchanged. Cookies never cross
between SearXNG and either `open_url()` selection even when their registrable
domains match.

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
   - Audit both runtime images for the parser and update the SearXNG hashed
     dependency lock if required.
   - Add deterministic tests before connecting it to transports.
3. **Obscura adapter**
   - Add stateless explicit cookie import/export to the shared client.
   - Opt in from direct `open_url()` and the shared SearXNG engine adapter with
     separate store instances/namespaces.
   - Rebuild both affected derived images and prove cross-caller and
     cross-provider separation.
4. **Search integration**
   - Add provider-scoped snapshot/validated-merge behavior only in
     `searxng/engines/_obscura.py`.
   - Preserve provider leases, classification, scheduler behavior, and existing
     retry ownership.
5. **Stock adapters**
   - Add the Requests store boundary.
   - Add the crawler-scoped Playwright boundary.
   - Keep their stores distinct and retain strict pinned-source validation.
6. **Integration and documentation**
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
- `searxng/engines/_obscura.py`;
- `searxng/requirements.in` and `searxng/requirements.txt` only if the audited
  selected image lacks the chosen PSL parser;
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

### SearXNG adapter and scheduler tests

Extend `tests/test_searxng_obscura_scheduling.py`,
`tests/test_searxng_obscura_engines.py`, and the relevant patch tests to prove:

- each `TERMINAL_HOSTS` provider produces a distinct
  `searxng-search:<engine_name>` namespace;
- a provider snapshot is imported before its one navigation and a validated
  result is merged before its lease is released;
- cookies persist when round robin selects the same provider again within its
  generation;
- selecting another provider neither reads, deletes, nor extends the first
  provider's generation;
- round-robin-disabled fan-out uses five independent namespaces;
- 429, CAPTCHA, access-denied, consent-terminal, unexpected-terminal,
  HTTP-failure, empty-DOM, and cookie-state results do not merge;
- a valid terminal response that later encounters the documented
  engine-parser-mismatch boundary follows the explicitly accepted merge
  semantics;
- cookie-state failure is not mapped to a provider-block suspension, while
  existing orchestration may retry one untried provider;
- a cookie hit/miss never changes provider availability, reservation,
  `last_start`, three-second cooldown, round-robin cursor, attempted-provider
  set, last-resort choice, or scoring;
- the provider lease remains held through cookie export/merge decision;
- no query string, cookie payload, or complete target URL enters logs; and
- no cookie state crosses to direct Obscura `open_url()`.

Do not repeat the store expiry matrix in scheduler tests. Use fake stores and
assert adapter calls and classification ownership.

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

The derived SearXNG image must exercise every custom provider adapter against
controlled provider aliases. Prove continuity within a provider, isolation
between providers and `open_url()`, no commit for each explicit block class,
and unchanged round-robin/fan-out navigation counts.

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
- cache eviction, expiry, or cookie rejection never triggers a network retry;
  SearXNG's already-existing retry after an unresponsive provider remains the
  only applicable orchestration.

## Live Validation Criteria

Live validation confirms wiring and resource behavior, not every deterministic
cookie rule. Use a controlled public HTTPS test origin with at least two
registrable sites or delegated subdomains for `open_url()`. Its response must
expose only test-specific nonce receipt, never production cookies. Search
provider cookie semantics are proved by the selected-image fixture because the
five production engine adapters deliberately allow only their real provider
origins.

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
5. Exercise each real SearXNG provider with a harmless diagnostic query and
   repeat at least one provider after rotation. Confirm ordinary results,
   unchanged reservations/cooldowns/navigation counts, no cookie-state error,
   and no secret-bearing log output. Treat any reduction in 429/CAPTCHA rate as
   observational only; do not claim one live run proves the benefit.
6. Stop the public egress bridge and then the Obscura CDP path in their
   applicable modes. Confirm fail-closed behavior and no fallback/retry.
7. Inspect API, SearXNG, Obscura, and policy-proxy logs for bounded errors,
   correct adapter selection, absence of cookie data, and absence of new
   routing warnings.
8. Stop the stack with the matching `make down-lite` and report its final
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
- peak API, SearXNG, and Obscura thread counts and approximate memory before
  and during ten-way `open_url()` plus ordinary provider rotation;
- the exact deterministic, image, and live commands run;
- any omitted case and its reason; and
- the explicit final stack state.

## Documentation Required at Implementation

Update every applicable normative document in the implementation change.
Replace obsolete statements; do not retain a history of the stateless
behavior or prior Obscura releases.

- [README.md](../../../README.md): briefly state the user-visible per-site,
  per-transport/provider, process-local one-hour cookie behavior, the stock
  Requests/Playwright separation, the lack of per-user isolation, and the
  search correlation/privacy tradeoff. Keep implementation details out.
- [Request handling](../../request_handling.md): become the complete normative
  authority for keys, scheme separation, accepted cookie state, fixed
  generation lifetime, concurrency, redirect handling, transport partitions,
  search provider partitions/commit rules, failure behavior, and process/user
  limitations.
- [Onyx patch information](../../onyx_patch_info.md): document why the
  `FirstPartyCookieStore`, direct-Obscura adapter, Requests adapter, and
  crawler-scoped Playwright context hook remain necessary; also document the
  SearXNG shared-engine adapter and identify strict startup source validation
  and sole ownership boundaries.
- [Onyx patches upgrade](../../onyx_patches_upgrade.md): add the Obscura
  lossless-cookie capability audit, pinned Onyx Requests/Playwright symbol and
  source-shape audit, SearXNG provider partition/classification audit, pinned
  PSL data/parser audit, selected-image commands, and ten-concurrent-call
  regression.
- [Internal network security](../../internal_network_security.md): document
  cookies as bounded process-local inter-request state, not a routing control;
  state that the cache is service-global rather than per-user and cannot grant
  LAN/private access or override final-hop validation.
- [VPN routing and restricted egress](../../vpn_routing_and_proxies.md):
  explicitly state that cookies do not change route selection, DNS ownership,
  proxy authentication, destination validation, or fail-closed behavior, and
  that no proxy enforces cookie policy.
- [Resource minimization](../../resource_minimization.md): document the three
  API stores plus one SearXNG store, fixed byte/site/cookie limits, dormant
  condition waiters, no per-domain workers/timers, no persistent storage, and
  the ten-call/provider-rotation resource validation.
- [Podman support](../../podman_suport.md): add the affected selected-image and
  live compatibility checks and state that no Podman mount, volume,
  capability, socket, or lifecycle override is introduced.
- `.env.wrapper.example`: update only the existing
  `ONYX_AGENT_USE_OBSCURA_BROWSER` explanation to describe the two resulting
  `open_url()` cookie transports and the SearXNG section to describe
  provider-scoped continuity. Do not add a TTL, transport-sharing, or
  provider-sharing setting. If privacy review instead requires one enable
  boolean, document that single setting here and in the request-handling
  document.
- `AGENTS.md`: update the repository-wide request-handling invariant only if
  maintainers want this boundary enforced for future work. The invariant
  should say that browser search and `open_url()` retain bounded first-party
  cookies only in their owning adapters, with separate stores for every
  transport/provider and no per-user claim.

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
- every custom SearXNG provider uses its own namespace and commits only a
  response accepted by the shared engine adapter;
- the shared Obscura client remains stateless by default while the direct
  `open_url()` and SearXNG adapters explicitly opt into transfer;
- all four store instances retain same-site cookies for no more than 3,600
  seconds and discard stale in-flight merges;
- Requests and Playwright never exchange cookies;
- SearXNG never exchanges cookies with another provider or `open_url()`, and
  cache state never affects scheduler decisions;
- ten concurrent Obscura `open_url()` calls pass without new per-site
  serialization or a substantial unbounded resource increase;
- no extra navigation, retry, fixed sleep, browser worker, persistent mount,
  sidecar, or routing path was added;
- deterministic, selected-image, routing, Docker, Podman, and live criteria
  pass or every omission is explicitly reported;
- all listed documentation describes only the implemented behavior
  consistently; and
- the matching stack is left in an explicit, reported state.
