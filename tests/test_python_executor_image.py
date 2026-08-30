from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PythonExecutorImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compose = (ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
        cls.dockerfile = (ROOT / "executor" / "Dockerfile").read_text(
            encoding="utf-8"
        )
        cls.requirements = (ROOT / "executor" / "requirements.txt").read_text(
            encoding="utf-8"
        )
        cls.makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        cls.wrapper = (
            ROOT / "onyx" / "patches" / "shared" / "wrapper_env_patches.py"
        ).read_text(encoding="utf-8")
        cls.bootstrap = (
            ROOT
            / "onyx"
            / "patches"
            / "sitecustomize_api_server"
            / "sitecustomize.py"
        ).read_text(encoding="utf-8")

    def test_executor_uses_hash_locked_sympy_layer(self) -> None:
        self.assertIn(
            "ARG PYTHON_EXECUTOR_UPSTREAM_IMAGE=docker.io/onyxdotapp/python-executor-sci:0.4.5@sha256:9fad684ba9588ca37312a3a561da0566394d89051677294d0b262d1470797bff",
            self.dockerfile,
        )
        self.assertIn("FROM ${PYTHON_EXECUTOR_UPSTREAM_IMAGE}", self.dockerfile)
        self.assertIn("--require-hashes", self.dockerfile)
        self.assertIn("--python /opt/executor-venv/bin/python", self.dockerfile)
        self.assertRegex(self.requirements, r"(?m)^sympy==1\.14\.0 \\")
        self.assertRegex(self.requirements, r"(?m)^mpmath==1\.3\.0 \\")
        self.assertGreaterEqual(self.requirements.count("--hash=sha256:"), 4)

    def test_compose_uses_only_makefile_selected_executor(self) -> None:
        self.assertIn(
            'PYTHON_EXECUTOR_DOCKER_IMAGE: "${PYTHON_EXECUTOR_IMAGE:?',
            self.compose,
        )
        self.assertNotIn(
            'PYTHON_EXECUTOR_DOCKER_IMAGE: "onyxdotapp/python-executor-sci:latest"',
            self.compose,
        )
        self.assertIn("PYTHON_EXECUTOR_WRAPPER_BUILD_INPUTS :=", self.makefile)
        self.assertIn("executor/requirements.txt", self.makefile)
        self.assertIn("'$(PYTHON_EXECUTOR_UPSTREAM_IMAGE)'", self.makefile)
        self.assertIn(
            "CODE_INTERPRETER_EXECUTOR_TARGETS := executor-image-ready", self.makefile
        )
        self.assertIn("--build-arg PYTHON_EXECUTOR_UPSTREAM_IMAGE=", self.makefile)

    def test_executor_capability_text_is_unconditional_and_current(self) -> None:
        self.assertIn("def apply_python_package_capability_patches()", self.wrapper)
        for package in ("sympy", "reportlab", "svglib"):
            self.assertIn(package, self.wrapper)
        package_call = self.bootstrap.index("apply_python_package_capability_patches()")
        network_call = self.bootstrap.index(
            "apply_code_interpreter_network_description_patches()"
        )
        self.assertLess(package_call, network_call)


if __name__ == "__main__":
    unittest.main()
