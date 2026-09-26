"""Make-selected feature models with synthetic configuration only."""
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
          host_os="Darwin", engine_mode="rootful", vpn=False, proxy=""):
    env = _wrapper_neutral_environment() | SECRET_ENV
    env.pop("SEARXNG_X402_PRIVKEY", None)
    if enabled:
        env["SEARXNG_X402_PRIVKEY"] = KEY
    if host is not None:
        env["SEARXNG_HOST"] = host
    with tempfile.TemporaryDirectory() as directory:
        configuration = Path(directory) / "settings.env"
        configuration.write_text((ROOT / ".env.wrapper.example").read_text())
        command = ["make", "-s", "--no-print-directory", "-f", "Makefile", "-f", "-", "model",
                   "MAKECMDGOALS=" + ("down-" if down else "up-") + mode,
                   "ENV_FILE=" + str(configuration), "CONTAINER_BIN=" + engine,
                   "PRIVATE_ONYX_DOCKER_ENGINE_MODE=" + engine_mode, "PRIVATE_ONYX_DOCKER_GATEWAY_MODE=isolated",
                   "HOST_OS=" + host_os, "DOCKER_SOCK_PATH=/tmp/fixture.sock",
                   "MYST_VPN_ENABLED=" + str(vpn).lower(), "EGRESS_UPSTREAM_PROXY_URL=" + proxy,
                   "ONYX_CODE_INTERPRETER_ENABLE_NETWORK=" + str(executor).lower(),
                   "TOR_EGRESS_ENABLED=" + str(tor).lower(), "TOR_ONION_SERVICE_ENABLED=" + str(onion).lower()]
        variable = mode.upper() + ("_DOWN_FILES" if down else "_FILES")
        recipe = 'model:\n\t@COMPOSE_FILE=$(' + variable + ') ' + shlex.join([
            *_compose_command(), "--env-file", "stack.versions.env", "--env-file", str(configuration),
            "config", "--no-env-resolution", "--format", "json",
        ]) + "\n"
        result = subprocess.run(command, input=recipe, env=env, cwd=ROOT, text=True, capture_output=True, check=True)
        return json.loads(result.stdout)


class ComposeTests(unittest.TestCase):
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
