"""api prompt stability patch implementation."""

from __future__ import annotations

import inspect
from types import ModuleType
from onyx_wrapper_patches.api.deep_research import _research_report_output_limits
from onyx_wrapper_patches.api.deep_research import _validate_research_report_output_limit
from onyx_wrapper_patches.common.source import _patch_function_source
from onyx_wrapper_patches.common.source import _prompt_stability_replace


_PROMPT_STABILITY_CHAT_REPLACEMENTS = {
    '        code_interpreter_file_generated: bool = False\n': '',
    (
        '                # Track if code interpreter generated files with download links\n'
        '                if (\n'
        '                    tool_call.tool_name == PythonTool.NAME\n'
        '                    and not code_interpreter_file_generated\n'
        '                ):\n'
        '                    try:\n'
        '                        parsed = json.loads(tool_response.llm_facing_response)\n'
        '                        if parsed.get("generated_files"):\n'
        '                            code_interpreter_file_generated = True\n'
        '                    except (json.JSONDecodeError, AttributeError):\n'
        '                        pass\n'
        '\n'
    ): '',
    '        reasoning_cycles = 0\n': (
        '        stable_default_prompt = bool(default_base_system_prompt) and not (\n'
        '            persona and persona.replace_base_system_prompt\n'
        '        )\n'
        '        stable_citations = include_citations and (\n'
        '            always_cite_documents or any(tool.name in CITEABLE_TOOLS_NAMES for tool in tools)\n'
        '        )\n'
        '        reasoning_cycles = 0\n'
    ),
    '            cite_documents = should_cite_documents or always_cite_documents\n': (
        '            cite_documents = (\n'
        '                stable_citations if stable_default_prompt\n'
        '                else should_cite_documents or always_cite_documents\n'
        '            )\n'
    ),
    '                just_ran_web_search=just_ran_web_search,\n': '                just_ran_web_search=just_ran_web_search and not stable_default_prompt,\n',
    (
        '                include_citation_reminder=should_cite_documents\n'
        '                or always_cite_documents,\n'
    ): '                include_citation_reminder=cite_documents,\n',
    '                include_file_reminder=code_interpreter_file_generated,\n': '                include_file_reminder=False,\n',
    '            if llm_step_result.tool_calls and any(\n': '            if not stable_default_prompt and llm_step_result.tool_calls and any(\n',
}



_PROMPT_STABILITY_DR_REPLACEMENTS = {
    '        include_internal_search_tunings = SearchTool.NAME in allowed_tool_names\n': '        include_internal_search_tunings = any(tool.name == SearchTool.NAME for tool in allowed_tools)\n',
    (
        '                if cycle == 1:\n'
        '                    first_cycle_reminder_message = ChatMessageSimple(\n'
        '                        message=FIRST_CYCLE_REMINDER,\n'
        '                        token_count=FIRST_CYCLE_REMINDER_TOKENS,\n'
        '                        message_type=MessageType.USER_REMINDER,\n'
        '                    )\n'
        '                else:\n'
        '                    first_cycle_reminder_message = None\n'
        '\n'
    ): (
        '                first_cycle_reminder_message = None\n'
        '\n'
    ),
}



_PROMPT_STABILITY_RESEARCH_REPLACEMENTS = {
    (
        '                # Gate the open_url nudge on the tool actually being available.\n'
        '                if just_ran_web_search and has_open_url_tool:\n'
        '                    reminder_message = ChatMessageSimple(\n'
        '                        message=OPEN_URL_REMINDER_RESEARCH_AGENT,\n'
        '                        token_count=100,\n'
        '                        message_type=MessageType.USER,\n'
        '                    )\n'
        '                else:\n'
        '                    reminder_message = None\n'
        '\n'
    ): (
        '                reminder_message = None\n'
        '\n'
    ),
}



_PROMPT_STABILITY_FUNCTIONS: list[tuple[ModuleType, str, object, str]] = []



_PROMPT_STABILITY_CONSTANTS: list[tuple[ModuleType, ModuleType, str, str]] = []



def _patch_investigation_source(
    module: ModuleType, name: str, replacements: dict[str, str],
) -> None:
    function = getattr(module, name)
    source = getattr(function, "_wrapper_patched_source", None)
    if not isinstance(source, str):
        raise RuntimeError(f"{name}: prompt stability must follow existing loop patches")
    expected = source
    for old, new in replacements.items():
        expected = _prompt_stability_replace(expected, old, new, name)
    _patch_function_source(
        module=module, function_name=name, replacements=replacements,
        patch_name=f"{name} prompt stability",
    )
    installed = getattr(module, name)
    if installed._wrapper_patched_source != expected:
        raise RuntimeError(f"{name}: accumulated source was not preserved")
    _PROMPT_STABILITY_FUNCTIONS.append((module, name, installed, expected))



