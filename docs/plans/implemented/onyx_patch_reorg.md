# Onyx runtime patch organization

Status: package extraction and scoped corrections implemented. Deterministic,
selected-image, host Docker, and Linux VM Podman/rootless Docker startup/recreation
checks pass; authenticated
chat/history, queued synthetic RAG, freshness recrawl, and post-restart
reprocessing/retrieval checks pass. Synthetic API/database/index records and the
temporary admin account are removed, and the initial stopped state is restored.
Live host API PDF retrieval passes for the arXiv and S3 test URLs in both crawler
modes without VPN; controlled API PDF execution also passes. The W3C URL's
direct-browser access denial is separate from PDF parsing. Standing package
ownership and failure contracts are documented in
[patch information](../onyx_patch_info.md#package-organization); validation
requirements are maintained in [the upgrade checklist](../onyx_patches_upgrade.md).

## Objective and scope

Reorganize the Onyx runtime patches around explicit service ownership and
coherent responsibilities, preserving their behavior, configuration,
installation order, strictness, and upstream pins, except for the explicitly
scoped crawler parsing and fixed-proxy configuration corrections, removal of
unused direct-proxy selection, and stronger materialized-schedule validation
below. Keep those corrections separately
reviewable from code moves. Validate that the final
patches execute in the supported application processes, including background workers and
spawned children. A mounted file, successful import, installation message, or
healthy parent container is not sufficient evidence. Preserve existing fatal
handling when a discovered bootstrap encounters a required patch failure.

Missing-bootstrap runtime enforcement is deferred to separately scoped follow-up
work after the reorganization. Tests must detect missing or incorrect activation
in the configurations they exercise; they do not guarantee that a later broken
deployment cannot run unpatched. This refactor adds no launcher, completion
protocol, child-launch patch, mandatory-strict-mode policy, or application command
rewrite. See [Activation validation and deferred enforcement](#activation-validation-and-deferred-enforcement).

The [live validation observer](onyx_patch_validation.md) is a separate follow-up,
not an implementation or acceptance prerequisite. This plan retains controlled
activation/composition tests and representative live checks, with their evidence
limits specified in [Process evidence collection](#process-evidence-collection).

This plan is intended to be implementable in a new conversation. Read the
repository's current `AGENTS.md` first. Use the current working tree and committed
version manifest as authoritative; do not overwrite concurrent user changes.
Do not upgrade images/dependencies or remove allegedly obsolete patches as part
of this refactor. Report any newly discovered behavioral defect separately;
if fixing it is necessary, isolate its change and validation from code moves.
Do not create commits unless requested.

Read these owning documents before implementation:

- [Patch information](../onyx_patch_info.md), especially bootstrap ownership.
- [Patch upgrade checklist](../onyx_patches_upgrade.md), especially runtime
  patch composition, reasoning/tool availability, prompt stability, and live gates.
- [Request handling](../request_handling.md),
  [internal network security](../internal_network_security.md), and
  [routing](../vpn_routing_and_proxies.md).
- [Local RAG](../local_docs_rag_search.md),
  [resource policy](../resource_minimization.md), and
  [Podman support](../podman_suport.md).
- [Native Tor](../native_tor_support.md) for affected routing/startup matrices;
  preserve its allowances even when testing only the configured route.

The implementation does not move independent services into the patch package:
the embedding shim, document server, host embedding lifecycle, shared Obscura
client, background entrypoint/watchdog, nginx companion, and SearXNG patches
retain their existing ownership and process-launch behavior. The background
entrypoint/watchdog remain lightweight control programs. The code-interpreter
service continues to use native settings without a runtime networking shim.

## Inventory and migration accounting

The starting inventory is 11 Python files and approximately 8,025 lines under
`onyx/patches/`. Refresh these counts and the symbol inventory before editing;
counts are orientation, not acceptance assertions.

| Area | Current shape | Decision |
| --- | --- | --- |
| `shared/wrapper_env_patches.py` | 5,260 lines; 24 `apply_*` installers | Split by ownership/responsibility; remove the monolith at completion |
| `sitecustomize_background/sitecustomize.py` | 1,292 lines of bootstrap and implementation | Extract implementation; retain a small bootstrap |
| `sitecustomize_api_server/sitecustomize.py` | 114-line explicit bootstrap | Preserve its explicit composition pattern |
| Eight dedicated API patch modules | 1,359 lines combined | Preserve responsibility boundaries; move into the explicit API package |

Before moving code, build a migration ledger in this plan: old symbol/module,
new owner/module, installing services, upstream targets, ordered dependencies,
configuration guards, and associated behavioral contracts. Include constants,
mutable state, private helpers, and validators, not just installer functions.
For each installer, record where import, validation, and application failures are
caught, their strict/non-strict outcomes, and which subsequent installers execute.
Distinguish preserved behavior from the explicit configuration corrections below;
non-strict mode does not universally continue the installation sequence.
Every old installer must have one accounted-for destination; an intentionally
conditional installer is not a missing installer.
Group purely local helpers/constants with their owning installer or behavior;
give cross-module dependencies and mutable state explicit ownership. This is
migration accounting, not a requirement for a separate acceptance row per symbol.

Maintain a separate contract-to-test map, referenced by the symbol ledger. Assign
each contract one primary test layer and reuse existing deterministic tests,
selected-image composition tests, and representative live probes where each adds
distinct evidence. Multiple symbols may reference the same contract and test.
Pure helpers and constants do not need individual image or live probes. Live
coverage establishes actual process activation and representative affected
behavior; it is not a per-symbol acceptance requirement.

Record explicit dependency edges before choosing final module boundaries:
reasoning preservation reads the Deep Research sharing flag; Deep Research,
continuation, and coding recovery consume reasoning helpers; artifact prompts
use exact-count prompt replacement; and the final prompt validator checks Deep
Research output limits. Keep the sharing flag in an API-owned configuration
module with no installer imports, and reasoning data/trace helpers below their
consumers with no dependency back on those installers. Expose the report-limit
validator from its behavior owner. Move exact-count string replacement to a
neutral source utility. Preserve import-time versus installation-time setting
evaluation and injected execution-global bindings; do not copy mutable state
or introduce a configuration registry to break a cycle.

Only three installers in the shared monolith are currently installed by both
API and background: `apply_embedding_tokenizer_alias_patch`,
`apply_configured_inference_proxy_patch`, and
`apply_playwright_helper_proxy_patch`. The remaining 21 belong to the API.
Some small utilities also have actual cross-service consumers, notably fixed
proxy validation and Playwright proxy context selection.

## Standing organization rules

The maintained rules now live in
[Package organization](../onyx_patch_info.md#package-organization), with the
repository-wide pointer in `AGENTS.md` and repeatable checks in the
[upgrade checklist](../onyx_patches_upgrade.md#runtime-patch-contract-audit).
They cover service ownership, inert canonical imports, explicit installer
ordering, dependency direction, source composition limits, preserved failure
boundaries, and automatic discovery without new runtime enforcement.
The sections below retain migration-specific decisions and acceptance evidence.

### Existing reasoning alias repair

Keep `_update_bound_module_attr` private to `api/reasoning.py`, with its existing
reasoning-detection and optional tracing callers. Preserve its snapshot of loaded
modules, attribute-name plus object-identity matching, and existing exception
handling during extraction. Do not promote it to `common/`, add consumers, or
change installation order merely to eliminate it. It repairs already-bound module
attributes; it is not activation enforcement and cannot repair defaults, closures,
decorators, or registries. Later imports receive the replaced owner attribute.

This is an explicit exception to the rule for new patches, not a new strictness
guarantee for the scan. The current model-limit installer imports
`factory -> multi_llm` before reasoning detection runs, so removing the scan can
leave a real consumer bound to the original detector. Record the existing scan
call sites in the ledger. Test identity-matched replacement, unrelated references
remaining unchanged, tolerated inspection failures, later imports, and actual
reasoning behavior after full bootstrap, including enabled tracing fixtures.
Re-audit known consumers in `multi_llm`, coding/research agents, and model-management
modules against the pinned source. An allowlist or installation-order redesign is
separate work only if this audit demonstrates a defect; it is not an extraction
prerequisite. Document this bounded exception in the canonical organization rules.

## Target layout and responsibility map

Use a uniquely named package under the existing patch root:

```text
onyx/patches/
  sitecustomize_api_server/sitecustomize.py
  sitecustomize_background/sitecustomize.py
  onyx_wrapper_patches/
    __init__.py
    common/       # strict source/contract/config utilities; no installers
    shared/       # actual cross-service patch implementations
    api/          # API-owned patch implementations
    background/   # background-owned patch implementations
```

Use inert initializers in all package directories. Keep the two existing
bootstrap directory names to avoid mixing a bootstrap rename into this work.
Within the package, the following destination groups cover all 24 current
shared installers. Exact module names may be refined when dependency analysis
shows a tighter coherent boundary; record the final mapping in the ledger.

| Destination | Current installers (`apply_` prefix omitted) |
| --- | --- |
| `shared/embedding_tokenizer.py` | `embedding_tokenizer_alias_patch` |
| `shared/inference_proxy.py` | `configured_inference_proxy_patch` |
| `shared/playwright_proxy.py` | `playwright_helper_proxy_patch`; owns proxy-selection context |
| `api/reasoning.py` | `native_reasoning_detection_override_patch`, `reasoning_content_preservation_patch`, `reasoning_mode_trace_patch` |
| `api/tool_calls.py` | `native_tool_calls_only_patch`, `vllm_glm_auto_tool_choice_patch` |
| `api/inference_continuation.py` | `midstream_inference_continuation_patch` |
| `api/model_limits.py` | `llm_max_tokens_override_patch` |
| `api/deep_research.py` | `deep_research_chat_agent_tools_patch`, `deep_research_output_limit_patch` |
| `api/coding_final_answer.py` | `coding_agent_final_answer_fallback_patch` |
| `api/prompt_stability.py` | `agent_prompt_stability_patches` and its final validator |
| `api/python_artifacts.py` | `python_file_link_enforcement_patches`, `chat_file_id_validation_patch`, `python_file_link_prompt_patches` |
| `api/python_capabilities.py` | `python_package_capability_patches`, `code_interpreter_network_description_patches` |
| `api/retrieval_limits.py` | `internal_search_context_patches`, `open_url_char_limit_patches` |
| `api/tool_result_history.py` | `preserve_tool_results_patch` |
| `api/mcp_egress.py` | `mcp_egress_proxy_patch` |
| `api/searxng_retry.py` | `searxng_single_attempt_patch` |

Move the eight dedicated API modules into `api/` with their current descriptive
names except for the scoped `open_url` orchestration consolidation below.
Preserve the mutually exclusive stock/direct crawler selection and
the failure-reporting/URL-count wrapper composition. Move `use_obscura_browser`
from the stock crawler into the lightweight API configuration module, then
import only the selected crawler implementation at its installation boundary.
Record this deliberate import-order change and prove both final compositions;
preserve the selector's evaluation timing and accepted values. The background
destinations are `resource_policy.py`, `web_connector_egress.py`, and
`document_freshness.py`.
Keep PDF freshness metadata, display-link rewriting, and indexing sentinel
handling together unless a demonstrated dependency boundary justifies splitting
them. Native content-hash skipping and secondary-index bypass must survive.

Do not move all private helpers into `common/`. For example, reasoning traces
belong with reasoning; artifact Markdown state belongs with artifacts; a
ContextVar that selects the Playwright proxy belongs with that shared behavior.
Inspect injected globals and captured references before moving any helper.

## Mounts, imports, and activation design

The package must be importable in every production interpreter independently
of inherited shell environment and current working directory. Mount
`onyx/patches/onyx_wrapper_patches` read-only at `/app/onyx_wrapper_patches`.
Explicitly include `/app` on the API Python path alongside its bootstrap and
Obscura client directories. Set the background container Python path to `/app`,
matching supervisor's worker setting. Retain only its single read-only
`/app/sitecustomize.py` bootstrap mount; remove the duplicate
`/app/wrapper-patches-background` directory mount after extraction. Both services
import the same canonical package. Preserve the repository bootstrap directory
names; do not inherit host `PYTHONPATH` to supply required application paths.

Confirm this design against the actual pinned commands and Python startup
behavior before adopting it. Audit base/lite/full overlays, engine overrides,
generated supervisor configuration, validation-container mounts, and test
fixtures. Do not patch generated upstream deployment files by hand. Remove
`/app/wrapper_env_patches.py`, old shared-directory mounts, and old imports only
after all consumers have moved. Do not leave stale mounts that can hide an
incomplete refactor by supplying old code.

The application bootstrap must complete before dependent Onyx modules bind
callables/constants. Preserve current import ordering unless final-behavior
tests prove an intentional ordering change. Check late imports as well as
already imported aliases. Do not repair a failing probe by explicitly invoking
an installer that production failed to invoke.

### Activation validation and deferred enforcement

Preserve native application commands, supervisor derivation, multiprocessing
spawn, and isolated-runner dispatch. Record the pinned launch paths needed to
validate this migration: API Alembic/Uvicorn, Beat, the six retained thread-based
Celery workers, spawn-based indexing/document-fetching, isolated PDF extraction,
and enabled bots. Classify existing control programs separately. This bounded
inventory guides tests; it does not introduce a guarantee for every dependency
subprocess or arbitrary operator invocation.

Use automatic Python startup with production mounts and paths in selected-image
tests, plus scoped live evidence from the actual application processes. Inspect
preexisting bootstrap/module state before probe imports can mask missing discovery.
The test harness must fail when the bootstrap is absent, undiscoverable, or loaded
from an unexpected origin. A harness failure in these cases is not evidence that
the unguarded application would itself refuse to start. Preserve and test the
existing exit-78 behavior when a discovered strict bootstrap cannot import a
required implementation or encounters a source-contract mismatch.

Preserve native isolated-process recovery. The pinned `run_in_isolated_process`
discards child stderr and translates a nonzero exit into `IsolatedProcessError`;
PDF extraction may then use pypdf in the parent. Positive PDF activation evidence
must show PDFium execution in the child, not merely successful parent fallback.
Keep exception mapping, stderr transport, descriptors, pickle transport, and
native timeouts unchanged. Run positive selected-image PDF child cases separately
under the API and background bootstrap environments, through each service's real
PDF caller and native isolated runner. Assert automatic bootstrap/package origins,
successful PDFium execution, and the returned pickle result in both environments;
background ingestion cannot qualify the API's different path and installer set.
Record representative cold and warm PDF child elapsed times for each environment
before and after extraction against the existing timeout. Do not compensate
for overhead by extending timeouts or adding retries; report a regression if a
previously passing fixture no longer completes within its native allowance.

A future enforcement proposal may evaluate an independent startup guard and
narrow child-launch adapters if the missing-bootstrap guarantee is needed. Scope
that work after the package organization is established. It must define supported
launch boundaries and exclusions, prove dispatch/argv/main-module/signal semantics,
and assess startup cost and native child recovery. No launcher, CPython launch
patch, completion record, production command preflight, or enforcement-specific
negative matrix is a prerequisite or deliverable of this reorganization. Existing
activation probes should be reusable by that follow-up.

## Scoped simplifications

Limit these changes to small extractions or deletions with existing or focused
characterization coverage. If a candidate needs a policy framework, new fixture
platform, broad installer rewrite, or changed caller semantics beyond the explicit
configuration and schedule-validation corrections below, leave it unchanged and
report it as deferred. Module consolidation is optional where it adds little
value; preserve coherent ownership rather than minimizing file count.

- Use the single background bootstrap mount and explicit paths above.
- Remove lite's duplicate API `WRAPPER_PATCH_STRICT` and `PYTHONPATH` environment
  overrides and inherit the base values. Also remove the duplicate API
  `WRAPPER_PATCH_STRICT` entries in full mode and the executor-network overlay.
  Retain lite's deliberate volume override, which excludes full-mode model-cache
  mounts, and update its required patch
  mounts explicitly. Verify the effective models rather than deleting the whole
  override to reduce duplication.
- Remove the API's unused `ONYX_WEB_CONNECTOR_PUBLIC_HTTP_PROXY_URL` and
  `ONYX_WEB_CONNECTOR_HOST_HTTP_PROXY_URL` entries from `docker-compose.yaml`,
  and background's unused `ONYX_MCP_PUBLIC_HTTP_PROXY_URL` and
  `ONYX_MCP_HOST_HTTP_PROXY_URL` entries from the full overlay. Confirm the
  service installer lists and selected upstream code have no readers before
  deletion. Retain each setting in its installing service; assert both presence
  and absence in the affected effective models and startup compositions. This
  removes configuration with no consumer, not a proxy or destination-policy layer.
- Remove the controller container's unused `ONYX_CODE_INTERPRETER_ENABLE_NETWORK`
  environment entry from the executor-network overlay after confirming the selected
  controller image has no reader. Retain the Make selection setting and API copy;
  native `PYTHON_EXECUTOR_DOCKER_NETWORK` and `PYTHON_EXECUTOR_DOCKER_RUN_ARGS`
  remain authoritative for controller behavior. Validate enabled/disabled effective
  models and the existing native executor image contract; do not add a shim.
- Simplify background scheduling to validate and transform the materialized
  `tasks_to_schedule` only. The pinned self-hosted module deep-copies templates
  during import; subsequent self-hosted schedule generation reads the materialized
  list. Remove duplicate template mutations/checks after testing that consumption
  path against the selected image. Retain the strict `MULTI_TENANT=false` guard,
  existing source-contract checks, worker filtering, reload interval, and liveness
  behavior. Consolidate overlapping removed-name and forbidden-conditional-name
  assertions into one exact final materialized-schedule assertion. Reject duplicate
  names before constructing a mapping, then check every retained name, task
  identifier, and cadence. Keep the independent prohibition on monitoring-queue
  destinations. Today's discovery checks validate names/cadences but not task
  identifiers, and the final dictionary can conceal duplicate housekeeping names;
  those stronger checks are an explicit validation correction, not an existing
  guarantee. Cover initial schedule generation and subsequent reloads, including
  the installed `DynamicTenantScheduler.schedule`, not only generated dictionaries.
  The pinned scheduler compares existing and generated schedule names only when
  the multiplier is unchanged; same-name entries can retain stale task identifiers,
  cadences, or queue options. Characterize this native limitation and require the
  validation harness to detect it. Do not change scheduler comparison/persistence
  behavior or clear live scheduler state as part of this extraction. If stale
  installed entries block live acceptance, report that separately rather than
  treating correct generated output as a pass. Isolate this cleanup and validation
  correction from code moves and update `docs/resource_minimization.md`,
  `docs/onyx_patch_info.md`, and the upgrade checklist where they describe template
  mutation or its validation. Resolve the
  existing `docs/onyx_patch_info.md` claim that the patch "removes their one-minute
  templates": the current patch rewrites discovery-template cadences, and the
  pinned incognito-cleanup template already runs every ten minutes. Replace that
  explanation with the final materialized-only behavior and retained housekeeping
  cadence; do not carry the obsolete rationale forward. Do not generalize the
  patch to support cloud scheduling.
- Consolidate URL-count enforcement and mixed-result failure reporting into one
  API-owned `open_url` orchestration module, retaining separately named explicit
  installers and their order. Retain the class-associated
  `_wrapper_failure_reporting_original_run` attribute alongside the existing
  installation markers; consolidation removes the cross-module handoff without
  requiring a new state mechanism. Do not replace it with a module-level singleton.
  Change its storage only for a demonstrated benefit with separate characterization.
  Test repeated installation on the same class and installation against distinct
  replacement classes so original callables and markers cannot become mismatched.
  Preserve ContextVar identity, direct crawler failure recording,
  final wrapper composition, and install idempotence. Cover both crawler choices,
  over-limit rejection before retrieval, and partial/all-success/all-failure
  results. Do not combine unrelated retrieval limits solely to reduce file count.
- Extract the crawler selector into API configuration as described above, removing
  the direct mode's unnecessary dependency on the stock implementation.
- Move the equivalent stock/direct crawler document-byte-limit parsers into one
  API-owned utility. Keep each caller's current evaluation timing, accepted
  values, bounds, and failure behavior; do not eagerly import the unselected
  crawler implementation.
- Make shared configuration mean the same thing for stock and direct
  Obscura-backed crawling. In particular, share the stock semantics for
  `EGRESS_ALLOW_HTTP_URLS`: strip whitespace, ignore case, accept
  `1/true/yes/on` and `0/false/no/off`, default to false, and reject other values.
  The direct crawler currently does not strip whitespace and silently treats
  invalid values as false; intentionally remove that discrepancy without a
  compatibility mode. Share the equivalent strict `EGRESS_ALLOW_HTTP_ONION_URLS`
  parser too (`true`/`false` only after stripping and case normalization).
  Use small pure API configuration helpers, retaining each caller's evaluation
  timing and avoiding imports of the unselected transport. Characterize both
  callers with the same valid/invalid input table and selected-image compositions.
  Keep this correction separately reviewable from code moves, and update
  `docs/request_handling.md` and affected configuration guidance with the common
  contract. Do not broaden accepted values in unrelated final-hop components;
  preserve their independent destination policy.
- Unify fixed-proxy value validation around stripped exact equality with the
  expected canonical URL, with no redundant structural parsing. Use one small
  pure equality utility for `_validated_fixed_proxy_url` callers, the stock
  crawler, GitHub, and the shared Playwright helper; keep failure handling at
  each caller's existing boundary. All three consumers of
  `ONYX_HELPER_HTTP_PROXY_URL` must accept the same nonempty canonical value.
  GitHub intentionally gains outer-whitespace normalization; Playwright rejects
  trailing slashes and equivalent scheme/host/port spellings. Replace Playwright's
  empty-value installer skip with its existing invalid-value warn-or-raise path:
  strict startup fails, while non-strict mode reports the invalid value and
  returns without installing the helper. These are explicit configuration
  corrections. The setting is a stack-owned Compose constant, so preserving
  divergent spellings or an unused empty-setting escape adds no deployment value.
  The [caller acceptance table](#fixed-proxy-acceptance-contracts) is authoritative
  for the scope of these corrections. Do not add compatibility modes or a
  configurable validator framework.
  Characterize the complete validator-to-selector-to-connector path, not just
  parser return values. A known pre-existing mismatch is that
  `_validated_fixed_proxy_url` accepts noncanonical representations and returns
  them unchanged, while `select_playwright_proxy` accepts only canonical fixed
  URLs. Reproduced examples are `http://onyx-public-egress-bridge:3128/`,
  `HTTP://ONYX-PUBLIC-EGRESS-BRIDGE:3128`, and
  `http://onyx-public-egress-bridge:03128`. The background
  Web Connector therefore accepts that configuration during installation but fails
  when a nonempty crawl enters the selection context, before browser fallback.
  Record this defect in the ledger. Exercise the acceptance table through
  installation and connector selection, including non-strict exception handling.
  Keep this accepted-value correction separately reviewable from extraction, and
  update owning configuration/routing documentation and tests together.
- Remove the unused empty-string direct mode from `select_playwright_proxy`.
  Its only current production caller is the background Web Connector, whose
  selector returns a canonical public or host bridge even for doc-drop. Recheck
  all callers before deletion. Reject empty selections before changing context
  state; retain `None` for the default helper proxy. Remove the corresponding
  unproxied Playwright launch branch and the background requests wrapper's
  unreachable empty-proxy-dictionary branch. Keep the background requests
  context's `None` meaning (an unrelated request outside connector routing)
  unchanged. Test both bridges, doc-drop host selection, nested overrides,
  restoration after exceptions, concurrent context isolation, and empty-selection
  rejection without state mutation. Keep this deletion separately reviewable
  from extraction and do not change the exact internal Teep exception or any
  independent route policy. Update the canonical patch and routing documentation
  with the remaining default/public/host selection contract when implemented.
- Keep small related installers together where their dependencies justify it.
  Remove redundant forwarding wrappers only after characterizing each exception
  boundary. A wrapper may catch import failures as well as installer failures;
  deleting it or moving its import outside the protected block can change which
  later installers run in non-strict mode. Verify strict propagation to fatal
  bootstrap handling, import-time failures, and stderr-only diagnostics. For
  non-strict cases, assert the ledger's exact subsequent installer sequence:
  locally handled failures may continue, while failures escaping to the outer
  bootstrap stop the remaining sequence without a strict exit. Do not turn the
  latter into continuation as part of extraction. Preserve meaningful error
  context without requiring duplicate log lines. Do not replace per-installer
  handling with one broad catch that silently skips the remainder of installation.
  Retain a wrapper if deleting it changes these semantics or requires a new error
  framework. Deferred strict-mode/enforcement work cannot justify changing today's
  non-strict exception boundaries. Keep bootstrap-local fatal
  handling independent of shared utilities so missing utilities remain fatal under
  existing strict settings.

### Fixed-proxy acceptance contracts

Here, public and host canonical URLs mean
`http://onyx-public-egress-bridge:3128` and
`http://onyx-host-egress-bridge:3128`, respectively. These settings are stack-owned
Compose constants; this change adds no user-facing proxy controls to
`.env.wrapper.example`.

| Caller / setting | Final acceptance and failure contract |
| --- | --- |
| MCP: `ONYX_MCP_PUBLIC_HTTP_PROXY_URL`, `ONYX_MCP_HOST_HTTP_PROXY_URL` | Strip outer whitespace, require the corresponding canonical URL by exact equality, and raise on empty or noncanonical values at the existing validation boundary |
| Configured inference: `ONYX_CONFIGURED_INFERENCE_HTTP_PROXY_URL` | Strip outer whitespace, require the canonical host URL by exact equality, and preserve the existing validation exception boundary |
| Background Web Connector: `ONYX_WEB_CONNECTOR_PUBLIC_HTTP_PROXY_URL`, `ONYX_WEB_CONNECTOR_HOST_HTTP_PROXY_URL` | Strip outer whitespace, require the corresponding canonical URL by exact equality during installation; a value accepted here must also pass Playwright context selection |
| Stock crawler and GitHub: `ONYX_HELPER_HTTP_PROXY_URL` | Strip outer whitespace and require exact equality with the canonical public URL; reject missing, empty, or noncanonical values at each caller's existing exception boundary |
| Standalone shared Playwright helper: `ONYX_HELPER_HTTP_PROXY_URL` | Apply the same stripped exact equality rule; missing, empty, or noncanonical values use the existing invalid-value warn-or-raise path (strict failure; non-strict diagnostic and return without installation) |
| `select_playwright_proxy` context API | Accept only exact canonical public/host URLs and `None` for the helper's default proxy; reject empty strings and other values before setting context state; do not normalize context selections |

Test standalone validators and complete service compositions separately, using
one value-acceptance table for the helper setting. Include missing values,
whitespace, trailing slashes, scheme/host case, credentials, zero-padded and invalid
ports, and empty values. Keep caller-specific assertions for exception propagation
and the ledger's non-strict stop/continue behavior. The equality utility must not
add a catch or move validation across an existing catch. Playwright's newly rejected
values must follow its invalid-value path; this explicitly replaces its former
empty-value skip without introducing a new exception-handling framework.

Do not collapse controls with different responsibilities: PDF freshness avoids
download/parsing while native hashes avoid later indexing; supervisor filtering
removes consumers while schedule filtering removes producers; application URL
checks and final-hop policy enforce different boundaries. Native Web Connector
`url_rewrites` changes stored IDs and cannot replace display-only link rewriting.
Keep source composition and final behavioral validation; neither substitutes
for the other. No wholesale patch removal or unrelated component redesign is
included without a demonstrated equivalent native contract and separate scope.

## Implementation sequence

Develop with the stack down; minimizing downtime is not a requirement on this
development machine. Stop it again after any baseline or intermediate live run
before moving bind-mounted code. Recreate affected containers after mount changes
before collecting new live evidence; no rolling migration machinery is needed.

1. **Baseline and activation harness.** Record the current tree, pins, mounts,
   ordered installer lists, exclusions, symbol ownership, and contract-to-test map.
   Discover supported engines and selected local images. Run `make check` and
   `make test-patch-images CONTAINER_BIN=<selected-engine>` with an available
   engine; report unavailable engine/image rows and Docker-only checks separately.
   Do not substitute artifacts or pull implicitly. Add automatic-startup probes
   before moving code. Characterize the source helper's supported compositions,
   including observable wrapper effects, and audit actual production callers.
   Record the missing-bootstrap runtime limitation without making its repair an
   extraction prerequisite. Isolate any necessary composition defect correction.
   Use the [process-evidence table](#process-evidence-collection) to distinguish
   controlled activation tests from live evidence available through native logs,
   task status, and fixture counters. Record gaps for the separate observer plan.
   Keep reusable tests under `tests/` and consolidated results here. Identify the
   supported primary-RAG fixture recipe and authorization or infrastructure
   blockers early; new observer or fixture-platform implementation is not a
   prerequisite for code moves.
2. **Background extraction.** Introduce the inert package and canonical mount;
   thin the background bootstrap without changing algorithms or launch commands.
   Verify supervisor-reset paths and spawned children. Shared installers may remain
   in their existing module temporarily; do not add compatibility forwarding layers.
   Remove the duplicate background bootstrap mount, update affected documentation,
   and run deterministic, composed-image, and affected worker/PDF activation checks.
   Apply the materialized-schedule cleanup as a separately reviewable change with
   its scheduler-generation/reload tests, rather than hiding it in the extraction.
3. **Shared and API extraction.** Separate neutral utilities and the three shared
   installers. Move the 21 API-owned installers, state, helpers, and eight existing
   API modules into the package. Keep ordered installation and final validators.
   Update tests and mounts together; apply scoped simplifications with
   characterization coverage. Remove the monolith and obsolete module/mount paths.
   Update owning documentation and test-state isolation with the code.
4. **Final qualification.** Complete standing guidance in `AGENTS.md` and the
   canonical patch documents. Run applicable validation below, reconcile the
   ledger and contract-to-test map, and record consolidated evidence and blockers.

Each phase updates the current paths/contracts in `docs/onyx_patch_info.md` and
`docs/onyx_patches_upgrade.md`; background moves also update
`docs/local_docs_rag_search.md` and `docs/resource_minimization.md` where paths or
contracts change. `docs/onyx_patch_info.md` owns package organization, supported
source compositions, bootstrap discovery, existing strict failure behavior, and
its missing-bootstrap limitation. It also owns native child-failure recovery and
troubleshooting, including discarded isolated-child stderr.

When moving the tool-choice installer, correct its stale coding-agent-only
docstring to describe its existing four caller contexts: coding, Deep Research,
nested research, and explicit chat tool forcing. This changes its description,
not its applicability or behavior.

Correct the overbroad PDF download-skip claims in `docs/local_docs_rag_search.md`
and `docs/onyx_patch_info.md`: freshness skips the scrape-stage PDF GET and parse,
but native connector connectivity GETs can still download the first URL's body.
Document that existing limitation without changing connectivity checks or eager
browser initialization; their simplification is outside this plan. Update the
RAG upgrade checks to distinguish total fixture traffic from scrape-stage work.

`docs/onyx_patches_upgrade.md` owns repeatable activation/composition checks.
Neither document should describe deferred runtime enforcement as implemented.
Describe executor network selection as native controller configuration, not a
runtime networking patch; retain the API prompt-capability patch's separate role.
Link to those contracts from resource, RAG, security, request-handling, and Podman
documentation only where existing guidance is affected; keep platform-specific
consequences in `docs/podman_suport.md`. Do not duplicate the full contract or
update unaffected documents merely to record validation. Add the concise AGENTS
guidance when the organization is introduced. Do not describe planned behavior as already
implemented or defer obsolete path corrections to the final phase. README changes
are unnecessary unless an actual user-visible setup instruction changes; this
refactor adds no new startup guarantee.

Do not retain docs describing superseded layouts or old-version compatibility.
Frozen records under `docs/plans/implemented/` and read-only `reference_repos/`
are not migration targets. Classify search hits in those directories rather than
editing them to satisfy a blanket zero-match assertion.

## Required deterministic and image validation

Run `make check` for each completed extraction phase and on the final tree.
Keep existing behavior/drift tests; update their imports rather than replacing
them with tests of module existence. Add or strengthen these checks:

- Import every implementation package/module without applying patches or
  requiring credentials, the private environment file, network access, or heavy
  service startup. Verify dependency direction and canonical module identity.
- Check exact service installer membership, feature-disabled branches, crawler
  alternatives, and dependency ordering. Preserve strict error propagation and
  final-composition validation. Avoid freezing incidental formatting/prose.
  Verify selected-crawler imports leave the unselected implementation unloaded
  in fresh compositions.
- Exercise the unified fixed-proxy acceptance table through standalone callers
  and complete service bootstraps, including Playwright's empty-setting correction
  and rejection of direct context selection. For representative import, validation,
  and application failures at each distinct exception boundary, assert the
  ledger's strict/non-strict outcome and exact subsequent installer sequence.
  Cover both local continuation and termination of the remaining bootstrap work;
  do not infer one from the other.
- Exercise successive source rewrites, source-level decorators, and behavioral
  wrappers as distinct cases. Use observable wrapper effects, not only transparent
  forwarding decorators, and test retained globals and repeated-decoration effects.
  Verify the audited supported ordering preserves all intended behavior; unsupported
  compositions must fail explicitly where enforced. The existing helper's wrapper
  loss must not be mistaken for a passing composition contract. Cover real final
  reasoning, prompt, research, history, artifact, and crawler compositions. Keep any
  necessary behavior correction separately reviewable from moves.
- Test import-order hazards: targets imported before installation where
  supported, targets imported afterward, existing bound aliases, and repeated
  installation semantics where currently supported. Never silently accept an
  unsupported order; validate/fail clearly rather than adding a fallback.
- Preserve background scheduling, PDF metadata/sentinels, secondary indexing,
  proxy ContextVar isolation, and once-only diagnostic state. Verify state is
  not duplicated through differently named imports.
- For the schedule simplification, exercise the pinned self-hosted scheduler's
  generation/reload path with controlled tenant inputs. Prove it reads the patched
  materialized list without regenerating from untouched templates. Assert the
  multi-tenant guard still rejects the unsupported cloud path. Check retained
  task identifiers, names, cadences, and queue options in the materialized list,
  generated schedule, and effective `scheduler.schedule` after initial installation
  and reload. Use native scheduler entry types when checking effective cadences.
  In a disposable controlled scheduler fixture, seed existing entries with correct
  names but stale task identifiers, cadences, or queues and an unchanged multiplier.
  Demonstrate that native reload retains them and that the installed-schedule
  assertion rejects them even though generation is correct. This characterizes a
  pre-existing limitation; it must not require a production scheduler repair or
  mutate the user's scheduler store. Also cover native installation/reload from
  an empty or changed-name fixture, including removal of obsolete producers.
  Add deterministic failures for duplicate names, an unexpected or removed name,
  a wrong retained task identifier/cadence, and a monitoring-queue destination.
  These cases establish the stronger materialized contract without duplicating
  template validation or separate overlapping name-membership checks.
- Replace fresh-file-module test loaders deliberately: canonical imports cache
  settings and state. Use fresh subprocesses for import-time setting variants;
  restore explicitly mutated state in ordinary unit fixtures. Exercise enabled
  then disabled cases and the reverse order, checking once-only diagnostics,
  prompt-validation collections, injected globals, and ContextVar identity.
  Do not reload only one module while its consumers retain old references.
- Render Make-selected Docker and Podman lite/full models, including native
  Linux rootful/rootless and macOS corrections, plus affected optional route
  layers. Assert the package and bootstrap mounts, read-only flags, Python paths,
  exact service ownership, and absence of obsolete mounts. Do not assume the
  host's inherited `PYTHONPATH` or working directory makes a broken model work.
  Use the existing public model fixtures for cross-platform rendering; report
  model checks separately from runtime checks on the actual engine/OS. Rendering
  another platform's model does not qualify execution on that platform.

Run `make test-patch-images CONTAINER_BIN=<selected-engine>` against the selected
local images after extraction and on the final tree. Extend its existing production
bootstrap coverage and `tests/validate_pinned_api.py`,
`tests/validate_pinned_background.py`, `tests/validate_prompt_stability.py`, and
`tests/validate_reasoning_tool_availability.py` rather than creating a competing
bootstrap recipe. Keep no-network image validation and no implicit image pulls.
Use the harness's existing Podman support and Docker-only executor exclusions;
an unavailable Docker engine does not block the supported Podman image checks.

Use separate fresh interpreters for complete stock and direct API compositions
and for background freshness enabled and disabled. Supply an offline tokenizer
fixture where needed. Retain disabled Deep Research sharing coverage. The current
background harness manually loads its bootstrap with freshness initially off;
replace that activation recipe with automatic startup and inspect preexisting
`sys.modules` state before any bootstrap imports. Apply the same correction to
the API validator's explicit `import sitecustomize`. Manual installer tests may
remain as unit contracts but cannot satisfy these activation cases.

Add subprocess-based startup tests beyond the current source-text checks in
`tests/test_sitecustomize_stdout.py`:

- Start ordinary Python with production mounts/path settings and automatic
  `sitecustomize` discovery. The probe must not import the bootstrap or call
  installers; assert preexisting module origins and final patched behavior.
- Repeat with worker `PYTHONPATH=/app`, unrelated working directories, and a
  sanitized host environment. Exercise native spawn, not fork-inherited state.
- In disposable containers, omit the bootstrap mount, remove its discovery path,
  and shadow it with an inert wrong-origin module. Require the validation probe
  to reject these configurations; do not require application exit 78 for an absent
  bootstrap. Separately keep the bootstrap discoverable, omit a required package
  or implementation, and inject source drift. Under existing strict settings,
  require bootstrap exit 78 before a sentinel application body executes. Exercise
  representative parent and fresh-child cases, preserving native parent recovery.
  Retain existing non-strict helper tests; do not introduce mandatory strict mode.
- Verify stderr-only startup diagnostics and a real PDFium child pickle result
  separately under API and background bootstraps, including cold/warm timings as
  specified in [Activation validation](#activation-validation-and-deferred-enforcement).
  Parent fallback cannot satisfy the positive case. Capture fatal bootstrap stderr
  directly in a disposable interpreter and verify the native isolated caller still
  discards child stderr. Source-text assertions alone are insufficient.
- Verify exact control exclusions: entrypoint/watchdog `python -S` and supervisor
  remain lightweight; Beat, workers, and spawn children retain their patches.
  Use controlled installed-entrypoint tests for both optional bots without needing
  real credentials; add live activation evidence for bots already enabled.

Use the existing image harness for these probes. No runtime completion protocol,
public diagnostics endpoint, generic patch registry, heavy health probe, or
periodic validation daemon is required. Assert behavior as well as module identity.

No broad `make upgrade` is needed. Run additional component image gates only if
implementation expands into those components. Merely relocating an Onyx import
does not justify rebuilding unrelated images or changing their pins.

## Required live activation and scoped behavior validation

Discover operational Docker/Podman engines and selected local images at execution
time. Use Makefile lifecycle commands with an explicit `CONTAINER_BIN`, render
both engine models, and run the applicable live rows on available supported
engines; mark unavailable engine rows unrun with the observed reason. Native
Linux Podman and rootless Docker run in the supplied VM; verify its engines,
selected images, and tree under test. Docker-only executor checks remain
Docker-only. The consolidated evidence below distinguishes this VM coverage
from host Docker and rendered-model checks.

Run engines serially when they share stack data or host services, using the
documented stopped-stack handoff. A second operational engine does not establish
fixture isolation; rootless Docker's separate database volumes also do not imply
shared database continuity. Report host and VM runtime coverage separately from
rendered-model coverage.

Use authorization actually present in the implementing session; this plan does
not grant access to private configuration or document directories. Pass runtime
configuration through supported Make/Compose mechanisms without reading or
sourcing `.env.wrapper`. Use synthetic/public inputs and disposable fixture
storage. Only if the necessary real connector path cannot use fixture storage
should implementation seek narrow authorization for a named synthetic file in
the configured document directory, including its removal. Do not enumerate the
user's document collection or delete existing indices, snapshots, or daemon state.

### Process evidence collection

Use the existing image validators for automatic bootstrap/module-origin checks in
fresh interpreters with production-equivalent mounts and paths, including native
spawn and isolated PDF children under both API and background bootstraps. Use
attributable startup/task logs, native terminal status, and fixture counters for
representative live behavior. Keep these evidence layers distinct: a separately
launched interpreter does not prove an existing worker's imports, and successful
PDF retrieval or ingestion alone does not distinguish PDFium from parent fallback.

The optional [live validation observer](onyx_patch_validation.md) owns automatic
per-process origin records, execution observation, instrumentation mounts, record
schemas/readers, and overhead qualification. It is not an extraction or acceptance
prerequisite. Record specific live claims existing evidence cannot establish for
that follow-up; do not build its infrastructure during this reorganization or
claim those gaps are covered. A demonstrated patch failure still blocks acceptance;
an unavailable observer-specific measurement does not.

| Process/path | Reorganization validation and evidence limits |
| --- | --- |
| API Alembic/Uvicorn, lite and full | Automatic discovery/composition in selected-image startup tests; cold Make startup and bounded chat/tool/follow-up through the real serving process, correlated with targeted logs and health |
| Background supervisor/control programs | Derived and actual commands preserve exact exclusions and lightweight controls; `python -S` programs do not load application bootstraps |
| Beat | Controlled pinned scheduler tests assert materialized, generated, and installed state after setup/reload, including stale same-name rejection; live startup/reload logs establish operation but do not prove every installed field |
| Six retained workers | Automatic startup tests use supervisor-reset paths; actual supervisor status and logs identify all six thread-pool programs without hidden restart/import failures; no unrelated task fixture per queue is required |
| Spawned indexing/doc-fetch children | Native spawn image tests plus queued synthetic PDF ingestion with attempt/task and child PID correlation where available in native logs; report missing live origin or boundary evidence |
| Enabled bots | Controlled installed-entrypoint tests cover both bots and disabled background code tools; inspect native startup evidence for bots already enabled without requiring credentials for image tests |
| Isolated PDF children, API and background | Separate positive selected-image PDFium execution, origin, pickle transport, and cold/warm timing cases; live synthetic API retrieval and queued background ingestion establish end-to-end behavior, with PDFium versus fallback recorded as unproven where native evidence cannot distinguish them |
| Restart/recreation | Repeat representative API retrieval, worker startup, and queued ingestion after restart and full down/up; correlate with current run/container identities and discard stale evidence |

Assign each contract one primary test layer and reuse its durable harness. The
process matrix above remains required. Preserve existing deterministic and
selected-image behavior coverage for moved code. Use bounded representative live
requests/tasks to establish activation and the affected behavior; do not repeat a
broad subsystem qualification merely because an import path moved.
Run the complete restart/recreation row once on the final tree for each available
supported engine. During intermediate phases, rerun affected activation paths;
repeat the full lifecycle matrix only if a failure or subsequent lifecycle change
invalidates the recorded evidence.

For each additional live check below, record the changed contract that requires
it. External-provider success is supplementary to controlled composition checks.
Broad nested-research, browser reconnect/cache, and secondary-index population
qualification is conditional on changes to those behaviors or an unresolved
regression; it is not a prerequisite for a behavior-preserving extraction.
Standing subsystem requirements still apply when their triggering behavior changes.

| Behavior | Decisive controlled evidence | Scoped live evidence |
| --- | --- | --- |
| Reasoning, tools, history, continuation | Full-bootstrap recorded streams with structured calls, pseudo-call text, partial failures, continuation progress/no-progress, and history round trips | One bounded chat/tool/follow-up through the serving process, correlated with current container/request logs; report any unproven live module-origin claim |
| Prompts and research | Stable-prefix/reminder structure, tool availability, output limits, and exact citation mapping with overlapping IDs under deterministic nested-agent fixtures | Bounded nested research smoke if prompt/research behavior changes or controlled coverage leaves a specific uncertainty; report model citation mistakes separately and claim no cache hits without telemetry |
| Artifacts and recovery | Existing artifact contracts, reconnect harness, and selected-image validators | File creation/download on Docker if artifact behavior changes; authenticated reconnect/reload and existing cache integration targets if recovery/cache behavior changes or a specific regression remains unresolved; backend cache success is not browser recovery evidence |
| Egress/retrieval | Complete stock/direct compositions; controlled mixed results, URL limits, denied destinations, proxy failures, inference/model-discovery and MCP routing; positive PDFium child results under the API bootstrap | Representative stock/direct retrieval for the crawler import/utility changes on configured available routes, including a synthetic PDF in the existing retrieval smoke; expand to GitHub/search or other routing checks when their behavior changes or controlled tests leave a specific uncertainty; reuse owning matrices rather than their Cartesian product |
| Resource/process policy | Exact schedules, commands, thread-pool topology, control exclusions, disabled background code tools, and existing strict-failure/discovery validation cases | Beat/worker startup, queued ingestion, and restart checks using the process-evidence table; no new restart loops, downloads, or heavy control-process imports |

Bound smoke runs by the existing subsystem/provider deadlines and configured
budgets, preserving Tor allowances. Record prerequisites, input, expected
observable, cleanup, and pass/fail/unavailable result for each live row. Do not
repeat the entire external-provider matrix after every extraction; run affected
activation checks per phase and consolidate final qualification. Reuse
`make integration-chat-stream-cache-lite` and `make integration-chat-stream-cache-full`
and the owning reconnect workflow when recovery/cache qualification is applicable,
without adding a competing recovery harness.

### Full-RAG fixtures and isolation

Queued synthetic ingestion and controlled native spawn/PDF activation remain
required. Use existing supported fixture mechanisms or narrowly authorized
synthetic records for those checks. Do not build a general disposable-stack platform for this
migration. Secondary-index live population is supplementary unless its behavior
changes or a specific regression makes it necessary; deterministic and
selected-image sentinel/hash-bypass coverage remains required.

#### Freshness traffic contract

Preserve the pinned Web Connector's existing entry behavior. Its
`load_from_state()` calls `check_internet_connection()` on the first URL using a
non-streaming `Session.get()`, then initializes Playwright before entering the
patched scrape method. Connector validation may also perform a connectivity GET.
For a single-PDF fixture, those GETs can download the PDF body even when the
freshness check subsequently skips the scrape-stage PDF GET and parsing. Removing
connectivity fetches or making browser initialization lazy is explicitly outside
this plan; do not add either change to make an acceptance assertion pass.

Characterize the complete native entry path with controlled dependencies in
selected-image tests, including the ordering of connectivity GET, browser
initialization, and the patched scrape boundary. Keep deterministic freshness
tests for the decision itself. During baseline fixture setup, record the expected
connectivity requests for each tested phase, including validation when it runs.
Retain all fixture HTTP/body counters in the reported totals; use the controlled
entry-path contract, correlated phase counters, and positive freshness evidence
to distinguish connectivity traffic from scrape-stage work. Unexpected additional
body requests fail validation. Do not hide connectivity downloads by discarding
them from counters or report a zero-download recrawl. The unchanged-crawl claim
is no scrape-stage PDF body GET, no PDF parse, and no embedding work.

Selected-image and deterministic tests must establish that contract. For live
recrawls, collect available fixture counters and attributable native status/logs
from dispatch through terminal completion and associated child completion. Record
which negative-work claims those sources can prove. Where they cannot establish
the positive freshness decision or absence of parsing/embedding, report that
specific gap for the observer follow-up; successful completion alone is not proof
that work was skipped, and missing counters are not zero work.

#### Fixture setup and isolation

Identify the primary-ingestion/recrawl recipe during baseline and resolve it before
the live run. Record the actual supported commands or API operations and durable
helper paths here, covering:

- The single synthetic PDF's storage location, served URL, stable HEAD validators,
  and fixture-scoped request counters. Use disposable fixture storage reachable
  through the existing permitted document path; identify any narrow authorization
  needed if the configured private document directory is unavoidable.
- The authenticated connector/credential creation mechanism, restriction to that
  one document, and test-owned resource IDs. Preserve native queues and entrypoints;
  name how initial ingestion and unchanged recrawl are triggered and how connector,
  attempt, task, and child-process records are correlated.
- The authenticated `internal_search` request and expected synthetic-content and
  display-link assertions, together with ingestion/recrawl terminal conditions and
  the counter/log collection window required for negative work assertions above.
- Cleanup by recorded test-owned IDs for the connector, credentials, document/index
  records, fixture files, and test output, plus restoration of the original
  service configuration and running/stopped state. Do not rely on broad collection
  enumeration or deletion to find test resources.

The production document server deliberately suppresses request logs and has no
fixture-scoped HTTP/body counters. Its existing logs cannot supply the traffic
evidence above. Resolve this concrete observation gap during baseline with a
small test-only helper under `tests/`, not production access logging or the
deferred process observer. The proposed mechanism is a subclass of the existing
document request handler that counts GET/HEAD requests, response status, and body
bytes written only for the exact synthetic fixture path. Preserve the existing
handler's confinement, peer restrictions, HEAD validators, and serving behavior;
all other paths retain their silent production behavior and contribute no records.
Do not record headers, credentials, document contents, or private path names.
Counter output belongs in disposable test-owned storage, with synchronized
updates and phase snapshots rather than resets that discard traffic. Bytes
written are server-side evidence, not proof that the client received every byte.

Before the live run, record and demonstrate the exact Make-selected temporary
mount/command mechanism that installs this helper on the existing permitted
document-serving route, including the macOS host-server case when applicable.
It must retain the expected doc-drop authority and gateway policy; a convenient
unrelated fixture hostname does not qualify the freshness allowlist. Verify
controlled GET/HEAD counts, body accounting, synthetic-path-only recording, and
restoration of the ordinary handler and service configuration. Remove only the
test helper's temporary mounts, counters, and fixture resources at cleanup.
Do not add a production logging option, new public listener, policy exception,
or general fixture platform. If the narrow mechanism cannot be installed and
removed safely using the authorized fixture setup, mark the affected live traffic
row blocked, retain controlled freshness coverage, and continue independent
extraction. Do not substitute missing logs for zero requests or weaken the stated
live acceptance requirement.

Prove this recipe can be set up and cleaned up within the implementing session's
authorization before running it. If it needs unavailable access or broad new
infrastructure, record the affected live row as blocked and continue independent
extraction and controlled validation. Do not silently substitute a manual function
probe for queued evidence or claim full live acceptance. Observer feasibility is
separate and must not turn into a prerequisite for this fixture. Use the isolation
checks below for any separate fixture root, including one proposed for primary
ingestion; this requirement does not authorize a general fixture platform.

If an isolated secondary-index fixture is already available or can be achieved
with small test-only setup, use dedicated database, cache, document, file-store,
and OpenSearch storage. First evaluate an isolated fixture checkout/root using
existing Make entrypoints. A separate root or Compose project name does not alone
prove isolation: inspect host-side lifecycle actions as well as the effective
model, including fixed `docker-data` paths, Podman `--project onyx` operations,
secret generation, tokenizer/host-service state, ports, networks, and teardown.
Prove neither path can touch private data or the live project's resources before
starting. Preserve production entrypoints and queues.

If isolation requires broad Makefile parameterization, new lifecycle machinery,
or a fixture platform, defer that infrastructure to a separately scoped plan and
report the secondary population run as deferred. Do not enable FUTURE settings
in the user's normal installation: native port discovery can schedule other
connectors and user files. If a changed secondary-index behavior requires live
validation, report that validation blocked rather than waiving it or mutating
shared state. No fixture-platform work is implicitly authorized by this plan.

Use the following required layers. Deterministic tests own edge cases and negative
assertions; selected-image tests establish compatibility and final composition
against real pinned callables; live tests establish actual queued process execution.
Reuse existing coverage at each layer rather than reproducing every edge case
through a live connector.

| RAG case | Deterministic | Selected image | Live |
| --- | --- | --- | --- |
| Initial PDF ingestion | Preserve extraction, identity/display-link, and indexing contracts | Required real PDFium child result through the fully bootstrapped background PDF caller, native spawn/isolated execution, and unchanged-timeout timing evidence; API child cases are separately required above | Required one synthetic queued ingestion through native spawn/PDF processing, embedding, primary indexing, and retrieval; report whether native evidence distinguishes PDFium from fallback |
| Freshness skip | Required matching/mismatching validators, skip side effects, and sentinel safety | Required enabled/disabled bootstrap compositions, complete native connector entry-path traffic characterization, and freshness/indexing integration using controlled inputs | Required one unchanged recrawl of the same synthetic PDF; account for connectivity body GETs and report proven skipped work and remaining evidence gaps under the traffic contract |
| Native hash skip | Required ordinary-document fixture reaching the hash gate without a freshness sentinel; assert downstream work is skipped | Required real pinned indexing gate behavior in final background composition | Supplementary unless hash/indexing behavior changes or a specific regression remains unresolved |
| Secondary population | Required ordinary-document hash bypass and sentinel exclusion | Required real pinned indexing boundary with native gates bypassed | Conditional only as described above; requires isolated fixture storage |

Use separate cases with explicit reachability evidence:

1. **Initial PDF ingestion:** queue a connector limited to one synthetic PDF;
   verify native doc-fetch/spawn work, PDF processing, shim embedding, primary
   indexing, display-only URL rewriting with unchanged internal identity, and
   `internal_search` retrieval. Distinguish controlled PDFium activation evidence
   from any unresolved live child/fallback distinction.
2. **Freshness skip:** crawl it unchanged and verify matching trusted HEAD
   validators and skipped work to the extent attributable counters/logs establish
   them. Preserve the full negative assertions in controlled tests; report live
   evidence gaps explicitly. Account for connectivity GETs and body transfers under the
   [freshness traffic contract](#freshness-traffic-contract). A successful crawl
   alone is insufficient evidence; zero total download is not claimed.
3. **Native hash skip:** use separate deterministic and selected-image document/DB
   fixtures, and a queued fixture only when demonstrating this live. Reach parsing
   without a freshness sentinel, with equal content hash and no advanced timestamp.
   Assert parsing occurs but downstream chunk/embed/index work does not. Do not
   infer this from case 2 or advance the timestamp while expecting hash skipping.
4. **Secondary population:** always verify at the indexing boundary in
   deterministic/image tests that ordinary documents pass with
   `ignore_content_hash_gate=True`, while freshness sentinels remain excluded
   even when native timestamp/hash gates are bypassed. For conditional live
   qualification, use the isolated stack's native queued port flow to prove the
   test document populates the secondary index despite its PRESENT hash. A sentinel
   is never an empty replacement document and does not populate a secondary index.

Keep the fixtures and assertions under `tests/`; do not mutate existing document
metadata to manufacture coverage. A manually invoked indexing function supplements
but cannot replace required queued ingestion or substantiate a claim of live
secondary population. Report deterministic/image evidence separately from live
evidence and deferred supplementary work.

Use real installed service entrypoints; do not replace them with a convenient
test command for live activation claims. Capture enough startup and task logs
to correlate worker/child PIDs with the synthetic job, without exposing private
prompts, credentials, document contents, or environment dumps. Runtime evidence
must come from recreated containers with the new mounts, not stale containers or
duplicate mounts containing old implementations. Attribute live module origins
only where available evidence establishes them, as specified in the process table.

## Acceptance and handoff

- Every installer/helper/state owner is accounted for in the migration ledger;
  final imports follow the dependency rules and no compatibility shim remains.
- Both bootstraps are orchestration-only; only genuinely shared behavior and
  neutral infrastructure remain shared. Canonical module identity is proven.
- Production mounts and reset-path/spawn behavior are validated in supported
  processes. Discovered strict bootstraps retain fatal required-import/drift
  handling. Discovery probes reject missing/wrong-origin bootstraps; no runtime
  guarantee for absent bootstraps is claimed. Preserve native child recovery;
  positive selected-image PDF evidence proves child execution rather than fallback
  under both API and background bootstraps. Live evidence and its limits follow
  the process-evidence table; a surviving supervisor does not count as proof that
  its workers are patched.
- Source composition is characterized with observable wrapper/decorator effects;
  supported ordering preserves behavior, and necessary corrections are isolated.
  Symbol ownership references shared behavioral contracts and their tests; no
  per-symbol live probe or deferred enforcement work is required.
- The [scoped simplifications](#scoped-simplifications) and their characterization
  tests are complete, including unified fixed-proxy value acceptance with
  caller-specific failure boundaries, deletion of unused direct-proxy selection,
  materialized-only schedule validation, and unused service-setting deletions.
  Intentional configuration/validation corrections remain separately reviewable
  from moves. State identity, independent policy boundaries, existing reasoning
  alias repair, inherited strict settings, and native executor behavior survive.
  No duplicate background mount, obsolete module alias, or replacement installer
  sequence remains. Owning documentation includes the specified schedule and
  freshness corrections; live freshness claims follow the traffic contract.
- `make check`, selected-image composition/activation tests, and the applicable
  required live rows pass, including queued-ingestion smoke and separate API and
  background PDF timing comparisons. Live fixtures use demonstrated authorized
  setup/cleanup recipes, including the narrow synthetic-only HTTP/body counter
  mechanism and restoration of ordinary document serving. The observer plan is
  not an acceptance dependency;
  explicitly report its unresolved live evidence questions separately from failed
  tests or unavailable required smoke runs.
  Report engine/OS runtime coverage separately from rendered-model coverage.
  Report each unavailable required row and each deferred supplementary row with
  its reason; do not describe full live acceptance as complete while required
  smoke runs remain unproven. Supplementary fixture
  infrastructure is not an extraction acceptance requirement.
- Rules are installed in `AGENTS.md` and the canonical patch documents, with
  current paths and standing validation requirements. No historical layout or
  obsolete configuration guidance remains in living documentation.
- Remove only test-owned connectors, credentials, records, indices, uploaded
  artifacts, and fixture files. Preserve existing data.
  Secondary-index live evidence comes only from the isolated fixture stack.
  Restore the stack to its initial running/stopped state and report that state.
- Summarize changed ownership, tests and live evidence, unrun cases, and any
  separately discovered defects. Keep durable validation helpers under `tests/`;
  do not make acceptance depend on scripts existing only in `/tmp` or an earlier
  conversation. Do not claim an image contract proves real-worker activation.

## Migration ledger (implementation working record)
Baseline: 11 Python files, 8,025 lines; manifest remains unchanged. The API
bootstrap calls 24 monolith installers plus eight dedicated installers; the
background calls tokenizer, resource policy, Playwright, inference, connector
egress, and freshness in that order.
### Monolith symbol ownership
Every symbol below originates in `shared/wrapper_env_patches.py`; destinations
are relative to `onyx_wrapper_patches/`. Constants, ContextVars, collections,
and private helpers stay with the listed owner.
| Destination | Symbols |
| --- | --- |
| `api/coding_final_answer.py` | `_CODING_AGENT_FINAL_TRACE_ENABLED`, `_sanitize_fallback_text`, `_summarize_tool_call_for_final_answer`, `_coding_agent_final_section_kind`, `_flatten_coding_agent_final_answer_history`, `_coding_agent_final_answer_fallback`, `_coding_agent_flattened_final_answer`, `apply_coding_agent_final_answer_fallback_patch` |
| `api/config.py` | `_DEEP_RESEARCH_PROVIDE_CHAT_AGENT_TOOLS` |
| `api/deep_research.py` | `_DEEP_RESEARCH_WORKER_LIMIT`, `_validate_deep_research_control_tool_batch`, `_deep_research_sub_turn_index`, `_prepare_deep_research_tool_calls`, `_DEEP_RESEARCH_OUTPUT_LIMIT_REPLACEMENTS`, `_research_report_output_limits`, `_validate_research_report_output_limit`, `apply_deep_research_output_limit_patch`, `apply_deep_research_chat_agent_tools_patch` |
| `api/inference_continuation.py` | `_MIDSTREAM_CONTINUATION_NOTICE`, `_MIDSTREAM_CONTINUATION_FAILED_NOTICE`, `_MIDSTREAM_FINALIZATION_FAILED_NOTICE`, `_MIDSTREAM_REASONING_CONTINUATION_NOTICE`, `_MIDSTREAM_CONTINUATION_INSTRUCTION`, `_exception_chain`, `_midstream_retryable_exception`, `_build_midstream_partial_assistant`, `apply_midstream_inference_continuation_patch` |
| `api/mcp_egress.py` | `apply_mcp_egress_proxy_patch` |
| `api/model_limits.py` | `apply_llm_max_tokens_override_patch` |
| `api/prompt_stability.py` | `_PROMPT_STABILITY_CHAT_REPLACEMENTS`, `_PROMPT_STABILITY_DR_REPLACEMENTS`, `_PROMPT_STABILITY_RESEARCH_REPLACEMENTS`, `_PROMPT_STABILITY_FUNCTIONS`, `_PROMPT_STABILITY_CONSTANTS`, `_patch_investigation_source`, `_patch_investigation_constant`, `apply_agent_prompt_stability_patches`, `validate_agent_prompt_stability_patches` |
| `api/python_artifacts.py` | `_CHAT_FILE_PATH_RE`, `_CHAT_FILE_MARKDOWN_CANDIDATE_LIMIT`, `_relative_chat_file_destination`, `_markdown_link_label`, `_generated_chat_file_filenames`, `_canonical_generated_chat_file_id`, `_find_unescaped`, `_is_escaped`, `_ChatFileMarkdownStream`, `_normalize_chat_file_markdown`, `_append_python_guidance_to_replacement_prompt`, `_ChatFileMarkdownEmitter`, `apply_python_file_link_enforcement_patches`, `apply_chat_file_id_validation_patch`, `_is_uuid`, `_PYTHON_EXECUTION_GUIDANCE`, `_UPSTREAM_PYTHON_STATELESS_GUIDANCE`, `apply_python_file_link_prompt_patches` |
| `api/python_capabilities.py` | `_is_code_interpreter_network_enabled`, `_PYTHON_PACKAGE_LIST`, `apply_python_package_capability_patches`, `_RESTRICTED_NETWORK_TEXT`, `apply_code_interpreter_network_description_patches` |
| `api/reasoning.py` | `_REASONING_TRACE_ENABLED`, `_REASONING_TRACE_LITELLM_DEBUG_ENABLED`, `_REASONING_TRACE_SEQ`, `_REASONING_REMINDER_REORDER_ENABLED`, `_NATIVE_REASONING_DETECTION_OVERRIDE_ENABLED`, `_NATIVE_REASONING_DETECTION_OVERRIDE_LOGGED`, `_REASONING_MODE_TRACE`, `_REASONING_MODE_TRACE_SEQ`, `_reasoning_digest`, `_trace_reasoning`, `_trace_reasoning_mode`, `_caller_context`, `_tool_names_from_definitions`, `_update_bound_module_attr`, `_message_field`, `_message_has_field`, `_tool_call_count`, `_message_role_counts`, `_trace_reasoning_message_census`, `_trace_reasoning_request_body`, `_enable_litellm_reasoning_trace_debug`, `_first_non_empty_string`, `_set_extra_attr`, `_attach_reasoning_fields`, `_dump_message_with_reasoning_fields`, `_is_tool_call_response_message`, `_is_assistant_message`, `_is_user_message`, `_message_reasoning_text`, `apply_native_reasoning_detection_override_patch`, `apply_reasoning_mode_trace_patch`, `apply_reasoning_content_preservation_patch` |
| `api/retrieval_limits.py` | `_set_single_default`, `apply_internal_search_context_patches`, `apply_open_url_char_limit_patches` |
| `api/searxng_retry.py` | `apply_searxng_single_attempt_patch` |
| `api/tool_calls.py` | `apply_native_tool_calls_only_patch`, `apply_vllm_glm_auto_tool_choice_patch` |
| `api/tool_result_history.py` | `apply_preserve_tool_results_patch` |
| `common/config.py` | `EFFECTIVE_UNLIMITED_CHARS`, `_strict_mode`, `_warn_or_raise`, `_raise_if_strict`, `_replace_or_warn`, `_parse_positive_int`, `_parse_optional_positive_int`, `_env_flag_enabled`, `_env_flag_default_true`, `_required_positive_int`, `_validated_fixed_proxy_url` |
| `common/source.py` | `_patch_function_source`, `_prompt_stability_replace` |
| `common/text.py` | `_truncate_text_with_notice` |
| `shared/embedding_tokenizer.py` | `apply_embedding_tokenizer_alias_patch` |
| `shared/inference_proxy.py` | `apply_configured_inference_proxy_patch` |
| `shared/playwright_proxy.py` | `_PLAYWRIGHT_PROXY_OVERRIDE`, `select_playwright_proxy`, `apply_playwright_helper_proxy_patch` |

### Dependency and contract map
API reasoning reads the import-time sharing flag from `api/config.py`;
Deep Research, continuation, and coding import reasoning helpers. Prompts
import the report-limit validator from Deep Research. Artifacts and prompts
use exact-count replacement in `common/source.py`. Only tokenizer, inference,
and Playwright installers serve both services. `_update_bound_module_attr`
remains reasoning-private; its detection and optional trace callers repair
identity-matched loaded attributes, not defaults/closures/registries.

| Contract | Primary existing evidence | Additional acceptance layer |
| --- | --- | --- |
| Reasoning, history, tools, continuation | `test_shared_agent_patch_contracts.py`, `test_midstream_inference_continuation.py`, `test_native_tool_calls_only.py` | Final API composition and bounded live follow-up |
| Prompts, research, artifacts | `test_agent_prompt_stability.py`, `test_shared_agent_patch_contracts.py` | Pinned translated streams and prompt globals |
| Inference, MCP, Playwright | `test_helper_and_inference_proxy_patches.py`, `test_mcp_egress_patch.py` | Complete compositions and canonical selection table |
| Crawlers and open_url | Existing stock/direct/failure/limit/identity tests | Both selected compositions and live PDF retrieval |
| Resource policy and RAG | `test_background_power_saving.py`, `test_web_connector_egress_patch.py` | Pinned materialized/generated/installed schedules, queued synthetic PDF |
| Process activation | `test_sitecustomize_stdout.py`, `patch_activation_probe.py` | Automatic startup, spawn, separate native PDF children, live lifecycle |

### Failure boundaries
API implementation imports occur inside `_install` but outside individual
installer catches: import failure ends the remaining sequence; the outer
bootstrap prints to stderr and exits 78 only in strict mode. Each installer
retains its existing local catches and early returns during extraction.
Background shared forwarding wrappers catch both import and application
errors: strict rethrows to exit 78; non-strict reports and continues to the
next installer. Resource policy catches its entire application similarly.
Connector egress catches dependency imports only: non-strict import failure
continues to freshness, but configuration or later application errors escape
and terminate the remaining sequence without exit 78 in non-strict mode.
Freshness keeps its separate import/application guards and indexing-sentinel
validation; control exclusions remain exact. No catch moves across imports.

Corrected configuration defect: fixed-proxy validation previously accepted
noncanonical representations that Playwright context selection rejected. Canonical equality,
GitHub whitespace, Playwright empty-value failure, crawler HTTP parsing, and
materialized schedule checks are intentional corrections, separate from moves.


### Background and dedicated-module ownership

The background bootstrap retains only strict fatal handling, exact control
exclusions, ordered calls, and three shared forwarding wrappers whose catch
boundaries include implementation imports. `background/config.py` owns its
strictness and environment helpers. `background/resource_policy.py` owns
`_apply_sleepy_background_patch` and the new materialized-schedule validator;
`background/web_connector_egress.py` owns `_WEB_CONNECTOR_PROXY` and its
installer. All freshness constants, `_INDEXING_SKIP_PATCHED`, `_PATCH_LOGGER`,
`_LOG_ONCE_KEYS`, callable/source contracts, metadata helpers, display-link
rewriting, indexing-sentinel filtering, and both freshness installers belong
to `background/document_freshness.py`. No original background top-level symbol
is unaccounted for.

The dedicated GitHub, model-display, stock/direct crawler, URL-identity, and
WebUI-reconnect modules retain their implementation names under `api/`.
The former failure-reporting and URL-limit modules share `api/open_url.py`,
including the original-run class marker and request ContextVar; its two explicit
installers retain failure-before-limit ordering. The crawler selector and
common crawler parsers belong to `api/config.py`, so selecting stock does not
import the direct implementation and vice versa.

### Consolidated acceptance evidence

- `make check` passes on macOS and the Linux VM: 689 tests, 20 skips,
  compilation, help validation, and
  whitespace checks. The Make-selected platform/feature model matrix passes;
  this does not qualify unavailable runtimes.
- Baseline and final pinned-image gates pass on host Docker, without changing
  image pins or dependency locks. Final automatic API startup passes in both
  crawler modes; background startup passes with freshness enabled and disabled.
- Canonical inert imports, native spawn, and native isolated PDFium execution
  pass under both bootstraps. API PDF cold/warm baseline timings were
  3.001/2.913 seconds and final timings 3.309/3.355 seconds; background baseline
  was 2.162/2.145 seconds and final 2.138/2.128 seconds. The native 120-second
  timeout is unchanged. This controlled evidence does not establish live queued
  child execution. Controlled strict child startup failures also preserve native
  parent error mapping, discarded stderr, clean pickle stdout, and pypdf recovery.
- Negative discovery, wrong-origin bootstrap, missing required package, and
  source-drift image fixtures pass. Materialized/generated/native-installed
  schedule checks pass, including detection of native stale same-name entries.
- Controlled native single-PDF connector entry paths perform two connectivity
  body GETs in both freshness modes. Disabled freshness performs one scrape GET;
  matching enabled freshness performs none. These counts are controlled-image
  evidence, not a live recrawl claim.
- Host Docker lite (stock crawler) and full (direct Obscura crawler) start and
  survive clean down/up recreation with native entrypoints and complete patch
  startup diagnostics. Full published `/api/health` and WebUI return HTTP 200;
  the native supervisor reports Beat and all six Celery worker roles RUNNING.
  No patch initialization failure appears in their startup logs. These checks
  establish startup, not authenticated tool behavior or live child origins.
  Authenticated stock-lite and direct-full HTML retrieval/tool/history checks
  pass using a temporary admin API key. Native tool results contained the
  fetched page, and the follow-up retained its title and synthetic phrase.
  Direct retrieval of the public W3C test PDF was access-denied; the harness
  rejected the model's answer from memory as evidence. The host PDF checks below
  establish successful live retrieval with other public URLs; controlled native
  PDFium evidence remains separate.
- Host no-VPN lite API PDF checks pass for both
  `https://arxiv.org/pdf/1706.03762` and
  `https://pdf-reader-dkraft.s3.us-east-2.amazonaws.com/1706.03762.pdf`, through
  both stock and direct Obscura crawler modes. Direct host downloads return
  HTTP 200 and PDF MIME types (2,215,244 and 2,201,700 bytes respectively).
  Each serving-process request executes `open_url`; native tool results contain
  the paper title and requested attention-head passage, rather than relying on
  the model's prior knowledge. Both crawler modes produce identical extracted
  text lengths for each URL: 40,435 and 40,258 characters respectively. The
  four disposable chat sessions and temporary admin account are removed.
  Separate installed-crawler diagnostics extract `Dummy PDF file` from W3C in
  stock mode, while direct Obscura receives a 4xx access-denied response before
  parsing. Those diagnostics do not substitute for serving-process evidence or
  identify the site's exact reason for denying the browser request.
- The user authorized creation/removal of one named empty document-source
  mountpoint. The synthetic PDF stays in `/tmp` and uses the ordinary read-only
  source mount, serving checks, route, and fixture-only HTTP counter. Native
  queued attempt 2164 processes one PDF; logs correlate docfetch, processing,
  and spawned indexing worker PID 132, one chunk, embedding, and index-write
  stages. Scoped `internal_search` returns exactly that document, with unchanged
  internal identity and rewritten display link. Live PDFium-versus-fallback and
  per-child module origins remain unproven by native logs. Initial ingestion
  succeeds with two GETs (1,214 body bytes) and two HEADs. Unchanged attempt 2165
  succeeds with one connectivity GET (607 bytes) and one HEAD, without CHUNKING,
  EMBEDDING, or VECTOR_DB_WRITE stage events. An additional unchanged attempt
  also succeeds. After full down/up and changing only the synthetic PDF text,
  attempt 2167 succeeds with fresh chunking, embedding, and an index write;
  scoped search returns the changed text. Its traffic again includes two GETs
  (1,214 bytes) and two HEADs. These results establish skipped downstream work
  and the retained connectivity download; they do not claim zero total GETs.
  Native queued deletion completed. Exact-ID checks confirm the connector,
  credential, pair, document, document set, and primary index chunks are gone.
  The private test persona's native deletion tombstone was purged only after
  its ownership and absence of chats were verified. The temporary admin key,
  associated account, and local secret file are removed.
  The fixture uses the documented recursive Web connector: native single-URL
  API validation tries to resolve the restricted internal hostname and fails.
  Owner-only private sets work in this installed edition; explicit private-set
  user sharing does not. No existing connector, persona, or document is changed.
- Native Linux ARM64 VM validation uses Podman 5.4.2 with crun and rootless
  Docker 26.1.5+dfsg1, with Compose 5.1.4. Both checkouts began at
  `1fcf891fa7fe91ede7185ea75139e6ed6ff51d12` on `onyx_v469_reorg`; the small
  validation fixes described below were applied to both. Engines ran serially
  through Make's shared-data ownership handoff. Rootless Docker used its
  explicit context and separate database volumes. Actual selected models retain
  the canonical package mounts and bootstrap paths. Docker 26 selects ordinary
  internal bridges, emits the documented host-reachability warning, and retains
  controller separation; Engine-28 isolated gateways are not claimed.
- Both VM `make test-patch-images` gates pass, including inert imports, strict
  API/background compositions, native spawn/PDFium, negative discovery/drift,
  and strict-child parent recovery. Podman API PDF cold/warm timings are
  3.330/3.436 seconds and background 2.218/2.288; rootless Docker API timings
  are 3.031/3.025 and background 2.161/2.090. The native timeout remains 120
  seconds. Docker-only executor contracts pass on rootless Docker.
- Both VM engines pass lite/full startup and full down/up recreation, published
  API health, actual Beat plus all six worker roles, and authenticated HTML
  retrieval/history before and after recreation. Public W3C PDF retrieval fails
  to return the expected native tool content on both; the harness rejects it.
  This does not weaken the separate controlled PDFium evidence.
- The user authorized the exact empty VM source mountpoint
  `onyx-reorg-vm-validation`, with payloads and counters under `/tmp`. Both
  applications initially lacked LLM/search providers; temporary native entries
  selected existing Teep/SearXNG services. Their saved 768-dimensional index
  settings disagreed with the configured 1,024-dimensional Qwen endpoint.
  After verifying no non-fixture documents existed, user-approved native
  migrations selected `nomic-ai/nomic-embed-text-v23`, dimension 1,024, and
  normalization. These corrected settings remain in both applications.
  Podman's first attempt failed with the old dimension. After migration, an
  unchanged retry skipped retained document metadata and returned no search
  result; changing only the fixture text forced real reprocessing. These checks
  do not qualify recovery from arbitrary failed-ingestion/index migrations.
- Podman attempt 5 successfully processes the changed PDF after recreation;
  scoped search verifies its exact uppercase `ONYX` text. Unchanged attempt 6
  succeeds while skipping downstream work. Rootless attempt 2 ingests the
  original PDF, attempt 3 skips unchanged downstream work, and attempt 4 after
  recreation processes and retrieves the changed text. Native logs correlate
  spawned tasks and processing stages, including rootless attempt 4 child PID
  104 and one chunk. Each real ingestion uses two GETs (1,214 bytes) plus two
  HEADs; each unchanged recrawl uses one GET (607 bytes) plus one HEAD. Live
  child module origins and PDFium-versus-fallback remain unproven by native
  logs. Conditional secondary-index population remains deferred; no isolated
  fixture storage platform is introduced by this change.
- VM prerequisite fixes give standalone Podman `onyx-build` its required image
  list and cover its dry-run pull commands. An exact-boundary idle-proxy test
  now uses a fixed clock to avoid uptime-dependent floating-point rounding;
  runtime lifecycle behavior is unchanged. The admin helper supports explicit
  Podman selection and disables persistent memory only on its new test account,
  so chat-history evidence cannot come from a saved memory. The synthetic prompt
  also avoids asking for persistent memory.

### Reproducible live fixture tools

`tests/patch_reorg_admin.py` creates/revokes an explicitly authorized temporary
admin key using native Onyx helpers and an exclusive owner-only credential file.
Use `--container-bin podman` for Podman; Docker inherits `DOCKER_CONTEXT`.
The newly created account has persistent memory disabled for scoped history tests.
`tests/validate_patch_live_chat.py` owns only its synthetic chat and hard-deletes
it after testing. `tests/prepare_patch_reorg_fixture.py` prepares the one-PDF
submount/counter and Make overlay; `tests/validate_patch_live_rag.py` owns only
its manifest-recorded connector, credential, document set, private search
persona, and chats. Queued connector deletion owns removal from the index;
cleanup must finish before revoking the admin key or stopping the stack.
`patch_reorg_admin.py revoke --fixture-manifest ...` verifies the exact fixture
rows and index chunks are absent, purges only its deleted private persona after
ownership/chat checks, and then revokes the key and removes its secret file.

The host and VM stacks are restored to their initial stopped state; the selected
`make ps-full` commands are empty and VM shared-data ownership is unclaimed.
The automatically started host MLX lifecycle proxy was stopped by the supported
teardown. The approved empty document-source mountpoints, temporary PDFs, and
overlays are removed. Native exact-ID/index checks precede each temporary admin
key/account revocation; test-owned providers, personas, document sets, connectors,
credentials, chats, and indexed documents are removed. Non-secret counters/logs
remain in disposable acceptance evidence storage; no credential secret remains.
Only fixture-owned records were removed. The approved VM embedding settings
remain corrected. The VM validation follow-up changes are uncommitted.
