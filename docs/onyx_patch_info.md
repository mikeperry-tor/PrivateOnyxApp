# Onyx patch information

Last updated for Onyx v4.2.5.

This document explains why this wrapper carries local Onyx patches, how those
patches modify Onyx at runtime or install time, and how the same behavior could
be turned into proper upstream merge requests. For line-oriented upgrade checks,
use the companion inventory in
[`docs/onyx_patches_upgrade.md`](onyx_patches_upgrade.md). For operator-facing
setup and troubleshooting of the local document RAG path, use
[`docs/local_docs_rag_search.md`](local_docs_rag_search.md).

Related implementation docs:

- [Local document RAG search](local_docs_rag_search.md) describes the
  `doc-drop-web` connector path, local embedding shim, optional MLX embedding
  server, and RAG-specific diagnostics.
- [Request handling](request_handling.md) describes how web search flows through
  SearXNG and CRW, how `open_url` reaches CRW directly, and how both use the
  prefetch policy plus the conditional CDP shim / Obscura browser path.
- [VPN routing and proxies](vpn_routing_and_proxies.md) describes the
  Compose-level VPN namespace, `ONYX_AGENT_OUTBOUND_PROXY_URL`, and optional teep, Tailscale, and
  code-interpreter routing modes.
- [Internal network security](internal_network_security.md) records the
  restricted component topology, final-hop validation, executor isolation,
  and remaining CONNECT/upstream-proxy risks.

Reference checkouts:

- `reference_repos/onyx` contains the Onyx source used for v4.2.5 review.
- `reference_repos/litellm` contains the LiteLLM source used for v1.89.4
  review.
- `reference_repos/python-sandbox` contains the code-interpreter image source
  used by Onyx's `onyxdotapp/code-interpreter` image.

## Design goals

The wrapper patches are not meant to fork Onyx behavior broadly. They solve a
small set of local deployment requirements that Onyx v4.2.5 does not expose as
configuration:

- Let a private deployment tune tool limits without rebuilding Onyx images.
- Let Deep Research use the current chat Agent's selected tools without
  silently dropping mixed or repeated tool calls from one model response.
- Preserve active-turn assistant reasoning fields across multi-step tool calls
  for OpenAI-compatible private inference providers, with optional preservation
  across all prior turns.
- Keep Onyx's per-call user reminder from trailing after assistant/tool history
  when reasoning fields must remain visible to provider chat templates.
- Keep coding-agent final answer synthesis compatible with OpenAI-compatible
  providers by flattening completed tool history before the final no-tool LLM
  call.
- Optionally preserve prior tool-call outputs across chat turns for research
  workflows that prefer recall over prompt compactness.
- Keep internal-search result count and content budgets small enough for local
  document RAG without relying on obscure upstream env names.
- Keep generated tool descriptions accurate when executor capabilities differ
  from upstream defaults.
- Support restricted proxy-only code-interpreter execution when explicitly
  enabled.
- Use a local OpenAI-compatible embedding server while preserving Onyx's
  model-server HTTP contract.
- Run useful Web and Open URL workflows in lite mode.
- Avoid repeated local PDF downloads and parsing when trusted HTTP validators
  prove the source document has not changed.
- Make Onyx's Docker Compose install and runtime fit the wrapper's container
  engine, proxy, VPN, and sidecar topology.

Upstreamable versions of these changes should keep current Onyx defaults
unchanged. Riskier behavior, especially code-interpreter network access and
trusted HTTP freshness, should remain explicit opt-in configuration.

The wrapper's `ONYX_SECURITY_SSRF_*` env vars are not prerequisites for the
runtime patches themselves. They only seed Onyx's Admin -> Security Hardening
SSRF Protection level for URL-fetching paths such as Web connectors, MCP/OAuth
endpoints, and the fallback `OnyxWebCrawler` provider. The local embedding shim
uses explicit model-server routing instead, and CRW/Obscura browser traffic is
governed by the Compose network/proxy/VPN layout rather than those Onyx SSRF
settings.

## Modification summary

| Area | Onyx service or component | Local mechanism | Upstream shape |
| --- | --- | --- | --- |
| LLM context window override env | `api_server` | Compose maps `ONYX_AGENT_LLM_MAX_TOKENS` to upstream `GEN_AI_MAX_TOKENS`; `sitecustomize` makes it win before DB/provider limits | First-class wrapper/admin override for model context window |
| Native reasoning detection override | `api_server` | Compose passes `ONYX_AGENT_USE_NATIVE_REASONING`; `sitecustomize` can force Onyx `model_is_reasoning_model()` true for wrapper-managed agents | Provider/model reasoning capability override for OpenAI-compatible deployments |
| Assistant reasoning preservation | `api_server` | `sitecustomize` carries active-turn saved/live assistant reasoning into LiteLLM `reasoning_content` fields by default; optional all-history mode preserves older turns too | Native chat-history support for provider reasoning fields |
| Deep Research selected chat-Agent tools | `api_server` | Compose supplies nested-agent cycle, batch, and worker limits; configured `sitecustomize` removes the search-only filter, retains complete ordinary tool batches, assigns distinct nested placements, and bounds execution workers | First-class Deep Research tool selection with separate batch and concurrency controls |
| Chat reminder placement for reasoning preservation | `api_server` | `sitecustomize` keeps Onyx's user-role reminder adjacent to the latest user request instead of trailing after assistant/tool history | Reminder placement that does not invalidate provider reasoning templates |
| GLM automatic tool choice | `api_server` | `sitecustomize` changes forced to automatic tool selection in Coding Agent, Deep Research, nested Research Agent, and explicit chat-tool forcing | Temporary compatibility with the vLLM 0.24.0 GLM parser/structural-tag regression |
| Coding-agent final answer synthesis | `api_server` | `sitecustomize` flattens structured tool history before the no-tool final-answer call and returns recent tool output if finalization still fails | Final-answer path that does not send tool-call protocol messages to non-tool calls |
| Saved tool-result preservation | `api_server` | `sitecustomize` optionally replaces Onyx's cross-message placeholder with saved tool responses | Per-agent/admin setting for how much saved tool output to keep |
| Open URL and web search character budgets | `api_server` | `sitecustomize` rewrites module constants and function defaults | Admin/env settings for per-URL and aggregate tool budgets |
| Internal search content caps | `api_server` | `sitecustomize` optionally wraps result formatting; full compose passes wrapper env aliases | Admin/env settings for per-result and aggregate tool-response budgets |
| Code-interpreter capability text | `api_server` | `sitecustomize` rewrites tool descriptions and prompt constants | Capability-driven tool descriptions generated from actual executor config |
| Lite Open URL availability | Lite `api_server` | `sitecustomize` forces `OpenURLTool.is_available` true | Separate Open URL availability from vector DB availability |
| Web connector PDF freshness | `background` | `sitecustomize` wraps `WebConnector._do_scrape` | Trusted-host HTTP validator freshness policy |
| Code-interpreter executor networking and proxying | `code-interpreter` and executor pods | `sitecustomize` mutates `DockerExecutor._build_run_command` | Supported executor network/proxy configuration in `python-sandbox` |
| Local embedding bridge | `api_server`, `background` | Shim service implements selected model-server endpoints | First-class OpenAI-compatible embedding provider |
| Compose wrapper | Runtime services | Compose `extends`, overrides, sidecars, network namespace | Official compose extension points and documented env knobs |
| Install hooks | Install/upgrade flow | Makefile plus installer wrapper scripts | Installer flags for engine, image tag, config ref, and noninteractive setup |

## LLM context window override env

Local files:

- `onyx/patches/sitecustomize_base/wrapper_env_patches.py`
- `onyx/patches/sitecustomize_base/sitecustomize.py`
- `docker-compose.yaml`
- `.env.wrapper.example`

Onyx source areas:

- `backend/onyx/configs/model_configs.py`
- `backend/onyx/llm/factory.py`
- `backend/onyx/llm/utils.py`

### Why this is needed

Onyx can already override some model context-window detection with
`GEN_AI_MAX_TOKENS`, but the normal chat construction path prefers a stored
`ModelConfiguration.max_input_tokens` value before consulting the provider
lookup path that honors that env var. The wrapper needs
`ONYX_AGENT_LLM_MAX_TOKENS` to act as a true deployment-wide override, for
example when a provider advertises no context limit, advertises a limit that is
too low, or advertises a 1M-token window but the admin wants Onyx to stay below
500k.

