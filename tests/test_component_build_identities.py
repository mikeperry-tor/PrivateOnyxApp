from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

if sys.platform not in ("linux", "darwin"):
    raise unittest.SkipTest("component fixtures require supported Linux/macOS filesystem and flock contracts")

from test_onyx_network_isolation import ROOT, _wrapper_neutral_environment


IMAGES = {
    "myst": "MYST_IMAGE",
    "teep": "TEEP_IMAGE",
    "tailscale": "TAILSCALE_IMAGE",
    "tor": "TOR_IMAGE",
    "obscura": "OBSCURA_IMAGE",
    "searxng": "SEARXNG_WRAPPER_IMAGE",
    "controller": "CODE_INTERPRETER_IMAGE",
    "executor": "PYTHON_EXECUTOR_IMAGE",
}
SELECTORS = (
    "OS_SECURITY_UPDATE_REVISION", "MYST_NODE_REPO", "MYST_NODE_REF",
    "TEEP_REPO", "TEEP_REF", "GO_BUILD_IMAGE", "ALPINE_RUNTIME_IMAGE",
    "TAILSCALE_UPSTREAM_IMAGE", "TOR_BASE_IMAGE", "SEARXNG_IMAGE", "SEARXNG_IMAGE_TAG",
    "SEARXNG_PYTHON_BUILD_IMAGE", "OBSCURA_UPSTREAM_IMAGE", "OBSCURA_BUILDER_IMAGE",
    "OBSCURA_SOURCE_REF", "OBSCURA_SOURCE_SHA256", "OBSCURA_RELEASE_VERSION",
    "CODE_INTERPRETER_UPSTREAM_IMAGE", "PYTHON_EXECUTOR_UPSTREAM_IMAGE",
    "CODE_INTERPRETER_IMAGE_TAG", "PYTHON_EXECUTOR_IMAGE_TAG",
    "ONYX_BACKEND_IMAGE", "ONYX_WEB_SERVER_IMAGE",
    "SEARXNG_WRAPPER_BUILD_INPUTS", "OBSCURA_WRAPPER_BUILD_INPUTS",
    "PYTHON_EXECUTOR_WRAPPER_BUILD_INPUTS",
)
INSPECT = "inspect-component-identities:\n\t@printf '%s\\n' " + " ".join(
    f"'{name}=$({name})'" for name in (*IMAGES.values(), *SELECTORS)
) + "\n"


def _environment() -> dict[str, str]:
    env = _wrapper_neutral_environment()
    for line in (ROOT / "stack.versions.env").read_text().splitlines():
        if line and not line.startswith("#"):
            env.pop(line.split("=", 1)[0], None)
    for name in (*IMAGES.values(), *SELECTORS, "MAKEFLAGS", "MAKEOVERRIDES"):
        env.pop(name, None)
    for name in ("MYST_DOCKERFILE", "TEEP_DOCKERFILE", "SEARXNG_DOCKERFILE",
                 "OBSCURA_DOCKERFILE", "PYTHON_EXECUTOR_DOCKERFILE"):
        env.pop(name, None)
    return env


