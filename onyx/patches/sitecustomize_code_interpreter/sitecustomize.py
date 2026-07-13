"""Restricted-network runtime patches for the Onyx code-interpreter.

Loaded automatically by Python when this directory is on PYTHONPATH.

When explicitly enabled, executor pods join only the named internal executor
network and receive a local HTTP proxy URL. They never inherit the broad Onyx
network namespace or the operator's upstream proxy URL.
"""

from __future__ import annotations

import os


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
    print(
        "sitecustomize: installed DockerExecutor run-command patch "
        f"(network={os.environ.get('PYTHON_EXECUTOR_DOCKER_NETWORK')}, "
        f"executor_http_proxy_url={_executor_http_proxy_url()})",
        flush=True,
    )

    def _patched_build_run_command(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        cmd = _original_build_run_command(self, *args, **kwargs)

        patched = list(cmd)

        # Inject proxy/policy env vars. Insert them right after the docker binary
        # and "run" tokens so they apply to the container being created. We
        # find the first "run" token and insert after it (and after any global
        # docker flags that precede the first "run"). A simple, robust approach:
        # insert immediately before the first "--network" token if present,
        # otherwise before the first image name. To keep it simple and safe,
        # insert right after the "run" subcommand token.
        if executor_env_args:
            out: list[str] = []
            injected = False
            for tok in patched:
                out.append(tok)
                if not injected and tok == "run":
                    out.extend(executor_env_args)
                    injected = True
            if not injected:
                # No "run" token found (unexpected); append at the end as a
                # best-effort so the env vars are at least present in the argv.
                out.extend(executor_env_args)
            patched = out
            print(
                "sitecustomize: injected executor network env vars into "
                f"executor pod command ({len(executor_env_args) // 2} vars)",
                flush=True,
            )

        return patched

    DockerExecutor._build_run_command = _patched_build_run_command  # type: ignore[assignment]


_apply_executor_patches()
