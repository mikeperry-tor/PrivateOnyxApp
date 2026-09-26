#!/usr/bin/env python3
"""Create an exclusive owner-only wallet file using the selected offline image."""

import argparse
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys

PROGRAM = """import json
from eth_account import Account
account = Account.create()
assert Account.from_key(account.key).address == account.address
print(json.dumps({"key": "0x" + account.key.hex().removeprefix("0x"), "address": account.address}))
"""


def create_wallet(container_bin, image, directory):
    # Only runtime connection settings are inherited by the CLI. No wrapper
    # environment, volumes, signer settings, or stack bootstrap enters it.
    environment = {name: os.environ[name] for name in (
        "PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
        "CONTAINER_HOST", "CONTAINER_CONNECTION", "XDG_RUNTIME_DIR",
    ) if name in os.environ}
    inspect = subprocess.run([container_bin, "image", "inspect", image],
                             env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if inspect.returncode:
        raise ValueError("Selected local image is missing; run make searxng-build.")
    directory = Path(directory).absolute()
    # Traverse through directory descriptors; a parent symlink swap cannot
    # redirect creation between validation and opening the wallet directory.
    descriptor = os.open(directory.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, part in enumerate(directory.parts[1:]):
            if index == len(directory.parts) - 2:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    except BaseException:
        os.close(descriptor)
        raise
    created = False
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("Wallet directory must be owned by this user with mode 0700.")
        # Reserve the filename before generating anything, including against
        # concurrent invocations. Never follow or replace a destination.
        fd = os.open("private-key.env", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=descriptor)
        created = True
        created_identity = os.fstat(fd)
        try:
            os.fchmod(fd, 0o600)
            result = subprocess.run([
                container_bin, "run", "--rm", "--pull", "never", "--network", "none",
                "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "--log-driver", "none", "--entrypoint", "/usr/local/searxng/.venv/bin/python",
                "-e", "PYTHONPATH=", image, "-I", "-c", PROGRAM,
            ], env=environment, capture_output=True, timeout=60)
            if result.returncode or len(result.stdout) > 1024:
                raise ValueError("Offline wallet generation failed.")
            payload = json.loads(result.stdout)
            if (not isinstance(payload, dict) or set(payload) != {"key", "address"}
                    or not re.fullmatch(r"0x[0-9a-fA-F]{64}", payload["key"])
                    or not re.fullmatch(r"0x[0-9a-fA-F]{40}", payload["address"])):
                raise ValueError("Invalid wallet generator output.")
            with os.fdopen(fd, "w", encoding="ascii") as stream:
                fd = None
                stream.write("SEARXNG_X402_PRIVKEY=" + payload["key"] + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(descriptor)
            return payload["address"], directory / "private-key.env"
        finally:
            if fd is not None:
                os.close(fd)
    except BaseException:
        if created:
            try:
                current = os.stat("private-key.env", dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                current = None
            if current is not None and (current.st_dev, current.st_ino) == (created_identity.st_dev, created_identity.st_ino):
                os.unlink("private-key.env", dir_fd=descriptor)
        raise
    finally:
        os.close(descriptor)


def main():
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container-bin", required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    try:
        address, path = create_wallet(args.container_bin, args.image, Path.cwd() / ".x402-wallet")
    except (Exception, KeyboardInterrupt):
        # Never render SDK, subprocess, or JSON exception content.
        print("Wallet creation failed; verify the local image (make searxng-build), directory permissions, and that no wallet already exists.", file=sys.stderr)
        return 1
    print(f"Address: {address}\nFile: {path}")
    print("Fund with a small amount of native USDC on Base. Manually copy the assignment into .env.wrapper after matching-mode make down-lite or make down-full, then start that mode. Enabling the key authorizes automatic uncapped spending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
