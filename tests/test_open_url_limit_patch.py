from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
from io import StringIO
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT / "onyx/patches/sitecustomize_api_server/open_url_limit_patch.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "test_open_url_limit_patch_module", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ToolCallException(Exception):
    def __init__(self, message: str, llm_facing_message: str):
        super().__init__(message)
        self.llm_facing_message = llm_facing_message


class _Field:
    default = 10


class _Override:
    model_fields = {"max_urls": _Field()}


class _CompatibleTool:
    DESCRIPTION = "Open and read the content of one or more URLs."

    def run(self, placement, override_kwargs, **llm_kwargs):  # noqa: ANN001
        urls = list(dict.fromkeys(llm_kwargs.get("urls") or []))
        if len(urls) > override_kwargs.max_urls:
            urls = urls[: override_kwargs.max_urls]
        return urls

    def tool_definition(self):
        URLS_FIELD = "urls"
        return {
            "type": "function",
            "function": {
                "name": "open_url",
                "description": self.DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        URLS_FIELD: {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of URLs to open.",
                        }
                    },
                    "required": [URLS_FIELD],
                },
            },
        }


def _fake_modules(tool_class) -> dict[str, ModuleType]:
    names = (
        "onyx",
        "onyx.tools",
        "onyx.tools.models",
        "onyx.tools.tool_implementations",
        "onyx.tools.tool_implementations.open_url",
        "onyx.tools.tool_implementations.open_url.open_url_tool",
    )
    modules = {name: ModuleType(name) for name in names}
    models = modules["onyx.tools.models"]
    models.OpenURLToolOverrideKwargs = _Override
    models.ToolCallException = ToolCallException
    open_url_tool = modules[
        "onyx.tools.tool_implementations.open_url.open_url_tool"
    ]
    open_url_tool.OpenURLTool = tool_class
    open_url_tool.URLS_FIELD = "urls"
    open_url_tool._normalize_string_list = lambda value: list(
        dict.fromkeys(value or [])
    )
    modules["onyx.tools"].models = models
    modules["onyx.tools.tool_implementations.open_url"].open_url_tool = open_url_tool
    return modules


class OpenUrlLimitPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_module()

    def test_schema_and_description_tell_agent_about_limit(self):
        tool_class = type("OpenURLTool", (_CompatibleTool,), {})
        modules = _fake_modules(tool_class)
        with patch.dict(sys.modules, modules), redirect_stdout(StringIO()):
            self.module.install()

        tool = tool_class()
        definition = tool.tool_definition()
        urls = definition["function"]["parameters"]["properties"]["urls"]
        self.assertEqual(urls["maxItems"], 10)
        self.assertIn("at most 10 URLs", urls["description"])
        self.assertIn("at most 10 URLs", tool.description if hasattr(tool, "description") else tool.DESCRIPTION)

    def test_over_limit_fails_visibly_without_calling_original(self):
        tool_class = type("OpenURLTool", (_CompatibleTool,), {})
        modules = _fake_modules(tool_class)
        with patch.dict(sys.modules, modules), redirect_stdout(StringIO()):
            self.module.install()
            override = SimpleNamespace(max_urls=10)
            with self.assertRaises(ToolCallException) as raised:
                tool_class().run(
                    None,
                    override,
                    urls=[f"https://example.com/{index}" for index in range(11)],
                )
        self.assertIn("at most 10 URLs", raised.exception.llm_facing_message)
        self.assertIn("No URLs from this call were opened", raised.exception.llm_facing_message)

    def test_ten_urls_continue_to_original_run(self):
        tool_class = type("OpenURLTool", (_CompatibleTool,), {})
        modules = _fake_modules(tool_class)
        urls = [f"https://example.com/{index}" for index in range(10)]
        with patch.dict(sys.modules, modules), redirect_stdout(StringIO()):
            self.module.install()
            result = tool_class().run(None, SimpleNamespace(max_urls=10), urls=urls)
        self.assertEqual(result, urls)

    def test_limit_applies_after_upstream_deduplication(self):
        tool_class = type("OpenURLTool", (_CompatibleTool,), {})
        modules = _fake_modules(tool_class)
        urls = [f"https://example.com/{index}" for index in range(10)]
        with patch.dict(sys.modules, modules), redirect_stdout(StringIO()):
            self.module.install()
            result = tool_class().run(
                None,
                SimpleNamespace(max_urls=10),
                urls=[*urls, urls[0]],
            )
        self.assertEqual(result, urls)

    def test_api_bootstrap_installs_limit_after_failure_reporting(self):
        source = (
            ROOT / "onyx/patches/sitecustomize_api_server/sitecustomize.py"
        ).read_text()
        self.assertLess(
            source.index("install_open_url_failure_reporting()"),
            source.index("install_open_url_limit()"),
        )
        self.assertLess(
            source.index("install_open_url_limit()"),
            source.index("if use_obscura_browser():"),
        )


if __name__ == "__main__":
    unittest.main()