### How it works

`docker-compose.yaml` maps `ONYX_AGENT_LLM_MAX_TOKENS` to Onyx's upstream
`GEN_AI_MAX_TOKENS` environment variable for `api_server`.

Leaving `ONYX_AGENT_LLM_MAX_TOKENS` empty passes an empty
`GEN_AI_MAX_TOKENS`. Onyx reads this as `None` because
`backend/onyx/configs/model_configs.py` parses
`int(os.environ.get("GEN_AI_MAX_TOKENS") or 0) or None`, so an empty value has
the same effect as no override.

When `GEN_AI_MAX_TOKENS` is set to a positive integer, the base
`sitecustomize` patch makes that value win before both:

- the stored `ModelConfiguration.max_input_tokens` DB value used by
  `llm_from_provider()`
- the DB/provider/LiteLLM fallback path in
  `get_max_input_tokens_from_llm_provider()`

Changing `ONYX_AGENT_LLM_MAX_TOKENS` therefore only requires restarting the
stack. The model row does not need to be removed or re-synced.

### Upstream merge request shape

Onyx could expose a clearer per-provider or per-model context-window override
in Admin settings and document the precedence against provider-discovered
limits.

## Assistant reasoning preservation

Local files:

- `onyx/patches/sitecustomize_base/wrapper_env_patches.py`
- `onyx/patches/sitecustomize_base/sitecustomize.py`
- `onyx/patches/sitecustomize/sitecustomize.py`
- `docker-compose.yaml`
- `.env.wrapper.example`

Onyx source areas:

- `backend/onyx/chat/models.py`
- `backend/onyx/chat/chat_utils.py`
- `backend/onyx/chat/llm_loop.py`
- `backend/onyx/chat/llm_step.py`
- `backend/onyx/deep_research/dr_loop.py`
- `backend/onyx/tools/fake_tools/research_agent.py`
- `backend/onyx/tools/fake_tools/coding_agent.py`
- `backend/onyx/llm/multi_llm.py`

### Why this is needed

Onyx v4.2.5 parses LiteLLM `reasoning_content` responses and saves reasoning
text as `reasoning_tokens` on assistant messages and tool-call rows. However,
the lightweight history model used for later LLM calls, `ChatMessageSimple`,
does not expose a reasoning field. When Onyx rebuilds prior assistant
tool-call history, it sends only assistant `content` and `tool_calls`, followed
by tool responses.

Reasoning-capable OpenAI-compatible models can require the prior assistant
reasoning to accompany assistant tool-call messages. This is especially
important for teep-backed GLM-5.2 and Kimi/Kimi-K2.6 style models, where
LiteLLM and upstream providers normalize or expect `reasoning_content` around
tool-use turns.

### How it modifies Onyx

The base API-server `sitecustomize` patch, also invoked by the lite
`sitecustomize` path:

- Reads `ONYX_AGENT_USE_NATIVE_REASONING`, which defaults to `true`. When true,
  it narrowly wraps `onyx.llm.utils.model_is_reasoning_model()` so Onyx treats
  the configured chat model as reasoning-capable even if LiteLLM does not know
  the OpenAI-compatible provider/model pair. This disables the synthetic
  `think_tool` path in the coding agent, deep-research orchestrator, and nested
  research agent, causing those agents to use the native-reasoning prompt/tool
  mode. The patch emits a minimal startup line when installed and a minimal
  first-use line per provider/model pair when the hook actually returns forced
  `supports_reasoning=true`, even if `_REASONING_MODE_TRACE` is disabled. Set it
  to `false` to restore upstream/LiteLLM detection.
- Reads `ONYX_AGENT_PRESERVE_TURN_REASONING`, which defaults to `true`, and
  `ONYX_AGENT_PRESERVE_ALL_REASONING`, which defaults to `false`. Set both to
  `false` to leave Onyx's upstream reasoning-field preservation behavior
  unpatched.
- Wraps `chat_utils.convert_chat_history()` so saved `reasoning_tokens` from
  assistant messages and tool-call rows can be attached to reconstructed
  `ChatMessageSimple` assistant messages. With the default turn-only setting,
  this applies only to assistant messages after the most recent user message.
  When `ONYX_AGENT_PRESERVE_ALL_REASONING=true`, it applies to all
  reconstructed assistant messages.
- Patches live tool loops in normal chat, deep research, research-agent calls,
  and coding-agent calls so the current `llm_step_result.reasoning` is attached
  before a follow-up LLM request sees the assistant tool-call message.
- Wraps `llm_step._build_structured_assistant_message()` so reasoning survives
  the transition from `ChatMessageSimple` to Onyx's internal
  `AssistantMessage`.
- Wraps `multi_llm._prompt_to_dicts()` so the LiteLLM request dictionary
  includes top-level `reasoning_content` and `reasoning` aliases for assistant
  messages that carry preserved reasoning. `reasoning_content` matches
  LiteLLM's normalized field, while `reasoning` matches OpenAI-compatible
  streams from providers such as GLM/Tinfoil that LiteLLM maps back into
  `reasoning_content` on response. The patch may carry reasoning internally in
  `provider_specific_fields.reasoning_content`, matching LiteLLM's provider
  fallback convention, but removes that duplicate nested copy before the final
  OpenAI-compatible request dictionary is sent.
- When internal reasoning tracing is enabled, wraps LiteLLM's OpenAI chat
  request transform to emit a metadata-only census of the final message body
  LiteLLM hands to the OpenAI client. This confirms whether
  `reasoning_content` and `reasoning` survived LiteLLM's OpenAI-compatible
  transform without logging raw reasoning text.
- Also wraps outbound `httpx` chat-completions sends to emit the same
  metadata-only census for the serialized JSON request body leaving the
  Onyx/LiteLLM process.

The patch does not synthesize Anthropic `thinking_blocks` or OpenAI Responses
API `reasoning_items`. It preserves Onyx's existing reasoning text as the
LiteLLM/OpenAI-compatible `reasoning_content` field, which is the best fit for
teep's OpenAI-compatible chat completions endpoint.

Onyx's LiteLLM monkey patches cover Ollama reasoning parsing, Responses API
reasoning-summary formatting, Azure Responses API streaming, and Responses API
usage typing. Those patches do not cover the wrapper's Chat Completions
problem: reconstructed assistant tool-call history still lacks a reasoning
field. LiteLLM v1.89.4 recognizes top-level `reasoning_content` and `reasoning`
in message dictionaries, so the wrapper emits both aliases for
OpenAI-compatible Chat Completions.

Onyx chat-history compression is a separate boundary. It summarizes messages
outside the recent verbatim window, represents assistant tool use only by tool
name, and omits tool responses and raw reasoning from the summarizer input.
The wrapper preserves reasoning and tool responses only for messages that
remain verbatim. `ONYX_AGENT_PRESERVE_ALL_REASONING=true` therefore means all
verbatim reconstructed assistant messages; it cannot restore details already
replaced by a persisted chat summary.

For upgrade/debug work, the patch also contains private developer switches in
`onyx/patches/sitecustomize_base/wrapper_env_patches.py`. They are intentionally
not exposed through `.env.wrapper.example`. `_REASONING_MODE_TRACE` defaults to
`false`; temporarily enable it in the patch during controlled upgrade/debug
work to emit metadata-only `reasoning_mode_trace` lines for reasoning-model
detection and the resulting request mode. These lines include
the provider/model pair, caller, `supports_reasoning` result, tool names,
whether `think_tool` was offered, whether a custom think-tool token processor
was installed, reasoning effort, placement indexes, and whether the LLM step
returned reasoning packets/result reasoning. If native reasoning is being used
by the Onyx agent, code agent, deep-research orchestrator, and nested research
agent, the corresponding request-mode logs should show `think_tool_offered=false`
and no `think_tool_processor_*` events; the result logs should show whether
reasoning was actually observed for that call. The same switch also emits
code-agent finalizer metadata showing whether the completed tool transcript was
flattened, the section order sent to the final summarizer, counts of reasoning,
tool-request, and bash-output sections, and short hashes for preserved reasoning
chunks. This is enough to compare an earlier `llm_step_result` reasoning hash
with the finalizer transcript without logging raw reasoning text.
`_REASONING_TRACE_ENABLED` emits
metadata-only Onyx trace lines for reasoning receipt, reattachment, structured
assistant-message conversion, role counts/role ordering, and the final message
dictionaries passed to LiteLLM. It also emits metadata-only LiteLLM transform
boundary lines named `litellm_openai_transform_request` and
`litellm_openai_async_transform_request`, plus outbound HTTP boundary lines
named `httpx_outbound_chat_completions` and
`httpx_async_outbound_chat_completions` when those paths are exercised.
`_CODING_AGENT_FINAL_TRACE_ENABLED` emits one metadata-only line for the
flattened coding-agent final-answer request shape, including role sequence,
tool-argument omission, character counts, and a short transcript hash.
`_REASONING_TRACE_LITELLM_DEBUG_ENABLED` additionally undoes Onyx's LiteLLM
debug suppression and calls LiteLLM's debug hook; only use it during controlled
local validation because LiteLLM debug logs can include full request/response
details.

