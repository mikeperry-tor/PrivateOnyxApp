from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SECRET_ENV = {
    # Production Make invocations export the content-derived image name before
    # Compose interpolation. Direct Compose-model tests supply a inert value.
    "SEARXNG_WRAPPER_IMAGE": "local/private-onyx-searxng:test-model",
    "SEARXNG_SECRET": "test",
    "USER_AUTH_SECRET": "test",
    "MINIO_ROOT_USER": "test",
    "MINIO_ROOT_PASSWORD": "test",
    "S3_AWS_ACCESS_KEY_ID": "test",
    "S3_AWS_SECRET_ACCESS_KEY": "test",
    "PODMAN_DOC_SERVER_PORT": "18091",
}


def _compose_model(
    mode: str,
    *extra_files: str,
    profiles: tuple[str, ...] = (),
    env_overrides: dict[str, str] | None = None,
) -> dict:
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
    ]
    for compose_file in extra_files:
        command.extend(("-f", compose_file))
    for profile in profiles:
        command.extend(("--profile", profile))
    command.extend([
        "config",
        "--no-env-resolution",
        "--format",
        "json",
    ])
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, **SECRET_ENV, **(env_overrides or {})},
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _make_compose_files(
    vpn_enabled: bool,
    container_bin: str = "docker",
    **overrides: str,
) -> str:
    override_args = [f"{name}={value}" for name, value in overrides.items()]
    completed = subprocess.run(
        [
            "make",
            "-pn",
            "ENV_FILE=.env.wrapper.example",
            f"MYST_VPN_ENABLED={'true' if vpn_enabled else 'false'}",
            f"CONTAINER_BIN={container_bin}",
            *override_args,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    prefix = "FULL_FILES := "
    for line in completed.stdout.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix)
    raise AssertionError("FULL_FILES is absent from the Make database")


@unittest.skipUnless(shutil.which("docker"), "docker compose is required")
class OnyxNetworkIsolationComposeTests(unittest.TestCase):
    def test_core_startup_does_not_wait_for_optional_browsing(self) -> None:
        lite = _compose_model("lite")
        self.assertFalse(lite["services"]["api_server"].get("depends_on"))

        full = _compose_model("full")
        self.assertEqual(
            set(full["services"]["api_server"].get("depends_on", {})),
            {"cache", "local-embedding-shim", "minio", "opensearch", "relational_db"},
        )
        self.assertEqual(
            set(full["services"]["background"].get("depends_on", {})),
            {"cache", "local-embedding-shim", "opensearch", "relational_db"},
        )
        optional = {
            "crw",
            "searxng-core",
            "obscura",
            "onyx-public-egress-bridge",
        }
        for service_name in ("api_server", "background"):
            self.assertFalse(
                optional & set(full["services"][service_name].get("depends_on", {}))
            )

    def test_removed_valkey_and_legacy_crawler_credential_stay_absent(self) -> None:
        model = _compose_model("full")
        self.assertNotIn("searxng-valkey", model["services"])
        self.assertNotIn("searxng-valkey", model["networks"])
        self.assertNotIn(
            "CRW_ONYX_API_KEY",
            model["services"]["searxng-core"]["environment"],
        )
        self.assertNotIn("CRW_ONYX_API_KEY", (ROOT / "Makefile").read_text())
        self.assertNotIn("ONYX_MODEL_SERVER_IMAGE", (ROOT / "Makefile").read_text())
        self.assertNotIn("VALKEY_IMAGE", (ROOT / "stack.versions.env").read_text())
        self.assertFalse((ROOT / "searxng" / "engines" / "_crw.py").exists())

    def test_makefile_selects_autoheal_only_for_vpn_models(self) -> None:
        vpn_files = _make_compose_files(vpn_enabled=True)
        self.assertIn("docker-compose.vpn-autoheal.yml", vpn_files)

        no_vpn_files = _make_compose_files(vpn_enabled=False)
        self.assertNotIn("docker-compose.vpn-autoheal.yml", no_vpn_files)

        podman_vpn_files = _make_compose_files(
            vpn_enabled=True, container_bin="podman"
        )
        self.assertIn("docker-compose.vpn-autoheal.yml", podman_vpn_files)
        self.assertIn("docker-compose.podman-vpn.yml", podman_vpn_files)

        podman_no_vpn_files = _make_compose_files(
            vpn_enabled=False, container_bin="podman"
        )
        self.assertNotIn("docker-compose.vpn-autoheal.yml", podman_no_vpn_files)
        self.assertNotIn("docker-compose.podman-vpn.yml", podman_no_vpn_files)

    def test_makefile_selects_every_optional_network_layer(self) -> None:
        default_files = _make_compose_files(vpn_enabled=True)
        optional_files = (
            "docker-compose.proxy.yml",
            "docker-compose.code-interpreter-network.yml",
            "docker-compose.teep-vpn.yml",
            "docker-compose.tailscale-vpn.yml",
        )
        for compose_file in optional_files:
            self.assertNotIn(compose_file, default_files)

        enabled_files = _make_compose_files(
            vpn_enabled=True,
            EGRESS_UPSTREAM_PROXY_URL="socks5h://host.docker.internal:9150",
            ONYX_CODE_INTERPRETER_ENABLE_NETWORK="true",
            TEEP_ROUTE_THROUGH_MYST_VPN="true",
            TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN="true",
        )
        for compose_file in optional_files:
            self.assertIn(compose_file, enabled_files)

    def test_podman_override_uses_user_owned_tmpfs_without_docker_uid_options(self) -> None:
        model = _compose_model(
            "lite", "docker-compose.podman.yml", profiles=("tailscale",)
        )
        tmpfs = model["services"]["tailscale-frontend-gateway"]["tmpfs"]
        self.assertEqual(
            tmpfs,
            ["/var/cache/nginx:U,mode=0755", "/var/run:U,mode=0755", "/tmp"],
        )
        self.assertNotIn("uid=", " ".join(tmpfs))
        self.assertNotIn("gid=", " ".join(tmpfs))

    def test_podman_override_reuses_docker_storage_and_gates_database_consumers(self) -> None:
        for mode in ("lite", "full"):
            extra = ["docker-compose.podman.yml"]
            if mode == "full":
                extra.append("docker-compose.podman-full.yml")
            model = _compose_model(mode, *extra)
            services = model["services"]
            postgres = services["relational_db"]
            self.assertEqual(postgres["userns_mode"], "keep-id:uid=70,gid=70")
            self.assertEqual(postgres["user"], "70:70")
            self.assertEqual(postgres["entrypoint"], ["postgres"])
            self.assertEqual(
                postgres["volumes"][0]["source"],
                str(ROOT / "docker-data/postgres"),
            )
            self.assertEqual(
                services["api_server"]["depends_on"]["relational_db"]["condition"],
                "service_healthy",
            )
            if mode == "full":
                self.assertEqual(
                    services["background"]["depends_on"]["relational_db"]["condition"],
                    "service_healthy",
                )
                opensearch = services["opensearch"]
                self.assertEqual(
                    opensearch["userns_mode"], "keep-id:uid=1000,gid=1000"
                )
                self.assertEqual(
                    opensearch["volumes"][0]["source"],
                    str(ROOT / "docker-data/opensearch"),
                )
                doc_relay = services["doc-drop-web"]
                self.assertFalse(doc_relay.get("volumes"))
                self.assertEqual(doc_relay["user"], "65534:65534")
                self.assertTrue(doc_relay["read_only"])
                self.assertEqual(doc_relay["cap_drop"], ["ALL"])
                self.assertEqual(
                    doc_relay["command"],
                    [
                        "tcp-listen:8091,fork,reuseaddr",
                        "tcp-connect:host.containers.internal:18091",
                    ],
                )
                self.assertEqual(
                    set(doc_relay["networks"]),
                    {"doc-drop-route", "doc-drop-publish", "podman-doc-host-uplink"},
                )
                self.assertFalse(
                    model["networks"]["podman-doc-host-uplink"].get("internal", False)
                )
                joined = {
                    name
                    for name, service in services.items()
                    if "podman-doc-host-uplink" in service.get("networks", {})
                }
                self.assertEqual(joined, {"doc-drop-web"})
                self.assertNotIn("podman-rag-docs", model.get("volumes", {}))

    def test_makefile_uses_only_the_two_core_podman_storage_overlays(self) -> None:
        files = _make_compose_files(vpn_enabled=False, container_bin="podman")
        self.assertIn("docker-compose.podman.yml", files)
        self.assertIn("docker-compose.podman-full.yml", files)
        self.assertNotIn("podman-docker-postgres", files)
        self.assertNotIn("podman-docker-opensearch", files)
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertNotIn("PODMAN_SHARE_DOCKER_POSTGRES", makefile)
        self.assertNotIn("PODMAN_SHARE_DOCKER_OPENSEARCH", makefile)

    def test_autoheal_is_present_only_in_vpn_models(self) -> None:
        for mode in ("lite", "full"):
            no_vpn_model = _compose_model(mode)
            self.assertNotIn("autoheal", no_vpn_model["services"])

            vpn_model = _compose_model(mode, "docker-compose.vpn-autoheal.yml")
            autoheal = vpn_model["services"]["autoheal"]
            self.assertEqual(autoheal["network_mode"], "none")
            self.assertEqual(
                autoheal["volumes"][0]["source"], "/var/run/docker.sock"
            )
            self.assertIn("myst-client", autoheal["depends_on"])
            self.assertEqual(autoheal["environment"]["AUTOHEAL_INTERVAL"], "60")
            self.assertTrue(autoheal["healthcheck"]["disable"])

    def test_sleepy_health_inventory_and_aggregate_ownership(self) -> None:
        model = _compose_model(
            "full",
            "docker-compose.vpn-autoheal.yml",
            "docker-compose.code-interpreter-network.yml",
            "docker-compose.teep-vpn.yml",
            profiles=("tailscale",),
        )
        services = model["services"]
        disabled = {"searxng-core", "obscura", "doc-drop-web", "cache", "opensearch", "autoheal"}
        absent = {
            "onyx-public-egress-proxy",
            "onyx-host-egress-proxy",
            "host-searxng-proxy",
            "host-webui-publisher",
            "host-doc-display-publisher",
            "host-teep-proxy",
            "tailscale-funnel",
        }
        for name in disabled:
            self.assertTrue(services[name]["healthcheck"]["disable"], name)
        for name in absent:
            self.assertNotIn("healthcheck", services[name], name)

        retained = {
            name: service["healthcheck"]
            for name, service in services.items()
            if service.get("healthcheck") and not service["healthcheck"].get("disable")
        }
        self.assertEqual(retained["myst-client"]["interval"], "1m0s")
        self.assertEqual(retained["myst-client"]["start_interval"], "5s")
        self.assertEqual(retained["myst-client"]["retries"], 2)
        for name, health in retained.items():
            if name == "myst-client":
                continue
            self.assertEqual(health["interval"], "10m0s", name)
            self.assertEqual(health["start_interval"], "5s", name)
            self.assertEqual(health["retries"], 1, name)

        self.assertIn("/json/version", " ".join(retained["obscura-cdp-gateway"]["test"]))
        self.assertIn("/health", " ".join(retained["local-embedding-shim"]["test"]))
        self.assertNotIn("/ready", " ".join(retained["local-embedding-shim"]["test"]))
        for gateway, origin in (
            ("obscura-cdp-gateway", "obscura"),
            ("searxng-service-gateway", "searxng-core"),
            ("doc-drop-route-gateway", "doc-drop-web"),
        ):
            self.assertEqual(
                services[gateway]["depends_on"][origin]["condition"],
                "service_started",
            )

        commands = "\n".join(" ".join(health["test"]) for health in retained.values())
        for forbidden in ("example.com", "--check-ready", "/ready"):
            self.assertNotIn(forbidden, commands)

    def test_full_storage_and_background_power_defaults(self) -> None:
        services = _compose_model("full")["services"]
        opensearch = services["opensearch"]
        self.assertEqual(opensearch["environment"]["OPENSEARCH_JAVA_OPTS"], "-Xms1g -Xmx1g")
        self.assertEqual(
            opensearch["environment"]["DISABLE_PERFORMANCE_ANALYZER_AGENT_CLI"],
            "true",
        )
        self.assertEqual(services["minio"]["environment"]["MINIO_SCANNER_SPEED"], "slowest")
        background = services["background"]
        self.assertEqual(background["environment"]["PROMETHEUS_METRICS_ENABLED"], "false")
        self.assertEqual(background["environment"]["ONYX_SLACK_BOT_ENABLED"], "false")
        self.assertEqual(background["environment"]["ONYX_DISCORD_BOT_ENABLED"], "false")
        self.assertEqual(
            background["entrypoint"],
            ["python", "-S", "/app/wrapper-background-entrypoint.py"],
        )

    def test_lite_and_full_application_services_are_internal_only(self) -> None:
        expected = {
            "api_server": {
                "onyx-frontend",
                "onyx-backend",
                "onyx-data",
                "onyx-public-egress",
                "onyx-host-egress",
                "onyx-teep",
                "onyx-obscura-control",
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

    def test_public_and_host_final_hop_listeners_are_distinct(self) -> None:
        model = _compose_model("full")
        services = model["services"]
        self.assertEqual(
            set(services["onyx-public-egress-bridge"]["networks"]),
            {"onyx-public-egress", "onyx-public-policy-upstream"},
        )
        self.assertEqual(
            set(services["onyx-host-egress-bridge"]["networks"]),
            {"onyx-host-egress", "onyx-host-policy-upstream"},
        )
        for route_class, port in (("public", "3132"), ("host", "3133")):
            proxy = services[f"onyx-{route_class}-egress-proxy"]
            self.assertEqual(proxy["network_mode"], "service:netns-holder")
            self.assertEqual(proxy["environment"]["EGRESS_ROUTE_CLASS"], route_class)
            self.assertEqual(proxy["environment"]["EGRESS_PROXY_PORT"], port)
        self.assertEqual(
            set(
                services["onyx-public-egress-proxy"]["environment"][
                    "EGRESS_PROXY_ALLOWED_CLIENT_HOSTS"
                ].split(",")
            ),
            {"onyx-public-egress-bridge", "obscura-egress-bridge"},
        )
        self.assertEqual(
            services["onyx-host-egress-proxy"]["environment"][
                "EGRESS_PROXY_ALLOWED_CLIENT_HOSTS"
            ],
            "onyx-host-egress-bridge",
        )

        public = services["onyx-public-egress-proxy"]["environment"]
        host = services["onyx-host-egress-proxy"]["environment"]
        self.assertEqual(public["ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS"], "false")
        self.assertNotIn("EGRESS_PROXY_TRUSTED_INTERNAL_DESTINATIONS", public)
        self.assertEqual(
            host["EGRESS_PROXY_TRUSTED_INTERNAL_DESTINATIONS"],
            "doc-drop-web:8091",
        )

    def test_integration_lan_option_reaches_only_host_policy_and_route_owner(
        self,
    ) -> None:
        model = _compose_model(
            "full",
            "docker-compose.code-interpreter-network.yml",
            env_overrides={"ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS": "true"},
        )
        services = model["services"]
        option = "ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS"

        self.assertEqual(
            services["onyx-host-egress-proxy"]["environment"][option], "true"
        )
        self.assertEqual(
            services["myst-client"]["environment"][option], "true"
        )
        self.assertEqual(
            services["onyx-public-egress-proxy"]["environment"][option], "false"
        )
        self.assertNotIn(option, services["code-interpreter"]["environment"])

    def test_every_restricted_listener_has_an_explicit_route_class(self) -> None:
        model = _compose_model(
            "full", "docker-compose.code-interpreter-network.yml"
        )
        services = model["services"]
        expected = {
            "onyx-public-egress-proxy": (
                "public",
                {"onyx-public-egress-bridge", "obscura-egress-bridge", "executor-egress-bridge"},
            ),
            "onyx-host-egress-proxy": ("host", {"onyx-host-egress-bridge"}),
        }
        ports: set[str] = set()
        for service_name, (route_class, bridge_names) in expected.items():
            service = services[service_name]
            environment = service["environment"]
            self.assertEqual(environment["EGRESS_ROUTE_CLASS"], route_class)
            self.assertEqual(
                set(environment["EGRESS_PROXY_ALLOWED_CLIENT_HOSTS"].split(",")),
                bridge_names,
            )
            self.assertEqual(service["network_mode"], "service:netns-holder")
            self.assertEqual(service["user"], "65534:65534")
            port = environment["EGRESS_PROXY_PORT"]
            self.assertNotIn(port, ports)
            ports.add(port)

        for service_name in expected:
            if service_name != "onyx-host-egress-proxy":
                environment = services[service_name]["environment"]
                self.assertEqual(environment["ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS"], "false")
                self.assertNotIn(
                    "EGRESS_PROXY_TRUSTED_INTERNAL_DESTINATIONS", environment
                )

    def test_optional_executor_route_is_isolated_and_hardened(self) -> None:
        model = _compose_model(
            "full", "docker-compose.code-interpreter-network.yml"
        )
        services = model["services"]
        bridge = services["executor-egress-bridge"]
        self.assertEqual(
            set(bridge["networks"]),
            {"executor-egress", "executor-policy-upstream"},
        )
        self.assertEqual(
            bridge["command"],
            [
                "tcp-listen:3128,fork,reuseaddr",
                "tcp-connect:myst-client:3132",
            ],
        )
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
        self.assertNotIn("ports", bridge)
        self.assertEqual(
            set(services["code-interpreter"]["networks"]), {"onyx-backend"}
        )
        self.assertNotIn(
            "EGRESS_PROXY_TRUSTED_INTERNAL_DESTINATIONS",
            services["onyx-public-egress-proxy"]["environment"],
        )
        self.assertNotIn("executor-egress-proxy", services)

    def test_optional_overlay_matrix_preserves_application_isolation(self) -> None:
        model = _compose_model(
            "full",
            "docker-compose.proxy.yml",
            "docker-compose.code-interpreter-network.yml",
            "docker-compose.teep-vpn.yml",
            "docker-compose.tailscale-vpn.yml",
            profiles=("tailscale",),
        )
        services = model["services"]
        for service_name in (
            "api_server",
            "background",
            "web_server",
            "nginx",
            "code-interpreter",
            "tailscale-frontend-gateway",
        ):
            service = services[service_name]
            self.assertNotEqual(
                service.get("network_mode"), "service:netns-holder"
            )
            for network in service.get("networks", {}):
                self.assertTrue(model["networks"][network].get("internal"))

        self.assertEqual(
            services["tailscale-funnel"]["network_mode"],
            "service:netns-holder",
        )
        self.assertEqual(
            services["tailscale-funnel"]["environment"][
                "TAILSCALE_FUNNEL_TARGET_HOST"
            ],
            "tailscale-frontend-gateway",
        )
        self.assertEqual(services["teep"]["network_mode"], "service:netns-holder")
        self.assertFalse(services["teep"].get("ports"))
        self.assertIn(
            "executor-policy-upstream", services["netns-holder"]["networks"]
        )
        self.assertIn("tailscale-ingress", services["netns-holder"]["networks"])
        self.assertIn("teep-vpn-ingress", services["netns-holder"]["networks"])

    def test_final_hop_proxies_and_bridges_are_narrow_and_hardened(self) -> None:
        model = _compose_model("full")
        services = model["services"]
        for route_class in ("public", "host"):
            proxy = services[f"onyx-{route_class}-egress-proxy"]
            self.assertEqual(proxy["user"], "65534:65534")
            self.assertTrue(proxy["read_only"])
            self.assertEqual(proxy["cap_drop"], ["ALL"])
            self.assertIn("no-new-privileges:true", proxy["security_opt"])
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
        self.assertIn("onyx-public-policy-upstream", netns_networks)
        self.assertIn("onyx-host-policy-upstream", netns_networks)
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
                    "onyx-public-policy-upstream",
                    "onyx-host-policy-upstream",
                    "browser-egress",
                    "executor-policy-upstream",
                }
            )

    def test_myst_receives_proxy_url_for_exact_rfc1918_endpoint_route(self) -> None:
        model = _compose_model("full")
        self.assertIn(
            "EGRESS_UPSTREAM_PROXY_URL",
            model["services"]["myst-client"]["environment"],
        )
        entrypoint = (ROOT / "myst" / "myst-client-entrypoint.sh").read_text()
        bootstrap = entrypoint.split("# Optional LAN access:", 1)[0]

        def exempt_cidrs(proxy_url: str) -> str:
            result = subprocess.run(
                [
                    "sh",
                    "-c",
                    bootstrap + '\nprintf "%s\\n" "${MYST_ROUTE_EXEMPT_CIDRS:-}"',
                ],
                check=True,
                capture_output=True,
                text=True,
                env={"EGRESS_UPSTREAM_PROXY_URL": proxy_url},
            )
            return result.stdout.splitlines()[-1] if result.stdout else ""

        self.assertEqual(
            exempt_cidrs("socks5h://user:secret@192.168.1.20:1080"),
            "192.168.1.20/32",
        )
        self.assertEqual(exempt_cidrs("https://93.184.216.34:8443"), "")
        self.assertEqual(exempt_cidrs("socks5h://proxy.internal:1080"), "")
        self.assertEqual(exempt_cidrs("ftp://192.168.1.20:21"), "")
        self.assertEqual(exempt_cidrs("https://192.168.1.20:70000"), "")

    def test_myst_default_route_parser_is_not_positional(self) -> None:
        entrypoint = (ROOT / "myst" / "myst-client-entrypoint.sh").read_text()
        route_setup = entrypoint.split(
            "# Capture the container-engine bridge gateway", 1
        )[1].split("# Start daemon in consumer-only mode", 1)[0]
        self.assertIn('$i == "via"', route_setup)
        self.assertIn('$i == "dev"', route_setup)
        self.assertIn('DOCKER_BRIDGE_DEV="eth0"', route_setup)
        self.assertNotIn("print $3", route_setup)
        self.assertNotIn("print $5", route_setup)

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

    def test_vpn_routed_teep_publisher_is_fixed_and_hardened(self) -> None:
        model = _compose_model("full", "docker-compose.teep-vpn.yml")
        publisher = model["services"]["host-teep-proxy"]
        self.assertEqual(set(publisher["networks"]), {"routing-uplink"})
        self.assertEqual(publisher["user"], "65534:65534")
        self.assertTrue(publisher["read_only"])
        self.assertEqual(publisher["cap_drop"], ["ALL"])
        self.assertIn("no-new-privileges:true", publisher["security_opt"])
        self.assertEqual(
            publisher["sysctls"],
            {
                "net.ipv4.ip_forward": "0",
                "net.ipv6.conf.all.forwarding": "0",
            },
        )
        self.assertEqual(
            publisher["command"],
            [
                "tcp-listen:8337,fork,reuseaddr",
                "tcp-connect:myst-client:8337",
            ],
        )

    def test_doc_drop_uses_exact_host_policy_gateway(self) -> None:
        model = _compose_model("full")
        services = model["services"]
        gateway = services["doc-drop-route-gateway"]
        self.assertEqual(
            set(gateway["networks"]),
            {"doc-drop-route", "onyx-host-policy-upstream"},
        )
        self.assertEqual(
            set(services["doc-drop-web"]["networks"]),
            {"doc-drop-publish", "doc-drop-route"},
        )
        self.assertNotIn("onyx-backend", services["doc-drop-web"]["networks"])
        self.assertTrue(model["networks"]["doc-drop-route"]["internal"])
        self.assertEqual(gateway["user"], "65534:65534")
        self.assertTrue(gateway["read_only"])
        self.assertEqual(gateway["cap_drop"], ["ALL"])
        self.assertFalse(gateway.get("ports"))
        self.assertEqual(
            services["onyx-host-egress-proxy"]["environment"][
                "EGRESS_PROXY_TRUSTED_INTERNAL_DESTINATIONS"
            ],
            "doc-drop-web:8091",
        )
        background_env = services["background"]["environment"]
        self.assertEqual(
            background_env["ONYX_WEB_CONNECTOR_INTERNAL_BASE_URL"],
            "http://doc-drop-web:8091/",
        )
        background_volumes = services["background"]["volumes"]
        self.assertIn(
            {
                "type": "bind",
                "source": str(
                    ROOT
                    / "onyx/patches/sitecustomize_background/sitecustomize.py"
                ),
                "target": "/app/sitecustomize.py",
                "read_only": True,
                "bind": {},
            },
            background_volumes,
        )
        self.assertIn(
            {
                "type": "bind",
                "source": str(ROOT / "onyx/patches/shared/wrapper_env_patches.py"),
                "target": "/app/wrapper_env_patches.py",
                "read_only": True,
                "bind": {},
            },
            background_volumes,
        )

    def test_open_url_document_limit_is_not_an_indexing_setting(self) -> None:
        model = _compose_model("full")
        services = model["services"]
        api_environment = services["api_server"]["environment"]
        background_environment = services["background"]["environment"]

        self.assertEqual(
            api_environment["ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB"], "20"
        )
        self.assertNotIn(
            "ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB", background_environment
        )
        self.assertNotIn("onyx-obscura-control", services["background"]["networks"])

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
                "onyx-public-egress-policy",
                "onyx-host-egress-policy",
                "onyx-public-route-broker",
                "onyx-host-route-broker",
                "browser-egress-proxy",
                "executor-egress-proxy",
                "searxng-valkey",
                "inference_model_server",
                "indexing_model_server",
            }
        )
        self.assertFalse((ROOT / "crw" / "route_broker.py").exists())
        makefile = (ROOT / "Makefile").read_text()
        self.assertNotIn("ROUTE_BROKER", makefile)
        self.assertNotIn("onyx-public-broker", model["networks"])
        self.assertNotIn("onyx-host-broker", model["networks"])
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
