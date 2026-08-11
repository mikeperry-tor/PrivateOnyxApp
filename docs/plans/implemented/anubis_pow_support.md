# Startpage Anubis Proof-of-Work Support

> **Status: implemented.** Normative runtime behavior is documented in
> [Request handling](../../request_handling.md). This record defines the
> accepted stack-specific solver design and its validation boundary.

## Objective

The `startpage2` SearXNG engine completes an admitted Anubis proof presented at
the homepage or result boundary inside its existing provider attempt. The
challenge, pass navigation, authorization cookie, optional form restoration,
and terminal result remain on the same retained Startpage target, route,
fingerprint, stealth client, cookie jar, provider lease, and absolute browser
deadline.

The implementation does not add a requests client, constructed result URL,
browser retry, alternate browser, direct-egress path, second deadline, external
solver, worker thread, process, or executor. It does not affect `open_url` or
any other provider.

## Admitted protocol

The client accepts a bounded, parseable Anubis puzzle using algorithm `fast`.
The version string is bounded printable metadata rather than an allowlist, and
difficulty 0 through 64 is accepted as puzzle input. Admission requires all of:

- an HTTPS/default-port Startpage terminal origin;
- the exact same-origin
  `/.within.website/x/cmd/anubis/static/js/main.mjs` module;
- exact `anubis_version` and `anubis_challenge` JSON elements within their
  byte bounds, without duplicates;
- visible `.sp-message` text containing normalized `Verifying your request`;
- a challenge object and rules object containing a bounded printable challenge
  ID, bounded printable ASCII random data, a coherent
  unspent state when present, and matching `fast` method and rule; and
- the exact pass path
  `/.within.website/x/cmd/anubis/api/pass-challenge`.

Additional unrelated envelope, challenge, and rule fields are ignored within
the element byte bound. Unsupported algorithms, out-of-range difficulty,
missing or inconsistent puzzle fields, malformed encodings, and incomplete
marker combinations are unsupported verification pages and fail closed
through the ordinary CAPTCHA suspension path. A protocol change that alters
the proof primitive, worker path, pass fields, cookie behavior, or redirect
transaction requires corresponding parser, solver, and continuation support;
a release-number change alone does not.

## Worker suppression

Before Startpage's initial homepage navigation, the shared client installs one
`Page.addScriptToEvaluateOnNewDocument` preload. It wraps the native Worker
constructor and returns an inert message-compatible Worker only for:

- an exact same-origin Anubis `sha256-webcrypto.mjs` or
  `sha256-purejs.mjs` worker path; or
- a target-local Blob URL while the document contains the exact Anubis main
  module marker.

Every other construction delegates to the native constructor. The wrapper
does not expose Anubis messages to Python and emits no result or progress. The
client requires acknowledged installation, active ownership, at least one
suppressed worker on an admitted challenge, termination, and removal. It keeps
the preload through the original result classification, removes it before the
pass navigation or after an ordinary terminal result, and closes the provider
generation on any ownership or cleanup mismatch.

This prevents Obscura v0.2.0's cooperative Worker implementation from running
the Anubis hash loop in the page isolate and racing or starving CDP.

## Proof and continuation ownership

The shared browser coroutine returns only an immutable challenge and random
opaque continuation token. The retained `SearchBrowserSession` owns the
remaining state, bound to the exact owner, target/session/frame, challenged
loader and URL, query, fixed form fields, interaction specification, and
deadline. One token can exist for Startpage and is consumed exactly once by
resume or abort. It does not expose CDP, cookies, targets, or route objects to
the engine.

The already allocated SearXNG engine caller computes, synchronously and in
constant memory:

```text
SHA256(randomData + decimalNonce)
```

It starts at nonce zero and returns the first hash with the configured number
of leading hexadecimal zeroes. It checks the shared deadline at least every
4,096 candidates and examines at most 16,777,216 candidates. Elapsed time is
monotonic. Exhaustion or deadline expiry aborts and closes the generation as a
non-blocking engine failure; it is not treated as provider rejection.

Resume independently recomputes the submitted hash and difficulty condition,
removes the preload, and constructs the exact pass URL inside the retained
page from protocol arguments. It validates any pass event emitted by CDP. The
pinned runtime can omit the intermediate redirect request, so absence of that
event is accepted only with the exact client-built navigation, a distinct
terminal loader, and the existing terminal-origin/document checks.

## Transaction bounds

The ordinary transaction is homepage `H0`, original declared form POST `R0`,
then one terminal result.

For a homepage challenge:

```text
H0 challenge -> proof -> C0 pass/redirect -> validated homepage form
             -> R0 original POST -> terminal result
```

For a result challenge:

```text
H0 homepage -> R0 challenge -> proof -> C0 pass/redirect
            -> terminal result
               or validated homepage form -> R1 one restored POST -> terminal
```

At most one challenge is solved and at most one form restoration occurs. A
renewed or explicitly rejected challenge is a CAPTCHA suspension. A second
challenge, invalid form, origin/loader mismatch, timeout, token mismatch,
interceptor failure, or cleanup ambiguity closes the generation without a
retry or fallback.

## Privacy and resource contract

Challenge JSON, random data, challenge IDs, proof hashes, nonces, pass URLs,
continuation tokens, queries, cookies, and response content are excluded from
wrapper logs. Diagnostics contain only sanitized request IDs, entry mode,
status class, stage/category, timings, and proof outcome.

Proof calculation opens no network connection. Every challenge asset, pass
request, redirect, and restored POST uses the existing Obscura target and
browser final-hop policy. Successful authorization remains partitioned to the
retained Startpage provider generation and expires with it.

## Required validation

- strict positive and negative protocol fixtures, including forward-versioned
  metadata, higher difficulty, bounded extensions, unsupported algorithms,
  out-of-range difficulty, casing, shape, duplicate, ID, and random-data cases;
- known proof vectors, first-valid-nonce behavior, deadline checks, candidate
  ceiling, constant memory, and caller-thread identity;
- exact direct/Blob interception, unrelated Worker delegation, acknowledgment,
  removal, termination, and tamper failure;
- homepage/result continuation, token single use, exact pass policy, cookie
  continuity, direct-result acceptance, one restored POST, second-challenge
  rejection, and generation-close paths;
- ordinary Startpage behavior with the preload installed and removed without a
  challenge;
- deterministic repository checks and the selected SearXNG image/parser gate;
- a live challenged Startpage search returning usable results, a subsequent
  retained-session search without another proof, sanitized logs, and healthy
  lite-stack services; and
- routing/fault checks proving no direct or alternate egress when Obscura, its
  bridge, the selected proxy/VPN/Tor route, or continuation fails.
