"""Candidate request contracts after the complete production patch composition."""
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def validate_reasoning_requests() -> None:
    from litellm.exceptions import BadRequestError
    from onyx.llm import factory, multi_llm
    from onyx.llm.litellm_singleton import litellm
    from onyx.llm.constants import LlmProviderNames
    from onyx.llm.models import ReasoningEffort, UserMessage
    from onyx.llm.well_known_providers.constants import (
        BIFROST_API_MODE_CONFIG_KEY, BIFROST_API_MODE_CHAT_COMPLETIONS,
        BIFROST_API_MODE_RESPONSES,
    )
    tools = [{"type": "function", "function": {
        "name": "fixture", "parameters": {"type": "object", "properties": {}},
    }}]
    prompt = [UserMessage(content="fixture")]
    for mode, model, offered, requires_none in (
        (BIFROST_API_MODE_CHAT_COMPLETIONS, "openai/gpt-5.6-sol", tools, True),
        (BIFROST_API_MODE_CHAT_COMPLETIONS, "openai/gpt-5.6-sol", None, False),
        (BIFROST_API_MODE_RESPONSES, "openai/gpt-5.6-sol", tools, False),
        (BIFROST_API_MODE_CHAT_COMPLETIONS, "openai/gpt-5.2", tools, False),
        (BIFROST_API_MODE_CHAT_COMPLETIONS, "glm-5", tools, False),
    ):
        llm = multi_llm.LitellmLLM(
            api_key="fixture", model_provider=LlmProviderNames.BIFROST,
            model_name=model, max_input_tokens=8192,
            api_base="https://fixture.invalid/v1",
            custom_config={BIFROST_API_MODE_CONFIG_KEY: mode},
        )
        with patch.object(litellm, "completion") as completion:
            llm._completion(prompt=prompt, tools=offered, tool_choice="auto",
                            stream=False, parallel_tool_calls=False,
                            reasoning_effort=ReasoningEffort.HIGH)
        sent = completion.call_args.kwargs
        assert sent["tools"] == offered
        assert (sent.get("reasoning_effort") == "none") is requires_none
        if requires_none:
            assert "reasoning" not in sent
        else:
            assert set(sent) & multi_llm._REASONING_KWARG_KEYS

    # Unknown aliases learn the provider-required value once. Neither retry
    # may strip tools, preserved history, or incognito retention controls.
    for model in ("gpt-5.6-sol-01-ptu", "opaque-alias"):
        llm = factory.get_llm(
            provider="openai", model=model, deployment_name=None,
            api_key="fixture", max_input_tokens=8192,
            policy_headers={"x-wrapper-policy": "incognito"},
            policy_model_kwargs={"store": False},
        )
        calls = []
        def completion(**kwargs):
            calls.append(kwargs)
            if kwargs.get("reasoning_effort") != "none":
                raise BadRequestError("set reasoning_effort to 'none'", model=model, llm_provider="openai")
            if "temperature" in kwargs:
                raise BadRequestError("unsupported temperature", model=model, llm_provider="openai")
            return "ok"
        with patch.object(litellm, "completion", side_effect=completion):
            assert llm._completion(prompt=prompt, tools=tools, tool_choice="auto",
                                   stream=False, parallel_tool_calls=False,
                                   reasoning_effort=ReasoningEffort.HIGH) == "ok"
        assert len(calls) == (2 if model.startswith("gpt-") else 3)
        for sent in calls:
            assert sent["tools"] == tools and sent["tool_choice"] == "auto"
            assert sent["messages"] == calls[0]["messages"]
            assert sent["store"] is False
            assert sent["extra_headers"]["x-wrapper-policy"] == "incognito"
        assert calls[-1]["reasoning_effort"] == "none"


