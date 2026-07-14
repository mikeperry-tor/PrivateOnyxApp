from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SECRET_ENV = {
    "SEARXNG_SECRET": "test",
    "USER_AUTH_SECRET": "test",
    "CRW_ONYX_API_KEY": "test",
    "MINIO_ROOT_USER": "test",
    "MINIO_ROOT_PASSWORD": "test",
    "S3_AWS_ACCESS_KEY_ID": "test",
    "S3_AWS_SECRET_ACCESS_KEY": "test",
    "ONYX_PUBLIC_ROUTE_BROKER_CREDENTIAL": "0" * 64,
    "ONYX_HOST_ROUTE_BROKER_CREDENTIAL": "1" * 64,
}


def _compose_model(mode: str) -> dict:
    command = [
        "docker",
        "compose",
        "--env-file",
        "stack.versions.env",
        "--env-file",
        ".env.wrapper.example",
        "-f",
        "docker-compose.yaml",
        "-f",
        f"docker-compose.{mode}.yml",
        "config",
        "--no-env-resolution",
        "--format",
        "json",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, **SECRET_ENV},
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


@unittest.skipUnless(shutil.which("docker"), "docker compose is required")
class OnyxNetworkIsolationComposeTests(unittest.TestCase):
    def test_lite_and_full_application_services_are_internal_only(self) -> None:
        expected = {
            "api_server": {
                "onyx-frontend",
                "onyx-backend",
                "onyx-data",
                "onyx-public-egress",
                "onyx-host-egress",
                "onyx-teep",
            },
            "web_server": {"onyx-frontend"},
            "nginx": {"onyx-frontend"},
            "code-interpreter": {"onyx-backend"},
        }
        for mode in ("lite", "full"):
            model = _compose_model(mode)
            for service_name, networks in expected.items():
                service = model["services"][service_name]
                self.assertNotIn("network_mode", service)
                self.assertEqual(set(service.get("networks", {})), networks)
                for network in networks:
                    self.assertTrue(model["networks"][network].get("internal"))

    def test_public_and_host_policy_boundaries_are_distinct(self) -> None:
        model = _compose_model("full")
        services = model["services"]
        self.assertEqual(
            set(services["onyx-public-egress-policy"]["networks"]),
            {"onyx-public-policy-upstream", "onyx-public-broker"},
        )
        self.assertEqual(
            set(services["onyx-host-egress-policy"]["networks"]),
            {"onyx-host-policy-upstream", "onyx-host-broker"},
        )
        self.assertEqual(
            set(services["onyx-public-egress-bridge"]["networks"]),
            {"onyx-public-egress", "onyx-public-policy-upstream"},
        )
        self.assertEqual(
            set(services["onyx-host-egress-bridge"]["networks"]),
            {"onyx-host-egress", "onyx-host-policy-upstream"},
        )
        self.assertNotIn("network_mode", services["onyx-public-egress-policy"])
        self.assertNotIn("network_mode", services["onyx-host-egress-policy"])
        self.assertEqual(
            services["onyx-public-egress-policy"]["environment"][
                "EGRESS_ROUTE_BROKER_CREDENTIAL"
            ],
            "0" * 64,
        )
        self.assertEqual(
            services["onyx-host-egress-policy"]["environment"][
                "EGRESS_ROUTE_BROKER_CREDENTIAL"
            ],
            "1" * 64,
        )
        self.assertNotEqual(
            services["onyx-public-egress-policy"]["environment"][
                "EGRESS_ROUTE_BROKER_HOST"
            ],
            services["onyx-host-egress-policy"]["environment"][
                "EGRESS_ROUTE_BROKER_HOST"
            ],
        )

    def test_brokers_and_bridges_are_narrow_and_hardened(self) -> None:
        model = _compose_model("full")
        services = model["services"]
        for route_class in ("public", "host"):
            broker = services[f"onyx-{route_class}-route-broker"]
            self.assertEqual(broker["user"], "65534:65534")
            self.assertEqual(
                broker["environment"]["EGRESS_BROKER_HOST"],
                f"onyx-{route_class}-route-broker",
            )
            bridge = services[f"onyx-{route_class}-egress-bridge"]
            self.assertEqual(bridge["user"], "65534:65534")
            self.assertTrue(bridge["read_only"])
            self.assertEqual(bridge["cap_drop"], ["ALL"])
            self.assertIn("no-new-privileges:true", bridge["security_opt"])
            self.assertEqual(
                bridge["sysctls"],
                {
                    "net.ipv4.ip_forward": "0",
                    "net.ipv6.conf.all.forwarding": "0",
                },
            )

        netns_networks = set(services["netns-holder"]["networks"])
        self.assertIn("onyx-public-broker", netns_networks)
        self.assertIn("onyx-host-broker", netns_networks)
        myst_sysctls = services["myst-client"].get("sysctls", {})
        self.assertEqual(str(myst_sysctls.get("net.ipv4.ip_forward")), "0")
        self.assertEqual(
            str(myst_sysctls.get("net.ipv6.conf.all.forwarding")), "0"
        )
        for service_name in ("api_server", "background", "code-interpreter"):
            networks = set(services[service_name].get("networks", {}))
            self.assertFalse(
                networks
                & {
                    "routing-uplink",
                    "onyx-public-broker",
                    "onyx-host-broker",
                    "onyx-public-policy-upstream",
                    "onyx-host-policy-upstream",
                    "browser-egress",
                    "executor-policy-upstream",
                }
            )

    def test_host_publish_edge_contains_only_fixed_publishers(self) -> None:
        for mode in ("lite", "full"):
            model = _compose_model(mode)
            services = model["services"]
            expected = {"host-webui-publisher", "host-searxng-proxy"}
            if mode == "full":
                expected.add("host-doc-display-publisher")
                self.assertFalse(services["doc-drop-web"].get("ports"))
            attached = {
                name
                for name, service in services.items()
                if "host-publish" in service.get("networks", {})
            }
            self.assertEqual(attached, expected)
            self.assertFalse(services["nginx"].get("ports"))
            self.assertEqual(
                model["networks"]["host-publish"]["driver_opts"],
                {
                    "com.docker.network.bridge.enable_ip_masquerade": "false",
                    "com.docker.network.bridge.enable_icc": "false",
                },
            )
            for name in expected:
                service = services[name]
                self.assertEqual(service["user"], "65534:65534")
                self.assertTrue(service["read_only"])
                self.assertEqual(service["cap_drop"], ["ALL"])

    def test_obscura_mcp_and_shared_namespace_artifacts_are_absent(self) -> None:
        model = _compose_model("full")
        service_names = set(model["services"])
        self.assertFalse(
            service_names
            & {
                "obscura-mcp",
                "obscura-mcp-gateway",
                "obscura-mcp-egress-bridge",
                "mcp-browser-egress-proxy",
                "host-web-proxy",
                "host-doc-drop-web-proxy",
                "onyx-helper-egress-proxy",
            }
        )
        for service in ("api_server", "background", "web_server", "nginx", "code-interpreter"):
            self.assertNotEqual(
                model["services"][service].get("network_mode"),
                "service:netns-holder",
            )
        helper_env = (ROOT / "onyx" / "helper-egress.env").read_text()
        self.assertNotIn("host.docker.internal", helper_env)
        self.assertNotIn("obscura-mcp", helper_env)
        tailscale_entrypoint = (ROOT / "tailscale" / "entrypoint.sh").read_text()
        self.assertNotIn("host-web-proxy", tailscale_entrypoint)


if __name__ == "__main__":
    unittest.main()
