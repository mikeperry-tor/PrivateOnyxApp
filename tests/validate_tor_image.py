#!/usr/bin/env python3
"""Focused, networkless contract checks for the selected Tor wrapper image."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tor.render_config import render_text


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


def wait_for_socket(engine: str, container: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        completed = subprocess.run(
            [
                engine,
                "exec",
                container,
                "/bin/sh",
                "-c",
                "test -S /run/tor-egress/socks && "
                "test -S /run/tor-control/control.sock && "
                "test -f /run/tor-control/control_auth_cookie",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if completed.returncode == 0:
            return
        time.sleep(0.25)
    raise RuntimeError("Tor did not create its Unix sockets and control cookie")


def validate_runtime_contract(
    *, engine: str, image: str, health_script: Path
) -> None:
    suffix = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    container = f"private-onyx-tor-contract-{suffix}"
    runtime_volume = f"private-onyx-tor-runtime-{suffix}"
    with tempfile.TemporaryDirectory(
        prefix="private-onyx-tor-runtime-"
    ) as directory:
        root = Path(directory)
        state = root / "state"
        state.mkdir(mode=0o700)
        torrc = root / "torrc"
        torrc.write_text(
            render_text(egress=True, onion=False, country="", fingerprints=()),
            encoding="ascii",
        )
        run(engine, "volume", "create", runtime_volume)
        try:
            command = [
                engine,
                "run",
                "--detach",
                "--name",
                container,
                "--network",
                "none",
                "--user",
                "101:102",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--entrypoint",
                "/usr/bin/tor",
                "--mount",
                f"type=bind,src={torrc},dst=/etc/tor/torrc,readonly",
                "--mount",
                f"type=bind,src={state},dst=/var/lib/tor",
                "--mount",
                f"type=bind,src={health_script},dst=/usr/local/bin/tor-healthcheck.py,readonly",
                "--mount",
                f"type=volume,src={runtime_volume},dst=/run/tor-egress",
            ]
            if engine == "podman":
                command.extend(
                    [
                        "--userns",
                        "keep-id:uid=101,gid=102",
                        "--sysctl",
                        "net.ipv4.ping_group_range=102 102",
                        "--tmpfs",
                        "/run/tor-control:U,mode=0700",
                    ]
                )
            else:
                command.extend(
                    [
                        "--tmpfs",
                        "/run/tor-control:uid=101,gid=102,mode=0700",
                    ]
                )
            command.extend([image, "-f", "/etc/tor/torrc"])
            run(*command)
            wait_for_socket(engine, container)

            inspection = json.loads(run(engine, "inspect", container))[0]
            assert inspection["Config"]["User"] == "101:102"
            assert inspection["HostConfig"]["ReadonlyRootfs"] is True
            assert not inspection["HostConfig"].get("PortBindings")
            destinations = {
                mount["Destination"]: mount for mount in inspection["Mounts"]
            }
            assert destinations["/var/lib/tor"]["Type"] == "bind"
            assert destinations["/var/lib/tor"]["RW"] is True
            assert destinations["/run/tor-egress"]["Type"] == "volume"
            assert destinations["/run/tor-egress"]["RW"] is True

            modes = run(
                engine,
                "exec",
                container,
                "/bin/sh",
                "-ec",
                """
test "$(stat -c '%a %u:%g' /run/tor-egress)" = "755 101:102"
test "$(stat -c '%a %u:%g' /run/tor-egress/socks)" = "666 101:102"
test "$(stat -c '%a %u:%g' /run/tor-control)" = "700 101:102"
test "$(stat -c '%a %u:%g' /run/tor-control/control.sock)" = "600 101:102"
test "$(stat -c '%a %u:%g' /run/tor-control/control_auth_cookie)" = "600 101:102"
echo TOR_RUNTIME_MODES_OK
""",
            )
            assert "TOR_RUNTIME_MODES_OK" in modes

            unauthenticated = run(
                engine,
                "exec",
                container,
                "python3",
                "-c",
                """
