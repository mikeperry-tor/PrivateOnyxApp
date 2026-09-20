"""api deep research patch implementation."""

from __future__ import annotations

import functools
import inspect
import threading
from contextvars import ContextVar
from types import ModuleType
from onyx_wrapper_patches.api.config import _DEEP_RESEARCH_PROVIDE_CHAT_AGENT_TOOLS
from onyx_wrapper_patches.api.reasoning import _attach_reasoning_fields
from onyx_wrapper_patches.common.config import _env_flag_default_true
from onyx_wrapper_patches.common.config import _env_flag_enabled
from onyx_wrapper_patches.common.config import _raise_if_strict
from onyx_wrapper_patches.common.config import _required_positive_int
from onyx_wrapper_patches.common.config import _warn_or_raise
from onyx_wrapper_patches.common.source import _patch_function_source


_DEEP_RESEARCH_WORKER_LIMIT: ContextVar[int | None] = ContextVar(
    "wrapper_deep_research_worker_limit",
    default=None,
)



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



_DEEP_RESEARCH_OUTPUT_LIMIT_REPLACEMENTS = {
    "run_deep_research_llm_loop": (
        """# Even for the reasoning tool, this should be plenty
                    # The generation here should never be very long as it's just the tool calls.
                    # This prevents timeouts where the model gets into an endless loop of null or bad tokens.
                    max_tokens=1024,""",
        """# Use the normal provider output allowance, including native reasoning.
                    max_tokens=None,""",
    ),
    "run_research_agent_call": (
        """# In case the model is tripped up by the long context and gets into an endless loop of
                    # things like null tokens, we set a max token limit here. The call will likely not be valid
                    # in these situations but it at least allows a chance of recovery. None of the tool calls should
                    # be this long.
                    max_tokens=1000,""",
        """# Use the normal provider output allowance, including native reasoning.
                    max_tokens=None,""",
    ),
}



def _research_report_output_limits() -> tuple[tuple[ModuleType, str, str, int], ...]:
    from onyx.deep_research import dr_loop
    from onyx.tools.fake_tools import research_agent

    return (
        (research_agent, "generate_intermediate_report", "MAX_INTERMEDIATE_REPORT_LENGTH_TOKENS", 10000),
        (dr_loop, "generate_final_report", "MAX_FINAL_REPORT_TOKENS", 20000),
    )



def _validate_research_report_output_limit(
    module: ModuleType, function_name: str, constant_name: str, expected: int | None,
) -> None:
    function = getattr(module, function_name)
    source = inspect.getsource(function)
    if (
        source.count(f"max_tokens={constant_name},") != 1
        or constant_name not in function.__code__.co_names
        or function.__globals__ is not module.__dict__
        or getattr(module, constant_name) != expected
    ):
        raise RuntimeError(f"{function_name}: report output-limit source/binding drift")



def apply_deep_research_output_limit_patch() -> None:
    """Delegate investigation and report output allowances to the provider."""
    from onyx.deep_research import dr_loop
    from onyx.tools.fake_tools import research_agent

    for module, name in (
        (dr_loop, "run_deep_research_llm_loop"),
        (research_agent, "run_research_agent_call"),
    ):
        old, new = _DEEP_RESEARCH_OUTPUT_LIMIT_REPLACEMENTS[name]
        function = getattr(module, name)
        source = getattr(function, "_wrapper_patched_source", None)
        if source is None:
            source = inspect.getsource(function)
        if source.count(old) != 1:
            raise RuntimeError(f"{name}: expected exactly one upstream output-limit block")
        _patch_function_source(
            module=module, function_name=name, replacements={old: new},
            patch_name=f"{name} provider output allowance",
        )

    # Report functions already read module constants at call time; no source
    # rebuild or imported-function rebinding is needed for these two limits.
    for module, function_name, constant_name, upstream_limit in _research_report_output_limits():
        _validate_research_report_output_limit(module, function_name, constant_name, upstream_limit)
        setattr(module, constant_name, None)
    print("sitecustomize: removed Deep Research report output caps", flush=True)



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
