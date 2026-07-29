#!/usr/bin/env python3
"""Focused, networkless contract checks for the selected Obscura image."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> str:
    completed = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if completed.returncode:
        raise RuntimeError(completed.stdout.rstrip())
    return completed.stdout


def cleanup(*args: str) -> None:
    subprocess.run(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def wait_for_fixture(engine: str, container: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        completed = subprocess.run(
            [
                engine,
                "exec",
                container,
                "python3",
                "-c",
                "import urllib.request; "
                "assert urllib.request.urlopen("
                "'http://127.0.0.1:8080/health', timeout=1).read() == b'ok'",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if completed.returncode == 0:
            return
        time.sleep(0.25)
    raise RuntimeError("Obscura fixture server did not become ready")


def wait_for_obscura(
    engine: str, network: str, client_image: str, cdp_host: str
) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        completed = subprocess.run(
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
                "import json,urllib.request; "
                f"data=json.load(urllib.request.urlopen('http://{cdp_host}:9222/json/version', timeout=2)); "
                "assert data['Protocol-Version'] == '1.3'",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if completed.returncode == 0:
            return
        time.sleep(0.25)
    raise RuntimeError("Obscura CDP endpoint did not become ready")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container-bin", choices=("docker", "podman"), required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--client-image", required=True)
    parser.add_argument("--fixture-image", required=True)
    args = parser.parse_args()

    inspection = json.loads(
        run(args.container_bin, "image", "inspect", args.image)
    )[0]
    assert inspection["Config"]["Entrypoint"] == ["/obscura"]
    assert inspection["Config"].get("User") == "0"

    suffix = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    network = f"private-onyx-obscura-contract-{suffix}"
    fixture = f"private-onyx-obscura-fixture-{suffix}"
    obscura = f"private-onyx-obscura-contract-{suffix}"
    fixture_host = "fixture.example"
    cdp_host = "obscura.example"

    run(args.container_bin, "network", "create", "--internal", network)
    try:
        run(
            args.container_bin,
            "run",
            "--detach",
            "--name",
            fixture,
            "--network",
            network,
            "--network-alias",
            fixture_host,
            "--read-only",
            "--mount",
            f"type=bind,src={ROOT / 'tests/obscura_fixture_server.py'},"
            "dst=/fixture-server.py,readonly",
            args.fixture_image,
            "python3",
            "/fixture-server.py",
        )
        wait_for_fixture(args.container_bin, fixture)

        run(
            args.container_bin,
            "run",
            "--detach",
            "--name",
            obscura,
            "--network",
            network,
            "--network-alias",
            cdp_host,
            "--user",
            "65534:65534",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--env",
            "OBSCURA_ALLOW_PRIVATE_NETWORK=true",
            "--env",
            "OBSCURA_NAV_TIMEOUT_MS=10000",
            "--env",
            "OBSCURA_CDP_COMMAND_TIMEOUT_MS=15000",
            "--env",
            "OBSCURA_FETCH_TIMEOUT_MS=10000",
            "--env",
            "OBSCURA_NETWORK_BODY_BUFFER_ENTRIES=16",
            "--env",
            "OBSCURA_NETWORK_BODY_BUFFER_BYTES=2097152",
            "--env",
            "OBSCURA_IO_STREAM_MAX_ENTRIES=1",
            "--env",
            "OBSCURA_IO_STREAM_MAX_BYTES=2097152",
            "--env",
            "RUST_LOG=info",
            args.image,
            "serve",
            "--host",
            "0.0.0.0",
            "--port",
            "9222",
            "--stealth",
            "--max-connections",
            "15",
        )
        wait_for_obscura(args.container_bin, network, args.client_image, cdp_host)

        output = run(
            args.container_bin,
            "run",
            "--rm",
            "--network",
            network,
            "--entrypoint",
            "python",
            "--env",
            "PYTHONPATH=/obscura-client",
            "--env",
            f"OBSCURA_TEST_CDP_URL=ws://{cdp_host}:9222/devtools/browser",
            "--env",
            f"OBSCURA_TEST_BASE_URL=http://{fixture_host}:8080",
            "--mount",
            f"type=bind,src={ROOT / 'browser/obscura_client'},"
            "dst=/obscura-client,readonly",
            "--mount",
            f"type=bind,src={ROOT / 'tests/validate_obscura_runtime.py'},"
            "dst=/validation.py,readonly",
            args.client_image,
            "/validation.py",
        )
        assert "PINNED_OBSCURA_RUNTIME_CONTRACTS_OK" in output

        logs = run(args.container_bin, "logs", obscura)
        assert "Headless Browser v0.1.11-private-onyx-search-v1" in logs
        assert "Private Onyx patchset: search-submission-v1" in logs
        assert (
            "Stealth mode enabled "
            "(TLS fingerprint impersonation + tracker blocking)"
        ) in logs
        assert "Stealth mode enabled (tracker blocking)\n" not in logs
        assert "q=fixture" not in logs
        assert "refusing CDP connection: at --max-connections (15)" in logs
        assert "worker processes" not in logs
        print(output.rstrip())
        print("PINNED_OBSCURA_IMAGE_CONTRACT_OK")
    finally:
        cleanup(args.container_bin, "rm", "--force", obscura)
        cleanup(args.container_bin, "rm", "--force", fixture)
        cleanup(args.container_bin, "network", "rm", network)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
