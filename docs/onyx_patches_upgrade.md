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

Keep API and background bootstrap diagnostics on stderr. Onyx isolated child
processes reserve stdout for their pickled result; validate at least one real
PDF extraction after changing bootstrap or process-isolation behavior.

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
- caller-specific absolute attempt deadlines covering navigation, event wait,
  DOM commands, body-stream creation and reads; exact post-navigation expiry
  stages; and the five-second-before-outer-deadline Onyx partial collector that
  finalizes queued work, preserves ordered completions, and does not retry;
- proxy resolution, redirect/subresource enforcement, logging/full-URL
  exposure, file-access guards, and the accepted ES-module local-file path.
- bounded structural challenge parsing that excludes script/style/template/
  noscript content; positive status, route, title, visible-prompt, and combined
  visible-plus-structure fixtures; and negative script-only, iframe-only, and
  ordinary article-text fixtures.

Run tagged-image capability tests, not only fake CDP fixtures. Prove static and
JS HTML, HTTP and JavaScript redirects, PDF, accepted raw text, unsupported and
oversized content, challenge/status failures, one origin navigation, body/DOM
byte limits, complete cleanup, and no reconnect/refetch.

### Pinned Obscura limitations and wrapper handling

Treat each item below as an explicit removal checkpoint on every Obscura pin
change. Do not carry a workaround forward merely because its old fixture still
passes:

- **Main Document retained-body eviction.** In 0.1.10 the per-page response
  store applies `OBSCURA_NETWORK_BODY_BUFFER_ENTRIES` to the main Document and
  subresources alike. The internal main entry can be evicted before
  `emit_navigation_events` creates its loader-id alias; `Page.navigate` has not
  returned yet, so no client can claim the body first. The wrapper maps the
  exact `Fetch.takeResponseBodyAsStream: no cached body` rejection to
  `body-unavailable` and permits only same-navigation HTML/XHTML DOM processing
  to continue. PDF/raw/binary handling remains strict. On upgrade, inspect
  `store_response_body`, eviction order, main-resource alias creation, and
  command/event ordering; test a page with more retained subresources than the
  configured entry count. Remove the HTML exception when the tagged image
  proves the main body remains retrievable. Do not substitute a larger entry
  count, which multiplies the per-entry memory bound.
- **Flattened duplicate session IDs.** Obscura 0.1.10 reuses the attached
  target session ID when Playwright 1.58 calls `new_cdp_session(page)`, causing
  the driver to reject a duplicate target. The shared client uses one minimal
  raw WebSocket CDP session. Re-test the high-level API on either pin change and
  remove the raw transport only if one navigation, event ordering, actual-body
  access, deadlines, redaction, and cleanup remain equivalent.
- **Deferred, non-atomic cookie clearing.** `Network.clearBrowserCookies` can
  wait behind work on the selected child, and another client can interleave
  between the clear and navigation. The wrapper treats clearing as best effort
  and makes no per-user isolation claim. Re-audit command dispatch, default
  context sharing, and two-client interleavings; prefer an upstream isolated
  context if one becomes complete and supported.
- **Connection-arrival hidden page/session retention.** A connection assigned
  during an active navigation can create pre-dispatch blank state that its
  disconnect path does not reclaim. The wrapper bounds client commands and
  closes its WebSocket but does not pretend that this reclaims inaccessible
  server state; worker restart remains the eventual cleanup. Re-test connection
  churn and worker memory.
- **Renderer stalls and incomplete multi-worker diagnostics.** DOM and cleanup
  commands can fail to answer, the multi-worker launcher discards child
  stdout/stderr, and the single-worker path can log full URLs. Caller-owned
  absolute setup/request/cleanup deadlines, opaque correlation IDs, sanitized
  stages, and warning-level typed failures provide the wrapper boundary.
  Re-test a permanently blocked command, target/connection cleanup, child-log
  forwarding, and URL redaction before removing any deadline or diagnostic.
- **Body-memory and classification gaps.** The server fully allocates a
  response before retention checks; the network byte limit is per entry and is
  amplified by entry count, base64, and aliases; the IO store is separately
  aggregate-bounded; and the stream API does not expose the internal
  text/binary decision. Retain strict limits and the mirrored
  `is_text_like_content_type` predicate until the tagged source and runtime
  tests provide authoritative aggregate accounting and classification.
- **Local-file and health gaps.** Disabling `--allow-file-access` does not
  repair the pinned ES-module local-file-read path, and the round-robin health
  endpoint does not establish aggregate child health. Keep the container
  unprivileged/read-only and free of private mounts, and keep request failures
  visible rather than adding public probes or a fallback browser. Re-audit both
  source paths on upgrade.

Playwright Python remains pinned to the version supplied by Onyx (1.58.0 at
the current baseline) for compatibility auditing and derived-image validation.
The current duplicate-session restriction and its removal criteria are tracked
in the pinned-limitations list above.

## Onyx API patch audit

