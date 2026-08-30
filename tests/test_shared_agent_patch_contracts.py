from __future__ import annotations

import importlib.util
import functools
import json
import linecache
import os
import sys
import unittest
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import patch


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "onyx"
    / "patches"
    / "shared"
    / "wrapper_env_patches.py"
)


class _SourcePatchAnnotation:
    pass


def _source_patch_annotated_fixture(
    value: _SourcePatchAnnotation,
) -> _SourcePatchAnnotation:
    return value


def _load_wrapper(env: dict[str, str] | None = None) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "wrapper_env_patches_agent_contracts_under_test", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, env or {}, clear=True):
        spec.loader.exec_module(module)
    return module


def _package(name: str) -> ModuleType:
    module = ModuleType(name)
    module.__path__ = []
    return module


def _exec_module_source(module: ModuleType, filename: str, source: str) -> None:
    lines = source.splitlines(keepends=True)
    linecache.cache[filename] = (len(source), None, lines, filename)
    exec(compile(source, filename, "exec"), module.__dict__)


def _reasoning_detector(model_name, model_provider):
    model_map = get_model_map()  # noqa: F821
    if model_map and model_name and model_provider:
        return True
    return _litellm_supports_reasoning(model_name)  # noqa: F821


def _drifted_reasoning_detector(model):
    return bool(model)


def _internal_search_formatter(
    top_sections,
    citation_start=1,
    limit=None,
    include_source_type=True,
    include_link=False,
    include_document_id=False,
    note=None,
):
    del citation_start, limit, include_source_type, include_link, include_document_id
    results = []
    for index, content in enumerate(top_sections, start=1):
        result = {"document": index}
        result["content"] = content
        results.append(result)
    payload = {}
    payload["results"] = results
    if note:
        payload["note"] = note
    return json.dumps(payload, indent=2), {1: "doc"}


def _configured_max(model_configuration):
    return model_configuration.max_input_tokens


def _provider_max(model_configuration, model_name=None):
    del model_name
    return model_configuration.max_input_tokens


def _saved_tool_response(tool_name, generated_images, tool_call_response):
    del tool_name, generated_images, tool_call_response
    return TOOL_CALL_RESPONSE_CROSS_MESSAGE  # noqa: F821


def _source_patch_composition_fixture():
    values = ["base"]
    return values


def _convert_history(
    chat_history,
    files,
    context_image_files,
    additional_context,
    token_counter,
    tool_id_to_name_map,
):
    del chat_history, files, context_image_files, additional_context
    del token_counter, tool_id_to_name_map
    return SimpleNamespace(
        simple_messages=[
            SimpleNamespace(
                message_type="tool",
                message="persisted output",
                token_count=0,
            )
        ]
    )


def _coding_owner():
    return run_llm_step_pkt_generator(  # noqa: F821
        tool_choice=ToolChoiceOptions.REQUIRED,  # noqa: F821
    )


def _deep_research_owner():
    return run_llm_step(  # noqa: F821
        tool_choice=ToolChoiceOptions.REQUIRED,  # noqa: F821
    )


def _research_owner():
    return run_llm_step(  # noqa: F821
        tool_choice=ToolChoiceOptions.REQUIRED,  # noqa: F821
    )


def _chat_owner():
    tool_choice = ToolChoiceOptions.REQUIRED  # noqa: F821
    return run_llm_step(tool_choice=tool_choice)  # noqa: F821


def _step(*args, **kwargs):
    del args
    return kwargs.get("tool_choice")


class _Placement:
    def __init__(self, sub_turn_index: int | None) -> None:
        self.sub_turn_index = sub_turn_index

    def model_copy(self, *, update):
        return _Placement(update["sub_turn_index"])


class _ToolCall:
    def __init__(self, name: str, sub_turn_index: int | None) -> None:
        self.tool_name = name
        self.placement = _Placement(sub_turn_index)

    def model_copy(self, *, update):
        copied = _ToolCall(self.tool_name, self.placement.sub_turn_index)
        copied.placement = update["placement"]
        return copied


