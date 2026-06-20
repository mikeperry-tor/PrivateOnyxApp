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
import sys


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


# Directory where PySocks/socksio are installed for SOCKS proxy support.
# The code-interpreter installs them here at startup (via _install_socks_libs),
# and executor pods mount this via --volumes-from + PYTHONPATH.
SOCKS_LIBS_DIR = "/tmp/proxy-libs"


def _is_socks_proxy() -> bool:
    """True if PROXY_URL is a SOCKS proxy (socks4/socks5/socks5h scheme)."""
    proxy_url = os.environ.get("PROXY_URL", "").strip().lower()
    return proxy_url.startswith(
        ("socks4://", "socks4a://", "socks5://", "socks5h://")
    )


def _install_socks_libs() -> None:
    """Install PySocks and socksio into SOCKS_LIBS_DIR for SOCKS proxy support.

    Called at code-interpreter startup (sitecustomize) when PROXY_URL is a SOCKS
    proxy. The installed packages are made available to executor pods via
    ``--volumes-from <self>`` + ``PYTHONPATH=SOCKS_LIBS_DIR`` injected by
    ``_proxy_env_vars``.

    PySocks enables SOCKS support in ``requests`` (via ``requests[socks]``).
    socksio enables SOCKS support in ``httpx`` (via ``httpx[socks]``).
    Without these, Python's ``urllib``/``requests``/``httpx`` cannot use SOCKS
    proxies — ``urllib`` treats ``HTTP_PROXY`` as an HTTP CONNECT proxy (which
    Tor rejects), and ``requests``/``httpx`` need the SOCKS transport libraries
    to honor ``ALL_PROXY`` for SOCKS URLs.
    """
    if not _is_socks_proxy():
        return

    import subprocess

    os.makedirs(SOCKS_LIBS_DIR, exist_ok=True)

    # Check if already installed (idempotent — skip if the import succeeds).
    try:
        import socks  # noqa: F401
        import socksio  # noqa: F401
        print(
            "sitecustomize: PySocks and socksio already available system-wide; "
            "no need to install into proxy-libs",
            flush=True,
        )
        return
    except ImportError:
        pass

    # Install into the shared directory so executor pods can use them.
    # CRITICAL: unset proxy env vars for the pip subprocess to avoid a
    # chicken-and-egg problem — pip would try to download PySocks through the
    # SOCKS proxy, but PySocks isn't installed yet so pip can't use SOCKS.
    # The code-interpreter is in the netns-holder namespace, so pip can
    # download directly through the VPN (or Docker bridge if VPN is off).
    clean_env = {
        k: v for k, v in os.environ.items()
        if not k.lower().startswith(("http_proxy", "https_proxy", "all_proxy"))
    }
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "pip", "install",
                "--target", SOCKS_LIBS_DIR,
                "--quiet",
                "--no-warn-script-location",
                "PySocks",
                "socksio",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env=clean_env,
        )
        if result.returncode == 0:
            print(
                f"sitecustomize: installed PySocks + socksio into {SOCKS_LIBS_DIR} "
                "for SOCKS proxy support in executor pods",
                flush=True,
            )
        else:
            print(
                f"sitecustomize: pip install PySocks/socksio failed (exit {result.returncode}): "
                f"{result.stderr.strip()[:200]}",
                flush=True,
            )
    except Exception as e:
        print(
            f"sitecustomize: failed to install PySocks/socksio: {e}",
            flush=True,
        )


def _proxy_env_vars() -> list[str]:
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
        # Also set PYTHONPATH to include SOCKS_LIBS_DIR so executor pods can
        # import PySocks/socksio installed by _install_socks_libs().
        existing_pythonpath = os.environ.get("PYTHONPATH", "")
        if existing_pythonpath:
            socks_pythonpath = f"{SOCKS_LIBS_DIR}:{existing_pythonpath}"
        else:
            socks_pythonpath = SOCKS_LIBS_DIR
        pairs = [
            ("PROXY_URL", proxy_url),
            ("ALL_PROXY", proxy_url),
            ("NO_PROXY", no_proxy),
            ("all_proxy", proxy_url),
            ("no_proxy", no_proxy),
            ("PYTHONPATH", socks_pythonpath),
        ]
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


def _apply_executor_patches() -> None:
    """Monkeypatch DockerExecutor to (a) route executor pods through our netns
    when VPN-routed, and (b) inject proxy env vars into every executor pod when
    PROXY_URL is set.

    The two concerns are independent: a user may set PROXY_URL without VPN
    routing (proxy without VPN) or VPN routing without PROXY_URL (VPN without
    proxy), or both (proxied egress through the VPN tunnel).
    """
    vpn_routed = os.environ.get("CODE_INTERPRETER_VPN_ROUTED", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    proxy_args = _proxy_env_vars()
    socks_active = _is_socks_proxy()

    if not vpn_routed and not proxy_args:
        return

    # Resolve container ID for --network container:<self> (VPN routing) and/or
    # --volumes-from <self> (SOCKS proxy: mount PySocks/socksio libs).
    container_id = None
    if vpn_routed or socks_active:
        container_id = _resolve_own_container_id()
    if vpn_routed and not container_id:
        print(
            "sitecustomize: CODE_INTERPRETER_VPN_ROUTED=true but could not "
            "determine this container's own ID; executor pods will keep "
            "--network none (no VPN routing). Set CODE_INTERPRETER_CONTAINER_ID "
            "explicitly to fix this.",
            flush=True,
        )
        # Still apply proxy injection if PROXY_URL is set.
        if not proxy_args:
            return
    if socks_active and not container_id:
        print(
            "sitecustomize: SOCKS proxy active but could not determine this "
            "container's own ID; executor pods won't get --volumes-from for "
            "PySocks/socksio. Set CODE_INTERPRETER_CONTAINER_ID explicitly.",
            flush=True,
        )

    try:
        from app.services.executor_docker import DockerExecutor
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing executor_docker.DockerExecutor: {e}",
            flush=True,
        )
        return

    network_arg = f"container:{container_id}" if container_id else None
    _original_build_run_command = DockerExecutor._build_run_command

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

        # (c) For SOCKS proxies, inject --volumes-from <self> so executor pods
        # can access PySocks/socksio installed in SOCKS_LIBS_DIR. The PYTHONPATH
        # env var (set by _proxy_env_vars) makes Python find them.
        if socks_active and container_id:
            out2: list[str] = []
            injected_vf = False
            for j, tok in enumerate(patched):
                out2.append(tok)
                if not injected_vf and tok == "run":
                    out2.extend(["--volumes-from", container_id])
                    injected_vf = True
            if not injected_vf:
                out2.extend(["--volumes-from", container_id])
            patched = out2
            print(
                f"sitecustomize: injected --volumes-from {container_id} into "
                f"executor pod command (SOCKS proxy: mount PySocks/socksio)",
                flush=True,
            )

        return patched

    DockerExecutor._build_run_command = _patched_build_run_command  # type: ignore[assignment]


# Install PySocks/socksio for SOCKS proxy support before patching executor
# pods. This runs at code-interpreter startup; the installed libs are mounted
# into executor pods via --volumes-from + PYTHONPATH.
_install_socks_libs()

_apply_executor_patches()
