from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "browser" / "obscura_client"))

from private_onyx_obscura import FetchFailure  # noqa: E402
from private_onyx_obscura import ObscuraClientError  # noqa: E402
from private_onyx_obscura import fetch_sync  # noqa: E402
from private_onyx_obscura import is_text_like_content_type  # noqa: E402
from private_onyx_obscura import normalize_public_url  # noqa: E402
from private_onyx_obscura import validate_wait_until  # noqa: E402


class ObscuraClientTests(unittest.TestCase):
    def test_wait_values_are_exact(self):
        for value in ("domcontentloaded", "load", "networkidle0", "networkidle2"):
            self.assertEqual(validate_wait_until(value), value)
        for value in ("", "Load", "networkidle", "commit"):
            with self.assertRaises(ValueError):
                validate_wait_until(value)

    def test_text_predicate_matches_pinned_obscura(self):
        text = (
            None, "", "text/plain", "text/html; charset=utf-8", "application/json",
            "application/xml", "application/xhtml+xml", "application/javascript",
            "application/ecmascript", "image/svg+xml", "application/ld+json",
            "application/atom+xml",
        )
        binary = ("application/pdf", "application/octet-stream", "image/png")
        for content_type in text:
            self.assertTrue(is_text_like_content_type(content_type), content_type)
        for content_type in binary:
            self.assertFalse(is_text_like_content_type(content_type), content_type)

    def test_structural_url_validation_does_not_resolve(self):
        normalized, fragment = normalize_public_url(
            "https://ExAmPle.COM./a?q=private#section", allow_http=False
        )
        self.assertEqual(normalized, "https://example.com/a?q=private")
        self.assertEqual(fragment, "section")
        forbidden = (
            "http://example.com", "file:///etc/passwd", "https://localhost/x",
            "https://service/x", "https://host.docker.internal/x",
            "https://127.0.0.1/x", "https://10.0.0.1/x", "https://[::1]/x",
            "https://user:secret@example.com/x",
        )
        for value in forbidden:
            with self.assertRaises(ObscuraClientError, msg=value):
                normalize_public_url(value, allow_http=False)

    def test_sync_adapter_rejects_nested_event_loop(self):
        async def nested():
            with self.assertRaisesRegex(RuntimeError, "active event loop"):
                fetch_sync("https://example.com")

        asyncio.run(nested())


if __name__ == "__main__":
    unittest.main()
