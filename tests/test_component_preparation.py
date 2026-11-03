from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from unittest.mock import patch

if sys.platform not in ("linux", "darwin"):
    raise unittest.SkipTest("component preparation uses supported Linux/macOS flock contracts")

from tools.components import configuration

SOURCE = Path(__file__).resolve().parents[1] / "tools/components/preparation.py"
spec = importlib.util.spec_from_file_location("component_preparation", SOURCE)
preparation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preparation)

REGISTRY = "docker.io/library/redis:7.4-alpine"
DIGEST = "sha256:" + "a" * 64
LOCAL = "docker.io/local/derived:identity"


class FakeEngine:
    def __init__(self):
        self.store = "store-one"
        self.arch = "aarch64"
        self.variant = ""
        self.version = {"ClientAPIVersion": "1.56", "ServerAPIVersion": "1.56"}
        self.inspect_default_platform = "linux/arm64"
        self.services = {"api": {"image": REGISTRY}, "local": {"image": LOCAL}, "disabled": {"image": "disabled:no-fetch"}}
        self.active = ["api", "local"]
        self.images = {}
        self.events = []
        self.fail_pull = False
        self.fail_build = False
        self.pull_platform = None
        self.first_pull = None
        self.release_pull = None
        self.first_start = None
        self.release_start = None
        self.info_event = None
        self.streams = []
        self.build_outputs = {"local-build": LOCAL}

    def run(self, command, *, env=None, capture_output=None, text=None, stdout=None):
        self.events.append((command, env))
        self.streams.append((command, capture_output, stdout))
        stdout, code = "", 0
        if command[0] == "fake-make":
            if self.fail_build:
                code = 1
            elif command[1] in ("_start-lite", "_start-full"):
                if self.first_start is not None:
                    self.first_start.set()
                    if not self.release_start.wait(5):
                        raise AssertionError("fixture startup lock timed out")
            else:
                self.images[self.build_outputs[command[1]]] = env["DOCKER_DEFAULT_PLATFORM"]
        elif command[1] == "info":
            if "podman" in command[0]:
                if command[-1] != preparation.PODMAN_INFO:
                    raise AssertionError("Podman info must project the supported identity fields only")
                stdout = json.dumps({"host": {"hostname": self.store, "arch": self.arch, "os": "linux", "variant": self.variant}, "store": {"graphRoot": "/fixture/" + self.store}})
            else:
                if command[-1] != preparation.DOCKER_INFO:
                    raise AssertionError("Docker info must project the supported identity fields only")
                stdout = json.dumps({"ID": self.store, "DockerRootDir": "/fixture/" + self.store, "OSType": "linux", "Architecture": self.arch})
            if self.info_event is not None:
                self.info_event.set()
        elif command[1] == "compose":
            if command[-1] == "--services":
                stdout = "\n".join(self.active) + "\n"
            else:
                stdout = json.dumps({"services": self.services, "x-secret": "fixture-secret-canary"})
        elif command[1] == "version":
            if "podman" in command[0] or command[2:] != ["--format", preparation.DOCKER_VERSION]:
                raise AssertionError("Docker version must project client/server API capability only")
            stdout = json.dumps(self.version)
        elif command[1:3] == ["image", "inspect"]:
            expected = preparation.PODMAN_IMAGE if "podman" in command[0] else preparation.DOCKER_IMAGE
            if command[-3:-1] != ["--format", expected]:
                raise AssertionError("Image inspection must project supported platform fields only")
            selected = self.images.get(command[-1])
            requested = command[command.index("--platform") + 1] if "--platform" in command else None
            if isinstance(selected, set):
                inspection_platform = requested or self.inspect_default_platform
                selected = inspection_platform if inspection_platform in selected else None
            elif requested and requested != selected:
                selected = None
            if selected:
                parts = selected.split("/")
                metadata = {"Os": parts[0], "Architecture": parts[1]}
                if len(parts) == 3 and "podman" not in command[0]:
                    metadata["Variant"] = parts[2]
                stdout = json.dumps([metadata])
            else:
                code = 1
        elif command[1] == "pull":
            if self.first_pull is not None:
                self.first_pull.set()
                if not self.release_pull.wait(5):
                    raise AssertionError("fixture timed out waiting for concurrent preparation")
            if self.fail_pull:
                code = 1
            else:
                selected = self.pull_platform or command[command.index("--platform") + 1]
                if isinstance(self.images.get(command[-1]), set):
                    self.images[command[-1]].add(selected)
                else:
                    self.images[command[-1]] = selected
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, code, stdout, "fixture-secret-canary" if code else "")


class PreparationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "public-compose.yml"
        self.config.write_text("services: {}\n")
        (self.root / "Makefile").write_text("fixture generation Makefile\n")
        (self.root / "docker-compose.yaml").write_text("fixture generation wrapper\n")
        self.engine = FakeEngine()
        self.inventory = {"registry_digests": {REGISTRY: DIGEST}}
        self.options = dict(container_bin="fake-docker", inventory=self.inventory,
                            pins={"OS_SECURITY_UPDATE_REVISION": "20261102.1"},
                            state_dir=self.root / "state", locals={LOCAL: "local-build"},
                            caller_owned=set(), compose_arguments=["--env-file", "/fixture/supported.env"],
                            make_command=["fake-make"])
        self.addCleanup(patch.stopall)
        patch.object(preparation.subprocess, "run", side_effect=self.engine.run).start()
        patch.object(preparation.sys, "stderr", io.StringIO()).start()
        patch.dict(os.environ, {"COMPOSE_FILE": str(self.config), "COMPOSE_PROFILES": "", "DOCKER_DEFAULT_PLATFORM": ""}).start()

    def prepare(self, **changes):
        return preparation.prepare(**(self.options | changes))

    def mutation_events(self):
        return [command for command, _ in self.engine.events if command[0] == "fake-make" or command[1] == "pull"]

    def test_populated_old_store_gets_reviewed_digest_and_local_artifact(self):
        self.engine.images[REGISTRY] = "linux/arm64"
        output = self.prepare()
        self.assertEqual(self.mutation_events(), [["fake-docker", "pull", "--platform", "linux/arm64", REGISTRY + "@" + DIGEST], ["fake-make", "local-build", "DOCKER_DEFAULT_PLATFORM=linux/arm64"]])
        overlay = json.loads(output.read_text())
        self.assertEqual(overlay, {"services": {"api": {"image": REGISTRY + "@" + DIGEST, "platform": "linux/arm64"}, "local": {"image": LOCAL, "platform": "linux/arm64"}}})
        self.assertNotIn("fixture-secret-canary", output.read_text())
        self.assertNotIn("disabled", output.read_text())
        self.engine.events.clear()
        self.assertEqual(self.prepare(), output)
        self.assertEqual(self.mutation_events(), [])
        inspections = [command[-1] for command, _ in self.engine.events if command[1:3] == ["image", "inspect"]]
        self.assertCountEqual(inspections, [REGISTRY + "@" + DIGEST, LOCAL])

    def test_changed_moving_tag_digest_and_pruning_cannot_reuse_old_artifact(self):
        original = self.prepare()
        self.inventory["registry_digests"][REGISTRY] = "sha256:" + "b" * 64
        self.assertNotEqual(self.prepare(), original)
        self.engine.events.clear()
        self.engine.images.clear()
        self.prepare()
        self.assertEqual(len(self.mutation_events()), 2)

    def test_mode_feature_transition_prepares_new_active_images_only(self):
        self.prepare()
        extra = "docker.io/library/postgres:15.2-alpine"
        self.inventory["registry_digests"][extra] = "sha256:" + "c" * 64
        self.engine.services["full"] = {"image": extra}
        self.engine.active.append("full")
        self.engine.events.clear()
        output = self.prepare()
        self.assertEqual(len(self.mutation_events()), 1)
        self.assertIn("full", json.loads(output.read_text())["services"])
        self.engine.active.remove("full")
        self.engine.events.clear()
        output = self.prepare()
        self.assertEqual(self.mutation_events(), [])
        self.assertNotIn("full", json.loads(output.read_text())["services"])

    def test_store_and_platform_selection_are_scoped_and_verified(self):
        first = self.prepare()
        self.engine.store = "store-two"
        self.engine.images = {}
        self.assertNotEqual(self.prepare(), first)
        with patch.dict(os.environ, {"DOCKER_DEFAULT_PLATFORM": "linux/amd64"}):
            self.engine.events.clear()
            output = self.prepare()
        self.assertNotEqual(output, first)
        self.assertEqual(len(self.mutation_events()), 2)
        self.assertTrue(all(value == "linux/amd64" for value in self.engine.images.values()))

    def test_modern_docker_reuses_the_requested_variant_independently_of_store_default(self):
        self.engine.active = ["api"]
        self.engine.images[REGISTRY + "@" + DIGEST] = {"linux/arm64", "linux/amd64", "linux/arm/v7"}
        for native, default, selected in (("aarch64", "linux/arm64", "linux/amd64"),
                                           ("amd64", "linux/amd64", "linux/arm/v7"),
                                           ("amd64", "linux/arm64", "linux/amd64")):
            with self.subTest(native=native, default=default, selected=selected), patch.dict(os.environ, {"DOCKER_DEFAULT_PLATFORM": selected}):
                self.engine.arch = native
                self.engine.inspect_default_platform = default
                self.engine.events.clear()
                output = self.prepare(locals={})
                self.assertEqual(self.mutation_events(), [])
                self.assertEqual(json.loads(output.read_text())["services"]["api"]["platform"], selected)
                inspections = [command for command, _ in self.engine.events if command[1:3] == ["image", "inspect"]]
                self.assertTrue(inspections)
                self.assertTrue(all(command[3:5] == ["--platform", selected] for command in inspections))

    def test_legacy_docker_native_preparation_preserves_api_144_support(self):
        for server in ("1.44", "1.56"):
            with self.subTest(client="1.44", server=server):
                self.engine.version = {"ClientAPIVersion": "1.44", "ServerAPIVersion": server}
                self.engine.images.clear()
                self.engine.events.clear()
                self.assertTrue(self.prepare().exists())
                inspections = [command for command, _ in self.engine.events if command[1:3] == ["image", "inspect"]]
                self.assertTrue(inspections)
                self.assertTrue(all("--platform" not in command for command in inspections))
                self.assertEqual(len(self.mutation_events()), 2)
                self.engine.events.clear()
                self.prepare()
                self.assertEqual(self.mutation_events(), [])

    def test_legacy_or_client_capped_docker_rejects_foreign_platforms_before_actions(self):
        for client, server in (("1.44", "1.44"), ("1.44", "1.56"), ("1.56", "1.48")):
            for global_platform in (True, False):
                with self.subTest(client=client, server=server, global_platform=global_platform):
                    self.engine.version = {"ClientAPIVersion": client, "ServerAPIVersion": server}
                    self.engine.events.clear()
                    self.engine.services["api"].pop("platform", None)
                    if not global_platform:
                        self.engine.services["api"]["platform"] = "linux/amd64"
                    with patch.dict(os.environ, {"DOCKER_DEFAULT_PLATFORM": "linux/amd64" if global_platform else ""}):
                        with self.assertRaisesRegex(preparation.PreparationError, "API 1.49"):
                            self.prepare()
                    self.assertEqual(self.mutation_events(), [])
                    self.assertFalse(any(command[1:3] == ["image", "inspect"] for command, _ in self.engine.events))

    def test_docker_selected_platform_inspection_capability_starts_at_api_149(self):
        self.engine.version = {"ClientAPIVersion": "1.49", "ServerAPIVersion": "1.49"}
        with patch.dict(os.environ, {"DOCKER_DEFAULT_PLATFORM": "linux/amd64"}):
            self.assertTrue(self.prepare().exists())
        self.assertTrue(any(command[1:5] == ["image", "inspect", "--platform", "linux/amd64"] for command, _ in self.engine.events))

    def test_docker_missing_or_malformed_api_capability_fails_closed(self):
        for version in (None, [], {}, {"ClientAPIVersion": "1.56"},
                        {"ClientAPIVersion": "1.56", "ServerAPIVersion": None},
                        {"ClientAPIVersion": 1.56, "ServerAPIVersion": "1.56"},
                        {"ClientAPIVersion": "1.56", "ServerAPIVersion": "bad-version"}):
            with self.subTest(version=version):
                self.engine.version = version
                self.engine.events.clear()
                with self.assertRaisesRegex(preparation.PreparationError, "valid client and server API"):
                    self.prepare()
                self.assertEqual(self.mutation_events(), [])
                self.assertFalse(any(command[1] == "compose" for command, _ in self.engine.events))

    def test_caller_override_is_explicit_and_missing_unknown_default_fails(self):
        custom = "docker.io/example/custom:mutable"
        self.engine.services["api"]["image"] = custom
        with self.assertRaisesRegex(preparation.PreparationError, "needs a valid reviewed digest"):
            self.prepare()
        self.assertEqual(self.mutation_events(), [])
        self.prepare(caller_owned={custom})
        self.engine.events.clear()
        self.prepare(caller_owned={custom})
        self.assertEqual(self.mutation_events(), [])
        exact = REGISTRY + "@sha256:" + "e" * 64
        self.engine.services["api"]["image"] = exact
        output = self.prepare(caller_owned={exact})
        self.assertEqual(json.loads(output.read_text())["services"]["api"]["image"], exact)

    def test_known_caller_override_uses_reviewed_digest(self):
        output = self.prepare(caller_owned={REGISTRY})
        self.assertEqual(json.loads(output.read_text())["services"]["api"]["image"], REGISTRY + "@" + DIGEST)

    def test_pull_build_and_wrong_arch_failures_have_no_completion(self):
        report = self.root / "report"
        report.write_text("stale")
        self.engine.fail_pull = True
        with self.assertRaises(preparation.PreparationError):
            self.prepare(overlay_output=report)
        self.assertFalse(report.exists())
        self.assertEqual(list(self.options["state_dir"].glob("*.json")), [])
        self.engine.fail_pull = False
        self.engine.fail_build = True
        with self.assertRaises(preparation.PreparationError):
            self.prepare()
        self.assertEqual(list(self.options["state_dir"].glob("*.json")), [])
        self.engine.fail_build = False
        self.engine.images = {}
        self.engine.pull_platform = "linux/amd64"
        with self.assertRaises(preparation.PreparationError):
            self.prepare()
        self.assertEqual(list(self.options["state_dir"].glob("*.json")), [])
        self.engine.pull_platform = None
        self.assertTrue(self.prepare().exists())

    def test_executor_is_prepared_outside_compose_and_omitted_on_podman(self):
        executor = "docker.io/local/executor:identity"
        self.engine.build_outputs["executor-build"] = executor
        self.prepare(executor=executor, locals={LOCAL: "local-build", executor: "executor-build"})
        self.assertIn(["fake-make", "executor-build", "DOCKER_DEFAULT_PLATFORM=linux/arm64"], self.mutation_events())
        self.engine.events.clear()
        output = self.prepare(container_bin="fake-podman")
        self.assertNotIn("executor", output.read_text())
        self.assertFalse(any(command[-1] == executor for command, _ in self.engine.events))
        with self.assertRaises(preparation.PreparationError):
            self.prepare(container_bin="fake-podman", executor=executor)

    def test_parallel_preparation_serializes_and_rechecks_presence(self):
        self.engine.first_pull = threading.Event()
        self.engine.release_pull = threading.Event()
        outputs, errors = [], []
        def worker():
            try:
                outputs.append(self.prepare())
            except Exception as exc:
                errors.append(exc)
        first, second = threading.Thread(target=worker), threading.Thread(target=worker)
        first.start()
        self.assertTrue(self.engine.first_pull.wait(5))
        second.start()
        self.engine.release_pull.set()
        first.join(5)
        second.join(5)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(len(self.mutation_events()), 2)

    def test_startup_runs_with_selected_overlay_while_store_lock_is_held(self):
        self.engine.first_start = threading.Event()
        self.engine.release_start = threading.Event()
        outputs, errors = [], []
        def worker(target):
            try:
                outputs.append(self.prepare(start_target=target))
            except Exception as exc:
                errors.append(exc)
        first = threading.Thread(target=worker, args=("_start-lite",))
        first.start()
        self.assertTrue(self.engine.first_start.wait(5))
        before = len([command for command, _ in self.engine.events if command[1] == "compose"])
        os.environ["DOCKER_CONTEXT"] = "another-alias-for-the-same-daemon"
        self.engine.info_event = threading.Event()
        second = threading.Thread(target=worker, args=("_start-full",))
        second.start()
        self.assertTrue(self.engine.info_event.wait(5))
        # An endpoint/context alias cannot split the same store startup lock.
        after = len([command for command, _ in self.engine.events if command[1] == "compose"])
        self.assertEqual(before, after)
        self.engine.release_start.set()
        first.join(5)
        second.join(5)
        self.assertEqual(errors, [])
        starts = [command for command, _ in self.engine.events if command[0] == "fake-make" and command[1].startswith("_start-")]
        self.assertEqual(len(starts), 2)
        self.assertTrue(all(command[2] == "PREPARED_IMAGES_FILE=" + str(outputs[0]) for command in starts))
        self.assertEqual(outputs[0], outputs[1])
        with self.assertRaises(preparation.PreparationError):
            self.prepare(start_target="arbitrary-target")

    def test_native_store_identity_ignores_connection_aliases(self):
        for engine in ("fake-docker", "fake-podman"):
            with self.subTest(engine=engine):
                with patch.dict(os.environ, {"DOCKER_CONTEXT": "alias-one", "DOCKER_HOST": "unix:///one", "CONTAINER_CONNECTION": "one", "CONTAINER_HOST": "ssh://one"}):
                    first = preparation.engine_selection(engine)[0]
                with patch.dict(os.environ, {"DOCKER_CONTEXT": "alias-two", "DOCKER_HOST": "unix:///two", "CONTAINER_CONNECTION": "two", "CONTAINER_HOST": "ssh://two"}):
                    second = preparation.engine_selection(engine)[0]
                self.assertEqual(first, second)
                previous = self.engine.store
                self.engine.store = "a-different-native-store"
                self.assertNotEqual(first, preparation.engine_selection(engine)[0])
                self.engine.store = previous

    def test_local_build_cannot_be_rerouted_or_change_platform_identity(self):
        with self.assertRaisesRegex(preparation.PreparationError, "both a local"):
            self.prepare(caller_owned={LOCAL})
        self.assertEqual(self.mutation_events(), [])
        self.engine.services["local"]["platform"] = "linux/amd64"
        with self.assertRaisesRegex(preparation.PreparationError, "build identity platform"):
            self.prepare()
        self.assertEqual(self.mutation_events(), [])
        with patch.dict(os.environ, {"DOCKER_DEFAULT_PLATFORM": "linux/amd64"}):
            output = self.prepare()
        self.assertTrue(all(service["platform"] == "linux/amd64" for service in json.loads(output.read_text())["services"].values()))

    def test_controlled_build_pull_and_start_messages_are_streamed_to_stderr(self):
        self.prepare(start_target="_start-full")
        for command, captured, destination in self.engine.streams:
            if command[0] == "fake-make" or command[1] == "pull":
                self.assertIsNone(captured)
                self.assertIsNotNone(destination)
                self.assertTrue(hasattr(destination, "write"))
            else:
                self.assertTrue(captured)
                self.assertIsNone(destination)

    def configured(self):
        destination = self.root / "generated-configuration"
        inventory = self.root / "tools/components/ownership.json"
        inventory.parent.mkdir(parents=True)
        inventory.write_text(json.dumps({"compatibility": {"onyx": "v4.8.8", "onyx_source": "a" * 40}}))
        def download(url):
            if url.endswith("env.template"):
                return b"IMAGE_TAG=latest\nUSER_AUTH_SECRET=\n"
            return b"fixture generated artifact\n"
        configuration.prepare(self.root, destination, destination / "deployment/.env", "v4.8.8", "v4.8.8", "0.4.9", "20261102.1", "fake-docker", downloader=download, validator=lambda *args: None)
        os.environ.update(ONYX_CONFIG_REF="v4.8.8", ONYX_IMAGE_TAG="v4.8.8", CODE_INTERPRETER_IMAGE_TAG="0.4.9")
        return destination

    def test_configuration_generation_matches_before_any_artifact_action(self):
        destination = self.configured()
        stamp = destination / configuration.STAMP
        with patch.object(preparation.Path, "cwd", return_value=self.root):
            self.prepare(configuration_stamp=stamp)
            self.engine.events.clear()
            with patch.dict(os.environ, {"ONYX_CONFIG_REF": "another-selection"}):
                with self.assertRaisesRegex(preparation.PreparationError, "configuration changed"):
                    self.prepare(configuration_stamp=stamp)
        self.assertEqual(self.mutation_events(), [])
        self.assertFalse(any(command[1] == "compose" for command, _ in self.engine.events))

    def test_configuration_stamp_generation_changes_overlay_identity(self):
        destination = self.configured()
        stamp = destination / configuration.STAMP
        with patch.object(preparation.Path, "cwd", return_value=self.root):
            first = self.prepare(configuration_stamp=stamp)
            configuration.prepare(self.root, destination, destination / "deployment/.env", "v4.8.8", "v4.8.8", "0.4.9", "20261102.1", "fake-docker", force=True, downloader=lambda url: b"fixture changed generation\n", validator=lambda *args: None)
            second = self.prepare(configuration_stamp=stamp)
        self.assertNotEqual(first, second)

    def test_configuration_writer_waits_through_startup_shared_lock(self):
        destination = self.configured()
        stamp = destination / configuration.STAMP
        self.engine.first_start = threading.Event()
        self.engine.release_start = threading.Event()
        writer_attempted, writer_acquired = threading.Event(), threading.Event()
        errors = []
        def start():
            try:
                self.prepare(configuration_stamp=stamp, start_target="_start-full")
            except Exception as exc:
                errors.append(exc)
        def writer():
            writer_attempted.set()
            with configuration.preparation_lock(destination):
                writer_acquired.set()
        with patch.object(preparation.Path, "cwd", return_value=self.root):
            startup = threading.Thread(target=start)
            startup.start()
            self.assertTrue(self.engine.first_start.wait(5))
            writing = threading.Thread(target=writer)
            writing.start()
            self.assertTrue(writer_attempted.wait(5))
            self.assertFalse(writer_acquired.wait(0.02))
            self.engine.release_start.set()
            startup.join(5)
            writing.join(5)
        self.assertFalse(startup.is_alive() or writing.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(writer_acquired.is_set())

    def test_start_environment_uses_prepared_application_references_and_platform(self):
        self.engine.services["api_server"] = self.engine.services.pop("api")
        self.engine.services["web_server"] = {"image": REGISTRY}
        self.engine.active = ["api_server", "web_server", "local"]
        self.prepare(start_target="_start-full")
        command, environment = next((command, environment) for command, environment in self.engine.events if command[0] == "fake-make" and command[1] == "_start-full")
        self.assertEqual(environment["ONYX_BACKEND_IMAGE"], REGISTRY + "@" + DIGEST)
        self.assertEqual(environment["ONYX_WEB_SERVER_IMAGE"], REGISTRY + "@" + DIGEST)
        self.assertEqual(environment["PRIVATE_ONYX_PREPARED_PLATFORM"], "linux/arm64")
        self.assertIn("ONYX_BACKEND_IMAGE=" + REGISTRY + "@" + DIGEST, command)
        self.assertIn("ONYX_WEB_SERVER_IMAGE=" + REGISTRY + "@" + DIGEST, command)
        self.assertIn("PRIVATE_ONYX_PREPARED_PLATFORM=linux/arm64", command)

    def test_backend_service_platform_is_forwarded_separately_from_native_build_platform(self):
        self.engine.services["api_server"] = self.engine.services.pop("api")
        self.engine.services["api_server"]["platform"] = "linux/amd64"
        self.engine.active = ["api_server", "local"]
        self.prepare(start_target="_start-full")
        command, environment = next((command, environment) for command, environment in self.engine.events if command[0] == "fake-make" and command[1] == "_start-full")
        self.assertEqual(environment["PRIVATE_ONYX_PREPARED_BACKEND_PLATFORM"], "linux/amd64")
        self.assertIn("PRIVATE_ONYX_PREPARED_BACKEND_PLATFORM=linux/amd64", command)
        self.assertEqual(environment["PRIVATE_ONYX_PREPARED_PLATFORM"], "linux/arm64")
        self.assertEqual(self.engine.images[REGISTRY + "@" + DIGEST], "linux/amd64")

    def test_builder_platform_assignment_outranks_inherited_makeflags(self):
        with patch.dict(os.environ, {"DOCKER_DEFAULT_PLATFORM": "linux/arm64/v8", "MAKEFLAGS": " -- DOCKER_DEFAULT_PLATFORM=linux/arm64/v8"}):
            self.prepare()
        command, environment = next((command, environment) for command, environment in self.engine.events if command[0] == "fake-make")
        self.assertIn("DOCKER_DEFAULT_PLATFORM=linux/arm64", command)
        self.assertEqual(environment["DOCKER_DEFAULT_PLATFORM"], "linux/arm64")

    def test_podman_rejects_unverifiable_variants_before_artifact_actions(self):
        with patch.dict(os.environ, {"DOCKER_DEFAULT_PLATFORM": "linux/arm64/v9"}):
            with self.assertRaisesRegex(preparation.PreparationError, "variant cannot be verified"):
                self.prepare(container_bin="fake-podman")
        self.assertEqual(self.mutation_events(), [])
        self.engine.arch, self.engine.variant = "arm", "v7"
        with self.assertRaisesRegex(preparation.PreparationError, "variant cannot be verified"):
            self.prepare(container_bin="fake-podman")
        self.assertEqual(self.mutation_events(), [])
        self.engine.arch, self.engine.variant = "aarch64", "v8"
        output = self.prepare(container_bin="fake-podman")
        self.assertEqual(json.loads(output.read_text())["services"]["api"]["platform"], "linux/arm64")
        self.engine.services["api"]["platform"] = "linux/arm/v7"
        self.engine.events.clear()
        with self.assertRaisesRegex(preparation.PreparationError, "variant cannot be verified"):
            self.prepare(container_bin="fake-podman")
        self.assertEqual(self.mutation_events(), [])

    def test_effective_selection_report_contains_refs_platform_and_kind_only(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            preparation.prepare(**self.options)
        report = output.getvalue()
        self.assertIn("Prepared reviewed registry: " + REGISTRY + "@" + DIGEST + " (linux/arm64)", report)
        self.assertIn("Prepared local build: " + LOCAL + " (linux/arm64)", report)
        self.assertNotIn("fixture-secret-canary", report)
        self.assertNotIn("/fixture/store", report)

    def test_state_directory_is_private_and_unsafe_existing_state_is_rejected(self):
        state = self.options["state_dir"]
        self.prepare()
        self.assertEqual(state.stat().st_mode & 0o777, 0o700)
        state.chmod(0o755)
        self.engine.events.clear()
        with self.assertRaisesRegex(preparation.PreparationError, "owner-only permissions"):
            self.prepare()
        self.assertEqual(state.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.mutation_events(), [])
        state.chmod(0o700)
        with patch.object(preparation.os, "getuid", return_value=state.stat().st_uid + 1):
            with self.assertRaisesRegex(preparation.PreparationError, "owned by the current user"):
                self.prepare()

    def test_state_lock_overlay_and_report_symlinks_are_rejected(self):
        target = self.root / "untouched"
        target.write_text("fixture untouched target")
        linked_state = self.root / "linked-state"
        linked_state.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(preparation.PreparationError, "symbolic links"):
            self.prepare(state_dir=linked_state)
        state = self.options["state_dir"]
        state.mkdir(mode=0o700)
        key = preparation.engine_selection(self.options["container_bin"])[0]
        lock = state / (key + ".lock")
        lock.symlink_to(target)
        with self.assertRaisesRegex(preparation.PreparationError, "symbolic links"):
            self.prepare()
        lock.unlink()
        overlay = self.prepare()
        overlay.unlink()
        overlay.symlink_to(target)
        with self.assertRaisesRegex(preparation.PreparationError, "symbolic links"):
            self.prepare()
        overlay.unlink()
        report = self.root / "report"
        report.symlink_to(target)
        with self.assertRaisesRegex(preparation.PreparationError, "symbolic links"):
            self.prepare(overlay_output=report)
        self.assertEqual(target.read_text(), "fixture untouched target")

    def test_invalid_image_urls_and_credentials_are_never_reported(self):
        for image in ("https://user:fixture-secret@registry.invalid/repo:tag", "user:fixture-secret@registry.invalid/repo:tag", "repo:tag@fixture-secret", "fixture-secret image"):
            with self.subTest(image=image):
                self.engine.services["api"]["image"] = image
                self.engine.events.clear()
                report = io.StringIO()
                with contextlib.redirect_stderr(report):
                    with self.assertRaises(preparation.PreparationError) as error:
                        preparation.prepare(**self.options)
                self.assertNotIn("fixture-secret", str(error.exception))
                self.assertNotIn("fixture-secret", report.getvalue())
                self.assertEqual(self.mutation_events(), [])

    def test_two_checkouts_share_store_lock_through_startup(self):
        second_checkout = self.root / "second-checkout"
        second_checkout.mkdir()
        second_config = second_checkout / "public-compose.yml"
        second_config.write_text("services: {}\n")
        self.engine.first_start = threading.Event()
        self.engine.release_start = threading.Event()
        outputs, errors = [], []
        def worker():
            try:
                outputs.append(self.prepare(start_target="_start-full"))
            except Exception as exc:
                errors.append(exc)
        first = threading.Thread(target=worker)
        first.start()
        self.assertTrue(self.engine.first_start.wait(5))
        os.environ["COMPOSE_FILE"] = str(second_config)
        self.engine.info_event = threading.Event()
        before = len([command for command, _ in self.engine.events if command[1] == "compose"])
        second = threading.Thread(target=worker)
        second.start()
        self.assertTrue(self.engine.info_event.wait(5))
        self.assertEqual(before, len([command for command, _ in self.engine.events if command[1] == "compose"]))
        self.engine.release_start.set()
        first.join(5)
        second.join(5)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(errors, [])
        self.assertNotEqual(outputs[0], outputs[1])
        self.assertEqual(len(self.mutation_events()), 4)  # One pull/build, two startup callbacks.

    def test_revision_and_public_configuration_change_overlay_identity(self):
        first = self.prepare()
        second = self.prepare(pins={"OS_SECURITY_UPDATE_REVISION": "20261102.2"})
        self.assertNotEqual(first, second)
        self.config.write_text("services: {}\n# changed configuration\n")
        self.assertNotEqual(self.prepare(), first)


if __name__ == "__main__":
    unittest.main()