def _make(root: Path, goals: tuple[str, ...], assignments: tuple[str, ...] = (),
          *, extra: str = INSPECT, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([
        "make", "--no-print-directory", "-s", "-f", "Makefile", "-f", "-",
        *goals, "ENV_FILE=.env.wrapper.example", "HOST_OS=Darwin",
        "PRIVATE_ONYX_DOCKER_ENGINE_MODE=rootful", "PRIVATE_ONYX_DOCKER_GATEWAY_MODE=isolated",
        "DOCKER_SOCK_PATH=/tmp/component-fixture.sock", "CONTAINER_BIN=docker", *assignments,
    ], cwd=root, input=extra, env=_environment() | (env or {}), text=True, capture_output=True,
       check=False)


def _selection(root: Path, *assignments: str, env: dict[str, str] | None = None) -> dict[str, str]:
    result = _make(root, ("inspect-component-identities",), assignments, env=env)
    if result.returncode:
        raise AssertionError(result.stderr)
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


class ComponentBuildIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="component-build-inputs-")
        cls.fixture = Path(cls.temporary.name).resolve()
        # Copy only tracked, public build inputs. No private/generated tree or
        # operator environment file is inspected or made available to Make.
        paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
        roots = ("browser/", "executor/", "searxng/", "myst/", "teep/", "tailscale/",
                 "tor/", "embedserv/", "tools/components/", "onyx/code-interpreter/")
        files = {"Makefile", "stack.versions.env", ".env.wrapper.example", ".dockerignore", ".containerignore",
                 "tools/components/identity.py"}
        for name in sorted(set(paths) | files):
            if name in files or name.startswith(roots):
                destination = cls.fixture / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, destination)
        cls.baseline = _selection(cls.fixture)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def assert_changed_owners(self, selected: dict[str, str], owners: set[str]) -> None:
        changed = {owner for owner, name in IMAGES.items() if selected[name] != self.baseline[name]}
        self.assertEqual(changed, owners)

    def test_selection_inputs_change_their_complete_owning_identity(self) -> None:
        cases = {
            "OS_SECURITY_UPDATE_REVISION": set(IMAGES),
            "MYST_NODE_REPO": {"myst"}, "MYST_NODE_REF": {"myst"},
            "TEEP_REPO": {"teep"}, "TEEP_REF": {"teep"},
            "GO_BUILD_IMAGE": {"myst", "teep"}, "ALPINE_RUNTIME_IMAGE": {"myst", "teep"},
            "TAILSCALE_UPSTREAM_IMAGE": {"tailscale"}, "TOR_BASE_IMAGE": {"tor"},
            "SEARXNG_IMAGE": {"searxng"}, "SEARXNG_PYTHON_BUILD_IMAGE": {"searxng"},
            "OBSCURA_UPSTREAM_IMAGE": {"obscura"}, "OBSCURA_BUILDER_IMAGE": {"obscura"},
            "OBSCURA_SOURCE_REF": {"obscura"}, "OBSCURA_SOURCE_SHA256": {"obscura"},
            "OBSCURA_RELEASE_VERSION": {"obscura"},
            "CODE_INTERPRETER_UPSTREAM_IMAGE": {"controller"},
            "PYTHON_EXECUTOR_UPSTREAM_IMAGE": {"executor"},
            "SEARXNG_IMAGE_TAG": {"searxng"}, "CODE_INTERPRETER_IMAGE_TAG": {"controller"},
            "PYTHON_EXECUTOR_IMAGE_TAG": {"executor"},
        }
        for name, owners in cases.items():
            with self.subTest(selector=name):
                original = self.baseline[name]
                if "@sha256:" in original:
                    value = original.split("@", 1)[0] + "@sha256:" + "a" * 64
                elif name.endswith("_REPO"):
                    value = "https://example.invalid/fixture/source.git"
                elif name.endswith("_REF"):
                    value = "a" * 40
                elif name.endswith("_SHA256"):
                    value = "a" * 64
                elif name == "OS_SECURITY_UPDATE_REVISION":
                    value = "20990101.1"
                else:
                    value = "9.9.9"
                self.assert_changed_owners(_selection(self.fixture, f"{name}={value}"), owners)
        self.assertEqual(_selection(self.fixture), self.baseline)

    def test_dockerfile_overrides_change_the_selected_build(self) -> None:
        for owner, variable, original in (
            ("myst", "MYST_DOCKERFILE", "myst/build/Dockerfile"),
            ("teep", "TEEP_DOCKERFILE", "teep/build/Dockerfile"),
            ("tor", "TOR_DOCKERFILE", "tor/Dockerfile"),
            ("obscura", "OBSCURA_DOCKERFILE", "browser/obscura_image/Dockerfile"),
            ("searxng", "SEARXNG_DOCKERFILE", "searxng/Dockerfile"),
            ("executor", "PYTHON_EXECUTOR_DOCKERFILE", "executor/Dockerfile"),
        ):
            with self.subTest(owner=owner):
                alternate = self.fixture / "fixture.Dockerfile"
                shutil.copyfile(self.fixture / original, alternate)
                try:
                    self.assert_changed_owners(_selection(self.fixture, f"{variable}=fixture.Dockerfile"), {owner})
                finally:
                    alternate.unlink()

    def test_new_copied_package_and_patch_files_change_selection(self) -> None:
        for name, owner in (
            ("browser/obscura_client/private_onyx_obscura/fixture_new.py", "searxng"),
            ("browser/obscura_client/private_onyx_obscura/fixture_data.json", "searxng"),
            ("searxng/engines/fixture_new.py", "searxng"),
            ("browser/obscura_image/patches/fixture_new.patch", "obscura"),
            ("browser/obscura_image/patches/fixture_data.json", "obscura"),
        ):
            with self.subTest(name=name):
                path = self.fixture / name
                try:
                    path.write_text("# copied public input\n")
                    self.assert_changed_owners(_selection(self.fixture), {owner})
                finally:
                    path.unlink()

    def test_each_copied_file_dockerfile_and_context_policy_changes_selection(self) -> None:
        files: dict[str, set[str]] = {}
        for owner, names in (
            ("myst", "myst/build/Dockerfile"),
            ("teep", "teep/build/Dockerfile teep/build/entrypoint.sh"),
            ("tailscale", "tailscale/Dockerfile"), ("tor", "tor/Dockerfile"),
            ("controller", "onyx/code-interpreter/Dockerfile onyx/code-interpreter/upgrade-os.sh"),
            ("executor", self.baseline["PYTHON_EXECUTOR_WRAPPER_BUILD_INPUTS"]),
            ("searxng", self.baseline["SEARXNG_WRAPPER_BUILD_INPUTS"]),
            ("obscura", self.baseline["OBSCURA_WRAPPER_BUILD_INPUTS"]),
        ):
            for name in names.split():
                files.setdefault(name, set()).add(owner)
        for name in (".dockerignore", ".containerignore"):
            files[name] = set(IMAGES)
        files["tools/components/identity.py"] = set(IMAGES)
        # Package directories are recursive COPY inputs, including data files.
        for name, owners in list(files.items()):
            path = self.fixture / name
            if path.is_dir():
                del files[name]
                for member in sorted(path.rglob("*")):
                    if member.is_file():
                        files.setdefault(member.relative_to(self.fixture).as_posix(), set()).update(owners)
        for name, owners in files.items():
            with self.subTest(input=name):
                path = self.fixture / name
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b"\n# changed public build input\n")
                    self.assert_changed_owners(_selection(self.fixture), owners)
                finally:
                    path.write_bytes(original)
        self.assertEqual(_selection(self.fixture), self.baseline)

    def test_recursive_package_directories_and_modes_change_selection(self) -> None:
        package = self.fixture / "browser/obscura_client/private_onyx_obscura"
        self._assert_recursive_inputs(package, "searxng")
        self._assert_recursive_inputs(self.fixture / "browser/obscura_image/patches", "obscura")

    def _assert_recursive_inputs(self, package: Path, owner: str) -> None:
        tree = package / "fixture_tree"
        for kind in ("nested-python", "nested-data", "empty-directory"):
            with self.subTest(kind=kind):
                try:
                    nested = tree / "nested"
                    nested.mkdir(parents=True)
                    if kind != "empty-directory":
                        (nested / ("module.py" if kind == "nested-python" else "data.bin")).write_bytes(b"fixture\n")
                    self.assert_changed_owners(_selection(self.fixture), {owner})
                finally:
                    shutil.rmtree(tree)
        for path in (package, next(path for path in sorted(package.iterdir()) if path.is_file())):
            with self.subTest(mode=str(path.relative_to(self.fixture))):
                mode = stat.S_IMODE(path.stat().st_mode)
                try:
                    path.chmod(mode ^ 0o020)
                    self.assert_changed_owners(_selection(self.fixture), {owner})
                finally:
                    path.chmod(mode)

    def test_canonical_python_cache_inputs_do_not_change_selection(self) -> None:
        cache = self.fixture / "browser/obscura_client/private_onyx_obscura/__pycache__"
        self.assertFalse(cache.exists())
        try:
            cache.mkdir()
            (cache / "fixture.cpython-314.pyc").write_bytes(b"local compilation cache\n")
            self.assertEqual(_selection(self.fixture), self.baseline)
        finally:
            shutil.rmtree(cache)

    def test_symlinked_inputs_and_dockerfile_specific_policies_fail_closed(self) -> None:
        package = self.fixture / "browser/obscura_client/private_onyx_obscura"
        target = self.fixture / "fixture-public-target"
        target.write_text("synthetic public target\n")
        for source in (package / "fixture_link.py", self.fixture / ".dockerignore"):
            with self.subTest(link=str(source.relative_to(self.fixture))):
                original = source.read_bytes() if source.exists() else None
                mode = stat.S_IMODE(source.stat().st_mode) if source.exists() else None
                try:
                    source.unlink(missing_ok=True)
                    source.symlink_to(target)
                    result = _make(self.fixture, ("inspect-component-identities",))
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn("symlink", result.stderr)
                finally:
                    source.unlink()
                    if original is not None:
                        source.write_bytes(original)
                        source.chmod(mode)
        target.unlink()
        directory = self.fixture / "fixture-source-directory"
        directory.mkdir()
        shutil.copy2(self.fixture / "myst/build/Dockerfile", directory / "Dockerfile")
        parent_link = self.fixture / "fixture-parent-link"
        try:
            parent_link.symlink_to(directory, target_is_directory=True)
            result = _make(self.fixture, ("inspect-component-identities",),
                           ("MYST_DOCKERFILE=fixture-parent-link/Dockerfile",))
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("symlink", result.stderr)
        finally:
            parent_link.unlink()
            shutil.rmtree(directory)
        policy = self.fixture / "myst/build/Dockerfile.dockerignore"
        try:
            policy.write_text("# unsupported policy override\n")
            result = _make(self.fixture, ("inspect-component-identities",))
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("Dockerfile-specific ignore policy", result.stderr)
        finally:
            policy.unlink()

    def test_ignore_symlinks_are_rejected_before_policy_contents_are_read(self) -> None:
        spec = importlib.util.spec_from_file_location("component_identity", ROOT / "tools/components/identity.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "public-rule-target"
            target.write_text("synthetic excluded target\n")
            policy = root / ".dockerignore"
            policy.symlink_to(target)
            with patch.object(Path, "read_text", autospec=True, side_effect=Path.read_text) as read:
                with self.assertRaisesRegex(ValueError, "symlink"):
                    module.identity("fixture", "generation", [policy])
                read.assert_not_called()

    def test_committed_defaults_require_valid_reviewed_registry_entries(self) -> None:
        inventory = self.fixture / "tools/components/ownership.json"
        original = inventory.read_bytes()
        registered = json.loads(original)
        references = [
            (self.baseline["ONYX_BACKEND_IMAGE"].split("@", 1)[0], "ONYX_IMAGE_TAG=v99.0.0"),
            (self.baseline["ONYX_WEB_SERVER_IMAGE"].split("@", 1)[0], "ONYX_IMAGE_TAG=v99.0.0"),
            (self.baseline["SEARXNG_IMAGE"].split("@", 1)[0], "SEARXNG_IMAGE_TAG=9.9.9"),
        ]
        try:
            for reference, override in references:
                for state in ("missing", "invalid"):
                    with self.subTest(reference=reference, state=state):
                        data = json.loads(original)
                        self.assertIn(reference, registered["registry_digests"])
                        if state == "missing":
                            del data["registry_digests"][reference]
                        else:
                            data["registry_digests"][reference] = "invalid-digest"
                        inventory.write_text(json.dumps(data))
                        result = _make(self.fixture, ("inspect-component-identities",))
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                        self.assertIn("reviewed", result.stderr)
                        if state == "missing":
                            result = _make(self.fixture, ("inspect-component-identities",), (override,))
                            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            inventory.write_bytes(original)

    def test_explicit_output_overrides_preserve_caller_selection(self) -> None:
        expected = {name: f"fixture/{owner}:explicit" for owner, name in IMAGES.items()}
        expected.update(ONYX_BACKEND_IMAGE="fixture/onyx-backend:explicit",
                        ONYX_WEB_SERVER_IMAGE="fixture/onyx-web-server:explicit",
                        SEARXNG_IMAGE="fixture/searxng-upstream:explicit")
        for origin in ("environment", "command-line"):
            with self.subTest(origin=origin):
                overrides = tuple(f"{name}={value}" for name, value in expected.items())
                selected = _selection(self.fixture, *(overrides if origin == "command-line" else ()),
                                      env=expected if origin == "environment" else {})
                self.assertEqual({name: selected[name] for name in expected}, expected)

    def test_missing_required_build_inputs_fail_selection(self) -> None:
        for name in ("myst/build/Dockerfile", "teep/build/entrypoint.sh", "tor/Dockerfile"):
            with self.subTest(input=name):
                path = self.fixture / name
                original = path.read_bytes()
                mode = stat.S_IMODE(path.stat().st_mode)
                try:
                    path.unlink()
                    result = _make(self.fixture, ("inspect-component-identities",))
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                finally:
                    path.write_bytes(original)
                    path.chmod(mode)
        for name in ("GO_BUILD_IMAGE", "ALPINE_RUNTIME_IMAGE", "SEARXNG_PYTHON_BUILD_IMAGE"):
            with self.subTest(selector=name):
                result = _make(self.fixture, ("inspect-component-identities",), (f"{name}=",))
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_each_builder_uses_native_base_pull_and_cache_policy(self) -> None:
        targets = {
            "myst": "myst-build", "teep": "teep-build", "tailscale": "tailscale-build",
            "tor": "tor-build", "obscura": "obscura-build", "searxng": "searxng-build",
            "controller": "code-interpreter-build", "executor": "executor-build",
        }
        arguments = {
            "myst": {"MYST_NODE_REPO": "MYST_NODE_REPO", "MYST_NODE_REF": "MYST_NODE_REF",
                     "GO_BUILD_IMAGE": "GO_BUILD_IMAGE", "ALPINE_RUNTIME_IMAGE": "ALPINE_RUNTIME_IMAGE"},
            "teep": {"TEEP_REPO": "TEEP_REPO", "TEEP_REF": "TEEP_REF",
                     "GO_BUILD_IMAGE": "GO_BUILD_IMAGE", "ALPINE_RUNTIME_IMAGE": "ALPINE_RUNTIME_IMAGE"},
            "tailscale": {"UPSTREAM_IMAGE": "TAILSCALE_UPSTREAM_IMAGE",
                          "OS_SECURITY_UPDATE_REVISION": "OS_SECURITY_UPDATE_REVISION"},
            "tor": {"TOR_BASE_IMAGE": "TOR_BASE_IMAGE"},
            "obscura": {name: name for name in ("OBSCURA_UPSTREAM_IMAGE", "OBSCURA_BUILDER_IMAGE",
                        "OBSCURA_RELEASE_VERSION", "OBSCURA_SOURCE_REF", "OBSCURA_SOURCE_SHA256")},
            "searxng": {"SEARXNG_UPSTREAM_IMAGE": "SEARXNG_IMAGE",
                        "SEARXNG_PYTHON_BUILD_IMAGE": "SEARXNG_PYTHON_BUILD_IMAGE"},
            "controller": {"UPSTREAM_IMAGE": "CODE_INTERPRETER_UPSTREAM_IMAGE",
                           "OS_SECURITY_UPDATE_REVISION": "OS_SECURITY_UPDATE_REVISION"},
            "executor": {"PYTHON_EXECUTOR_UPSTREAM_IMAGE": "PYTHON_EXECUTOR_UPSTREAM_IMAGE",
                         "OS_SECURITY_UPDATE_REVISION": "OS_SECURITY_UPDATE_REVISION"},
        }
        platform_images = _selection(self.fixture, "DOCKER_DEFAULT_PLATFORM=linux/amd64")
        for engine_name, pull_flag in (("docker", "--pull=true"), ("podman", "--pull=always")):
            with self.subTest(engine=engine_name), tempfile.TemporaryDirectory() as directory:
                engine = Path(directory) / engine_name
                log = Path(directory) / "calls.jsonl"
                engine.write_text(
                    "#!/usr/bin/env python3\nimport json, os, sys\n"
                    "with open(os.environ['COMPONENT_ENGINE_LOG'], 'a') as stream:\n"
                    "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                    "if sys.argv[1] not in ('pull', 'build'): raise SystemExit(37)\n"
                )
                engine.chmod(0o755)
                selected = targets if engine_name == "docker" else {
                    owner: target for owner, target in targets.items() if owner not in ("controller", "executor")
                }
                result = _make(self.fixture, tuple(selected.values()), (f"CONTAINER_BIN={engine}", "DOCKER_DEFAULT_PLATFORM=linux/amd64"),
                               extra="", env={"COMPONENT_ENGINE_LOG": str(log)})
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                builds = {call[call.index("--tag") + 1]: call for call in calls if call[0] == "build"}
                self.assertEqual(set(builds), {platform_images[IMAGES[owner]] for owner in selected})
                for owner in selected:
                    command = builds[platform_images[IMAGES[owner]]]
                    self.assertIn(pull_flag, command)
                    self.assertIn("--platform=linux/amd64", command)
                    self.assertIn("--no-cache", command)
                    actual_args = dict(command[i + 1].split("=", 1)
                                       for i, token in enumerate(command) if token == "--build-arg")
                    for name, selector in arguments[owner].items():
                        self.assertEqual(actual_args[name], platform_images[selector], (owner, name))
                self.assertEqual([call for call in calls if call[0] == "pull"],
                                 [["pull", "--platform=linux/amd64", platform_images["TOR_BASE_IMAGE"]]])


if __name__ == "__main__":
    unittest.main()