def _patch_investigation_constant(
    owner: ModuleType, consumer: ModuleType, name: str, old: str, new: str,
    count: int = 1,
) -> None:
    current = getattr(owner, name)
    if getattr(consumer, name) != current:
        raise RuntimeError(f"{name}: stale prompt consumer before installation")
    value = _prompt_stability_replace(current, old, new, name, count)
    setattr(owner, name, value)
    setattr(consumer, name, value)
    _PROMPT_STABILITY_CONSTANTS.append((owner, consumer, name, value))



def apply_agent_prompt_stability_patches() -> None:
    """Keep ordinary investigation prefixes stable without changing tool policy."""
    from onyx.chat import llm_loop, prompt_utils
    from onyx.deep_research import dr_loop
    from onyx.prompts import chat_prompts, tool_prompts
    from onyx.prompts import prompt_utils as placeholder_prompts
    from onyx.prompts.deep_research import dr_tool_prompts, orchestration_layer
    from onyx.prompts.deep_research import research_agent as research_prompts
    from onyx.prompts.coding_agent import coding_agent as coding_prompts
    from onyx.tools.fake_tools import research_agent, coding_agent

    _patch_investigation_source(llm_loop, "run_llm_loop", _PROMPT_STABILITY_CHAT_REPLACEMENTS)
    if placeholder_prompts.REQUIRE_CITATION_GUIDANCE != chat_prompts.REQUIRE_CITATION_GUIDANCE:
        raise RuntimeError("stale citation guidance in prompt placeholder consumer")
    _patch_investigation_constant(
        chat_prompts, prompt_utils, "REQUIRE_CITATION_GUIDANCE",
        "DO NOT provide any links following the citations.",
        "Do not append URLs to numeric citations. Citation numbers belong to the "
        "current user turn: use numbers from the current context documents or tool "
        "results obtained after the latest user message. Earlier assistant responses "
        "and earlier-turn tool results may use the same numbers for different sources. "
        "When citing a source from an earlier turn, use an inline descriptive Markdown "
        "link to its known source URL, such as [Source title](URL), instead of copying "
        "its old number or numbered link. You may reuse evidence still present in "
        "the conversation without fetching it again; retrieve the source again if "
        "you need a current numbered citation or missing or updated evidence. If its "
        "URL is unavailable, retrieve it with an available tool or state the source "
        "limitation; never invent a URL or citation number.",
    )
    placeholder_prompts.REQUIRE_CITATION_GUIDANCE = chat_prompts.REQUIRE_CITATION_GUIDANCE
    _PROMPT_STABILITY_CONSTANTS.append((
        chat_prompts, placeholder_prompts, "REQUIRE_CITATION_GUIDANCE",
        chat_prompts.REQUIRE_CITATION_GUIDANCE,
    ))
    _patch_investigation_constant(
        chat_prompts, prompt_utils, "CITATION_REMINDER",
        'based on the "document" field of the documents.',
        'based on the "document" field of the current turn\'s documents. For earlier-turn '
        'sources, use descriptive links to known source URLs, not their old citation numbers.',
    )
    _patch_investigation_source(
        dr_loop, "run_deep_research_llm_loop", _PROMPT_STABILITY_DR_REPLACEMENTS,
    )
    _patch_investigation_source(
        research_agent, "run_research_agent_call", _PROMPT_STABILITY_RESEARCH_REPLACEMENTS,
    )
    _patch_investigation_constant(
        tool_prompts, prompt_utils, "OPEN_URLS_GUIDANCE",
        "You should almost always use open_url after a web_search call.",
        "After web_search, use open_url to open promising pages unless the snippets "
        "completely answer the query.",
    )
    for name in ("ORCHESTRATOR_PROMPT", "ORCHESTRATOR_PROMPT_REASONING"):
        _patch_investigation_constant(
            orchestration_layer, dr_loop, name,
            "You have currently used {current_cycle_count} of {max_cycles} max research cycles.",
            "You have a maximum budget of {max_cycles} research cycles.",
        )
    for name in ("RESEARCH_AGENT_PROMPT", "RESEARCH_AGENT_PROMPT_REASONING"):
        _patch_investigation_constant(
            research_prompts, research_agent, name,
            "You are on cycle {current_cycle_count} of ",
            "Your maximum cycle budget is ",
        )
    for name in ("OPEN_URLS_TOOL_DESCRIPTION", "OPEN_URLS_TOOL_DESCRIPTION_REASONING"):
        current = getattr(dr_tool_prompts, name)
        current = _prompt_stability_replace(current, "open_urls", "open_url", name, 3)
        if name.endswith("_REASONING"):
            old = "You should almost always use open_url after a web_search call."
            new = (
                "After web_search, use open_url to open promising pages unless the snippets "
                "completely answer the query."
            )
        else:
            old = (
                "You should almost always use open_url after a web_search call and "
                "sometimes after reasoning with the think_tool tool."
            )
            new = (
                "After web_search, use open_url to open promising pages unless the snippets "
                "completely answer the query, and consider opening pages after reasoning "
                "with the think_tool tool."
            )
        value = _prompt_stability_replace(current, old, new, name)
        _patch_investigation_constant(
            dr_tool_prompts, research_agent, name, getattr(dr_tool_prompts, name), value,
        )
    for name in ("CODING_AGENT_PROMPT", "CODING_AGENT_PROMPT_REASONING"):
        _patch_investigation_constant(
            coding_prompts, coding_agent, name,
            " (you are on cycle {current_cycle_count})", "",
        )



