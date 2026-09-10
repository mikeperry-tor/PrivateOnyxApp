"""Offline security floors and inherited runtime contracts for selected images."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("container-bin", "tailscale", "controller", "executor", "revision"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()

    def run(*command: str) -> str:
        return subprocess.check_output([args.container_bin, *command], text=True)

    images = [("tailscale", args.tailscale)]
    if "podman" not in Path(args.container_bin).name:
        images.extend((("controller", args.controller), ("executor", args.executor)))
    for kind, image in images:
        metadata = json.loads(run("image", "inspect", image))[0]
        assert metadata["Config"]["Labels"]["org.private-onyx.os-security-update-revision"] == args.revision
        assert metadata["Config"].get("User", "") in ("", "0", "root")
        if kind == "tailscale":
            script = """
set -eu
for package in libssl3 libcrypto3; do
    apk info --installed "$package>=3.5.8-r0"
done
tailscaled --version
test "$(tailscale version | head -n 1)" = 1.102.3
test -x /usr/local/bin/containerboot
"""
        else:
            script = """
set -eu
for package in openssl libssl3t64 openssl-provider-legacy; do
    version=$(dpkg-query -W -f='${Version}' "$package")
    dpkg --compare-versions "$version" ge '3.5.7-1~deb13u2'
done
python -c 'import ssl; print(ssl.OPENSSL_VERSION)'
"""
            if kind == "controller":
                script += "docker --version\n"
            else:
                script += """
uv pip check --python /opt/executor-venv/bin/python
python - <<'PY'
from io import BytesIO
import cryptography
from cryptography.hazmat.backends.openssl.backend import backend
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pypdf import PdfReader, PdfWriter
from pdfminer.high_level import extract_text
from reportlab.pdfgen import canvas
assert cryptography.__version__ == '50.0.1'
assert backend.openssl_version_text().startswith('OpenSSL 4.0.2 ')
cipher = AESGCM(AESGCM.generate_key(bit_length=128))
nonce = b'0' * 12
assert cipher.decrypt(nonce, cipher.encrypt(nonce, b'evidence', None), None) == b'evidence'
pdf = BytesIO()
c = canvas.Canvas(pdf)
c.drawString(20, 20, 'security fixture')
c.save()
pdf.seek(0)
assert 'security fixture' in extract_text(pdf)
pdf.seek(0)
writer = PdfWriter()
writer.append(PdfReader(pdf))
writer.encrypt('fixture-password', algorithm='AES-256')
encrypted = BytesIO()
writer.write(encrypted)
encrypted.seek(0)
reader = PdfReader(encrypted)
assert reader.decrypt('fixture-password')
assert 'security fixture' in reader.pages[0].extract_text()
print(cryptography.__version__, backend.openssl_version_text())
PY
"""
        print(run("run", "--rm", "--pull", "never", "--network", "none",
                  "--entrypoint", "sh", image, "-c", script), end="")
        if kind == "tailscale":
            linkage = run("run", "--rm", "--pull", "never", "--network", "none",
                          "--entrypoint", "scanelf", image, "-n", "-B",
                          "/usr/local/bin/tailscaled").split()
            assert linkage == ["ET_EXEC", "/usr/local/bin/tailscaled"], linkage
        print(f"SECURITY_IMAGE_OK {kind} {image}")


if __name__ == "__main__":
    main()