## Deep Research selected chat-Agent tools

Local files:

- `onyx/patches/sitecustomize_base/wrapper_env_patches.py`
- `onyx/patches/sitecustomize_base/sitecustomize.py`
- `docker-compose.yaml`
- `.env.wrapper.example`

Onyx source areas:

- `backend/onyx/deep_research/dr_loop.py`
- `backend/onyx/prompts/deep_research/research_agent.py`
- `backend/onyx/tools/fake_tools/research_agent.py`
- `backend/onyx/tools/tool_runner.py`
- `backend/onyx/utils/threadpool_concurrency.py`

### Why this is needed

Onyx constructs the current chat Agent's tools, including per-chat
`allowed_tool_ids`, before entering Deep Research. The Deep Research
orchestrator then filters that list to `internal_search`, `web_search`, and
`open_url`. Inside each nested research agent, Onyx also keeps only calls whose
name matches the first tool call in a model response and passes
`max_concurrent_tools=1` to the generic runner. That argument both truncates
the call list to one merged call and limits the thread pool to one worker.

The search-only filter prevents Agent-selected Python, coding-agent, custom,
and MCP tools from reaching nested research. The two call-list limits silently
discard ordinary calls from otherwise valid assistant tool-call messages. The
fixed nested-agent cycle limit is also independent of the normal chat
`MAX_LLM_CYCLES` setting.

### How it modifies Onyx

`docker-compose.yaml` supplies these fixed API-server settings immediately
after `MAX_LLM_CYCLES`:

- `MAX_RESEARCH_AGENT_CYCLES=200` controls the number of LLM/tool cycles in
  each nested research agent and updates the cycle count shown in both nested
  research prompts.
- `MAX_DEEP_RESEARCH_TOOL_CALLS_PER_BATCH=256` is a high fail-closed cutoff for
  the number of tool calls emitted by one LLM response. It is not an ordinary
  workflow limit. If it is exceeded, no call in that response is executed and
  the research branch reports a visible error.
- `MAX_DEEP_RESEARCH_PARALLEL_TOOLS=1` bounds worker threads without truncating
  the accepted call list. The default therefore executes a complete batch
  sequentially.

`ONYX_DEEP_RESEARCH_PROVIDE_CHAT_AGENT_TOOLS=true` is exposed in
`.env.wrapper.example` and passed to the API server by the base Compose file.
When true, the base `sitecustomize` patch:

- passes the already-constructed current chat Agent tool list to nested
  research agents instead of applying Onyx's search-only allowlist;
- removes the first-tool-name filter from the nested research loop;
- reuses Onyx's merging of repeated `internal_search`, `web_search`, and
  `open_url` calls;
- assigns every remaining merged call a distinct reserved `sub_turn_index`
  range, so the existing Deep Research renderer keeps nested tool packets in
  separate UI groups;
- calls the stock generic tool runner with call truncation disabled, while a
  patch-local `ContextVar` independently bounds only the thread-pool worker
  count;
- keeps tool responses in model call order and advances the nested placement
  counter by the original batch size so later LLM cycles cannot reuse a UI
  index; and
- rejects a response that mixes `think_tool` or `generate_report` with any
  other call. Control tools must be called alone rather than causing ordinary
  calls to disappear silently.

The reserved nested-placement stride is 1024. A narrow `CodingAgentTool.run()`
wrapper offsets coding-agent packets into the selected call's reserved range;
coding-agent calls are serialized around that emitter mapping even if the
general worker limit is raised. This prevents the coding agent's own substeps,
which start at zero upstream, from colliding with sibling Deep Research tools.

The patch validates the expected prompt cycle fragment, Deep Research source
blocks, tool-call merge/truncation logic, and thread-pool behavior at startup.
With `WRAPPER_PATCH_STRICT=true`, any mismatch stops API-server startup. Setting
`ONYX_DEEP_RESEARCH_PROVIDE_CHAT_AGENT_TOOLS=false` restores the upstream tool
filter and call handling while retaining the configured nested-agent cycle
limit.

The base API-server environment and patch mount are inherited by full, lite,
Docker, and Podman Compose layering. No mode-specific override is required.
Available tools still depend on the selected Agent, per-chat tool toggles,
component availability, and mode; for example, lite mode has no vector-backed
local-document search. Onyx's automatically injected memory tool is outside
the per-chat `allowed_tool_ids` filter and remains available when user memory
is enabled. The nested runner still supplies no chat-file list or user-memory
context to tool overrides, so Python file inputs and memory-aware behavior are
not added by this patch.

### Upstream merge request shape

Onyx should let Deep Research consume the tool set already selected for the
chat Agent, separate maximum accepted calls from maximum execution workers,
preserve a response for every accepted tool-call ID, and assign unique nested
placements. Control tools should have an explicit mixed-batch policy. Nested
cycle and timeout settings should be first-class configuration rather than
unrelated hardcoded prompt and loop constants.

## Chat reminder placement for reasoning preservation

Local files:

- `onyx/patches/sitecustomize_base/wrapper_env_patches.py`
- `onyx/patches/sitecustomize_base/sitecustomize.py`

Onyx source areas:

- `backend/onyx/chat/llm_loop.py`

### Why this is needed

Onyx v4.2.5 can add a per-call `USER_REMINDER` message for citation, URL,
file-link, persona, or final-answer guidance. Onyx constructs that reminder as
the final message in the LLM request and `llm_step` serializes it as an
OpenAI-compatible `role="user"` message wrapped in `<system-reminder>` tags.

For teep/Tinfoil GLM-5.2 through the OpenAI-compatible provider path, a final
user-role message after assistant tool-call and tool-response messages causes
the provider chat template to discard the prior assistant reasoning fields for
that turn. Onyx and LiteLLM can therefore send the preserved
`reasoning_content` fields correctly, while the downstream template still
removes them before model execution.

### How it modifies Onyx

This change is applied by default by the base API-server `sitecustomize` patch.
The implementation still has an internal developer switch,
`_REASONING_REMINDER_REORDER_ENABLED`, so upgrade debugging can compare against
upstream ordering, but the wrapper default is `true`.

When enabled, the patch rewrites
`llm_loop.construct_message_history()` so any `USER_REMINDER` is appended
immediately after the most recent real user message and before
`messages_after_last_user` assistant/tool history. The reminder remains a
separate Onyx `USER_REMINDER` and still becomes a user-role
`<system-reminder>` message in the final LiteLLM request. Only its position
changes:

- upstream order:
  `[system], history_before, last_user, assistant/tool messages, reminder`
- wrapper order:
  `[system], history_before, last_user, reminder, assistant/tool messages`

The patch intentionally keeps the reminder instead of suppressing it, because
Onyx uses these reminders for important answer-format and workflow nudges.
If a future provider template also rejects adjacent user-role messages, the
next upgrade investigation should consider merging the reminder text into the
latest user message rather than dropping it outright.

### Upstream merge request shape

Onyx should add first-class reasoning fields to its lightweight chat-history
models and structured assistant-message model, then copy saved/live reasoning
into those fields anywhere assistant messages with tool calls are constructed.
The upstream implementation should preserve provider-specific constraints:
OpenAI-compatible providers should receive `reasoning_content`, Anthropic
should only receive valid signed thinking blocks, and Responses API paths
should keep their native reasoning item shape.

