from __future__ import annotations

import json
from types import SimpleNamespace
import unittest

from patch_test_support import FreshPatchTestCase, load_patch


class Block:
    def __init__(self, **data):
        self.data = data

    def model_dump(self, **kwargs):
        return self.data.copy()


class MCPResultTests(FreshPatchTestCase):
    def setUp(self):
        self.module = load_patch("api.mcp_results")

    def result(self, content=(), structured=None, error=False):
        return self.module.process_mcp_result(SimpleNamespace(
            content=[Block(**block) for block in content],
            structuredContent=structured, isError=error,
        ))

    def text(self, value):
        return self.result([{"type": "text", "text": value}])

    def test_json_decoded_once_and_server_fields_preserved(self):
        for value in ({"tool_result": {"count": 3}}, [1, {"x": "雪"}], {}, [],
                      0, False, None, "quoted string"):
            with self.subTest(value=value):
                result = self.text(json.dumps(value))
                self.assertEqual(result.data, value)
                self.assertEqual(result.response_type, "json")
                self.assertEqual(json.loads(result.llm_text), value)
                self.assertEqual(result.display_data, "null" if value is None else value)
        nested = json.dumps({"count": 3})
        self.assertEqual(self.text(json.dumps(nested)).data, nested)

    def test_plain_text_and_ambiguous_json_preserved_verbatim(self):
        for value in ("", "hello\nworld", "{broken", 'prefix {"a":1}',
                      '```json\n{"a":1}\n```', '{"a":1,"a":2}',
                      "NaN", "Infinity", "1e999", '[1, NaN]'):
            with self.subTest(value=value):
                result = self.text(value)
                self.assertEqual(result.response_type, "text")
                self.assertEqual(result.llm_text, value)
                self.assertEqual(result.display_data, value)

    def test_structured_content_including_empty_and_duplicate(self):
        for value in ({}, {"count": 3, "valid": True}):
            for blocks in ([], [{"type": "text", "text": json.dumps(value, indent=2)}]):
                result = self.result(blocks, value)
                self.assertEqual(result.data, value)
                self.assertEqual(json.loads(result.llm_text), value)
        # Python considers True == 1; these are different JSON payloads.
        result = self.result([{"type": "text", "text": '{"count":true}'}], {"count": 1})
        self.assertIn("content", result.data)

    def test_supplementary_and_typed_content_remains_ordered(self):
        content = [
            {"type": "text", "text": "Explanation"},
            {"type": "resource_link", "uri": "https://example.com/a", "name": "a"},
            {"type": "resource", "resource": {"uri": "file:///fixture", "text": "body"}},
            {"type": "image", "data": "AA==", "mimeType": "image/png"},
            {"type": "audio", "data": "AA==", "mimeType": "audio/wav"},
        ]
        for structured in (None, {}, {"count": 1}):
            result = self.result(content, structured)
            self.assertEqual(result.data["content"], content)
            self.assertEqual(json.loads(result.llm_text), result.data)
            if structured is not None:
                self.assertEqual(result.data["structuredContent"], structured)
        annotated = [{"type": "text", "text": "{}", "annotations": {"audience": ["user"]}}]
        self.assertEqual(self.result(annotated, {}).data["content"], annotated)
        self.assertEqual(self.result().data, {"content": []})

    def test_tool_errors_keep_details_and_error_state(self):
        content = [{"type": "text", "text": "No such record"}]
        result = self.result(content, {"code": "missing"}, True)
        self.assertTrue(result.is_error)
        self.assertEqual(json.loads(result.llm_text), {
            "isError": True, "content": content, "structuredContent": {"code": "missing"},
        })
        self.assertEqual(self.result(error=True).data, {"content": [], "isError": True})

    def test_signature_defaults_kinds_and_source_drift_fail(self):
        def target(value=None):
            return value
        contract = (("value", "POSITIONAL_OR_KEYWORD", None),)
        self.module._validate(target, contract, ("return value",))
        for params, markers in (
            ((("value", "POSITIONAL_OR_KEYWORD", 1),), ("return value",)),
            ((("value", "KEYWORD_ONLY", None),), ("return value",)),
            (contract, ("missing marker",)),
        ):
            with self.assertRaises(RuntimeError):
                self.module._validate(target, params, markers)
        target.__wrapped__ = lambda value=None: value
        with self.assertRaises(RuntimeError):
            self.module._validate(target, contract, ())


if __name__ == "__main__":
    unittest.main()
