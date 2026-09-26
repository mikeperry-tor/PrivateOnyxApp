"""Real loopback CONNECT/TLS/framing fixtures; no public networking."""
import asyncio
import importlib.util
import json
import socket
import socketserver
import ssl
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from searxng.engines import _x402_payment as payment
from tests.test_x402exa import KEY, challenge, encoded
from tests.x402_tls_fixture import CERT, KEY as TLS_KEY


@contextmanager
def proxy_fixture(mode="success"):
    with tempfile.TemporaryDirectory() as root:
        cert, key = Path(root) / "cert.pem", Path(root) / "key.pem"
        cert.write_text(CERT)
        key.write_text(TLS_KEY)
        server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_tls.load_cert_chain(cert, key)
        client_tls = ssl.create_default_context(cafile=str(cert))
        observed = []

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(2)
                raw = b""
                while not raw.endswith(b"\r\n\r\n"):
                    chunk = self.request.recv(1)
                    if not chunk:
                        return
                    raw += chunk
                observed.append(raw.split(b"\r\n", 1)[0])
                if not raw.startswith(b"CONNECT api.exa.ai:443 HTTP/1.1\r\n"):
                    return
                self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                try:
                    stream = server_tls.wrap_socket(self.request, server_side=True)
                except ssl.SSLError:
                    return  # Expected certificate-rejection fixture.
                with stream:
                    reader = stream.makefile("rb")
                    assert reader.readline() == b"POST /search HTTP/1.1\r\n"
                    headers = {}
                    while line := reader.readline():
                        if line == b"\r\n":
                            break
                        name, value = line.decode().split(":", 1)
                        headers[name.lower()] = value.strip()
                    body = reader.read(int(headers["content-length"]))
                    observed.append((headers, body))
                    if "payment-signature" not in headers:
                        response = ("HTTP/1.1 402 Payment Required\r\nPAYMENT-REQUIRED: " + encoded(challenge()) + "\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").encode()
                        stream.sendall(response)
                        return
                    receipt = encoded({"success": True, "transaction": "", "network": payment.NETWORK})
                    content = b'{"results":[]}'
                    extra = b""
                    length = len(content)
                    if mode == "gzip":
                        extra = b"Content-Encoding: gzip\r\n"
                    elif mode == "truncated":
                        length += 10
                    stream.sendall(("HTTP/1.1 200 OK\r\nPAYMENT-RESPONSE: " + receipt + "\r\nContent-Length: " + str(length) + "\r\nConnection: close\r\n").encode() + extra + b"\r\n")
                    if mode == "slow":
                        time.sleep(.2)
                        return
                    stream.sendall(content)

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
        server = Server(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}", client_tls, observed
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


@unittest.skipUnless(importlib.util.find_spec("x402"), "requires selected image SDK")
class TransportTests(unittest.TestCase):
    def test_connect_cold_warm_and_no_destination_dns(self):
        import httpx
        client = payment.PaymentClient(KEY)
        real_dns = socket.getaddrinfo
        def dns(host, *args, **kwargs):
            self.assertIn(host, ("127.0.0.1", b"127.0.0.1"))
            return real_dns(host, *args, **kwargs)
        with proxy_fixture() as (proxy, tls, observed), patch.object(socket, "getaddrinfo", dns):
            for _ in range(2):
                result = asyncio.run(payment.exchange(client, b"{}", time.monotonic()+2, payment.Outcome(), lambda:None,
                    transport=httpx.AsyncHTTPTransport(proxy=proxy, verify=tls, retries=0, trust_env=False)))
                self.assertEqual(result, {"results": []})
        self.assertEqual(len(observed), 6)
        self.assertNotIn("payment-signature", observed[1][0])
        self.assertIn("payment-signature", observed[3][0])
        self.assertIn("payment-signature", observed[5][0])

    def test_tls_rejection_and_route_loss_do_not_fall_back(self):
        import httpx
        client = payment.PaymentClient(KEY)
        with proxy_fixture() as (proxy, tls, observed):
            with self.assertRaises(httpx.ConnectError):
                asyncio.run(payment.exchange(client, b"{}", time.monotonic()+2, payment.Outcome(), lambda:None,
                    transport=httpx.AsyncHTTPTransport(proxy=proxy, verify=True, retries=0, trust_env=False)))
            self.assertEqual(len(observed), 1)
        with self.assertRaises(httpx.ConnectError):
            asyncio.run(payment.exchange(client, b"{}", time.monotonic()+2, payment.Outcome(), lambda:None,
                transport=httpx.AsyncHTTPTransport(proxy=proxy, verify=tls, retries=0, trust_env=False)))

    def test_receipt_is_retained_after_framing_decode_and_deadline_failure(self):
        import httpx
        for mode, error in (("truncated", httpx.RemoteProtocolError), ("gzip", httpx.DecodingError), ("slow", TimeoutError)):
            with self.subTest(mode=mode), proxy_fixture(mode) as (proxy, tls, observed):
                client = payment.PaymentClient(KEY)
                client.requirements = client.decode(encoded(challenge()))
                outcome = payment.Outcome()
                with self.assertRaises(error):
                    asyncio.run(payment.exchange(client, b"{}", time.monotonic()+(.1 if mode == "slow" else 2), outcome, lambda:None,
                        transport=httpx.AsyncHTTPTransport(proxy=proxy, verify=tls, retries=0, trust_env=False)))
                self.assertEqual(outcome.payment, "reported_success")
                self.assertEqual(len(observed), 2)


if __name__ == "__main__":
    unittest.main()
