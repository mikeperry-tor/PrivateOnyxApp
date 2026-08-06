# Deferred Onyx Higher-Layer Inference Error Recovery

> **Status: deferred.** This document is a potential future Onyx-oriented
> design, not current behavior and not the active stack implementation plan.
> The current recovery owner remains the `LitellmLLM.stream` wrapper documented
> in [Wrapper patch information](../../onyx_patch_info.md). There is no separate
> near-term recovery-hardening implementation plan; the requirements below are
> gates and invariants for reconsidering this deferred replacement.
>
> Do not implement this design as a runtime copy or source rewrite of
> `run_llm_step_pkt_generator`. Reconsider it only after the capability gates
> below pass, preferably through an upstream Onyx recovery seam.

## Purpose

Record the semantically preferable long-term location for interrupted-stream
recovery if Onyx exposes a stable orchestration-level extension point.

The LLM-step layer can observe distinctions unavailable at the raw provider
stream boundary:

- raw model output versus citation/custom-processed visible output;
- reasoning and final-answer phase transitions;
- buffered citation and custom-token-processor state;
- native tool-call accumulation and terminal parsing;
- `ChatStateContainer` updates;
- returned `LlmStepResult` and trace state; and
- the packets that reconstruct the saved message.

Those capabilities can make a higher-layer owner architecturally cleaner in
Onyx itself. They do not make a large runtime monkey patch cleaner in this
wrapper repository. Pinned `run_llm_step_pkt_generator` is a large, delicate
generator used by ordinary chat, Deep Research, research agents, and coding
agents. Its useful state lives in local variables and nested generators. An
outer wrapper cannot recover that state after the generator unwinds, while a
local source rewrite would duplicate or reconstruct upstream orchestration.

## Why This Work Is Deferred

Implementation remains deferred until all of these gates pass:

1. **Supported recovery seam:** pinned Onyx exposes a callback, coordinator,
   typed event loop, or comparably narrow hook inside LLM-step stream
   processing. Copying or source-rewriting the complete generator does not
   satisfy this gate.
2. **Typed transport interruption:** the stream boundary distinguishes a
   retryable provider transport interruption after output from citation,
   custom-processor, parser, persistence, cancellation, and application
   failures.
3. **Stable processed-state access:** the seam exposes raw and processed
   answer/reasoning plus citation/custom-processor flush or rollback semantics
   without duplicating their owners.
4. **Strict native-tool gate:** provider-native tool arguments and terminal
   state are validated before kickoff, and an interrupted/invalid batch can be
   discarded without executing a valid sibling.
5. **Cancellation signal:** the coordinator can decline to start a new
   continuation after user cancellation without changing the existing
   disconnect/resume durability contract.
6. **All caller families:** ordinary chat, multi-model chat, Deep Research,
   research agents, coding agents, evals, and callers without a
   `ChatStateContainer` have an explicit supported behavior.
7. **Upgrade stability:** pinned-source validation can protect the narrow seam
   without reconstructing a large function or depending on incidental local
   variable names.

An upstream Onyx implementation is preferred. A derived backend image could be
considered only if it carries a small, source-maintainable change rather than a
runtime function copy and retains the repository's Docker/Podman and upgrade
contracts.

## Corrected Scope

If the gates pass, higher-layer recovery should:

- preserve the existing progress-gated continuation policy;
- use processed LLM-step state when selecting and saving a terminal warning;
- use raw model answer/reasoning only as continuation context;
- preserve final answer text by default;
- keep clean EOF for ordinary answer text compatible with the audited provider
  contract;
- exclude structured output;
- terminate rather than regenerate after any native tool-call delta;
- emit only existing append-only packet types unless a separate product design
  justifies a frontend protocol; and
- keep emitted visible state, returned result, trace projection, persisted
  content, and reload reconstruction semantically consistent.

It should not:

- replace or prune reasoning or answer blocks automatically;
- invent a provisional-narration phase from ordinary answer text;
- regenerate an interrupted native tool draft;
- add an executed-side-effect marker inside one LLM step, because tool
  execution occurs only after that step returns kickoffs;
- duplicate the LLM-step answer, reasoning, citation, processor, parser, or
  tool accumulators in a second recovery state;
- add persisted continuation/provider counters to `ChatMessage`;
- require a new frontend packet or database migration merely to relocate
  recovery; or
- add an absolute continuation count, aggregate deadline, token budget, cost
  budget, or other overall recovery limit while semantic progress continues.

## Recovery-Limit Decision

The future higher-layer owner must preserve this deliberate policy:

- a continuation that produces new model-generated answer or reasoning text
  may authorize another continuation after a later retryable interruption;
- a continuation that fails without new answer or reasoning terminates
  recovery; and
