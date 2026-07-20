#!/usr/bin/env python3
"""Install and verify Podman's native startup health checks for Compose services."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from decimal import Decimal
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
PODMAN_OVERRIDE_XATTR = "user.containers.override_stat"


class ContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class ContainerHealth:
    container_id: str
    service: str
    state: str
    regular: dict[str, Any] | None
    startup: dict[str, Any] | None


def prepare_shared_data(
    *, postgres: str | None = None, opensearch: str | None = None
) -> list[str]:
    """Validate initialized Docker binds and remove one unsafe mount override."""
    if (postgres is None) == (opensearch is None):
        raise ContractError("select exactly one shared-data path")

    prepared: list[str] = []
    if postgres is not None:
        postgres_path = os.path.abspath(postgres)
        if not os.path.isfile(os.path.join(postgres_path, "PG_VERSION")):
            raise ContractError("shared PostgreSQL data is not initialized")
        try:
            attributes = _run(["xattr", postgres_path]).stdout.splitlines()
            if PODMAN_OVERRIDE_XATTR in attributes:
                _run(["xattr", "-d", PODMAN_OVERRIDE_XATTR, postgres_path])
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ContractError(
                "could not prepare the PostgreSQL mount ownership override"
            ) from exc
        prepared.append("PostgreSQL")

    if opensearch is not None:
        opensearch_path = os.path.abspath(opensearch)
        if not os.path.isdir(os.path.join(opensearch_path, "nodes")):
            raise ContractError("shared OpenSearch data is not initialized")
        prepared.append("OpenSearch")
    return prepared


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
        service = labels.get("com.docker.compose.service")
        if not service:
            raise ContractError(
                f"container {item.get('Id', '<unknown>')} has no Compose service label"
            )
        regular = item.get("Config", {}).get("Healthcheck")
        result.append(
            ContainerHealth(
                container_id=item["Id"],
                service=service,
                state=item.get("State", {}).get("Status", "unknown"),
                regular=regular if _health_enabled(regular) else None,
                startup=item.get("Config", {}).get("StartupHealthCheck"),
            )
        )
    return result


def _duration_ns(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    units = {
        "h": Decimal(3_600_000_000_000),
        "m": Decimal(60_000_000_000),
        "s": Decimal(1_000_000_000),
        "ms": Decimal(1_000_000),
        "us": Decimal(1_000),
        "ns": Decimal(1),
    }
    text = str(value)
    parts = re.findall(r"([0-9]+(?:[.][0-9]+)?)(h|ms|us|ns|m|s)", text)
    if not parts or "".join(number + unit for number, unit in parts) != text:
        raise ContractError(f"unsupported Compose duration: {value!r}")
    return int(sum(Decimal(number) * units[unit] for number, unit in parts))


def _load_expected_health(
    container_bin: str, env_files: Sequence[str]
) -> dict[str, dict[str, Any]]:
    command = [container_bin, "compose"]
    for env_file in env_files:
        command.extend(("--env-file", env_file))
    command.extend(("config", "--format", "json"))
    try:
        model = json.loads(_run(command).stdout)
    except (json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        raise ContractError(f"could not render the effective Compose model: {exc}") from exc

    active_profiles = {
        profile.strip()
        for profile in os.environ.get("COMPOSE_PROFILES", "").split(",")
        if profile.strip()
    }
    expected: dict[str, dict[str, Any]] = {}
    for service, config in model.get("services", {}).items():
        profiles = set(config.get("profiles") or ())
        if profiles and not profiles.intersection(active_profiles):
            continue
        health = config.get("healthcheck")
        if not isinstance(health, dict) or health.get("disable"):
            continue
        test = health.get("test")
        if not isinstance(test, list) or not test or test[0] == "NONE":
            continue
        expected[service] = health
    return expected


def _verify_expected_health_set(
    containers: Sequence[ContainerHealth], expected: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    created_services = {container.service for container in containers}
    relevant = {
        service: health
        for service, health in expected.items()
        if service in created_services
    }
    actual = {
        container.service for container in containers if container.regular is not None
    }
    if actual != set(relevant):
        missing = sorted(set(relevant) - actual)
        unexpected = sorted(actual - set(relevant))
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        raise ContractError(
            "container health-check set differs from effective Compose ("
            + "; ".join(details)
            + ")"
        )
    if not relevant:
        raise ContractError("created Compose containers have no enabled health checks")
    return relevant


def _expected_regular_interval(service: str) -> int:
    return MYST_INTERVAL_NS if service == "myst-client" else ORDINARY_INTERVAL_NS


def _verify_regular(
    container: ContainerHealth, expected_health: dict[str, Any] | None = None
) -> None:
    if container.regular is None:
        raise ContractError(f"{container.service}: regular health check is absent")
    interval = container.regular.get("Interval")
    expected = _expected_regular_interval(container.service)
    if interval != expected:
        raise ContractError(
            f"{container.service}: regular health interval is {interval!r}, expected {expected}ns"
        )
    timeout = container.regular.get("Timeout")
    if not isinstance(timeout, int) or timeout <= 0:
        raise ContractError(f"{container.service}: regular health timeout is invalid")
    if expected_health is not None:
        comparisons = {
            "Test": expected_health.get("test"),
            "Interval": _duration_ns(expected_health.get("interval")),
            "Timeout": _duration_ns(expected_health.get("timeout")),
            "StartPeriod": _duration_ns(expected_health.get("start_period")),
            "Retries": int(expected_health.get("retries", 0)),
        }
        for field, expected_value in comparisons.items():
            actual_value = container.regular.get(field, 0)
            if actual_value != expected_value:
                raise ContractError(
                    f"{container.service}: regular health {field} is "
                    f"{actual_value!r}, expected {expected_value!r} from Compose"
                )


def _verify_startup(container: ContainerHealth) -> None:
    if container.regular is None:
        raise ContractError(f"{container.service}: regular health check is absent")
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


def configure_project(
    container_bin: str, project: str, env_files: Sequence[str] = ()
) -> int:
    server_version = check_capability(container_bin)
    expected = _load_expected_health(container_bin, env_files)
    containers = _load_containers(container_bin, project)
    relevant = _verify_expected_health_set(containers, expected)
    configured = 0
    for container in containers:
        if container.service not in relevant:
            continue
        _verify_regular(container, relevant[container.service])
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
    relevant = _verify_expected_health_set(verified, expected)
    for container in verified:
        if container.service not in relevant:
            continue
        _verify_regular(container, relevant[container.service])
        _verify_startup(container)
    print(
        f"Podman {server_version}: verified native startup health for "
        f"{len(relevant)} service(s); configured {configured} stopped container(s)."
    )
    return len(relevant)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("check", "configure", "prepare-shared-data")
    )
    parser.add_argument("--container-bin", default="podman")
    parser.add_argument("--project", default="onyx")
    parser.add_argument("--env-file", action="append", default=[])
    parser.add_argument("--postgres")
    parser.add_argument("--opensearch")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.action == "check":
            version = check_capability(args.container_bin)
            print(f"Podman {version} startup-health controls are available.")
        elif args.action == "prepare-shared-data":
            prepared = prepare_shared_data(
                postgres=args.postgres, opensearch=args.opensearch
            )
            print("Prepared shared Docker data for Podman: " + ", ".join(prepared))
        else:
            configure_project(args.container_bin, args.project, args.env_file)
    except (ContractError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
