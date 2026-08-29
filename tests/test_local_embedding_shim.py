from __future__ import annotations

import importlib.util
import io
import json
import math
import os
import threading
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "onyx" / "local_embedding_shim.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "local_embedding_shim_test", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        os.environ,
        {
            "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL": (
                "http://host.docker.internal:3210/v1/embeddings"
            ),
            "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL": "test-model",
            "ONYX_RAG_EMBEDDING_SHIM_HTTP_PROXY_URL": (
                "http://onyx-host-egress-bridge:3128"
            ),
        },
        clear=True,
    ):
        spec.loader.exec_module(module)
    return module


class LocalEmbeddingShimReadinessTests(unittest.TestCase):
    def test_prefixes_honor_text_type_manual_override_and_escaped_newline(self) -> None:
        module = _load_module()
        with patch.object(module, "DEFAULT_QUERY_PREFIX", "default query: "), patch.object(
            module, "DEFAULT_PASSAGE_PREFIX", "default passage:\\n"
        ):
            self.assertEqual(
                module.apply_prefixes(
                    {
                        "texts": ["first", "second"],
                        "text_type": "query",
                        "manual_query_prefix": "manual query:\\n",
                    }
                ),
                (["manual query:\nfirst", "manual query:\nsecond"], "manual_query", 14),
            )
            self.assertEqual(
                module.apply_prefixes(
                    {"texts": ["document"], "text_type": "PASSAGE"}
                ),
                (["default passage:\ndocument"], "default_passage", 17),
            )
            self.assertEqual(
                module.apply_prefixes(
                    {"texts": ["unchanged"], "text_type": "UNKNOWN"}
                ),
                (["unchanged"], "none", 0),
            )

    def test_embedding_normalization_honors_onyx_request_flag(self) -> None:
        module = _load_module()
        original = [[3, 4], [0.0, -5.0]]
        self.assertIs(module.apply_embedding_normalization(original, False), original)
        normalized = module.apply_embedding_normalization(original, True)
        self.assertEqual(normalized, [[0.6, 0.8], [0.0, -1.0]])
        for vector in normalized:
            self.assertAlmostEqual(math.hypot(*vector), 1.0)
        large = module.apply_embedding_normalization([[1e308, 1e308]], True)[0]
        self.assertTrue(all(math.isfinite(value) for value in large))
        self.assertAlmostEqual(math.hypot(*large), 1.0)

        with self.assertRaisesRegex(ValueError, "expected boolean"):
            module.apply_embedding_normalization(original, "true")
        with self.assertRaisesRegex(module.UpstreamResponseError, "cannot be normalized"):
            module.apply_embedding_normalization([[0.0, 0.0]], True)

    def test_exact_internal_teep_endpoint_can_bypass_host_proxy(self) -> None:
        module = _load_module()
        pool = module.UpstreamConnectionPool(
            url="http://teep:8337/v1/embeddings",
            pool_size=1,
            timeout_seconds=17,
            pool_wait_timeout_seconds=3,
            proxy_url="",
        )
        connection = Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = b"{}"
        with patch.object(
            module.http.client, "HTTPConnection", return_value=connection
        ) as connection_class:
            pool.request("POST", b"{}", {"Content-Type": "application/json"})
        connection_class.assert_called_once_with("teep", 8337, timeout=17)
        connection.request.assert_called_once_with(
            "POST",
            "/v1/embeddings",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )

    def test_empty_proxy_is_rejected_for_every_other_endpoint(self) -> None:
        module = _load_module()
        with self.assertRaisesRegex(ValueError, "exact internal Teep"):
            module.UpstreamConnectionPool(
                url="http://host.docker.internal:8337/v1/embeddings",
                pool_size=1,
                timeout_seconds=17,
                pool_wait_timeout_seconds=3,
                proxy_url="",
            )

    def test_shim_has_bounded_upstream_timeout_and_no_post_retry(self) -> None:
        module = _load_module()
        self.assertEqual(module.HTTP_TIMEOUT_SECONDS, 540.0)
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("upstream_connection_retry", source)

        failed = Mock()
        failed.request.side_effect = OSError("connection reset")
        with patch.object(
            module.http.client,
            "HTTPConnection",
            return_value=failed,
        ) as connection_class:
            pool = module.UpstreamConnectionPool(
                url="http://host.docker.internal:3210/v1/embeddings",
                pool_size=1,
                timeout_seconds=17,
                pool_wait_timeout_seconds=3,
                proxy_url="http://onyx-host-egress-bridge:3128",
            )
            with self.assertRaisesRegex(OSError, "connection reset"):
                pool.request("POST", b"{}", {"Content-Type": "application/json"})
        failed.request.assert_called_once()
        failed.close.assert_called_once()
        self.assertEqual(connection_class.call_count, 1)

    def test_connection_creation_failure_releases_concurrency_slot(self) -> None:
        module = _load_module()
        pool = module.UpstreamConnectionPool(
            url="http://host.docker.internal:3210/v1/embeddings",
            pool_size=1,
            timeout_seconds=17,
            pool_wait_timeout_seconds=3,
            proxy_url="http://onyx-host-egress-bridge:3128",
        )
        with patch.object(pool, "_new_connection", side_effect=OSError("offline")):
            with self.assertRaisesRegex(OSError, "offline"):
                pool.request("POST", b"{}", {"Content-Type": "application/json"})
        self.assertTrue(pool._slots.acquire(blocking=False))
        pool._slots.release()

    def test_pool_wait_is_bounded(self) -> None:
        module = _load_module()
        pool = module.UpstreamConnectionPool(
            url="http://host.docker.internal:3210/v1/embeddings",
            pool_size=1,
            timeout_seconds=17,
            pool_wait_timeout_seconds=3,
            proxy_url="http://onyx-host-egress-bridge:3128",
        )
        pool._slots = Mock()
        pool._slots.acquire.return_value = False
        with self.assertRaisesRegex(TimeoutError, "pool wait"):
            pool.request("POST", b"{}", {"Content-Type": "application/json"})
        pool._slots.acquire.assert_called_once_with(timeout=3)
        pool._slots.release.assert_not_called()

    def test_health_never_requests_embeddings(self) -> None:
        module = _load_module()
        handler = self._handler(module)
        handler.path = "/health"
        with patch.object(
            module,
            "request_local_embeddings",
            side_effect=AssertionError("health must remain inference-free"),
        ):
            handler.do_GET()
        handler._send_json.assert_called_once_with(200, {"status": "ok"})

    def _handler(self, module: ModuleType):
        handler = module.Handler.__new__(module.Handler)
        handler.path = "/ready"
        handler._send_json = Mock()
        return handler

    def test_ready_requires_one_nonempty_upstream_vector(self) -> None:
        module = _load_module()
        handler = self._handler(module)
        with patch.object(
            module,
            "request_local_embeddings",
            return_value=([[0.1, 0.2]], 0.0, 1.0),
        ) as request:
            handler.do_GET()

        request.assert_called_once_with("test-model", ["readiness"])
        handler._send_json.assert_called_once_with(200, {"status": "ready"})

    def test_ready_failure_response_does_not_expose_upstream_detail(self) -> None:
        module = _load_module()
        handler = self._handler(module)
        with patch.object(
            module,
            "request_local_embeddings",
            side_effect=module.UpstreamHTTPError(401),
        ), patch.object(module, "log_line"):
            handler.do_GET()

        handler._send_json.assert_called_once_with(503, {"status": "not_ready"})

    def test_embedding_response_is_reordered_by_valid_openai_indices(self) -> None:
        module = _load_module()
        response = {
            "data": [
                {"index": 1, "embedding": [3, 4.5]},
                {"index": 0, "embedding": [1.0, 2]},
            ]
        }
        pool = Mock()
        pool.request.return_value = (200, json.dumps(response), 1.0, 2.0)
        with patch.object(module, "UPSTREAM_POOL", pool):
            embeddings, pool_wait_ms, upstream_ms = module.request_local_embeddings(
                "model", ["first", "second"]
            )
        self.assertEqual(embeddings, [[1.0, 2], [3, 4.5]])
        self.assertEqual((pool_wait_ms, upstream_ms), (1.0, 2.0))

    def test_embedding_response_validation_is_model_agnostic_and_strict(self) -> None:
        module = _load_module()
        invalid_responses = {
            "wrong count": {
                "data": [{"index": 0, "embedding": [1.0]}],
            },
            "duplicate index": {
                "data": [
                    {"index": 0, "embedding": [1.0]},
                    {"index": 0, "embedding": [2.0]},
                ],
            },
            "out of range index": {
                "data": [
                    {"index": 0, "embedding": [1.0]},
                    {"index": 2, "embedding": [2.0]},
                ],
            },
            "missing index": {
                "data": [
                    {"embedding": [1.0]},
                    {"index": 1, "embedding": [2.0]},
                ],
            },
            "empty vector": {
                "data": [
                    {"index": 0, "embedding": []},
                    {"index": 1, "embedding": []},
                ],
            },
            "inconsistent dimensions": {
                "data": [
                    {"index": 0, "embedding": [1.0]},
                    {"index": 1, "embedding": [2.0, 3.0]},
                ],
            },
            "non-finite value": {
                "data": [
                    {"index": 0, "embedding": [math.inf]},
                    {"index": 1, "embedding": [2.0]},
                ],
            },
            "boolean value": {
                "data": [
                    {"index": 0, "embedding": [True]},
                    {"index": 1, "embedding": [2.0]},
                ],
            },
            "unrepresentable integer": {
                "data": [
                    {"index": 0, "embedding": [10**400]},
                    {"index": 1, "embedding": [2.0]},
                ],
            },
        }
        for name, response in invalid_responses.items():
            with self.subTest(name=name):
                pool = Mock()
                pool.request.return_value = (200, json.dumps(response), 0.0, 1.0)
                with patch.object(module, "UPSTREAM_POOL", pool):
                    with self.assertRaises(module.UpstreamResponseError):
                        module.request_local_embeddings(
                            "any-model", ["first", "second"]
                        )

    def test_embedding_response_accepts_arbitrary_consistent_dimension(self) -> None:
        module = _load_module()
        for dimension in (1, 7, 1536):
            with self.subTest(dimension=dimension):
                response = {
                    "data": [
                        {"index": 0, "embedding": [0.25] * dimension},
                    ]
                }
                pool = Mock()
                pool.request.return_value = (200, json.dumps(response), 0.0, 1.0)
                with patch.object(module, "UPSTREAM_POOL", pool):
                    embeddings, _, _ = module.request_local_embeddings(
                        "any-model", ["text"]
                    )
                self.assertEqual(len(embeddings[0]), dimension)

    def test_embedding_endpoint_composes_prefix_and_normalization(self) -> None:
        module = _load_module()
        payload = json.dumps(
            {
                "model_name": "requested",
                "text_type": "QUERY",
                "texts": ["question"],
                "manual_query_prefix": "query:\\n",
                "normalize_embeddings": True,
            }
        ).encode()
        handler = module.Handler.__new__(module.Handler)
        handler.path = "/encoder/bi-encoder-embed"
        handler.headers = {"Content-Length": str(len(payload))}
        handler.rfile = io.BytesIO(payload)
        handler.client_address = ("127.0.0.1", 1)
        handler._send_json = Mock()

        with patch.object(
            module,
            "request_local_embeddings",
            return_value=([[3.0, 4.0]], 1.0, 2.0),
        ) as request, patch.object(module, "log_line"):
            handler.do_POST()

        request.assert_called_once_with("test-model", ["query:\nquestion"])
        handler._send_json.assert_called_once_with(
            200, {"embeddings": [[0.6, 0.8]]}
        )

    def test_upstream_response_read_is_bounded(self) -> None:
        module = _load_module()
        response = Mock(status=200)
        response.read.return_value = b"x" * 9
        connection = Mock()
        connection.getresponse.return_value = response
        pool = module.UpstreamConnectionPool(
            url="http://host.docker.internal:3210/v1/embeddings",
            pool_size=1,
            timeout_seconds=17,
            pool_wait_timeout_seconds=3,
            proxy_url="http://onyx-host-egress-bridge:3128",
        )
        with patch.object(module, "MAX_UPSTREAM_RESPONSE_BYTES", 8), patch.object(
            pool, "_new_connection", return_value=connection
        ):
            with self.assertRaises(module.UpstreamResponseError):
                pool.request("POST", b"{}", {"Content-Type": "application/json"})
        response.read.assert_called_once_with(9)

    def test_request_body_requires_length_and_obeys_size_limit(self) -> None:
        module = _load_module()
        handler = module.Handler.__new__(module.Handler)
        handler.headers = {}
        handler.rfile = io.BytesIO()
        with self.assertRaisesRegex(module.ClientRequestError, "Content-Length"):
            handler._read_json_body()

        handler.headers = {
            "Content-Length": str(module.MAX_REQUEST_BODY_BYTES + 1)
        }
        with self.assertRaisesRegex(module.ClientRequestError, "size limit"):
            handler._read_json_body()

    def test_upstream_http_error_is_scrubbed_from_response_and_log(self) -> None:
        module = _load_module()
        payload = json.dumps(
            {
                "model_name": "requested",
                "text_type": "QUERY",
                "texts": ["safe test"],
                "normalize_embeddings": True,
            }
        ).encode()
        handler = module.Handler.__new__(module.Handler)
        handler.path = "/encoder/bi-encoder-embed"
        handler.headers = {"Content-Length": str(len(payload))}
        handler.rfile = io.BytesIO(payload)
        handler.client_address = ("127.0.0.1", 1)
        handler._send_json = Mock()
        with patch.object(
            module,
            "request_local_embeddings",
            side_effect=module.UpstreamHTTPError(401),
        ), patch.object(module, "log_line") as log:
            handler.do_POST()

        handler._send_json.assert_called_once_with(
            401,
            {"detail": "Upstream embedding service returned HTTP 401"},
        )
        self.assertNotIn("secret", " ".join(str(call) for call in log.call_args_list))

    def test_server_bounds_active_threads_and_sets_socket_timeout(self) -> None:
        module = _load_module()
        server = module.BoundedThreadingHTTPServer.__new__(
            module.BoundedThreadingHTTPServer
        )
        server._request_slots = threading.BoundedSemaphore(1)
        server.shutdown_request = Mock()
        request = Mock()
        with patch.object(
            module.ThreadingHTTPServer, "process_request"
        ) as start_thread:
            server.process_request(request, ("127.0.0.1", 1))
            server.process_request(request, ("127.0.0.1", 2))
        start_thread.assert_called_once()
        server.shutdown_request.assert_called_once_with(request)
        request.settimeout.assert_called_with(module.REQUEST_SOCKET_TIMEOUT_SECONDS)

    def test_server_releases_active_request_slot(self) -> None:
        module = _load_module()
        server = module.BoundedThreadingHTTPServer.__new__(
            module.BoundedThreadingHTTPServer
        )
        server._request_slots = threading.BoundedSemaphore(1)
        self.assertTrue(server._request_slots.acquire(blocking=False))
        with patch.object(
            module.ThreadingHTTPServer, "process_request_thread"
        ) as handle_request:
            server.process_request_thread(Mock(), ("127.0.0.1", 1))
        handle_request.assert_called_once()
        self.assertTrue(server._request_slots.acquire(blocking=False))


if __name__ == "__main__":
    unittest.main()
