# Optional Exa search through x402 on Base

Status: implementation plan; implementation has not started. The decisions below
include the user's explicit preferences and the subsequent implementation
clarifications. This file provides the accepted design for implementation.

## Objective and accepted decisions

Add an optional SearXNG engine named `x402exa` that purchases Exa searches using
the x402 Foundation Python SDK and a local Base wallet. Preserve free search as
the default and preserve the existing privacy routing boundaries.

Use `x402exa` consistently for the engine name, module, shortcut, scheduler
identifier, and engine-specific tests; use `integration-x402exa` for its live
Make target. SearXNG rejects underscores in engine names. The naming convention
leaves room for future `x402tavily` and `x402brave` engines without introducing
a generic paid-provider framework in this change.

| Subject | Accepted behavior |
| --- | --- |
| Enablement | Unset or empty `SEARXNG_X402_PRIVKEY` disables the feature. A valid nonempty key enables it. Invalid nonempty configuration fails startup. |
| Default order | Existing regular engines, then Bing, then Exa. Do not replace Bing or promote it into the regular pool. |
| Exhaustion | In the scheduled pool, blocks, suspensions, empty results, timeouts, and parser/transport failures can exhaust a free provider. Busy/reserved/cooling eligible providers cause waiting, not paid spillover. |
| Explicit selection | Exhaust only caller-selected, request-capable free providers in the configured scheduler pool. An explicit Exa-only search may pay immediately. Do not force omitted or incapable free engines back into the selection. |
| Provider override | Keep `SEARXNG_ROUND_ROBIN_PROVIDERS` unrestricted. Its existing pool-selection behavior can bypass the default free-first order; explain this in configuration comments. |
| Scheduling mode | An enabled key requires `SEARXNG_ROUND_ROBIN=true`. Reject the incompatible configuration at startup, even if a particular query would omit Exa. |
| Search product | Fixed `auto` search, up to ten results, bounded snippets, no summaries and no separate content requests. |
| Payment limits | No per-payment ceiling, daily budget, aggregate budget, or fixed expected-price check. Accept Exa's valid quoted amount. This was explicitly chosen after discussing per-request ceilings. |
| Signing | Inside SearXNG; supply the private key only to that service. No signer sidecar. |
| Payment retry | One payment authorization/submission per engine attempt. Never automatically create a second payment after an ambiguous timeout or failed paid response. A subsequent search may try again. |
| Price discovery | Reuse validated payment requirements for the fixed search product without a time-based expiry where Exa/x402 specify none. Invalidate on a rejected payment attempt; discover only on a cache miss and always create a fresh authorization. No periodic refresh or arbitrary cache TTL. |
| Connection diagnostics | Use a non-search JSON probe for Onyx admin setup validation. It invokes no engines and cannot pay, regardless of key configuration. Actual paid search qualification stays in separate bounded live tests. |
| Caller timeout | Preserve current execution after Onyx stops waiting: an already queued SearXNG search may later start Exa. No new end-to-end cancellation protocol. Disclose this behavior. |
| Authorization lifetime | Use the audited SDK’s authorization lifetime and timestamp behavior, independent of the local request deadline; no wrapper lifetime ceiling or clock-skew policy. An issued authorization can remain settleable after timeout or cooldown expiry. |
| Host search access | Default `SEARXNG_HOST` to `127.0.0.1`, including when empty. An explicit non-loopback bind exposes an unauthenticated search endpoint that can spend when Exa is enabled. |
| Recovery | Payment failures suspend Exa for a bounded cooldown. Funding the wallet does not require a service restart after the cooldown expires. |
| Wallet helper | `make x402-wallet` creates a new local wallet, writes a new gitignored owner-only file, refuses overwrite, and prints only the address, file path, and setup instructions. |
| Funding guidance | Recommend a new wallet created with that target, funded with a small amount of native USDC on Base. Wallet funding, not an application budget, bounds exposure. |

The absence of amount limits does not disable SDK protocol validation/signing,
stack-owned chain/token/mechanism selection, origin restrictions, execution
deadlines, or routing policy. Enabling the feature
authorizes automatic spending under these rules, including explicit Exa-only
requests by anyone able to use the instance's search endpoint.

No API-key billing, Solana, testnet, general wallet service, top-up automation,
balance polling, generic fallback framework, paid content crawler, or new search
UI is in scope. Keep Onyx's existing single SearXNG request per query.

## Read first and repository map

Read `AGENTS.md` and these current owning documents before implementation:

- [Request handling](../request_handling.md), especially SearXNG selection,
  provider leases, suspension, fresh per-provider budgets, and scoring.
- [Routing](../vpn_routing_and_proxies.md), [internal network security](../internal_network_security.md),
  and [native Tor](../native_tor_support.md).
- [Resource policy](../resource_minimization.md) and [Podman support](../podman_suport.md).
- [Patch information](../onyx_patch_info.md) and [upgrade requirements](../onyx_patches_upgrade.md).

Current implementation anchors:

| Area | Files and relevant contracts |
| --- | --- |
| Engine registry | `searxng/core-config/settings.yml`: five custom offline engines; inherited engines and plugins omitted; Bing has `last_resort: true`. |
| Scheduling | `searxng/patches/sitecustomize.py`: `_round_robin_providers`, `_round_robin_selected_refs`, `_record_unavailable_round_robin_providers`, `apply_round_robin_search_patch`, offline processor patch, and scoring patch. |
| Browser admission | `searxng/engines/_obscura.py`: reservation/lease/capacity ownership and the browser-specific provider registry. |
| Bootstrap | `searxng/patches/bootstrap_role.py`; resource-tracker imports must stay inert. |
| Onyx search and diagnostics | `onyx/patches/onyx_wrapper_patches/api/searxng_retry.py`; pinned `SearXNGClient.search`, `test_connection`, and `_test_json_mode` in the Onyx reference. |
| Build and settings | `Makefile`, `stack.versions.env`, `.env.wrapper.example`, `searxng/Dockerfile`, `searxng/requirements.in`, `searxng/requirements.txt`. |
| Network | `docker-compose.yaml`, `compose_overlays/docker-compose.code-interpreter-network.yml`, Docker isolation overlays, Tor overlays, `egress/final_hop_proxy.py`. |
| Host settings parser | `tor/render_config.py`: restricted dotenv parser, environment precedence, and preflight. Never shell-source wrapper settings. |
| Tests | `tests/test_searxng_obscura_scheduling.py`, `tests/test_searxng_obscura_engines.py`, `tests/test_searxng_no_retry_patch.py`, `tests/test_searxng_bootstrap_role.py`, `tests/test_obscura_direct_compose.py`, routing/isolation tests, `tests/validate_pinned_patch_images.sh`. |

Current scheduler selection is only regular-versus-last-resort. Two engines
marked last-resort would share a pool; that does not put Exa behind Bing.
Admission and offline processing also currently assume Obscura provider leases.
Both assumptions must change narrowly and explicitly for an HTTP API engine.
The scheduler currently reserves before upstream `get_params()` filters engine
capabilities, and stops when that produces no requests. All five browser
engines lack time-range support. Fix this ordering so an ineligible free engine
cannot prevent a time-filtered request from reaching eligible Exa.

SearXNG currently has only `searxng-api` and `obscura-control` networks and no
Internet path apart from browser work delegated to Obscura. An HTTP SDK cannot
simply be added and allowed to use an ambient proxy or a new direct uplink.

`reference_repos/` is read-only audit material. Do not modify it. Do not read,
rewrite, print, or stage the operator's private configuration or local data.
Use supported Make/Compose configuration paths and synthetic fixtures.
Deterministic/image tests never read private configuration or pay. Explicit
live validation uses the configured key through supported Make/Compose paths;
key presence enables its bounded paid tests as specified below. This plan
revision is not itself an instruction to run the stack or live tests.

## External contracts and audited SDK integration

Primary sources for the selected integration; reverify them on upgrades:

- [Exa x402 quickstart](https://exa.ai/docs/integrations/payments/x402/quickstart).
  As reviewed on 2026-09-26, the flow is an unpaid POST, a 402 challenge, a signed
  POST, and a settlement response. `auto` is documented at $0.007 for up to ten
  results; this is informational, never a price assertion or ceiling. Base is
  `eip155:8453`, with native USDC at
  `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` (six decimals). Discovery is rate
  limited to five unpaid requests per IP per 60 seconds; a successful paid
  request decrements that counter. Paid requests have a separate ten-per-second
  wallet limit. Shared exits can exhaust discovery before this worker's first
  request. Settlement can fail after search work.
- [Exa search reference](https://exa.ai/docs/reference/search): request fields,
  highlights, moderation, publication dates, and result schema.
- [x402 Foundation repository](https://github.com/x402-foundation/x402) and
  [Python SDK](https://github.com/x402-foundation/x402/blob/main/python/x402/README.md):
  transport-independent signing, synchronous/asynchronous clients, policies,
  and built-in spend controls. The reviewed README describes a default amount
  ceiling; explicitly remove that ceiling for this feature while enforcing
  the chain/token restrictions independently. Do not copy wildcard-network
  quickstart registrations.

The Python SDK source audit selects `x402[evm]==2.24.0`, release tag
`pypi-x402@v2.24.0` at `71eb9a55e081e7b81ba3046d0bd17c3eb9c7bf81`.
The local checkout at `4fcf836cc393174130e1358577ce5d37356da1c3` has identical
Python runtime source; its post-tag Python changes concern the unused Solana
dependency bound, its lock, and a changelog entry. The following contract is
audited against the release source, not inferred from the main README:

| Area and source under `reference_repos/x402/python/x402/` | Audited behavior and implementation choice |
| --- | --- |
| `client.py`, `client_base.py`, `mechanisms/evm/exact/register.py` | Use `x402ClientSync` with direct `register("eip155:8453", ExactEvmScheme(EthAccountSigner(account)))`. Do not use `register_exact_evm_client`: even with an explicit v2 network it also registers every legacy v1 EVM network. Register no creation, response, recovery, or extension hooks. |
| `client_base.py` | Call `set_spend_controls({"max_amount_per_payment": False})` to remove the monetary ceiling while retaining the SDK default-asset filtering. Register one deployment policy for native Base USDC, `exact`, absent or `eip3009` transfer method, and absent or `authorization` payment flow. The SDK otherwise recognizes `upfront`/`escrow` too and prefers authorization offers before its first-remaining selector. Within the supported policy, retain the first offer without price comparison or deduplication. |
| `schemas/base.py`, `schemas/payments.py`, `http/utils.py` | Decode `PAYMENT-REQUIRED` with `decode_payment_required_header`; encode with `encode_payment_signature_header` and the v2 `PAYMENT-SIGNATURE` name. Models accept camelCase/field-name aliases, use ordinary Pydantic coercion, ignore unmodeled fields, and default an omitted `x402Version` to 2. Version is an integer, not a literal constraint: explicitly require the decoded version to equal 2. Require absent/empty top-level extensions. Resource metadata is optional; when present, its URL must equal the fixed endpoint. Do not create a second wire parser. |
| `mechanisms/evm/exact/client.py`, `default_assets.py` | Only the literal `permit2` selects Permit2; other values fall through to EIP-3009 in the SDK. Therefore filter unknown transfer methods before signing. Native Base USDC resolves to `USD Coin`, version `2`. If `extra.name` is absent the signer fills name/version from its local asset table; if name exists but version is absent it defaults to `1`. Check the effective domain against Base USDC before signing, accepting the SDK's missing-name fallback but rejecting a supplied incomplete/mismatched domain that resolves differently. The fallback can mutate the selected offer's `extra`; preserve SDK behavior and do not reconstruct signed payloads. |
| `mechanisms/evm/exact/client.py`, `utils.py`, `eip712.py` | Every creation obtains `os.urandom(32)` for its nonce, sets `validAfter="0"`, and sets `validBefore=str(int(time.time()) + (max_timeout_seconds or 3600))`. The required timeout field has no positive-range schema bound; zero selects 3,600 seconds. Typed data uses `TransferWithAuthorization`, the signer address, quoted recipient/value, selected chain/asset, and effective domain. Numeric conversion and typed-data encoding remain SDK/signer responsibilities; no wrapper lifetime ceiling or stricter amount parser is added. |
| `mechanisms/evm/signers.py` | Use `EthAccountSigner(Account.from_key(...))`, never `EthAccountSignerWithRPC` or a facilitator signer. The selected EIP-3009 path performs local typed-data signing with no RPC/facilitator call. Importing the signer still requires both `eth_account` and `web3`; retain the `evm` dependency extra rather than inventing a replacement signer. Its debug log contains the authorization message: force `x402.signers` to WARNING before signing to preserve payment-data redaction. |
| `http/utils.py`, `schemas/responses.py`, `http/x402_http_client_base.py` | Decode only the v2 `PAYMENT-RESPONSE` header with `decode_payment_response_header`. `SettleResponse` validates a boolean success and string transaction/network, with optional payer/amount; it does not validate hash format or bind these fields to the submitted authorization. Require decoded success plus successful HTTP status for paid results. Treat this as Exa's authenticated assertion, with no signature, nonce, amount, payer, or on-chain correlation check claimed. Avoid the convenience getter's legacy-header fallback. |
| `http/clients/httpx.py`, `http/clients/requests.py` | Automatic wrappers own discovery/replay and support hook-driven recovery; the HTTPX wrapper can create a fresh second payment after recovery. Use neither wrapper. Own the two-exchange state machine with plain async HTTPX, explicit proxy/TLS settings, retries disabled, and the existing absolute deadline; call synchronous SDK signing once inside that attempt. |

The source audit is complete; no separate audit artifact or provider statement
is required before implementation. Exa's quickstart and the audited v2/exact-EVM
specifications define authorization validity but no reusable-requirements cache
lifetime. The accepted policy treats that absence as unbounded reuse until a
rejected payment attempt, not as a guarantee that Exa will continue accepting
old terms. `maxTimeoutSeconds` controls each fresh authorization, not cache age.

Implementation still requires build/runtime qualification:

1. Prove hashed installation and imports in the actual Linux Python 3.14
   SearXNG image, including native crypto wheels on supported architectures.
   The current build copies dependencies from a Debian builder into the upstream
   runtime. Verify libc/ABI compatibility before copying any new native wheels;
   adjust the dependency build stage if required, without changing runtime
   Python or silently upgrading unrelated components.
   Validate the combined installed environment with dependency-consistency and
   import checks, including SearXNG's existing HTTPX dependencies; successful
   SDK imports alone do not establish compatibility.
2. Use SDK core signing plus its HTTP codecs with a wrapper-owned HTTP exchange bounded by its execution deadline. Use the SDK for payment serialization/signing rather than
   implementing EIP-712 or USDC authorization manually.
   SDK schemas/codecs are the sole protocol validation authority, including
   their supported coercions and defaults. Do not add stricter wire-type checks,
   a second parser, post-signature reconstruction, or a signature verifier.
   Wrapper policy selects the supported endpoint, network, asset, mechanism,
   and extension behavior through SDK configuration/policies where available;
   these are deployment choices, not a second protocol validator. Audit and
   document the selected release’s behavior rather than emulating it.
3. Require a local signer with no client-side blockchain RPC or facilitator
   requests. Settlement is server-side. If the selected mechanism unexpectedly
   requires additional network authorities, resolve that design discrepancy
   before expanding egress.

The release declares Python >=3.10, core Pydantic/nest-asyncio/typing-extensions,
and EVM eth-abi/eth-keys/eth-utils/eth-account/web3 dependencies. Source compatibility
is not proof of Python 3.14 native-wheel or SearXNG environment compatibility.
Pin directly imported HTTPX and eth-account deliberately during lock integration,
using the combined-image checks above; no Solana, TVM, server, or SDK HTTP-wrapper
extras are needed. Copy this audited integration contract into the owning upgrade
checklist with implementation, and re-audit changed source on later upgrades.

Add exact direct dependency pins to `searxng/requirements.in`, including directly
imported packages as appropriate. Regenerate using `make upgrade-python-deps`;
inspect any other regenerated locks and report unrelated changes rather than
silently accepting them as feature scope. Install at image build only.

## Configuration and startup

Add only `SEARXNG_X402_PRIVKEY=` as the new payment option. Separately document
the existing `SEARXNG_HOST` bind option with its new loopback default. Keep
endpoint, chain, asset, search type, admission limits, and proxy authority
stack-owned.

- Unset and genuinely empty values mean disabled. Whitespace-only or malformed
  nonempty values are errors, not silent disablement. Accept a 32-byte hex key
  with optional `0x`; reject an invalid secp256k1 scalar. Never include its
  value, prefix, or an SDK validation exception in diagnostics.
- Validate syntax, round-robin, and topology agreement before serving requests.
  No startup balance lookup, discovery POST, payment, or wallet RPC.
- Extend the existing structured settings mechanism to emit only an enabled
  boolean for Make layer selection. Do not expand the key in Make commands,
  shell arguments, build args, image labels, hashes, or debug output. Preserve
  existing file/environment/command-line precedence and test overrides.
- Evaluate operator key presence only for workflows needing the selected stack
  configuration, including startup and explicit live qualification. Do not add
  unconditional Make-parse-time key reads: deterministic/image checks, image
  builds, help, and wallet creation must not newly read or validate the operator's
  key. Feature tests use synthetic configuration and isolate inherited key
  values. A failed settings-helper invocation must fail the requesting workflow
  visibly, not become an empty/disabled result through `$(shell ...)`; validate
  the helper's success and boolean output without exposing the key.
- Treat the key specially in generic settings diagnostics/getters: do not add
  a convenience command that prints it. The service receives it through the
  supported Compose environment mechanism, only in the optional Exa layer.
- Reject enabled Exa with round-robin disabled at both startup preflight and
  SearXNG's own startup boundary. Startup validation must fail service launch,
  not merely emit a warning that Python's `sitecustomize` import machinery
  could ignore. Verify the real entrypoint/worker failure behavior in-image.
- Conditionally register Exa through the existing ordered bootstrap. Importing
  pinned `searx` initializes settings, while `searx.search.initialize()` loads
  engines later. Add one secret-free Exa entry to the in-memory settings before
  the existing processor/search imports and before engine loading. Keep the
  tracked base settings as the sole browser-engine configuration; disabled
  mode leaves it untouched and never imports the signer or Exa engine.
  Source-validate this ordering, preserve the canonical settings object, make
  bootstrap installation idempotent, and fail if engines initialized too early.
  Do not generate settings files, redirect `SEARXNG_SETTINGS_PATH`, introduce
  runtime configuration mounts, or replace the upstream entrypoint. This avoids
  stale generated settings and auxiliary-config directory changes.
  Fatal feature/bootstrap errors must escape Python's ordinary `sitecustomize`
  exception suppression (for example through an explicit fatal exit), and be
  qualified with the real parent/worker entrypoint. Engine `init()` failure is
  insufficient: upstream can merely omit that engine and continue serving.
- Preserve the exact resource-tracker/bootstrap exclusion. Pure modules are
  inert on import; no key handling in excluded helper processes.
- Do not expose the private key in diagnostic settings pages, engine metadata,
  exception representations, or health inventory. Container administrators can
  still inspect environment values; document this residual boundary accurately.

Configuration design rationale: do not introduce a generated settings file
selected through `SEARXNG_SETTINGS_PATH`. A Python generator inheriting the
wrapper's `PYTHONPATH` runs `sitecustomize` and its SearXNG imports before the
generator body, so settings/patches can initialize against missing or stale
output. A safe generator would require a bootstrap-isolated step, startup
ordering, writable-file ownership and stale-file handling. Changing the settings
path also changes auxiliary-configuration lookup. Native file selection is not
inherently unsafe; it adds those obligations without improving this feature.
Retain ordered in-memory augmentation and validate settings-before-engines.
Do not reintroduce generation merely to use the native settings-path option.

The integrated Make-selected stack is the supported deployment path. Do not
add standalone-specific key detection or a host-key presence marker. A service
receiving no key remains disabled; a service receiving a key must satisfy the
ordinary startup checks and use the fixed explicit proxy. Invalid configuration
or an unavailable route fails visibly, with no direct or ambient-proxy fallback.

## Onyx connection diagnostics

The pinned `SearXNGClient.test_connection()` calls `_test_json_mode()`, which
POSTs `q=test` using default engine selection and a five-second client timeout.
The admin UI runs this validation before saving/activating a new provider or
changed configuration; reactivation of an unchanged provider skips it. The
search-based probe can time out while browser search continues and does not
check usable search results even when it succeeds.

Replace that probe with a narrow, startup-validated Onyx runtime patch:

1. Keep the `/config` request and SearXNG identity check. Add a bounded timeout
   to this currently unbounded GET.
2. POST `/search` with exactly `format=json` and `q=""`. The pinned SearXNG
   route checks enabled output formats before its missing-query branch; it
   returns before query parsing, scheduler admission, or engine execution.
3. Accept only HTTP 400 with JSON content type and decoded object exactly
   `{"error": "No query"}`. This expected protocol response proves that the
   JSON search endpoint is enabled and reachable. Do not broadly suppress
   HTTP 400 errors. HTTP 403 retains the JSON-disabled/access-denied diagnostic;
   unexpected status, content type, malformed JSON, or a different body fails
   validation visibly. In particular, HTTP 200 is not the expected response.

Use explicit five-second connect/read timeouts on both local HTTP calls,
disable redirect following, and make no retries. These are socket-operation
bounds, not a claimed whole-operation deadline. They no longer need to cover
external search latency, Tor, browser setup, or admission waits. A transport
timeout still prevents that admin save/activation attempt; it cannot leave a
search or payment running because this probe never starts either.

Keep the change in the API service's SearXNG patch area with explicit bootstrap
installation; validate the affected Onyx source shape and composition with
the existing ordinary `search()` retry-removal patch. Require no SearXNG
runtime special case, new endpoint, test-query sentinel, or payment header.
An ordinary user query `test` retains normal search behavior. The setup check
validates local connectivity, service identity, and JSON endpoint access, not
external provider health or payment readiness. Qualify those separately in
the bounded live tests. Periodic/container health checks remain local-only.
Document this patch and its upstream missing-query dependency in the patch
and upgrade guides, and verify both sides against the pinned images.

## Selection, admission, and deadlines

The default provider pool uses three explicit scheduling tiers:

1. Selected regular browser providers, with existing round-robin behavior.
2. Selected `bing2`.
3. Selected and enabled `x402exa`.

Resolve selected engines, request capabilities, suspension availability, and
capacity in that order. Reuse upstream `EngineProcessor.get_params()` for
native pagination/time-range eligibility and add only Exa-specific unsupported
constraint handling. Compute native params once per candidate per search and
carry those params into dispatch; do not duplicate capability rules or invoke
`get_params()` again after reservation. Recheck mutable suspension/capacity
under admission ownership. Do not reserve or wait for an ineligible engine. A
non-suspended, untried, request-capable provider in an earlier tier prevents a
later tier from starting while it is busy or cooling. A skipped provider cannot
terminate rotation while another eligible provider remains. A completed attempt
with no main results exhausts that provider for this search. Existing main-result
success stops rotation. Never
select any provider twice in one search.

Default engine selection includes Exa only when enabled. Explicit engine
parameters, shortcuts, categories, and preferences must resolve to the actual
selected set before tiering. Selecting Exa alone authorizes immediate paid
execution; omitting Exa authorizes none. Use the documented shortcut
`x402exa`. Retain the unrestricted internal `SEARXNG_ROUND_ROBIN_PROVIDERS`
override and its current selection semantics. Do not require inclusion of Exa
or any free engine, silently insert omitted names, or reject custom pools.
When selected engines intersect a nonempty override pool, schedule that
intersection; selected engines outside it are omitted. When there is no
intersection, including an empty override, retain native selected-engine
dispatch, which can fan out and start enabled Exa immediately. Within a
scheduler pool, retain the three tiers above. Free-only requests never pay.
Choose pool-versus-native dispatch from the caller's selected set before
capability/suspension filtering; an exhausted or ineligible pool must not switch
to native dispatch and resurrect engines the override omitted. In native
dispatch, Exa's admission wait remains bounded by that dispatch's effective
engine deadline; the fresh-after-admission window applies to scheduled attempts.

Require comments beside the scheduler configuration in
`searxng/core-config/settings.yml` and the enablement/round-robin comments in
`.env.wrapper.example` to explain these properties: free-first is the default
pool policy, excluding free providers can accelerate payment, excluding Exa can
omit it, and an empty or disjoint pool restores native dispatch and can run Exa
alongside free providers. Keep the internal override out of the example's
user-option assignments. Payment validation, API admission, deadlines, and
the one-authorization rule apply under every override.

Separate selection priority from result scoring. Retain Bing's current scoring
unchanged; Exa results retain Exa ordering through normal SearXNG result
handling. Do not mark Exa as a regular engine merely to reuse scoring, and do
not use Bing's quality penalties as its selection mechanism. Exa uses ordinary
non-Bing scoring in native mixed-engine dispatch; native URL merging can affect
combined order. Retain Bing’s confirmation/penalty behavior because native
fan-out remains supported.

Remove the existing temporary mutation of the upstream results module’s global
`sorted` binding in `apply_last_resort_scoring_patch`. Use a source-validated
function-local sorting implementation that preserves upstream grouping and
cache behavior without shared mutable globals or a process-wide sorting lock.
One Granian worker still handles concurrent requests. Add a deterministic
interleaving test for independent result containers, including mixed Exa/Bing
results, and verify the upstream module globals remain unchanged.

Add a small API-provider admission implementation or narrowly abstract the
existing admission boundary. Share only reservation ownership, release, and
wake-up mechanics where that reduces duplication; browser sessions and browser
capacity remain transport-specific. Do not build a generic provider framework.
Do not add Exa to the Obscura browser registry,
create a browser context for it, or require a fake browser lease. Dispatch,
reservation release, failure classification, and unavailable reporting must
handle both transports explicitly. Preserve atomic classification-before-release
so concurrent searches cannot race a newly recorded suspension.
Use the existing processor `SuspendedStatus` as the sole cooldown authority.
API admission owns only reservation, active-attempt ownership, and start
spacing; do not duplicate suspension state in an API-provider registry. Reuse
native exception `suspended_time` support and processor exception handling for
explicit durations; do not build a parallel cooldown manager or error ledger.

Initial fixed API admission policy: one active Exa attempt per SearXNG worker
and at least three seconds between attempts' first HTTP request starts (unsigned
on a cache miss, signed on a hit). Keep the current single-worker deployment;
validate that assumption when Exa is enabled. No
distributed limiter, request deduplication cache, or persistent payment ledger.
Use condition/deadline waits, not polling threads. Different user searches are
different billable attempts, even if their query text is identical.
The three-second interval is local start spacing, not a guarantee against
Exa's per-IP discovery limit, including when an exit is shared. Reusing payment
requirements avoids routine discovery traffic; it cannot guarantee a cold-cache
request succeeds on an already exhausted exit. Retain the documented 429
handling; do not add a second rate-limit manager or change exits automatically.

Configure Exa with a 60-second SearXNG engine window and a maximum 55-second
transport/signing budget after admission. Derive its absolute monotonic
deadline as the earlier of admission completion plus 55 seconds and the
processor's actual `start_time + timeout_limit` minus one second. Honor shorter
caller `timeout_limit` and configured `max_request_timeout`; an already-expired
budget sends neither request. Matching the browser engines' configured window
preserves upstream's shared native-dispatch timeout without a new per-engine
timeout scheduler. Up to two HTTPS exchanges and server-side settlement must fit
inside this budget, including when the selected route uses Tor.
Qualify the 60-second choice with slow-response fixtures and the bounded live
Tor case; report insufficient route latency allowance rather than adding retries
or silently extending a deadline. Existing browser timeouts remain unchanged.
Scheduled Exa capacity waiting precedes its engine window; the native-dispatch
exception is specified above.

The absolute deadline must cover connection setup, all response bodies,
challenge validation, signing, and paid submission. Requests-style read
timeouts alone are not total deadlines. A practical implementation is bounded
async HTTPX I/O under `asyncio.timeout` in the selected engine thread, using
SDK local signing and a per-attempt client closed on all exits. Do not add a
permanent event-loop thread or share Obscura's browser loop for this purpose.
`asyncio.timeout` is cooperative and cannot preempt synchronous SDK signing or
parsing. Check the monotonic deadline and engine timeout marker before and after
each such operation, and immediately before paid dispatch. Do not restart the
budget after discovery, signing, or an HTTP phase. Keep API ownership until
the actual attempt exits, including cleanup after SearXNG stops waiting.
No deferred task may start a payment after engine timeout. A paid request
already sent can settle despite local cancellation; report that uncertainty.
Do not claim ordinary SearXNG HTTP disconnects cancel work unless the pinned
server actually propagates them; document and test that boundary.

Preserve the existing lack of a whole-SearXNG-request deadline. Onyx's
ten-minute tool-runner timeout stops waiting without cancelling the in-flight
SearXNG request. Admission waits can therefore leave a search queued when Onyx
returns a timeout, and that search may subsequently start and pay Exa. This
feature does not add cancellation propagation or a durable request ledger.
State this consequence beside automatic-spending setup guidance. It is distinct
from a submitted payment settling late. Each admitted Exa attempt still obeys
its effective engine deadline, and no local retry is introduced.

## Exa request and payment state machine

Implement `searxng/engines/x402exa.py` as an offline callback owning the HTTP
exchange, with inert support code separated for transport, SDK integration,
and result parsing where useful. It is an API engine despite the SearXNG
offline callback interface. Adapt the processor patch accordingly.

Use fixed `https://api.exa.ai/search`, POST JSON, `type: "auto"`,
`numResults: 10`, and highlights within that search response. Set text retrieval
off and omit summaries, extras, subpages, generated answers, and location
inference. Preserve query text without adding wallet/user identity to it.
Audit the current API's supported highlights options; bound snippets locally
to 2,000 Unicode characters per result regardless of upstream controls.

Pass query operators such as `site:` through as text after native SearXNG query
resolution, without promising browser-engine-equivalent filtering. Exa documents
structured domain filters instead of `site:` operators; this feature does not
translate operators into those fields or add a query parser. Document this
limitation beside the language and time-range behavior.

Resolve request behavior before admission/discovery:

| Input | Exa behavior |
| --- | --- |
| Page | `paging = False`; reuse native exclusion for page > 1. Never purchase page one again for a later-page request. |
| SafeSearch | `0` sends `moderation: false`; `1` and `2` send `moderation: true`. Advertise moderation support with the documented limitation that strict and moderate are equivalent at Exa. |
| Time range | Advertise support. Capture one UTC instant when serializing the request; day/week/month/year mean the preceding 24 hours/7 days/30 days/365 days. Send that lower bound as `startPublishedDate` and the captured instant as `endPublishedDate`. Omit both when unset. Use the same serialized bounds for both POSTs. |
| Language | Advertise no language filtering. Unset, `auto`, and `all` impose no language constraint. Exclude Exa for a concrete requested language, including one saved in preferences or supplied in query syntax. Use the native pre-resolution `selected_locale` returned by `get_search_query_from_webapp`, passed through a narrow request-scoped adapter, so an automatically resolved locale is not mistaken for an explicit constraint. Do not reparse language syntax or infer provenance from engine `searxng_locale` alone. |
| Category | Register only in `general`; honor native selected-engine/category resolution without mapping SearXNG categories to Exa product categories. |

Native capability exclusions and unsupported concrete language are engine
ineligibility, not provider failure: no discovery, payment, suspension, or
synthetic result. An Exa-only request excluded this way follows ordinary
SearXNG no-eligible-engine behavior. Invalid parameter values rejected by the
request parser retain its normal error response. Do not create a generic
constraint framework or silently claim to honor unsupported filters.

### Reusing payment requirements

Keep one worker-local, in-memory entry for the fixed endpoint, `auto`, ten-result,
highlights-only product and the audited supported payment policy. Reuse across
the supported query/filter variations and exit changes; a rejected payment
invalidates stale terms. Do not key by query or exit IP, retain search bodies/results, or
cache signed payloads, nonces, receipts, or authorizations. This is requirements
reuse, not request deduplication or HTTP response caching.

No requirements lifetime is specified by the audited Exa/x402 contracts, so
retain the entry without a time-based expiry and invalidate only on payment
rejection during that worker's lifetime. Restart or a product/policy change
clears this process-local entry. Refresh only on demand. No disk
store, periodic refresh, startup probe, separate cache service, or cache lock:
the existing single active Exa attempt owns reads and updates. A fresh SDK
authorization is created for each search using current signing time and a new
nonce; an earlier authorization's expiry does not expire the reusable terms.

Successful payment/search retains the cached requirements for subsequent searches.
An HTTP 402 response to a signed request, or an SDK-decoded settlement receipt
reporting `success=false`, ends the attempt and invalidates the cached
requirements. These are payment rejections for cache purposes, not independent
proof of non-settlement. If that response carries new SDK-valid, policy-approved
reusable requirements, retain those for a later
search after the normal cooldown; otherwise leave the cache empty. Never make
another payment or a discovery request to recover that same attempt. Other
failures, including 429 or ambiguous transport failure, retain usable terms;
they do not by themselves prove a price change. Cache updates must not overwrite
the independently recorded settlement outcome. A cached quote is not a promise
of today's price or recipient: Exa may reject it, and rejection can cost one
failed search. Do not fetch a fresh quote before every payment to avoid that
possibility, or invent a current quote from documentation when discovery fails.

State machine:

1. Admit the attempt; capture an immutable serialized request body and deadline.
2. Use reusable cached requirements if present; otherwise send one unsigned
   request. Never send API-key or bearer authorization.
3. On a cache miss, decode the x402 v2 challenge with the audited SDK codec.
   Let the SDK own structural validation, coercions, defaults, amount/address parsing, and
   serialization. SDK-rejected input is terminal and creates no authorization.
4. Filter SDK-decoded offers to `exact` on `eip155:8453` for native USDC and
   the supported EIP-3009 mechanism; then use the SDK's first-supported-offer
   selector. Mixed unsupported alternatives such as Solana do not invalidate
   a supported offer. No supported offer is terminal. Do not deduplicate offers,
   compare supported offers for ambiguity, or choose by price. Accept any SDK-
   accepted quoted amount; remove the SDK's default monetary ceiling.
   Accept absent/explicit `eip3009` transfer method and absent/explicit
   `authorization` payment flow as established by the audit above; exclude
   Permit2, unknown methods, upfront, and escrow before signing.
   Register no extension signing hooks; require absent/empty top-level
   extensions for this initial deployment. Descriptive resource metadata is
   not an extension. Never sign allowances, approvals, or extension payloads.
5. Trust the recipient and quote supplied by the authenticated fixed Exa HTTPS
   origin; do not pin a recipient from examples or independently revalidate
   addresses/amounts. Apply the fixed endpoint/resource and Base-USDC domain
   policy to SDK-decoded inputs before signing, preferably through SDK policy
   facilities. Enforce the audited effective token domain (`USD Coin`, version `2`,
   chain 8453, native-USDC contract), including the SDK's local default behavior. Domain data
   is an input to SDK typed-data signing, not a field to recover from the
   returned authorization. SDK-level synthetic tests establish domain,
   recipient, amount, payer, and timestamp construction; do not add production
   signature recovery or reconstruct typed data in wrapper code.
6. Retain reusable validated requirements. Create one fresh SDK payment
   authorization and send it once to the fixed endpoint using the SDK's v2
   payment header. If discovery was needed, use exactly the same request body;
   a cache hit sends only this signed POST, with the current search's body.
7. Decode the settlement receipt through the SDK and require successful HTTP
   status and settlement outcome before returning paid results. The audited
   codec performs schema validation only, without binding the receipt to the
   submitted authorization; do not invent independent transaction or on-chain
   verification. A receipt is a server assertion, not RPC proof.
   Missing/SDK-invalid receipts fail visibly. Do not issue a third request.
   Acquire the response with the HTTP client's streaming interface and retain
   the decoded receipt outcome as soon as headers arrive, before consuming or
   decoding the body. Read the body under the same absolute deadline and close
   the response on every exit; native framing/decompression remain authoritative.
8. Return normalized SearXNG results, or record a sanitized engine error and
   release the reservation after outcome/suspension recording.

Use the SDK's authorization timestamps, lifetime defaults, and supported
challenge semantics. Do not add a wrapper 1..3,600-second acceptance rule,
one-second rounding comparison, or fixed 30-second dispatch-skew allowance.
The host clock must be synchronized. Test the audited SDK's
`validAfter`/`validBefore` behavior, including its defaults, without duplicating
it in runtime code. Local monotonic deadline checks still prevent signing or
submission after the engine's execution window, and failures never re-sign.
Do not claim authorization validity is bounded by that local window or by a
wrapper-defined maximum lifetime.

The authorization expiry is an on-chain settlement boundary; the monotonic
deadline only bounds local execution. Cancelling a request, suspending Exa for
300 seconds, restarting the worker, or funding the wallet does not revoke a
submitted authorization. An ambiguous authorization may settle until its
expiry, including after a later top-up or another search. No revocation RPC,
persistent ledger, or cooldown extension to track every authorization is added.

An unsolicited valid unsigned 200 response may be accepted as an unpaid search
result; it must not cause signing merely because the engine is configured to
pay. Any 402 after paid submission is terminal for this attempt, even if
it contains another challenge; cache maintenance only affects later searches.
No automatic HTTP retries, redirect following,
SDK payment recovery hooks, or caller-layer replay.

Use the SDK for payment headers and the HTTP client's native framing,
header handling, and decompression. Do not add custom 64-KiB payment-header or
1-MiB Exa-body caps, a bounded-decompression implementation, or a second JSON
protocol parser. Parse the Exa search JSON separately because it is an Exa
product response, not an x402 payment structure. Validate only the fields needed
for SearXNG results. Preserve order, return at most ten entries, normalize
title/highlights to safe plain text, and omit remote images/favicons.
Require a nonempty string title, valid URL, and a nonempty list of string
highlights yielding nonempty plain text for each usable row. Join highlights
in their supplied order and truncate the combined snippet to 2,000 Unicode
characters. Missing/empty highlights do not trigger text retrieval or a second
content request. Skip invalid rows, retain valid rows in order, and emit a
sanitized invalid-row count through existing diagnostics. Return at most the
first ten valid rows; do not return raw row data in diagnostics.
Validate result URLs with `private_onyx_obscura.normalize_public_url`, without
local target DNS, and reattach its separately returned fragment. Preserve query
strings, fragments, and percent-encoded path/query/fragment separators; do not
unquote the URL or apply a second canonicalizer. Use `allow_http=True` for
result metadata, matching the browser parsers' acceptance of HTTP and HTTPS
links. This also permits structurally acceptable onion links as metadata,
without checking onion-address validity or enabling access to them. Returning
a link is not authorization to fetch it: subsequent crawling independently
enforces its configured cleartext-HTTP/onion policy and final-hop destination
validation. Do not add a Tor capability or network call to normalization.
Reject credentials, internal destinations, and unsupported schemes through
the existing helper. Do not fetch result URLs during normalization. An empty
well-formed result list is a successful empty search; malformed data or a list
with no valid entries due to invalid rows is an error, not fabricated emptiness.

### Trust and resource scope

Verified TLS to the fixed Exa origin prevents an ordinary VPN, proxy, Tor exit,
or network observer from replacing challenges or search responses. Exa is
trusted to quote a recipient/price and to return search content. A compromised
or faulty Exa service can provide excessive responses; indexed websites can
influence result content, but do not directly supply the authenticated payment
challenge. A compromised local service/key holder is outside the protection of
application response limits. The public-only egress boundary still applies.

The SDK validates payment structures and signs payments; it does not validate
Exa result rows or impose this application's response-memory budget. Retaining
native HTTP/SDK behavior accepts the risk of an excessive response consuming
memory: execution deadlines are not a hard memory or synchronous parsing-CPU
bound. The ten-result request and 2,000-character output snippets are product
and downstream-context limits, not hostile-response memory protection. Keep
existing container resource policy; do not claim the removed byte caps are
provided implicitly by the SDK. Any future hard response-memory requirement
needs evidence and a separate explicit design, not speculative parser layers.

## Failure and diagnostic policy

Use sanitized exception classes/reason codes, not SDK exception bodies or
provider-provided text. Distinguish `payment_rejected`, `payment_status_unknown`,
`rate_limited`, `provider_unavailable`, `invalid_response`, and configuration
errors. Preserve ordinary SearXNG unresponsive-engine reporting and the
existing Onyx client behavior; do not return synthetic search results to
explain failures.

Initial bounded cooldown policy:

| Outcome | Behavior |
| --- | --- |
| HTTP 429 | Suspend at least 60 seconds; honor valid Retry-After up to 3,600 seconds. |
| Access denial | Suspend for 3,600 seconds, consistent with free-provider blocking policy. |
| Any other unexpected outcome or attempt failure | Suspend for 60 seconds before signed dispatch, or 300 seconds after signed dispatch; preserve any known payment outcome. |
| Valid empty result | Return empty; no failure suspension beyond normal admission spacing. |
| Invalid key/configuration | Fail startup; do not downgrade to disabled mode. |

All unexpected outcomes cause suspension through that default rule; do not
enumerate every HTTP status, malformed response, or library exception. Explicit
429/access-denial rules take precedence. For Retry-After, accept standard delay
seconds or HTTP dates and clamp the delay to 60..3,600 seconds; absent, invalid,
or past values use 60 seconds. Expected capability exclusions and admission
deadline expiry before an attempt starts are not provider failures and do not
suspend Exa.

Translate attempt failures once at the API processor boundary into sanitized
exceptions using native `suspended_time` support and `handle_exception` with
suspension enabled. In particular, do not let SDK/parser `ValueError` take the
existing offline processor's log-only path, or let an unexpected exception
escape cooldown through its generic non-suspending path. Retain one suspension
authority and classification-before-release; no parallel error manager.

Suspension is demand-driven and process-local like current provider state.
It clears on restart and expires without a background probe; recheck on the
next eligible search. Do not pretend generic verification errors prove an
insufficient balance. Unknown paid outcomes remain potentially charged.
Track payment outcome separately from search-response validity within the
attempt: no submission, reported rejection, reported settlement success, or
unknown after submission. These are transient classifications, not a ledger.
A successful SDK-decoded receipt remains reported settlement success even if
result JSON is malformed or every row is invalid; report `invalid_response`
with that payment outcome and suspend for 300 seconds. Valid empty results with
a successful receipt remain successful paid empty searches without a failure
suspension. Retain a decoded receipt independently of body consumption as well
as JSON parsing: truncated framing, decompression errors, disconnects, and
deadline expiry after headers cannot erase available settlement evidence.
These failures return no results and use the paid-failure cooldown while
preserving any reported settlement outcome. A failure before usable receipt
headers remains unknown after submission.

Once a signed request has been dispatched, failures without a conclusive
SDK-decoded settlement outcome use the 300-second payment-uncertainty cooldown.
An explicit 429/access denial uses its status-specific cooldown while retaining
the independently established payment outcome. HTTP status alone does not
prove non-settlement. Reported success is still the server assertion described
above, not independent on-chain proof. No failure path re-signs or retries.

Never log keys, signatures, signed payloads, raw challenges, request bodies,
queries, result bodies, wallet addresses, or transaction hashes automatically.
This restriction applies to the new Exa/payment path. Existing Onyx debug-level
search-payload logging is accepted and remains unchanged; do not claim a
stack-wide prohibition on debug query logging. The SDK's authorization-message
debug logging is payment data and must remain suppressed as specified above.
Use existing engine timing/failure statistics and sanitized reason codes;
no persistent accounting system or metrics service. Ensure processor logging,
SDK logging, nested exceptions, and debug representations cannot bypass this.
Wallet creation deliberately prints the new public address for funding.

## Host search ingress

Keep the existing diagnostic host publisher, but change its port mapping to
`${SEARXNG_HOST:-127.0.0.1}:${HOST_PORT_SEARXNG:-8080}:8888`. Both unset and empty
`SEARXNG_HOST` bind IPv4 loopback, with no implicit `0.0.0.0` or IPv6 wildcard
listener. An explicit operator bind remains supported. Add `SEARXNG_HOST=127.0.0.1`
to `.env.wrapper.example` beside the diagnostic port. Apply this default in
both lite/full modes regardless of Exa enablement; preserve container-to-container
SearXNG gateway access.

This endpoint has no Onyx authentication. Local callers, and any callers made
reachable through an explicit broader bind/forwarder, can select Exa and spend.
Loopback binding reduces network exposure; it does not add user authorization.
Document that consequence beside the bind option and paid-search setup. Put
the browser-mediated spending residual in `docs/internal_network_security.md`;
no dedicated tests for that residual are required. No new
auth proxy, service profile, or publisher removal is part of this plan.

## Optional restricted egress

Add a Make-selected overlay, provisionally
`compose_overlays/docker-compose.searxng-x402.yml`, only when the key is nonempty.
Use a new dedicated `searxng-x402-egress` internal caller network and
`searxng-x402-policy-upstream` internal bridge network:

```text
searxng-core
  -> searxng-x402-egress-bridge:3128
  -> shared public final-hop listener in netns-holder:3132
  -> selected explicit no-VPN / VPN / upstream proxy / Tor route
  -> api.exa.ai:443
```

Reuse existing fixed TCP bridge and public policy implementation. Join the
namespace holder to the dedicated upstream network; add the bridge to the
public policy's allowed peer set. Never attach SearXNG to the routing namespace,
browser-egress, Onyx public/host caller networks, or an external uplink. No
host ports, host exceptions, Docker socket, or Tor socket mount.

The application transport accepts only the fixed Exa HTTPS endpoint and the
exact stack-owned proxy URL, with TLS certificate verification, no redirects,
no ambient proxy discovery, no `NO_PROXY` bypass, and no ambient `.netrc`
authentication. Public destination DNS remains at the selected final hop.
Reuse the existing public destination policy; do not add a new general policy
language. This means a compromised SearXNG service gains public-only proxy
egress when enabled, rather than a network-enforced Exa-only route. Document
that residual scope explicitly; application endpoint restrictions are not a
sandbox after compromise.

Compose integration must account for:

- The executor overlay currently replaces `EGRESS_PROXY_ALLOWED_CLIENT_HOSTS`.
  Derive the final stack-owned peer list centrally from selected feature
  booleans so executor and Exa peers survive together; remove competing
  overlay replacements. Do not add executor/Exa combination overlays. Do not allow
  arbitrary operator peer additions or include unresolved optional peer names.
- Docker isolated gateway options for both new internal networks, and separate
  Podman behavior. Do not claim Podman provides Docker's host isolation.
- Existing rootless/native Linux/macOS layering and root-base-first paths.
- Startup dependencies and local bridge health checks following five-second
  startup/ten-minute steady cadence. Health must never query Exa or spend.
- Clean shutdown after a key is removed: Make's down-layer handling must still
  remove previously enabled feature services without requiring the old secret.
- Health inventory, preflight, effective model inspection, and enabled/disabled
  models with both executor settings and Tor roles.

Enabling or disabling Exa requires a full matching-mode transition on the same
container engine: `make down-lite`, change the key configuration, then
`make up-lite`; or `make down-full`, change the key configuration, then
`make up-full`. Run down while the old configuration is available when possible;
the down-layer handling above must also work if the key was already removed.
Do not promise an in-place toggle through `make up-*` alone. Adding/removing the
upstream network changes `netns-holder`, so the complete teardown/recreation
also replaces its namespace residents, including Myst and both policy proxies.
Use the existing lifecycle workflow, not a new rolling-recreation controller.

No direct fallback if Exa blocks a Tor exit or the selected proxy fails.
The API uses Python TLS rather than Obscura browser impersonation; do not claim
it inherits browser anti-fingerprinting. Exa sees queries and a stable wallet
identity; settlement exposes public payment activity and possible funding
linkage. This remains true over Tor.

## Wallet creation workflow

Add `make x402-wallet` to `.PHONY`, `make help`, and user setup documentation.
Default output: `.x402-wallet/private-key.env`, containing a single
`SEARXNG_X402_PRIVKEY=0x...` assignment. Ignore the entire `.x402-wallet/`
directory in both `.gitignore` and `.dockerignore` before any helper can create
it. The directory is mode 0700 and the file mode 0600, owned by the invoking
host user. Reject symlink parents/destinations and unsafe existing directory
ownership/permissions. Never overwrite an existing destination, even an empty
file, and handle concurrent invocations without a check-then-write race.

Use the already-selected local derived SearXNG image for audited crypto
dependencies, in a disposable `--network none`, read-only, capability-dropped
container without runtime env files or stack volumes. Disable container log
storage for the key-generation process so its captured stdout is not also
retained by the engine logging driver; verify this under both runtimes. If missing, fail with
`make searxng-build` instructions; wallet generation must not silently pull or
build. Do not require a funded key or a running stack to create the first wallet.

A small host Python helper can capture the container's bounded machine-readable
key/address output in a private pipe, validate its shape, and create the file
exclusively using host filesystem APIs. Never stream that payload to the
terminal or interpolate it into shell arguments. Override the image entrypoint
and bootstrap environment so SearXNG does not start. Use the audited library's
secure local key generation/address derivation, not handwritten crypto.

Complete writes before reporting success; on failure clean up only the file
created by that invocation, never an existing wallet. Test interrupted/failed
writes, permission checks, symlinks, and concurrent creation. No binds requiring
cross-engine ownership repair, no host pip installation, no external key service.

Print the public address and file path with instructions to fund that address
with a small amount of native USDC on Base and manually copy the assignment
into `.env.wrapper`. Do not automatically read/write/source `.env.wrapper`,
import the wallet file on startup, fund the address, show its key, or create a
seed phrase. Manual configuration preserves the explicit nonempty-key opt-in.

## Implementation phases

### 1. Feasibility and payment primitive

- Apply the audited SDK 2.24.0 integration and pin direct dependencies;
  regenerate hashed locks and prove
  Linux Python 3.14 image compatibility.
- Remove unused Playwright from SearXNG's dependency input and Dockerfile import
  check, then remove unused transitives through lock regeneration. Its engines
  and shared Obscura client use direct CDP/WebSockets. Preserve Onyx's separate
  Playwright dependency and runtime patches. Remove the obsolete SearXNG
  Playwright/Onyx-version coupling from the upgrade guide and qualify the
  resulting image through the focused engine/parser gate.
- Implement inert SDK integration, local signing adapter, deadline-bound HTTP
  exchange, single-entry requirements reuse, and response normalization with
  synthetic fixtures using unbounded requirements reuse until payment rejection.
- Qualify the audited release in the selected image: removed monetary ceilings,
  restricted network/asset/method/flow/domain, and no hidden network requests or
  retries. The source audit is complete; hashed installation, combined dependency
  compatibility, and actual image/runtime tests remain implementation validation.

### 2. Engine and scheduling

- Add conditional engine registration and fatal startup validation.
- Implement capability filtering before tier/capacity selection, the third tier,
  and transport-specific admission/processor handling with one suspension
  authority.
- Remove Google/Brave time-range dictionaries and request-field branches that
  native `get_params()` cannot reach while `time_range_support=False`.
  Preserve that capability declaration; enabling browser time filters is not
  part of this feature. Remove direct-callback tests that imply supported
  filtering and cover exclusion through the real processor instead.
  Update `docs/request_handling.md` to remove unreachable Google `tbs` and
  Brave `tf` fields from its form-field description; retain pagination fields.
  Remove those fields from `INTERACTIONS`' `allowed_fixed_field_names` in
  `searxng/engines/_obscura.py` too, and update the associated policy fixtures.
- Add explicit-selection behavior, cooldowns, deadlines, sanitized errors, and
  no-repayment guarantees. Preserve browser behavior and scoring semantics;
  remove the shared-global `sorted` mutation with concurrent regression coverage.
- Replace Onyx's admin setup search with the non-search JSON probe and bound
  both local HTTP calls. Preserve ordinary search and its caller-timeout boundary.

### 3. Deployment and wallet helper

- Change the host search bind default to loopback and document explicit exposure.
- Add the conditional egress layers, safe boolean detection, key injection,
  peer-list composition, Docker isolation, Podman lifecycle handling, and
  feature-removal shutdown handling. The live test uses the existing service,
  so removing its fixed container name is not required for this feature.
- Add offline wallet helper, ignore rules, Make target/help, and exact image
  build-input hashing for every newly embedded source file. Confirm the current
  `SEARXNG_WRAPPER_BUILD_INPUTS` globs include all new support modules/scripts;
  explicitly extend them where necessary.

### 4. Documentation and qualification

- Update current owning docs in the same implementation change, complete the
  tests below, and consolidate acceptance evidence and remaining limitations.
- Keep this active plan accurate. Before implementation is declared complete,
  move lasting behavior to owning docs and replace duplicated specifications
  here with links. Do not edit frozen implemented plans. Do not commit unless
  requested; if commits are requested, use one per phase.

## Deterministic and image validation

Use synthetic keys, fake clocks, mocked transports, and disposable filesystem
paths. Tests must not need real funds, live Internet, or private env contents.
Add focused modules such as `tests/test_x402exa_engine.py` and
`tests/test_x402_wallet.py`; extend existing scheduling/Compose/image tests.

Required cases:

- Disabled mode: no Exa registration/signing/discovery/network services; free
  search behavior and resource-tracker imports unchanged.
- Valid/invalid keys, whitespace, scalar bounds, configuration precedence,
  round-robin conflict, real fatal parent/worker startup behavior, and secret-free
  errors. Prove in-memory registration precedes engine loading, is idempotent,
  and preserves the canonical settings object and auxiliary configuration path.
  Exercise the pinned real engine loader and assert `x402exa` is present in both
  the engine registry and processor registry; scheduler stubs are insufficient.
- Make feature detection: synthetic file/environment/command-line precedence,
  helper failure and invalid output cannot disable the feature silently, and
  offline/image/build/help/wallet workflows do not evaluate the operator's key.
  Include an inherited malformed key to prove those workflows stay independent.
- Free success avoids payment; all exhaustion causes rotate in order; occupied
  regular/Bing providers block Exa; Bing suspension permits Exa; each attempted
  engine runs once; explicit Exa-only pays; free-only never pays; selected
  subsets omit unselected providers. Custom pools remain unrestricted: cover
  omitted free providers, omitted Exa, empty/disjoint pools, and native fan-out;
  payment/admission invariants still apply when free-first ordering is bypassed.
  An ineligible/exhausted nonempty intersection cannot restore omitted engines;
  native dispatch cannot start a queued Exa payment after its engine deadline.
  Mixed browser/Exa native dispatch uses the common configured 60-second
  window; Exa must not enlarge browser processor/admission windows. Preserve
  upstream query/configuration timeout calculation and test shorter limits.
- Eligibility precedes admission: time-filtered default searches reach Exa;
  busy ineligible providers cannot delay them; unsupported pagination/language
  performs no discovery/payment and does not suspend Exa. Cover mixed-capability
  selections and no-eligible-engine searches. Verify SafeSearch mapping, fixed
  UTC bounds, automatic language resolution versus explicit/saved language, and
  categories through the pinned request parser, not engine stubs alone. Assert
  one native params calculation per candidate, reused after admission.
  Verify query operators remain text without wrapper translation or extra calls.
- In the offline image gate, exercise default free-provider exhaustion through
  the real scheduler and engine into a synthetic successful payment; keep this
  fixture out of production configuration and the live validation workflow.
- Concurrent reservation and suspension races, cooldown expiry/restart, cleanup
  after all exceptions, and no browser leases/contexts for the API engine.
- Cache miss: at most one unsigned POST plus one signed POST with the same body
  and endpoint. Cache hit: only one signed POST with a fresh nonce/authorization
  and the current query body. Unsigned 200 never signs or fabricates cache terms;
  paid 402 is terminal; no redirect/retry/auth inheritance.
- Requirements reuse across supported query/filter changes and simulated exit
  changes; no age-based expiry, including after prior authorizations expire;
  restart invalidation; serial concurrent callers share one discovery.
  Paid 402 or a decoded negative settlement receipt replaces
  terms only with a validated challenge for a later attempt, otherwise clears
  them. Transport/429 failures preserve usable terms and normal cooldowns.
  A cold-cache discovery 429 neither signs nor retries; a warm-cache search sends
  no discovery. Prove fresh signing timestamps after earlier authorizations
  expire, and no retained search bodies or signed payloads. These fixtures
  prove the accepted reuse policy, not a provider guarantee of future acceptance.
- SDK protocol acceptance/rejection and deployment network/asset/mechanism/
  endpoint/domain policies; SDK default price cap actually removed; a quote
  above $1 accepted in a synthetic test. Test the pinned SDK's documented
  coercions/defaults without introducing stricter wrapper expectations.
- Mixed Base/Solana alternatives; duplicate and differing supported offers use
  the SDK selector; no supported offer; absent/explicit EIP-3009 method;
  Permit2, unknown methods, upfront/escrow flows, and extensions excluded before signing;
  descriptive metadata accepted; no allowance/approval signing or RPC calls.
  Explicit non-v2 versions are rejected after decoding; omitted version retains
  the SDK default. Cover missing-name domain fallback, name-without-version
  resolving to unsupported version 1, and explicit incorrect domain fields.
  Receipt tests reflect schema-only validation, optional payer/amount, and no
  authorization correlation; the wrapper accepts only the v2 receipt header.
- SDK authorization lifetime/defaults, `validAfter=0`, and typed-data domain,
  payer, recipient, amount, and timestamp construction using synthetic keys.
  Separate fake wall/monotonic clocks prove local timeout/cooldown does not
  imply authorization revocation; no custom lifetime/skew enforcement.
- Concurrent scoring on independent containers preserves Bing/Exa ordering,
  duplicate-URL merging, grouping, and cache behavior without mutating module
  globals. Native fan-out retains Bing's existing scoring semantics.
- Successful receipt and results; successful receipt with malformed JSON,
  all-invalid rows, or valid empty results, preserving payment outcome separately
  from result validity and applying the specified cooldown; missing/invalid
  receipt after payment;
  successful receipt headers followed by truncated framing, decompression
  failure, disconnect, or body deadline expiry retain reported settlement
  success, return no results, and never trigger repayment;
  ambiguous timeout before/after submission; cancellation before signing and
  dispatch; late engine results cannot trigger another payment.
- Absolute deadline against slow connect/read/chunked responses, native HTTP
  decoding failures, malformed Exa JSON, snippets/URL normalization, empty results,
  mixed/all invalid rows, missing/empty highlights, and no URL fetches.
  Exercise shortened query/configuration timeouts during discovery, signing,
  and dispatch; synchronous work that crosses the deadline cannot submit a
  payment afterward. Assert reservation retention until real cleanup completes.
- Result URL identity: preserve query-addressed resources, fragments, and
  encoded separators through normalization and SearXNG/Onyx serialization.
  Cover HTTP/HTTPS/onion metadata, invalid schemes, credentials, internal
  destinations, and absence of DNS/fetch calls. Returning an HTTP/onion link
  must not change subsequent crawler authorization.
- Onyx setup probe: valid SearXNG identity plus exact HTTP 400 JSON `No query`
  succeeds; wrong identity, disabled JSON/403, redirects, unexpected statuses
  (including 200 and other 400 responses), wrong content type, malformed JSON,
  and transport timeouts fail visibly without retry. Assert bounded timeouts
  on both calls and no query-engine execution, browser work, discovery,
  signing, or payment regardless of key presence, provider state, or overrides.
  Verify the pinned SearXNG route rejects empty queries before search dispatch
  and checks disabled JSON before missing-query handling. Ordinary query text
  `test` retains normal search behavior. Periodic health checks never search/pay.
  Test patch installation and its composition with ordinary search retry removal.
- The setup probe must remain independent of the ordinary search lifetime.
  Exercise Onyx tool timeout and caller disconnect while a normal search waits
  for admission: later paid execution remains possible, with only one payment
  in that engine attempt and no caller-layer replay.
- Default suspension for representative unexpected outcomes before/after signed
  dispatch, including a parser `ValueError` and an unexpected exception through
  the real processor; verify unresponsive-engine reporting, metrics, sanitized
  logs, and suspension before reservation release. Cover explicit status
  overrides and bounded Retry-After, plus expected exclusions/admission expiry
  without suspension. No exhaustive malformed-response taxonomy or
  health/discovery polling.
- Wallet path permissions/ownership/symlinks, exclusive creation races,
  interruption cleanup, address/key consistency, no terminal secret output,
  no network/env inheritance, missing-image error, and Docker/Podman selection.
- Derived image hash changes for every embedded feature input; hashed install
  and dependency consistency; no runtime installation or browser downloads.

Run `make check`. Extend `make test-patch-images` to exercise installed engine
registration/patch composition, Onyx's connection-diagnostic behavior, and the
real pinned SDK using synthetic keys and an in-process fake HTTP transport with
container networking disabled.
Supplement in-process mocks with local HTTP/proxy/TLS fixtures for actual
CONNECT routing, certificate rejection, framing, native decompression, slow
responses, and cancellation. These fixtures use synthetic keys and no public
network; loopback fixtures can run in network-disabled validation containers.
Do not equate mock transport evidence with socket/proxy behavior.
The gate validates selected existing images only; build missing images using
the reported Make target first. Do not add live calls to that gate.

Do not run `make test-all-images` for this focused feature. Add
`make test-obscura-image` only if implementation changes Obscura/client runtime
behavior, and `make test-tor-image` only if Tor image/config/mount/control
contracts change. Pure use of the existing Tor final hop does not justify a
Tor image rebuild or an unrelated OpenSearch/security image gate.

## Compose and practical live matrix

Render effective models through Make-selected configuration with synthetic
fixture settings, never by printing an operator-key-bearing Compose model.
Use model rendering to cover feature composition, local socket fixtures for
transport properties, and representative live rows for distinct platform and
lifecycle behavior. Do not repeat the entire payment/parser suite for every
Cartesian-product combination. Record which combinations each evidence row
covers and retain interaction cases such as executor plus Exa and both Tor roles.
Cover lite/full, Exa disabled/enabled, Docker/Podman, executor disabled/enabled
where supported, no-VPN/VPN/configured proxy/Tor, and platform overlays.
Include simultaneous Tor egress/onion ingress as a composition check. Assert
the new route adds no host/Onyx/browser/executor reachability, preserves peer
lists, hides the key from all other service environments, and disappears from
disabled startup. Inspect feature removal and shutdown too. Test unset/empty
`SEARXNG_HOST` yielding only `127.0.0.1`, explicit host overrides, and unchanged
internal gateway connectivity in both modes. Check actual host bindings on
available runtimes. Exercise enable/disable with the matching down/config-change/up
sequence above. Verify the old namespace/services/networks are removed and
the recreated holder, Myst, public/host proxies, browser/executor peers, and
selected VPN/proxy/Tor routes work in the new configuration. Include host-policy
connectivity; do not substitute a public-proxy-only restart for this lifecycle test.

Run practical startup with `make up-lite` and `make up-full`, inspect `make
ps-*` and sanitized targeted logs, and use `make health-inventory` where safe.
Distinguish rendered-model evidence from actual Docker Desktop, native Linux
Docker/rootless Docker, and Podman execution. Run shared-state engine changes
serially using the existing ownership workflow.

For network fixtures, prove the explicit proxy is used for cold-cache unsigned
and signed POSTs and warm-cache signed-only POSTs, and that
bridge/proxy/Tor loss gives no direct DNS or network attempt. Exercise the new
network's host-service isolation to the documented engine-specific standard.
Faults must be confined to disposable/test-owned resources or an explicitly
authorized running stack, with cleanup restoring its prior state.

Add `make integration-x402exa` as the sole paid qualification workflow,
listed in `make help` and the request-handling validation section. It is never
a dependency of startup, wallet generation, `make test`, `make check`, image
gates, or upgrade targets. Use the configured already-running stack; do not
create a separate paid instance, test Compose overlay, or live free-provider
fixtures. Do not start, restart, or reconfigure the stack automatically.

Prove default free-first fallback through the real scheduler and engine with
synthetic payments in the offline image gate. Separately, the live runner
selects `x402exa` explicitly and submits one search with a public synthetic
query, qualifying the configured payment/result path under Exa's documented
settlement-before-results contract and the engine's
receipt schema/success check. The runner validates nonempty results with usable snippets
attributed to `x402exa` in its own response; it does not independently observe
the upstream receipt or on-chain settlement. Do not add a settlement-diagnostics
channel, response-schema extension, correlation store, or ledger for this test.
Do not hammer public free engines until they block or mutate their
production state to force fallback. This combination tests fallback and real
payment separately; it is not evidence of a live default-selection paid fallback.

The runner sends at most one search request per explicit invocation, with no
HTTP retries or automatic restart/replay after failure, ambiguity, or
interruption. That request may authorize at most one payment under the normal
engine contract. The bound covers only runner-issued work, not unrelated or
already-queued searches on the running stack. It is neither a production budget
nor a wallet-wide spending bound. A new explicit invocation is a new run, not
crash recovery. Test single submission and no-retry behavior with fake attempts;
no persistent allowance ledger is needed.

If `.env.wrapper` supplies `SEARXNG_X402_PRIVKEY` (subject to the same supported
environment precedence), execute the paid case automatically for this explicit
invocation. No additional payment confirmation is required. An unset/empty key
skips it with an explicit reason; a malformed key fails validation. Require the
running stack to expose the enabled engine; do not silently enable it or treat
zero engine executions as payment qualification. Pass configuration through
supported Make/Compose mechanisms without printing or shell-sourcing it.
Prefer a dedicated small-funded wallet; do not create, replace, or fund one
automatically.

Report a successful live search as payment-path qualification relying on Exa's
documented contract, not independent proof of a debit. The engine still accepts
an unsolicited unsigned 200 without paying; if Exa supplies such a response,
the runner cannot distinguish it from paid success using ordinary results.
Accept this evidence limitation rather than adding instrumentation. Synthetic
tests separately prove unsigned success never signs and paid success requires
the SDK receipt. Suspension, admission failure, empty results, or an ambiguous
failed search does not qualify the live result path and must not trigger
another attempt. Never print the secret. Tor latency/exit acceptance may be
qualified by the same single attempt when the running stack uses Tor; no extra attempts or
automatic route changes are part of the invocation. Report unavailable rows
and exact reasons; do not silently skip them or call mocked settlement a real
payment test.

## Owning documentation and completion criteria

Update owning documents alongside implementation, describing current behavior
rather than review history. Keep payment codec/admission internals out of user
setup text. Distinguish SearXNG's optional `x402exa` engine from Onyx's separate
Exa search/content provider. Document enablement, wallet funding, round-robin,
automatic uncapped spending, cooldown recovery, wallet/query linkage, payment
that can start after Onyx stops waiting, and authorizations that can settle
after timeout/cooldown or a later top-up. No wrapper lifetime ceiling is promised.
Document requirements reuse, the unspecified provider lifetime, and the accepted
unbounded-until-rejection policy in `docs/request_handling.md`
and the SDK upgrade checklist. Explain the shared-exit cold-cache 429 limitation
in setup guidance. Resource policy must include the single demand-driven,
worker-local cache and its restart loss; no refresh polling or persistence is
introduced. Cached requirements never mean a reused payment authorization.
Setup and routing/lifecycle docs must specify the matching-mode
down/config-change/up requirement for enablement changes; no in-place namespace
transition is supported. Request-handling documentation must scope payment-data
redaction to the Exa path and preserve the accepted existing Onyx debug query logs.

Concrete documentation replacements required by implementation:

| Owner | Replace or add |
| --- | --- |
| `.env.wrapper.example`, README setup | Add the loopback `SEARXNG_HOST` default and explicit-bind spending warning; explain local callers remain unauthenticated. Add wallet setup and name `make integration-x402exa`, its single runner-issued search limit (which excludes unrelated stack traffic), and its exclusion from automatic checks. Explain concrete-language exclusion, page > 1 exclusion, and time-filtered default searches skipping all current browser engines; unsupported Exa-only requests do not pay. Explain that query operators are passed through without equivalent-filtering guarantees. |
| `.env.wrapper.example`, `docker-compose.yaml`, `searxng/core-config/settings.yml` comments | Qualify browser-only engine/transport statements for optional API Exa and describe regular/Bing/Exa selection accurately. Keep browser blocking durations distinct from Exa's failure policy. |
| `README_PATCHES.md` search overview | Add optional x402 Exa support and the non-search admin connection check. Qualify one-hour blocking/rate-limit suspensions as browser-provider behavior, and describe Bing after regular browser providers and Exa after Bing. Link to owning docs for paid-search policy and configuration. |
| `docs/request_handling.md`, README engine descriptions | Replace “Search always uses the pinned Obscura browser” and unconditional free-exhaustion claims with five browser engines plus optional API Exa, selected/capable free-first scheduling, and override/native-dispatch exceptions. Replace search-based admin-probe descriptions with the empty-query JSON check and its limited scope. Document SDK-owned payment semantics, separate payment/result outcomes, default suspension for unexpected outcomes, query-operator limitations, and response-memory residuals. |
| `docs/internal_network_security.md` reachability table; `docs/vpn_routing_and_proxies.md` search route | Replace SearXNG's unconditional browser-only Internet path with the optional dedicated public-policy bridge. Add loopback host publishing, unauthenticated caller spending, Python TLS, and wallet linkage. In `docs/internal_network_security.md`, explicitly document browser-mediated spending: an external page may induce a search through the operator's browser without needing to read the response; loopback binding and CORS are not spending authorization, and browser local-network protections are not a stack guarantee. Document this residual without adding cross-origin route or browser tests; preserve the distinction between application Exa restriction and general public-only egress after compromise. |
| `docs/resource_minimization.md` SearXNG section | Qualify the five-engine set as disabled/default mode; add optional demand-driven Exa admission and bridge health. State one suspension authority and no extra polling, ledger, or permanent event-loop thread. |
| `docs/onyx_patch_info.md` SearXNG/Onyx retry sections | Document ordered in-memory Exa registration, fatal bootstrap checks, once-per-candidate native params, transport-specific admission, function-local scoring, and the non-search connection probe. Preserve ordinary retry removal and native mixed-engine scoring. State canonical settings identity, registration-before-loading, unchanged auxiliary paths, and resource-tracker exclusion; retain rejected-alternative analysis only in this plan. |
| `docs/onyx_patches_upgrade.md` SearXNG/Onyx/routing audits | Replace exact-five-engine assertions with five engines with Exa disabled/six with Exa enabled and change universal offline-browser assumptions to separate browser/API contracts. Remove SearXNG's obsolete Playwright pin/Onyx-version coupling. Record the exact released SDK's codecs, coercions/defaults, offer selection, domain/timestamp signing, receipts, dependency compatibility, and no-RPC/no-repayment contract. Require entrypoint, concurrent-scoring, empty-query route, and feature-transition regression evidence. |
| `docs/podman_suport.md`, `docs/native_tor_support.md` | Add applicable Exa overlay/bridge composition, loopback publishing, and validation rows while preserving platform isolation differences and Tor latency/fail-closed requirements; no unrelated image-gate expansion. |
| `AGENTS.md` command orientation | Add the user-facing wallet workflow and explicitly paid `integration-x402exa` workflow with links to the owning docs; keep payment implementation details in those docs. |

Completion requires all accepted behavior implemented, deterministic checks
passing, focused selected-image evidence recorded, effective models qualified,
practical lifecycle/live rows either completed or explicitly reported blocked,
and owning docs matching implementation. No new hidden retries, spending caps,
direct egress, unrequested wallet mutation, or unrelated patch rewrites.
