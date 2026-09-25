"""Controlled provider boundary for the opt-in real-API browser recovery test.

Only the child API process replaces LLM completion. Native send, stream buffer,
reconnect, resume, authentication and database persistence remain in use. No
provider configuration or running service is patched; the listener is loopback.
"""
from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

PARAGRAPHS = tuple(f'Amber otter checkpoint {i:02d} ends here.' for i in range(12))
ANSWER = '\n\n'.join(PARAGRAPHS)
CASES = ('resume_running', 'complete_offline')


def wait_until(predicate, *, timeout=60, tick=None):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError('Timed out waiting for browser recovery fixture')
        (tick or time.sleep)(0.1)


def serve(directory: Path, fd: int):
    import threading
    import litellm
    import uvicorn
    from onyx.llm.multi_llm import LitellmLLM
    from onyx.main import app

    assert getattr(LitellmLLM.stream, '_wrapper_midstream_continuation', False)
    calls = {case: 0 for case in CASES}
    lock = threading.Lock()

    def completion(self, *args, **kwargs):
        # Titles and other nonstreaming metadata must not call a real provider.
        if not kwargs.get('stream'):
            return litellm.ModelResponse(choices=[{'message': {
                'role': 'assistant', 'content': 'Synthetic recovery validation'}}])
        prompt = str(kwargs.get('prompt', args[0] if args else ''))
        selected = [case for case in CASES if 'wrapper-recovery-' + case in prompt]
        assert len(selected) == 1, 'Unexpected inference in isolated fixture API'
        case = selected[0]
        with lock:
            calls[case] += 1
            assert calls[case] == 1, 'Recovery resubmitted inference'
        (directory / (case + '.started')).touch()

        def stream():
            for index, paragraph in enumerate(PARAGRAPHS):
                if index == 6:
                    (directory / (case + '.paused')).touch()
                    wait_until(lambda: (directory / (case + '.release')).exists(), timeout=120)
                yield litellm.ModelResponse(stream=True, choices=[{
                    'index': 0, 'delta': {'content': ('\n\n' if index else '') + paragraph},
                    'finish_reason': None}])
            yield litellm.ModelResponse(stream=True, choices=[{
                'index': 0, 'delta': {}, 'finish_reason': 'stop'}])
            (directory / (case + '.finished')).touch()
        return stream()

    LitellmLLM._completion = completion
    uvicorn.run(app(), fd=fd, access_log=False)


@contextmanager
def browser_proxy(api_port, directory=None):
    """Preserve browser origin/cookies and stream bytes without buffering EOF."""
    import http.client
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from urllib.parse import urlsplit

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_request(self):
            target = urlsplit(self.path)
            if target.scheme != 'http' or target.netloc != 'nginx':
                self.send_error(403, 'Fixture proxy only permits internal nginx')
                return
            is_chat = target.path.startswith('/api/chat/')
            upstream = http.client.HTTPConnection('127.0.0.1' if is_chat else 'nginx',
                                                   api_port if is_chat else 80, timeout=180)
            path = target.path.removeprefix('/api') if is_chat else target.path
            if target.query:
                path += '?' + target.query
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in ('connection', 'proxy-connection', 'host')}
            body = self.rfile.read(int(self.headers.get('content-length', '0')))
            done = threading.Event()
            interrupter = None
            if directory is not None and target.path == '/api/chat/send-chat-message':
                prompt = json.loads(body)['message']
                case = next(case for case in CASES if 'wrapper-recovery-' + case == prompt)

                def interrupt():
                    while not done.wait(0.05):
                        if (directory / (case + '.disconnect')).exists():
                            try:
                                self.connection.shutdown(socket.SHUT_RDWR)
                            except OSError as error:
                                import errno
                                if error.errno != errno.ENOTCONN:
                                    raise
                            (directory / (case + '.disconnected')).touch()
                            return
                interrupter = threading.Thread(target=interrupt, daemon=True)
                interrupter.start()
            try:
                upstream.request(self.command, path, body=body, headers=headers)
                response = upstream.getresponse()
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in ('connection', 'transfer-encoding', 'content-length'):
                        self.send_header(key, value)
                # Preserve detectable truncation: closing a connection-delimited
                # response would falsely report clean EOF after partial output.
                self.send_header('Transfer-Encoding', 'chunked')
                self.send_header('Connection', 'close')
                self.end_headers()
                if self.command != 'HEAD':
                    while data := response.read1(65536):
                        self.wfile.write(f'{len(data):x}\r\n'.encode() + data + b'\r\n')
                        self.wfile.flush()
                    self.wfile.write(b'0\r\n\r\n')
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                # Deliberate browser disconnection; Onyx owns continued generation.
                pass
            finally:
                done.set()
                if interrupter is not None:
                    interrupter.join(timeout=5)
                upstream.close()
                self.close_connection = True

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_request

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def fixture_api():
    # Keep diagnostics on failure, including the isolated process startup log.
    directory = Path(tempfile.mkdtemp(prefix='onyx-browser-recovery-'))
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    port = listener.getsockname()[1]
    with (directory / 'api.log').open('w') as log:
        process = subprocess.Popen([
            sys.executable, str(Path(__file__).resolve()), '--directory', str(directory),
            '--fd', str(listener.fileno()),
        ], pass_fds=(listener.fileno(),), stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, 'PYTHONPATH': '/app/wrapper-patches-api:/app/obscura-client:/app'})
        listener.close()
        try:
            import urllib.request
            import urllib.error
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def ready():
                assert process.poll() is None, f'Fixture API exited; see {directory}'
                try:
                    with opener.open(f'http://127.0.0.1:{port}/health', timeout=1) as response:
                        return response.status == 200
                except (urllib.error.URLError, TimeoutError):
                    return False
            wait_until(ready, timeout=120)
            with browser_proxy(port, directory) as proxy:
                yield proxy, directory
        finally:
            for case in CASES:
                (directory / (case + '.release')).touch()
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            print(f'Browser recovery diagnostics: {directory}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--fd', type=int, required=True)
    args = parser.parse_args()
    serve(args.directory, args.fd)
