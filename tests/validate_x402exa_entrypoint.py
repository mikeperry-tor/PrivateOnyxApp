"""Disposable real-entrypoint checks with public synthetic keys and no network."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time


def run(container_bin, image, root):
    base = [container_bin, "run", "--rm", "--pull", "never", "--network", "none",
            "--read-only", "--user", "65534:65534", "--cap-drop", "ALL",
            "--tmpfs", "/tmp", "--tmpfs", "/var/cache/searxng",
            "-e", "FORCE_OWNERSHIP=false", "-e", "SEARXNG_PORT=8888",
            "-e", "PYTHONPATH=/patches:/usr/local/lib:/usr/local/searxng",
            "-v", str(root / "searxng/patches") + ":/patches:ro",
            "-v", str(root / "searxng/core-config") + ":/etc/searxng:ro"]
    settings = {"SEARXNG_X402_PRIVKEY": "0" * 63 + "1", "SEARXNG_ROUND_ROBIN": "true",
                "GRANIAN_WORKERS": "1", "SEARXNG_X402_PROXY": "http://searxng-x402-egress-bridge:3128"}
    for changed in ({"SEARXNG_X402_PRIVKEY": "synthetic-invalid-key"},
                    {"SEARXNG_ROUND_ROBIN": "false"}, {"GRANIAN_WORKERS": "2"},
                    {"SEARXNG_X402_PROXY": "http://wrong.invalid:3128"}):
        env = [item for name, value in (settings | changed).items() for item in ("-e", name + "=" + value)]
        result = subprocess.run(base + env + [image], capture_output=True, timeout=30)
        assert result.returncode == 78, "real entrypoint did not fail closed"
        assert b"synthetic-invalid-key" not in result.stdout + result.stderr
    for enabled in (False, True):
        selection = settings | ({ } if enabled else {"SEARXNG_X402_PRIVKEY": ""})
        env = [item for name, value in selection.items() for item in ("-e", name + "=" + value)]
        container = subprocess.check_output(base + ["-d"] + env + [image], text=True).strip()
        try:
            deadline = time.monotonic() + 40
            program = 'import json,urllib.request; c=json.load(urllib.request.urlopen("http://127.0.0.1:8888/config",timeout=2)); print(json.dumps([e["name"] for e in c["engines"]]))'
            while True:
                probe = subprocess.run([container_bin, "exec", container, "/usr/local/searxng/.venv/bin/python", "-I", "-c", program], capture_output=True, text=True)
                if probe.returncode == 0:
                    engines = json.loads(probe.stdout)
                    assert ("x402exa" in engines) == enabled
                    assert len(engines) == (6 if enabled else 5)
                    break
                if time.monotonic() >= deadline:
                    raise AssertionError("real worker did not become ready")
                time.sleep(.2)
            logs = subprocess.check_output([container_bin, "logs", container], stderr=subprocess.STDOUT, text=True)
            assert logs.count("patched offline blocking-condition suspension") >= 2, "parent and worker bootstrap missing"
            assert "fatal SearXNG" not in logs
        finally:
            subprocess.run([container_bin, "stop", "-t", "2", container], check=True, stdout=subprocess.DEVNULL)
    print("PINNED_X402EXA_REAL_ENTRYPOINT_OK")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--container-bin", required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    run(args.container_bin, args.image, Path(__file__).resolve().parents[1])
