"""Validate wrapper and upstream API contracts inside the pinned Onyx image."""

from __future__ import annotations

import inspect
import json
import sys
from importlib.metadata import version
from types import SimpleNamespace

import wrapper_env_patches as patches


def _validate_production_bootstrap() -> None:
    """Prove this process was patched by the same bootstrap as api_server."""
    import sitecustomize

    from onyx.prompts import tool_prompts
    from onyx.prompts.coding_agent import coding_agent as coding_agent_prompts
    from onyx.server.features.mcp import ssrf as mcp_ssrf
    from onyx.tools.tool_implementations.bash.bash_tool import BashTool
    from onyx.tools.tool_implementations.open_url import onyx_web_crawler
    from onyx.tools.tool_implementations.open_url.open_url_tool import OpenURLTool
    from onyx.tools.tool_implementations.python.python_tool import PythonTool
    from onyx.utils import playwright_fetch
    from onyx.utils import url as url_utils

    assert sitecustomize.__file__ == "/api-patches/sitecustomize.py"
    assert sys.modules["sitecustomize"] is sitecustomize
    assert getattr(playwright_fetch, "_wrapper_helper_proxy_patched", False)
    assert mcp_ssrf.mcp_ssrf_httpx_client_factory.__module__ == (
        "wrapper_env_patches"
    )
    assert onyx_web_crawler.OnyxWebCrawler.contents.__module__ == (
        "obscura_crawler_patch"
    )
    assert getattr(url_utils, "_wrapper_url_identity_preservation_patch", False)
    assert getattr(OpenURLTool, "_wrapper_failure_reporting_patch", False)
    assert getattr(OpenURLTool, "_wrapper_explicit_url_limit_patch", False)

    restricted = "Network access is available through a restricted HTTP/HTTPS proxy."
    assert restricted in PythonTool.DESCRIPTION
    assert restricted in BashTool.DESCRIPTION
    assert restricted in tool_prompts.PYTHON_TOOL_GUIDANCE
    assert "restricted proxy-only network access" in (
        coding_agent_prompts.CODING_AGENT_PROMPT
    )

    from onyx.llm.multi_llm import LitellmLLM

    configured = LitellmLLM(
        api_key="contract-key",
        model_provider="openai_compatible",
        model_name="wrapper-configured-inference-contract",
        max_input_tokens=4096,
        api_base="https://inference.example/v1",
        timeout=1,
    )
    assert configured._wrapper_configured_inference_client is not None
    assert configured._wrapper_configured_inference_http_client is not None
    configured._wrapper_configured_inference_http_client.close()


def _validate_python_tool_identity() -> None:
    from onyx.file_store.utils import build_frontend_file_url
    from onyx.prompts.tool_prompts import PYTHON_TOOL_GUIDANCE
    from onyx.tools import built_in_tools
    from onyx.tools.constants import PYTHON_TOOL_NAME
    from onyx.tools.tool_implementations.python import python_tool
    from onyx.tools.tool_implementations.python.python_tool import PythonTool

    assert PYTHON_TOOL_NAME == "run_python"
    assert PythonTool.NAME == "run_python"
    assert PythonTool.DISPLAY_NAME == "Code Interpreter"
    assert built_in_tools.TOOL_NAME_TO_CLASS["run_python"] is PythonTool
    assert "python" not in built_in_tools.TOOL_NAME_TO_CLASS
    assert not hasattr(built_in_tools, "llm_tool_name")
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

    # The production bootstrap composes failure reporting and the explicit
    # ten-URL guard around this upstream implementation. Inspect the stored
    # source callable so this contract remains valid under the final wrappers.
    upstream_run = open_url_tool.OpenURLTool._wrapper_failure_reporting_original_run
    run_source = inspect.getsource(upstream_run)
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
    run_source = inspect.getsource(
        open_url_tool.OpenURLTool._wrapper_failure_reporting_original_run
    )
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
    assert version("pydantic") == "2.12.5"
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


