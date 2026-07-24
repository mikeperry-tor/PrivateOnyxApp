#!/usr/bin/env python3
"""Manage the wrapper's optional full-mode host processes.

This shared, stdlib-only lifecycle helper lives with the bundled MLX embedding
server, but it also manages the Podman-only host document server. It is not
used by lite mode, by Docker's containerized document server, or by a clean
full-mode Docker start whose embedding shim targets Teep or another custom
upstream. After custom policy and embedding readiness succeed, a configuration
change may use it to stop a previously wrapper-managed MLX process.

Service-specific policy remains in the managed services: this module owns only
detached launch, atomic PID/token/configuration records, readiness waits, and
identity-checked stops.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


TOKEN_PATTERN = re.compile(r"[0-9a-f]{64}")


class ContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class Record:
    pid: int
    token: str
    config_id: str


def _read_record(path: Path) -> Record | None:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except FileNotFoundError:
        return None
    if len(lines) != 3 or not lines[0].isdigit() or not TOKEN_PATTERN.fullmatch(lines[1]):
        return None
    return Record(int(lines[0]), lines[1], lines[2])


def _write_record(path: Path, record: Record) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        f"{record.pid}\n{record.token}\n{record.config_id}\n", encoding="ascii"
    )
    temporary.replace(path)


def _command_line(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _owned(record: Record, identity: str) -> bool:
    command = _command_line(record.pid)
    return bool(
        command
        and identity in command
        and f"--owner-token {record.token}" in command
    )


def _running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _tcp_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _ready(port: int, health_path: str | None) -> bool:
    if health_path is None:
        return _tcp_ready(port)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        connection.request("GET", health_path)
        response = connection.getresponse()
        response.read()
        return response.status == 200
    except OSError:
        return False
    finally:
        connection.close()


def _config_id(command: Sequence[str], fingerprint_files: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for item in command:
        digest.update(item.encode())
        digest.update(b"\0")
    for path in fingerprint_files:
        digest.update(str(path).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _wait_stopped(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while _running(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    return not _running(pid)


def _stop_owned(record: Record, identity: str, timeout: float) -> None:
    if not _owned(record, identity):
        raise ContractError(f"PID {record.pid} does not match the recorded service identity")
    os.kill(record.pid, signal.SIGTERM)
    if not _wait_stopped(record.pid, timeout):
        raise ContractError(f"PID {record.pid} did not stop within {timeout:g} seconds")


def start(args: argparse.Namespace) -> None:
    command = list(args.command)
    if command[:1] == ["--"]:
        command.pop(0)
    if not command:
        raise ContractError("a service command is required after --")
    config_id = _config_id(command, args.fingerprint_file)
    had_record = args.record_file.exists()
    record = _read_record(args.record_file)
    if had_record and record is None:
        raise ContractError(f"{args.record_file} is malformed; refusing to guess ownership")

    if record is not None and _owned(record, args.identity):
        if record.config_id == config_id:
            if _ready(args.port, args.health_path):
                print(f"{args.name} is already ready on port {args.port}")
                return
            raise ContractError(
                f"tracked {args.name} PID {record.pid} is running but not ready; stop it first"
            )
        print(f"Restarting tracked {args.name} after configuration changed")
        _stop_owned(record, args.identity, args.stop_timeout)
        args.record_file.unlink(missing_ok=True)
    elif record is not None:
        if _running(record.pid) or _tcp_ready(args.port):
            raise ContractError(
                f"recorded {args.name} identity does not match; no process was signaled"
            )
        args.record_file.unlink(missing_ok=True)

    if _tcp_ready(args.port):
        raise ContractError(f"port {args.port} is owned by an untracked process")

    if not any(args.identity in item for item in command):
        raise ContractError("service identity must be present in the launched command")

    for path in args.require_executable:
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ContractError(f"required executable is unavailable: {path}")
    for path in args.require_directory:
        if not path.is_dir():
            raise ContractError(f"required directory is unavailable: {path}")

    token = secrets.token_hex(32)
    launched_command = [*command, "--owner-token", token]
    args.log_file.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if args.truncate_log else "ab"
    with args.log_file.open(mode, buffering=0) as log:
        process = subprocess.Popen(
            launched_command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    _write_record(args.record_file, Record(process.pid, token, config_id))

    deadline = time.monotonic() + args.startup_timeout
    while time.monotonic() < deadline:
        if _ready(args.port, args.health_path):
            print(f"{args.name} is ready on port {args.port}")
            return
        if process.poll() is not None:
            args.record_file.unlink(missing_ok=True)
            raise ContractError(
                f"{args.name} exited during startup; inspect {args.log_file}"
            )
        time.sleep(args.poll_interval)

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=args.stop_timeout)
        except subprocess.TimeoutExpired as exc:
            raise ContractError(
                f"{args.name} did not stop after startup timed out; ownership record retained"
            ) from exc
    args.record_file.unlink(missing_ok=True)
    raise ContractError(
        f"{args.name} did not become ready within {args.startup_timeout:g} seconds; "
        f"inspect {args.log_file}"
    )


def stop(args: argparse.Namespace) -> None:
    had_record = args.record_file.exists()
    record = _read_record(args.record_file)
    if not had_record:
        print(f"No automatically started {args.name} is recorded")
        return
    if record is None:
        print(f"Ignoring malformed {args.name} record")
        args.record_file.unlink(missing_ok=True)
        return
    if not _owned(record, args.identity):
        print(f"PID {record.pid} no longer belongs to {args.name}; leaving it untouched")
        args.record_file.unlink(missing_ok=True)
        return
    print(f"Stopping automatically started {args.name} (PID {record.pid})")
    _stop_owned(record, args.identity, args.stop_timeout)
    args.record_file.unlink(missing_ok=True)
    print(f"Automatically started {args.name} stopped")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action", required=True)

    def common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--name", required=True)
        subparser.add_argument("--record-file", type=Path, required=True)
        subparser.add_argument("--identity", required=True)
        subparser.add_argument("--stop-timeout", type=float, required=True)

    start_parser = subparsers.add_parser("start")
    common(start_parser)
    start_parser.add_argument("--log-file", type=Path, required=True)
    start_parser.add_argument("--port", type=int, required=True)
    start_parser.add_argument("--health-path")
    start_parser.add_argument("--startup-timeout", type=float, required=True)
    start_parser.add_argument("--poll-interval", type=float, default=0.25)
    start_parser.add_argument(
        "--fingerprint-file", type=Path, action="append", default=[]
    )
    start_parser.add_argument("--truncate-log", action="store_true")
    start_parser.add_argument(
        "--require-executable", type=Path, action="append", default=[]
    )
    start_parser.add_argument(
        "--require-directory", type=Path, action="append", default=[]
    )
    start_parser.add_argument("command", nargs=argparse.REMAINDER)

    stop_parser = subparsers.add_parser("stop")
    common(stop_parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "start":
            start(args)
        else:
            stop(args)
    except (ContractError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
