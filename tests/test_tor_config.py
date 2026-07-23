from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


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
                'TOR_ONION_SERVICE_ENABLED="false"\n',
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

    def test_settings_parser_rejects_ambiguous_multiword_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings_file = Path(temporary) / "wrapper.env"
            settings_file.write_text(
                "WEBUI_CANONICAL_ORIGIN=https://example.com extra\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(tor_config.ConfigError, "one shell-style"):
                tor_config.read_wrapper_settings(settings_file)

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
