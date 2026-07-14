#!/usr/bin/env python3
"""Authenticated fixed-route broker for isolated Onyx egress policies.

The broker runs in the trusted routing namespace.  It accepts only a bounded
versioned request from one policy peer, repeats destination validation, opens
one final connection using the existing audited VPN/upstream-proxy code, and
then carries a byte stream.  It is intentionally not an HTTP/SOCKS proxy.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import os
import sys
from typing import Any

import prefetch_blocking_proxy as policy

MAGIC = "onyx-route-broker-v1"
MAX_REQUEST_BYTES = 4096
HANDSHAKE_TIMEOUT = 10
IDLE_TIMEOUT = int(os.environ.get("EGRESS_BROKER_IDLE_TIMEOUT", "300"))
TOTAL_TIMEOUT = int(os.environ.get("EGRESS_BROKER_TOTAL_TIMEOUT", "1800"))
MAX_CONCURRENT = int(os.environ.get("EGRESS_BROKER_MAX_CONCURRENT", "128"))
LISTEN_HOST = os.environ.get("EGRESS_BROKER_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("EGRESS_BROKER_PORT", "3140"))
CREDENTIAL = os.environ.get("EGRESS_ROUTE_BROKER_CREDENTIAL", "")
ALLOWED_CLIENT = os.environ.get("EGRESS_BROKER_ALLOWED_CLIENT_HOST", "").strip()
_active_connections = 0
_active_lock: asyncio.Lock | None = None


def _validate_config() -> None:
    if len(CREDENTIAL) != 64 or any(c not in "0123456789abcdef" for c in CREDENTIAL):
        raise RuntimeError(
            "EGRESS_ROUTE_BROKER_CREDENTIAL must be a 64-character lowercase hex secret"
        )
    if not ALLOWED_CLIENT:
        raise RuntimeError("EGRESS_BROKER_ALLOWED_CLIENT_HOST is required")
    if policy.DEFER_DNS_TO_BROKER or policy.ROUTE_BROKER_HOST:
        raise RuntimeError("route broker cannot itself delegate to another broker")
    if not 1 <= MAX_CONCURRENT <= 4096:
        raise RuntimeError("EGRESS_BROKER_MAX_CONCURRENT must be between 1 and 4096")
    if TOTAL_TIMEOUT <= 0 or IDLE_TIMEOUT <= 0:
        raise RuntimeError("route broker timeouts must be positive")


async def _allowed_peer(peer: Any) -> bool:
    if not isinstance(peer, tuple) or not peer:
        return False
    peer_ip = str(peer[0]).split("%", 1)[0]
    try:
        ipaddress.ip_address(peer_ip)
        allowed = await policy._resolve_system_host(ALLOWED_CLIENT, LISTEN_PORT)
    except (ValueError, OSError):
        return False
    return peer_ip in allowed


async def _pipe_with_idle_timeout(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    deadline: float,
) -> None:
    try:
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            data = await asyncio.wait_for(
                reader.read(policy.TUNNEL_BUFFER), min(IDLE_TIMEOUT, remaining)
            )
            if not data:
                try:
                    writer.write_eof()
                    await writer.drain()
                except (AttributeError, OSError, RuntimeError):
                    pass
                return
            writer.write(data)
            await writer.drain()
    except (asyncio.TimeoutError, ConnectionError, OSError):
        return


async def _send_error(writer: asyncio.StreamWriter, reason: str) -> None:
    safe = " ".join(reason.replace("\r", " ").replace("\n", " ").split())[:512]
    writer.write((json.dumps({"status": "denied", "reason": safe}) + "\n").encode())
    await writer.drain()


async def handle_client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    global _active_connections, _active_lock
    upstream_writer: asyncio.StreamWriter | None = None
    admitted = False
    try:
        if _active_lock is None:
            _active_lock = asyncio.Lock()
        async with _active_lock:
            if _active_connections >= MAX_CONCURRENT:
                await _send_error(writer, "route broker capacity reached")
                return
            _active_connections += 1
            admitted = True
        if not await _allowed_peer(writer.get_extra_info("peername")):
            await _send_error(writer, "unauthorized policy peer")
            return
        raw = await asyncio.wait_for(reader.readline(), HANDSHAKE_TIMEOUT)
        if not raw or len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
            await _send_error(writer, "invalid broker request framing")
            return
        try:
            request = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            await _send_error(writer, "invalid broker request")
            return
        if not isinstance(request, dict) or set(request) != {
            "version", "credential", "route_class", "host", "port"
        }:
            await _send_error(writer, "invalid broker request fields")
            return
        if request["version"] != MAGIC or not hmac.compare_digest(
            str(request["credential"]), CREDENTIAL
        ):
            await _send_error(writer, "invalid broker authentication")
            return
        if request["route_class"] != policy.ROUTE_CLASS:
            await _send_error(writer, "wrong route class")
            return
        host = request["host"]
        port = request["port"]
        if not isinstance(host, str) or len(host.encode("utf-8")) > 1024:
            await _send_error(writer, "invalid destination host")
            return
        if not isinstance(port, int):
            await _send_error(writer, "invalid destination port")
            return
        reason, validated_ips = await policy._validate_destination(host, port)
        if reason:
            await _send_error(writer, reason)
            return
        upstream_reader, upstream_writer = await policy._connect_via_upstream(
            host, port, validated_ips
        )
        writer.write(b'{"status":"ok"}\n')
        await writer.drain()
        deadline = asyncio.get_running_loop().time() + TOTAL_TIMEOUT
        await asyncio.gather(
            _pipe_with_idle_timeout(reader, upstream_writer, deadline),
            _pipe_with_idle_timeout(upstream_reader, writer, deadline),
        )
    except Exception as exc:
        try:
            await _send_error(writer, f"route unavailable: {exc}")
        except Exception:
            pass
    finally:
        if admitted and _active_lock is not None:
            async with _active_lock:
                _active_connections -= 1
        if upstream_writer is not None:
            upstream_writer.close()
        writer.close()


async def _check_ready() -> None:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(LISTEN_HOST, LISTEN_PORT), HANDSHAKE_TIMEOUT
    )
    writer.close()
    await writer.wait_closed()
    if policy.UPSTREAM_PROXY:
        scheme, host, port, username, password = policy._parse_proxy_url(
            policy.UPSTREAM_PROXY
        )
        if scheme in {"socks5", "socks5h"}:
            await policy._probe_socks5_proxy_endpoint(
                host, port, username, password, scheme
            )
        else:
            await policy._probe_http_proxy_endpoint(
                host, port, username, password, scheme
            )
    else:
        addresses = await policy._resolve_target_host("example.com", 443)
        if not addresses or any(policy._ip_block_reason(ip) for ip in addresses):
            raise RuntimeError("route broker egress resolver is not ready")


async def main() -> None:
    _validate_config()
    policy._validate_upstream_proxy_config()
    server = await asyncio.start_server(
        handle_client,
        LISTEN_HOST,
        LISTEN_PORT,
        limit=MAX_REQUEST_BYTES + 1,
        backlog=min(MAX_CONCURRENT, 256),
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--check-ready":
        _validate_config()
        asyncio.run(_check_ready())
    elif len(sys.argv) == 1:
        asyncio.run(main())
    else:
        raise SystemExit("usage: route_broker.py [--check-ready]")
