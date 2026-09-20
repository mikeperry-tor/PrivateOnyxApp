"""api reasoning patch implementation."""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import os
import sys
from typing import Any
from onyx_wrapper_patches.api.config import _DEEP_RESEARCH_PROVIDE_CHAT_AGENT_TOOLS
from onyx_wrapper_patches.common.config import _env_flag_default_true
from onyx_wrapper_patches.common.config import _env_flag_enabled
from onyx_wrapper_patches.common.config import _raise_if_strict
from onyx_wrapper_patches.common.config import _warn_or_raise
from onyx_wrapper_patches.common.source import _patch_function_source


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
        from onyx.llm import model_capabilities
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing reasoning detection utils: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    original = model_capabilities.model_is_reasoning_model
    if getattr(original, "_wrapper_native_reasoning_override", False):
        return
    signature = inspect.signature(original)
    if tuple(signature.parameters) != ("model_name", "model_provider"):
        _warn_or_raise(
            "model_capabilities.model_is_reasoning_model signature changed; "
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
                "model_capabilities.model_is_reasoning_model source contract changed; "
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
    model_capabilities.model_is_reasoning_model = (
        _model_is_reasoning_model_native_override
    )
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
        from onyx.llm import model_capabilities
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed importing reasoning-mode utils: {e}", flush=True)
        _raise_if_strict()
        return

    original_model_is_reasoning_model = model_capabilities.model_is_reasoning_model
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
        model_capabilities.model_is_reasoning_model = (
            _model_is_reasoning_model_with_trace
        )
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
