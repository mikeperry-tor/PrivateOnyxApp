from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tor" / "render_config.py"
SPEC = importlib.util.spec_from_file_location("tor_render_config", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
tor_config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tor_config)


def settings(**overrides: str) -> dict[str, str]:
    values = {
        "TOR_EGRESS_ENABLED": "false",
        "TOR_ONION_SERVICE_ENABLED": "false",
        "TOR_EXIT_COUNTRY": "",
        "TOR_EXIT_NODE_FINGERPRINTS": "",
        "EGRESS_UPSTREAM_PROXY_URL": "",
        "WEBUI_CANONICAL_ORIGIN": "http://localhost:3000",
        "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL": "",
        "ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL": "",
        "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL": "",
    }
    names = {
        "egress": "TOR_EGRESS_ENABLED",
        "onion": "TOR_ONION_SERVICE_ENABLED",
        "country": "TOR_EXIT_COUNTRY",
        "fingerprints": "TOR_EXIT_NODE_FINGERPRINTS",
        "upstream_proxy": "EGRESS_UPSTREAM_PROXY_URL",
        "canonical_origin": "WEBUI_CANONICAL_ORIGIN",
    }
    values.update({names[name]: value for name, value in overrides.items()})
    return values


class TorConfigTests(unittest.TestCase):
    def run_onion_address_target(
        self,
        *,
        onion_enabled: str = "true",
        running: str = "true",
        address: str = "authoritative-tor-value",
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings_file = root / "wrapper.env"
            settings_file.write_text("", encoding="utf-8")
            engine = root / "fake-engine"
            engine.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                'case "$1" in\n'
                "  compose) printf '%s\\n' fake-tor-container ;;\n"
                '  inspect) printf "%s\\n" "${FAKE_TOR_RUNNING}" ;;\n'
                '  exec) printf "%s" "${FAKE_TOR_ADDRESS}" ;;\n'
                "  *) exit 64 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            engine.chmod(0o755)
            environment = dict(os.environ)
            environment.update(
                {
                    "TOR_EGRESS_ENABLED": "false",
                    "TOR_ONION_SERVICE_ENABLED": onion_enabled,
                    "FAKE_TOR_RUNNING": running,
                    "FAKE_TOR_ADDRESS": address,
                }
            )
            return subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "tor-onion-address",
                    f"ENV_FILE={settings_file}",
                    f"CONTAINER_BIN={engine}",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
            )

    def test_onion_address_target_uses_selected_engine_and_tor_value(self) -> None:
        completed = self.run_onion_address_target()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout.strip(), "Onion address: authoritative-tor-value"
        )

    def test_onion_address_target_fails_safely(self) -> None:
        for options in (
            {"onion_enabled": "false"},
            {"running": "false"},
            {"address": ""},
            {"address": "first\nsecond"},
        ):
            with self.subTest(options=options):
                completed = self.run_onion_address_target(**options)
                self.assertNotEqual(completed.returncode, 0)
                self.assertNotIn("Onion address:", completed.stdout)

    def test_settings_file_preserves_optional_dollar_fingerprint(self) -> None:
        fingerprint = "$" + "a" * 40
        with tempfile.TemporaryDirectory() as temporary:
            settings_file = Path(temporary) / "wrapper.env"
            settings_file.write_text(
                "TOR_EGRESS_ENABLED=true\n"
                "TOR_ONION_SERVICE_ENABLED=false\n"
                f'TOR_EXIT_NODE_FINGERPRINTS="{fingerprint}"\n',
                encoding="utf-8",
            )
            values = tor_config.settings_from_file_and_environment(settings_file)
            self.assertEqual(values["TOR_EXIT_NODE_FINGERPRINTS"], fingerprint)

            environment = dict(os.environ)
            for name in tor_config.SETTING_DEFAULTS:
                environment.pop(name, None)
            completed = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "wrapper-config-preflight",
                    f"ENV_FILE={settings_file}",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_embedding_lifecycle_settings_share_reader_and_precedence(self) -> None:
        names = (
            "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL",
            "ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL",
            "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL",
        )
        with tempfile.TemporaryDirectory() as temporary:
            settings_file = Path(temporary) / "wrapper.env"
            settings_file.write_text(
                f'{names[0]}="http://host.docker.internal:8337/v1/embeddings"\n'
                f'{names[0]}="http://host.docker.internal:11434/v1/embeddings"\n'
                f'{names[1]}="model$file"\n'
                f'{names[2]}="served$file"\n',
                encoding="utf-8",
            )
            clean_environment = {
                key: value
                for key, value in os.environ.items()
                if key not in tor_config.SETTING_DEFAULTS
            }
            with mock.patch.dict(
                os.environ, clean_environment, clear=True
            ):
                values = tor_config.settings_from_file_and_environment(
                    settings_file
                )
            self.assertEqual(
                values[names[0]],
                "http://host.docker.internal:11434/v1/embeddings",
            )
            self.assertEqual(values[names[1]], "model$file")
            self.assertEqual(values[names[2]], "served$file")

            environment = clean_environment | {names[1]: "environment$model"}
            completed = subprocess.run(
                [
                    "python3",
                    str(MODULE_PATH),
                    "get",
                    "--settings-file",
                    str(settings_file),
                    "--name",
                    names[1],
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), "environment$model")

            completed = subprocess.run(
                [
                    "make",
                    "-pn",
                    "help",
                    f"ENV_FILE={settings_file}",
                    f"{names[2]}=command$line",
                ],
                cwd=ROOT,
                env=clean_environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn(
                f"{names[2]} := command$$line",
                completed.stdout,
            )

    def test_command_line_environment_preserves_optional_dollar_fingerprint(
        self,
    ) -> None:
        fingerprint = "$" + "B" * 40
        with tempfile.TemporaryDirectory() as temporary:
            settings_file = Path(temporary) / "wrapper.env"
            settings_file.write_text("", encoding="utf-8")
            environment = dict(os.environ)
            for name in tor_config.SETTING_DEFAULTS:
                environment.pop(name, None)
            completed = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "wrapper-config-preflight",
                    f"ENV_FILE={settings_file}",
                    "TOR_EGRESS_ENABLED=true",
                    "TOR_ONION_SERVICE_ENABLED=false",
                    f"TOR_EXIT_NODE_FINGERPRINTS={fingerprint}",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_role_matrix_and_listener_contract(self) -> None:
        for egress, onion in ((True, False), (False, True), (True, True)):
            with self.subTest(egress=egress, onion=onion):
                text = tor_config.render_text(
                    egress=egress, onion=onion, country="", fingerprints=()
                )
                self.assertIn("DataDirectory /var/lib/tor\n", text)
                self.assertIn("ControlPort 0\n", text)
                self.assertIn(
                    "ControlSocket /run/tor-control/control.sock\n", text
                )
                self.assertIn("CookieAuthentication 1\n", text)
                self.assertIn(
                    "CookieAuthFile /run/tor-control/control_auth_cookie\n", text
                )
                self.assertIn("ClientOnly 1\n", text)
                for redundant_relay_setting in (
                    "ORPort",
                    "DirPort",
                    "ExtORPort",
                    "ExitPolicy",
                ):
                    self.assertNotIn(redundant_relay_setting, text)
                self.assertNotIn("HashedControlPassword", text)
                self.assertNotIn("StrictExitNodes", text)
                self.assertNotIn("StrictNodes", text)
                if egress:
                    self.assertIn(
                        "SocksPort unix:/run/tor-egress/socks WorldWritable "
                        "RelaxDirModeCheck\n",
                        text,
                    )
                else:
                    self.assertIn("SocksPort 0\n", text)
                if onion:
                    self.assertIn("HiddenServiceVersion 3\n", text)
                    self.assertIn(
                        "HiddenServicePort 80 10.253.247.3:8080\n", text
                    )
                else:
                    self.assertNotIn("HiddenService", text)

    def test_country_and_fingerprints_normalize_without_fallback(self) -> None:
        egress, onion, country, fingerprints = tor_config.validate_settings(
            settings(egress="true", country="IS")
        )
        self.assertEqual((egress, onion, country, fingerprints), (True, False, "is", ()))
        self.assertIn(
            "ExitNodes {is}\n",
            tor_config.render_text(
                egress=egress,
                onion=onion,
                country=country,
                fingerprints=fingerprints,
            ),
        )

        first = "a" * 40
        second = "$" + "B" * 40
        _, _, country, fingerprints = tor_config.validate_settings(
            settings(egress="true", fingerprints=f"{first},{second}")
        )
        self.assertEqual(fingerprints, ("A" * 40, "B" * 40))
        text = tor_config.render_text(
            egress=True, onion=False, country=country, fingerprints=fingerprints
        )
        self.assertIn(f"ExitNodes ${'A' * 40},${'B' * 40}\n", text)
        self.assertNotIn("Strict", text)

    def test_invalid_settings_fail_closed(self) -> None:
        invalid = (
            {"egress": "yes"},
            {"onion": "1"},
            {"country": "is"},
            {"egress": "true", "country": "A1"},
            {"egress": "true", "country": "i{"},
            {"egress": "true", "fingerprints": "a" * 39},
            {"egress": "true", "fingerprints": f"{'a' * 40},"},
            {"egress": "true", "fingerprints": f"{'a' * 40},${'A' * 40}"},
            {
                "egress": "true",
                "country": "is",
                "fingerprints": "a" * 40,
            },
            {"egress": "true", "upstream_proxy": "http://proxy.example:8080"},
            {"canonical_origin": "https://user@example.com"},
            {"canonical_origin": "https://example.com/path"},
            {"canonical_origin": "https://*.example.com"},
            {"canonical_origin": "https://example.com,https://other.example"},
            {"canonical_origin": "https://exa mple.com"},
            {"canonical_origin": "https://-invalid.example"},
            {"canonical_origin": "https://example.com:99999"},
            {"canonical_origin": "ftp://example.com"},
            {"canonical_origin": "https://example.com?query"},
        )
        for overrides in invalid:
            with self.subTest(overrides=overrides), self.assertRaises(
                tor_config.ConfigError
            ):
                tor_config.validate_settings(settings(**overrides))

    def test_native_tor_proxy_conflict_explains_deferred_plan(self) -> None:
        with self.assertRaisesRegex(
            tor_config.ConfigError,
            (
                r"mutually exclusive.*zero-data-retention \(ZDR\).*"
                r"docs/plans/deferred/https_proxy_after_tor\.md"
            ),
        ):
            tor_config.validate_settings(
                settings(
                    egress="true",
                    upstream_proxy="https://proxy.example:8443",
                )
            )

    def test_selector_without_egress_is_rejected(self) -> None:
        with self.assertRaisesRegex(tor_config.ConfigError, "require"):
            tor_config.validate_settings(settings(country="is"))

    def test_settings_parser_is_shared_with_make_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings_file = Path(temporary) / "wrapper.env"
            settings_file.write_text(
                "TOR_EGRESS_ENABLED=false\n"
                "TOR_EGRESS_ENABLED='true' # last definition wins\n"
                'TOR_ONION_SERVICE_ENABLED="false"\n'
                "WEBUI_CANONICAL_ORIGIN='https://linux.example'\n",
                encoding="utf-8",
            )
            values = tor_config.settings_from_file_and_environment(settings_file)
            self.assertEqual(values["TOR_EGRESS_ENABLED"], "true")

            environment = dict(os.environ)
            for name in tor_config.SETTING_DEFAULTS:
                environment.pop(name, None)
            completed = subprocess.run(
                [
                    "make",
                    "-pn",
                    f"ENV_FILE={settings_file}",
                    "CONTAINER_BIN=docker",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            )
            full_files = next(
                line.removeprefix("FULL_FILES := ")
                for line in completed.stdout.splitlines()
                if line.startswith("FULL_FILES := ")
            )
            self.assertIn(
                "compose_overlays/docker-compose.tor.yml", full_files
            )
            self.assertIn(
                "compose_overlays/docker-compose.tor-egress.yml", full_files
            )
            self.assertIn(
                "WEBUI_CANONICAL_ORIGIN := https://linux.example",
                completed.stdout.splitlines(),
            )

    def test_empty_wrapper_uses_example_routing_defaults(self) -> None:
        names = (
            "TOR_EGRESS_ENABLED",
            "TOR_ONION_SERVICE_ENABLED",
            "TOR_EXIT_COUNTRY",
            "TOR_EXIT_NODE_FINGERPRINTS",
            "EGRESS_UPSTREAM_PROXY_URL",
            "WEBUI_CANONICAL_ORIGIN",
        )
        clean_environment = {
            key: value
            for key, value in os.environ.items()
            if key not in tor_config.SETTING_DEFAULTS
        }
        with tempfile.TemporaryDirectory() as temporary:
            empty = Path(temporary) / "empty.env"
            empty.write_text("", encoding="utf-8")
            with mock.patch.dict(os.environ, clean_environment, clear=True):
                defaults = tor_config.settings_from_file_and_environment(empty)
                example = tor_config.settings_from_file_and_environment(
                    ROOT / ".env.wrapper.example"
                )

        for name in names:
            with self.subTest(name=name):
                self.assertEqual(defaults[name], example[name])

    def test_settings_parser_rejects_ambiguous_multiword_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings_file = Path(temporary) / "wrapper.env"
            settings_file.write_text(
                "WEBUI_CANONICAL_ORIGIN=https://example.com extra\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(tor_config.ConfigError, "one shell-style"):
                tor_config.read_wrapper_settings(settings_file)

    def test_preflight_requires_existing_settings_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing.env"
            completed = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "wrapper-config-preflight",
                    f"ENV_FILE={missing}",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("wrapper settings file does not exist", completed.stderr)

    def test_preflight_validates_unselected_setting_syntax(self) -> None:
        malformed_values = (
            'HOST_PORT_ONYX_WEBUI="unterminated\n',
            "HOST_PORT_ONYX_WEBUI=3000 extra\n",
            "HOST_PORT_ONYX_WEBUI\n",
            " HOST_PORT_ONYX_WEBUI=3000\n",
            "host_port_onyx_webui=3000\n",
        )
        with tempfile.TemporaryDirectory() as temporary:
            settings_file = Path(temporary) / "wrapper.env"
            for content in malformed_values:
                with self.subTest(content=content):
                    settings_file.write_text(content, encoding="utf-8")
                    completed = subprocess.run(
                        [
                            "make",
                            "--no-print-directory",
                            "wrapper-config-preflight",
                            f"ENV_FILE={settings_file}",
                        ],
                        cwd=ROOT,
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn("ERROR:", completed.stderr)

    def test_atomic_failure_preserves_existing_config_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config" / "torrc"
            state_key = root / "state" / "onion-service" / "hs_ed25519_secret_key"
            config.parent.mkdir(parents=True)
            state_key.parent.mkdir(parents=True)
            config.write_text("old\n", encoding="ascii")
            state_key.write_bytes(b"private")
            with self.assertRaises(UnicodeEncodeError):
                tor_config.atomic_write(config, "bad \N{SNOWMAN}\n")
            self.assertEqual(config.read_text(encoding="ascii"), "old\n")
            self.assertEqual(state_key.read_bytes(), b"private")


if __name__ == "__main__":
    unittest.main()
