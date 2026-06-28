#!/usr/bin/env python3
"""Prefetch-blocking HTTP proxy for CRW.

Sits between CRW's HTTP prefetcher and the internet to eliminate the
double-hit problem: CRW unconditionally sends a bare reqwest GET (non-browser
TLS fingerprint, no JS) to every target URL before escalating to obscura's
stealth CDP browser. For anti-bot-protected search engines, this double-hit
from the same IP — first with a non-browser TLS fingerprint — is a strong
bot detection signal and a primary cause of 429s.

This proxy intercepts CRW's HTTP prefetch requests and:

1. **For known search engine URLs**: returns ``403 Forbidden`` immediately,
   without any network request. CRW's auto-mode renderer sees the 403
   (``is_auth_blocked``) and escalates to the CDP renderer (obscura). The
   search engine never sees the bare reqwest request — only obscura's stealth
   navigation.

2. **For all other URLs**: issues a ``HEAD`` request to the real target
   (through ``ONYX_AGENT_OUTBOUND_PROXY_URL`` if set) to check ``Content-Type``:
   - ``application/pdf`` → tunnels the original GET request through to the
     target and returns the full response (with body) so CRW's
     ``pdf_inspector`` can extract text.
   - Anything else → returns ``403 Forbidden`` so CRW escalates to CDP.

When ``ONYX_AGENT_OUTBOUND_PROXY_URL`` is set (e.g., Tor SOCKS proxy), the proxy
routes its own upstream requests (HEAD and tunnel) through that proxy. This
ensures the HEAD request and the PDF tunnel egress through the same proxy as
obscura, so the target sees a consistent exit IP.

Architecture::

    CRW :3010 ──HTTP proxy──> prefetch-blocking-proxy :3128
                                   │
                                   ├─ Search engine URL → 403 (no fetch)
                                   ├─ Other URL → HEAD → PDF? → tunnel GET
                                   │                        └─ 403
                                   │
                                   └── upstream ──> ONYX_AGENT_OUTBOUND_PROXY_URL (Tor/VPN)
                                                    (if set)

CRW is configured with ``HTTP_PROXY=http://127.0.0.1:3128`` and
``HTTPS_PROXY=http://127.0.0.1:3128`` so its reqwest HTTP fetcher routes
through this proxy while CDP/WebSocket traffic stays direct via ``NO_PROXY``.
The stack intentionally avoids ``CRW_CRAWLER__PROXY``; the CDP shim still
strips ``proxyServer`` from ``Target.createBrowserContext`` as a safety net if
that path is enabled later.

See ``docs/request_handling.md`` §1.6 for the full wait strategy and §1.7
for the prefetch-blocking proxy design.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import ssl
import time
import traceback
from typing import Any
from urllib.parse import urlparse

# ── Configuration ────────────────────────────────────────────────────────

LISTEN_HOST = os.environ.get("PREFETCH_PROXY_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("PREFETCH_PROXY_PORT", "3128"))

# Upstream proxy (ONYX_AGENT_OUTBOUND_PROXY_URL from .env.wrapper). When set,
# the proxy routes its own HEAD and tunnel requests through this upstream. Supports:
# http://, https://, socks5://, socks5h://
# When empty, requests go direct (through the VPN namespace).
UPSTREAM_PROXY = os.environ.get("ONYX_AGENT_OUTBOUND_PROXY_URL", "").strip()

# Search engine hostnames that should get an immediate 403 without any
# network request. These are the eTLD+1 of the four SearXNG stub engines.
SEARCH_ENGINE_HOSTS = frozenset(
    h.strip().lower()
    for h in os.environ.get(
        "PREFETCH_BLOCK_HOSTS",
        "google.com,search.brave.com,html.duckduckgo.com,startpage.com",
    ).split(",")
    if h.strip()
)

# Timeout for upstream HEAD requests (seconds).
HEAD_TIMEOUT = int(os.environ.get("PREFETCH_HEAD_TIMEOUT", "10"))

# Timeout for establishing a tunnel connection (seconds).
TUNNEL_CONNECT_TIMEOUT = int(os.environ.get("PREFETCH_TUNNEL_TIMEOUT", "15"))

# Buffer size for tunneling.
TUNNEL_BUFFER = 65536

# Max response body size for PDF tunneling (50 MB, matching CRW's limit).
MAX_PDF_BYTES = int(os.environ.get("PREFETCH_MAX_PDF_BYTES", str(50 * 1024 * 1024)))

logging.basicConfig(
    level=os.environ.get("PREFETCH_PROXY_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [prefetch-proxy] %(levelname)s %(message)s",
)
logger = logging.getLogger("prefetch-proxy")


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


async def _connect_via_upstream(
    target_host: str, target_port: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect to target_host:target_port through UPSTREAM_PROXY.

    Supports HTTP CONNECT, SOCKS5, and SOCKS5h proxies.
    Returns (reader, writer) for the established tunnel.
    """
    if not UPSTREAM_PROXY:
        # Direct connection
        return await asyncio.wait_for(
            asyncio.open_connection(target_host, target_port),
            timeout=TUNNEL_CONNECT_TIMEOUT,
        )

    scheme, proxy_host, proxy_port, proxy_user, proxy_pass = _parse_proxy_url(
        UPSTREAM_PROXY
    )

    if scheme in ("socks5", "socks5h"):
        return await _connect_via_socks5(
            proxy_host, proxy_port, target_host, target_port, proxy_user, proxy_pass, scheme
        )
    elif scheme in ("http", "https"):
        return await _connect_via_http_connect(
            proxy_host, proxy_port, target_host, target_port, proxy_user, proxy_pass, scheme
        )
    else:
        raise ValueError(f"Unsupported proxy scheme: {scheme}")


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
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(proxy_host, proxy_port),
        timeout=TUNNEL_CONNECT_TIMEOUT,
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

    # SOCKS5 connect request
    # For socks5h, the proxy resolves the hostname; for socks5, we resolve.
    if scheme == "socks5h":
        host_bytes = target_host.encode("utf-8")
        writer.write(
            b"\x05\x01\x00\x03"
            + bytes([len(host_bytes)])
            + host_bytes
            + target_port.to_bytes(2, "big")
        )
    else:
        # socks5: resolve locally, send IPv4 or IPv6
        try:
            addrs = await asyncio.get_event_loop().getaddrinfo(
                target_host, target_port, type=socket.SOCK_STREAM
            )
            addr_tuple = addrs[0][4]
            ip_bytes = socket.inet_pton(
                socket.AF_INET if len(addr_tuple) == 2 else socket.AF_INET6,
                addr_tuple[0],
            )
            atyp = b"\x01" if len(addr_tuple) == 2 else b"\x04"
            writer.write(
                b"\x05\x01\x00" + atyp + ip_bytes + target_port.to_bytes(2, "big")
            )
        except (socket.gaierror, OSError):
            writer.close()
            raise ConnectionError(f"SOCKS5: cannot resolve {target_host}")
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
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(proxy_host, proxy_port),
        timeout=TUNNEL_CONNECT_TIMEOUT,
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


