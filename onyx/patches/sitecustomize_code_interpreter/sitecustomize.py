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


def _apply_vpn_network_patch() -> None:
    """Monkeypatch DockerExecutor to route executor pods through our netns."""
    if os.environ.get("CODE_INTERPRETER_VPN_ROUTED", "").lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return

    container_id = _resolve_own_container_id()
    if not container_id:
        print(
            "sitecustomize: CODE_INTERPRETER_VPN_ROUTED=true but could not "
            "determine this container's own ID; executor pods will keep "
            "--network none (no VPN routing). Set CODE_INTERPRETER_CONTAINER_ID "
            "explicitly to fix this.",
            flush=True,
        )
        return

    try:
        from app.services.executor_docker import DockerExecutor
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing executor_docker.DockerExecutor: {e}",
            flush=True,
        )
        return

    network_arg = f"container:{container_id}"
    _original_build_run_command = DockerExecutor._build_run_command

    def _patched_build_run_command(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        cmd = _original_build_run_command(self, *args, **kwargs)
        # Replace the hardcoded "--network" "none" pair. Docker errors if both
        # "--network none" and another "--network" are present, so we must
        # mutate the list rather than append.
        patched: list[str] = []
        i = 0
        replaced = False
        while i < len(cmd):
            if (
                not replaced
                and cmd[i] == "--network"
                and i + 1 < len(cmd)
                and cmd[i + 1] == "none"
            ):
                patched.extend(["--network", network_arg])
                replaced = True
                i += 2
                continue
            patched.append(cmd[i])
            i += 1

        if not replaced:
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
        return patched

    DockerExecutor._build_run_command = _patched_build_run_command  # type: ignore[assignment]


_apply_vpn_network_patch()
