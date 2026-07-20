#!/usr/bin/env python3
"""Bounded localhost-only lifecycle proxy for the bundled MLX embed server."""

from __future__ import annotations

import argparse
import http.client
import http.server
import ipaddress
import json
import os
import shlex
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable


REQUEST_PATH = "/v1/embeddings"
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
OUTER_TIMEOUT_SECONDS = 30.0
DEFAULT_IDLE_SECONDS = 600.0
DEFAULT_STARTUP_SECONDS = 20.0
CHILD_STOP_GRACE_SECONDS = 15.0
MAX_ACTIVE_CONNECTIONS = 16


class Lifecycle:
    def __init__(
        self,
        command: list[str],
        child_port: int,
        served_model_name: str,
        *,
        child_pid_file: Path | None = None,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        startup_seconds: float = DEFAULT_STARTUP_SECONDS,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        self.command = command
        self.child_port = child_port
        self.served_model_name = served_model_name
        self.child_pid_file = child_pid_file
        self.idle_seconds = idle_seconds
        self.startup_seconds = startup_seconds
        self._popen = popen
        self._require_port_available = require_port_available
        self._condition = threading.Condition()
        self._child: subprocess.Popen[bytes] | None = None
        self._starting = False
        self._stopping = False
        self._active = 0
        self._last_completed = time.monotonic()
        self._closed = False

    def _child_healthy(self, child: subprocess.Popen[bytes]) -> bool:
        if child.poll() is not None:
            return False
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.child_port, timeout=0.5
        )
        try:
            connection.request("GET", "/v1/models")
            response = connection.getresponse()
            body = response.read(64 * 1024)
            if response.status != 200:
                return False
            payload = json.loads(body)
            if not isinstance(payload, dict):
                return False
            return any(
                item.get("id") == self.served_model_name
                for item in payload.get("data", ())
                if isinstance(item, dict)
            )
        except (OSError, ValueError, TypeError):
            return False
        finally:
            connection.close()

    def begin_request(self, deadline: float) -> None:
        while True:
            with self._condition:
                if self._closed:
                    raise RuntimeError("embedding lifecycle proxy is shutting down")
                if self._child is not None and self._child.poll() is not None:
                    if self.child_pid_file is not None:
                        _remove_matching_pid_file(
                            self.child_pid_file, self._child.pid
                        )
                    self._child = None
                if self._child is not None and not self._stopping:
                    self._active += 1
                    return
                if self._starting or self._stopping:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("timed out waiting for MLX lifecycle state")
                    self._condition.wait(remaining)
                    continue
                self._starting = True

            child: subprocess.Popen[bytes] | None = None
            failure: BaseException | None = None
            try:
                self._require_port_available(self.child_port)
                child = self._popen(self.command, start_new_session=True)
                if self.child_pid_file is not None:
                    _write_pid_file(self.child_pid_file, child.pid)
                startup_deadline = min(deadline, time.monotonic() + self.startup_seconds)
                while time.monotonic() < startup_deadline:
                    if child.poll() is not None:
                        raise RuntimeError(
                            f"MLX child exited during startup with status {child.returncode}"
                        )
                    if self._child_healthy(child):
                        break
                    time.sleep(0.1)
                else:
                    raise TimeoutError("MLX child did not become healthy before startup deadline")
            except BaseException as exc:
                failure = exc

            with self._condition:
                self._starting = False
                if failure is None:
                    self._child = child
                    self._active += 1
                else:
                    self._child = None
                self._condition.notify_all()
            if failure is not None:
                if child is not None and child.poll() is None:
                    self._terminate_child(child)
                if child is not None and self.child_pid_file is not None:
                    _remove_matching_pid_file(self.child_pid_file, child.pid)
                raise failure
            return

    def end_request(self) -> None:
        with self._condition:
            if self._active <= 0:
                raise RuntimeError("embedding lifecycle active-request underflow")
            self._active -= 1
            self._last_completed = time.monotonic()
            self._condition.notify_all()

    def _terminate_child(self, child: subprocess.Popen[bytes]) -> None:
        if child.poll() is not None:
            if self.child_pid_file is not None:
                _remove_matching_pid_file(self.child_pid_file, child.pid)
            return
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=CHILD_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=CHILD_STOP_GRACE_SECONDS)
        if self.child_pid_file is not None:
            _remove_matching_pid_file(self.child_pid_file, child.pid)

    def reap_if_idle(self, now: float | None = None) -> bool:
        with self._condition:
            current = time.monotonic() if now is None else now
            child = self._child
            if (
                child is None
                or self._active
                or self._starting
                or self._stopping
                or current - self._last_completed < self.idle_seconds
            ):
                return False
            self._stopping = True
        try:
            self._terminate_child(child)
        finally:
            with self._condition:
                if self._child is child:
                    self._child = None
                self._stopping = False
                self._condition.notify_all()
        return True

    def shutdown(self) -> None:
        with self._condition:
            self._closed = True
            while self._active or self._starting or self._stopping:
                self._condition.wait(0.1)
            child = self._child
            self._child = None
        if child is not None:
            self._terminate_child(child)


def require_port_available(port: int) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))
    except OSError as exc:
        raise RuntimeError(
            f"refusing to launch MLX child: 127.0.0.1:{port} is already occupied"
        ) from exc
    finally:
        probe.close()


