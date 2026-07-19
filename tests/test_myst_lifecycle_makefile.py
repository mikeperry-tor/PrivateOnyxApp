from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MystLifecycleMakefileTests(unittest.TestCase):
    def test_stack_start_preserves_integrated_myst_container(self) -> None:
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn('com.docker.compose.project', makefile)
        self.assertIn('if [ "$$myst_project" = "onyx" ]', makefile)
        self.assertIn(
            "Integrated Onyx Myst container is already running; preserving its routing namespace.",
            makefile,
        )


if __name__ == "__main__":
    unittest.main()