Onyx should also avoid emitting reminder messages after assistant/tool history
for providers whose chat templates treat a trailing user turn as the start of a
new reasoning segment. A provider-aware upstream implementation could either
place reminders next to the triggering user request or merge them into that
request under the existing `<system-reminder>` convention.

## Saved tool-result preservation

Local files:

- `onyx/patches/sitecustomize_base/wrapper_env_patches.py`
- `onyx/patches/sitecustomize_base/sitecustomize.py`
- `docker-compose.yaml`
- `.env.wrapper.example`

Onyx source areas:

- `backend/onyx/chat/chat_utils.py`
- `backend/onyx/prompts/chat_prompts.py`

### Why this is needed

Onyx v4.2.5 reconstructs previous assistant tool-call history with the tool
call name and arguments, but replaces every non-image tool response with the
fixed placeholder `This tool call completed but the results are no longer
accessible.` This keeps future prompts compact, but it means follow-up turns
cannot inspect raw prior `internal_search`, `web_search`, `open_url`,
code-interpreter, code-agent, custom tool, or MCP tool output unless the
assistant's final answer already captured it.

The wrapper defaults to preserving saved tool responses because this deployment
is tuned for multi-turn research workflows where follow-up turns often need to
inspect earlier tool output.

### How it modifies Onyx

By default, `ONYX_AGENT_PRESERVE_TOOL_RESULTS=true` and the base
`sitecustomize` patch:

- Replaces `chat_utils._build_tool_call_response_history_message()` so saved
  `tool_call_response` text is returned for all non-image tools instead of the
  placeholder.
- Keeps upstream image-generation metadata behavior unchanged.
- Wraps `chat_utils.convert_chat_history()` to recompute token counts for
  `TOOL_CALL_RESPONSE` messages, since upstream normally assigns non-image
  placeholder responses a small fixed token estimate.

Set `ONYX_AGENT_PRESERVE_TOOL_RESULTS=false` to restore upstream placeholder
behavior.

### Upstream merge request shape

Onyx could expose this as a per-agent or admin setting with an explicit prompt
budget warning. A more refined upstream implementation could preserve only
selected tool classes, compress large tool outputs, or apply per-tool size
caps before carrying them into later turns.

## Open URL and web search character budgets

Local files:

- `onyx/patches/sitecustomize_base/wrapper_env_patches.py`
- `onyx/patches/sitecustomize_base/sitecustomize.py`
- `docker-compose.yaml`

Onyx source areas:

- `backend/onyx/tools/tool_implementations/web_search/utils.py`
- `backend/onyx/tools/tool_implementations/open_url/open_url_tool.py`

### Why this is needed

Onyx v4.2.5 hardcodes the amount of page text that `web_search` and `open_url`
can return to the LLM. Those defaults are reasonable for many hosted
deployments, but they are too small for local research tasks that intentionally
feed longer documents, local manuals, or source references through Onyx tools.

The wrapper needs to raise or effectively remove those limits from environment
configuration, without rebuilding the upstream Onyx backend image.

### How it modifies Onyx

The base `sitecustomize` module imports Onyx's web-search and open-url modules
at interpreter startup. When `ONYX_OPEN_URL_MAX_CHARS_PER_URL` or
`ONYX_OPEN_URL_MAX_TOTAL_CHARS` is set, it:

- Replaces `web_search.utils.MAX_CHARS_PER_URL`.
- Rewrites default arguments on truncation helper functions that captured the
  old constant at function definition time.
- Replaces `open_url_tool.MAX_CHARS_ACROSS_URLS`.
- Rewrites the default argument on the Open URL section-to-LLM formatting
  helper.

Setting a limit to `0` means "effectively unlimited" and is implemented as a
very large integer budget.

### Upstream merge request shape

This should become regular Onyx configuration instead of a monkey patch.
Possible options:

- `ONYX_OPEN_URL_MAX_CHARS_PER_URL`
- `ONYX_OPEN_URL_MAX_TOTAL_CHARS`
- Equivalent admin preferences or per-assistant tool settings

The implementation should read the configured values at call time or pass them
through tool configuration, rather than relying on module constants captured as
default parameters. Tests should cover default behavior, custom limits, and the
"unlimited" value if that is accepted upstream.

## Internal search content caps

Local files:

- `onyx/patches/sitecustomize_base/wrapper_env_patches.py`
- `onyx/patches/sitecustomize_base/sitecustomize.py`
- `docker-compose.full.yml`
- `.env.wrapper.example`

Onyx source areas:

- `backend/onyx/tools/tool_implementations/utils.py`
- `backend/onyx/server/features/search/api.py`
- `backend/onyx/mcp_server/tools/search.py`

### Why this is needed

Onyx v4.2.5's internal search result payload is section/chunk content, not a
short excerpt. The agent-facing `internal_search` tool, the `/search` API, and
the MCP `search_indexed_documents` tool all ultimately forward the LLM-facing
search JSON. That JSON uses the selected section's `combined_content`, and a
section may contain merged adjacent chunks or chunks added by Onyx's context
expansion flow.

The wrapper intentionally leaves Onyx's candidate-section and final-section
selection behavior alone. Those paths involve retrieval scores, LLM section
selection, neighbor expansion, and upstream section-count constants;
overriding them locally made the behavior harder to reason about.

### How it modifies Onyx

Full mode passes optional wrapper settings to `api_server`:

- `ONYX_RAG_INTERNAL_SEARCH_MAX_CONTENT_CHARS_PER_RESULT`
- `ONYX_RAG_INTERNAL_SEARCH_MAX_TOTAL_CONTENT_CHARS`

Empty or `0` means no wrapper cap. If either value is positive, the base
`sitecustomize` patch:

- Wraps `convert_inference_sections_to_llm_string()` so each result's
  `content` field and the aggregate returned content can be capped after Onyx
  has merged and expanded sections.
- Replaces the formatter reference imported into `search_tool.py`, since that
  module imports the helper by name.

### Upstream merge request shape

This should become first-class Onyx configuration for per-result and aggregate
tool-response content budgets. The budget should be applied after context
expansion, because expansion is where a selected section can grow past the
nominal chunk count.

Tests should cover the chat `internal_search` tool, `/search`, and MCP
`search_indexed_documents`, because all three paths can expose the same
oversized content.

## GLM automatic tool choice

Local files:

- `onyx/patches/sitecustomize_base/wrapper_env_patches.py`
- `onyx/patches/sitecustomize_base/sitecustomize.py`
- `onyx/patches/sitecustomize/sitecustomize.py`

Onyx source area:

- `backend/onyx/tools/fake_tools/coding_agent.py`
- `backend/onyx/deep_research/dr_loop.py`
- `backend/onyx/tools/fake_tools/research_agent.py`
- `backend/onyx/chat/llm_loop.py`

### Why this is needed

Onyx uses `ToolChoiceOptions.REQUIRED` in the internal Coding Agent loop, Deep
Research orchestrator, nested Research Agent, and an explicit top-level chat
request that forces one selected tool.

Unfortunately, vLLM 0.24.0 moved GLM-5.x to the unified streaming parser and
structural-tag engine. In doing so it removed the vLLM 0.23.0 GLM request
adjustment that explicitly skipped structured decoding for required or named
tool choice because GLM emits XML tool calls. The affected GLM-5.2 service
accepts the request as HTTP 200, starts its SSE stream, and then emits an
internal error before a tool call. Teep therefore sees a successful HTTP stream
while LiteLLM raises `MidStreamFallbackError`.

### How it modifies Onyx

The base API-server patch wraps the step-function references bound separately
inside the Coding Agent, Deep Research orchestrator, nested Research Agent, and
normal chat-loop modules. Calls using `ToolChoiceOptions.REQUIRED` are changed
to `ToolChoiceOptions.AUTO`; `AUTO` and `NONE` calls pass through unchanged.
This module-local approach avoids changing unrelated callers of the shared LLM
step functions.

Coding Agent ordinary text takes the existing final-synthesis path. Later Deep
Research orchestrator no-tool cycles generate the final report, while a
first-cycle no-tool result remains a loud upstream error. Nested Research Agent
no-tool output follows its normal cycle/finalization behavior. For an explicit
top-level forced tool, Onyx still sends only that selected tool, but the model
may return ordinary text instead of invoking it.

