# Patch and component upgrade checklist

Use this checklist whenever changing Onyx, code-interpreter, SearXNG,
Obscura, Teep, support-image pins, runtime Python inputs, or source-shape
patches. `stack.versions.env` is the committed pin source; `reference_repos/`
contains read-only audit checkouts when present.

## Upgrade procedure

1. Record the old/new image tags, source refs, Python versions, and moving-tag
   support-image versions.
2. Read the upstream implementations named below. Do not infer compatibility
   from a tag or passing import alone.
3. Update pins and hashed locks through the Makefile upgrade targets.
4. Rebuild derived images. Runtime startup must not invoke a package manager,
   `pip`, or a Playwright browser download.
5. Run strict source-shape tests, deterministic unit tests, effective Compose
   checks, and the practical live matrix.
6. Update `docs/onyx_patch_info.md`, request/routing/security docs, README, and
   AGENTS when behavior or topology changes.
7. Remove a wrapper patch only when the pinned upstream behavior provides the
   same strict, fail-closed semantics and tests prove it.

## Direct Obscura audit

Audit these Obscura 0.1.10 areas (or their new equivalents):

- flattened Target attachment/session identifiers, target creation/closure,
  multi-worker connection assignment, and concurrent hidden-page retention;
- `Page.navigate` command-response/event ordering and lifecycle wait values;
- Network request/response/loading events, redirect collapse, main-frame
  Document selection, JavaScript navigation, request-id/loader-id aliases,
  challenges, and terminal frame URL;
- `Fetch.takeResponseBodyAsStream`, plain/base64 `IO.read`, `IO.close`, body
  eviction, content-type predicate, compressed/chunked/false-length behavior;
- per-body network retention, entry/alias/base64 amplification, aggregate IO
  stream accounting, and initial full-body allocation before retention;
- `Network.clearBrowserCookies` deferral and cross-client non-atomicity;
- the cumulative 45-second pre-navigation deadline across connect, cookie
  clear, target creation, attachment, and domain setup; the separate bounded
  cleanup commands; typed stage-specific expiry; and URL-free correlation logs;
- proxy resolution, redirect/subresource enforcement, logging/full-URL
  exposure, file-access guards, and the accepted ES-module local-file path.

Run tagged-image capability tests, not only fake CDP fixtures. Prove static and
JS HTML, HTTP and JavaScript redirects, PDF, accepted raw text, unsupported and
oversized content, challenge/status failures, one origin navigation, body/DOM
byte limits, complete cleanup, and no reconnect/refetch.

Playwright Python remains pinned to the version supplied by Onyx (1.58.0 at
the current baseline) for compatibility auditing and derived-image validation.
At the current pins, its public `new_cdp_session(page)` cannot attach to an
Obscura-created page without a duplicate flattened session-id crash. The
shared runtime client therefore uses the pinned WebSocket transport. Re-test
this limitation on either pin change; prefer a supported high-level API only
if it preserves one navigation, actual-request body access, event ordering,
and complete cleanup.

## Onyx API patch audit

Re-audit the pinned Onyx symbols for:

- `OnyxWebCrawler.contents`, `_fetch_url`, `_fetch_web_content`, outer
  `open_url` timeout callback, copied context, nested executor, and result
  ordering;
- `WebContent` failure shape, requested/terminal URL metadata, snippet and
  indexed-document preference/merge behavior;
- `is_pdf_resource`, `extract_pdf_text`, HTML extraction, raw-text decoding,
  and character-budget call sites;
- built-in versus external provider dispatch and Admin/provider test APIs;
- any new requests, local-browser, Firecrawl, parser, or generic fallback.

Verify `sitecustomize_api_server` is the only API bootstrap in both modes,
neutral shared helpers are imported rather than executed, and patch drift is
startup-fatal. Test one shared 120-second monotonic invocation state through
outer/nested jobs, five process-global permits, remaining-budget admission,
finalization races before navigation, one release on every path, partial
results, stable ordering, redirect correlation, content dispatch, limits, and
external-provider non-interference. Do not add database/provider-row migration
or startup enforcement of the operator's saved crawler choice.

## SearXNG audit

Re-audit the pinned offline and online processors, engine loader, exception
suspension mapping, result containers, timeout/late-result handling, engine
selection, round-robin retry patch points, and last-resort scoring.

