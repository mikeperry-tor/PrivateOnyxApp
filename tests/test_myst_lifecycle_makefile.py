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

    def test_full_start_stages_one_embedding_readiness_call(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        full_start = makefile.split("up-full:", 2)[2].split("\n\n", 1)[0]
        self.assertIn("up -d --wait local-embedding-shim", full_start)
        self.assertIn("embedding-ready-once", full_start)
        self.assertTrue(full_start.rstrip().endswith("up -d --wait"))
        self.assertLess(
            full_start.index("local-embedding-shim"),
            full_start.index("embedding-ready-once"),
        )
        ready_recipe = makefile.split("embedding-ready-once:", 1)[1].split(
            "\n\n", 1
        )[0]
        self.assertEqual(ready_recipe.count("/ready"), 1)
        self.assertNotIn("while", ready_recipe)
        lite_start = makefile.split("up-lite:", 2)[2].split("\n\n", 1)[0]
        self.assertNotIn("embedding-ready-once", lite_start)
        self.assertNotIn("/ready", lite_start)

    def test_container_capability_gate_is_a_start_prerequisite(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("Docker Engine 25.0+", makefile)
        self.assertIn("Docker Compose 2.20.2+", makefile)
        self.assertIn("Podman startup-health has not passed", makefile)
        for target in ("up-lite:", "up-full:"):
            definitions = [
                line for line in makefile.splitlines()
                if line.startswith(target)
            ]
            self.assertTrue(
                any("check-container-health-capability" in line for line in definitions)
            )


if __name__ == "__main__":
    unittest.main()
