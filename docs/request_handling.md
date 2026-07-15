# Request handling

This document describes the wrapper-managed `web_search` and built-in Onyx
Web Crawler `open_url` paths. Both use the pinned Obscura browser directly.
There is no CRW, preliminary HTTP fetch, local-browser retry, or second origin
request in either path.

## Runtime flows

```text
Onyx open_url
  -> private_onyx_obscura shared client
  -> obscura-cdp-gateway (API-only control networks)
  -> Obscura
  -> obscura-egress-bridge
  -> public final-hop proxy
  -> VPN, configured upstream proxy, or explicit no-VPN route

Onyx web_search
  -> searxng-service-gateway
  -> one custom offline SearXNG engine
  -> private_onyx_obscura shared client
  -> Obscura
  -> the same browser bridge and public final hop
```

The API container and SearXNG import the same client implementation from
`browser/obscura_client`. SearXNG connects on `obscura-control`; the API can
connect only through `obscura-cdp-gateway` on `onyx-obscura-control`. CDP is
not published on the host or attached to an Onyx data/backend network.

The client uses flattened CDP messages over the pinned WebSocket transport.
At Obscura 0.1.10, attaching Playwright 1.58's public
`new_cdp_session(page)` to an Obscura-created target reuses Obscura's session
identifier and crashes the Playwright driver as a duplicate target. The
audited raw transport avoids that incompatible second attachment while
preserving the exact one-`Page.navigate` contract. The derived SearXNG image
still pins the audited Playwright package and contains no browser binary.

## One-navigation contract

For each accepted target the shared client:

1. validates the URL syntax and scheme without resolving the target;
2. best-effort clears browser cookies on the connection;
3. creates one fresh target, enables the required CDP domains, and registers
   event observation before navigation;
4. runs the caller's pre-navigation finalization guard;
5. sends exactly one raw `Page.navigate`;
6. waits for the matching main-frame completion event;
7. identifies the terminal main-frame Document request across redirects and
   JavaScript navigation, retaining its actual request id, status, headers,
   final frame URL, and challenge state;
8. obtains rendered DOM and/or that same navigation's retained response body;
9. closes every IO stream, target, and WebSocket on success or failure.

The client does not issue `HEAD`, `GET`, range, MIME-probe, CLI, normal
SearXNG HTTP-client, or retry requests. It does not reconnect after a CDP
failure. Redirects and browser subresources are part of the single browser
navigation; they are not wrapper refetches.

Search uses `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH` (default `load`). Built-in
`open_url` uses `OBSCURA_BROWSER_WAIT_UNTIL_WEB` (default
`domcontentloaded`). Accepted values are the finite Obscura lifecycle values
validated by the shared client. These are event conditions, not sleeps;
Obscura also has a finite navigation deadline.

Connection, cookie clearing, target creation, attachment, and CDP domain setup
share one absolute 45-second pre-navigation deadline. It is not a fresh
45-second allowance for each command. Expiry closes the connection, returns a
typed `pre-navigation-timeout`, and leaves headroom beneath SearXNG's 60-second
engine deadline and Onyx's 120-second invocation deadline. Cleanup CDP commands
have a separate five-second bound so an unresponsive renderer cannot retain a
caller permit merely by blocking target or body-stream closure.

Cookie clearing is best effort, not a user-isolation boundary. At the pinned
server it may wait behind active work assigned to the same renderer, and the
clear plus subsequent target creation is not atomic across clients. Targets
on a worker share that worker's browser trust domain. Local storage and other
non-cookie state are not claimed to be cleared. Five workers improve capacity,
not per-user isolation.

## Body and content handling

Rendered HTML comes from `DOM.getOuterHTML`. The terminal main-resource body
comes from `Fetch.takeResponseBodyAsStream` followed by bounded `IO.read` and
an unconditional `IO.close`. Plain and base64 chunks are counted as actual
bytes. The client drains and closes an oversized stream before returning a
typed failure.