For every custom engine verify:

- offline registration and no normal SearXNG HTTP transport;
- URL/query/locale/safe-search/time/page construction;
- sanitized selector fixtures, normalization, explicit no-results marker,
  parser mismatch, exact terminal hosts, and shared block markers;
- one provider lease held through cleanup, exact monotonic 3.0-second start
  interval, no queued busy-provider thread, and different-provider concurrency;
- CAPTCHA, rate-limit, and access-denied exceptions use the ordinary offline
  suspension path; non-blocking failures become unresponsive records;
- engines and the CDP client never retry or select another provider;
- enabled round-robin normal/last-resort order and same-request retry, plus
  disabled-round-robin selected-engine fan-out and disclosure warning.

The derived SearXNG image must use hashed dependencies, one Granian process,
one replica, no Chromium/browser download, no runtime installation, and a
successful shared-client import validation.

## Routing and Compose audit

Render the exact Makefile layering for lite, full, VPN, no-VPN, upstream proxy,
Tailscale, Podman, and optional executor modes that changed. Verify:

- only API/gateway join `onyx-obscura-control`; the gateway is absent from
  `onyx-backend`; SearXNG joins the browser control network;
- both Onyx `NO_PROXY` forms contain `obscura-cdp-gateway` and no removed alias;
- Obscura/SearXNG have no direct Internet route and CDP has no host port;
- public Onyx, browser, executor, and host-capable caller networks/bridges stay
  distinct, fixed-destination, and peer authenticated;
- browser/public/executor share the public policy while host-only exceptions
  remain on the host listener; public search hosts are not locally denied;
- target DNS is Myst/provider, upstream-owned remote DNS, or explicit no-VPN
  DNS as selected, never Docker target DNS;
- wrapper-resolved destinations are complete-set validated and pinned;
  remote-DNS mode retains its explicit private-resolution residual;
- VPN/upstream/no-VPN failures, internal targets, redirects, subresources,
  metadata, mixed answers, and framing errors fail closed;
- long-lived CONNECT streams have no arbitrary total lifetime;
- Obscura remains five workers, 65534:65534, read-only, capability-free,
  secret/data-volume-free, and without storage/file-access flags;
- full local RAG, embedding, configured inference, Teep, hardened publishers,
  and optional Tailscale behavior remain intact.

## Other patch regression audit

Retest the unaffected patches listed in
[Patch information](onyx_patch_info.md#other-retained-patches), especially
reasoning preservation, selected Deep Research tools/batches, character and
upload limits, helper routes, lite `open_url`, background PDF freshness,
executor networking/capability text, and the embedding shim. Moving the shared
helper must not change their source-shape or import behavior.

## Minimum deterministic validation

```sh
make help
python3 -m unittest discover -s tests -p 'test_*.py' -v
git diff --check
```

Also inspect effective lite/full Compose models through the Makefile. Search
current runtime files for removed service/env/path names and manually classify
historical implemented plans and read-only reference repositories rather than
using an indiscriminate zero-match rule.

## Live validation matrix

Where external dependencies are available, exercise lite/full with VPN,
explicit no-VPN, and a documented remote-DNS upstream. For each practical row:

- start with the Makefile and inspect health plus API, SearXNG, Obscura,
  gateway, bridge, final-hop, and Myst logs;
- run a real built-in crawler request for static and JS HTML, redirect, PDF,
  raw text, unsupported and oversized content;
- query every custom search engine and distinguish genuine results/no-results
  from visible provider CAPTCHA/429/access denial;
- verify a same-provider lease never overlaps, another provider can progress,
  and `open_url`/helpers/executors remain outside search scheduling;
- interrupt Myst, Obscura, gateway, and the final hop and confirm no stale
  connection reuse, direct fallback, or application restart storm;
- test full doc-drop crawl/freshness/reindex, embedding and `internal_search`;
- test disabled/enabled executor network paths and LLM-facing descriptions;
- exercise configured inference, Teep, WebUI/authentication/MinIO, host
  publishers, and optional Tailscale where configured.

If credentials, funding, provider stability, private documents, or long-lived
external services prevent a row, record exactly what was not run and retain
the deterministic and topology evidence.
