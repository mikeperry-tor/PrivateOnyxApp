# Onyx wrapper patches

Last updated for Onyx v4.1.7.

Reference checkouts:

- `reference_repos/onyx` at `v4.1.7`
  (`34fc4c3d1febb8866b980a67d62258288e852343`).
- `reference_repos/python-sandbox` at `code-interpreter-0.4.4`
  (`8950eadc06567798ec61354f24260e5dc996684b`), remote
  `https://github.com/onyx-dot-app/python-sandbox`.
- Companion web stack checked against `reference_repos/crw` at `v0.18.3`,
  `reference_repos/obscura` at `v0.1.9`, and `reference_repos/searxng` at
  `f8ffbf36f903`.

Use this document when moving to a new major Onyx release: inspect the
referenced upstream code first, then decide whether each wrapper patch is still
applicable. For why each patch exists and what an upstreamable version might
look like, see [`docs/onyx_patch_info.md`](onyx_patch_info.md). For
operator-facing setup and troubleshooting of the local document RAG path, see
[`docs/local_docs_rag_search.md`](local_docs_rag_search.md). For the
SearXNG/CRW/Obscura request path and live parser assumptions, see
[`docs/request_handling.md`](request_handling.md). For the internal-network
security findings to re-check when changing CRW, Obscura, code-interpreter,
proxy, or shim behavior, see
[`docs/internal_network_security.md`](internal_network_security.md).
For the planned restricted-egress architecture, component version scope, and
network-placement assumptions to keep synchronized during upgrades, see
[`docs/plans/restricted_egress.md`](plans/restricted_egress.md).

## Fast upgrade checklist

1. Refresh the reference checkout to the target Onyx tag and diff every upstream
   reference named below against v4.1.7.
2. Re-run `make upgrade-onyx ONYX_CONFIG_REF=<tag>` to refresh
   `onyx/onyx_data/deployment/*`, then inspect wrapper compose layering with
   `docker compose config`.
3. Confirm every `sitecustomize` patch still imports its target module and that
   replaced strings/default parameters still exist.
4. Refresh `reference_repos/python-sandbox` to the code-interpreter image tag
   that will be deployed and re-check the executor references below.
5. Confirm `onyx/local_embedding_shim.py` still matches Onyx's model-server
   HTTP contract for embeddings, GPU status, rerank, and query analysis.
6. Confirm `onyx/install.sh` is either intentionally pinned/customized or has
   been rebased onto upstream `deployment/docker_compose/install.sh`.
