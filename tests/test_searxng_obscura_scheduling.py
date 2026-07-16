from __future__ import annotations

import importlib.util
import sys
import threading
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT_PATH = ROOT / "browser/obscura_client"
MODULE_PATH = ROOT / "searxng/engines/_obscura.py"


class SearxEngineResponseException(RuntimeError):
    pass


def _load_module():
    searx = types.ModuleType("searx")
    exceptions = types.ModuleType("searx.exceptions")
    exceptions.SearxEngineResponseException = SearxEngineResponseException
    sys.modules["searx"] = searx
    sys.modules["searx.exceptions"] = exceptions
    sys.path.insert(0, str(CLIENT_PATH))
    try:
        spec = importlib.util.spec_from_file_location(
            "test_searxng_obscura_scheduling_module", MODULE_PATH
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(CLIENT_PATH))


class SearxngObscuraSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()

    def test_reservation_is_atomic_before_engine_lease(self):
        tokens: list[str] = []
        lock = threading.Lock()

        def reserve():
            token = self.module.reserve_provider("brave2")
            if token is not None:
                with lock:
                    tokens.append(token)

        threads = [threading.Thread(target=reserve) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(tokens), 1)
        self.assertFalse(self.module.provider_available("brave2"))
        with self.module._lease("brave2", tokens[0]):
            self.assertTrue(self.module._PROVIDERS["brave2"].active)
        self.assertFalse(self.module._PROVIDERS["brave2"].active)

    def test_wrong_token_cannot_consume_reservation(self):
        token = self.module.reserve_provider("google2")
        self.assertIsNotNone(token)
        with self.assertRaises(SearxEngineResponseException):
            with self.module._lease("google2", "wrong-token"):
                pass
        self.module.release_provider_reservation("google2", token)
        self.assertTrue(self.module.provider_available("google2"))

    def test_scheduler_source_does_not_fan_out_when_no_provider_reserves(self):
        source = (ROOT / "searxng/patches/sitecustomize.py").read_text()
        self.assertIn("return [], {}", source)
        self.assertIn(
            "requests, actual_timeout = original_get_requests(self)", source
        )
        self.assertIn("return requests, actual_timeout", source)
        self.assertIn(
            '"searx.exceptions.SearxEngineTooManyRequestsException"', source
        )
        self.assertNotIn(
            "return [first_ref_by_name[name] for name in candidate_provider_order]",
            source,
        )


if __name__ == "__main__":
    unittest.main()
