#!/usr/bin/env python3
"""Destination-validating final-hop HTTP proxy.

Sits between CRW's HTTP prefetcher and the internet to eliminate the
double-hit problem: CRW unconditionally sends a bare reqwest GET (non-browser
TLS fingerprint, no JS) to every target URL before escalating to obscura's
stealth CDP browser. For anti-bot-protected search engines, this double-hit
from the same IP — first with a non-browser TLS fingerprint — is a strong
bot detection signal and a primary cause of 429s.

``EGRESS_PROXY_POLICY`` selects ``prefetch``, ``executor``, ``browser``, or
``searxng-external``. Prefetch/executor modes block configured search hosts;
browser modes allow them. Every mode blocks private/internal targets and uses
the same cleartext URL policy.

For prefetch traffic this proxy:

1. **For known search engine URLs**: returns ``403 Forbidden`` immediately,
   without any network request. CRW's auto-mode renderer sees the 403
   (``is_auth_blocked``) and escalates to the CDP renderer (obscura). The
   search engine never sees the bare reqwest request — only obscura's stealth
   navigation.

2. **For plain HTTP URLs**: returns ``403 Forbidden`` by default with a clear
   message telling callers to use ``https://`` instead. Set
   ``ONYX_AGENT_ALLOW_HTTP_URLS=true`` only when cleartext HTTP fetches are
   intentionally needed.

3. **For HTTPS URLs, and explicitly allowed plain HTTP URLs**: forwards the
   request to the target, through ``ONYX_AGENT_OUTBOUND_PROXY_URL`` if set.

Before any CONNECT tunnel or HTTP forwarding, the proxy rejects loopback,
private/RFC1918, link-local, multicast, reserved, and other non-global IP
destinations. It also rejects ``localhost``,
``host.docker.internal``, their subdomains, and single-label Docker-style
hostnames. When ``ONYX_AGENT_OUTBOUND_PROXY_URL`` is empty, DNS names are
resolved locally and all returned addresses are classified. When an upstream
proxy is set, target DNS resolution is intentionally skipped to avoid leaking
target DNS outside the configured proxy path.

When ``ONYX_AGENT_OUTBOUND_PROXY_URL`` is set (e.g., Tor SOCKS proxy), the proxy
routes its own upstream requests through that proxy. Restricted components
reach policy instances only through their local bridges.

Architecture::

    restricted component ──HTTP proxy──> local bridge ──> policy proxy
                                   │
                                   ├─ Search engine URL → 403 (no fetch)
                                   ├─ Internal/private target → 403
                                   ├─ Other HTTP URL → 403 unless explicitly allowed
                                   ├─ Other HTTPS URL → CONNECT tunnel
                                   │
                                   └── upstream ──> ONYX_AGENT_OUTBOUND_PROXY_URL (Tor/VPN)
                                                    (if set)

CRW is configured with ``HTTP_PROXY``/``HTTPS_PROXY`` pointing at its
restricted bridge. CDP/WebSocket traffic uses a separate peer network.
The stack intentionally avoids ``CRW_CRAWLER__PROXY``; the CDP shim still
strips ``proxyServer`` from ``Target.createBrowserContext`` as a safety net if
that path is enabled later.

Code-interpreter executor pods use a separate ``executor`` policy instance and
always see an ordinary local HTTP proxy, regardless of upstream proxy scheme.

See ``docs/request_handling.md`` §1.6 for the full wait strategy and §1.7
for the prefetch-blocking proxy design.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import ssl
import traceback
from typing import Any
from urllib.parse import urlparse

# ── Configuration ────────────────────────────────────────────────────────

LISTEN_HOST = os.environ.get("PREFETCH_PROXY_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("PREFETCH_PROXY_PORT", "3128"))

POLICY_MODE = os.environ.get("EGRESS_PROXY_POLICY", "prefetch").strip().lower()
if POLICY_MODE not in {"prefetch", "executor", "browser", "searxng-external"}:
    raise RuntimeError(f"Unsupported EGRESS_PROXY_POLICY={POLICY_MODE!r}")
BLOCK_SEARCH_ENGINES = POLICY_MODE in {"prefetch", "executor"}

# Upstream proxy (ONYX_AGENT_OUTBOUND_PROXY_URL from .env.wrapper). When set,
# the proxy routes its own requests through this upstream. Supports:
# http://, https://, socks5://, socks5h://
# When empty, requests go direct (through the VPN namespace).
UPSTREAM_PROXY = os.environ.get("ONYX_AGENT_OUTBOUND_PROXY_URL", "").strip()

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
# explicitly opts in.
ALLOW_HTTP_URLS = os.environ.get("ONYX_AGENT_ALLOW_HTTP_URLS", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
HTTP_URL_BLOCK_MESSAGE = (
    "HTTP URLs are disabled by ONYX_AGENT_ALLOW_HTTP_URLS=false. "
    "Use an https:// URL instead."
)

# Search engine hostnames that should get an immediate 403 without any
# network request. These are the hostnames/eTLD+1s of the SearXNG stub engines.
SEARCH_ENGINE_HOSTS = frozenset(
    h.strip().lower()
    for h in os.environ.get(
        "PREFETCH_BLOCK_HOSTS",
        "google.com,search.brave.com,html.duckduckgo.com,startpage.com,bing.com",
    ).split(",")
    if h.strip()
)

# Timeout for establishing a tunnel connection (seconds).
TUNNEL_CONNECT_TIMEOUT = int(os.environ.get("PREFETCH_TUNNEL_TIMEOUT", "15"))

# Buffer size for tunneling.
TUNNEL_BUFFER = 65536

# Hostnames that should never be proxied, even when an upstream proxy is
# configured and arbitrary DNS resolution is intentionally avoided.
BLOCKED_HOSTNAMES = frozenset(
    h.strip().lower().rstrip(".")
    for h in os.environ.get(
        "PREFETCH_BLOCK_INTERNAL_HOSTS",
        "localhost,host.docker.internal",
    ).split(",")
    if h.strip()
)

logging.basicConfig(
    level=os.environ.get("PREFETCH_PROXY_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [prefetch-proxy] %(levelname)s %(message)s",
)
logger = logging.getLogger("prefetch-proxy")


# ── Destination validation ────────────────────────────────────────────────


def _normalize_host(host: str) -> str:
    """Normalize a parsed host for comparison and IP classification."""
    host = host.strip().strip("[]").lower().rstrip(".")
    return host


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
    if not host:
        return "empty host"
    if "%" in host:
        return "IPv6 zone identifier"
    if host in BLOCKED_HOSTNAMES:
        return "blocked internal hostname"
    if any(host.endswith("." + blocked) for blocked in BLOCKED_HOSTNAMES):
        return "blocked internal hostname suffix"
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


async def _resolve_host(host: str, port: int) -> set[str]:
    """Resolve host to all stream addresses. Called only without upstream proxy."""
    loop = asyncio.get_running_loop()
    addrs = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    resolved: set[str] = set()
    for addr in addrs:
        sockaddr = addr[4]
        if sockaddr:
            resolved.add(sockaddr[0])
    return resolved


async def _validate_destination(
    host: str, port: int
) -> tuple[str | None, tuple[str, ...]]:
    """Validate a proxy target before any upstream connection is opened.

    Literal IPs and known internal hostnames are blocked in every mode. When no
    explicit upstream proxy is configured, DNS names are also resolved locally
    and every returned address is classified. When an upstream proxy is set,
    this function intentionally avoids DNS resolution to prevent DNS leakage.
    """
    host = _normalize_host(host)

    if not 0 < port <= 65535:
        return "invalid port", ()

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

    if UPSTREAM_PROXY:
        return None, ()

    try:
        resolved_ips = await _resolve_host(host, port)
    except (socket.gaierror, OSError) as e:
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

    logger.warning(
        "BLOCKED %s %s:%d (%s, peer=%s) -> 403",
        method,
        _normalize_host(host) or "(empty)",
        port,
        reason,
        peer,
    )
    writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
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
    port = parsed.port or (1080 if "socks" in scheme else 8080)
    username = parsed.username
    password = parsed.password
    return scheme, host, port, username, password


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
    if not UPSTREAM_PROXY:
        return await _open_validated_direct_connection(validated_ips, target_port)

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
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Establish a SOCKS5/SOCKS5h tunnel to the target."""
    reader, writer = await _open_http_proxy_connection(
        proxy_host, proxy_port, scheme
    )

    # SOCKS5 greeting: version 5, auth methods
    if username and password:
        writer.write(b"\x05\x02\x00\x02")  # 2 methods: no-auth, userpass
    else:
        writer.write(b"\x05\x01\x00")  # 1 method: no-auth
    await writer.drain()

    resp = await reader.readexactly(2)
    if resp[0] != 5:
        writer.close()
        raise ConnectionError(f"SOCKS5 version mismatch: {resp[0]}")

    auth_method = resp[1]
    if auth_method == 0xFF:
        writer.close()
        raise ConnectionError("SOCKS5: no acceptable auth method")

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
        auth_resp = await reader.readexactly(2)
        if auth_resp[1] != 0:
            writer.close()
            raise ConnectionError("SOCKS5: auth failed")

    # SOCKS5 connect request. Send DNS names to the proxy for both socks5 and
    # socks5h so an explicit upstream proxy does not trigger local DNS leaks.
    normalized_host = _normalize_host(target_host)
    literal_ip = _parse_ip_literal(normalized_host)
    loose_ip = _loose_ipv4_literal(normalized_host)
    if literal_ip is None and loose_ip is None:
        host_bytes = target_host.encode("utf-8")
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
    resp = await reader.readexactly(4)
    if resp[1] != 0:
        writer.close()
        raise ConnectionError(f"SOCKS5 connect failed: status {resp[1]}")

    # Read and discard the bound address
    atyp = resp[3]
    if atyp == 1:
        await reader.readexactly(4 + 2)
    elif atyp == 3:
        length = (await reader.readexactly(1))[0]
        await reader.readexactly(length + 2)
    elif atyp == 4:
        await reader.readexactly(16 + 2)

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
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(
            proxy_host,
            proxy_port,
            ssl=ssl_ctx,
            server_hostname=proxy_host if ssl_ctx else None,
        ),
        timeout=TUNNEL_CONNECT_TIMEOUT,
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
    if not UPSTREAM_PROXY:
        reader, writer = await _open_validated_direct_connection(
            validated_ips, target_port
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


def _is_search_engine(host: str) -> bool:
    """Check if the host matches a known search engine (by eTLD+1)."""
    host = host.lower()
    for se_host in SEARCH_ENGINE_HOSTS:
        if host == se_host or host.endswith("." + se_host):
            return True
    return False


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
        request_line = await asyncio.wait_for(reader.readline(), timeout=30)
        if not request_line:
            return

        request_str = request_line.decode("utf-8", errors="replace").strip()
        if not request_str:
            return

        parts = request_str.split()
        if len(parts) < 3:
            await _write_text_response(
                writer,
                400,
                "Bad Request",
                "Bad proxy request.",
            )
            return

        method, target, _version = parts[0].upper(), parts[1], parts[2]

        # Read headers
        headers: dict[str, str] = {}
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not line or line == b"\r\n":
                break
            try:
                name, value = line.decode("utf-8", errors="replace").split(":", 1)
                headers[name.strip().lower()] = value.strip()
            except ValueError:
                pass

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
    """Handle a CONNECT request (HTTPS tunnel).

    Search engine hosts: reject with 403 immediately (no network request).
    This forces CRW's auto mode to escalate to the CDP renderer (obscura),
    eliminating the double-hit where the search engine sees a bare reqwest
    GET with non-browser TLS followed by obscura's stealth navigation.

    Non-search-engine hosts: tunnel through to the target.
    """
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

    # Search engine short-circuit: return 403 immediately
    if BLOCK_SEARCH_ENGINES and _is_search_engine(target_host):
        logger.info(
            "BLOCKED CONNECT %s:%d (search engine) → 403",
            target_host,
            target_port,
        )
        await _write_text_response(
            client_writer,
            403,
            "Forbidden",
            "Search-engine prefetches are blocked locally so CRW uses the Obscura browser path.",
        )
        return

    if target_port == 80 and not ALLOW_HTTP_URLS:
        await _write_text_response(
            client_writer, 403, "Forbidden", HTTP_URL_BLOCK_MESSAGE
        )
        return

    blocked, validated_ips = await _reject_blocked_destination(
        "CONNECT", target_host, target_port, client_writer, peer
    )
    if blocked:
        return

    # Non-search-engine: tunnel through.
    try:
        upstream_reader, upstream_writer = await _connect_via_upstream(
            target_host, target_port, validated_ips
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

    logger.debug(
        "TUNNEL %s:%d (non-search-engine HTTPS, allowing)",
        target_host,
        target_port,
    )

    # Bidirectional pipe
    await asyncio.gather(
        _pipe(client_reader, upstream_writer),
        _pipe(upstream_reader, client_writer),
    )

async def _handle_forward_http(
    method: str,
    target: str,
    headers: dict[str, str],
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    peer: Any,
) -> None:
    """Handle plain HTTP proxy requests for general executor egress.

    HTTPS clients normally use CONNECT and are handled by _handle_connect().
    Plain HTTP requests are denied by default before upstream egress. If
    ONYX_AGENT_ALLOW_HTTP_URLS=true, they are forwarded after destination
    validation. Search-engine targets are still denied locally.
    """
    if target.startswith("http://") or target.startswith("https://"):
        parsed = urlparse(target)
        target_host = parsed.hostname or ""
        target_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        use_tls = parsed.scheme == "https"
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
    else:
        host_header = headers.get("host", "")
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

    if not use_tls and not ALLOW_HTTP_URLS:
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

    if BLOCK_SEARCH_ENGINES and _is_search_engine(target_host):
        logger.info(
            "BLOCKED FORWARD %s %s:%d%s (search engine) → 403",
            method,
            target_host,
            target_port,
            path,
        )
        await _write_text_response(
            client_writer,
            403,
            "Forbidden",
            "Search-engine prefetches are blocked locally so CRW uses the Obscura browser path.",
        )
        return

    blocked, validated_ips = await _reject_blocked_destination(
        f"FORWARD {method}", target_host, target_port, client_writer, peer
    )
    if blocked:
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

        request = f"{method} {request_target} HTTP/1.1\r\n"
        saw_host = False
        for name, value in headers.items():
            lower_name = name.lower()
            if lower_name in ("connection", "proxy-connection", "proxy-authorization"):
                continue
            if lower_name == "host":
                saw_host = True
            request += f"{name}: {value}\r\n"
        if not saw_host:
            request += f"host: {target_host}\r\n"
        if proxy_authorization:
            request += f"proxy-authorization: {proxy_authorization}\r\n"
        request += "connection: close\r\n"
        request += "\r\n"

        upstream_writer.write(request.encode("utf-8"))
        await upstream_writer.drain()

        content_length = int(headers.get("content-length", "0"))
        if content_length > 0:
            body = await client_reader.readexactly(content_length)
            upstream_writer.write(body)
            await upstream_writer.drain()

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
    logger.info(
        "Restricted egress proxy starting on %s:%d (policy: %s, upstream: %s, allow_http_urls: %s, block_hosts: %s, block_internal_hosts: %s, dns_internal_check: %s)",
        LISTEN_HOST,
        LISTEN_PORT,
        POLICY_MODE,
        UPSTREAM_PROXY or "(direct)",
        ALLOW_HTTP_URLS,
        ", ".join(sorted(SEARCH_ENGINE_HOSTS)),
        ", ".join(sorted(BLOCKED_HOSTNAMES)),
        "disabled (upstream proxy set)" if UPSTREAM_PROXY else "enabled",
    )

    server = await asyncio.start_server(
        handle_request,
        LISTEN_HOST,
        LISTEN_PORT,
    )

    async with server:
        logger.info(
            "Restricted egress proxy listening on %s:%d (policy=%s)",
            LISTEN_HOST,
            LISTEN_PORT,
            POLICY_MODE,
        )
        await server.serve_forever()


if __name__ == "__main__":
    try:
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
