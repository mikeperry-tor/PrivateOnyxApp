"""Explicitly installed environment-driven patches for stock Onyx services.

This module is imported by sitecustomize so wrapper-level env vars can
adjust hardcoded limits without rebuilding images.
"""

from __future__ import annotations

import functools
import hashlib
import importlib
import inspect
import json
import os
import sys
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit


# We cannot remove truncation logic entirely without editing upstream code, so
# for "unlimited" we use a very large budget that won't be hit in practice.
EFFECTIVE_UNLIMITED_CHARS = 2_000_000_000

# Internal developer diagnostics. Reasoning-mode tracing is metadata-only but
# quiet by default; enable it only during controlled model/provider routing
# validation. The deeper reasoning traces remain opt-in; LiteLLM debug logging
# is intentionally separate because it can include full request/response details.
_REASONING_TRACE_ENABLED = False
_REASONING_TRACE_LITELLM_DEBUG_ENABLED = False
_REASONING_TRACE_SEQ = 0
_REASONING_REMINDER_REORDER_ENABLED = True
_NATIVE_REASONING_DETECTION_OVERRIDE_ENABLED = os.environ.get(
    "ONYX_AGENT_USE_NATIVE_REASONING", "true"
).lower() in ("1", "true", "yes", "on")
_NATIVE_REASONING_DETECTION_OVERRIDE_LOGGED: set[tuple[str, str]] = set()
_REASONING_MODE_TRACE = False
_REASONING_MODE_TRACE_SEQ = 0
_CODING_AGENT_FINAL_TRACE_ENABLED = _REASONING_MODE_TRACE
_DEEP_RESEARCH_WORKER_LIMIT: ContextVar[int | None] = ContextVar(
    "wrapper_deep_research_worker_limit",
    default=None,
)
_PLAYWRIGHT_PROXY_OVERRIDE: ContextVar[str | None] = ContextVar(
    "wrapper_playwright_proxy_override", default=None
)


@contextmanager
def select_playwright_proxy(proxy_url: str | None):
    """Temporarily select a validated fixed proxy, or direct internal mode."""
    if proxy_url not in {
        None,
        "",
        "http://onyx-public-egress-bridge:3128",
        "http://onyx-host-egress-bridge:3128",
    }:
        raise RuntimeError("invalid stack-owned Playwright proxy selection")
    token = _PLAYWRIGHT_PROXY_OVERRIDE.set(proxy_url)
    try:
        yield
    finally:
        _PLAYWRIGHT_PROXY_OVERRIDE.reset(token)


def _strict_mode() -> bool:
    return os.environ.get("WRAPPER_PATCH_STRICT", "true").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _warn_or_raise(message: str) -> None:
    print(f"sitecustomize: WARNING: {message}", flush=True)
    if _strict_mode():
        raise RuntimeError(message)


def _raise_if_strict() -> None:
    if _strict_mode():
        raise


def _replace_or_warn(
    *,
    owner_name: str,
    current: str,
    old: str,
    new: str,
) -> str:
    replaced = current.replace(old, new)
    if replaced == current:
        _warn_or_raise(
            f"{owner_name} patch did not match expected upstream text"
        )
    else:
        print(f"sitecustomize: patched {owner_name}", flush=True)
    return replaced


def _set_single_default(function, value: int, function_name: str) -> None:
    signature = inspect.signature(function)
    if not signature.parameters:
        _warn_or_raise(f"{function_name} has no parameters; cannot patch default")
        return

    defaults = function.__defaults__
    if defaults is None or len(defaults) != 1:
        _warn_or_raise(
            f"{function_name} expected one positional default, found {defaults!r}; "
            f"signature={signature}"
        )
        return

    function.__defaults__ = (value,)


def _parse_positive_int(var_name: str) -> int | None:
    raw = os.environ.get(var_name)
    if not raw:
        return None

    try:
        value = int(raw)
    except ValueError:
        print(
            f"sitecustomize: ignoring {var_name}={raw!r} (must be integer)",
            flush=True,
        )
        return None

    if value == 0:
        print(
            f"sitecustomize: {var_name}=0 -> using effectively unlimited budget "
            f"({EFFECTIVE_UNLIMITED_CHARS})",
            flush=True,
        )
        return EFFECTIVE_UNLIMITED_CHARS

    if value < 0:
        print(
            f"sitecustomize: ignoring {var_name}={raw!r} (must be >= 0)",
            flush=True,
        )
        return None

    return value


def _parse_optional_positive_int(var_name: str) -> int | None:
    raw = os.environ.get(var_name)
    if not raw:
        return None

    try:
        value = int(raw)
    except ValueError:
        print(
            f"sitecustomize: ignoring {var_name}={raw!r} (must be integer)",
            flush=True,
        )
        return None

    if value == 0:
        return None

    if value < 0:
        print(
            f"sitecustomize: ignoring {var_name}={raw!r} (must be >= 0)",
            flush=True,
        )
        return None

    return value


def _env_flag_enabled(var_name: str) -> bool:
    return os.environ.get(var_name, "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _env_flag_default_true(var_name: str) -> bool:
    return os.environ.get(var_name, "true").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


_DEEP_RESEARCH_PROVIDE_CHAT_AGENT_TOOLS = _env_flag_default_true(
    "ONYX_DEEP_RESEARCH_PROVIDE_CHAT_AGENT_TOOLS"
)


def _reasoning_digest(value: Any) -> tuple[int, str | None]:
    if not isinstance(value, str) or not value:
        return 0, None
    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]
    return len(value), digest


def _trace_reasoning(event: str, **fields: Any) -> None:
    if not _REASONING_TRACE_ENABLED:
        return

    global _REASONING_TRACE_SEQ
    _REASONING_TRACE_SEQ += 1
    rendered = " ".join(
        f"{key}={json.dumps(value, sort_keys=True)}"
        for key, value in sorted(fields.items())
    )
    print(
        f"sitecustomize: reasoning_trace seq={_REASONING_TRACE_SEQ} "
        f"event={event} {rendered}",
        flush=True,
    )


def _trace_reasoning_mode(event: str, **fields: Any) -> None:
    if not _REASONING_MODE_TRACE:
        return

    global _REASONING_MODE_TRACE_SEQ
    _REASONING_MODE_TRACE_SEQ += 1
    rendered = " ".join(
        f"{key}={json.dumps(value, sort_keys=True)}"
        for key, value in sorted(fields.items())
    )
    print(
        f"sitecustomize: reasoning_mode_trace seq={_REASONING_MODE_TRACE_SEQ} "
        f"event={event} {rendered}",
        flush=True,
    )


def _caller_context() -> str:
    try:
        frame = inspect.currentframe()
        # current -> _caller_context -> wrapped helper -> wrapped helper caller
        for _ in range(2):
            frame = frame.f_back if frame is not None else None
        module_name = frame.f_globals.get("__name__") if frame is not None else None
        function_name = frame.f_code.co_name if frame is not None else None
        if module_name and function_name:
            return f"{module_name}.{function_name}"
        if module_name:
            return str(module_name)
    except Exception:
        pass
    return "unknown"


def _tool_names_from_definitions(tool_definitions: Any) -> list[str]:
    if not isinstance(tool_definitions, list):
        return []

    names: list[str] = []
    for tool_definition in tool_definitions:
        try:
            function_def = tool_definition.get("function")
            name = function_def.get("name") if isinstance(function_def, dict) else None
        except Exception:
            name = None
        if isinstance(name, str):
            names.append(name)
    return names


def _update_bound_module_attr(original: Any, replacement: Any, attr_name: str) -> None:
    for module in list(sys.modules.values()):
        if module is None:
            continue
        try:
            if getattr(module, attr_name, None) is original:
                setattr(module, attr_name, replacement)
        except Exception:
            continue


def _message_field(message: Any, name: str) -> Any:
    if isinstance(message, dict):
        return message.get(name)
    return getattr(message, name, None)


def _message_has_field(message: Any, name: str) -> bool:
    if isinstance(message, dict):
        return name in message
    return hasattr(message, name)


def _tool_call_count(tool_calls: Any) -> int:
    if isinstance(tool_calls, list):
        return len(tool_calls)
    if tool_calls:
        try:
            return len(tool_calls)
        except Exception:
            return 1
    return 0


