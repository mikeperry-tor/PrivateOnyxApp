from __future__ import annotations

import argparse
import html
import io
import ipaddress
import os
import sys
import urllib.parse
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from http.server import ThreadingHTTPServer


ALWAYS_HIDDEN_NAMES = {
    ".DS_Store",
    "__pycache__",
}
HEALTH_PATH = "/_health"


def _is_hidden_name(name: str) -> bool:
    return name.startswith(".") or name in ALWAYS_HIDDEN_NAMES


def _path_contains_hidden_name(path: str) -> bool:
    normalized = os.path.normpath(path)
    return any(_is_hidden_name(part) for part in normalized.split(os.sep))


class DocDropRequestHandler(SimpleHTTPRequestHandler):
    """Static file handler that converts unreadable files into HTTP errors."""

    def _client_is_loopback(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

    def _reject_non_loopback_client(self) -> bool:
        if not getattr(self.server, "loopback_peers_only", False):
            return False
        if self._client_is_loopback():
            return False
        self.send_error(HTTPStatus.FORBIDDEN, "Host-local access only")
        return True

    def _send_health(self) -> None:
        body = b"ok\n"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self._reject_non_loopback_client():
            return
        if urllib.parse.urlsplit(self.path).path == HEALTH_PATH:
            self._send_health()
            return
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802
        if self._reject_non_loopback_client():
            return
        if urllib.parse.urlsplit(self.path).path == HEALTH_PATH:
            self._send_health()
            return
        super().do_HEAD()

    def send_head(self):  # noqa: ANN201
        path = self.translate_path(self.path)
        if _path_contains_hidden_name(path):
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None

        try:
            return super().send_head()
        except PermissionError:
            self.send_error(HTTPStatus.FORBIDDEN, "File not readable")
            return None
        except OSError:
            self.send_error(HTTPStatus.FORBIDDEN, "File not readable")
            return None

    def list_directory(self, path):  # noqa: ANN201
        try:
            entries = [name for name in os.listdir(path) if not _is_hidden_name(name)]
        except PermissionError:
            self.send_error(HTTPStatus.FORBIDDEN, "No permission to list directory")
            return None
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "Directory not found")
            return None

        entries.sort(key=lambda a: a.lower())
        displaypath = self.path
        displaypath = displaypath.split("#", 1)[0]
        displaypath = displaypath.split("?", 1)[0]
        try:
            displaypath = urllib.parse.unquote(displaypath, errors="surrogatepass")
        except UnicodeDecodeError:
            displaypath = urllib.parse.unquote(displaypath)
        displaypath = html.escape(displaypath, quote=False)
        enc = sys.getfilesystemencoding()
        title = f"Directory listing for {displaypath}"

        lines = [
            "<!DOCTYPE HTML>",
            '<html lang="en">',
            "<head>",
            f'<meta charset="{enc}">',
            f"<title>{title}</title>\n</head>",
            f"<body>\n<h1>{title}</h1>",
            "<hr>\n<ul>",
        ]
        for name in entries:
            fullname = os.path.join(path, name)
            displayname = linkname = name
            if os.path.isdir(fullname):
                displayname = name + "/"
                linkname = name + "/"
            if os.path.islink(fullname):
                displayname = name + "@"
            lines.append(
                '<li><a href="%s">%s</a></li>'
                % (
                    urllib.parse.quote(linkname, errors="surrogatepass"),
                    html.escape(displayname, quote=False),
                )
            )
        lines.append("</ul>\n<hr>\n</body>\n</html>\n")

        encoded = "\n".join(lines).encode(enc, "surrogateescape")
        f = io.BytesIO()
        f.write(encoded)
        f.seek(0)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-type", f"text/html; charset={enc}")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        return f

    def log_message(self, format: str, *args: object) -> None:
        # Request paths can contain private document names. Lifecycle failures
        # are surfaced by the caller without retaining an access log.
        del format, args


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve a read-only document drop")
    parser.add_argument("port", nargs="?", type=int, default=8091)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--directory", default="/import/docs")
    parser.add_argument("--loopback-peers-only", action="store_true")
    args = parser.parse_args()

    handler_class = partial(DocDropRequestHandler, directory=args.directory)
    server = ThreadingHTTPServer((args.bind, args.port), handler_class)
    server.loopback_peers_only = args.loopback_peers_only
    try:
        print(f"Serving doc-drop on {args.bind}:{args.port}", flush=True)
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