- there is no absolute attempt count or aggregate recovery budget while
  progress continues.

Existing timeouts remain scoped to their individual Onyx, LiteLLM, Teep, or
network invocation. Do not reinterpret one as a cumulative deadline. Recovery
notices, usage, finish markers, empty deltas, and tool deltas never count as
progress.

## Minimal Future State

Do not create the broad duplicate state object proposed by the retired plan.
The existing LLM-step locals remain canonical for processed output, citations,
custom processors, phase, tool accumulation, placements, and tracing.

The recovery seam needs only genuinely new state:

| State | Purpose |
| --- | --- |
| Immutable translated prompt and request controls | Rebuild a continuation without mutating the original prefix or policy. |
| Raw model answer and reasoning | Supply only model-generated continuation context. |
| Continuation count and Onyx stream-invocation count | Apply the progress gate and provide accurately named diagnostics. |
| Current-invocation semantic-progress flag | Decide whether another continuation is authorized. |
| Raw native-tool-delta flag | Prohibit continuation and synthetic completion for an ambiguous tool draft. |
| Terminal finish reason and finalization state | Distinguish completed answer output from a later iterator/accounting failure. |
| Cancellation state, when supplied by Onyx | Prevent a new continuation after an explicit stop. |

Actual Teep upstream attempts remain Teep-owned diagnostics and are not inferred
from these counters.

## Transition Contract

The future seam should implement one pure decision function over typed events,
while leaving output mutation to the existing LLM-step owners.

| Event/state | Decision |
| --- | --- |
| Retryable transport failure before preservable output | Propagate ordinary failure after pinned pre-first-chunk retry handling. |
| Retryable interruption during reasoning, no raw tool delta | Append the existing sanitized reasoning seam notice and continue from raw model reasoning. |
| Retryable interruption during answer, no raw tool delta | Preserve processed visible answer, append the existing sanitized answer seam notice, and continue from raw model output. |
| Continuation failure without new model answer/reasoning | Append the generic terminal partial-answer warning and return a normal partial result only when doing so yields a valid answer state. |
| Any raw native tool delta followed by failure | Discard the unexecuted draft within its existing owner, issue no continuation, and execute no tool. Preserve preceding answer only if the supported seam can remove the draft and return a coherent result atomically. Otherwise propagate ordinary failure. |
| Structured output followed by failure | Propagate ordinary failure. |
| Answer/reasoning terminal finish followed by iterator/accounting failure, no tool delta | Preserve processed output and append the generic finalization warning. |
| Native tool delta followed by any finalization failure | Execute no tool and do not synthesize completion. |
| Clean EOF with ordinary answer text | Retain the audited stock-compatible behavior. |
| Clean EOF with a native tool draft | Apply the strict native-tool terminal/completeness gate; absent proof, execute nothing. |
| User cancellation before another continuation | Start no new continuation and retain Onyx's cancellation-save behavior. |

The decision function must accept only a typed provider transport interruption.
It must never retry an exception from citation processing, a custom token
processor, tool parsing, state updates, packet emission, persistence, or the
coordinator itself.

## Native-Tool Safety Contract

The higher-layer owner must treat permission to execute a side effect as a
stricter contract than ordinary answer completion. Before any native-tool
kickoff reaches `run_tool_calls`, it must prove all of the following through an
audited Onyx/provider seam:

- the tool name is complete;
- arguments are present, completely parsed, and a JSON object;
- a genuinely empty object `{}` remains valid, while missing, truncated,
  malformed, scalar, list, `null`, or failed double-decoding forms are invalid;
- a generated fallback call ID is used only for correlation and is not treated
  as evidence of completeness; and
- the stream ended with a terminal native-tool finish value explicitly
  verified for every supported LiteLLM/provider route.

Reject the entire model-emitted tool batch when any member is invalid so a
valid sibling cannot execute beside an ambiguous call. A clean EOF that is
compatible with ordinary answer text is not sufficient proof for a native
tool draft. Unknown or absent terminal state, an interruption after any raw
tool delta, or an iterator/usage/accounting/cleanup failure after a tool finish
must execute no tool and must not be converted to synthetic completion.

Keep this enforcement at the single Onyx native-tool kickoff boundary. Do not
duplicate it in the recovery coordinator, egress proxy, frontend, or each tool
implementation. Custom `think_tool` processing and inert JSON/XML assistant
text must not weaken or enter this provider-native execution gate.

## Output and Persistence Contract

Use existing append-only `ReasoningDelta` and `AgentResponseDelta` packets and
the existing saved `reasoning_tokens` and message fields. Operator-generated
warnings remain fixed sanitized content so stock WebUI streaming, durable
stream-buffer replay, database save, and reload reconstruct the same visible
values.

