"""Native tool and bot contracts, imported after the complete wrapper bootstrap."""
from contextlib import ExitStack, nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import asyncio


def validate_native_tools(*, background: bool) -> None:
    from onyx.tools.tool_implementations.python import python_tool
    from onyx.tools.tool_implementations.bash import bash_tool
    from onyx.tools.tool_implementations.coding_agent.coding_agent_tool import CodingAgentTool
    from onyx.tools import tool_constructor
    modules = (python_tool, bash_tool)
    classes = (python_tool.PythonTool, bash_tool.BashTool, CodingAgentTool)
    with ExitStack() as stack:
        clients = []
        for module in modules:
            assert bool(module.CODE_INTERPRETER_BASE_URL) is not background
            db = stack.enter_context(patch.object(module, "fetch_code_interpreter_server"))
            client = stack.enter_context(patch.object(module, "CodeInterpreterClient"))
            clients.append((db, client))
            if background:
                db.side_effect = AssertionError("empty URL must not query controller settings")
                client.side_effect = AssertionError("empty URL must not call controller")
            else:
                db.return_value.server_enabled = True
                client.return_value.__enter__.return_value.health.return_value.healthy = True
                client.return_value.__enter__.return_value.supports.return_value = True
        for cls in classes:
            assert cls.is_available(MagicMock()) is (not background), cls.__name__
        if background:
            persona = SimpleNamespace(id=123, name="fixture", tools=[
                SimpleNamespace(id=i, name=cls.NAME, in_code_tool_id=cls.__name__)
                for i, cls in enumerate((python_tool.PythonTool, CodingAgentTool), 1)
            ], document_sets=[], attached_documents=[], hierarchy_nodes=[])
            stack.enter_context(patch.object(tool_constructor, "get_current_search_settings"))
            stack.enter_context(patch.object(tool_constructor, "get_default_document_index"))
            stack.enter_context(patch.object(tool_constructor, "get_session_with_current_tenant_if_none", return_value=nullcontext(MagicMock())))
            assert tool_constructor.construct_tools(
                persona=persona, emitter=MagicMock(),
                user=SimpleNamespace(oauth_accounts=[], enable_memory_tool=False),
                llm=MagicMock(), db_session=MagicMock(),
            ) == {}
            for db, client in clients:
                db.assert_not_called()
                client.assert_not_called()


def validate_bot_requests() -> None:
    from onyx.onyxbot.discord.api_client import OnyxAPIClient
    from onyx.onyxbot.slack.handlers import handle_regular_answer as slack
    from onyx.configs.constants import MessageType

    class Captured(BaseException):
        pass

    discord_client = OnyxAPIClient()
    captured = {}
    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        raise Captured()
    discord_client._session = SimpleNamespace(post=post)
    try:
        asyncio.run(discord_client.send_chat_message("fixture", "fixture-token", persona_id=123))
    except Captured:
        pass
    assert captured["url"].endswith("/chat/send-chat-message")
    assert captured["headers"]["Authorization"] == "Bearer fixture-token"
    assert captured["json"]["deep_research"] is False
    assert captured["json"]["chat_session_info"]["persona_id"] == 123
    assert captured["json"]["allowed_tool_ids"] is None

    captured.clear()
    def stream(**kwargs):
        captured.update(kwargs)
        raise Captured()
    user = SimpleNamespace(id="fixture-user")
    persona = SimpleNamespace(id=123, name="fixture", tools=[], document_sets=[])
    info = SimpleNamespace(
        thread_messages=[SimpleNamespace(message="fixture", sender="fixture", role=MessageType.USER)],
        msg_to_respond="1.0", is_slash_command=False, is_bot_dm=True,
        email="fixture@example.invalid", sender_id="fixture", slack_context=None,
    )
    with ExitStack() as stack:
        stack.enter_context(patch.object(slack, "get_user_by_email", return_value=user))
        get_persona = stack.enter_context(patch.object(slack, "get_persona_by_id", return_value=persona))
        stack.enter_context(patch.object(slack, "resolve_channel_references", return_value=("fixture", [])))
        stack.enter_context(patch.object(slack, "get_channel_name_from_id", return_value=("fixture", None)))
        stack.enter_context(patch.object(slack, "rate_limits", return_value=lambda f: f))
        stack.enter_context(patch.object(slack, "handle_stream_message_objects", side_effect=stream))
        try:
            slack.handle_regular_answer(
                info, SimpleNamespace(channel_config={}, persona_id=123, persona=persona),
                None, MagicMock(), "fixture", MagicMock(), MagicMock(), None, num_retries=1,
            )
        except Captured:
            pass
        assert get_persona.call_args.kwargs["user"] is user
        assert get_persona.call_args.kwargs["is_for_edit"] is False
    assert captured["new_msg_req"].deep_research is False
    assert captured["new_msg_req"].allowed_tool_ids is None
    assert captured["user"] is user and captured["bypass_acl"] is False