Startup validation requires exactly one forced-tool site in each owner
function and fails in strict mode on drift. Wrapping module-bound step
functions, rather than rebuilding loops, also composes with the later
reasoning-preservation source patches.

### Upstream merge request shape

The preferred fix is in vLLM: restore the GLM required/named-tool safeguard or
make its new structural-tag path compatible with GLM XML tool output, with an
end-to-end streaming test. Remove this wrapper patch after the deployed GLM
service contains that fix.

## Code-interpreter capability descriptions

Local files:

- `onyx/patches/sitecustomize_base/wrapper_env_patches.py`
- `docker-compose.yaml`
- `docker-compose.code-interpreter-network.yml`

Onyx source areas:

- `backend/onyx/tools/tool_implementations/python/python_tool.py`
- `backend/onyx/prompts/tool_prompts.py`
- `backend/onyx/tools/tool_implementations/bash/bash_tool.py`
- `backend/onyx/coding_agent/mock_tools.py`
- `backend/onyx/prompts/coding_agent/coding_agent.py`
- `backend/onyx/tools/fake_tools/coding_agent.py`

### Why this is needed

Onyx describes the Python tool, Bash tool, and coding agent as running without
network access when `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=false`. That matches
code-interpreter 0.4.4's default executor network, `none`.

The wrapper can explicitly enable executor networking through a dedicated
internal network and policy proxy. When enabled, upstream descriptions become
harmful: the LLM is told not to use network operations even though network
access is available and expected. The inverse would also be dangerous, so the
text must track actual executor capabilities.

### How it modifies Onyx

When `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true` is present in `api_server`, the base
patch rewrites LLM-facing text for:

- `PythonTool.DESCRIPTION`
- `PYTHON_TOOL_GUIDANCE`
- `BashTool.DESCRIPTION`
- Coding-agent bash tool metadata
- Coding-agent system prompts

The replacement text says that HTTP/HTTPS access is available only through a
restricted proxy, direct sockets and internal/private targets are blocked, and
direct search-engine URLs are blocked. It does not advertise CRW, SearXNG,
CDP, or other stack endpoints. Python package limitations and the code-agent
recommendation remain aligned with the upstream tool roles.

The patch checks exact upstream string matches before claiming success. The
wrapper runs these patches in strict mode, so missing expected strings or
changed helper signatures fail startup instead of silently leaving stale tool
text in place.

The network-description patch must run before the reasoning-preservation patch:
reasoning preservation imports `onyx.tools.fake_tools.coding_agent`, which binds
the coding-agent prompt constants by value. If the order is reversed, startup
logs can claim that `CODING_AGENT_PROMPT` was rewritten while the imported
coding-agent module still tells the LLM that the sandbox is network-isolated.

The wrapper also patches the coding-agent final-answer step. Onyx passes the
completed coding-agent transcript, including saved assistant `tool_calls`
and `TOOL_CALL_RESPONSE` messages, into a final `ToolChoiceOptions.NONE` LLM
call. Some OpenAI-compatible upstreams accept that structure during the active
tool loop but reject it when the request no longer declares tools.

The wrapper wraps `_generate_final_answer()` so that, before the no-tool final
summarizer runs, completed tool-call history is converted into a single
plain-text user-role transcript message. For this flattened path, the wrapper
also builds the final two-message request directly instead of calling
`construct_message_history()`: the transcript and
`USER_FINAL_ANSWER_QUERY` reminder are merged into one user message after the
coding-agent final-answer system prompt. It calls the LLM stream interface
directly with no `tools` argument and with reasoning effort disabled, because
some OpenAI-compatible GLM/Tinfoil paths reject a no-tool finalizer request that
still carries an empty `tools: []` member plus no-tool/tool-choice metadata.
Bash command requests, bash outputs, and preserved assistant reasoning remain
visible to the final model in chronological plain text. Reasoning is inserted
immediately before the corresponding tool-request section so the summarizer sees
the same think-before-act ordering as the active code-agent loop. If the
code-agent stops because an LLM step produced no tool calls, that terminal
assistant answer and reasoning are appended to the in-memory history before the
summarizer runs, so final no-tool reasoning is represented as an ordered
plain-text assistant reasoning section instead of being dropped. No
`role="tool"` messages, assistant `tool_calls`, repeated assistant-only
transcript messages, trailing separate user-role reminder, structured preserved
reasoning fields, or empty tool-definition arrays are sent for that synthesis
call. When `_REASONING_MODE_TRACE` is temporarily enabled, it emits
`coding_agent_final_answer_history_flattened` and
`coding_agent_final_summarizer_request` events for this path; both events include
metadata-only section ordering and counts so upgrades can verify that reasoning
sections precede the matching tool-request sections.

During Onyx upgrades, re-check whether this is still necessary by inspecting
`backend/onyx/tools/fake_tools/coding_agent.py`. If `_generate_final_answer()`
still builds `final_history` from structured coding-agent history and then calls
`run_llm_step_pkt_generator()` with `tool_definitions=[]` and
`ToolChoiceOptions.NONE`, the flattening patch is still relevant for
OpenAI-compatible providers that reject tool protocol messages or empty
tool-definition arrays in a no-tools request. Confirm the upstream function
still contains the exact `"LLM failed to produce a final answer"` guard used by
the wrapper's startup validation, and confirm the wrapper still patches before
code-agent execution. Also confirm the final flattened request has exactly one
system message and one user message, no `tools` member, no `tool_choice` member,
and any preserved assistant reasoning appears only as ordered plain-text
transcript evidence.

Upstream also catches any exception in `run_coding_agent_call()` and returns
`None`, including cases where bash commands succeeded but the final no-tool LLM
summarization call failed. The wrapper keeps a fallback around
`_generate_final_answer()` so finalization failures return the recent collected
tool output with a clear diagnostic instead of dropping the entire coding-agent
result.

### Upstream merge request shape

Onyx should generate tool descriptions from a capability model rather than
hardcoded assumptions. Useful capability fields include:

- Executor network mode: disabled or restricted proxy-only.
- Whether Python package installation is supported.
- Which package managers or network commands are expected to work.
- No local service hints unless a separately reviewed gateway is added later.

The API server and code-interpreter service should share the same source of
truth. A merge request could start with static env-driven capabilities, then
later grow into a code-interpreter capability endpoint.

## Lite Open URL availability

Local files:

- `onyx/patches/sitecustomize/sitecustomize.py`
- `docker-compose.lite.yml`

Onyx source areas:

- `backend/onyx/tools/tool_implementations/open_url/open_url_tool.py`
- `backend/onyx/tools/tool_constructor.py`
- `backend/onyx/server/query_and_chat/session_loading.py`

### Why this is needed

Lite mode disables the vector DB and removes full-mode dependencies. The wrapper
still wants live web and Open URL workflows in lite mode, especially for chat
and research against external or local web pages.

The lite bootstrap also applies every base API-server helper relevant without a
vector database: context-window override, Open URL budgets, native reasoning,
capability text, reasoning preservation, coding-agent finalization, and saved
tool-result preservation. Internal-search caps remain full-mode-only.

In Onyx v4.2.5, `OpenURLTool.is_available` is coupled to vector DB availability.
That disables a tool that can still be useful as a live fetch and summarization
tool.

### How it modifies Onyx

The lite `sitecustomize` patch imports the applicable base helpers, then forces
`OpenURLTool.is_available` to return `True`.

This changes tool exposure only. It does not add vector DB functionality or make
indexed retrieval available in lite mode.

### Upstream merge request shape

Onyx should separate:

- Whether live URL fetching is configured and allowed.
- Whether indexed document retrieval is available.
- Whether vector DB backed workflows are available.

`OpenURLTool` could degrade gracefully when vector DB is disabled, or Onyx could
expose a lite-mode tool allowlist. Tests should cover lite mode with Open URL
enabled and no vector DB.

## Background Web connector PDF freshness

Local files:

- `onyx/patches/sitecustomize_background/sitecustomize.py`
- `docker-compose.full.yml`

Onyx source areas:

- `backend/onyx/connectors/web/connector.py`
- `backend/onyx/connectors/models.py`
- `backend/onyx/db/models.py`

