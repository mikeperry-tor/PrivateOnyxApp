"""The browser fixture must preserve incremental delivery and restrict routing."""
import http.client
import json
from pathlib import Path
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from urllib.parse import urlsplit

from webui_recovery_fixture import browser_proxy, wait_until


class RecoveryFixtureTests(unittest.TestCase):
    def test_partial_stream_arrives_before_provider_completion(self):
        release = threading.Event()
        seen = []

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen.append(self.path)
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson')
                self.end_headers()
                self.wfile.write(b'first\n')
                self.wfile.flush()
                if release.wait(5):
                    self.wfile.write(b'last\n')

        upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        try:
            with browser_proxy(upstream.server_port) as address:
                target = urlsplit(address)
                client = http.client.HTTPConnection(target.hostname, target.port, timeout=2)
                try:
                    client.request('GET', 'http://nginx/api/chat/resume-stream?cursor=0')
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(6), b'first\n')
                    self.assertFalse(release.is_set())
                    release.set()
                    self.assertEqual(response.read(), b'last\n')
                    self.assertEqual(seen, ['/chat/resume-stream?cursor=0'])
                finally:
                    release.set()
                    client.close()
        finally:
            release.set()
            upstream.shutdown()
            upstream.server_close()
            thread.join(timeout=5)

    def test_disconnect_is_a_transport_error_not_clean_partial_eof(self):
        release = threading.Event()

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'first\n')
                self.wfile.flush()
                release.wait(5)

        upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp, browser_proxy(upstream.server_port, Path(tmp)) as address:
                target = urlsplit(address)
                client = http.client.HTTPConnection(target.hostname, target.port, timeout=2)
                try:
                    client.request('POST', 'http://nginx/api/chat/send-chat-message',
                                   body=json.dumps({'message': 'wrapper-recovery-resume_running'}))
                    response = client.getresponse()
                    self.assertEqual(response.read(6), b'first\n')
                    (Path(tmp) / 'resume_running.disconnect').touch()
                    wait_until(lambda: (Path(tmp) / 'resume_running.disconnected').exists(), timeout=2)
                    with self.assertRaises(http.client.IncompleteRead):
                        response.read()
                finally:
                    release.set()
                    client.close()
        finally:
            release.set()
            upstream.shutdown()
            upstream.server_close()
            thread.join(timeout=5)

    def test_proxy_rejects_any_other_destination(self):
        with browser_proxy(1) as address:
            target = urlsplit(address)
            for url in ('http://example.com/', 'http://nginx:1234/',
                        'http://nginx@127.0.0.1/', 'https://nginx/'):
                with self.subTest(url=url):
                    client = http.client.HTTPConnection(target.hostname, target.port, timeout=2)
                    try:
                        client.request('GET', url)
                        self.assertEqual(client.getresponse().status, 403)
                    finally:
                        client.close()


if __name__ == '__main__':
    unittest.main()
