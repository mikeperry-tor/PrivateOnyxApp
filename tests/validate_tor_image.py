#!/usr/bin/env python3
"""Focused, networkless contract checks for the selected Tor wrapper image."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("docker", "podman"), required=True)
    parser.add_argument("--image", required=True)
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

    print(f"Tor image contract passed with {args.engine}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