Audit both values of `ONYX_AGENT_USE_OBSCURA_BROWSER`. The default `false` mode must
retain the pinned stock requests and Playwright fallback while the narrow
egress adapter still matches the crawler's imported `ssrf_safe_get` and
`fetch_rendered_html` symbols. Confirm every initial URL and requests redirect
receives public-only structural validation without API-side DNS, the session
sets `trust_env=False` and explicitly uses `onyx-public-egress-bridge`, the
Admin private-network value cannot widen it, and Playwright validation is
scoped to this crawler. Confirm the shared launcher still uses the exact
public/host proxy selected for each consumer and `<-loopback>` disables direct
loopback bypass. Exercise a public success, qualifying 403 browser fallback,
public redirect, private/loopback initial URL and redirect, NXDOMAIN, broken
bridge, and remote-DNS upstream mode. SearXNG must remain direct Obscura under
both preference values.

The current default reflects parallel testing in which the stock crawler was
blocked less often and was substantially more reliable than Obscura 0.1.10.
Every Obscura pin upgrade must repeat comparable parallel URL batches and
retries, recording blocked, empty-content, timeout, and successful results.
Monitor upstream Obscura improvements and switch the default to `true` if the
new pin reverses the reliability result while preserving the audited privacy,
cleanup, body-retention, and concurrency properties.

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

Re-audit the exact fake embedding model contract. The saved
`nomic-ai/nomic-embed-text-v23` value must continue to activate the intended
Onyx `nomic-ai` RAG feature gates, while only tokenizer construction is mapped
to the bundled `nomic-ai/nomic-embed-text-v1` tokenizer. Confirm the strict
`HuggingFaceTokenizer.__init__` source-shape check, both API and background
installation points, no Hugging Face lookup for v23, and unchanged behavior
for every other tokenizer model.

Verify `sitecustomize_api_server` is the only API bootstrap in both modes,
neutral shared helpers are imported rather than executed, and patch drift is
startup-fatal. Test one shared 120-second monotonic invocation state through
outer/nested jobs, five process-global permits, remaining-budget admission,
finalization races before navigation, one release on every path, partial
results, stable ordering, redirect correlation, content dispatch, limits, and
external-provider non-interference. Do not add database/provider-row migration
or startup enforcement of the operator's saved crawler choice.
In both stock and direct Obscura modes, verify mixed-success batches retain
successful documents and citations while appending the final post-fallback
per-URL failure reasons to the LLM-facing response; all-success, all-failure,
and timeout-only response forms must remain unchanged. Confirm the shared
failure-reporting patch is installed before the selected crawler transport.
Inject a permanently blocked per-URL worker and prove completed siblings return
in order before the outer deadline, the unfinished URL receives a visible
failure, queued work cannot navigate after collection finalization, and the
blocked CDP operation expires at its exact logged stage and releases its permit.

## SearXNG audit

The current audited SearXNG pin is `2026.7.15-7b2199ecd`, sourced from commit
`7b2199ecdf75a00981583fa2f392a785dfc4fcee`. Re-audit the pinned offline and
online processors, engine loader, exception suspension mapping, result
containers, timeout/late-result handling, engine selection, round-robin retry
patch points, and last-resort scoring.

For every custom engine verify:

- offline registration and no normal SearXNG HTTP transport;
- URL/query/locale/safe-search/time/page construction;
- sanitized selector fixtures, normalization, explicit no-results marker,
  parser mismatch, exact terminal hosts, and shared block markers;
- one atomic pre-thread reservation consumed only by its exact engine attempt,
  one provider lease held through cleanup, exact monotonic 3.0-second start
  interval, no queued busy-provider thread, no all-unavailable fan-out, and
  visible unresponsive records for pre-execution unavailability, and
  different-provider concurrency;
- CAPTCHA, rate-limit, and access-denied exceptions use the ordinary offline
  suspension path; non-blocking failures become unresponsive records;
- Bing's visible `One last step` challenge is a typed CAPTCHA while the same
  phrases in excluded script/style/template/noscript content are ignored;
- engines and the CDP client never retry or select another provider;
- enabled round-robin normal/last-resort order and same-request retry, plus
  disabled-round-robin selected-engine fan-out and disclosure warning.

The derived SearXNG image must install its complete Python dependency set from
the generated hashed `searxng/requirements.txt` lock, use one Granian process,
one replica, perform no Chromium/browser download or runtime installation, and
pass the shared-client import validation. Its local image tag must change when
the upstream pin or any embedded Dockerfile, lock, shared-client, or engine
input changes. Its explicit `PYTHONPATH` must expose
the wrapper patches, SearXNG application root, and shared client before the
embedded interpreter imports `sitecustomize`; verify the real Granian
entrypoint logs every strict patch success and loads every custom engine.

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

Confirm Compose still sets `ENABLE_CRAFT=false` for the API and full-mode
background services unless the wrapper deliberately adds and documents a
Craft backend. With Craft disabled, the background bootstrap must find and
remove exactly one `cleanup-idle-sandboxes` beat template; beat logs must not
show Kubernetes sandbox-manager initialization attempts. Re-audit the template
name and collection shape on every Onyx upgrade.

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
