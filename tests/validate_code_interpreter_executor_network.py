"""Validate the pinned code-interpreter native executor-network contract."""

from __future__ import annotations

import inspect
import os
import shlex
from collections.abc import Callable, Mapping, Sequence
from typing import Any


EXPECTED_BUILD_RUN_COMMAND_PARAMETERS = (
    "self",
    "container_name",
    "cpu_time_limit_sec",
    "memory_limit_mb",
    "sleep_seconds",
    "labels",
)
EXPECTED_BUILD_RUN_COMMAND_SOURCE_MARKERS = (
    '"--network",',
    "PYTHON_EXECUTOR_DOCKER_NETWORK,",
    "if self.run_args:",
    "cmd.extend(shlex.split(self.run_args))",
    'cmd.extend([self.image, "sleep", str(sleep_seconds)])',
)
PROXY_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)
EXPECTED_PROXY_URL = "http://executor-egress-bridge:3128"
EXPECTED_NO_PROXY = "127.0.0.1,localhost,::1"


def expected_executor_environment() -> dict[str, str]:
    return {
        key: EXPECTED_NO_PROXY if key.lower() == "no_proxy" else EXPECTED_PROXY_URL
        for key in PROXY_KEYS
    }


def parse_run_args(run_args: str) -> dict[str, str]:
    tokens = shlex.split(run_args)
    if len(tokens) != len(PROXY_KEYS) * 2:
        raise RuntimeError(
            "PYTHON_EXECUTOR_DOCKER_RUN_ARGS must contain exactly eight "
            "executor proxy variables"
        )
    parsed: dict[str, str] = {}
    for flag, assignment in zip(tokens[0::2], tokens[1::2], strict=True):
        if flag not in ("-e", "--env") or "=" not in assignment:
            raise RuntimeError(
                "PYTHON_EXECUTOR_DOCKER_RUN_ARGS contains an unexpected argument"
            )
        key, value = assignment.split("=", 1)
        if key in parsed:
            raise RuntimeError(f"duplicate executor environment variable: {key}")
        parsed[key] = value
    return parsed


def validate_build_run_command_target(
    build_run_command: Callable[..., Any],
) -> None:
    parameters = tuple(inspect.signature(build_run_command).parameters)
    if parameters != EXPECTED_BUILD_RUN_COMMAND_PARAMETERS:
        raise RuntimeError(
            "DockerExecutor._build_run_command signature changed: "
            f"expected {EXPECTED_BUILD_RUN_COMMAND_PARAMETERS!r}, got {parameters!r}"
        )
    try:
        source = inspect.getsource(build_run_command)
    except (OSError, TypeError) as e:
        raise RuntimeError(
            "could not inspect DockerExecutor._build_run_command source"
        ) from e
    missing = [
        marker
        for marker in EXPECTED_BUILD_RUN_COMMAND_SOURCE_MARKERS
        if marker not in source
    ]
    if missing:
        raise RuntimeError(
            "DockerExecutor._build_run_command source contract changed; "
            f"missing markers: {missing!r}"
        )


def validate_generated_command(
    command: Sequence[str],
    expected_network: str,
    expected_environment: Mapping[str, str],
) -> None:
    if not isinstance(command, (list, tuple)) or not all(
        isinstance(token, str) for token in command
    ):
        raise RuntimeError("DockerExecutor returned a non-string command")
    if len(command) < 4 or command[1] != "run" or command.count("--network") != 1:
        raise RuntimeError("DockerExecutor returned an unexpected Docker run command")
    network_index = command.index("--network")
    if command[network_index + 1] != expected_network:
        raise RuntimeError("DockerExecutor did not select the dedicated executor network")
    env_tokens = [
        token
        for index, token in enumerate(command)
        if index > 0 and command[index - 1] in ("-e", "--env")
    ]
    proxy_assignments = [
        token.split("=", 1)
        for token in env_tokens
        if token.split("=", 1)[0] in PROXY_KEYS
    ]
    if (
        len(proxy_assignments) != len(PROXY_KEYS)
        or dict(proxy_assignments) != expected_environment
    ):
        raise RuntimeError("DockerExecutor did not preserve the exact proxy environment")


def validate_native_executor_contract(executor_class: type | None = None) -> None:
    network = os.environ.get("PYTHON_EXECUTOR_DOCKER_NETWORK", "").strip()
    if not network:
        raise RuntimeError("PYTHON_EXECUTOR_DOCKER_NETWORK is required")
    if network.startswith("container:") or network == "host":
        raise RuntimeError(
            "executor network must be a dedicated named Docker network, not "
            f"{network!r}"
        )

    expected_environment = expected_executor_environment()
    run_args = os.environ.get("PYTHON_EXECUTOR_DOCKER_RUN_ARGS", "")
    if parse_run_args(run_args) != expected_environment:
        raise RuntimeError(
            "PYTHON_EXECUTOR_DOCKER_RUN_ARGS does not match the restricted "
            "proxy configuration"
        )

    if executor_class is None:
        from app.services.executor_docker import DockerExecutor

        executor_class = DockerExecutor

    validate_build_run_command_target(executor_class._build_run_command)
    executor = object.__new__(executor_class)
    executor.docker_binary = "docker"
    executor.image = "validated-executor-image"
    executor.run_args = run_args
    command = executor._build_run_command("validation", 5, 64, 10, None)
    validate_generated_command(command, network, expected_environment)


if __name__ == "__main__":
    validate_native_executor_contract()
    print("PINNED_EXECUTOR_NATIVE_NETWORK_CONTRACT_OK")
