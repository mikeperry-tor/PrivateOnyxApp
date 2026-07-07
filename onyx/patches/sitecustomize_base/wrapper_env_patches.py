"""Environment-driven runtime patches for stock Onyx containers.

This module is imported by sitecustomize so wrapper-level env vars can
adjust hardcoded limits without rebuilding images.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import os
import sys
from types import ModuleType
from typing import Any


# We cannot remove truncation logic entirely without editing upstream code, so
# for "unlimited" we use a very large budget that won't be hit in practice.
EFFECTIVE_UNLIMITED_CHARS = 2_000_000_000

# Internal developer diagnostics. Reasoning-mode tracing is metadata-only and
# enabled by default while validating model/provider routing. The deeper
# reasoning traces remain opt-in; LiteLLM debug logging is intentionally
# separate because it can include full request/response details.
_REASONING_TRACE_ENABLED = False
_REASONING_TRACE_LITELLM_DEBUG_ENABLED = False
_REASONING_TRACE_SEQ = 0
_REASONING_REMINDER_REORDER_ENABLED = True
_REASONING_MODE_TRACE = True
_REASONING_MODE_TRACE_SEQ = 0
_CODING_AGENT_FINAL_TRACE_ENABLED = False


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
    keep = max(0, max_chars - len(suffix))
    suffix = notice_template.format(omitted=len(text) - keep)
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

    for index, message in enumerate(history, start=1):
        message_text = getattr(message, "message", "")

        if _is_tool_call_response_message(message):
            tool_call_id = getattr(message, "tool_call_id", None)
            label = "Bash tool output"
            if tool_call_id:
                label += f" ({tool_call_id})"
            transcript_parts.append(f"## {label}\n\n{message_text}")
            saw_tool_history = True
            continue

        tool_calls = getattr(message, "tool_calls", None)
        if _is_assistant_message(message) and tool_calls:
            reasoning_text = _message_reasoning_text(message)
            if reasoning_text:
                transcript_parts.append(
                    f"## Coding agent reasoning before tool requests {index}\n\n"
                    f"{reasoning_text}"
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
            continue

        if _is_user_message(message):
            transcript_parts.append(f"## User message {index}\n\n{message_text}")
            continue

        if _is_assistant_message(message):
            if message_text:
                transcript_parts.append(
                    f"## Coding agent assistant message {index}\n\n{message_text}"
                )
            continue

        if message_text:
            transcript_parts.append(f"## Coding agent message {index}\n\n{message_text}")

    if not saw_tool_history:
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
    _trace_reasoning(
        "coding_agent_final_answer_history_flattened",
        original_messages=len(history),
        flattened_messages=len(flattened),
    )

    return flattened


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
        print(
            "sitecustomize: coding-agent flattened final request shape "
            "roles=[\"system\",\"user\"] "
            "reasoning_effort=off "
            "tools_arg=none "
            f"message_count=2 user_chars={len(user_message)} "
            f"user_newlines={user_message.count(chr(10))} "
            f"user_backslashes={user_message.count(chr(92))} "
            f"user_quotes={user_message.count(chr(34))} "
            f"user_control_chars={user_control_chars} "
            f"user_sha256={hashlib.sha256(user_message.encode()).hexdigest()[:12]} "
            f"json_encoding_ok={json_ok}",
            flush=True,
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

    for module_name, function_name, old, new in [
        (
            "onyx.deep_research.dr_loop",
            "run_deep_research_llm_loop",
            "                    simple_chat_history.append(assistant_with_tools)\n",
            (
                "                    _wrapper_attach_reasoning_fields(\n"
                "                        assistant_with_tools,\n"
                "                        llm_step_result.reasoning or most_recent_reasoning,\n"
                "                        source=\"deep_research_llm_loop\",\n"
                "                    )\n"
                "                    simple_chat_history.append(assistant_with_tools)\n"
            ),
        ),
        (
            "onyx.tools.fake_tools.research_agent",
            "run_research_agent_call",
            "                        msg_history.append(assistant_with_tools)\n",
            (
                "                        _wrapper_attach_reasoning_fields(\n"
                "                            assistant_with_tools,\n"
                "                            llm_step_result.reasoning or most_recent_reasoning,\n"
                "                            source=\"research_agent_call\",\n"
                "                        )\n"
                "                        msg_history.append(assistant_with_tools)\n"
            ),
        ),
        (
            "onyx.tools.fake_tools.coding_agent",
            "run_coding_agent_call",
            "                    msg_history.append(assistant_with_tools)\n",
            (
                "                    _wrapper_attach_reasoning_fields(\n"
                "                        assistant_with_tools,\n"
                "                        llm_step_result.reasoning or most_recent_reasoning,\n"
                "                        source=\"coding_agent_call\",\n"
                "                    )\n"
                "                    msg_history.append(assistant_with_tools)\n"
            ),
        ),
    ]:
        try:
            module = __import__(module_name, fromlist=[function_name])
            module._wrapper_attach_reasoning_fields = _attach_reasoning_fields
            _patch_function_source(
                module=module,
                function_name=function_name,
                patch_name=f"{module_name}.{function_name} reasoning preservation",
                replacements={old: new},
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

    Upstream Onyx v4.1.7 lets GEN_AI_MAX_TOKENS override provider/LiteLLM
    fallback metadata, but the normal chat construction path checks stored
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

    original_convert_chat_history = chat_utils.convert_chat_history

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


def _is_code_interpreter_network_enabled() -> bool:
    return os.environ.get("ONYX_CODE_INTERPRETER_ENABLE_NETWORK", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


# Hint text for the Python tool: crw Firecrawl API only (no CDP browser, no
# SearXNG). The Python tool is for data analysis / code execution, not browser
# automation, so we only mention the scraping API.
_PYTHON_SERVICE_HINTS = (
    " Additionally, the sandbox shares a network namespace with a local Firecrawl-compatible "
    "REST API at http://127.0.0.1:3010 exposing /v1/scrape, /v1/crawl, /v1/map, "
    "and /v1/search, all backed by a stealth headless browser. All outbound "
    "browser traffic egresses through the VPN tunnel."
)

# Hint text for the coding agent (BashTool + system prompt): crw Firecrawl API
# + CDP browser (no SearXNG). The coding agent benefits from direct browser
# automation for investigating web-accessible repos and docs.
_CODING_AGENT_SERVICE_HINTS = (
    " Additionally, the sandbox shares a network namespace with local scraping services: "
    "a Firecrawl-compatible REST API at http://127.0.0.1:3010 exposing "
    "/v1/scrape, /v1/crawl, /v1/map, and /v1/search, all backed by a stealth "
    "headless browser. Direct control of the stealth browser is available via "
    "Chrome DevTools Protocol (CDP) browser at ws://127.0.0.1:9222/devtools/browser. "
    "All outbound browser traffic egresses through the VPN tunnel."
)


def apply_code_interpreter_network_description_patches() -> None:
    """Update tool descriptions, system-prompt guidance, and coding-agent
    prompts when the code-interpreter executor pods are VPN-routed.

    By default, Onyx's code-interpreter hardcodes ``--network none`` on every
    executor pod, so the Python tool, BashTool, and coding-agent bash sessions
    have no network access. The upstream tool descriptions, the
    ``PYTHON_TOOL_GUIDANCE`` system prompt, and the coding-agent system prompts
    all advertise this ("network-restricted", "no network access",
    "network-isolated sandbox", "Internet access for this session is
    disabled").

    When ``ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`` is set on the api_server
    container, a companion sitecustomize patch in the code-interpreter
    container rewrites ``--network none`` to ``--network container:<self>``,
    giving executor pods VPN-routed internet access. This function updates the
    api_server-side descriptions and prompts so the LLM is told it has network
    access and can use network commands (curl, pip install, etc.) — otherwise
    the LLM would continue to avoid network commands based on the stale
    "no network" descriptions.
    """
    if not _is_code_interpreter_network_enabled():
        return

    # ── PythonTool description ──────────────────────────────────────────
    # Upstream: "Execute Python code in an isolated sandbox environment."
    # We replace "isolated sandbox environment" with VPN access info, the pip
    # restriction, the package list, and a code-agent recommendation. Using
    # .replace() preserves any future upstream additions to the description.
    try:
        from onyx.tools.tool_implementations.python.python_tool import PythonTool

        PythonTool.DESCRIPTION = _replace_or_warn(
            owner_name="PythonTool.DESCRIPTION",
            current=PythonTool.DESCRIPTION,
            old="Execute Python code in an isolated sandbox environment.",
            new=(
                "Execute Python code in a sandbox environment with internet access "
                "via VPN. Network operations (requests, urllib, etc.) are permitted "
                "and egress through the VPN tunnel. pip package installation is NOT "
                "supported — only the pre-installed scientific stack is available "
                "(numpy, pandas, scipy, matplotlib, seaborn, scikit-learn, "
                "scikit-image, opencv-python, xgboost, openpyxl, pdfplumber, pypdf, "
                "python-docx, python-pptx, fpdf2, pydantic). For tasks requiring "
                "additional packages or complex multi-step workflows, invoke the "
                "code agent instead." + _PYTHON_SERVICE_HINTS
            ),
        )
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch PythonTool.DESCRIPTION: {e}", flush=True)
        _raise_if_strict()

    # ── PYTHON_TOOL_GUIDANCE system prompt ──────────────────────────────
    # This is the per-tool guidance injected into the system prompt. Upstream
    # it says "Internet access for this session is disabled. Do not make
    # external web requests or API calls as they will fail." — we replace that
    # with VPN access info, the pip restriction, the package list, and a
    # recommendation to use the code agent for involved tasks. Patched at the
    # module level before prompt_utils.py imports it by name.
    try:
        from onyx.prompts import tool_prompts

        tool_prompts.PYTHON_TOOL_GUIDANCE = _replace_or_warn(
            owner_name="PYTHON_TOOL_GUIDANCE",
            current=tool_prompts.PYTHON_TOOL_GUIDANCE,
            old="Internet access for this session is disabled. Do not make external web requests or API calls as they will fail.",
            new=(
                "Internet access is available via VPN — external web requests and API calls are permitted and egress through the VPN tunnel. "
                "pip package installation is NOT supported; only the pre-installed scientific stack is available "
                "(numpy, pandas, scipy, matplotlib, seaborn, scikit-learn, scikit-image, opencv-python, xgboost, openpyxl, pdfplumber, pypdf, python-docx, python-pptx, fpdf2, pydantic). "
                "For tasks requiring additional packages or complex multi-step workflows, invoke the code agent instead."
                + _PYTHON_SERVICE_HINTS
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
                "Execute a bash command inside a session with internet access via "
                "VPN. Network commands (curl, wget, pip install, npm install, git "
                "clone, etc.) are permitted and egress through the VPN tunnel."
                + _CODING_AGENT_SERVICE_HINTS
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
                "The session has internet access via VPN. Network commands (curl, "
                "pip install, npm install, git clone, etc.) are permitted and "
                "egress through the VPN tunnel." + _CODING_AGENT_SERVICE_HINTS
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
            new="sandbox with VPN-routed internet access",
        )
        ca_prompts.CODING_AGENT_PROMPT = _replace_or_warn(
            owner_name="CODING_AGENT_PROMPT network commands",
            current=ca_prompts.CODING_AGENT_PROMPT,
            old="Network commands (`curl`, `pip install`, `npm install`, `git pull`) — the sandbox has no network.",
            new=(
                "Network commands (`curl`, `pip install`, `npm install`, `git pull`) are permitted — the sandbox has VPN-routed internet access."
                + _CODING_AGENT_SERVICE_HINTS
            ),
        )

        ca_prompts.CODING_AGENT_PROMPT_REASONING = _replace_or_warn(
            owner_name="CODING_AGENT_PROMPT_REASONING network sandbox",
            current=ca_prompts.CODING_AGENT_PROMPT_REASONING,
            old="network-isolated sandbox",
            new="sandbox with VPN-routed internet access",
        )
        ca_prompts.CODING_AGENT_PROMPT_REASONING = _replace_or_warn(
            owner_name="CODING_AGENT_PROMPT_REASONING network access",
            current=ca_prompts.CODING_AGENT_PROMPT_REASONING,
            old="No network.",
            new="VPN-routed internet access is available."
            + _CODING_AGENT_SERVICE_HINTS,
        )

    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch coding-agent prompts: {e}", flush=True)
        _raise_if_strict()


def apply_firecrawl_wait_for_patch() -> None:
    """Patch FirecrawlClient to omit `waitFor` from /v2/scrape payloads.

    Onyx's FirecrawlClient sends ``{url, formats: ["markdown"]}`` with no
    ``waitFor`` field. This patch monkey-patches
    ``FirecrawlClient._get_webpage_content`` to ensure no ``waitFor`` field
    is ever sent, even if a future upstream change adds one.

    A ``waitFor`` field would make CRW do a blind fixed sleep after
    ``Page.loadEventFired`` before extracting content. This is actively
    harmful: it wastes time on fast pages (always sleeps the full duration
    even when the page is ready) and is insufficient on slow pages (Tor/
    VPN loads can take 20-40s, far exceeding any reasonable fixed sleep).
    Instead, page load waiting is handled by the CDP shim's ``waitUntil``
    injection (``OBSCURA_BROWSER_WAIT_UNTIL_SEARCH=networkidle2``), which makes obscura
    adaptively wait for network silence before returning the nav response.
    CRW then uses its smart heuristics (SPA selector poll, content
    stability, challenge retry) for any remaining post-navigate work.

    See ``docs/request_handling.md`` §1.6 for the full wait strategy.
    """
    try:
        from onyx.tools.tool_implementations.open_url.firecrawl import FirecrawlClient
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed importing FirecrawlClient: {e}", flush=True)
        _raise_if_strict()
        return

    def _patched_get_webpage_content(self, url: str):
        # No waitFor field — page load waiting is handled by the CDP shim's
        # waitUntil injection (OBSCURA_BROWSER_WAIT_UNTIL_SEARCH=networkidle2). CRW uses
        # its smart SPA selector poll + content stability heuristics for
        # any remaining post-navigate work instead of a blind fixed sleep.
        payload: dict = {
            "url": url,
            "formats": ["markdown"],
        }
        import requests as _requests
        response = _requests.post(
            self._base_url,
            headers=self._headers,
            json=payload,
            timeout=self._timeout_seconds,
        )

        if response.status_code != 200:
            try:
                error_payload = response.json()
            except Exception:
                error_payload = response.text
            self._last_error = (
                error_payload if isinstance(error_payload, str) else str(error_payload)
            )
            if 400 <= response.status_code < 500:
                from onyx.tools.tool_implementations.open_url.models import WebContent
                return WebContent(
                    title="",
                    link=url,
                    full_content="",
                    published_date=None,
                    scrape_successful=False,
                )
            raise ValueError(
                f"Firecrawl fetch failed with status {response.status_code}."
            )
        else:
            self._last_error = None

        response_json = response.json()
        extracted = self._extract_content_fields(response_json, url)
        from onyx.tools.tool_implementations.open_url.models import WebContent
        return WebContent(
            title=extracted.title,
            link=url,
            full_content=extracted.text,
            published_date=extracted.published_date,
            scrape_successful=bool(extracted.text),
        )

    FirecrawlClient._get_webpage_content = _patched_get_webpage_content
    print(
        f"sitecustomize: patched FirecrawlClient._get_webpage_content "
        f"(waitFor omitted, using CDP shim waitUntil + CRW heuristics)",
        flush=True,
    )
