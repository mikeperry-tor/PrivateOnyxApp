#!/usr/bin/env python3
"""Loopback-only synthetic DNS for CRW's URL-safety preflight.

CRW 0.23 resolves a target locally before using its configured HTTP proxy.
Restricted CRW networks intentionally have no external DNS route, so this
server supplies a global-looking A record solely for that redundant preflight.
The final-hop policy proxy still performs the authoritative destination
resolution and validation before making any external connection.
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import socketserver
import struct
import threading


LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 53
SYNTHETIC_IPV4 = ipaddress.IPv4Address("93.184.216.34")
READINESS_NAME = "crw-validation.test"

# Docker resolves known service names before forwarding. If an unknown name
# reaches this server, retain a fail-closed floor for Docker/host-local naming.
BLOCKED_EXACT_NAMES = frozenset(
    {
        "localhost",
        "host.docker.internal",
        "gateway.docker.internal",
        "host.containers.internal",
        "gateway.containers.internal",
        "docker.for.mac.host.internal",
        "docker.for.mac.localhost",
        "docker.for.win.host.internal",
        "docker.for.win.localhost",
    }
)
BLOCKED_SUFFIXES = (
    ".localhost",
    ".docker.internal",
    ".containers.internal",
    ".local",
    ".localdomain",
    ".internal",
    ".home.arpa",
)


class DNSFormatError(ValueError):
    pass


def _parse_question(packet: bytes) -> tuple[str, int, int, bytes]:
    if len(packet) < 12:
        raise DNSFormatError("DNS header is truncated")

    _, flags, qdcount, _, _, _ = struct.unpack("!HHHHHH", packet[:12])
    if flags & 0x8000 or qdcount != 1:
        raise DNSFormatError("expected one DNS query question")

    labels: list[str] = []
    offset = 12
    while True:
        if offset >= len(packet):
            raise DNSFormatError("DNS name is truncated")
        length = packet[offset]
        offset += 1
        if length == 0:
            break
        if length & 0xC0 or length > 63 or offset + length > len(packet):
            raise DNSFormatError("unsupported DNS name encoding")
        try:
            labels.append(packet[offset : offset + length].decode("ascii"))
        except UnicodeDecodeError as exc:
            raise DNSFormatError("DNS label is not ASCII") from exc
        offset += length

    if offset + 4 > len(packet):
        raise DNSFormatError("DNS question is truncated")
    qtype, qclass = struct.unpack("!HH", packet[offset : offset + 4])
    name = ".".join(labels).lower()
    if not name or len(name) > 253:
        raise DNSFormatError("invalid DNS name")
    # Do not echo optional EDNS data after the question.
    return name, qtype, qclass, packet[12 : offset + 4]


def _is_blocked_name(name: str) -> bool:
    return (
        "." not in name
        or any(
            name == internal_name or name.endswith("." + internal_name)
            for internal_name in BLOCKED_EXACT_NAMES
        )
        or any(name.endswith(suffix) for suffix in BLOCKED_SUFFIXES)
    )


def build_response(packet: bytes) -> bytes:
    """Build a minimal DNS response without forwarding the query anywhere."""

    query_id = packet[:2].ljust(2, b"\0")
    try:
        name, qtype, qclass, question = _parse_question(packet)
    except DNSFormatError:
        # QR + recursion available + FORMERR. There is no question to echo.
        return query_id + struct.pack("!HHHHH", 0x8081, 0, 0, 0, 0)

    # Preserve the recursion-desired bit and advertise a complete local reply.
    flags = 0x8080 | (struct.unpack("!H", packet[2:4])[0] & 0x0100)
    if _is_blocked_name(name):
        # NXDOMAIN prevents unknown Docker-like names from becoming public.
        return packet[:2] + struct.pack("!HHHHH", flags | 0x0003, 1, 0, 0, 0) + question

    # getaddrinfo normally asks for both A and AAAA. Return no AAAA rather than
    # inventing IPv6 connectivity; CRW receives the synthetic global IPv4.
    if qclass != 1 or qtype != 1:
        return packet[:2] + struct.pack("!HHHHH", flags, 1, 0, 0, 0) + question

    answer = (
        b"\xc0\x0c"
        + struct.pack("!HHIH", 1, 1, 30, 4)
        + SYNTHETIC_IPV4.packed
    )
    return packet[:2] + struct.pack("!HHHHH", flags, 1, 1, 0, 0) + question + answer


class _UDPHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        packet, sock = self.request
        sock.sendto(build_response(packet), self.client_address)


class _TCPHandler(socketserver.BaseRequestHandler):
    def _recv_exactly(self, length: int) -> bytes:
        data = bytearray()
        while len(data) < length:
            chunk = self.request.recv(length - len(data))
            if not chunk:
                raise ConnectionError("DNS TCP request ended early")
            data.extend(chunk)
        return bytes(data)

    def handle(self) -> None:
        try:
            length = struct.unpack("!H", self._recv_exactly(2))[0]
            packet = self._recv_exactly(length)
            response = build_response(packet)
            self.request.sendall(struct.pack("!H", len(response)) + response)
        except (ConnectionError, OSError, struct.error):
            return


class _ThreadingUDPServer(socketserver.ThreadingUDPServer):
    allow_reuse_address = True
    daemon_threads = True


class _ThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _query(name: str) -> bool:
    labels = b"".join(
        bytes((len(label),)) + label.encode() for label in name.split(".")
    )
    packet = struct.pack("!HHHHHH", 0xC7A1, 0x0100, 1, 0, 0, 0)
    packet += labels + b"\0" + struct.pack("!HH", 1, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(2)
        sock.sendto(packet, (LISTEN_HOST, LISTEN_PORT))
        response, _ = sock.recvfrom(512)
    return (
        len(response) >= 16
        and response[:2] == packet[:2]
        and SYNTHETIC_IPV4.packed in response
    )


def serve() -> None:
    udp_server = _ThreadingUDPServer((LISTEN_HOST, LISTEN_PORT), _UDPHandler)
    tcp_server = _ThreadingTCPServer((LISTEN_HOST, LISTEN_PORT), _TCPHandler)
    tcp_thread = threading.Thread(target=tcp_server.serve_forever, daemon=True)
    tcp_thread.start()
    try:
        udp_server.serve_forever()
    finally:
        udp_server.server_close()
        tcp_server.shutdown()
        tcp_server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-ready", action="store_true")
    args = parser.parse_args()
    if args.check_ready:
        return 0 if _query(READINESS_NAME) else 1
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