7. Refresh `reference_repos/searxng` to the target SearXNG commit/tag and
   re-check the wrapper SearXNG engine files, minimal settings overlay, and
   custom engine DOM selectors described in
   [SearXNG companion stack](#searxng-companion-stack).
8. Update `stack.versions.env` for image/source pins and run
   `make upgrade-python-deps` to refresh runtime Python package locks.
9. Re-run the internal reachability checks summarized in
   [Internal network security](internal_network_security.md) when changes touch
   CRW, Obscura, the prefetch proxy, CDP shim, code-interpreter executor
   networking, or shared-namespace service placement.
10. Update the version scope and affected component assumptions in
    [Restricted egress network plan](plans/restricted_egress.md) when changing
    Onyx, code-interpreter, SearXNG, CRW, Obscura, Mysterium, routing/support
    images, proxy behavior, CDP/prefetch shims, executor networking, or
    full-mode RAG placement.

## Service map

| Wrapper file | Onyx service patched | Activation path | Upstream Onyx references |
| --- | --- | --- | --- |
| `onyx/patches/sitecustomize_base/sitecustomize.py` | `api_server` | Mounted at `/app/wrapper-patches-base`; `PYTHONPATH` in `docker-compose.yaml` | `backend/onyx/chat/chat_utils.py`, `backend/onyx/chat/llm_loop.py`, `backend/onyx/chat/llm_step.py`, `backend/onyx/llm/multi_llm.py`, `backend/onyx/deep_research/dr_loop.py`, fake-tool agent loops, web/open-url modules, code-interpreter tool/prompt files |
| `onyx/patches/sitecustomize/sitecustomize.py` | `api_server` in lite mode | Mounted at `/app/wrapper-patches`; `PYTHONPATH` in `docker-compose.lite.yml` before base path | Base reasoning-preservation helper plus `backend/onyx/tools/tool_implementations/open_url/open_url_tool.py` |
| `onyx/patches/sitecustomize_background/sitecustomize.py` | `background` | Mounted at `/app/wrapper-patches-background`; `PYTHONPATH` in `docker-compose.full.yml` | `backend/onyx/connectors/web/connector.py`, `backend/onyx/db/models.py` |
| `onyx/patches/sitecustomize_code_interpreter/sitecustomize.py` | `code-interpreter` and spawned executor pods | Mounted by `docker-compose.code-interpreter-vpn.yml` when `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`; proxy env added by `docker-compose.proxy.yml` | Main Onyx compose defines the service; executor code is in `reference_repos/python-sandbox/code-interpreter/app/services/executor_docker.py` |
| `onyx/local_embedding_shim.py` | `api_server` and `background` model-server calls | `docker-compose.full.yml` points `MODEL_SERVER_*` and `INDEXING_MODEL_SERVER_*` to `127.0.0.1:9101` | `backend/model_server/*`, `backend/onyx/natural_language_processing/search_nlp_models.py`, `backend/onyx/indexing/embedder.py`, `backend/shared_configs/model_server_models.py` |
| `onyx/doc_drop_webserver.py` | `doc-drop-web` | Mounted at `/app/doc_drop_webserver.py`; command in `docker-compose.full.yml` | Python `http.server.SimpleHTTPRequestHandler` behavior |
| `searxng/engines/*.py`, `searxng/patches/sitecustomize.py`, `searxng/core-config/settings.yml`, `searxng/searxng-proxy-entrypoint.sh` | `searxng-core` | Mounted by `searxng/docker-compose.yml` and optional `docker-compose.proxy.yml`; patch path added to `PYTHONPATH` | `reference_repos/searxng/searx/results.py`, `reference_repos/searxng/searx/result_types/_base.py`, `reference_repos/searxng/searx/settings_loader.py`, `reference_repos/searxng/searx/settings.yml`, stock engine modules |
| `onyx/install.sh`, `onyx/install-with-container-bin.sh`, `Makefile` | install/upgrade flow, not a runtime container | `make ensure-onyx-config`, `make upgrade-onyx`, `make onyx-build` | `deployment/docker_compose/install.sh`, `deployment/docker_compose/docker-compose.yml`, `deployment/docker_compose/env.template` |

`onyx/patches/sitecustomize_base/__pycache__/...` is generated bytecode, not an
intended patch surface.

## Base api_server patches

Local files:

- `onyx/patches/sitecustomize_base/sitecustomize.py`
- `onyx/patches/sitecustomize_base/wrapper_env_patches.py`
- `docker-compose.yaml`

Activation:

- `docker-compose.yaml` mounts `./onyx/patches/sitecustomize_base` into
  `api_server` and sets `PYTHONPATH=/app/wrapper-patches-base:${PYTHONPATH:-}`.
- The same base patches are also available in lite mode behind the lite-specific
  patch directory.

### LLM context window override env

Compose behavior:

- `docker-compose.yaml` maps wrapper `ONYX_AGENT_LLM_MAX_TOKENS` to upstream
  `GEN_AI_MAX_TOKENS` for `api_server`.
- If unset or empty, Compose passes an empty `GEN_AI_MAX_TOKENS`, which Onyx
  parses as `None`.
- If set to a positive integer, `sitecustomize` makes that value override both
  stored `ModelConfiguration.max_input_tokens` values and provider/LiteLLM
  metadata lookups.

Onyx service: `api_server`.

Upstream v4.1.7 assumptions to re-check:

- `backend/onyx/configs/model_configs.py:59` reads `GEN_AI_MAX_TOKENS`.
- `backend/onyx/llm/utils.py:621` makes `GEN_AI_MAX_TOKENS` override
  LiteLLM/provider metadata when computing max input tokens.
- `backend/onyx/llm/factory.py:312` prefers stored
  `ModelConfiguration.max_input_tokens` before falling back to provider/model
  lookup; the wrapper patch must continue to intercept this DB-first helper.
- `backend/onyx/llm/utils.py:737` also reads stored model configuration before
  fallback lookup; the wrapper patch must continue to intercept this helper too.

Upgrade notes:

- If upstream changes `GEN_AI_MAX_TOKENS` parsing, confirm an empty compose
  value still behaves like no override.
- If upstream renames or removes `GEN_AI_MAX_TOKENS`, update or remove the
  compose mapping.
- If upstream exposes a first-class max-input-token override that wins before DB
  and provider values, remove this runtime patch and map
  `ONYX_AGENT_LLM_MAX_TOKENS` to that setting.

### Assistant reasoning preservation

Patch behavior:

- Reads `ONYX_AGENT_USE_NATIVE_REASONING`, which defaults to `true`. When true,
  wraps `onyx.llm.utils.model_is_reasoning_model()` so wrapper-managed Onyx
  agents treat the configured OpenAI-compatible chat model as native
  reasoning-capable even if LiteLLM does not recognize the provider/model pair.
  This is intentionally separate from `_REASONING_MODE_TRACE`: the env setting
  changes behavior, while the private trace only reports decisions. The override
  logs one startup installation line and one first-use line per provider/model
  pair from inside the hook so disabled trace mode still confirms the hook is
  active.
- Reads `ONYX_AGENT_PRESERVE_TURN_REASONING`, which defaults to `true`, and
  `ONYX_AGENT_PRESERVE_ALL_REASONING`, which defaults to `false`.
- When both settings are explicitly `false`, the reasoning-field preservation
  parts return before mutating Onyx chat/LLM modules. The reminder-placement
  patch still applies.
- Reasoning-field preservation is enabled by the base API-server patch path
  when either setting is true.
- Also invoked by the lite API-server patch path before the lite-only Open URL
  availability patch.
- Reorders Onyx's per-call `USER_REMINDER` in normal chat history so the
  reminder stays next to the latest real user message instead of trailing after
  assistant/tool messages. This is always enabled because provider templates
  can treat a trailing user-role reminder as a new turn boundary.
- Carries saved assistant/tool-call `reasoning_tokens` into reconstructed
  `ChatMessageSimple` assistant messages. In default turn-only mode, saved
  reasoning is attached only to reconstructed assistant messages after the most
  recent user message. In all-history mode, saved reasoning is attached to all
  reconstructed assistant messages.
- Adds current-loop reasoning to assistant tool-call messages in normal chat,
  deep research, research-agent, and coding-agent loops before tool responses
  are sent back to the model.
- Adds top-level `reasoning_content` and `reasoning` aliases to the LiteLLM
  request dictionaries for assistant messages carrying preserved reasoning.
  The patch can carry reasoning internally in
  `provider_specific_fields.reasoning_content`, but strips that duplicate
  nested copy from the final OpenAI-compatible request dictionary when
  top-level reasoning fields are present.
- When internal reasoning tracing is enabled, wraps LiteLLM's OpenAI chat
  request transform to log a metadata-only census of the final transformed
  message body. Use the `litellm_openai_transform_request` and
  `litellm_openai_async_transform_request` events to confirm fields survived
  LiteLLM without enabling raw request/response debug logs.
- Also wraps outbound `httpx` chat-completions sends and logs
  `httpx_outbound_chat_completions` or
  `httpx_async_outbound_chat_completions` with a metadata-only census of the
  serialized JSON request body leaving the Onyx/LiteLLM process.
- Contains internal developer switches for reasoning diagnostics. Keep
  `_REASONING_REMINDER_REORDER_ENABLED` true by default; only flip it during
  controlled upgrade debugging when comparing wrapper ordering with upstream
  `construct_message_history()` behavior. Keep
  `_REASONING_TRACE_ENABLED` false in normal operation; when temporarily
  flipped in the patch, it logs metadata-only Onyx reasoning trace events. Keep
  `_REASONING_TRACE_LITELLM_DEBUG_ENABLED` separate and false unless full
  LiteLLM request/response debug logging is intentionally needed for boundary
  confirmation. `_CODING_AGENT_FINAL_TRACE_ENABLED` follows
  `_REASONING_MODE_TRACE`; do not expose these private trace switches as wrapper
  env preferences.

Onyx service: `api_server`.

Upstream v4.1.7 assumptions to re-check:

- `backend/onyx/chat/models.py:147` defines `ChatMessageSimple` without
  reasoning fields.
- `backend/onyx/chat/chat_utils.py:750` reconstructs assistant tool-call
  history with `message=""` and no reasoning field.
- `backend/onyx/chat/chat_utils.py:790` appends the final assistant message for
  each stored assistant chat message; the wrapper's saved-reasoning alignment
  assumes the reconstructed assistant messages remain in the same order as
  assistant `reasoning_tokens` and tool-call-row `reasoning_tokens`.
- `backend/onyx/chat/llm_loop.py:479` builds chat history in the order
  `[system], [history_before_last_user], [custom_agent], [context_files],
  [forgotten_files], [last_user_message], [messages_after_last_user],
  [reminder]`; the wrapper moves `[reminder]` directly after
  `[last_user_message]`.
- `backend/onyx/chat/llm_loop.py:506` appends `reminder_message` at the end of
  `construct_message_history()`.
- `backend/onyx/chat/llm_loop.py:1134` builds live assistant tool-call
  messages with no reasoning field.
- `backend/onyx/chat/llm_step.py:693` builds structured assistant messages
  with only `role`, `content`, and `tool_calls`.
- `backend/onyx/chat/llm_step.py:963` converts `MessageType.USER_REMINDER` to
  an OpenAI-compatible user-role message wrapped in `<system-reminder>` tags.
- `backend/onyx/llm/multi_llm.py:126` serializes Pydantic messages through
  `model_dump(exclude_none=True)` before calling LiteLLM.
- The deep-research, research-agent, and coding-agent loops still append
  assistant tool-call messages through the exact snippets patched by
  `sitecustomize`.

Upgrade notes:

- If upstream adds first-class reasoning fields to `ChatMessageSimple` and
  `AssistantMessage`, remove the serializer monkey patch and verify saved and
  live tool-call histories populate those fields.
- If upstream starts preserving Anthropic `thinking_blocks`, do not collapse
  signed blocks into plain `reasoning_content`; preserve provider-native
  fields only for providers that accept them.
- Re-test at least one teep OpenAI-compatible GLM-5.2 or Kimi/Kimi-K2.6
  tool-using conversation. Inspect the metadata trace, request payload, or
  LiteLLM debug logs for assistant `reasoning_content` and `reasoning`
  immediately before tool responses, and confirm there is no trailing user-role
  reminder after the final assistant/tool history in the outbound request.
- In the default `ONYX_AGENT_PRESERVE_TURN_REASONING=true` /
  `ONYX_AGENT_PRESERVE_ALL_REASONING=false` mode, confirm old turns before the
  latest user message do not carry reasoning fields. With
  `ONYX_AGENT_PRESERVE_ALL_REASONING=true`, confirm older assistant messages
  carry reasoning fields too.
- If a model still behaves as if reasoning was stripped, temporarily flip
  `_REASONING_TRACE_ENABLED` in the patch and compare the
  `state_set_reasoning_tokens`, `attach_reasoning_fields`,
  `dump_assistant_message`, `litellm_prompt_to_dicts`, and
  `litellm_openai_transform_request` trace events, then compare them against
  the `httpx_*_outbound_chat_completions` events. The prompt, LiteLLM
  transform, and outbound HTTP traces include role counts, role ordering,
  assistant reasoning indexes, reasoning lengths, and short hashes so
  multi-user-turn tool conversations can be checked without logging message
  text.
- `_REASONING_MODE_TRACE` is a private developer switch that defaults to `true`.
  Its `reasoning_mode_trace` lines should be checked during model/provider
  upgrade work. Confirm `model_detection` reports the expected
  `supports_reasoning` value for the configured provider/model. With
  `ONYX_AGENT_USE_NATIVE_REASONING=true`, this should be `true` even when
  LiteLLM would otherwise miss the OpenAI-compatible provider/model pair. Also
  confirm the Onyx agent, coding agent, deep-research orchestrator, and nested
  research agent request traces show `think_tool_offered=false` plus no
  `think_tool_processor_*` events when a native-reasoning model such as the teep
  OpenAI-compatible GLM/Kimi path is in use. Also inspect `llm_step_result`
  events for `reasoning_packet_seen`, `result_reasoning`, and reasoning length /
  hash metadata to confirm reasoning actually appeared without logging the raw
  reasoning text. For code-agent final synthesis, inspect
  `coding_agent_final_answer_history_flattened` and
  `coding_agent_final_summarizer_request`; they should show section ordering,
  reasoning/tool/bash section counts, and short reasoning hashes for the
  plain-text transcript sent to the final summarizer.
- If the outbound request keeps reasoning fields but the provider still drops
  them, inspect the provider chat template behavior for adjacent user-role
  messages. The next fallback should be merging the reminder into the latest
  user message under `<system-reminder>` tags, not removing reminder content
  unless Onyx has replaced it with an upstream-safe mechanism.

### Coding-agent final answer synthesis

Patch behavior:

- Wraps `onyx.tools.fake_tools.coding_agent._generate_final_answer()`.
- Before the final no-tool summarizer runs, converts completed coding-agent
  tool-call history into a single plain-text user-role transcript message. Bash
  command requests, bash outputs, and preserved assistant reasoning remain
  visible to the final model, but the final request should not contain
  `role="tool"` messages, assistant `tool_calls`, repeated assistant-only
  transcript messages, or structured assistant reasoning fields from the tool
  loop.
- If the coding-agent loop stops because the model produced no tool calls,
  appends that terminal assistant answer/reasoning to the in-memory history
  before `_generate_final_answer()` runs so the finalizer transcript includes
  the last reasoning step instead of only earlier tool-request reasoning.
- For flattened history, bypasses upstream `construct_message_history()` and
  merges `USER_FINAL_ANSWER_QUERY` into the same user message as the transcript,
  so the request is exactly `[system, user]` rather than `[system, user,
  user-reminder]`.
- For the flattened finalizer only, calls `llm.stream()` directly without a
  `tools` argument and with reasoning effort disabled. Do not route this path
  through `run_llm_step_pkt_generator()` with `tool_definitions=[]`: some
  OpenAI-compatible GLM/Tinfoil paths reject no-tool requests that still carry
  an empty `tools: []` member plus tool-choice metadata.
- Keeps a fallback around `_generate_final_answer()` so successful bash output
  is returned with a clear diagnostic if the final summarizer LLM call still
  fails.
- Startup validation checks the upstream function source still contains
  `"LLM failed to produce a final answer"` before installing the wrapper.

Onyx service: `api_server`.

Upstream v4.1.7 assumptions to re-check:

- `backend/onyx/tools/fake_tools/coding_agent.py:156` defines
  `_generate_final_answer()`.
- `_generate_final_answer()` builds `final_history` from the coding-agent
  `history` via `construct_message_history()`.
- `_generate_final_answer()` calls `run_llm_step_pkt_generator()` with
  `tool_definitions=[]` and `tool_choice=ToolChoiceOptions.NONE`.
- The coding-agent loop still appends assistant messages with `tool_calls` and
  `TOOL_CALL_RESPONSE` messages to the same in-memory `msg_history` that is
  passed to `_generate_final_answer()`.
- `run_coding_agent_call()` still catches broad exceptions and returns `None`,
  which would otherwise hide successful bash output if only final answer
  synthesis failed.

Upgrade notes:

- If upstream flattens or summarizes coding-agent tool history before the final
  no-tools LLM call, remove this wrapper flattening and keep only any needed
  fallback behavior.
- If upstream starts passing tool definitions into the final synthesis call,
  inspect whether the final request is intentionally a tool-capable request
  before preserving the flattening patch.
- Re-test a coding-agent run against the teep OpenAI-compatible GLM/Kimi path.
  With `_REASONING_MODE_TRACE` enabled, metadata trace for the final summarizer
  should show a final role sequence of `["system", "user"]`, `tools_arg=none`,
  section ordering, and reasoning/tool/bash section counts. Teep debug metadata
  should show `tools_present=false` / `tools_count=0`, and raw request
  inspection, if enabled, should show no `tools` or `tool_choice` member at all.
  The active tool loop before final synthesis may still contain structured tool
  calls and tool responses.
- Confirm the flattened finalizer transcript places preserved code-agent
  reasoning before the corresponding tool-request section. This reasoning is
  plain transcript evidence for the summarizer, not outbound
  `reasoning_content` / `reasoning` message fields.
- Confirm a code-agent run that ends with `Coding agent LLM produced no tool
  calls; forcing final answer.` includes the terminal no-tool reasoning hash in
  `coding_agent_final_answer_history_flattened` as an `assistant_reasoning`
  section.

### Previous tool-result preservation

Patch behavior:

- Reads `ONYX_AGENT_PRESERVE_TOOL_RESULTS`.
- When true, replaces `chat_utils._build_tool_call_response_history_message()`
  so saved `tool_call_response` content is returned for non-image tools instead
  of `TOOL_CALL_RESPONSE_CROSS_MESSAGE`.
- Preserves image-generation metadata behavior.
- Wraps `chat_utils.convert_chat_history()` to recompute token counts for all
  `TOOL_CALL_RESPONSE` messages instead of keeping upstream's fixed non-image
  estimate.

Onyx service: `api_server`.

Upstream v4.1.7 assumptions to re-check:

- `backend/onyx/prompts/chat_prompts.py:95` defines
  `TOOL_CALL_RESPONSE_CROSS_MESSAGE`.
- `backend/onyx/chat/chat_utils.py:576` defines
  `_build_tool_call_response_history_message()`.
- `backend/onyx/chat/chat_utils.py:581` returns the placeholder for every
  non-image tool.
- `backend/onyx/chat/chat_utils.py:773` creates `TOOL_CALL_RESPONSE` history
  messages and `:775` assigns non-image tool responses a fixed token count.
- Custom and MCP tool calls should still be stored through the same `ToolCall`
  / `convert_chat_history()` path.

Upgrade notes:

- If upstream starts preserving or summarizing prior tool responses natively,
  remove this patch or map the wrapper env to the new upstream setting.
- If upstream changes tool response storage or splits MCP/custom tool history
  into a separate path, verify `ONYX_AGENT_PRESERVE_TOOL_RESULTS=true` covers
  those tools before carrying the patch forward.

### open_url and web_search character budgets

Patch behavior:

- Wrapper patches run in strict mode. Missing exact upstream strings, changed
  helper signatures, or failed imports should fail startup instead of silently
  leaving stale runtime behavior. The compose wrapper passes this internal env
  var to every service that mounts wrapper `sitecustomize` patches.
- Reads `ONYX_OPEN_URL_MAX_CHARS_PER_URL` and `ONYX_OPEN_URL_MAX_TOTAL_CHARS`.
- Replaces `web_search.utils.MAX_CHARS_PER_URL`.
- Rewrites default arguments for
  `truncate_search_result_content`,
  `_truncate_content_around_snippet`, and
  `_convert_sections_to_llm_string_with_citations`.
- `0` means effectively unlimited (`2_000_000_000` chars).

Onyx service: `api_server`.

Upstream v4.1.7 assumptions to re-check:

- `backend/onyx/tools/tool_implementations/web_search/utils.py:15` defines
  `MAX_CHARS_PER_URL = 15000`.
- `backend/onyx/tools/tool_implementations/web_search/utils.py:34` and `:43`
  take `max_chars: int = MAX_CHARS_PER_URL`.
- `backend/onyx/tools/tool_implementations/open_url/open_url_tool.py:65`
  defines `MAX_CHARS_ACROSS_URLS = 10 * MAX_CHARS_PER_URL`.
- `backend/onyx/tools/tool_implementations/open_url/open_url_tool.py:301`
  defines `_convert_sections_to_llm_string_with_citations`, with
  `max_document_chars` defaulting from `MAX_CHARS_ACROSS_URLS` at `:305`.

Upgrade notes:

- If upstream moves truncation into another module or changes defaults to
  keyword-only parameters, this default-rewrite patch may silently stop helping.
- If Onyx exposes first-class env/config for these limits, prefer that over the
  monkey patch.

### Internal search content caps

Patch behavior:

- Reads `ONYX_RAG_INTERNAL_SEARCH_MAX_CONTENT_CHARS_PER_RESULT` and
  `ONYX_RAG_INTERNAL_SEARCH_MAX_TOTAL_CONTENT_CHARS`.
- Empty or `0` means no wrapper cap and no formatter patch is installed.
- Wraps `convert_inference_sections_to_llm_string()` and the symbol imported
  into `search_tool.py` to cap each result's `content` and aggregate returned
  content after context expansion.

Onyx service: `api_server` in full mode.

Upstream v4.1.7 assumptions to re-check:

- `backend/onyx/tools/tool_implementations/search/search_tool.py:975` calls
  `convert_inference_sections_to_llm_string()` with
  `limit=override_kwargs.max_llm_chunks`.
- `backend/onyx/tools/tool_implementations/utils.py:29` defines
  `convert_inference_sections_to_llm_string()`, and `:111` serializes
  `section.combined_content` into each result's `content`.
- `backend/onyx/server/features/search/api.py:191` maps the LLM-facing search
  JSON into `/search` results.
- `backend/onyx/mcp_server/tools/search.py:46` forwards `/search` result
  content to MCP clients.

Upgrade notes:

- If upstream changes search formatting or stops importing
  `convert_inference_sections_to_llm_string` by name, update the wrapper patch
  or remove the extra imported-symbol assignment.
- If upstream adds first-class settings for internal search content budgets,
  prefer those and keep the wrapper env names as aliases only if useful.

### Firecrawl waitFor omission

Patch behavior:

- Replaces `FirecrawlClient._get_webpage_content`.
- Sends only `{url, formats: ["markdown"]}` to the Firecrawl-compatible endpoint.
- Intentionally omits `waitFor`; wrapper page readiness is handled by the CRW /
  Obscura CDP path described in `docs/request_handling.md`.

Onyx service: `api_server`.

Upstream v4.1.7 assumptions to re-check:

- `backend/onyx/tools/tool_implementations/open_url/firecrawl.py:32` defines
  `FirecrawlClient`.
- `backend/onyx/tools/tool_implementations/open_url/firecrawl.py:76` defines
  `_get_webpage_content`.
- `backend/onyx/tools/tool_implementations/open_url/firecrawl.py:77` builds a
  payload containing only `url` and `formats` in v4.1.7, so this patch is mostly
  defensive for this release.
- `backend/onyx/tools/tool_implementations/open_url/firecrawl.py:124` defines
  `_extract_content_fields`, which the patch still calls.

Upgrade notes:

- If upstream changes the response parser, error handling, or WebContent model,
  copy the new behavior into the patch or remove the patch if no longer needed.
- The patch currently imports `requests` inside the replacement function and
  preserves 4xx-as-empty-content behavior.

### Code-interpreter tool and prompt descriptions

Patch behavior:

- Only active when `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true` is present in `api_server`.
- Updates API-side LLM-facing text so Python, Bash, and coding-agent tools say
  the executor pods have VPN-routed network access.
- Adds wrapper hints for local CRW, SearXNG JSON search, `/v1/search`, and CDP
  browser availability only to coding-agent system prompts. Do not expose those
  API details in the Python tool prompt, Python tool description, Bash tool
  description, or coding-agent tool metadata.

Onyx service: `api_server`.

Upstream v4.1.7 assumptions to re-check:

- `backend/onyx/tools/tool_implementations/python/python_tool.py:98` has
  `PythonTool.DESCRIPTION = "Execute Python code in an isolated sandbox environment."`
- `backend/onyx/prompts/tool_prompts.py:54` defines `PYTHON_TOOL_GUIDANCE`, with
  the no-internet sentence patched by exact string replacement.
- `backend/onyx/tools/tool_implementations/bash/bash_tool.py:48` defines the
  network-restricted `BashTool.DESCRIPTION`.
- `backend/onyx/coding_agent/mock_tools.py:48` defines
  `BASH_TOOL_DESCRIPTION`, including "The session has no network access."
- `backend/onyx/prompts/coding_agent/coding_agent.py:8` and `:79` define the
  coding-agent prompt constants that describe a network-isolated sandbox.
- `backend/onyx/tools/fake_tools/coding_agent.py:30` imports those constants by
  name, so startup-time patching still needs to happen before Onyx imports that
  module.

Upgrade notes:

- These are exact `.replace()` patches. After a major update, grep the target
  strings. If any are gone, the patch may no-op while the executor pods are
  network-enabled.
- The API-side text must remain aligned with the code-interpreter-side network
  patch below. Do not advertise network access unless executor pods are actually
  routed.

## Lite api_server patch

Local files:

- `onyx/patches/sitecustomize/sitecustomize.py`
- `docker-compose.lite.yml`

Patch behavior:

- Imports and applies the base env patches when available.
- Forces `OpenURLTool.is_available` to return `True`.

Onyx service: `api_server` in lite mode.

Why it exists:

- Lite mode sets `DISABLE_VECTOR_DB=true` and uses postgres backends for file,
  cache, and auth in `docker-compose.lite.yml`.
- Upstream `OpenURLTool.is_available` is disabled when vector DB is disabled,
  even though the wrapper wants chat/Web/Research usage in lite mode.

Upstream v4.1.7 assumptions to re-check:

- `backend/onyx/tools/tool_implementations/open_url/open_url_tool.py:399`
  defines `OpenURLTool`.
- `backend/onyx/tools/tool_implementations/open_url/open_url_tool.py:459`
  contains the availability check that this patch bypasses.
- `backend/onyx/tools/tool_constructor.py` and
  `backend/onyx/server/query_and_chat/session_loading.py` still use
  `OpenURLTool.is_available` for tool exposure.

Upgrade notes:

- If Onyx supports open_url without a vector DB upstream, remove this patch.
- Re-test lite chat, web_search, and open_url together because this patch changes
  tool availability, not just behavior.

## Background Web connector PDF freshness patch

Local files:

- `onyx/patches/sitecustomize_background/sitecustomize.py`
- `docker-compose.full.yml`

Companion docs:

- Rationale and upstream shape:
  [Background Web connector PDF freshness](onyx_patch_info.md#background-web-connector-pdf-freshness)
- Operator flow and diagnostics:
  [Local Document RAG Search](local_docs_rag_search.md#pdf-freshness-patch)

Patch behavior:

- Active by default with `ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_ENABLED=true`.
- Only applies to URLs whose host is in
  `ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_HOSTS` (default:
  `localhost,127.0.0.1,::1`).
- For PDFs, performs a HEAD request and records wrapper metadata:
  `_wrapper_http_freshness_version`, `_wrapper_http_last_modified`,
  `_wrapper_http_content_length`, `_wrapper_http_freshness_source`, and
  `_wrapper_http_freshness_unchanged` on pre-download skip sentinels.
- For trusted PDF URLs whose HEAD response is HTTP 401, 403, or 404, returns
  an unreadable skip sentinel with `_wrapper_http_freshness_unreadable` and
  `_wrapper_http_status` instead of falling into Onyx's direct PDF parser.
- If the DB document already has matching freshness metadata and
  `doc_updated_at`, skips downloading the unchanged PDF.
- Patches Onyx's document update gate so wrapper skip sentinels are skipped even
  on forced/targeted reindex paths that disable the normal timestamp gate.
- If content hash matches after a normal scrape, seeds the DB freshness metadata
  and avoids pointless re-indexing on subsequent runs.
- Logs startup status and one-time warnings through Onyx's logger. Per-document
  hit/miss diagnostics such as `skipped unchanged PDF before download`,
  `seeded unchanged PDF freshness`, and detailed miss reasons are available only
  when `ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_DEBUG=true`.

Onyx service: `background`.

Upstream v4.1.7 assumptions to re-check:

- `backend/onyx/connectors/web/connector.py:346` defines `WebConnector`.
- `backend/onyx/connectors/web/connector.py:396` defines `_do_scrape`.
- `backend/onyx/connectors/web/connector.py:421` performs the HEAD request.
- `backend/onyx/connectors/web/connector.py:431` branches for PDFs.
- `backend/onyx/connectors/web/connector.py:447` intentionally leaves
  `doc_updated_at` unset for web PDFs because Last-Modified can be unreliable.
- `backend/onyx/connectors/models.py` must still expose `Document` with
  `doc_updated_at`, `doc_metadata`, and `content_hash()`.
- `backend/onyx/db/models.py` must still have the DB `Document` fields
  `id`, `doc_updated_at`, `doc_metadata`, and `content_hash`.

Upgrade notes:

- This intentionally overrides upstream's choice to avoid Last-Modified for web
  docs, but only for allowlisted local hosts, primarily the wrapper's
  `doc-drop-web`.
- If Onyx adds first-class incremental web/PDF freshness, replace this patch
  with upstream behavior.
- Re-test with an unchanged local PDF and a modified local PDF. The unchanged
  run should skip the download; the modified run should re-index.

## Code-interpreter executor network and proxy patch

Local files:

- `onyx/patches/sitecustomize_code_interpreter/sitecustomize.py`
- `docker-compose.code-interpreter-vpn.yml`
- `docker-compose.proxy.yml`
- `docker-compose.yaml`

Related plan:

- [Restricted egress network plan](plans/restricted_egress.md), especially the
  code-interpreter executor target model and policy preference surface.

Patch behavior:

- Pins the code-interpreter image with `CODE_INTERPRETER_IMAGE_TAG=0.4.4`.
- When `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`, spawned executor containers inherit
  the shared `netns-holder` namespace via
  `PYTHON_EXECUTOR_DOCKER_NETWORK=container:onyx-netns-holder-1`.
- When `ONYX_AGENT_OUTBOUND_PROXY_URL` is set, injects proxy env vars into every executor pod.
- Executor `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` point at the
  prefetch-blocking-proxy service's local HTTP listener on
  `http://127.0.0.1:3128` for every upstream proxy scheme. The sidecar adapts
  egress to `ONYX_AGENT_OUTBOUND_PROXY_URL`, including SOCKS URLs.

Onyx service: `code-interpreter`, plus its transient executor pods.

Upstream v4.1.7 assumptions to re-check:

- Main Onyx compose defines `code-interpreter` in
  `deployment/docker_compose/docker-compose.yml:534`.
- The code-interpreter service runs `bash ./entrypoint.sh code-interpreter-api`
  at `deployment/docker_compose/docker-compose.yml:536`.

Python-sandbox / code-interpreter assumptions to re-check:

- `reference_repos/python-sandbox` is the correct source repository for the
  `onyxdotapp/code-interpreter` image. Its README shows Docker usage with
  `onyxdotapp/code-interpreter` at `README.md:27` to `:31`, and its Helm values
  use `repository: onyxdotapp/code-interpreter` at
  `kubernetes/code-interpreter/values.yaml:7` to `:11`.
- `code-interpreter/Dockerfile:70` sets `ENTRYPOINT ["./entrypoint.sh"]`, and
  `:71` sets `CMD ["code-interpreter-api"]`.
- `code-interpreter/pyproject.toml:22` to `:23` maps the
  `code-interpreter-api` console script to `app.main:run`.
- `code-interpreter/app/app_configs.py:15` defines
  `PYTHON_EXECUTOR_DOCKER_RUN_ARGS`.
- `code-interpreter/app/app_configs.py:19` defines
  `PYTHON_EXECUTOR_DOCKER_NETWORK`, defaulting to `none`.
- `code-interpreter/app/services/executor_docker.py:61` defines
  `DockerExecutor`.
- `code-interpreter/app/services/executor_docker.py:250` defines
  `_build_run_command`.
- `code-interpreter/app/services/executor_docker.py:265` starts building the
  `docker run` argv, and `:272`/`:273` pass
  `PYTHON_EXECUTOR_DOCKER_NETWORK` to `--network`.
- `code-interpreter/app/services/executor_docker.py:314` to `:315` appends
  `PYTHON_EXECUTOR_DOCKER_RUN_ARGS` after the built-in isolation flags. Use the
  upstream network setting rather than passing another `--network` here,
  because Docker errors if conflicting network flags are present.
- `code-interpreter/app/services/executor_docker.py:422` creates persistent
  session containers with `_build_run_command`; bash executions inherit that
  container network namespace, as documented in `execute_bash_in_session` at
  `:514` to `:526`.

Wrapper compose assumptions:

- `docker-compose.yaml` runs `code-interpreter` inside
  `network_mode: service:netns-holder`, sets `PORT=7000`, and points
  `CODE_INTERPRETER_BASE_URL` at `http://localhost:7000`.
- `docker-compose.code-interpreter-vpn.yml` mounts the patch directory and adds
  `ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true` plus
  `PYTHON_EXECUTOR_DOCKER_NETWORK=container:onyx-netns-holder-1`.
- `docker-compose.proxy.yml` mounts the code-interpreter patch when proxy mode
  is active and adds `ONYX_AGENT_OUTBOUND_PROXY_URL` / `ALL_PROXY` /
  `NO_PROXY` plus `ONYX_AGENT_EXECUTOR_HTTP_PROXY_URL` to the code-interpreter
  container. The patch decides which variables to forward to executor pods.

Security notes:

- Enabling this removes the upstream executor pod network isolation. The LLM can
  run arbitrary outbound network commands from generated Python and bash.
- Only enable in trusted, single-tenant deployments.

Upgrade notes:

- Update [Restricted egress network plan](plans/restricted_egress.md) if the
  code-interpreter image tag, executor Docker network setting, proxy injection
  behavior, executor prompt/capability text, or
  `ONYX_CODE_INTERPRETER_ENABLE_NETWORK` semantics change.
- If upstream changes command construction, proxy injection can fail open or
  fail closed. Verify logs contain the startup patch status and proxy injection
  messages when `ONYX_AGENT_OUTBOUND_PROXY_URL` is set.
- If the executor network model changes away from `container:onyx-netns-holder-1`,
  revisit `ONYX_AGENT_EXECUTOR_HTTP_PROXY_URL`. The current default
  `http://127.0.0.1:3128` works only because executor pods share the namespace
  where `prefetch-blocking-proxy` listens.

## Local embedding shim

Local files:

- `onyx/local_embedding_shim.py`
- `docker-compose.full.yml`
- `Makefile` embedserv targets

Companion docs:

- Rationale and upstream shape:
  [Local embedding shim](onyx_patch_info.md#local-embedding-shim)
- Operator setup, prefix behavior, and diagnostics:
  [Local Document RAG Search](local_docs_rag_search.md#embedding-shim)

Patch behavior:

- Runs a small threaded HTTP server on `0.0.0.0:9101`.
- Exposes model-server-compatible endpoints used by Onyx:
  - `GET /health`
  - `GET /api/gpu-status`
  - `POST /encoder/bi-encoder-embed`
  - `POST /encoder/cross-encoder-scores` as an intentional 501 stub
  - `POST /custom/query-analysis` as an intentional 501 stub
- Translates Onyx `EmbedRequest` payloads (`texts`, `model_name`, `text_type`,
  `manual_query_prefix`, `manual_passage_prefix`, etc.) into OpenAI-compatible
  `/v1/embeddings` calls (`{"model": ..., "input": [...]}`).
- Applies query/passage prefixes from either Onyx request fields or wrapper env:
  `SHIM_QUERY_PREFIX` / `ONYX_RAG_EMBEDDING_QUERY_PREFIX` and
  `SHIM_PASSAGE_PREFIX` / `ONYX_RAG_EMBEDDING_PASSAGE_PREFIX`.
- Keeps a small upstream connection pool to the configured
  OpenAI-compatible embedding server. If pooled connection reuse fails with a
  transport-level exception (`OSError`, `TimeoutError`, or
  `http.client.HTTPException`), the shim closes and replaces that connection,
  logs `upstream_connection_retry`, and retries the embedding request once.
  This covers local embedding servers that silently close idle keep-alive
  sockets. HTTP error statuses from the upstream server are returned without
  retrying.
- Supports `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL`, `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_API_KEY`,
  `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL`, `SHIM_UPSTREAM_POOL_SIZE`, and
  `SHIM_METRICS_LOG_EVERY`.

Onyx services: `api_server` and `background`.

Compose wiring:

- `docker-compose.full.yml` sets `MODEL_SERVER_HOST=127.0.0.1`,
  `MODEL_SERVER_PORT=9101`, `INDEXING_MODEL_SERVER_HOST=127.0.0.1`, and
  `INDEXING_MODEL_SERVER_PORT=9101` for `api_server` and `background`.
- `local-embedding-shim` runs in `network_mode: service:netns-holder`, so those
  loopback references resolve inside the shared namespace.
- `make embedserv-install`, `make embedserv-verify-model`, and
  `make embedserv-serve` install and run `mlx-openai-server` on the host-side
  `ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL` (default `http://host.docker.internal:1234/v1/embeddings`).

Upstream v4.1.7 assumptions to re-check:

- `backend/shared_configs/model_server_models.py:10` defines `EmbedRequest`.
- `backend/shared_configs/model_server_models.py:34` defines `EmbedResponse`.
- `backend/model_server/main.py:130` includes the management router and `:131`
  includes the encoder router.
- `backend/model_server/management_endpoints.py:7` uses prefix `/api`, `:10`
  defines `/api/health`, and `:15` defines `/api/gpu-status`.
- `backend/model_server/encoders.py:176` defines
  `POST /encoder/bi-encoder-embed`.
- `backend/model_server/encoders.py:203` to `:217` selects query/passage prefix
  and calls `embed_text`.
- `backend/onyx/natural_language_processing/search_nlp_models.py:199` builds
  model-server URLs.
- `backend/onyx/natural_language_processing/search_nlp_models.py:797` to `:800`
  builds the embedding endpoint.
- `backend/onyx/natural_language_processing/search_nlp_models.py:874` defines
  `_make_model_server_request`.
- `backend/onyx/natural_language_processing/search_nlp_models.py:1224` builds
  `/encoder/cross-encoder-scores`.
- `backend/onyx/natural_language_processing/search_nlp_models.py:1321` builds
  `/custom/query-analysis`.
- `backend/onyx/tools/tool_implementations/search/search_tool.py:448` runs
  the agent-facing `internal_search` query through `search_pipeline()`.
- `backend/onyx/context/search/pipeline.py:336` calls `search_chunks()`.
- `backend/onyx/context/search/retrieval/search_runner.py:58` embeds query
  text before hybrid search.
- `backend/onyx/context/search/utils.py:163` calls `get_query_embeddings()`;
  `:110` delegates to `EmbeddingModel.encode()`.
- `backend/onyx/indexing/embedder.py:60` constructs `EmbeddingModel` for
  indexing and `:72`/`:73` pass the indexing model-server host/port.
- `backend/onyx/utils/gpu_utils.py` calls `/api/gpu-status`.

Upgrade notes:

- Update [Restricted egress network plan](plans/restricted_egress.md) if the
  local embedding shim, doc-drop, Web connector, or full-mode model-server
  placement changes the networks or local peers used by full-mode RAG.
- When upgrading Onyx, verify that `internal_search` still uses the same
  query-embedding path and still depends on `MODEL_SERVER_HOST` /
  `MODEL_SERVER_PORT`. If Onyx adds a distinct internal-search embedding
  provider, wire that provider to the local embedding service or remove the
  shim dependency for `api_server` search.
- Reproduce the stale-connection case by making a fake upstream close the first
  socket before responding, then confirm the shim logs
  `upstream_connection_retry` and returns a successful embedding response on
  the second attempt.
- The shim does not implement reranking or query analysis. It is correct only
  when local rerank/query-analysis are unused or when 501 failures are acceptable
  and visible.
- Onyx v4.1.7's real `/api/health` lives under `/api/health`; the shim exposes
  `/health` for its own compose healthcheck and `/api/gpu-status` for Onyx. If
  Onyx starts probing `/api/health` through `MODEL_SERVER_HOST`, add that route.
- If upstream changes `EmbedRequest` fields, especially `text_type`, prefix
  fields, `normalize_embeddings`, `max_context_length`, or response shape,
  update the shim before upgrading.
- If the target local embedding server supports native query/document task
  fields, consider passing those instead of prefixing text.

## SearXNG companion stack

Local files:

- `searxng/docker-compose.yml`
- `searxng/engines/_crw.py`
- `searxng/engines/google2.py`
- `searxng/engines/brave2.py`
- `searxng/engines/duckduckgo2.py`
- `searxng/engines/startpage2.py`
- `searxng/engines/bing2.py`
- `searxng/patches/sitecustomize.py`
- `searxng/core-config/settings.yml`
- `searxng/searxng-proxy-entrypoint.sh`

Upstream SearXNG references:

- `reference_repos/searxng/searx/results.py`
- `reference_repos/searxng/searx/search/__init__.py`
- `reference_repos/searxng/searx/result_types/_base.py`
- `reference_repos/searxng/searx/settings_loader.py`
- `reference_repos/searxng/searx/settings.yml`
- Stock engine modules for `google`, `brave`, `duckduckgo`, `startpage`, and
  `bing`

Related plan:

- [Restricted egress network plan](plans/restricted_egress.md), especially the
  CRW, SearXNG, Obscura browser, final-hop proxy, and policy preference
  sections.

### Custom CRW-backed engines

Behavior:

- `searxng/docker-compose.yml` bind-mounts the wrapper engine files into the
  image's `searx/engines/` directory one file at a time, preserving the rest of
  the built-in engine tree.
- `_crw.py` rewrites an engine request so SearXNG POSTs to local CRW
  `http://127.0.0.1:3010/v1/scrape` with `formats: ["rawHtml"]` and
  `onlyMainContent: false`.
- `_crw.py` intentionally does not send `renderer` or `waitFor`. CRW's compose
  mode and the prefetch-blocking proxy decide when to escalate to the CDP
  renderer, while the CDP shim handles page-readiness waiting. See
  [Request Handling: SearXNG to CRW](request_handling.md#12-searxng--crw-custom-engines).
- `_crw.extract_crw_html()` maps CRW anti-bot failures back to SearXNG engine
  exceptions so engine suspension stats work instead of silently returning an
  empty result list.
- `_crw.raise_no_results()` is used by each custom engine when its parser finds
  zero organic rows. It logs the engine, parser-miss reason, and rendered HTML
  length without logging query text or raw SERP HTML, then raises
  `SearxEngineAccessDeniedException` so round-robin can retry another provider.
- `google2`, `brave2`, `duckduckgo2`, `startpage2`, and `bing2` parse the
  rendered SERP DOM with XPath. They replace the stock search-engine variants
  that are blocked or challenge-prone on VPN/datacenter exit IPs.
- `bing2` is configured as a last-resort engine. With the default
  `SEARXNG_ROUND_ROBIN=true`, the SearXNG scheduling patch rotates among one
  normal CRW-backed provider per query and selects `bing2` only when all
  selected normal providers are already suspended or unavailable. With
  `SEARXNG_ROUND_ROBIN=false` or explicit multi-engine usage, the scoring patch
  demotes Bing-only results and treats Bing matches on normal-engine results as
  confirmation.

Upgrade notes:

- Update [Restricted egress network plan](plans/restricted_egress.md) if a
  SearXNG, CRW, or Obscura version change alters custom engine transport,
  CRW scrape URL assumptions, HTTP prefetch behavior, CDP rendering, search
  host blocking, or browser egress policy.
- Re-test each custom engine with a real query after every SearXNG, CRW,
  Obscura, or target-search-engine change. Import checks are not enough because
  the fragile contract is the rendered SERP DOM.
- Verify the DOM selectors listed in
  [Custom engine parser assumptions](request_handling.md#custom-engine-parser-assumptions):
  Google result anchors with `h3`, Brave `data-type="web"` cards,
  DuckDuckGo HTML `web-result` cards, and Startpage post-hydration organic
  result cards. For Bing, verify `ol#b_results > li.b_algo`, `h2 > a` title
  links, `b_caption` snippets, `/ck/a?u=a1...` redirect decoding, and
  captcha/Turing page detection.
- When changing Obscura, re-test Google `google2` through the rendered CDP
  path specifically. As of 2026-06-30, direct host `curl` could receive
  Google's JS retry shell for `https://www.google.com/search?q=...&udm=14`,
  while Obscura on the same query/exit path received an immediate
  `HTTP 429` document, navigated into `/sorry/index?...`, loaded
  `recaptcha/enterprise.js`, and produced no organic-result DOM markers.
  Obscura is actively improving fingerprint and stealth behavior, so an
  Obscura update may change this failure mode into either a successful
  `udm=14` SERP or a different challenge path.
- Use `CDP_SHIM_TRACE=1` during those Obscura/Google checks to distinguish an
  immediate 429/sorry response from a JavaScript retry shell or a successful
  rendered SERP. Keep query-value logging redacted by leaving
  `CDP_SHIM_TRACE_INCLUDE_QUERY_VALUES=0`; the default safe query keys still
  show diagnostics such as `udm=14`.
- Confirm SearXNG still loads custom modules from `searx.engines.*` and that
  `OnlineParams` still honors the in-place `method`, `url`, `data`, and
  `headers` rewrite.
- Confirm SearXNG exceptions used by `_crw.py` still exist:
  `SearxEngineTooManyRequestsException`,
  `SearxEngineAccessDeniedException`, `SearxEngineCaptchaException`, and
  `SearxEngineResponseException`.
- Confirm each custom engine still raises through `_crw.raise_no_results()`
  when the rendered SERP has no parseable organic results. A parser miss should
  appear in SearXNG `unresponsive_engines` and trigger round-robin retry rather
  than returning a successful empty result set to Onyx.
- Keep `enable_http: true` on the custom engines. Their SearXNG-side request is
  a loopback HTTP POST to CRW, even though CRW/Obscura later navigates HTTPS
  target search-engine pages.
- Keep `bing2.last_resort: true`,
  `bing2.last_resort_fallback_weight`, and
  `bing2.last_resort_confirmation_bonus` in the overlay unless the scoring
  model is intentionally changed.

### Round-robin scheduling and last-resort scoring patch

Behavior:

- `searxng/patches/sitecustomize.py` is mounted into `searxng-core` and added
  to `PYTHONPATH`, so Python imports it during SearXNG startup.
- When `SEARXNG_ROUND_ROBIN=true`, the patch wraps
  `searx.search.Search._get_requests()` before SearXNG launches engine
  threads. It reduces the selected CRW-backed web provider list to one
  available normal provider, or one available last-resort provider if every
  selected normal provider is already suspended or unavailable. When that
  provider pool is present, other selected SearXNG engines are dropped for the
  request.
- The patch also wraps `Search.search_standard()` so a round-robin search can
  retry within the same SearXNG HTTP request after the selected provider
  records itself as unresponsive and returns no main results. The custom
  CRW-backed engines are expected to classify zero parseable organic rows as
  unresponsive via `_crw.raise_no_results()`. Retries stop after a provider
  returns main results or after all configured providers have been tried or are
  already down.
- The patch inspects upstream `searx.search` and `searx.results` functions in
  strict mode before replacing them. Missing expected source fragments must
  stop startup instead of silently producing stale scheduling or ranking
  behavior.
- The patched merge path records result positions by engine. The patched close
  path preserves stock scoring for ordinary results, scores last-resort-only
  results with `last_resort_fallback_weight`, and applies
  `last_resort_confirmation_bonus` when a last-resort engine confirms a
  normal-engine result.
- The patched ordering path sorts any result with normal-engine support before
  last-resort-only results, then sorts by score inside each tier.

Upgrade notes:

- Update [Restricted egress network plan](plans/restricted_egress.md) if
  `SEARXNG_ROUND_ROBIN`, last-resort scoring, provider suspension behavior, or
  custom-provider selection changes the search fan-out or network egress
  expectations for SearXNG.
- Re-check `reference_repos/searxng/searx/search/__init__.py` for the stock
  `Search._get_requests()` flow. Confirm SearXNG still builds a request list
  by iterating `self.search_query.engineref_list`, skips suspended processors
  with `processor.extend_container_if_suspended(...)`, and appends
  `(engineref.name, self.search_query.query, request_params)` tuples before
  `search_multiple_requests()` starts threads.
- Re-check `Search.search_standard()` to confirm it still calls
  `_get_requests()`, assigns `self.actual_timeout`, and invokes
  `search_multiple_requests(requests)`.
- Verify round-robin selection with synthetic or live checks: with all normal
  providers available, successive searches should use exactly one of
  `google2`, `brave2`, `duckduckgo2`, or `startpage2`; `bing2` should not be
  scheduled, and unrelated default-category engines such as `wikipedia` should
  not run. When normal providers are suspended/unavailable, `bing2` should be
  eligible as the fallback tier.
- Verify retry behavior with a synthetic or live failure: if the first selected
  provider records an unresponsive error and returns no main results, the same
  SearXNG request should try another untried provider before returning. The
  query should return empty only after every configured provider has failed or
  is suspended. A live provider returning a successful empty result from one of
  the custom engines indicates the no-results failure contract has drifted.
- Confirm `SEARXNG_ROUND_ROBIN=false` restores SearXNG's selected-engine
  fan-out so the last-resort scoring patch still protects merged Bing results.
- Re-check `reference_repos/searxng/searx/results.py` for the stock
  `calculate_score()` algorithm. If upstream stops multiplying engine weights,
  changes how duplicate positions are represented, or introduces first-class
  engine-tier/fallback support, prefer removing or simplifying the wrapper
  patch.
- Re-check `ResultContainer._merge_main_result()` and
  `merge_two_main_results()` to confirm duplicate URL results are still merged
  before scoring and that result objects still expose `engine`, `engines`, and
  `positions` with the same meaning.
- Re-check `ResultContainer.close()` and `get_ordered_results()` to confirm
  scores are still assigned before ordering and that the category grouping pass
  still follows the first score sort.
- Verify SearXNG still permits arbitrary engine config keys to become engine
  attributes. The patch relies on `last_resort`,
  `last_resort_fallback_weight`, and `last_resort_confirmation_bonus` being
  visible on the loaded `bing2` engine object.
- Test ranking with synthetic duplicate results: Bing-only results should sort
  below all normal-engine results, a normal result also found by Bing should
  not be penalized, and the confirmation bonus should be visible in the merged
  score.
- Confirm SearXNG startup logs include
  `sitecustomize: patched SearXNG round-robin search provider scheduling and retry`
  when `SEARXNG_ROUND_ROBIN=true`, and always include
  `sitecustomize: patched SearXNG last-resort engine scoring` in strict mode.

### SearXNG settings overlay

`searxng/core-config/settings.yml` is a minimal overlay. It uses
`use_default_settings.engines.remove` to drop the stock Google, Brave,
DuckDuckGo, and Startpage engines that are replaced by the CRW-backed engines.
SearXNG then merges the rest of the overlay with the image's built-in
`searx/settings.yml`.

Overlay-owned settings:

- `search.formats: [html, json]`, because Onyx calls SearXNG with
  `format=json` and HTML is useful for local diagnostics.
- `server.secret_key`, which is overwritten by the ephemeral `SEARXNG_SECRET`
  generated by the Makefile.
- `outgoing.request_timeout: 6.0`, used by non-custom engines.
- `use_default_settings.engines.remove`, which prevents double-querying the
  direct stock engines replaced by the custom CRW-backed engines.
- `engines` entries for `google2`, `brave2`, `duckduckgo2`, `startpage2`, and
  `bing2`, each with `timeout: 60.0` and `enable_http: true` for the loopback
  POST to CRW.
- `bing2` last-resort scoring attributes:
  `last_resort: true`, `last_resort_fallback_weight: 0.05`, and
  `last_resort_confirmation_bonus: 0.15`.

Inherited SearXNG env overrides:

- Keep env-overridden stock defaults in the image defaults unless the wrapper
  intentionally owns the setting.
- The Makefile-generated `SEARXNG_SECRET` overrides the overlay's
  `server.secret_key` during wrapper starts.
- `SEARXNG_PORT`, `SEARXNG_BIND_ADDRESS`, `SEARXNG_BASE_URL`,
  `SEARXNG_LIMITER`, `SEARXNG_PUBLIC_INSTANCE`, `SEARXNG_IMAGE_PROXY`,
  `SEARXNG_METHOD`, and `SEARXNG_VALKEY_URL` override inherited image-default
  settings.

Upgrade procedure:

- Keep `settings.yml` as an overlay. Do not copy the full upstream
  `searx/settings.yml` into `searxng/core-config/`.
- Update [Restricted egress network plan](plans/restricted_egress.md) if
  SearXNG outgoing proxy semantics, per-engine `network`, custom-engine
  `enable_http`, or CRW-loopback/direct-network assumptions change.
- Check `reference_repos/searxng/searx/settings_loader.py` for
  `use_default_settings` merge behavior. The wrapper relies on mapping
  deep-merge, engine removal by name, and appending unknown custom engine
  names.
- Check `reference_repos/searxng/searx/settings_defaults.py` for env alias
  changes, especially `server.secret_key` / `SEARXNG_SECRET` and inherited
  `SEARXNG_*` defaults listed above.
- Keep Makefile generation and Compose
  `${SEARXNG_SECRET:?SEARXNG_SECRET must be set}` checks aligned.
- Check `reference_repos/searxng/searx/settings.yml` for renamed stock engines.
  If upstream renames Google, Brave, DuckDuckGo, Startpage, or Bing web
  entries, update `use_default_settings.engines.remove`.
- Check whether SearXNG adds first-class equivalents for the custom engines or
  CRW-style rendered fetching. If so, prefer upstream configuration over local
  engine modules.
- Keep `searxng/core-config/` free of copied default files. The local config
  surface should stay limited to wrapper-owned settings.
- Re-check `searxng/searxng-proxy-entrypoint.sh` when changing the overlay
  structure. Its proxy merge finds the top-level `outgoing:` and `engines:`
  blocks and injects `network: direct` into the custom engine entries,
  including `bing2`, so loopback CRW requests bypass
  `ONYX_AGENT_OUTBOUND_PROXY_URL`.

## Docker Compose wrapper modifications

Local files:

- `docker-compose.yaml`
- `docker-compose.full.yml`
- `docker-compose.lite.yml`
- `docker-compose.podman.yml`
- `docker-compose.podman-full.yml`
- `docker-compose.code-interpreter-vpn.yml`
- `docker-compose.proxy.yml`

Primary upstream reference:

- `reference_repos/onyx/deployment/docker_compose/docker-compose.yml`

Related plan:

- [Restricted egress network plan](plans/restricted_egress.md), especially
  Compose layering, routing matrix, component bridges, final-hop proxy policy,
  and validation plan.

### Base wrapper (`docker-compose.yaml`)

Patched Onyx services:

- `api_server`: extends upstream, replaces image/build, joins
  `netns-holder`, disables telemetry/paid-cloud settings, sets SSRF-related
  env aliases, sets `ASYM_QUERY_PREFIX`, raises `MAX_LLM_CYCLES`, injects base
  `PYTHONPATH`, mounts wrapper patches and persistent file/model-cache dirs,
  removes upstream `extra_hosts`, and lengthens healthcheck startup.
- `web_server`: extends upstream, joins `netns-holder`, disables frontend
  analytics/cloud/billing flags, changes healthcheck to probe namespace IP.
- `nginx`: extends upstream, joins `netns-holder`, removes direct published
  ports; host access is via `host-web-proxy`.
- `code-interpreter`: extends upstream, joins `netns-holder`, moves service port
  from 8000 to 7000, raises executor limits, and points API/background to
  `http://localhost:7000`.
- `relational_db`: extends upstream and bind-mounts wrapper postgres data.

Additional wrapper services:

- `netns-holder` owns the shared network namespace.
- `myst-client`, `searxng-core`, `searxng-valkey`, `obscura`,
  `obscura-mcp`, `cdp-shim`, `prefetch-blocking-proxy`, `crw`,
  `host-web-proxy`, `host-searxng-proxy`, `autoheal`, `tailscale-funnel`, and
  `teep` are wrapper-side services around Onyx.

Upgrade notes:

- Update [Restricted egress network plan](plans/restricted_egress.md) if base
  service placement, shared-namespace membership, host bridge services,
  `netns-holder`, `myst-client`, `prefetch-blocking-proxy`, `cdp-shim`, CRW,
  Obscura, SearXNG, or code-interpreter topology changes.
- Compare upstream service names and `depends_on` shape. Compose `extends`
  depends on stable service names: `api_server`, `web_server`, `nginx`,
  `code-interpreter`, and `relational_db`.
- Re-check upstream healthcheck commands and ports. The wrapper relies on
  `api_server:8080`, `web_server:3000`, nginx port 80, and code-interpreter port
  remapping to 7000 inside the shared namespace.
- Confirm upstream env names for telemetry/cloud/license flags still exist or
  are harmless if ignored.

### Full mode compose

File: `docker-compose.full.yml`.

For the RAG-specific user flow around `doc-drop-web`,
`host-doc-drop-web-proxy`, and `local-embedding-shim`, see
[Local Document RAG Search](local_docs_rag_search.md). For why the wrapper
uses full-mode sidecars and compose overrides at all, see
[Docker Compose wrapper modifications](onyx_patch_info.md#docker-compose-wrapper-modifications).

Patched Onyx services:

- `background`: extends upstream, joins `netns-holder`, disables telemetry,
  points model-server env to the local shim, applies the background
  `sitecustomize`, and depends on `local-embedding-shim`.
- `api_server`: adds model-server env pointing to the shim, forwards optional
  internal-search content cap env vars, and depends on the shim.
- `inference_model_server` and `indexing_model_server`: still extend upstream
  and mount caches/logs, but API/background model-server traffic is routed to
  the shim instead.
- `opensearch`, `cache`, and `minio`: provide full-mode data services. MinIO is
  defined locally so inherited upstream dependencies are satisfied.

Wrapper additions:

- `doc-drop-web` and `host-doc-drop-web-proxy` expose a local read-only docs
  directory for the Onyx Web connector. `doc-drop-web` runs
  `onyx/doc_drop_webserver.py`, a small `http.server` wrapper that hides hidden
  filesystem entries such as `.git`, `._*`, `.DS_Store`, and `__pycache__` from
  directory listings, returns HTTP 404 for direct hidden-path requests, and
  returns HTTP 403 for unreadable file requests instead of closing the
  connection with a traceback.
- `local-embedding-shim` provides the model-server-compatible local embedding
  bridge.

Upgrade notes:

- Update [Restricted egress network plan](plans/restricted_egress.md) if
  full-mode service placement changes doc-drop, embedding-shim, model-server,
  MinIO, Valkey, or local document RAG reachability.
- Confirm upstream `background` still accepts `MODEL_SERVER_HOST`,
  `MODEL_SERVER_PORT`, `INDEXING_MODEL_SERVER_HOST`, and
  `INDEXING_MODEL_SERVER_PORT`.
- If internal-search content caps are enabled, confirm the wrapper
  `sitecustomize` still patches the formatter described in
  [Internal search content caps](#internal-search-content-caps).
- If upstream makes model-server calls from additional services, point them at
  the shim or document why not.

### Lite mode (`docker-compose.lite.yml`)

Patched Onyx service: `api_server`.

Behavior:

- Overrides upstream `depends_on` to remove full-mode-only services.
- Sets `DISABLE_VECTOR_DB=true`, `FILE_STORE_BACKEND=postgres`,
  `CACHE_BACKEND=postgres`, and `AUTH_BACKEND=postgres`.
- Mounts both lite-specific and base `sitecustomize` paths.

Upgrade notes:

- Upstream lite support may change. Compare with
  `deployment/docker_compose/docker-compose.onyx-lite.yml`.
- If upstream adds more required dependencies through `extends`, this override
  may need to drop or replace them.

### Podman overrides

Patched Onyx services:

- `docker-compose.podman.yml`: places `autoheal` and `code-interpreter` behind
  a `requires-docker-socket` profile because rootless Podman on macOS cannot
  reliably provide the Docker socket behavior those containers need.
- `docker-compose.podman-full.yml`: sets OpenSearch `userns_mode:
  keep-id:uid=1000,gid=1000` so the bind-mounted data dir is writable under
  rootless Podman.

Upgrade notes:

- Re-check upstream OpenSearch image UID/GID. If it no longer runs as 1000,
  update the `keep-id` mapping.
- Re-test Podman full mode after any upstream change to OpenSearch version,
  volume paths, or code-interpreter socket usage.

### Code-interpreter VPN override

See the code-interpreter section above. The compose layer is intentionally
conditional via `Makefile`: it is only added when
`ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`.

### Proxy override

Patched services:

- Wrapper services `obscura`, `obscura-mcp`, and `searxng-core`.
- Onyx `code-interpreter` for executor-pod proxy propagation.

Behavior:

- Adds `--proxy` to Obscura commands.
- Starts SearXNG through `searxng-proxy-entrypoint.sh`, which merges proxy
  settings into `settings.yml`.
- Adds `ONYX_AGENT_OUTBOUND_PROXY_URL` to code-interpreter so the code-interpreter `sitecustomize`
  can inject the right env vars into executor pods.

Upgrade notes:

- Update [Restricted egress network plan](plans/restricted_egress.md) if
  proxy-mode service coverage, `ONYX_AGENT_OUTBOUND_PROXY_URL`,
  `ONYX_AGENT_ALLOW_HTTP_URLS`, SearXNG proxy mutation, Obscura `--proxy`, or
  executor proxy propagation changes.
- Main Onyx source references are mostly indirect here: `api_server` prompt text
  must match executor network/proxy reality, and code-interpreter service wiring
  must still allow `PYTHONPATH` injection.
- Re-test both HTTP and SOCKS proxy modes.

## Install and upgrade hooks

Local files:

- `Makefile`
- `stack.versions.env`
- `onyx/install-with-container-bin.sh`
- `onyx/install.sh`

Onyx source references:

- `reference_repos/onyx/deployment/docker_compose/install.sh`
- `reference_repos/onyx/deployment/docker_compose/docker-compose.yml`
- `reference_repos/onyx/deployment/docker_compose/env.template`

### Makefile orchestration

Behavior:

- Requires `ONYX_IMAGE_TAG`, `SEARXNG_IMAGE_TAG`, and
  `CODE_INTERPRETER_IMAGE_TAG` from `stack.versions.env`, `.env.wrapper`
  overrides, or make CLI overrides.
  `ONYX_IMAGE_TAG` remains the source of truth for Onyx backend/web/model
  image tags.
- Generates local stack auth material (`SEARXNG_SECRET`, `USER_AUTH_SECRET`,
  `CRW_ONYX_API_KEY`, and MinIO/S3 credentials) ephemerally for each Makefile
  invocation.
- Builds `COMPOSE_FILE` layer lists for lite/full mode, Podman mode, optional
  teep/tailscale/code-interpreter VPN routing, and optional proxy routing.
- `ensure-onyx-config` verifies `onyx/onyx_data/deployment/.env` exists and
  that its `IMAGE_TAG` matches `ONYX_IMAGE_TAG`; otherwise it runs
  `upgrade-onyx`.
- `upgrade-onyx` downloads Onyx deployment compose files, env template, README,
  and nginx files from `https://raw.githubusercontent.com/onyx-dot-app/onyx/<ref>/deployment/...`.
- `init-onyx-env` and `onyx-build` invoke `onyx/install-with-container-bin.sh`
  with `ONYX_DESIRED_IMAGE_TAG`, `CONTAINER_BIN`, and
  `ONYX_INSTALL_HOST_PORT_80`.
- `sync-onyx-env` ensures `IMAGE_TAG` and `CODE_INTERPRETER_IMAGE_TAG` are
  pinned in Onyx's deployment env.
- `embedserv-*` targets install, verify, and serve the local MLX embedding
  server that `onyx/local_embedding_shim.py` calls.
- `upgrade-python-deps` upgrades the hashed Python lock files for
  `embedserv/requirements.txt` and `crw/cdp-shim-requirements.txt` from their
  corresponding `requirements.in` files.

Upgrade notes:

- When changing `stack.versions.env`, update the version scope in
  [Restricted egress network plan](plans/restricted_egress.md) for any pin
  that affects Onyx, code-interpreter, SearXNG, CRW, Obscura, Mysterium,
  routing/support images, or full-mode data/search support.
- If upstream moves deployment files out of `deployment/docker_compose`, update
  `upgrade-onyx`.
- If upstream env vars change names, update `sync-onyx-env` and the wrapper
  compose env overrides together.
- `make upgrade` includes `upgrade-python-deps`, so normal stack upgrades also
  refresh the Python locks. Most runtime Python inputs are unconstrained to
  allow upgrades; `embedserv/requirements.in` intentionally pins
  `transformers<5.13` because transformers 5.13 breaks `mlx-lm` 0.31.3's
  tokenizer registration during embedserv handler startup, and pins
  `typer==0.20.0` because newer Typer releases trigger a `sys.exit()` handler
  traceback in the local embedserv CLI path.
- If a runtime Python dependency input changes, run `make upgrade-python-deps`
  and keep the generated hash locks in the same upgrade commit as the version
  manifest change.

### Container binary install wrapper

File: `onyx/install-with-container-bin.sh`.

Behavior:

- Resolves `CONTAINER_BIN` to a real executable.
- Creates a temporary `docker` shim on `PATH` so upstream `install.sh` can keep
  calling `docker`.
- Routes `docker compose ...` to `$REAL_CONTAINER_BIN compose ...`.
- Routes other docker commands to `$REAL_CONTAINER_BIN`.
- For `docker system info`, adds a `Total Memory: ...GiB` line when Podman
  reports `memTotal` instead of Docker's wording, keeping upstream resource
  checks happy.
- When `ONYX_DESIRED_IMAGE_TAG` is set, rewrites
  `DEFAULT_IMAGE_TAG="edge"` in the runtime copy of `install.sh`.

Upgrade notes:

- Re-check upstream `install.sh` for how it invokes Docker and how it parses
  `docker system info`.
- If upstream stops using `DEFAULT_IMAGE_TAG="edge"`, the sed rewrite will no
  longer pin the installer prompt/default.

### Local `onyx/install.sh`

`onyx/install.sh` is a local copy of upstream
`deployment/docker_compose/install.sh`. As of this doc, it is not identical to
the v4.1.7 reference. Notable local behavior:

- Forces `craft-edge` when `--include-craft` is used, rather than using the
  regular backend image tag behavior from v4.1.7.
- Writes `SANDBOX_BACKEND=docker` directly for Craft instead of upstream's
  version-sensitive `sandbox_backend_for_tag` helper.
- Does not rely on persisted MinIO/S3 credentials in Onyx's deployment env;
  wrapper Compose receives ephemeral MinIO/S3 credentials from the Makefile at
  runtime.
- Does not create upstream's `sandbox_proxy_ca` Docker volume.
- Treats `craft-*` tags as floating tags that should use config ref `main`.

Upgrade notes:

- On every major Onyx update, diff:
  `reference_repos/onyx/deployment/docker_compose/install.sh` vs
  `onyx/install.sh`.
- Decide whether local differences are still intentional. If not, rebase the
  local file onto upstream and keep only the wrapper-required changes.
- The preferred wrapper path is to minimize local installer edits and keep
  container-engine adaptation in `install-with-container-bin.sh`.

## Validation commands

Run these after updating Onyx:

```sh
make upgrade-onyx ONYX_CONFIG_REF=<target-tag>
make up-lite
make down-lite
make up-full
make down-full
```

With feature flags:

```sh
ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true make up-lite
ONYX_AGENT_OUTBOUND_PROXY_URL=socks5h://host.docker.internal:9150 make up-lite
make embedserv-serve
```

SearXNG upgrade checks:

```sh
rg -n "def update_settings|use_default_settings|keep_only|remove" reference_repos/searxng/searx/settings_loader.py
rg -n "SEARXNG_SECRET|SEARXNG_PORT|SEARXNG_BIND_ADDRESS|SEARXNG_VALKEY_URL" reference_repos/searxng/searx/settings_defaults.py
rg -n "name: (google|brave|duckduckgo|startpage|bing)" reference_repos/searxng/searx/settings.yml
rg -n "google2|brave2|duckduckgo2|startpage2|bing2|last_resort|use_default_settings" searxng docs
```

Manual behavior checks:

- Lite chat can use `web_search` and `open_url`.
- SearXNG JSON search returns results from each custom engine shortcut
  (`go2`, `br2`, `ddg2`, `sp2`) when the target engine is not suspended or
  serving a challenge page.
- Full mode can index a document through `doc-drop-web`.
- Full mode embedding calls hit `local-embedding-shim` and return vectors of the
  expected dimension.
- Query and passage prefixes appear in shim logs with the expected source.
- Code-interpreter executor pods either remain network-isolated or are created
  with `PYTHON_EXECUTOR_DOCKER_NETWORK=container:onyx-netns-holder-1`, matching
  the selected `ONYX_CODE_INTERPRETER_ENABLE_NETWORK` setting.
- Proxy mode shows executor pod commands receiving proxy env vars. For SOCKS
  upstreams, executor `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` all point at
  `http://127.0.0.1:3128`, and
  `urllib.request.urlopen("https://example.com")` succeeds through the
  configured upstream proxy.
