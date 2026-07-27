from __future__ import annotations

import importlib.util
import sys
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
CLIENT_PATH = ROOT / "browser/obscura_client"
MODULE_PATH = ROOT / "searxng/engines/_obscura.py"
PATCH_PATH = ROOT / "searxng/patches/sitecustomize.py"


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


def _load_patch_module():
    bootstrap_role = types.ModuleType("bootstrap_role")
    bootstrap_role.current_process_is_resource_tracker = lambda: True
    sys.modules["bootstrap_role"] = bootstrap_role
    spec = importlib.util.spec_from_file_location(
        "test_searxng_sitecustomize_scheduling_module", PATCH_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Processor:
    def __init__(self, suspended: bool):
        self.suspended_status = SimpleNamespace(is_suspended=suspended)

    def extend_container_if_suspended(self, result_container):
        if not self.suspended_status.is_suspended:
            return False
        result_container.suspended.append(True)
        return True


def _install_scheduler_stubs(*, suspended: set[str], reservable: set[str]):
    provider_names = ("google2", "brave2", "duckduckgo2", "startpage2", "bing2")
    calls: list[str] = []

    searx = types.ModuleType("searx")
    searx.__path__ = []
    engines = types.ModuleType("searx.engines")
    engines.__path__ = []
    engines.engines = {
        name: SimpleNamespace(name=name, last_resort=name == "bing2")
        for name in provider_names
    }
    obscura = types.ModuleType("searx.engines._obscura")

    def reserve_provider(name):
        calls.append(name)
        return f"{name}-token" if name in reservable else None

    obscura.reserve_provider = reserve_provider
    engines._obscura = obscura
    search = types.ModuleType("searx.search")
    search.__path__ = []
    processors = types.ModuleType("searx.search.processors")
    processors.PROCESSORS = {
        name: _Processor(name in suspended) for name in provider_names
    }
    searx.engines = engines
    searx.search = search
    search.processors = processors
    sys.modules.update(
        {
            "searx": searx,
            "searx.engines": engines,
            "searx.engines._obscura": obscura,
            "searx.search": search,
            "searx.search.processors": processors,
        }
    )
    return calls


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

    def test_last_resort_requires_regular_providers_to_be_exhausted(self):
        source = (ROOT / "searxng/patches/sitecustomize.py").read_text()
        self.assertIn(
            "if chosen is None and not available_regular:",
            source,
        )
        self.assertNotIn(
            "if chosen is None:\n"
            "        chosen, token = _reserve_round_robin_engine(available_last_resort)",
            source,
        )
        self.assertIn(
            "if _is_last_resort_engine(engine_name) and not last_resort_eligible:",
            source,
        )

    def test_zero_result_attempt_advances_to_the_next_provider(self):
        source = (ROOT / "searxng/patches/sitecustomize.py").read_text()
        self.assertIn("failed_providers = attempted_this_round", source)
        self.assertNotIn(
            "if not failed_providers:\n"
            "                return True",
            source,
        )

    def test_busy_regular_providers_deny_last_resort_selection_and_statistics(self):
        patch = _load_patch_module()
        calls = _install_scheduler_stubs(
            suspended=set(),
            reservable={"bing2"},
        )
        refs = [SimpleNamespace(name=name) for name in patch._round_robin_providers()]

        selected, reservations = patch._round_robin_selected_refs(refs)

        self.assertEqual(selected, [])
        self.assertEqual(reservations, {})
        self.assertNotIn("bing2", calls)

        result_container = SimpleNamespace(
            suspended=[],
            unavailable=[],
            add_unresponsive_engine=lambda name, _exc: result_container.unavailable.append(
                name
            ),
        )
        patch._record_unavailable_round_robin_providers(
            engineref_list=refs,
            exclude=set(),
            result_container=result_container,
        )
        self.assertEqual(
            set(result_container.unavailable),
            {"google2", "brave2", "duckduckgo2", "startpage2"},
        )
        self.assertNotIn("bing2", result_container.unavailable)

    def test_suspended_regular_providers_allow_last_resort_selection(self):
        patch = _load_patch_module()
        regular = {"google2", "brave2", "duckduckgo2", "startpage2"}
        calls = _install_scheduler_stubs(
            suspended=regular,
            reservable={"bing2"},
        )
        refs = [SimpleNamespace(name=name) for name in patch._round_robin_providers()]

        selected, reservations = patch._round_robin_selected_refs(refs)

        self.assertEqual([ref.name for ref in selected], ["bing2"])
        self.assertEqual(reservations, {"bing2": "bing2-token"})
        self.assertEqual(calls, ["bing2"])


if __name__ == "__main__":
    unittest.main()
