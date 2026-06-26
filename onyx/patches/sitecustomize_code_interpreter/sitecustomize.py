"""Wrapper-side runtime patches for the Onyx code-interpreter container.

Loaded automatically by Python when this directory is on PYTHONPATH.

This patch is only active when CODE_INTERPRETER_VPN_ROUTED=true is set on the
code-interpreter container. It rewrites the executor pod's ``--network none``
flag (hardcoded upstream in ``DockerExecutor._build_run_command``) to
``--network container:<self>`` so that every Python/bash executor pod inherits
the code-interpreter's own network namespace.

Because the code-interpreter already runs inside the shared ``netns-holder``
VPN namespace (via ``network_mode: service:netns-holder`` in the wrapper
compose), inheriting its namespace gives executor pods VPN-routed egress
through the Mysterium tunnel — with no image rebuild required.

Security note: enabling this removes the code-interpreter's network isolation.
Executor pods (Python tool + coding agent bash sessions) gain outbound internet
access through the VPN. Only enable this on trusted, single-tenant deployments
where you understand the LLM will be able to make arbitrary outbound network
requests from its generated code.
"""

from __future__ import annotations

import os
import subprocess


def _env_enabled(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _strict_mode() -> bool:
    return _env_enabled("WRAPPER_PATCH_STRICT", True)


def _fail_or_false(message: str) -> bool:
    print(message, flush=True)
    if _strict_mode():
        raise RuntimeError(message)
    return False


def _raise_if_strict() -> None:
    if _strict_mode():
        raise


def _resolve_own_container_id() -> str | None:
    """Best-effort discovery of this container's own Docker container ID.

    The code-interpreter runs as root with the Docker socket mounted, so it can
    spawn sibling containers. To make those siblings inherit our network
    namespace we need our own container ID (or name) for
    ``docker run --network container:<id>``.

    Discovery order:
      1. CODE_INTERPRETER_CONTAINER_ID env var (explicit override).
      2. HOSTNAME — Docker sets this to the short container ID (12 hex chars)
         unless overridden by ``hostname:`` in the compose service. The wrapper
         compose does not set ``hostname:`` on code-interpreter, so this is the
         reliable default.
      3. /proc/self/cgroup v2 ``0::/...`` path tail (fallback for environments
         where hostname is overridden).
    """
    explicit = os.environ.get("CODE_INTERPRETER_CONTAINER_ID", "").strip()
    if explicit:
        return explicit

    hostname = os.environ.get("HOSTNAME", "").strip()
    if hostname and len(hostname) >= 12 and all(
        c in "0123456789abcdef" for c in hostname.lower()
    ):
        # Looks like a Docker short container ID.
        return hostname

    # cgroup v2 fallback: the final path component is the container ID scope.
    try:
        with open("/proc/self/cgroup", "r") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                # cgroup v2: "0::/docker/<id>" or "0::/system.slice/..."
                # cgroup v1: "<subsys>:<path>"
                path = line.split(":", 2)[-1]
                tail = path.rstrip("/").rsplit("/", 1)[-1]
                if tail and tail not in ("", "docker", "system.slice"):
                    # Heuristic: Docker scopes look like 64-hex-char IDs or
                    # "docker-<id>.scope". Strip a "docker-" prefix / ".scope"
                    # suffix if present.
                    candidate = tail
                    if candidate.startswith("docker-"):
                        candidate = candidate[len("docker-"):]
                    if candidate.endswith(".scope"):
                        candidate = candidate[: -len(".scope")]
                    if candidate and all(
                        c in "0123456789abcdef" for c in candidate.lower()
                    ):
                        return candidate
    except OSError:
        pass

    return None


# Docker named volume for PySocks/socksio shared between code-interpreter and
# executor pods. The code-interpreter installs the libs into this volume at
# startup; executor pods mount it via -v <volume>:/tmp/proxy-libs.
# We use a named volume (not --volumes-from) because the code-interpreter has
# no named volumes of its own — --volumes-from only shares named volumes, not
# the container's filesystem or bind mounts.
SOCKS_LIBS_VOLUME = "onyx-proxy-libs"
SOCKS_LIBS_DIR = "/tmp/proxy-libs"


def _is_socks_proxy() -> bool:
    """True if PROXY_URL is a SOCKS proxy (socks4/socks5/socks5h scheme)."""
    proxy_url = os.environ.get("PROXY_URL", "").strip().lower()
    return proxy_url.startswith(
        ("socks4://", "socks4a://", "socks5://", "socks5h://")
    )


def _install_socks_libs() -> bool:
    """Install PySocks and socksio into SOCKS_LIBS_DIR for SOCKS proxy support.

    Called synchronously at code-interpreter startup (sitecustomize) when
    PROXY_URL is a SOCKS proxy. The installed packages are made available to
    executor pods through a Docker named volume mounted at SOCKS_LIBS_DIR.

    PySocks enables SOCKS support in ``requests`` (via ``requests[socks]``).
    socksio enables SOCKS support in ``httpx`` (via ``httpx[socks]``).
    Without these, Python's ``urllib``/``requests``/``httpx`` cannot use SOCKS
    proxies — ``urllib`` treats ``HTTP_PROXY`` as an HTTP CONNECT proxy (which
    Tor rejects), and ``requests``/``httpx`` need the SOCKS transport libraries
    to honor ``ALL_PROXY`` for SOCKS URLs.

    This function blocks until the install succeeds or fails. The startup delay
    is intentional: executor pods should not receive a PYTHONPATH pointing at an
    empty proxy-libs volume.
    """
    if not _is_socks_proxy():
        return False

    # Create the Docker named volume if it doesn't exist, then run a one-shot
    # container that mounts the volume and pip-installs into it. We cannot
    # install directly in the code-interpreter container because /tmp is not
    # shared with executor pods.
    docker_bin = os.environ.get("CONTAINER_BIN", "docker")
    try:
        volume_result = subprocess.run(
            [docker_bin, "volume", "create", SOCKS_LIBS_VOLUME],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if volume_result.returncode != 0:
            return _fail_or_false(
                "sitecustomize: failed to create SOCKS proxy libs volume "
                f"{SOCKS_LIBS_VOLUME} (exit {volume_result.returncode}): "
                f"{volume_result.stderr.strip()[:300]}"
            )

        # Use the same Python image as the executor pods for ABI compatibility.
        result = subprocess.run(
            [
                docker_bin, "run", "--rm",
                "-v", f"{SOCKS_LIBS_VOLUME}:/proxy-libs",
                "python:3.11-slim",
                "pip", "install", "--target", "/proxy-libs",
                "--quiet", "--no-warn-script-location",
                "PySocks", "socksio",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return _fail_or_false(
                f"sitecustomize: pip install PySocks/socksio failed "
                f"(exit {result.returncode}); SOCKS proxy support in Python "
                f"executor pods may fail: {result.stderr.strip()[:300]}"
            )
    except RuntimeError:
        raise
    except Exception as e:
        return _fail_or_false(
            f"sitecustomize: failed to install PySocks/socksio: {e}"
        )

    print(
        "sitecustomize: PySocks + socksio ready in Docker volume "
        f"{SOCKS_LIBS_VOLUME} for SOCKS proxy support in executor pods",
        flush=True,
    )
    return True


def _proxy_env_vars(*, socks_libs_available: bool) -> list[str]:
    """Build the ``-e KEY=VALUE`` argument pairs for proxy env vars.

    Returns an empty list when ``PROXY_URL`` is unset/empty.

    For **HTTP/HTTPS proxies**: injects ``HTTP_PROXY`` / ``HTTPS_PROXY`` /
    ``ALL_PROXY`` (all = PROXY_URL) plus ``NO_PROXY`` so intra-namespace traffic
    stays off the proxy. Python's ``urllib``, ``requests``, and ``httpx`` all
    honor these for HTTP CONNECT proxies.

    For **SOCKS proxies** (socks4://, socks5://, socks5h://): injects only
    ``ALL_PROXY`` / ``all_proxy`` (which ``requests`` and ``httpx`` honor for
    SOCKS when PySocks/socksio is installed). Does NOT inject ``HTTP_PROXY`` /
    ``HTTPS_PROXY`` because Python's ``urllib`` treats those as HTTP CONNECT
    proxies and sends a ``CONNECT`` request to the SOCKS port, which Tor
    rejects with "501 Tor is not an HTTP Proxy" / "Socks version 67 not
    recognized". If PySocks is not installed (the default code-interpreter
    image), SOCKS proxies won't work for the Python tool — executor pods will
    fall back to VPN direct egress (they're in the netns-holder namespace).

    Lowercase variants (``http_proxy`` etc.) are also injected because some
    tools (notably ``curl`` and ``git``) only honor the lowercase form.
    """
    proxy_url = os.environ.get("PROXY_URL", "").strip()
    if not proxy_url:
        return []

    no_proxy = os.environ.get(
        "NO_PROXY",
        "127.0.0.1,localhost,::1,myst-client,api_server,web_server,background,"
        "nginx,code-interpreter,obscura,crw,searxng-core,searxng-valkey,"
        "netns-holder,host-web-proxy,host-searxng-proxy",
    )

    is_socks = proxy_url.lower().startswith(
        ("socks4://", "socks4a://", "socks5://", "socks5h://")
    )

    if is_socks:
        # SOCKS proxies: only set ALL_PROXY (honored by requests/httpx with
        # PySocks/socksio). Do NOT set HTTP_PROXY/HTTPS_PROXY — urllib
        # misinterprets them as HTTP CONNECT proxies and fails on SOCKS.
        pairs = [
            ("PROXY_URL", proxy_url),
            ("ALL_PROXY", proxy_url),
            ("NO_PROXY", no_proxy),
            ("all_proxy", proxy_url),
            ("no_proxy", no_proxy),
        ]
        if socks_libs_available:
            # Make Python executor pods find PySocks/socksio from the mounted
            # proxy-libs volume. If setup failed, do not inject a dead path.
            existing_pythonpath = os.environ.get("PYTHONPATH", "")
            if existing_pythonpath:
                socks_pythonpath = f"{SOCKS_LIBS_DIR}:{existing_pythonpath}"
            else:
                socks_pythonpath = SOCKS_LIBS_DIR
            pairs.append(("PYTHONPATH", socks_pythonpath))
        else:
            print(
                "sitecustomize: SOCKS proxy configured but proxy libraries are "
                "not ready; executor pods will receive ALL_PROXY but no "
                "proxy-libs PYTHONPATH",
                flush=True,
            )
    else:
        # HTTP/HTTPS proxies: all standard env vars work with urllib/requests/httpx.
        pairs = [
            ("PROXY_URL", proxy_url),
            ("HTTP_PROXY", proxy_url),
            ("HTTPS_PROXY", proxy_url),
            ("ALL_PROXY", proxy_url),
            ("NO_PROXY", no_proxy),
            ("http_proxy", proxy_url),
            ("https_proxy", proxy_url),
            ("all_proxy", proxy_url),
            ("no_proxy", no_proxy),
        ]

    args: list[str] = []
    for key, value in pairs:
        args.extend(["-e", f"{key}={value}"])
    return args


def _apply_executor_patches(*, socks_libs_available: bool) -> None:
    """Monkeypatch DockerExecutor to (a) route executor pods through our netns
    when VPN-routed, and (b) inject proxy env vars into every executor pod when
    PROXY_URL is set.

    The two concerns are independent: a user may set PROXY_URL without VPN
    routing (proxy without VPN) or VPN routing without PROXY_URL (VPN without
    proxy), or both (proxied egress through the VPN tunnel).
    """
    vpn_routed = _env_enabled("CODE_INTERPRETER_VPN_ROUTED", False)
    proxy_args = _proxy_env_vars(socks_libs_available=socks_libs_available)
    socks_active = _is_socks_proxy()

    if not vpn_routed and not proxy_args:
        return

    # Resolve container ID for --network container:<self> (VPN routing).
    container_id = None
    if vpn_routed:
        container_id = _resolve_own_container_id()
    if vpn_routed and not container_id:
        message = (
            "sitecustomize: CODE_INTERPRETER_VPN_ROUTED=true but could not "
            "determine this container's own ID; executor pods will keep "
            "--network none (no VPN routing). Set CODE_INTERPRETER_CONTAINER_ID "
            "explicitly to fix this."
        )
        print(message, flush=True)
        if _strict_mode():
            raise RuntimeError(message)
        # Still apply proxy injection if PROXY_URL is set.
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

    network_arg = f"container:{container_id}" if container_id else None
    _original_build_run_command = DockerExecutor._build_run_command
    print(
        "sitecustomize: installed DockerExecutor run-command patch "
        f"(vpn_routed={vpn_routed}, network_arg={network_arg or 'none'}, "
        f"proxy_enabled={bool(proxy_args)}, socks_proxy={socks_active}, "
        f"socks_libs_available={socks_libs_available})",
        flush=True,
    )

    def _patched_build_run_command(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        cmd = _original_build_run_command(self, *args, **kwargs)

        # (a) Replace --network none with --network container:<self> when
        # VPN-routed. Docker errors if both --network none and another
        # --network are present, so we mutate the list rather than append.
        patched: list[str] = []
        i = 0
        replaced_network = False
        while i < len(cmd):
            if (
                network_arg is not None
                and not replaced_network
                and cmd[i] == "--network"
                and i + 1 < len(cmd)
                and cmd[i + 1] == "none"
            ):
                patched.extend(["--network", network_arg])
                replaced_network = True
                i += 2
                continue
            patched.append(cmd[i])
            i += 1

        if network_arg is not None:
            if not replaced_network:
                print(
                    "sitecustomize: CODE_INTERPRETER_VPN_ROUTED=true but "
                    "--network none was not found in the executor run command; "
                    "executor pods may not be VPN-routed.",
                    flush=True,
                )
            else:
                print(
                    f"sitecustomize: routing code-interpreter executor pods through "
                    f"this container's netns (--network {network_arg})",
                    flush=True,
                )

        # (b) Inject proxy env vars. Insert them right after the docker binary
        # and "run" tokens so they apply to the container being created. We
        # find the first "run" token and insert after it (and after any global
        # docker flags that precede the first "run"). A simple, robust approach:
        # insert immediately before the first "--network" token if present,
        # otherwise before the first image name. To keep it simple and safe,
        # insert right after the "run" subcommand token.
        if proxy_args:
            out: list[str] = []
            injected = False
            for j, tok in enumerate(patched):
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
                f"command (PROXY_URL set, {len(proxy_args) // 2} vars)",
                flush=True,
            )

        # (c) For SOCKS proxies, inject -v <volume>:/tmp/proxy-libs so executor
        # pods can access PySocks/socksio installed in the Docker named volume.
        # The PYTHONPATH env var (set by _proxy_env_vars) makes Python find them.
        # We use a Docker named volume (not --volumes-from) because the
        # code-interpreter has no named volumes of its own.
        if socks_active and socks_libs_available:
            out2: list[str] = []
            injected_vf = False
            for j, tok in enumerate(patched):
                out2.append(tok)
                if not injected_vf and tok == "run":
                    out2.extend(["-v", f"{SOCKS_LIBS_VOLUME}:{SOCKS_LIBS_DIR}:ro"])
                    injected_vf = True
            if not injected_vf:
                out2.extend(["-v", f"{SOCKS_LIBS_VOLUME}:{SOCKS_LIBS_DIR}:ro"])
            patched = out2
            print(
                f"sitecustomize: injected -v {SOCKS_LIBS_VOLUME}:{SOCKS_LIBS_DIR}:ro "
                f"into executor pod command (SOCKS proxy: mount PySocks/socksio)",
                flush=True,
            )

        return patched

    DockerExecutor._build_run_command = _patched_build_run_command  # type: ignore[assignment]


# Install PySocks/socksio for SOCKS proxy support before patching executor
# pods. This runs at code-interpreter startup; the installed libs are mounted
# into executor pods via -v <volume>:/tmp/proxy-libs:ro + PYTHONPATH.
_SOCKS_LIBS_AVAILABLE = _install_socks_libs()

_apply_executor_patches(socks_libs_available=_SOCKS_LIBS_AVAILABLE)
