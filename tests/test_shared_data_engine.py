from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from podman import shared_data_engine


class SharedDataEngineTests(unittest.TestCase):
    def test_claim_is_idempotent_for_same_engine_and_blocks_other(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "owner"
            self.assertEqual(shared_data_engine.claim(marker, "docker"), "docker")
            self.assertEqual(shared_data_engine.claim(marker, "docker"), "docker")
            with self.assertRaisesRegex(
                shared_data_engine.GuardError, "claimed by docker"
            ):
                shared_data_engine.claim(marker, "podman")

    def test_release_requires_matching_engine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "owner"
            shared_data_engine.claim(marker, "podman")
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


if __name__ == "__main__":
    unittest.main()
