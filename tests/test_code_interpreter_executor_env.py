from __future__ import annotations

import os
import shlex
import unittest
from unittest.mock import patch

from tests import validate_code_interpreter_executor_network as validator


PYTHON_EXECUTOR_DOCKER_NETWORK = "onyx-code-interpreter-executor"


class _CompatibleDockerExecutor:
    def __init__(self, malformed: bool = False) -> None:
        self.docker_binary = "docker"
        self.image = "executor-image"
        self.run_args = ""
        self.malformed = malformed

    def _build_run_command(
        self,
        container_name,
        cpu_time_limit_sec,
        memory_limit_mb,
        sleep_seconds,
        labels,
    ):
        if getattr(self, "malformed", False):
            return [self.docker_binary, "version"]
        cmd = [
            self.docker_binary,
            "run",
            "--network",
            PYTHON_EXECUTOR_DOCKER_NETWORK,
            "--name",
            container_name,
        ]
        if self.run_args:
            cmd.extend(shlex.split(self.run_args))
        cmd.extend([self.image, "sleep", str(sleep_seconds)])
        return cmd


class _DriftedDockerExecutor:
    def _build_run_command(self, container_name):
        return ["docker", "run", "image"]


def _native_run_args() -> str:
    proxy = "http://executor-egress-bridge:3128"
    no_proxy = "127.0.0.1,localhost,::1"
    return " ".join(
        f"--env {key}={no_proxy if key.lower() == 'no_proxy' else proxy}"
        for key in validator.PROXY_KEYS
    )


def _native_environment() -> dict[str, str]:
    return {
        "PYTHON_EXECUTOR_DOCKER_NETWORK": PYTHON_EXECUTOR_DOCKER_NETWORK,
        "PYTHON_EXECUTOR_DOCKER_RUN_ARGS": _native_run_args(),
    }


class CodeInterpreterExecutorEnvTests(unittest.TestCase):
    def test_accepts_only_restricted_bridge_run_args(self) -> None:
        env = _native_environment()
        env["EGRESS_UPSTREAM_PROXY_URL"] = "socks5h://host.docker.internal:9150"
        with patch.dict(os.environ, env, clear=True):
            executor_env = validator.parse_run_args(_native_run_args())
            validator.validate_native_executor_contract(_CompatibleDockerExecutor)

        self.assertEqual(
            executor_env["HTTP_PROXY"], "http://executor-egress-bridge:3128"
        )
        self.assertEqual(
            executor_env["HTTPS_PROXY"], "http://executor-egress-bridge:3128"
        )
        self.assertEqual(
            executor_env["ALL_PROXY"], "http://executor-egress-bridge:3128"
        )
        self.assertEqual(executor_env["NO_PROXY"], "127.0.0.1,localhost,::1")
        self.assertNotIn("EGRESS_UPSTREAM_PROXY_URL", executor_env)

    def test_rejects_shared_namespace(self) -> None:
        env = _native_environment()
        env["PYTHON_EXECUTOR_DOCKER_NETWORK"] = "container:onyx-netns-holder-1"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "dedicated named Docker network"):
                validator.validate_native_executor_contract(_CompatibleDockerExecutor)

    def test_validates_native_actual_run_command_without_mutation(self) -> None:
        with patch.dict(os.environ, _native_environment(), clear=True):
            expected_environment = validator.expected_executor_environment()
            validator.validate_native_executor_contract(_CompatibleDockerExecutor)

        executor = _CompatibleDockerExecutor()
        executor.run_args = _native_run_args()
        command = executor._build_run_command("sandbox", 10, 128, 60, None)
        validator.validate_generated_command(
            command, PYTHON_EXECUTOR_DOCKER_NETWORK, expected_environment
        )
        self.assertFalse(
            hasattr(
                _CompatibleDockerExecutor._build_run_command,
                "_private_onyx_executor_proxy_patch",
            )
        )

        with self.assertRaisesRegex(RuntimeError, "unexpected Docker run command"):
            validator.validate_generated_command(
                _CompatibleDockerExecutor(malformed=True)._build_run_command(
                    "sandbox", 10, 128, 60, None
                ),
                PYTHON_EXECUTOR_DOCKER_NETWORK,
                expected_environment,
            )

    def test_rejects_extra_native_run_argument(self) -> None:
        env = _native_environment()
        env["PYTHON_EXECUTOR_DOCKER_RUN_ARGS"] += " --privileged"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "exactly eight"):
                validator.validate_native_executor_contract(_CompatibleDockerExecutor)

    def test_rejects_upstream_signature_drift(self) -> None:
        with patch.dict(os.environ, _native_environment(), clear=True):
            with self.assertRaisesRegex(RuntimeError, "signature changed"):
                validator.validate_native_executor_contract(_DriftedDockerExecutor)


if __name__ == "__main__":
    unittest.main()
