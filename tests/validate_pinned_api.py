"""Validate wrapper and upstream API contracts inside the pinned Onyx image."""

from __future__ import annotations

import inspect

import wrapper_env_patches as patches


def _install_wrapper_patches() -> None:
    patches.apply_llm_max_tokens_override_patch()
    patches.apply_open_url_char_limit_patches()
    patches.apply_internal_search_context_patches()
    patches.apply_native_reasoning_detection_override_patch()
    patches.apply_python_file_link_prompt_patches()
    patches.apply_python_package_capability_patches()
    patches.apply_vllm_glm_auto_tool_choice_patch()
    patches.apply_deep_research_chat_agent_tools_patch()
    patches.apply_reasoning_content_preservation_patch()
    patches.apply_coding_agent_final_answer_fallback_patch()
    patches.apply_preserve_tool_results_patch()


def _validate_python_tool_identity() -> None:
    from onyx.prompts.tool_prompts import PYTHON_TOOL_GUIDANCE
    from onyx.tools import built_in_tools
    from onyx.tools.constants import PYTHON_TOOL_ID, PYTHON_TOOL_NAME
    from onyx.tools.tool_implementations.python.python_tool import PythonTool

    assert PYTHON_TOOL_NAME == "run_python"
    assert PythonTool.NAME == "run_python"
    assert PythonTool.DISPLAY_NAME == "Code Interpreter"
    assert built_in_tools.TOOL_NAME_TO_CLASS["run_python"] is PythonTool
    assert "python" not in built_in_tools.TOOL_NAME_TO_CLASS
    assert built_in_tools.llm_tool_name(PYTHON_TOOL_ID, "stale-db-name") == "run_python"
    assert "## run_python" in PYTHON_TOOL_GUIDANCE
    assert "Use the `run_python` tool" in PYTHON_TOOL_GUIDANCE


def _validate_indexed_open_url_contract() -> None:
    from onyx.tools.tool_implementations.open_url import open_url_tool

    original_normalize = open_url_tool.normalize_url_candidates
    original_filter = open_url_tool.filter_existing_document_ids
    try:
        open_url_tool.normalize_url_candidates = lambda _url: [
            "candidate-first",
            "candidate-second",
        ]
        open_url_tool.filter_existing_document_ids = (
            lambda _session, _ids: {"candidate-first", "candidate-second"}
        )
        matches, unresolved = open_url_tool._resolve_urls_to_document_ids(
            ["requested-url"], None
        )
        assert unresolved == []
        assert len(matches) == 1
        assert matches[0].document_id == "candidate-first"
        assert matches[0].original_url == "requested-url"

        open_url_tool.filter_existing_document_ids = (
            lambda _session, _ids: {"candidate-second"}
        )
        matches, unresolved = open_url_tool._resolve_urls_to_document_ids(
            ["requested-url"], None
        )
        assert unresolved == []
        assert len(matches) == 1
        assert matches[0].document_id == "candidate-second"
    finally:
        open_url_tool.normalize_url_candidates = original_normalize
        open_url_tool.filter_existing_document_ids = original_filter

    filter_source = inspect.getsource(open_url_tool.OpenURLTool._build_index_filters)
    assert "build_access_filters_for_user(self._user, db_session)" in filter_source
    assert "access_control_list=access_control_list" in filter_source

    run_source = inspect.getsource(open_url_tool.OpenURLTool.run)
    assert "run_functions_tuples_in_parallel" in run_source
    assert "(_retrieve_indexed_with_filters, (all_requests,))" in run_source
    assert "(self._fetch_web_content, (urls, override_kwargs.url_snippet_map))" in run_source
    assert "allow_failures=True" in run_source


if __name__ == "__main__":
    _install_wrapper_patches()
    _validate_python_tool_identity()
    _validate_indexed_open_url_contract()
    print("PINNED_API_PATCH_CONTRACTS_OK")
