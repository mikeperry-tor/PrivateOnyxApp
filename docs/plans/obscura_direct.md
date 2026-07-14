# Direct Obscura Request Handling Plan

> **Status: planned.** The
> [Onyx application network isolation](onyx_network_isolation.md) prerequisite
> is implemented. This plan describes a future atomic migration from CRW and
> the CDP shim to direct, single-navigation Obscura integrations. Until it is
> implemented, the normative runtime documents are
> [Request handling](../request_handling.md),
> [VPN routing and restricted egress](../vpn_routing_and_proxies.md), and
> [Internal network security](../internal_network_security.md).
>
> **Architecture revision (2026-07-14):** the prerequisite no longer has an
> isolated request-policy stage or route brokers. This plan builds directly on
> fixed component bridges and final-hop policy proxies. It must not recreate a
> custom proxy-to-proxy protocol, credentials, admission counter, or fixed
> tunnel lifetime.

## Executive decision

Remove CRW from the runtime request path and use the pinned, unmodified
Obscura image as the single browser renderer for both `open_url` and custom
SearXNG engines:

```text
Onyx open_url ----+
                  +-> internal CDP gateway -> Obscura
SearXNG engine ---+                          -> browser egress bridge
                                             -> public final-hop proxy
                                             -> selected final route
```

Each `open_url` request performs exactly one browser navigation and consumes
that navigation's retained main-resource body or rendered DOM. Do not add a
preliminary `HEAD`/`GET`, requests fetch, local-Chromium retry, CRW prefetch,
Obscura CLI `--dump original`, or other second content fetch.

Custom SearXNG engines navigate through the same Obscura instance, read the
rendered DOM, and retain their existing selectors and normalization. Rename
the deployed helper from `_crw.py` to `_obscura.py`; no CRW compatibility
alias remains.

## Required outcomes

- Preserve one-fetch HTML, PDF, and supported raw-text handling.
- Preserve browser fingerprint consistency, explicit waits, challenge
  visibility, and useful non-secret diagnostics.
- Preserve one active SearXNG navigation per provider and approximately one
  provider start every three seconds by default.
- Keep `open_url`, helpers, and network-enabled executors intentionally outside
  the SearXNG provider scheduler.
- Preserve final-hop private/internal destination denial on initial requests,
  redirects, and browser subresources.
- Keep target DNS and final routing at the selected final-hop proxy.
- Preserve VPN, configured-upstream, and explicit no-VPN fail-closed behavior.
- Preserve lite/full `open_url`, full-mode local RAG, configured inference,
  local embedding, optional Tailscale, and optional executor semantics.
- Remove CRW, its validation DNS, the CDP shim, obsolete networks, secrets,
  images, builds, configuration, health dependencies, tests, and docs.
- Build on the already simplified prerequisite: unused SearXNG Valkey,
  bypassed full-mode Onyx model servers, the fake CRW credential, and duplicate
  identical-policy proxy processes have already been removed.

## Non-goals

- Do not patch, fork, or locally rebuild Obscura. A missing capability blocks
  cutover until a compatible upstream release is pinned and audited.
- Do not use plain HTTP clients as a renderer fallback.
- Do not give Obscura, SearXNG, or Onyx a direct Internet route.
- Do not expose CDP on a host port or general application/data network.
- Do not merge browser, executor, or Onyx caller networks or their fixed
  bridges.
- Do not make document byte limits unlimited.
- Do not imply per-user browser isolation: at the pinned Obscura version the
  CDP clients share one browser trust domain.

## Version and source audit

Implement against the committed pins in `stack.versions.env`. Before cutover,
re-audit these symbols in the matching `reference_repos/` checkouts:

- Onyx `OnyxWebCrawler`, `is_pdf_resource`, `extract_pdf_text`, and current
  Playwright fallback/source-shape patch points;
- SearXNG custom engine orchestration, engine deadlines, cancellation, and
  suspension behavior;
- Obscura main-resource aliasing/body retention, `Network.getResponseBody`,
  `Fetch.takeResponseBodyAsStream`, `IO.read`/`IO.close`, navigation waits,
  context behavior, and cookie-clearing CDP methods;
- CRW scrape/search mapping, PDF behavior, waits, retries, proxy assumptions,
  and diagnostics being removed; and
- CDP shim wait rewriting, target selection, proxy/context stripping, stealth
  handling, cookie clearing, and traces being retired.

Any pin change requires a fresh source and runtime audit. Patch drift remains
strict and startup-fatal.

## Target networks and services

Retain:

