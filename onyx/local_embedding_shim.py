#!/usr/bin/env python3
import http.client
import json
import os
import queue
import threading
import sys
import time
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse


def log_line(message: str) -> None:
    print(message, flush=True)
    sys.stdout.flush()


def parse_positive_int_env(var_name: str, default: int) -> int:
    raw = os.environ.get(var_name)
    if raw is None or raw == "":
        return default

    try:
        value = int(raw)
    except ValueError as e:
        log_line(f"fatal_config {var_name}={raw!r} must be an integer")
        raise ValueError(f"{var_name} must be an integer") from e

    if value < 1:
        log_line(f"fatal_config {var_name}={raw!r} must be >= 1")
        raise ValueError(f"{var_name} must be >= 1")

    return value


HOST = "0.0.0.0"
PORT = 9101
EMBEDDINGS_URL = os.environ.get(
    "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL",
    "http://host.docker.internal:3210/v1/embeddings",
)
DEFAULT_MODEL = (
    os.environ.get("ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL", "").strip()
    or os.environ.get("ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL", "").strip()
)
API_KEY = os.environ.get("ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_API_KEY", "").strip()

DEFAULT_QUERY_PREFIX = os.environ.get("SHIM_QUERY_PREFIX", "")
DEFAULT_PASSAGE_PREFIX = os.environ.get("SHIM_PASSAGE_PREFIX", "")

HTTP_TIMEOUT_SECONDS = 30.0
UPSTREAM_POOL_SIZE = parse_positive_int_env("SHIM_UPSTREAM_POOL_SIZE", 8)
METRICS_LOG_EVERY = parse_positive_int_env("SHIM_METRICS_LOG_EVERY", 50)
GPU_AVAILABLE = True


class UpstreamHTTPError(Exception):
    def __init__(
        self,
        status: int,
        detail: str,
        pool_wait_ms: float | None = None,
        upstream_ms: float | None = None,
    ):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.pool_wait_ms = pool_wait_ms
        self.upstream_ms = upstream_ms


class ShimMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests = 0
        self._successes = 0
        self._upstream_errors = 0
        self._client_errors = 0
        self._sum_pool_wait_ms = 0.0
        self._sum_upstream_ms = 0.0
        self._sum_total_ms = 0.0
        self._sum_inputs = 0

    def observe(
        self,
        *,
        success: bool,
        upstream_error: bool,
        total_ms: float,
        input_count: int,
        pool_wait_ms: float | None,
        upstream_ms: float | None,
    ) -> None:
        with self._lock:
            self._requests += 1
            if success:
                self._successes += 1
            elif upstream_error:
                self._upstream_errors += 1
            else:
                self._client_errors += 1

            self._sum_total_ms += total_ms
            self._sum_inputs += input_count
            if pool_wait_ms is not None:
                self._sum_pool_wait_ms += pool_wait_ms
            if upstream_ms is not None:
                self._sum_upstream_ms += upstream_ms

            if self._requests % METRICS_LOG_EVERY != 0:
                return

            avg_total_ms = self._sum_total_ms / self._requests
            avg_pool_wait_ms = self._sum_pool_wait_ms / self._requests
            avg_upstream_ms = self._sum_upstream_ms / self._requests
            avg_inputs = self._sum_inputs / self._requests
            success_rate = self._successes / self._requests

        log_line(
            "embed_metrics"
            f" requests={self._requests}"
            f" success_rate={success_rate:.3f}"
            f" upstream_errors={self._upstream_errors}"
            f" client_errors={self._client_errors}"
            f" avg_inputs={avg_inputs:.2f}"
            f" avg_total_ms={avg_total_ms:.2f}"
            f" avg_pool_wait_ms={avg_pool_wait_ms:.2f}"
            f" avg_upstream_ms={avg_upstream_ms:.2f}"
        )