The built-in crawler dispatches the same-navigation result as follows:

- PDF is recognized by terminal URL, MIME type, or PDF magic and parsed with
  the pinned Onyx in-process PDF extractor.
- HTML uses the rendered DOM and the pinned Onyx HTML-to-content path.
- Raw text is accepted only for the explicit MIME, extension, and charset
  allowlists in `obscura_crawler_patch.py`.
- Unsupported, conflicting, misleading, or incorrectly decoded content fails
  visibly. There is no generic-parser or remote-provider fallback.

Binary response bodies are byte-accounted. Text-classified bodies are always
marked potentially lossy: equal encoded and retained byte counts do not make
them byte-identical, and rendered DOM is never described as response-byte
identical. Declared non-UTF-8 text must satisfy the strict permitted decoding
contract.

`ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB` defaults to 50 MiB and is a positive
integer. It bounds a retained main-resource body and drives Obscura retention
floors. Rendered DOM has a separate 20 MiB cap; search DOM also has a 20 MiB
cap. Existing Onyx character budgets apply after parsing. Increasing the
document limit increases potential memory use across five simultaneous API
fetches and five browser workers.

These limits do not bound Obscura's initial response allocation. The pinned
server can read a complete response before applying retained-body limits. Its
network retention limit is per body and may be amplified by entry count,
base64 representation, and request/loader aliases. The IO stream store has
separate aggregate accounting, but neither limit is an aggregate browser
process-memory bound. In-process PDF parsing also has no complete CPU,
transient-memory, or parser wall-time bound beyond the outer invocation.

## Onyx `open_url`

The API-only startup patch strictly replaces the built-in `OnyxWebCrawler`
fetch path. Patch source-shape drift is startup-fatal. A crawler invocation
creates one absolute 120-second monotonic deadline and finalization object
before outer parallel work begins. All nested crawler jobs receive that same
state.

Five process-global permits bound active API fetches. A job may wait for a
permit only for its remaining invocation budget. It checks finalization before
setup and again immediately before `Page.navigate`; finalized work cannot send
a new origin request. A navigation already sent retains its permit through
cleanup. Admission is process-local and non-FIFO, and waiting caller threads
are not themselves a durable queue or a cross-process capacity reservation.
The shared 45-second setup bound means a renderer blocked before navigation
releases its caller-side permit within that budget; it does not retry or prove
that the selected Obscura child cleaned up its own pre-dispatch state.

Results remain ordered by requested URL. Failures and snippets are correlated
with that requested URL even after redirects; successful content and citations
use the terminal URL. Cardinality mismatches fail loudly. Individual transport,
status, challenge, type, parse, limit, and timeout failures become the normal
unsuccessful `WebContent` result without hiding the reason or discarding other
successful URLs.

Operators must select **Onyx Web Crawler** in the Web Search Admin page to use
this path. The wrapper does not rewrite saved provider rows or enforce a
provider choice at startup. Deliberately selecting Firecrawl, Exa, or another
external Onyx content provider remains supported through Onyx's public egress
route, but it is outside the one-navigation guarantee: that provider owns the
subsequent target fetch and its current retention, training, and zero-data-
retention policy. Its API key also associates activity with the operator's
account.

Full-mode doc-drop ingestion, local embedding, and `internal_search` do not use
Obscura and are unchanged.

## SearXNG search

`google2`, `brave2`, `duckduckgo2`, `startpage2`, and `bing2` are custom
offline engines. Each builds one provider URL, performs one direct Obscura
navigation, verifies an exact terminal-host allowlist, classifies status and
challenge markers, and parses the rendered DOM. Explicit provider no-results
selectors return no results; missing expected structure is an unresponsive
parser mismatch. The engines cannot call SearXNG's normal HTTP transport,
retry internally, or choose another provider.

One Granian process owns scheduling. Each provider has one active lease and an
exact 3.0-second minimum interval between navigation starts, with zero jitter.
Busy and cooling providers are skipped before an engine thread is created;
the lease is retained through target cleanup.

