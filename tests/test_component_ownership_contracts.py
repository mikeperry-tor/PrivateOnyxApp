"""Offline Docker Compose rendering of public Make-selected engine models."""
from __future__ import annotations

from collections import Counter
from itertools import product
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_component_build_identities import IMAGES, _make
from test_onyx_network_isolation import SECRET_ENV, _compose_command, _wrapper_neutral_environment
from tools.components.manage import normalized, read_pins, reviewed_reference

HASHES = {
    "myst": "MYST_WRAPPER_SOURCE_HASH", "teep": "TEEP_WRAPPER_SOURCE_HASH",
    "tailscale": "TAILSCALE_SOURCE_HASH", "tor": "TOR_WRAPPER_SOURCE_HASH",
    "obscura": "OBSCURA_WRAPPER_SOURCE_HASH", "searxng": "SEARXNG_WRAPPER_SOURCE_HASH",
    "controller": "CODE_INTERPRETER_SOURCE_HASH", "executor": "PYTHON_EXECUTOR_WRAPPER_SOURCE_HASH",
}
BASE_ALIASES = {
    ("controller", "UPSTREAM_IMAGE"): "CODE_INTERPRETER_UPSTREAM_IMAGE",
    ("tailscale", "UPSTREAM_IMAGE"): "TAILSCALE_UPSTREAM_IMAGE",
    ("searxng", "SEARXNG_UPSTREAM_IMAGE"): "SEARXNG_IMAGE",
}
SERVICE_IMAGES = {
    "myst-client": "MYST_IMAGE", "teep": "TEEP_IMAGE", "tailscale-funnel": "TAILSCALE_IMAGE",
    "tor": "TOR_IMAGE", "obscura": "OBSCURA_IMAGE", "searxng-core": "SEARXNG_WRAPPER_IMAGE",
    "code-interpreter": "CODE_INTERPRETER_IMAGE", "api_server": "ONYX_BACKEND_IMAGE",
    "background": "ONYX_BACKEND_IMAGE", "web_server": "ONYX_WEB_SERVER_IMAGE",
}
DIGEST = re.compile(r".+@sha256:[0-9a-f]{64}$")


def hash_input_expressions(makefile: str) -> dict[str, str]:
    """Let Make expand the input expressions from its actual identity calls."""
    result = {}
    for owner, variable in HASHES.items():
        line = next(line for line in makefile.splitlines() if line.startswith(variable + " := "))
        expression = line.split("$(call os_image_hash,", 1)[1]
        depth, arguments, start = 1, [], 0
        for index, character in enumerate(expression):
            depth += (character == "(") - (character == ")")
            if character == "," and depth == 1:
                arguments.append(expression[start:index])
                start = index + 1
            elif depth == 0:
                arguments.append(expression[start:index])
                break
        if len(arguments) != 2:
            raise AssertionError(f"unsupported identity declaration for {owner}")
        result[owner] = arguments[1]
    return result


def instructions(path: Path):
    for line in re.sub(r"\\\n\s*", " ", path.read_text()).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        operation, _, rest = line.partition(" ")
        yield operation.upper(), rest.strip()


def copied_sources(path: Path) -> list[str]:
    sources = []
    stages = set()
    for operation, rest in instructions(path):
        if operation == "FROM":
            tokens = shlex.split(rest)
            if len(tokens) > 2 and tokens[-2].upper() == "AS":
                stages.add(tokens[-1])
        if operation not in ("COPY", "ADD"):
            continue
        tokens = shlex.split(rest)
        flags = []
        while tokens and tokens[0].startswith("--"):
            flags.append(tokens.pop(0))
        origin = next((flag.split("=", 1)[1] for flag in flags if flag.startswith("--from=")), None)
        if origin is not None:
            if origin not in stages:
                raise AssertionError(f"external COPY source needs an owner: {path.name}: {origin}")
            continue
        if rest.startswith("["):
            tokens = json.loads(rest)
        if len(tokens) < 2:
            raise AssertionError(f"unsupported COPY/ADD instruction: {path}")
        sources.extend(tokens[:-1])
    return sources


class ComponentOwnershipContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inventory = json.loads((ROOT / "tools/components/ownership.json").read_text())
        cls.pins = read_pins(ROOT / "stack.versions.env")
        cls.inputs = hash_input_expressions((ROOT / "Makefile").read_text())
        exports = {name for declaration in re.findall(r"^export\s+(.+)$", (ROOT / "Makefile").read_text(), re.M)
                   for name in declaration.split("=", 1)[0].replace(":", "").split() if name.isidentifier()}
        cls.variables = sorted(set(cls.pins) | set(IMAGES.values()) | {
            "LITE_FILES", "FULL_FILES", "COMPOSE_PROFILES", "SEARXNG_IMAGE",
            "ONYX_BACKEND_IMAGE", "ONYX_WEB_SERVER_IMAGE",
        } | exports | {"CONTRACT_INPUTS_" + name for name in HASHES})
        cls.snapshots = {}

    @classmethod
    def selection(cls, engine: str, features=(False, False, False, False), *, host_os="Darwin", engine_mode="rootful"):
        key = engine, features, host_os, engine_mode
        if key not in cls.snapshots:
            egress, onion, tailscale, executor = features
            settings = {
                "CONTAINER_BIN": engine,
                "HOST_OS": host_os,
                "PRIVATE_ONYX_DOCKER_ENGINE_MODE": engine_mode,
                "TOR_EGRESS_ENABLED": str(egress).lower(),
                "TOR_ONION_SERVICE_ENABLED": str(onion).lower(),
                "TAILSCALE_FUNNEL_ENABLED": str(tailscale).lower(),
                "ONYX_CODE_INTERPRETER_ENABLE_NETWORK": str(executor).lower(),
            }
            # Only inert public secrets, never SECRET_ENV's placeholder images.
            settings.update({name: value for name, value in SECRET_ENV.items()
                             if name in {"OBSCURA_CDP_TOKEN", "SEARXNG_SECRET", "USER_AUTH_SECRET",
                                         "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD", "S3_AWS_ACCESS_KEY_ID", "S3_AWS_SECRET_ACCESS_KEY"}})
            extra = "\n".join(f"CONTRACT_INPUTS_{owner} := {value}" for owner, value in cls.inputs.items())
            extra += "\ninspect-ownership-contract:\n\t@printf '%s\\n' " + " ".join(
                f"'{name}=$({name})'" for name in cls.variables) + "\n"
            result = _make(ROOT, ("inspect-ownership-contract",), tuple(f"{name}={value}" for name, value in settings.items()), extra=extra)
            if result.returncode:
                raise AssertionError(result.stderr)
            values = dict(line.split("=", 1) for line in result.stdout.splitlines())
            cls.snapshots[key] = values
        return cls.snapshots[key]

    def test_effective_default_registry_and_local_images_have_authorities(self):
        owned = {value for name, value in self.pins.items() if value.startswith("docker.io/")}
        owned.update(reference + "@" + digest for reference, digest in self.inventory["registry_digests"].items())
        seen_services = set()
        cases = [(engine, features, "Darwin", "rootful")
                 for engine in ("docker", "podman")
                 for features in product((False, True), repeat=4)
                 if engine != "podman" or not features[3]]
        # Linux-only overlays have additional consumers. A focused all-feature
        # selection covers these without duplicating the Darwin Cartesian set.
        cases += [("docker", (True, True, True, True), "Linux", engine_mode)
                  for engine_mode in ("rootful", "rootless")]
        cases += [("podman", (True, True, True, False), "Linux", "rootful")]
        for engine, features, host_os, engine_mode in cases:
            selected = self.selection(engine, features, host_os=host_os, engine_mode=engine_mode)
            local = {selected[name] for name in IMAGES.values()}
            self.assertEqual(len(local), len(IMAGES), "each local output has one component owner")
            for mode in ("lite", "full"):
                with self.subTest(engine=engine, host_os=host_os, engine_mode=engine_mode, features=features, mode=mode):
                    command = [*_compose_command(), "--env-file", "stack.versions.env", "--env-file", ".env.wrapper.example"]
                    for path in selected[mode.upper() + "_FILES"].split(":"):
                        command += ["-f", path]
                    command += ["config", "--no-env-resolution", "--format", "json"]
                    environment = _wrapper_neutral_environment()
                    for name in (*self.pins, *IMAGES.values(), "COMPOSE_PROFILES"):
                        environment.pop(name, None)
                    environment.update({name: value for name, value in selected.items() if value})
                    # Direct model helpers also need an explicit Teep output;
                    # this comes from actual Make, never the raw upstream fallback.
                    self.assertTrue(environment["TEEP_IMAGE"].startswith("local/"))
                    result = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True, check=False)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    services = json.loads(result.stdout)["services"]
                    seen_services.update(services)
                    for name, service in services.items():
                        image = service["image"]
                        if image in local:
                            continue
                        # Inherited owner tags are qualified by the ledger;
                        # C3 preparation materializes those exact bytes.
                        qualified = reviewed_reference(image, self.inventory, required=True)
                        self.assertRegex(qualified, DIGEST, f"{name} needs reviewed immutable bytes")
                        self.assertIn(normalized(qualified), owned, f"{name} image has no declared owner")
                    for service in ("nginx", "tailscale-frontend-gateway", "tor-frontend-gateway"):
                        if service in services:
                            self.assertEqual(services[service]["image"], selected["NGINX_IMAGE"])
                    for service, variable in SERVICE_IMAGES.items():
                        if service in services:
                            self.assertEqual(services[service]["image"], selected[variable])
                    if "code-interpreter" in services:
                        self.assertEqual(services["code-interpreter"]["environment"]["PYTHON_EXECUTOR_DOCKER_IMAGE"], selected["PYTHON_EXECUTOR_IMAGE"])
                    if engine == "podman":
                        self.assertNotIn("code-interpreter", services)
        self.assertTrue({"tor", "tor-frontend-gateway", "tailscale-funnel", "code-interpreter", "opensearch", "cache"} <= seen_services)

    def test_parameterized_external_build_bases_use_owned_reviewed_selections(self):
        selected = self.selection("docker")
        declared = {name for component in self.inventory["components"].values() for name in component["selection"]}
        qualified = {normalized(value) for value in self.pins.values() if value.startswith("docker.io/")}
        qualified.update(reference + "@" + digest for reference, digest in self.inventory["registry_digests"].items())
        for owner, component in self.inventory["components"].items():
            for name in component["build"]:
                path = ROOT / name
                if path.name != "Dockerfile":
                    continue
                stages = set()
                defaults = {rest.split("=", 1)[0]: rest.split("=", 1)[1] for operation, rest in instructions(path)
                            if operation == "ARG" and "=" in rest}
                for operation, rest in instructions(path):
                    if operation != "FROM":
                        continue
                    tokens = shlex.split(rest)
                    base = next(token for token in tokens if not token.startswith("--"))
                    if base not in stages and base != "scratch":
                        match = re.fullmatch(r"\$\{([A-Z_]+)\}|\$([A-Z_]+)", base)
                        if match:
                            argument = next(value for value in match.groups() if value)
                            key = BASE_ALIASES.get((owner, argument), argument)
                            self.assertTrue(key in declared or (owner == "searxng" and key == "SEARXNG_IMAGE"), (name, key))
                            reference = selected[key]
                            self.assertRegex(reference, DIGEST)
                            self.assertIn(normalized(reference), qualified)
                            if argument in defaults:
                                self.assertEqual(normalized(defaults[argument]), normalized(reference),
                                                 f"{name}: standalone default diverges from the committed authority")
                        else:
                            self.assertIn(base, component.get("literal_bases", []))
                    if len(tokens) > 2 and tokens[-2].upper() == "AS":
                        stages.add(tokens[-1])

    def test_every_local_copy_is_in_its_actual_identity_input_declaration(self):
        selected = self.selection("docker")
        for owner, component in self.inventory["components"].items():
            for name in component["build"]:
                path = ROOT / name
                if path.name != "Dockerfile":
                    continue
                declared = [ROOT / value for value in selected["CONTRACT_INPUTS_" + owner].split()]
                for source in copied_sources(path):
                    with self.subTest(owner=owner, source=source):
                        copied = ROOT / source.rstrip("/")
                        self.assertTrue(copied.exists(), source)
                        self.assertTrue(any(copied == member or (member.is_dir() and copied.is_relative_to(member))
                                            for member in declared),
                                        f"{owner}: COPY {source} lacks recursive identity coverage")

    def test_committed_selection_keys_have_one_owner_and_outputs_are_derived(self):
        counts = Counter(key for component in self.inventory["components"].values() for key in component["selection"])
        self.assertFalse({key: count for key, count in counts.items() if count != 1})
        repositories = {key for key in self.pins if key.endswith("_WRAPPER_IMAGE_REPOSITORY")}
        self.assertEqual(set(self.pins), set(counts) | repositories | {"OS_SECURITY_UPDATE_REVISION"})
        selected = self.selection("docker")
        outputs = {selected[name].split(":", 1)[0] for name in IMAGES.values()}
        for key in repositories:
            self.assertIn(self.pins[key], outputs)
        self.assertFalse(set(IMAGES.values()) & self.pins.keys(), "derived outputs must not become independent committed pins")


if __name__ == "__main__":
    unittest.main()
