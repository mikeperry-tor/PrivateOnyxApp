#!/usr/bin/env python3
"""Maintainer-only component ownership reconciliation and refresh generation."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
REVISION = "OS_SECURITY_UPDATE_REVISION"


def read_pins(path: Path) -> dict[str, str]:
    values = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or name in values:
            raise ValueError(f"{path}:{number}: invalid or duplicate pin")
        values[name] = value
    return values


def write_pins(path: Path, changes: dict[str, str]) -> None:
    """Replace existing keys without shell evaluation, preserving unrelated text."""
    values = read_pins(path)
    missing = changes.keys() - values.keys()
    if missing:
        raise ValueError(f"missing manifest keys: {sorted(missing)}")
    lines = path.read_text().splitlines()
    output = "\n".join(
        f"{line.partition('=')[0]}={changes[line.partition('=')[0]]}"
        if line.partition("=")[0] in changes else line for line in lines
    ) + "\n"
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.chmod(path.stat().st_mode & 0o777)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def normalized(image: str) -> str:
    if not image or "$" in image or any(c.isspace() for c in image):
        raise ValueError(f"unresolved or invalid image selection: {image!r}")
    parts = image.split("/")
    if len(parts) == 1:
        image = "docker.io/library/" + image
    elif "." not in parts[0] and ":" not in parts[0] and parts[0] != "localhost":
        image = "docker.io/" + image
    if image.startswith("index.docker.io/"):
        image = "docker.io/" + image.removeprefix("index.docker.io/")
    if image.startswith("library/"):
        image = "docker.io/" + image
    if image.startswith("docker.io/") and image.count("/") == 1:
        image = "docker.io/library/" + image.removeprefix("docker.io/")
    return image


def repository(image: str) -> str:
    name = image.split("@", 1)[0]
    if ":" not in name.rsplit("/", 1)[-1]:
        raise ValueError(f"image requires an explicit tag: {image}")
    return name.rsplit(":", 1)[0]


def reviewed_reference(image: str, inventory: dict, *, required: bool = False) -> str:
    image = normalized(image)
    if "@" in image:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image.split("@", 1)[1]):
            raise ValueError("invalid image digest")
        return image
    digest = inventory.get("registry_digests", {}).get(image)
    if digest is None:
        if required:
            raise ValueError(f"missing reviewed digest for {image}; qualify the owner-selected artifact")
        return image
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError(f"invalid reviewed registry digest for {image}")
    return image + "@" + digest


def reviewed_onyx_source(ref: str, inventory: dict) -> str:
    """Use the reviewed commit for its release; retain other explicit refs."""
    decision = inventory.get("compatibility", {})
    if ref != decision.get("onyx"):
        return ref
    source = decision.get("onyx_source")
    if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("invalid reviewed Onyx source commit")
    return source


def reconcile(model: dict, inventory: dict, pins: dict[str, str]) -> dict[str, str]:
    services = model.get("services", {})
    contracts = inventory["onyx_services"]
    if services.keys() != contracts.keys():
        raise ValueError("Onyx service contract changed; review ownership: "
                         f"added={sorted(services.keys() - contracts.keys())}, "
                         f"removed={sorted(contracts.keys() - services.keys())}")
    changes = {}
    for service, contract in contracts.items():
        image = normalized(services[service].get("image", ""))
        if repository(image) != contract["repository"]:
            raise ValueError(f"{service}: owner repository changed to {image}; review contract")
        if contract.get("application") and not image.endswith(":" + pins["ONYX_IMAGE_TAG"]):
            raise ValueError(f"{service}: does not select the exact Onyx release")
        if contract.get("application") or contract.get("inherited"):
            reviewed_reference(image, inventory, required=True)
        if contract.get("compatibility"):
            decision = inventory["compatibility"]
            if (pins["ONYX_IMAGE_TAG"] != decision["onyx"] or
                image != contract["repository"] + ":" + decision["default"] or
                any(pins[key] != decision["selected"] for key in
                    ("CODE_INTERPRETER_IMAGE_TAG", "PYTHON_EXECUTOR_IMAGE_TAG")) or
                any(normalized(pins[key]) != artifact for key, artifact in decision["artifacts"].items())):
                raise ValueError("controller release/default changed; repeat bounded compatibility review")
        if "copy" in contract:
            key = contract["copy"]
            current = normalized(pins[key])
            # Preserve a reviewed digest when the upstream authority still selects its tag.
            if current != image and not ("@" not in image and current.split("@")[0] == image):
                if "source" in contract:
                    raise ValueError("MinIO artifact changed; verify source identity and update image/source together")
                changes[key] = image
    return changes


def verify_checkout(compose: Path, release: str) -> str:
    checkout = Path(subprocess.check_output(
        ["git", "-C", str(compose.parent), "rev-parse", "--show-toplevel"], text=True).strip())
    selected = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "--verify", f"refs/tags/{release}^{{commit}}"], text=True).strip()
    head = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    relative = compose.relative_to(checkout)
    committed = subprocess.check_output(["git", "-C", str(checkout), "show", f"{selected}:{relative}"])
    if head != selected or compose.read_bytes() != committed:
        raise ValueError("owner Compose must be unmodified at the exact selected release tag")
    return selected


def render_owner(compose: Path, engine: str, release: str) -> dict:
    # No operator environment, project .env, or service environment files.
    environment = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    environment["IMAGE_TAG"] = release
    result = subprocess.run([engine, "compose", "--env-file", "/dev/null", "--profile", "*", "-f", str(compose),
                             "config", "--no-env-resolution", "--format", "json"],
                            env=environment, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def next_revision(current: str, date: str) -> str:
    revision_tuple(current)
    if date < current.split(".")[0]:
        raise ValueError("refresh date precedes committed generation")
    sequence = int(current.split(".")[1]) if "." in current else 0
    return date + "." + str(sequence + 1 if current.startswith(date) else 1)


def revision_tuple(value: str) -> tuple[str, int]:
    if not re.fullmatch(r"\d{8}(?:\.[1-9]\d*)?", value):
        raise ValueError("invalid refresh revision; expected YYYYMMDD[.sequence]")
    date, _, sequence = value.partition(".")
    datetime.strptime(date, "%Y%m%d")
    return date, int(sequence or "0")


def check_revision(root: Path, base: str, pins: dict[str, str], inventory: dict | None = None) -> None:
    # Resolve first so a typo or absent history cannot masquerade as no changes.
    commit = subprocess.check_output(["git", "rev-parse", "--verify", base + "^{commit}"], cwd=root, text=True).strip()
    old = subprocess.check_output(["git", "show", f"{commit}:stack.versions.env"], cwd=root, text=True)
    previous = dict(line.split("=", 1) for line in old.splitlines() if line and not line.startswith("#"))
    changed = subprocess.check_output(["git", "diff", "--name-only", commit, "--"], cwd=root, text=True).splitlines()
    if inventory is None:
        inventory = json.loads(Path(__file__).with_name("ownership.json").read_text())
    inputs = tuple(inventory["refresh_inputs"]["directories"])
    files = inventory["refresh_inputs"]["files"]
    # Enumerate names only; never open untracked operator configuration or data.
    changed += subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "--",
                                        *inputs, *files], cwd=root, text=True).splitlines()
    relevant = [p for p in changed if p in files or p.startswith(inputs)]
    if relevant and revision_tuple(pins[REVISION]) <= revision_tuple(previous[REVISION]):
        raise ValueError("component inputs changed without refresh revision bump: " + ", ".join(relevant))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pins", type=Path, default=ROOT / "stack.versions.env")
    parser.add_argument("--inventory", type=Path, default=Path(__file__).with_name("ownership.json"))
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="reconcile the exact release's deployment Compose")
    sync.add_argument("--compose", type=Path)
    sync.add_argument("--container-bin", default="docker")
    sync.add_argument("--write", action="store_true")
    commands.add_parser("bump", help="publish the next explicit refresh generation")
    check = commands.add_parser("check-revision")
    check.add_argument("--base", required=True)
    resolve = commands.add_parser("resolve")
    resolve.add_argument("--reference", required=True)
    resolve.add_argument("--require-reviewed", action="store_true")
    args = parser.parse_args()
    pins = read_pins(args.pins)
    try:
        if args.command == "resolve":
            print(reviewed_reference(args.reference, json.loads(args.inventory.read_text()), required=args.require_reviewed))
        elif args.command == "bump":
            revision = next_revision(pins[REVISION], datetime.now(timezone.utc).strftime("%Y%m%d"))
            write_pins(args.pins, {REVISION: revision})
            print(f"{REVISION}={revision}")
        elif args.command == "check-revision":
            check_revision(ROOT, args.base, pins, json.loads(args.inventory.read_text()))
        else:
            inventory = json.loads(args.inventory.read_text())
            release = pins["ONYX_IMAGE_TAG"]
            if not re.fullmatch(r"v\d+\.\d+\.\d+(?:[-.][A-Za-z0-9.-]+)?", release):
                raise ValueError("Onyx owner requires an explicit release tag")
            if args.compose:
                source = verify_checkout(args.compose.resolve(), release)
                reviewed = reviewed_onyx_source(release, inventory)
                if reviewed != release and source != reviewed:
                    raise ValueError("Onyx source changed; repeat bounded compatibility review")
                model = render_owner(args.compose.resolve(), args.container_bin, release)
            else:
                with tempfile.TemporaryDirectory(prefix="private-onyx-owner-") as directory:
                    compose = Path(directory) / "docker-compose.yml"
                    source = reviewed_onyx_source(release, inventory)
                    url = f"https://raw.githubusercontent.com/onyx-dot-app/onyx/{source}/deployment/docker_compose/docker-compose.yml"
                    with urlopen(url, timeout=60) as response:
                        compose.write_bytes(response.read())
                    model = render_owner(compose, args.container_bin, release)
            changes = reconcile(model, inventory, pins)
            if changes and not args.write:
                raise ValueError("owner selections differ; review then sync --write: " + json.dumps(changes, sort_keys=True))
            if changes:
                write_pins(args.pins, changes)
            print(json.dumps({"release": pins["ONYX_IMAGE_TAG"], "changes": changes,
                              "inherited": {name: service["image"] for name, service in model["services"].items()
                                            if inventory["onyx_services"][name].get("inherited")}}, sort_keys=True))
    except (ValueError, KeyError) as exc:
        parser.exit(1, f"component reconciliation failed: {exc}\n")


if __name__ == "__main__":
    main()
