"""api inference continuation patch implementation."""

from __future__ import annotations

import functools
import inspect
from onyx_wrapper_patches.api.reasoning import _attach_reasoning_fields
from onyx_wrapper_patches.common.config import _env_flag_default_true
from onyx_wrapper_patches.common.config import _raise_if_strict
from onyx_wrapper_patches.common.config import _warn_or_raise


_MIDSTREAM_CONTINUATION_NOTICE = (
    "\n\n> ⚠️ The inference stream was interrupted. The response continues "
    "below from a recovery request and may contain a repeated or omitted "
    "fragment.\n\n"
)



_MIDSTREAM_CONTINUATION_FAILED_NOTICE = (
    "\n\n> ⚠️ Recovery also failed before the response completed. The generation "
    "above is partial and may end in corrupted reasoning, a sentence, or a "
    "Markdown structure.\n"
)



_MIDSTREAM_FINALIZATION_FAILED_NOTICE = (
    "\n\n> ⚠️ The model signaled that this response was complete, but stream "
    "finalization then failed. The response above was preserved, but its "
    "delivery or accounting could not be fully verified.\n"
)



_MIDSTREAM_REASONING_CONTINUATION_NOTICE = (
    "\n\n⚠️ The inference reasoning stream was interrupted. Reasoning continues "
    "below from a recovery request and may contain a repeated or omitted "
    "fragment.\n\n"
)



_MIDSTREAM_CONTINUATION_INSTRUCTION = (
    "The preceding assistant generation was interrupted by a transport failure. "
    "Continue its reasoning or response directly from its final character. Do "
    "not restart, summarize, or repeat completed material. Complete any "
    "unfinished sentence or Markdown structure, following the original request "
    "and tool policy."
)



def _exception_chain(exc: BaseException) -> list[BaseException]:
    """Return a bounded exception chain without trusting provider wrapper shape."""
    chain: list[BaseException] = []
    pending = [exc]
    seen: set[int] = set()
    while pending and len(chain) < 12:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        for nested in (current.__cause__, current.__context__):
            if isinstance(nested, BaseException):
                pending.append(nested)
        if len(current.args) == 1 and isinstance(current.args[0], BaseException):
            pending.append(current.args[0])
    return chain



def _midstream_retryable_exception(exc: BaseException) -> bool:
    from litellm.exceptions import APIConnectionError as LiteLLMAPIConnectionError
    from litellm.exceptions import InternalServerError as LiteLLMInternalServerError
    from litellm.exceptions import (
        ServiceUnavailableError as LiteLLMServiceUnavailableError,
    )
    from litellm.exceptions import Timeout as LiteLLMTimeout

    retryable_types: tuple[type[BaseException], ...] = (
        LiteLLMTimeout,
        LiteLLMAPIConnectionError,
        LiteLLMServiceUnavailableError,
        LiteLLMInternalServerError,
    )
    try:
        from openai import APIConnectionError as OpenAIAPIConnectionError
        from openai import APITimeoutError as OpenAIAPITimeoutError

        retryable_types += (OpenAIAPIConnectionError, OpenAIAPITimeoutError)
    except ImportError:  # pragma: no cover - LiteLLM normally installs OpenAI
        pass
    return any(isinstance(item, retryable_types) for item in _exception_chain(exc))



def _build_midstream_partial_assistant(
    assistant_message_cls: type,
    *,
    content: str | None,
    reasoning: str | None,
):
    """Build a Pydantic message whose reasoning survives stock serialization."""

    class _MidstreamPartialAssistant(assistant_message_cls):
        reasoning_content: str | None = None

    message = _MidstreamPartialAssistant(content=content)
    _attach_reasoning_fields(
        message,
        reasoning,
        source="midstream_continuation",
    )
    return message



