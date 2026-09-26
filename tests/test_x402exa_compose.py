"""Make-selected feature models with synthetic configuration only."""
import itertools
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

from test_onyx_network_isolation import ROOT, SECRET_ENV, _compose_command, _wrapper_neutral_environment

KEY = "0" * 63 + "1"


def model(mode, engine, enabled, executor=False, tor=False, onion=False, host=None, down=False,
          host_os="Darwin", engine_mode="rootful", vpn=False, proxy="", file_key=None, key_override=None):
    env = _wrapper_neutral_environment() | SECRET_ENV
    env.pop("SEARXNG_X402_PRIVKEY", None)
    if enabled:
        env["SEARXNG_X402_PRIVKEY"] = KEY
    if host is not None:
        env["SEARXNG_HOST"] = host
    with tempfile.TemporaryDirectory() as directory:
        configuration = Path(directory) / "settings.env"
        configuration.write_text((ROOT / ".env.wrapper.example").read_text())
        if file_key is not None:
            with configuration.open("a") as stream:
                stream.write("\nSEARXNG_X402_PRIVKEY=" + file_key + "\n")
        command = ["make", "-s", "--no-print-directory", "-f", "Makefile", "-f", "-", "model",
                   "MAKECMDGOALS=" + ("down-" if down else "up-") + mode,
                   "ENV_FILE=" + str(configuration), "CONTAINER_BIN=" + engine,
                   "PRIVATE_ONYX_DOCKER_ENGINE_MODE=" + engine_mode, "PRIVATE_ONYX_DOCKER_GATEWAY_MODE=isolated",
                   "HOST_OS=" + host_os, "DOCKER_SOCK_PATH=/tmp/fixture.sock",
                   "MYST_VPN_ENABLED=" + str(vpn).lower(), "EGRESS_UPSTREAM_PROXY_URL=" + proxy,
                   "ONYX_CODE_INTERPRETER_ENABLE_NETWORK=" + str(executor).lower(),
                   "TOR_EGRESS_ENABLED=" + str(tor).lower(), "TOR_ONION_SERVICE_ENABLED=" + str(onion).lower()]
        if key_override is not None:
            command.append("SEARXNG_X402_PRIVKEY=" + key_override)
        variable = mode.upper() + ("_DOWN_FILES" if down else "_FILES")
        recipe = 'model:\n\t@COMPOSE_FILE=$(' + variable + ') ' + shlex.join([
            *_compose_command(), "--env-file", "stack.versions.env", "--env-file", str(configuration),
            "config", "--no-env-resolution", "--format", "json",
        ]) + "\n"
        result = subprocess.run(command, input=recipe, env=env, cwd=ROOT, text=True, capture_output=True, check=True)
        return json.loads(result.stdout)


