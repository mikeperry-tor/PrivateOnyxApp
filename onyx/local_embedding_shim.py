#!/usr/bin/env python3
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import Request
from urllib.request import urlopen


HOST = "0.0.0.0"
PORT = 9101
EMBEDDINGS_URL = os.environ.get(
    "LOCAL_EMBEDDINGS_URL", "http://host.docker.internal:1234/v1/embeddings"
)
DEFAULT_MODEL = os.environ.get("LOCAL_EMBEDDING_MODEL", "").strip()
API_KEY = os.environ.get("LOCAL_EMBEDDING_API_KEY", "").strip()

DEFAULT_QUERY_PREFIX = os.environ.get("SHIM_QUERY_PREFIX", "")
DEFAULT_PASSAGE_PREFIX = os.environ.get("SHIM_PASSAGE_PREFIX", "")

HTTP_TIMEOUT_SECONDS = 30.0


def log_line(message: str) -> None:
    print(message, flush=True)
    sys.stdout.flush()


def normalize_prefix(prefix: str | None) -> str:
    if not prefix:
        return ""
    # Admin/UIs often store escaped newline sequences.
    return prefix.replace("\\n", "\n")


def apply_prefixes(payload: dict) -> tuple[list[str], str, int]:
    texts = payload.get("texts")
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
        raise ValueError("Invalid 'texts' field; expected list[str]")

    text_type = str(payload.get("text_type", "")).upper()
    manual_query_prefix = payload.get("manual_query_prefix")
    manual_passage_prefix = payload.get("manual_passage_prefix")

    prefix = ""
    prefix_source = "none"
    if text_type == "QUERY":
        normalized_manual = normalize_prefix(manual_query_prefix)
        normalized_default = normalize_prefix(DEFAULT_QUERY_PREFIX)
        if normalized_manual:
            prefix = normalized_manual
            prefix_source = "manual_query"
        else:
            prefix = normalized_default
            if normalized_default:
                prefix_source = "default_query"
    elif text_type == "PASSAGE":
        normalized_manual = normalize_prefix(manual_passage_prefix)
        normalized_default = normalize_prefix(DEFAULT_PASSAGE_PREFIX)
        if normalized_manual:
            prefix = normalized_manual
            prefix_source = "manual_passage"
        else:
            prefix = normalized_default
            if normalized_default:
                prefix_source = "default_passage"

    if not prefix:
        return texts, prefix_source, 0

    return [f"{prefix}{t}" for t in texts], prefix_source, len(prefix)


def request_local_embeddings(model_name: str, texts: list[str]) -> list[list[float]]:
    body = {"model": model_name, "input": texts}
    data = json.dumps(body).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    req = Request(EMBEDDINGS_URL, data=data, headers=headers, method="POST")

    with urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
        raw = response.read().decode("utf-8")
        parsed = json.loads(raw)

    items = parsed.get("data")
    if not isinstance(items, list):
        raise ValueError("Embedding response missing 'data' list")

    embeddings: list[list[float]] = []
    for item in items:
        embedding = item.get("embedding") if isinstance(item, dict) else None
        if not isinstance(embedding, list):
            raise ValueError("Embedding response item missing 'embedding' vector")
        embeddings.append(embedding)

    return embeddings


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: object) -> None:
        # Keep concise stdout logs for container visibility.
        log_line(f"shim {self.address_string()} - {format % args}")

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        self._send_json(404, {"detail": "Not found"})

    def do_POST(self) -> None:
        if self.path != "/encoder/bi-encoder-embed":
            self._send_json(404, {"detail": "Not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            body_raw = self.rfile.read(content_length)
            payload = json.loads(body_raw.decode("utf-8"))

            requested_model = str(payload.get("model_name") or "").strip() or DEFAULT_MODEL
            if not requested_model:
                raise ValueError(
                    "Missing model_name in request and LOCAL_EMBEDDING_MODEL is not set"
                )

            # If LOCAL_EMBEDDING_MODEL is configured, use it for upstream requests.
            # Otherwise pass through the requested model name as-is.
            upstream_model = DEFAULT_MODEL if DEFAULT_MODEL else requested_model

            text_type = str(payload.get("text_type", "")).upper()
            input_count = len(payload.get("texts", [])) if isinstance(payload.get("texts"), list) else 0
            prefixed_texts, prefix_source, prefix_len = apply_prefixes(payload)
            log_line(
                "embed_request"
                f" text_type={text_type}"
                f" requested_model={requested_model}"
                f" upstream_model={upstream_model}"
                f" inputs={input_count}"
                f" prefix_source={prefix_source}"
                f" prefix_len={prefix_len}"
            )
            embeddings = request_local_embeddings(upstream_model, prefixed_texts)
            first_dim = len(embeddings[0]) if embeddings else 0
            log_line(
                "embed_success"
                f" model={upstream_model}"
                f" inputs={len(prefixed_texts)}"
                f" vectors={len(embeddings)}"
                f" dim={first_dim}"
            )
            self._send_json(200, {"embeddings": embeddings})
        except HTTPError as e:
            try:
                detail = e.read().decode("utf-8")
            except Exception:
                detail = str(e)
            log_line(f"embed_http_error status={e.code} detail={detail[:200]}")
            self._send_json(e.code, {"detail": detail})
        except URLError as e:
            log_line(f"embed_upstream_unreachable reason={e.reason}")
            self._send_json(
                502,
                {
                    "detail": (
                        f"Failed to reach local embeddings endpoint at {EMBEDDINGS_URL}: {e.reason}"
                    )
                },
            )
        except Exception as e:
            log_line(f"embed_error detail={e}")
            self._send_json(400, {"detail": str(e)})


if __name__ == "__main__":
    log_line(
        f"Starting local embedding shim on {HOST}:{PORT}, upstream={EMBEDDINGS_URL}"
    )
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()