With `SEARXNG_ROUND_ROBIN=true` (default), the existing orchestration selects
one available normal custom provider, removes the other selected engines from
that attempt, and may try a different provider only after SearXNG records the
first as unresponsive and the request has no main results. Last-resort scoring
is retained. Blocking conditions flow through the ordinary offline processor
and suspend the provider. SearXNG owns caller timeouts, late results,
unresponsive-engine reporting, and suspension state.

With round robin disabled, ordinary selected-engine fan-out returns. That can
disclose the same query concurrently to several providers and can leave one
late attempt per provider. `open_url`, generic helpers, and enabled executors
are intentionally outside the search-provider scheduler. Enabled executors
may reach public search engines through the shared public final-hop policy.

## Routing, DNS, and failure behavior

The clients validate URL syntax but deliberately do not resolve public target
names. Browser target DNS is authoritative at the selected final hop: Myst
provider DNS in VPN mode, the configured upstream proxy when it owns remote
DNS, or explicit system DNS in no-VPN mode. Docker DNS resolves only internal
service names.

The public final-hop policy rejects canonical internal names and non-public
addresses with HTTP 403 for initial requests, redirects, and subresources when
the wrapper performs DNS and pins the approved addresses. In a deliberately selected
remote-DNS upstream-proxy mode, a malicious or misconfigured upstream can
resolve a public-looking name privately; the wrapper cannot claim address-
level private-target denial in that mode. Proxy-side NXDOMAIN, no-address, and
resolver failures return HTTP 502 rather than being mislabeled as policy
denials; Obscura exposes the resulting tunnel failure as a typed browser
transport failure. There is no direct network fallback
if Obscura, its bridge, the proxy, Myst, or an upstream proxy fails.

Wrapper-owned diagnostics omit query strings, request bodies, response
contents, cookies, credentials, and sensitive headers. They report only a
stage, typed category, status class, safe host where needed, and bounded sizes.
Each shared-client attempt has a random opaque correlation ID. Start, completed
setup, terminal result, typed failure, and cleanup records include that ID plus
elapsed time; timeout records identify the exact setup or cleanup stage without
logging the target URL.
The unmodified upstream Obscura image does not offer equivalent end-to-end
redaction: single-worker debugging may log full URLs, and multi-worker child
logs can be incomplete. Treat its logs as private browsing data.

Obscura is run as UID/GID 65534, read-only, capability-free, without browser
data or secret mounts, `--storage-dir`, or `--allow-file-access`. The shared
client rejects `file:` targets. These controls reduce impact but do not repair
the pinned upstream ES-module local-file-read path; Obscura remains a trusted
browser component and must not receive private mounts.

## Diagnostics

Use the Makefile-selected mode and targeted logs:

```sh
make ps-lite                 # or make ps-full
docker logs searxng-core --since 10m
docker logs onyx-obscura-1 --since 10m
docker logs onyx-obscura-cdp-gateway-1 --since 10m
docker logs onyx-onyx-public-egress-proxy-1 --since 10m
docker logs onyx-api_server-1 --since 10m
```

Expected search failures include provider 429, access-denied, CAPTCHA, parser
mismatch, timeout, and suspension records. They must not become empty-success
substitutes. For `open_url`, verify the API startup log contains
`installed strict direct Obscura crawler`. A missing gateway, browser bridge,
or final hop must fail the feature closed without moving an application onto a
public network.

Relevant controls are documented in `.env.wrapper.example`:

- `OBSCURA_BROWSER_WAIT_UNTIL_SEARCH` and
  `OBSCURA_BROWSER_WAIT_UNTIL_WEB`;
- `ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB`;
- `EGRESS_ALLOW_HTTP_URLS`;
- `SEARXNG_ROUND_ROBIN`;
- the VPN and upstream-proxy route settings.

Obscura worker count, API fetch concurrency, browser navigation deadline, and
the internal retention multipliers are fixed reviewed implementation values,
not user-facing tuning controls.