# ── HEAD request for content-type detection ─────────────────────────────


async def _check_content_type(target_host: str, target_port: int, use_tls: bool, path: str) -> str | None:
    """Issue a HEAD request to the target and return the Content-Type, or None on error.

    For HTTPS targets, establishes a TLS connection through the upstream proxy
    (CONNECT tunnel + TLS handshake). For HTTP targets, connects directly.
    """
    try:
        if use_tls:
            if not UPSTREAM_PROXY:
                # Direct TLS connection
                ssl_ctx = ssl.create_default_context()
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        target_host, target_port, ssl=ssl_ctx, server_hostname=target_host
                    ),
                    timeout=TUNNEL_CONNECT_TIMEOUT,
                )
            else:
                # Connect through the upstream proxy, then upgrade to TLS.
                reader, writer = await _connect_via_upstream(target_host, target_port)
                loop = asyncio.get_event_loop()
                transport = writer.transport
                ssl_ctx = ssl.create_default_context()
                new_transport = await loop.start_tls(
                    transport,
                    transport.get_extra_info("socket"),
                    ssl_ctx,
                    server_hostname=target_host,
                )
                writer = asyncio.StreamWriter(
                    new_transport,
                    asyncio.StreamReaderProtocol(asyncio.StreamReader()),
                    reader,
                    loop,
                )
        else:
            # Plain HTTP
            reader, writer = await _connect_via_upstream(target_host, target_port)

        request = (
            f"HEAD {path} HTTP/1.1\r\n"
            f"Host: {target_host}\r\n"
            f"User-Agent: prefetch-proxy/1.0\r\n"
            f"Accept: */*\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        )
        writer.write(request.encode("utf-8"))
        await writer.drain()

        # Read response status line
        status_line = await asyncio.wait_for(reader.readline(), timeout=HEAD_TIMEOUT)
        if not status_line:
            writer.close()
            return None

        # Read headers
        content_type = None
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=HEAD_TIMEOUT)
            if not line or line == b"\r\n":
                break
            try:
                header_name, header_value = line.decode("utf-8", errors="replace").split(":", 1)
                if header_name.strip().lower() == "content-type":
                    content_type = header_value.strip().split(";")[0].strip().lower()
            except ValueError:
                pass

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        return content_type
    except Exception as e:
        logger.debug("HEAD request to %s:%d failed: %s", target_host, target_port, e)
        return None


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
    """Handle a single proxy request (CONNECT or GET/HEAD)."""
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
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await writer.drain()
            return

        method, target, _version = parts[0], parts[1], parts[2]

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
        elif method in ("GET", "HEAD"):
            await _handle_get(method, target, headers, reader, writer, peer)
        else:
            writer.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            await writer.drain()

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

    Non-search-engine hosts: tunnel through to the target. This accepts the
    double-hit for open_url URLs because:
    - PDFs over HTTPS require the tunnel (can't detect content-type without
      TLS interception/MITM, which would break reqwest's end-to-end TLS)
    - open_url URLs are one-off requests, not parallel fan-out, so they're
      less likely to trigger 429s
    - The OnyxWebCrawler path (Option A, direct HTTP) handles PDFs natively
      and is the default for open_url
    """
    # Parse target (host:port)
    if ":" in target:
        target_host, target_port_str = target.rsplit(":", 1)
        target_port = int(target_port_str)
    else:
        target_host = target
        target_port = 443

    # Search engine short-circuit: return 403 immediately
    if _is_search_engine(target_host):
        logger.info(
            "BLOCKED CONNECT %s:%d (search engine) → 403",
            target_host,
            target_port,
        )
        client_writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
        await client_writer.drain()
        return

    # Non-search-engine: tunnel through (accepts double-hit for open_url)
    try:
        upstream_reader, upstream_writer = await _connect_via_upstream(
            target_host, target_port
        )
    except Exception as e:
        logger.warning(
            "CONNECT %s:%d failed to establish upstream: %s",
            target_host,
            target_port,
            e,
        )
        client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        await client_writer.drain()
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


async def _handle_get(
    method: str,
    target: str,
    headers: dict[str, str],
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    peer: Any,
) -> None:
    """Handle a plain HTTP GET/HEAD request (non-CONNECT).

    For search engine hosts: return 403 immediately.
    For other hosts: do a HEAD check. If PDF, forward the GET. If not, 403.
    """
    # Parse the target URL
    if target.startswith("http://") or target.startswith("https://"):
        parsed = urlparse(target)
        target_host = parsed.hostname or ""
        target_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        use_tls = parsed.scheme == "https"
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
    else:
        # Relative URL — use Host header
        host_header = headers.get("host", "")
        if ":" in host_header:
            target_host, port_str = host_header.rsplit(":", 1)
            target_port = int(port_str)
        else:
            target_host = host_header
            target_port = 80
        use_tls = False
        path = target

    # Search engine short-circuit
    if _is_search_engine(target_host):
        logger.info(
            "BLOCKED %s %s:%d%s (search engine) → 403",
            method,
            target_host,
            target_port,
            path,
        )
        client_writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
        await client_writer.drain()
        return

    # For non-search-engine hosts, check content-type via HEAD
    content_type = await _check_content_type(target_host, target_port, use_tls, path)

    if content_type == "application/pdf":
        # Forward the original GET request through the tunnel
        logger.info(
            "TUNNEL %s %s:%d%s (PDF, forwarding) → tunnel",
            method,
            target_host,
            target_port,
            path,
        )
        try:
            upstream_reader, upstream_writer = await _connect_via_upstream(
                target_host, target_port
            )

            # Rebuild the request
            request = f"{method} {path} HTTP/1.1\r\n"
            for name, value in headers.items():
                request += f"{name}: {value}\r\n"
            request += "\r\n"
            upstream_writer.write(request.encode("utf-8"))
            await upstream_writer.drain()

            # Read any request body and forward
            content_length = int(headers.get("content-length", "0"))
            if content_length > 0:
                body = await client_reader.readexactly(content_length)
                upstream_writer.write(body)
                await upstream_writer.drain()

            # Pipe the response back to the client
            await _pipe(upstream_reader, client_writer)
            upstream_writer.close()
        except Exception as e:
            logger.warning(
                "TUNNEL %s %s:%d%s failed: %s",
                method,
                target_host,
                target_port,
                path,
                e,
            )
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await client_writer.drain()
    else:
        # Not a PDF — return 403 to force CDP escalation
        logger.info(
            "BLOCKED %s %s:%d%s (content-type: %s) → 403",
            method,
            target_host,
            target_port,
            path,
            content_type or "unknown",
        )
        client_writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
        await client_writer.drain()


# ── Server ────────────────────────────────────────────────────────────────


async def main() -> None:
    logger.info(
        "Prefetch-blocking proxy starting on %s:%d (upstream: %s, block_hosts: %s)",
        LISTEN_HOST,
        LISTEN_PORT,
        UPSTREAM_PROXY or "(direct)",
        ", ".join(sorted(SEARCH_ENGINE_HOSTS)),
    )

    server = await asyncio.start_server(
        handle_request,
        LISTEN_HOST,
        LISTEN_PORT,
    )

    async with server:
        logger.info(
            "Prefetch-blocking proxy listening on %s:%d",
            LISTEN_HOST,
            LISTEN_PORT,
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
