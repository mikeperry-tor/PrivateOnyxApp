#!/usr/bin/env python3
"""Authenticate to Tor's private control socket and verify full bootstrap."""

from __future__ import annotations

import re
import socket
import sys
from pathlib import Path

SOCKET_PATH = "/run/tor-control/control.sock"
COOKIE_PATH = "/run/tor-control/control_auth_cookie"
MAX_LINE = 4096
MAX_TOTAL = 16384


class HealthError(RuntimeError):
    pass


BOOTSTRAP_PROGRESS_RE = re.compile(
    rb"^250-status/bootstrap-phase=NOTICE BOOTSTRAP PROGRESS=([0-9]{1,3})(?: |$)"
)


def read_reply(connection: socket.socket) -> list[bytes]:
    stream = connection.makefile("rb", buffering=0)
    lines: list[bytes] = []
    total = 0
    while True:
        line = stream.readline(MAX_LINE + 1)
        if not line or len(line) > MAX_LINE or not line.endswith(b"\r\n"):
            raise HealthError("invalid control reply framing")
        total += len(line)
        if total > MAX_TOTAL:
            raise HealthError("control reply is too large")
        lines.append(line[:-2])
        if len(line) < 4 or not line[:3].isdigit():
            raise HealthError("invalid control status")
        if line[3:4] == b" ":
            return lines
        if line[3:4] != b"-":
            raise HealthError("unsupported control reply framing")


def command(connection: socket.socket, value: bytes) -> list[bytes]:
    connection.sendall(value + b"\r\n")
    reply = read_reply(connection)
    if not reply[-1].startswith(b"250 "):
        raise HealthError("control command failed")
    return reply


def parse_bootstrap_progress(reply: list[bytes]) -> int:
    matches = [
        match.group(1)
        for line in reply
        if (match := BOOTSTRAP_PROGRESS_RE.match(line)) is not None
    ]
    if len(matches) != 1:
        raise HealthError("control bootstrap reply has invalid progress")
    progress = int(matches[0])
    if not 0 <= progress <= 100:
        raise HealthError("control bootstrap progress is out of range")
    return progress


def bootstrap_progress() -> int:
    """Authenticate with Tor's private cookie and return bounded progress."""
    cookie = Path(COOKIE_PATH).read_bytes()
    if len(cookie) != 32:
        raise HealthError("invalid control cookie")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(SOCKET_PATH)
        command(connection, b"AUTHENTICATE " + cookie.hex().encode("ascii"))
        reply = command(connection, b"GETINFO status/bootstrap-phase")
    return parse_bootstrap_progress(reply)


def check() -> None:
    if bootstrap_progress() != 100:
        raise HealthError("Tor bootstrap is incomplete")


def main() -> int:
    try:
        check()
    except (HealthError, OSError) as exc:
        print(f"Tor health failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
