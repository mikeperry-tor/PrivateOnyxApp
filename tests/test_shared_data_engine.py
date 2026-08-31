from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podman import shared_data_engine


class SharedDataEngineTests(unittest.TestCase):
    def test_running_standalone_myst_is_a_shared_data_writer(self) -> None:
        responses = (
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout="myst-client-vpn\n", stderr=""
            ),
        )
        with patch.object(shared_data_engine.subprocess, "run", side_effect=responses):
            self.assertEqual(
                shared_data_engine._running_shared_writers("docker"),
                {"myst-client"},
            )

    def test_claim_is_idempotent_for_same_engine_and_blocks_other(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "owner"
            self.assertEqual(
                shared_data_engine.claim(marker, "docker", inspect_commands=()),
                "docker",
            )
            self.assertEqual(
                shared_data_engine.claim(marker, "docker", inspect_commands=()),
                "docker",
            )
            with self.assertRaisesRegex(
                shared_data_engine.GuardError, "claimed by docker"
            ):
                shared_data_engine.claim(marker, "podman", inspect_commands=())

    def test_release_requires_matching_engine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "owner"
            shared_data_engine.claim(marker, "podman", inspect_commands=())
            with self.assertRaisesRegex(shared_data_engine.GuardError, "refusing"):
                shared_data_engine.release(marker, "docker")
            self.assertEqual(shared_data_engine.read_owner(marker), "podman")
            shared_data_engine.release(marker, "podman")
            self.assertIsNone(shared_data_engine.read_owner(marker))

    def test_docker_flavors_are_distinct_and_upgrade_legacy_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "owner"
            marker.write_text("docker\n", encoding="ascii")
            self.assertEqual(
                shared_data_engine.claim(
                    marker, "docker-rootless", inspect_commands=()
                ),
                "docker-rootless",
            )
            self.assertEqual(
                shared_data_engine.read_owner(marker), "docker-rootless"
            )
            with self.assertRaisesRegex(
                shared_data_engine.GuardError, "claimed by docker-rootless"
            ):
                shared_data_engine.claim(
                    marker, "docker-rootful", inspect_commands=()
                )
            shared_data_engine.release(marker, "docker-rootless")

    def test_invalid_marker_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "owner"
            marker.write_text("unknown\n", encoding="ascii")
            with self.assertRaisesRegex(shared_data_engine.GuardError, "invalid"):
                shared_data_engine.read_owner(marker)

    def test_first_claim_rejects_other_engine_running_shared_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "owner"
            with (
                patch.object(
                    shared_data_engine, "_available_command", return_value=True
                ),
                patch.object(
                    shared_data_engine,
                    "_running_shared_writers",
                    side_effect=lambda command: (
                        {"opensearch"} if command == "docker" else set()
                    ),
                ),
            ):
                with self.assertRaisesRegex(
                    shared_data_engine.GuardError, "docker has running"
                ):
                    shared_data_engine.claim(
                        marker, "podman", inspect_commands=("docker", "podman")
                    )
            self.assertFalse(marker.exists())

    def test_first_claim_fails_closed_when_engine_inspection_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "owner"
            with (
                patch.object(
                    shared_data_engine, "_available_command", return_value=True
                ),
                patch.object(
                    shared_data_engine,
                    "_running_shared_writers",
                    side_effect=shared_data_engine.GuardError("could not inspect docker"),
                ),
            ):
                with self.assertRaisesRegex(shared_data_engine.GuardError, "inspect"):
                    shared_data_engine.claim(marker, "docker")
            self.assertFalse(marker.exists())

    def test_first_docker_claim_skips_positively_stopped_podman_machine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "owner"
            with (
                patch.object(
                    shared_data_engine, "_available_command", return_value=True
                ),
                patch.object(
                    shared_data_engine,
                    "_running_shared_writers",
                    side_effect=(
                        set(),
                        shared_data_engine.GuardError("could not inspect podman"),
                    ),
                ),
                patch.object(
                    shared_data_engine,
                    "_podman_machine_is_stopped",
                    return_value=True,
                ) as stopped,
            ):
                self.assertEqual(
                    shared_data_engine.claim(
                        marker, "docker", inspect_commands=("docker", "podman")
                    ),
                    "docker",
                )
            stopped.assert_called_once_with("podman")

    def test_first_podman_claim_skips_docker_client_with_absent_local_endpoint(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "owner"
            with (
                patch.object(
                    shared_data_engine, "_available_command", return_value=True
                ),
                patch.object(
                    shared_data_engine,
                    "_running_shared_writers",
                    side_effect=(
                        shared_data_engine.GuardError("could not inspect docker"),
                        set(),
                    ),
                ),
                patch.object(
                    shared_data_engine,
                    "_docker_local_endpoint_is_absent",
                    return_value="unix:///var/run/docker.sock",
                ) as absent,
            ):
                self.assertEqual(
                    shared_data_engine.claim(
                        marker, "podman", inspect_commands=("docker", "podman")
                    ),
                    "podman",
                )
            absent.assert_called_once_with("docker")

    def test_selected_docker_failure_is_not_excused_by_absent_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "owner"
            with (
                patch.object(
                    shared_data_engine, "_available_command", return_value=True
                ),
                patch.object(
                    shared_data_engine,
                    "_running_shared_writers",
                    side_effect=shared_data_engine.GuardError("could not inspect docker"),
                ),
                patch.object(
                    shared_data_engine, "_docker_local_endpoint_is_absent"
                ) as absent,
            ):
                with self.assertRaisesRegex(shared_data_engine.GuardError, "docker"):
                    shared_data_engine.claim(
                        marker, "docker", inspect_commands=("docker",)
                    )
            absent.assert_not_called()
            self.assertFalse(marker.exists())

    def test_absent_docker_endpoint_requires_missing_local_unix_socket(self) -> None:
        inspected = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="unix:///var/run/docker.sock\n",
            stderr="",
        )
        with (
            patch.object(
                shared_data_engine.subprocess, "run", return_value=inspected
            ),
            patch.object(
                shared_data_engine.os.path, "lexists", return_value=False
            ),
        ):
            self.assertEqual(
                shared_data_engine._docker_local_endpoint_is_absent("docker"),
                "unix:///var/run/docker.sock",
            )

        for returncode, endpoint in (
            (1, ""),
            (0, "tcp://127.0.0.1:2375"),
            (0, "unix:///var/run/docker.sock\nunix:///tmp/other.sock"),
        ):
            with (
                patch.object(
                    shared_data_engine.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        args=[],
                        returncode=returncode,
                        stdout=endpoint,
                        stderr="",
                    ),
                ),
                patch.object(
                    shared_data_engine.os.path, "lexists", return_value=False
                ),
            ):
                self.assertIsNone(
                    shared_data_engine._docker_local_endpoint_is_absent("docker")
                )

        with (
            patch.object(
                shared_data_engine.subprocess, "run", return_value=inspected
            ),
            patch.object(
                shared_data_engine.os.path, "lexists", return_value=True
            ),
        ):
            self.assertIsNone(
                shared_data_engine._docker_local_endpoint_is_absent("docker")
            )

    def test_stopped_podman_machine_requires_exact_positive_state(self) -> None:
        with patch.object(
            shared_data_engine.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="stopped\n", stderr=""
            ),
        ):
            self.assertTrue(shared_data_engine._podman_machine_is_stopped("podman"))

        for returncode, stdout in ((0, "running\n"), (0, ""), (125, "stopped\n")):
            with patch.object(
                shared_data_engine.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=returncode, stdout=stdout, stderr=""
                ),
            ):
                self.assertFalse(
                    shared_data_engine._podman_machine_is_stopped("podman")
                )

    def test_first_claim_keeps_unknown_unselected_podman_failure_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "owner"
            with (
                patch.object(
                    shared_data_engine, "_available_command", return_value=True
                ),
                patch.object(
                    shared_data_engine,
                    "_running_shared_writers",
                    side_effect=shared_data_engine.GuardError("could not inspect podman"),
                ),
                patch.object(
                    shared_data_engine,
                    "_podman_machine_is_stopped",
                    return_value=False,
                ),
            ):
                with self.assertRaisesRegex(shared_data_engine.GuardError, "podman"):
                    shared_data_engine.claim(
                        marker, "docker", inspect_commands=("podman",)
                    )
            self.assertFalse(marker.exists())

    def test_selected_podman_failure_is_not_excused_by_stopped_machine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "owner"
            with (
                patch.object(
                    shared_data_engine, "_available_command", return_value=True
                ),
                patch.object(
                    shared_data_engine,
                    "_running_shared_writers",
                    side_effect=shared_data_engine.GuardError("could not inspect podman"),
                ),
                patch.object(
                    shared_data_engine, "_podman_machine_is_stopped"
                ) as stopped,
            ):
                with self.assertRaisesRegex(shared_data_engine.GuardError, "podman"):
                    shared_data_engine.claim(
                        marker, "podman", inspect_commands=("podman",)
                    )
            stopped.assert_not_called()
            self.assertFalse(marker.exists())

    def test_explicit_adoption_seeds_absent_marker_without_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "owner"
            with patch.object(shared_data_engine, "inspect_first_claim") as inspect:
                self.assertEqual(
                    shared_data_engine.claim(
                        marker, "podman", adopt=True
                    ),
                    "podman",
                )
            inspect.assert_not_called()

    def test_explicit_adoption_replaces_stale_other_engine_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "owner"
            marker.write_text("podman\n", encoding="ascii")
            with patch.object(shared_data_engine, "inspect_first_claim") as inspect:
                self.assertEqual(
                    shared_data_engine.claim(marker, "docker-rootful", adopt=True),
                    "docker-rootful",
                )
            inspect.assert_not_called()
            self.assertEqual(
                shared_data_engine.read_owner(marker), "docker-rootful"
            )
            self.assertEqual(marker.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