def _write_pid_file(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(f"{pid}\n", encoding="ascii")
    os.replace(temporary, path)


def _remove_matching_pid_file(path: Path, pid: int) -> None:
    try:
        recorded = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return
    if recorded == str(pid):
        path.unlink(missing_ok=True)


def _command_contains_expected(actual: str, expected: list[str]) -> bool:
    try:
        tokens = shlex.split(actual)
    except ValueError:
        return False
    width = len(expected)
    return any(tokens[index : index + width] == expected for index in range(len(tokens)))


def cleanup_recorded_child(command: list[str], child_pid_file: Path) -> bool:
    try:
        raw_pid = child_pid_file.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return False
    if not raw_pid.isascii() or not raw_pid.isdigit() or int(raw_pid) <= 1:
        raise RuntimeError(f"invalid MLX child PID file: {child_pid_file}")
    pid = int(raw_pid)
    inspected = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        check=False,
        capture_output=True,
        text=True,
    )
    actual = inspected.stdout.strip()
    if inspected.returncode != 0 or not actual:
        child_pid_file.unlink(missing_ok=True)
        return False
    if not _command_contains_expected(actual, command):
        raise RuntimeError(
            f"recorded MLX child PID {pid} has a different command; leaving it untouched"
        )
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        child_pid_file.unlink(missing_ok=True)
        return False
    deadline = time.monotonic() + CHILD_STOP_GRACE_SECONDS
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            child_pid_file.unlink(missing_ok=True)
            return True
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        child_pid_file.unlink(missing_ok=True)
        return True
    kill_deadline = time.monotonic() + 5
    while time.monotonic() < kill_deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            child_pid_file.unlink(missing_ok=True)
            return True
        time.sleep(0.1)
    raise RuntimeError(f"recorded MLX child PID {pid} survived SIGKILL")


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    server: "ProxyServer"

    def _reject_non_loopback_client(self) -> bool:
        try:
            allowed = ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            allowed = False
        if allowed:
            return False
        self.send_error(403, "Host-local access only")
        return True

    def do_POST(self) -> None:  # noqa: N802
        if self._reject_non_loopback_client():
            return
        if self.path != REQUEST_PATH:
            self.send_error(404)
            return
        if self.headers.get("Transfer-Encoding") is not None:
            self.send_error(400, "Transfer-Encoding is not supported")
            return
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "")
        except ValueError:
            self.send_error(411, "a valid Content-Length is required")
            return
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self.send_error(413, "embedding request body is outside the supported bound")
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self.send_error(400, "incomplete embedding request body")
            return

        deadline = time.monotonic() + OUTER_TIMEOUT_SECONDS
        begun = False
        try:
            self.server.lifecycle.begin_request(deadline)
            begun = True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("embedding deadline expired before forwarding")
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.server.lifecycle.child_port, timeout=remaining
            )
            headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
            if authorization := self.headers.get("Authorization"):
                headers["Authorization"] = authorization
            # Exactly one forward: a POST is never replayed after ambiguous delivery.
            connection.request("POST", REQUEST_PATH, body=body, headers=headers)
            response = connection.getresponse()
            response_body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(response_body) > MAX_RESPONSE_BYTES:
                raise RuntimeError("MLX response exceeded the supported bound")
            self.send_response(response.status)
            self.send_header(
                "Content-Type", response.getheader("Content-Type", "application/json")
            )
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)
            connection.close()
        except (OSError, RuntimeError, TimeoutError) as exc:
            payload = json.dumps(
                {"error": {"message": f"local MLX embedding backend unavailable: {type(exc).__name__}"}}
            ).encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        finally:
            if begun:
                self.server.lifecycle.end_request()

    def do_GET(self) -> None:  # noqa: N802
        if self._reject_non_loopback_client():
            return
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class ProxyServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        lifecycle: Lifecycle,
        *,
        max_active_connections: int = MAX_ACTIVE_CONNECTIONS,
    ) -> None:
        if max_active_connections <= 0:
            raise ValueError("max_active_connections must be positive")
        self.lifecycle = lifecycle
        self._request_slots = threading.BoundedSemaphore(max_active_connections)
        super().__init__(address, ProxyHandler)

    def process_request(
        self, request: socket.socket, client_address: tuple[str, int]
    ) -> None:
        if not self._request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(
        self, request: socket.socket, client_address: tuple[str, int]
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port", type=int, default=3210)
    parser.add_argument("--child-port", type=int, default=3211)
    parser.add_argument("--server-executable", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--served-model-name", required=True)
    parser.add_argument("--child-pid-file", type=Path, required=True)
    parser.add_argument("--cleanup-recorded-child", action="store_true")
    args = parser.parse_args()
    if args.listen_port == args.child_port:
        raise RuntimeError("proxy and child ports must differ")
    command = [
        str(args.server_executable), "launch",
        "--model-type", "embeddings",
        "--model-path", str(args.model_path),
        "--served-model-name", args.served_model_name,
        "--host", "127.0.0.1",
        "--port", str(args.child_port),
        "--no-log-file",
    ]
    if args.cleanup_recorded_child:
        cleanup_recorded_child(command, args.child_pid_file)
        return
    version = subprocess.run(
        [str(args.server_executable), "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if "Version: 1.8.1" not in version:
        raise RuntimeError("mlx-openai-server version drifted from 1.8.1")
    lifecycle = Lifecycle(
        command,
        args.child_port,
        args.served_model_name,
        child_pid_file=args.child_pid_file,
    )
    # Docker Desktop reaches the wrapper-owned proxy through
    # host.docker.internal. Only the heavy MLX child is loopback-only.
    server = ProxyServer(("0.0.0.0", args.listen_port), lifecycle)
    try:
        cleanup_recorded_child(command, args.child_pid_file)
        require_port_available(args.child_port)
    except BaseException:
        server.server_close()
        raise
    stop = threading.Event()

    def _shutdown(_signum: int, _frame: object) -> None:
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    def _reaper() -> None:
        while not stop.wait(5):
            lifecycle.reap_if_idle()

    threading.Thread(target=_reaper, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        lifecycle.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
