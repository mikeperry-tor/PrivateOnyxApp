from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podman import startup_health


def _compose_capability_model(
    *, gw_priority: int | None = 1
) -> dict[str, object]:
    network: dict[str, object] = {}
    if gw_priority is not None:
        network["gw_priority"] = gw_priority
    return {
        "services": {
            "probe": {
                "healthcheck": {"test": ["NONE"], "start_interval": "5s"},
                "networks": {"default": network},
            }
        }
    }


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
    @patch.object(startup_health, "prepare_shared_data")
    @patch.object(startup_health, "_run")
    def test_initialize_postgres_initializes_an_empty_bind(
        self, run, prepare
    ) -> None:
        with tempfile.TemporaryDirectory() as parent:
            directory = Path(parent) / "postgres"

            def initialize(command: list[str], *, check: bool = True):
                if command[-2:] == [
                    "-ec",
                    "cp -a /private-onyx-postgres-init/. /var/lib/postgresql/data/",
                ]:
                    (directory / "PG_VERSION").write_text("15\n", encoding="ascii")
                return subprocess.CompletedProcess(command, 0, stdout="")

            run.side_effect = initialize
            result = startup_health.initialize_postgres_data(
                "podman", str(directory), ("one.env", "two.env")
            )

        self.assertEqual(result, "initialized")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][0:3], ["podman", "volume", "create"])
        self.assertEqual(
            commands[1][0:7],
            [
                "podman",
                "compose",
                "--env-file",
                "one.env",
                "--env-file",
                "two.env",
                "run",
            ],
        )
        self.assertIn(startup_health.POSTGRES_ENTRYPOINT, commands[1])
        self.assertEqual(commands[1][-2:], ["relational_db", "postgres"])
        self.assertEqual(commands[2][0:2], ["podman", "exec"])
        self.assertEqual(commands[2][-1], "pg_isready")
        self.assertEqual(commands[3][0:3], ["podman", "stop", "--time"])
        self.assertEqual(
            commands[4][-3:],
            [
                "relational_db",
                "-ec",
                "cp -a /private-onyx-postgres-init/. /var/lib/postgresql/data/",
            ],
        )
        self.assertEqual(
            commands[5][0:3], ["podman", "rm", "--force"]
        )
        self.assertEqual(
            commands[6][0:4], ["podman", "volume", "rm", "--force"]
        )
        self.assertEqual(commands[0][-1], commands[6][-1])
        prepare.assert_called_once_with(postgres=str(directory))

    @patch.object(startup_health, "_run")
    def test_initialize_postgres_refuses_partial_data(self, run) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "partial").write_text("unknown\n", encoding="ascii")
            with self.assertRaisesRegex(
                startup_health.ContractError, "nonempty but not initialized"
            ):
                startup_health.initialize_postgres_data("podman", directory)
        run.assert_not_called()

    @patch.object(startup_health.uuid, "uuid4")
    @patch.object(startup_health, "_run")
    def test_initialize_postgres_removes_staging_volume_after_failure(
        self, run, uuid4
    ) -> None:
        uuid4.return_value.hex = "fixed"
        run.side_effect = [
            subprocess.CompletedProcess([], 0, stdout=""),
            subprocess.CalledProcessError(
                1, ["podman", "compose", "run"], stderr="init failed"
            ),
            subprocess.CompletedProcess([], 0, stdout=""),
        ]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                startup_health.ContractError, "init failed"
            ):
                startup_health.initialize_postgres_data("podman", directory)
        self.assertEqual(
            run.call_args_list[-1].args[0],
            [
                "podman",
                "volume",
                "rm",
                "--force",
                "private-onyx-postgres-init-fixed",
            ],
        )

    @patch.object(startup_health, "prepare_shared_data")
    @patch.object(startup_health, "_run")
    def test_initialize_postgres_reuses_an_initialized_bind(
        self, run, prepare
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "PG_VERSION").write_text("15\n", encoding="ascii")
            self.assertEqual(
                startup_health.initialize_postgres_data("podman", directory),
                "reused",
            )
        run.assert_not_called()
        prepare.assert_called_once_with(postgres=directory)

    @patch.object(startup_health, "prepare_shared_data")
    @patch.object(startup_health, "_run")
    def test_initialize_opensearch_initializes_an_empty_bind(
        self, run, prepare
    ) -> None:
        with tempfile.TemporaryDirectory() as parent:
            directory = Path(parent) / "opensearch"

            def initialize(command: list[str], *, check: bool = True):
                if command[-3:] == [
                    "opensearch",
                    "-ec",
                    "cp -a /private-onyx-opensearch-init/. "
                    "/usr/share/opensearch/data/",
                ]:
                    (directory / "nodes").mkdir()
                return subprocess.CompletedProcess(command, 0, stdout="")

            run.side_effect = initialize
            result = startup_health.initialize_opensearch_data(
                "podman", str(directory), ("one.env", "two.env")
            )

        self.assertEqual(result, "initialized")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][0:3], ["podman", "volume", "create"])
        self.assertEqual(
            commands[1][0:7],
            [
                "podman",
                "compose",
                "--env-file",
                "one.env",
                "--env-file",
                "two.env",
                "run",
            ],
        )
        self.assertEqual(commands[1][-1], "opensearch")
        self.assertEqual(commands[2][0:2], ["podman", "exec"])
        self.assertEqual(commands[2][-1], startup_health.OPENSEARCH_READY_COMMAND)
        self.assertEqual(commands[3][0:3], ["podman", "stop", "--time"])
        self.assertEqual(
            commands[4][-3:],
            [
                "opensearch",
                "-ec",
                "cp -a /private-onyx-opensearch-init/. "
                "/usr/share/opensearch/data/",
            ],
        )
        self.assertEqual(commands[5][0:3], ["podman", "rm", "--force"])
        self.assertEqual(commands[6][0:4], ["podman", "volume", "rm", "--force"])
        self.assertEqual(commands[0][-1], commands[6][-1])
        prepare.assert_called_once_with(opensearch=str(directory))

    @patch.object(startup_health, "_run")
    def test_initialize_opensearch_refuses_partial_data(self, run) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "partial").write_text("unknown\n", encoding="ascii")
            with self.assertRaisesRegex(
                startup_health.ContractError, "nonempty but not initialized"
            ):
                startup_health.initialize_opensearch_data("podman", directory)
        run.assert_not_called()

    @patch.object(startup_health.uuid, "uuid4")
    @patch.object(startup_health, "_run")
    def test_initialize_opensearch_removes_staging_volume_after_failure(
        self, run, uuid4
    ) -> None:
        uuid4.return_value.hex = "fixed"
        run.side_effect = [
            subprocess.CompletedProcess([], 0, stdout=""),
            subprocess.CalledProcessError(
                1, ["podman", "compose", "run"], stderr="init failed"
            ),
            subprocess.CompletedProcess([], 0, stdout=""),
        ]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                startup_health.ContractError, "init failed"
            ):
                startup_health.initialize_opensearch_data("podman", directory)
        self.assertEqual(
            run.call_args_list[-1].args[0],
            [
                "podman",
                "volume",
                "rm",
                "--force",
                "private-onyx-opensearch-init-fixed",
            ],
        )

    @patch.object(startup_health, "prepare_shared_data")
    @patch.object(startup_health, "_run")
    def test_initialize_opensearch_reuses_an_initialized_bind(
        self, run, prepare
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "nodes").mkdir()
            self.assertEqual(
                startup_health.initialize_opensearch_data("podman", directory),
                "reused",
            )
        run.assert_not_called()
        prepare.assert_called_once_with(opensearch=directory)

    @patch.object(startup_health.sys, "platform", "darwin")
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

    @patch.object(startup_health.sys, "platform", "darwin")
    @patch.object(startup_health, "_run")
    def test_prepare_shared_postgres_accepts_absent_override(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            [], 0, stdout="com.docker.grpcfuse.ownership\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "PG_VERSION").write_text("15\n", encoding="ascii")
            startup_health.prepare_shared_data(postgres=directory)
        self.assertEqual(run.call_count, 1)

    @patch.object(startup_health.sys, "platform", "linux")
    @patch.object(startup_health, "_run")
    def test_prepare_shared_postgres_skips_macos_xattr_on_linux(self, run) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "PG_VERSION").write_text("15\n", encoding="ascii")
            self.assertEqual(
                startup_health.prepare_shared_data(postgres=directory),
                ["PostgreSQL"],
            )
        run.assert_not_called()

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
                stdout="5.4.2\tlinux\n",
                stderr="",
            ),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="5.1.4\n", stderr=""),
            subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(_compose_capability_model()), stderr=""
            ),
            subprocess.CompletedProcess([], 0, stdout=flags, stderr=""),
        ]
        diagnostics = io.StringIO()
        with contextlib.redirect_stderr(diagnostics):
            self.assertEqual(startup_health.check_capability("podman"), "5.4.2")
        self.assertEqual(diagnostics.getvalue(), "")
        self.assertEqual(
            run.call_args_list[0].args[0],
            [
                "podman",
                "version",
                "--format",
                "{{.Server.Version}}\t{{.Server.Os}}",
            ],
        )

    @patch.object(startup_health, "_run")
    def test_capability_gate_warns_but_tests_older_podman(self, run) -> None:
        flags = "\n".join(sorted(startup_health.REQUIRED_UPDATE_FLAGS))
        run.side_effect = [
            subprocess.CompletedProcess(
                [],
                0,
                stdout="5.4.1\tlinux\n",
                stderr="",
            ),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="2.26.1\n", stderr=""),
            subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(_compose_capability_model()), stderr=""
            ),
            subprocess.CompletedProcess([], 0, stdout=flags, stderr=""),
        ]
        diagnostics = io.StringIO()
        with contextlib.redirect_stderr(diagnostics):
            self.assertEqual(startup_health.check_capability("podman"), "5.4.1")
        self.assertIn("older than the validated 5.4.2 baseline", diagnostics.getvalue())
        self.assertIn("continuing with explicit capability checks", diagnostics.getvalue())

    @patch.object(startup_health, "_run")
    def test_compose_capability_gate_rejects_silently_dropped_gateway(self, run) -> None:
        run.side_effect = [
            subprocess.CompletedProcess([], 0, stdout="2.26.1\n", stderr=""),
            subprocess.CompletedProcess(
                [],
                0,
                stdout=json.dumps(_compose_capability_model(gw_priority=None)),
                stderr="",
            ),
        ]
        with self.assertRaisesRegex(
            startup_health.ContractError, "silently drops.*gw_priority"
        ):
            startup_health.check_compose_capability("podman")

    @patch.object(startup_health, "_run")
    def test_compose_capability_gate_passes_required_model(self, run) -> None:
        run.side_effect = [
            subprocess.CompletedProcess([], 0, stdout="2.35.0\n", stderr=""),
            subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(_compose_capability_model()), stderr=""
            ),
        ]
        self.assertEqual(
            startup_health.check_compose_capability("podman"), "2.35.0"
        )
        self.assertEqual(
            run.call_args_list[1].kwargs["input_text"],
            startup_health.COMPOSE_CAPABILITY_MODEL,
        )

    @patch.object(startup_health, "_run")
    def test_capability_gate_reports_unusable_image_store(self, run) -> None:
        run.side_effect = [
            subprocess.CompletedProcess(
                [],
                0,
                stdout="5.8.2\tlinux\n",
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
            "Test": ["CMD-SHELL", "true"],
            "Interval": startup_health.STARTUP_INTERVAL_NS,
            "Timeout": 5_000_000_000,
            "Successes": 1,
        }
        startup_health._verify_startup(_container(startup=expected))
        for key, value in (
            ("Test", ["CMD-SHELL", "false"]),
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
                "Test": ["CMD-SHELL", "true"],
                "Interval": startup_health.STARTUP_INTERVAL_NS,
                "Timeout": 5_000_000_000,
                "Successes": 1,
            }
        )
        load_containers.side_effect = [[before], [after]]
        self.assertEqual(startup_health.configure_project("podman", "onyx"), 1)
        command = run.call_args.args[0]
        self.assertEqual(command[0:2], ["podman", "update"])
        self.assertIn("--health-startup-cmd=true", command)
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
            "Test": ["CMD-SHELL", "true"],
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

    def test_exec_health_command_is_shell_quoted_for_podman_update(self) -> None:
        regular = {
            "Test": ["CMD", "python", "-c", "print('one two')"],
        }
        self.assertEqual(
            startup_health._startup_test(regular),
            ["CMD-SHELL", "python -c 'print('\"'\"'one two'\"'\"')'"],
        )

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

    @patch.object(startup_health, "_load_containers")
    def test_post_wait_assertion_requires_fresh_running_health(
        self, load_containers
    ) -> None:
        healthy = _container(state="running")
        healthy = startup_health.ContainerHealth(
            healthy.container_id,
            "onyx-host-egress-proxy",
            healthy.state,
            healthy.regular,
            healthy.startup,
            "healthy",
        )
        bridge = startup_health.ContainerHealth(
            "bridge-id",
            "onyx-host-egress-bridge",
            "running",
            healthy.regular,
            healthy.startup,
            "starting",
        )
        load_containers.return_value = [healthy, bridge]
        with self.assertRaisesRegex(
            startup_health.ContractError,
            "onyx-host-egress-bridge: expected running/healthy",
        ):
            startup_health.assert_services_healthy(
                "podman",
                "onyx",
                ("onyx-host-egress-proxy", "onyx-host-egress-bridge"),
            )

        load_containers.return_value = [
            healthy,
            startup_health.ContainerHealth(
                bridge.container_id,
                bridge.service,
                bridge.state,
                bridge.regular,
                bridge.startup,
                "healthy",
            ),
        ]
        self.assertEqual(
            startup_health.assert_services_healthy(
                "podman",
                "onyx",
                ("onyx-host-egress-proxy", "onyx-host-egress-bridge"),
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
