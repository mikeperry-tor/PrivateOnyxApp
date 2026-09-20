"""api coding final answer patch implementation."""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
from typing import Any
from onyx_wrapper_patches.api.reasoning import _REASONING_MODE_TRACE
from onyx_wrapper_patches.api.reasoning import _is_assistant_message
from onyx_wrapper_patches.api.reasoning import _is_tool_call_response_message
from onyx_wrapper_patches.api.reasoning import _is_user_message
from onyx_wrapper_patches.api.reasoning import _message_reasoning_text
from onyx_wrapper_patches.api.reasoning import _reasoning_digest
from onyx_wrapper_patches.api.reasoning import _trace_reasoning_mode
from onyx_wrapper_patches.common.config import _raise_if_strict
from onyx_wrapper_patches.common.config import _warn_or_raise


_CODING_AGENT_FINAL_TRACE_ENABLED = _REASONING_MODE_TRACE



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
