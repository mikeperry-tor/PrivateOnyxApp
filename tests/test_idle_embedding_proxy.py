from __future__ import annotations

import importlib.util
import sys
import tempfile
import threading
import time
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
            ["server"], 3211, "expected-model", popen=popen
        )
        lifecycle._child_healthy = lambda child: True
        lifecycle._require_port_available = lambda port: None
        barrier = threading.Barrier(3)
        failures: list[BaseException] = []

        def caller() -> None:
            try:
                barrier.wait()
                lifecycle.begin_request()
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
        lifecycle.begin_request()
        lifecycle.end_request()
        self.assertIs(lifecycle._child, second)
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertEqual(source.count('connection.request("POST", REQUEST_PATH'), 1)

    def test_proxy_surface_and_limits_are_narrow(self) -> None:
        self.assertEqual(self.module.REQUEST_PATH, "/v1/embeddings")
        self.assertEqual(self.module.DEFAULT_IDLE_SECONDS, 600.0)
        self.assertEqual(self.module.CHILD_STOP_GRACE_SECONDS, 15.0)
        self.assertEqual(self.module.CHILD_REQUEST_TIMEOUT_SECONDS, 300.0)
        self.assertEqual(self.module.MAX_ACTIVE_CONNECTIONS, 16)
        self.assertLess(self.module.MAX_REQUEST_BYTES, self.module.MAX_RESPONSE_BYTES)
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("OUTER_TIMEOUT_SECONDS", source)
        self.assertNotIn("DEFAULT_STARTUP_SECONDS", source)
        self.assertNotIn("_interrupt_idle_stop", source)
        self.assertIn('ProxyServer(("0.0.0.0", args.listen_port)', source)
        self.assertIn('"--host", "127.0.0.1"', source)
        self.assertIn('self.headers.get("Transfer-Encoding")', source)

    def test_non_loopback_peer_is_rejected_before_model_work(self) -> None:
        handler = self.module.ProxyHandler.__new__(self.module.ProxyHandler)
        handler.client_address = ("192.0.2.10", 12345)
        handler.path = self.module.REQUEST_PATH
        handler.send_error = MagicMock()
        handler.server = MagicMock()
        handler.rfile = MagicMock()
        handler.do_POST()
        handler.send_error.assert_called_once_with(403, "Host-local access only")
        handler.server.lifecycle.begin_request.assert_not_called()
        handler.rfile.read.assert_not_called()

    def test_loopback_peer_reaches_narrow_get_surface(self) -> None:
        handler = self.module.ProxyHandler.__new__(self.module.ProxyHandler)
        handler.client_address = ("127.0.0.1", 12345)
        handler.send_error = MagicMock()
        handler.do_GET()
        handler.send_error.assert_called_once_with(404)

    def test_connection_limit_rejects_before_starting_another_thread(self) -> None:
        server = self.module.ProxyServer.__new__(self.module.ProxyServer)
        server._request_slots = threading.BoundedSemaphore(1)
        server.shutdown_request = MagicMock()
        request = MagicMock()
        with patch.object(
            self.module.http.server.ThreadingHTTPServer,
            "process_request",
        ) as start_thread:
            server.process_request(request, ("127.0.0.1", 1))
            server.process_request(request, ("127.0.0.1", 2))
        start_thread.assert_called_once()
        server.shutdown_request.assert_called_once_with(request)

    def test_non_loopback_peer_is_rejected_before_request_thread(self) -> None:
        server = self.module.ProxyServer.__new__(self.module.ProxyServer)
        self.assertTrue(server.verify_request(MagicMock(), ("127.0.0.1", 1)))
        self.assertFalse(server.verify_request(MagicMock(), ("192.0.2.10", 1)))

    def test_accepted_socket_gets_bounded_idle_timeout(self) -> None:
        server = self.module.ProxyServer.__new__(self.module.ProxyServer)
        server._request_slots = threading.BoundedSemaphore(1)
        server.shutdown_request = MagicMock()
        request = MagicMock()
        with patch.object(
            self.module.http.server.ThreadingHTTPServer,
            "process_request",
        ):
            server.process_request(request, ("127.0.0.1", 1))
        request.settimeout.assert_called_once_with(
            self.module.REQUEST_SOCKET_TIMEOUT_SECONDS
        )

    def test_accepted_request_threads_are_drained_on_server_close(self) -> None:
        self.assertFalse(self.module.ProxyServer.daemon_threads)

    def test_connection_slot_is_released_after_thread_completion(self) -> None:
        server = self.module.ProxyServer.__new__(self.module.ProxyServer)
        server._request_slots = threading.BoundedSemaphore(1)
        self.assertTrue(server._request_slots.acquire(blocking=False))
        with patch.object(
            self.module.http.server.ThreadingHTTPServer,
            "process_request_thread",
        ) as handle_request:
            server.process_request_thread(MagicMock(), ("127.0.0.1", 1))
        handle_request.assert_called_once()
        self.assertTrue(server._request_slots.acquire(blocking=False))

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
            lifecycle.begin_request()
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
            with self.assertRaisesRegex(RuntimeError, "configured served model"):
                lifecycle._child_healthy(child)
        response.read.return_value = b'{"data":[{"id":"expected-model"}]}'
        with patch.object(
            self.module.http.client, "HTTPConnection", return_value=connection
        ):
            self.assertTrue(lifecycle._child_healthy(child))
        connection.request.assert_called_with("GET", "/v1/models")

    def test_child_ownership_is_in_memory_only(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("child-pid-file", source)
        self.assertNotIn("cleanup_recorded_child", source)
        self.assertNotIn('["ps",', source)

    def test_orphaned_child_port_fails_before_top_level_listener(self) -> None:
        argv = [
            str(MODULE_PATH),
            "--listen-port",
            "3210",
            "--child-port",
            "3211",
            "--server-executable",
            "/venv/mlx-openai-server",
            "--model-path",
            "/models/embedding",
            "--served-model-name",
            "expected-model",
        ]
        version = MagicMock(stdout="Version: 1.8.1\n")
        occupied = RuntimeError("127.0.0.1:3211 is already occupied")
        with patch.object(sys, "argv", argv), patch.object(
            self.module.subprocess, "run", return_value=version
        ), patch.object(
            self.module, "require_port_available", side_effect=occupied
        ) as port_check, patch.object(
            self.module, "ProxyServer"
        ) as proxy_server, patch.object(
            self.module.os, "killpg"
        ) as kill_group:
            with self.assertRaisesRegex(RuntimeError, "3211 is already occupied"):
                self.module.main()
        port_check.assert_called_once_with(3211)
        proxy_server.assert_not_called()
        kill_group.assert_not_called()

    def test_request_racing_idle_stop_waits_then_relaunches(self) -> None:
        old_child = FakeChild()
        new_child = FakeChild()
        lifecycle = self.module.Lifecycle(
            ["server"],
            3211,
            "expected-model",
            popen=lambda *args, **kwargs: new_child,
            idle_seconds=0,
        )
        lifecycle._child = old_child
        lifecycle._last_completed = 0
        lifecycle._child_healthy = lambda child: True
        lifecycle._require_port_available = lambda port: None
        stop_entered = threading.Event()
        stop_released = threading.Event()

        def terminate(child):
            self.assertIs(child, old_child)
            stop_entered.set()
            self.assertTrue(stop_released.wait(2))
            child.returncode = 0

        lifecycle._terminate_child = terminate
        reaper = threading.Thread(target=lifecycle.reap_if_idle)
        reaper.start()
        self.assertTrue(stop_entered.wait(1))
        completed: list[bool] = []
        failures: list[BaseException] = []

        def request() -> None:
            try:
                lifecycle.begin_request()
                completed.append(True)
                lifecycle.end_request()
            except BaseException as exc:
                failures.append(exc)

        caller = threading.Thread(target=request)
        caller.start()
        time.sleep(0.02)
        self.assertTrue(caller.is_alive())
        stop_released.set()
        reaper.join(2)
        caller.join(2)
        self.assertEqual(failures, [])
        self.assertEqual(completed, [True])
        self.assertIs(lifecycle._child, new_child)

    def test_startup_waits_while_child_is_alive_without_short_deadline(self) -> None:
        child = FakeChild()
        lifecycle = self.module.Lifecycle(
            ["server"],
            3211,
            "expected-model",
            popen=lambda *args, **kwargs: child,
        )
        health_results = iter((False, False, True))
        lifecycle._child_healthy = lambda owned: next(health_results)
        lifecycle._require_port_available = lambda port: None
        with patch.object(self.module.time, "sleep") as sleep:
            lifecycle.begin_request()
        self.assertEqual(sleep.call_count, 2)
        lifecycle.end_request()

    def test_proxy_shutdown_can_cancel_an_indefinite_cold_start(self) -> None:
        child = FakeChild()
        lifecycle = self.module.Lifecycle(
            ["server"],
            3211,
            "expected-model",
            popen=lambda *args, **kwargs: child,
        )
        lifecycle._child_healthy = lambda owned: False
        lifecycle._require_port_available = lambda port: None
        lifecycle._terminate_child = lambda owned: setattr(owned, "returncode", 0)
        entered_sleep = threading.Event()

        def wait_for_shutdown(_seconds):
            entered_sleep.set()
            lifecycle._shutdown_requested.wait(1)

        failure: list[BaseException] = []

        def start() -> None:
            try:
                lifecycle.begin_request()
            except BaseException as exc:
                failure.append(exc)

        with patch.object(self.module.time, "sleep", side_effect=wait_for_shutdown):
            starter = threading.Thread(target=start)
            starter.start()
            self.assertTrue(entered_sleep.wait(1))
            lifecycle.request_shutdown()
            starter.join(1)
        self.assertFalse(starter.is_alive())
        self.assertEqual(len(failure), 1)
        self.assertIn("canceled by proxy shutdown", str(failure[0]))

    def test_lifecycle_shutdown_waits_for_active_request(self) -> None:
        child = FakeChild()
        lifecycle = self.module.Lifecycle(["server"], 3211, "expected-model")
        lifecycle._child = child
        lifecycle._active = 1
        terminated = threading.Event()
        lifecycle._terminate_child = lambda owned: terminated.set()
        shutdown = threading.Thread(target=lifecycle.shutdown)
        shutdown.start()
        time.sleep(0.02)
        self.assertTrue(shutdown.is_alive())
        self.assertFalse(terminated.is_set())
        lifecycle.end_request()
        shutdown.join(1)
        self.assertFalse(shutdown.is_alive())
        self.assertTrue(terminated.is_set())


if __name__ == "__main__":
    unittest.main()
