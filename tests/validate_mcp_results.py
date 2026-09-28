"""Exercise the installed MCP result path with the pinned SDK and Onyx models."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


def validate() -> None:
    from mcp.types import CallToolResult, TextContent
    from onyx.server.features.mcp import client
    from onyx.server.query_and_chat.placement import Placement
    from onyx.server.query_and_chat import session_loading
    from onyx.tools.tool_implementations.mcp import mcp_tool
    from onyx_wrapper_patches.api import mcp_results

    assert client.process_mcp_result is mcp_results.process_mcp_result
    installed = mcp_tool.MCPTool.run
    assert installed._wrapper_mcp_results
    mcp_results.install()
    assert mcp_tool.MCPTool.run is installed
    assert mcp_tool.call_mcp_tool is client.call_mcp_tool

    emitter = Mock()
    credentials = Mock()
    credentials.build_headers.return_value = {}
    credentials.can_authenticate.return_value = True
    tool = mcp_tool.MCPTool(
        tool_id=1, emitter=emitter,
        mcp_server=SimpleNamespace(name="fixture", server_url="https://example.invalid/mcp",
                                  auth_type=None, transport=client.MCPTransport.STREAMABLE_HTTP),
        tool_name="fixture", tool_description="fixture", tool_definition={},
        resolved_credentials=credentials,
    )
    session = SimpleNamespace(initialize=AsyncMock(), call_tool=AsyncMock())
    def run(function, *args):
        return asyncio.run(function(session))

    cases = [
        (CallToolResult(content=[TextContent(type="text", text='{"count":3}')]), {"count": 3}),
        (CallToolResult(content=[], structuredContent={}), {}),
        (CallToolResult(content=[TextContent(type="text", text='{"count":3}')],
                        structuredContent={"count": 3}), {"count": 3}),
        (CallToolResult(content=[TextContent(type="text", text="hello\nworld")]), "hello\nworld"),
        (CallToolResult(content=[TextContent(type="text", text="null")]), None),
        (CallToolResult(content=[TextContent(type="text", text="false")]), False),
        (CallToolResult(content=[TextContent(type="text", text="[1,2]")]), [1, 2]),
        (CallToolResult(content=[TextContent(type="text", text='{"tool_result":3}')]), {"tool_result": 3}),
        (CallToolResult(content=[TextContent(type="text", text="explanation")], structuredContent={"x": 1}),
         {"content": [{"type": "text", "text": "explanation"}], "structuredContent": {"x": 1}}),
        (CallToolResult(content=[TextContent(type="text", text="not found")], isError=True),
         {"content": [{"type": "text", "text": "not found"}], "isError": True}),
    ]
    with patch.object(client, "_call_mcp_client_function_sync", side_effect=run), \
            patch.object(mcp_tool, "record_mcp_client_tool_outcome") as metrics:
        for raw, expected in cases:
            session.call_tool.return_value = raw
            emitter.reset_mock()
            result = tool.run(Placement(turn_index=1), query="fixture")
            assert result.rich_response.tool_result == expected
            assert result.llm_facing_response == (expected if isinstance(expected, str) else json.dumps(expected, ensure_ascii=False))
            packet = emitter.emit.call_args.args[0]
            assert packet.obj.data == ("null" if expected is None else expected)
            assert packet.obj.response_type == result.rich_response.response_type
            # Test the actual wire schema and persisted rich summary, not just Python values.
            assert json.loads(packet.model_dump_json())["obj"]["data"] == packet.obj.data
            assert json.loads(result.rich_response.model_dump_json())["tool_result"] == expected
            saved = json.loads(result.rich_response.model_dump_json())
            reloaded = session_loading.create_custom_tool_packets(
                tool_name=saved["tool_name"], response_type=saved["response_type"],
                turn_index=1, data=saved["tool_result"],
            )
            delta = next(p.obj for p in reloaded if p.obj.type == "custom_tool_delta")
            assert delta.data == packet.obj.data
            assert metrics.call_args.kwargs["status"] == (
                mcp_tool.MCPToolCallStatus.ERROR if raw.isError else mcp_tool.MCPToolCallStatus.SUCCESS
            )
            session.call_tool.assert_awaited_with("fixture", {"query": "fixture"})

        session.call_tool.side_effect = RuntimeError("fixture transport failure")
        result = tool.run(Placement(turn_index=1))
        assert "fixture transport failure" in json.loads(result.llm_facing_response)["error"]
        assert metrics.call_args.kwargs["status"] == mcp_tool.MCPToolCallStatus.ERROR
        session.call_tool.reset_mock()
        credentials.can_authenticate.return_value = False
        credentials.needs_reauth.return_value = True
        result = tool.run(Placement(turn_index=1))
        assert "connection values" in result.rich_response.tool_result["error"]
        session.call_tool.assert_not_called()
        assert metrics.call_args.kwargs["status"] == mcp_tool.MCPToolCallStatus.AUTH_ERROR
    print("PINNED_MCP_RESULT_PAYLOADS_OK")
