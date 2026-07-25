from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SECRET_ENV = {
    # Production Make invocations export the content-derived image name before
    # Compose interpolation. Direct Compose-model tests supply a inert value.
    "SEARXNG_WRAPPER_IMAGE": "local/private-onyx-searxng:test-model",
    "PYTHON_EXECUTOR_IMAGE": "local/private-onyx-python-executor:test-model",
    "TOR_IMAGE": "local/private-onyx-tor:test-model",
    "SEARXNG_SECRET": "test",
    "USER_AUTH_SECRET": "test",
    "MINIO_ROOT_USER": "test",
    "MINIO_ROOT_PASSWORD": "test",
    "S3_AWS_ACCESS_KEY_ID": "test",
    "S3_AWS_SECRET_ACCESS_KEY": "test",
    "PODMAN_DOC_SERVER_PORT": "18091",
}


def _wrapper_neutral_environment() -> dict[str, str]:
    env = dict(os.environ)
    example = (ROOT / ".env.wrapper.example").read_text(encoding="utf-8")
    for raw_line in example.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = line.split("=", 1)[0].strip()
        if name.isidentifier():
            env.pop(name, None)
    return env


@lru_cache(maxsize=1)
def _compose_command() -> tuple[str, ...]:
    candidates = (
        ("docker", "compose"),
        ("podman", "compose"),
        ("docker-compose",),
    )
    rejected: list[str] = []
    for candidate in candidates:
        if shutil.which(candidate[0]) is None:
            continue
        inspected = subprocess.run(
            [*candidate, "config", "--help"],
            cwd=ROOT,
            env=_wrapper_neutral_environment(),
            check=False,
            capture_output=True,
            text=True,
        )
        if inspected.returncode == 0 and "--no-env-resolution" in inspected.stdout:
            return candidate
        rejected.append(" ".join(candidate))
    raise RuntimeError(
        "no Compose frontend with `config --no-env-resolution` was found; "
        "Docker Compose 2.35.0+ is required to render test models without "
        "reading service env files (checked: "
        + (", ".join(rejected) or "no installed frontends")
        + ")"
    )


def _compose_model(
    mode: str,
    *extra_files: str,
    profiles: tuple[str, ...] = (),
    env_overrides: dict[str, str] | None = None,
    wrapper_env_file: str = ".env.wrapper.example",
) -> dict:
    command = [
        *_compose_command(),
        "--env-file",
        "stack.versions.env",
        "--env-file",
        wrapper_env_file,
        "-f",
        "docker-compose.yaml",
        "-f",
        f"compose_overlays/docker-compose.{mode}.yml",
    ]
    for compose_file in extra_files:
        command.extend(("-f", f"compose_overlays/{compose_file}"))
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
        env={**_wrapper_neutral_environment(), **SECRET_ENV, **(env_overrides or {})},
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
        env=_wrapper_neutral_environment(),
        check=True,
        capture_output=True,
        text=True,
    )
    prefix = "FULL_FILES := "
    for line in completed.stdout.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix)
    raise AssertionError("FULL_FILES is absent from the Make database")


