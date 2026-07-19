from __future__ import annotations

import json
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podman import startup_health


def _container(
    *,
    service: str = "api_server",
    state: str = "created",
    startup: dict | None = None,
) -> startup_health.ContainerHealth:
    regular = {
        "Test": ["CMD", "true"],
        "Interval": (
            startup_health.MYST_INTERVAL_NS
            if service == "myst-client"
            else startup_health.ORDINARY_INTERVAL_NS
        ),
        "Timeout": 5_000_000_000,
        "Retries": 1,
    }
    return startup_health.ContainerHealth(
        container_id="abc123",
        service=service,
        state=state,
        regular=regular,
        startup=startup,
    )


class PodmanStartupHealthTests(unittest.TestCase):
    @patch.object(startup_health, "_run")
    def test_prepare_shared_postgres_removes_only_mount_root_override(
        self, run
    ) -> None:
        run.side_effect = [
            subprocess.CompletedProcess(
                [], 0, stdout="com.docker.grpcfuse.ownership\nuser.containers.override_stat\n"
            ),
            subprocess.CompletedProcess([], 0, stdout=""),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "PG_VERSION").write_text("15\n", encoding="ascii")
            self.assertEqual(
                startup_health.prepare_shared_data(postgres=directory),
                ["PostgreSQL"],
            )
        self.assertEqual(run.call_args_list[0].args[0], ["xattr", directory])
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["xattr", "-d", startup_health.PODMAN_OVERRIDE_XATTR, directory],
        )

    @patch.object(startup_health, "_run")
    def test_prepare_shared_postgres_accepts_absent_override(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            [], 0, stdout="com.docker.grpcfuse.ownership\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "PG_VERSION").write_text("15\n", encoding="ascii")
            startup_health.prepare_shared_data(postgres=directory)
        self.assertEqual(run.call_count, 1)

    def test_prepare_shared_opensearch_requires_initialized_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                startup_health.ContractError, "OpenSearch data is not initialized"
            ):
                startup_health.prepare_shared_data(opensearch=directory)
            (Path(directory) / "nodes").mkdir()
            self.assertEqual(
                startup_health.prepare_shared_data(opensearch=directory),
                ["OpenSearch"],
            )

    def test_prepare_shared_data_requires_exactly_one_path(self) -> None:
        with self.assertRaisesRegex(startup_health.ContractError, "exactly one"):
            startup_health.prepare_shared_data()

    def test_capability_gate_refuses_non_podman_binary(self) -> None:
        with self.assertRaisesRegex(startup_health.ContractError, "non-Podman"):
            startup_health.check_capability("docker")

    @patch.object(startup_health.subprocess, "Popen")
    @patch.object(startup_health, "_run")
    def test_document_staging_uses_metadata_free_offline_tar_stream(
        self, run, popen
    ) -> None:
        tar_process = unittest.mock.Mock()
        tar_process.stdout = io.BytesIO(b"")
        tar_process.wait.return_value = 0
        receive_process = unittest.mock.Mock()
        receive_process.wait.return_value = 0
        popen.side_effect = [tar_process, receive_process]
        run.side_effect = [
            subprocess.CompletedProcess([], 1, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        ]

        with tempfile.TemporaryDirectory() as source:
            self.assertEqual(
                startup_health.stage_document_source(
                    "podman", source, "python:test", "onyx-rag-docs"
                ),
                "onyx-rag-docs",
            )

        tar_command = popen.call_args_list[0].args[0]
        self.assertEqual(tar_command[0], "tar")
        self.assertIn("--no-mac-metadata", tar_command)
        self.assertIn("--no-xattrs", tar_command)
        self.assertIn("--exclude=._*", tar_command)
        self.assertEqual(
            popen.call_args_list[0].kwargs["env"]["COPYFILE_DISABLE"], "1"
        )
        receive_command = popen.call_args_list[1].args[0]
        self.assertEqual(receive_command[0:2], ["podman", "run"])
        self.assertIn("--network=none", receive_command)
        self.assertIn("--pull=never", receive_command)
        self.assertIn("--interactive", receive_command)
        self.assertIn(
            "--mount=type=volume,src=onyx-rag-docs,target=/volume",
            receive_command,
        )
        rotate_command = run.call_args_list[-1].args[0]
        self.assertIn("/volume/.source-manifest", rotate_command[-3])
        self.assertIn("mv /volume/.previous /volume/docs", rotate_command[-3])
        self.assertRegex(rotate_command[-1], r"^[0-9a-f]{64}$")

    @patch.object(startup_health.subprocess, "Popen")
    @patch.object(startup_health, "_run")
    def test_document_staging_reuses_matching_cached_manifest(
        self, run, popen
    ) -> None:
        with tempfile.TemporaryDirectory() as source:
            manifest = startup_health._document_source_manifest(source)
            run.side_effect = [
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                subprocess.CompletedProcess(
                    [], 0, stdout=f"{manifest}\n", stderr=""
                ),
            ]
            self.assertEqual(
                startup_health.stage_document_source(
                    "podman", source, "python:test", "onyx-rag-docs"
                ),
                "onyx-rag-docs",
            )
        popen.assert_not_called()
        self.assertEqual(run.call_count, 2)

    @patch.object(startup_health.subprocess, "Popen")
    @patch.object(startup_health, "_run")
    def test_document_staging_preserves_previous_copy_on_stream_failure(
        self, run, popen
    ) -> None:
        tar_process = unittest.mock.Mock()
        tar_process.stdout = io.BytesIO(b"")
        tar_process.wait.return_value = 1
        receive_process = unittest.mock.Mock()
        receive_process.wait.return_value = 0
        popen.side_effect = [tar_process, receive_process]
        run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as source:
            with self.assertRaisesRegex(
                startup_health.ContractError,
                "previous staged copy was preserved.*archive status 1",
            ):
                startup_health.stage_document_source(
                    "podman", source, "python:test", "onyx-rag-docs"
                )
        scripts = [
            argument
            for call in run.call_args_list
            for argument in call.args[0]
            if isinstance(argument, str)
        ]
        self.assertFalse(any("mv /volume/.incoming" in item for item in scripts))

    def test_archive_error_class_never_returns_path_text(self) -> None:
        private_error = io.BytesIO(
            b"tar: ./private/customer.pdf: Cannot open: Permission denied\n"
        )
        self.assertEqual(
            startup_health._archive_error_class(private_error), "permission-denied"
        )

    def test_document_staging_rejects_invalid_volume_name(self) -> None:
        with tempfile.TemporaryDirectory() as source:
            with self.assertRaisesRegex(startup_health.ContractError, "volume name"):
                startup_health.stage_document_source(
                    "podman", source, "python:test", "bad/name"
                )

    @patch.object(startup_health, "_run")
    def test_capability_gate_checks_server_and_update_flags(self, run) -> None:
        flags = "\n".join(sorted(startup_health.REQUIRED_UPDATE_FLAGS))
        run.side_effect = [
            subprocess.CompletedProcess(
                [],
                0,
                stdout=json.dumps(
                    {"Server": {"Version": "5.8.1", "Os": "linux"}}
                ),
                stderr="",
            ),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="5.1.4\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout=flags, stderr=""),
        ]
        self.assertEqual(startup_health.check_capability("podman"), "5.8.1")

    @patch.object(startup_health, "_run")
    def test_capability_gate_reports_unusable_image_store(self, run) -> None:
        run.side_effect = [
            subprocess.CompletedProcess(
                [],
                0,
                stdout=json.dumps(
                    {"Server": {"Version": "5.8.2", "Os": "linux"}}
                ),
                stderr="",
            ),
            subprocess.CalledProcessError(125, ["podman", "images"]),
        ]
        with self.assertRaisesRegex(startup_health.ContractError, "image store"):
            startup_health.check_capability("podman")

    def test_regular_cadence_is_strict_for_myst_and_ordinary_services(self) -> None:
        startup_health._verify_regular(_container())
        startup_health._verify_regular(_container(service="myst-client"))
        drifted = _container()
        drifted.regular["Interval"] = 5_000_000_000
        with self.assertRaisesRegex(startup_health.ContractError, "regular health interval"):
            startup_health._verify_regular(drifted)

    def test_startup_contract_requires_exact_command_timing_and_success(self) -> None:
        expected = {
            "Test": ["CMD", "true"],
            "Interval": startup_health.STARTUP_INTERVAL_NS,
            "Timeout": 5_000_000_000,
            "Successes": 1,
        }
        startup_health._verify_startup(_container(startup=expected))
        for key, value in (
            ("Test", ["CMD", "false"]),
            ("Interval", 30_000_000_000),
            ("Timeout", 1_000_000_000),
            ("Successes", 2),
            ("Retries", 1),
        ):
            candidate = dict(expected)
            candidate[key] = value
            with self.assertRaises(startup_health.ContractError, msg=key):
                startup_health._verify_startup(_container(startup=candidate))

    @patch.object(startup_health, "_load_containers")
    @patch.object(startup_health, "_run")
    @patch.object(startup_health, "check_capability", return_value="5.8.1")
    def test_configure_copies_regular_command_before_start(
        self, _capability, run, load_containers
    ) -> None:
        before = _container()
        after = _container(
            startup={
                "Test": ["CMD", "true"],
                "Interval": startup_health.STARTUP_INTERVAL_NS,
                "Timeout": 5_000_000_000,
                "Successes": 1,
            }
        )
        load_containers.side_effect = [[before], [after]]
        self.assertEqual(startup_health.configure_project("podman", "onyx"), 1)
        command = run.call_args.args[0]
        self.assertEqual(command[0:2], ["podman", "update"])
        self.assertIn('--health-startup-cmd=["CMD","true"]', command)
        self.assertIn("--health-startup-interval=5s", command)
        self.assertIn("--health-startup-retries=0", command)

    @patch.object(startup_health, "_load_containers")
    @patch.object(startup_health, "check_capability", return_value="5.8.1")
    def test_running_container_without_native_startup_health_fails_closed(
        self, _capability, load_containers
    ) -> None:
        load_containers.return_value = [_container(state="running")]
        with self.assertRaisesRegex(startup_health.ContractError, "is absent"):
            startup_health.configure_project("podman", "onyx")


if __name__ == "__main__":
    unittest.main()