def validate_file_tool_availability() -> None:
    from onyx.chat import llm_loop
    from onyx.chat.models import FileToolMetadata
    from validate_prompt_stability import context_files, main_chat, assert_prefix
    metadata = FileToolMetadata(file_id="11111111-1111-4111-8111-111111111111",
                                filename="fixture.pdf", approx_char_count=100000)
    for names, expected in (({"read_file"}, "Use the read_file"),
                            ({"internal_search"}, "use internal search"),
                            (set(), "no tool here can read"),
                            (None, "no tool here can read")):
        with patch.object(llm_loop, "_create_file_tool_metadata_message",
                          wraps=llm_loop._create_file_tool_metadata_message) as build:
            from validate_prompt_stability import user_history
            result = llm_loop.construct_message_history(
                system_prompt=None, custom_agent_prompt=None,
                simple_chat_history=user_history(), reminder_message=None,
                context_files=context_files(file_metadata_for_tool=[metadata]),
                available_tokens=8192, token_counter=len, available_tool_names=names,
            )
        assert build.call_args.args[2] == names
        text = "\n".join(m.message for m in result)
        assert expected in text
        assert (metadata.file_id in text) is (names == {"read_file"})
    # Actual installed main loop must forward its selected tools and retain a
    # growing semantic prefix with the metadata notice present on every cycle.
    requests, _, _ = main_chat(context_files=context_files(file_metadata_for_tool=[metadata]))
    assert_prefix(requests[0], requests[1])
    assert_prefix(requests[1], requests[2])
    assert "no tool here can read" in str(requests[0]["prompt"])
    from onyx.tools.tool_implementations.file_reader.file_reader_tool import FileReaderTool
    reader = FileReaderTool(tool_id=104, emitter=MagicMock(), user_file_ids=[], chat_file_ids=[])
    for final_cycle in (False, True):
        # A final-cycle tool-free request must not inherit the preceding
        # request's read_file promise, even when the Agent owns that tool.
        with patch.object(llm_loop, "MAX_LLM_CYCLES", 1 if final_cycle else 2):
            requests, _, _ = main_chat(
                steps=["Fixture answer"], tools=[reader],
                context_files=context_files(file_metadata_for_tool=[metadata]),
            )
        text = str(requests[0]["prompt"])
        assert ("Use the read_file" in text) is (not final_cycle)
        assert (metadata.file_id in text) is (not final_cycle)


def validate_disabled_tools() -> None:
    from onyx.tools import tool_constructor as module
    from onyx.tools.tool_implementations.python.python_tool import PythonTool
    tool = SimpleNamespace(id=1, name=PythonTool.NAME, in_code_tool_id="PythonTool", enabled=False)
    persona = SimpleNamespace(id=1, name="fixture", tools=[tool], document_sets=[],
                              attached_documents=[], hierarchy_nodes=[])
    with ExitStack() as stack:
        stack.enter_context(patch.object(module, "get_current_search_settings"))
        stack.enter_context(patch.object(module, "get_default_document_index"))
        available = stack.enter_context(patch.object(PythonTool, "is_available", return_value=True))
        for whitelist in (None, [], [1]):
            result = module._construct_tools_impl(
                persona=persona, db_session=MagicMock(), emitter=MagicMock(),
                user=SimpleNamespace(oauth_accounts=[], enable_memory_tool=False),
                llm=MagicMock(), allowed_tool_ids=whitelist,
            )
            assert result == {}
        available.assert_not_called()
        tool.enabled = True
        assert 1 in module._construct_tools_impl(
            persona=persona, db_session=MagicMock(), emitter=MagicMock(),
            user=SimpleNamespace(oauth_accounts=[], enable_memory_tool=False), llm=MagicMock(),
        )


def validate_context_override() -> None:
    from onyx.configs.model_configs import GEN_AI_MAX_TOKENS
    from onyx.db import llm as storage
    from onyx.llm import factory, utils
    # Native echo suppression is write-time policy, not a replacement for the
    # wrapper's authoritative read-time limit. Explicit stored values survive.
    with patch.object(storage, "get_max_input_tokens", return_value=32768):
        assert storage._stored_max_input_tokens("openai", "fixture", 32768, None) is None
        assert storage._stored_max_input_tokens("openai", "fixture", 32768, 8192) == 32768
        assert storage._stored_max_input_tokens("openai", "fixture", 16384, None) == 16384
        assert storage._stored_max_input_tokens("openai", "fixture", None, 8192) is None
        assert storage._stored_max_input_tokens("ollama_chat", "fixture", 32768, None) == 32768
    assert GEN_AI_MAX_TOKENS
    # No database access is necessary when the wrapper override is selected.
    assert factory._get_model_configured_max_input_tokens(None, None) == GEN_AI_MAX_TOKENS
    assert utils.get_max_input_tokens_from_llm_provider(None, None) == GEN_AI_MAX_TOKENS


def validate() -> None:
    validate_reasoning_requests()
    validate_file_tool_availability()
    validate_disabled_tools()
    validate_context_override()
    print("PINNED_REASONING_TOOL_AVAILABILITY_OK")
