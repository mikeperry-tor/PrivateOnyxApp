from __future__ import annotations

import importlib.util
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "embedserv/idle_embedding_proxy.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("idle_embedding_proxy_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeChild:
    pid = 424242

    def __init__(self) -> None:
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        del timeout
        self.returncode = 0
        return 0


class IdleEmbeddingLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module()

    def test_concurrent_cold_requests_launch_one_child(self) -> None:
        launches: list[FakeChild] = []

        def popen(command, start_new_session):
            self.assertTrue(start_new_session)
            self.assertEqual(command, ["server"])
            child = FakeChild()
            launches.append(child)
            return child

        lifecycle = self.module.Lifecycle(
            ["server"], 3211, popen=popen, startup_seconds=1
        )
        lifecycle._child_healthy = lambda child: True
        barrier = threading.Barrier(3)
        failures: list[BaseException] = []

        def caller() -> None:
            try:
                barrier.wait()
                lifecycle.begin_request(self.module.time.monotonic() + 2)
                lifecycle.end_request()
            except BaseException as exc:
                failures.append(exc)

        threads = [threading.Thread(target=caller) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])
        self.assertEqual(len(launches), 1)

    def test_idle_timer_starts_after_completion_and_never_stops_active_batch(self) -> None:
        child = FakeChild()
        lifecycle = self.module.Lifecycle(["server"], 3211, idle_seconds=600)
        lifecycle._child = child
        lifecycle._active = 1
        lifecycle._last_completed = 100
        with patch.object(lifecycle, "_terminate_child") as terminate:
            self.assertFalse(lifecycle.reap_if_idle(now=1000))
            terminate.assert_not_called()
            lifecycle.end_request()
            completed = lifecycle._last_completed
            self.assertFalse(lifecycle.reap_if_idle(now=completed + 599.99))
            self.assertTrue(lifecycle.reap_if_idle(now=completed + 600))
            terminate.assert_called_once_with(child)

    def test_child_crash_causes_one_new_launch_without_post_replay_logic(self) -> None:
        first = FakeChild()
        first.returncode = 2
        second = FakeChild()
        launches = [second]
        lifecycle = self.module.Lifecycle(
            ["server"], 3211, popen=lambda *args, **kwargs: launches.pop(0)
        )
        lifecycle._child = first
        lifecycle._child_healthy = lambda child: True
        lifecycle.begin_request(self.module.time.monotonic() + 2)
        lifecycle.end_request()
        self.assertIs(lifecycle._child, second)
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertEqual(source.count('connection.request("POST", REQUEST_PATH'), 1)

    def test_proxy_surface_and_limits_are_narrow(self) -> None:
        self.assertEqual(self.module.REQUEST_PATH, "/v1/embeddings")
        self.assertEqual(self.module.OUTER_TIMEOUT_SECONDS, 30.0)
        self.assertEqual(self.module.DEFAULT_IDLE_SECONDS, 600.0)
        self.assertEqual(self.module.CHILD_STOP_GRACE_SECONDS, 15.0)
        self.assertLess(self.module.MAX_REQUEST_BYTES, self.module.MAX_RESPONSE_BYTES)
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn('ProxyServer(("0.0.0.0", args.listen_port)', source)
        self.assertIn('"--host", "127.0.0.1"', source)
        self.assertIn('self.headers.get("Transfer-Encoding")', source)


if __name__ == "__main__":
    unittest.main()
