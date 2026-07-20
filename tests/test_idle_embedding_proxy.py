from __future__ import annotations

import importlib.util
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


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
            ["server"], 3211, "expected-model", popen=popen, startup_seconds=1
        )
        lifecycle._child_healthy = lambda child: True
        lifecycle._require_port_available = lambda port: None
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
        lifecycle = self.module.Lifecycle(
            ["server"], 3211, "expected-model", idle_seconds=600
        )
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
            ["server"],
            3211,
            "expected-model",
            popen=lambda *args, **kwargs: launches.pop(0),
        )
        lifecycle._child = first
        lifecycle._child_healthy = lambda child: True
        lifecycle._require_port_available = lambda port: None
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

    def test_occupied_child_port_fails_before_launch(self) -> None:
        launches = []
        lifecycle = self.module.Lifecycle(
            ["server"],
            3211,
            "expected-model",
            popen=lambda *args, **kwargs: launches.append(args),
        )
        lifecycle._require_port_available = lambda port: (_ for _ in ()).throw(
            RuntimeError(f"127.0.0.1:{port} is already occupied")
        )
        with self.assertRaisesRegex(RuntimeError, "already occupied"):
            lifecycle.begin_request(self.module.time.monotonic() + 1)
        self.assertEqual(launches, [])

    def test_readiness_requires_expected_served_model(self) -> None:
        lifecycle = self.module.Lifecycle(["server"], 3211, "expected-model")
        child = FakeChild()
        response = MagicMock()
        response.status = 200
        response.read.return_value = b'{"data":[{"id":"other-model"}]}'
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch.object(
            self.module.http.client, "HTTPConnection", return_value=connection
        ):
            self.assertFalse(lifecycle._child_healthy(child))
        response.read.return_value = b'{"data":[{"id":"expected-model"}]}'
        with patch.object(
            self.module.http.client, "HTTPConnection", return_value=connection
        ):
            self.assertTrue(lifecycle._child_healthy(child))
        connection.request.assert_called_with("GET", "/v1/models")

    def test_stale_child_cleanup_is_identity_safe(self) -> None:
        command = ["/venv/mlx-openai-server", "launch", "--port", "3211"]
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "child.pid"
            pid_file.write_text("424242\n", encoding="ascii")
            inspected = MagicMock()
            inspected.returncode = 0
            inspected.stdout = "python /different/server launch --port 3211\n"
            with patch.object(self.module.subprocess, "run", return_value=inspected):
                with self.assertRaisesRegex(RuntimeError, "different command"):
                    self.module.cleanup_recorded_child(command, pid_file)
            self.assertTrue(pid_file.exists())

    def test_proxy_crash_record_cleans_matching_child_group(self) -> None:
        command = ["/venv/mlx-openai-server", "launch", "--port", "3211"]
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "child.pid"
            pid_file.write_text("424242\n", encoding="ascii")
            inspected = MagicMock()
            inspected.returncode = 0
            inspected.stdout = (
                "python /venv/mlx-openai-server launch --port 3211\n"
            )
            with (
                patch.object(self.module.subprocess, "run", return_value=inspected),
                patch.object(self.module.os, "killpg") as kill_group,
                patch.object(
                    self.module.os, "kill", side_effect=ProcessLookupError
                ),
            ):
                self.assertTrue(
                    self.module.cleanup_recorded_child(command, pid_file)
                )
            kill_group.assert_called_once_with(424242, self.module.signal.SIGTERM)
            self.assertFalse(pid_file.exists())


if __name__ == "__main__":
    unittest.main()
