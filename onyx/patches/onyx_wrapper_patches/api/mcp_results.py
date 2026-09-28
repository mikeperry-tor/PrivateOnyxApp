"""Preserve MCP payloads through Onyx's text and rich response boundaries."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
import textwrap
from typing import Any

from onyx_wrapper_patches.common.source import _patch_function_source


@dataclass(frozen=True)
class MCPResult:
    data: Any
    response_type: str
    is_error: bool

    @property
    def llm_text(self) -> str:
        return self.data if self.response_type == "text" else json.dumps(
            self.data, ensure_ascii=False, allow_nan=False
        )

    @property
    def display_data(self) -> Any:
        # The pinned WebUI hides null data rather than displaying JSON null.
        return "null" if self.data is None else self.data


def _reject_constant(value: str) -> None:
    raise ValueError("Non-JSON numeric constant")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _json_text(text: str) -> tuple[Any, str]:
    try:
        value = json.loads(text, parse_constant=_reject_constant,
                           object_pairs_hook=_unique_object)
        # Reject overflowing JSON numbers too, without losing the original text.
        json.dumps(value, allow_nan=False)
    except (ValueError, OverflowError):
        return text, "text"
    return value, "json"


def process_mcp_result(call_tool_result) -> MCPResult:
    structured = call_tool_result.structuredContent
    content = [block.model_dump(mode="json", by_alias=True, exclude_none=True)
               for block in call_tool_result.content]
    is_error = bool(call_tool_result.isError)
    # Only simplify an unannotated, single text block. Preserve all metadata,
    # resource identities, binary blocks, and multi-block ordering otherwise.
    simple_text = (
        len(content) == 1 and set(content[0]) == {"type", "text"}
        and content[0]["type"] == "text"
    )
    if not is_error:
        if structured is not None:
            duplicate = False
            if simple_text:
                parsed, kind = _json_text(content[0]["text"])
                duplicate = (
                    kind == "json" and json.dumps(parsed, sort_keys=True)
                    == json.dumps(structured, sort_keys=True)
                )
            if not content or duplicate:
                return MCPResult(structured, "json", False)
        elif simple_text:
            data, kind = _json_text(content[0]["text"])
            return MCPResult(data, kind, False)

    # MCP's own content envelope is necessary when more than one representation
    # carries information. Never unwrap a server-owned field named tool_result.
    data = {"content": content}
    if structured is not None:
        data["structuredContent"] = structured
    if is_error:
        data["isError"] = True
    return MCPResult(data, "json", is_error)


_OLD_RESULT = '''            logger.info("MCP tool '%s' executed successfully", self._name)

            # Format the tool result for response
            tool_result_dict = {"tool_result": tool_result}
            llm_facing_response = json.dumps(tool_result_dict)

            # Emit CustomToolDelta packet
            self.emitter.emit(
                Packet(
                    placement=placement,
                    obj=CustomToolDelta(
                        tool_name=self._name,
                        response_type="json",
                        data=tool_result_dict,
                    ),
                )
            )

            response = ToolResponse(
                rich_response=CustomToolCallSummary(
                    tool_name=self._name,
                    response_type="json",
                    tool_result=tool_result_dict,
                ),
                llm_facing_response=llm_facing_response,
            )
            outcome = MCPToolCallStatus.SUCCESS
            return response'''

_NEW_RESULT = '''            response = ToolResponse(
                rich_response=CustomToolCallSummary(
                    tool_name=self._name,
                    response_type=tool_result.response_type,
                    tool_result=tool_result.data,
                ),
                llm_facing_response=tool_result.llm_text,
            )
            self.emitter.emit(
                Packet(
                    placement=placement,
                    obj=CustomToolDelta(
                        tool_name=self._name,
                        response_type=tool_result.response_type,
                        data=tool_result.display_data,
                    ),
                )
            )
            outcome = MCPToolCallStatus.ERROR if tool_result.is_error else MCPToolCallStatus.SUCCESS
            logger.info("MCP tool '%s' completed (isError=%s)", self._name, tool_result.is_error)
            return response'''


def _validate(function, parameters, markers) -> str:
    signature = inspect.signature(function)
    actual = tuple((p.name, p.kind.name, p.default) for p in signature.parameters.values())
    if actual != parameters or hasattr(function, "__wrapped__"):
        raise RuntimeError("MCP result patch callable contract changed")
    source = inspect.getsource(function)
    if any(source.count(marker) != 1 for marker in markers):
        raise RuntimeError("MCP result patch source contract changed")
    return source


def install() -> None:
    from onyx.server.features.mcp import client
    from onyx.server.query_and_chat import session_loading
    from onyx.tools.tool_implementations.mcp import mcp_tool

    if client.process_mcp_result is process_mcp_result:
        if not (getattr(mcp_tool.MCPTool.run, "_wrapper_mcp_results", False)
                and getattr(session_loading.create_custom_tool_packets, "_wrapper_mcp_results", False)):
            raise RuntimeError("MCP result patch partially installed")
        return
    required = inspect.Parameter.empty
    def positional(*names):
        return tuple((name, "POSITIONAL_OR_KEYWORD", required) for name in names)

    _validate(client.process_mcp_result, positional("call_tool_result"), (
        '"""Flatten MCP CallToolResult->text (prefers text content blocks)."""',
        'return "\\n\\n".join(p for p in parts if p) or str(call_tool_result.structuredContent)',
    ))
    _validate(client._call_mcp_tool, positional("tool_name", "arguments"), (
        "await session.initialize()", "result = await session.call_tool(tool_name, arguments)",
        "return process_mcp_result(result)",
    ))
    _validate(client.call_mcp_tool, positional("server_url", "tool_name", "arguments") + (
        ("connection_headers", "POSITIONAL_OR_KEYWORD", None),
        ("transport", "POSITIONAL_OR_KEYWORD", client.MCPTransport.STREAMABLE_HTTP),
        ("auth", "POSITIONAL_OR_KEYWORD", None),
    ), ("return _call_mcp_client_function_sync(", "_call_mcp_tool(tool_name, arguments),"))
    original = mcp_tool.MCPTool.run
    source = _validate(original, positional("self", "placement") + (
        ("override_kwargs", "POSITIONAL_OR_KEYWORD", None),
        ("llm_kwargs", "VAR_KEYWORD", required),
    ), (_OLD_RESULT, "tool_result = call_mcp_tool(", "except Exception as e:",
        "record_mcp_client_tool_outcome("))
    if mcp_tool.call_mcp_tool is not client.call_mcp_tool:
        raise RuntimeError("MCP result patch client binding changed")
    _validate(session_loading.create_custom_tool_packets,
              positional("tool_name", "response_type", "turn_index") + tuple(
                  (name, "POSITIONAL_OR_KEYWORD", default) for name, default in (
                      ("tab_index", 0), ("data", None), ("file_ids", None),
                      ("error", None), ("tool_args", None), ("tool_id", None),
                  )
              ), ("obj=CustomToolDelta(", "                data=data,", "return packets"))

    # Keep the upstream authentication, transport, exception mapping, and metric
    # finalizer intact. The shared helper owns source reconstruction and globals.
    _patch_function_source(
        module=mcp_tool.MCPTool, function_name="run",
        replacements={source: textwrap.dedent(source.replace(_OLD_RESULT, _NEW_RESULT))},
        patch_name="MCP result payloads",
    )
    if mcp_tool.MCPTool.run is original:
        raise RuntimeError("MCP result patch installation failed")
    # Historical packets are rebuilt from the typed saved summary. JSON null
    # needs the same display adaptation as live packets (also valid for custom
    # HTTP tools); retain absent file/error/text payload behavior.
    original_packets = session_loading.create_custom_tool_packets
    _patch_function_source(
        module=session_loading, function_name="create_custom_tool_packets",
        replacements={
            "                data=data,":
            '                data="null" if response_type == "json" and data is None and not file_ids and error is None else data,',
        },
        patch_name="saved JSON null tool display",
    )
    if session_loading.create_custom_tool_packets is original_packets:
        raise RuntimeError("MCP result replay patch installation failed")
    session_loading.create_custom_tool_packets._wrapper_mcp_results = True
    mcp_tool.MCPTool.run._wrapper_mcp_results = True
    client.process_mcp_result = process_mcp_result
