from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("component_manage", ROOT / "tools/components/manage.py")
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)


class ComponentOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.inventory = json.loads((ROOT / "tools/components/ownership.json").read_text())
        self.pins = manage.read_pins(ROOT / "stack.versions.env")
        self.model = {"services": {}}
        for name, contract in self.inventory["onyx_services"].items():
            tag = "fixture"
            if contract.get("application"):
                tag = self.pins["ONYX_IMAGE_TAG"]
            if contract.get("compatibility"):
                tag = self.inventory["compatibility"]["default"]
            image = contract["repository"] + ":" + tag
            if contract.get("inherited"):
                image = next(ref for ref in self.inventory["registry_digests"]
                             if manage.repository(ref) == contract["repository"])
            if contract.get("copy"):
                image = self.pins[contract["copy"]]
            self.model["services"][name] = {"image": image}

    def test_unchanged_and_changed_owner_values(self):
        self.assertEqual(manage.reconcile(self.model, self.inventory, self.pins), {})
        self.model["services"]["nginx"]["image"] = "nginx:fixture-next"
        self.assertEqual(manage.reconcile(self.model, self.inventory, self.pins),
                         {"NGINX_IMAGE": "docker.io/library/nginx:fixture-next"})

    def test_registry_aliases_preserve_tags_and_digests(self):
        image = "nginx:1.25.5-alpine@sha256:" + "a" * 64
        expected = "docker.io/library/" + image
        self.assertEqual(manage.normalized(image), expected)
        self.assertEqual(manage.normalized("index.docker.io/library/" + image), expected)
        self.assertEqual(manage.normalized("docker.io/" + image), expected)
        self.model["services"]["nginx"]["image"] = image
        self.assertEqual(manage.reconcile(self.model, self.inventory, self.pins), {"NGINX_IMAGE": expected})

    def test_contract_add_remove_missing_and_ambiguous_fail(self):
        for mutation in ("added", "removed", "missing", "unresolved", "repository"):
            model = copy.deepcopy(self.model)
            if mutation == "added":
                model["services"]["new_owner"] = {"image": "nginx:fixture"}
            elif mutation == "removed":
                del model["services"]["cache"]
            else:
                model["services"]["cache"] = {"image": {"missing": "", "unresolved": "redis:${TAG}",
                                                         "repository": "other/redis:fixture"}[mutation]}
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                manage.reconcile(model, self.inventory, self.pins)

    def test_controller_authority_changes_require_review(self):
        for key in ("ONYX_IMAGE_TAG", "CODE_INTERPRETER_IMAGE_TAG", "PYTHON_EXECUTOR_IMAGE_TAG"):
            pins = self.pins | {key: "fixture-next"}
            with self.subTest(key=key), self.assertRaises(ValueError):
                manage.reconcile(self.model, self.inventory, pins)
        self.model["services"]["code-interpreter"]["image"] = "onyxdotapp/code-interpreter:0.4.8"
        with self.assertRaisesRegex(ValueError, "compatibility review"):
            manage.reconcile(self.model, self.inventory, self.pins)
        self.setUp()
        for key in self.inventory["compatibility"]["artifacts"]:
            for artifact in ("docker.io/evil/controller:0.4.10@sha256:" + "a" * 64,
                             self.pins[key].split("@")[0] + "@sha256:" + "b" * 64):
                with self.subTest(key=key, artifact=artifact), self.assertRaisesRegex(ValueError, "compatibility review"):
                    manage.reconcile(self.model, self.inventory, self.pins | {key: artifact})

    def test_minio_image_and_source_must_be_reviewed_together(self):
        self.model["services"]["minio"]["image"] = "onyxdotapp/minio:new@sha256:" + "a" * 64
        with self.assertRaisesRegex(ValueError, "source identity"):
            manage.reconcile(self.model, self.inventory, self.pins)

    def test_owner_selected_registry_defaults_need_qualification(self):
        for name in ("api_server", "web_server", "relational_db", "cache", "opensearch"):
            reference = self.model["services"][name]["image"]
            for missing in (True, False):
                inventory = copy.deepcopy(self.inventory)
                if missing:
                    inventory["registry_digests"].pop(reference)
                else:
                    inventory["registry_digests"][reference] = "invalid-digest"
                with self.subTest(name=name, missing=missing), self.assertRaises(ValueError):
                    manage.reconcile(self.model, inventory, self.pins)

    def test_multiple_refreshes_and_atomic_pin_edit(self):
        self.assertEqual(manage.next_revision("20261101", "20261102"), "20261102.1")
        self.assertEqual(manage.next_revision("20261102.1", "20261102"), "20261102.2")
        for revision in ("bad", "20261103"):
            with self.assertRaises(ValueError):
                manage.next_revision(revision, "20261102")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pins"
            path.write_text("# preserve\nPIN=old\n")
            manage.write_pins(path, {"PIN": "new"})
            self.assertEqual(path.read_text(), "# preserve\nPIN=new\n")
            with self.assertRaises(ValueError):
                manage.write_pins(path, {"UNKNOWN": "new"})
            path.write_text("PIN=one\nPIN=two\n")
            with self.assertRaises(ValueError):
                manage.read_pins(path)

    def test_every_literal_build_base_has_an_owner(self):
        import shlex
        covered = set()
        for component in self.inventory["components"].values():
            self.assertTrue(component["owner"])
            for key in component["selection"]:
                self.assertIn(key, self.pins)
            for name in component["build"]:
                path = ROOT / name
                self.assertTrue(path.is_file(), name)
                if path.name != "Dockerfile":
                    continue
                covered.add(path.relative_to(ROOT).as_posix())
                for line in path.read_text().splitlines():
                    if not line.lstrip().upper().startswith("FROM "):
                        continue
                    tokens = shlex.split(line)
                    if not tokens or tokens[0].upper() != "FROM":
                        continue
                    base = next(token for token in tokens[1:] if not token.startswith("--"))
                    if not base.startswith("$"):
                        self.assertIn(base, component.get("literal_bases", []), name)
        tracked = subprocess.check_output(["git", "ls-files", "*Dockerfile"], cwd=ROOT, text=True).splitlines()
        self.assertEqual(covered, set(tracked))

    def test_three_nginx_consumers_obey_one_effective_override(self):
        from test_onyx_network_isolation import _compose_model
        image = "docker.io/library/nginx:fixture@sha256:" + "c" * 64
        for mode in ("lite", "full"):
            services = _compose_model(mode, "docker-compose.tor.yml", "docker-compose.tor-onion.yml",
                                      profiles=("tailscale",), env_overrides={"NGINX_IMAGE": image})["services"]
            for name in ("nginx", "tailscale-frontend-gateway", "tor-frontend-gateway"):
                self.assertEqual(services[name]["image"], image)

    def test_revision_requires_explicit_valid_base_and_changed_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
            git("init", "-q")
            git("config", "user.email", "fixture@example.invalid")
            git("config", "user.name", "Fixture")
            (root / "stack.versions.env").write_text("OS_SECURITY_UPDATE_REVISION=20261102.1\n")
            (root / "Makefile").write_text("old\n")
            git("add", "stack.versions.env", "Makefile")
            git("commit", "-qm", "baseline")
            base = git("rev-parse", "HEAD")
            pins = {manage.REVISION: "20261102.1"}
            manage.check_revision(root, base, pins)
            (root / "Makefile").write_text("new\n")
            with self.assertRaisesRegex(ValueError, "without refresh"):
                manage.check_revision(root, base, pins)
            for revision in ("20261101.9", "bad", "20261301.1"):
                with self.subTest(revision=revision), self.assertRaises(ValueError):
                    manage.check_revision(root, base, {manage.REVISION: revision})
            manage.check_revision(root, base, {manage.REVISION: "20261102.2"})
            (root / "Makefile").write_text("old\n")
            (root / "browser").mkdir()
            (root / "browser/new.py").write_text("# new build input\n")
            with self.assertRaisesRegex(ValueError, "browser/new.py"):
                manage.check_revision(root, base, pins)
            (root / "browser/new.py").unlink()
            for name in ("egress/new.py", "onyx/patches/new.py", "onyx/nginx/new.conf",
                         "onyx/opensearch/audit.yml", "compose_overlays/new.yml", "podman/new.py",
                         "onyx/doc_drop_webserver.py", "docker-compose.yaml", ".dockerignore"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture\n")
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, name):
                    manage.check_revision(root, base, pins)
                path.unlink()
            for name in ("onyx/onyx_data/deployment/.env", "reference_repos/fixture/new.py",
                         "docs/fixture.md", "tests/test_fixture.py", ".env.wrapper"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic excluded fixture\n")
            manage.check_revision(root, base, pins)
            with self.assertRaises(subprocess.CalledProcessError):
                manage.check_revision(root, "missing", pins)


if __name__ == "__main__":
    unittest.main()