Define consistency over projections rather than literal object identity:

- visible answer text;
- visible reasoning text;
- citations and emitted citation numbers;
- executable tool calls;
- sanitized interruption/finalization notice; and
- terminal completion classification.

Packets, `ChatStateContainer`, `LlmStepResult`, trace data, and reloaded message
objects are different representations and need not have byte-identical
serialization or packet sequences.

Do not persist raw exception details, URLs, credentials, prompts, generated
raw content, provider bodies, tracebacks, or operational counters. Raw model
answer/reasoning used for a continuation remains request-local.

User-visible recovery and finalization content must be fixed generic text.
Operator diagnostics may include the accepted exception class, coordinator
continuation count, Onyx stream-invocation count, already-permitted
model/provider identifiers, and a non-secret correlation identifier. They must
not interpolate nested exception text, prompts, generated content, raw tool
arguments, URLs, response bodies, credentials, or tracebacks.

## Prompt and Request Contract

Every continuation must reuse:

- the unchanged translated system/prefix message objects;
- the same LLM instance and configured inference authority;
- original tools and `tool_choice`;
- reasoning effort;
- timeout override;
- maximum output tokens;
- user identity; and
- every other applicable request control.

Append one assistant message containing only model-generated partial answer and
supported historical reasoning, then the fixed continuation instruction. Do
not place recovery notices in the continuation context as if the model emitted
them. Retain the declared/synthetic assistant subtype until upstream
`AssistantMessage` and serialization natively preserve the required reasoning
field.

If the resulting prompt cannot fit the provider's accepted input contract,
preserve the partial response and terminate recovery with a generic notice. Do
not truncate the original prefix, discard system policy, or silently omit
model-generated context to force another request. This is an input-validity
failure, not an overall recovery-attempt budget.

## Accounting

Use these terms consistently:

| Counter | Owner | Meaning |
| --- | --- | --- |
| Coordinator continuation count | Higher-layer coordinator | Additional continuation prompts started after the original stream invocation. |
| Onyx stream-invocation count | Coordinator/transport seam | Calls to pinned `LitellmLLM.stream`, including the original invocation and continuations. |
| Onyx pre-first-chunk attempt count | Pinned `LitellmLLM.stream` | Provider-facing attempts hidden inside one stream invocation under `LLM_FIRST_CHUNK_MAX_RETRIES`. |
| Teep upstream-attempt/failover count | Teep | Actual provider/instance attempts, including Teep-owned failover. |

Do not call an Onyx stream invocation a provider call. The higher layer cannot
derive Teep's counter. Keep all counts operational in logs/traces and Teep
diagnostics; do not add a database migration.

Characterize usage packets for every supported configured inference route
before aggregating trace usage. If each invocation emits one terminal usage
snapshot, expose the sum to the LLM-step generation span while preserving
LiteLLM's existing per-response cost tracking and avoiding a second count of
the aggregate. If a route repeatedly emits cumulative snapshots, define and
test a per-invocation last-snapshot rule first. Unproven usage semantics block
trace aggregation for that route, not the safety contract or continuation
relocation.

## Future Implementation Phases

These phases are non-executable until every deferral gate passes.

### Phase 1: adopt and pin the supported Onyx seam

- Audit the exact upstream API, all call sites, and import timing.
- Add strict source/signature assertions limited to the seam.
- Add the pure transition model without changing behavior.
- Prove that local processor/application exceptions cannot enter recovery.

### Phase 2: move answer and reasoning continuation

- Transfer continuation counting and prompt reconstruction from the low-level
  wrapper into the supported seam.
- Preserve existing append-only packets, notices, and progress semantics.
- Keep the low-level wrapper active until deterministic and pinned-image tests
  prove behavioral equivalence, but ensure only one owner is enabled in any
  running validation configuration.

### Phase 3: handle native-tool interruption conservatively

- Implement or adopt the strict native-tool completion gate at the single
  kickoff boundary before enabling higher-layer recovery.
- Discard only a draft that the seam can remove atomically before kickoff.
- Never regenerate it automatically.
- Fall back to ordinary failure if a coherent partial answer cannot be returned
  without the draft.

### Phase 4: remove the low-level continuation owner

- Remove only continuation ownership from the LiteLLM wrapper.
- Install the higher-layer owner unconditionally in the supported backend
  bootstrap and remove the redundant default-true
  `ONYX_LLM_MIDSTREAM_CONTINUATION_ENABLED` Compose setting and feature gate.
- Retain transport exception normalization, pre-first-chunk upstream behavior,
  reasoning serialization helpers, and native-tool enforcement wherever they
  still have an owner.
