#!/usr/bin/env python3
"""Destination-validating final-hop HTTP proxy for restricted components.

The proxy exposes two explicit destination-policy route classes. The public
route accepts only globally routable targets. The host route additionally
supports the narrow, validated local-host and opt-in RFC1918 exceptions used
by configured Onyx integrations. Both routes fail closed on ambiguous DNS,
private destinations, malformed HTTP framing, and disallowed cleartext URLs.

1. **For plain HTTP URLs**: returns ``403 Forbidden`` by default with a clear
   explanatory message. The host route retains narrow exceptions for exact
   ``host.docker.internal``, RFC1918 literals, and opt-in proxy-validated
   ``.local``/``.internal``/``.home.arpa`` destinations. Set
   ``EGRESS_ALLOW_HTTP_URLS=true`` only when general cleartext HTTP fetches are
   intentionally required.

2. **For HTTPS URLs, and explicitly allowed plain HTTP URLs**: forwards the
   request to the target, through ``EGRESS_UPSTREAM_PROXY_URL`` or the
   stack-owned native-Tor Unix SOCKS endpoint when selected.

Before any CONNECT tunnel or HTTP forwarding, the proxy rejects loopback,
private/RFC1918, link-local, multicast, reserved, and other non-global IP
destinations. It also rejects loopback names, Docker Desktop's current and
legacy ``*.docker.internal`` host/gateway names, their subdomains, and
single-label Docker-style hostnames. When
``EGRESS_UPSTREAM_PROXY_URL`` is empty, DNS names are resolved directly
through the Mysterium provider resolver when VPN routing is enabled, or through
system DNS only in explicit no-VPN mode; all returned addresses are classified.
When any remote-DNS upstream is set, target DNS resolution is skipped so the
target hostname is sent only through the proxy protocol. The host route
consults system/Docker DNS for arbitrary targets only when explicit no-VPN mode
is selected. With RFC1918 access
enabled, VPN mode additionally permits classification of names ending in
``.local``, ``.internal``, or ``.home.arpa``.
Failed or empty lookups for those operator-local names fail closed instead of
falling through to Myst DNS or an external upstream proxy. Non-empty all-global
answers return to the selected public final hop; mixed answers fail.

When ``EGRESS_UPSTREAM_PROXY_URL`` is set, the proxy resolves and classifies
that configured TCP endpoint before connecting. Native Tor instead opens only
the fixed ``/run/tor-egress/socks`` Unix socket and shares the same SOCKS5
state machine. Restricted components reach policy instances only through their
local bridges. In VPN mode, public proxy names use provider DNS and public
proxy addresses follow the Myst route. Exact ``host.docker.internal`` uses its
narrow route; an RFC1918 IPv4 literal receives only an exact proxy-endpoint
route; and operator-local proxy names use system DNS only with
``ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true``.

Architecture::

    restricted component ──HTTP proxy──> local bridge ──> policy proxy
                                   │
                                   ├─ Internal/private target → 403
                                   ├─ Other HTTP URL → 403 unless explicitly allowed
                                   ├─ Other HTTPS URL → CONNECT tunnel
                                   │
                                   └── upstream ──> configured TCP proxy or
                                                    native-Tor Unix SOCKS
                                                    (if selected)

Restricted callers reach a fixed bridge into the applicable route-class
listener. They always see an ordinary local HTTP proxy, regardless of the
configured upstream proxy scheme.

HTTP forwarding requires CRLF field lines, rejects forbidden control
characters, validates chunk-extension and trailer syntax, and rejects
ambiguous header framing before opening an origin connection. Chunked body
syntax is validated incrementally before each line is forwarded.

See ``docs/vpn_routing_and_proxies.md`` for the complete routing design.
"""

from __future__ import annotations

import asyncio
import fcntl
import ipaddress
import logging
import os
import re
import secrets
import socket
import ssl
import struct
import sys
import traceback
from typing import Any
from urllib.parse import unquote, urlparse

# ── Configuration ────────────────────────────────────────────────────────

LISTEN_HOST = os.environ.get("EGRESS_PROXY_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("EGRESS_PROXY_PORT", "3128"))

# The public and host Onyx listeners are complete final-hop proxies in the
# trusted routing namespace. ROUTE_CLASS selects whether the listener has only
# public destination policy or the additional exact-host/opt-in RFC1918 rules.
ROUTE_CLASS = os.environ.get("EGRESS_ROUTE_CLASS", "public").strip().lower()
if ROUTE_CLASS not in {"public", "host"}:
    raise RuntimeError("EGRESS_ROUTE_CLASS must be exactly 'public' or 'host'")
ALLOW_LAN_ENDPOINTS = (
    os.environ.get("ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS", "false").strip()
    == "true"
)
RFC1918_SYSTEM_DNS_SUFFIXES = (".local", ".internal", ".home.arpa")

# Upstream proxy (EGRESS_UPSTREAM_PROXY_URL from .env.wrapper). When set,
# the proxy routes its own requests through this upstream. Supports:
# http://, https://, socks5://, socks5h://
# When empty, requests go direct (through the VPN namespace).
UPSTREAM_PROXY = os.environ.get("EGRESS_UPSTREAM_PROXY_URL", "").strip()
TOR_SOCKS_UNIX_PATH = os.environ.get("EGRESS_TOR_SOCKS_UNIX_PATH", "").strip()
if TOR_SOCKS_UNIX_PATH and TOR_SOCKS_UNIX_PATH != "/run/tor-egress/socks":
    raise RuntimeError(
        "EGRESS_TOR_SOCKS_UNIX_PATH is an internal fixed setting and must be "
        "/run/tor-egress/socks"
    )
if TOR_SOCKS_UNIX_PATH and UPSTREAM_PROXY:
    raise RuntimeError(
        "native Tor egress and EGRESS_UPSTREAM_PROXY_URL are mutually exclusive"
    )
_MYST_VPN_ENABLED_RAW = os.environ.get("MYST_VPN_ENABLED", "true").strip()
if _MYST_VPN_ENABLED_RAW not in {"true", "false"}:
    raise RuntimeError("MYST_VPN_ENABLED must be exactly 'true' or 'false'")
MYST_VPN_ENABLED = _MYST_VPN_ENABLED_RAW == "true"
MYST_VPN_INTERFACE = "myst0"
DNS_QUERY_TIMEOUT = int(os.environ.get("EGRESS_DNS_QUERY_TIMEOUT", "10"))

# Comma-separated Docker service names allowed to reach this policy listener.
# Loopback is always allowed for the local healthcheck. Each policy instance
# names only the fixed bridge peer or peers that share its exact policy,
# preventing unrelated containers on another netns-holder attachment from
# using the listener directly.
ALLOWED_CLIENT_HOSTS = tuple(
    host.strip()
    for host in os.environ.get("EGRESS_PROXY_ALLOWED_CLIENT_HOSTS", "").split(",")
    if host.strip()
)


def _listener_is_loopback_only() -> bool:
    try:
        return ipaddress.ip_address(LISTEN_HOST.split("%", 1)[0]).is_loopback
    except ValueError:
        return False

# HTTPS upstream proxies use normal certificate verification and explicit SNI.
# When the runtime supports TLS 1.3, require it for the proxy leg so
# "https://" proxy credentials and CONNECT metadata are not sent over older
# TLS versions. Runtimes without TLS 1.3 support fall back to the default
# verified context rather than failing at import time.
HTTPS_PROXY_REQUIRE_TLS13 = (
    os.environ.get("ONYX_AGENT_HTTPS_PROXY_REQUIRE_TLS13", "true").lower()
    in ("1", "true", "yes", "on")
)

# Plain HTTP URLs are blocked by default. This is intentionally separate from
# destination validation: even public HTTP hosts leak path/query contents in
# cleartext and should not be fetched by an LLM web tool unless the operator
# explicitly opts in. The host route's exact ``host.docker.internal`` identity
# is the narrow exception needed by local HTTP inference and embedding servers;
# it remains proxy-resolved, address-pinned, and unavailable to public routes.
ALLOW_HTTP_URLS = os.environ.get("EGRESS_ALLOW_HTTP_URLS", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
HTTP_URL_BLOCK_MESSAGE = (
    "HTTP URLs are disabled by EGRESS_ALLOW_HTTP_URLS=false. "
    "Use an https:// URL instead."
)

# Timeout for establishing a tunnel connection (seconds).
TUNNEL_CONNECT_TIMEOUT = int(os.environ.get("EGRESS_CONNECT_TIMEOUT", "15"))

# Buffer size for tunneling.
TUNNEL_BUFFER = 65536

# Built-in hostnames that should never be proxied. Keep these independent of
# operator additions so configuration cannot remove the Docker/loopback floor.
BUILTIN_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "host.docker.internal",
        "gateway.docker.internal",
        "vm.docker.internal",
        # Deprecated Docker Desktop names remain blocked for older engines.
        "docker.for.mac.host.internal",
        "docker.for.mac.localhost",
        "docker.for.mac.gateway.internal",
        "docker.for.win.host.internal",
        "docker.for.win.localhost",
        "host.containers.internal",
        "gateway.containers.internal",
    }
)
BUILTIN_BLOCKED_HOSTNAME_SUFFIXES = frozenset(
    {"docker.internal", "containers.internal"}
)

