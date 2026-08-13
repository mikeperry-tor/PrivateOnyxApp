#!/usr/bin/env python3
"""Validate engine compatibility and manage Podman startup health checks."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence
from urllib.parse import unquote, urlsplit


VALIDATED_PODMAN_VERSION = (5, 4, 2)
STARTUP_INTERVAL_NS = 5_000_000_000
MYST_INTERVAL_NS = 60_000_000_000
ORDINARY_INTERVAL_NS = 600_000_000_000
COMMON_HOST_BIND_DIRS = (
    "postgres",
    "file-system",
    "searxng-cache",
    "myst-data",
)
FULL_HOST_BIND_DIRS = ("opensearch", "minio", "model-cache")
REQUIRED_UPDATE_FLAGS = {
    "--health-startup-cmd",
    "--health-startup-interval",
    "--health-startup-retries",
    "--health-startup-success",
    "--health-startup-timeout",
}
PODMAN_OVERRIDE_XATTR = "user.containers.override_stat"
POSTGRES_ENTRYPOINT = "/usr/local/bin/docker-entrypoint.sh"
OPENSEARCH_READY_COMMAND = (
    'curl --silent --fail --insecure '
    '--user "admin:${OPENSEARCH_INITIAL_ADMIN_PASSWORD}" '
    "https://localhost:9200/_cluster/health >/dev/null"
)
OPENSEARCH_AUDIT_BOOTSTRAP_COMMAND = """\
/usr/share/opensearch/plugins/opensearch-security/tools/securityadmin.sh \
  -f /usr/share/opensearch/config/opensearch-security/audit.yml \
  -t audit -icl -nhnv \
  -cacert /usr/share/opensearch/config/root-ca.pem \
  -cert /usr/share/opensearch/config/kirk.pem \
  -key /usr/share/opensearch/config/kirk-key.pem
deadline=$((SECONDS + 30))
while true; do
  audit="$(
    curl --silent --fail --insecure \
      --user "admin:${OPENSEARCH_INITIAL_ADMIN_PASSWORD}" \
      https://localhost:9200/_plugins/_security/api/audit
  )"
  if printf '%s' "$audit" |
      grep -Eq '"log_request_body"[[:space:]]*:[[:space:]]*false' &&
    printf '%s' "$audit" |
      grep -Eq '"exclude_sensitive_headers"[[:space:]]*:[[:space:]]*true'; then
    break
  fi
  if ((SECONDS >= deadline)); then
    printf '%s\n' "tracked OpenSearch audit policy did not become active" >&2
    exit 1
  fi
  sleep 1
done
"""
COMPOSE_CAPABILITY_MODEL = """\
services:
  probe:
    image: scratch
    volumes: !override []
    healthcheck:
      test: ["NONE"]
      start_interval: 5s
    networks:
      default:
        gw_priority: 1
networks:
  default: {}
"""


class ContractError(RuntimeError):
    pass


def docker_engine_mode(container_bin: str) -> str:
    """Classify the selected Docker daemon's user-namespace mode."""
    if "podman" in os.path.basename(container_bin).lower():
        raise ContractError("Docker engine inspection refuses a Podman binary")
    try:
        raw_options = _run(
            [container_bin, "info", "--format", "{{json .SecurityOptions}}"]
        ).stdout.strip()
        options = json.loads(raw_options)
    except (json.JSONDecodeError, OSError, subprocess.CalledProcessError) as exc:
        raise ContractError(
            f"could not inspect the selected Docker daemon security options: {exc}"
        ) from exc
    if not isinstance(options, list) or not all(
        isinstance(option, str) for option in options
    ):
        raise ContractError("Docker returned malformed security options")
    names = {option.split(",", 1)[0] for option in options}
    if "name=rootless" in names:
        return "rootless"
    if "name=userns" in names:
        return "userns-remap"
    return "rootful"


def check_docker_engine(container_bin: str, expected_mode: str | None = None) -> str:
    """Reject daemon-wide userns-remap and verify Make selected the right layer."""
    mode = docker_engine_mode(container_bin)
    if mode == "userns-remap":
        raise ContractError(
            "the selected Docker daemon has userns-remap enabled. This stack "
            "does not support daemon-wide userns-remap because its host binds "
            "and engine-socket executor require ownership semantics that Docker "
            "remaps incompatibly. Disable userns-remap or select a rootless "
            "Docker daemon; no stack containers were started."
        )
    if expected_mode and expected_mode != mode:
        raise ContractError(
            f"Docker daemon mode changed while preparing the stack "
            f"(Make selected {expected_mode}, daemon reports {mode}); rerun make"
        )
    return mode


