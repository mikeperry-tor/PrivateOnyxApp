"""Exercise the actual Make-selected Compose model using only public fixtures."""
from __future__ import annotations

import copy
import json
import shlex
import os
import socket
import subprocess
import tempfile
import unittest

from test_onyx_network_isolation import (
    ROOT, SECRET_ENV, _compose_command, _wrapper_neutral_environment,
)

GATEWAY_KEYS = tuple(f"com.docker.network.bridge.gateway_mode_ipv{v}" for v in (4, 6))
CONTROL = "onyx-code-interpreter-control"


def validate_model(model: dict, *, engine: str, gateway: str) -> None:
    """Check effective values, including internal networks added by future layers."""
    if not isinstance(model, dict) or not isinstance(model.get("networks"), dict):
        raise ValueError("invalid Compose network model")
    services = model.get("services")
    if not isinstance(services, dict) or "api_server" not in services:
        raise ValueError("invalid Compose services")
    for name, network in model["networks"].items():
        if not isinstance(network, dict):
            raise ValueError("invalid network definition")
        options = network.get("driver_opts", {})
        if not isinstance(options, dict):
            raise ValueError("invalid driver options")
        if engine == "docker" and gateway == "isolated" and network.get("internal"):
            if network.get("driver", "bridge") != "bridge" or network.get("external"):
                raise ValueError("internal network must use the local bridge driver")
            if any(options.get(key) != "isolated" for key in GATEWAY_KEYS):
                raise ValueError("internal network is missing isolated gateways: " + name)
        elif any(key in options for key in GATEWAY_KEYS):
            raise ValueError("unexpected isolated gateway options")
    for service in services.values():
        attachments = service.get("networks", {})
        if attachments and all(model["networks"][n].get("internal") for n in attachments):
            dropped = set(service.get("cap_drop", []))
            if "ALL" not in dropped and not {"NET_RAW", "NET_ADMIN"} <= dropped:
                raise ValueError("internal-only service retains raw/admin network capabilities")
            if service.get("privileged") or {"NET_RAW", "NET_ADMIN", "ALL"} & set(service.get("cap_add", [])):
                raise ValueError("internal-only service regains network capabilities")
    if engine == "podman":
        if "code-interpreter" in services or CONTROL in model["networks"] or "executor-egress" in model["networks"]:
            raise ValueError("Podman acquired Docker controller/executor topology")
    else:
        if set(services["code-interpreter"]["networks"]) != {CONTROL}:
            raise ValueError("controller must have exactly one network")
        peers = {name for name, service in services.items() if CONTROL in service.get("networks", {})}
        if peers != {"api_server", "code-interpreter"}:
            raise ValueError("unexpected controller caller")
        if services["code-interpreter"].get("ports"):
            raise ValueError("controller must not publish ports")
    if "background" in services and services["background"]["environment"].get("CODE_INTERPRETER_BASE_URL") != "":
        raise ValueError("background must explicitly disable native execution tools")


def make_model(mode: str, *, engine="docker", gateway="isolated", down=False, **settings):
    env = {**_wrapper_neutral_environment(), **SECRET_ENV}
    variable = mode.upper() + ("_DOWN_FILES" if down else "_FILES")
    command = [
        "make", "--no-print-directory", "-s", "-f", "Makefile", "-f", "-", "isolation-model-files",
        "ENV_FILE=.env.wrapper.example", f"CONTAINER_BIN={engine}",
        "PRIVATE_ONYX_DOCKER_ENGINE_MODE=rootful", f"PRIVATE_ONYX_DOCKER_GATEWAY_MODE={gateway}",
        "DOCKER_SOCK_PATH=/tmp/isolation-fixture.sock", "HOST_OS=Darwin",
        *[f"{key}={SECRET_ENV[key]}" for key in (
            "SEARXNG_SECRET", "USER_AUTH_SECRET", "MINIO_ROOT_USER",
            "MINIO_ROOT_PASSWORD", "S3_AWS_ACCESS_KEY_ID", "S3_AWS_SECRET_ACCESS_KEY",
        )],
        *[f"{key}={value}" for key, value in settings.items()],
    ]
    selected = subprocess.run(command, input=f'isolation-model-files:\n\t@echo "$({variable})"\n',
                              cwd=ROOT, env=env, text=True, capture_output=True, check=True).stdout.strip().split(":")
    assert selected[0] == "docker-compose.yaml"
    if engine == "docker":
        assert "compose_overlays/docker-compose.docker-controller.yml" in selected
        assert (selected[-1] == "compose_overlays/docker-compose.docker-isolation.yml") == (gateway == "isolated")
    else:
        assert not any("docker-controller" in path or "docker-isolation" in path for path in selected)
    compose = [*_compose_command(), "--env-file", "stack.versions.env", "--env-file", ".env.wrapper.example"]
    for path in selected:
        compose += ["-f", path]
    render = shlex.join([*compose, "config", "--no-env-resolution", "--format", "json"])
    result = subprocess.run(command, input=f"isolation-model-files:\n\t@{render}\n",
                            cwd=ROOT, env={**env, **settings}, text=True, capture_output=True, check=True)
    model = json.loads(result.stdout)
    validate_model(model, engine=engine, gateway=gateway)
    return model


class DockerGatewayIsolationTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix" and hasattr(socket, "AF_UNIX"), "requires Unix sockets")
    def test_repeated_tor_runtime_initialization_preserves_live_socket(self):
        model = make_model("full", TOR_EGRESS_ENABLED="true")
        command = model["services"]["tor-runtime-init"]["command"][0]
        with tempfile.TemporaryDirectory() as directory:
            with socket.socket(socket.AF_UNIX) as listener:
                path = directory + "/socks"
                try:
                    listener.bind(path)
                except PermissionError:
                    self.skipTest("sandbox does not permit Unix socket listeners")
                listener.listen()
                inode = os.stat(path).st_ino
                command = command.replace("chown 0:0", f"chown {os.getuid()}:{os.getgid()}")
                command = command.replace("/run/tor-egress", shlex.quote(directory))
                for _ in range(2):
                    subprocess.run(["sh", "-ec", command], check=True, capture_output=True)
                    self.assertEqual(os.stat(path).st_ino, inode)
                    with socket.socket(socket.AF_UNIX) as client:
                        client.connect(path)
                        connection, _ = listener.accept()
                        connection.close()

    def test_gateway_layer_preserves_effective_application_and_network_metadata(self):
        settings = dict(TOR_ONION_SERVICE_ENABLED="true", ONYX_CODE_INTERPRETER_ENABLE_NETWORK="true",
                        MYST_VPN_ENABLED="true", TEEP_ROUTE_THROUGH_MYST_VPN="true")
        ordinary = make_model("full", gateway="ordinary", **settings)
        isolated = make_model("full", gateway="isolated", **settings)
        for network in isolated["networks"].values():
            if network.get("internal"):
                for key in GATEWAY_KEYS:
                    del network["driver_opts"][key]
                if not network["driver_opts"]:
                    del network["driver_opts"]
        self.assertEqual(ordinary, isolated)

    def test_default_and_custom_socket_do_not_depend_on_child_network(self):
        for socket_path in ("", "/tmp/custom-docker.sock"):
            for enabled in ("", "false", "true"):
                model = make_model("lite", DOCKER_SOCK_PATH=socket_path,
                                   ONYX_CODE_INTERPRETER_ENABLE_NETWORK=enabled)
                volumes = model["services"]["code-interpreter"]["volumes"]
                sockets = [v for v in volumes if v["target"] == "/var/run/docker.sock"]
                self.assertEqual(len(sockets), 1)
                self.assertEqual(sockets[0]["source"], socket_path or "/var/run/docker.sock")

    def test_make_selected_platform_feature_matrix(self):
        features = [
            {},
            {"ONYX_CODE_INTERPRETER_ENABLE_NETWORK": "false"},
            {"ONYX_CODE_INTERPRETER_ENABLE_NETWORK": "true"},
            {"MYST_VPN_ENABLED": "true", "TEEP_ROUTE_THROUGH_MYST_VPN": "true", "TAILSCALE_FUNNEL_ENABLED": "true", "TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN": "true"},
            {"TOR_EGRESS_ENABLED": "true"},
            {"TOR_ONION_SERVICE_ENABLED": "true"},
            {"TOR_EGRESS_ENABLED": "true", "TOR_ONION_SERVICE_ENABLED": "true", "ONYX_CODE_INTERPRETER_ENABLE_NETWORK": "true", "MYST_VPN_ENABLED": "true", "TEEP_ROUTE_THROUGH_MYST_VPN": "true", "TAILSCALE_FUNNEL_ENABLED": "true", "TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN": "true"},
            {"HOST_OS": "Linux"},
            {"HOST_OS": "Linux", "PRIVATE_ONYX_DOCKER_ENGINE_MODE": "rootless", "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL": "http://host.docker.internal:8337/v1/embeddings"},
        ]
        for engine, gateway in (("docker", "isolated"), ("docker", "ordinary"), ("podman", "ordinary")):
            for mode in ("lite", "full"):
                for feature in features:
                    with self.subTest(engine=engine, gateway=gateway, mode=mode, feature=feature):
                        model = make_model(mode, engine=engine, gateway=gateway, **feature)
                        if engine == "docker":
                            controller = model["services"]["code-interpreter"]
                            sockets = [v for v in controller["volumes"] if v["target"] == "/var/run/docker.sock"]
                            self.assertEqual(len(sockets), 1)
                            self.assertEqual(sockets[0]["source"], "/tmp/isolation-fixture.sock")
                            self.assertNotIn("CODE_INTERPRETER_BASE_URL", controller["environment"])
                make_model(mode, engine=engine, gateway=gateway, down=True)

    def test_validator_rejects_new_uncovered_network_and_wrong_effective_values(self):
        model = make_model("lite")
        for change in (
            {"new-network": {"internal": True}},
            {"onyx-backend": {"internal": True, "driver": "macvlan", "driver_opts": dict.fromkeys(GATEWAY_KEYS, "isolated")}},
            {"onyx-backend": {"internal": True, "driver_opts": dict.fromkeys(GATEWAY_KEYS, "nat")}},
            {"host-publish": {"driver_opts": dict.fromkeys(GATEWAY_KEYS, "isolated")}},
        ):
            broken = copy.deepcopy(model)
            broken["networks"].update(change)
            with self.assertRaises(ValueError):
                validate_model(broken, engine="docker", gateway="isolated")
        for broken in ({}, {"networks": []}, {"networks": {}, "services": []}):
            with self.assertRaises(ValueError):
                validate_model(broken, engine="docker", gateway="isolated")

    def test_bot_switches_keep_background_tools_disabled(self):
        for slack, discord in (("false", "false"), ("true", "false"), ("false", "true"), ("true", "true")):
            for engine in ("docker", "podman"):
                model = make_model("full", engine=engine, ONYX_AGENT_SLACK_BOT=slack, ONYX_AGENT_DISCORD_BOT=discord)
                env = model["services"]["background"]["environment"]
                self.assertEqual(env["ONYX_AGENT_SLACK_BOT"], slack)
                self.assertEqual(env["ONYX_AGENT_DISCORD_BOT"], discord)