- Retain `LLM_FIRST_CHUNK_MAX_RETRIES=1` and the independent
  `ONYX_LLM_NATIVE_TOOL_CALLS_ONLY=true` policy unless a separate reviewed
  change makes their owners unconditional.
- Prove that no direct `llm.stream` caller has lost required behavior.

### Phase 5: canonical documentation

- Update current behavior in `README.md`, `docs/onyx_patch_info.md`,
  `docs/onyx_patches_upgrade.md`, and `docs/resource_minimization.md`.
- Audit inference-routing and internal-security documentation for unchanged
  authority and fail-closed behavior.
- Move this document to `docs/plans/implemented/` only after deterministic,
  pinned-image, Compose, and live validation passes.

## Required Future Coverage

A future relocation must provide deterministic coverage for:

- ordinary chat, multi-model chat, Deep Research, research agents, coding
  agents, evals, and no-state-container callers;
- original invocation plus zero, one, three, and many progress-bearing
  continuations without an absolute cap;
- failure before output and the separately owned pre-first-chunk retry;
- a continuation that fails before semantic progress and authorizes no further
  invocation;
- empty, usage-only, notice-only, finish-only, and tool-only deltas not counting
  as progress;
- unchanged prompt-prefix object identity and preserved tools, `tool_choice`,
  timeout, maximum output tokens, reasoning effort, user identity, and all
  other request controls;
- citation buffering across an interruption without duplicate or lost
  citations;
- custom processors that buffer and reinterpret native tool deltas;
- raw continuation context versus processed visible/persisted output;
- reasoning-to-answer placement with no second frontend reasoning phase;
- cancellation immediately before the next continuation;
- prompt input overflow preserving the original policy and partial answer;
- simultaneous emitted, state-container, returned-result, trace-projection,
  saved-message, and reload-projection consistency;
- ordinary clean EOF without a terminal finish marker;
- answer/reasoning terminal finish followed by finalization failure producing
  a saved generic warning;
- no tool execution after interruption, clean EOF with a tool draft, unknown
  or missing terminal finish, or post-finish finalization failure;
- native-tool arguments covering missing, truncated, malformed, scalar, list,
  `null`, valid `{}`, valid nested, defensive double-encoded, missing-name,
  fallback-ID, and multiple interleaved-call cases;
- one invalid native call rejecting otherwise valid siblings;
- visible inert JSON/XML text remaining non-executable;
- bounded cause/context exception classification and complete exception-detail
  scrubbing;
- distinct continuation, Onyx stream-invocation, pre-first-chunk attempt, and
  Teep diagnostic terminology;
- cumulative trace usage without duplicate cost tracking for every accepted
  provider usage shape;
- stream-buffer reconnect/replay and final database refresh; and
- every continuation retaining the original fail-closed inference authority.

Use the exact pinned Onyx backend image and real LLM-step orchestration in
addition to pure transition fakes. Deterministic tests must not require Internet
access, credentials, or private configuration. A frontend build or database
migration is not required by this design. If later product requirements
introduce either, write a separate plan with its own image, schema,
compatibility, and validation contracts.

## Required Future Validation

Run:

- `make check`;
- `make test-patch-images`;
- effective Compose rendering for affected Docker and Podman lite/full
  selections, including removal of the redundant continuation setting; and
- targeted startup-log inspection proving strict seam installation and exactly
  one continuation owner.

Live validation must include:

- a lite-mode answer interruption and successful continuation;
- a no-progress continuation failure that preserves a coherent partial answer;
- at least three successive progress-bearing continuations, with no assertion
  that the third is a maximum;
- a full-mode citation-bearing interruption and reload;
- a reasoning-bearing response;
- an interrupted native-tool draft and a post-tool-finish finalization failure,
  both with zero tool-runner invocations;
- valid empty-object and nonempty native tool calls still executing normally;
- refreshed-page and in-flight stream-buffer replay consistency for saved
  answer, reasoning, citations, and warnings;
- Teep diagnostics correlated with Onyx stream-invocation logs without claiming
  their counters are equal; and
- configured-route verification that every continuation uses the same
  fail-closed inference authority as the original invocation.

Record provider, funding, fault-injection, or environment limitations exactly.
Do not substitute a state-container-only test for browser/database reload
validation.

## Reconsideration Criteria

Reopen this plan only when one of these is true:

- upstream Onyx publishes a stable recovery seam satisfying all gates;
- a small, maintainable upstreamable Onyx change has been accepted for the
  selected pin; or
- a demonstrated correctness requirement cannot be satisfied safely at the
  existing stream boundary and justifies the added orchestration complexity.

Do not reopen it merely to move code to the conceptually ideal layer. The
change must reduce real ambiguity or enable required behavior without replacing
a narrow wrapper with a fragile reconstruction of upstream orchestration.