class ComposeTests(unittest.TestCase):
    def test_command_line_key_overrides_file_and_environment_in_both_modes(self):
        for mode, engine in itertools.product(("lite", "full"), ("docker", "podman")):
            for environment_enabled, file_key, override in (
                (False, KEY, ""), (True, KEY, ""),
                (False, "", KEY), (False, "invalid-overridden-key", KEY),
            ):
                with self.subTest(mode=mode, engine=engine, environment_enabled=environment_enabled, override_enabled=bool(override)):
                    value = model(mode, engine, environment_enabled, file_key=file_key, key_override=override)
                    services = value["services"]
                    selected = bool(override)
                    self.assertEqual("searxng-x402-egress-bridge" in services, selected)
                    self.assertEqual("searxng-x402-egress" in services["searxng-core"]["networks"], selected)
                    self.assertEqual(services["searxng-core"]["environment"].get("SEARXNG_X402_PRIVKEY"), override if selected else None)
                    peers = services["onyx-public-egress-proxy"]["environment"]["EGRESS_PROXY_ALLOWED_CLIENT_HOSTS"].split(",")
                    self.assertEqual("searxng-x402-egress-bridge" in peers, selected)

    def test_invalid_command_line_key_fails_before_diagnostic_or_teardown_action(self):
        for goal in ("up-lite", "up-full", "ps-lite", "logs-full", "down-lite", "down-full", "health-inventory", "integration-x402exa", "wrapper-config-preflight"):
            with self.subTest(goal=goal), tempfile.TemporaryDirectory() as directory:
                marker = Path(directory) / "must-not-run"
                container_marker = Path(directory) / "container-action"
                container = Path(directory) / "docker"
                container.write_text("#!/bin/sh\ntouch " + shlex.quote(str(container_marker)) + "\nexit 99\n")
                container.chmod(0o755)
                key = "secret-canary$(shell touch " + str(marker) + ")"
                result = subprocess.run([
                    "make", "-s", goal, "ENV_FILE=.env.wrapper.example",
                    "CONTAINER_BIN=" + str(container), "DOCKER_SOCK_PATH=/tmp/fixture.sock",
                    "PRIVATE_ONYX_DOCKER_ENGINE_MODE=rootful", "PRIVATE_ONYX_DOCKER_GATEWAY_MODE=isolated",
                    "SEARXNG_X402_PRIVKEY=" + key,
                ], cwd=ROOT, env=_wrapper_neutral_environment(), capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid SEARXNG_X402_PRIVKEY", result.stderr)
                self.assertNotIn("secret-canary", result.stdout + result.stderr)
                self.assertFalse(marker.exists())
                self.assertFalse(container_marker.exists())

    def test_platform_and_final_hop_variants(self):
        for engine, host_os, engine_mode in (("docker", "Linux", "rootful"),
                                            ("docker", "Linux", "rootless"),
                                            ("podman", "Linux", "rootful")):
            for route in ({}, {"vpn": True}, {"proxy": "http://proxy.example:8080"}, {"tor": True, "onion": True}):
                with self.subTest(engine=engine, engine_mode=engine_mode, route=route):
                    value = model("full", engine, True, host_os=host_os, engine_mode=engine_mode,
                                  executor=engine == "docker", **route)
                    services = value["services"]
                    self.assertEqual(set(services["searxng-core"]["networks"]),
                                     {"searxng-api", "obscura-control", "searxng-x402-egress"})
                    self.assertIn("searxng-x402-policy-upstream", services["netns-holder"]["networks"])
                    self.assertNotIn("network_mode", services["searxng-core"])

    def test_enabled_disabled_modes_and_feature_composition(self):
        for mode in ("lite", "full"):
            for engine in ("docker", "podman"):
                for enabled in (False, True):
                    with self.subTest(mode=mode, engine=engine, enabled=enabled):
                        value = model(mode, engine, enabled, executor=engine == "docker", tor=enabled, onion=enabled)
                        services = value["services"]
                        self.assertEqual("searxng-x402-egress-bridge" in services, enabled)
                        names = set(services["searxng-core"]["networks"])
                        self.assertEqual(names, {"searxng-api", "obscura-control"} | ({"searxng-x402-egress"} if enabled else set()))
                        for name, service in services.items():
                            self.assertEqual("SEARXNG_X402_PRIVKEY" in service.get("environment", {}), enabled and name == "searxng-core")
                        peers = services["onyx-public-egress-proxy"]["environment"]["EGRESS_PROXY_ALLOWED_CLIENT_HOSTS"].split(",")
                        self.assertEqual("executor-egress-bridge" in peers, engine == "docker")
                        self.assertEqual("searxng-x402-egress-bridge" in peers, enabled)
                        if enabled:
                            bridge = services["searxng-x402-egress-bridge"]
                            self.assertEqual(set(bridge["networks"]), {"searxng-x402-egress", "searxng-x402-policy-upstream"})
                            self.assertNotIn("ports", bridge)
                            self.assertNotIn("volumes", bridge)
                            for name in bridge["networks"]:
                                network = value["networks"][name]
                                self.assertTrue(network["internal"])
                                self.assertEqual(network.get("driver_opts", {}).get("com.docker.network.bridge.gateway_mode_ipv4") == "isolated", engine == "docker")

    def test_loopback_default_empty_and_explicit_bind_and_down_removal(self):
        for mode in ("lite", "full"):
            for host, expected in ((None, "127.0.0.1"), ("", "127.0.0.1"), ("0.0.0.0", "0.0.0.0")):
                value = model(mode, "docker", False, host=host)
                publisher = next(service for service in value["services"].values() if any(str(p.get("target")) == "8888" for p in service.get("ports", [])))
                self.assertEqual(publisher["ports"][0]["host_ip"], expected)
            self.assertIn("searxng-x402-egress-bridge", model(mode, "docker", False, down=True)["services"])

    def test_make_helper_failure_is_fatal_and_nonstack_workflows_ignore_key(self):
        env = _wrapper_neutral_environment() | {"SEARXNG_X402_PRIVKEY": "secret-canary"}
        for goal, success in (("up-lite", False), ("help", True), ("x402-wallet", True), ("test-patch-images", True)):
            result = subprocess.run(["make", "-n", goal, "ENV_FILE=.env.wrapper.example", "PRIVATE_ONYX_DOCKER_ENGINE_MODE=rootful", "PRIVATE_ONYX_DOCKER_GATEWAY_MODE=isolated"], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, success)
            self.assertNotIn("secret-canary", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
