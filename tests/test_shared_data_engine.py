from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podman import shared_data_engine


class SharedDataEngineTests(unittest.TestCase):
    def test_claim_is_idempotent_for_same_engine_and_blocks_other(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "owner"
            self.assertEqual(
                shared_data_engine.claim(marker, "docker", inspect_commands=()),
                "docker",
            )
            self.assertEqual(shared_data_engine.claim(marker, "docker"), "docker")
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
                        marker, "podman", adopt_unclaimed=True
                    ),
                    "podman",
                )
            inspect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
