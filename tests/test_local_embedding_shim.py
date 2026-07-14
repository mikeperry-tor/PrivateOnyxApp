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
