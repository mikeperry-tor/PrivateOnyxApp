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
    patches.apply_native_tool_calls_only_patch()
    patches.apply_midstream_inference_continuation_patch()
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
    assert "opaque per-execution file ID" in PythonTool.DESCRIPTION
    assert "opaque per-execution file ID" in PYTHON_TOOL_GUIDANCE
    assert "an sandbox" not in PythonTool.DESCRIPTION
    assert getattr(PythonTool.run, "_wrapper_python_file_link_patch", False)
    assert python_tool.build_full_frontend_file_url is build_frontend_file_url


def _validate_python_tool_generated_id_identity() -> None:
    """Prove one saved ID reaches every Python-tool response channel unchanged."""
    from onyx.tools.models import PythonToolOverrideKwargs
    from onyx.server.query_and_chat.streaming_models import Placement
    from onyx.tools.tool_implementations.python import python_tool
    from onyx.tools.tool_implementations.python.python_tool import PythonTool

    saved_id = "11111111-1111-4111-8111-111111111111"
    executor_id = "executor-opaque-id"
    file_bytes = b"\x89PNG\r\n\x1a\nwrapper-contract"
    emitted: list[object] = []

    class FakeResultEvent:
        files = [
            SimpleNamespace(
                kind="file", file_id=executor_id, path="/workspace/graph.png"
            )
        ]
        exit_code = 0
        timed_out = False

    class FakeClient:
        deleted: list[str] = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute_streaming(self, **kwargs):
            assert kwargs["code"] == "generate graph"
            yield FakeResultEvent()

        def download_file(self, file_id):
            assert file_id == executor_id
            return file_bytes

        def delete_file(self, file_id):
            self.deleted.append(file_id)

    class FakeStore:
        def save_file(self, content, **kwargs):
            assert content.read() == file_bytes
            assert kwargs["display_name"] == "graph.png"
            assert kwargs["file_type"] == "image/png"
            return saved_id

    originals = (
        python_tool.CodeInterpreterClient,
        python_tool.StreamResultEvent,
        python_tool.get_default_file_store,
    )
    try:
        python_tool.CodeInterpreterClient = FakeClient
        python_tool.StreamResultEvent = FakeResultEvent
        python_tool.get_default_file_store = lambda: FakeStore()
        tool = PythonTool(1, SimpleNamespace(emit=emitted.append))
        response = tool.run(
            Placement(turn_index=0),
            PythonToolOverrideKwargs(),
            code="generate graph",
        )
    finally:
        (
            python_tool.CodeInterpreterClient,
            python_tool.StreamResultEvent,
            python_tool.get_default_file_store,
        ) = originals

    payload = json.loads(response.llm_facing_response)
    generated = payload["generated_files"]
    expected_link = f"/api/chat/file/{saved_id}"
    assert generated == [
        {
            "filename": "graph.png",
            "file_link": expected_link,
            "response_markdown": f"[graph.png]({expected_link})",
        }
    ]
    assert response.rich_response.generated_files[0].file_link == expected_link
    emitted_ids = [
        file_id
        for packet in emitted
        for file_id in (getattr(packet.obj, "file_ids", None) or [])
    ]
    assert emitted_ids == [saved_id]
    assert FakeClient.deleted == [executor_id]


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
    fabricated_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert patches._normalize_chat_file_markdown(
        f"![graph.png](/api/chat/file/{fabricated_id})",
        {canonical_id: "graph.png"},
    ) == f"[graph.png](/api/chat/file/{fabricated_id})"
    assert patches._normalize_chat_file_markdown(
        f"[descriptive label](/api/chat/file/{fabricated_id})",
        {canonical_id: "graph.png"},
    ) == f"[descriptive label](/api/chat/file/{fabricated_id})"
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

    from onyx.tools import tool_constructor

    web_search_tool = SimpleNamespace(in_code_tool_id="WebSearchTool", id=17)
    assert not tool_constructor.should_disable_open_url_web_fetch(
        [web_search_tool], None
    )
    assert not tool_constructor.should_disable_open_url_web_fetch(
        [web_search_tool], [17]
    )
    assert tool_constructor.should_disable_open_url_web_fetch(
        [web_search_tool], []
    )
    constructor_source = inspect.getsource(tool_constructor._construct_tools_impl)
    assert "open_url_web_fetch_disabled" in constructor_source
    assert (
        "if open_url_web_fetch_disabled and DISABLE_VECTOR_DB:"
        in constructor_source
    )
    assert "web_fetch_disabled=open_url_web_fetch_disabled" in constructor_source
    assert "if self._web_fetch_disabled:" in run_source
    assert "WEB_FETCH_DISABLED_REASON" in run_source


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
    from onyx.chat import llm_loop
    from onyx.chat import llm_step
    from onyx.configs.chat_configs import LLM_FIRST_CHUNK_MAX_RETRIES
    from litellm.litellm_core_utils.get_model_cost_map import (
        get_model_cost_map_source_info,
    )
    from litellm.exceptions import Timeout as LiteLLMTimeout
    from litellm.llms.openai.chat.gpt_transformation import OpenAIGPTConfig
    from litellm.types.utils import Message
    from onyx.llm.multi_llm import LitellmLLM
    from onyx.llm.models import AssistantMessage

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
    assert getattr(LitellmLLM.stream, "_wrapper_midstream_continuation", False)
    assert LLM_FIRST_CHUNK_MAX_RETRIES == 1
    assert getattr(
        llm_loop._try_fallback_tool_extraction,
        "_wrapper_native_tool_calls_only",
        False,
    )
    assert getattr(
        llm_step._XmlToolCallContentFilter.process,
        "_wrapper_xml_tool_text_passthrough",
        False,
    )

    fallback_result = SimpleNamespace(tool_calls=None)
    returned, attempted = llm_loop._try_fallback_tool_extraction(
        fallback_result,
        "required",
        False,
        [{"type": "function", "function": {"name": "search"}}],
        0,
    )
    assert returned is fallback_result
    assert attempted is False

    xml_parts = [
        "before <fun",
        'ction_calls><invoke name="search">',
        '<parameter name="q">onyx</parameter></invoke></function_calls> after',
    ]
    xml_filter = llm_step._XmlToolCallContentFilter()
    assert (
        "".join(xml_filter.process(part) for part in xml_parts)
        + xml_filter.flush()
        == "".join(xml_parts)
    )

    # The continuation suffix is a synthetic user turn. Its partial assistant
    # must therefore declare reasoning_content so stock Pydantic serialization
    # retains it even when the optional cross-turn reasoning patch is disabled.
    partial = patches._build_midstream_partial_assistant(
        AssistantMessage,
        content="partial answer",
        reasoning="forced continuation reasoning",
    )
    assert partial.model_dump(exclude_none=True)["reasoning_content"] == (
        "forced continuation reasoning"
    )

    class ContractTimeout(LiteLLMTimeout):
        def __init__(self) -> None:
            super().__init__(
                "contract timeout",
                model="wrapper-contract-model",
                llm_provider="wrapper-contract-provider",
            )

    llm = LitellmLLM(
        api_key=None,
        model_provider="wrapper-contract-provider",
        model_name="wrapper-contract-model",
        max_input_tokens=4096,
        timeout=1,
    )
    completion_calls = 0

    def fail_before_first_chunk(**_kwargs):
        nonlocal completion_calls
        completion_calls += 1
        raise ContractTimeout()

    llm._completion = fail_before_first_chunk
    try:
        list(LitellmLLM.stream(llm, prompt=[]))
    except ContractTimeout:
        pass
    else:
        raise AssertionError("pre-chunk retries unexpectedly hid terminal failure")
    assert completion_calls == 2
    assert "prechunk_retry_count" not in LitellmLLM.stream.__code__.co_varnames

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

    # These are separate source patches of the same live function. Check the
    # final compiled body, not each installer's intermediate success message.
    final_loop_names = set(llm_loop.run_llm_loop.__code__.co_names)
    assert "_wrapper_attach_reasoning_fields" in final_loop_names
    assert "_wrapper_append_python_guidance" in final_loop_names


