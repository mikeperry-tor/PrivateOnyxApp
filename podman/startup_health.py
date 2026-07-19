#!/usr/bin/env python3
"""Install and verify Podman's native startup health checks for Compose services."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Sequence


MIN_PODMAN_VERSION = (5, 8, 1)
MIN_COMPOSE_VERSION = (2, 20, 2)
STARTUP_INTERVAL_NS = 5_000_000_000
MYST_INTERVAL_NS = 60_000_000_000
ORDINARY_INTERVAL_NS = 600_000_000_000
REQUIRED_UPDATE_FLAGS = {
    "--health-startup-cmd",
    "--health-startup-interval",
    "--health-startup-retries",
    "--health-startup-success",
    "--health-startup-timeout",
}


class ContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class ContainerHealth:
    container_id: str
    service: str
    state: str
    regular: dict[str, Any]
    startup: dict[str, Any] | None


def _run(
    command: Sequence[str], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=check,
        capture_output=True,
        text=True,
    )


def _version_tuple(value: str) -> tuple[int, int, int]:
    numbers = [int(item) for item in re.findall(r"\d+", value)]
    return tuple((numbers + [0, 0, 0])[:3])  # type: ignore[return-value]


def _require_podman_binary(container_bin: str) -> None:
    if "podman" not in os.path.basename(container_bin).lower():
        raise ContractError(
            f"Podman startup-health helper refuses non-Podman binary: {container_bin}"
        )


def check_capability(container_bin: str) -> str:
    _require_podman_binary(container_bin)
    try:
        version_data = json.loads(
            _run([container_bin, "version", "--format", "json"]).stdout
        )
        server_version = version_data["Server"]["Version"]
        server_os = version_data["Server"]["Os"]
    except (KeyError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        raise ContractError(f"could not inspect the Podman server: {exc}") from exc
    if server_os != "linux":
        raise ContractError(f"Podman server must run Linux, found {server_os!r}")
    if _version_tuple(server_version) < MIN_PODMAN_VERSION:
        minimum = ".".join(str(part) for part in MIN_PODMAN_VERSION)
        raise ContractError(
            f"Podman server {minimum}+ is required for the validated "
            f"startup-health contract; found {server_version}"
        )

    try:
        _run([container_bin, "images", "--format", "{{.ID}}"])
    except subprocess.CalledProcessError as exc:
        raise ContractError(
            "the Podman image store is unusable; restart validation found this "
            "with some newer machine images, so recreate the machine with the "
            "currently verified 5.8.1 machine-os image"
        ) from exc

    try:
        compose_version = _run(
            [container_bin, "compose", "version", "--short"]
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise ContractError(f"could not inspect the Podman Compose provider: {exc}") from exc
    if _version_tuple(compose_version) < MIN_COMPOSE_VERSION:
        minimum = ".".join(str(part) for part in MIN_COMPOSE_VERSION)
        raise ContractError(
            f"Podman Compose provider {minimum}+ is required; found {compose_version!r}"
        )

    update_help = _run([container_bin, "update", "--help"]).stdout
    missing = sorted(flag for flag in REQUIRED_UPDATE_FLAGS if flag not in update_help)
    if missing:
        raise ContractError(
            "Podman is missing required startup-health controls: " + ", ".join(missing)
        )
    return server_version


def stage_document_source(
    container_bin: str, source: str, image: str, volume: str
) -> str:
    """Atomically refresh a Podman-native read-only document cache.

    A metadata-free tar stream feeds the remote Podman client.  This works even
    when macOS cannot re-export the source filesystem through virtiofs (notably
    user-mounted WebDAV volumes), and avoids AppleDouble/xattr failures without
    modifying the source.
    """
    _require_podman_binary(container_bin)
    source_path = os.path.abspath(source)
    if not os.path.isdir(source_path):
        raise ContractError("the configured RAG document source is not a host directory")
    if not image:
        raise ContractError("the Podman document-staging image is not configured")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", volume):
        raise ContractError("the Podman document-cache volume name is invalid")

    manifest = _document_source_manifest(source_path)

    if _run([container_bin, "volume", "inspect", volume], check=False).returncode != 0:
        _run([container_bin, "volume", "create", volume])

    volume_mount = f"type=volume,src={volume},target=/volume"
    cached_manifest = _run(
        [
            container_bin,
            "run",
            "--rm",
            "--network=none",
            "--pull=never",
            f"--mount={volume_mount},ro",
            image,
            "cat",
            "/volume/.source-manifest",
        ],
        check=False,
    )
    if cached_manifest.returncode == 0 and cached_manifest.stdout.strip() == manifest:
        return volume

    prepare_script = "rm -rf /volume/.incoming; mkdir /volume/.incoming"
    _run(
        [
            container_bin,
            "run",
            "--rm",
            "--network=none",
            "--pull=never",
            f"--mount={volume_mount}",
            image,
            "sh",
            "-ceu",
            prepare_script,
        ]
    )

    tar_command = [
        "tar",
        "--no-mac-metadata",
        "--no-xattrs",
        "--no-acls",
        "--no-fflags",
        "--exclude=._*",
        "--exclude=.DS_Store",
        "-C",
        source_path,
        "-cf",
        "-",
        ".",
    ]
    receive_command = [
        container_bin,
        "run",
        "--rm",
        "--interactive",
        "--network=none",
        "--pull=never",
        f"--mount={volume_mount}",
        image,
        "tar",
        "-xf",
        "-",
        "-C",
        "/volume/.incoming",
    ]
    archive_env = os.environ.copy()
    archive_env["COPYFILE_DISABLE"] = "1"
    with tempfile.TemporaryFile() as tar_errors, tempfile.TemporaryFile() as podman_errors:
        tar_process = subprocess.Popen(
            tar_command,
            stdout=subprocess.PIPE,
            stderr=tar_errors,
            env=archive_env,
        )
        assert tar_process.stdout is not None
        receive_process = subprocess.Popen(
            receive_command,
            stdin=tar_process.stdout,
            stdout=subprocess.DEVNULL,
            stderr=podman_errors,
        )
        tar_process.stdout.close()
        receive_status = receive_process.wait()
        tar_status = tar_process.wait()
    if tar_status != 0 or receive_status != 0:
        raise ContractError(
            "Podman could not stage the configured RAG document source into "
            "its native volume; the previous staged copy was preserved "
            f"(archive status {tar_status}, receiver status {receive_status})"
        )

    rotate_script = (
        "rm -f /volume/.source-manifest.tmp; "
        "printf '%s\\n' \"$1\" > /volume/.source-manifest.tmp; "
        "rm -rf /volume/.previous; "
        "if [ -e /volume/docs ]; then mv /volume/docs /volume/.previous; fi; "
        "if mv /volume/.incoming /volume/docs && "
        "mv /volume/.source-manifest.tmp /volume/.source-manifest; then "
        "rm -rf /volume/.previous; "
        "else "
        "rm -rf /volume/docs /volume/.source-manifest.tmp; "
        "if [ -e /volume/.previous ]; then "
        "mv /volume/.previous /volume/docs; "
        "fi; "
        "exit 1; "
        "fi"
    )
    try:
        _run(
            [
                container_bin,
                "run",
                "--rm",
                "--network=none",
                "--pull=never",
                f"--mount={volume_mount}",
                image,
                "sh",
                "-ceu",
                rotate_script,
                "sh",
                manifest,
            ]
        )
    except subprocess.CalledProcessError as exc:
        raise ContractError(
            "Podman staged the RAG document source but could not activate the "
            "new native-volume copy"
        ) from exc
    return volume


def _document_source_manifest(source_path: str) -> str:
    """Hash sync-relevant metadata without reading or disclosing file bodies."""
    digest = hashlib.sha256(b"private-onyx-podman-rag-v1\0")
    try:
        for root, directories, files in os.walk(source_path, followlinks=False):
            directories[:] = sorted(
                item for item in directories if not item.startswith("._")
            )
            names = directories + sorted(
                item
                for item in files
                if not item.startswith("._") and item != ".DS_Store"
            )
            for name in names:
                path = os.path.join(root, name)
                metadata = os.lstat(path)
                relative = os.path.relpath(path, source_path)
                if stat.S_ISLNK(metadata.st_mode):
                    kind = b"l"
                    extra = os.fsencode(os.readlink(path))
                elif stat.S_ISDIR(metadata.st_mode):
                    kind = b"d"
                    extra = b""
                elif stat.S_ISREG(metadata.st_mode):
                    kind = b"f"
                    extra = b""
                else:
                    kind = b"o"
                    extra = b""
                for value in (
                    kind,
                    os.fsencode(relative),
                    str(metadata.st_size).encode("ascii"),
                    str(metadata.st_mtime_ns).encode("ascii"),
                    extra,
                ):
                    digest.update(len(value).to_bytes(8, "big"))
                    digest.update(value)
    except OSError as exc:
        raise ContractError(
            "could not inventory the configured RAG document source"
        ) from exc
    return digest.hexdigest()


def _health_enabled(health: Any) -> bool:
    if not isinstance(health, dict):
        return False
    test = health.get("Test")
    return isinstance(test, list) and bool(test) and test[0] != "NONE"


def _load_containers(container_bin: str, project: str) -> list[ContainerHealth]:
    listed = _run(
        [
            container_bin,
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.ID}}",
        ]
    ).stdout.splitlines()
    container_ids = [item.strip() for item in listed if item.strip()]
    if not container_ids:
        raise ContractError(
            f"no Compose containers found for project {project!r}; run compose create first"
        )
    inspected = json.loads(_run([container_bin, "inspect", *container_ids]).stdout)
    result: list[ContainerHealth] = []
    for item in inspected:
        labels = item.get("Config", {}).get("Labels") or {}
        regular = item.get("Config", {}).get("Healthcheck")
        if not _health_enabled(regular):
            continue
        service = labels.get("com.docker.compose.service")
        if not service:
            raise ContractError(
                f"health-checked container {item.get('Id', '<unknown>')} has no Compose service label"
            )
        result.append(
            ContainerHealth(
                container_id=item["Id"],
                service=service,
                state=item.get("State", {}).get("Status", "unknown"),
                regular=regular,
                startup=item.get("Config", {}).get("StartupHealthCheck"),
            )
        )
    if not result:
        raise ContractError(f"project {project!r} has no enabled health checks")
    return result


def _expected_regular_interval(service: str) -> int:
    return MYST_INTERVAL_NS if service == "myst-client" else ORDINARY_INTERVAL_NS


def _verify_regular(container: ContainerHealth) -> None:
    interval = container.regular.get("Interval")
    expected = _expected_regular_interval(container.service)
    if interval != expected:
        raise ContractError(
            f"{container.service}: regular health interval is {interval!r}, expected {expected}ns"
        )
    timeout = container.regular.get("Timeout")
    if not isinstance(timeout, int) or timeout <= 0:
        raise ContractError(f"{container.service}: regular health timeout is invalid")


def _verify_startup(container: ContainerHealth) -> None:
    startup = container.startup
    if not isinstance(startup, dict):
        raise ContractError(f"{container.service}: native startup health check is absent")
    if startup.get("Test") != container.regular.get("Test"):
        raise ContractError(f"{container.service}: startup health command drifted")
    if startup.get("Interval") != STARTUP_INTERVAL_NS:
        raise ContractError(f"{container.service}: startup interval is not 5s")
    if startup.get("Timeout") != container.regular.get("Timeout"):
        raise ContractError(f"{container.service}: startup timeout drifted")
    if startup.get("Successes") != 1:
        raise ContractError(f"{container.service}: startup success threshold is not one")
    if startup.get("Retries", 0) != 0:
        raise ContractError(
            f"{container.service}: startup health must not restart the container"
        )


def configure_project(container_bin: str, project: str) -> int:
    server_version = check_capability(container_bin)
    containers = _load_containers(container_bin, project)
    configured = 0
    for container in containers:
        _verify_regular(container)
        if container.state == "running":
            _verify_startup(container)
            continue
        command_json = json.dumps(
            container.regular["Test"], separators=(",", ":"), ensure_ascii=True
        )
        timeout_ns = container.regular["Timeout"]
        _run(
            [
                container_bin,
                "update",
                f"--health-startup-cmd={command_json}",
                "--health-startup-interval=5s",
                f"--health-startup-timeout={timeout_ns}ns",
                "--health-startup-retries=0",
                "--health-startup-success=1",
                container.container_id,
            ]
        )
        configured += 1

    verified = _load_containers(container_bin, project)
    for container in verified:
        _verify_regular(container)
        _verify_startup(container)
    print(
        f"Podman {server_version}: verified native startup health for "
        f"{len(verified)} service(s); configured {configured} stopped container(s)."
    )
    return len(verified)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("check", "configure", "stage-docs")
    )
    parser.add_argument("--container-bin", default="podman")
    parser.add_argument("--project", default="onyx")
    parser.add_argument("--path")
    parser.add_argument("--image")
    parser.add_argument("--volume")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.action == "check":
            version = check_capability(args.container_bin)
            print(f"Podman {version} startup-health controls are available.")
        elif args.action == "stage-docs":
            if args.path is None or args.image is None or args.volume is None:
                raise ContractError("stage-docs requires --path, --image, and --volume")
            stage_document_source(
                args.container_bin, args.path, args.image, args.volume
            )
            print("Podman RAG document source staged into a native volume.")
        else:
            configure_project(args.container_bin, args.project)
    except (ContractError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