class UpstreamConnectionPool:
    def __init__(self, url: str, pool_size: int, timeout_seconds: float):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                "Unsupported ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL "
                f"scheme: {parsed.scheme}"
            )
        if not parsed.hostname:
            raise ValueError("ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL must include a hostname")

        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.target = parsed.path or "/"
        if parsed.query:
            self.target = f"{self.target}?{parsed.query}"

        self.timeout_seconds = timeout_seconds
        self._pool: queue.LifoQueue[http.client.HTTPConnection] = queue.LifoQueue(
            maxsize=pool_size
        )
        for _ in range(pool_size):
            self._pool.put(self._new_connection())

    def _new_connection(self) -> http.client.HTTPConnection:
        if self.scheme == "https":
            return http.client.HTTPSConnection(
                self.host,
                self.port,
                timeout=self.timeout_seconds,
            )
        return http.client.HTTPConnection(
            self.host,
            self.port,
            timeout=self.timeout_seconds,
        )

    def request(
        self, method: str, body: bytes, headers: dict[str, str]
    ) -> tuple[int, str, float, float]:
        max_attempts = 2
        total_pool_wait_ms = 0.0
        total_upstream_ms = 0.0

        for attempt in range(1, max_attempts + 1):
            wait_start = time.monotonic()
            connection = self._pool.get()
            pool_wait_ms = (time.monotonic() - wait_start) * 1000.0
            total_pool_wait_ms += pool_wait_ms
            replace_connection = False
            try:
                upstream_start = time.monotonic()
                connection.request(method, self.target, body=body, headers=headers)
                response = connection.getresponse()
                raw = response.read().decode("utf-8")
                upstream_ms = (time.monotonic() - upstream_start) * 1000.0
                total_upstream_ms += upstream_ms
                status = response.status
                if status >= 400:
                    raise UpstreamHTTPError(
                        status=status,
                        detail=raw,
                        pool_wait_ms=total_pool_wait_ms,
                        upstream_ms=total_upstream_ms,
                    )
                return status, raw, total_pool_wait_ms, total_upstream_ms
            except UpstreamHTTPError:
                raise
            except (OSError, TimeoutError, http.client.HTTPException) as e:
                replace_connection = True
                if attempt >= max_attempts:
                    raise
                log_line(
                    "upstream_connection_retry"
                    f" attempt={attempt}"
                    f" max_attempts={max_attempts}"
                    f" reason={e}"
                )
            finally:
                if replace_connection:
                    try:
                        connection.close()
                    except Exception:
                        pass
                    connection = self._new_connection()
                self._pool.put(connection)

        raise RuntimeError("unreachable upstream retry state")


try:
    UPSTREAM_POOL = UpstreamConnectionPool(
        url=EMBEDDINGS_URL,
        pool_size=UPSTREAM_POOL_SIZE,
        timeout_seconds=HTTP_TIMEOUT_SECONDS,
    )
except Exception as e:
    log_line(
        f"fatal_config failed to initialize upstream embedding pool url={EMBEDDINGS_URL!r} error={e}"
    )
    raise
SHIM_METRICS = ShimMetrics()


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


