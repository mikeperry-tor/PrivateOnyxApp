"""api tool calls patch implementation."""

from __future__ import annotations

import functools
import inspect
from onyx_wrapper_patches.common.config import _env_flag_default_true
from onyx_wrapper_patches.common.config import _raise_if_strict
from onyx_wrapper_patches.common.config import _warn_or_raise


def apply_native_tool_calls_only_patch() -> None:
    """Leave JSON/XML-looking tool text visible and never execute it as a tool."""
    if not _env_flag_default_true("ONYX_LLM_NATIVE_TOOL_CALLS_ONLY"):
        return

    try:
        from onyx.chat import llm_loop
        from onyx.chat import llm_step
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing textual tool fallback modules: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    current_fallback = llm_loop._try_fallback_tool_extraction
    current_process = llm_step._XmlToolCallContentFilter.process
    current_flush = llm_step._XmlToolCallContentFilter.flush
    if (
        getattr(current_fallback, "_wrapper_native_tool_calls_only", False)
        and getattr(current_process, "_wrapper_xml_tool_text_passthrough", False)
        and getattr(current_flush, "_wrapper_xml_tool_text_passthrough", False)
    ):
        return

    expected_fallback_parameters = (
        "llm_step_result",
        "tool_choice",
        "fallback_extraction_attempted",
        "tool_defs",
        "turn_index",
    )
    if (
        tuple(inspect.signature(current_fallback).parameters)
        != expected_fallback_parameters
    ):
        _warn_or_raise(
            "llm_loop._try_fallback_tool_extraction signature changed; refusing "
            "native-tool-only patch"
        )
        return
    if tuple(inspect.signature(current_process).parameters) != ("self", "content"):
        _warn_or_raise(
            "llm_step._XmlToolCallContentFilter.process signature changed; "
            "refusing XML tool-text passthrough patch"
        )
        return
    if tuple(inspect.signature(current_flush).parameters) != ("self",):
        _warn_or_raise(
            "llm_step._XmlToolCallContentFilter.flush signature changed; refusing "
            "XML tool-text passthrough patch"
        )
        return

    try:
        fallback_source = inspect.getsource(current_fallback)
        process_source = inspect.getsource(current_process)
        flush_source = inspect.getsource(current_flush)
    except Exception as e:  # pragma: no cover
        _warn_or_raise(f"could not inspect textual tool fallback helpers: {e}")
        return

    fallback_markers = (
        "extract_tool_calls_from_response_text(",
        "xml_tool_call_text_detected",
        "return llm_step_result, True",
    )
    process_markers = (
        "self._inside_function_calls_block",
        "_find_function_calls_open_marker",
        "Drop the whole function_calls block",
    )
    flush_markers = (
        "if self._inside_function_calls_block:",
        "Drop any incomplete block at stream end",
        "remaining = self._pending",
    )
    missing = [marker for marker in fallback_markers if marker not in fallback_source]
    missing += [
        marker for marker in process_markers if marker not in process_source
    ]
    missing += [marker for marker in flush_markers if marker not in flush_source]
    if missing:
        _warn_or_raise(
            "textual tool fallback no longer matches the expected extraction/filter "
            f"contract; missing {missing!r}"
        )
        return

    @functools.wraps(current_fallback)
    def _native_tool_calls_only(
        llm_step_result,
        tool_choice,
        fallback_extraction_attempted,
        tool_defs,
        turn_index,
    ):
        del tool_choice, fallback_extraction_attempted, tool_defs, turn_index
        return llm_step_result, False

    @functools.wraps(current_process)
    def _pass_through_xml_tool_text(self, content: str) -> str:
        del self
        return content or ""

    @functools.wraps(current_flush)
    def _flush_no_buffered_xml_tool_text(self) -> str:
        del self
        return ""

    _native_tool_calls_only._wrapper_native_tool_calls_only = True
    _pass_through_xml_tool_text._wrapper_xml_tool_text_passthrough = True
    _flush_no_buffered_xml_tool_text._wrapper_xml_tool_text_passthrough = True
    llm_loop._try_fallback_tool_extraction = _native_tool_calls_only
    llm_step._XmlToolCallContentFilter.process = _pass_through_xml_tool_text
    llm_step._XmlToolCallContentFilter.flush = _flush_no_buffered_xml_tool_text
    print(
        "sitecustomize: disabled textual JSON/XML tool extraction and made XML "
        "tool text visible",
        flush=True,
    )



def apply_vllm_glm_auto_tool_choice_patch() -> None:
    """Avoid vLLM's broken forced-tool path in wrapper agent workflows.

    vLLM 0.24.0 moved GLM-5.x onto its unified parser/structural-tag engine
    and removed the previous GLM safeguard that skipped structured decoding
    for ``tool_choice=required``. Keep the workaround scoped to the internal
    coding, Deep Research, nested research, and explicit chat tool-forcing
    contexts. Each retains its existing no-tool completion branch when the
    model returns ordinary assistant content.
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
