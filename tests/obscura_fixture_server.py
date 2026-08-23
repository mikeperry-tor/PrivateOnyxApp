#!/usr/bin/env python3
"""Networkless HTTP fixtures for the pinned Obscura image contract."""

from __future__ import annotations

import gzip
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
_CONNECTION_IDS: dict[int, int] = {}
_NEXT_CONNECTION_ID = 0


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

    def _connection_id(self) -> int:
        global _NEXT_CONNECTION_ID
        identity = id(self.connection)
        with _COUNTS_LOCK:
            connection_id = _CONNECTION_IDS.get(identity)
            if connection_id is None:
                _NEXT_CONNECTION_ID += 1
                connection_id = _NEXT_CONNECTION_ID
                _CONNECTION_IDS[identity] = connection_id
            return connection_id

    def _search_page(self, *, method: str, result_path: str) -> None:
        connection_id = self._connection_id()
        body = (
            "<html><body>"
            f"<main data-connection='{connection_id}'>homepage</main>"
            f"<form action='{result_path}' method='{method}'>"
            "<textarea name='q'></textarea>"
            "<input type='hidden' name='lang' value='en'>"
            "</form></body></html>"
        ).encode()
        self._send(body, content_type="text/html; charset=utf-8")

    def _search_result(self, *, method: str) -> None:
        connection_id = self._connection_id()
        body = (
            "<html><body>"
            f"<main data-method='{method}' data-connection='{connection_id}'>"
            "submitted</main></body></html>"
        ).encode()
        self._send(body, content_type="text/html; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        global _BARRIER_ACTIVE, _CAPACITY_ACTIVE, _STRESS_ACTIVE

        path = urlsplit(self.path).path
        if path == "/health":
            self._send(b"ok", content_type="text/plain")
            return
        if path == "/retained-active":
            self._send(
                b"<html><body><main id='retained-active'>active</main><script>"
                b"setTimeout(() => setInterval(() => {"
                b"let value = 0; for (let index = 0; index < 20000; index += 1) "
                b"value = (value + index) % 2147483647;"
                b"fetch('/idle-pulse', {cache: 'no-store'});"
                b"globalThis.__privateOnyxRetainedValue = value;"
                b"}, 20), 100);"
                b"</script></body></html>",
                content_type="text/html; charset=utf-8",
            )
            return
        if path == "/idle-pulse":
            with _COUNTS_LOCK:
                _COUNTS[path] = _COUNTS.get(path, 0) + 1
            self._send(b"pulse", content_type="text/plain")
            return
        if path == "/idle-pulse-count":
            with _COUNTS_LOCK:
                count = _COUNTS.get("/idle-pulse", 0)
            self._send(str(count).encode(), content_type="text/plain")
            return
        if path == "/idle-pulse-reset":
            with _COUNTS_LOCK:
                _COUNTS["/idle-pulse"] = 0
            self._send(b"reset", content_type="text/plain")
            return
        if path == "/search-get-home":
            self._search_page(method="get", result_path="/search-get-result")
            return
        if path == "/search-post-home":
            self._search_page(method="post", result_path="/search-post-result")
            return
        if path == "/search-post-302-home":
            self._search_page(method="post", result_path="/search-post-302")
            return
        if path == "/search-post-307-home":
            self._search_page(method="post", result_path="/search-post-307")
            return
        if path == "/search-get-result":
            self._search_result(method="GET")
            return
        if path == "/search-post-redirected-result":
            self._search_result(method="GET")
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
        if path == "/post-message":
            self._send(
                b"<html><body><main id='message-state'>pending</main>"
                b"<iframe src='/post-message-child'></iframe><script>"
                b"addEventListener('message', event => {"
                b"if (event.origin === location.origin && event.data === 'frame-ready') "
                b"document.getElementById('message-state').textContent = event.data;"
                b"});</script></body></html>",
                content_type="text/html; charset=utf-8",
            )
            return
        if path == "/post-message-child":
            self._send(
                b"<html><body><script>"
                b"parent.postMessage('frame-ready', location.origin);"
                b"</script></body></html>",
                content_type="text/html; charset=utf-8",
            )
            return
        if path == "/modern-javascript":
            self._send(
                b"<html><head><title>Modern JavaScript</title></head><body>"
                b"<template id='source'><span class='clone'>pending</span></template>"
                b"<script id='__NEXT_DATA__' type='application/json'>{}</script>"
                b"<main id='state'>initial</main>"
                b"<main id='named-state'>initial</main>"
                b"<main id='timing-state'>initial</main>"
                b"<main id='svg-state'>initial</main>"
                b"<main id='stream-state'>initial</main>"
                b"<main id='module-state'>initial</main><script>"
                b"'use strict';"
                b"window.__NEXT_DATA__={ready:'named-shadow'};"
                b"document.getElementById('named-state').textContent=window.__NEXT_DATA__.ready;"
                b"document.getElementById('timing-state').textContent="
                b"typeof PerformanceNavigationTiming;"
                b"const svgAnchor=document.createElementNS('http://www.w3.org/2000/svg','a');"
                b"document.getElementById('svg-state').textContent="
                b"String(svgAnchor instanceof SVGAElement);"
                b"const response=new Response(new TextEncoder().encode('streamed'));"
                b"response.body.pipeThrough(new TextDecoderStream()).getReader().read()"
                b".then(item=>document.getElementById('stream-state').textContent=item.value);"
                b"const clone=document.getElementById('source').content.cloneNode(true);"
                b"clone.querySelector('.clone').textContent='cloned';"
                b"document.body.appendChild(clone);"
                b"const state=document.getElementById('state');"
                b"state.addEventListener('ready',event=>state.textContent=event.detail);"
                b"state.dispatchEvent(new CustomEvent('ready',{detail:'custom-event'}));"
                b"state.style.cssText='color: rgb(4, 5, 6)';"
                b"</script><script type='module' src='/search-module.js'></script>"
                b"</body></html>",
                content_type="text/html; charset=utf-8",
            )
            return
        if path == "/search-module.js":
            self._send(
                b"import {value} from './search-module-dependency.js';"
                b"document.getElementById('module-state').textContent=value;",
                content_type="application/javascript; charset=utf-8",
            )
            return
        if path == "/search-module-dependency.js":
            self._send(
                b"export const value='module-graph';",
                content_type="application/javascript; charset=utf-8",
            )
            return
        if path == "/connection-state/cache-page":
            self._send(
                b"<html><body>"
                b"<script src='/connection-state/cache-script.js'></script>"
                b"<script src='/connection-state/cache-script.js'></script>"
                b"</body></html>",
                content_type="text/html; charset=utf-8",
            )
            return
        if path == "/connection-state/cache-script.js":
            with _COUNTS_LOCK:
                count = _COUNTS.get(path, 0) + 1
                _COUNTS[path] = count
            self._send(
                (
                    "globalThis.__privateOnyxCacheObservations ||= [];"
                    f"globalThis.__privateOnyxCacheObservations.push({count});"
                ).encode(),
                content_type="application/javascript; charset=utf-8",
                headers={"Cache-Control": "public, max-age=3600"},
            )
            return
        if path == "/compressed":
            body = gzip.compress(
                b"<html><body><main id='compressed'>decoded gzip</main></body></html>"
            )
            self._send(
                body,
                content_type="text/html; charset=utf-8",
                headers={"Content-Encoding": "gzip"},
            )
            return
        if path == "/charset":
            self._send(
                "<html><body><main id='charset'>café €</main></body></html>".encode(
                    "cp1252"
                ),
                content_type="text/html; charset=windows-1252",
            )
            return
        if path == "/session/set":
            self._send(
                b"<html><body><main id='session'>set</main></body></html>",
                content_type="text/html; charset=utf-8",
                headers={
                    "Set-Cookie": (
                        "private-onyx-provider-session=retained; "
                        "Path=/; HttpOnly; SameSite=Lax"
                    )
                },
            )
            return
        if path == "/session/check":
            retained = (
                "private-onyx-provider-session=retained"
                in self.headers.get("Cookie", "")
            )
            state = "retained" if retained else "missing"
            self._send(
                (
                    f"<html><body><main id='session'>{state}</main></body></html>"
                ).encode(),
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

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        if path == "/search-post-result":
            self._search_result(method="POST")
            return
        if path == "/search-post-302":
            self._send(
                b"",
                content_type="text/plain",
                status=HTTPStatus.FOUND,
                headers={"Location": "/search-post-redirected-result"},
            )
            return
        if path == "/search-post-307":
            self._send(
                b"",
                content_type="text/plain",
                status=HTTPStatus.TEMPORARY_REDIRECT,
                headers={"Location": "/search-post-preserved-result"},
            )
            return
        if path == "/search-post-preserved-result":
            self._search_result(method="POST")
            return
        self._send(
            b"not found",
            content_type="text/plain",
            status=HTTPStatus.NOT_FOUND,
        )


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
