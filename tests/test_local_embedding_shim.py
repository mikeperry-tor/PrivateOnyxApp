from __future__ import annotations

import importlib.util
import os
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
    def test_shim_has_no_independent_upstream_timeout_or_post_retry(self) -> None:
        module = _load_module()
        self.assertIsNone(module.HTTP_TIMEOUT_SECONDS)
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
                timeout_seconds=None,
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
            timeout_seconds=None,
            proxy_url="http://onyx-host-egress-bridge:3128",
        )
        with patch.object(pool, "_new_connection", side_effect=OSError("offline")):
            with self.assertRaisesRegex(OSError, "offline"):
                pool.request("POST", b"{}", {"Content-Type": "application/json"})
        self.assertTrue(pool._slots.acquire(blocking=False))
        pool._slots.release()

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
            side_effect=module.UpstreamHTTPError(401, "secret upstream body"),
        ), patch.object(module, "log_line"):
            handler.do_GET()

        handler._send_json.assert_called_once_with(503, {"status": "not_ready"})


if __name__ == "__main__":
    unittest.main()
