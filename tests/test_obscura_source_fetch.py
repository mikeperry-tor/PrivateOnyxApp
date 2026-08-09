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
FETCHER_PATH = ROOT / "browser/obscura_image/fetch_source.py"
SOURCE_REF = "97124edeb2ea610615e78f43e097454e3b221f6b"
ARCHIVE_ROOT = f"obscura-{SOURCE_REF}"


def load_fetcher():
    spec = importlib.util.spec_from_file_location("obscura_source_fetch", FETCHER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_archive(
    *extra_members: tuple[tarfile.TarInfo, bytes | None],
) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        root = tarfile.TarInfo(f"{ARCHIVE_ROOT}/")
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        directory = tarfile.TarInfo(f"{ARCHIVE_ROOT}/src/")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        contents = b"fn main() {}\n"
        source = tarfile.TarInfo(f"{ARCHIVE_ROOT}/src/main.rs")
        source.size = len(contents)
        archive.addfile(source, io.BytesIO(contents))
        for member, data in extra_members:
            if data is not None:
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            else:
                archive.addfile(member)
    return output.getvalue()


class ObscuraSourceFetchTests(unittest.TestCase):
    def invoke(self, archive: bytes, digest: str, output: Path) -> None:
        module = load_fetcher()
        argv = [
            str(FETCHER_PATH),
            "--ref",
            SOURCE_REF,
            "--sha256",
            digest,
            "--output-dir",
            str(output),
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(module, "urlopen", return_value=io.BytesIO(archive)) as get,
        ):
            self.assertEqual(module.main(), 0)
        get.assert_called_once_with(
            f"https://github.com/h4ckf0r0day/obscura/archive/{SOURCE_REF}.tar.gz",
            timeout=120,
        )

    def test_extracts_verified_regular_source_tree(self) -> None:
        archive = source_archive()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "source"
            self.invoke(archive, hashlib.sha256(archive).hexdigest(), output)
            self.assertEqual((output / "src/main.rs").read_bytes(), b"fn main() {}\n")

    def test_rejects_digest_mismatch_before_creating_output(self) -> None:
        archive = source_archive()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "source"
            with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
                self.invoke(archive, "f" * 64, output)
            self.assertFalse(output.exists())

    def test_rejects_traversal_wrong_root_and_non_regular_members(self) -> None:
        cases: list[tarfile.TarInfo] = []
        cases.append(tarfile.TarInfo(f"{ARCHIVE_ROOT}/../escape"))
        cases.append(tarfile.TarInfo("another-root/file"))
        link = tarfile.TarInfo(f"{ARCHIVE_ROOT}/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "src/main.rs"
        cases.append(link)
        device = tarfile.TarInfo(f"{ARCHIVE_ROOT}/device")
        device.type = tarfile.CHRTYPE
        cases.append(device)
        for member in cases:
            with self.subTest(member=member.name, kind=member.type):
                archive = source_archive((member, None))
                with tempfile.TemporaryDirectory() as temp:
                    output = Path(temp) / "source"
                    with self.assertRaises(RuntimeError):
                        self.invoke(
                            archive, hashlib.sha256(archive).hexdigest(), output
                        )
                    self.assertFalse(output.exists())

    def test_rejects_duplicate_paths_and_existing_output(self) -> None:
        duplicate = tarfile.TarInfo(f"{ARCHIVE_ROOT}/src/main.rs")
        archive = source_archive((duplicate, b"replacement"))
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "source"
            with self.assertRaisesRegex(RuntimeError, "duplicate"):
                self.invoke(archive, hashlib.sha256(archive).hexdigest(), output)
            self.assertFalse(output.exists())

            output.mkdir()
            module = load_fetcher()
            with self.assertRaises(FileExistsError):
                module.fetch_source(
                    ref=SOURCE_REF,
                    expected_digest=hashlib.sha256(archive).hexdigest(),
                    output_dir=output,
                )


if __name__ == "__main__":
    unittest.main()
