"""Validate wrapper and upstream API contracts inside the pinned Onyx image."""

from __future__ import annotations

import inspect
import json
from importlib.metadata import version
from types import SimpleNamespace

import wrapper_env_patches as patches


def _install_wrapper_patches() -> None:
    patches.apply_llm_max_tokens_override_patch()
    patches.apply_open_url_char_limit_patches()
    patches.apply_internal_search_context_patches()
    patches.apply_native_reasoning_detection_override_patch()
    patches.apply_python_file_link_prompt_patches()
    patches.apply_chat_file_id_validation_patch()
    patches.apply_python_package_capability_patches()
    patches.apply_vllm_glm_auto_tool_choice_patch()
    patches.apply_deep_research_chat_agent_tools_patch()
    patches.apply_reasoning_content_preservation_patch()
    patches.apply_coding_agent_final_answer_fallback_patch()
    patches.apply_preserve_tool_results_patch()
    patches.apply_python_file_link_enforcement_patches()
    patches.apply_searxng_single_attempt_patch()


def _validate_python_tool_identity() -> None:
    from onyx.file_store.utils import build_frontend_file_url
    from onyx.prompts.tool_prompts import PYTHON_TOOL_GUIDANCE
    from onyx.tools import built_in_tools
    from onyx.tools.constants import PYTHON_TOOL_ID, PYTHON_TOOL_NAME
    from onyx.tools.tool_implementations.python import python_tool
    from onyx.tools.tool_implementations.python.python_tool import PythonTool

    assert PYTHON_TOOL_NAME == "run_python"
    assert PythonTool.NAME == "run_python"
    assert PythonTool.DISPLAY_NAME == "Code Interpreter"
    assert built_in_tools.TOOL_NAME_TO_CLASS["run_python"] is PythonTool
    assert "python" not in built_in_tools.TOOL_NAME_TO_CLASS
    assert built_in_tools.llm_tool_name(PYTHON_TOOL_ID, "stale-db-name") == "run_python"
    assert "## run_python" in PYTHON_TOOL_GUIDANCE
    assert "Use the `run_python` tool" in PYTHON_TOOL_GUIDANCE
    assert "response_markdown" in PythonTool.DESCRIPTION
    assert "response_markdown" in PYTHON_TOOL_GUIDANCE
    assert "an sandbox" not in PythonTool.DESCRIPTION
    assert getattr(PythonTool.run, "_wrapper_python_file_link_patch", False)
    assert python_tool.build_full_frontend_file_url is build_frontend_file_url


def _validate_python_file_link_enforcement() -> None:
    from onyx.chat import llm_loop
    from onyx.server.query_and_chat import session_loading
    from onyx.tools.tool_implementations.python.python_tool import PythonTool

    assert getattr(llm_loop.run_llm_step, "_wrapper_chat_file_markdown", False)
    assert getattr(
        session_loading.translate_assistant_message_to_packets,
        "_wrapper_chat_file_markdown",
        False,
    )

    tool = PythonTool(1, None)
    replacement_prompt = llm_loop._wrapper_append_python_guidance(
        "replacement prompt", [tool]
    )
    assert "## run_python" in replacement_prompt
    assert "response_markdown" in replacement_prompt

    raw = (
        "before ![graph.png](https://tail.example/api/chat/file/file-id) "
        "and [data.csv](http://localhost:3000/api/chat/file/data-id) after"
    )
    expected = (
        "before [graph.png](/api/chat/file/file-id) "
        "and [data.csv](/api/chat/file/data-id) after"
    )
    assert patches._normalize_chat_file_markdown(raw) == expected
    assert patches._normalize_chat_file_markdown(
        "![Simple Function Graphs](/api/chat/file/file-id)",
        {"file-id": "graph.png"},
    ) == "[graph.png](/api/chat/file/file-id)"
    canonical_id = "0ad58c02-1c2d-4e22-9c41-d9e13a5e1d7b"
    assert patches._normalize_chat_file_markdown(
        "![Simple Function Graphs](/api/chat/file/"
        "0ad5_8c02-1c2d-4e22-9c41-d9e13a5e1d7b)",
        {canonical_id: "graph.png"},
    ) == f"[graph.png](/api/chat/file/{canonical_id})"
    saved_tool_call = SimpleNamespace(
        generated_files=None,
        tool_call_response=json.dumps(
            {
                "generated_files": [
                    {
                        "filename": "saved.png",
                        "file_link": "/api/chat/file/saved-id",
                    }
                ]
            }
        ),
    )
    assert patches._generated_chat_file_filenames([saved_tool_call]) == {
        "saved-id": "saved.png"
    }
    assert session_loading._wrapper_normalize_saved_chat_file_markdown(
        "![Saved chart](/api/chat/file/saved-id)", [saved_tool_call]
    ) == "[saved.png](/api/chat/file/saved-id)"
    literal = "`![literal](/api/chat/file/literal)`"
    assert patches._normalize_chat_file_markdown(literal) == literal
    for split in range(len(raw) + 1):
        stream = patches._ChatFileMarkdownStream()
        actual = (
            stream.feed(raw[:split])
            + stream.feed(raw[split:])
            + stream.flush()
        )
        assert actual == expected

    from onyx.db import user_file

    assert getattr(
        user_file.get_file_id_by_user_file_id,
        "_wrapper_chat_file_id_guard",
        False,
    )
    assert user_file.get_file_id_by_user_file_id(
        "0ad5_8c02-1c2d-4e22-9c41-d9e13a5e1d7b", object()
    ) is None

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
    assert "self._fetch_web_content," in run_source
    assert "(urls, override_kwargs.url_snippet_map)," in run_source
    assert "allow_failures=True" in run_source


