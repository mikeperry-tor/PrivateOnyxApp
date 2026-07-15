from __future__ import annotations

import importlib.util
import os
import sys
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


if __name__ == "__main__":
    unittest.main()