- internal Onyx application/data/Teep networks from the isolation prerequisite;
- `obscura-control`, reachable by SearXNG and through a narrow Onyx CDP gateway;
- `browser-egress`, reachable only by Obscura and its fixed bridge;
- `browser-policy-upstream`, joining only the browser bridge and
  `netns-holder`;
- separate public and host Onyx bridges and policy-side networks;
- separate optional executor network, bridge, and policy-side network; and
- full-mode doc-drop and embedding networks.

Delete every CRW-only network after verifying no retained service uses it.

`obscura-cdp-gateway` joins `onyx-backend` and `obscura-control`, forwards only
the CDP port, publishes no host port, and exposes no policy/configuration
interface. SearXNG may join `obscura-control` directly. Neither caller joins
`browser-egress`.

Obscura retains its mandatory proxy setting and can resolve only the internal
browser bridge. The browser network remains internal, so ignoring the proxy
does not create direct egress.

## Final-hop proxy model

Use the same final-hop model as the isolation prerequisite:

- clients reach only hardened fixed TCP bridges;
- bridges forward to fixed listener ports in `netns-holder`;
- final-hop proxy processes accept only their configured bridge peers;
- the proxy parses HTTP framing, validates destinations, selects DNS/upstream
  behavior, pins direct addresses, and establishes the connection; and
- no caller can select a resolver, upstream proxy, source address, or route
  class.

As part of the atomic migration, move the generic implementation from the
legacy `crw/` path to a neutral location and rename `PREFETCH_*` vocabulary.
Remove named search-host policy modes: after CRW prefetch is gone, search
engines are ordinary public destinations.

After CRW prefetch is removed, use the existing shared public final-hop proxy
for generic Onyx, browser, and optional executor bridges. Each bridge remains
on a distinct caller network and the proxy keeps an explicit peer allowlist.
Retain the separate host-capable proxy for exact host and opt-in RFC1918 Onyx
traffic. Both run hardened in `netns-holder`. This process split is defense in
depth and operational separation, not a sandbox: arbitrary code execution in
either trusted proxy compromises the shared namespace.

Do not introduce an intermediate request-policy process. Do not duplicate
destination parsing across two custom daemons. Do not add a custom stream
protocol, per-route credentials, admission lease, idle lease, or fixed total
CONNECT deadline. Established tunnels close when a peer closes or I/O fails;
protocol clients and endpoints own long-lived connection timeouts.

The host proxy alone retains exact `host.docker.internal`, exact stack-owned
destinations, and `EGRESS_ALLOW_RFC1918` behavior. Public Onyx, browser, and
executor listeners reject those destinations. Mixed public/private answers,
loopback, link-local, metadata, container-internal names, and alternate-route
attempts fail closed.

Each bridge remains numeric-nonroot, read-only, capability-free,
`no-new-privileges`, packet-forwarding-disabled, mount-free, and limited to one
literal listen/forward command. It exposes no host, admin, DNS, metrics, or
control port.

## Direct Obscura client

Create one shared CDP client library, embedded independently in the Onyx and
SearXNG processes, with typed results for:

- rendered HTML;
- retained binary main-resource bytes;
- accepted raw text;
- blocked/redirected destination;
- challenge/HTTP failure;
- body unavailable/evicted;
- oversized content; and
- cancellation/timeout/protocol incompatibility.

For binary-classified PDFs and supported documents, read the retained body
from the same navigation and pass exact bytes to Onyx's existing in-process
document extraction pipeline. For HTML, use the rendered DOM and Onyx's
existing cleanup pipeline. Treat
misleading content types, non-UTF-8 text conversion, and unavailable body
identity as explicit limitations or typed failures; never silently refetch.

Use one positive finite user-facing document byte limit, default 50 MiB.
Propagate it to navigation retention, body streaming, parser input, and
diagnostics. Count actual bytes even when `Content-Length` is
missing, false, duplicated, compressed, or combined unsafely with transfer
encoding. Reject ambiguous framing.

Do not add a parser service, Unix socket, private volume, IPC protocol, or
parser readiness dependency. Parsing already occurs inside the trusted Onyx
API process; retain its existing parser libraries and process/container
limits, and enforce the byte cap before invoking them. Reconsider separate
parser isolation only if a concrete parser threat or reliability result
justifies the added service boundary.

## SearXNG scheduling and cancellation

Run exactly one Granian worker and prevent horizontal scaling unless the
scheduler is redesigned. `_obscura.py` owns process-wide provider locks and
minimum-start timestamps shared across request threads.