def _validate_textual_tool_output_persistence() -> None:
    from onyx.chat.chat_state import ChatStateContainer
    from onyx.chat.llm_step import run_llm_step_pkt_generator
    from onyx.llm.model_response import Delta
    from onyx.llm.model_response import ModelResponseStream
    from onyx.llm.model_response import StreamingChoice
    from onyx.llm.models import ToolChoiceOptions
    from onyx.server.query_and_chat.streaming_models import Placement

    xml_parts = [
        "before <fun",
        'ction_calls><invoke name="search">',
        '<parameter name="q">onyx</parameter></invoke></function_calls> after',
    ]

    class TextOnlyLLM:
        config = SimpleNamespace(
            model_provider="wrapper-contract-provider",
            model_name="wrapper-contract-model",
            api_base=None,
        )

        def stream(self, **_kwargs):
            for index, content in enumerate(xml_parts):
                yield ModelResponseStream(
                    id="textual-tool-contract",
                    created="now",
                    choice=StreamingChoice(
                        finish_reason="stop" if index == len(xml_parts) - 1 else None,
                        delta=Delta(content=content),
                    ),
                )

    state = ChatStateContainer()
    generator = run_llm_step_pkt_generator(
        history=[],
        tool_definitions=[
            {"type": "function", "function": {"name": "search"}}
        ],
        tool_choice=ToolChoiceOptions.AUTO,
        llm=TextOnlyLLM(),
        placement=Placement(turn_index=0),
        state_container=state,
        citation_processor=None,
    )
    packets = []
    while True:
        try:
            packets.append(next(generator))
        except StopIteration as stop:
            llm_step_result, _has_reasoned = stop.value
            break

    expected = "".join(xml_parts)
    visible = "".join(
        content
        for packet in packets
        if isinstance((content := getattr(packet.obj, "content", None)), str)
    )
    assert visible == expected
    assert state.get_answer_tokens() == expected
    assert llm_step_result.answer == expected
    assert llm_step_result.raw_answer == expected
    assert llm_step_result.tool_calls is None