class ComposeOverlayLayoutTests(unittest.TestCase):
    def test_hidden_policy_options_keep_explicit_compose_defaults(self) -> None:
        hidden_options = {
            "MYST_VPN_WIREGUARD_MTU",
            "ONYX_AGENT_PRESERVE_TOOL_RESULTS",
            "ONYX_AGENT_USE_NATIVE_REASONING",
            "ONYX_DEEP_RESEARCH_PROVIDE_CHAT_AGENT_TOOLS",
        }
        example = (ROOT / ".env.wrapper.example").read_text(encoding="utf-8")
        for option_name in hidden_options:
            with self.subTest(option_name=option_name):
                self.assertNotRegex(
                    example,
                    rf"(?m)^{re.escape(option_name)}=",
                )

        for mode in ("lite", "full"):
            with self.subTest(mode=mode):
                services = _compose_model(mode)["services"]
                self.assertEqual(
                    services["myst-client"]["environment"][
                        "MYST_VPN_WIREGUARD_MTU"
                    ],
                    "1280",
                )
                api_environment = services["api_server"]["environment"]
                self.assertEqual(
                    api_environment["ONYX_AGENT_PRESERVE_TOOL_RESULTS"], "true"
                )
                self.assertEqual(
                    api_environment["ONYX_AGENT_USE_NATIVE_REASONING"], "true"
                )
                self.assertEqual(
                    api_environment[
                        "ONYX_DEEP_RESEARCH_PROVIDE_CHAT_AGENT_TOOLS"
                    ],
                    "true",
                )

    def test_every_example_option_has_a_compose_or_host_default_owner(self) -> None:
        example_names = {
            line.split("=", 1)[0]
            for raw_line in (ROOT / ".env.wrapper.example")
            .read_text(encoding="utf-8")
            .splitlines()
            if (line := raw_line.strip())
            and not line.startswith("#")
            and "=" in line
        }
        compose_names: set[str] = set()
        for path in (
            ROOT / "docker-compose.yaml",
            *(ROOT / "compose_overlays").glob("docker-compose*.yml"),
        ):
            compose_names.update(
                re.findall(
                    r"\$\{([A-Z][A-Z0-9_]*)",
                    path.read_text(encoding="utf-8"),
                )
            )

        self.assertEqual(
            example_names - compose_names,
            {
                "CONTAINER_BIN",
                "TEEP_ROUTE_THROUGH_MYST_VPN",
                "MYST_VPN_ORDER_AMOUNT",
                "MYST_VPN_ORDER_CURRENCY",
                "MYST_VPN_ORDER_GATEWAY",
                "MYST_VPN_ORDER_COUNTRY",
                "MYST_VPN_ORDER_GATEWAY_DATA",
                "TOR_EGRESS_ENABLED",
                "TOR_ONION_SERVICE_ENABLED",
                "TOR_EXIT_COUNTRY",
                "TOR_EXIT_NODE_FINGERPRINTS",
                "ONYX_CODE_INTERPRETER_ENABLE_NETWORK",
                "TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN",
            },
        )

    def test_example_values_match_compose_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            empty_env = Path(temporary) / "empty.env"
            empty_env.write_text("", encoding="utf-8")

            cases = (
                ("lite", ()),
                ("full", ()),
                ("lite", ("docker-compose.podman.yml",)),
                (
                    "full",
                    (
                        "docker-compose.podman.yml",
                        "docker-compose.podman-full.yml",
                        "docker-compose.podman-macos-full.yml",
                    ),
                ),
            )
            for mode, extra_files in cases:
                with self.subTest(mode=mode, extra_files=extra_files):
                    expected = _compose_model(
                        mode, *extra_files, profiles=("tailscale",)
                    )
                    defaulted = _compose_model(
                        mode,
                        *extra_files,
                        profiles=("tailscale",),
                        wrapper_env_file=str(empty_env),
                    )

                    # Compose interpolation cannot express the model-specified
                    # newline inside a ${VAR:-default} expression. Keep the
                    # newline-correct example value while accepting the
                    # single-line Compose fallback when the option is absent.
                    prefix_fields = {
                        "api_server": "ASYM_QUERY_PREFIX",
                        "background": "ASYM_QUERY_PREFIX",
                        "local-embedding-shim": "SHIM_QUERY_PREFIX",
                    }
                    specified_prefix = (
                        "Instruct: Given a document query, retrieve the most "
                        "relevant chunk.\nQuery: "
                    )
                    fallback_prefix = specified_prefix.replace("\n", " ")
                    for service_name, field_name in prefix_fields.items():
                        if service_name not in expected["services"]:
                            continue
                        self.assertEqual(
                            expected["services"][service_name]["environment"][
                                field_name
                            ],
                            specified_prefix,
                        )
                        self.assertEqual(
                            defaulted["services"][service_name]["environment"][
                                field_name
                            ],
                            fallback_prefix,
                        )
                        del expected["services"][service_name]["environment"][
                            field_name
                        ]
                        del defaulted["services"][service_name]["environment"][
                            field_name
                        ]

                    self.assertEqual(defaulted, expected)

    def test_teep_placeholder_keys_are_defaults_not_forced_values(self) -> None:
        services = _compose_model(
            "lite",
            env_overrides={
                "TEEP_TINFOIL_API_KEY": "",
                "TEEP_NEARAI_API_KEY": "",
            },
        )["services"]
        teep_environment = services["teep"]["environment"]
        self.assertEqual(teep_environment["TINFOIL_API_KEY"], "")
        self.assertEqual(teep_environment["NEARAI_API_KEY"], "")

    def test_wrapper_overlays_are_colocated_below_the_root_base(self) -> None:
        expected = {
            "docker-compose.code-interpreter-network.yml",
            "docker-compose.docker-linux.yml",
            "docker-compose.full.yml",
            "docker-compose.lite.yml",
            "docker-compose.podman-full.yml",
            "docker-compose.podman-macos-full.yml",
            "docker-compose.podman.yml",
            "docker-compose.tailscale-vpn.yml",
            "docker-compose.teep-vpn.yml",
            "docker-compose.tor-egress.yml",
            "docker-compose.tor-onion-podman.yml",
            "docker-compose.tor-onion.yml",
            "docker-compose.tor-podman.yml",
            "docker-compose.tor.yml",
        }
        overlay_directory = ROOT / "compose_overlays"
        self.assertEqual(
            {path.name for path in overlay_directory.glob("docker-compose.*.yml")},
            expected,
        )
        self.assertEqual(
            {path.name for path in ROOT.glob("docker-compose*.yml")},
            set(),
        )
        self.assertTrue((ROOT / "docker-compose.yaml").is_file())