def _code_description_modules(
    python_description: str = "Execute Python code in an isolated sandbox environment.",
):
    onyx = _package("onyx")
    file_store = _package("onyx.file_store")
    file_utils = ModuleType("onyx.file_store.utils")

    def build_frontend_file_url(file_id: str) -> str:
        return f"/api/chat/file/{file_id}"

    def build_full_frontend_file_url(file_id: str) -> str:
        return f"http://localhost:3000/api/chat/file/{file_id}"

    file_utils.build_frontend_file_url = build_frontend_file_url
    file_utils.build_full_frontend_file_url = build_full_frontend_file_url
    tools = _package("onyx.tools")
    implementations = _package("onyx.tools.tool_implementations")
    python_package = _package("onyx.tools.tool_implementations.python")
    python_tool = ModuleType("onyx.tools.tool_implementations.python.python_tool")

    class PythonTool:
        DESCRIPTION = python_description

        def run(self, placement, override_kwargs, **llm_kwargs):
            del placement, override_kwargs, llm_kwargs
            return self.response

    python_tool.build_full_frontend_file_url = build_full_frontend_file_url
    python_tool.PythonTool = PythonTool
    bash_package = _package("onyx.tools.tool_implementations.bash")
    bash_tool = ModuleType("onyx.tools.tool_implementations.bash.bash_tool")

    class BashTool:
        DESCRIPTION = (
            "Execute a bash command inside an isolated, network-restricted session."
        )

    bash_tool.BashTool = BashTool
    prompts = _package("onyx.prompts")
    tool_prompts = ModuleType("onyx.prompts.tool_prompts")
    tool_prompts.PYTHON_TOOL_GUIDANCE = (
        "## run_python\nFiles written to the current directory will be returned with a `file_link`. "
        "Use this to give the user a way to download the file OR to display "
        "generated images. Internet access for this session is disabled. Do not "
        "make external web requests or API calls as they will fail. Use `openpyxl` "
        "to read and write Excel files. You have access to libraries like numpy, "
        "pandas, scipy, matplotlib, and PIL."
    )
    tool_prompts.TOOL_SECTION_HEADER = "\n# Tools\n\n"
    prompts.tool_prompts = tool_prompts
    chat_prompts = ModuleType("onyx.prompts.chat_prompts")
    chat_prompts.FILE_REMINDER = (
        "Your code execution generated file(s) with download links.\n"
        "If you reference or share these files, use the exact markdown format "
        "[filename](file_link) with the file_link from the execution result."
    )
    prompts.chat_prompts = chat_prompts
    coding_agent_package = _package("onyx.coding_agent")
    mock_tools = ModuleType("onyx.coding_agent.mock_tools")
    mock_tools.BASH_TOOL_DESCRIPTION = {
        "function": {
            "description": (
                "Run a bash command. The session has no network access. State persists."
            )
        }
    }
    coding_agent_package.mock_tools = mock_tools
    prompt_coding_package = _package("onyx.prompts.coding_agent")
    ca_prompts = ModuleType("onyx.prompts.coding_agent.coding_agent")
    avoid = (
        "Avoid:\n"
        "- Network commands (`curl`, `pip install`, `npm install`, `git pull`) "
        "— the sandbox has no network.\n"
    )
    ca_prompts.CODING_AGENT_PROMPT = "Use a network-isolated sandbox.\n" + avoid
    ca_prompts.CODING_AGENT_PROMPT_REASONING = (
        "Use a network-isolated sandbox. No network."
    )
    prompt_coding_package.coding_agent = ca_prompts
    return (
        {
            "onyx": onyx,
            "onyx.file_store": file_store,
            "onyx.file_store.utils": file_utils,
            "onyx.tools": tools,
            "onyx.tools.tool_implementations": implementations,
            "onyx.tools.tool_implementations.python": python_package,
            "onyx.tools.tool_implementations.python.python_tool": python_tool,
            "onyx.tools.tool_implementations.bash": bash_package,
            "onyx.tools.tool_implementations.bash.bash_tool": bash_tool,
            "onyx.prompts": prompts,
            "onyx.prompts.tool_prompts": tool_prompts,
            "onyx.prompts.chat_prompts": chat_prompts,
            "onyx.coding_agent": coding_agent_package,
            "onyx.coding_agent.mock_tools": mock_tools,
            "onyx.prompts.coding_agent": prompt_coding_package,
            "onyx.prompts.coding_agent.coding_agent": ca_prompts,
        },
        PythonTool,
        BashTool,
        tool_prompts,
        chat_prompts,
        mock_tools,
        ca_prompts,
    )