def _message_role_counts(messages: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for msg in messages:
        role = msg.get("role")
        if not isinstance(role, str) or not role:
            role = "unknown"
        counts[role] = counts.get(role, 0) + 1
    return counts


def _trace_reasoning_message_census(
    event: str,
    messages: Any,
    **fields: Any,
) -> None:
    if not isinstance(messages, list):
        _trace_reasoning(event, message_count=0, messages_is_list=False, **fields)
        return

    dict_messages = [msg for msg in messages if isinstance(msg, dict)]
    assistant_reasoning_content_indexes: list[int] = []
    assistant_reasoning_indexes: list[int] = []
    assistant_reasoning_content_lens: list[int] = []
    assistant_reasoning_lens: list[int] = []
    assistant_reasoning_content_hashes: list[str | None] = []
    assistant_reasoning_hashes: list[str | None] = []
    assistant_provider_specific_reasoning_indexes: list[int] = []

    for idx, msg in enumerate(dict_messages):
        if msg.get("role") != "assistant":
            continue

        reasoning_content = _first_non_empty_string(msg.get("reasoning_content"))
        reasoning = _first_non_empty_string(msg.get("reasoning"))
        if reasoning_content:
            reasoning_len, reasoning_sha = _reasoning_digest(reasoning_content)
            assistant_reasoning_content_indexes.append(idx)
            assistant_reasoning_content_lens.append(reasoning_len)
            assistant_reasoning_content_hashes.append(reasoning_sha)
        if reasoning:
            reasoning_len, reasoning_sha = _reasoning_digest(reasoning)
            assistant_reasoning_indexes.append(idx)
            assistant_reasoning_lens.append(reasoning_len)
            assistant_reasoning_hashes.append(reasoning_sha)

        provider_specific_fields = msg.get("provider_specific_fields")
        if (
            isinstance(provider_specific_fields, dict)
            and provider_specific_fields.get("reasoning_content")
        ):
            assistant_provider_specific_reasoning_indexes.append(idx)

    _trace_reasoning(
        event,
        message_count=len(dict_messages),
        messages_is_list=True,
        role_counts=_message_role_counts(dict_messages),
        role_sequence=[msg.get("role") for msg in dict_messages],
        user_messages=sum(1 for msg in dict_messages if msg.get("role") == "user"),
        assistant_with_tool_calls=sum(
            1
            for msg in dict_messages
            if msg.get("role") == "assistant" and msg.get("tool_calls")
        ),
        assistant_with_reasoning_content=len(assistant_reasoning_content_indexes),
        assistant_reasoning_content_indexes=assistant_reasoning_content_indexes,
        assistant_reasoning_content_lens=assistant_reasoning_content_lens,
        assistant_reasoning_content_sha256=assistant_reasoning_content_hashes,
        assistant_with_reasoning=len(assistant_reasoning_indexes),
        assistant_reasoning_indexes=assistant_reasoning_indexes,
        assistant_reasoning_lens=assistant_reasoning_lens,
        assistant_reasoning_sha256=assistant_reasoning_hashes,
        assistant_provider_specific_reasoning_indexes=(
            assistant_provider_specific_reasoning_indexes
        ),
        **fields,
    )


def _trace_reasoning_request_body(
    event: str,
    body: bytes | None,
    **fields: Any,
) -> None:
    if not body:
        _trace_reasoning(event, body_available=False, **fields)
        return

    body_sha = hashlib.sha256(body).hexdigest()[:12]
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as e:
        _trace_reasoning(
            event,
            body_available=True,
            body_sha256=body_sha,
            json_parse_error=repr(e),
            **fields,
        )
        return

    if not isinstance(payload, dict):
        _trace_reasoning(
            event,
            body_available=True,
            body_sha256=body_sha,
            payload_type=type(payload).__name__,
            **fields,
        )
        return

    _trace_reasoning_message_census(
        event,
        payload.get("messages"),
        body_available=True,
        body_sha256=body_sha,
        has_stream=bool(payload.get("stream")),
        model=payload.get("model"),
        **fields,
    )


def _enable_litellm_reasoning_trace_debug() -> None:
    if not _REASONING_TRACE_ENABLED:
        return
    if not _REASONING_TRACE_LITELLM_DEBUG_ENABLED:
        _trace_reasoning("litellm_debug_skipped")
        return

    try:
        from onyx.llm.litellm_singleton import litellm

        litellm.suppress_debug_info = False
        turn_on_debug = getattr(litellm, "_turn_on_debug", None)
        if callable(turn_on_debug):
            turn_on_debug()
        _trace_reasoning(
            "litellm_debug_enabled",
            suppress_debug_info=getattr(litellm, "suppress_debug_info", None),
        )
    except Exception as e:  # pragma: no cover
        _trace_reasoning("litellm_debug_enable_failed", error=repr(e))


def _first_non_empty_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _set_extra_attr(obj: Any, name: str, value: Any) -> None:
    try:
        setattr(obj, name, value)
    except Exception:
        try:
            object.__setattr__(obj, name, value)
        except Exception:
            obj.__dict__[name] = value


def _attach_reasoning_fields(
    message: Any,
    reasoning: str | None,
    *,
    source: str = "unknown",
) -> None:
    """Carry prior reasoning across Onyx's internal message model boundary."""
    reasoning_content = _first_non_empty_string(reasoning)
    reasoning_len, reasoning_sha = _reasoning_digest(reasoning_content)
    _trace_reasoning(
        "attach_reasoning_fields",
        source=source,
        target_type=type(message).__name__,
        incoming_reasoning=bool(reasoning_content),
        reasoning_len=reasoning_len,
        reasoning_sha256=reasoning_sha,
        had_reasoning_content=_message_has_field(message, "reasoning_content"),
        role=_message_field(message, "role"),
        tool_calls=_tool_call_count(_message_field(message, "tool_calls")),
    )
    if not reasoning_content:
        return

    _set_extra_attr(message, "reasoning_content", reasoning_content)

    provider_specific_fields = getattr(message, "provider_specific_fields", None)
    if isinstance(provider_specific_fields, dict):
        provider_specific_fields = dict(provider_specific_fields)
    else:
        provider_specific_fields = {}
    provider_specific_fields.setdefault("reasoning_content", reasoning_content)
    _set_extra_attr(message, "provider_specific_fields", provider_specific_fields)


def _dump_message_with_reasoning_fields(
    message: Any,
    *,
    idx: int | None = None,
) -> dict[str, Any]:
    if hasattr(message, "model_dump"):
        dumped = message.model_dump(exclude_none=True)
    else:
        dumped = dict(message)

    if dumped.get("role") != "assistant":
        return dumped

    reasoning_content = _first_non_empty_string(
        dumped.get("reasoning_content"),
        getattr(message, "reasoning_content", None),
    )
    if not reasoning_content:
        _trace_reasoning(
            "dump_assistant_message",
            idx=idx,
            role=dumped.get("role"),
            tool_calls=_tool_call_count(dumped.get("tool_calls")),
            reasoning_content=False,
            provider_specific_reasoning=False,
        )
        return dumped

    dumped["reasoning_content"] = reasoning_content
    dumped.setdefault("reasoning", reasoning_content)

    provider_specific_fields = dumped.get("provider_specific_fields")
    if isinstance(provider_specific_fields, dict):
        provider_specific_fields = dict(provider_specific_fields)
    else:
        provider_specific_fields = {}
    provider_specific_reasoning = _first_non_empty_string(
        provider_specific_fields.get("reasoning_content")
    )
    provider_reasoning_matches = (
        provider_specific_reasoning == reasoning_content
        if provider_specific_reasoning
        else None
    )
    provider_specific_fields.pop("reasoning_content", None)
    if provider_specific_fields:
        dumped["provider_specific_fields"] = provider_specific_fields
    else:
        dumped.pop("provider_specific_fields", None)
    reasoning_len, reasoning_sha = _reasoning_digest(reasoning_content)
    provider_reasoning_len, provider_reasoning_sha = _reasoning_digest(
        provider_specific_reasoning
    )
    _trace_reasoning(
        "dump_assistant_message",
        idx=idx,
        role=dumped.get("role"),
        tool_calls=_tool_call_count(dumped.get("tool_calls")),
        reasoning=bool(dumped.get("reasoning")),
        reasoning_content=True,
        provider_specific_reasoning=bool(provider_specific_reasoning),
        provider_specific_reasoning_removed=bool(provider_specific_reasoning),
        provider_specific_reasoning_matches=provider_reasoning_matches,
        provider_specific_reasoning_len=provider_reasoning_len,
        provider_specific_reasoning_sha256=provider_reasoning_sha,
        reasoning_len=reasoning_len,
        reasoning_sha256=reasoning_sha,
    )

    return dumped


def _patch_function_source(
    *,
    module: ModuleType,
    function_name: str,
    replacements: dict[str, str],
    patch_name: str,
) -> None:
    function = getattr(module, function_name)
    try:
        source = inspect.getsource(function)
        filename = inspect.getsourcefile(function) or f"<{patch_name}>"
    except Exception as e:  # pragma: no cover
        _warn_or_raise(f"could not inspect {patch_name}: {e}")
        return

    patched_source = source
    for old, new in replacements.items():
        if old not in patched_source:
            _warn_or_raise(f"{patch_name} patch did not match expected upstream text")
            return
        patched_source = patched_source.replace(old, new, 1)

    namespace: dict[str, Any] = {}
    exec(compile(patched_source, filename, "exec"), function.__globals__, namespace)
    patched_function = namespace.get(function_name)
    if not callable(patched_function):
        _warn_or_raise(f"{patch_name} patch did not rebuild {function_name}")
        return

    setattr(module, function_name, functools.wraps(function)(patched_function))
    print(f"sitecustomize: patched {patch_name}", flush=True)


def _required_positive_int(var_name: str, default: int) -> int:
    raw = os.environ.get(var_name, str(default))
    try:
        value = int(raw)
    except ValueError:
        _warn_or_raise(f"{var_name} must be a positive integer, got {raw!r}")
        return default
    if value <= 0:
        _warn_or_raise(f"{var_name} must be a positive integer, got {raw!r}")
        return default
    return value


def _validate_deep_research_control_tool_batch(tool_calls) -> None:  # noqa: ANN001
    if len(tool_calls) <= 1:
        return
    control_names = {"think_tool", "generate_report"}
    mixed_controls = sorted(
        {call.tool_name for call in tool_calls if call.tool_name in control_names}
    )
    if mixed_controls:
        raise RuntimeError(
            "Deep Research control tools must be called alone; mixed batch contained "
            + ", ".join(mixed_controls)
        )


def _deep_research_sub_turn_index(index: int, stride: int = 1024) -> int:
    return index * stride


def _prepare_deep_research_tool_calls(
    tool_runner,  # noqa: ANN001
    tool_calls,  # noqa: ANN001
    *,
    max_tool_calls_per_batch: int,
    nested_placement_stride: int = 1024,
):
    """Validate, merge, and give every nested tool call a unique placement."""
    tool_calls = list(tool_calls or [])
    if len(tool_calls) > max_tool_calls_per_batch:
        raise RuntimeError(
            "Deep Research model emitted "
            f"{len(tool_calls)} tool calls in one batch; configured maximum is "
            f"{max_tool_calls_per_batch}. No calls from this batch were executed."
        )

    merged_tool_calls = tool_runner._merge_tool_calls(tool_calls)
    if not merged_tool_calls:
        return merged_tool_calls

    base_sub_turn = merged_tool_calls[0].placement.sub_turn_index
    if base_sub_turn is None:
        raise RuntimeError("Deep Research nested tool batch is missing sub_turn_index")
    return [
        tool_call.model_copy(
            update={
                "placement": tool_call.placement.model_copy(
                    update={
                        "sub_turn_index": (
                            base_sub_turn + index * nested_placement_stride
                        )
                    }
                )
            }
        )
        for index, tool_call in enumerate(merged_tool_calls)
    ]


def apply_deep_research_chat_agent_tools_patch() -> None:
    """Provide selected chat-Agent tools to nested Deep Research agents.

    Upstream filters nested agents to search/open-url tools, drops every tool
    name after the first name in an LLM batch, and uses one setting both to
    truncate a batch and to select its thread-pool size. Keep all accepted
    calls, give each merged call a distinct nested UI placement, and bound only
    execution concurrency.
    """

    max_research_cycles = _required_positive_int(
        "MAX_RESEARCH_AGENT_CYCLES", 200
    )
    max_tool_calls_per_batch = _required_positive_int(
        "MAX_DEEP_RESEARCH_TOOL_CALLS_PER_BATCH", 256
    )
    max_parallel_tools = _required_positive_int(
        "MAX_DEEP_RESEARCH_PARALLEL_TOOLS", 1
    )
    nested_placement_stride = 1024
    if max_parallel_tools > max_tool_calls_per_batch:
        _warn_or_raise(
            "MAX_DEEP_RESEARCH_PARALLEL_TOOLS cannot exceed "
            "MAX_DEEP_RESEARCH_TOOL_CALLS_PER_BATCH"
        )
        return

    try:
        from onyx.deep_research import dr_loop
        from onyx.prompts.deep_research import research_agent as research_prompts
        from onyx.tools import tool_runner
        from onyx.tools.fake_tools import research_agent
        from onyx.tools.tool_implementations.coding_agent.coding_agent_tool import (
            CodingAgentTool,
        )
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing Deep Research tool modules: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    upstream_cycle_limit = research_prompts.MAX_RESEARCH_CYCLES
    if not isinstance(upstream_cycle_limit, int) or upstream_cycle_limit <= 0:
        _warn_or_raise(
            "Deep Research cycle patch found invalid upstream "
            f"MAX_RESEARCH_CYCLES={upstream_cycle_limit!r}"
        )
        return

    cycle_fragment = f"of {upstream_cycle_limit}."
    replacement_fragment = f"of {max_research_cycles}."
    for prompt_name in ("RESEARCH_AGENT_PROMPT", "RESEARCH_AGENT_PROMPT_REASONING"):
        prompt = getattr(research_prompts, prompt_name)
        if prompt.count(cycle_fragment) != 1:
            _warn_or_raise(
                f"{prompt_name} did not contain exactly one expected cycle-limit fragment"
            )
            return
        patched_prompt = prompt.replace(cycle_fragment, replacement_fragment, 1)
        setattr(research_prompts, prompt_name, patched_prompt)
        setattr(research_agent, prompt_name, patched_prompt)

    research_prompts.MAX_RESEARCH_CYCLES = max_research_cycles
    research_agent.MAX_RESEARCH_CYCLES = max_research_cycles
    print(
        "sitecustomize: configured Deep Research nested-agent cycles "
        f"max={max_research_cycles}",
        flush=True,
    )

    if not _DEEP_RESEARCH_PROVIDE_CHAT_AGENT_TOOLS:
        print(
            "sitecustomize: Deep Research chat-Agent tools disabled; "
            "keeping upstream tool filtering",
            flush=True,
        )
        return

    try:
        run_tool_calls_source = inspect.getsource(tool_runner.run_tool_calls)
        parallel_source = inspect.getsource(
            tool_runner.run_functions_tuples_in_parallel
        )
    except Exception as e:  # pragma: no cover
        _warn_or_raise(f"could not inspect Deep Research tool runner helpers: {e}")
        return
    for expected in (
        "merged_tool_calls = _merge_tool_calls(tool_calls)",
        "filtered_tool_calls = filtered_tool_calls[:max_concurrent_tools]",
        "max_workers=max_concurrent_tools",
    ):
        if expected not in run_tool_calls_source:
            _warn_or_raise(
                "Deep Research tool runner no longer contains expected fragment "
                f"{expected!r}"
            )
            return
    if "else len(functions_with_args)" not in parallel_source:
        _warn_or_raise(
            "Deep Research thread-pool helper no longer has expected unlimited-worker behavior"
        )
        return

    original_parallel_runner = tool_runner.run_functions_tuples_in_parallel
    if not getattr(original_parallel_runner, "_wrapper_worker_limit", False):

        @functools.wraps(original_parallel_runner)
        def _parallel_runner_with_deep_research_limit(
            functions_with_args,
            allow_failures=False,
            max_workers=None,
            timeout=None,
            timeout_callback=None,
        ):
            worker_limit = _DEEP_RESEARCH_WORKER_LIMIT.get()
            if worker_limit is not None:
                max_workers = (
                    worker_limit
                    if max_workers is None
                    else min(max_workers, worker_limit)
                )
            return original_parallel_runner(
                functions_with_args,
                allow_failures=allow_failures,
                max_workers=max_workers,
                timeout=timeout,
                timeout_callback=timeout_callback,
            )

        _parallel_runner_with_deep_research_limit._wrapper_worker_limit = True
        tool_runner.run_functions_tuples_in_parallel = (
            _parallel_runner_with_deep_research_limit
        )

    original_run_tool_calls = tool_runner.run_tool_calls

    def _run_deep_research_tool_calls(*args, **kwargs):
        tool_calls = kwargs.get("tool_calls")
        if tool_calls is None and args:
            tool_calls = args[0]
        merged_tool_calls = _prepare_deep_research_tool_calls(
            tool_runner,
            tool_calls,
            max_tool_calls_per_batch=max_tool_calls_per_batch,
            nested_placement_stride=nested_placement_stride,
        )

        if args:
            args = (merged_tool_calls, *args[1:])
        else:
            kwargs["tool_calls"] = merged_tool_calls
        kwargs["max_concurrent_tools"] = None
        token = _DEEP_RESEARCH_WORKER_LIMIT.set(max_parallel_tools)
        try:
            return original_run_tool_calls(*args, **kwargs)
        finally:
            _DEEP_RESEARCH_WORKER_LIMIT.reset(token)

    coding_run_source = inspect.getsource(CodingAgentTool.run)
    if "run_coding_agent_call" not in coding_run_source:
        _warn_or_raise(
            "CodingAgentTool.run no longer invokes the expected nested coding-agent loop"
        )
        return
    original_coding_agent_run = CodingAgentTool.run
    if not getattr(original_coding_agent_run, "_wrapper_nested_placement", False):
        coding_agent_emitter_lock = threading.Lock()

        class _NestedPlacementEmitter:
            def __init__(self, emitter, base_sub_turn: int):
                self._emitter = emitter
                self._base_sub_turn = base_sub_turn

            def emit(self, packet) -> None:
                placement = packet.placement
                if placement is None:
                    self._emitter.emit(packet)
                    return
                sub_turn = placement.sub_turn_index
                is_parent_start = type(packet.obj).__name__ == "CodingAgentStart"
                mapped_sub_turn = (
                    self._base_sub_turn
                    if sub_turn is None or is_parent_start
                    else self._base_sub_turn + sub_turn
                )
                self._emitter.emit(
                    packet.model_copy(
                        update={
                            "placement": placement.model_copy(
                                update={"sub_turn_index": mapped_sub_turn}
                            )
                        }
                    )
                )

        @functools.wraps(original_coding_agent_run)
        def _coding_agent_run_with_nested_placement(
            self, placement, override_kwargs, **llm_kwargs
        ):
            if placement.sub_turn_index is None:
                return original_coding_agent_run(
                    self, placement, override_kwargs, **llm_kwargs
                )
            with coding_agent_emitter_lock:
                original_emitter = self._emitter
                self._emitter = _NestedPlacementEmitter(
                    original_emitter,
                    placement.sub_turn_index,
                )
                try:
                    return original_coding_agent_run(
                        self, placement, override_kwargs, **llm_kwargs
                    )
                finally:
                    self._emitter = original_emitter

        _coding_agent_run_with_nested_placement._wrapper_nested_placement = True
        CodingAgentTool.run = _coding_agent_run_with_nested_placement
        print(
            "sitecustomize: patched nested coding-agent UI placement",
            flush=True,
        )

    preserve_reasoning = _env_flag_default_true(
        "ONYX_AGENT_PRESERVE_TURN_REASONING"
    ) or _env_flag_enabled("ONYX_AGENT_PRESERVE_ALL_REASONING")
    dr_loop_replacements = {
        (
            "        # Filter tools to only allow web search, internal search, and open URL\n"
            "        allowed_tool_names = {SearchTool.NAME, WebSearchTool.NAME, OpenURLTool.NAME}\n"
            "        allowed_tools = [tool for tool in tools if tool.name in allowed_tool_names]\n"
        ): (
            "        # The wrapper passes the tools already selected for this chat Agent.\n"
            "        allowed_tool_names = {SearchTool.NAME, WebSearchTool.NAME, OpenURLTool.NAME}\n"
            "        allowed_tools = list(tools)\n"
        )
    }
    research_replacements = {
        (
            "                # TODO handle the restriction of only 1 tool call type per turn\n"
            "                # This is a problem right now because of the Placement system not allowing for\n"
            "                # differentiating sub-tool calls.\n"
            "                # Filter tool calls to only include the first tool type used\n"
            "                # This prevents mixing different tool types in the same batch\n"
            "                if tool_calls:\n"
            "                    first_tool_type = tool_calls[0].tool_name\n"
            "                    tool_calls = [\n"
            "                        tc for tc in tool_calls if tc.tool_name == first_tool_type\n"
            "                    ]\n"
        ): (
            "                _wrapper_validate_control_tool_batch(tool_calls)\n"
        ),
        "                    parallel_tool_call_results = run_tool_calls(\n": (
            "                    parallel_tool_call_results = _wrapper_run_tool_calls(\n"
        ),
        "                        sub_turn_index=llm_cycle_count + reasoning_cycles,\n": (
            "                        sub_turn_index=_wrapper_sub_turn_index(\n"
            "                            llm_cycle_count + reasoning_cycles\n"
            "                        ),\n"
        ),
        (
            "                        research_cycle_count += 1\n"
            "                        llm_cycle_count += 1\n"
            "                        continue\n"
        ): (
            "                        research_cycle_count += 1\n"
            "                        llm_cycle_count += max(1, len(tool_calls))\n"
            "                        continue\n"
        ),
        (
            "                most_recent_reasoning = None\n"
            "                llm_cycle_count += 1\n"
            "                research_cycle_count += 1\n"
        ): (
            "                most_recent_reasoning = None\n"
            "                llm_cycle_count += max(1, len(tool_calls))\n"
            "                research_cycle_count += 1\n"
        ),
    }
    if preserve_reasoning:
        dr_loop._wrapper_attach_reasoning_fields = _attach_reasoning_fields
        research_agent._wrapper_attach_reasoning_fields = _attach_reasoning_fields
        dr_loop_replacements[
            "                    simple_chat_history.append(assistant_with_tools)\n"
        ] = (
            "                    _wrapper_attach_reasoning_fields(\n"
            "                        assistant_with_tools,\n"
            "                        llm_step_result.reasoning or most_recent_reasoning,\n"
            "                        source=\"deep_research_llm_loop\",\n"
            "                    )\n"
            "                    simple_chat_history.append(assistant_with_tools)\n"
        )
        research_replacements[
            "                        msg_history.append(assistant_with_tools)\n"
        ] = (
            "                        _wrapper_attach_reasoning_fields(\n"
            "                            assistant_with_tools,\n"
            "                            llm_step_result.reasoning or most_recent_reasoning,\n"
            "                            source=\"research_agent_call\",\n"
            "                        )\n"
            "                        msg_history.append(assistant_with_tools)\n"
        )

    research_agent._wrapper_run_tool_calls = _run_deep_research_tool_calls
    research_agent._wrapper_validate_control_tool_batch = (
        _validate_deep_research_control_tool_batch
    )
    research_agent._wrapper_sub_turn_index = lambda index: (
        _deep_research_sub_turn_index(index, nested_placement_stride)
    )
    try:
        _patch_function_source(
            module=dr_loop,
            function_name="run_deep_research_llm_loop",
            patch_name="Deep Research selected chat-Agent tools",
            replacements=dr_loop_replacements,
        )
        _patch_function_source(
            module=research_agent,
            function_name="run_research_agent_call",
            patch_name="Deep Research nested tool batches",
            replacements=research_replacements,
        )
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch Deep Research tools: {e}", flush=True)
        _raise_if_strict()
        return

    print(
        "sitecustomize: Deep Research provides selected chat-Agent tools "
        f"batch_max={max_tool_calls_per_batch} workers={max_parallel_tools}",
        flush=True,
    )


def _truncate_text_with_notice(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 0:
        return ""

    notice_template = (
        "\n... [internal search content truncated, {omitted} characters omitted]"
    )
    # Compute the suffix using a first-pass omitted count, then recompute once
    # because suffix length can change with digit count.
    suffix = notice_template.format(omitted=len(text))
    if len(suffix) >= max_chars:
        return text[:max_chars]
    keep = max(0, max_chars - len(suffix))
    suffix = notice_template.format(omitted=len(text) - keep)
    if len(suffix) >= max_chars:
        return text[:max_chars]
    keep = max(0, max_chars - len(suffix))
    return text[:keep] + suffix


def _sanitize_fallback_text(value: Any, max_chars: int) -> str:
    if not isinstance(value, str):
        value = str(value)

    sanitized = "".join(
        ch if ch in "\n\r\t" or ch.isprintable() else "?"
        for ch in value
    )
    if len(sanitized) <= max_chars:
        return sanitized

    omitted = len(sanitized) - max_chars
    return (
        sanitized[:max_chars]
        + f"\n... [coding-agent output truncated, {omitted} characters omitted]"
    )


def _is_tool_call_response_message(message: Any) -> bool:
    message_type = getattr(message, "message_type", None)
    if getattr(message_type, "name", None) == "TOOL_CALL_RESPONSE":
        return True
    return str(message_type).endswith("TOOL_CALL_RESPONSE")


def _is_assistant_message(message: Any) -> bool:
    message_type = getattr(message, "message_type", None)
    if getattr(message_type, "name", None) == "ASSISTANT":
        return True
    return str(message_type).endswith("ASSISTANT")


def _is_user_message(message: Any) -> bool:
    message_type = getattr(message, "message_type", None)
    if getattr(message_type, "name", None) == "USER":
        return True
    return str(message_type).endswith("USER")


def _summarize_tool_call_for_final_answer(tool_call: Any) -> str:
    tool_name = getattr(tool_call, "tool_name", None) or "tool"
    tool_call_id = getattr(tool_call, "tool_call_id", None)
    arguments = getattr(tool_call, "tool_arguments", None)
    try:
        arguments_text = json.dumps(arguments, sort_keys=True)
    except Exception:
        arguments_text = str(arguments)

    label = f"{tool_name}"
    if tool_call_id:
        label += f" ({tool_call_id})"
    return f"- {label}: {arguments_text}"


def _message_reasoning_text(message: Any) -> str | None:
    reasoning = _first_non_empty_string(
        getattr(message, "reasoning_content", None),
        getattr(message, "reasoning", None),
    )
    if reasoning:
        return reasoning

    provider_specific_fields = getattr(message, "provider_specific_fields", None)
    if isinstance(provider_specific_fields, dict):
        return _first_non_empty_string(
            provider_specific_fields.get("reasoning_content")
        )
    return None


def _coding_agent_final_section_kind(line: str) -> str | None:
    if not line.startswith("## "):
        return None
    heading = line[3:]
    if heading.startswith("User message "):
        return "user_message"
    if heading.startswith("Coding agent reasoning before tool requests "):
        return "reasoning_before_tool_requests"
    if heading.startswith("Coding agent reasoning message "):
        return "assistant_reasoning"
    if heading == "Coding agent tool requests":
        return "tool_requests"
    if heading.startswith("Bash tool output"):
        return "bash_output"
    if heading.startswith("Coding agent assistant message "):
        return "assistant_message"
    if heading.startswith("Coding agent message "):
        return "other_message"
    if heading == "Final answer request":
        return "final_answer_request"
    return "unknown_heading"


def _flatten_coding_agent_final_answer_history(
    history: Any,
    token_counter: Any,
) -> Any:
    """Convert structured tool history into plain text for no-tool synthesis."""

    if not isinstance(history, list):
        return history

    try:
        from onyx.chat.models import ChatMessageSimple
        from onyx.configs.constants import MessageType
    except Exception:
        return history

    transcript_parts: list[str] = []
    saw_tool_history = False
    section_order: list[str] = []
    reasoning_digests: list[dict[str, Any]] = []
    tool_request_sections = 0
    tool_response_sections = 0
    assistant_reasoning_sections = 0

    for index, message in enumerate(history, start=1):
        message_text = getattr(message, "message", "")

        if _is_tool_call_response_message(message):
            tool_call_id = getattr(message, "tool_call_id", None)
            label = "Bash tool output"
            if tool_call_id:
                label += f" ({tool_call_id})"
            transcript_parts.append(f"## {label}\n\n{message_text}")
            saw_tool_history = True
            section_order.append("bash_output")
            tool_response_sections += 1
            continue

        tool_calls = getattr(message, "tool_calls", None)
        if _is_assistant_message(message) and tool_calls:
            reasoning_text = _message_reasoning_text(message)
            if reasoning_text:
                reasoning_len, reasoning_sha = _reasoning_digest(reasoning_text)
                transcript_parts.append(
                    f"## Coding agent reasoning before tool requests {index}\n\n"
                    f"{reasoning_text}"
                )
                section_order.append("reasoning_before_tool_requests")
                reasoning_digests.append(
                    {
                        "index": index,
                        "kind": "reasoning_before_tool_requests",
                        "len": reasoning_len,
                        "sha256": reasoning_sha,
                    }
                )
            tool_call_lines = [
                _summarize_tool_call_for_final_answer(tool_call)
                for tool_call in tool_calls
            ]
            transcript_part = (
                "## Coding agent tool requests\n\n"
                + ("\n\n".join([str(message_text), ""]) if message_text else "")
                + "\n".join(tool_call_lines)
            )
            transcript_parts.append(transcript_part)
            saw_tool_history = True
            section_order.append("tool_requests")
            tool_request_sections += 1
            continue

        if _is_user_message(message):
            transcript_parts.append(f"## User message {index}\n\n{message_text}")
            section_order.append("user_message")
            continue

        if _is_assistant_message(message):
            reasoning_text = _message_reasoning_text(message)
            if reasoning_text:
                reasoning_len, reasoning_sha = _reasoning_digest(reasoning_text)
                transcript_parts.append(
                    f"## Coding agent reasoning message {index}\n\n"
                    f"{reasoning_text}"
                )
                section_order.append("assistant_reasoning")
                assistant_reasoning_sections += 1
                reasoning_digests.append(
                    {
                        "index": index,
                        "kind": "assistant_reasoning",
                        "len": reasoning_len,
                        "sha256": reasoning_sha,
                    }
                )
            if message_text:
                transcript_parts.append(
                    f"## Coding agent assistant message {index}\n\n{message_text}"
                )
                section_order.append("assistant_message")
            continue

        if message_text:
            transcript_parts.append(f"## Coding agent message {index}\n\n{message_text}")
            section_order.append("other_message")

    if not saw_tool_history and not reasoning_digests:
        _trace_reasoning_mode(
            "coding_agent_final_answer_history_not_flattened",
            original_messages=len(history),
            reason="no_tool_history",
            section_order=section_order,
            reasoning_sections=len(reasoning_digests),
            reasoning_digests=reasoning_digests,
        )
        return history

    transcript = (
        "The following is a plain-text transcript of the coding agent's completed "
        "tool investigation. Use it as evidence for the final answer; do not treat "
        "it as an active tool-call protocol.\n\n"
        + "\n\n".join(transcript_parts)
    )
    flattened = [
        ChatMessageSimple(
            message=transcript,
            token_count=token_counter(transcript),
            message_type=MessageType.USER,
        )
    ]
    transcript_len, transcript_sha = _reasoning_digest(transcript)
    _trace_reasoning_mode(
        "coding_agent_final_answer_history_flattened",
        original_messages=len(history),
        flattened_messages=len(flattened),
        section_order=section_order,
        reasoning_sections=len(reasoning_digests),
        assistant_reasoning_sections=assistant_reasoning_sections,
        reasoning_digests=reasoning_digests,
        tool_request_sections=tool_request_sections,
        tool_response_sections=tool_response_sections,
        transcript_len=transcript_len,
        transcript_sha256=transcript_sha,
    )

    return flattened


def apply_native_reasoning_detection_override_patch() -> None:
    """Optionally force Onyx reasoning-model detection on for wrapper models."""

    if not _NATIVE_REASONING_DETECTION_OVERRIDE_ENABLED:
        print(
            "sitecustomize: native reasoning detection override disabled "
            "(ONYX_AGENT_USE_NATIVE_REASONING=false)",
            flush=True,
        )
        return

    try:
        from onyx.llm import utils as llm_utils
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing reasoning detection utils: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    original = llm_utils.model_is_reasoning_model
    if getattr(original, "_wrapper_native_reasoning_override", False):
        return
    signature = inspect.signature(original)
    if tuple(signature.parameters) != ("model_name", "model_provider"):
        _warn_or_raise(
            "llm_utils.model_is_reasoning_model signature changed; "
            f"found {signature}"
        )
        return
    try:
        source = inspect.getsource(original)
    except Exception as e:  # pragma: no cover
        _warn_or_raise(f"could not inspect model_is_reasoning_model: {e}")
        return
    for marker in ("get_model_map()", "_litellm_supports_reasoning"):
        if marker not in source:
            _warn_or_raise(
                "llm_utils.model_is_reasoning_model source contract changed; "
                f"missing {marker!r}"
            )
            return

    @functools.wraps(original)
    def _model_is_reasoning_model_native_override(  # noqa: ANN001
        model_name,
        model_provider,
    ):
        key = (str(model_name), str(model_provider))
        if key not in _NATIVE_REASONING_DETECTION_OVERRIDE_LOGGED:
            _NATIVE_REASONING_DETECTION_OVERRIDE_LOGGED.add(key)
            print(
                "sitecustomize: native reasoning detection override applied "
                f"model={json.dumps(model_name)} "
                f"provider={json.dumps(model_provider)} "
                "supports_reasoning=true",
                flush=True,
            )
        return True

    _model_is_reasoning_model_native_override._wrapper_native_reasoning_override = True
    llm_utils.model_is_reasoning_model = _model_is_reasoning_model_native_override
    _update_bound_module_attr(
        original,
        _model_is_reasoning_model_native_override,
        "model_is_reasoning_model",
    )
    print(
        "sitecustomize: forcing Onyx reasoning-model detection true "
        "(ONYX_AGENT_USE_NATIVE_REASONING=true)",
        flush=True,
    )


def apply_reasoning_mode_trace_patch() -> None:
    """Emit metadata-only traces for native-vs-simulated reasoning decisions."""

    if not _REASONING_MODE_TRACE:
        return

    try:
        from onyx.llm import utils as llm_utils
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed importing reasoning-mode utils: {e}", flush=True)
        _raise_if_strict()
        return

    original_model_is_reasoning_model = llm_utils.model_is_reasoning_model
    if not getattr(
        original_model_is_reasoning_model,
        "_wrapper_reasoning_mode_trace",
        False,
    ):

        @functools.wraps(original_model_is_reasoning_model)
        def _model_is_reasoning_model_with_trace(model_name, model_provider):  # noqa: ANN001
            result = original_model_is_reasoning_model(model_name, model_provider)
            _trace_reasoning_mode(
                "model_detection",
                caller=_caller_context(),
                model=model_name,
                provider=model_provider,
                supports_reasoning=bool(result),
            )
            return result

        _model_is_reasoning_model_with_trace._wrapper_reasoning_mode_trace = True
        llm_utils.model_is_reasoning_model = _model_is_reasoning_model_with_trace
        _update_bound_module_attr(
            original_model_is_reasoning_model,
            _model_is_reasoning_model_with_trace,
            "model_is_reasoning_model",
        )

    try:
        from onyx.chat import llm_step
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed importing reasoning-mode llm_step: {e}", flush=True)
        _raise_if_strict()
        return

    original_run_llm_step_pkt_generator = llm_step.run_llm_step_pkt_generator
    if not getattr(
        original_run_llm_step_pkt_generator,
        "_wrapper_reasoning_mode_trace",
        False,
    ):
        packet_signature = inspect.signature(original_run_llm_step_pkt_generator)

        @functools.wraps(original_run_llm_step_pkt_generator)
        def _run_llm_step_pkt_generator_with_trace(*args, **kwargs):
            bound = packet_signature.bind(*args, **kwargs)
            bound.apply_defaults()
            tool_names = _tool_names_from_definitions(
                bound.arguments.get("tool_definitions")
            )
            think_tool_offered = "think_tool" in tool_names
            custom_token_processor = bool(
                bound.arguments.get("custom_token_processor")
            )
            is_deep_research = bool(bound.arguments.get("is_deep_research"))
            llm = bound.arguments.get("llm")
            model_name = getattr(getattr(llm, "config", None), "model_name", None)
            model_provider = getattr(
                getattr(llm, "config", None),
                "model_provider",
                None,
            )
            placement = bound.arguments.get("placement")
            reasoning_effort = bound.arguments.get("reasoning_effort")
            _trace_reasoning_mode(
                "llm_step_request",
                caller=_caller_context(),
                model=model_name,
                provider=model_provider,
                tool_choice=str(bound.arguments.get("tool_choice")),
                tools=tool_names,
                tools_count=len(tool_names),
                think_tool_offered=think_tool_offered,
                custom_token_processor=custom_token_processor,
                is_deep_research=is_deep_research,
                reasoning_effort=(
                    getattr(reasoning_effort, "value", None) or str(reasoning_effort)
                ),
                turn_index=getattr(placement, "turn_index", None),
                tab_index=getattr(placement, "tab_index", None),
                sub_turn_index=getattr(placement, "sub_turn_index", None),
            )
            generator = original_run_llm_step_pkt_generator(*args, **kwargs)
            reasoning_packet_seen = False
            try:
                while True:
                    packet = next(generator)
                    obj = getattr(packet, "obj", None)
                    if type(obj).__name__ == "ReasoningDelta":
                        reasoning_packet_seen = True
                    yield packet
            except StopIteration as e:
                llm_step_result = None
                has_reasoned = None
                if isinstance(e.value, tuple) and e.value:
                    llm_step_result = e.value[0]
                    if len(e.value) > 1:
                        has_reasoned = e.value[1]
                reasoning = getattr(llm_step_result, "reasoning", None)
                reasoning_len, reasoning_sha = _reasoning_digest(reasoning)
                tool_calls = getattr(llm_step_result, "tool_calls", None)
                _trace_reasoning_mode(
                    "llm_step_result",
                    caller=_caller_context(),
                    model=model_name,
                    provider=model_provider,
                    reasoning_packet_seen=reasoning_packet_seen,
                    has_reasoned=bool(has_reasoned),
                    result_reasoning=bool(reasoning),
                    reasoning_len=reasoning_len,
                    reasoning_sha256=reasoning_sha,
                    result_answer=bool(getattr(llm_step_result, "answer", None)),
                    result_tool_calls=_tool_call_count(tool_calls),
                    think_tool_offered=think_tool_offered,
                    custom_token_processor=custom_token_processor,
                    is_deep_research=is_deep_research,
                    native_reasoning_expected=(
                        not think_tool_offered and not custom_token_processor
                    ),
                )
                return e.value

        _run_llm_step_pkt_generator_with_trace._wrapper_reasoning_mode_trace = True
        llm_step.run_llm_step_pkt_generator = _run_llm_step_pkt_generator_with_trace
        _update_bound_module_attr(
            original_run_llm_step_pkt_generator,
            _run_llm_step_pkt_generator_with_trace,
            "run_llm_step_pkt_generator",
        )

    original_run_llm_step = llm_step.run_llm_step
    if not getattr(original_run_llm_step, "_wrapper_reasoning_mode_trace", False):
        step_signature = inspect.signature(original_run_llm_step)

        @functools.wraps(original_run_llm_step)
        def _run_llm_step_with_trace(*args, **kwargs):
            bound = step_signature.bind(*args, **kwargs)
            bound.apply_defaults()
            tool_names = _tool_names_from_definitions(
                bound.arguments.get("tool_definitions")
            )
            llm = bound.arguments.get("llm")
            model_name = getattr(getattr(llm, "config", None), "model_name", None)
            model_provider = getattr(
                getattr(llm, "config", None),
                "model_provider",
                None,
            )
            placement = bound.arguments.get("placement")
            reasoning_effort = bound.arguments.get("reasoning_effort")
            _trace_reasoning_mode(
                "llm_step_call",
                caller=_caller_context(),
                model=model_name,
                provider=model_provider,
                tool_choice=str(bound.arguments.get("tool_choice")),
                tools=tool_names,
                tools_count=len(tool_names),
                think_tool_offered="think_tool" in tool_names,
                custom_token_processor=bool(
                    bound.arguments.get("custom_token_processor")
                ),
                is_deep_research=bool(bound.arguments.get("is_deep_research")),
                reasoning_effort=(
                    getattr(reasoning_effort, "value", None) or str(reasoning_effort)
                ),
                turn_index=getattr(placement, "turn_index", None),
                tab_index=getattr(placement, "tab_index", None),
                sub_turn_index=getattr(placement, "sub_turn_index", None),
            )
            return original_run_llm_step(*args, **kwargs)

        _run_llm_step_with_trace._wrapper_reasoning_mode_trace = True
        llm_step.run_llm_step = _run_llm_step_with_trace
        _update_bound_module_attr(
            original_run_llm_step,
            _run_llm_step_with_trace,
            "run_llm_step",
        )

    try:
        from onyx.deep_research import utils as dr_utils
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing reasoning-mode deep-research utils: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    original_create_processor = dr_utils.create_think_tool_token_processor
    if not getattr(original_create_processor, "_wrapper_reasoning_mode_trace", False):

        @functools.wraps(original_create_processor)
        def _create_think_tool_token_processor_with_trace(*args, **kwargs):
            _trace_reasoning_mode(
                "think_tool_processor_created",
                caller=_caller_context(),
            )
            processor = original_create_processor(*args, **kwargs)

            @functools.wraps(processor)
            def _think_tool_processor_with_trace(delta, state):  # noqa: ANN001
                if delta is None:
                    _trace_reasoning_mode(
                        "think_tool_processor_flush",
                        caller=_caller_context(),
                        had_state=state is not None,
                    )
                else:
                    tool_names = []
                    for tool_call in getattr(delta, "tool_calls", None) or []:
                        function = getattr(tool_call, "function", None)
                        name = getattr(function, "name", None)
                        if name:
                            tool_names.append(name)
                    if "think_tool" in tool_names:
                        _trace_reasoning_mode(
                            "think_tool_delta_observed",
                            caller=_caller_context(),
                            tool_names=tool_names,
                        )

                modified_delta, new_state = processor(delta, state)
                if modified_delta is not None:
                    reasoning_content = getattr(
                        modified_delta,
                        "reasoning_content",
                        None,
                    )
                    reasoning_len, reasoning_sha = _reasoning_digest(reasoning_content)
                    modified_tool_names = []
                    for tool_call in getattr(modified_delta, "tool_calls", None) or []:
                        function = getattr(tool_call, "function", None)
                        name = getattr(function, "name", None)
                        if name:
                            modified_tool_names.append(name)
                    _trace_reasoning_mode(
                        "think_tool_processor_output",
                        caller=_caller_context(),
                        emitted_reasoning=bool(reasoning_content),
                        reasoning_len=reasoning_len,
                        reasoning_sha256=reasoning_sha,
                        emitted_tool_names=modified_tool_names,
                    )
                return modified_delta, new_state

            return _think_tool_processor_with_trace

        _create_think_tool_token_processor_with_trace._wrapper_reasoning_mode_trace = True
        dr_utils.create_think_tool_token_processor = (
            _create_think_tool_token_processor_with_trace
        )
        _update_bound_module_attr(
            original_create_processor,
            _create_think_tool_token_processor_with_trace,
            "create_think_tool_token_processor",
        )

    print("sitecustomize: patched reasoning mode trace", flush=True)


def _coding_agent_final_answer_fallback(history: Any, error: BaseException) -> str:
    tool_outputs: list[tuple[str | None, str]] = []
    if isinstance(history, list):
        for message in history:
            if not _is_tool_call_response_message(message):
                continue
            output = getattr(message, "message", None)
            if not output:
                continue
            tool_call_id = getattr(message, "tool_call_id", None)
            tool_outputs.append(
                (str(tool_call_id) if tool_call_id else None, str(output))
            )

    header = (
        "The coding agent executed its tool steps, but the final answer LLM "
        "call failed before it could summarize them. Returning the collected "
        f"tool output instead. Finalization error: {type(error).__name__}."
    )
    if not tool_outputs:
        return header + "\n\nNo bash tool output was available in the agent history."

    recent_outputs = tool_outputs[-6:]
    remaining_chars = 16000
    chunks: list[str] = []
    for index, (tool_call_id, output) in enumerate(recent_outputs, start=1):
        remaining_slots = len(recent_outputs) - index + 1
        max_output_chars = max(1000, remaining_chars // remaining_slots)
        sanitized = _sanitize_fallback_text(output, max_output_chars)
        label = f"Tool output {index}"
        if tool_call_id:
            label += f" ({tool_call_id})"
        chunk = f"{label}:\n```text\n{sanitized}\n```"
        chunks.append(chunk)
        remaining_chars = max(0, remaining_chars - len(chunk))

    return header + "\n\n" + "\n\n".join(chunks)


def apply_vllm_glm_auto_tool_choice_patch() -> None:
    """Avoid vLLM's broken forced-tool path in wrapper agent workflows.

    vLLM 0.24.0 moved GLM-5.x onto its unified parser/structural-tag engine
    and removed the previous GLM safeguard that skipped structured decoding
    for ``tool_choice=required``. Keep the workaround scoped to the internal
    coding-agent loop; its existing no-tool branch safely proceeds to final
    answer synthesis when the model returns ordinary assistant content.
    """

    try:
        from onyx.chat import llm_loop
        from onyx.deep_research import dr_loop
        from onyx.tools.fake_tools import coding_agent
        from onyx.tools.fake_tools import research_agent
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing agent tool-choice modules: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    targets = (
        (
            "coding-agent",
            coding_agent,
            "run_coding_agent_call",
            "tool_choice=ToolChoiceOptions.REQUIRED,",
            "run_llm_step_pkt_generator",
        ),
        (
            "deep-research orchestrator",
            dr_loop,
            "run_deep_research_llm_loop",
            "tool_choice=ToolChoiceOptions.REQUIRED,",
            "run_llm_step",
        ),
        (
            "nested research-agent",
            research_agent,
            "run_research_agent_call",
            "tool_choice=ToolChoiceOptions.REQUIRED,",
            "run_llm_step",
        ),
        (
            "explicit chat tool forcing",
            llm_loop,
            "run_llm_loop",
            "tool_choice = ToolChoiceOptions.REQUIRED",
            "run_llm_step",
        ),
    )

    for label, module, owner_function_name, expected, step_function_name in targets:
        try:
            source = inspect.getsource(getattr(module, owner_function_name))
        except Exception as e:  # pragma: no cover
            _warn_or_raise(f"could not inspect {label} tool-choice call site: {e}")
            continue
        if source.count(expected) != 1:
            _warn_or_raise(
                f"{label} automatic tool-choice patch expected exactly one "
                f"forced-tool call site, found {source.count(expected)}"
            )
            continue

        original_step = getattr(module, step_function_name)
        if getattr(original_step, "_wrapper_vllm_glm_auto_choice", False):
            continue

        def _make_auto_choice_wrapper(original, owner_module):
            @functools.wraps(original)
            def _step_with_auto_choice(*args, **kwargs):
                if (
                    kwargs.get("tool_choice")
                    == owner_module.ToolChoiceOptions.REQUIRED
                ):
                    kwargs["tool_choice"] = owner_module.ToolChoiceOptions.AUTO
                return original(*args, **kwargs)

            _step_with_auto_choice._wrapper_vllm_glm_auto_choice = True
            return _step_with_auto_choice

        setattr(
            module,
            step_function_name,
            _make_auto_choice_wrapper(original_step, module),
        )
        print(f"sitecustomize: patched {label} automatic tool choice", flush=True)


def _coding_agent_flattened_final_answer(
    coding_agent: Any,
    *,
    query: str,
    repo: str,
    history: list[Any],
    llm: Any,
    token_counter: Any,
    user_identity: Any,
    emitter: Any,
    placement: Any,
) -> str:
    """Run code-agent final synthesis without tool-protocol history."""

    llm_messages = [
        {
            "role": "system",
            "content": coding_agent.CODING_AGENT_FINAL_ANSWER_PROMPT,
        }
    ]
    reminder_str = coding_agent.USER_FINAL_ANSWER_QUERY.format(query=query, repo=repo)
    transcript = history[0].message if history else ""
    user_message = (
        f"{transcript}\n\n"
        "## Final answer request\n\n"
        f"{reminder_str}"
    )
    llm_messages.append({"role": "user", "content": user_message})
    if _CODING_AGENT_FINAL_TRACE_ENABLED:
        section_order = [
            kind
            for kind in (
                _coding_agent_final_section_kind(line)
                for line in user_message.splitlines()
            )
            if kind is not None
        ]
        try:
            json.dumps({"messages": llm_messages})
            json_ok = True
        except Exception:
            json_ok = False
        user_control_chars = sum(
            1
            for ch in user_message
            if ord(ch) < 32 and ch not in "\n\r\t"
        )
        _trace_reasoning_mode(
            "coding_agent_final_summarizer_request",
            roles=["system", "user"],
            reasoning_effort="off",
            tools_arg="none",
            message_count=len(llm_messages),
            user_chars=len(user_message),
            user_newlines=user_message.count(chr(10)),
            user_backslashes=user_message.count(chr(92)),
            user_quotes=user_message.count(chr(34)),
            user_control_chars=user_control_chars,
            user_sha256=hashlib.sha256(user_message.encode()).hexdigest()[:12],
            json_encoding_ok=json_ok,
            section_order=section_order,
            reasoning_sections=section_order.count(
                "reasoning_before_tool_requests"
            )
            + section_order.count("assistant_reasoning"),
            tool_request_sections=section_order.count("tool_requests"),
            tool_response_sections=section_order.count("bash_output"),
        )

    with coding_agent.function_span("generate_coding_agent_answer") as span:
        span.span_data.input = f"history_length={len(history)} flattened=true"
        final_answer_chunks: list[str] = []
        for packet in llm.stream(
            prompt=llm_messages,
            max_tokens=coding_agent.MAX_FINAL_ANSWER_TOKENS,
            reasoning_effort=coding_agent.ReasoningEffort.OFF,
            user_identity=user_identity,
        ):
            delta = packet.choice.delta
            if delta.content:
                final_answer_chunks.append(delta.content)

        final_answer = "".join(final_answer_chunks).strip()
        if not final_answer:
            raise ValueError("LLM failed to produce a final answer")
        span.span_data.output = final_answer
        return final_answer


def apply_coding_agent_final_answer_fallback_patch() -> None:
    """Return gathered code-agent tool output if final answer synthesis fails.

    Upstream catches every coding-agent exception and returns ``None`` to the
    outer tool wrapper. That loses successful bash output when only the final
    no-tool summarization LLM call fails. Keep setup/session failures as hard
    errors, but make finalization failures user-visible.
    """

    try:
        from onyx.tools.fake_tools import coding_agent
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing coding-agent final-answer module: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    original = coding_agent._generate_final_answer
    if getattr(original, "_wrapper_final_answer_fallback", False):
        return

    try:
        source = inspect.getsource(original)
    except Exception as e:  # pragma: no cover
        _warn_or_raise(f"could not inspect coding-agent final answer function: {e}")
        return

    if "LLM failed to produce a final answer" not in source:
        _warn_or_raise(
            "coding-agent final answer fallback patch did not match expected "
            "upstream text"
        )
        return

    signature = inspect.signature(original)

    @functools.wraps(original)
    def _generate_final_answer_with_fallback(*args, **kwargs):
        original_history = None
        flattened_history = None
        try:
            bound = signature.bind(*args, **kwargs)
            history = bound.arguments.get("history")
            original_history = history
            token_counter = bound.arguments.get("token_counter")
            if token_counter is not None:
                flattened_history = _flatten_coding_agent_final_answer_history(
                    history,
                    token_counter,
                )
                if flattened_history is not history:
                    return _coding_agent_flattened_final_answer(
                        coding_agent,
                        query=bound.arguments["query"],
                        repo=bound.arguments["repo"],
                        history=flattened_history,
                        llm=bound.arguments["llm"],
                        token_counter=token_counter,
                        user_identity=bound.arguments["user_identity"],
                        emitter=bound.arguments["emitter"],
                        placement=bound.arguments["placement"],
                    )
        except Exception as e:
            if flattened_history is None:
                print(
                    "sitecustomize: failed to flatten coding-agent final answer "
                    f"history; using original structured history ({type(e).__name__})",
                    flush=True,
                )
            else:
                print(
                    "sitecustomize: flattened coding-agent final answer "
                    f"generation failed ({type(e).__name__})",
                    flush=True,
                )
                return _coding_agent_final_answer_fallback(original_history, e)

        try:
            return original(*args, **kwargs)
        except Exception as e:
            try:
                bound = signature.bind(*args, **kwargs)
                history = original_history or bound.arguments.get("history")
            except Exception:
                history = original_history or kwargs.get("history")

            print(
                "sitecustomize: coding-agent final answer generation failed; "
                "returning tool-output fallback "
                f"({type(e).__name__})",
                flush=True,
            )
            return _coding_agent_final_answer_fallback(history, e)

    _generate_final_answer_with_fallback._wrapper_final_answer_fallback = True
    coding_agent._generate_final_answer = _generate_final_answer_with_fallback
    print(
        "sitecustomize: patched coding-agent final answer fallback",
        flush=True,
    )


def apply_reasoning_content_preservation_patch() -> None:
    """Preserve assistant reasoning fields when Onyx rebuilds LLM history.

    Onyx stores model reasoning text as ``reasoning_tokens`` on chat messages
    and tool-call rows, but its lightweight ``ChatMessageSimple`` and
    ``AssistantMessage`` request models do not carry that text back into later
    LiteLLM requests. Reasoning-capable OpenAI-compatible models, including
    GLM and Kimi variants served through teep, may need that prior reasoning
    beside assistant tool-call messages when a tool response follows.
    """

    if _REASONING_REMINDER_REORDER_ENABLED:
        try:
            from onyx.chat import llm_loop

            _patch_function_source(
                module=llm_loop,
                function_name="construct_message_history",
                patch_name="chat reminder placement for reasoning preservation",
                replacements={
                    (
                        "    # 5. Add last user message (with context images attached)\n"
                        "    result.append(last_user_message)\n"
                        "\n"
                        "    # 6. Add messages after last user message (tool calls, responses, etc.)\n"
                        "    result.extend(messages_after_last_user)\n"
                        "\n"
                        "    # 7. Add reminder message at the very end\n"
                        "    if reminder_message:\n"
                        "        result.append(reminder_message)\n"
                    ): (
                        "    # 5. Add last user message (with context images attached)\n"
                        "    result.append(last_user_message)\n"
                        "\n"
                        "    # 6. Keep reminders adjacent to the user request instead of\n"
                        "    # trailing after assistant/tool messages. Some reasoning model\n"
                        "    # templates discard prior assistant reasoning fields when a tool\n"
                        "    # turn is followed by a final user-role reminder.\n"
                        "    if reminder_message:\n"
                        "        result.append(reminder_message)\n"
                        "\n"
                        "    # 7. Add messages after last user message (tool calls, responses, etc.)\n"
                        "    result.extend(messages_after_last_user)\n"
                    )
                },
            )
        except Exception as e:  # pragma: no cover
            print(
                f"sitecustomize: failed to patch chat reminder placement: {e}",
                flush=True,
            )
            _raise_if_strict()

    preserve_turn_reasoning = _env_flag_default_true(
        "ONYX_AGENT_PRESERVE_TURN_REASONING"
    )
    preserve_all_reasoning = _env_flag_enabled("ONYX_AGENT_PRESERVE_ALL_REASONING")

    if not (preserve_turn_reasoning or preserve_all_reasoning):
        return

    try:
        from onyx.chat import chat_utils
        from onyx.chat import chat_state
        from onyx.configs.constants import MessageType
        from onyx.llm import multi_llm
        from onyx.chat import llm_step
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing reasoning preservation modules: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    try:
        structured_source = inspect.getsource(
            llm_step._build_structured_assistant_message
        )
        prompt_dump_source = inspect.getsource(multi_llm._prompt_to_dicts)
    except Exception as e:  # pragma: no cover
        _warn_or_raise(f"could not inspect reasoning preservation helpers: {e}")
        return

    if "tool_calls=tool_calls_list" not in structured_source:
        _warn_or_raise(
            "llm_step._build_structured_assistant_message no longer contains "
            "the expected tool_calls assignment"
        )
        return
    if "model_dump(exclude_none=True)" not in prompt_dump_source:
        _warn_or_raise(
            "multi_llm._prompt_to_dicts no longer contains the expected "
            "Pydantic model_dump serialization"
        )
        return

    original_build_structured_assistant_message = (
        llm_step._build_structured_assistant_message
    )
    original_convert_chat_history = chat_utils.convert_chat_history
    original_set_reasoning_tokens = chat_state.ChatStateContainer.set_reasoning_tokens

    try:
        import httpx
        from litellm.llms.openai.chat.gpt_transformation import OpenAIGPTConfig

        original_litellm_transform_request = OpenAIGPTConfig.transform_request
        original_litellm_async_transform_request = (
            OpenAIGPTConfig.async_transform_request
        )
        original_httpx_client_send = httpx.Client.send
        original_httpx_async_client_send = httpx.AsyncClient.send
    except Exception as e:  # pragma: no cover
        _warn_or_raise(f"could not import LiteLLM/httpx trace targets: {e}")
        return

    @functools.wraps(original_set_reasoning_tokens)
    def _reasoning_set_reasoning_tokens(self, reasoning):  # noqa: ANN001
        reasoning_len, reasoning_sha = _reasoning_digest(reasoning)
        _trace_reasoning(
            "state_set_reasoning_tokens",
            reasoning=bool(reasoning),
            reasoning_len=reasoning_len,
            reasoning_sha256=reasoning_sha,
        )
        return original_set_reasoning_tokens(self, reasoning)

    @functools.wraps(original_litellm_transform_request)
    def _reasoning_litellm_transform_request(self, *args, **kwargs):
        data = original_litellm_transform_request(self, *args, **kwargs)
        _trace_reasoning_message_census(
            "litellm_openai_transform_request",
            data.get("messages") if isinstance(data, dict) else None,
            has_stream=bool(data.get("stream")) if isinstance(data, dict) else False,
        )
        return data

    @functools.wraps(original_litellm_async_transform_request)
    async def _reasoning_litellm_async_transform_request(self, *args, **kwargs):
        data = await original_litellm_async_transform_request(self, *args, **kwargs)
        _trace_reasoning_message_census(
            "litellm_openai_async_transform_request",
            data.get("messages") if isinstance(data, dict) else None,
            has_stream=bool(data.get("stream")) if isinstance(data, dict) else False,
        )
        return data

    def _httpx_request_body(request):  # noqa: ANN001
        try:
            return request.content
        except Exception:
            return None

    def _should_trace_httpx_chat_request(request):  # noqa: ANN001
        try:
            return (
                request.method == "POST"
                and request.url.path.endswith("/chat/completions")
            )
        except Exception:
            return False

    @functools.wraps(original_httpx_client_send)
    def _reasoning_httpx_client_send(self, request, *args, **kwargs):  # noqa: ANN001
        if _should_trace_httpx_chat_request(request):
            _trace_reasoning_request_body(
                "httpx_outbound_chat_completions",
                _httpx_request_body(request),
                host=getattr(request.url, "host", None),
                path=getattr(request.url, "path", None),
            )
        return original_httpx_client_send(self, request, *args, **kwargs)

    @functools.wraps(original_httpx_async_client_send)
    async def _reasoning_httpx_async_client_send(
        self,
        request,
        *args,
        **kwargs,
    ):  # noqa: ANN001
        if _should_trace_httpx_chat_request(request):
            _trace_reasoning_request_body(
                "httpx_async_outbound_chat_completions",
                _httpx_request_body(request),
                host=getattr(request.url, "host", None),
                path=getattr(request.url, "path", None),
            )
        return await original_httpx_async_client_send(self, request, *args, **kwargs)

    @functools.wraps(original_build_structured_assistant_message)
    def _reasoning_structured_assistant_message(*args, **kwargs):
        assistant_message = original_build_structured_assistant_message(
            *args, **kwargs
        )
        msg = args[0] if args else kwargs.get("msg")
        reasoning_content = _first_non_empty_string(
            getattr(msg, "reasoning_content", None) if msg is not None else None,
            getattr(msg, "reasoning", None) if msg is not None else None,
        )
        _attach_reasoning_fields(
            assistant_message,
            reasoning_content,
            source="structured_assistant_message",
        )
        return assistant_message

    @functools.wraps(original_convert_chat_history)
    def _reasoning_convert_chat_history(*args, **kwargs):
        result = original_convert_chat_history(*args, **kwargs)
        chat_history = kwargs.get("chat_history")
        if chat_history is None and args:
            chat_history = args[0]
        if not chat_history:
            return result

        assistant_reasoning: list[str | None] = []
        for chat_message in chat_history:
            if chat_message.message_type != MessageType.ASSISTANT:
                continue

            if chat_message.tool_calls:
                tool_calls_by_turn: dict[int, list[Any]] = {}
                for tool_call in chat_message.tool_calls:
                    turn_number = getattr(tool_call, "turn_number", None)
                    if turn_number is None:
                        continue
                    tool_calls_by_turn.setdefault(turn_number, []).append(tool_call)

                for turn_number in sorted(tool_calls_by_turn.keys()):
                    turn_tool_calls = tool_calls_by_turn[turn_number]
                    turn_tool_calls.sort(key=lambda tc: getattr(tc, "tool_id", 0))
                    assistant_reasoning.append(
                        _first_non_empty_string(
                            *[
                                getattr(tool_call, "reasoning_tokens", None)
                                for tool_call in turn_tool_calls
                            ]
                        )
                    )

            assistant_reasoning.append(
                _first_non_empty_string(
                    getattr(chat_message, "reasoning_tokens", None)
                )
            )

        last_user_idx = max(
            (
                idx
                for idx, simple_message in enumerate(result.simple_messages)
                if simple_message.message_type == MessageType.USER
            ),
            default=-1,
        )
        attached_reasoning = 0
        skipped_reasoning = 0

        reasoning_iter = iter(assistant_reasoning)
        for idx, simple_message in enumerate(result.simple_messages):
            if simple_message.message_type == MessageType.ASSISTANT:
                reasoning_content = next(reasoning_iter, None)
                should_attach_reasoning = preserve_all_reasoning or (
                    preserve_turn_reasoning
                    and last_user_idx >= 0
                    and idx > last_user_idx
                )
                if not should_attach_reasoning:
                    if reasoning_content:
                        skipped_reasoning += 1
                    continue

                _attach_reasoning_fields(
                    simple_message,
                    reasoning_content,
                    source="convert_chat_history",
                )
                if reasoning_content:
                    attached_reasoning += 1

        _trace_reasoning(
            "convert_chat_history_reasoning_scope",
            preserve_turn_reasoning=preserve_turn_reasoning,
            preserve_all_reasoning=preserve_all_reasoning,
            last_user_idx=last_user_idx,
            attached_reasoning=attached_reasoning,
            skipped_reasoning=skipped_reasoning,
        )

        return result

    def _reasoning_prompt_to_dicts(prompt):
        _enable_litellm_reasoning_trace_debug()
        if isinstance(prompt, list):
            messages = [
                _dump_message_with_reasoning_fields(msg, idx=idx)
                for idx, msg in enumerate(prompt)
            ]
        else:
            messages = [_dump_message_with_reasoning_fields(prompt, idx=0)]
        _trace_reasoning(
            "litellm_prompt_to_dicts",
            message_count=len(messages),
            role_counts=_message_role_counts(messages),
            role_sequence=[msg.get("role") for msg in messages],
            user_messages=sum(1 for msg in messages if msg.get("role") == "user"),
            assistant_with_reasoning=sum(
                1
                for msg in messages
                if msg.get("role") == "assistant" and msg.get("reasoning_content")
            ),
            assistant_reasoning_indexes=[
                idx
                for idx, msg in enumerate(messages)
                if msg.get("role") == "assistant" and msg.get("reasoning_content")
            ],
            assistant_with_reasoning_alias=sum(
                1
                for msg in messages
                if msg.get("role") == "assistant" and msg.get("reasoning")
            ),
            assistant_with_tool_calls=sum(
                1
                for msg in messages
                if msg.get("role") == "assistant" and msg.get("tool_calls")
            ),
        )
        return messages

    chat_state.ChatStateContainer.set_reasoning_tokens = _reasoning_set_reasoning_tokens
    OpenAIGPTConfig.transform_request = _reasoning_litellm_transform_request
    OpenAIGPTConfig.async_transform_request = _reasoning_litellm_async_transform_request
    httpx.Client.send = _reasoning_httpx_client_send
    httpx.AsyncClient.send = _reasoning_httpx_async_client_send
    llm_step._build_structured_assistant_message = (
        _reasoning_structured_assistant_message
    )
    multi_llm._prompt_to_dicts = _reasoning_prompt_to_dicts
    chat_utils.convert_chat_history = _reasoning_convert_chat_history

    try:
        from onyx.chat import llm_loop

        llm_loop._wrapper_attach_reasoning_fields = _attach_reasoning_fields
        _patch_function_source(
            module=llm_loop,
            function_name="run_llm_loop",
            patch_name="chat llm_loop reasoning preservation",
            replacements={
                "                simple_chat_history.append(assistant_with_tools)\n": (
                    "                _wrapper_attach_reasoning_fields(\n"
                    "                    assistant_with_tools,\n"
                    "                    llm_step_result.reasoning,\n"
                    "                    source=\"run_llm_loop\",\n"
                    "                )\n"
                    "                simple_chat_history.append(assistant_with_tools)\n"
                )
            },
        )
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch chat llm loop reasoning: {e}", flush=True)
        _raise_if_strict()

    for module_name, function_name, replacements in [
        (
            "onyx.deep_research.dr_loop",
            "run_deep_research_llm_loop",
            {
                "                    simple_chat_history.append(assistant_with_tools)\n": (
                    "                    _wrapper_attach_reasoning_fields(\n"
                    "                        assistant_with_tools,\n"
                    "                        llm_step_result.reasoning or most_recent_reasoning,\n"
                    "                        source=\"deep_research_llm_loop\",\n"
                    "                    )\n"
                    "                    simple_chat_history.append(assistant_with_tools)\n"
                )
            },
        ),
        (
            "onyx.tools.fake_tools.research_agent",
            "run_research_agent_call",
            {
                "                        msg_history.append(assistant_with_tools)\n": (
                    "                        _wrapper_attach_reasoning_fields(\n"
                    "                            assistant_with_tools,\n"
                    "                            llm_step_result.reasoning or most_recent_reasoning,\n"
                    "                            source=\"research_agent_call\",\n"
                    "                        )\n"
                    "                        msg_history.append(assistant_with_tools)\n"
                )
            },
        ),
        (
            "onyx.tools.fake_tools.coding_agent",
            "run_coding_agent_call",
            {
                "                    msg_history.append(assistant_with_tools)\n": (
                    "                    _wrapper_attach_reasoning_fields(\n"
                    "                        assistant_with_tools,\n"
                    "                        llm_step_result.reasoning or most_recent_reasoning,\n"
                    "                        source=\"coding_agent_call\",\n"
                    "                    )\n"
                    "                    msg_history.append(assistant_with_tools)\n"
                ),
                (
                    "                    if not tool_calls:\n"
                    "                        logger.warning(\n"
                    "                            \"Coding agent LLM produced no tool calls; \"\n"
                    "                            \"forcing final answer.\"\n"
                    "                        )\n"
                    "                        break\n"
                ): (
                    "                    if not tool_calls:\n"
                    "                        final_assistant_msg = ChatMessageSimple(\n"
                    "                            message=llm_step_result.answer or \"\",\n"
                    "                            token_count=token_counter(\n"
                    "                                llm_step_result.answer or \"\"\n"
                    "                            ),\n"
                    "                            message_type=MessageType.ASSISTANT,\n"
                    "                            tool_calls=None,\n"
                    "                            image_files=None,\n"
                    "                        )\n"
                    "                        _wrapper_attach_reasoning_fields(\n"
                    "                            final_assistant_msg,\n"
                    "                            llm_step_result.reasoning or most_recent_reasoning,\n"
                    "                            source=\"coding_agent_call_no_tool\",\n"
                    "                        )\n"
                    "                        msg_history.append(final_assistant_msg)\n"
                    "                        logger.warning(\n"
                    "                            \"Coding agent LLM produced no tool calls; \"\n"
                    "                            \"forcing final answer.\"\n"
                    "                        )\n"
                    "                        break\n"
                ),
            },
        ),
    ]:
        if _DEEP_RESEARCH_PROVIDE_CHAT_AGENT_TOOLS and module_name in {
            "onyx.deep_research.dr_loop",
            "onyx.tools.fake_tools.research_agent",
        }:
            # The selected-tools patch composes these source edits with its own
            # changes so a later recompilation cannot replace either patch.
            continue
        try:
            module = __import__(module_name, fromlist=[function_name])
            module._wrapper_attach_reasoning_fields = _attach_reasoning_fields
            _patch_function_source(
                module=module,
                function_name=function_name,
                patch_name=f"{module_name}.{function_name} reasoning preservation",
                replacements=replacements,
            )
        except Exception as e:  # pragma: no cover
            print(
                f"sitecustomize: failed to patch {module_name} reasoning: {e}",
                flush=True,
            )
            _raise_if_strict()

    print(
        "sitecustomize: preserving assistant reasoning_content in LLM history "
        f"turn={preserve_turn_reasoning} all={preserve_all_reasoning}",
        flush=True,
    )


def apply_llm_max_tokens_override_patch() -> None:
    """Make GEN_AI_MAX_TOKENS override DB and provider context limits.

    Onyx lets GEN_AI_MAX_TOKENS override provider/LiteLLM fallback metadata,
    but the normal chat construction path checks stored
    ModelConfiguration.max_input_tokens first. The wrapper exposes this as an
    admin override, so a configured value must win before the DB lookup too.
    """

    try:
        from onyx.configs.model_configs import GEN_AI_MAX_TOKENS
        from onyx.llm import factory
        from onyx.llm import utils as llm_utils
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing LLM max-token override modules: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    if not GEN_AI_MAX_TOKENS:
        return

    try:
        configured_source = inspect.getsource(
            factory._get_model_configured_max_input_tokens
        )
        provider_source = inspect.getsource(
            llm_utils.get_max_input_tokens_from_llm_provider
        )
    except Exception as e:  # pragma: no cover
        _warn_or_raise(f"could not inspect LLM max-token helpers: {e}")
        return

    if "model_configuration.max_input_tokens" not in configured_source:
        _warn_or_raise(
            "factory._get_model_configured_max_input_tokens no longer "
            "contains the expected DB max_input_tokens lookup"
        )
        return
    if "model_configuration.max_input_tokens" not in provider_source:
        _warn_or_raise(
            "llm_utils.get_max_input_tokens_from_llm_provider no longer "
            "contains the expected DB max_input_tokens lookup"
        )
        return

    original_get_model_configured_max_input_tokens = (
        factory._get_model_configured_max_input_tokens
    )
    original_get_max_input_tokens_from_llm_provider = (
        llm_utils.get_max_input_tokens_from_llm_provider
    )

    @functools.wraps(original_get_model_configured_max_input_tokens)
    def _override_model_configured_max_input_tokens(*args, **kwargs):
        return GEN_AI_MAX_TOKENS

    @functools.wraps(original_get_max_input_tokens_from_llm_provider)
    def _override_max_input_tokens_from_llm_provider(*args, **kwargs):
        return GEN_AI_MAX_TOKENS

    factory._get_model_configured_max_input_tokens = (
        _override_model_configured_max_input_tokens
    )
    factory.get_max_input_tokens_from_llm_provider = (
        _override_max_input_tokens_from_llm_provider
    )
    llm_utils.get_max_input_tokens_from_llm_provider = (
        _override_max_input_tokens_from_llm_provider
    )
    print(
        "sitecustomize: forcing LLM max input tokens to "
        f"GEN_AI_MAX_TOKENS={GEN_AI_MAX_TOKENS}",
        flush=True,
    )


def apply_embedding_tokenizer_alias_patch() -> None:
    """Keep Onyx's intentional fake nomic model name without a network lookup.

    The saved v23 name activates Onyx's nomic-family RAG behavior, while the
    local embedding shim selects the real upstream model. Onyx otherwise asks
    Hugging Face for a nonexistent tokenizer and then falls back to the bundled
    v1 tokenizer. Map only tokenizer construction to that same fallback model;
    the saved model name and every feature gate remain unchanged.
    """

    fake_model = "nomic-ai/nomic-embed-text-v23"
    tokenizer_model = "nomic-ai/nomic-embed-text-v1"

    try:
        from onyx.natural_language_processing import utils as nlp_utils
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing embedding tokenizer module: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    original_init = nlp_utils.HuggingFaceTokenizer.__init__
    try:
        source = inspect.getsource(original_init)
    except Exception as e:  # pragma: no cover
        _warn_or_raise(f"could not inspect HuggingFaceTokenizer.__init__: {e}")
        return

    if "Tokenizer.from_pretrained(model_name)" not in source:
        _warn_or_raise(
            "HuggingFaceTokenizer.__init__ no longer contains the expected "
            "from_pretrained(model_name) call"
        )
        return

    @functools.wraps(original_init)
    def _aliased_init(self, model_name: str):
        return original_init(
            self,
            tokenizer_model if model_name == fake_model else model_name,
        )

    nlp_utils.HuggingFaceTokenizer.__init__ = _aliased_init
    print(
        "sitecustomize: mapped fake nomic v23 tokenizer to bundled nomic v1 "
        "without changing the saved embedding model name",
        flush=True,
    )


def apply_internal_search_context_patches() -> None:
    """Apply optional character caps to Onyx internal search payloads.

    The wrapper leaves Onyx's own candidate and section-count logic untouched.
    These caps run after Onyx has merged/expanded sections and formatted the
    LLM-facing JSON.
    """

    max_chars_per_result = _parse_optional_positive_int(
        "ONYX_RAG_INTERNAL_SEARCH_MAX_CONTENT_CHARS_PER_RESULT"
    )
    max_total_chars = _parse_optional_positive_int(
        "ONYX_RAG_INTERNAL_SEARCH_MAX_TOTAL_CONTENT_CHARS"
    )

    if max_chars_per_result is None and max_total_chars is None:
        return

    try:
        from onyx.tools.tool_implementations import utils as tool_utils
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing search formatter module: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    original_convert = tool_utils.convert_inference_sections_to_llm_string
    signature = inspect.signature(original_convert)
    if tuple(signature.parameters) != (
        "top_sections",
        "citation_start",
        "limit",
        "include_source_type",
        "include_link",
        "include_document_id",
    ):
        _warn_or_raise(
            "convert_inference_sections_to_llm_string signature changed; "
            f"found {signature}"
        )
        return
    try:
        source = inspect.getsource(original_convert)
    except Exception as e:  # pragma: no cover
        _warn_or_raise(
            "could not inspect convert_inference_sections_to_llm_string: "
            f"{e}"
        )
        return
    for marker in ('result["content"]', 'json.dumps({"results": results}'):
        if marker not in source:
            _warn_or_raise(
                "convert_inference_sections_to_llm_string source contract "
                f"changed; missing {marker!r}"
            )
            return

    @functools.wraps(original_convert)
    def _limited_convert_inference_sections_to_llm_string(*args, **kwargs):
        docs_str, citation_mapping = original_convert(*args, **kwargs)
        if not docs_str:
            return docs_str, citation_mapping

        try:
            payload = json.loads(docs_str)
            results = payload.get("results", [])
        except Exception as e:
            print(
                f"sitecustomize: failed parsing internal search payload: {e}",
                flush=True,
            )
            if _strict_mode():
                raise
            return docs_str, citation_mapping

        remaining_total = max_total_chars
        for entry in results:
            content = entry.get("content")
            if not isinstance(content, str):
                continue

            cap = len(content)
            if max_chars_per_result is not None:
                cap = min(cap, max_chars_per_result)
            if remaining_total is not None:
                cap = min(cap, remaining_total)

            limited_content = _truncate_text_with_notice(content, cap)
            entry["content"] = limited_content

            if remaining_total is not None:
                remaining_total = max(0, remaining_total - len(limited_content))

        return (
            json.dumps(payload, indent=2, ensure_ascii=False),
            citation_mapping,
        )

    tool_utils.convert_inference_sections_to_llm_string = (
        _limited_convert_inference_sections_to_llm_string
    )

    # search_tool imports the formatter by name. Patch the imported symbol
    # too if the module is already importable during startup.
    try:
        from onyx.tools.tool_implementations.search import search_tool

        search_tool.convert_inference_sections_to_llm_string = (
            _limited_convert_inference_sections_to_llm_string
        )
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: search_tool formatter patch deferred/unavailable: {e}",
            flush=True,
        )
        if _strict_mode():
            raise

    print(
        "sitecustomize: applied internal search content caps "
        f"max_chars_per_result={max_chars_per_result}, "
        f"max_total_chars={max_total_chars}",
        flush=True,
    )


def apply_preserve_tool_results_patch() -> None:
    """Optionally preserve prior-turn tool responses in future LLM context.

    Upstream Onyx reconstructs previous assistant tool-call history with a
    placeholder for every non-image tool response. When enabled, keep the
    saved tool_call_response instead and recompute response token counts so
    context trimming reflects the larger payload.
    """

    if not _env_flag_enabled("ONYX_AGENT_PRESERVE_TOOL_RESULTS"):
        return

    try:
        from onyx.chat import chat_utils
        from onyx.configs.constants import MessageType
        from onyx.prompts.chat_prompts import TOOL_CALL_RESPONSE_CROSS_MESSAGE
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing tool-result preservation modules: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    try:
        source = inspect.getsource(chat_utils._build_tool_call_response_history_message)
    except Exception as e:  # pragma: no cover
        _warn_or_raise(
            "could not inspect chat_utils._build_tool_call_response_history_message: "
            f"{e}"
        )
        return

    if "TOOL_CALL_RESPONSE_CROSS_MESSAGE" not in source:
        _warn_or_raise(
            "chat_utils._build_tool_call_response_history_message no longer "
            "contains the expected placeholder behavior"
        )
        return

    helper_signature = inspect.signature(
        chat_utils._build_tool_call_response_history_message
    )
    if tuple(helper_signature.parameters) != (
        "tool_name",
        "generated_images",
        "tool_call_response",
    ):
        _warn_or_raise(
            "chat_utils._build_tool_call_response_history_message signature "
            f"changed; found {helper_signature}"
        )
        return

    original_convert_chat_history = chat_utils.convert_chat_history
    convert_signature = inspect.signature(original_convert_chat_history)
    if tuple(convert_signature.parameters) != (
        "chat_history",
        "files",
        "context_image_files",
        "additional_context",
        "token_counter",
        "tool_id_to_name_map",
    ):
        _warn_or_raise(
            "chat_utils.convert_chat_history signature changed; "
            f"found {convert_signature}"
        )
        return

    def _preserving_tool_call_response_history_message(
        tool_name: str,
        generated_images: list[dict] | None,
        tool_call_response: str | None,
    ) -> str:
        if tool_name == chat_utils.IMAGE_GENERATION_TOOL_NAME:
            if generated_images:
                llm_image_context: list[dict[str, str]] = []
                for image in generated_images:
                    file_id = image.get("file_id")
                    revised_prompt = image.get("revised_prompt")
                    if not isinstance(file_id, str):
                        continue

                    llm_image_context.append(
                        {
                            "file_id": file_id,
                            "revised_prompt": (
                                revised_prompt
                                if isinstance(revised_prompt, str)
                                else ""
                            ),
                        }
                    )

                if llm_image_context:
                    return json.dumps(llm_image_context)

        if tool_call_response:
            return tool_call_response

        return TOOL_CALL_RESPONSE_CROSS_MESSAGE

    @functools.wraps(original_convert_chat_history)
    def _preserving_convert_chat_history(*args, **kwargs):
        result = original_convert_chat_history(*args, **kwargs)

        token_counter = kwargs.get("token_counter")
        if token_counter is None and len(args) >= 5:
            token_counter = args[4]
        if token_counter is None:
            _warn_or_raise("convert_chat_history token_counter not found")
            return result

        for msg in result.simple_messages:
            if msg.message_type == MessageType.TOOL_CALL_RESPONSE:
                msg.token_count = token_counter(msg.message)

        return result

    chat_utils._build_tool_call_response_history_message = (
        _preserving_tool_call_response_history_message
    )
    chat_utils.convert_chat_history = _preserving_convert_chat_history
    print(
        "sitecustomize: preserving previous tool-call responses in chat history",
        flush=True,
    )


def apply_open_url_char_limit_patches() -> None:
    per_url = _parse_positive_int("ONYX_OPEN_URL_MAX_CHARS_PER_URL")
    across_urls = _parse_positive_int("ONYX_OPEN_URL_MAX_TOTAL_CHARS")

    if per_url is None and across_urls is None:
        return

    try:
        from onyx.tools.tool_implementations.web_search import utils as ws_utils
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed importing web_search.utils: {e}", flush=True)
        _raise_if_strict()
        return

    if per_url is not None:
        ws_utils.MAX_CHARS_PER_URL = per_url
        _set_single_default(
            ws_utils.truncate_search_result_content,
            per_url,
            "web_search.utils.truncate_search_result_content",
        )
        _set_single_default(
            ws_utils._truncate_content_around_snippet,
            per_url,
            "web_search.utils._truncate_content_around_snippet",
        )

    if across_urls is None:
        across_urls = 10 * ws_utils.MAX_CHARS_PER_URL

    try:
        from onyx.tools.tool_implementations.open_url import open_url_tool
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed importing open_url_tool: {e}", flush=True)
        _raise_if_strict()
        return

    open_url_tool.MAX_CHARS_ACROSS_URLS = across_urls
    _set_single_default(
        open_url_tool._convert_sections_to_llm_string_with_citations,
        across_urls,
        "open_url_tool._convert_sections_to_llm_string_with_citations",
    )

    print(
        "sitecustomize: applied open_url char limit patch "
        f"(per_url={ws_utils.MAX_CHARS_PER_URL}, across_urls={across_urls})",
        flush=True,
    )


def apply_coding_agent_repo_download_limit_patch() -> None:
    """Align coding-agent repository downloads with the upload receiver.

    Onyx downloads a GitHub tarball in the API server and then uploads the
    resulting bytes to code-interpreter. Upstream currently permits a 500 MiB
    download while code-interpreter accepts 100 MiB by default. Patch the
    bound downloader default so an oversized archive fails at the producer,
    before Onyx attempts an upload that code-interpreter must reject.
    """

    var_name = "ONYX_CODE_INTERPRETER_MAX_FILE_SIZE_MB"
    raw = os.environ.get(var_name, "1000").strip()
    try:
        max_size_mb = int(raw)
    except ValueError:
        _warn_or_raise(f"{var_name} must be a positive integer, found {raw!r}")
        return

    if max_size_mb <= 0:
        _warn_or_raise(f"{var_name} must be greater than zero, found {raw!r}")
        return

    try:
        from onyx.utils import github as github_utils
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed importing onyx.utils.github: {e}", flush=True)
        _raise_if_strict()
        return

    function = github_utils.download_github_repo
    signature = inspect.signature(function)
    parameter_names = tuple(signature.parameters)
    defaults = function.__defaults__
    if parameter_names != ("repo", "github_token", "max_size_bytes"):
        _warn_or_raise(
            "onyx.utils.github.download_github_repo parameters changed; "
            f"found signature={signature}"
        )
        return
    if defaults is None or len(defaults) != 2 or defaults[0] is not None:
        _warn_or_raise(
            "onyx.utils.github.download_github_repo expected defaults "
            f"(None, max_size_bytes), found {defaults!r}; signature={signature}"
        )
        return

    max_size_bytes = max_size_mb * 1024 * 1024
    github_utils.DEFAULT_MAX_TARBALL_SIZE_BYTES = max_size_bytes
    function.__defaults__ = (None, max_size_bytes)
    print(
        "sitecustomize: aligned coding-agent repository download limit with "
        f"code-interpreter uploads ({max_size_mb} MiB)",
        flush=True,
    )


def apply_playwright_helper_proxy_patch() -> None:
    """Route Onyx's shared Playwright launcher through the helper policy."""

    proxy_url = os.environ.get("ONYX_HELPER_HTTP_PROXY_URL", "").strip()
    if not proxy_url:
        return

    parsed = urlsplit(proxy_url)
    try:
        proxy_port = parsed.port
    except ValueError:
        proxy_port = None
    if (
        parsed.scheme != "http"
        or parsed.hostname != "onyx-public-egress-bridge"
        or proxy_port != 3128
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        _warn_or_raise(
            "ONYX_HELPER_HTTP_PROXY_URL must be exactly "
            f"http://onyx-public-egress-bridge:3128, found {proxy_url!r}"
        )
        return

    try:
        from onyx.utils import playwright_fetch
        from playwright import sync_api as playwright_sync_api
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed importing playwright_fetch: {e}", flush=True)
        _raise_if_strict()
        return

    if getattr(playwright_fetch, "_wrapper_helper_proxy_patched", False):
        return

    original_sync_playwright = playwright_fetch.sync_playwright
    if playwright_sync_api.sync_playwright is not original_sync_playwright:
        _warn_or_raise(
            "Onyx Playwright helper no longer uses playwright.sync_api's "
            "sync_playwright factory"
        )
        return
    signature = inspect.signature(original_sync_playwright)
    if signature.parameters:
        _warn_or_raise(
            "onyx.utils.playwright_fetch.sync_playwright signature changed; "
            f"found signature={signature}"
        )
        return

    class _BrowserTypeProxy:
        def __init__(self, browser_type) -> None:
            self._browser_type = browser_type

        def __getattr__(self, name: str) -> Any:
            return getattr(self._browser_type, name)

        def launch(self, *args, **kwargs):
            if kwargs.get("proxy") is not None:
                _warn_or_raise(
                    "Onyx Playwright launcher now supplies its own proxy; "
                    "wrapper helper-proxy injection must be reviewed"
                )
                return self._browser_type.launch(*args, **kwargs)
            selected_proxy = _PLAYWRIGHT_PROXY_OVERRIDE.get()
            if selected_proxy is None:
                selected_proxy = proxy_url
            if selected_proxy:
                # Chromium otherwise bypasses proxies implicitly for loopback.
                # Keep redirects and subresources on the selected final hop.
                kwargs["proxy"] = {
                    "server": selected_proxy,
                    "bypass": "<-loopback>",
                }
            return self._browser_type.launch(*args, **kwargs)

    class _PlaywrightProxy:
        def __init__(self, playwright) -> None:
            self._playwright = playwright
            self.chromium = _BrowserTypeProxy(playwright.chromium)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._playwright, name)

    class _PlaywrightContextManagerProxy:
        def __init__(self, manager) -> None:
            self._manager = manager

        def __getattr__(self, name: str) -> Any:
            return getattr(self._manager, name)

        def start(self):
            return _PlaywrightProxy(self._manager.start())

        def __enter__(self):
            return _PlaywrightProxy(self._manager.__enter__())

        def __exit__(self, exc_type, exc_value, traceback):
            return self._manager.__exit__(exc_type, exc_value, traceback)

    def _helper_proxy_sync_playwright():
        return _PlaywrightContextManagerProxy(original_sync_playwright())

    playwright_fetch.sync_playwright = _helper_proxy_sync_playwright
    # Cover connector modules (currently Highspot) that import sync_playwright
    # directly instead of using onyx.utils.playwright_fetch.start_playwright().
    playwright_sync_api.sync_playwright = _helper_proxy_sync_playwright
    playwright_fetch._wrapper_helper_proxy_patched = True
    print(
        "sitecustomize: routed Onyx Playwright through fixed helper proxy",
        flush=True,
    )


def _validated_fixed_proxy_url(env_name: str, expected_host: str) -> str:
    proxy_url = os.environ.get(env_name, "").strip()
    parsed = urlsplit(proxy_url)
    try:
        port = parsed.port
    except ValueError:
        port = None
    if (
        parsed.scheme != "http"
        or parsed.hostname != expected_host
        or port != 3128
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(
            f"{env_name} must be exactly http://{expected_host}:3128"
        )
    return proxy_url


def apply_mcp_egress_proxy_patch() -> None:
    """Select an explicit public/host MCP transport from saved SSRF policy."""

    public_proxy = _validated_fixed_proxy_url(
        "ONYX_MCP_PUBLIC_HTTP_PROXY_URL", "onyx-public-egress-bridge"
    )
    host_proxy = _validated_fixed_proxy_url(
        "ONYX_MCP_HOST_HTTP_PROXY_URL", "onyx-host-egress-bridge"
    )
    try:
        import httpx

        from onyx.server.security.models import SSRFProtectionLevel
        from onyx.server.security.store import get_security_settings
        from onyx.tools.tool_implementations.mcp import mcp_ssrf
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed importing MCP egress patch deps: {e}", flush=True)
        _raise_if_strict()
        return

    original_factory = mcp_ssrf.mcp_ssrf_httpx_client_factory
    signature = inspect.signature(original_factory)
    if tuple(signature.parameters) != ("headers", "timeout", "auth"):
        _warn_or_raise(f"MCP HTTP client factory signature changed: {signature}")
        return

    def _patched_factory(headers=None, timeout=None, auth=None):  # noqa: ANN001
        level = get_security_settings().ssrf_protection_level
        use_host = level in {
            SSRFProtectionLevel.ALLOW_PRIVATE_NETWORK,
            SSRFProtectionLevel.DISABLED,
        }
        kwargs: dict[str, Any] = {
            "follow_redirects": True,
            # The selected policy and route broker validate every initial and
            # SDK-derived destination. Keep the Onyx transport deliberately
            # limited to explicit route selection so destination policy is not
            # duplicated here with subtly different hostname/DNS semantics.
            "transport": httpx.AsyncHTTPTransport(
                proxy=host_proxy if use_host else public_proxy
            ),
            "trust_env": False,
            "timeout": timeout
            or httpx.Timeout(
                mcp_ssrf._MCP_DEFAULT_TIMEOUT,
                read=mcp_ssrf._MCP_DEFAULT_SSE_READ_TIMEOUT,
            ),
        }
        if headers is not None:
            kwargs["headers"] = headers
        if auth is not None:
            kwargs["auth"] = auth
        return httpx.AsyncClient(**kwargs)

    mcp_ssrf.mcp_ssrf_httpx_client_factory = _patched_factory
    # The client module imports the factory by name; update it if source import
    # order caused it to be cached while applying this startup patch.
    client_module = sys.modules.get(
        "onyx.tools.tool_implementations.mcp.mcp_client"
    )
    if client_module is not None:
        setattr(client_module, "mcp_ssrf_httpx_client_factory", _patched_factory)
    print(
        "sitecustomize: routed MCP/OAuth HTTP through saved-level-selected fixed egress",
        flush=True,
    )


def apply_configured_inference_proxy_patch() -> None:
    """Give supported configured chat bases one explicit controlled client."""

    proxy_url = _validated_fixed_proxy_url(
        "ONYX_CONFIGURED_INFERENCE_HTTP_PROXY_URL", "onyx-host-egress-bridge"
    )
    internal_base_url = os.environ.get(
        "ONYX_CONFIGURED_INFERENCE_INTERNAL_BASE_URL", ""
    ).strip()
    internal_base = urlsplit(internal_base_url)
    try:
        internal_port = internal_base.port
    except ValueError:
        internal_port = None
    if (
        internal_base.scheme != "http"
        or internal_base.hostname != "teep"
        or internal_port != 8337
        or internal_base.username is not None
        or internal_base.password is not None
        or internal_base.path.rstrip("/") != "/v1"
        or internal_base.query
        or internal_base.fragment
    ):
        raise RuntimeError(
            "ONYX_CONFIGURED_INFERENCE_INTERNAL_BASE_URL must be exactly "
            "http://teep:8337/v1"
        )
    try:
        import httpx
        import openai

        from onyx.llm import multi_llm
        from onyx.llm.constants import LlmProviderNames
        from onyx.utils.url import validate_outbound_http_url
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing configured inference patch deps: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    original_init = multi_llm.LitellmLLM.__init__
    original_completion = multi_llm.LitellmLLM._completion
    completion_signature = inspect.signature(original_completion)
    if "client" not in completion_signature.parameters:
        _warn_or_raise(
            f"LitellmLLM._completion no longer accepts client: {completion_signature}"
        )
        return

    supported = {
        str(LlmProviderNames.OPENAI),
        str(LlmProviderNames.OPENAI_COMPATIBLE),
        str(LlmProviderNames.BIFROST),
        str(LlmProviderNames.LITELLM_PROXY),
        str(LlmProviderNames.LM_STUDIO),
        str(LlmProviderNames.OLLAMA_CHAT),
    }

    @functools.wraps(original_init)
    def _patched_init(self, *args, **kwargs):  # noqa: ANN001
        original_init(self, *args, **kwargs)
        self._wrapper_configured_inference_client = None
        self._wrapper_configured_inference_http_client = None
        if not self._api_base:
            return
        validate_outbound_http_url(
            self._api_base,
            allow_private_network=True,
            block_loopback_and_link_local=True,
            resolve_dns=False,
        )
        provider = str(self._model_provider)
        if provider not in supported:
            raise RuntimeError(
                "configured inference api_base is not proxy-covered for provider "
                f"{provider!r} at the pinned Onyx/LiteLLM version"
            )
        configured_base = urlsplit(self._api_base)
        is_internal_teep = (
            configured_base.scheme == internal_base.scheme
            and configured_base.hostname == internal_base.hostname
            and configured_base.port == internal_base.port
            and configured_base.path.rstrip("/") == internal_base.path.rstrip("/")
            and configured_base.username is None
            and configured_base.password is None
            and not configured_base.query
            and not configured_base.fragment
        )
        client_kwargs: dict[str, Any] = {
            "trust_env": False,
            "timeout": self._timeout,
        }
        if not is_internal_teep:
            client_kwargs["proxy"] = proxy_url
        http_client = httpx.Client(**client_kwargs)
        inference_client = openai.OpenAI(
            api_key=self._api_key or "not-needed",
            base_url=self._api_base,
            http_client=http_client,
        )
        self._wrapper_configured_inference_http_client = http_client
        self._wrapper_configured_inference_client = inference_client

    @functools.wraps(original_completion)
    def _patched_completion(self, *args, **kwargs):  # noqa: ANN001
        configured_client = getattr(
            self, "_wrapper_configured_inference_client", None
        )
        if configured_client is None:
            return original_completion(self, *args, **kwargs)
        bound = completion_signature.bind_partial(self, *args, **kwargs)
        bound.arguments["client"] = configured_client
        return original_completion(*bound.args, **bound.kwargs)

    multi_llm.LitellmLLM.__init__ = _patched_init
    multi_llm.LitellmLLM._completion = _patched_completion
    print(
        "sitecustomize: routed supported configured inference bases through "
        "fixed host egress with an exact internal Teep exception",
        flush=True,
    )


def _is_code_interpreter_network_enabled() -> bool:
    return os.environ.get("ONYX_CODE_INTERPRETER_ENABLE_NETWORK", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def apply_python_file_link_prompt_patches() -> None:
    """Make generated-file response links explicit, portable, and prominent.

    Upstream constructs absolute ``file_link`` values from one canonical
    ``WEB_DOMAIN``. The WebUI recognizes an ordinary Markdown link whose label
    is an image filename, reduces it to its file ID, and renders it from a
    relative same-origin URL. Returning the relative URL in the first place
    also makes an accidental Markdown image work through any wrapper frontend
    without adding a remote CSP source.

    The rule is repeated in the function description, Python guidance,
    post-execution reminder, and the result itself. The result supplies a
    ready-to-copy ``response_markdown`` value so the model does not have to
    reconstruct either the label or URL.
    """
    link_instruction = (
        "In the final answer, include every user-requested generated file by "
        "copying its `response_markdown` value exactly. This is an ordinary "
        "Markdown link `[filename](file_link)`. Never construct or hard-code a "
        "file URL."
    )

    try:
        from onyx.tools.tool_implementations.python.python_tool import PythonTool

        PythonTool.DESCRIPTION = _replace_or_warn(
            owner_name="PythonTool.DESCRIPTION file-link instruction",
            current=PythonTool.DESCRIPTION,
            old="Execute Python code in an isolated sandbox environment.",
            new=(
                "Execute Python code in an isolated sandbox environment. "
                + link_instruction
            ),
        )
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed to patch PythonTool.DESCRIPTION file links: {e}",
            flush=True,
        )
        _raise_if_strict()

    try:
        from onyx.prompts import tool_prompts

        tool_prompts.PYTHON_TOOL_GUIDANCE = _replace_or_warn(
            owner_name="PYTHON_TOOL_GUIDANCE file-link instruction",
            current=tool_prompts.PYTHON_TOOL_GUIDANCE,
            old=(
                "Use this to give the user a way to download the file OR to "
                "display generated images."
            ),
            new=(
                "Use this to give the user a way to download the file or display "
                "a generated image. "
                + link_instruction
                + " Even for an image, do not substitute Markdown image syntax "
                "(`![filename](file_link)`) for the supplied ordinary link."
            ),
        )
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed to patch PYTHON_TOOL_GUIDANCE file links: {e}",
            flush=True,
        )
        _raise_if_strict()

    try:
        from onyx.prompts import chat_prompts

        chat_prompts.FILE_REMINDER = _replace_or_warn(
            owner_name="FILE_REMINDER file-link instruction",
            current=chat_prompts.FILE_REMINDER,
            old=(
                "If you reference or share these files, use the exact markdown "
                "format [filename](file_link) with the file_link from the "
                "execution result."
            ),
            new=(
                "Do not omit a graph or other file the user requested. "
                + link_instruction
            ),
        )
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch FILE_REMINDER: {e}", flush=True)
        _raise_if_strict()

    try:
        file_utils = importlib.import_module("onyx.file_store.utils")
        python_tool_module = importlib.import_module(
            "onyx.tools.tool_implementations.python.python_tool"
        )
        PythonTool = python_tool_module.PythonTool

        if (
            python_tool_module.build_full_frontend_file_url
            is not file_utils.build_full_frontend_file_url
        ):
            _warn_or_raise(
                "PythonTool file-link helper did not match the expected "
                "build_full_frontend_file_url import"
            )

        original_run = PythonTool.run
        parameters = tuple(inspect.signature(original_run).parameters.values())
        if (
            tuple(parameter.name for parameter in parameters)
            != ("self", "placement", "override_kwargs", "llm_kwargs")
            or parameters[-1].kind is not inspect.Parameter.VAR_KEYWORD
        ):
            _warn_or_raise(
                "PythonTool.run signature changed; cannot add generated-file "
                "response_markdown safely"
            )

        python_tool_module.build_full_frontend_file_url = (
            file_utils.build_frontend_file_url
        )

        @functools.wraps(original_run)
        def _patched_run(self, *args, **kwargs):  # noqa: ANN001
            response = original_run(self, *args, **kwargs)
            parsed = json.loads(response.llm_facing_response)
            generated_files = parsed.get("generated_files")
            if not generated_files:
                return response
            if not isinstance(generated_files, list):
                raise RuntimeError(
                    "PythonTool generated_files result is not a list"
                )

            rich_files = getattr(response.rich_response, "generated_files", None)
            if not isinstance(rich_files, list) or len(rich_files) != len(
                generated_files
            ):
                raise RuntimeError(
                    "PythonTool LLM and rich generated-file results disagree"
                )

            for index, generated_file in enumerate(generated_files):
                if not isinstance(generated_file, dict):
                    raise RuntimeError(
                        "PythonTool generated-file result is not an object"
                    )
                filename = generated_file.get("filename")
                file_link = generated_file.get("file_link")
                if not isinstance(filename, str) or not filename:
                    raise RuntimeError(
                        "PythonTool generated-file result has no filename"
                    )
                if (
                    not isinstance(file_link, str)
                    or not file_link.startswith("/api/chat/file/")
                ):
                    raise RuntimeError(
                        "PythonTool generated-file result has no relative "
                        "same-origin file_link"
                    )

                label = (
                    filename.replace("\\", "\\\\")
                    .replace("[", "\\[")
                    .replace("]", "\\]")
                )
                generated_file["response_markdown"] = f"[{label}]({file_link})"
                rich_files[index].file_link = file_link

            patched_json = json.dumps(parsed, separators=(",", ":"))
            if hasattr(response, "model_copy"):
                return response.model_copy(
                    update={"llm_facing_response": patched_json}
                )
            response.llm_facing_response = patched_json
            return response

        _patched_run._wrapper_python_file_link_patch = True
        PythonTool.run = _patched_run
        print(
            "sitecustomize: patched PythonTool generated-file result links",
            flush=True,
        )
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed to patch PythonTool generated-file results: {e}",
            flush=True,
        )
        _raise_if_strict()


_PYTHON_PACKAGE_LIST = (
    "numpy, pandas, scipy, sympy, matplotlib, seaborn, scikit-learn, "
    "scikit-image, opencv-python, xgboost, openpyxl, pdfplumber, pypdf, "
    "python-docx, python-pptx, fpdf2, pydantic, Pillow"
)


def apply_python_package_capability_patches() -> None:
    """Advertise the executor's wrapper-maintained pre-installed package set."""
    package_sentence = f"Pre-installed packages include ({_PYTHON_PACKAGE_LIST})."

    try:
        from onyx.tools.tool_implementations.python.python_tool import PythonTool

        PythonTool.DESCRIPTION = _replace_or_warn(
            owner_name="PythonTool.DESCRIPTION package capabilities",
            current=PythonTool.DESCRIPTION,
            old="Execute Python code in an isolated sandbox environment.",
            new=(
                "Execute Python code in an isolated sandbox environment. "
                + package_sentence
            ),
        )
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed to patch PythonTool.DESCRIPTION packages: {e}",
            flush=True,
        )
        _raise_if_strict()

    try:
        from onyx.prompts import tool_prompts

        tool_prompts.PYTHON_TOOL_GUIDANCE = _replace_or_warn(
            owner_name="PYTHON_TOOL_GUIDANCE package capabilities",
            current=tool_prompts.PYTHON_TOOL_GUIDANCE,
            old=(
                "Use `openpyxl` to read and write Excel files. You have access "
                "to libraries like numpy, pandas, scipy, matplotlib, and PIL."
            ),
            new=(
                "Use `openpyxl` to read and write Excel files. " + package_sentence
            ),
        )
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed to patch PYTHON_TOOL_GUIDANCE packages: {e}",
            flush=True,
        )
        _raise_if_strict()


