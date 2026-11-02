#!/usr/bin/env python3
"""Prepare selected Onyx configuration without starting services or installing images."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import tempfile
from urllib.parse import quote
from urllib.request import urlopen

from .manage import reviewed_onyx_source

CONTRACT = 1
STAMP = ".private-onyx-configuration.json"
ARTIFACTS = {
    "deployment/docker-compose.yml": ("docker_compose/docker-compose.yml", 0o644),
    "deployment/docker-compose.onyx-lite.yml": ("docker_compose/docker-compose.onyx-lite.yml", 0o644),
    "deployment/docker-compose.craft.yml": ("docker_compose/docker-compose.craft.yml", 0o644),
    "deployment/env.template": ("docker_compose/env.template", 0o644),
    "README.md": ("docker_compose/README.md", 0o644),
    "data/nginx/app.conf.template": ("data/nginx/app.conf.template", 0o644),
    "data/nginx/run-nginx.sh": ("data/nginx/run-nginx.sh", 0o755),
}


class ConfigurationError(RuntimeError):
    pass


def atomic_write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def preparation_lock(destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (destination.parent / ("." + destination.name + ".configuration.lock")).open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def environment_assignments(lines: list[str]) -> list[tuple[str, int, int]]:
    """Locate dotenv assignments while retaining quoted multiline value spans."""
    assignments = []
    pending = None
    escaped = False
    for index, line in enumerate(lines):
        if pending is None:
            matched = re.match(r"^[ \t]*(?:export[ \t]+)?([^=\s]+)[ \t]*=[ \t]*([\s\S]*)", line)
            if not matched or matched.group(1).startswith("#"):
                continue
            key, value = matched.groups()
            if not value.startswith(("'", '\"')):
                assignments.append((key, index, index))
                continue
            pending = (key, index, value[0])
            escaped = False
            value = value[1:]
        else:
            value = line
        for character in value:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == pending[2]:
                assignments.append((pending[0], pending[1], index))
                pending = None
                break
    if pending is not None:
        raise ConfigurationError("unterminated quoted generated environment assignment")
    return assignments


def selected_environment(content: str, values: dict[str, str]) -> str:
    """Change selected assignment spans, retaining unrelated bytes and comments."""
    lines = content.splitlines(keepends=True)
    assignments = environment_assignments(lines)
    replacements = []
    additions = []
    for key, value in values.items():
        if not re.fullmatch(r"[A-Za-z0-9_.-]+|", value):
            raise ConfigurationError(f"unsafe selected value for {key}")
        positions = [(start, end) for name, start, end in assignments if name == key]
        if len(positions) > 1:
            raise ConfigurationError(f"duplicate generated environment assignment: {key}")
        replacement = f"{key}={value}\n"
        if positions:
            replacements.append((*positions[0], replacement))
        else:
            additions.append(replacement)
    for start, end, replacement in sorted(replacements, reverse=True):
        lines[start:end + 1] = [replacement]
    if additions:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.extend(additions)
    return "".join(lines)


def sync_environment(env_file: Path, image_tag: str, controller_tag: str, *, template: str | None = None, lite: bool = False) -> None:
    if env_file.is_symlink():
        raise ConfigurationError("generated environment must not be a symlink")
    existing = env_file.exists()
    if existing:
        content = env_file.read_bytes().decode()
        mode = stat.S_IMODE(env_file.stat().st_mode)
    elif template is not None:
        content = selected_environment(template, {"AUTH_TYPE": "basic", "USER_AUTH_SECRET": secrets.token_hex(32)})
        if lite:
            content = selected_environment(content, {"COMPOSE_PROFILES": "", "FILE_STORE_BACKEND": "postgres"})
        mode = 0o600
    else:
        raise ConfigurationError("generated environment is missing; prepare configuration first")
    updated = selected_environment(content, {"IMAGE_TAG": image_tag, "CODE_INTERPRETER_IMAGE_TAG": controller_tag})
    if not existing or updated != content:
        atomic_write(env_file, updated.encode(), mode)


def configuration_source(root: Path, ref: str) -> str:
    inventory = json.loads((root / "tools/components/ownership.json").read_text())
    return reviewed_onyx_source(ref, inventory)


def desired_identity(root: Path, ref: str, image_tag: str, controller_tag: str, revision: str) -> str:
    digest = hashlib.sha256()
    for value in (str(CONTRACT), ref, configuration_source(root, ref), image_tag, controller_tag, revision):
        digest.update(value.encode() + b"\0")
    # The generator itself is an input; no operator configuration enters identity.
    digest.update(Path(__file__).read_bytes())
    for path in (root / "Makefile", root / "docker-compose.yaml", *sorted((root / "compose_overlays").glob("*.yml"))):
        digest.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def artifact_evidence(destination: Path) -> dict[str, dict]:
    result = {}
    for relative, (_, mode) in ARTIFACTS.items():
        path = destination / relative
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != mode:
            raise ConfigurationError(f"missing or invalid generated artifact: {relative}")
        result[relative] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "mode": mode}
    return result


def reusable(destination: Path, identity: str) -> bool:
    try:
        record = json.loads((destination / STAMP).read_text())
        return record == {"identity": identity, "artifacts": artifact_evidence(destination)}
    except (FileNotFoundError, json.JSONDecodeError, ConfigurationError):
        return False


def fetch(url: str) -> bytes:
    with urlopen(url, timeout=120) as response:
        return response.read()


def validate_staged(stage: Path, container_bin: str) -> None:
    """Use the selected native Compose frontend, never resolving service env files."""
    script = stage / "data/nginx/run-nginx.sh"
    checked = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True)
    if checked.returncode:
        raise ConfigurationError("staged nginx runner failed shell syntax validation")
    empty_env = stage / ".validation.env"
    empty_env.write_text("")
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.startswith("COMPOSE_"):
            environment.pop(key)
    base = stage / "deployment/docker-compose.yml"
    for overlay in (None, "docker-compose.onyx-lite.yml", "docker-compose.craft.yml"):
        command = [container_bin, "compose", "--env-file", str(empty_env), "-f", str(base)]
        if overlay:
            command += ["-f", str(stage / "deployment" / overlay)]
        command += ["config", "--no-env-resolution", "--no-interpolate", "--format", "json"]
        result = subprocess.run(command, env=environment, capture_output=True, text=True)
        if result.returncode:
            # Compose diagnostics may include expanded environment values: do not echo them.
            raise ConfigurationError(f"staged Compose validation failed ({overlay or 'base'}); exit {result.returncode}")
        try:
            model = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ConfigurationError("staged Compose validation returned invalid JSON") from exc
        if not isinstance(model, dict) or not isinstance(model.get("services"), dict) or not model["services"]:
            raise ConfigurationError("staged Compose validation produced no service contracts")


def prepare(root: Path, destination: Path, env_file: Path, ref: str, image_tag: str, controller_tag: str, revision: str, container_bin: str, *, lite: bool = False, force: bool = False, sync_only: bool = False, downloader=None, validator=None) -> bool:
    if not ref or not revision or not image_tag or not controller_tag:
        raise ConfigurationError("configuration ref, selected versions and refresh revision must be explicit")
    identity = desired_identity(root, ref, image_tag, controller_tag, revision)
    downloader = downloader or fetch
    validator = validator or validate_staged
    with preparation_lock(destination):
        if sync_only:
            if not reusable(destination, identity):
                (destination / STAMP).unlink(missing_ok=True)
            try:
                sync_environment(env_file, image_tag, controller_tag)
            except (ConfigurationError, OSError, UnicodeError):
                (destination / STAMP).unlink(missing_ok=True)
                raise
            return False
        if not force and reusable(destination, identity) and env_file.is_file():
            try:
                sync_environment(env_file, image_tag, controller_tag)
            except (ConfigurationError, OSError, UnicodeError):
                (destination / STAMP).unlink(missing_ok=True)
                raise
            return False
        with tempfile.TemporaryDirectory(prefix=".onyx-config-stage-", dir=destination.parent) as temporary:
            stage = Path(temporary)
            source_ref = configuration_source(root, ref)
            for relative, (source, mode) in ARTIFACTS.items():
                url = f"https://raw.githubusercontent.com/onyx-dot-app/onyx/{quote(source_ref, safe='')}/deployment/{source}"
                payload = downloader(url)
                if not payload.strip():
                    raise ConfigurationError(f"empty downloaded configuration: {relative}")
                atomic_write(stage / relative, payload, mode)
            validator(stage, container_bin)
            # Validate the initial environment before changing any installed artifact.
            template = (stage / "deployment/env.template").read_text()
            selected_environment(template, {"IMAGE_TAG": image_tag, "CODE_INTERPRETER_IMAGE_TAG": controller_tag})
            destination.mkdir(parents=True, exist_ok=True)
            (destination / STAMP).unlink(missing_ok=True)
            for relative, (_, mode) in ARTIFACTS.items():
                atomic_write(destination / relative, (stage / relative).read_bytes(), mode)
            (destination / "data/nginx/local").mkdir(parents=True, exist_ok=True)
            sync_environment(env_file, image_tag, controller_tag, template=template, lite=lite)
            record = {"identity": identity, "artifacts": artifact_evidence(destination)}
            atomic_write(destination / STAMP, (json.dumps(record, sort_keys=True) + "\n").encode(), 0o644)
            return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--image-tag", required=True)
    parser.add_argument("--controller-tag", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--container-bin", required=True)
    parser.add_argument("--lite", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--sync-only", action="store_true")
    args = parser.parse_args()
    try:
        changed = prepare(**vars(args))
    except (ConfigurationError, OSError, ValueError) as exc:
        # Exception values from network/process/filesystem errors can contain sensitive paths/URLs.
        parser.exit(1, f"ERROR: {exc}\n" if isinstance(exc, ConfigurationError) else f"ERROR: configuration preparation failed ({type(exc).__name__})\n")
    if args.sync_only:
        print("Synchronized selected Onyx environment")
    else:
        print("Prepared selected Onyx configuration" if changed else "Selected Onyx configuration is reusable")


if __name__ == "__main__":
    main()
