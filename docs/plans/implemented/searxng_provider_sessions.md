# SearXNG Provider Browser Sessions

> **Status: implemented; pending review/merge.** This is a review artifact, not
> long-term documentation. Normative behavior lives in
> [Request handling](../../request_handling.md); the resource inventory only
> records consequences in
> [Resource minimization](../../resource_minimization.md). After merge, preserve
> this plan as a historical decision/acceptance record and update the owning
> documents—not this plan—for future behavior changes.

## Decision

Obscura v0.1.11 isolates browser context, cookie jar, HTTP client, and V8 state
per WebSocket. Opening a new WebSocket for every search therefore made repeated
queries look like unrelated new clients. Cookie export/import was rejected
because CDP export loses host-only scope and import can widen it.

Each of the five fixed search providers instead retains one lazy native
Obscura connection. Every query uses a fresh target on that connection. The
connection closes after 3,600 seconds without provider use, so cookies and
context-owned transport state remain native and process-local without Python or
disk serialization.

One lazy SearXNG event-loop thread owns all provider connections. The existing
atomic provider reservation and lease is the same-provider serialization
authority; different providers can navigate concurrently for simultaneous
agent search calls. When concurrent calls occupy every eligible regular
provider, later calls wait on provider release or the nearest cooldown deadline
before engine dispatch; they do not return empty or spill into Bing. Each
selected provider receives its own configured 60-second SearXNG engine window,
which starts after provider admission. The patched offline processor owns the
provider lease across the complete `engine.search()` call, including CDP
cleanup, provider parsing, and suspension recording, so a concurrent caller
cannot reserve a provider in the interval between a blocking response and its
suspension. All blocking conditions suspend the provider for the same 3,600
seconds, allowing the affected connection to expire before readmission.

SearXNG owns sequential next-provider rotation under one request deadline.
Onyx's generic whole-request retry is removed, so a tool call cannot replay an
already exhausted provider rotation.

## Boundaries

- Search state is partitioned by provider and SearXNG process, not user or
  conversation. This matches the deployment's single-user trust assumption.
- No cookie is exported, imported, inspected, filtered, logged, or persisted.
- Direct `open_url` remains request-scoped and retains ten independent permits,
  fresh WebSockets, and no provider-session participation.
- Generic HTTP/challenge classification belongs to the shared client and
  ignores excluded script-like text plus iframe-only markers. Provider parsers
  retain provider-specific visible-DOM challenge checks.
- A transport, protocol, stream-cleanup, or target-close ambiguity discards the
  affected connection. No navigation is retried within an attempt.
- The Obscura cap remains 15 live WebSockets: five retained provider sessions
  plus ten direct-Obscura `open_url` attempts.

## Acceptance evidence

Deterministic tests cover reuse, sliding idle expiry, stale-timer handling,
expiry-boundary cleanup, different-provider concurrency on one event loop,
same-provider reservation exclusion, one-hour suspension alignment, generic
challenge ownership, no Onyx whole-search retry, and unchanged `open_url`
capacity configuration.

The selected-image gates additionally prove native cookie continuity across
fresh targets, isolation after connection replacement, repeated target cleanup,
15-slot capacity, installed SearXNG patch compatibility, and no second origin
navigation. See [the upgrade checklist](../../onyx_patches_upgrade.md) for the
commands and live validation matrix.

Review validation against the running full stack completed four synchronized
SearXNG requests in 2.91 seconds: DuckDuckGo and Brave returned results
concurrently while blocked providers remained explicit unresponsive records.
An isolated API process selecting
`ONYX_AGENT_USE_OBSCURA_BROWSER=true` then returned ten of ten simultaneous
direct-Obscura `open_url` results in 0.846 seconds while the live search
sessions remained retained. Both selected-image gates and `make check` passed.
