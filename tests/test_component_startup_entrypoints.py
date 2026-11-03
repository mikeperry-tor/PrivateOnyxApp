"""Exercise real Make startup ordering against a disposable fake image store."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

if sys.platform not in ("linux", "darwin"):
    raise unittest.SkipTest("startup fixtures require supported Linux/macOS process and flock contracts")

ROOT = Path(__file__).resolve().parents[1]

from tools.components.manage import next_revision, read_pins, write_pins

CONTROL = r'''import json, os, sys
from pathlib import Path
from tools.components import configuration
root = Path.cwd()
def event(kind, **values):
    with Path(os.environ["FIXTURE_EVENTS"]).open("a") as stream:
        stream.write(json.dumps(dict(kind=kind, **values)) + "\n")
if sys.argv[1] == "configure":
    event("configuration", revision=os.environ["OS_SECURITY_UPDATE_REVISION"], nginx=os.environ["NGINX_IMAGE"])
    def download(url):
        return b"IMAGE_TAG=latest\nUSER_AUTH_SECRET=fixture-only\n" if url.endswith("env.template") else b"public synthetic artifact\n"
    configuration.prepare(root, root / "onyx/onyx_data", Path(os.environ["ONYX_ENV_FILE"]),
        os.environ["ONYX_CONFIG_REF"], os.environ["ONYX_IMAGE_TAG"], os.environ["CODE_INTERPRETER_IMAGE_TAG"],
        os.environ["OS_SECURITY_UPDATE_REVISION"], os.environ["CONTAINER_BIN"],
        lite=os.environ.get("ONYX_INSTALL_ARGS") == "--lite", downloader=download, validator=lambda *args: None)
elif sys.argv[1] in ("bump", "sync", "locks"):
    from tools.components.manage import next_revision, read_pins, write_pins
    pins_path = root / "stack.versions.env"
    pins = read_pins(pins_path)
    if sys.argv[1] == "bump":
        current = pins["OS_SECURITY_UPDATE_REVISION"]
        write_pins(pins_path, {"OS_SECURITY_UPDATE_REVISION": next_revision(current, current.split(".")[0])})
    elif sys.argv[1] == "sync":
        write_pins(pins_path, {"NGINX_IMAGE": "docker.io/library/nginx:fixture-next@sha256:" + "b" * 64})
    else:
        with (root / "searxng/requirements.txt").open("a") as stream:
            stream.write("\n# fixture refreshed lock\n")
    event(sys.argv[1])
else:
    event("operator-helper", target=sys.argv[2])
'''

ENGINE = r'''import fcntl, json, os, sys
from pathlib import Path
state_path = Path(os.environ["FIXTURE_STORE"])
store_lock = state_path.with_suffix(".lock").open("a+")
fcntl.flock(store_lock, fcntl.LOCK_EX)
state = json.loads(state_path.read_text())
arguments = sys.argv[1:]
podman = "podman" in Path(sys.argv[0]).name

def normal(image):
    if image.count("/") == 0: return "docker.io/library/" + image
    if "." not in image.split("/")[0] and ":" not in image.split("/")[0]: return "docker.io/" + image
    return image

def event(kind, **values):
    with Path(os.environ["FIXTURE_EVENTS"]).open("a") as stream:
        stream.write(json.dumps(dict(kind=kind, **values)) + "\n")

def save(): state_path.write_text(json.dumps(state))
def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)

def model(*, native_guard=False):
    selected = {
        "api_server": os.environ["ONYX_BACKEND_IMAGE"], "web_server": os.environ["ONYX_WEB_SERVER_IMAGE"],
        "nginx": os.environ["NGINX_IMAGE"], "netns-holder": os.environ["NETNS_HOLDER_IMAGE"],
        "myst-client": os.environ["MYST_IMAGE"], "teep": os.environ["TEEP_IMAGE"],
        "searxng-core": os.environ["SEARXNG_WRAPPER_IMAGE"], "obscura": os.environ["OBSCURA_IMAGE"],
        "browser-policy-proxy": os.environ["PYTHON_SLIM_IMAGE"], "host-webui-proxy": os.environ["SOCAT_IMAGE"],
        "relational_db": "docker.io/library/postgres:15.2-alpine",
    }
    if not podman: selected["code-interpreter"] = os.environ["CODE_INTERPRETER_IMAGE"]
    full = "docker-compose.full.yml" in os.environ["COMPOSE_FILE"]
    if full:
        selected.update(background=os.environ["ONYX_BACKEND_IMAGE"], cache="docker.io/library/redis:7.4-alpine",
            opensearch="docker.io/opensearchproject/opensearch:3.6.0", minio=os.environ["MINIO_IMAGE"],
            **{"local-embedding-shim": os.environ["PYTHON_ALPINE_IMAGE"]})
    profiles = os.environ.get("COMPOSE_PROFILES", "").split(",")
    if "tailscale" in profiles: selected["tailscale-funnel"] = os.environ["TAILSCALE_IMAGE"]
    if "docker-compose.tor.yml" in os.environ["COMPOSE_FILE"]: selected["tor"] = os.environ["TOR_IMAGE"]
    services = {name: {"image": image} for name, image in selected.items()}
    backend_platform = os.environ.get("FIXTURE_BACKEND_PLATFORM")
    if backend_platform:
        for name in ("api_server", "background"):
            if name in services: services[name]["platform"] = backend_platform
    active = sorted(services)
    services["disabled-optional"] = {"image": "fixture.invalid/disabled:must-not-fetch", "profiles": ["disabled"]}
    sources = os.environ["COMPOSE_FILE"].split(":")
    prepared = os.environ.get("PREPARED_IMAGES_FILE")
    if native_guard and prepared and prepared not in sources: sources.append(prepared)
    for source in sources:
        if source.endswith(".json"):
            overlay = json.loads(Path(source).read_text())
            for name, value in overlay["services"].items(): services[name].update(value)
    return services, active

if arguments[0] == "info":
    event("info")
    if podman: print(json.dumps({"host": {"hostname": "fixture-store", "os": "linux", "arch": "aarch64", "variant": "v8"}, "store": {"graphRoot": "/fixture/store"}}))
    else: print(json.dumps({"ID": "fixture-store", "DockerRootDir": "/fixture/store", "OSType": "linux", "Architecture": "aarch64"}))
elif arguments[0] == "version":
    if podman: fail("Podman must not use the Docker API capability query")
    expected = '{"ClientAPIVersion":{{json .Client.APIVersion}},"ServerAPIVersion":{{json .Server.APIVersion}}}'
    if arguments != ["version", "--format", expected]: fail("version query must project only client/server API versions")
    event("version")
    print(json.dumps({"ClientAPIVersion": "1.56", "ServerAPIVersion": "1.56"}))
elif arguments[:2] == ["image", "inspect"]:
    if podman and "--platform" in arguments: fail("Podman inspect does not support the Docker platform selector")
    if not podman and "--format" in arguments and "--platform" not in arguments: fail("platform-aware Docker inspection omitted its selected platform")
    selected = arguments[arguments.index("--platform") + 1] if "--platform" in arguments else None
    image = normal(arguments[-1]); event("inspect", image=image, platform=selected)
    if image not in state["images"] or (selected and state["images"][image] != selected): sys.exit(1)
    parts = state["images"][image].split("/")
    value = {"Os": parts[0], "Architecture": parts[1]}
    if len(parts) == 3 and not podman: value["Variant"] = parts[2]
    print(json.dumps([value]))
elif arguments[0] == "pull":
    image = normal(arguments[-1]); event("pull", image=image)
    local_variables = ("MYST_IMAGE", "TEEP_IMAGE", "SEARXNG_WRAPPER_IMAGE", "OBSCURA_IMAGE", "TOR_IMAGE", "TAILSCALE_IMAGE", "CODE_INTERPRETER_IMAGE", "PYTHON_EXECUTOR_IMAGE")
    if image in {normal(os.environ[name]) for name in local_variables if name in os.environ}: fail("local builder image was sent to a registry")
    if state.get("fail_pull") and (not state.get("fail_pull_image") or state["fail_pull_image"] == image): fail("fixture required pull failed")
    selected = arguments[arguments.index("--platform") + 1] if "--platform" in arguments else "linux/arm64"
    state["images"][image] = selected; save()
elif arguments[0] == "build":
    image = normal(arguments[arguments.index("--tag") + 1])
    selected = next((part.split("=", 1)[1] for part in arguments if part.startswith("--platform=")), "linux/arm64")
    event("build", image=image, platform=selected, revision=os.environ["OS_SECURITY_UPDATE_REVISION"])
    if state.get("fail_build"): fail("fixture selected build failed")
    state["images"][image] = selected; save()
elif arguments[0] == "run":
    # The native tokenizer consumes explicit image/platform arguments, so use
    # the exported preparation projection only for its all-images-ready guard.
    # Compose itself must consume precisely the actual COMPOSE_FILE inputs.
    services, active = model(native_guard=True)
    for name in active:
        selected = services[name].get("platform", "linux/arm64")
        if state["images"].get(normal(services[name]["image"])) != selected: fail("native run happened before complete image preparation")
    image = normal(arguments[arguments.index("--entrypoint") + 2])
    selected = arguments[arguments.index("--platform") + 1] if "--platform" in arguments else "linux/arm64"
    if state["images"].get(image) != selected: fail("native tokenizer consumed an unprepared image/platform")
    event("run", image=image, platform=selected, pull_never="--pull" in arguments and arguments[arguments.index("--pull") + 1] == "never")
    print('{"model":{}}')
elif arguments[0] == "compose":
    services, active = model()
    if "config" in arguments:
        if "--services" in arguments: print("\n".join(active))
        else:
            event("model", images=[normal(services[name]["image"]) for name in active], env_files=[arguments[i + 1] for i, arg in enumerate(arguments) if arg == "--env-file"])
            print(json.dumps({"services": services, "x-private-model": "fixture-secret-canary"}))
    elif any(action in arguments for action in ("up", "create", "run")):
        for name in active:
            selected = services[name].get("platform", "linux/arm64")
            if state["images"].get(normal(services[name]["image"])) != selected: fail("creation happened before the complete active image selection was prepared")
        if not podman and normal(os.environ["PYTHON_EXECUTOR_IMAGE"]) not in state["images"]: fail("Docker executor was not prepared before creation")
        if "--pull" not in arguments or arguments[arguments.index("--pull") + 1] != "never": fail("creation permits implicit image pulls")
        if "run" not in arguments and "--no-build" not in arguments: fail("creation permits implicit image builds")
        event("create", action="create" if "create" in arguments else "up", images=[normal(services[name]["image"]) for name in active])
    elif "exec" in arguments:
        event("embedding-ready")
    elif "down" in arguments: event("down")
    else: fail("unexpected fixture Compose operation")
else: fail("unexpected fixture engine operation")
'''

PYTHON_WRAPPER = r'''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args[:1] == ["podman/startup_health.py"] and args[1:2] in (["configure"], ["assert-healthy"]):
    with Path(os.environ["FIXTURE_EVENTS"]).open("a") as stream:
        stream.write(json.dumps({"kind": "health-helper", "target": args[1]}) + "\n")
else: os.execv(sys.executable, [sys.executable, *args])
'''

MAKE_WRAPPER = r'''import os, sys
os.execvp("make", ["make", "--no-print-directory", "-s", "-f", "Makefile", "-f", "fixture.mk", *sys.argv[1:]])
'''

STUB_TARGETS = " ".join((
    "wrapper-config-preflight", "check-container-health-capability", "claim-shared-data-engine",
    "prepare-lite-host-data", "prepare-full-host-data", "prepare-podman-postgres-data",
    "prepare-podman-opensearch-data", "ensure-myst-funded", "tor-config-ready",
    "embedserv-start-if-installed", "podman-doc-server-start", "podman-doc-server-stop-if-started",
    "embedserv-stop-after-custom-ready", "release-myst-data-ownership", "release-shared-data-engine",
    "embedserv-stop-if-started",
))
OVERRIDES = (STUB_TARGETS + ":\n\t@python3 fixture_control.py event $@\n\n"
             "ensure-onyx-config:\n\t@python3 fixture_control.py configure\n")


class ComponentStartupEntrypointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_temporary = tempfile.TemporaryDirectory(prefix="component-startup-public-")
        cls.base = Path(cls.base_temporary.name)
        names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
        roots = ("browser/", "executor/", "searxng/", "myst/", "teep/", "tailscale/", "tor/", "embedserv/", "tools/components/", "onyx/code-interpreter/", "compose_overlays/")
        files = {"Makefile", "docker-compose.yaml", "stack.versions.env", ".env.wrapper.example", ".dockerignore", ".containerignore", "onyx/bootstrap_tokenizer_cache.py", "tools/components/preparation.py"}
        for name in sorted(set(names) | files):
            if name in files or name.startswith(roots):
                target = cls.base / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, target)
        (cls.base / "fixture.mk").write_text(OVERRIDES)
        (cls.base / "fixture_control.py").write_text(CONTROL)

    @classmethod
    def tearDownClass(cls):
        cls.base_temporary.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="component-startup-entrypoint-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        shutil.copytree(self.base, self.root)
        self.store = self.root.parent / "store.json"
        self.store.write_text(json.dumps({"images": {"docker.io/library/redis:7.4-alpine": "linux/arm64", "docker.io/onyxdotapp/onyx-backend:v4.8.8": "linux/arm64"}}))
        self.events_file = self.root.parent / "events.jsonl"
        self.events_file.touch()
        self.bin = self.root / "fixture-bin"
        self.bin.mkdir()
        for name, source in (("fake-docker", ENGINE), ("fake-podman", ENGINE), ("python3", PYTHON_WRAPPER), ("fixture-make", MAKE_WRAPPER)):
            path = self.bin / name
            path.write_text("#!" + sys.executable + "\n" + source)
            path.chmod(0o755)
        self.environment = {name: value for name, value in os.environ.items() if name in ("PATH", "HOME", "TMPDIR", "SYSTEMROOT")}
        self.environment.update(PATH=str(self.bin) + os.pathsep + self.environment["PATH"], FIXTURE_STORE=str(self.store), FIXTURE_EVENTS=str(self.events_file))

    def make(self, goal="up-lite", *, engine="fake-docker", assignments=(), jobs=4, env=None):
        command = ["make", "--no-print-directory", "-s", "-j" + str(jobs), "-f", "Makefile", "-f", "fixture.mk", goal,
                   "MAKE=" + str(self.bin / "fixture-make"), "ENV_FILE=.env.wrapper.example", "HOST_OS=Darwin",
                   "PRIVATE_ONYX_DOCKER_ENGINE_MODE=rootful", "PRIVATE_ONYX_DOCKER_GATEWAY_MODE=isolated", "DOCKER_SOCK_PATH=/fixture/engine.sock",
                   "CONTAINER_BIN=" + str(self.bin / engine), "COMPONENT_PREPARATION_STATE_DIR=" + str(self.root.parent / "prepared"), *assignments]
        return subprocess.run(command, cwd=self.root, env=self.environment | (env or {}), capture_output=True, text=True, check=False, timeout=180)

    def events(self):
        return [json.loads(line) for line in self.events_file.read_text().splitlines()]

    def reset_events(self):
        self.events_file.write_text("")

    def successful(self, result):
        diagnostic = "\n".join(line for line in (result.stdout + result.stderr).splitlines() if "overriding commands for target" not in line and "ignoring old commands for target" not in line)
        self.assertEqual(result.returncode, 0, diagnostic)
        self.assertNotIn("fixture-secret-canary", result.stdout + result.stderr)

    def configure_upgrade_fixture(self):
        with (self.root / "fixture.mk").open("a") as stream:
            stream.write("\nupgrade-onyx:\n\t@python3 fixture_control.py configure\n")
            for target, action in (("component-refresh-revision", "bump"), ("component-sync", "sync"), ("upgrade-python-deps", "locks")):
                stream.write(f"\n{target}:\n\t@python3 fixture_control.py {action}\n")

    def test_upgrade_reparses_published_generation_synchronized_pins_and_locks(self):
        self.configure_upgrade_fixture()
        self.successful(self.make())
        for origin in ("default", "environment", "command-line"):
            with self.subTest(origin=origin):
                self.reset_events()
                overrides = {"MYST_IMAGE": "fixture.invalid/myst:explicit", "OBSCURA_IMAGE": "fixture.invalid/obscura:explicit", "NGINX_IMAGE": "fixture.invalid/nginx:explicit", "OS_SECURITY_UPDATE_REVISION": "19990101.1"}
                assignments = tuple(f"{name}={value}" for name, value in overrides.items()) if origin == "command-line" else ()
                environment = overrides if origin == "environment" else {}
                self.successful(self.make("upgrade", assignments=assignments, env=environment))
                pins = read_pins(self.root / "stack.versions.env")
                events = self.events()
                builds = [event for event in events if event["kind"] == "build"]
                self.assertEqual(len(builds), 8)
                self.assertEqual({event["revision"] for event in builds}, {pins["OS_SECURITY_UPDATE_REVISION"]})
                configuration = next(event for event in events if event["kind"] == "configuration")
                self.assertEqual(configuration["revision"], pins["OS_SECURITY_UPDATE_REVISION"])
                self.assertEqual(configuration["nginx"], pins["NGINX_IMAGE"] if origin == "default" else overrides["NGINX_IMAGE"])
                self.assertEqual([event["kind"] for event in events[:3]], ["bump", "sync", "locks"])
                self.assertFalse(any(event["kind"] in ("create", "run", "operator-helper") for event in events))
                first_model = next(index for index, event in enumerate(events) if event["kind"] == "model")
                self.assertTrue(all(event["kind"] != "build" for event in events[first_model:]))
                self.assertEqual(events[first_model]["env_files"], ["stack.versions.env", ".env.wrapper.example"])
                inventory = json.loads((self.root / "tools/components/ownership.json").read_text())
                stored = json.loads(self.store.read_text())["images"]
                for tag in ("docker.io/onyxdotapp/onyx-backend:" + pins["ONYX_IMAGE_TAG"], "docker.io/onyxdotapp/onyx-web-server:" + pins["ONYX_IMAGE_TAG"], "docker.io/opensearchproject/opensearch:3.6.0"):
                    self.assertIn(tag + "@" + inventory["registry_digests"][tag], stored)
                self.assertIn(pins["PYTHON_ALPINE_IMAGE"], stored)
                if origin != "default":
                    self.assertTrue(any(event["image"] == overrides["MYST_IMAGE"] for event in builds))
                    self.assertTrue(any(event["image"] == overrides["OBSCURA_IMAGE"] for event in builds))
                else:
                    self.reset_events()
                    self.successful(self.make("up-full"))
                    self.assertFalse(any(event["kind"] in ("build", "pull") for event in self.events()))

    def test_podman_upgrade_acquisition_failure_is_visible_and_retry_omits_docker_artifacts(self):
        self.configure_upgrade_fixture()
        inventory = json.loads((self.root / "tools/components/ownership.json").read_text())
        backend = "docker.io/onyxdotapp/onyx-backend:v4.8.8"
        state = json.loads(self.store.read_text())
        state.update(fail_pull=True, fail_pull_image=backend + "@" + inventory["registry_digests"][backend])
        self.store.write_text(json.dumps(state))
        result = self.make("upgrade", engine="fake-podman")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fixture required pull failed", result.stdout + result.stderr)
        self.assertNotIn("Upgrade artifacts are ready", result.stdout)
        self.assertFalse(any(event["kind"] in ("create", "run", "operator-helper") for event in self.events()))
        state = json.loads(self.store.read_text())
        state["fail_pull"] = False
        self.store.write_text(json.dumps(state))
        self.reset_events()
        self.successful(self.make("upgrade", engine="fake-podman"))
        events = self.events()
        self.assertEqual(sum(event["kind"] == "build" for event in events), 6)
        self.assertTrue(any(event["kind"] == "pull" and event["image"].startswith(backend + "@") for event in events))
        self.assertFalse(any(event["kind"] in ("create", "run", "operator-helper") for event in events))
        stored = json.loads(self.store.read_text())["images"]
        self.assertFalse(any("code-interpreter" in image or "python-executor" in image for image in stored))

    def test_actual_parallel_make_prepares_before_creation_reuses_and_recovers_after_prune(self):
        self.successful(self.make())
        events = self.events()
        first_model = next(index for index, item in enumerate(events) if item["kind"] == "model")
        first_creation = next(index for index, item in enumerate(events) if item["kind"] == "create")
        self.assertEqual([item["target"] for item in events[:first_model] if item["kind"] == "operator-helper"], ["wrapper-config-preflight", "check-container-health-capability"])
        self.assertTrue(any(item["kind"] == "configuration" for item in events[:first_model]))
        self.assertTrue(any(item["kind"] == "pull" for item in events[:first_creation]))
        self.assertTrue(any(item["kind"] == "build" and "python-executor" in item["image"] for item in events[:first_creation]))
        self.assertFalse(any(item["kind"] in ("pull", "build") for item in events[first_creation:]))
        self.reset_events()
        self.successful(self.make())
        self.assertFalse(any(item["kind"] in ("pull", "build") for item in self.events()))
        state = json.loads(self.store.read_text()); state["images"] = {}; self.store.write_text(json.dumps(state))
        self.reset_events()
        self.successful(self.make())
        self.assertTrue(any(item["kind"] == "pull" for item in self.events()))
        self.assertTrue(any(item["kind"] == "build" for item in self.events()))

    def test_failed_pull_or_build_prevents_all_startup_and_retry_recovers(self):
        pins = read_pins(self.root / "stack.versions.env")
        inventory = json.loads((self.root / "tools/components/ownership.json").read_text())
        old_backend = "docker.io/onyxdotapp/onyx-backend:" + pins["ONYX_IMAGE_TAG"]
        reviewed_backend = old_backend + "@" + inventory["registry_digests"][old_backend]
        for failure in ("fail_pull", "fail_build"):
            with self.subTest(failure=failure):
                state = {"images": {old_backend: "linux/arm64"}, failure: True}
                if failure == "fail_pull":
                    state["fail_pull_image"] = reviewed_backend
                self.store.write_text(json.dumps(state))
                self.reset_events()
                result = self.make()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(item["kind"] in ("create", "run") for item in self.events()))
                remaining = json.loads(self.store.read_text())["images"]
                self.assertIn(old_backend, remaining)
                if failure == "fail_pull":
                    self.assertTrue(any(item["kind"] == "pull" and item["image"] == reviewed_backend for item in self.events()))
                    self.assertNotIn(reviewed_backend, remaining)
                    self.assertIn("fixture required pull failed", result.stdout + result.stderr)
                else:
                    self.assertTrue(any(item["kind"] == "build" for item in self.events()))
                    self.assertIn("fixture selected build failed", result.stdout + result.stderr)
        # Retry from the real partial store, retaining the available older tag.
        state = json.loads(self.store.read_text())
        for failure in ("fail_pull", "fail_pull_image", "fail_build"):
            state.pop(failure, None)
        self.store.write_text(json.dumps(state))
        self.reset_events()
        self.successful(self.make())
        self.assertTrue(any(item["kind"] == "create" for item in self.events()))
        self.assertIn(old_backend, json.loads(self.store.read_text())["images"])

    def test_full_transition_and_backend_platform_reach_native_tokenizer_before_creation(self):
        self.successful(self.make())
        self.reset_events()
        result = self.make("up-full", env={"FIXTURE_BACKEND_PLATFORM": "linux/amd64"})
        self.successful(result)
        events = self.events()
        run_index = next(index for index, item in enumerate(events) if item["kind"] == "run")
        create_index = next(index for index, item in enumerate(events) if item["kind"] == "create")
        tokenizer = events[run_index]
        self.assertIn("@sha256:", tokenizer["image"])
        self.assertEqual(tokenizer["platform"], "linux/amd64")
        self.assertTrue(tokenizer["pull_never"])
        self.assertLess(run_index, create_index)
        preparations = [index for index, item in enumerate(events) if item["kind"] == "operator-helper" and item["target"] == "prepare-full-host-data"]
        self.assertEqual(len(preparations), 1)
        self.assertLess(preparations[0], run_index)
        self.assertTrue(any(item["kind"] == "pull" and "/redis:" in item["image"] and "@sha256:" in item["image"] for item in events[:run_index]))
        self.assertIn("Press Ctrl-C to stop waiting", result.stdout + result.stderr)
        self.assertEqual(sum(item["kind"] == "embedding-ready" for item in events), 1)

    def test_explicit_registry_and_local_output_overrides_remain_authoritative(self):
        result = self.make(assignments=("ONYX_BACKEND_IMAGE=fixture.invalid/backend:operator", "MYST_IMAGE=fixture.invalid/derived:myst-operator"))
        self.successful(result)
        events = self.events()
        self.assertTrue(any(item["kind"] == "pull" and item["image"] == "fixture.invalid/backend:operator" for item in events))
        self.assertTrue(any(item["kind"] == "build" and item["image"] == "fixture.invalid/derived:myst-operator" for item in events))
        self.assertFalse(any(item["kind"] == "pull" and item["image"] == "fixture.invalid/derived:myst-operator" for item in events))

    def test_readme_down_update_up_delivers_new_digest_behind_unchanged_tag(self):
        self.successful(self.make("up-full"))
        initial_events = self.events()
        initial_builds = {item["image"] for item in initial_events if item["kind"] == "build"}
        inventory_path = self.root / "tools/components/ownership.json"
        inventory = json.loads(inventory_path.read_text())
        tag = "docker.io/library/redis:7.4-alpine"
        previous_digest = inventory["registry_digests"][tag]
        self.assertIn(tag + "@" + previous_digest, json.loads(self.store.read_text())["images"])
        self.reset_events()
        self.successful(self.make("down-full"))
        self.assertEqual(sum(item["kind"] == "down" for item in self.events()), 1)
        self.assertFalse(any(item["kind"] in ("pull", "build", "create", "run") for item in self.events()))
        # Simulate the committed selections delivered by git pull, without any
        # network or changes to the fixture's application/Redis tag strings.
        pins_path = self.root / "stack.versions.env"
        pins = read_pins(pins_path)
        old_revision = pins["OS_SECURITY_UPDATE_REVISION"]
        selected_revision = next_revision(old_revision, old_revision.split(".")[0])
        write_pins(pins_path, {"OS_SECURITY_UPDATE_REVISION": selected_revision})
        next_digest = "sha256:" + ("f" if previous_digest != "sha256:" + "f" * 64 else "e") * 64
        inventory["registry_digests"][tag] = next_digest
        inventory_path.write_text(json.dumps(inventory))
        self.reset_events()
        self.successful(self.make("up-full"))
        events = self.events()
        first_run = next(index for index, item in enumerate(events) if item["kind"] == "run")
        self.assertTrue(any(item["kind"] == "pull" and item["image"] == tag + "@" + next_digest for item in events[:first_run]))
        new_builds = {item["image"] for item in events if item["kind"] == "build"}
        self.assertTrue(new_builds)
        self.assertFalse(new_builds.intersection(initial_builds))
        creations = [item for item in events if item["kind"] == "create"]
        self.assertTrue(creations)
        self.assertTrue(all(tag + "@" + next_digest in item["images"] for item in creations))
        self.assertFalse(any(tag + "@" + previous_digest in item["images"] for item in creations))
        self.assertFalse(any(item["kind"] in ("pull", "build") for item in events[first_run:]))

    def test_fixture_exposes_stale_registry_selection_when_startup_overlay_is_removed(self):
        self.successful(self.make("up-full"))
        state = json.loads(self.store.read_text())
        # Populate ordinary tag aliases too: a native engine can successfully
        # start these old bytes when a buggy startup omits its qualified overlay.
        for image, selected_platform in list(state["images"].items()):
            if "@" in image:
                state["images"][image.split("@", 1)[0]] = selected_platform
        self.store.write_text(json.dumps(state))
        makefile = self.root / "Makefile"
        original = makefile.read_text()
        projection = "$(if $(PREPARED_IMAGES_FILE),:$(PREPARED_IMAGES_FILE),)"
        self.assertGreaterEqual(original.count(projection), 2)
        makefile.write_text(original.replace(projection, ""))
        inventory_path = self.root / "tools/components/ownership.json"
        inventory = json.loads(inventory_path.read_text())
        tag = "docker.io/library/redis:7.4-alpine"
        old_digest = inventory["registry_digests"][tag]
        digest = "sha256:" + ("e" if old_digest != "sha256:" + "e" * 64 else "f") * 64
        inventory["registry_digests"][tag] = digest
        inventory_path.write_text(json.dumps(inventory))
        self.reset_events()
        self.successful(self.make("up-full"))
        events = self.events()
        self.assertTrue(any(item["kind"] == "pull" and item["image"] == tag + "@" + digest for item in events))
        creations = [item for item in events if item["kind"] == "create"]
        self.assertTrue(creations)
        # This intentional fault must remain observable. Unconditionally adding
        # PREPARED_IMAGES_FILE inside the fake Compose model would mask the bug.
        self.assertTrue(all(tag in item["images"] for item in creations))
        self.assertFalse(any(tag + "@" + digest in item["images"] for item in creations))

    def test_actual_podman_full_start_prepares_before_tokenizer_and_staged_creation(self):
        result = self.make("up-full", engine="fake-podman")
        self.successful(result)
        events = self.events()
        mutations = [item["image"] for item in events if item["kind"] in ("pull", "build", "inspect")]
        self.assertFalse(any("code-interpreter" in image or "python-executor" in image for image in mutations))
        self.assertTrue(any(item["kind"] == "pull" and "/redis:" in item["image"] and "@sha256:" in item["image"] for item in events))
        tokenizer = next(index for index, item in enumerate(events) if item["kind"] == "run")
        creation = next(index for index, item in enumerate(events) if item["kind"] == "create")
        self.assertLess(tokenizer, creation)
        self.assertTrue(events[tokenizer]["pull_never"])
        self.assertFalse(any(item["kind"] in ("pull", "build") for item in events[tokenizer:]))
        self.assertGreaterEqual(sum(item["kind"] == "health-helper" and item["target"] == "configure" for item in events), 2)
        ready = next(index for index, item in enumerate(events) if item["kind"] == "embedding-ready")
        self.assertTrue(any(item["kind"] == "create" for item in events[:ready]))
        self.assertTrue(any(item["kind"] == "create" for item in events[ready + 1:]))
        self.assertIn("Press Ctrl-C to stop waiting", result.stdout + result.stderr)

    def test_podman_public_start_omits_docker_artifacts_and_prepares_optional_profiles(self):
        self.successful(self.make(engine="fake-podman"))
        images = [item["image"] for item in self.events() if item["kind"] in ("pull", "build", "inspect")]
        self.assertFalse(any("code-interpreter" in image or "python-executor" in image or "tailscale" in image or "private-onyx-tor:" in image for image in images))
        self.reset_events()
        self.successful(self.make(engine="fake-podman", assignments=("TAILSCALE_FUNNEL_ENABLED=true", "TOR_ONION_SERVICE_ENABLED=true")))
        images = [item["image"] for item in self.events() if item["kind"] in ("pull", "build", "inspect")]
        self.assertTrue(any("tailscale" in image for image in images))
        self.assertTrue(any("private-onyx-tor:" in image for image in images))
        self.assertFalse(any("code-interpreter" in image or "python-executor" in image for image in images))
        self.assertTrue(any(item["kind"] == "health-helper" for item in self.events()))


if __name__ == "__main__":
    unittest.main()