def apply_midstream_inference_continuation_patch() -> None:
    """Preserve and conditionally continue a partially streamed chat answer."""
    if not _env_flag_default_true("ONYX_LLM_MIDSTREAM_CONTINUATION_ENABLED"):
        return

    try:
        from onyx.llm import multi_llm
        from onyx.llm.model_response import Delta
        from onyx.llm.model_response import ModelResponseStream
        from onyx.llm.model_response import StreamingChoice
        from onyx.llm.models import AssistantMessage
        from onyx.llm.models import ReasoningEffort
        from onyx.llm.models import UserMessage
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing mid-stream continuation modules: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    current_stream = multi_llm.LitellmLLM.stream
    if getattr(current_stream, "_wrapper_midstream_continuation", False):
        return

    expected_parameters = (
        "self",
        "prompt",
        "tools",
        "tool_choice",
        "structured_response_format",
        "timeout_override",
        "max_tokens",
        "reasoning_effort",
        "user_identity",
    )
    if tuple(inspect.signature(current_stream).parameters) != expected_parameters:
        _warn_or_raise(
            "LitellmLLM.stream signature changed; refusing mid-stream continuation patch"
        )
        return
    try:
        stream_source = inspect.getsource(current_stream)
    except Exception as e:  # pragma: no cover
        _warn_or_raise(f"could not inspect LitellmLLM.stream: {e}")
        return
    expected_source_markers = (
        "retryable_exceptions = (",
        "LLM_FIRST_CHUNK_MAX_RETRIES",
        "if yielded_any or attempt >= max_attempts - 1:",
        "from_litellm_model_response_stream(chunk)",
    )
    missing = [
        marker for marker in expected_source_markers if marker not in stream_source
    ]
    if missing:
        _warn_or_raise(
            "LitellmLLM.stream no longer matches the expected retry contract; "
            f"missing {missing!r}"
        )
        return

    original_stream = current_stream

    def _synthetic_chunk(
        last_packet,
        content: str,
        finish_reason: str | None = None,
        *,
        as_reasoning: bool = False,
    ):
        return ModelResponseStream(
            id=last_packet.id,
            created=last_packet.created,
            choice=StreamingChoice(
                finish_reason=finish_reason,
                index=last_packet.choice.index,
                delta=Delta(
                    content=None if as_reasoning else content,
                    reasoning_content=content if as_reasoning else None,
                ),
            ),
        )

    @functools.wraps(original_stream)
    def _stream_with_midstream_continuation(
        self,
        prompt,
        tools=None,
        tool_choice=None,
        structured_response_format=None,
        timeout_override=None,
        max_tokens=None,
        reasoning_effort=ReasoningEffort.AUTO,
        user_identity=None,
    ):
        original_prompt = list(prompt) if isinstance(prompt, list) else [prompt]
        generated_answer_parts: list[str] = []
        generated_reasoning_parts: list[str] = []
        last_packet = None
        saw_tool_delta = False
        saw_terminal_finish = False
        continuation_count = 0
        continuation_prompt = prompt

        while True:
            is_continuation = continuation_count > 0
            made_continuation_progress = False
            try:
                for packet in original_stream(
                    self,
                    prompt=continuation_prompt,
                    tools=tools,
                    tool_choice=tool_choice,
                    structured_response_format=structured_response_format,
                    timeout_override=timeout_override,
                    max_tokens=max_tokens,
                    reasoning_effort=reasoning_effort,
                    user_identity=user_identity,
                ):
                    last_packet = packet
                    delta = packet.choice.delta
                    if delta.tool_calls:
                        saw_tool_delta = True
                    if delta.reasoning_content:
                        generated_reasoning_parts.append(delta.reasoning_content)
                        if is_continuation:
                            made_continuation_progress = True
                        if is_continuation and generated_answer_parts:
                            # A second reasoning phase after answer output violates
                            # Onyx's one-ReasoningStart stream contract. Retain it for
                            # another continuation prompt, but do not emit it to UI.
                            packet = packet.model_copy(deep=True)
                            packet.choice.delta.reasoning_content = None
                            delta = packet.choice.delta
                    if delta.content:
                        generated_answer_parts.append(delta.content)
                        if is_continuation:
                            made_continuation_progress = True
                    if packet.choice.finish_reason:
                        saw_terminal_finish = True
                    if (
                        delta.content
                        or delta.reasoning_content is not None
                        or delta.tool_calls
                        or packet.usage
                        or packet.choice.finish_reason
                    ):
                        yield packet
                return
            except Exception as exc:
                if saw_terminal_finish:
                    print(
                        "sitecustomize: inference stream finalization failed after "
                        "a terminal finish; preserving response with a warning",
                        flush=True,
                    )
                    yield _synthetic_chunk(
                        last_packet,
                        _MIDSTREAM_FINALIZATION_FAILED_NOTICE,
                        "stop",
                    )
                    return
                can_preserve = (
                    bool(generated_answer_parts or generated_reasoning_parts)
                    and not saw_tool_delta
                    and structured_response_format is None
                    and _midstream_retryable_exception(exc)
                )
                if not can_preserve:
                    raise

                if is_continuation and not made_continuation_progress:
                    print(
                        "sitecustomize: mid-stream inference recovery exhausted; "
                        f"preserving partial response after {continuation_count} "
                        "continuation attempt(s)",
                        flush=True,
                    )
                    yield _synthetic_chunk(
                        last_packet,
                        _MIDSTREAM_CONTINUATION_FAILED_NOTICE,
                        "stop",
                    )
                    return

                continuation_count += 1
                print(
                    "sitecustomize: continuing interrupted inference stream "
                    f"({type(exc).__name__}); attempt "
                    f"{continuation_count}",
                    flush=True,
                )
                answer_started = bool(generated_answer_parts)
                yield _synthetic_chunk(
                    last_packet,
                    (
                        _MIDSTREAM_CONTINUATION_NOTICE
                        if answer_started
                        else _MIDSTREAM_REASONING_CONTINUATION_NOTICE
                    ),
                    as_reasoning=not answer_started,
                )

                partial_assistant = _build_midstream_partial_assistant(
                    AssistantMessage,
                    content="".join(generated_answer_parts) or None,
                    reasoning="".join(generated_reasoning_parts) or None,
                )
                # Keep the original prefix and its objects unchanged for both
                # reasoning preservation and provider-side prefix-cache reuse.
                continuation_prompt = [
                    *original_prompt,
                    partial_assistant,
                    UserMessage(content=_MIDSTREAM_CONTINUATION_INSTRUCTION),
                ]

    _stream_with_midstream_continuation._wrapper_midstream_continuation = True
    multi_llm.LitellmLLM.stream = _stream_with_midstream_continuation
    print(
        "sitecustomize: patched progress-gated mid-stream inference continuation",
        flush=True,
    )
