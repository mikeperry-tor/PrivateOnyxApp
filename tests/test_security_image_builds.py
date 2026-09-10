from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_onyx_network_isolation import ROOT, _wrapper_neutral_environment


class SecurityImageBuildTests(unittest.TestCase):
    def make(self, target, *options, extra="", env=None):
        environment = _wrapper_neutral_environment()
        for name in ("TAILSCALE_IMAGE", "CODE_INTERPRETER_IMAGE", "PYTHON_EXECUTOR_IMAGE"):
            environment.pop(name, None)
        environment.update(env or {})
        return subprocess.check_output([
            "make", "--no-print-directory", "-s", "-f", "Makefile", "-f", "-",
            target, "ENV_FILE=.env.wrapper.example", "HOST_OS=Darwin",
            "PRIVATE_ONYX_DOCKER_ENGINE_MODE=rootful",
            "PRIVATE_ONYX_DOCKER_GATEWAY_MODE=isolated", "DOCKER_SOCK_PATH=/tmp/fixture.sock",
            *options,
        ], input=extra, text=True, cwd=ROOT, env=environment)

    def test_refresh_revision_changes_all_selected_images_and_preserves_podman_omission(self):
        extra = "inspect-security:\n\t@printf '%s\\n' '$(TAILSCALE_IMAGE)' '$(CODE_INTERPRETER_IMAGE)' '$(PYTHON_EXECUTOR_IMAGE)' '$(CODE_INTERPRETER_EXECUTOR_TARGETS)'\n"
        first = self.make("inspect-security", "CONTAINER_BIN=docker", extra=extra).splitlines()
        second = self.make("inspect-security", "CONTAINER_BIN=docker",
                           "OS_SECURITY_UPDATE_REVISION=fixture-next", extra=extra).splitlines()
        self.assertTrue(all(a != b for a, b in zip(first[:3], second[:3])))
        self.assertEqual(first[3], "code-interpreter-image-ready executor-image-ready")
        podman = self.make("inspect-security", "CONTAINER_BIN=podman", extra=extra).splitlines()
        self.assertEqual(podman[:3], first[:3])
        self.assertEqual(podman[3], "")

    def test_ready_reuses_image_and_missing_image_builds_without_layer_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = root / "docker"
            log = root / "calls"
            engine.write_text("#!/bin/sh\nset -eu\nprintf '%s\\n' \"$*\" >> \"$CALL_LOG\"\n"
                              "if [ \"$1\" = image ]; then exit \"$INSPECT_EXIT\"; fi\n"
                              "test \"$1\" = build\n")
            engine.chmod(0o755)
            for target in ("tailscale-image-ready", "code-interpreter-image-ready", "executor-image-ready"):
                for status in ("0", "1"):
                    log.write_text("")
                    self.make(target, f"CONTAINER_BIN={engine}", env={
                        "CALL_LOG": str(log), "INSPECT_EXIT": status,
                    })
                    calls = log.read_text().splitlines()
                    builds = [line for line in calls if line.startswith("build ")]
                    self.assertEqual(len(builds), int(status))
                    if builds:
                        self.assertIn("--no-cache", builds[0])
                        self.assertIn("--build-arg OS_SECURITY_UPDATE_REVISION=", builds[0])

    def test_failed_repository_refresh_does_not_upgrade_from_stale_indexes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "calls"
            apt = root / "apt-get"
            apt.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$CALL_LOG\"\nexit 7\n")
            apt.chmod(0o755)
            result = subprocess.run(["/bin/sh", str(ROOT / "onyx/code-interpreter/upgrade-os.sh")],
                env={**os.environ, "PATH": str(root), "CALL_LOG": str(log)}, check=False)
            self.assertEqual(result.returncode, 7)
            self.assertEqual(log.read_text().splitlines(), ["update --error-on=any"])


if __name__ == "__main__":
    unittest.main()
