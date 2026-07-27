from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "onyx/patches/sitecustomize_api_server/open_url_failure_reporting_patch.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "test_open_url_failure_reporting_patch_module", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class OpenUrlFailureReportingPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_module()

    def test_partial_failure_report_is_appended_only_to_partial_success(self):
        response = SimpleNamespace(rich_response=object(), llm_facing_response="documents")
        failures = [SimpleNamespace(url="https://example.com", failure_reason="denied")]

        def build_failure_message(**kwargs):
            self.assertEqual(kwargs["missing_document_ids"], [])
            self.assertIs(kwargs["failed_web_fetches"], failures)
            return "Failed to fetch content from URLs https://example.com (denied)"

        result = self.module._append_partial_failure_report(
            response, failures, build_failure_message
        )
        self.assertIn(
            "documents\n\nPartial open_url failure report:",
            result.llm_facing_response,
        )
        self.assertIn("https://example.com (denied)", result.llm_facing_response)

        all_failed = SimpleNamespace(rich_response=None, llm_facing_response="failed")
        self.module._append_partial_failure_report(
            all_failed, failures, build_failure_message
        )
        self.assertEqual(all_failed.llm_facing_response, "failed")

    def test_failure_state_copies_records(self):
        state = self.module._FailureState()
        failures = [SimpleNamespace(url="https://example.com")]
        state.record(failures)
        failures.clear()
        recorded = state.snapshot()
        self.assertEqual(len(recorded), 1)
        recorded.clear()
        self.assertEqual(len(state.snapshot()), 1)

    def test_api_bootstrap_installs_reporter_before_transport_choice(self):
        source = (
            ROOT / "onyx/patches/sitecustomize_api_server/sitecustomize.py"
        ).read_text()
        report_index = source.index("install_open_url_failure_reporting()")
        choice_index = source.index("if use_obscura_browser():")
        self.assertLess(report_index, choice_index)

    def test_reporter_exposes_original_run_for_later_strict_patches(self):
        source = MODULE_PATH.read_text()
        self.assertIn("_wrapper_failure_reporting_original_run = original_run", source)


if __name__ == "__main__":
    unittest.main()