def validate_agent_prompt_stability_patches() -> None:
    """Fail bootstrap if a later installer restores source or stale imports."""
    from onyx.chat import llm_loop, process_message, prompt_utils
    from onyx.deep_research import dr_loop
    from onyx.tools.fake_tools import research_agent, coding_agent

    if len(_PROMPT_STABILITY_FUNCTIONS) != 3 or len(_PROMPT_STABILITY_CONSTANTS) != 12:
        raise RuntimeError("prompt stability installation missing or duplicated")
    for module, name, installed, source in _PROMPT_STABILITY_FUNCTIONS:
        if (
            getattr(module, name) is not installed
            or installed._wrapper_patched_source != source
        ):
            raise RuntimeError(f"{name}: final prompt stability source/binding drift")
    for owner, consumer, name, value in _PROMPT_STABILITY_CONSTANTS:
        if getattr(owner, name) != value or getattr(consumer, name) != value:
            raise RuntimeError(f"{name}: final prompt binding drift")
    for name, module in (("run_llm_loop", llm_loop), ("run_deep_research_llm_loop", dr_loop)):
        if getattr(process_message, name) is not getattr(module, name):
            raise RuntimeError(f"process_message.{name}: stale application caller binding")
    source = getattr(llm_loop.construct_message_history, "_wrapper_patched_source", "")
    if (
        "result.append(reminder_message)" not in source
        or source.rfind("result.append(reminder_message)")
        > source.rfind("result.extend(messages_after_last_user)")
    ):
        raise RuntimeError("prompt stability requires installed reminder placement")
    for module, name, names in (
        (prompt_utils, "build_system_prompt", ("OPEN_URLS_GUIDANCE",)),
        (dr_loop, "run_deep_research_llm_loop", (
            "ORCHESTRATOR_PROMPT", "ORCHESTRATOR_PROMPT_REASONING",
        )),
        (research_agent, "run_research_agent_call", (
            "RESEARCH_AGENT_PROMPT", "RESEARCH_AGENT_PROMPT_REASONING",
            "OPEN_URLS_TOOL_DESCRIPTION", "OPEN_URLS_TOOL_DESCRIPTION_REASONING",
        )),
        (coding_agent, "run_coding_agent_call", (
            "CODING_AGENT_PROMPT", "CODING_AGENT_PROMPT_REASONING",
        )),
    ):
        function = getattr(module, name)
        # The pinned orchestrator retains its timing decorator. Inspect the
        # executed closure, not functools.wraps' historical __wrapped__ link.
        if module is dr_loop:
            function = inspect.getclosurevars(function).nonlocals["func"]
        for binding in names:
            if (
                binding not in function.__code__.co_names
                or function.__globals__.get(binding) != getattr(module, binding)
            ):
                raise RuntimeError(f"{name}.{binding}: stale active function globals")
    for module, function_name, constant_name, _ in _research_report_output_limits():
        _validate_research_report_output_limit(module, function_name, constant_name, None)
    print("sitecustomize: validated investigation prompt stability", flush=True)
