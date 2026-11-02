from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

if sys.platform not in ("linux", "darwin"):
    raise unittest.SkipTest("component fixtures require supported Linux/macOS filesystem and flock contracts")

from tools.components import configuration


class ConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "Makefile").write_text("fixture public Makefile\n")
        (self.root / "docker-compose.yaml").write_text("fixture public wrapper\n")
        self.inventory = self.root / "tools/components/ownership.json"
        self.inventory.parent.mkdir(parents=True)
        self.inventory.write_text(json.dumps({"compatibility": {"onyx": "v4.8.8", "onyx_source": "a" * 40}}))
        self.destination = self.root / "onyx_data"
        self.env_file = self.destination / "deployment/.env"
        self.calls = []
        self.options = dict(root=self.root, destination=self.destination, env_file=self.env_file,
                            ref="v4.8.8", image_tag="v4.8.8", controller_tag="0.4.9",
                            revision="20261102.1", container_bin="fake-engine", lite=True,
                            downloader=self.download, validator=lambda stage, engine: None)

    def download(self, url):
        self.calls.append(url)
        if url.endswith("env.template"):
            return b'IMAGE_TAG=latest\nUSER_AUTH_SECRET=""\nCOMPOSE_PROFILES=s3-filestore\nFILE_STORE_BACKEND=s3\n'
        if url.endswith(".yml"):
            return b"services:\n  fixture:\n    image: fixture:one\n"
        if url.endswith("run-nginx.sh"):
            return b"#!/bin/sh\nset -eu\nexit 0\n"
        return b"fixture generated content\n"

    def prepare(self, **changes):
        return configuration.prepare(**(self.options | changes))

    def test_first_create_and_reuse(self):
        self.assertTrue(self.prepare())
        self.assertEqual(len(self.calls), 7)
        env = self.env_file.read_text()
        self.assertIn("IMAGE_TAG=v4.8.8\n", env)
        self.assertIn("CODE_INTERPRETER_IMAGE_TAG=0.4.9\n", env)
        self.assertIn("AUTH_TYPE=basic\n", env)
        self.assertIn("COMPOSE_PROFILES=\n", env)
        self.assertIn("FILE_STORE_BACKEND=postgres\n", env)
        self.assertRegex(env, r"USER_AUTH_SECRET=[a-f0-9]{64}\n")
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)
        self.assertFalse(self.prepare())
        self.assertEqual(len(self.calls), 7)
        self.assertEqual(self.env_file.read_text(), env)
        for relative, (_, mode) in configuration.ARTIFACTS.items():
            self.assertEqual(stat.S_IMODE((self.destination / relative).stat().st_mode), mode)

    def test_selection_changes_refresh_without_changing_application_version(self):
        for change in ({"ref": "v4.8.8-config"}, {"revision": "20261102.2"}, {"controller_tag": "0.4.10"}):
            with self.subTest(change=change):
                self.prepare()
                self.assertTrue(self.prepare(**change))

    def test_reviewed_source_is_shared_by_all_downloads_and_invalidates_identity(self):
        self.prepare()
        self.assertTrue(all("/" + "a" * 40 + "/deployment/" in url for url in self.calls))
        self.inventory.write_text(json.dumps({"compatibility": {"onyx": "v4.8.8", "onyx_source": "b" * 40}}))
        self.calls.clear()
        self.assertTrue(self.prepare())
        self.assertEqual(len(self.calls), 7)
        self.assertTrue(all("/" + "b" * 40 + "/deployment/" in url for url in self.calls))
        self.assertFalse(self.prepare())
        # Unrelated ownership metadata does not trigger remote preparation.
        inventory = json.loads(self.inventory.read_text())
        self.inventory.write_text(json.dumps(inventory | {"unrelated": "changed"}))
        self.assertFalse(self.prepare())

    def test_other_explicit_configuration_refs_are_retained(self):
        self.prepare(ref="custom-config")
        self.assertTrue(all("/custom-config/deployment/" in url for url in self.calls))
        self.assertFalse(self.prepare(ref="custom-config"))

    def test_invalid_reviewed_commit_fails_before_downloading_or_installing(self):
        self.inventory.write_text(json.dumps({"compatibility": {"onyx": "v4.8.8", "onyx_source": "moving-tag"}}))
        with self.assertRaisesRegex(ValueError, "reviewed Onyx source"):
            self.prepare()
        self.assertEqual(self.calls, [])
        self.assertFalse(self.destination.exists())

    def test_wrapper_generation_inputs_change_identity(self):
        self.prepare()
        (self.root / "Makefile").write_text("fixture changed generation input")
        self.assertTrue(self.prepare())
        self.assertFalse(self.prepare())
        overlay = self.root / "compose_overlays/full.yml"
        overlay.parent.mkdir()
        overlay.write_text("fixture overlay input")
        self.assertTrue(self.prepare())

    def test_missing_content_changed_or_wrong_mode_repairs_every_artifact(self):
        self.prepare()
        for relative in configuration.ARTIFACTS:
            with self.subTest(relative=relative):
                path = self.destination / relative
                path.unlink()
                self.assertTrue(self.prepare())
                path.write_text("modified")
                self.assertTrue(self.prepare())
                path.chmod(0o600)
                self.assertTrue(self.prepare())

    def test_existing_secret_custom_settings_and_local_files_survive(self):
        self.prepare()
        original = 'USER_AUTH_SECRET="fixture secret ${literal}"\nCUSTOM="keep # value"\nIMAGE_TAG=old\nCODE_INTERPRETER_IMAGE_TAG=old\n'
        self.env_file.write_text(original)
        local = self.destination / "data/nginx/local/custom.conf"
        local.write_text("local configuration")
        self.assertTrue(self.prepare(revision="next"))
        self.assertEqual(self.env_file.read_text(), original.replace("CODE_INTERPRETER_IMAGE_TAG=old", "CODE_INTERPRETER_IMAGE_TAG=0.4.9").replace("\nIMAGE_TAG=old", "\nIMAGE_TAG=v4.8.8"))
        self.assertEqual(local.read_text(), "local configuration")

    def test_sync_accepts_single_exported_and_spaced_selected_assignments(self):
        self.prepare()
        self.env_file.write_bytes(b'export IMAGE_TAG = old\r\n  CODE_INTERPRETER_IMAGE_TAG = old\nCUSTOM="fixture ${literal}"\r\nUSER_AUTH_SECRET="retain secret"\r\n')
        self.prepare(sync_only=True)
        self.assertEqual(self.env_file.read_bytes(), b'IMAGE_TAG=v4.8.8\nCODE_INTERPRETER_IMAGE_TAG=0.4.9\nCUSTOM="fixture ${literal}"\r\nUSER_AUTH_SECRET="retain secret"\r\n')

    def test_effective_duplicate_assignments_reject_sync_and_invalidate_stamp(self):
        for key in ("IMAGE_TAG", "CODE_INTERPRETER_IMAGE_TAG"):
            for sync_only in (False, True):
                with self.subTest(key=key, sync_only=sync_only):
                    self.env_file.unlink(missing_ok=True)
                    self.prepare()
                    original = f'{key}=first\n export {key} = last\nUSER_AUTH_SECRET="fixture secret"\n'.encode()
                    self.env_file.write_bytes(original)
                    with self.assertRaisesRegex(configuration.ConfigurationError, "duplicate generated environment assignment"):
                        self.prepare(sync_only=sync_only)
                    self.assertEqual(self.env_file.read_bytes(), original)
                    self.assertFalse((self.destination / configuration.STAMP).exists())

    def test_multiline_secret_values_keep_interior_selected_key_lines(self):
        for quote in ("'", '"'):
            with self.subTest(quote=quote):
                self.prepare()
                secret = f"SECRET={quote}first \\{quote} escaped\r\nIMAGE_TAG=sensitive\r\nCODE_INTERPRETER_IMAGE_TAG=also-sensitive\r\nlast{quote} # comment\r\n"
                original = secret + "IMAGE_TAG=old\n export CODE_INTERPRETER_IMAGE_TAG = old\n"
                self.env_file.write_bytes(original.encode())
                self.prepare(sync_only=True)
                expected = secret + "IMAGE_TAG=v4.8.8\nCODE_INTERPRETER_IMAGE_TAG=0.4.9\n"
                self.assertEqual(self.env_file.read_bytes(), expected.encode())

    def test_unterminated_quoted_environment_fails_before_write(self):
        for quote in ("'", '"'):
            with self.subTest(quote=quote):
                self.env_file.unlink(missing_ok=True)
                self.prepare()
                original = f"SECRET={quote}first\nIMAGE_TAG=secret-interior\n".encode()
                self.env_file.write_bytes(original)
                with self.assertRaisesRegex(configuration.ConfigurationError, "unterminated quoted"):
                    self.prepare(sync_only=True)
                self.assertEqual(self.env_file.read_bytes(), original)
                self.assertFalse((self.destination / configuration.STAMP).exists())

    def test_existing_environment_retains_unrelated_crlf_bytes(self):
        self.prepare()
        original = b'CUSTOM="fixture"\r\nUSER_AUTH_SECRET="retain"\r\nIMAGE_TAG=old\r\n'
        self.env_file.write_bytes(original)
        self.prepare(revision="next")
        self.assertTrue(self.env_file.read_bytes().startswith(b'CUSTOM="fixture"\r\nUSER_AUTH_SECRET="retain"\r\n'))

    def test_download_or_validation_failure_installs_nothing(self):
        def broken(url):
            if url.endswith("docker-compose.craft.yml"):
                raise OSError("fixture download failure")
            return self.download(url)
        with self.assertRaises(OSError):
            self.prepare(downloader=broken)
        self.assertFalse(self.destination.exists())
        def invalid(stage, engine):
            raise configuration.ConfigurationError("fixture invalid model")
        with self.assertRaises(configuration.ConfigurationError):
            self.prepare(validator=invalid)
        self.assertFalse(self.destination.exists())

    def test_partial_install_or_env_failure_leaves_no_completion_and_retry_repairs(self):
        self.prepare()
        write = configuration.atomic_write
        def broken(path, content, mode):
            if path == self.destination / "deployment/env.template":
                raise OSError("fixture install failure")
            write(path, content, mode)
        with patch.object(configuration, "atomic_write", side_effect=broken):
            with self.assertRaises(OSError):
                self.prepare(revision="next")
        self.assertFalse((self.destination / configuration.STAMP).exists())
        self.assertTrue(self.prepare(revision="next"))
        self.env_file.write_text("IMAGE_TAG=one\nIMAGE_TAG=two\nUSER_AUTH_SECRET=fixture\n")
        with self.assertRaises(configuration.ConfigurationError):
            self.prepare(revision="another")
        self.assertFalse((self.destination / configuration.STAMP).exists())
        self.assertIn("USER_AUTH_SECRET=fixture", self.env_file.read_text())

    def test_parallel_preparation_reuses_first_complete_result(self):
        entered = threading.Event()
        release = threading.Event()
        results, failures = [], []
        def delayed(url):
            if not entered.is_set():
                entered.set()
                self.assertTrue(release.wait(5))
            return self.download(url)
        def run():
            try:
                results.append(self.prepare(downloader=delayed))
            except Exception as error:
                failures.append(error)
        first = threading.Thread(target=run)
        second = threading.Thread(target=run)
        first.start()
        self.assertTrue(entered.wait(5))
        second.start()
        release.set()
        first.join(5)
        second.join(5)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(failures, [])
        self.assertCountEqual(results, [True, False])
        self.assertEqual(len(self.calls), 7)

    def test_required_wrapper_input_cannot_disappear(self):
        for name in ("Makefile", "docker-compose.yaml"):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.unlink()
                with self.assertRaises(FileNotFoundError):
                    self.prepare()
                self.assertEqual(self.calls, [])
                path.write_bytes(original)

    def test_reusable_fast_path_env_failure_invalidates_completion(self):
        self.prepare()
        self.env_file.write_text("IMAGE_TAG=one\nIMAGE_TAG=two\nUSER_AUTH_SECRET=fixture\n")
        with self.assertRaises(configuration.ConfigurationError):
            self.prepare()
        self.assertFalse((self.destination / configuration.STAMP).exists())
        self.assertEqual(len(self.calls), 7)

    def test_sync_only_preserves_secrets_never_downloads_and_rejects_failure(self):
        self.prepare()
        original = self.env_file.read_bytes()
        self.prepare(sync_only=True)
        self.assertEqual(self.env_file.read_bytes(), original)
        self.assertTrue((self.destination / configuration.STAMP).exists())
        self.prepare(sync_only=True, image_tag="v4.8.9")
        self.assertIn("IMAGE_TAG=v4.8.9\n", self.env_file.read_text())
        self.assertFalse((self.destination / configuration.STAMP).exists())
        self.assertEqual(len(self.calls), 7)
        self.prepare()
        self.env_file.write_text("IMAGE_TAG=one\nIMAGE_TAG=two\nUSER_AUTH_SECRET=fixture\n")
        with self.assertRaises(configuration.ConfigurationError):
            self.prepare(sync_only=True)
        self.assertFalse((self.destination / configuration.STAMP).exists())
        self.env_file.unlink()
        with self.assertRaises(configuration.ConfigurationError):
            self.prepare(sync_only=True)

    def test_native_validation_rejects_malformed_runner_before_compose(self):
        runner = self.destination / "data/nginx/run-nginx.sh"
        runner.parent.mkdir(parents=True)
        runner.write_text("#!/bin/sh\nif then\n")
        with self.assertRaisesRegex(configuration.ConfigurationError, "shell syntax"):
            configuration.validate_staged(self.destination, "nonexistent-engine-must-not-run")

    def test_native_validation_only_renders_without_env_resolution(self):
        self.destination.mkdir()
        with patch.dict("os.environ", {"COMPOSE_FILE": "private-file", "COMPOSE_ENV_FILES": "private-env", "COMPOSE_PROFILES": "private-profile"}):
            with patch.object(configuration.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps({"services": {"fixture": {"image": "fixture"}}}), "")) as run:
                configuration.validate_staged(self.destination, "podman")
        self.assertEqual(run.call_count, 4)
        self.assertEqual(run.call_args_list[0].args[0][:2], ["sh", "-n"])
        for call in run.call_args_list[1:]:
            command = call.args[0]
            self.assertEqual(command[:2], ["podman", "compose"])
            self.assertIn("--no-env-resolution", command)
            self.assertIn("--no-interpolate", command)
            self.assertEqual(command[-3:], ["--no-interpolate", "--format", "json"])
            self.assertNotIn("COMPOSE_FILE", call.kwargs["env"])
            self.assertNotIn("COMPOSE_ENV_FILES", call.kwargs["env"])
            self.assertNotIn("up", command)
            self.assertNotIn("pull", command)


if __name__ == "__main__":
    unittest.main()