def _validate_incognito_gateway_contract() -> None:
    from litellm.exceptions import BadRequestError
    from onyx.chat import process_message
    from onyx.chat.incognito import incognito_llm_request_policy
    from onyx.chat.incognito_context import INCOGNITO_CONTEXT_TTL_SECONDS
    from onyx.chat.incognito_context import incognito_context_available
    from onyx.configs import app_configs
    from onyx.db.enums import IncognitoRecordMode
    from onyx.llm import factory
    from onyx.llm import multi_llm

    assert INCOGNITO_CONTEXT_TTL_SECONDS == 60 * 60
    assert incognito_context_available() is (
        app_configs.CACHE_BACKEND.value == "redis"
    )

    openai_policy = incognito_llm_request_policy(
        IncognitoRecordMode.USAGE_ONLY, "openai"
    )
    assert openai_policy.model_kwargs == {"store": False}
    proxy_policy = incognito_llm_request_policy(
        IncognitoRecordMode.USAGE_ONLY, "litellm_proxy"
    )
    assert proxy_policy.headers == {"x-litellm-enable-message-redaction": "true"}

    llm = factory.get_llm(
        provider="openai",
        model="wrapper-incognito-contract",
        deployment_name=None,
        api_key="contract-key",
        max_input_tokens=4096,
        additional_headers={"x-wrapper-policy": "ordinary"},
        model_kwargs={"store": True},
        policy_headers={"x-wrapper-policy": "incognito"},
        policy_model_kwargs={"store": False},
    )
    assert llm._model_kwargs["store"] is False
    assert llm._model_kwargs["extra_headers"]["x-wrapper-policy"] == "incognito"

    process_source = inspect.getsource(process_message.build_chat_turn)
    assert "incognito_policy_fn = partial(" in process_source
    assert "policy_fn=incognito_policy_fn" in process_source
    model_runner_source = inspect.getsource(process_message._run_models)
    assert "run_llm_loop(" in model_runner_source

    completion_source = inspect.getsource(multi_llm.LitellmLLM._completion)
    assert "except BadRequestError as e:" in completion_source
    assert "_rejection_names_strippable_kwargs" in completion_source
    assert "_REASONING_KWARG_KEYS" in completion_source
    assert "_BEST_EFFORT_KWARG_KEYS" in completion_source
    assert "**passthrough_kwargs" in completion_source
    assert multi_llm._rejection_names_strippable_kwargs(
        ValueError("unsupported reasoning_effort"), {"reasoning_effort"}
    )
    assert not multi_llm._rejection_names_strippable_kwargs(
        ValueError("context length exceeded"), {"reasoning_effort", "temperature"}
    )

    # Exercise the installed v4.6.5 completion method rather than relying on
    # source markers alone. Incognito policy fields must survive both provider
    # fallback attempts while reasoning and then temperature are removed.
    from onyx.llm.interfaces import ReasoningEffort
    from onyx.llm.litellm_singleton import litellm

    policy_llm = factory.get_llm(
        provider="openai",
        model="gpt-5",
        deployment_name=None,
        api_key="contract-key",
        max_input_tokens=4096,
        additional_headers={"x-wrapper-policy": "ordinary"},
        model_kwargs={"store": True},
        policy_headers={"x-wrapper-policy": "incognito"},
        policy_model_kwargs={"store": False},
    )
    completion_calls: list[dict] = []
    sentinel = object()

    def provider_rejects_best_effort_kwargs(**kwargs):
        completion_calls.append(kwargs)
        if len(completion_calls) == 1:
            raise BadRequestError(
                "unsupported reasoning",
                model="gpt-5",
                llm_provider="openai",
            )
        if len(completion_calls) == 2:
            raise BadRequestError(
                "unsupported temperature",
                model="gpt-5",
                llm_provider="openai",
            )
        return sentinel

    original_completion = litellm.completion
    try:
        litellm.completion = provider_rejects_best_effort_kwargs
        result = policy_llm._completion(
            prompt=[],
            tools=None,
            tool_choice=None,
            stream=False,
            parallel_tool_calls=False,
            reasoning_effort=ReasoningEffort.HIGH,
        )
    finally:
        litellm.completion = original_completion

    assert result is sentinel
    assert len(completion_calls) == 3
    assert "reasoning" in completion_calls[0]
    assert "temperature" in completion_calls[0]
    assert not multi_llm._REASONING_KWARG_KEYS.intersection(completion_calls[1])
    assert "temperature" in completion_calls[1]
    assert not multi_llm._BEST_EFFORT_KWARG_KEYS.intersection(completion_calls[2])
    for call in completion_calls:
        assert call["store"] is False
        assert call["extra_headers"]["x-wrapper-policy"] == "incognito"