For local document-drop setup and troubleshooting, see
[Local Document RAG Search](local_docs_rag_search.md#local-document-rag-search).
For the line-oriented upgrade inventory, see
[Background Web connector PDF freshness patch](onyx_patches_upgrade.md#background-web-connector-pdf-freshness-patch).

### Why this is needed

The wrapper exposes a local, read-only document drop over HTTP so Onyx's Web
connector can ingest local PDFs. These PDFs are often large and mostly static.
Re-downloading and re-parsing every unchanged file wastes time and can make
local indexing feel broken or noisy.

Onyx v4.2.5 intentionally does not trust `Last-Modified` for web PDFs, which is
a good default for arbitrary public web pages. The wrapper has a narrower case:
trusted local hosts where `Last-Modified` and `Content-Length` are stable
validators from a controlled file server.

### How it modifies Onyx

The background `sitecustomize` patch wraps `WebConnector._do_scrape`.

For allowlisted hosts only, it:

- Performs a HEAD request before scraping a PDF.
- Reads `Last-Modified` and `Content-Length`.
- Treats HTTP 401, 403, and 404 from the trusted preflight as terminal
  unreadable/missing PDF states and returns a wrapper skip sentinel instead of
  letting Onyx parse the error body as a PDF. Upstream Web connector code uses
  4xx/5xx page responses as the "skip this URL" signal; the wrapper applies
  that same intent to the direct PDF download path.
- Compares those validators to metadata stored on the DB document.
- If the validators and `doc_updated_at` match, returns an empty-section
  `ScrapeResult` that tells Onyx the document is unchanged.
- Marks unchanged and unreadable sentinels with wrapper `doc_metadata` and
  patches Onyx's document update gate so forced/targeted reindex paths do not
  accidentally index those empty sentinels as empty documents.
- If the PDF is scraped normally and its content hash matches the existing DB
  document, seeds freshness metadata so future runs can skip the download.
- If the parsed content hash differs, allows Onyx's normal re-index path.

Full-mode Compose sets the internal freshness allowlist to localhost addresses.
The patch still reads `ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_HOSTS` and
`ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_ENABLED` for upgrade/debug overrides, but
those are not part of the user-facing `.env.wrapper` surface.

The patch stores wrapper metadata keys:

- `_wrapper_http_freshness_version`
- `_wrapper_http_last_modified`
- `_wrapper_http_content_length`
- `_wrapper_http_freshness_source`
- `_wrapper_http_freshness_unchanged` on pre-download skip sentinels only
- `_wrapper_http_freshness_unreadable` on terminal HTTP-status skip sentinels
  only
- `_wrapper_http_status` on terminal HTTP-status skip sentinels only

By default, the patch logs startup status and one-time warnings for unexpected
conditions such as missing validators, HEAD failures, sentinel mismatches, or
indexing-patch failures. The internal
`ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_DEBUG=true` override enables per-document hit
and miss details while validating patch behavior.

### Upstream merge request shape

This should become a Web connector freshness policy, not a wrapper metadata
hack. The important upstream distinction is trust:

- Public web default: keep current cautious behavior.
- Trusted hosts or connectors: allow HTTP validator based freshness.

Useful options:

- Trusted-host allowlist.
- Validator set: `ETag`, `Last-Modified`, `Content-Length`.
- Whether validator matches can skip download before parsing.
- Observability logs for hit, miss, and fallback reasons.

A merge request should include tests with a local static HTTP server:

- First ingest indexes the PDF.
- Second ingest with unchanged validators skips download or parsing.
- Changed content with changed validators re-indexes.
- Missing validators fall back to current behavior.

## Code-interpreter executor networking and proxying

Local files:

- `onyx/patches/sitecustomize_code_interpreter/sitecustomize.py`
- `docker-compose.code-interpreter-network.yml`
- `docker-compose.yaml`

Python-sandbox source area:

- `reference_repos/python-sandbox/code-interpreter/app/services/executor_docker.py`

### Why this is needed

Onyx's code-interpreter source, from `python-sandbox`, defaults executor pods to
Docker network `none`. That is a strong and sensible hosted default.

This wrapper optionally gives generated Python and bash restricted HTTP/HTTPS
egress without placing untrusted pods in the stack routing namespace.

The wrapper uses code-interpreter 0.4.4's
`PYTHON_EXECUTOR_DOCKER_NETWORK` setting to choose the executor container
network before command construction.

### How it modifies Onyx

When `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`,
`docker-compose.code-interpreter-network.yml` sets
`PYTHON_EXECUTOR_DOCKER_NETWORK=onyx-code-interpreter-executor`. The internal
network contains executor pods and one egress bridge. The code-interpreter
container also loads a `sitecustomize` patch that:

- Patches `DockerExecutor._build_run_command`.
- Requires the dedicated named network and rejects `container:*` and `host`.
- Requires `ONYX_AGENT_EXECUTOR_HTTP_PROXY_URL`.
- Injects upper/lowercase HTTP proxy variables pointing only at the bridge.
- Injects executor-only `NO_PROXY=127.0.0.1,localhost,::1`.

The trusted code-interpreter control service still shares `netns-holder` to
serve Onyx and spawn containers through the Docker socket. Executor pods do
not inherit it. `ONYX_AGENT_OUTBOUND_PROXY_URL` is consumed only by final-hop
policy proxies and is never injected into executor pods. With network access
disabled, the patch makes no changes and upstream `network=none` remains.

When active, the patch injects `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and
their lowercase variants pointing at
`http://executor-egress-bridge:3128`. That gives Python `urllib`,
`requests`, `httpx`, curl, git, and similar clients an ordinary HTTP proxy
endpoint while the sidecar adapts upstream egress to the configured
`ONYX_AGENT_OUTBOUND_PROXY_URL` scheme, including SOCKS.

The executor policy still blocks configured search-engine hosts, so the
code-interpreter path should not expect direct access to those search pages
through the injected proxy variables. With
`ONYX_AGENT_ALLOW_HTTP_URLS=false`, it also blocks plain `http://` requests
from executor clients. Raw sockets cannot bypass the proxy because the
executor network has no direct route. CONNECT to port 80 is also rejected in
this mode; other CONNECT ports remain opaque. See
[VPN routing and proxies](vpn_routing_and_proxies.md#code-interpreter)
for the service-level routing behavior.

## Local embedding shim

Local files:

- `onyx/local_embedding_shim.py`
- `docker-compose.full.yml`
- `Makefile`

Onyx source areas:

- `backend/model_server/*`
- `backend/shared_configs/model_server_models.py`
- `backend/onyx/natural_language_processing/search_nlp_models.py`
- `backend/onyx/indexing/embedder.py`
- `backend/onyx/utils/gpu_utils.py`

For local/custom embedding setup, query prefix behavior, and diagnostics, see
[Local Document RAG Search](local_docs_rag_search.md#embedding-shim). For
upgrade checks against Onyx's model-server contract, see
[Local embedding shim](onyx_patches_upgrade.md#local-embedding-shim).

### Why this is needed

Onyx expects embedding calls to go through its model-server API, especially
`/encoder/bi-encoder-embed`. Many local embedding servers, including
OpenAI-compatible MLX servers, expose `/v1/embeddings` instead.

The wrapper wants to use local embeddings, often on Apple Silicon, without
pretending that the local server implements the full Onyx model-server API and
without rebuilding Onyx.

Older notes may refer to `local_embedding_sim.py`; the checked-in file is
`onyx/local_embedding_shim.py`.

### How it modifies Onyx

The shim does not import or patch Onyx directly. Instead, full-mode compose
points Onyx's model-server environment variables at the shim:

- `MODEL_SERVER_HOST=127.0.0.1`
- `MODEL_SERVER_PORT=9101`
- `INDEXING_MODEL_SERVER_HOST=127.0.0.1`
- `INDEXING_MODEL_SERVER_PORT=9101`

The shim runs in the shared network namespace, so `127.0.0.1:9101` resolves for
both `api_server` and `background`.

The shim implements the endpoints Onyx needs for local embedding startup and
embedding requests:

- `GET /health`
- `GET /api/gpu-status`
- `POST /encoder/bi-encoder-embed`

It translates Onyx embedding payloads into OpenAI-compatible embedding requests:

- Onyx `texts` becomes OpenAI `input`.
- Onyx or wrapper model names become OpenAI `model`.
- Query and passage prefixes come from Onyx request fields or wrapper env.
- Responses are translated back into `{"embeddings": ...}`.

Onyx's agent-facing `internal_search` tool uses the normal Onyx search
pipeline, not a separate Web connector HTTP path. In v4.2.5,
`SearchTool._run_search_for_query()` calls `search_pipeline()`, which calls
`search_chunks()`, which embeds each query through
`get_query_embedding()`/`EmbeddingModel.encode()`. Because full mode points
`MODEL_SERVER_HOST`/`MODEL_SERVER_PORT` at the shim, a failed query embedding
surfaces to the agent as an `internal_search` tool failure even when the
document-drop HTTP server and indexed Web connector documents are healthy.

The shim keeps a small pool of upstream HTTP connections to the local
OpenAI-compatible embedding server. Some local embedding servers close idle
keep-alive connections without the client knowing. Reusing one of those stale
sockets raises a low-level transport error such as `Remote end closed
connection without response`; Onyx then wraps the resulting 502 as
`HTTP error occurred - response is None.` To avoid making transient stale
connection reuse visible to `internal_search`, the shim closes and replaces a
connection on `OSError`, `TimeoutError`, or `http.client.HTTPException`, logs
`upstream_connection_retry`, and retries the embedding request once on a fresh
socket. HTTP status errors from the upstream server are not retried; they remain
visible.

It intentionally returns 501 for model-server features it does not implement:

- `POST /encoder/cross-encoder-scores`
- `POST /custom/query-analysis`

That keeps unsupported rerank and query-analysis calls visible instead of
silently producing fake results.

### Upstream merge request shape

Onyx could support OpenAI-compatible embedding providers directly. Useful
configuration:

- Embedding provider type: Onyx model server or OpenAI-compatible.
- Base URL and API key.
- Served model name override.
- Separate query and passage prefixes.
- Optional normalization and dimensions settings if supported.
- Separate routing for embeddings, reranking, and query analysis.

The key design point is that embeddings should not require a server to mimic
the entire Onyx model-server surface. Reranking and query analysis should be
independently configurable or explicitly disabled.

## Docker Compose wrapper modifications

Local files:

- `docker-compose.yaml`
- `docker-compose.full.yml`
- `docker-compose.lite.yml`
- `docker-compose.podman.yml`
- `docker-compose.podman-full.yml`
- `docker-compose.code-interpreter-network.yml`
- `docker-compose.proxy.yml`

Onyx source area:

- `reference_repos/onyx/deployment/docker_compose/docker-compose.yml`
- `reference_repos/onyx/deployment/docker_compose/docker-compose.onyx-lite.yml`

The RAG-specific sidecars described here are covered operationally in
[Local Document RAG Search](local_docs_rag_search.md#web-connector-server), and
line-oriented full-mode compose checks live in
[Full mode](onyx_patches_upgrade.md#full-mode-compose).

### Why this is needed

The wrapper runs Onyx as part of a larger local system with:

- A shared network namespace.
- VPN and proxy egress.
- Search and browser sidecars.
- Optional Tailscale and Teep routing.
- Local document-drop services.
- Local embedding services.
- Docker and Podman compatibility layers.
- Full and lite runtime profiles.

Upstream Compose files are a deployment product, not a library API. The wrapper
therefore uses Compose `extends` and override layers to keep close to upstream
while changing the pieces needed for the local topology.

### How it modifies Onyx

The base compose wrapper changes the runtime shape of core Onyx services:

- `api_server` joins the shared namespace, receives wrapper env, mounts
  `sitecustomize` patches, disables telemetry/cloud flags, and points
  code-interpreter calls at localhost.
- `web_server` joins the shared namespace and disables analytics/cloud UI flags.
- `nginx` no longer publishes ports directly; host access goes through a
  wrapper proxy.
- `code-interpreter` joins the trusted shared namespace, moves to port 7000,
  and can spawn executor pods on the separate restricted network.
- `relational_db` uses wrapper-managed persistent storage.

Full mode adds model-server routing through the local embedding shim, internal
search context-limit env aliases for `api_server`, background worker patches,
OpenSearch/cache/MinIO storage, and local document-drop services.

The local `doc-drop-web` service runs `onyx/doc_drop_webserver.py` instead of the
stdlib `python -m http.server` entrypoint directly. It keeps normal static-file
behavior for readable documents, hides hidden filesystem entries such as
`.git`, `._*`, `.DS_Store`, and `__pycache__` from directory listings, returns
HTTP 404 for direct requests to hidden paths, and converts unreadable file
requests into HTTP 403 responses so crawlers receive a proper status instead of
a closed connection.

Lite mode removes full-mode dependencies, uses Postgres-backed storage options,
sets `DISABLE_VECTOR_DB=true`, and loads the lite Open URL patch.

Podman overrides disable or profile services that depend on Docker socket
semantics that rootless Podman on macOS does not reliably provide.

Proxy and VPN override files thread optional egress configuration through Onyx
services and wrapper sidecars. The routing matrix is documented in
[VPN routing and proxies](vpn_routing_and_proxies.md).

### SearXNG overlay

The SearXNG sidecar uses `searxng/core-config/settings.yml` as a minimal
`use_default_settings` overlay on top of the image defaults. The wrapper owns
only the settings needed for the Onyx web-search path:

- enable `json` output while keeping `html` diagnostics;
- set `server.secret_key` from the ephemeral `SEARXNG_SECRET` generated by the
  Makefile;
- set the non-custom default `outgoing.request_timeout`;
- remove stock direct Google, Brave, DuckDuckGo, Startpage, and Bing web
  engines;
- add the CRW-backed `google2`, `brave2`, `duckduckgo2`, `startpage2`, and
  `bing2` engines.

The Makefile exports `SEARXNG_SECRET` for wrapper starts so startup does not
rely on the overlay placeholder. Other SearXNG env-overridden defaults, such
as `SEARXNG_PORT`, `SEARXNG_BIND_ADDRESS`, `SEARXNG_BASE_URL`,
`SEARXNG_LIMITER`, `SEARXNG_PUBLIC_INSTANCE`, `SEARXNG_IMAGE_PROXY`,
`SEARXNG_METHOD`, and `SEARXNG_VALKEY_URL`, stay inherited from the image
defaults rather than being duplicated in the overlay.

### SearXNG round-robin scheduling and last-resort scoring patches

Local files:

- `searxng/patches/sitecustomize.py`
- `searxng/core-config/settings.yml`
- `searxng/engines/_crw.py`
- `searxng/engines/bing2.py`

SearXNG source area:

- `reference_repos/searxng/searx/search/__init__.py`
- `reference_repos/searxng/searx/results.py`
- `reference_repos/searxng/searx/result_types/_base.py`

The wrapper enables `bing2` as a search engine of last resort. Bing can be
useful when Google, Brave, DuckDuckGo, or Startpage are blocked or sparse, but
its web results are often broad enough to add noise to the top of an aggregate
result set. The wrapper addresses that operationally in two places:

- `SEARXNG_ROUND_ROBIN=true` changes request scheduling so each query uses one
  CRW-backed web provider at a time. Normal providers rotate first; engines
  marked `last_resort: true`, currently `bing2`, are selected only when all
  selected normal providers are already suspended or unavailable. When the
  configured provider pool is present in a request, other selected SearXNG
  engines are dropped so only the chosen provider runs.
- The scoring patch still protects parallel or explicit multi-engine searches
  by making last-resort engines confirm normal results without penalizing them.

SearXNG's stock engine `weight` setting is not a good fit for the scoring half
of this role.

The scheduling patch wraps `searx.search.Search._get_requests()`, the point at
which SearXNG turns `SearchQuery.engineref_list` into engine request work. It
uses SearXNG's existing per-processor `suspended_status` as the health signal
before request threads are launched. The patch also wraps
`Search.search_standard()` so a query can retry within the same SearXNG HTTP
request: if the chosen provider returns no main results and records itself as
unresponsive, SearXNG chooses another untried normal provider, or the
last-resort tier when all normal providers have failed or are unavailable. The
CRW-backed engines support this by treating zero parseable organic rows as a
provider block/parser miss: `_crw.raise_no_results()` logs the engine name,
reason, and rendered HTML length without logging the query or raw body, then
raises `SearxEngineAccessDeniedException`. That keeps parser drift and soft
anti-bot shells visible to SearXNG's engine suspension machinery instead of
returning a misleading empty successful result. Retries stop when a provider
returns main results or when all configured providers have been tried or are
already down.

Stock SearXNG merges duplicate URL results before scoring. For a merged result,
`calculate_score()` multiplies the weights of every engine that found that URL,
multiplies again by the number of positions, then adds a position-adjusted score
for each occurrence. A low Bing weight therefore does two things at once:

- Bing-only results are demoted, which is desirable.
- Results found by Bing and a normal engine are also demoted, which is not
  desirable because Bing should not penalize corroborated results.

The wrapper SearXNG patch module is mounted into `searxng-core` as a
`sitecustomize.py` module and runs in strict mode by default through
`WRAPPER_PATCH_STRICT=true`. At startup it inspects the upstream source shape
for `Search._get_requests()`, `calculate_score()`,
`ResultContainer._merge_main_result()`, `ResultContainer.close()`, and
`ResultContainer.get_ordered_results()`. If those functions no longer contain
the expected scheduling, scoring, merge, and ordering fragments, strict mode
raises and SearXNG fails closed instead of silently applying a stale patch.

The patched merge method records per-engine result positions while preserving
SearXNG's existing merged `positions` and `engines` fields. The patched close
method then applies these rules:

- If a merged result has no last-resort engine, use stock SearXNG scoring.
- If a result has at least one normal engine and one last-resort engine, score
  only the normal-engine positions with stock-style scoring and then apply the
  last-resort engine's `last_resort_confirmation_bonus`.
- If a result has only last-resort engines, score it with
  `last_resort_fallback_weight`.
- During ordering, results with any normal-engine support sort before
  last-resort-only results. Scores still order results inside each tier.

The current `bing2` config is:

```yaml
last_resort: true
last_resort_fallback_weight: 0.05
last_resort_confirmation_bonus: 0.15
```

Consequences:

- With the default `SEARXNG_ROUND_ROBIN=true`, ordinary Onyx web searches use
  one CRW-backed provider per query instead of hitting every search engine.
  Other default-category SearXNG engines are dropped when the configured
  provider pool is present.
- Bing remains available when normal providers are already suspended or
  unavailable, but it is not queried while a normal provider can be selected.
- The scheduler is health-aware and performs same-request retry after a
  provider records itself as unresponsive. This can increase worst-case latency
  when several providers fail sequentially, but avoids returning an empty
  SearXNG response while another configured provider is still eligible.
- The custom CRW-backed engines intentionally fail closed on zero parseable
  organic results. This can mark a provider briefly down for very obscure
  queries that genuinely have no web results, but that is preferable for Onyx
  agent search because a single provider's empty page should not suppress other
  configured providers.
- The round-robin cursor is in-memory and process-local. It resets on SearXNG
  restart; if SearXNG is later run with multiple Python worker processes, each
  worker has its own cursor.
- In round-robin mode, the last-resort scoring patch is mostly inert for
  ordinary single-provider searches, but remains important for explicit
  multi-engine searches and for `SEARXNG_ROUND_ROBIN=false`.
- In parallel mode, Bing-only results remain available when other engines fail,
  but they do not crowd out normal-engine results while those engines are
  healthy.
- A Bing match can modestly promote a URL already found elsewhere.
- Normal results that Bing also finds are not penalized by Bing's fallback
  weight.
- SearXNG's public JSON still exposes one merged `score`; the per-engine
  position map is internal to the patched container and is not part of the
  response schema.

Alternatives considered:

- Use `weight: 0.05` directly on Bing. This demotes Bing-only results, but it
  also multiplies down results confirmed by normal engines.
- Keep Bing weight at `1.0` and post-process Bing-only results in a plugin.
  That avoids core scoring changes, but it is less explicit, less robust across
  SearXNG's grouping pass, and cannot cleanly apply a confirmation bonus.
- Run Bing only after normal engines return too few results in the same HTTP
  request. This is the cleanest "last resort" model operationally, but it
  requires a two-phase scheduler and would add latency on sparse/blocked
  searches.
- Hard-code the engine name `bing2` in the scoring function. The wrapper uses
  config attributes instead so future noisy engines can opt into the same
  behavior without another scoring patch.
- Use Valkey for a cross-worker round-robin cursor. This would make rotation
  global across multiple SearXNG worker processes, but the current container
  shape does not require that extra state and dependency surface.

### Upstream merge request shape

Some wrapper sidecars are deployment-specific and do not belong in upstream
Onyx. The upstreamable pieces are extension points:

- Documented env vars for external code-interpreter URL and port.
- Documented env vars for external model-server or embedding-provider routing.
- A supported way to disable telemetry and hosted-cloud assumptions for local
  deployments.
- A maintained lite profile that keeps live web tools available when safe.
- Compose snippets or documentation for Podman limitations.
- Clear support for running Onyx behind a reverse proxy or shared network
  namespace.

## Install and upgrade hooks

Local files:

- `Makefile`
- `stack.versions.env`
- `onyx/install.sh`
- `onyx/install-with-container-bin.sh`

Onyx source area:

- `reference_repos/onyx/deployment/docker_compose/install.sh`
- `reference_repos/onyx/deployment/docker_compose/env.template`

### Why this is needed

The upstream install script is optimized for directly installing Onyx's Docker
Compose deployment. The wrapper needs a more deterministic and automatable
workflow:

- Require wrapper image tags and source refs from `stack.versions.env`, with
  `.env.wrapper` and make CLI values available as explicit local overrides.
- Generate local stack auth material (`SEARXNG_SECRET`, `USER_AUTH_SECRET`,
  `CRW_ONYX_API_KEY`, and MinIO/S3 credentials) ephemerally for each Makefile
  invocation.
- Support Docker or Podman through `CONTAINER_BIN`.
- Refresh upstream deployment files for a chosen config ref.
- Initialize and sync Onyx's `.env` noninteractively.
- Avoid operator-managed local stack secrets.
- Start full or lite wrapper Compose stacks with the correct override layers.

### How it modifies Onyx

The Makefile orchestrates upgrade and runtime flow:

- `upgrade-onyx` downloads upstream compose, lite compose, env template, README,
  and nginx files for `ONYX_CONFIG_REF`.
- `init-onyx-env` runs the Onyx installer through the local wrapper.
- `sync-onyx-env` pins `IMAGE_TAG` and `CODE_INTERPRETER_IMAGE_TAG`.
- `onyx-build` uses the installer path to prepare or pull required Onyx images.
- `upgrade-python-deps` upgrades the hashed runtime Python locks for
  `embedserv`, `cdp-shim`, and code-interpreter SOCKS proxy support from their
  `requirements.in` files. Most package inputs are unconstrained so this target
  can move them forward; `embedserv/requirements.in` keeps
  `transformers<5.13` because transformers 5.13 breaks `mlx-lm` 0.31.3's
  tokenizer registration during embedserv handler startup, and keeps
  `typer==0.20.0` because newer Typer releases trigger a `sys.exit()` handler
  traceback in the local embedserv CLI path.

`install-with-container-bin.sh` wraps the upstream install script so it can run
through the selected container engine instead of assuming `docker`. The local
`onyx/install.sh` is kept as a patched installer entrypoint for the wrapper's
current flow.

### Upstream merge request shape

Onyx's installer could expose flags for the behaviors the wrapper currently has
to patch around:

- Container engine or compose command.
- Desired image tag.
- Config ref to download.
- Noninteractive env initialization.
- No-start or prepare-images-only mode.
- Lite or full install selection.
- Runtime secret injection without editing env files.
- Port remapping without editing compose by script.

The goal would be to let downstream deployments automate Onyx upgrades without
sed-based install wrappers.

## Upstreaming priorities

The smallest high-value merge requests are:

1. Env-configurable Open URL and web search character limits.
2. Open URL availability independent of vector DB availability.
3. Code-interpreter capability descriptions driven by executor configuration.
4. `python-sandbox` executor network and proxy configuration.
5. OpenAI-compatible embedding provider support with independent rerank and
   query-analysis routing.
6. Trusted-host HTTP validator freshness for the Web connector.
7. Installer flags for container engine, image tag, config ref, and
   noninteractive setup.

For each upstream change, preserve current Onyx behavior as the default, add
tests for both default and enabled behavior, and document security implications
where the option changes network or scraping trust boundaries.