def _validate_lite_open_url_contract() -> None:
    from onyx.tools.tool_implementations.open_url import open_url_tool

    assert open_url_tool.OpenURLTool.is_available(None) is True
    run_source = inspect.getsource(open_url_tool.OpenURLTool.run)
    assert "if DISABLE_VECTOR_DB:" in run_source
    assert "IndexedRetrievalResult(" in run_source
    assert "self._fetch_web_content" in run_source


def _validate_web_search_timeout_contract() -> None:
    from onyx.tools import tool_runner
    from onyx.tools.tool_implementations.web_search.clients.searxng_client import (
        SearXNGClient,
    )

    assert tool_runner.TOOL_EXECUTION_TIMEOUT_SECONDS == 10 * 60
    search_source = inspect.getsource(SearXNGClient.search)
    # inspect.getsource() reports the original decorated definition even after
    # the runtime callable has been unwrapped.  Callable metadata, not source
    # text, is authoritative for whether retry wrappers remain installed.
    assert "@retry_builder(tries=3, delay=1, backoff=2)" in search_source
    assert not hasattr(SearXNGClient.search, "__wrapped__")
    assert "requests.post(" in search_source
    assert "timeout=" not in search_source


def _validate_web_search_concurrency_contract() -> None:
    from onyx.tools import tool_runner
    from onyx.tools.tool_implementations.web_search.web_search_tool import (
        QUERIES_FIELD,
        WebSearchTool,
    )

    assert tool_runner.MERGEABLE_TOOL_FIELDS[WebSearchTool.NAME] == QUERIES_FIELD
    definition_source = inspect.getsource(WebSearchTool.tool_definition)
    assert '"type": "array"' in definition_source
    assert '"maxItems"' not in definition_source
    run_source = inspect.getsource(WebSearchTool.run)
    assert "for query in queries" in run_source
    assert "run_functions_tuples_in_parallel(" in run_source
    assert "max_workers=" not in run_source


def _validate_litellm_contract() -> None:
    from litellm.litellm_core_utils.get_model_cost_map import (
        get_model_cost_map_source_info,
    )
    from litellm.llms.openai.chat.gpt_transformation import OpenAIGPTConfig
    from litellm.types.utils import Message

    assert version("litellm") == "1.93.0"
    assert get_model_cost_map_source_info() == {
        "source": "local",
        "url": None,
        "is_env_forced": True,
        "fallback_reason": None,
    }
    expected_parameters = (
        "self",
        "model",
        "messages",
        "optional_params",
        "litellm_params",
        "headers",
    )
    assert tuple(
        inspect.signature(OpenAIGPTConfig.transform_request).parameters
    ) == expected_parameters
    assert tuple(
        inspect.signature(OpenAIGPTConfig.async_transform_request).parameters
    ) == expected_parameters
    assert "reasoning_content" in Message.model_fields

    transformed = OpenAIGPTConfig().transform_request(
        "wrapper-contract-model",
        [
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "retained reasoning",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "tool", "arguments": "{}"},
                    }
                ],
            }
        ],
        {},
        {},
        {},
    )
    assert transformed["messages"][0]["reasoning_content"] == "retained reasoning"
    assert transformed["messages"][0]["tool_calls"][0]["id"] == "call-1"


if __name__ == "__main__":
    _install_wrapper_patches()
    _validate_python_tool_identity()
    _validate_python_file_link_enforcement()
    _validate_indexed_open_url_contract()
    _validate_lite_open_url_contract()
    _validate_web_search_timeout_contract()
    _validate_web_search_concurrency_contract()
    _validate_litellm_contract()
    print("PINNED_API_PATCH_CONTRACTS_OK")
