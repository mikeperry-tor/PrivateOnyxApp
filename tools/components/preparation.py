#!/usr/bin/env python3
"""Prepare the active Compose image selection without creating containers."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import stat
import sys
import tempfile

from tools.components.manage import normalized, read_pins, reviewed_reference
from tools.components.configuration import STAMP, desired_identity, reusable, preparation_lock

DOCKER_INFO = '{"ID":{{json .ID}},"DockerRootDir":{{json .DockerRootDir}},"OSType":{{json .OSType}},"Architecture":{{json .Architecture}}}'
DOCKER_VERSION = '{"ClientAPIVersion":{{json .Client.APIVersion}},"ServerAPIVersion":{{json .Server.APIVersion}}}'
PODMAN_INFO = '{"host":{"hostname":{{json .Host.Hostname}},"os":{{json .Host.OS}},"arch":{{json .Host.Arch}},"variant":{{json .Host.Variant}}},"store":{"graphRoot":{{json .Store.GraphRoot}}}}'
DOCKER_IMAGE = '[{"Os":{{json .Os}},"Architecture":{{json .Architecture}},"Variant":{{json .Variant}}}]'
PODMAN_IMAGE = '[{"Os":{{json .Os}},"Architecture":{{json .Architecture}}}]'


class PreparationError(RuntimeError):
    pass


def selected_image(value: str) -> str:
    if not isinstance(value, str) or "://" in value or ("@" in value and not re.fullmatch(r"[^@]+@sha256:[0-9a-f]{64}", value)):
        raise PreparationError("invalid image reference syntax; URLs and credentials are not image selections")
    try:
        return normalized(value)
    except ValueError as exc:
        raise PreparationError("invalid or unresolved image reference") from exc


def reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise PreparationError("preparation state must not use symbolic links")


def run(command: list[str], *, environment: dict | None = None, missing_ok: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run(command, env=environment, capture_output=True, text=True)
    if result.returncode and not missing_ok:
        # Never relay Compose's full model, diagnostics, or engine configuration.
        raise PreparationError(f"required {Path(command[0]).name} {command[1]} operation failed (exit {result.returncode})")
    return result


def run_visible(command: list[str], *, environment: dict | None = None) -> None:
    # Controlled Make recipes preserve their foreground progress/readiness
    # messages; helper stdout remains reserved for the final overlay path.
    result = subprocess.run(command, env=environment, stdout=sys.stderr, text=True)
    if result.returncode:
        raise PreparationError(f"required {Path(command[0]).name} {command[1]} operation failed (exit {result.returncode})")


def json_output(command: list[str]) -> dict | list:
    try:
        return json.loads(run(command).stdout)
    except json.JSONDecodeError as exc:
        raise PreparationError("engine returned invalid JSON") from exc


def platform(value: str) -> str:
    pieces = value.lower().split("/")
    if len(pieces) not in (2, 3) or any(not piece for piece in pieces):
        raise PreparationError("platform must select OS/architecture[/variant]")
    aliases = {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "arm", "armv6l": "arm"}
    original = pieces[1]
    pieces[1] = aliases.get(original, original)
    if len(pieces) == 2 and original in ("armv7l", "armv6l"):
        pieces.append("v7" if original == "armv7l" else "v6")
    if pieces == ["linux", "arm64", "v8"]:
        pieces.pop()
    return "/".join(pieces)


def engine_selection(container_bin: str) -> tuple[str, str, bool, bool]:
    podman = "podman" in Path(container_bin).name.lower()
    info = json_output([container_bin, "info", "--format", PODMAN_INFO if podman else DOCKER_INFO])
    if not isinstance(info, dict):
        raise PreparationError("engine info must be an object")
    if podman:
        host, store = info.get("host", {}), info.get("store", {})
        identity = ["podman", host.get("hostname"), store.get("graphRoot")]
        os_name, architecture = host.get("os"), host.get("arch")
    else:
        identity = ["docker", info.get("ID"), info.get("DockerRootDir")]
        os_name, architecture = info.get("OSType"), info.get("Architecture")
    if not all(isinstance(value, str) and value for value in identity) or not os_name or not architecture:
        raise PreparationError("engine did not identify its selected store and native platform")
    # Native identity keeps aliases for one engine/store under one lock. Podman
    # hostname/root collisions conservatively serialize distinct stores; no
    # preparation receipts are reused in place of native image inspection.
    store_key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    native = f"{os_name}/{architecture}"
    if podman and host.get("variant"):
        native += "/" + host["variant"]
    native = platform(native)
    selected_platform = platform(os.environ.get("DOCKER_DEFAULT_PLATFORM") or native)
    platform_inspection = False
    if not podman:
        version = json_output([container_bin, "version", "--format", DOCKER_VERSION])
        if not isinstance(version, dict) or any(
            not isinstance(version.get(key), str) or not re.fullmatch(r"\d+\.\d+", version[key])
            for key in ("ClientAPIVersion", "ServerAPIVersion")
        ):
            raise PreparationError("Docker must report valid client and server API versions")
        platform_inspection = all(tuple(map(int, version[key].split("."))) >= (1, 49)
                                  for key in ("ClientAPIVersion", "ServerAPIVersion"))
        if not platform_inspection and selected_platform != native:
            raise PreparationError("foreign Docker image platforms require client and server API 1.49+ for selected-platform inspection")
    if podman and selected_platform.count("/") > 1:
        raise PreparationError("selected Podman image variant cannot be verified by supported image inspection")
    return store_key, selected_platform, podman, platform_inspection


def active_model(container_bin: str, compose_arguments: list[str]) -> tuple[dict, list[str]]:
    prefix = [container_bin, "compose", *compose_arguments, "config", "--no-env-resolution"]
    model = json_output([*prefix, "--format", "json"])
    services = run([*prefix, "--services"]).stdout.splitlines()
    if not isinstance(model, dict) or not isinstance(model.get("services"), dict) or not services:
        raise PreparationError("effective Compose model has no active service contracts")
    if len(services) != len(set(services)) or any(service not in model["services"] for service in services):
        raise PreparationError("active Compose service selection is inconsistent")
    return model, services


def present(container_bin: str, image: str, selected_platform: str, platform_inspection: bool) -> bool:
    image_projection = PODMAN_IMAGE if "podman" in Path(container_bin).name.lower() else DOCKER_IMAGE
    result = run([container_bin, "image", "inspect", *(["--platform", selected_platform] if platform_inspection else []),
                  "--format", image_projection, image], missing_ok=True)
    if result.returncode:
        return False
    try:
        inspected = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PreparationError("image inspection returned invalid JSON") from exc
    if not isinstance(inspected, list) or len(inspected) != 1:
        raise PreparationError("image inspection returned ambiguous image metadata")
    metadata = inspected[0]
    os_name, architecture = metadata.get("Os"), metadata.get("Architecture")
    if not os_name or not architecture:
        raise PreparationError("selected image does not report its OS/architecture")
    actual = f"{os_name}/{architecture}"
    if metadata.get("Variant"):
        actual += "/" + metadata["Variant"]
    return platform(actual) == selected_platform


def atomic_write(path: Path, content: bytes, mode: int = 0o644) -> None:
    reject_symlink(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
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
def store_lock(state_dir: Path, store_key: str):
    reject_symlink(state_dir)
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = state_dir.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise PreparationError("preparation state directory must be owned by the current user with owner-only permissions")
    lock = state_dir / (store_key + ".lock")
    reject_symlink(lock)
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


@contextmanager
def configuration_read_lock(stamp: Path | None, revision: str):
    if stamp is None:
        yield ""
        return
    if stamp.name != STAMP:
        raise PreparationError("configuration stamp must name the selected configuration completion file")
    destination = stamp.parent
    with preparation_lock(destination, shared=True):
        selections = [os.environ.get(name) for name in ("ONYX_CONFIG_REF", "ONYX_IMAGE_TAG", "CODE_INTERPRETER_IMAGE_TAG")]
        if not all(selections):
            raise PreparationError("selected configuration ref/application/controller must be exported")
        expected = desired_identity(Path.cwd(), *selections, revision)
        if not reusable(destination, expected):
            raise PreparationError("selected configuration changed or is incomplete; rerun startup preparation")
        yield hashlib.sha256(stamp.read_bytes()).hexdigest()


def configuration_identity() -> list[tuple[str, str]]:
    paths = os.environ.get("COMPOSE_FILE", "").split(os.environ.get("COMPOSE_PATH_SEPARATOR", os.pathsep))
    if not paths or any(not path for path in paths):
        raise PreparationError("COMPOSE_FILE must identify the selected public configuration")
    # These are Compose source files, never the --env-file inputs or model environment.
    return [(path, hashlib.sha256(Path(path).read_bytes()).hexdigest()) for path in paths]


def prepare(container_bin: str, inventory: dict, pins: dict[str, str], state_dir: Path, locals: dict[str, str], caller_owned: set[str], compose_arguments: list[str], *, executor: str | None = None, overlay_output: Path | None = None, make_command: list[str] | None = None, revision: str | None = None, start_target: str | None = None, configuration_stamp: Path | None = None) -> Path:
    store_key, native_platform, podman, platform_inspection = engine_selection(container_bin)
    if podman and executor:
        raise PreparationError("Docker executor selection must be omitted on Podman")
    if start_target not in (None, "_start-lite", "_start-full"):
        raise PreparationError("startup target must be _start-lite or _start-full")
    revision = revision if revision is not None else pins.get("OS_SECURITY_UPDATE_REVISION")
    if not revision:
        raise PreparationError("committed refresh revision is missing")
    local_images = {selected_image(image): target for image, target in locals.items()}
    caller_images = {selected_image(image) for image in caller_owned}
    if caller_images.intersection(local_images):
        raise PreparationError("an image cannot be both a local builder output and a caller-owned registry selection")
    with store_lock(state_dir, store_key), configuration_read_lock(configuration_stamp, revision) as generation:
        if overlay_output:
            reject_symlink(overlay_output)
            overlay_output.unlink(missing_ok=True)
        model, active_services = active_model(container_bin, compose_arguments)
        images, service_selections = {}, {}
        for service in active_services:
            config = model["services"][service]
            image = selected_image(config.get("image", ""))
            selected_platform = platform(config.get("platform") or native_platform)
            if podman and selected_platform.count("/") > 1:
                raise PreparationError("selected Podman service image variant cannot be verified by supported image inspection")
            if not podman and not platform_inspection and selected_platform != native_platform:
                raise PreparationError("foreign Docker service platforms require client and server API 1.49+ for selected-platform inspection")
            if image in local_images and selected_platform != native_platform:
                raise PreparationError("local service platform differs from its selected build identity platform")
            service_selections[service] = (image, selected_platform)
            if image in images and images[image] != selected_platform:
                raise PreparationError("one image reference selects conflicting service platforms")
            images[image] = selected_platform
        if executor:
            executor_image = selected_image(executor)
            if executor_image in images and images[executor_image] != native_platform:
                raise PreparationError("executor image conflicts with selected service platform")
            images[executor_image] = native_platform
        resolved = {}
        for image, selected_platform in sorted(images.items()):
            is_local = image in local_images
            if is_local:
                selected = image
            else:
                try:
                    selected = reviewed_reference(image, inventory, required=image not in caller_images)
                except ValueError as exc:
                    raise PreparationError("selected registry image needs a valid reviewed digest: " + image) from exc
                if image in caller_images and "@" not in selected:
                    print(f"Caller-selected mutable image: {image}", file=sys.stderr)
            if not present(container_bin, selected, selected_platform, platform_inspection):
                if is_local:
                    environment = dict(os.environ, DOCKER_DEFAULT_PLATFORM=selected_platform)
                    run_visible([*(make_command or [os.environ.get("MAKE", "make")]), local_images[image], "DOCKER_DEFAULT_PLATFORM=" + selected_platform], environment=environment)
                else:
                    run_visible([container_bin, "pull", "--platform", selected_platform, selected])
                if not present(container_bin, selected, selected_platform, platform_inspection):
                    raise PreparationError(f"selected artifact is unavailable for {selected_platform}: {image}")
            resolved[image] = selected
            kind = "local build" if is_local else "caller registry" if image in caller_images else "reviewed registry"
            print(f"Prepared {kind}: {selected} ({selected_platform})", file=sys.stderr)
        overlay = {"services": {service: {"image": resolved[image], "platform": selected_platform} for service, (image, selected_platform) in sorted(service_selections.items())}}
        payload = (json.dumps(overlay, sort_keys=True, separators=(",", ":")) + "\n").encode()
        key = hashlib.sha256(json.dumps([store_key, revision, generation, service_selections, images, resolved, configuration_identity()], sort_keys=True).encode()).hexdigest()
        output = state_dir / (key + ".json")
        reject_symlink(output)
        if output.exists():
            if output.read_bytes() != payload:
                raise PreparationError("immutable prepared overlay content does not match its identity")
        else:
            atomic_write(output, payload, 0o444)
        if overlay_output:
            atomic_write(overlay_output, (str(output.resolve()) + "\n").encode())
        if start_target:
            environment = dict(os.environ, PRIVATE_ONYX_PREPARED_PLATFORM=native_platform)
            assignments = ["PREPARED_IMAGES_FILE=" + str(output.resolve()), "PRIVATE_ONYX_PREPARED_PLATFORM=" + native_platform]
            for service, variable in (("api_server", "ONYX_BACKEND_IMAGE"), ("web_server", "ONYX_WEB_SERVER_IMAGE")):
                if service in service_selections:
                    environment[variable] = resolved[service_selections[service][0]]
                    if service == "api_server":
                        backend_platform = service_selections[service][1]
                        environment["PRIVATE_ONYX_PREPARED_BACKEND_PLATFORM"] = backend_platform
                        assignments.append("PRIVATE_ONYX_PREPARED_BACKEND_PLATFORM=" + backend_platform)
                    assignments.append(variable + "=" + environment[variable])
            # MAKEFLAGS propagates original command-line values, which outrank
            # environment exports. Explicit assignments retain qualified selections.
            run_visible([*(make_command or [os.environ.get("MAKE", "make")]), start_target, *assignments], environment=environment)
        return output.resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container-bin", required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--pins", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--overlay-output", type=Path)
    parser.add_argument("--local", action="append", default=[])
    parser.add_argument("--caller-owned", action="append", default=[])
    parser.add_argument("--executor")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--start-target", choices=("_start-lite", "_start-full"))
    parser.add_argument("--configuration-stamp", type=Path)
    parser.add_argument("--make-command", default=os.environ.get("MAKE", "make"))
    parser.add_argument("compose_arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        local = {}
        for item in args.local:
            image, separator, target = item.rpartition("=")
            if not separator or not image or not target or selected_image(image) in local:
                raise PreparationError("local image declarations require unique IMAGE=MAKE_TARGET values")
            local[selected_image(image)] = target
        compose_arguments = args.compose_arguments
        if compose_arguments[:1] == ["--"]:
            compose_arguments = compose_arguments[1:]
        output = prepare(args.container_bin, json.loads(args.inventory.read_text()), read_pins(args.pins), args.state_dir, local, set(args.caller_owned), compose_arguments, executor=args.executor, overlay_output=args.overlay_output, make_command=shlex.split(args.make_command), revision=args.revision, start_target=args.start_target, configuration_stamp=args.configuration_stamp)
    except (PreparationError, ValueError, OSError) as exc:
        parser.exit(1, f"ERROR: {exc}\n" if isinstance(exc, PreparationError) else f"ERROR: artifact preparation failed ({type(exc).__name__})\n")
    if not args.start_target:
        print(output)


if __name__ == "__main__":
    main()
