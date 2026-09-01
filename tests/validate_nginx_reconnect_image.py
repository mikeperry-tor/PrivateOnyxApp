from __future__ import annotations

import argparse
import gzip
import os
import subprocess
import tempfile
import uuid
from pathlib import Path


class ValidationError(RuntimeError):
    pass


def run(command: list[str], *, check: bool = True, text: bool = True) -> subprocess.CompletedProcess:
    completed = subprocess.run(command, check=False, capture_output=True, text=text)
    if check and completed.returncode != 0:
        stderr = completed.stderr if text else completed.stderr.decode("utf-8", "replace")
        raise ValidationError(f"command failed ({completed.returncode}): {' '.join(command)}\n{stderr}")
    return completed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container-bin", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--client-image", required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    args = parser.parse_args()

    engine = args.container_bin
    image = args.image
    client_image = args.client_image
    root = args.repo_root.resolve()
    suffix = uuid.uuid4().hex[:12]
    network = f"private-onyx-nginx-validation-{suffix}"
    fixture = f"private-onyx-nginx-fixture-{suffix}"
    proxy = f"private-onyx-nginx-proxy-{suffix}"

    run([engine, "image", "inspect", image])
    version = run([engine, "run", "--rm", "--network", "none", "--entrypoint", "nginx", image, "-V"]).stderr
    if "--with-http_sub_module" not in version:
        raise ValidationError("selected nginx image lacks --with-http_sub_module")

    with tempfile.TemporaryDirectory(prefix="private-onyx-nginx-validation-") as directory:
        temporary = Path(directory)
        fixture_root = temporary / "fixture"
        fixture_root.mkdir()
        html = b"<!doctype html><html><head><title>fixture</title></head><body>ok</body></html>"
        javascript = b"window.privateOnyxFixture = 'unchanged';\n"
        (fixture_root / "index.html").write_bytes(html)
        (fixture_root / "asset.js").write_bytes(javascript)
        (fixture_root / "download.txt").write_bytes(b"download unchanged\n")
        fixture_conf = temporary / "nginx.conf"
        fixture_conf.write_text(
            "events {}\n"
            "http {\n"
            "  gzip on; gzip_min_length 1; gzip_types text/html application/javascript text/css application/json text/x-component;\n"
            "  server {\n"
            "    listen 3000; listen 8080; root /fixture;\n"
            "    add_header Content-Security-Policy \"default-src 'self'\" always;\n"
            "    add_header X-Fixture-Accept-Encoding $http_accept_encoding always;\n"
            "    location = / { try_files /index.html =404; }\n"
            "    location = /asset.js { try_files /asset.js =404; }\n"
            "    location = /data { default_type application/json; return 200 '{\"ok\":true,\"head\":\"</head>\"}'; }\n"
            "    location = /rsc { default_type text/x-component; return 200 'rsc </head> unchanged'; }\n"
            "    location = /download { default_type text/html; add_header Content-Disposition 'attachment; filename=download.html'; return 200 '<html><head></head><body>download</body></html>'; }\n"
            "    location = /missing { default_type text/html; return 404 '<html><head></head><body>missing</body></html>'; }\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )

        run([engine, "network", "create", network])
        try:
            run([
                engine, "run", "-d", "--name", fixture, "--network", network,
                "--network-alias", "fixture", "--read-only", "--tmpfs", "/var/cache/nginx",
                "--tmpfs", "/var/run", "-v", f"{fixture_conf}:/etc/nginx/nginx.conf:ro",
                "-v", f"{fixture_root}:/fixture:ro", image,
            ])
            run([
                engine, "run", "-d", "--name", proxy, "--network", network,
                "--tmpfs", "/var/cache/nginx",
                "--tmpfs", "/var/run",
                "-e", "ONYX_BACKEND_API_HOST=fixture", "-e", "ONYX_WEB_SERVER_HOST=fixture",
                "-e", "MCP_SERVER_ENABLED=false",
                "-v", f"{root / 'onyx/onyx_data/data/nginx'}:/nginx-templates:ro",
                "-v", f"{root / 'onyx/nginx/webui-csp.conf'}:/etc/nginx/conf.d/webui-csp.conf:ro",
                "-v", f"{root / 'onyx/nginx/webui-reconnect-http.conf'}:/etc/nginx/conf.d/webui-reconnect-http.conf:ro",
                "-v", f"{root / 'onyx/nginx/webui-reconnect-server.inc'}:/etc/nginx/wrapper/webui-reconnect-server.inc:ro",
                "-v", f"{root / 'onyx/nginx/webui-reconnect.js'}:/usr/share/private-onyx/webui-reconnect.js:ro",
                "-v", f"{root / 'onyx/nginx/run-nginx-wrapper.sh'}:/usr/local/bin/run-nginx-wrapper.sh:ro",
                "--entrypoint", "/usr/local/bin/run-nginx-wrapper.sh", image,
            ])
            state = run(
                [engine, "inspect", "--format", "{{.State.Running}}", proxy]
            ).stdout.strip()
            if state != "true":
                logs = run([engine, "logs", proxy], check=False).stdout
                errors = run([engine, "logs", proxy], check=False).stderr
                raise ValidationError(f"nginx reconnect fixture failed to start\n{logs}{errors}")

            def wget(path: str, *headers: str, server: str = proxy) -> bytes:
                command = [
                    engine,
                    "run",
                    "--rm",
                    "--network",
                    network,
                    "--entrypoint",
                    "wget",
                    image,
                ]
                for header in headers:
                    command.extend(("--header", header))
                command.extend(("-q", "-O", "-", f"http://{server}{path}"))
                return run(command, text=False).stdout

            def wget_headers(path: str, *headers: str) -> str:
                command = [
                    engine,
                    "run",
                    "--rm",
                    "--network",
                    network,
                    "--entrypoint",
                    "wget",
                    image,
                ]
                for header in headers:
                    command.extend(("--header", header))
                command.extend(("-S", "-O", "/dev/null", f"http://{proxy}{path}"))
                return run(command).stderr.lower()

            def raw_http(path: str) -> bytes:
                return run(
                    [
                        engine,
                        "run",
                        "--rm",
                        "--network",
                        network,
                        "--entrypoint",
                        "python",
                        client_image,
                        "-c",
                        "import sys,urllib.error,urllib.request; "
                        "url=sys.argv[1]; "
                        "\ntry: response=urllib.request.urlopen(url, timeout=5)"
                        "\nexcept urllib.error.HTTPError as error: response=error"
                        "\nsys.stdout.buffer.write(f'HTTP/1.1 {response.status}\\r\\n'.encode())"
                        "\nfor key,value in response.headers.items(): sys.stdout.buffer.write(f'{key}: {value}\\r\\n'.encode())"
                        "\nsys.stdout.buffer.write(b'\\r\\n'+response.read())",
                        f"http://{proxy}{path}",
                    ],
                    text=False,
                ).stdout

            tag = b'data-private-onyx-webui-reconnect'
            for headers in (
                ("Accept: text/html", "Accept-Encoding: gzip"),
                ("Accept: text/html", "Accept-Encoding: gzip", "Sec-Fetch-Dest: document"),
            ):
                body = wget("/", *headers)
                if body.count(tag) != 1 or not body.endswith(b"</html>"):
                    raise ValidationError("HTML did not receive exactly one intact companion tag")
                if body.startswith(b"\x1f\x8b"):
                    raise ValidationError("HTML upstream compression was not disabled")
                if "x-fixture-accept-encoding:" in wget_headers("/", *headers):
                    raise ValidationError("HTML request retained upstream Accept-Encoding")

            compressed_js = wget("/asset.js", "Accept: */*", "Accept-Encoding: gzip")
            decoded_js = gzip.decompress(compressed_js) if compressed_js.startswith(b"\x1f\x8b") else compressed_js
            if decoded_js != javascript:
                raise ValidationError("non-HTML asset bytes changed")
            if "x-fixture-accept-encoding: gzip" not in wget_headers(
                "/asset.js", "Accept: */*", "Accept-Encoding: gzip"
            ):
                raise ValidationError("non-HTML compression negotiation was stripped")

            for path in ("/api/data", "/rsc", "/download"):
                body = wget(path, "Accept: */*", "Accept-Encoding: identity")
                if tag in body:
                    raise ValidationError(f"companion was injected into excluded response {path}")

            missing = raw_http("/missing")
            if not missing.startswith(b"HTTP/1.1 404") or tag in missing:
                raise ValidationError(
                    "non-success HTML was injected or returned success\n"
                    + missing.decode("utf-8", "replace")
                )
            if b"content-security-policy:" not in missing.lower():
                raise ValidationError("error HTML lost the wrapper CSP policy")

            tracked = (root / "onyx/nginx/webui-reconnect.js").read_bytes()
            if wget("/_private-onyx/webui-reconnect.js") != tracked:
                raise ValidationError("served companion bytes differ from the tracked asset")
            unknown = raw_http("/_private-onyx/unknown.js")
            if not unknown.startswith(b"HTTP/1.1 404") or b"content-security-policy:" not in unknown.lower():
                raise ValidationError("unknown wrapper asset path reached an upstream")

            headers = run([
                engine, "run", "--rm", "--network", network, "--entrypoint", "wget", image,
                "-S", "-O", "/dev/null", f"http://{proxy}/",
            ], check=True).stderr.lower()
            if headers.count("content-security-policy:") < 2:
                raise ValidationError("normal HTML does not carry both CSP policies")
            asset_headers = run([
                engine, "run", "--rm", "--network", network, "--entrypoint", "wget", image,
                "-S", "-O", "/dev/null", f"http://{proxy}/_private-onyx/webui-reconnect.js",
            ]).stderr.lower()
            for expected_header in (
                "content-type: application/javascript",
                "x-content-type-options: nosniff",
                "cache-control: no-store",
            ):
                if expected_header not in asset_headers:
                    raise ValidationError(f"companion response lacks {expected_header}")
        finally:
            run([engine, "rm", "--force", proxy], check=False)
            run([engine, "rm", "--force", fixture], check=False)
            run([engine, "network", "rm", network], check=False)

    print("PINNED_NGINX_WEBUI_RECONNECT_CONTRACT_OK")


if __name__ == "__main__":
    main()