def docker_socket_path(container_bin: str) -> str:
    """Resolve the selected local Docker context to its Unix socket path."""
    endpoint = os.environ.get("DOCKER_HOST", "").strip()
    if not endpoint:
        try:
            endpoint = _run(
                [
                    container_bin,
                    "context",
                    "inspect",
                    "--format",
                    "{{.Endpoints.docker.Host}}",
                ]
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ContractError(
                f"could not resolve the selected Docker context endpoint: {exc}"
            ) from exc
    parsed = urlsplit(endpoint)
    if parsed.scheme != "unix" or parsed.netloc or not parsed.path:
        raise ContractError(
            "rootless Docker requires a local unix:// context so the code "
            "interpreter can mount the selected engine socket"
        )
    path = unquote(parsed.path)
    if not os.path.isabs(path):
        raise ContractError("Docker context returned a non-absolute Unix socket path")
    return path


def engine_socket_path(container_bin: str) -> str:
    """Resolve the selected engine's local Unix API socket path."""
    if "podman" not in os.path.basename(container_bin).lower():
        return docker_socket_path(container_bin)

    commands = (
        [
            container_bin,
            "machine",
            "inspect",
            "--format",
            "{{.ConnectionInfo.PodmanSocket.Path}}",
        ],
        [container_bin, "info", "--format", "{{.Host.RemoteSocket.Path}}"],
    )
    for command in commands:
        try:
            output = _run(command).stdout
        except (OSError, subprocess.CalledProcessError):
            continue
        for line in output.splitlines():
            path = line.strip()
            if os.path.isabs(path):
                return path
    raise ContractError("could not resolve the selected Podman Unix API socket")


@dataclass(frozen=True)
class ContainerHealth:
    container_id: str
    service: str
    state: str
    regular: dict[str, Any] | None
    startup: dict[str, Any] | None
    health_status: str | None = None


def prepare_host_directories(
    data_root: str,
    *,
    full: bool,
    doc_source: str | None = None,
    default_doc_source: str | None = None,
) -> list[str]:
    """Create host bind roots before a container engine can create them as root."""
    data_root_path = os.path.abspath(data_root)
    names = COMMON_HOST_BIND_DIRS + (FULL_HOST_BIND_DIRS if full else ())
    prepared: list[str] = []

    for name in names:
        path = os.path.join(data_root_path, name)
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            raise ContractError(f"could not create host data directory {path!r}") from exc
        if not os.path.isdir(path):
            raise ContractError(f"host data path is not a directory: {path!r}")
        prepared.append(path)

    if full:
        if doc_source is None or default_doc_source is None:
            raise ContractError(
                "full host-data preparation requires document source paths"
            )
        source_path = os.path.abspath(doc_source)
        default_path = os.path.abspath(default_doc_source)
        if not os.path.exists(source_path):
            if source_path != default_path:
                raise ContractError(
                    "configured RAG document source does not exist; "
                    "refusing to create a custom path"
                )
            try:
                os.makedirs(source_path, exist_ok=False)
            except OSError as exc:
                raise ContractError(
                    "could not create the default RAG document source"
                ) from exc
        if not os.path.isdir(source_path):
            raise ContractError("configured RAG document source is not a directory")
        prepared.append(source_path)

    return prepared


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
        if sys.platform == "darwin":
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


def initialize_postgres_data(
    container_bin: str,
    postgres: str,
    env_files: Sequence[str] = (),
) -> str:
    """Initialize an empty shared bind, or prepare an existing PostgreSQL cluster."""
    _require_podman_binary(container_bin)
    postgres_path = os.path.abspath(postgres)
    version_path = os.path.join(postgres_path, "PG_VERSION")

    if not os.path.isfile(version_path):
        if os.path.exists(postgres_path):
            if not os.path.isdir(postgres_path):
                raise ContractError("shared PostgreSQL data path is not a directory")
            try:
                with os.scandir(postgres_path) as entries:
                    nonempty = next(entries, None) is not None
            except OSError as exc:
                raise ContractError(
                    "could not inspect the shared PostgreSQL data path"
                ) from exc
            if nonempty:
                raise ContractError(
                    "shared PostgreSQL data is nonempty but not initialized; "
                    "refusing to overwrite partial or unknown state"
                )
        else:
            try:
                os.makedirs(postgres_path, exist_ok=False)
            except OSError as exc:
                raise ContractError(
                    "could not create the shared PostgreSQL data path"
                ) from exc

        compose_command = [container_bin, "compose"]
        for env_file in env_files:
            compose_command.extend(("--env-file", env_file))
        suffix = uuid.uuid4().hex
        init_volume = f"private-onyx-postgres-init-{suffix}"
        init_container = f"private-onyx-postgres-init-{suffix}"
        _run(
            [
                container_bin,
                "volume",
                "create",
                "--label",
                "io.private-onyx.role=postgres-init",
                init_volume,
            ]
        )
        container_created = False
        try:
            _run(
                [
                    *compose_command,
                    "run",
                    "--detach",
                    "--name",
                    init_container,
                    "--no-deps",
                    "--user",
                    "0:0",
                    "--volume",
                    f"{init_volume}:/var/lib/postgresql/data",
                    "--entrypoint",
                    POSTGRES_ENTRYPOINT,
                    "relational_db",
                    "postgres",
                ]
            )
            container_created = True
            deadline = time.monotonic() + 120
            while True:
                ready = _run(
                    [container_bin, "exec", init_container, "pg_isready"],
                    check=False,
                )
                if ready.returncode == 0:
                    break
                if time.monotonic() >= deadline:
                    raise ContractError(
                        "temporary PostgreSQL initialization did not become ready "
                        "within 120 seconds"
                    )
                time.sleep(1)
            _run([container_bin, "stop", "--time", "30", init_container])
            _run(
                [
                    *compose_command,
                    "run",
                    "--rm",
                    "--no-deps",
                    "--volume",
                    f"{init_volume}:/private-onyx-postgres-init:ro",
                    "--entrypoint",
                    "/bin/sh",
                    "relational_db",
                    "-ec",
                    "cp -a /private-onyx-postgres-init/. /var/lib/postgresql/data/",
                ]
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip()
            suffix = f": {detail}" if detail else ""
            raise ContractError(
                "PostgreSQL initialization container failed; partial bind data "
                f"was left in place for diagnosis{suffix}"
            ) from exc
        finally:
            container_removed = subprocess.CompletedProcess([], 0)
            if container_created:
                container_removed = _run(
                    [container_bin, "rm", "--force", init_container],
                    check=False,
                )
            removed = _run(
                [container_bin, "volume", "rm", "--force", init_volume],
                check=False,
            )
            if container_removed.returncode != 0:
                raise ContractError(
                    f"could not remove temporary PostgreSQL container {init_container!r}"
                )
            if removed.returncode != 0:
                raise ContractError(
                    f"could not remove temporary PostgreSQL volume {init_volume!r}"
                )
        if not os.path.isfile(version_path):
            raise ContractError(
                "PostgreSQL initialization completed without creating PG_VERSION"
            )
        result = "initialized"
    else:
        result = "reused"

    prepare_shared_data(postgres=postgres_path)
    return result


def initialize_opensearch_data(
    container_bin: str,
    opensearch: str,
    audit_config: str,
    env_files: Sequence[str] = (),
) -> str:
    """Initialize an empty shared bind, or validate an existing OpenSearch node."""
    _require_podman_binary(container_bin)
    opensearch_path = os.path.abspath(opensearch)
    audit_config_path = os.path.abspath(audit_config)
    if not os.path.isfile(audit_config_path):
        raise ContractError("tracked OpenSearch audit config is not a regular file")
    nodes_path = os.path.join(opensearch_path, "nodes")

    if not os.path.isdir(nodes_path):
        if os.path.exists(opensearch_path):
            if not os.path.isdir(opensearch_path):
                raise ContractError("shared OpenSearch data path is not a directory")
            try:
                with os.scandir(opensearch_path) as entries:
                    nonempty = next(entries, None) is not None
            except OSError as exc:
                raise ContractError(
                    "could not inspect the shared OpenSearch data path"
                ) from exc
            if nonempty:
                raise ContractError(
                    "shared OpenSearch data is nonempty but not initialized; "
                    "refusing to overwrite partial or unknown state"
                )
        else:
            try:
                os.makedirs(opensearch_path, exist_ok=False)
            except OSError as exc:
                raise ContractError(
                    "could not create the shared OpenSearch data path"
                ) from exc

        compose_command = [container_bin, "compose"]
        for env_file in env_files:
            compose_command.extend(("--env-file", env_file))
        suffix = uuid.uuid4().hex
        init_volume = f"private-onyx-opensearch-init-{suffix}"
        init_container = f"private-onyx-opensearch-init-{suffix}"
        _run(
            [
                container_bin,
                "volume",
                "create",
                "--label",
                "io.private-onyx.role=opensearch-init",
                init_volume,
            ]
        )
        container_created = False
        try:
            _run(
                [
                    *compose_command,
                    "run",
                    "--detach",
                    "--name",
                    init_container,
                    "--no-deps",
                    "--volume",
                    f"{init_volume}:/usr/share/opensearch/data",
                    "--volume",
                    f"{audit_config_path}:"
                    "/usr/share/opensearch/config/opensearch-security/audit.yml:ro",
                    "opensearch",
                ]
            )
            container_created = True
            deadline = time.monotonic() + 240
            while True:
                ready = _run(
                    [
                        container_bin,
                        "exec",
                        init_container,
                        "/bin/bash",
                        "-ec",
                        OPENSEARCH_READY_COMMAND,
                    ],
                    check=False,
                )
                if ready.returncode == 0:
                    break
                if time.monotonic() >= deadline:
                    raise ContractError(
                        "temporary OpenSearch initialization did not become ready "
                        "within 240 seconds"
                    )
                time.sleep(1)
            try:
                _run(
                    [
                        container_bin,
                        "exec",
                        init_container,
                        "/bin/bash",
                        "-ec",
                        OPENSEARCH_AUDIT_BOOTSTRAP_COMMAND,
                    ]
                )
            except subprocess.CalledProcessError as exc:
                detail = ((exc.stderr or exc.stdout) or "").strip()
                suffix = f": {detail}" if detail else ""
                raise ContractError(
                    "temporary OpenSearch audit-policy bootstrap or verification "
                    f"failed{suffix}"
                ) from exc
            _run([container_bin, "stop", "--time", "60", init_container])
            _run(
                [
                    *compose_command,
                    "run",
                    "--rm",
                    "--no-deps",
                    "--volume",
                    f"{init_volume}:/private-onyx-opensearch-init:ro",
                    "--entrypoint",
                    "/bin/bash",
                    "opensearch",
                    "-ec",
                    "cp -a /private-onyx-opensearch-init/. "
                    "/usr/share/opensearch/data/",
                ]
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip()
            suffix = f": {detail}" if detail else ""
            raise ContractError(
                "OpenSearch initialization container failed; partial bind data "
                f"was left in place for diagnosis{suffix}"
            ) from exc
        finally:
            container_removed = subprocess.CompletedProcess([], 0)
            if container_created:
                container_removed = _run(
                    [container_bin, "rm", "--force", init_container],
                    check=False,
                )
            removed = _run(
                [container_bin, "volume", "rm", "--force", init_volume],
                check=False,
            )
            if container_removed.returncode != 0:
                raise ContractError(
                    f"could not remove temporary OpenSearch container {init_container!r}"
                )
            if removed.returncode != 0:
                raise ContractError(
                    f"could not remove temporary OpenSearch volume {init_volume!r}"
                )
        if not os.path.isdir(nodes_path):
            raise ContractError(
                "OpenSearch initialization completed without creating nodes"
            )
        result = "initialized"
    else:
        result = "reused"

    prepare_shared_data(opensearch=opensearch_path)
    return result


def _run(
    command: Sequence[str], *, check: bool = True, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=check,
        capture_output=True,
        text=True,
        input=input_text,
    )


def _version_tuple(value: str) -> tuple[int, int, int]:
    numbers = [int(item) for item in re.findall(r"\d+", value)]
    return tuple((numbers + [0, 0, 0])[:3])  # type: ignore[return-value]


def _require_podman_binary(container_bin: str) -> None:
    if "podman" not in os.path.basename(container_bin).lower():
        raise ContractError(
            f"Podman startup-health helper refuses non-Podman binary: {container_bin}"
        )


def check_compose_capability(container_bin: str) -> str:
    """Verify the Compose model features on which the stack's topology depends."""
    try:
        compose_version = _run(
            [container_bin, "compose", "version", "--short"]
        ).stdout.strip()
        rendered = json.loads(
            _run(
                [
                    container_bin,
                    "compose",
                    "-f",
                    "-",
                    "config",
                    "--format",
                    "json",
                ],
                input_text=COMPOSE_CAPABILITY_MODEL,
            ).stdout
        )
        probe = rendered["services"]["probe"]
        network = probe["networks"]["default"]
        healthcheck = probe["healthcheck"]
    except (
        KeyError,
        json.JSONDecodeError,
        OSError,
        subprocess.CalledProcessError,
    ) as exc:
        raise ContractError(
            "the Compose provider cannot render the stack's required model "
            f"features (gw_priority, !override, and start_interval): {exc}"
        ) from exc
    missing: list[str] = []
    if network.get("gw_priority") != 1:
        missing.append("gw_priority")
    if healthcheck.get("start_interval") != "5s":
        missing.append("start_interval")
    if probe.get("volumes") not in (None, []):
        missing.append("!override")
    if missing:
        raise ContractError(
            f"Compose provider {compose_version!r} silently drops or changes "
            "required model features: "
            + ", ".join(missing)
            + ". Install a current Docker Compose provider (2.33.1+ implements "
            "the routing-critical gw_priority interface)."
        )
    return compose_version


def check_capability(container_bin: str) -> str:
    _require_podman_binary(container_bin)
    try:
        server_version, server_os = _run(
            [
                container_bin,
                "version",
                "--format",
                "{{.Server.Version}}\t{{.Server.Os}}",
            ]
        ).stdout.strip().split("\t")
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise ContractError(f"could not inspect the Podman server: {exc}") from exc
    if server_os != "linux":
        raise ContractError(f"Podman server must run Linux, found {server_os!r}")
    if _version_tuple(server_version) < VALIDATED_PODMAN_VERSION:
        baseline = ".".join(str(part) for part in VALIDATED_PODMAN_VERSION)
        print(
            f"WARNING: Podman server {server_version} is older than the "
            f"validated {baseline} baseline; continuing with explicit "
            "capability checks.",
            file=sys.stderr,
        )

    try:
        _run([container_bin, "images", "--format", "{{.ID}}"])
    except subprocess.CalledProcessError as exc:
        raise ContractError(
            "the Podman image store is unusable; restart validation found this "
            "with some newer machine images, so recreate the machine with the "
            "currently verified 5.8.1 machine-os image"
        ) from exc

    check_compose_capability(container_bin)

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
                health_status=(item.get("State", {}).get("Health") or {}).get(
                    "Status"
                ),
            )
        )
    return result


def assert_services_healthy(
    container_bin: str, project: str, services: Sequence[str]
) -> int:
    """Fail unless every named Compose service is running and healthy."""
    if not services:
        raise ContractError("select at least one service to assert healthy")
    containers = _load_containers(container_bin, project)
    by_service = {container.service: container for container in containers}
    for service in services:
        container = by_service.get(service)
        if container is None:
            raise ContractError(f"{service}: Compose container is absent")
        if container.state != "running" or container.health_status != "healthy":
            raise ContractError(
                f"{service}: expected running/healthy, found "
                f"{container.state}/{container.health_status or 'none'}"
            )
    print(
        "Podman post-wait health assertion passed for: "
        + ", ".join(sorted(set(services)))
    )
    return len(set(services))


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


def _startup_test(regular: dict[str, Any]) -> list[str]:
    test = regular.get("Test")
    if not isinstance(test, list) or len(test) < 2:
        raise ContractError("regular health command cannot become startup health")
    if test[0] == "CMD-SHELL" and len(test) == 2 and isinstance(test[1], str):
        command = test[1]
    elif test[0] == "CMD" and all(isinstance(part, str) for part in test[1:]):
        command = shlex.join(test[1:])
    else:
        raise ContractError(
            f"unsupported regular health command form for startup health: {test!r}"
        )
    return ["CMD-SHELL", command]


def _verify_startup(container: ContainerHealth) -> None:
    if container.regular is None:
        raise ContractError(f"{container.service}: regular health check is absent")
    startup = container.startup
    if not isinstance(startup, dict):
        raise ContractError(f"{container.service}: native startup health check is absent")
    if startup.get("Test") != _startup_test(container.regular):
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
    container_bin: str,
    project: str,
    env_files: Sequence[str] = (),
    *,
    check_engine: bool = True,
) -> int:
    server_version = check_capability(container_bin) if check_engine else "already checked"
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
        startup_test = _startup_test(container.regular)
        timeout_ns = container.regular["Timeout"]
        _run(
            [
                container_bin,
                "update",
                f"--health-startup-cmd={startup_test[1]}",
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
    capability = (
        f"Podman {server_version}" if check_engine else "Podman capability already checked"
    )
    print(
        f"{capability}: verified native startup health for "
        f"{len(relevant)} service(s); configured {configured} stopped container(s)."
    )
    return len(relevant)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "assert-healthy",
            "check",
            "check-compose",
            "check-docker",
            "configure",
            "docker-mode",
            "engine-socket-path",
            "initialize-opensearch",
            "initialize-postgres",
            "prepare-host-directories",
            "prepare-shared-data",
        ),
    )
    parser.add_argument("--container-bin", default="podman")
    parser.add_argument("--expected-docker-mode", choices=("rootful", "rootless"))
    parser.add_argument("--project", default="onyx")
    parser.add_argument("--env-file", action="append", default=[])
    parser.add_argument("--skip-capability-check", action="store_true")
    parser.add_argument("--postgres")
    parser.add_argument("--opensearch")
    parser.add_argument("--audit-config")
    parser.add_argument("--data-root")
    parser.add_argument("--mode", choices=("lite", "full"))
    parser.add_argument("--doc-source")
    parser.add_argument("--default-doc-source")
    parser.add_argument("--service", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.action == "check":
            version = check_capability(args.container_bin)
            print(f"Podman {version} startup-health controls are available.")
        elif args.action == "check-compose":
            version = check_compose_capability(args.container_bin)
            print(f"Compose {version} required model features are available.")
        elif args.action == "check-docker":
            mode = check_docker_engine(
                args.container_bin, expected_mode=args.expected_docker_mode
            )
            print(f"Docker {mode} daemon mode is supported.")
        elif args.action == "docker-mode":
            print(docker_engine_mode(args.container_bin))
        elif args.action == "engine-socket-path":
            print(engine_socket_path(args.container_bin))
        elif args.action == "assert-healthy":
            assert_services_healthy(args.container_bin, args.project, args.service)
        elif args.action == "prepare-shared-data":
            prepared = prepare_shared_data(
                postgres=args.postgres, opensearch=args.opensearch
            )
            print("Prepared shared Docker data for Podman: " + ", ".join(prepared))
        elif args.action == "prepare-host-directories":
            if args.data_root is None or args.mode is None:
                raise ContractError(
                    "--data-root and --mode are required for host-data preparation"
                )
            prepared = prepare_host_directories(
                args.data_root,
                full=args.mode == "full",
                doc_source=args.doc_source,
                default_doc_source=args.default_doc_source,
            )
            suffix = "y" if len(prepared) == 1 else "ies"
            print(f"Prepared {len(prepared)} host bind director{suffix}.")
        elif args.action == "initialize-postgres":
            if args.postgres is None:
                raise ContractError("--postgres is required for initialize-postgres")
            result = initialize_postgres_data(
                args.container_bin, args.postgres, args.env_file
            )
            print(f"Podman PostgreSQL data {result} and prepared.")
        elif args.action == "initialize-opensearch":
            if args.opensearch is None or args.audit_config is None:
                raise ContractError(
                    "--opensearch and --audit-config are required for "
                    "initialize-opensearch"
                )
            result = initialize_opensearch_data(
                args.container_bin,
                args.opensearch,
                args.audit_config,
                args.env_file,
            )
            print(f"Podman OpenSearch data {result} and prepared.")
        else:
            configure_project(
                args.container_bin,
                args.project,
                args.env_file,
                check_engine=not args.skip_capability_check,
            )
    except (ContractError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
