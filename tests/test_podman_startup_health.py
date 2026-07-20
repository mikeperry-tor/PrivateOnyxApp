from __future__ import annotations

import json
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


def _expected(service: str = "api_server") -> dict[str, dict]:
    return {
        service: {
            "test": ["CMD", "true"],
            "interval": "1m" if service == "myst-client" else "10m",
            "timeout": "5s",
            "retries": 1,
        }
    }


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
    @patch.object(startup_health, "_load_expected_health", return_value=_expected())
    @patch.object(startup_health, "_run")
    @patch.object(startup_health, "check_capability", return_value="5.8.1")
    def test_configure_copies_regular_command_before_start(
        self, _capability, run, _expected_health, load_containers
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
    @patch.object(startup_health, "_load_expected_health", return_value=_expected())
    @patch.object(startup_health, "check_capability", return_value="5.8.1")
    def test_running_container_without_native_startup_health_fails_closed(
        self, _capability, _expected_health, load_containers
    ) -> None:
        load_containers.return_value = [_container(state="running")]
        with self.assertRaisesRegex(startup_health.ContractError, "is absent"):
            startup_health.configure_project("podman", "onyx")

    @patch.object(startup_health, "_load_containers")
    @patch.object(startup_health, "_load_expected_health", return_value=_expected())
    @patch.object(startup_health, "check_capability")
    def test_second_configuration_can_reuse_capability_result(
        self, capability, _expected_health, load_containers
    ) -> None:
        startup = {
            "Test": ["CMD", "true"],
            "Interval": startup_health.STARTUP_INTERVAL_NS,
            "Timeout": 5_000_000_000,
            "Successes": 1,
        }
        container = _container(state="running", startup=startup)
        load_containers.side_effect = [[container], [container]]
        self.assertEqual(
            startup_health.configure_project(
                "podman", "onyx", check_engine=False
            ),
            1,
        )
        capability.assert_not_called()

    def test_missing_compose_health_check_fails_closed(self) -> None:
        container = _container()
        container = startup_health.ContainerHealth(
            container.container_id,
            container.service,
            container.state,
            None,
            None,
        )
        with self.assertRaisesRegex(startup_health.ContractError, "missing: api_server"):
            startup_health._verify_expected_health_set([container], _expected())

    def test_regular_health_is_compared_to_effective_compose(self) -> None:
        expected = _expected()["api_server"]
        startup_health._verify_regular(_container(), expected)
        drifted = dict(expected)
        drifted["retries"] = 2
        with self.assertRaisesRegex(startup_health.ContractError, "Retries"):
            startup_health._verify_regular(_container(), drifted)


if __name__ == "__main__":
    unittest.main()