class OnyxNetworkIsolationComposeTests(unittest.TestCase):
    def test_api_health_program_survives_podman_compatibility_api(self) -> None:
        model = _compose_model("lite")
        self.assertEqual(
            model["services"]["api_server"]["healthcheck"]["test"],
            [
                "CMD-SHELL",
                "python -c \"import urllib.request; "
                "urllib.request.urlopen('http://localhost:8080/health')\"",
            ],
        )

    def test_canonical_origin_and_onion_gateway_contract(self) -> None:
        origin = "http://" + ("a" * 56) + ".onion"
        model = _compose_model(
            "lite",
            "docker-compose.tor.yml",
            "docker-compose.tor-onion.yml",
            env_overrides={"WEBUI_CANONICAL_ORIGIN": origin},
        )
        self.assertEqual(
            model["services"]["api_server"]["environment"]["WEB_DOMAIN"], origin
        )
        self.assertEqual(
            model["services"]["web_server"]["environment"]["WEB_DOMAIN"], origin
        )

        gateway = (ROOT / "tor" / "frontend-gateway.conf").read_text(
            encoding="utf-8"
        )
        self.assertIn(r'"~^[a-z2-7]{56}[.]onion$"', gateway)
        self.assertIn("proxy_set_header Host $host;", gateway)
        self.assertIn("proxy_set_header X-Forwarded-For $remote_addr;", gateway)
        self.assertIn("proxy_set_header X-Forwarded-Proto http;", gateway)
        self.assertIn('proxy_set_header X-Forwarded-Host "";', gateway)
        self.assertNotIn("$http_x_forwarded", gateway)

    def test_tor_role_models_are_structurally_exact(self) -> None:
        default = _compose_model("lite")
        self.assertNotIn("tor", default["services"])
        self.assertNotIn("tor-uplink", default["networks"])
        self.assertNotIn("tor-ingress", default["networks"])
        self.assertNotIn("tor-runtime", default.get("volumes", {}))

        egress = _compose_model(
            "lite", "docker-compose.tor.yml", "docker-compose.tor-egress.yml"
        )
        self.assertIn("tor", egress["services"])
        self.assertNotIn("tor-frontend-gateway", egress["services"])
        self.assertNotIn("tor-ingress", egress["networks"])
        self.assertIn("tor-runtime", egress["volumes"])
        self.assertEqual(
            egress["services"]["api_server"]["environment"][
                "EGRESS_ALLOW_HTTP_ONION_URLS"
            ],
            "true",
        )

        onion = _compose_model(
            "lite", "docker-compose.tor.yml", "docker-compose.tor-onion.yml"
        )
        self.assertIn("tor-frontend-gateway", onion["services"])
        self.assertIn("tor-ingress", onion["networks"])
        self.assertNotIn("tor-runtime", onion.get("volumes", {}))
        self.assertNotIn(
            "EGRESS_ALLOW_HTTP_ONION_URLS",
            onion["services"]["api_server"]["environment"],
        )
        for proxy in ("onyx-public-egress-proxy", "onyx-host-egress-proxy"):
            self.assertNotIn(
                "EGRESS_TOR_SOCKS_UNIX_PATH",
                onion["services"][proxy]["environment"],
            )

        combined = _compose_model(
            "lite",
            "docker-compose.tor.yml",
            "docker-compose.tor-egress.yml",
            "docker-compose.tor-onion.yml",
        )
        self.assertEqual(
            set(combined["services"]["tor"]["networks"]),
            {"tor-uplink", "tor-ingress"},
        )
        tor = combined["services"]["tor"]
        self.assertEqual(tor["user"], "101:102")
        self.assertTrue(tor["read_only"])
        self.assertEqual(tor["cap_drop"], ["ALL"])
        self.assertEqual(tor["security_opt"], ["no-new-privileges:true"])
        self.assertEqual(tor["entrypoint"], [])
        self.assertEqual(tor["command"], ["tor", "-f", "/etc/tor/torrc"])
        self.assertEqual(tor["healthcheck"]["interval"], "10m0s")
        self.assertEqual(tor["healthcheck"]["start_interval"], "5s")
        self.assertEqual(
            tor["healthcheck"]["test"],
            ["CMD", "python3", "/usr/local/bin/tor-healthcheck.py"],
        )
        self.assertEqual(tor["tmpfs"], ["/run/tor-control:uid=101,gid=102,mode=0700"])
        self.assertNotIn("ports", combined["services"]["tor"])
        self.assertNotIn("onyx-frontend", combined["services"]["tor"]["networks"])
        self.assertEqual(
            set(combined["services"]["tor-frontend-gateway"]["networks"]),
            {"tor-ingress", "onyx-frontend"},
        )
        runtime_receivers = {
            name
            for name, service in combined["services"].items()
            if any(
                volume.get("source", "").endswith("tor-runtime")
                for volume in service.get("volumes", [])
            )
        }
        self.assertEqual(
            runtime_receivers,
            {"tor", "onyx-public-egress-proxy", "onyx-host-egress-proxy"},
        )
        for proxy in ("onyx-public-egress-proxy", "onyx-host-egress-proxy"):
            runtime = next(
                volume
                for volume in combined["services"][proxy]["volumes"]
                if volume["source"].endswith("tor-runtime")
            )
            self.assertTrue(runtime["read_only"])

        podman = _compose_model(
            "lite",
            "docker-compose.podman.yml",
            "docker-compose.tor.yml",
            "docker-compose.tor-egress.yml",
            "docker-compose.tor-onion.yml",
            "docker-compose.tor-podman.yml",
            "docker-compose.tor-onion-podman.yml",
        )
        self.assertEqual(
            podman["services"]["tor"]["userns_mode"],
            "keep-id:uid=101,gid=102",
        )
        self.assertEqual(
            podman["services"]["tor"]["sysctls"]["net.ipv4.ping_group_range"],
            "102 102",
        )
        self.assertEqual(
            podman["services"]["tor"]["tmpfs"],
            ["/run/tor-control:U,mode=0700"],
        )

    def test_tor_makefile_layer_selection_is_role_and_engine_specific(self) -> None:
        for egress, onion in ((False, False), (True, False), (False, True), (True, True)):
            for engine in ("docker", "podman"):
                with self.subTest(egress=egress, onion=onion, engine=engine):
                    files = _make_compose_files(
                        vpn_enabled=False,
                        container_bin=engine,
                        TOR_EGRESS_ENABLED=str(egress).lower(),
                        TOR_ONION_SERVICE_ENABLED=str(onion).lower(),
                    )
                    enabled = egress or onion
                    self.assertEqual("docker-compose.tor.yml" in files, enabled)
                    self.assertEqual(
                        "docker-compose.tor-egress.yml" in files, egress
                    )
                    self.assertEqual(
                        "docker-compose.tor-onion.yml" in files, onion
                    )
                    self.assertEqual(
                        "docker-compose.tor-podman.yml" in files,
                        enabled and engine == "podman",
                    )
                    self.assertEqual(
                        "docker-compose.tor-onion-podman.yml" in files,
                        onion and engine == "podman",
                    )

    def test_core_startup_does_not_wait_for_optional_browsing(self) -> None:
        lite = _compose_model("lite")
        self.assertFalse(lite["services"]["api_server"].get("depends_on"))

        full = _compose_model("full")
        self.assertEqual(
            set(full["services"]["api_server"].get("depends_on", {})),
            {
                "cache",
                "local-embedding-shim",
                "minio",
                "opensearch",
                "relational_db",
            },
        )
        self.assertEqual(
            set(full["services"]["background"].get("depends_on", {})),
            {
                "cache",
                "local-embedding-shim",
                "opensearch",
                "relational_db",
            },
        )
        for service_name in ("api_server", "background"):
            self.assertEqual(
                full["services"][service_name]["depends_on"]["opensearch"]["condition"],
                "service_started",
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

    def test_makefile_never_selects_removed_autoheal_overlays(self) -> None:
        for container_bin in ("docker", "podman"):
            for vpn_enabled in (False, True):
                with self.subTest(
                    container_bin=container_bin, vpn_enabled=vpn_enabled
                ):
                    files = _make_compose_files(
                        vpn_enabled=vpn_enabled, container_bin=container_bin
                    )
                    self.assertNotIn("docker-compose.vpn-autoheal.yml", files)
                    self.assertNotIn("docker-compose.podman-vpn.yml", files)

    def test_makefile_selects_every_optional_network_layer(self) -> None:
        default_files = _make_compose_files(vpn_enabled=True)
        optional_files = (
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
        self.assertNotIn("docker-compose.proxy.yml", enabled_files)

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
                extra.extend(
                    (
                        "docker-compose.podman-full.yml",
                        "docker-compose.podman-macos-full.yml",
                    )
                )
            model = _compose_model(mode, *extra)
            services = model["services"]
            self.assertEqual(
                services["onyx-host-egress-proxy"]["environment"][
                    "EGRESS_PODMAN_HOST_GATEWAY_IP"
                ],
                "169.254.1.2",
            )
            self.assertNotIn(
                "EGRESS_PODMAN_HOST_GATEWAY_IP",
                services["onyx-public-egress-proxy"]["environment"],
            )
            postgres = services["relational_db"]
            self.assertEqual(postgres["userns_mode"], "keep-id:uid=70,gid=70")
            self.assertEqual(
                postgres["sysctls"]["net.ipv4.ping_group_range"],
                "70 70",
            )
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
                    opensearch["sysctls"]["net.ipv4.ping_group_range"],
                    "1000 1000",
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

    def test_native_linux_docker_preserves_rootless_postgres_ownership(self) -> None:
        linux_files = _make_compose_files(
            vpn_enabled=False, container_bin="docker", HOST_OS="Linux"
        )
        macos_files = _make_compose_files(
            vpn_enabled=False, container_bin="docker", HOST_OS="Darwin"
        )
        self.assertIn("docker-compose.docker-linux.yml", linux_files)
        self.assertNotIn("docker-compose.docker-linux.yml", macos_files)

        model = _compose_model(
            "lite",
            "docker-compose.docker-linux.yml",
            env_overrides={
                "PRIVATE_ONYX_HOST_UID": "1234",
                "PRIVATE_ONYX_HOST_GID": "1235",
            },
        )
        postgres = model["services"]["relational_db"]
        self.assertEqual(postgres["user"], "1234:1235")
        self.assertIsNone(postgres["entrypoint"])
        self.assertNotIn("userns_mode", postgres)

    def test_makefile_scopes_podman_document_relay_to_macos(self) -> None:
        linux_files = _make_compose_files(
            vpn_enabled=False, container_bin="podman", HOST_OS="Linux"
        )
        macos_files = _make_compose_files(
            vpn_enabled=False, container_bin="podman", HOST_OS="Darwin"
        )
        for files in (linux_files, macos_files):
            self.assertIn("docker-compose.podman.yml", files)
            self.assertIn("docker-compose.podman-full.yml", files)
            self.assertNotIn("podman-docker-postgres", files)
            self.assertNotIn("podman-docker-opensearch", files)
        self.assertNotIn("docker-compose.podman-macos-full.yml", linux_files)
        self.assertIn("docker-compose.podman-macos-full.yml", macos_files)

        linux = _compose_model(
            "full",
            "docker-compose.podman.yml",
            "docker-compose.podman-full.yml",
        )
        doc_server = linux["services"]["doc-drop-web"]
        self.assertEqual(
            doc_server["image"], "docker.io/library/python:3.12-alpine"
        )
        self.assertIn("/import/docs", doc_server["command"])
        self.assertTrue(
            any(
                volume["target"] == "/import/docs" and volume["read_only"]
                for volume in doc_server["volumes"]
            )
        )
        self.assertNotIn("podman-doc-host-uplink", linux["networks"])

        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertNotIn("PODMAN_SHARE_DOCKER_POSTGRES", makefile)
        self.assertNotIn("PODMAN_SHARE_DOCKER_OPENSEARCH", makefile)

    def test_myst_uses_common_socket_free_health_supervisor(self) -> None:
        expected_health = [
            "CMD",
            "/bin/sh",
            "/usr/local/bin/myst-healthcheck.sh",
            "check",
            "1",
            "/run/myst-healthcheck",
            "/usr/local/bin/myst-readiness.sh",
            "/proc/uptime",
            "60",
            "/proc",
        ]
        for mode in ("lite", "full"):
            for engine in ("docker", "podman"):
                extras = ["docker-compose.podman.yml"] if engine == "podman" else []
                if engine == "podman" and mode == "full":
                    extras.append("docker-compose.podman-full.yml")
                for vpn_enabled in (False, True):
                    with self.subTest(
                        mode=mode, engine=engine, vpn_enabled=vpn_enabled
                    ):
                        model = _compose_model(
                            mode,
                            *extras,
                            env_overrides={
                                "MYST_VPN_ENABLED": (
                                    "true" if vpn_enabled else "false"
                                )
                            },
                        )
                        self.assertNotIn("autoheal", model["services"])
                        myst = model["services"]["myst-client"]
                        self.assertEqual(myst["restart"], "unless-stopped")
                        self.assertEqual(
                            myst["network_mode"], "service:netns-holder"
                        )
                        self.assertEqual(myst["healthcheck"]["test"], expected_health)
                        self.assertIn(
                            "/usr/local/bin/myst-child-process-control.sh",
                            {
                                volume["target"]
                                for volume in myst.get("volumes", [])
                                if isinstance(volume, dict)
                            },
                        )
                        self.assertNotIn("autoheal", myst.get("labels", {}))
                        self.assertEqual(
                            myst["environment"]["MYST_VPN_ENABLED"],
                            "true" if vpn_enabled else "false",
                        )
                        sockets = [
                            volume["source"]
                            for service in model["services"].values()
                            for volume in service.get("volumes", [])
                            if isinstance(volume, dict)
                            and volume.get("source") == "/var/run/docker.sock"
                        ]
                        if engine == "podman":
                            self.assertEqual(sockets, [])
                        else:
                            self.assertLessEqual(len(sockets), 1)

    def test_removed_autoheal_contract_stays_absent_from_active_configuration(self) -> None:
        self.assertFalse((ROOT / "docker-compose.vpn-autoheal.yml").exists())
        self.assertFalse((ROOT / "docker-compose.podman-vpn.yml").exists())
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                ROOT / "Makefile",
                ROOT / "stack.versions.env",
                ROOT / "docker-compose.yaml",
                ROOT / "compose_overlays/docker-compose.full.yml",
                ROOT / "compose_overlays/docker-compose.lite.yml",
                ROOT / "compose_overlays/docker-compose.podman.yml",
                ROOT / "compose_overlays/docker-compose.podman-full.yml",
                ROOT / "compose_overlays/docker-compose.podman-macos-full.yml",
            )
        )
        for forbidden in (
            "AUTOHEAL_IMAGE",
            "willfarrell/autoheal",
            'autoheal: "true"',
            "docker-compose.vpn-autoheal.yml",
            "docker-compose.podman-vpn.yml",
        ):
            self.assertNotIn(forbidden, active)

    def test_sleepy_health_inventory_and_aggregate_ownership(self) -> None:
        model = _compose_model(
            "full",
            "docker-compose.code-interpreter-network.yml",
            "docker-compose.teep-vpn.yml",
            profiles=("tailscale",),
        )
        services = model["services"]
        disabled = {
            "searxng-core",
            "obscura",
            "doc-drop-web",
            "web_server",
            "cache",
            "opensearch",
        }
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
        self.assertEqual(
            services["nginx"]["depends_on"]["web_server"]["condition"],
            "service_started",
        )

        commands = "\n".join(" ".join(health["test"]) for health in retained.values())
        for forbidden in ("example.com", "--check-ready", "/ready"):
            self.assertNotIn(forbidden, commands)

    def test_full_storage_and_background_power_defaults(self) -> None:
        services = _compose_model("full")["services"]
        opensearch = services["opensearch"]
        self.assertEqual(
            opensearch["environment"]["OPENSEARCH_JAVA_OPTS"],
            "-Xms512m -Xmx512m",
        )
        self.assertEqual(opensearch["environment"]["node.processors"], "4")
        self.assertEqual(
            opensearch["environment"]["DISABLE_PERFORMANCE_ANALYZER_AGENT_CLI"],
            "true",
        )
        self.assertEqual(
            opensearch["environment"]["plugins.security.audit.config.index"],
            "'security-auditlog-'YYYY.MM",
        )
        for metric in ("latency", "cpu", "memory"):
            self.assertEqual(
                opensearch["environment"][
                    f"search.insights.top_queries.{metric}.enabled"
                ],
                "false",
            )
        audit_mount = next(
            volume
            for volume in opensearch["volumes"]
            if volume["target"]
            == "/usr/share/opensearch/config/opensearch-security/audit.yml"
        )
        self.assertTrue(audit_mount["read_only"])
        audit_source = (ROOT / "onyx/opensearch/audit.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("log_request_body: false", audit_source)
        self.assertNotIn("log_request_body: true", audit_source)
        self.assertNotIn("opensearch-configure", services)
        for service_name in ("api_server", "background"):
            self.assertEqual(
                services[service_name]["environment"]["OPENSEARCH_INDEX_NUM_REPLICAS"],
                "0",
            )
        self.assertEqual(services["minio"]["environment"]["MINIO_SCANNER_SPEED"], "slowest")
        background = services["background"]
        self.assertEqual(background["environment"]["PROMETHEUS_METRICS_ENABLED"], "false")
        self.assertEqual(background["environment"]["ONYX_AGENT_SLACK_BOT"], "false")
        self.assertEqual(background["environment"]["ONYX_AGENT_DISCORD_BOT"], "false")
        self.assertEqual(
            background["entrypoint"],
            ["python", "-S", "/app/wrapper-background-entrypoint.py"],
        )

    def test_bot_options_are_full_mode_only(self) -> None:
        lite = _compose_model("lite")
        self.assertNotIn("background", lite["services"])
        rendered_lite = json.dumps(lite)
        for name in ("ONYX_AGENT_SLACK_BOT", "ONYX_AGENT_DISCORD_BOT"):
            self.assertNotIn(name, rendered_lite)

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
        self.assertEqual(public["ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS"], "none")
        self.assertEqual(host["ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS"], "none")
        self.assertEqual(public["ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL"], "")
        self.assertEqual(
            host["ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL"],
            "http://host.docker.internal:3210/v1/embeddings",
        )
        self.assertEqual(public["ONYX_INTEGRATIONS_ALLOW_LAN_ENDPOINTS"], "false")
        self.assertNotIn("EGRESS_PROXY_TRUSTED_INTERNAL_DESTINATIONS", public)
        self.assertEqual(
            host["EGRESS_PROXY_TRUSTED_INTERNAL_DESTINATIONS"],
            "doc-drop-web:8091",
        )
        dependency = services["onyx-host-egress-bridge"]["depends_on"][
            "onyx-host-egress-proxy"
        ]
        self.assertEqual(dependency["condition"], "service_started")
        self.assertTrue(dependency["restart"])
        health = services["onyx-host-egress-bridge"]["healthcheck"]["test"]
        self.assertIn("grep -q '^HTTP/1.1 403'", health[-1])

    def test_host_port_and_embedding_policy_reach_only_intended_services(
        self,
    ) -> None:
        model = _compose_model(
            "full",
            env_overrides={
                "ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS": "3210,11434",
                "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL": (
                    "http://host.docker.internal:8337/v1/embeddings"
                ),
            },
        )
        services = model["services"]
        operator = "ONYX_INTEGRATIONS_ALLOWED_HOST_PORTS"
        embedding = "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL"
        self.assertEqual(
            services["onyx-host-egress-proxy"]["environment"][operator],
            "3210,11434",
        )
        self.assertEqual(
            services["onyx-host-egress-proxy"]["environment"][embedding],
            "http://host.docker.internal:8337/v1/embeddings",
        )
        self.assertEqual(
            services["onyx-public-egress-proxy"]["environment"][operator], "none"
        )
        self.assertEqual(
            services["onyx-public-egress-proxy"]["environment"][embedding], ""
        )
        for name, service in services.items():
            if name not in {
                "onyx-host-egress-proxy",
                "onyx-public-egress-proxy",
                "local-embedding-shim",
            }:
                self.assertNotIn(operator, service.get("environment", {}))
                self.assertNotIn(embedding, service.get("environment", {}))

        lite = _compose_model("lite")["services"]
        self.assertEqual(
            lite["onyx-host-egress-proxy"]["environment"][embedding], ""
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
        executor_environment = services["code-interpreter"]["environment"]
        self.assertEqual(
            executor_environment["PYTHON_EXECUTOR_DOCKER_NETWORK"],
            "onyx-code-interpreter-executor",
        )
        run_args = executor_environment["PYTHON_EXECUTOR_DOCKER_RUN_ARGS"]
        self.assertEqual(run_args.count("--env "), 8)
        self.assertNotIn("EGRESS_UPSTREAM_PROXY_URL", run_args)
        self.assertIn(
            "HTTP_PROXY=http://executor-egress-bridge:3128", run_args
        )
        self.assertNotIn("PYTHONPATH", executor_environment)
        self.assertNotIn("WRAPPER_PATCH_STARTUP_FATAL", executor_environment)
        self.assertEqual(len(services["code-interpreter"]["volumes"]), 1)
        self.assertEqual(
            services["code-interpreter"]["volumes"][0]["target"],
            "/var/run/docker.sock",
        )
        self.assertNotIn(
            "EGRESS_PROXY_TRUSTED_INTERNAL_DESTINATIONS",
            services["onyx-public-egress-proxy"]["environment"],
        )
        self.assertNotIn("executor-egress-proxy", services)

    def test_optional_overlay_matrix_preserves_application_isolation(self) -> None:
        model = _compose_model(
            "full",
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

    def test_open_url_character_defaults_are_unlimited(self) -> None:
        for mode in ("lite", "full"):
            with self.subTest(mode=mode):
                environment = _compose_model(mode)["services"]["api_server"][
                    "environment"
                ]
                self.assertEqual(
                    environment["ONYX_OPEN_URL_MAX_CHARS_PER_URL"], "0"
                )
                self.assertEqual(environment["ONYX_OPEN_URL_MAX_TOTAL_CHARS"], "0")

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
