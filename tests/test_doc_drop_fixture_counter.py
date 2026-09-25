from __future__ import annotations

from functools import partial
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "onyx"))
import doc_drop_webserver
from doc_drop_fixture_counter import FixtureRequestHandler


class FixtureCounterTests(unittest.TestCase):
    def test_exact_path_head_get_bytes_and_ordinary_handler_restoration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_root = root / "synthetic"
            fixture = fixture_root / "onyx-reorg-test"
            fixture.mkdir(parents=True)
            ordinary = root / "ordinary"
            ordinary.mkdir()
            (fixture / "fixture.pdf").write_bytes(b"synthetic fixture")
            (ordinary / "ordinary.txt").write_text("unrecorded")
            counters = root / "counter.json"
            server = doc_drop_webserver.BoundedThreadingHTTPServer(
                ("127.0.0.1", 0), partial(FixtureRequestHandler, directory=str(ordinary)),
                loopback_peers_only=True,
            )
            server.fixture_directory = str(fixture_root)
            server.fixture_path = "/onyx-reorg-test/fixture.pdf"
            server.counter_file = str(counters)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            validators = []
            try:
                for method, path in (("HEAD", server.fixture_path), ("GET", server.fixture_path), ("GET", "/ordinary.txt")):
                    connection = HTTPConnection(*server.server_address)
                    connection.request(method, path)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read()
                    if path == server.fixture_path:
                        validators.append((response.getheader("Last-Modified"), response.getheader("Content-Length")))
                    connection.close()
            finally:
                server.shutdown()
                thread.join()
                server.server_close()
            self.assertEqual(validators[0], validators[1])
            self.assertEqual(json.loads(counters.read_text()), {
                "HEAD:200": {"requests": 1, "body_bytes": 0},
                "GET:200": {"requests": 1, "body_bytes": len(b"synthetic fixture")},
            })
            self.assertIsNot(doc_drop_webserver.DocDropRequestHandler, FixtureRequestHandler)
