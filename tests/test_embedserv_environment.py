from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from embedserv import sync_environment


ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class EmbedservEnvironmentSyncTests(unittest.TestCase):
    def test_direct_dependency_pins_and_target_runtimes_are_explicit(self) -> None:
        embedserv_inputs = {
            line
            for line in (ROOT / "embedserv/requirements.in")
            .read_text(encoding="utf-8")
            .splitlines()
            if line and not line.startswith("#")
        }
        self.assertEqual(
            embedserv_inputs,
            {
                "mlx-openai-server==1.8.1",
                "mlx-embeddings==0.0.5",
                "mlx-lm==0.31.3",
                "huggingface_hub==1.16.1",
                "transformers==5.14.1",
                "typer==0.25.0",
            },
        )
        self.assertEqual(
            (ROOT / "searxng/requirements.in").read_text(encoding="utf-8"),
            "playwright==1.58.0\nwebsockets==17.0.1\n",
        )
        self.assertEqual(
            (ROOT / "executor/requirements.in").read_text(encoding="utf-8"),
            "sympy==1.14.0\n",
        )

        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("EMBEDSERV_PYTHON_VERSION := 3.12", makefile)
        self.assertIn("SEARXNG_PYTHON_VERSION := 3.14", makefile)
        self.assertIn("PYTHON_EXECUTOR_PYTHON_VERSION := 3.11", makefile)
        self.assertIn("--python-platform linux", makefile)
        self.assertIn("'$(EMBEDSERV_REQUIREMENTS)' '$(EMBEDSERV_ENV_SYNC)'", makefile)

    def test_success_replaces_environment_and_records_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            venv = root / "venv"
            venv.mkdir()
            (venv / "old-install").write_text("old", encoding="utf-8")
            requirements = root / "requirements.txt"
            requirements.write_text("# test lock\n", encoding="utf-8")

            def fake_run(command: list[str], *, uv_cache_dir: str) -> None:
                self.assertEqual(uv_cache_dir, str(root / "uv-cache"))
                if command[1] == "venv":
                    _write_executable(
                        venv / "bin" / "python",
                        "#!/bin/sh\nprintf '3.12\\n'\n",
                    )

            with mock.patch.object(sync_environment, "_run", side_effect=fake_run):
                sync_environment.synchronize(
                    venv=venv,
                    requirements=requirements,
                    python_version="3.12",
                    fingerprint="abc123",
                    stamp_name=".install-hash",
                    uv_cache_dir=str(root / "uv-cache"),
                )

            self.assertFalse((venv / "old-install").exists())
            self.assertEqual(
                (venv / ".install-hash").read_text(encoding="utf-8"),
                "abc123\n",
            )
            self.assertEqual(list(root.glob("venv.private-onyx-backup-*")), [])

    def test_failure_restores_previous_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            venv = root / "venv"
            venv.mkdir()
            (venv / "old-install").write_text("old", encoding="utf-8")
            requirements = root / "requirements.txt"
            requirements.write_text("# test lock\n", encoding="utf-8")

            def fake_run(command: list[str], *, uv_cache_dir: str) -> None:
                if command[1] == "venv":
                    _write_executable(
                        venv / "bin" / "python",
                        "#!/bin/sh\nprintf '3.12\\n'\n",
                    )
                    return
                raise subprocess.CalledProcessError(1, command)

            with (
                mock.patch.object(sync_environment, "_run", side_effect=fake_run),
                self.assertRaises(subprocess.CalledProcessError),
            ):
                sync_environment.synchronize(
                    venv=venv,
                    requirements=requirements,
                    python_version="3.12",
                    fingerprint="abc123",
                    stamp_name=".install-hash",
                    uv_cache_dir=str(root / "uv-cache"),
                )

            self.assertEqual((venv / "old-install").read_text(encoding="utf-8"), "old")
            self.assertEqual(list(root.glob("venv.private-onyx-backup-*")), [])

    def test_make_refreshes_only_a_stale_existing_bundled_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / "wrapper.env"
            env_file.write_text("", encoding="utf-8")
            venv = root / "venv"
            _write_executable(
                venv / "bin" / "python",
                "#!/bin/sh\nprintf '3.12\\n'\n",
            )
            _write_executable(venv / "bin" / "mlx-openai-server", "#!/bin/sh\nexit 0\n")
            model_cache = root / "models"
            (model_cache / "majentik/harrier-oss-v1-0.6b-MLX-8bit").mkdir(parents=True)
            calls = root / "sync-calls"
            fake_sync = root / "sync.py"
            fake_sync.write_text(
                f"""\
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--venv', type=Path, required=True)
parser.add_argument('--requirements')
parser.add_argument('--python-version')
parser.add_argument('--fingerprint', required=True)
parser.add_argument('--stamp-name', required=True)
parser.add_argument('--uv-cache-dir')
args = parser.parse_args()
(args.venv / args.stamp_name).write_text(args.fingerprint + '\\n')
Path({str(calls)!r}).write_text('called\\n')
""",
                encoding="utf-8",
            )
            fake_uv_dir = root / "bin"
            _write_executable(fake_uv_dir / "uv", "#!/bin/sh\nexit 0\n")
            command = [
                "make",
                "--no-print-directory",
                "embedserv-sync-if-installed",
                f"ENV_FILE={env_file}",
                f"EMBEDSERV_VENV={venv}",
                f"EMBEDSERV_MODEL_CACHE={model_cache}",
                f"EMBEDSERV_ENV_SYNC={fake_sync}",
                "EMBEDSERV_REQUIREMENTS=/dev/null",
                "EMBEDSERV_INSTALL_SOURCE_HASH=test-fingerprint",
                "EMBEDSERV_INSTALL_STAMP_NAME=.install-hash",
                f"EMBEDSERV_PID_FILE={root / 'missing.pid'}",
            ]
            environment = {
                **os.environ,
                "PATH": f"{fake_uv_dir}{os.pathsep}{os.environ['PATH']}",
            }

            first = subprocess.run(
                command, cwd=ROOT, env=environment, capture_output=True, text=True
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertTrue(calls.is_file())
            calls.unlink()

            second = subprocess.run(
                command, cwd=ROOT, env=environment, capture_output=True, text=True
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertFalse(calls.exists())
            self.assertIn("already matches", second.stdout)


if __name__ == "__main__":
    unittest.main()
