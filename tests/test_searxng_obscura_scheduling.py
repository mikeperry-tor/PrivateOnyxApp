from __future__ import annotations

import asyncio
import importlib.util
import sys
import threading
import time
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


class _PatchableSearch:
    def _get_requests(self):
        from searx.search.processors import PROCESSORS

        requests = []
        for engineref in self.search_query.engineref_list:
            processor = PROCESSORS[engineref.name]
            if processor.extend_container_if_suspended(self.result_container):
                continue
            request_params = {}
            requests.append((engineref.name, self.search_query.query, request_params))
        return requests, 60.0

    def search_standard(self):
        requests, self.actual_timeout = self._get_requests()
        if requests:
            self.search_multiple_requests(requests)
        return True

    def search_multiple_requests(self, _requests):
        raise AssertionError("test must replace search_multiple_requests")


_ORIGINAL_PATCHABLE_GET_REQUESTS = _PatchableSearch._get_requests
_ORIGINAL_PATCHABLE_SEARCH_STANDARD = _PatchableSearch.search_standard


def _reset_patchable_search() -> None:
    _PatchableSearch._get_requests = _ORIGINAL_PATCHABLE_GET_REQUESTS
    _PatchableSearch.search_standard = _ORIGINAL_PATCHABLE_SEARCH_STANDARD
    if hasattr(_PatchableSearch, "_wrapper_round_robin_patch"):
        delattr(_PatchableSearch, "_wrapper_round_robin_patch")


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
    obscura.provider_capacity_generation = lambda: 0
    obscura.wait_for_provider_capacity_change = lambda *_args: None
    obscura.release_provider_reservation = lambda *_args: None
    engines._obscura = obscura
    search = types.ModuleType("searx.search")
    search.__path__ = []
    search.default_timer = time.monotonic
    processors = types.ModuleType("searx.search.processors")
    processors.__path__ = []
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
        self.addCleanup(self.module._PROVIDER_BROWSER_LOOP.stop)

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
        with self.module.provider_lease("brave2", tokens[0]):
            self.assertTrue(self.module._PROVIDERS["brave2"].active)
        self.assertFalse(self.module._PROVIDERS["brave2"].active)

    def test_wrong_token_cannot_consume_reservation(self):
        token = self.module.reserve_provider("google2")
        self.assertIsNotNone(token)
        with self.assertRaises(SearxEngineResponseException):
            with self.module.provider_lease("google2", "wrong-token"):
                pass
        self.module.release_provider_reservation("google2", token)
        self.assertTrue(self.module.provider_available("google2"))

    def test_cooldown_is_stamped_only_by_pre_navigation_guard(self):
        state = self.module._PROVIDERS["google2"]

        with self.module.provider_lease("google2") as record_start:
            self.assertEqual(state.last_start, float("-inf"))

        self.assertEqual(state.last_start, float("-inf"))
        with self.module.provider_lease("google2") as record_start:
            self.assertTrue(record_start())

        self.assertNotEqual(state.last_start, float("-inf"))

    def test_blocking_suspension_is_recorded_before_provider_release(self):
        patch = _load_patch_module()
        patch._require_source = lambda *_args, **_kwargs: None

        class AccessDenied(RuntimeError):
            pass

        class Captcha(AccessDenied):
            pass

        class TooManyRequests(AccessDenied):
            pass

        exceptions = types.ModuleType("searx.exceptions")
        exceptions.SearxEngineAccessDeniedException = AccessDenied
        exceptions.SearxEngineCaptchaException = Captcha
        exceptions.SearxEngineResponseException = SearxEngineResponseException
        exceptions.SearxEngineTooManyRequestsException = TooManyRequests

        class OfflineProcessor:
            def search(
                self,
                query,
                params,
                result_container,
                start_time,
                timeout_limit,
            ):
                try:
                    search_results = self.engine.search(query, params)
                    self.extend_container(result_container, start_time, search_results)
                except ValueError as exc:
                    self.handle_exception(result_container, exc)

            def __init__(self):
                self.engine = SimpleNamespace(
                    name="brave2",
                    search=lambda _query, _params: (_ for _ in ()).throw(
                        TooManyRequests("blocked")
                    ),
                )
                self.logger = SimpleNamespace(exception=lambda *_args: None)
                self.suspended = False

            def extend_container(self, *_args):
                raise AssertionError("blocked result must not extend the container")

            def handle_exception(self, _container, _exc, suspend=False):
                self.suspended = suspend

        offline = types.ModuleType("searx.search.processors.offline")
        offline.OfflineProcessor = OfflineProcessor
        engines = types.ModuleType("searx.engines")
        engines.__path__ = []
        engines._obscura = self.module
        sys.modules.update(
            {
                "searx.exceptions": exceptions,
                "searx.engines": engines,
                "searx.engines._obscura": self.module,
                "searx.search.processors.offline": offline,
            }
        )
        patch.apply_offline_block_suspension_patch()

        token = self.module.reserve_provider("brave2")
        self.assertIsNotNone(token)
        processor = OfflineProcessor()
        release_observations = []
        original_changed = self.module._provider_capacity_changed
        self.module._provider_capacity_changed = lambda: release_observations.append(
            processor.suspended
        )
        self.addCleanup(
            setattr,
            self.module,
            "_provider_capacity_changed",
            original_changed,
        )

        processor.search(
            "query",
            {self.module.RESERVATION_PARAM: token},
            SimpleNamespace(),
            0.0,
            60.0,
        )

        self.assertTrue(processor.suspended)
        self.assertEqual(release_observations, [True])
        self.assertFalse(self.module._PROVIDERS["brave2"].active)

    def test_capacity_waiter_is_not_lost_between_check_and_lease_release(self):
        original_interval = self.module.MINIMUM_START_INTERVAL
        self.module.MINIMUM_START_INTERVAL = 0.0
        self.addCleanup(
            setattr,
            self.module,
            "MINIMUM_START_INTERVAL",
            original_interval,
        )
        token = self.module.reserve_provider("google2")
        self.assertIsNotNone(token)
        waiter_done = threading.Event()
        waiter_token = []

        def wait_for_capacity():
            while True:
                generation = self.module.provider_capacity_generation()
                reserved = self.module.reserve_provider("google2")
                if reserved is not None:
                    waiter_token.append(reserved)
                    waiter_done.set()
                    return
                self.module.wait_for_provider_capacity_change(
                    generation,
                    ("google2",),
                )

        with self.module.provider_lease("google2", token):
            waiter = threading.Thread(target=wait_for_capacity)
            waiter.start()
            self.assertFalse(waiter_done.wait(timeout=0.03))

        waiter.join(timeout=1.0)
        self.assertFalse(waiter.is_alive())
        self.assertEqual(len(waiter_token), 1)
        self.module.release_provider_reservation("google2", waiter_token[0])

    def test_capacity_wait_uses_exact_cooldown_deadline_without_polling(self):
        original_interval = self.module.MINIMUM_START_INTERVAL
        self.module.MINIMUM_START_INTERVAL = 0.03
        self.addCleanup(
            setattr,
            self.module,
            "MINIMUM_START_INTERVAL",
            original_interval,
        )
        state = self.module._PROVIDERS["brave2"]
        state.last_start = time.monotonic()
        generation = self.module.provider_capacity_generation()

        started = time.monotonic()
        self.module.wait_for_provider_capacity_change(
            generation,
            ("brave2",),
        )
        elapsed = time.monotonic() - started

        self.assertGreaterEqual(elapsed, 0.02)
        token = self.module.reserve_provider("brave2")
        self.assertIsNotNone(token)
        self.module.release_provider_reservation("brave2", token)

    def test_provider_browser_reuses_then_idle_expires_one_session(self):
        created = []

        class Session:
            def __init__(self):
                self.closed = False
                created.append(self)

            async def close(self):
                self.closed = True

        async def fake_fetch(_url, *, session_owner, **_kwargs):
            return session_owner

        browser = self.module._ProviderBrowserSession(
            "brave2",
            idle_seconds=0.03,
            session_factory=Session,
            fetch_async=fake_fetch,
        )
        try:
            first = browser.fetch_sync("https://search.brave.com/search?q=one")
            second = browser.fetch_sync("https://search.brave.com/search?q=two")
            self.assertIs(first, second)
            self.assertEqual(len(created), 1)

            deadline = time.monotonic() + 1.0
            while not first.closed and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertTrue(first.closed)

            third = browser.fetch_sync("https://search.brave.com/search?q=three")
            self.assertIsNot(third, first)
            self.assertEqual(len(created), 2)
        finally:
            browser.close()

    def test_distinct_provider_calls_share_one_loop_and_run_concurrently(self):
        sessions = []
        event_loop_threads = []
        active = 0
        maximum_active = 0
        active_lock = threading.Lock()

        class Session:
            async def close(self):
                return None

        async def fake_fetch(_url, *, session_owner, **_kwargs):
            nonlocal active, maximum_active
            sessions.append(session_owner)
            event_loop_threads.append(threading.get_ident())
            with active_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.02)
            with active_lock:
                active -= 1
            return session_owner

        google = self.module._ProviderBrowserSession(
            "google2",
            session_factory=Session,
            fetch_async=fake_fetch,
        )
        brave = self.module._ProviderBrowserSession(
            "brave2",
            session_factory=Session,
            fetch_async=fake_fetch,
        )
        try:
            callers = [
                threading.Thread(
                    target=browser.fetch_sync,
                    args=(url,),
                )
                for browser, url in (
                    (google, "https://www.google.com/search?q=one"),
                    (brave, "https://search.brave.com/search?q=two"),
                )
            ]
            for caller in callers:
                caller.start()
            for caller in callers:
                caller.join(timeout=1.0)

            self.assertTrue(all(not caller.is_alive() for caller in callers))
            self.assertEqual(maximum_active, 2)
            self.assertEqual(len(sessions), 2)
            self.assertIsNot(sessions[0], sessions[1])
            self.assertEqual(len(set(event_loop_threads)), 1)
        finally:
            google.close()
            brave.close()

    def test_query_at_idle_expiry_waits_for_old_connection_close(self):
        events: list[str] = []
        close_started = threading.Event()

        class Session:
            def __init__(self):
                events.append("created")

            async def close(self):
                events.append("close-start")
                close_started.set()
                await self.module_sleep()
                events.append("close-end")

            @staticmethod
            async def module_sleep():
                await asyncio.sleep(0.05)

        async def fake_fetch(_url, *, session_owner, **_kwargs):
            events.append("fetch")
            return session_owner

        browser = self.module._ProviderBrowserSession(
            "google2",
            idle_seconds=0.01,
            session_factory=Session,
            fetch_async=fake_fetch,
        )
        try:
            browser.fetch_sync("https://www.google.com/search?q=one")
            self.assertTrue(close_started.wait(timeout=1.0))

            second_done = threading.Event()

            def second_fetch():
                browser.fetch_sync("https://www.google.com/search?q=two")
                second_done.set()

            caller = threading.Thread(target=second_fetch)
            caller.start()
            caller.join(timeout=1.0)
            self.assertTrue(second_done.is_set())
            self.assertEqual(
                events[:6],
                ["created", "fetch", "close-start", "close-end", "created", "fetch"],
            )
        finally:
            browser.close()

    def test_overdue_idle_callback_cannot_revive_expired_session(self):
        events: list[str] = []

        class Session:
            def __init__(self):
                events.append("created")

            async def close(self):
                events.append("closed")

        async def fake_fetch(_url, *, session_owner, **_kwargs):
            events.append("fetch")
            return session_owner

        browser = self.module._ProviderBrowserSession(
            "duckduckgo2",
            session_factory=Session,
            fetch_async=fake_fetch,
        )
        try:
            loop = self.module._PROVIDER_BROWSER_LOOP.ensure_started()

            async def seed_overdue_session():
                browser._session = Session()
                browser._generation = 1
                browser._idle_deadline = loop.time() - 1.0
                browser._idle_handle = loop.call_later(60.0, lambda: None)

            asyncio.run_coroutine_threadsafe(seed_overdue_session(), loop).result()
            browser.fetch_sync("https://html.duckduckgo.com/html/?q=next")
            self.assertEqual(
                events,
                ["created", "closed", "created", "fetch"],
            )
        finally:
            browser.close()

    def test_wrapper_owns_matching_captcha_and_idle_durations(self):
        settings = (ROOT / "searxng/core-config/settings.yml").read_text()
        self.assertEqual(self.module.PROVIDER_SESSION_IDLE_SECONDS, 3600.0)
        self.assertIn("SearxEngineCaptcha: 3600", settings)
        self.assertIn("SearxEngineAccessDenied: 3600", settings)
        self.assertIn("SearxEngineTooManyRequests: 3600", settings)

    def test_provider_browser_owners_are_partitioned(self):
        google = self.module._provider_browser("google2")
        brave = self.module._provider_browser("brave2")
        self.assertIsNot(google, brave)
        self.assertIs(self.module._provider_browser("google2"), google)

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
        no_result_check = source.index(
            "if _has_main_results(self.result_container):"
        )
        next_provider_check = source.index(
            "if not _has_untried_round_robin_provider("
        )
        self.assertLess(no_result_check, next_provider_check)
        self.assertNotIn("_new_unresponsive_provider_names", source)

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

    def test_round_robin_never_reselects_an_attempted_provider(self):
        patch = _load_patch_module()
        _install_scheduler_stubs(
            suspended={"brave2", "duckduckgo2", "startpage2", "bing2"},
            reservable={"google2"},
        )
        refs = [SimpleNamespace(name=name) for name in patch._round_robin_providers()]

        selected, reservations = patch._round_robin_selected_refs(
            refs,
            exclude={"google2"},
        )

        self.assertEqual(selected, [])
        self.assertEqual(reservations, {})

    def test_round_robin_rotations_each_receive_one_searxng_timeout(self):
        patched_source = (
            ROOT / "searxng/patches/sitecustomize.py"
        ).read_text()
        self.assertIn(
            "self.start_time = search_mod.default_timer()",
            patched_source,
        )

    def test_executable_scheduler_waits_before_starting_provider_timeout(self):
        _reset_patchable_search()
        patch = _load_patch_module()
        patch._env_enabled = lambda *_args: True
        patch._require_source = lambda *_args, **_kwargs: None
        provider_names = ("google2", "brave2", "duckduckgo2", "startpage2", "bing2")
        searx = types.ModuleType("searx")
        searx.__path__ = []
        engines = types.ModuleType("searx.engines")
        engines.__path__ = []
        engines.engines = {
            name: SimpleNamespace(name=name, last_resort=name == "bing2")
            for name in provider_names
        }
        obscura = types.ModuleType("searx.engines._obscura")
        obscura.RESERVATION_PARAM = "_reservation"
        reservable = False
        released = []

        def reserve_provider(name):
            if name == "google2" and reservable:
                return "token"
            return None

        def wait_for_capacity(_generation, _names):
            nonlocal reservable
            time.sleep(0.03)
            reservable = True

        obscura.reserve_provider = reserve_provider
        obscura.provider_capacity_generation = lambda: 0
        obscura.wait_for_provider_capacity_change = wait_for_capacity
        obscura.release_provider_reservation = (
            lambda name, token: released.append((name, token))
        )
        engines._obscura = obscura
        search_module = types.ModuleType("searx.search")
        search_module.__path__ = []
        search_module.Search = _PatchableSearch
        search_module.default_timer = lambda: 321.0
        processors = types.ModuleType("searx.search.processors")
        processors.PROCESSORS = {
            name: _Processor(name != "google2") for name in provider_names
        }
        processors.__path__ = []
        abstract = types.ModuleType("searx.search.processors.abstract")
        abstract.EngineProcessor = SimpleNamespace(extend_container=lambda: None)
        search_module.processors = processors
        searx.engines = engines
        searx.search = search_module
        sys.modules.update(
            {
                "searx": searx,
                "searx.engines": engines,
                "searx.engines._obscura": obscura,
                "searx.search": search_module,
                "searx.search.processors": processors,
                "searx.search.processors.abstract": abstract,
            }
        )
        patch.apply_round_robin_search_patch()

        search = _PatchableSearch()
        search.search_query = SimpleNamespace(
            engineref_list=[SimpleNamespace(name=name) for name in provider_names],
            query="capacity",
        )
        unavailable = []
        search.result_container = SimpleNamespace(
            main_results_map={},
            suspended=[],
            add_unresponsive_engine=lambda *args: unavailable.append(args),
        )
        search.start_time = 100.0
        observed = []

        def execute(requests):
            observed.extend(requests)
            search.result_container.main_results_map["result"] = True

        search.search_multiple_requests = execute
        search.search_standard()

        self.assertEqual([request[0] for request in observed], ["google2"])
        self.assertEqual(observed[0][2]["_reservation"], "token")
        self.assertEqual(search.start_time, 321.0)
        self.assertEqual(released, [])
        self.assertEqual(unavailable, [])

    def test_engine_dispatch_failure_releases_reserved_provider(self):
        _reset_patchable_search()
        patch = _load_patch_module()
        patch._env_enabled = lambda *_args: True
        patch._require_source = lambda *_args, **_kwargs: None
        calls = _install_scheduler_stubs(
            suspended={"brave2", "duckduckgo2", "startpage2", "bing2"},
            reservable={"google2"},
        )
        obscura = sys.modules["searx.engines._obscura"]
        obscura.RESERVATION_PARAM = "_reservation"
        released = []
        obscura.release_provider_reservation = (
            lambda name, token: released.append((name, token))
        )
        search_module = sys.modules["searx.search"]
        search_module.Search = _PatchableSearch
        abstract = types.ModuleType("searx.search.processors.abstract")
        abstract.EngineProcessor = SimpleNamespace(extend_container=lambda: None)
        sys.modules["searx.search.processors.abstract"] = abstract
        patch.apply_round_robin_search_patch()

        search = _PatchableSearch()
        search.search_query = SimpleNamespace(
            engineref_list=[
                SimpleNamespace(name=name) for name in patch._round_robin_providers()
            ],
            query="dispatch failure",
        )
        search.result_container = SimpleNamespace(
            main_results_map={},
            suspended=[],
            add_unresponsive_engine=lambda *_args: None,
        )
        search.start_time = 100.0
        search.search_multiple_requests = lambda _requests: (_ for _ in ()).throw(
            RuntimeError("thread creation failed")
        )

        with self.assertRaisesRegex(RuntimeError, "thread creation failed"):
            search.search_standard()
        self.assertEqual(released, [("google2", "google2-token")])
        self.assertIn("google2", calls)


if __name__ == "__main__":
    unittest.main()
