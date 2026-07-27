from __future__ import annotations

import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
FETCHER_PATH = ROOT / "browser/obscura_image/fetch_release.py"


def load_fetcher():
    spec = importlib.util.spec_from_file_location("obscura_release_fetch", FETCHER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def release_archive(*, extra_member: bool = False) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, contents in (
            ("obscura", b"server"),
            ("obscura-worker", b"worker"),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(contents)
            archive.addfile(member, io.BytesIO(contents))
        if extra_member:
            member = tarfile.TarInfo("unexpected")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
    return output.getvalue()


class ObscuraReleaseFetchTests(unittest.TestCase):
    def invoke(self, archive: bytes, digest: str, output: Path) -> None:
        module = load_fetcher()
        argv = [
            str(FETCHER_PATH),
            "--version",
            "0.1.11",
            "--architecture",
            "amd64",
            "--amd64-sha256",
            digest,
            "--arm64-sha256",
            "0" * 64,
            "--output-dir",
            str(output),
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(module, "urlopen", return_value=io.BytesIO(archive)) as get,
        ):
            self.assertEqual(module.main(), 0)
        get.assert_called_once_with(
            "https://github.com/h4ckf0r0day/obscura/releases/download/"
            "v0.1.11/obscura-x86_64-linux-stealth.tar.gz",
            timeout=120,
        )

    def test_extracts_only_verified_executables(self) -> None:
        archive = release_archive()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "release"
            self.invoke(archive, hashlib.sha256(archive).hexdigest(), output)
            self.assertEqual((output / "obscura").read_bytes(), b"server")
            self.assertEqual((output / "obscura-worker").read_bytes(), b"worker")
            self.assertEqual((output / "obscura").stat().st_mode & 0o777, 0o755)
            self.assertEqual((output / "obscura-worker").stat().st_mode & 0o777, 0o755)

    def test_rejects_digest_mismatch_before_creating_output(self) -> None:
        archive = release_archive()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "release"
            with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
                self.invoke(archive, "f" * 64, output)
            self.assertFalse(output.exists())

    def test_rejects_archive_member_drift(self) -> None:
        archive = release_archive(extra_member=True)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "release"
            with self.assertRaisesRegex(RuntimeError, "members changed"):
                self.invoke(archive, hashlib.sha256(archive).hexdigest(), output)


if __name__ == "__main__":
    unittest.main()
