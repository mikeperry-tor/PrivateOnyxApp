from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "browser" / "obscura_client"))

from private_onyx_obscura import BodyClassification  # noqa: E402
from private_onyx_obscura import FetchFailure  # noqa: E402
from private_onyx_obscura import ObscuraClientError  # noqa: E402


def _load_patch():
    path = ROOT / "onyx/patches/sitecustomize_api_server/obscura_crawler_patch.py"
    spec = importlib.util.spec_from_file_location("test_obscura_crawler_patch", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class OnyxObscuraCrawlerPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_patch()

    def test_document_limit_is_positive_decimal_mib(self):
        with patch.dict(os.environ, {"ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB": "7"}):
            self.assertEqual(self.module._parse_document_limit(), 7 * 1024 * 1024)
        for value in ("", "0", "-1", "1.5", "unlimited"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB": value}
            ):
                with self.assertRaises(RuntimeError):
                    self.module._parse_document_limit()

    def test_invocation_state_is_deadline_and_finalization_guard(self):
        live = self.module.InvocationState(time.monotonic() + 10)
        self.assertTrue(live.permits_navigation())
        live.finish()
        self.assertFalse(live.permits_navigation())
        expired = self.module.InvocationState(time.monotonic() - 1)
        self.assertEqual(expired.remaining(), 0.0)
        self.assertFalse(expired.permits_navigation())

    def test_invocation_state_copies_partial_failure_records(self):
        state = self.module.InvocationState(time.monotonic() + 10)
        failures = [SimpleNamespace(url="https://example.com", failure_reason="denied")]
        state.record_partial_failures(failures)
        failures.clear()
        recorded = state.get_partial_failures()
        self.assertEqual(len(recorded), 1)
        recorded.clear()
        self.assertEqual(len(state.get_partial_failures()), 1)

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
        self.assertIn("documents\n\nPartial open_url failure report:", result.llm_facing_response)
        self.assertIn("https://example.com (denied)", result.llm_facing_response)

        all_failed = SimpleNamespace(rich_response=None, llm_facing_response="failed")
        self.module._append_partial_failure_report(
            all_failed, failures, build_failure_message
        )
        self.assertEqual(all_failed.llm_facing_response, "failed")

    def test_result_collection_returns_completed_work_without_waiting_for_orphan(self):
        release = threading.Event()
        state = self.module.InvocationState(time.monotonic() + 0.08)

        def fetch_one(url):
            if url == "slow":
                release.wait(1)
            return f"result:{url}"

        def failure(url, reason):
            return SimpleNamespace(url=url, failure_reason=reason)

        started = time.monotonic()
        try:
            results = self.module._collect_url_results(
                ["fast", "slow"],
                state,
                fetch_one,
                failure,
                max_workers=2,
                headroom_seconds=0.02,
            )
        finally:
            release.set()
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertEqual(results[0], "result:fast")
        self.assertEqual(results[1].url, "slow")
        self.assertIn("collection deadline", results[1].failure_reason)
        self.assertFalse(state.permits_navigation())

    def test_raw_text_decoding_is_strict_and_charset_bounded(self):
        result = SimpleNamespace(
            body=b"hello", body_classification=BodyClassification.TEXT, charset="utf-8"
        )
        self.assertEqual(self.module._decode_raw(result), "hello")
        result.charset = "windows-1252"
        with self.assertRaises(ObscuraClientError) as raised:
            self.module._decode_raw(result)
        self.assertEqual(raised.exception.category, FetchFailure.UNSUPPORTED_CHARSET)

    def test_source_contains_no_removed_fetch_fallback(self):
        source = Path(self.module.__file__).read_text()
        self.assertNotIn("requests.get", source)
        # The one occurrence is a strict upstream source-shape assertion, not
        # an invocation retained by the replacement path.
        self.assertEqual(source.count("fetch_rendered_html("), 1)
        self.assertNotIn("Firecrawl", source)
        self.assertIn('want="both"', source)
        self.assertIn("ACTIVE_FETCHES = threading.BoundedSemaphore(5)", source)
        self.assertIn(
            'FetchFailure.PRE_NAVIGATION_TIMEOUT: "browser setup timed out before navigation"',
            source,
        )
        self.assertIn("Partial open_url failure report:", source)
        self.assertIn("BROWSER_ATTEMPT_TIMEOUT_SECONDS = 105.0", source)
        self.assertIn("executor.shutdown(wait=False, cancel_futures=True)", source)


if __name__ == "__main__":
    unittest.main()
