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
        self.assertIn("podman/startup_health.py check", makefile)
        self.assertNotIn("Podman startup-health has not passed", makefile)
        for target in ("up-lite:", "up-full:"):
            definitions = [
                line for line in makefile.splitlines()
                if line.startswith(target)
            ]
            self.assertTrue(
                any("check-container-health-capability" in line for line in definitions)
            )

    def test_podman_create_configure_start_sequence_is_explicit(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        lite_start = makefile.split(
            "up-lite: ensure-onyx-config", 1
        )[1].split("\n\n", 1)[0]
        self.assertLess(lite_start.index(" compose "), lite_start.index("startup_health.py configure"))
        self.assertLess(lite_start.index("startup_health.py configure"), lite_start.index("up -d --wait"))

        full_start = makefile.split(
            "up-full: ensure-onyx-config", 1
        )[1].split("\n\n", 1)[0]
        self.assertEqual(full_start.count("startup_health.py configure"), 2)
        self.assertIn("create local-embedding-shim", full_start)
        self.assertLess(
            full_start.index("create local-embedding-shim"),
            full_start.index("up -d --wait local-embedding-shim"),
        )

    def test_podman_full_start_stages_document_source(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        full_prerequisites = next(
            line
            for line in makefile.splitlines()
            if line.startswith("up-full: ensure-onyx-config")
        )
        self.assertIn("stage-podman-full-docs", full_prerequisites)
        stage_target = makefile.split("stage-podman-full-docs:", 1)[1].split(
            "\n\n", 1
        )[0]
        self.assertIn("startup_health.py stage-docs", stage_target)
        self.assertIn('"$(CONTAINER_BIN)"', stage_target)
        self.assertIn('"$(ONYX_RAG_DOC_SOURCE_DIR)"', stage_target)
        self.assertIn('"$(PODMAN_RAG_DOC_VOLUME)"', stage_target)

    def test_podman_excludes_socket_only_code_interpreter_and_pulls_directly(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn(
            "ONYX_STACK_REQUIRED_IMAGES := $(ONYX_BACKEND_IMAGE) $(ONYX_WEB_SERVER_IMAGE)",
            makefile,
        )
        onyx_build = makefile.split("onyx-build:", 1)[1].split("\n\nmyst-image-ready:", 1)[0]
        podman_build = onyx_build.split("else", 1)[0]
        self.assertIn('"$(CONTAINER_BIN)" pull', podman_build)
        self.assertNotIn("ONYX_INSTALL_WRAPPER", podman_build)


if __name__ == "__main__":
    unittest.main()