class SharedAgentPatchContractTests(unittest.TestCase):
    def test_source_patch_uses_unwrapped_function_globals(self) -> None:
        wrapper = _load_wrapper()
        decorator_module = ModuleType("source_patch_decorator_fixture")
        decorator_module.functools = functools
        exec(
            "def decorate(function):\n"
            "    @functools.wraps(function)\n"
            "    def wrapped(*args, **kwargs):\n"
            "        return function(*args, **kwargs)\n"
            "    return wrapped\n",
            decorator_module.__dict__,
        )
        module = SimpleNamespace(
            _source_patch_annotated_fixture=decorator_module.decorate(
                _source_patch_annotated_fixture
            )
        )

        wrapper._patch_function_source(
            module=module,
            function_name="_source_patch_annotated_fixture",
            patch_name="annotated decorator fixture",
            replacements={"    return value\n": "    return value\n"},
        )

        value = _SourcePatchAnnotation()
        self.assertIs(module._source_patch_annotated_fixture(value), value)

    def test_source_patches_compose_on_the_same_function(self) -> None:
        wrapper = _load_wrapper()
        module = SimpleNamespace(
            _source_patch_composition_fixture=_source_patch_composition_fixture
        )

        wrapper._patch_function_source(
            module=module,
            function_name="_source_patch_composition_fixture",
            patch_name="first composition fixture",
            replacements={
                '    values = ["base"]\n': '    values = ["base", "first"]\n'
            },
        )
        wrapper._patch_function_source(
            module=module,
            function_name="_source_patch_composition_fixture",
            patch_name="second composition fixture",
            replacements={
                "    return values\n": '    values.append("second")\n    return values\n'
            },
        )

        self.assertEqual(
            module._source_patch_composition_fixture(),
            ["base", "first", "second"],
        )

    def test_python_package_capabilities_are_unconditional(self) -> None:
        wrapper = _load_wrapper()
        modules, PythonTool, _, tool_prompts, _, _, _ = _code_description_modules()

        with patch.dict(
            os.environ, {"WRAPPER_PATCH_STRICT": "true"}, clear=True
        ), patch.dict(sys.modules, modules):
            wrapper.apply_python_package_capability_patches()

        for description in (PythonTool.DESCRIPTION, tool_prompts.PYTHON_TOOL_GUIDANCE):
            self.assertIn("Pre-installed packages include", description)
            for package in ("sympy", "reportlab", "svglib"):
                self.assertIn(package, description)
            self.assertIn("Pillow", description)

    def test_python_package_description_drift_fails_strict(self) -> None:
        wrapper = _load_wrapper()
        modules, *_ = _code_description_modules("Upstream changed this description")

        with patch.dict(
            os.environ, {"WRAPPER_PATCH_STRICT": "true"}, clear=True
        ), patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(RuntimeError, "did not match"):
                wrapper.apply_python_package_capability_patches()

    def test_python_package_guidance_drift_fails_strict(self) -> None:
        wrapper = _load_wrapper()
        modules, _, _, tool_prompts, _, _, _ = _code_description_modules()
        tool_prompts.PYTHON_TOOL_GUIDANCE = "Upstream changed this guidance."

        with patch.dict(
            os.environ, {"WRAPPER_PATCH_STRICT": "true"}, clear=True
        ), patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(RuntimeError, "did not match"):
                wrapper.apply_python_package_capability_patches()

    def test_python_file_link_prompts_are_patched_without_network(self) -> None:
        wrapper = _load_wrapper()
        modules, PythonTool, _, tool_prompts, chat_prompts, _, _ = (
            _code_description_modules()
        )

        with patch.dict(
            os.environ, {"WRAPPER_PATCH_STRICT": "true"}, clear=True
        ), patch.dict(sys.modules, modules):
            wrapper.apply_python_file_link_prompt_patches()

        for prompt in (
            PythonTool.DESCRIPTION,
            tool_prompts.PYTHON_TOOL_GUIDANCE,
            chat_prompts.FILE_REMINDER,
        ):
            self.assertIn("response_markdown", prompt)
            self.assertIn("every user-requested generated file", prompt)
            self.assertIn("Never construct or hard-code a file URL", prompt)
            self.assertIn("opaque per-execution file ID", prompt)
            self.assertIn("do not retype, rename, shorten, describe", prompt)
        self.assertIn(
            "do not substitute Markdown image syntax",
            tool_prompts.PYTHON_TOOL_GUIDANCE,
        )
        self.assertIn("Do not omit a graph", chat_prompts.FILE_REMINDER)

    def test_python_result_supplies_relative_ready_to_copy_markdown(self) -> None:
        wrapper = _load_wrapper()
        modules, PythonTool, *_ = _code_description_modules()
        response = SimpleNamespace(
            llm_facing_response=json.dumps(
                {
                    "generated_files": [
                        {
                            "filename": "requested [graph].png",
                            "file_link": "/api/chat/file/file-id",
                        }
                    ]
                }
            ),
            rich_response=SimpleNamespace(
                generated_files=[
                    SimpleNamespace(
                        filename="requested [graph].png",
                        file_link="/api/chat/file/file-id",
                    )
                ]
            ),
        )

        with patch.dict(
            os.environ, {"WRAPPER_PATCH_STRICT": "true"}, clear=True
        ), patch.dict(sys.modules, modules):
            wrapper.apply_python_file_link_prompt_patches()
            tool = PythonTool()
            tool.response = response
            result = tool.run(None, None)

        payload = json.loads(result.llm_facing_response)
        generated_file = payload["generated_files"][0]
        self.assertEqual(
            generated_file["response_markdown"],
            r"[requested \[graph\].png](/api/chat/file/file-id)",
        )
        self.assertEqual(
            result.rich_response.generated_files[0].file_link,
            "/api/chat/file/file-id",
        )
        python_tool = modules[
            "onyx.tools.tool_implementations.python.python_tool"
        ]
        self.assertEqual(
            python_tool.build_full_frontend_file_url("another-id"),
            "/api/chat/file/another-id",
        )

    def test_chat_file_markdown_normalization_is_origin_independent(self) -> None:
        wrapper = _load_wrapper()
        normalize = wrapper._normalize_chat_file_markdown

        self.assertEqual(
            normalize(
                r"![math \[plot\].png](https://onyx.tail.example/api/chat/file/file-id)"
            ),
            r"[math \[plot\].png](/api/chat/file/file-id)",
        )
        self.assertEqual(
            normalize(
                "[graph.png](http://localhost:3000/api/chat/file/file-id?download=1#view)"
            ),
            "[graph.png](/api/chat/file/file-id?download=1#view)",
        )
        self.assertEqual(
            normalize("![graph.png](//onion.example/api/chat/file/file-id)"),
            "[graph.png](/api/chat/file/file-id)",
        )
        self.assertEqual(
            normalize("![graph.png](</api/chat/file/file-id>)"),
            "[graph.png](/api/chat/file/file-id)",
        )
        self.assertEqual(
            normalize(
                "![Simple Function Graphs](/api/chat/file/file-id)",
                {"file-id": "math_[functions].png"},
            ),
            r"[math_\[functions\].png](/api/chat/file/file-id)",
        )
        canonical_id = "0ad58c02-1c2d-4e22-9c41-d9e13a5e1d7b"
        corrupted = (
            "![Simple Function Graphs](/api/chat/file/"
            "0ad5_8c02-1c2d-4e22-9c41-d9e13a5e1d7b)"
        )
        recovered = (
            "[simple_function_graphs.png](/api/chat/file/" f"{canonical_id})"
        )
        self.assertEqual(
            normalize(
                corrupted,
                {canonical_id: "simple_function_graphs.png"},
            ),
            recovered,
        )
        for split in range(len(corrupted) + 1):
            stream = wrapper._ChatFileMarkdownStream(
                {canonical_id: "simple_function_graphs.png"}
            )
            actual = stream.feed(corrupted[:split])
            actual += stream.feed(corrupted[split:]) + stream.flush()
            self.assertEqual(actual, recovered, f"corrupted ID split={split}")
        self.assertEqual(
            normalize(
                "[unknown](/api/chat/file/0ad5_8c02-1c2d-4e22-9c41-d9e13a5e1d7b)",
                {"11111111-1111-4111-8111-111111111111": "other.png"},
            ),
            "[unknown](/api/chat/file/0ad5_8c02-1c2d-4e22-9c41-d9e13a5e1d7b)",
        )

    def test_fabricated_python_file_ids_remain_visible_for_diagnostics(self) -> None:
        wrapper = _load_wrapper()
        filenames = {
            "11111111-1111-4111-8111-111111111111": "graph_x_squared.png",
            "22222222-2222-4222-8222-222222222222": "graph_sin_x.png",
            "33333333-3333-4333-8333-333333333333": "graph_combined.png",
        }
        raw = (
            "## y = x²\n"
            "[y = x²](/api/chat/file/8ea7c9d2-b6e5-4d08-acdc-1dcfbfbd8e6d)\n"
            "## y = sin(x)\n"
            "![y = sin(x)](/api/chat/file/4d21d9e6-c4e8-4dce-9a89-6e2919bf9e3a)\n"
            "## Combined\n"
            "[Combined](/api/chat/file/c0e5e2ed-0e5f-4d18-9e95-d9830d1e7f5e)"
        )
        expected = (
            "## y = x²\n"
            "[y = x²](/api/chat/file/8ea7c9d2-b6e5-4d08-acdc-1dcfbfbd8e6d)\n"
            "## y = sin(x)\n"
            "[y = sin(x)](/api/chat/file/4d21d9e6-c4e8-4dce-9a89-6e2919bf9e3a)\n"
            "## Combined\n"
            "[Combined](/api/chat/file/c0e5e2ed-0e5f-4d18-9e95-d9830d1e7f5e)"
        )

        self.assertEqual(wrapper._normalize_chat_file_markdown(raw, filenames), expected)
        for split in range(len(raw) + 1):
            stream = wrapper._ChatFileMarkdownStream(filenames)
            actual = stream.feed(raw[:split]) + stream.feed(raw[split:])
            actual += stream.flush()
            self.assertEqual(actual, expected, f"fabricated IDs split={split}")
            self.assertEqual(stream.flush(), "", f"second flush split={split}")

    def test_generated_chat_file_filenames_support_live_and_saved_tool_calls(self) -> None:
        wrapper = _load_wrapper()
        expected = {"live-id": "live.png", "saved-id": "saved chart.png"}
        tool_calls = [
            SimpleNamespace(
                generated_files=[
                    SimpleNamespace(
                        filename="live.png",
                        file_link="https://tail/api/chat/file/live-id",
                    )
                ],
                tool_call_response=None,
            ),
            SimpleNamespace(
                generated_files=None,
                tool_call_response=json.dumps(
                    {
                        "generated_files": [
                            {
                                "filename": "saved chart.png",
                                "file_link": "/api/chat/file/saved-id",
                            }
                        ]
                    }
                ),
            ),
        ]
        self.assertEqual(wrapper._generated_chat_file_filenames(tool_calls), expected)

    def test_chat_file_markdown_normalization_is_narrow(self) -> None:
        wrapper = _load_wrapper()
        normalize = wrapper._normalize_chat_file_markdown
        unchanged = (
            "![remote](https://images.example/graph.png) "
            "![near](/api/chat/file/id/extra) "
            "![title](/api/chat/file/id \"caption\") "
            "![scheme](javascript:/api/chat/file/id) "
            r"\![escaped](/api/chat/file/id)"
        )
        self.assertEqual(normalize(unchanged), unchanged)
        self.assertEqual(
            normalize(
                "![one](/api/chat/file/one) and "
                "[two](https://tail/api/chat/file/two)"
            ),
            "[one](/api/chat/file/one) and [two](/api/chat/file/two)",
        )
        literal_examples = (
            "`![inline](/api/chat/file/inline)`\n"
            "```markdown\n![fenced](https://tail/api/chat/file/fenced)\n```"
        )
        self.assertEqual(normalize(literal_examples), literal_examples)

        for split in range(len(literal_examples) + 1):
            stream = wrapper._ChatFileMarkdownStream()
            actual = stream.feed(literal_examples[:split])
            actual += stream.feed(literal_examples[split:]) + stream.flush()
            self.assertEqual(actual, literal_examples, f"code split={split}")

    def test_chat_file_markdown_stream_handles_every_chunk_boundary(self) -> None:
        wrapper = _load_wrapper()
        raw = (
            "before ![math_functions_graph.png](https://tail.example/api/chat/file/"
            "6a8c8c24-7d45-4f55-bcf5-85f9b1a62b4e) after"
        )
        expected = (
            "before [math_functions_graph.png](/api/chat/file/"
            "6a8c8c24-7d45-4f55-bcf5-85f9b1a62b4e) after"
        )
        for split in range(len(raw) + 1):
            stream = wrapper._ChatFileMarkdownStream()
            actual = stream.feed(raw[:split]) + stream.feed(raw[split:])
            actual += stream.flush()
            self.assertEqual(actual, expected, f"split={split}")

        stream = wrapper._ChatFileMarkdownStream()
        actual = "".join(stream.feed(character) for character in raw)
        actual += stream.flush()
        self.assertEqual(actual, expected)

    def test_chat_file_markdown_stream_flushes_incomplete_text_losslessly(self) -> None:
        wrapper = _load_wrapper()
        raw = "ordinary [unfinished and ![also unfinished"
        stream = wrapper._ChatFileMarkdownStream()
        self.assertEqual(stream.feed(raw), "ordinary ")
        self.assertEqual(stream.flush(), "[unfinished and ![also unfinished")

    def test_python_file_link_enforcement_covers_prompt_stream_state_and_history(
        self,
    ) -> None:
        wrapper = _load_wrapper()
        modules, *_ = _code_description_modules()
        onyx = modules["onyx"]
        chat = _package("onyx.chat")
        llm_loop = ModuleType("onyx.chat.llm_loop")
        server = _package("onyx.server")
        query_and_chat = _package("onyx.server.query_and_chat")
        streaming_models = ModuleType(
            "onyx.server.query_and_chat.streaming_models"
        )
        session_loading = ModuleType("onyx.server.query_and_chat.session_loading")

        _exec_module_source(
            llm_loop,
            "<file_link_fake_llm_loop>",
            """\
class ChatMessageSimple:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

class MessageType:
    SYSTEM = "system"

def run_llm_loop(tools, persona):
    if True:
        if True:
            if persona and persona.replace_base_system_prompt:
                processed_system_prompt = "replacement prompt"
                system_prompt = (
                    ChatMessageSimple(
                        message=processed_system_prompt,
                        token_count=len(processed_system_prompt),
                        message_type=MessageType.SYSTEM,
                    )
                    if processed_system_prompt
                    else None
                )
                return system_prompt
            return None
        return None
    return None

def run_llm_step(emitter, state_container):
    raw = "before ![Simple Function Graphs](https://tail.example/api/chat/file/file-id) after"
    state_container.set_answer_tokens(raw)
    emitter.emit(Packet(placement=Placement(2), obj=AgentResponseDelta(content=raw[:19])))
    emitter.emit(Packet(placement=Placement(2), obj=AgentResponseDelta(content=raw[19:])))
    return Result(raw), False
""",
        )
        _exec_module_source(
            session_loading,
            "<file_link_fake_session_loading>",
            """\
def create_message_packets(message_text, final_documents, turn_index):
    return [message_text, final_documents, turn_index]

def translate_assistant_message_to_packets(chat_message, db_session):
    packets = []
    if chat_message.message:
        packets.extend(
            create_message_packets(
                message_text=chat_message.message,
                final_documents=None,
                turn_index=0,
            )
        )
    return packets
""",
        )

        class Placement:
            def __init__(self, turn_index):
                self.turn_index = turn_index

        class AgentResponseDelta:
            def __init__(self, content):
                self.content = content

        class Packet:
            def __init__(self, placement, obj):
                self.placement = placement
                self.obj = obj

        class Result:
            def __init__(self, answer):
                self.answer = answer

            def model_copy(self, *, update):
                return Result(update.get("answer", self.answer))

        class State:
            def __init__(self):
                self.answer = None

            def get_tool_calls(self):
                return [
                    SimpleNamespace(
                        generated_files=[
                            SimpleNamespace(
                                filename="graph.png",
                                file_link="/api/chat/file/file-id",
                            )
                        ],
                        tool_call_response=None,
                    )
                ]

            def set_answer_tokens(self, answer):
                self.answer = answer

        class Collector:
            def __init__(self):
                self.packets = []

            def emit(self, packet):
                self.packets.append(packet)

        llm_loop.Packet = Packet
        llm_loop.Placement = Placement
        llm_loop.AgentResponseDelta = AgentResponseDelta
        llm_loop.Result = Result
        streaming_models.AgentResponseDelta = AgentResponseDelta
        streaming_models.Packet = Packet
        chat.llm_loop = llm_loop
        server.query_and_chat = query_and_chat
        query_and_chat.session_loading = session_loading
        query_and_chat.streaming_models = streaming_models
        modules.update(
            {
                "onyx.chat": chat,
                "onyx.chat.llm_loop": llm_loop,
                "onyx.server": server,
                "onyx.server.query_and_chat": query_and_chat,
                "onyx.server.query_and_chat.streaming_models": streaming_models,
                "onyx.server.query_and_chat.session_loading": session_loading,
            }
        )
        onyx.chat = chat
        onyx.server = server

        with patch.dict(
            os.environ, {"WRAPPER_PATCH_STRICT": "true"}, clear=True
        ), patch.dict(sys.modules, modules):
            wrapper.apply_python_file_link_prompt_patches()
            wrapper.apply_python_file_link_enforcement_patches()

            prompt = llm_loop.run_llm_loop(
                [SimpleNamespace(name="run_python")],
                SimpleNamespace(replace_base_system_prompt=True),
            )
            self.assertIn("## run_python", prompt.message)
            self.assertIn("response_markdown", prompt.message)
            self.assertEqual(
                llm_loop._wrapper_append_python_guidance(
                    "replacement prompt", [SimpleNamespace(name="open_url")]
                ),
                "replacement prompt",
            )
            self.assertIn(
                "## run_python",
                llm_loop._wrapper_append_python_guidance(
                    None, [SimpleNamespace(name="run_python")]
                ),
            )

            state = State()
            collector = Collector()
            result, has_reasoned = llm_loop.run_llm_step(collector, state)
            expected = "before [graph.png](/api/chat/file/file-id) after"
            self.assertFalse(has_reasoned)
            self.assertEqual(result.answer, expected)
            self.assertEqual(state.answer, expected)
            self.assertEqual(
                "".join(packet.obj.content for packet in collector.packets),
                expected,
            )
            saved_message = SimpleNamespace(
                message="![Old chart](http://localhost:3000/api/chat/file/old-id)",
                tool_calls=[
                    SimpleNamespace(
                        generated_files=None,
                        tool_call_response=json.dumps(
                            {
                                "generated_files": [
                                    {
                                        "filename": "old.png",
                                        "file_link": "/api/chat/file/old-id",
                                    }
                                ]
                            }
                        ),
                    )
                ],
            )
            self.assertEqual(
                session_loading.translate_assistant_message_to_packets(
                    saved_message, None
                ),
                ["[old.png](/api/chat/file/old-id)", None, 0],
            )

    def test_python_file_link_description_drift_fails_strict(self) -> None:
        wrapper = _load_wrapper()
        modules, *_ = _code_description_modules("Upstream changed this description")

        with patch.dict(
            os.environ, {"WRAPPER_PATCH_STRICT": "true"}, clear=True
        ), patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(RuntimeError, "did not match"):
                wrapper.apply_python_file_link_prompt_patches()

    def test_chat_file_user_file_lookup_skips_non_uuid_ids(self) -> None:
        wrapper = _load_wrapper()
        onyx = _package("onyx")
        db = _package("onyx.db")
        user_file = ModuleType("onyx.db.user_file")
        _exec_module_source(
            user_file,
            "<chat_file_id_fake_user_file>",
            """\
class UserFile:
    id = "id"

def get_user_file_by_id(user_file_id, db_session):
    return db_session.query(UserFile).filter(UserFile.id == user_file_id).first()

def get_file_id_by_user_file_id(user_file_id, db_session):
    user_file = get_user_file_by_id(user_file_id, db_session)
    if user_file:
        return user_file.file_id
    return None
""",
        )
        onyx.db = db
        db.user_file = user_file

        class Query:
            def __init__(self):
                self.calls = 0

            def filter(self, _condition):
                self.calls += 1
                return self

            def first(self):
                return None

        class Session:
            def __init__(self):
                self.query_object = Query()

            def query(self, _model):
                return self.query_object

        modules = {
            "onyx": onyx,
            "onyx.db": db,
            "onyx.db.user_file": user_file,
        }
        with patch.dict(
            os.environ, {"WRAPPER_PATCH_STRICT": "true"}, clear=True
        ), patch.dict(sys.modules, modules):
            wrapper.apply_chat_file_id_validation_patch()
            session = Session()
            self.assertIsNone(
                user_file.get_file_id_by_user_file_id(
                    "0ad5_8c02-1c2d-4e22-9c41-d9e13a5e1d7b", session
                )
            )
            self.assertEqual(session.query_object.calls, 0)
            self.assertIsNone(
                user_file.get_file_id_by_user_file_id(
                    "0ad58c02-1c2d-4e22-9c41-d9e13a5e1d7b", session
                )
            )
            self.assertEqual(session.query_object.calls, 1)
            self.assertTrue(
                getattr(
                    user_file.get_file_id_by_user_file_id,
                    "_wrapper_chat_file_id_guard",
                    False,
                )
            )
            self.assertTrue(
                getattr(
                    user_file.get_user_file_by_id,
                    "_wrapper_chat_file_id_guard",
                    False,
                )
            )

    def test_python_file_link_prompt_drift_fails_strict(self) -> None:
        wrapper = _load_wrapper()
        modules, _, _, tool_prompts, _, _, _ = _code_description_modules()
        tool_prompts.PYTHON_TOOL_GUIDANCE = "Upstream changed this guidance."

        with patch.dict(
            os.environ, {"WRAPPER_PATCH_STRICT": "true"}, clear=True
        ), patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(RuntimeError, "did not match"):
                wrapper.apply_python_file_link_prompt_patches()

    def test_python_file_reminder_drift_fails_strict(self) -> None:
        wrapper = _load_wrapper()
        modules, _, _, _, chat_prompts, _, _ = _code_description_modules()
        chat_prompts.FILE_REMINDER = "Upstream changed this reminder."

        with patch.dict(
            os.environ, {"WRAPPER_PATCH_STRICT": "true"}, clear=True
        ), patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(RuntimeError, "did not match"):
                wrapper.apply_python_file_link_prompt_patches()

    def test_agent_forced_tool_choices_are_narrowly_changed_to_auto(self) -> None:
        wrapper = _load_wrapper()
        onyx = _package("onyx")
        chat = _package("onyx.chat")
        llm_loop = ModuleType("onyx.chat.llm_loop")
        deep_research = _package("onyx.deep_research")
        dr_loop = ModuleType("onyx.deep_research.dr_loop")
        tools = _package("onyx.tools")
        fake_tools = _package("onyx.tools.fake_tools")
        coding_agent = ModuleType("onyx.tools.fake_tools.coding_agent")
        research_agent = ModuleType("onyx.tools.fake_tools.research_agent")

        class ToolChoiceOptions:
            REQUIRED = "required"
            AUTO = "auto"

        llm_loop.ToolChoiceOptions = ToolChoiceOptions
        llm_loop.run_llm_loop = _chat_owner
        llm_loop.run_llm_step = _step
        dr_loop.ToolChoiceOptions = ToolChoiceOptions
        dr_loop.run_deep_research_llm_loop = _deep_research_owner
        dr_loop.run_llm_step = _step
        coding_agent.ToolChoiceOptions = ToolChoiceOptions
        coding_agent.run_coding_agent_call = _coding_owner
        coding_agent.run_llm_step_pkt_generator = _step
        research_agent.ToolChoiceOptions = ToolChoiceOptions
        research_agent.run_research_agent_call = _research_owner
        research_agent.run_llm_step = _step
        modules = {
            "onyx": onyx,
            "onyx.chat": chat,
            "onyx.chat.llm_loop": llm_loop,
            "onyx.deep_research": deep_research,
            "onyx.deep_research.dr_loop": dr_loop,
            "onyx.tools": tools,
            "onyx.tools.fake_tools": fake_tools,
            "onyx.tools.fake_tools.coding_agent": coding_agent,
            "onyx.tools.fake_tools.research_agent": research_agent,
        }
        with patch.dict(os.environ, {"WRAPPER_PATCH_STRICT": "true"}, clear=True), patch.dict(
            sys.modules, modules
        ):
            wrapper.apply_vllm_glm_auto_tool_choice_patch()

        self.assertEqual(
            coding_agent.run_llm_step_pkt_generator(tool_choice="required"), "auto"
        )
        self.assertEqual(dr_loop.run_llm_step(tool_choice="required"), "auto")
        self.assertEqual(research_agent.run_llm_step(tool_choice="required"), "auto")
        self.assertEqual(llm_loop.run_llm_step(tool_choice="required"), "auto")

    def test_code_interpreter_descriptions_match_restricted_network(self) -> None:
        wrapper = _load_wrapper()
        modules, PythonTool, BashTool, tool_prompts, _, mock_tools, ca_prompts = (
            _code_description_modules()
        )
        env = {
            "WRAPPER_PATCH_STRICT": "true",
            "ONYX_CODE_INTERPRETER_ENABLE_NETWORK": "true",
        }

        with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, modules):
            wrapper.apply_python_file_link_prompt_patches()
            wrapper.apply_python_package_capability_patches()
            wrapper.apply_code_interpreter_network_description_patches()

        descriptions = (
            PythonTool.DESCRIPTION,
            BashTool.DESCRIPTION,
            tool_prompts.PYTHON_TOOL_GUIDANCE,
            mock_tools.BASH_TOOL_DESCRIPTION["function"]["description"],
            ca_prompts.CODING_AGENT_PROMPT,
            ca_prompts.CODING_AGENT_PROMPT_REASONING,
        )
        self.assertTrue(
            all("restricted HTTP/HTTPS proxy" in text for text in descriptions)
        )
        self.assertTrue(all("no network access" not in text for text in descriptions))
        self.assertNotIn("an sandbox", PythonTool.DESCRIPTION)
        self.assertIn(
            "do not substitute Markdown image syntax",
            tool_prompts.PYTHON_TOOL_GUIDANCE,
        )
        self.assertIn("sympy", PythonTool.DESCRIPTION)
        self.assertIn("sympy", tool_prompts.PYTHON_TOOL_GUIDANCE)
        self.assertIn("reportlab", PythonTool.DESCRIPTION)
        self.assertIn("reportlab", tool_prompts.PYTHON_TOOL_GUIDANCE)
        self.assertIn("svglib", PythonTool.DESCRIPTION)
        self.assertIn("svglib", tool_prompts.PYTHON_TOOL_GUIDANCE)

    def test_code_interpreter_description_drift_fails_strict(self) -> None:
        wrapper = _load_wrapper()
        modules, *_ = _code_description_modules("Upstream changed this description")
        env = {
            "WRAPPER_PATCH_STRICT": "true",
            "ONYX_CODE_INTERPRETER_ENABLE_NETWORK": "true",
        }
        with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(RuntimeError, "did not match"):
                wrapper.apply_code_interpreter_network_description_patches()

    def test_deep_research_batch_limits_controls_and_placements(self) -> None:
        wrapper = _load_wrapper()
        runner = SimpleNamespace(_merge_tool_calls=lambda calls: list(calls))
        calls = [_ToolCall("open_url", 7), _ToolCall("search", 7)]

        prepared = wrapper._prepare_deep_research_tool_calls(
            runner,
            calls,
            max_tool_calls_per_batch=2,
            nested_placement_stride=1024,
        )
        self.assertEqual(
            [call.placement.sub_turn_index for call in prepared], [7, 1031]
        )
        self.assertEqual(wrapper._deep_research_sub_turn_index(3), 3072)

        with self.assertRaisesRegex(RuntimeError, "No calls.*executed"):
            wrapper._prepare_deep_research_tool_calls(
                runner, calls, max_tool_calls_per_batch=1
            )
        with self.assertRaisesRegex(RuntimeError, "must be called alone"):
            wrapper._validate_deep_research_control_tool_batch(
                [_ToolCall("think_tool", 0), _ToolCall("open_url", 0)]
            )

    def test_reasoning_fields_and_final_answer_fallback_are_preserved(self) -> None:
        wrapper = _load_wrapper()

        class Message:
            role = "assistant"
            tool_calls = [object()]
            provider_specific_fields = {}

            def model_dump(self, *, exclude_none):
                del exclude_none
                return {
                    key: value
                    for key, value in vars(self).items()
                    if not key.startswith("_") and value is not None
                } | {
                    "role": self.role,
                    "tool_calls": self.tool_calls,
                    "provider_specific_fields": self.provider_specific_fields,
                }

        message = Message()
        wrapper._attach_reasoning_fields(message, "private reasoning", source="test")
        dumped = wrapper._dump_message_with_reasoning_fields(message)
        self.assertEqual(dumped["reasoning_content"], "private reasoning")
        self.assertEqual(dumped["reasoning"], "private reasoning")

        history = [
            SimpleNamespace(
                message_type="TOOL_CALL_RESPONSE",
                message="command output",
                tool_call_id="call-1",
            )
        ]
        fallback = wrapper._coding_agent_final_answer_fallback(
            history, RuntimeError("provider unavailable")
        )
        self.assertIn("command output", fallback)
        self.assertIn("RuntimeError", fallback)
        self.assertNotIn("provider unavailable", fallback)

    def test_native_reasoning_override_validates_and_patches_target(self) -> None:
        wrapper = _load_wrapper(
            {"WRAPPER_PATCH_STRICT": "true", "ONYX_AGENT_USE_NATIVE_REASONING": "true"}
        )
        onyx = _package("onyx")
        llm = _package("onyx.llm")
        model_capabilities = ModuleType("onyx.llm.model_capabilities")
        model_capabilities.model_is_reasoning_model = _reasoning_detector
        llm.model_capabilities = model_capabilities
        onyx.llm = llm
        modules = {
            "onyx": onyx,
            "onyx.llm": llm,
            "onyx.llm.model_capabilities": model_capabilities,
        }

        with patch.dict(os.environ, {"WRAPPER_PATCH_STRICT": "true"}, clear=True), patch.dict(
            sys.modules, modules
        ):
            wrapper.apply_native_reasoning_detection_override_patch()

        self.assertTrue(
            model_capabilities.model_is_reasoning_model("unknown", "openai")
        )

        model_capabilities.model_is_reasoning_model = _drifted_reasoning_detector
        with patch.dict(os.environ, {"WRAPPER_PATCH_STRICT": "true"}, clear=True), patch.dict(
            sys.modules, modules
        ):
            with self.assertRaisesRegex(RuntimeError, "signature changed"):
                wrapper.apply_native_reasoning_detection_override_patch()

    def test_reasoning_trace_wraps_model_capabilities_detector(self) -> None:
        wrapper = _load_wrapper()
        wrapper._REASONING_MODE_TRACE = True
        traces = []
        wrapper._trace_reasoning_mode = lambda event, **fields: traces.append(
            (event, fields)
        )

        onyx = _package("onyx")
        llm = _package("onyx.llm")
        model_capabilities = ModuleType("onyx.llm.model_capabilities")
        model_capabilities.model_is_reasoning_model = (
            lambda model_name, model_provider: bool(model_name and model_provider)
        )
        llm.model_capabilities = model_capabilities
        onyx.llm = llm
        modules = {
            "onyx": onyx,
            "onyx.llm": llm,
            "onyx.llm.model_capabilities": model_capabilities,
        }

        with patch.dict(
            os.environ, {"WRAPPER_PATCH_STRICT": "false"}, clear=True
        ), patch.dict(sys.modules, modules):
            wrapper.apply_reasoning_mode_trace_patch()
            self.assertTrue(
                model_capabilities.model_is_reasoning_model("model", "provider")
            )

        self.assertEqual(traces[0][0], "model_detection")
        self.assertTrue(traces[0][1]["supports_reasoning"])

    def test_internal_search_caps_validate_and_limit_payload(self) -> None:
        wrapper = _load_wrapper()
        onyx = _package("onyx")
        tools_package = _package("onyx.tools")
        implementations = _package("onyx.tools.tool_implementations")
        tool_utils = ModuleType("onyx.tools.tool_implementations.utils")
        tool_utils.convert_inference_sections_to_llm_string = _internal_search_formatter
        search_package = _package("onyx.tools.tool_implementations.search")
        search_tool = ModuleType("onyx.tools.tool_implementations.search.search_tool")
        search_tool.convert_inference_sections_to_llm_string = _internal_search_formatter
        modules = {
            "onyx": onyx,
            "onyx.tools": tools_package,
            "onyx.tools.tool_implementations": implementations,
            "onyx.tools.tool_implementations.utils": tool_utils,
            "onyx.tools.tool_implementations.search": search_package,
            "onyx.tools.tool_implementations.search.search_tool": search_tool,
        }
        env = {
            "WRAPPER_PATCH_STRICT": "true",
            "ONYX_RAG_INTERNAL_SEARCH_MAX_CONTENT_CHARS_PER_RESULT": "80",
            "ONYX_RAG_INTERNAL_SEARCH_MAX_TOTAL_CONTENT_CHARS": "120",
        }

        with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, modules):
            wrapper.apply_internal_search_context_patches()

        payload, citations = tool_utils.convert_inference_sections_to_llm_string(
            ["a" * 200, "b" * 200],
            note="Search note",
        )
        parsed_payload = json.loads(payload)
        results = parsed_payload["results"]
        self.assertEqual(parsed_payload["note"], "Search note")
        self.assertEqual(citations, {1: "doc"})
        self.assertLessEqual(len(results[0]["content"]), 80)
        self.assertLessEqual(len(results[1]["content"]), 40)
        self.assertIs(
            search_tool.convert_inference_sections_to_llm_string,
            tool_utils.convert_inference_sections_to_llm_string,
        )

    def test_llm_context_override_wins_over_stored_values(self) -> None:
        wrapper = _load_wrapper()
        onyx = _package("onyx")
        configs = _package("onyx.configs")
        model_configs = ModuleType("onyx.configs.model_configs")
        model_configs.GEN_AI_MAX_TOKENS = 131072
        llm = _package("onyx.llm")
        factory = ModuleType("onyx.llm.factory")
        utils = ModuleType("onyx.llm.utils")
        factory._get_model_configured_max_input_tokens = _configured_max
        factory.get_max_input_tokens_from_llm_provider = _provider_max
        utils.get_max_input_tokens_from_llm_provider = _provider_max
        modules = {
            "onyx": onyx,
            "onyx.configs": configs,
            "onyx.configs.model_configs": model_configs,
            "onyx.llm": llm,
            "onyx.llm.factory": factory,
            "onyx.llm.utils": utils,
        }

        with patch.dict(os.environ, {"WRAPPER_PATCH_STRICT": "true"}, clear=True), patch.dict(
            sys.modules, modules
        ):
            wrapper.apply_llm_max_tokens_override_patch()

        stored = SimpleNamespace(max_input_tokens=4096)
        self.assertEqual(factory._get_model_configured_max_input_tokens(stored), 131072)
        self.assertEqual(utils.get_max_input_tokens_from_llm_provider(stored), 131072)

    def test_saved_tool_results_are_used_and_recounted(self) -> None:
        wrapper = _load_wrapper()
        onyx = _package("onyx")
        chat = _package("onyx.chat")
        chat_utils = ModuleType("onyx.chat.chat_utils")
        chat_utils.IMAGE_GENERATION_TOOL_NAME = "image_generation"
        chat_utils._build_tool_call_response_history_message = _saved_tool_response
        chat_utils.convert_chat_history = _convert_history
        configs = _package("onyx.configs")
        constants = ModuleType("onyx.configs.constants")

        class MessageType:
            TOOL_CALL_RESPONSE = "tool"

        constants.MessageType = MessageType
        prompts = _package("onyx.prompts")
        chat_prompts = ModuleType("onyx.prompts.chat_prompts")
        chat_prompts.TOOL_CALL_RESPONSE_CROSS_MESSAGE = "placeholder"
        modules = {
            "onyx": onyx,
            "onyx.chat": chat,
            "onyx.chat.chat_utils": chat_utils,
            "onyx.configs": configs,
            "onyx.configs.constants": constants,
            "onyx.prompts": prompts,
            "onyx.prompts.chat_prompts": chat_prompts,
        }
        env = {
            "WRAPPER_PATCH_STRICT": "true",
            "ONYX_AGENT_PRESERVE_TOOL_RESULTS": "true",
        }

        with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, modules):
            wrapper.apply_preserve_tool_results_patch()

        self.assertEqual(
            chat_utils._build_tool_call_response_history_message(
                "bash", None, "saved output"
            ),
            "saved output",
        )
        result = chat_utils.convert_chat_history([], [], [], None, len, {})
        self.assertEqual(result.simple_messages[0].token_count, len("persisted output"))

    def test_open_url_character_defaults_are_patched(self) -> None:
        wrapper = _load_wrapper()
        onyx = _package("onyx")
        tools_package = _package("onyx.tools")
        implementations = _package("onyx.tools.tool_implementations")
        web_search = _package("onyx.tools.tool_implementations.web_search")
        ws_utils = ModuleType("onyx.tools.tool_implementations.web_search.utils")
        ws_utils.MAX_CHARS_PER_URL = 1000

        def truncate(content, max_chars=1000):
            return content[:max_chars]

        def around(content, max_chars=1000):
            return content[:max_chars]

        ws_utils.truncate_search_result_content = truncate
        ws_utils._truncate_content_around_snippet = around
        open_url = _package("onyx.tools.tool_implementations.open_url")
        open_url_tool = ModuleType(
            "onyx.tools.tool_implementations.open_url.open_url_tool"
        )
        open_url_tool.MAX_CHARS_ACROSS_URLS = 10000

        def convert(sections, max_chars=10000):
            return sections[:max_chars]

        open_url_tool._convert_sections_to_llm_string_with_citations = convert
        modules = {
            "onyx": onyx,
            "onyx.tools": tools_package,
            "onyx.tools.tool_implementations": implementations,
            "onyx.tools.tool_implementations.web_search": web_search,
            "onyx.tools.tool_implementations.web_search.utils": ws_utils,
            "onyx.tools.tool_implementations.open_url": open_url,
            "onyx.tools.tool_implementations.open_url.open_url_tool": open_url_tool,
        }
        env = {
            "WRAPPER_PATCH_STRICT": "true",
            "ONYX_OPEN_URL_MAX_CHARS_PER_URL": "321",
            "ONYX_OPEN_URL_MAX_TOTAL_CHARS": "654",
        }

        with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, modules):
            wrapper.apply_open_url_char_limit_patches()

        self.assertEqual(ws_utils.MAX_CHARS_PER_URL, 321)
        self.assertEqual(truncate.__defaults__, (321,))
        self.assertEqual(around.__defaults__, (321,))
        self.assertEqual(open_url_tool.MAX_CHARS_ACROSS_URLS, 654)
        self.assertEqual(convert.__defaults__, (654,))


if __name__ == "__main__":
    unittest.main()