The outer SearXNG deadline must cooperatively cancel a timed-out navigation,
close its target, release the provider lease, and prevent late result writes.
A timed-out attempt must not leave a target, task, thread, or lock behind.
`open_url` may overlap SearXNG and can still contribute to upstream 403/429;
that tradeoff is documented rather than hidden behind a global scheduler.

## Browser state and trust

Use one stable Obscura fingerprint and explicit navigation waits. Clear the
relevant cookies and storage at the request/navigation boundary, or use a
disposable browser context if the pinned Obscura release supports it reliably.
A clearing failure fails that request. Do not add a state-clearer sidecar,
readiness epoch, shared state volume, or stack-wide health gate.

Request-boundary clearing is not cross-process isolation: concurrent Onyx and
SearXNG navigations may still share or perturb browser state. Keep that
limitation explicit. If tests show it is unacceptable, prefer separate
Obscura instances or upstream-supported isolated contexts over a custom
cross-process lock/coordination service.

Onyx and SearXNG share full CDP authority at the pinned version. Network
placement excludes other services but does not provide confidentiality or
integrity isolation between those clients. Document this residual risk.

## Readiness and failure behavior

Optional browsing readiness is local and does not gate core Onyx startup:

```text
selected routing substrate -> final-hop proxy -> fixed bridge
Obscura local CDP -> CDP gateway and SearXNG
Onyx API does not wait for the browsing chain
```

Proxy readiness validates configuration and DNS/upstream substrate without an
arbitrary public fetch. Bridge readiness traverses the fixed hop and expects a
blocked-target denial. Obscura health checks local CDP shape. SearXNG may wait
for its browser dependency; Onyx verifies CDP capabilities when `open_url` is
invoked, without generating periodic public traffic or blocking core startup.
Mode-required data and embedding dependencies remain unchanged.

Failure of Obscura, a bridge, final-hop proxy, Myst, or configured upstream
fails the dependent request path. There is no direct, cross-listener,
local-Chromium, CRW, or plain-HTTP fallback. Only Myst retains VPN-mode
autoheal.

## Validation

Add deterministic unit and effective-Compose tests for:

- single-navigation HTML, JS DOM, redirects, PDFs, raw text, missing/false
  length, compression, oversize, body eviction, challenge pages, cancellation,
  and no second origin hit;
- SearXNG provider serialization, minimum start interval, deadline
  cancellation, target cleanup, suspension, and single-worker enforcement;
- CDP method/version compatibility, wait semantics, target selection, stable
  fingerprint, request-boundary state clearing, clearing failure, and shared
  trust boundaries;
- internal/Docker/Podman/metadata and every non-public address class, IDNA,
  trailing dots, redirects, mixed DNS answers, and upstream remote-DNS modes;
- public listener equivalence for generic Onyx/browser/executor traffic and
  denial of all host-route exceptions;
- exact host and opt-in RFC1918 behavior only on the host proxy;
- HTTP request-smuggling/framing defenses and bridge source authentication;
- absence of any broker protocol, credential, capacity lease, or total tunnel
  deadline;
- bridge hardening, fixed destinations, network separation, and inability to
  route packets between caller networks;
- explicit public/host route classes, host-only trusted authorities, combined
  optional-overlay Compose models, and deadline-free framed body streaming;
- removal of CRW, validation DNS, CDP shim, legacy env names, images, networks,
  secrets, health dependencies, and docs; continued absence of Valkey and
  obsolete model servers;
- lite/full startup, real `open_url`, every custom search engine, local RAG,
  configured inference, embedding, VPN/no-VPN/upstream modes, Tailscale, and
  executor modes when external dependencies are available.

## Acceptance criteria

The migration is complete only when:

1. Every Onyx document and SearXNG search attempt uses one Obscura navigation.
2. No retained path can silently refetch or fall back to CRW/local Chromium.
3. Obscura and SearXNG have no direct route and target DNS remains final-hop.
4. Browser, executor, public Onyx, and host Onyx networks/bridges remain
   distinct and cannot select one another's listener.
5. Final-hop proxies are the only custom egress enforcement processes; no
   broker or isolated duplicate policy stage exists.
6. Long-lived CONNECT tunnels have no arbitrary proxy total lifetime.
7. Document byte limits, in-process parsing, cancellation, and error reporting
   are bounded and tested without a parser sidecar or IPC protocol.
8. Search rate/concurrency behavior and shared browser-state risks are explicit
   and tested.
9. All obsolete services, pins, files, settings, tests, and documentation are
   removed atomically.
10. README, AGENTS.md, runtime docs, patch docs, upgrade docs, and this plan
    describe the deployed topology without compatibility aliases or historical
    obsolete broker concepts as current or planned behavior.