# Additional deployment-specific internal names. Docker service names,
# container names, and the aliases in this repository are single-label and
# are already rejected structurally.
CONFIGURED_BLOCKED_HOSTNAMES = frozenset(
    h.strip().lower().rstrip(".")
    for h in os.environ.get(
        "EGRESS_BLOCK_INTERNAL_HOSTS",
        "",
    ).split(",")
    if h.strip()
)
BLOCKED_HOSTNAMES = BUILTIN_BLOCKED_HOSTNAMES | CONFIGURED_BLOCKED_HOSTNAMES

logging.basicConfig(
    level=os.environ.get("EGRESS_PROXY_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [final-hop-proxy] %(levelname)s %(message)s",
)
logger = logging.getLogger("final-hop-proxy")


# ── Destination validation ────────────────────────────────────────────────


def _normalize_host(host: str) -> str:
    """Normalize a parsed host, including IDNA-equivalent DNS separators."""
    normalized = host.strip().strip("[]").lower().rstrip(".")
    try:
        return normalized.encode("idna").decode("ascii").rstrip(".")
    except UnicodeError:
        # Classification rejects remaining non-ASCII text before any request.
        return normalized


def _parse_ip_literal(
    ip_text: str,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse an IPv4/IPv6 literal, returning None for DNS names."""
    try:
        return ipaddress.ip_address(ip_text)
    except ValueError:
        return None


def _ip_block_reason(ip_text: str) -> str | None:
    """Return a block reason for internal/non-global IPs, else None."""
    ip = _parse_ip_literal(ip_text)
    if ip is None:
        return None

    if ip.is_loopback:
        return "loopback IP"
    if ip.is_private:
        return "private/RFC1918 IP"
    if ip.is_link_local:
        return "link-local IP"
    if ip.is_multicast:
        return "multicast IP"
    if ip.is_unspecified:
        return "unspecified IP"
    if ip.is_reserved:
        return "reserved IP"
    if not ip.is_global:
        return "non-global IP"
    return None


def _is_rfc1918(ip_text: str) -> bool:
    """Return True only for the three operator-routable IPv4 private ranges."""
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    return isinstance(ip, ipaddress.IPv4Address) and any(
        ip in network
        for network in (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        )
    )


def _is_exact_host_exception(host: str) -> bool:
    return ROUTE_CLASS == "host" and _normalize_host(host) == "host.docker.internal"


def _is_rfc1918_system_dns_name(host: str) -> bool:
    """Return whether an operator-local name may use system DNS classification."""
    normalized = _normalize_host(host)
    return any(normalized.endswith(suffix) for suffix in RFC1918_SYSTEM_DNS_SUFFIXES)


def _loose_ipv4_literal(host: str) -> str | None:
    """Parse legacy IPv4 forms such as 2130706433 or 0177.0.0.1."""
    if not host or not all(c in "0123456789abcdefABCDEFxX." for c in host):
        return None
    try:
        return socket.inet_ntoa(socket.inet_aton(host))
    except OSError:
        return None


def _hostname_block_reason(host: str) -> str | None:
    """Return a block reason for hostnames that are internal by name."""
    host = _normalize_host(host)
    if not host:
        return "empty host"
    if not host.isascii():
        return "invalid IDNA hostname"
    if "%" in host:
        return "IPv6 zone identifier"
    if host in BLOCKED_HOSTNAMES:
        return "blocked internal hostname"
    if any(host.endswith("." + blocked) for blocked in BLOCKED_HOSTNAMES):
        return "blocked internal hostname suffix"
    if any(
        host == suffix or host.endswith("." + suffix)
        for suffix in BUILTIN_BLOCKED_HOSTNAME_SUFFIXES
    ):
        return "blocked Docker-internal hostname suffix"
    if "." not in host:
        return "single-label/internal hostname"
    return None


def _parse_authority(authority: str, default_port: int) -> tuple[str, int]:
    """Parse host[:port] authority, including bracketed IPv6 literals."""
    parsed = urlparse("//" + authority)
    host = parsed.hostname or ""
    try:
        port = parsed.port or default_port
    except ValueError as e:
        raise ValueError(f"invalid port in target {authority!r}") from e
    return host, port


def _parse_trusted_internal_destinations() -> frozenset[tuple[str, int]]:
    raw = os.environ.get("EGRESS_PROXY_TRUSTED_INTERNAL_DESTINATIONS", "").strip()
    if not raw:
        return frozenset()
    if ROUTE_CLASS != "host":
        raise RuntimeError(
            "EGRESS_PROXY_TRUSTED_INTERNAL_DESTINATIONS is allowed only for "
            "the host route class"
        )

    destinations: set[tuple[str, int]] = set()
    for item in raw.split(","):
        authority = item.strip()
        if not authority:
            continue
        parsed = urlparse("//" + authority)
        try:
            port = parsed.port
        except ValueError as exc:
            raise RuntimeError(
                f"invalid trusted internal destination {authority!r}"
            ) from exc
        host = _normalize_host(parsed.hostname or "")
        if (
            not host
            or port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(
                "trusted internal destinations must be exact host:port "
                f"authorities, found {authority!r}"
            )
        destinations.add((host, port))
    if not destinations:
        raise RuntimeError(
            "EGRESS_PROXY_TRUSTED_INTERNAL_DESTINATIONS contained no destinations"
        )
    return frozenset(destinations)


TRUSTED_INTERNAL_DESTINATIONS = _parse_trusted_internal_destinations()


def _is_trusted_internal_destination(host: str, port: int) -> bool:
    return (_normalize_host(host), port) in TRUSTED_INTERNAL_DESTINATIONS


def _plain_http_allowed(
    host: str, port: int, validated_ips: tuple[str, ...] = ()
) -> bool:
    validated_rfc1918 = bool(validated_ips) and all(
        _is_rfc1918(ip) for ip in validated_ips
    )
    return (
        ALLOW_HTTP_URLS
        or _is_trusted_internal_destination(host, port)
        or _is_exact_host_exception(host)
        or (
            ROUTE_CLASS == "host"
            and ALLOW_LAN_ENDPOINTS
            and validated_rfc1918
        )
    )


async def _resolve_system_host(host: str, port: int) -> set[str]:
    """Resolve Docker/internal names through the container system resolver."""
    loop = asyncio.get_running_loop()
    addrs = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    resolved: set[str] = set()
    for addr in addrs:
        sockaddr = addr[4]
        if sockaddr:
            resolved.add(sockaddr[0])
    return resolved


def _interface_ipv4_network(interface: str) -> tuple[ipaddress.IPv4Address, ipaddress.IPv4Network]:
    """Return an interface IPv4 address and network using Linux ioctls."""
    if not interface or len(interface.encode("ascii")) >= 16:
        raise RuntimeError("invalid Mysterium VPN interface name")
    ifreq = struct.pack("256s", interface.encode("ascii"))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            address_raw = fcntl.ioctl(sock.fileno(), 0x8915, ifreq)[20:24]
            netmask_raw = fcntl.ioctl(sock.fileno(), 0x891B, ifreq)[20:24]
        except OSError as exc:
            raise RuntimeError(
                f"Mysterium VPN interface {interface!r} has no usable IPv4 configuration"
            ) from exc
    address = ipaddress.IPv4Address(address_raw)
    netmask = ipaddress.IPv4Address(netmask_raw)
    network = ipaddress.IPv4Network(f"{address}/{netmask}", strict=False)
    return address, network


def _myst_provider_dns_endpoint() -> tuple[str, str]:
    """Return (provider DNS, local VPN IP) without consulting system DNS."""
    local_address, network = _interface_ipv4_network(MYST_VPN_INTERFACE)
    if network.prefixlen == 32:
        raise RuntimeError("cannot derive provider DNS from a /32 Myst interface")
    provider_dns = network.network_address + 1
    if provider_dns == local_address:
        raise RuntimeError("derived provider DNS address equals the local Myst address")
    return str(provider_dns), str(local_address)


def _myst_provider_dns_ip() -> str:
    """Return Myst's provider DNS address without consulting system DNS."""
    return _myst_provider_dns_endpoint()[0]


def _encode_dns_name(host: str) -> bytes:
    normalized = _normalize_host(host)
    try:
        ascii_host = normalized.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise socket.gaierror(f"invalid IDNA hostname: {host}") from exc
    labels = ascii_host.split(".")
    if not labels or any(not label or len(label.encode("ascii")) > 63 for label in labels):
        raise socket.gaierror(f"invalid DNS hostname: {host}")
    encoded = b"".join(bytes([len(label)]) + label.encode("ascii") for label in labels)
    if len(encoded) + 1 > 255:
        raise socket.gaierror(f"DNS hostname is too long: {host}")
    return encoded + b"\x00"


def _skip_dns_name(message: bytes, offset: int) -> int:
    """Return the first byte after a possibly compressed DNS name."""
    labels_seen = 0
    while True:
        if offset >= len(message):
            raise ValueError("truncated DNS name")
        length = message[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(message):
                raise ValueError("truncated DNS compression pointer")
            return offset + 2
        if length & 0xC0:
            raise ValueError("invalid DNS label type")
        offset += 1
        if length == 0:
            return offset
        offset += length
        labels_seen += 1
        if offset > len(message) or labels_seen > 127:
            raise ValueError("invalid DNS name")


def _parse_dns_a_response(message: bytes, query_id: int) -> tuple[set[str], bool]:
    """Parse A answers and return (addresses, truncated)."""
    if len(message) < 12:
        raise ValueError("truncated DNS response")
    (
        response_id,
        flags,
        question_count,
        answer_count,
        _authority_count,
        _additional_count,
    ) = struct.unpack("!HHHHHH", message[:12])
    if response_id != query_id or not flags & 0x8000:
        raise ValueError("mismatched DNS response")
    rcode = flags & 0x000F
    if rcode == 3:
        raise socket.gaierror(socket.EAI_NONAME, "DNS name does not exist")
    if rcode != 0:
        raise socket.gaierror(f"DNS resolver returned rcode {rcode}")
    if flags & 0x0200:
        return set(), True

    offset = 12
    for _ in range(question_count):
        offset = _skip_dns_name(message, offset)
        if offset + 4 > len(message):
            raise ValueError("truncated DNS question")
        offset += 4

    addresses: set[str] = set()
    for _ in range(answer_count):
        offset = _skip_dns_name(message, offset)
        if offset + 10 > len(message):
            raise ValueError("truncated DNS answer")
        record_type, record_class, _ttl, data_length = struct.unpack(
            "!HHIH", message[offset : offset + 10]
        )
        offset += 10
        data_end = offset + data_length
        if data_end > len(message):
            raise ValueError("truncated DNS answer data")
        if record_type == 1 and record_class == 1 and data_length == 4:
            addresses.add(str(ipaddress.IPv4Address(message[offset:data_end])))
        offset = data_end
    return addresses, False


def _bind_socket_to_vpn_address(sock: socket.socket, local_ip: str) -> None:
    """Source-bind a DNS socket to the Myst address without extra capabilities."""
    try:
        sock.bind((local_ip, 0))
    except OSError as exc:
        raise RuntimeError(
            f"cannot source-bind provider DNS socket to Myst address {local_ip}"
        ) from exc


async def _dns_query_a(host: str, resolver_ip: str, local_ip: str) -> set[str]:
    """Resolve A records against a literal resolver through one VPN device."""
    query_id = secrets.randbits(16)
    query = (
        struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0)
        + _encode_dns_name(host)
        + struct.pack("!HH", 1, 1)
    )
    loop = asyncio.get_running_loop()
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        _bind_socket_to_vpn_address(udp_sock, local_ip)
        udp_sock.setblocking(False)
        udp_sock.connect((resolver_ip, 53))
        await asyncio.wait_for(
            loop.sock_sendall(udp_sock, query),
            timeout=DNS_QUERY_TIMEOUT,
        )
        response = await asyncio.wait_for(
            loop.sock_recv(udp_sock, 65535), timeout=DNS_QUERY_TIMEOUT
        )
    finally:
        udp_sock.close()

    addresses, truncated = _parse_dns_a_response(response, query_id)
    if not truncated:
        return addresses

    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _bind_socket_to_vpn_address(tcp_sock, local_ip)
        tcp_sock.setblocking(False)
        await asyncio.wait_for(
            loop.sock_connect(tcp_sock, (resolver_ip, 53)),
            timeout=DNS_QUERY_TIMEOUT,
        )
        reader, writer = await asyncio.open_connection(sock=tcp_sock)
    except BaseException:
        tcp_sock.close()
        raise
    try:
        writer.write(struct.pack("!H", len(query)) + query)
        await writer.drain()
        length_raw = await asyncio.wait_for(
            reader.readexactly(2), timeout=DNS_QUERY_TIMEOUT
        )
        response_length = struct.unpack("!H", length_raw)[0]
        tcp_response = await asyncio.wait_for(
            reader.readexactly(response_length), timeout=DNS_QUERY_TIMEOUT
        )
    finally:
        writer.close()
        await writer.wait_closed()
    addresses, tcp_truncated = _parse_dns_a_response(tcp_response, query_id)
    if tcp_truncated:
        raise socket.gaierror("DNS TCP response was unexpectedly truncated")
    return addresses


async def _resolve_target_host(host: str, port: int) -> set[str]:
    """Resolve public targets through Myst DNS, or system DNS in no-VPN mode."""
    if not MYST_VPN_ENABLED:
        return await _resolve_system_host(host, port)
    resolver_ip, local_ip = _myst_provider_dns_endpoint()
    return await _dns_query_a(host, resolver_ip, local_ip)


async def _validate_destination(
    host: str, port: int
) -> tuple[str | None, tuple[str, ...]]:
    """Validate a proxy target before any upstream connection is opened.

    Literal IPs and known internal hostnames are blocked in every mode. When no
    explicit upstream proxy is configured, DNS names are resolved through the
    selected VPN/no-VPN resolver and every returned address is classified.
    When an upstream proxy is set, target resolution is left to that proxy.
    """
    host = _normalize_host(host)

    if not 0 < port <= 65535:
        return "invalid port", ()

    # The host-capable route has one always-available Docker-host identity.
    # It is deliberately exact (not a suffix rule) and is resolved only by the
    # host-capable final-hop proxy in the trusted namespace.
    if _is_exact_host_exception(host):
        try:
            resolved_ips = await _resolve_system_host(host, port)
        except (socket.gaierror, OSError, ValueError) as e:
            return f"host exception DNS resolution failed: {e}", ()
        if not resolved_ips:
            return "host exception DNS resolution returned no addresses", ()
        for resolved_ip in resolved_ips:
            ip = _parse_ip_literal(resolved_ip)
            if (
                ip is None
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_unspecified
                or ip.is_reserved
            ):
                return "host exception resolved to a forbidden address", ()
        return None, tuple(sorted(resolved_ips))

    if _is_trusted_internal_destination(host, port):
        try:
            resolved_ips = await _resolve_system_host(host, port)
        except (socket.gaierror, OSError, ValueError) as e:
            return f"trusted internal DNS resolution failed: {e}", ()
        if not resolved_ips:
            return "trusted internal DNS resolution returned no addresses", ()
        return None, tuple(sorted(resolved_ips))

    if (
        ROUTE_CLASS == "host"
        and ALLOW_LAN_ENDPOINTS
        and _is_rfc1918(host)
    ):
        return None, (host,)

    if _parse_ip_literal(host) and not _ip_block_reason(host):
        return None, (host,)

    ip_reason = _ip_block_reason(host)
    if ip_reason:
        return ip_reason, ()
    if _loose_ipv4_literal(host):
        loose_ip = _loose_ipv4_literal(host)
        loose_reason = _ip_block_reason(loose_ip or "")
        if loose_reason:
            return f"{loose_reason} via IPv4 shorthand {loose_ip}", ()
        return None, (loose_ip or host,)

    hostname_reason = _hostname_block_reason(host)
    if hostname_reason:
        return hostname_reason, ()

    # The LAN option exists only on the host final-hop proxy. To avoid leaking
    # every host-route name to system/Docker DNS, classify only operator-local names
    # with an explicitly supported suffix. Mixed answers fail closed.
    # All-global answers are discarded and follow the normal selected final
    # route below. Arbitrary names never reach system DNS in VPN mode.
    if (
        ROUTE_CLASS == "host"
        and ALLOW_LAN_ENDPOINTS
        and _is_rfc1918_system_dns_name(host)
    ):
        try:
            candidate_ips = await _resolve_system_host(host, port)
        except (socket.gaierror, OSError, ValueError) as e:
            return f"operator-local DNS resolution failed: {e}", ()
        if not candidate_ips:
            return "operator-local DNS resolution returned no addresses", ()
        private = {_is_rfc1918(ip) for ip in candidate_ips}
        if private == {True}:
            return None, tuple(sorted(candidate_ips))
        if private != {False}:
            return "DNS returned mixed RFC1918 and non-RFC1918 addresses", ()

    if UPSTREAM_PROXY or TOR_SOCKS_UNIX_PATH:
        # Every supported proxy protocol can carry a target hostname. Keep
        # target DNS at the configured proxy instead of resolving locally or
        # through Myst first. Exact host and validated RFC1918 exceptions have
        # already returned above and never traverse the upstream proxy.
        return None, ()

    try:
        resolved_ips = await _resolve_target_host(host, port)
    except (socket.gaierror, OSError, RuntimeError, ValueError) as e:
        return f"DNS resolution failed: {e}", ()

    if not resolved_ips:
        return "DNS resolution returned no addresses", ()

    for resolved_ip in sorted(resolved_ips):
        resolved_reason = _ip_block_reason(resolved_ip)
        if resolved_reason:
            return f"DNS resolved to blocked {resolved_ip} ({resolved_reason})", ()

    return None, tuple(sorted(resolved_ips))


async def _blocked_destination_reason(host: str, port: int) -> str | None:
    """Compatibility helper used by policy tests and diagnostics."""
    reason, _validated_ips = await _validate_destination(host, port)
    return reason


async def _allowed_client_reason(peer: Any) -> str | None:
    """Return a rejection reason unless peer is loopback or an allowed bridge."""
    if not isinstance(peer, tuple) or not peer:
        return "missing client address"

    peer_ip_text = str(peer[0]).split("%", 1)[0]
    try:
        peer_ip = ipaddress.ip_address(peer_ip_text)
    except ValueError:
        return "invalid client address"
    if peer_ip.is_loopback:
        return None
    if not ALLOWED_CLIENT_HOSTS:
        return "no allowed bridge service configured"

    allowed_ips: set[str] = set()
    for host in ALLOWED_CLIENT_HOSTS:
        try:
            allowed_ips.update(await _resolve_system_host(host, LISTEN_PORT))
        except (socket.gaierror, OSError):
            logger.warning("Unable to resolve allowed bridge service %s", host)
    if peer_ip_text not in allowed_ips:
        return "client is not an allowed bridge service"
    return None


async def _reject_blocked_destination(
    method: str,
    host: str,
    port: int,
    writer: asyncio.StreamWriter,
    peer: Any,
) -> tuple[bool, tuple[str, ...]]:
    reason, validated_ips = await _validate_destination(host, port)
    if not reason:
        return False, validated_ips

    # A proxy-side resolver failure is not a policy refusal.  The client
    # cannot observe target DNS directly, so report it as a failed gateway
    # operation rather than incorrectly claiming that policy forbade it.
    dns_failure = (
        "DNS resolution failed:" in reason
        or reason.endswith("DNS resolution returned no addresses")
    )
    status_code = 502 if dns_failure else 403
    status_reason = "Bad Gateway" if dns_failure else "Forbidden"
    outcome = "FAILED" if dns_failure else "BLOCKED"
    logger.warning(
        "%s %s %s:%d (%s, peer=%s) -> %d",
        outcome,
        method,
        _normalize_host(host) or "(empty)",
        port,
        reason,
        peer,
        status_code,
    )
    writer.write(f"HTTP/1.1 {status_code} {status_reason}\r\n\r\n".encode("ascii"))
    await writer.drain()
    return True, ()


async def _write_text_response(
    writer: asyncio.StreamWriter,
    status_code: int,
    reason: str,
    body: str,
) -> None:
    """Write a small text/plain HTTP response and close the connection."""
    body_bytes = (body.rstrip() + "\n").encode("utf-8")
    response = (
        f"HTTP/1.1 {status_code} {reason}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(body_bytes)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("utf-8") + body_bytes
    writer.write(response)
    await writer.drain()


# ── Upstream proxy connection helpers ────────────────────────────────────


def _parse_proxy_url(proxy_url: str) -> tuple[str, str, int, str | None, str | None]:
    """Parse a proxy URL into (scheme, host, port, username, password)."""
    parsed = urlparse(proxy_url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""
    default_ports = {"http": 80, "https": 443, "socks5": 1080, "socks5h": 1080}
    port = parsed.port or default_ports.get(scheme, 0)
    username = unquote(parsed.username) if parsed.username is not None else None
    password = unquote(parsed.password) if parsed.password is not None else None
    return scheme, host, port, username, password


def _validate_upstream_proxy_config() -> None:
    """Reject unusable upstream proxy configuration before listening."""
    if TOR_SOCKS_UNIX_PATH and UPSTREAM_PROXY:
        raise RuntimeError(
            "native Tor egress and EGRESS_UPSTREAM_PROXY_URL are mutually exclusive"
        )
    if not UPSTREAM_PROXY:
        return

    try:
        parsed = urlparse(UPSTREAM_PROXY)
        scheme, host, port, username, password = _parse_proxy_url(UPSTREAM_PROXY)
    except ValueError as exc:
        raise RuntimeError(f"Invalid EGRESS_UPSTREAM_PROXY_URL: {exc}") from exc

    if scheme not in {"http", "https", "socks5", "socks5h"}:
        raise RuntimeError(
            "EGRESS_UPSTREAM_PROXY_URL must use http, https, socks5, or "
            f"socks5h, not {scheme or '(missing scheme)'}"
        )
    if not host:
        raise RuntimeError("EGRESS_UPSTREAM_PROXY_URL must include a hostname")
    if not 0 < port <= 65535:
        raise RuntimeError("EGRESS_UPSTREAM_PROXY_URL has an invalid port")
    if parsed.query or parsed.fragment or parsed.params:
        raise RuntimeError(
            "EGRESS_UPSTREAM_PROXY_URL must not include params, a query, or a fragment"
        )
    if parsed.path not in ("", "/"):
        raise RuntimeError("EGRESS_UPSTREAM_PROXY_URL must not include a path")
    if (username is None) != (password is None):
        raise RuntimeError(
            "EGRESS_UPSTREAM_PROXY_URL must provide both username and password"
        )
    if scheme in {"socks5", "socks5h"} and username is not None:
        if len(username.encode("utf-8")) > 255 or len((password or "").encode("utf-8")) > 255:
            raise RuntimeError("SOCKS5 proxy credentials must be at most 255 bytes each")


def _sanitized_upstream_proxy() -> str:
    """Return a credential-free proxy description for logs."""
    if not UPSTREAM_PROXY:
        return "(direct)"
    scheme, host, port, _username, _password = _parse_proxy_url(UPSTREAM_PROXY)
    display_host = f"[{host}]" if ":" in host else host
    return f"{scheme}://{display_host}:{port}"


def _target_dns_mode() -> str:
    if TOR_SOCKS_UNIX_PATH:
        return "native-tor"
    if UPSTREAM_PROXY:
        return "upstream-proxy"
    if MYST_VPN_ENABLED:
        return f"myst-provider-via-{MYST_VPN_INTERFACE}"
    return "system-explicit-no-vpn"


def _https_proxy_ssl_context() -> ssl.SSLContext:
    """Build a verified TLS context for HTTPS upstream proxy connections."""
    ssl_ctx = ssl.create_default_context()
    if HTTPS_PROXY_REQUIRE_TLS13:
        try:
            ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        except (AttributeError, ValueError):
            logger.warning(
                "Python/OpenSSL runtime does not expose TLS 1.3 controls; "
                "using the default verified TLS context for HTTPS upstream proxy"
            )
    return ssl_ctx


async def _connect_via_upstream(
    target_host: str,
    target_port: int,
    validated_ips: tuple[str, ...] = (),
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect to target_host:target_port through UPSTREAM_PROXY.

    Supports HTTP CONNECT, SOCKS5, and SOCKS5h proxies.
    Returns (reader, writer) for the established tunnel.
    """
    if _is_trusted_internal_destination(target_host, target_port):
        return await _open_validated_direct_connection(validated_ips, target_port)

    if _is_exact_host_exception(target_host):
        return await _open_validated_direct_connection(validated_ips, target_port)

    if ROUTE_CLASS == "host" and ALLOW_LAN_ENDPOINTS and validated_ips and all(
        _is_rfc1918(ip) for ip in validated_ips
    ):
        return await _open_validated_direct_connection(validated_ips, target_port)

    if not UPSTREAM_PROXY and not TOR_SOCKS_UNIX_PATH:
        return await _open_validated_direct_connection(validated_ips, target_port)

    if TOR_SOCKS_UNIX_PATH:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(TOR_SOCKS_UNIX_PATH),
                timeout=TUNNEL_CONNECT_TIMEOUT,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise ConnectionError("native Tor SOCKS socket is unavailable") from exc
        return await _socks5_connect(
            reader,
            writer,
            target_host,
            target_port,
            None,
            None,
        )

    scheme, proxy_host, proxy_port, proxy_user, proxy_pass = _parse_proxy_url(
        UPSTREAM_PROXY
    )

    if scheme in ("socks5", "socks5h"):
        return await _connect_via_socks5(
            proxy_host,
            proxy_port,
            target_host,
            target_port,
            proxy_user,
            proxy_pass,
            scheme,
            validated_ips,
        )
    elif scheme in ("http", "https"):
        return await _connect_via_http_connect(
            proxy_host,
            proxy_port,
            target_host,
            target_port,
            proxy_user,
            proxy_pass,
            scheme,
        )
    else:
        raise ValueError(f"Unsupported proxy scheme: {scheme}")


async def _open_validated_direct_connection(
    validated_ips: tuple[str, ...], target_port: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect only to addresses returned by destination validation."""
    if not validated_ips:
        raise ConnectionError(
            "direct connection has no validated destination addresses"
        )

    failures: list[str] = []
    for ip in validated_ips:
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(ip, target_port),
                timeout=TUNNEL_CONNECT_TIMEOUT,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            failures.append(f"{ip}: {exc}")
    raise ConnectionError(
        "all validated destination addresses failed: " + "; ".join(failures)
    )


async def _connect_via_socks5(
    proxy_host: str,
    proxy_port: int,
    target_host: str,
    target_port: int,
    username: str | None,
    password: str | None,
    scheme: str,
    validated_ips: tuple[str, ...] = (),
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Establish a SOCKS5/SOCKS5h tunnel to the target."""
    reader, writer = await _open_http_proxy_connection(
        proxy_host, proxy_port, scheme
    )
    return await _socks5_connect(
        reader, writer, target_host, target_port, username, password
    )


async def _socks5_connect(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    target_host: str,
    target_port: int,
    username: str | None,
    password: str | None,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Run the common bounded SOCKS5 CONNECT exchange on an open stream."""
    async def read_exactly(size: int) -> bytes:
        try:
            return await asyncio.wait_for(
                reader.readexactly(size), timeout=TUNNEL_CONNECT_TIMEOUT
            )
        except asyncio.TimeoutError as exc:
            writer.close()
            raise ConnectionError("SOCKS5 reply timed out") from exc
        except asyncio.IncompleteReadError as exc:
            writer.close()
            raise ConnectionError("SOCKS5 reply was truncated") from exc

    # SOCKS5 greeting: version 5, auth methods
    if username and password:
        offered_methods = {0x00, 0x02}
        writer.write(b"\x05\x02\x00\x02")  # 2 methods: no-auth, userpass
    else:
        offered_methods = {0x00}
        writer.write(b"\x05\x01\x00")  # 1 method: no-auth
    await writer.drain()

    resp = await read_exactly(2)
    if resp[0] != 5:
        writer.close()
        raise ConnectionError(f"SOCKS5 version mismatch: {resp[0]}")

    auth_method = resp[1]
    if auth_method == 0xFF:
        writer.close()
        raise ConnectionError("SOCKS5: no acceptable auth method")
    if auth_method not in offered_methods:
        writer.close()
        raise ConnectionError("SOCKS5: server selected an unoffered auth method")

    if auth_method == 0x02:
        # Username/password auth
        if not username:
            writer.close()
            raise ConnectionError("SOCKS5: server requires auth but no credentials")
        u_bytes = username.encode("utf-8")
        p_bytes = (password or "").encode("utf-8")
        writer.write(
            b"\x01"
            + bytes([len(u_bytes)])
            + u_bytes
            + bytes([len(p_bytes)])
            + p_bytes
        )
        await writer.drain()
        auth_resp = await read_exactly(2)
        if auth_resp[0] != 1 or auth_resp[1] != 0:
            writer.close()
            raise ConnectionError("SOCKS5: auth failed")

    # Both accepted SOCKS URL spellings keep target-name resolution at the
    # configured proxy. Literal targets remain literals.
    normalized_host = _normalize_host(target_host)
    literal_ip = _parse_ip_literal(normalized_host)
    loose_ip = _loose_ipv4_literal(normalized_host)
    if literal_ip is None and loose_ip is None:
        host_bytes = target_host.encode("utf-8")
        if not host_bytes or len(host_bytes) > 255:
            writer.close()
            raise ConnectionError("SOCKS5: invalid target hostname length")
        writer.write(
            b"\x05\x01\x00\x03"
            + bytes([len(host_bytes)])
            + host_bytes
            + target_port.to_bytes(2, "big")
        )
    else:
        try:
            parsed_ip = ipaddress.ip_address(loose_ip or normalized_host)
            ip_bytes = parsed_ip.packed
            atyp = b"\x01" if parsed_ip.version == 4 else b"\x04"
            writer.write(
                b"\x05\x01\x00" + atyp + ip_bytes + target_port.to_bytes(2, "big")
            )
        except ValueError:
            writer.close()
            raise ConnectionError(f"SOCKS5: invalid target IP {target_host}")
    await writer.drain()

    # Read SOCKS5 response
    resp = await read_exactly(4)
    if resp[0] != 5:
        writer.close()
        raise ConnectionError(f"SOCKS5 response version mismatch: {resp[0]}")
    if resp[2] != 0:
        writer.close()
        raise ConnectionError("SOCKS5 response has a nonzero reserved byte")
    if resp[1] != 0:
        writer.close()
        raise ConnectionError(f"SOCKS5 connect failed: status {resp[1]}")

    # Read and discard the bound address
    atyp = resp[3]
    if atyp == 1:
        await read_exactly(4 + 2)
    elif atyp == 3:
        length = (await read_exactly(1))[0]
        if length == 0:
            writer.close()
            raise ConnectionError("SOCKS5 response has an empty bound hostname")
        await read_exactly(length + 2)
    elif atyp == 4:
        await read_exactly(16 + 2)
    else:
        writer.close()
        raise ConnectionError(f"SOCKS5 response has unknown address type: {atyp}")

    return reader, writer


async def _connect_via_http_connect(
    proxy_host: str,
    proxy_port: int,
    target_host: str,
    target_port: int,
    username: str | None,
    password: str | None,
    scheme: str,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Establish an HTTP CONNECT tunnel to the target."""
    reader, writer = await _open_http_proxy_connection(
        proxy_host, proxy_port, scheme
    )

    connect_request = (
        f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
        f"Host: {target_host}:{target_port}\r\n"
    )
    if username and password:
        import base64

        credentials = base64.b64encode(
            f"{username}:{password}".encode("utf-8")
        ).decode("ascii")
        connect_request += f"Proxy-Authorization: Basic {credentials}\r\n"
    connect_request += "\r\n"

    writer.write(connect_request.encode("utf-8"))
    await writer.drain()

    # Read the CONNECT response
    response_line = await reader.readline()
    if not response_line:
        writer.close()
        raise ConnectionError("HTTP CONNECT: empty response from proxy")

    status_parts = response_line.decode("utf-8", errors="replace").split(None, 2)
    if len(status_parts) < 2 or status_parts[1] != "200":
        writer.close()
        raise ConnectionError(
            f"HTTP CONNECT failed: {response_line.decode('utf-8', errors='replace').strip()}"
        )

    # Read and discard headers until empty line
    while True:
        line = await reader.readline()
        if not line or line == b"\r\n":
            break

    return reader, writer


async def _open_http_proxy_connection(
    proxy_host: str,
    proxy_port: int,
    scheme: str,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a connection to an HTTP/HTTPS upstream proxy."""
    ssl_ctx = _https_proxy_ssl_context() if scheme == "https" else None
    proxy_addresses = await _resolve_upstream_proxy_endpoint(proxy_host, proxy_port)

    failures: list[str] = []
    for address in proxy_addresses:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    address,
                    proxy_port,
                    ssl=ssl_ctx,
                    server_hostname=proxy_host if ssl_ctx else None,
                ),
                timeout=TUNNEL_CONNECT_TIMEOUT,
            )
            break
        except (OSError, asyncio.TimeoutError) as exc:
            failures.append(f"{address}: {exc}")
    else:
        raise ConnectionError(
            "all upstream proxy addresses failed: " + "; ".join(failures)
        )
    if ssl_ctx:
        ssl_object = writer.get_extra_info("ssl_object")
        logger.debug(
            "Connected to HTTPS upstream proxy %s:%d with %s",
            proxy_host,
            proxy_port,
            ssl_object.version() if ssl_object else "unknown TLS version",
        )
    return reader, writer


async def _resolve_upstream_proxy_endpoint(
    proxy_host: str, proxy_port: int
) -> tuple[str, ...]:
    """Resolve and classify the operator-configured TCP proxy endpoint."""
    normalized_host = _normalize_host(proxy_host)
    literal_ip = _parse_ip_literal(normalized_host)
    if literal_ip is not None:
        if literal_ip.version == 4 and literal_ip.is_private:
            if _is_rfc1918(normalized_host):
                return (normalized_host,)
        reason = _ip_block_reason(normalized_host)
        if reason:
            raise ConnectionError(f"blocked upstream proxy address ({reason})")
        return (normalized_host,)

    hostname_reason = _hostname_block_reason(normalized_host)
    if normalized_host == "host.docker.internal":
        resolved = await _resolve_system_host(normalized_host, proxy_port)
        if not resolved:
            raise ConnectionError("system DNS returned no Docker-host proxy addresses")
        for address in resolved:
            parsed = _parse_ip_literal(address)
            if (
                parsed is None
                or parsed.is_loopback
                or parsed.is_link_local
                or parsed.is_multicast
                or parsed.is_unspecified
                or parsed.is_reserved
            ):
                raise ConnectionError("Docker-host proxy resolved to a forbidden address")
        return tuple(sorted(resolved))

    if hostname_reason is not None:
        raise ConnectionError(
            "upstream proxy hostname requires an ordinary public name, exact "
            "host.docker.internal, an RFC1918 literal, or an operator-local "
            "suffix with ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true"
        )

    if _is_rfc1918_system_dns_name(normalized_host):
        if not ALLOW_LAN_ENDPOINTS:
            raise ConnectionError(
                "operator-local upstream proxy name requires "
                "ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS=true"
            )
        resolved = await _resolve_system_host(normalized_host, proxy_port)
        if not resolved or not all(_is_rfc1918(address) for address in resolved):
            raise ConnectionError(
                "operator-local upstream proxy name did not resolve entirely "
                "to RFC1918 addresses"
            )
        return tuple(sorted(resolved))

    resolved = (
        await _resolve_target_host(normalized_host, proxy_port)
        if MYST_VPN_ENABLED
        else await _resolve_system_host(normalized_host, proxy_port)
    )
    if not resolved:
        raise ConnectionError("resolver returned no upstream proxy addresses")
    for address in resolved:
        reason = _ip_block_reason(address)
        if reason:
            raise ConnectionError(
                f"upstream proxy resolved to blocked {address} ({reason})"
            )
    return tuple(sorted(resolved))


async def _open_plain_http_forward_connection(
    target_host: str,
    target_port: int,
    validated_ips: tuple[str, ...],
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, bool]:
    """Open the connection used to forward a plain HTTP origin request.

    Returns ``(reader, writer, use_absolute_uri)``. HTTP/HTTPS upstream proxies
    expect absolute-form request targets for plain HTTP forwarding; direct and
    SOCKS paths expect origin-form.
    """
    if (
        _is_trusted_internal_destination(target_host, target_port)
        or _is_exact_host_exception(target_host)
        or (
            ROUTE_CLASS == "host"
            and ALLOW_LAN_ENDPOINTS
            and validated_ips
            and all(_is_rfc1918(ip) for ip in validated_ips)
        )
    ):
        reader, writer = await _open_validated_direct_connection(
            validated_ips, target_port
        )
        return reader, writer, False

    if not UPSTREAM_PROXY and not TOR_SOCKS_UNIX_PATH:
        reader, writer = await _open_validated_direct_connection(
            validated_ips, target_port
        )
        return reader, writer, False

    if TOR_SOCKS_UNIX_PATH:
        reader, writer = await _connect_via_upstream(
            target_host, target_port, validated_ips
        )
        return reader, writer, False

    scheme, proxy_host, proxy_port, proxy_user, proxy_pass = _parse_proxy_url(
        UPSTREAM_PROXY
    )
    if scheme in ("http", "https"):
        reader, writer = await _open_http_proxy_connection(
            proxy_host, proxy_port, scheme
        )
        return reader, writer, True
    if scheme in ("socks5", "socks5h"):
        reader, writer = await _connect_via_socks5(
            proxy_host,
            proxy_port,
            target_host,
            target_port,
            proxy_user,
            proxy_pass,
            scheme,
        )
        return reader, writer, False
    raise ValueError(f"Unsupported proxy scheme: {scheme}")


def _proxy_authorization_header() -> str | None:
    """Return Proxy-Authorization for HTTP upstream proxy forwarding."""
    if not UPSTREAM_PROXY:
        return None
    scheme, _proxy_host, _proxy_port, username, password = _parse_proxy_url(
        UPSTREAM_PROXY
    )
    if scheme not in ("http", "https") or not (username and password):
        return None
    import base64

    credentials = base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("ascii")
    return f"Basic {credentials}"


async def _open_origin_connection(
    target_host: str,
    target_port: int,
    use_tls: bool,
    validated_ips: tuple[str, ...],
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a connection to an origin, optionally upgrading through TLS."""
    reader, writer = await _connect_via_upstream(
        target_host, target_port, validated_ips
    )
    if use_tls:
        ssl_ctx = ssl.create_default_context()
        await writer.start_tls(ssl_ctx, server_hostname=target_host)
    return reader, writer


# ── Proxy server ─────────────────────────────────────────────────────────


_TOKEN_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_BYTE_TOKEN = rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
_CHUNK_QUOTED_VALUE = (
    rb'"(?:[\t !#-\[\]-~\x80-\xff]|\\[\t !-~\x80-\xff])*"'
)
_CHUNK_SIZE_LINE_RE = re.compile(
    rb"^(" + rb"[0-9A-Fa-f]+" + rb")"
    rb"(?:[ \t]*;[ \t]*"
    + _BYTE_TOKEN
    + rb"(?:[ \t]*=[ \t]*(?:"
    + _BYTE_TOKEN
    + rb"|"
    + _CHUNK_QUOTED_VALUE
    + rb"))?)*[ \t]*\r\n$"
)


def _parse_header_line(line: bytes) -> tuple[str, str]:
    """Parse one strict CRLF-terminated HTTP field line."""
    if not line.endswith(b"\r\n"):
        raise ValueError("HTTP header line must end with CRLF")
    field = line[:-2]
    if field[:1] in (b" ", b"\t"):
        raise ValueError("obsolete folded headers are not accepted")
    if b":" not in field:
        raise ValueError("malformed HTTP request header")
    raw_name, raw_value = field.split(b":", 1)
    try:
        name = raw_name.decode("ascii").lower()
    except UnicodeDecodeError as exc:
        raise ValueError("invalid HTTP header name") from exc
    if not _TOKEN_RE.fullmatch(name):
        raise ValueError("invalid HTTP header name")
    if any(byte < 0x20 and byte != 0x09 or byte == 0x7F for byte in raw_value):
        raise ValueError("HTTP header value contains a forbidden control character")
    return name, raw_value.decode("utf-8", errors="replace").strip(" \t")


def _header_values(headers: list[tuple[str, str]], name: str) -> list[str]:
    return [value for header_name, value in headers if header_name == name]


def _parse_request_framing(
    headers: list[tuple[str, str]],
) -> tuple[int | None, tuple[str, ...]]:
    """Validate request framing and return content length / transfer codings."""
    content_length_values: list[str] = []
    for value in _header_values(headers, "content-length"):
        content_length_values.extend(part.strip() for part in value.split(","))

    content_length: int | None = None
    if content_length_values:
        if any(
            not value or not value.isascii() or not value.isdecimal()
            for value in content_length_values
        ):
            raise ValueError("invalid Content-Length")
        parsed_lengths = {int(value, 10) for value in content_length_values}
        if len(parsed_lengths) != 1:
            raise ValueError("conflicting Content-Length values")
        content_length = parsed_lengths.pop()

    transfer_codings: list[str] = []
    for value in _header_values(headers, "transfer-encoding"):
        transfer_codings.extend(part.strip().lower() for part in value.split(","))
    if transfer_codings:
        if any(not coding or not _TOKEN_RE.fullmatch(coding) for coding in transfer_codings):
            raise ValueError("invalid Transfer-Encoding")
        if transfer_codings[-1] != "chunked" or transfer_codings.count("chunked") != 1:
            raise ValueError("Transfer-Encoding must end in exactly one chunked coding")
        if content_length is not None:
            raise ValueError("Content-Length and Transfer-Encoding must not be combined")

    return content_length, tuple(transfer_codings)


async def _read_body_bytes(reader: asyncio.StreamReader, size: int) -> bytes:
    try:
        return await reader.readexactly(size)
    except asyncio.IncompleteReadError as exc:
        raise ValueError("request body ended before its declared framing") from exc


async def _forward_request_body(
    client_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
    content_length: int | None,
    transfer_codings: tuple[str, ...],
) -> None:
    """Stream a validated fixed-length or chunked request body upstream."""
    if content_length is not None:
        remaining = content_length
        while remaining:
            data = await _read_body_bytes(client_reader, min(remaining, TUNNEL_BUFFER))
            upstream_writer.write(data)
            await upstream_writer.drain()
            remaining -= len(data)
        return

    if not transfer_codings:
        return

    while True:
        size_line = await client_reader.readline()
        size_match = _CHUNK_SIZE_LINE_RE.fullmatch(size_line)
        if size_match is None:
            raise ValueError("invalid chunk-size line or extension")
        chunk_size = int(size_match.group(1), 16)
        upstream_writer.write(size_line)
        await upstream_writer.drain()

        if chunk_size == 0:
            while True:
                trailer_line = await client_reader.readline()
                if trailer_line == b"\r\n":
                    upstream_writer.write(trailer_line)
                    await upstream_writer.drain()
                    return
                try:
                    trailer_name, _trailer_value = _parse_header_line(trailer_line)
                except ValueError as exc:
                    raise ValueError(f"invalid chunk trailer: {exc}") from exc
                if trailer_name in {
                    "connection",
                    "content-length",
                    "host",
                    "trailer",
                    "transfer-encoding",
                }:
                    raise ValueError("chunk trailer contains a forbidden field")
                upstream_writer.write(trailer_line)
                await upstream_writer.drain()

        remaining = chunk_size
        while remaining:
            data = await _read_body_bytes(client_reader, min(remaining, TUNNEL_BUFFER))
            upstream_writer.write(data)
            await upstream_writer.drain()
            remaining -= len(data)
        terminator = await _read_body_bytes(client_reader, 2)
        if terminator != b"\r\n":
            raise ValueError("chunk data is not followed by CRLF")
        upstream_writer.write(terminator)
        await upstream_writer.drain()


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Bidirectional pipe helper."""
    try:
        while True:
            data = await reader.read(TUNNEL_BUFFER)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Handle a single proxy request."""
    peer = "?"
    try:
        peer = writer.get_extra_info("peername")
    except Exception:
        pass

    try:
        client_rejection = await _allowed_client_reason(peer)
        if client_rejection:
            logger.warning("BLOCKED proxy client %s (%s)", peer, client_rejection)
            await _write_text_response(
                writer, 403, "Forbidden", "This policy listener accepts only its configured bridge."
            )
            return

        request_line = await asyncio.wait_for(reader.readline(), timeout=30)
        if not request_line:
            return
        if not request_line.endswith(b"\r\n"):
            await _write_text_response(
                writer, 400, "Bad Request", "HTTP request line must end with CRLF."
            )
            return

        request_str = request_line.decode("utf-8", errors="replace").strip()
        if not request_str:
            return

        parts = request_str.split()
        if len(parts) != 3 or parts[2] not in {"HTTP/1.0", "HTTP/1.1"}:
            await _write_text_response(
                writer,
                400,
                "Bad Request",
                "Bad proxy request.",
            )
            return

        method, target, _version = parts[0].upper(), parts[1], parts[2]

        # Read headers
        headers: list[tuple[str, str]] = []
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not line:
                await _write_text_response(
                    writer, 400, "Bad Request", "HTTP headers ended prematurely."
                )
                return
            if line == b"\r\n":
                break
            try:
                headers.append(_parse_header_line(line))
            except ValueError as exc:
                await _write_text_response(
                    writer, 400, "Bad Request", str(exc)
                )
                return

        try:
            _parse_request_framing(headers)
        except ValueError as exc:
            await _write_text_response(writer, 400, "Bad Request", str(exc))
            return

        if method == "CONNECT":
            await _handle_connect(target, reader, writer, peer)
        elif method in (
            "GET",
            "HEAD",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "OPTIONS",
        ):
            await _handle_forward_http(method, target, headers, reader, writer, peer)
        else:
            await _write_text_response(
                writer,
                405,
                "Method Not Allowed",
                "HTTP proxy method is not allowed by this wrapper.",
            )

    except asyncio.TimeoutError:
        logger.debug("Request from %s timed out reading request line", peer)
    except Exception as e:
        logger.error(
            "Error handling request from %s: %s: %s\n%s",
            peer,
            type(e).__name__,
            e,
            traceback.format_exc(),
        )
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _handle_connect(
    target: str,
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    peer: Any,
) -> None:
    """Handle a destination-validated CONNECT request."""
    # Parse target (host:port)
    try:
        target_host, target_port = _parse_authority(target, 443)
    except ValueError:
        await _write_text_response(
            client_writer,
            400,
            "Bad Request",
            "Invalid CONNECT target.",
        )
        return

    blocked, validated_ips = await _reject_blocked_destination(
        "CONNECT", target_host, target_port, client_writer, peer
    )
    if blocked:
        return

    if target_port == 80 and not _plain_http_allowed(
        target_host, target_port, validated_ips
    ):
        await _write_text_response(
            client_writer, 403, "Forbidden", HTTP_URL_BLOCK_MESSAGE
        )
        return

    try:
        upstream_reader, upstream_writer = await _connect_via_upstream(
            target_host,
            target_port,
            validated_ips,
        )
    except Exception as e:
        logger.warning(
            "CONNECT %s:%d failed to establish upstream: %s",
            target_host,
            target_port,
            e,
        )
        await _write_text_response(
            client_writer,
            502,
            "Bad Gateway",
            f"Failed to establish upstream tunnel: {e}",
        )
        return

    # Tell the client the tunnel is established
    client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await client_writer.drain()

    logger.debug("TUNNEL %s:%d", target_host, target_port)

    # Bidirectional pipe
    await asyncio.gather(
        _pipe(client_reader, upstream_writer),
        _pipe(upstream_reader, client_writer),
    )

async def _handle_forward_http(
    method: str,
    target: str,
    headers: list[tuple[str, str]],
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    peer: Any,
) -> None:
    """Handle plain HTTP proxy requests for general executor egress.

    HTTPS clients normally use CONNECT and are handled by _handle_connect().
    Plain HTTP requests are denied by default before upstream egress. If
    EGRESS_ALLOW_HTTP_URLS=true, they are forwarded after destination
    validation.
    """
    parsed = urlparse(target)
    if parsed.scheme:
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            await _write_text_response(
                client_writer,
                400,
                "Bad Request",
                "Absolute-form proxy targets must use http or https.",
            )
            return
        target_host = parsed.hostname or ""
        try:
            target_port = parsed.port or (
                443 if parsed.scheme.lower() == "https" else 80
            )
        except ValueError:
            await _write_text_response(
                client_writer, 400, "Bad Request", "Invalid HTTP proxy target port."
            )
            return
        use_tls = parsed.scheme.lower() == "https"
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
    else:
        host_values = _header_values(headers, "host")
        if len(host_values) != 1:
            await _write_text_response(
                client_writer,
                400,
                "Bad Request",
                "Origin-form proxy requests require exactly one Host header.",
            )
            return
        host_header = host_values[0]
        try:
            target_host, target_port = _parse_authority(host_header, 80)
        except ValueError:
            await _write_text_response(
                client_writer,
                400,
                "Bad Request",
                "Invalid HTTP proxy target.",
            )
            return
        use_tls = False
        path = target or "/"

    blocked, validated_ips = await _reject_blocked_destination(
        f"FORWARD {method}", target_host, target_port, client_writer, peer
    )
    if blocked:
        return

    if not use_tls and not _plain_http_allowed(
        target_host, target_port, validated_ips
    ):
        logger.info(
            "BLOCKED FORWARD %s http://%s:%d%s (HTTP URLs disabled) -> 403",
            method,
            target_host,
            target_port,
            path,
        )
        await _write_text_response(
            client_writer,
            403,
            "Forbidden",
            HTTP_URL_BLOCK_MESSAGE,
        )
        return

    try:
        if use_tls:
            upstream_reader, upstream_writer = await _open_origin_connection(
                target_host, target_port, use_tls, validated_ips
            )
            request_target = path
            proxy_authorization = None
        else:
            (
                upstream_reader,
                upstream_writer,
                use_absolute_uri,
            ) = await _open_plain_http_forward_connection(
                target_host, target_port, validated_ips
            )
            request_target = (
                f"http://{target_host}:{target_port}{path}"
                if use_absolute_uri
                else path
            )
            proxy_authorization = (
                _proxy_authorization_header() if use_absolute_uri else None
            )

        content_length, transfer_codings = _parse_request_framing(headers)
        request = f"{method} {request_target} HTTP/1.1\r\n"
        for name, value in headers:
            if name in (
                "connection",
                "proxy-connection",
                "proxy-authorization",
                "host",
                "content-length",
                "transfer-encoding",
            ):
                continue
            request += f"{name}: {value}\r\n"
        default_port = 443 if use_tls else 80
        display_host = f"[{target_host}]" if ":" in target_host else target_host
        host_authority = (
            display_host if target_port == default_port else f"{display_host}:{target_port}"
        )
        request += f"host: {host_authority}\r\n"
        if content_length is not None:
            request += f"content-length: {content_length}\r\n"
        if transfer_codings:
            request += f"transfer-encoding: {', '.join(transfer_codings)}\r\n"
        if proxy_authorization:
            request += f"proxy-authorization: {proxy_authorization}\r\n"
        request += "connection: close\r\n"
        request += "\r\n"

        upstream_writer.write(request.encode("utf-8"))
        await upstream_writer.drain()

        await _forward_request_body(
            client_reader,
            upstream_writer,
            content_length,
            transfer_codings,
        )

        logger.debug(
            "FORWARD %s %s:%d%s through %s listener",
            method,
            target_host,
            target_port,
            path,
            "TLS" if use_tls else "plain HTTP",
        )
        await _pipe(upstream_reader, client_writer)
        upstream_writer.close()
    except Exception as e:
        logger.warning(
            "FORWARD %s %s:%d%s failed: %s",
            method,
            target_host,
            target_port,
            path,
            e,
        )
        await _write_text_response(
            client_writer,
            502,
            "Bad Gateway",
            f"HTTP proxy forwarding failed: {e}",
        )
        return


# ── Server ────────────────────────────────────────────────────────────────


async def main() -> None:
    _validate_upstream_proxy_config()
    if not ALLOWED_CLIENT_HOSTS and not _listener_is_loopback_only():
        raise RuntimeError(
            "EGRESS_PROXY_ALLOWED_CLIENT_HOSTS must name at least one fixed bridge peer "
            "unless EGRESS_PROXY_HOST is a literal loopback address"
        )
    logger.info(
        "Restricted egress proxy starting on %s:%d "
        "(route_class: %s, upstream: %s, allowed_clients: %s, "
        "allow_http_urls: %s, block_internal_hosts: %s, "
        "trusted_internal_destinations: %s, target_dns: %s)",
        LISTEN_HOST,
        LISTEN_PORT,
        ROUTE_CLASS,
        _sanitized_upstream_proxy(),
        ", ".join(ALLOWED_CLIENT_HOSTS) or "loopback only",
        ALLOW_HTTP_URLS,
        ", ".join(sorted(BLOCKED_HOSTNAMES)),
        ", ".join(
            f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
            for host, port in sorted(TRUSTED_INTERNAL_DESTINATIONS)
        )
        or "none",
        _target_dns_mode(),
    )

    server = await asyncio.start_server(
        handle_request,
        LISTEN_HOST,
        LISTEN_PORT,
    )

    async with server:
        logger.info(
            "Restricted egress proxy listening on %s:%d (route_class=%s)",
            LISTEN_HOST,
            LISTEN_PORT,
            ROUTE_CLASS,
        )
        await server.serve_forever()


if __name__ == "__main__":
    try:
        if sys.argv[1:]:
            raise SystemExit("usage: final_hop_proxy.py")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(
            "Fatal error: %s: %s\n%s",
            type(e).__name__,
            e,
            traceback.format_exc(),
        )
        raise