import importlib.util, socket
spec = importlib.util.spec_from_file_location("tor_health", "/usr/local/bin/tor-healthcheck.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
    connection.settimeout(5)
    connection.connect(module.SOCKET_PATH)
    connection.sendall(b"GETINFO status/bootstrap-phase\\r\\n")
    reply = module.read_reply(connection)
assert not reply[-1].startswith(b"250 ")
print("TOR_UNAUTHENTICATED_CONTROL_REJECTED")
""",
            )
            assert "TOR_UNAUTHENTICATED_CONTROL_REJECTED" in unauthenticated

            authenticated = run(
                engine,
                "exec",
                container,
                "python3",
                "-c",
                """
import importlib.util
spec = importlib.util.spec_from_file_location("tor_health", "/usr/local/bin/tor-healthcheck.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
progress = module.bootstrap_progress()
assert 0 <= progress <= 100
print("TOR_COOKIE_CONTROL_AUTHENTICATED")
""",
            )
            assert "TOR_COOKIE_CONTROL_AUTHENTICATED" in authenticated

            policy = run(
                engine,
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "65534:65534",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--entrypoint",
                "python3",
                "--mount",
                f"type=volume,src={runtime_volume},dst=/run/tor-egress,readonly",
                image,
                "-c",
                """
import socket
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
    connection.settimeout(5)
    connection.connect("/run/tor-egress/socks")
    connection.sendall(b"\\x05\\x01\\x00")
    assert connection.recv(2) == b"\\x05\\x00"
print("TOR_READ_ONLY_POLICY_SOCKET_OK")
""",
            )
            assert "TOR_READ_ONLY_POLICY_SOCKET_OK" in policy
        finally:
            cleanup(engine, "rm", "--force", container)
            cleanup(engine, "volume", "rm", "--force", runtime_volume)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("docker", "podman"), required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--base-image", required=True)
    args = parser.parse_args()

    inspection = json.loads(run(args.engine, "image", "inspect", args.image))[0]
    config = inspection["Config"]
    assert config.get("Entrypoint") == [
        "/sbin/tini",
        "--",
        "/usr/local/bin/entrypoint.sh",
    ]
    assert config.get("Volumes") == {"/var/lib/tor": {}}
    assert "/run/tor-egress" not in (config.get("Volumes") or {})
    base_inspection = json.loads(
        run(args.engine, "image", "inspect", args.base_image)
    )[0]
    base_layers = base_inspection["RootFS"]["Layers"]
    wrapper_layers = inspection["RootFS"]["Layers"]
    assert wrapper_layers[: len(base_layers)] == base_layers
    assert len(wrapper_layers) == len(base_layers) + 1

    command = [
        args.engine,
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        "/bin/sh",
        args.image,
    ]
    output = run(
        *command,
        "-ec",
        """
test "$(id -u tor):$(id -g tor)" = 101:102
test "$(stat -c '%a %u:%g' /run/tor-egress)" = "755 101:102"
test -r /usr/share/tor/geoip
test -r /usr/share/tor/geoip6
test -x /usr/bin/python3
tor --version | head -n 1
""",
    )
    assert "Tor version 0.4.9.11" in output

    with tempfile.TemporaryDirectory(prefix="private-onyx-tor-image-") as directory:
        torrc = Path(directory, "torrc")
        torrc.write_text(
            render_text(egress=True, onion=True, country="", fingerprints=()),
            encoding="ascii",
        )
        run(
            args.engine,
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "101:102",
            "--entrypoint",
            "/usr/bin/tor",
            "--mount",
            f"type=bind,src={torrc},dst=/etc/tor/torrc,readonly",
            args.image,
            "--verify-config",
            "-f",
            "/etc/tor/torrc",
        )

    validate_runtime_contract(
        engine=args.engine,
        image=args.image,
        health_script=Path(__file__).resolve().parents[1]
        / "tor"
        / "healthcheck.py",
    )

    print(f"Tor image contract passed with {args.engine}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