def request_local_embeddings(
    model_name: str, texts: list[str]
) -> tuple[list[list[float]], float, float]:
    body = {"model": model_name, "input": texts}
    data = json.dumps(body).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    _, raw, pool_wait_ms, upstream_ms = UPSTREAM_POOL.request(
        method="POST", body=data, headers=headers
    )
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

    return embeddings, pool_wait_ms, upstream_ms


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
        if self.path == "/ready":
            if not DEFAULT_MODEL:
                log_line("embedding_readiness_failed reason=missing_default_model")
                self._send_json(503, {"status": "not_ready"})
                return
            try:
                embeddings, _, _ = request_local_embeddings(
                    DEFAULT_MODEL, ["readiness"]
                )
                if len(embeddings) != 1 or not embeddings[0]:
                    raise ValueError(
                        "readiness embedding response did not contain one non-empty vector"
                    )
            except Exception as e:
                # Do not return upstream bodies or configuration values from a
                # health endpoint. The exception class is enough for operators
                # to distinguish transport, HTTP, and response-shape failures.
                log_line(
                    "embedding_readiness_failed"
                    f" reason_type={type(e).__name__}"
                )
                self._send_json(503, {"status": "not_ready"})
                return
            self._send_json(200, {"status": "ready"})
            return
        if self.path == "/api/gpu-status":
            # Onyx startup probes this endpoint before indexing setup.
            self._send_json(200, {"gpu_available": GPU_AVAILABLE})
            return
        self._send_json(404, {"detail": "Not found"})

    def do_POST(self) -> None:
        if self.path == "/encoder/cross-encoder-scores":
            content_length = int(self.headers.get("Content-Length", "0"))
            body_raw = self.rfile.read(content_length)
            payload: dict = {}
            if body_raw:
                try:
                    payload = json.loads(body_raw.decode("utf-8"))
                except Exception:
                    payload = {}

            passages = payload.get("passages")
            passage_count = len(passages) if isinstance(passages, list) else 0
            query = payload.get("query")
            query_len = len(query) if isinstance(query, str) else 0
            payload_keys = sorted(payload.keys()) if isinstance(payload, dict) else []

            log_line(
                "rerank_stub_called"
                f" remote={self.client_address[0]}"
                f" path={self.path}"
                f" query_len={query_len}"
                f" passages={passage_count}"
                f" body_bytes={content_length}"
                f" payload_keys={payload_keys}"
            )
            self._send_json(
                501,
                {
                    "detail": (
                        "Reranking endpoint is not implemented in local embedding shim. "
                        "Route this call to an Onyx model-server that supports "
                        "/encoder/cross-encoder-scores."
                    )
                },
            )
            return

        if self.path == "/custom/query-analysis":
            content_length = int(self.headers.get("Content-Length", "0"))
            body_raw = self.rfile.read(content_length)
            payload: dict = {}
            if body_raw:
                try:
                    payload = json.loads(body_raw.decode("utf-8"))
                except Exception:
                    payload = {}

            user_query = payload.get("query")
            query_len = len(user_query) if isinstance(user_query, str) else 0
            has_history = isinstance(payload.get("history"), list)
            payload_keys = sorted(payload.keys()) if isinstance(payload, dict) else []

            log_line(
                "query_analysis_stub_called"
                f" remote={self.client_address[0]}"
                f" path={self.path}"
                f" query_len={query_len}"
                f" has_history={has_history}"
                f" body_bytes={content_length}"
                f" payload_keys={payload_keys}"
            )
            self._send_json(
                501,
                {
                    "detail": (
                        "Query-analysis endpoint is not implemented in local embedding shim. "
                        "Route this call to an Onyx model-server that supports "
                        "/custom/query-analysis."
                    )
                },
            )
            return

        if self.path != "/encoder/bi-encoder-embed":
            self._send_json(404, {"detail": "Not found"})
            return

        request_start = time.monotonic()
        input_count = 0
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            body_raw = self.rfile.read(content_length)
            payload = json.loads(body_raw.decode("utf-8"))

            requested_model = str(payload.get("model_name") or "").strip() or DEFAULT_MODEL
            if not requested_model:
                raise ValueError(
                    "Missing model_name in request and ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL is not set"
                )

            # If ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL is configured, use it for upstream requests.
            # Otherwise pass through the requested model name as-is.
            upstream_model = DEFAULT_MODEL if DEFAULT_MODEL else requested_model

            text_type = str(payload.get("text_type", "")).upper()
            input_count = (
                len(payload.get("texts", []))
                if isinstance(payload.get("texts"), list)
                else 0
            )
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
            embeddings, pool_wait_ms, upstream_ms = request_local_embeddings(
                upstream_model, prefixed_texts
            )
            total_ms = (time.monotonic() - request_start) * 1000.0
            first_dim = len(embeddings[0]) if embeddings else 0
            log_line(
                "embed_success"
                f" model={upstream_model}"
                f" inputs={len(prefixed_texts)}"
                f" vectors={len(embeddings)}"
                f" dim={first_dim}"
                f" pool_wait_ms={pool_wait_ms:.2f}"
                f" upstream_ms={upstream_ms:.2f}"
                f" total_ms={total_ms:.2f}"
            )
            SHIM_METRICS.observe(
                success=True,
                upstream_error=False,
                total_ms=total_ms,
                input_count=len(prefixed_texts),
                pool_wait_ms=pool_wait_ms,
                upstream_ms=upstream_ms,
            )
            self._send_json(200, {"embeddings": embeddings})
        except UpstreamHTTPError as e:
            total_ms = (time.monotonic() - request_start) * 1000.0
            pool_wait_ms = 0.0 if e.pool_wait_ms is None else e.pool_wait_ms
            upstream_ms = 0.0 if e.upstream_ms is None else e.upstream_ms
            log_line(
                "embed_http_error"
                f" status={e.status}"
                f" pool_wait_ms={pool_wait_ms:.2f}"
                f" upstream_ms={upstream_ms:.2f}"
                f" total_ms={total_ms:.2f}"
                f" detail={e.detail[:200]}"
            )
            SHIM_METRICS.observe(
                success=False,
                upstream_error=True,
                total_ms=total_ms,
                input_count=input_count,
                pool_wait_ms=e.pool_wait_ms,
                upstream_ms=e.upstream_ms,
            )
            self._send_json(e.status, {"detail": e.detail})
        except (OSError, TimeoutError, http.client.HTTPException) as e:
            # Common path for connect/reset/timeout failures to upstream.
            reason = str(e)
            total_ms = (time.monotonic() - request_start) * 1000.0
            log_line(
                "embed_upstream_unreachable"
                f" total_ms={total_ms:.2f}"
                f" reason={reason}"
            )
            SHIM_METRICS.observe(
                success=False,
                upstream_error=True,
                total_ms=total_ms,
                input_count=input_count,
                pool_wait_ms=None,
                upstream_ms=None,
            )
            self._send_json(
                502,
                {
                    "detail": (
                        f"Failed to reach local embeddings endpoint at {EMBEDDINGS_URL}: {reason}"
                    )
                },
            )
        except Exception as e:
            total_ms = (time.monotonic() - request_start) * 1000.0
            log_line(f"embed_error total_ms={total_ms:.2f} detail={e}")
            SHIM_METRICS.observe(
                success=False,
                upstream_error=False,
                total_ms=total_ms,
                input_count=input_count,
                pool_wait_ms=None,
                upstream_ms=None,
            )
            self._send_json(400, {"detail": str(e)})


if __name__ == "__main__":
    log_line(
        f"Starting local embedding shim on {HOST}:{PORT}, upstream={EMBEDDINGS_URL}, upstream_pool_size={UPSTREAM_POOL_SIZE}, metrics_log_every={METRICS_LOG_EVERY}, gpu_available={GPU_AVAILABLE}"
    )
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()
