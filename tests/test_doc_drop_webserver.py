from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from onyx.doc_drop_webserver import DocDropRequestHandler, _path_is_confined


class DocDropWebserverTests(unittest.TestCase):
    def test_health_is_local_and_request_logging_is_silent(self) -> None:
        handler = object.__new__(DocDropRequestHandler)
        handler.path = "/_health?probe=1"
        handler.client_address = ("127.0.0.1", 12345)
        handler.server = Mock(loopback_peers_only=True)
        handler._send_health = Mock()
        handler.do_GET()
        handler._send_health.assert_called_once_with()
        handler.log_message("private path %s", "must-not-be-logged")

    def test_health_head_has_no_body(self) -> None:
        handler = object.__new__(DocDropRequestHandler)
        handler.command = "HEAD"
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler._send_health()
        handler.send_response.assert_called_once_with(200)
        self.assertEqual(handler.wfile.getvalue(), b"")

    def test_non_loopback_client_is_rejected_before_serving(self) -> None:
        handler = object.__new__(DocDropRequestHandler)
        handler.path = "/_health"
        handler.client_address = ("192.0.2.10", 12345)
        handler.server = Mock(loopback_peers_only=True)
        handler.send_error = Mock()
        handler._send_health = Mock()
        handler.do_GET()
        handler.send_error.assert_called_once_with(403, "Host-local access only")
        handler._send_health.assert_not_called()

    def test_docker_mode_does_not_apply_host_peer_restriction(self) -> None:
        handler = object.__new__(DocDropRequestHandler)
        handler.client_address = ("192.0.2.10", 12345)
        handler.server = Mock(loopback_peers_only=False)
        handler.send_error = Mock()
        self.assertFalse(handler._reject_non_loopback_client())
        handler.send_error.assert_not_called()

    def test_document_path_rejects_symlinks_inside_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.txt"
            target.write_text("private", encoding="utf-8")
            link = root / "link.txt"
            os.symlink(target, link)
            self.assertFalse(_path_is_confined(str(link), directory))

    def test_document_path_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with tempfile.TemporaryDirectory() as outside:
                root = Path(directory)
                link = root / "outside"
                os.symlink(outside, link)
                self.assertFalse(
                    _path_is_confined(str(link / "secret.txt"), directory)
                )

    def test_document_path_allows_regular_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            nested = Path(directory) / "papers" / "paper.pdf"
            nested.parent.mkdir()
            nested.write_bytes(b"pdf")
            self.assertTrue(_path_is_confined(str(nested), directory))


if __name__ == "__main__":
    unittest.main()