_RESTRICTED_NETWORK_TEXT = (
    "Network access is available through a restricted HTTP/HTTPS proxy. "
    "Direct socket access to the internet and direct access to stack-internal "
    "services are blocked. Internal/private network targets and direct "
    "search-engine URLs are blocked by the proxy."
)


def apply_code_interpreter_network_description_patches() -> None:
    """Update tool descriptions, system-prompt guidance, and coding-agent
    prompts when executor pods use the restricted egress network.

    By default, Onyx's code-interpreter selects ``--network none`` on every
    executor pod, so the Python tool, BashTool, and coding-agent bash sessions
    have no network access. The upstream tool descriptions, the
    ``PYTHON_TOOL_GUIDANCE`` system prompt, and the coding-agent system prompts
    all advertise this ("network-restricted", "no network access",
    "network-isolated sandbox", "Internet access for this session is
    disabled").

    When ``ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`` is set on the api_server
    container, the wrapper configures upstream's native Docker network and run-
    argument settings to attach pods to a dedicated internal network and inject
    its local policy proxy. The deterministic and pinned-image suites validate
    that native contract outside the running service. This function updates the
    api_server-side descriptions and prompts so the LLM is told it has network
    access and can use network commands (curl, pip install, etc.) — otherwise
    the LLM would continue to avoid network commands based on the stale
    "no network" descriptions.
    """
    if not _is_code_interpreter_network_enabled():
        return

    # ── PythonTool description ──────────────────────────────────────────
    # The unconditional package patch has already extended the upstream
    # description. Replace only its sandbox phrase with the network and pip
    # capability text so the package list retains one owner.
    try:
        from onyx.tools.tool_implementations.python.python_tool import PythonTool

        PythonTool.DESCRIPTION = _replace_or_warn(
            owner_name="PythonTool.DESCRIPTION",
            current=PythonTool.DESCRIPTION,
            old="isolated sandbox environment.",
            new=(
                "sandbox environment. "
                + _RESTRICTED_NETWORK_TEXT
                + " pip package installation is NOT "
                "supported. For tasks requiring "
                "additional packages or complex multi-step workflows, invoke the "
                "code agent instead."
            ),
        )
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch PythonTool.DESCRIPTION: {e}", flush=True)
        _raise_if_strict()

    # ── PYTHON_TOOL_GUIDANCE system prompt ──────────────────────────────
    # This is the per-tool guidance injected into the system prompt. Upstream
    # it says "Internet access for this session is disabled. Do not make
    # external web requests or API calls as they will fail." — we replace that
    # with VPN access info, the pip restriction, and a recommendation to use
    # the code agent for involved tasks. The unconditional package patch owns
    # the package list. Patched before prompt_utils.py imports it by name.
    try:
        from onyx.prompts import tool_prompts

        tool_prompts.PYTHON_TOOL_GUIDANCE = _replace_or_warn(
            owner_name="PYTHON_TOOL_GUIDANCE",
            current=tool_prompts.PYTHON_TOOL_GUIDANCE,
            old="Internet access for this session is disabled. Do not make external web requests or API calls as they will fail.",
            new=(
                _RESTRICTED_NETWORK_TEXT + " "
                "pip package installation is NOT supported. "
                "For tasks requiring additional packages or complex multi-step workflows, invoke the code agent instead."
            ),
        )
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch PYTHON_TOOL_GUIDANCE: {e}", flush=True)
        _raise_if_strict()

    # ── BashTool description ────────────────────────────────────────────
    # Upstream: "Execute a bash command inside an isolated, network-restricted
    # session." We replace "isolated, network-restricted session" with VPN
    # access info. Using .replace() preserves any future upstream additions.
    try:
        from onyx.tools.tool_implementations.bash.bash_tool import BashTool

        BashTool.DESCRIPTION = _replace_or_warn(
            owner_name="BashTool.DESCRIPTION",
            current=BashTool.DESCRIPTION,
            old="Execute a bash command inside an isolated, network-restricted session.",
            new=(
                "Execute a bash command inside a sandboxed session. "
                + _RESTRICTED_NETWORK_TEXT
            ),
        )
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch BashTool.DESCRIPTION: {e}", flush=True)
        _raise_if_strict()

    # ── Coding agent bash tool description ──────────────────────────────
    # Upstream: "Run a bash command in the sandboxed session containing the
    # checked-out repository. The session has no network access. Use commands
    # like `ls`, `cat`, `grep -r`, `find`, `wc -l`, etc. to inspect the code.
    # Filesystem state persists across calls within the same session."
    # We replace "The session has no network access." with VPN access info.
    # Using .replace() preserves the rest of the description.
    try:
        from onyx.coding_agent import mock_tools

        _desc = mock_tools.BASH_TOOL_DESCRIPTION["function"]["description"]
        mock_tools.BASH_TOOL_DESCRIPTION["function"]["description"] = _replace_or_warn(
            owner_name="coding-agent BASH_TOOL_DESCRIPTION",
            current=_desc,
            old="The session has no network access.",
            new=(
                _RESTRICTED_NETWORK_TEXT
            ),
        )
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed to patch coding-agent BASH_TOOL_DESCRIPTION: {e}",
            flush=True,
        )
        _raise_if_strict()

    # ── Coding agent system prompts ─────────────────────────────────────
    # The prompts are module-level string constants. They are imported by name
    # in fake_tools/coding_agent.py, so we must patch the module attributes
    # BEFORE that import happens (sitecustomize runs at interpreter startup,
    # before any onyx module is imported by the application).
    try:
        from onyx.prompts.coding_agent import coding_agent as ca_prompts

        ca_prompts.CODING_AGENT_PROMPT = _replace_or_warn(
            owner_name="CODING_AGENT_PROMPT network sandbox",
            current=ca_prompts.CODING_AGENT_PROMPT,
            old="network-isolated sandbox",
            new="sandbox with restricted proxy-only network access",
        )
        ca_prompts.CODING_AGENT_PROMPT = _replace_or_warn(
            owner_name="CODING_AGENT_PROMPT network commands",
            current=ca_prompts.CODING_AGENT_PROMPT,
            old=(
                "Avoid:\n"
                "- Network commands (`curl`, `pip install`, `npm install`, `git pull`) "
                "— the sandbox has no network.\n"
            ),
            new=(
                "Network access:\n"
                "- Network commands (`curl`, `pip install`, `npm install`, `git pull`) "
                "may use the restricted HTTP/HTTPS proxy. Direct sockets, "
                "private/internal targets, and direct search-engine URLs are blocked."
                + "\n\nAvoid:\n"
            ),
        )

        ca_prompts.CODING_AGENT_PROMPT_REASONING = _replace_or_warn(
            owner_name="CODING_AGENT_PROMPT_REASONING network sandbox",
            current=ca_prompts.CODING_AGENT_PROMPT_REASONING,
            old="network-isolated sandbox",
            new="sandbox with restricted proxy-only network access",
        )
        ca_prompts.CODING_AGENT_PROMPT_REASONING = _replace_or_warn(
            owner_name="CODING_AGENT_PROMPT_REASONING network access",
            current=ca_prompts.CODING_AGENT_PROMPT_REASONING,
            old="No network.",
            new=_RESTRICTED_NETWORK_TEXT,
        )

    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch coding-agent prompts: {e}", flush=True)
        _raise_if_strict()
