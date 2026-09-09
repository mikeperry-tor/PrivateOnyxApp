# Onyx Agent Investigation Prompt Cache Stability Plan

**Status:** Implemented; deterministic and pinned-image gates passed. Live
main-chat validation passed; live Deep Research remains blocked by model
behavior reproduced against the unchanged baseline.

**Canonical behavior:** [Investigation prompt stability](../../onyx_patch_info.md#investigation-prompt-stability)
and [upgrade validation](../../onyx_patches_upgrade.md#runtime-patch-contract-audit).

**Validation evidence (2026-09-09):**

- `make check`: deterministic Python tests, compilation, help validation, and
  whitespace validation pass.
- `make test-patch-images CONTAINER_BIN=docker`: passes against the local pinned
  images, including the three translated loop captures, final bindings, both
  custom-prompt branches, and disabled-sharing bootstrap with a 37-cycle budget.
- Full-stack stock chat with the configured GLM-5.3 Flash model: successful web
  search, useful official-page retrieval, citations, Python-generated four-row
  CSV, and normal streaming completion. The requested artifact is downloadable
  and appears once with the exact persisted `response_markdown`; all three
  tool results and their reasoning remain saved.
- Live default Deep Research: the model returns no research-agent call at the
  first orchestration request. The same request and model fail identically
  against the unchanged tracked patch baseline in a temporary loopback-only
  API process. Live nested research and report generation therefore remain
  unverified; the deterministic installed-loop captures pass. No prompt-change
  regression is established by this comparison.
- A distinct smaller-model artifact check is inapplicable: GLM-5.3 Flash is the
  only visible configured model. Provider cached-token telemetry was not
  collected and is not an acceptance gate.
- The temporary account and baseline process are removed; the full Docker
  stack is returned to its initial stopped state.

**Scope:** Narrow Onyx runtime patches for the common stock/default main-chat
tool loop and the default-enabled Deep Research investigation path, plus one
constant-only coding-agent cleanup. The goal is to remove prompt mutations that
occur on most multi-cycle investigations, not to preserve cacheability through
every specialized branch.

Remove the redundant generated-file reminder across all main-chat branches,
including replacement personas; preserve their stable Python guidance. This
cleanup has broader change scope than the cache-stability guarantee.

Deep Research prompt cleanup and correctness fixes apply with chat-tool sharing
enabled or disabled; only the default-enabled path receives a prefix guarantee.
Provider adapters, explicit-cache breakpoint policy, Teep, NearAI components,
CVM ingress, and upstream reference checkouts receive no cache-specific redesign.

**Authority:** Throughout this plan, [Stability Contract](#stability-contract)
is authoritative for the prefix guarantee, reminder policy, exclusions, and
scope limits. [Test Coverage](#test-coverage) and [Validation](#validation) are
authoritative for required assertions, fixture scope, execution gates, and live
evidence. These requirements apply to every implementation, documentation, and
completion section without repetition. Implementation Design supplies the
specific edits within those boundaries.

**Upstream baseline:** The inspected source is Onyx v4.6.7 at commit
`a64b87a456a4a7dfa573553ed9b2df8e7b6c9dd3`, matching the committed
`ONYX_IMAGE_TAG`. Reconfirm the tag, checkout commit, and installed image
source before implementing any exact-source runtime patch.

## Objective

Keep the early request prefix stable across ordinary, consecutive tool cycles
on the paths this stack uses most often:

- stock/default main chat with automatic model-selected tools and without a
  replacement base system prompt, including stable task prompts, fixed selected
  tool subsets, and Python calls that generate files;
- Deep Research orchestration with the wrapper's default
  `ONYX_DEEP_RESEARCH_PROVIDE_CHAT_AGENT_TOOLS=true` behavior; and
- the nested research-agent investigation invoked by that default Deep
  Research path.

Also remove the changing cycle number from the coding-agent investigation
prompt through a constant-only patch.

This should give provider-side prefix caches a better opportunity to reuse
prefill work. It does not promise a cache hit, a billing discount, or movement
of Onyx's explicit cache breakpoint. Cache affinity, worker retention, provider
policy, minimum cacheable length, and eviction remain external outcomes.

Preserve citations, selected tool behavior, tool results, structured reasoning,
file links, streaming, persistence, context-window enforcement, cycle limits,
model-specific tool choice, existing finalization behavior, and visible error
handling. Do not improve apparent reuse by omitting useful context.

## Why This Scope

The pinned source changes early prompt text during common investigation loops:

1. `backend/onyx/chat/llm_loop.py` changes citation guidance after a citeable
   tool returns, adds an ordinary post-web-search reminder, and starts a
   persistent file reminder after Python generates a file. The wrapper already
   moves reminders beside the latest user message, before subsequent
   assistant/tool history. Reconstructing a reminder at that position does not
   break prefix growth when its presence and text remain unchanged; changing
   citation applicability and switching web/file reminders do.
2. `backend/onyx/deep_research/dr_loop.py` puts the current cycle number in
   the orchestration system prompt and adds a one-time first-cycle reminder.
3. `backend/onyx/tools/fake_tools/research_agent.py` puts the current cycle
   number in its investigation prompt and adds a post-search reminder.
4. The coding-agent investigation prompt contains a changing cycle number.
5. Multiple local patches rewrite these functions, so patch ordering and
   exact-source validation are part of correctness.

The plan intentionally does not generalize from these cases into a universal
prompt-snapshot framework:

- common-path tool definitions are deterministic and can be checked for
  semantic equality without introducing copied immutable snapshots or
  stateful accessors;
- replacement-base personas have deliberately different prompt ownership and
  should not be forced to inherit stock citation or `open_url` policy;
- stable citation and task reminders already have a stable position in the
  wrapper's constructed history; retain them without relocating user-authored
  instructions or introducing task-prompt-specific cache branches;
- generated-file delivery already has stable requirements in
  `PythonTool.DESCRIPTION` and `PYTHON_TOOL_GUIDANCE`, an exact
  `response_markdown` value in each result, and output normalization; the
  dynamic reminder duplicates those stronger layers and has no recorded
  model-specific validation;
- transitions in tool selection or tool choice, image handling, truncation, and
  finalization already change the request for functional reasons; a fixed tool
  subset does not itself require a different prompt policy; and
- provider routing and worker warmth are not properties an Onyx runtime patch
  can guarantee.

## Narrow Prerequisite Correctness Fixes

Two defects should be corrected with the prompt-stability patch because stable
guidance would otherwise preserve incorrect information:

- nested-research guidance must name the installed `open_url` tool, not
  `open_urls`; and
- Deep Research internal-search tuning must be derived from the tools actually
  selected for that request, not merely from the configured allow-name list.

These are correctness fixes, not cache machinery. Their tests should assert
tool/prompt agreement directly.

## Sources and Files to Re-audit Before Editing

Do not edit `reference_repos/`; it is read-only audit material. Compare the
installed pinned image with the reference checkout before relying on source
text or replacement counts.

Primary upstream call sites:

- `reference_repos/onyx/backend/onyx/chat/llm_loop.py`
- `reference_repos/onyx/backend/onyx/chat/llm_step.py`
- `reference_repos/onyx/backend/onyx/chat/process_message.py`
- `reference_repos/onyx/backend/onyx/chat/prompt_utils.py`
- `reference_repos/onyx/backend/onyx/prompts/chat_prompts.py`
- `reference_repos/onyx/backend/onyx/prompts/tool_prompts.py`
- `reference_repos/onyx/backend/onyx/deep_research/dr_loop.py`
- `reference_repos/onyx/backend/onyx/prompts/deep_research/`
- `reference_repos/onyx/backend/onyx/tools/fake_tools/research_agent.py`
- `reference_repos/onyx/backend/onyx/tools/fake_tools/coding_agent.py`
- `reference_repos/onyx/backend/onyx/prompts/coding_agent/`

Local patch composition and validation:

- `onyx/patches/shared/wrapper_env_patches.py`
- `onyx/patches/sitecustomize_api_server/sitecustomize.py`
- `tests/test_shared_agent_patch_contracts.py`
- `tests/validate_pinned_api.py`
- existing reasoning, tool-history, finalization, Deep Research, coding-agent,
  citation, and generated-file-link tests

Canonical documentation:

- `docs/onyx_patch_info.md`
- `docs/onyx_patches_upgrade.md`
- `README.md`, under **Key Patches to Stock Onyx**

Read `docs/request_handling.md` before changing web-search or `open_url`
instructions. No update is expected there because this plan does not change
transport, availability, limits, or user-visible retrieval behavior.

## Stability Contract

### Common-path semantic prefix

For consecutive requests in one scoped investigation phase, the earlier Onyx
message list, after the installed `translate_history_to_llm_format` conversion
and as passed to the LLM boundary, must remain a semantic prefix of the later
message list. Compare tool definitions and request options separately:

- the system message and stable initial guidance are equal;
- existing messages retain their roles, content blocks, tool-call IDs, tool
  results, reasoning fields, and order;
- new assistant calls and tool responses are appended after retained history;
  and
- tool definitions are semantically equal and remain in the same order.

The prompt-shaping inputs must also remain equal: model/deployment identity,
tool choice, reasoning options, chat template and arguments, initial injected
context, existing message content, multimodal inputs, and output-token policy.
A change to one of those inputs is outside this contract. Newly retrieved
documents and other tool results appended to history are expected growth, not
changes to the initial context or exclusions from the guarantee.

Tool definitions need no new immutability guarantee. Do not add production
snapshots or stateful tool-definition accessors.

The system date may remain stable for an invocation. A request spanning a date
rollover may be cold; no clock-freezing mechanism is warranted.

### Reminder policy

Classify prompt additions by semantics rather than routing every item through
one configurable reminder framework:

- **Stable advisory policy** belongs in the initial request whenever the
  corresponding capability is available. This includes citation policy,
  web-search-to-`open_url` guidance, generated-file response requirements, and
  the substantive guidance currently repeated by first-cycle reminders.
  Retain the ordinary citation reminder and task prompt at their existing
  position beside the latest user message, before assistant/tool history.
  They need neither removal nor relocation when their presence and text are
  stable from the first request.
- **Terminal transition directives** may remain request-specific at an already
  cold boundary. This includes post-image response instructions, cycle-limit
  forced completion, and final-answer/report synthesis.

The generated-file event is represented by the appended Python tool result,
including its exact `response_markdown`; it does not need a second dynamic
user-role reminder. Do not add an environment preference, model-name heuristic,
or generic reminder registry. Small, named transforms at the owning loop are
clearer and avoid a new behavior matrix.

### Deliberate cache boundaries and exclusions

No prefix guarantee is made across:

- a new user invocation;
- a replacement base system prompt, including the empty-default-base custom
  prompt branch;
- a change in selected tools or tool choice, including a forced-tool request's
  transition back to normal tools;
- post-image and cycle-limit forced completion;
- requests carrying the translator's trailing `IMAGE_DROP_REMINDER`, even
  when the attached images and dropped-image count remain unchanged;
- context truncation or summarization;
- Deep Research clarification, planning, orchestration-to-report, or final
  report transitions;
- Deep Research with
  `ONYX_DEEP_RESEARCH_PROVIDE_CHAT_AGENT_TOOLS=false`;
- nested research report generation;
- coding-agent final synthesis;
- a change in model, persona, project instructions, memory, configured tools,
  prompt template inputs, initial injected context, existing message content,
  or multimodal inputs; or
- provider retries that intentionally change request fields.

When Onyx's per-request image cap drops attached images,
`translate_history_to_llm_format` appends the notice after all other messages
on every request. New tool history therefore appears before the old notice's
position, breaking whole-message prefix growth even with unchanged attachments.
Leave this uncommon case excluded; do not relocate the notice or add
image-specific cache handling.

These exclusions do not relax functional correctness. Retained history must
still be valid, tool responses must remain paired with their calls, and
reasoning, citations, files, and errors must not be silently lost.

Stable task prompts and fixed selected tool subsets use the same default-base
prompt policy as other main-chat requests. Do not track whether a request began
forced or restricted just to preserve a separate reminder policy. A cold tool
transition does not require special cache handling in later ordinary cycles.

Router selection, backend choice, warm-worker affinity, eviction, minimum
cacheable length, and provider accounting are external cache-hit non-goals,
not Onyx prompt phases.

Do not broaden implementation to make excluded cases prefix-compatible or
redesign alternate nested-agent configurations. Report a scoped stability
failure, including an actual tool-definition mutation, and defer any broader
solution to a separate design rather than weakening the contract.

## Implementation Design

### 1. Patch composition and source validation

Implement narrow exact-source transforms in
`onyx/patches/shared/wrapper_env_patches.py` and invoke them in the API server
startup sequence immediately after `apply_python_file_link_enforcement_patches()`.
This places them after the Python prompt patches, Deep Research tool and cycle
configuration, reasoning preservation, native-tool handling, continuation,
coding finalization, and tool-result preservation patches. Consume their
accumulated function sources and already-configured prompt constants; do not
rebuild from pristine upstream text or restore upstream cycle budgets. Put the
final source/constant/binding assertions at the end of the API patch sequence,
after the selected crawler installer.

The existing `_patch_function_source` helper checks that a fragment exists and
replaces its first occurrence; it does not reject duplicate matches. Add
focused exact-count preflight for these new transforms before using the helper.
Do not expand this work into a redesign of all existing patch installers.

For every transformed function or constant:

- preflight the expected upstream source shape and exact match count;
- transform the latest accumulated `_wrapper_patched_source` where another
  wrapper patch already modified the function;
- validate the final installed source or constant after all patch functions
  run;
- fail startup loudly on source drift, zero matches, duplicate matches, wrong
  patch ordering, or stale bindings; and
- avoid a generic prompt-rewriting abstraction when a small named transform is
  sufficient.

Do not change generated Onyx deployment files by hand.

Cover each changed guidance constant, both reasoning variants where present,
and their consumer bindings:

| Owning prompt module | Constants | Imported consumer bindings |
| --- | --- | --- |
| `onyx.prompts.tool_prompts` | `OPEN_URLS_GUIDANCE` | `onyx.chat.prompt_utils` |
| `onyx.prompts.deep_research.orchestration_layer` | `ORCHESTRATOR_PROMPT`, `ORCHESTRATOR_PROMPT_REASONING` | `onyx.deep_research.dr_loop` |
| `onyx.prompts.deep_research.research_agent` | `RESEARCH_AGENT_PROMPT`, `RESEARCH_AGENT_PROMPT_REASONING` | `onyx.tools.fake_tools.research_agent` |
| `onyx.prompts.deep_research.dr_tool_prompts` | `OPEN_URLS_TOOL_DESCRIPTION`, `OPEN_URLS_TOOL_DESCRIPTION_REASONING` | `onyx.tools.fake_tools.research_agent` |
| `onyx.prompts.coding_agent.coding_agent` | `CODING_AGENT_PROMPT`, `CODING_AGENT_PROMPT_REASONING` | `onyx.tools.fake_tools.coding_agent` |

Validate the bindings actually used by the final installed functions, including
their globals after source rebuilding. The `OPEN_URLS_*` Python identifiers can
remain unchanged; correct the model-facing tool name in both values.

Also validate that `onyx.chat.process_message.run_llm_loop` and
`onyx.chat.process_message.run_deep_research_llm_loop` resolve to the final
installed functions in their defining modules. These direct imports can retain
stale functions if startup import order changes. Check them after the complete
bootstrap; a correct defining module alone is insufficient. Preserve the current
import order and fail loudly on stale bindings rather than adding a generic
alias-rebinding mechanism.

### 2. Stock/default main chat

Apply one stable main-chat policy to the branch that retains Onyx's default
base system prompt, including nonempty task prompts and fixed selected tool
subsets. Preserve task-prompt processing and its existing position. Keep
replacement-base branches, including the empty-default-base custom prompt
branch, separate because they have different prompt ownership.

At request setup, determine citation applicability from stable inputs:

`include_citations and (context files require citation or a supplied tool is citeable)`

Use the request's supplied `tools` and the existing `CITEABLE_TOOLS_NAMES`
contract, not an agent's unfiltered configuration or the temporary forced-tool
subset. Preserve the existing context-file applicability predicate.

Use that same decision from the first model request for system/template citation
guidance and the ordinary citation reminder, so neither appears only after a
citeable tool returns. Do not let later tool calls override the stable decision
on this path. Keep the existing citations protocol and citation validation
behavior.

On this default-base path, remove the ordinary post-web-search reminder and the
dynamic transition that adds citation guidance after a search result. Retain
`CITATION_REMINDER` and task-prompt processing at the position already supplied
by the wrapper's reminder-placement patch. In the initial capability-gated
`OPEN_URLS_GUIDANCE`, replace the blanket "almost always" post-search wording
with one concise instruction to open promising pages unless the snippets
completely answer the query. This preserves the removed reminder's
snippet-sufficiency exception without adding a second paragraph. Retain guidance
for user-provided URLs and the installed tool's limits; do not copy "Open as many
as you want" from the reminder. Preserve terminal citation and completion
instructions. Upstream clears `forced_tool_id` after the first forced cycle;
it is not a stable input to this prompt policy.

Replacement-base branches retain their existing citation/web reminder behavior.
Do not rewrite `construct_message_history`; validate that the existing reminder
placement patch remains installed and keeps stable reminders before tool history.

Separately, remove the dynamic application of `FILE_REMINDER` across every
main-chat branch, including replacement personas, task-prompt requests, and
forced/final cycles. Remove reminder-only generated-file state and parsing if
it has no remaining consumer. Do not alter the separate generated-file metadata,
persistence, or normalization paths.

Retain generated-file response requirements in:

- `PythonTool.DESCRIPTION`;
- stable `PYTHON_TOOL_GUIDANCE`, including its use for replacement prompts;
- each generated-file result's exact `response_markdown`; and
- streaming, persistence, and replay normalization.

Both the replace-base checkbox and empty-default-base custom-prompt branches
must receive the same capability-gated stable Python guidance. Reuse the
existing Python-only helper at the empty-base construction site, preserving
the custom text and citation/web policy without injecting general stock
guidance. Cover both branches with and without Python available.

Stop locally rewriting the upstream `FILE_REMINDER` constant once no main-chat
branch applies it, and remove tests and documentation that claim the
local patch owns that reminder's text.

Do not alter:

- post-image and cycle-limit completion directives, apart from removing the
  separate generated-file reminder;
- forced-tool tool selection;
- message/history construction;
- replacement-base persona processing;
- the existing Python-only replacement-prompt guidance helper; or
- file-link enforcement and finalization.

### 3. Deep Research orchestration

Apply the orchestration and nested-research transforms in sections 3–4
unconditionally, for both values of
`ONYX_DEEP_RESEARCH_PROVIDE_CHAT_AGENT_TOOLS`. The existing tool-sharing installer
returns early when disabled, leaving different accumulated source. Target the
common source fragments for these prompt changes; do not require the enabled
branch's `allowed_tools = list(tools)` replacement or introduce a separate
prompt policy. Preserve each branch's existing tool selection and execution.

For orchestration:

- replace changing `current_cycle` text with stable wording that retains the
  configured maximum-cycle budget;
- remove `FIRST_CYCLE_REMINDER` without adding prompt text: both installed
  orchestration variants already require coverage of the question and plan and
  investigation of newly discovered directions. Reconfirm that equivalence at
  the upgrade gate;
- determine internal-search tuning from the actual `allowed_tools` selected
  after either branch's filtering, not `allowed_tool_names`; and
- preserve current tool filtering, ordering, reasoning, history, limits, and
  report transition behavior.

### 4. Nested research-agent investigation

For the nested research loop:

- correct `open_urls` to `open_url`;
- replace changing cycle-number text with stable maximum-budget wording;
- in both initial `OPEN_URLS_TOOL_DESCRIPTION` variants, replace the blanket
  post-search wording with the same snippet-sufficiency exception specified for
  main chat, retaining capability gating and the non-reasoning variant's
  think-tool guidance; remove the dynamic post-search reminder without adding
  a second paragraph or weakening the installed tool's limits; and
- preserve tool calls/results, selected-tool order, reasoning, limits, and
  report generation.

### 5. Coding-agent constant cleanup

Patch both coding-agent investigation prompt constants so neither includes the
current cycle number and both still state the fixed maximum budget. Validate
that the active function bindings use the patched constants.

Leave the coding loop and its flattened-history compatibility path unchanged.

### 6. Explicit cache and provider behavior

Leave Onyx's `should_cache`, `PROMPT_CACHE_CHAT_HISTORY`, provider request
translation, cache-control markers, and Teep/NearAI routing unchanged.
`PROMPT_CACHE_CHAT_HISTORY` remains false by default, and this plan does not
claim that the explicit cache breakpoint advances during an active tool turn.

Do not add provider-specific cache headers, provider-specific request rewrites,
an HMAC prompt logger, cross-process prompt capture, or cache-key plumbing.

If later evidence shows a provider adapter mutates the stable Onyx request,
handle that as a separate provider-scoped plan with its own documentation and
validation.

## Test Coverage

Compare structured values without relying on immutability, Python object
identity, deep-copy behavior, or JSON object-key order. Detect changes to
serialized semantics or ordering.

### Deterministic host tests

Extend `tests/test_shared_agent_patch_contracts.py` unless a reusable helper
makes a small dedicated module clearer.

Cover:

1. **Strict patch installation**
   - expected source/constant match counts;
   - accumulated patched-source ordering;
   - final active function/constant bindings, including the application caller
     bindings in `onyx.chat.process_message`; and
   - loud failure for representative source drift, including duplicate matches.

2. **Main-chat stable policy**
   - citation guidance is present from the first ordinary request when context
     files or supplied citeable tools require it;
   - it remains absent when citations are disabled or no citeable source is
     available;
   - ordinary web-search completion does not change the early prompt;
   - initial `open_url` guidance remains capability-gated and preserves the
     snippet-sufficiency exception and installed tool limits;
   - the ordinary citation reminder is present from the first request when
     applicable and remains equal beside the latest user message, before tool
     history; a representative nonempty task prompt retains its processing,
     content, and position without breaking prefix growth;
   - fixed tool subsets use the same prompt policy, citation applicability
     follows the supplied tools, and forced-tool selection remains functional
     without adding state to track whether the request began forced;
   - a generated-file Python result appends normally without adding
     `FILE_REMINDER` or changing the earlier request prefix;
   - the result still contains exact `response_markdown`, and model-emitted
     file links remain intact through existing normalization and persistence
     contracts; these tests do not prove that a model will include every
     requested artifact;
   - replacement-base prompts are not given new stock citation or `open_url`
     guidance but retain their existing stable Python guidance;
   - file reminders are absent across main-chat branches, while
     task-prompt processing/placement and terminal completion directives remain
     intact; use focused branch tests, not a persona/feature cross-product; and
   - no generated-file reminder preference or model-name branch is introduced.

3. **Deep Research correctness and stability**
   - orchestration and nested prompts do not contain a changing current-cycle
     value;
   - both reasoning and non-reasoning prompt variants format correctly, retain
     their maximum-cycle limit, and have the correct active bindings; use a
     nondefault nested research budget to detect restoration of upstream limits;
   - the redundant first-cycle/post-search reminders are absent;
   - both `open_url` guidance variants match the installed tool name, remain
     gated on tool availability, and preserve the snippet-sufficiency exception;
     assert the prompt-transform contract without freezing unrelated prose; and
   - internal-search tuning follows the tools actually selected, including a
     selected set without internal search.

4. **Tool semantics**
   - repeated scoped construction yields equal ordered tool definitions.

5. **Coding constants**
   - both variants format correctly without changing cycle numbers;
   - the maximum budget remains in both; and
   - the active prompt bindings use the patched constants.

Constant formatting and binding tests cover both reasoning variants.

Keep the existing tests for reasoning preservation, tool-history ordering,
forced finalization, coding fallback, citations, and generated-file links. Do
not duplicate their assertions in a new cache-specific harness. Update the
file-link prompt contract tests to stop expecting a locally rewritten
`FILE_REMINDER` while retaining coverage of `PythonTool.DESCRIPTION`,
`PYTHON_TOOL_GUIDANCE`, `response_markdown`, normalization, persistence, and
replacement-prompt guidance.

### Pinned-image validation

Extend `tests/validate_pinned_api.py` to validate the installed pinned Onyx
image after the full local patch stack is applied:

- final source/constant transforms and active bindings for every changed site,
  including the application caller bindings;
- one additional isolated installation smoke test with chat-tool sharing
  disabled, checking the common transforms and retained upstream tool filtering
  after the full bootstrap; do not repeat behavioral captures for this setting;
- one actual three-request stock/default main-chat capture: the first request
  calls web search, the second calls Python to generate a file, and the third
  completes normally before the cycle limit. Keep citeable tools and `open_url`
  available throughout, with no task prompt. Compare both consecutive request
  pairs for semantic-prefix growth and equal ordered tool definitions. Require
  initial citation guidance and a stable citation reminder before tool history,
  no dynamic web/file reminder, and exact
  generated-file `response_markdown` in the appended tool result;
- a representative default Deep Research capture with at least two consecutive
  investigation requests in each changed layer: orchestration and nested
  research. Assert semantic-prefix growth and equal ordered tool definitions
  in both layers. Capture orchestrator cycles `0` and `1`: the pinned source
  inserts `FIRST_CYCLE_REMINDER` at `cycle == 1`, despite its name. In the nested
  loop, execute a successful web search with `open_url` available, then capture
  the next investigation request to exercise post-search reminder removal.

Use the installed prompt/history builders, the installed
`translate_history_to_llm_format`, and real tool definitions. Intercept at the
LLM boundary after Onyx translation, before provider adaptation, and stub model
responses and external effects such as search, Python execution, and
persistence. Compare the translated serialized messages, including reminder
wrapping, assistant/tool formatting, reasoning fields, and cache markers, plus
the ordered tool definitions and stable request options. A capture of only
`ChatMessageSimple` loop inputs is insufficient.

Serialize every intercepted request immediately into an independent
structured value; retaining references to live history/messages can make an
earlier capture reflect later mutations and hide failures. This is test
bookkeeping, not a production snapshot requirement. Include
reasoning-bearing assistant tool calls in each changed loop's captured sequence
and assert that their reasoning and paired tool results remain in the prefix.

If one representative Deep Research fixture cannot reach both layers without
turning into a synthetic orchestration framework, use one small capture per
changed layer. Fixture organization is flexible; the two-request stability
assertion in each layer is required. Do not add a model/provider matrix,
feature-flag cross-product, replacement-persona matrix, or pinned four-loop suite. The single
main-chat capture above covers both search and generated-file cache regressions;
do not create a second parallel harness for it.

Coding-agent pinned validation is limited to both installed constants, source
shape, and active bindings, supplemented by existing synthetic tool-choice
call-site tests, reasoning-field helper tests, and fallback-output tests.
These do not execute the complete installed coding loop; do not add a
coding-loop harness.

## Validation

Required deterministic and image checks:

```sh
make check
make test-patch-images
```

`make test-patch-images` must use the already-built pinned image and must not
silently pull or substitute one. Build the documented image first if it is
missing.

When the required credentials and a matching stack are available, perform
representative live checks:

1. one stock/default main-chat request on the normal target model that researches
   information requiring page content beyond search snippets, uses Python to
   create at least one requested artifact from that research, and reaches the
   final answer. Require successful web search, useful `open_url` retrieval,
   and correct citations together with the artifact link;
2. one focused Python artifact check on the smallest model the stack intends
   to support, if that is a distinct configured model; this check need not
   repeat the research scenario; and
3. one default Deep Research request that reaches nested research and report
   generation.

For each, confirm normal streaming/final output, retained tool results and
reasoning, expected citations/links, and absence of patch/startup errors. For
the generated-file checks, confirm every user-requested artifact appears once
with the exact returned `response_markdown`. Record provider cached-token
telemetry or provider-rendered token IDs only if already exposed. Compare
equivalent warm requests descriptively; cache hits, token IDs, and latency
reductions are not acceptance gates.

Use the single combined main-chat scenario for live search and file-delivery
validation without an additional harness. Scripted responses in the
deterministic capture prove prompt stability but cannot establish whether a model opens necessary
pages, cites sources correctly, or includes requested artifacts.

Removing current-cycle and progress reminders may change average cycle use or
forced-finalizer frequency even when their substantive guidance is retained.
The representative live checks should look for obvious regressions in those
behaviors. Removing the generated-file reminder may expose file omissions by a
less capable supported model; a reproducible omission is a failed validation.
For a suspected behavioral regression, repeat the same scenario against the
unchanged baseline with the same model and configuration before attributing
it to this patch. Revisit the design if the regression is confirmed. A broader
quality or provider benchmark is not warranted unless the targeted checks show
a problem.

If live checks cannot be run because credentials, funding, private
configuration, or a compatible running stack are unavailable, record exactly
what was omitted and why.

## Documentation Updates

Update:

- `docs/onyx_patch_info.md` with the scoped stock/default main-chat and
  default Deep Research prefix guarantee, unconditional Deep Research prompt
  cleanup, retained snippet-sufficiency exception, generated-file reminder
  removal and retained structural safeguards, the coding constant cleanup, strict patch
  installation, and deliberate boundaries;
- `docs/onyx_patches_upgrade.md` with the changed source anchors, startup
  assertions including application caller bindings, translated-request captures,
  disabled-sharing installation smoke test, and focused live regression checks.
  Require re-auditing that both orchestration prompts already cover the deleted
  first-cycle reminder and that initial web guidance preserves the
  snippet-sufficiency exception and installed tool limits; and
- the existing README patch summary with a short user-facing statement that
  ordinary stock/default chat and default Deep Research avoid common
  mid-investigation prompt mutations.

The README and canonical docs must not promise cache hits or cost savings.
Keep the contract's detailed exclusions in `docs/onyx_patch_info.md`, including
requests with a trailing image-drop notice despite unchanged attachments.
Explain that the existing wrapper places reminders beside the latest user
message before tool history, so stable citation/task reminders can remain
without breaking prefix growth.
Do not describe reconstructed constant reminders as inherently moving suffixes.
Describe newly retrieved documents and generated-file tool results as
ordinary append-only history on the scoped main-chat path. Keep the README
summary brief and link to the canonical patch document for scope and boundaries.

Rewrite the current generated-file documentation in place so it no longer
claims the response requirement is repeated in a post-execution reminder or
that the wrapper patches `FILE_REMINDER`. State that removal applies to all
main-chat branches, including replacement personas, although the prefix
guarantee is narrower. Preserve the documented function description, stable
Python guidance, exact result `response_markdown`,
normalization, persistence, and residual inability to append an artifact the
model omitted. Do not update `docs/request_handling.md` unless implementation
unexpectedly changes the external web-search or `open_url` contract.

Keep this plan current through implementation and review. Once accepted and
implemented, move the accepted design record to `docs/plans/implemented/`
without adding a progress diary; later behavior changes belong in the
canonical owning documents.

## Implementation Sequence

1. Reconfirm the committed baseline, reference checkout, installed pinned image
   source, and current patch composition.
2. Add the focused host contracts specified in Test Coverage.
3. Implement the narrow transforms in patch order with strict preflight and
   final-source validation.
4. Add the three representative loop captures at the translated-request
   boundary, final binding checks, and disabled-sharing installation smoke test.
5. Update canonical patch/upgrade documentation and the scoped README summary.
6. Run `make check` and `make test-patch-images`; run the representative live
   checks when prerequisites are available.

## Completion Criteria

Use this evidence mapping for acceptance:

| Change | Required evidence |
| --- | --- |
| Patch installation and composition | Exact-count/drift host tests, full-bootstrap source and binding checks including application callers, disabled-sharing installation smoke test |
| Main-chat policy and global file-reminder removal | Focused branch contracts, both adjacent pairs in the three-request translated capture, retained file-link tests, combined live search/citation/artifact check and supported small-model artifact check when available |
| Orchestration and nested research cleanup | Both prompt variants and configured budgets, tool/prompt agreement, one two-request translated capture per layer, targeted live Deep Research check when available |
| Coding constant cleanup | Both formatted constants and active bindings, existing helper and synthetic call-site tests |
| Documentation and handoff | Canonical updates listed above, passing `make check` and `make test-patch-images`, explicit blockers for any omitted live checks |
