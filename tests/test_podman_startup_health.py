from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
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
    def test_capability_gate_refuses_non_podman_binary(self) -> None:
        with self.assertRaisesRegex(startup_health.ContractError, "non-Podman"):
            startup_health.check_capability("docker")

    @patch.object(startup_health, "_run")
    def test_bind_probe_uses_podman_without_network_or_pull(self, run) -> None:
        with tempfile.TemporaryDirectory() as source:
            self.assertEqual(
                startup_health.check_bind_mount("podman", source, "python:test"),
                os.path.abspath(source),
            )
        command = run.call_args.args[0]
        self.assertEqual(command[0:2], ["podman", "run"])
        self.assertIn("--rm", command)
        self.assertIn("--network=none", command)
        self.assertIn("--pull=never", command)
        self.assertTrue(any(arg.startswith("--mount=type=bind,") for arg in command))
        self.assertEqual(command[-2:], ["python:test", "/bin/true"])

    @patch.object(startup_health, "_run", side_effect=subprocess.CalledProcessError(125, []))
    def test_bind_probe_reports_machine_share_failure_without_path(self, _run) -> None:
        with tempfile.TemporaryDirectory() as source:
            with self.assertRaisesRegex(
                startup_health.ContractError, "machine cannot bind"
            ) as caught:
                startup_health.check_bind_mount("podman", source, "python:test")
        self.assertNotIn(source, str(caught.exception))

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
            subprocess.CompletedProcess([], 0, stdout="5.1.4\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout=flags, stderr=""),
        ]
        self.assertEqual(startup_health.check_capability("podman"), "5.8.1")

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
