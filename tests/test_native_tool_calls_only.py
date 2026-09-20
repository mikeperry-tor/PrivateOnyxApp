from __future__ import annotations

from patch_test_support import FreshPatchTestCase, load_patch

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "onyx" / "patches" / "onyx_wrapper_patches" / "api/tool_calls.py"


def _try_fallback_tool_extraction(
    llm_step_result,
    tool_choice,
    fallback_extraction_attempted,
    tool_defs,
    turn_index,
):
    # Strict patch-contract markers from pinned Onyx:
    # xml_tool_call_text_detected
    # extract_tool_calls_from_response_text(
    del tool_choice, fallback_extraction_attempted, tool_defs, turn_index
    return llm_step_result, True


class _XmlToolCallContentFilter:
    def __init__(self):
        self._inside_function_calls_block = False
        self._pending = ""

    def process(self, content):
        # Strict patch-contract markers from pinned Onyx:
        # _find_function_calls_open_marker
        # Drop the whole function_calls block
        if self._inside_function_calls_block:
            return ""
        return content

    def flush(self):
        # Strict patch-contract markers from pinned Onyx:
        # if self._inside_function_calls_block:
        # Drop any incomplete block at stream end
        # remaining = self._pending
        return ""


ORIGINAL_XML_PROCESS = _XmlToolCallContentFilter.process
ORIGINAL_XML_FLUSH = _XmlToolCallContentFilter.flush


def _load_wrapper():
    wrapper = load_patch("api.tool_calls")
    return wrapper


class NativeToolCallsOnlyTests(FreshPatchTestCase):
    def setUp(self):
        _XmlToolCallContentFilter.process = ORIGINAL_XML_PROCESS
        _XmlToolCallContentFilter.flush = ORIGINAL_XML_FLUSH
        onyx = ModuleType("onyx")
        chat = ModuleType("onyx.chat")
        llm_loop = ModuleType("onyx.chat.llm_loop")
        llm_step = ModuleType("onyx.chat.llm_step")
        llm_loop._try_fallback_tool_extraction = _try_fallback_tool_extraction
        llm_step._XmlToolCallContentFilter = _XmlToolCallContentFilter
        onyx.chat = chat
        chat.llm_loop = llm_loop
        chat.llm_step = llm_step
        self.llm_loop = llm_loop
        self.llm_step = llm_step
        self.module_patch = patch.dict(
            sys.modules,
            {
                "onyx": onyx,
                "onyx.chat": chat,
                "onyx.chat.llm_loop": llm_loop,
                "onyx.chat.llm_step": llm_step,
            },
        )
        self.module_patch.start()
        self.wrapper = _load_wrapper()
        self.wrapper.apply_native_tool_calls_only_patch()

    def tearDown(self):
        self.module_patch.stop()

    def test_json_or_xml_text_never_becomes_an_executable_tool_call(self):
        result = SimpleNamespace(
            answer='{"name":"search","arguments":{"q":"onyx"}}',
            reasoning=None,
            raw_answer=None,
            tool_calls=None,
        )

        returned, attempted = self.llm_loop._try_fallback_tool_extraction(
            result,
            "required",
            False,
            [{"type": "function", "function": {"name": "search"}}],
            0,
        )

        self.assertIs(returned, result)
        self.assertFalse(attempted)
        self.assertIsNone(returned.tool_calls)

    def test_split_xml_tool_payload_is_visible_byte_for_byte(self):
        pieces = [
            "before <fun",
            'ction_calls><invoke name="search">',
            '<parameter name="q">onyx</parameter></invoke>',
            "</function_calls> after",
        ]
        content_filter = self.llm_step._XmlToolCallContentFilter()

        visible = "".join(content_filter.process(piece) for piece in pieces)
        visible += content_filter.flush()

        self.assertEqual(visible, "".join(pieces))

    def test_patch_is_idempotent(self):
        fallback = self.llm_loop._try_fallback_tool_extraction
        process = self.llm_step._XmlToolCallContentFilter.process
        self.wrapper.apply_native_tool_calls_only_patch()
        self.assertIs(self.llm_loop._try_fallback_tool_extraction, fallback)
        self.assertIs(self.llm_step._XmlToolCallContentFilter.process, process)

    def test_policy_can_be_disabled_independently(self):
        self.llm_loop._try_fallback_tool_extraction = _try_fallback_tool_extraction
        self.llm_step._XmlToolCallContentFilter.process = ORIGINAL_XML_PROCESS
        self.llm_step._XmlToolCallContentFilter.flush = ORIGINAL_XML_FLUSH

        with patch.dict(os.environ, {"ONYX_LLM_NATIVE_TOOL_CALLS_ONLY": "false"}):
            wrapper = _load_wrapper()
            wrapper.apply_native_tool_calls_only_patch()

        self.assertIs(
            self.llm_loop._try_fallback_tool_extraction,
            _try_fallback_tool_extraction,
        )
        self.assertIs(
            self.llm_step._XmlToolCallContentFilter.process,
            ORIGINAL_XML_PROCESS,
        )
        self.assertIs(
            self.llm_step._XmlToolCallContentFilter.flush,
            ORIGINAL_XML_FLUSH,
        )


if __name__ == "__main__":
    unittest.main()