def _validate_local_embedding_caller_contract() -> None:
    """Exercise Onyx's side of the local embedding-shim protocol."""
    from requests import RequestException
    from tenacity import wait_none

    from onyx.db.models import SearchSettings
    from onyx.natural_language_processing import search_nlp_models
    from onyx.natural_language_processing.search_nlp_models import EmbeddingModel
    from shared_configs.configs import (
        MODEL_SERVER_CONNECT_TIMEOUT,
        MODEL_SERVER_READ_TIMEOUT,
    )
    from shared_configs.enums import EmbedTextType
    from shared_configs.model_server_models import EmbedRequest

    assert MODEL_SERVER_CONNECT_TIMEOUT == 30
    assert MODEL_SERVER_READ_TIMEOUT == 600
    assert SearchSettings.can_use_large_chunks(
        True, "nomic-ai/nomic-embed-text-v23", None
    )
    assert not SearchSettings.can_use_large_chunks(
        False, "nomic-ai/nomic-embed-text-v23", None
    )
    assert not SearchSettings.can_use_large_chunks(
        True, "majentik/harrier-oss-v1-0.6b", None
    )

    model = EmbeddingModel(
        server_host="local-embedding-shim",
        server_port=9101,
        model_name="nomic-ai/nomic-embed-text-v1",
        normalize=True,
        query_prefix=None,
        passage_prefix=None,
        api_key=None,
        api_url=None,
        provider_type=None,
    )

    def request(text_type: EmbedTextType) -> EmbedRequest:
        return EmbedRequest(
            texts=["wrapper embedding contract"],
            model_name=model.model_name,
            max_context_length=8192,
            normalize_embeddings=True,
            text_type=text_type,
        )

    class FakeResponse:
        status_code = 200
        text = ""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"embeddings": [[0.1, 0.2, 0.3]]}

    original_post = search_nlp_models.requests.post
    original_wait_fixed = search_nlp_models.wait_fixed
    calls: list[dict] = []
    failures_remaining = 0

    def fake_post(endpoint, **kwargs):
        nonlocal failures_remaining
        calls.append({"endpoint": endpoint, **kwargs})
        if failures_remaining:
            failures_remaining -= 1
            raise RequestException("synthetic embedding failure")
        return FakeResponse()

    try:
        search_nlp_models.requests.post = fake_post
        search_nlp_models.wait_fixed = lambda _seconds: wait_none()

        query_response = model._make_model_server_request(
            request(EmbedTextType.QUERY)
        )
        assert query_response.embeddings == [[0.1, 0.2, 0.3]]
        assert len(calls) == 1

        failures_remaining = 2
        passage_response = model._make_model_server_request(
            request(EmbedTextType.PASSAGE)
        )
        assert passage_response.embeddings == [[0.1, 0.2, 0.3]]
        assert len(calls) == 4

        failures_remaining = 3
        try:
            model._make_model_server_request(request(EmbedTextType.PASSAGE))
        except Exception as exc:
            assert "synthetic embedding failure" in str(exc)
        else:
            raise AssertionError("terminal passage embedding failure was hidden")
        assert len(calls) == 7
    finally:
        search_nlp_models.requests.post = original_post
        search_nlp_models.wait_fixed = original_wait_fixed

    for call in calls:
        assert call["endpoint"] == (
            "http://local-embedding-shim:9101/encoder/bi-encoder-embed"
        )
        assert call["timeout"] == (30, 600)
        assert call["json"]["texts"] == ["wrapper embedding contract"]
        assert call["json"]["normalize_embeddings"] is True


def _validate_opensearch_startup_contract() -> None:
    from onyx.document_index.opensearch import client
    from onyx.document_index.opensearch import opensearch_document_index

    assert issubclass(client.OpenSearchIndexWriteBlockedError, Exception)
    verify_source = inspect.getsource(
        opensearch_document_index.OpenSearchDocumentIndex.verify_and_create_index_if_necessary
    )
    assert "is_cluster_block_error(e)" in verify_source
    assert "raise OpenSearchIndexWriteBlockedError(" in verify_source
    init_source = inspect.getsource(
        opensearch_document_index.OpenSearchDocumentIndex.__init__
    )
    assert "except OpenSearchIndexWriteBlockedError as e:" in init_source
    assert "_verified_index_names_for_current_process.add(index_name)" in init_source


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
            deployment_name=None,
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
    _validate_production_bootstrap()
    _validate_python_tool_identity()
    _validate_python_tool_generated_id_identity()
    _validate_python_file_link_enforcement()
    _validate_indexed_open_url_contract()
    _validate_lite_open_url_contract()
    _validate_web_search_timeout_contract()
    _validate_web_search_concurrency_contract()
    _validate_litellm_contract()
    _validate_incognito_gateway_contract()
    _validate_local_embedding_caller_contract()
    _validate_opensearch_startup_contract()
    _validate_textual_tool_output_persistence()
    _validate_midstream_continuation_state_persistence()
    print("PINNED_API_PATCH_CONTRACTS_OK")
