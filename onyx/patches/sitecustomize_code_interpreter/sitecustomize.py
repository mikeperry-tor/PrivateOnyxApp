"""Restricted-network runtime patches for the Onyx code-interpreter.

Loaded automatically by Python when this directory is on PYTHONPATH.

When explicitly enabled, executor pods join only the named internal executor
network and receive a local HTTP proxy URL. They never inherit the broad Onyx
network namespace or the operator's upstream proxy URL.
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Callable, Sequence
from typing import Any


_EXPECTED_BUILD_RUN_COMMAND_PARAMETERS = (
    "self",
    "container_name",
    "cpu_time_limit_sec",
    "memory_limit_mb",
    "sleep_seconds",
    "labels",
)
_EXPECTED_BUILD_RUN_COMMAND_SOURCE_MARKERS = (
    "self.docker_binary,",
    '"run",',
    '"--network",',
    "PYTHON_EXECUTOR_DOCKER_NETWORK,",
    'cmd.extend([self.image, "sleep", str(sleep_seconds)])',
)


def _env_enabled(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _strict_mode() -> bool:
    return _env_enabled("WRAPPER_PATCH_STRICT", True)


def _raise_if_strict() -> None:
    if _strict_mode():
        raise


def _network_enabled() -> bool:
    return _env_enabled("ONYX_CODE_INTERPRETER_ENABLE_NETWORK", False)


def _executor_http_proxy_url() -> str:
    """HTTP proxy URL injected into executor pods for all proxy schemes."""
    return os.environ.get(
        "ONYX_AGENT_EXECUTOR_HTTP_PROXY_URL",
        "",
    ).strip()


def _validate_configuration() -> None:
    if not _network_enabled():
        return
    network = os.environ.get("PYTHON_EXECUTOR_DOCKER_NETWORK", "").strip()
    if not network:
        raise RuntimeError("PYTHON_EXECUTOR_DOCKER_NETWORK is required")
    if network.startswith("container:") or network == "host":
        raise RuntimeError(
            "executor network must be a dedicated named Docker network, not "
            f"{network!r}"
        )
    if not _executor_http_proxy_url():
        raise RuntimeError("ONYX_AGENT_EXECUTOR_HTTP_PROXY_URL is required")


def _executor_env_vars() -> list[str]:
    """Build the ``-e KEY=VALUE`` argument pairs for executor network env vars.

    Disabled executor networking leaves the upstream ``network=none`` behavior
    untouched. Enabled networking always injects the restricted bridge URL.

    Lowercase variants (``http_proxy`` etc.) are also injected because some
    tools (notably ``curl`` and ``git``) only honor the lowercase form.
    """
    if not _network_enabled():
        return []

    _validate_configuration()

    no_proxy = os.environ.get(
        "ONYX_AGENT_EXECUTOR_NO_PROXY",
        "127.0.0.1,localhost,::1",
    )

    executor_proxy_url = _executor_http_proxy_url()
    pairs = [
        ("HTTP_PROXY", executor_proxy_url),
        ("HTTPS_PROXY", executor_proxy_url),
        ("ALL_PROXY", executor_proxy_url),
        ("NO_PROXY", no_proxy),
        ("http_proxy", executor_proxy_url),
        ("https_proxy", executor_proxy_url),
        ("all_proxy", executor_proxy_url),
        ("no_proxy", no_proxy),
    ]

    args: list[str] = []
    for key, value in pairs:
        args.extend(["-e", f"{key}={value}"])
    return args


def _validate_build_run_command_target(build_run_command: Callable[..., Any]) -> None:
    """Reject upstream executor drift before replacing its command builder."""
    if getattr(build_run_command, "_private_onyx_executor_proxy_patch", False):
        return

    parameters = tuple(inspect.signature(build_run_command).parameters)
    if parameters != _EXPECTED_BUILD_RUN_COMMAND_PARAMETERS:
        raise RuntimeError(
            "DockerExecutor._build_run_command signature changed: "
            f"expected {_EXPECTED_BUILD_RUN_COMMAND_PARAMETERS!r}, got {parameters!r}"
        )

    try:
        source = inspect.getsource(build_run_command)
    except (OSError, TypeError) as e:
        raise RuntimeError(
            "could not inspect DockerExecutor._build_run_command source"
        ) from e

    missing = [
        marker
        for marker in _EXPECTED_BUILD_RUN_COMMAND_SOURCE_MARKERS
        if marker not in source
    ]
    if missing:
        raise RuntimeError(
            "DockerExecutor._build_run_command source contract changed; "
            f"missing markers: {missing!r}"
        )


def _inject_executor_env_args(
    command: Sequence[str], executor_env_args: Sequence[str]
) -> list[str]:
    """Insert executor-only proxy variables into a validated Docker command."""
    if not isinstance(command, (list, tuple)) or not all(
        isinstance(token, str) for token in command
    ):
        raise RuntimeError(
            "DockerExecutor._build_run_command returned a non-string command"
        )

    patched = list(command)
    if len(patched) < 4 or patched[1] != "run" or patched.count("run") != 1:
        raise RuntimeError(
            "DockerExecutor._build_run_command returned an unexpected Docker "
            "run command"
        )
    if patched.count("--network") != 1 or patched.index("--network") <= 1:
        raise RuntimeError(
            "DockerExecutor._build_run_command returned an unexpected network "
            "argument layout"
        )

    return patched[:2] + list(executor_env_args) + patched[2:]


def _apply_executor_patches() -> None:
    """Monkeypatch DockerExecutor to inject proxy settings into executor pods."""
    executor_env_args = _executor_env_vars()

    if not executor_env_args:
        return

    try:
        from app.services.executor_docker import DockerExecutor
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing executor_docker.DockerExecutor: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    _original_build_run_command = DockerExecutor._build_run_command
    if getattr(
        _original_build_run_command,
        "_private_onyx_executor_proxy_patch",
        False,
    ):
        return
    _validate_build_run_command_target(_original_build_run_command)

    def _patched_build_run_command(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        cmd = _original_build_run_command(self, *args, **kwargs)

        return _inject_executor_env_args(cmd, executor_env_args)

    setattr(
        _patched_build_run_command,
        "_private_onyx_executor_proxy_patch",
        True,
    )

    DockerExecutor._build_run_command = _patched_build_run_command  # type: ignore[assignment]
    print(
        "sitecustomize: installed validated DockerExecutor run-command patch "
        f"(network={os.environ.get('PYTHON_EXECUTOR_DOCKER_NETWORK')}, "
        f"executor_http_proxy_url={_executor_http_proxy_url()}, "
        f"injected_vars={len(executor_env_args) // 2})",
        flush=True,
    )


_apply_executor_patches()
