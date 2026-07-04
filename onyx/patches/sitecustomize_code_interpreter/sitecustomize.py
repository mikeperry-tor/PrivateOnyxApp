"""Wrapper-side runtime patches for the Onyx code-interpreter container.

Loaded automatically by Python when this directory is on PYTHONPATH.

In VPN mode, code-interpreter 0.4.4 receives
``PYTHON_EXECUTOR_DOCKER_NETWORK=container:onyx-netns-holder-1`` from compose,
so Python/bash executor containers inherit the shared ``netns-holder`` network
namespace.

This patch forwards proxy settings into executor containers created by
code-interpreter.

Security note: enabling this removes the code-interpreter's network isolation.
Executor pods (Python tool + coding agent bash sessions) gain outbound internet
access through the VPN. Only enable this on trusted, single-tenant deployments
where you understand the LLM will be able to make arbitrary outbound network
requests from its generated code.
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


DEFAULT_EXECUTOR_HTTP_PROXY_URL = "http://127.0.0.1:3128"


def _proxy_url() -> str:
    return os.environ.get("ONYX_AGENT_OUTBOUND_PROXY_URL", "").strip()


def _executor_http_proxy_url() -> str:
    """HTTP proxy URL injected into executor pods for all proxy schemes."""
    return os.environ.get(
        "ONYX_AGENT_EXECUTOR_HTTP_PROXY_URL",
        DEFAULT_EXECUTOR_HTTP_PROXY_URL,
    ).strip()


def _proxy_env_vars() -> list[str]:
    """Build the ``-e KEY=VALUE`` argument pairs for proxy env vars.

    Returns an empty list when ``ONYX_AGENT_OUTBOUND_PROXY_URL`` is unset/empty.

    Executor pods always receive an HTTP proxy URL that points at the local
    prefetch-blocking proxy. That sidecar adapts to the configured upstream
    proxy scheme (HTTP, HTTPS, SOCKS5, or SOCKS5h), so executor pods do not need
    SOCKS transport libraries.

    Lowercase variants (``http_proxy`` etc.) are also injected because some
    tools (notably ``curl`` and ``git``) only honor the lowercase form.
    """
    proxy_url = _proxy_url()
    if not proxy_url:
        return []

    no_proxy = os.environ.get(
        "NO_PROXY",
        "127.0.0.1,localhost,::1,myst-client,api_server,web_server,background,"
        "nginx,code-interpreter,obscura,crw,searxng-core,searxng-valkey,"
        "netns-holder,host-web-proxy,host-searxng-proxy",
    )

    executor_proxy_url = _executor_http_proxy_url()
    pairs = [
        ("ONYX_AGENT_OUTBOUND_PROXY_URL", proxy_url),
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
    proxy_args = _proxy_env_vars()

    if not proxy_args:
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
        f"(proxy_enabled={bool(proxy_args)}, "
        f"executor_http_proxy_url={_executor_http_proxy_url()})",
        flush=True,
    )

    def _patched_build_run_command(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        cmd = _original_build_run_command(self, *args, **kwargs)

        patched = list(cmd)

        # Inject proxy env vars. Insert them right after the docker binary
        # and "run" tokens so they apply to the container being created. We
        # find the first "run" token and insert after it (and after any global
        # docker flags that precede the first "run"). A simple, robust approach:
        # insert immediately before the first "--network" token if present,
        # otherwise before the first image name. To keep it simple and safe,
        # insert right after the "run" subcommand token.
        if proxy_args:
            out: list[str] = []
            injected = False
            for tok in patched:
                out.append(tok)
                if not injected and tok == "run":
                    out.extend(proxy_args)
                    injected = True
            if not injected:
                # No "run" token found (unexpected); append at the end as a
                # best-effort so the env vars are at least present in the argv.
                out.extend(proxy_args)
            patched = out
            print(
                f"sitecustomize: injected proxy env vars into executor pod "
                f"command (ONYX_AGENT_OUTBOUND_PROXY_URL set, {len(proxy_args) // 2} vars)",
                flush=True,
            )

        return patched

    DockerExecutor._build_run_command = _patched_build_run_command  # type: ignore[assignment]


_apply_executor_patches()