def _validate_midstream_continuation_state_persistence() -> None:
    from litellm.exceptions import Timeout as LiteLLMTimeout
    from onyx.chat.chat_state import ChatStateContainer
    from onyx.chat.llm_step import run_llm_step_pkt_generator
    from onyx.llm import model_response
    from onyx.llm.model_response import Delta
    from onyx.llm.model_response import ModelResponseStream
    from onyx.llm.model_response import StreamingChoice
    from onyx.llm.models import ToolChoiceOptions
    from onyx.llm.multi_llm import LitellmLLM
    from onyx.server.query_and_chat.streaming_models import Placement

    class ContractTimeout(LiteLLMTimeout):
        def __init__(self) -> None:
            super().__init__(
                "contract timeout",
                model="wrapper-contract-model",
                llm_provider="wrapper-contract-provider",
            )

    def packet(content: str, finish_reason: str | None = None) -> ModelResponseStream:
        return ModelResponseStream(
            id="midstream-state-contract",
            created="now",
            choice=StreamingChoice(
                finish_reason=finish_reason,
                delta=Delta(content=content),
            ),
        )

    def drain(llm: LitellmLLM):
        state = ChatStateContainer()
        generator = run_llm_step_pkt_generator(
            history=[],
            tool_definitions=[],
            tool_choice=ToolChoiceOptions.AUTO,
            llm=llm,
            placement=Placement(turn_index=0),
            state_container=state,
            citation_processor=None,
        )
        packets = []
        while True:
            try:
                packets.append(next(generator))
            except StopIteration as stop:
                llm_step_result, _has_reasoned = stop.value
                break
        visible = "".join(
            content
            for emitted in packets
            if isinstance(
                (content := getattr(emitted.obj, "content", None)), str
            )
        )
        assert visible == state.get_answer_tokens()
        assert visible == llm_step_result.answer
        assert visible == llm_step_result.raw_answer
        return visible, llm_step_result

    def make_llm() -> LitellmLLM:
        return LitellmLLM(
            api_key=None,
            model_provider="wrapper-contract-provider",
            model_name="wrapper-contract-model",
            max_input_tokens=4096,
            timeout=1,
        )

    original_converter = model_response.from_litellm_model_response_stream
    model_response.from_litellm_model_response_stream = lambda chunk: chunk
    try:
        llm = make_llm()
        completion_calls = 0

        def fail_continuation(**_kwargs):
            nonlocal completion_calls
            completion_calls += 1
            if completion_calls == 1:
                def interrupted_stream():
                    yield packet("partial answer")
                    raise ContractTimeout()

                return interrupted_stream()
            raise ContractTimeout()

        llm._completion = fail_continuation
        visible, result = drain(llm)
        assert completion_calls == 3
        assert "partial answer" in visible
        assert "inference stream was interrupted" in visible
        assert "Recovery also failed" in visible
        assert result.finish_reason == "stop"

        progressing_llm = make_llm()
        progressing_calls = 0

        def keep_progressing(**_kwargs):
            nonlocal progressing_calls
            progressing_calls += 1
            current_call = progressing_calls

            def progressing_stream():
                yield packet(f"part {current_call}")
                if current_call < 4:
                    raise ContractTimeout()
                yield packet(" complete", "stop")

            return progressing_stream()

        progressing_llm._completion = keep_progressing
        visible, result = drain(progressing_llm)
        assert progressing_calls == 4
        assert visible.count("inference stream was interrupted") == 3
        assert "Recovery also failed" not in visible
        assert "part 4 complete" in visible
        assert result.finish_reason == "stop"

        finalizing_llm = make_llm()

        def fail_after_finish(**_kwargs):
            def finishing_stream():
                yield packet("complete answer", "stop")
                raise ValueError("finalizer failed")

            return finishing_stream()

        finalizing_llm._completion = fail_after_finish
        visible, result = drain(finalizing_llm)
        assert "complete answer" in visible
        assert "stream finalization then failed" in visible
        assert result.finish_reason == "stop"
    finally:
        model_response.from_litellm_model_response_stream = original_converter


if __name__ == "__main__":
    _install_wrapper_patches()
    _validate_python_tool_identity()
    _validate_python_tool_generated_id_identity()
    _validate_python_file_link_enforcement()
    _validate_indexed_open_url_contract()
    _validate_lite_open_url_contract()
    _validate_web_search_timeout_contract()
    _validate_web_search_concurrency_contract()
    _validate_litellm_contract()
    _validate_textual_tool_output_persistence()
    _validate_midstream_continuation_state_persistence()
    print("PINNED_API_PATCH_CONTRACTS_OK")
