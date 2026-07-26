#!/usr/bin/env python3
"""Networkless HTTP fixtures for the pinned Obscura image contract."""

from __future__ import annotations

import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


_COUNTS: dict[str, int] = {}
_COUNTS_LOCK = threading.Lock()
_BARRIER = threading.Condition()
_BARRIER_ACTIVE = 0
_STRESS = threading.Condition()
_STRESS_ACTIVE = 0
_CAPACITY = threading.Condition()
_CAPACITY_ACTIVE = 0


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send(
        self,
        body: bytes,
        *,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        global _BARRIER_ACTIVE, _CAPACITY_ACTIVE, _STRESS_ACTIVE

        path = urlsplit(self.path).path
        if path == "/health":
            self._send(b"ok", content_type="text/plain")
            return
        if path == "/static":
            self._send(
                b"<html><head><title>Static</title></head>"
                b"<body><main id='static'>static fixture</main></body></html>",
                content_type="text/html; charset=utf-8",
            )
            return
        if path == "/javascript":
            self._send(
                b"<html><head><title>JavaScript</title></head><body>"
                b"<main id='state'>initial</main>"
                b"<script>document.getElementById('state').textContent='rendered';</script>"
                b"</body></html>",
                content_type="text/html; charset=utf-8",
            )
            return
        if path == "/redirect":
            self._send(
                b"",
                content_type="text/plain",
                status=HTTPStatus.FOUND,
                headers={"Location": "/final"},
            )
            return
        if path == "/final":
            self._send(
                b"<html><body><main id='final'>redirect terminal</main></body></html>",
                content_type="text/html; charset=utf-8",
            )
            return
        if path == "/document.pdf":
            self._send(
                b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n",
                content_type="application/pdf",
            )
            return
        if path == "/raw.txt":
            self._send(b"raw text fixture\n", content_type="text/plain; charset=utf-8")
            return
        if path == "/unsupported.bin":
            self._send(b"\x00\x01\x02private-onyx", content_type="application/octet-stream")
            return
        if path.startswith("/style/"):
            self._send(b"body { color: rgb(1, 2, 3); }", content_type="text/css")
            return
        if path == "/heavy":
            styles = "".join(
                f"<link rel='stylesheet' href='/style/{index}.css'>"
                for index in range(24)
            )
            body = (
                "<html><head><title>Heavy</title>"
                + styles
                + "</head><body><main id='heavy'>heavy fixture</main></body></html>"
            ).encode()
            self._send(body, content_type="text/html; charset=utf-8")
            return
        if path.startswith("/counted/"):
            token = path.removeprefix("/counted/")
            with _COUNTS_LOCK:
                count = _COUNTS.get(token, 0) + 1
                _COUNTS[token] = count
            body = (
                f"<html><body><main id='count'>{count}</main></body></html>"
            ).encode()
            self._send(body, content_type="text/html; charset=utf-8")
            return
        if path.startswith("/barrier/"):
            with _BARRIER:
                _BARRIER_ACTIVE += 1
                _BARRIER.notify_all()
                deadline = time.monotonic() + 4
                while _BARRIER_ACTIVE < 2 and time.monotonic() < deadline:
                    _BARRIER.wait(deadline - time.monotonic())
                parallel = _BARRIER_ACTIVE >= 2
            body = (
                b"<html><body><main id='barrier'>parallel</main></body></html>"
                if parallel
                else b"<html><body><main id='barrier'>serialized</main></body></html>"
            )
            self._send(body, content_type="text/html; charset=utf-8")
            return
        if path.startswith("/stress/"):
            with _STRESS:
                _STRESS_ACTIVE += 1
                _STRESS.notify_all()
                deadline = time.monotonic() + 8
                while _STRESS_ACTIVE < 10 and time.monotonic() < deadline:
                    _STRESS.wait(deadline - time.monotonic())
                parallel = _STRESS_ACTIVE >= 10
            body = (
                b"<html><body><main id='stress'>parallel-ten</main></body></html>"
                if parallel
                else b"<html><body><main id='stress'>under-capacity</main></body></html>"
            )
            self._send(body, content_type="text/html; charset=utf-8")
            return
        if path.startswith("/capacity/"):
            with _CAPACITY:
                _CAPACITY_ACTIVE += 1
                _CAPACITY.notify_all()
                deadline = time.monotonic() + 12
                while _CAPACITY_ACTIVE < 15 and time.monotonic() < deadline:
                    _CAPACITY.wait(deadline - time.monotonic())
                full = _CAPACITY_ACTIVE >= 15
            body = (
                b"<html><body><main id='capacity'>parallel-fifteen</main></body></html>"
                if full
                else b"<html><body><main id='capacity'>under-capacity</main></body></html>"
            )
            self._send(body, content_type="text/html; charset=utf-8")
            return
        self._send(
            b"not found",
            content_type="text/plain",
            status=HTTPStatus.NOT_FOUND,
        )


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
