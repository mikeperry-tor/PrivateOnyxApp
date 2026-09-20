"""Test-only synthetic-path counters on the ordinary document-serving route.

Mount alongside doc_drop_webserver.py, set the exact fixture URL path and an
output file in disposable storage, then use this file as the server command.
All ordinary handler confinement, peer restrictions, and validators remain.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit

import doc_drop_webserver


class _CountingWriter:
    def __init__(self, writer, handler):
        self.writer = writer
        self.handler = handler

    def __getattr__(self, name):
        return getattr(self.writer, name)

    def write(self, data):
        result = self.writer.write(data)
        if self.handler._count_body:
            self.handler._body_bytes += len(data) if result is None else result
        return result


class FixtureRequestHandler(doc_drop_webserver.DocDropRequestHandler):
    counter_lock = threading.Lock()

    def setup(self):
        super().setup()
        self._count_body = False
        self._body_bytes = 0
        self.wfile = _CountingWriter(self.wfile, self)

    def handle_one_request(self):
        self._count_body = False
        self._body_bytes = 0
        self._status = None
        self.path = ""
        try:
            super().handle_one_request()
        finally:
            if (
                urlsplit(self.path).path == self.server.fixture_path
                and self._status is not None
                and self.command in {"GET", "HEAD"}
            ):
                output = Path(self.server.counter_file)
                with self.counter_lock:
                    totals = json.loads(output.read_text()) if output.exists() else {}
                    key = f"{self.command}:{self._status}"
                    row = totals.setdefault(key, {"requests": 0, "body_bytes": 0})
                    row["requests"] += 1
                    row["body_bytes"] += self._body_bytes
                    temporary = output.with_suffix(".tmp")
                    temporary.write_text(json.dumps(totals, sort_keys=True) + "\n")
                    temporary.replace(output)

    def send_response(self, code, message=None):
        self._status = int(code)
        super().send_response(code, message)

    def end_headers(self):
        super().end_headers()
        self._count_body = urlsplit(self.path).path == self.server.fixture_path


def main():
    fixture_path = os.environ["ONYX_TEST_FIXTURE_PATH"]
    counter_file = os.environ["ONYX_TEST_COUNTER_FILE"]
    if not fixture_path.startswith("/onyx-reorg-") or not fixture_path.endswith("/fixture.pdf"):
        raise RuntimeError("counter requires the exact synthetic fixture path")
    # Configure only this test process; no production setting or handler changes.
    doc_drop_webserver.DocDropRequestHandler = FixtureRequestHandler
    original = doc_drop_webserver.BoundedThreadingHTTPServer

    class FixtureServer(original):
        def __init__(self, *args, **kwargs):
            self.fixture_path = fixture_path
            self.counter_file = counter_file
            super().__init__(*args, **kwargs)

    doc_drop_webserver.BoundedThreadingHTTPServer = FixtureServer
    doc_drop_webserver.main()


if __name__ == "__main__":
    main()
