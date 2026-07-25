from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MystLifecycleMakefileTests(unittest.TestCase):
    def _make_database(
        self,
        env_file: Path,
        *,
        environment: dict[str, str] | None = None,
        assignments: tuple[str, ...] = (),
    ) -> str:
        clean_environment = dict(os.environ)
        names_to_clear = {
            "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL",
            "ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL",
            "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL",
        }
        example = (ROOT / ".env.wrapper.example").read_text(encoding="utf-8")
        for raw_line in example.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name = line.split("=", 1)[0].strip()
            if name.isidentifier():
                names_to_clear.add(name)
        for name in names_to_clear:
            clean_environment.pop(name, None)
        clean_environment.update(environment or {})
        result = subprocess.run(
            [
                "make",
                "-pn",
                "help",
                f"ENV_FILE={env_file}",
                *assignments,
            ],
            cwd=ROOT,
            env=clean_environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    @staticmethod
    def _simple_make_value(database: str, name: str) -> str:
        prefix = f"{name} := "
        return next(
            line.removeprefix(prefix)
            for line in database.splitlines()
            if line.startswith(prefix)
        )

    def test_empty_wrapper_uses_example_host_workflow_defaults(self) -> None:
        expected = {
            "CONTAINER_BIN": "docker",
            "TEEP_ROUTE_THROUGH_MYST_VPN": "false",
            "TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN": "false",
            "TAILSCALE_FUNNEL_ENABLED": "false",
            "ONYX_CODE_INTERPRETER_ENABLE_NETWORK": "false",
            "MYST_VPN_ENABLED": "false",
            "MYST_VPN_ORDER_AMOUNT": "100",
            "MYST_VPN_ORDER_CURRENCY": "MYST",
            "MYST_VPN_ORDER_GATEWAY": "coingate",
            "MYST_VPN_ORDER_COUNTRY": "US",
            "MYST_VPN_ORDER_GATEWAY_DATA": "custom_id=mysterium-onyx",
        }
        with tempfile.TemporaryDirectory() as temporary:
            empty = Path(temporary) / "empty.env"
            empty.write_text("", encoding="utf-8")
            database = self._make_database(empty)

        for name, value in expected.items():
            with self.subTest(name=name):
                self.assertEqual(self._simple_make_value(database, name), value)

    def test_connection_info_target_executes_myst_in_running_container(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake_container = Path(directory) / "fake-container"
            fake_container.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$*\"\n", encoding="utf-8"
            )
            fake_container.chmod(0o755)
            result = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "vpn-connection-info",
                    f"CONTAINER_BIN={fake_container}",
                    "ENV_FILE=/dev/null",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(), "exec myst-client-vpn myst connection info"
        )

    def test_signup_targets_use_single_owner_nonrestarting_mode(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        for target in ("vpn-signup-orderform:", "vpn-signup-blockchain:"):
            recipe = makefile.split(target, 1)[1].split("\n\n", 1)[0]
            self.assertIn("myst/signup_guard.py", recipe)
            self.assertIn("claim-shared-data-engine", recipe)
            self.assertIn("MYST_SETUP_ONLY=true", recipe)
            self.assertIn("MYST_RESTART_POLICY=no", recipe)
            self.assertIn("-p $(MYST_SIGNUP_PROJECT)", recipe)
            self.assertNotIn("sleep 3", recipe)
        order_recipe = makefile.split("vpn-signup-orderform:", 1)[1].split("\n\n", 1)[0]
        for variable in (
            "MYST_VPN_ORDER_AMOUNT",
            "MYST_VPN_ORDER_CURRENCY",
            "MYST_VPN_ORDER_GATEWAY",
            "MYST_VPN_ORDER_COUNTRY",
            "MYST_VPN_ORDER_GATEWAY_DATA",
        ):
            self.assertIn(f'{variable}="$({variable})"', order_recipe)

        funded_recipe = makefile.split("ensure-myst-funded:", 1)[1].split("\n# ", 1)[0]
        self.assertIn("--classify", funded_recipe)
        self.assertIn("-p $(MYST_SIGNUP_PROJECT)", funded_recipe)
        self.assertNotIn("rm -f $(MYST_CONTAINER_NAME)", funded_recipe)

    def test_integrated_entrypoint_performs_no_signup_or_order_mutation(self) -> None:
        entrypoint = (ROOT / "myst/myst-client-entrypoint.sh").read_text(encoding="utf-8")
        self.assertNotIn("identities new", entrypoint)
        self.assertNotIn("account register", entrypoint)
        self.assertNotIn("orders create", entrypoint)
        self.assertIn("MYST_VPN_IDENTITY", entrypoint)
        self.assertIn("Multiple Myst identities exist", entrypoint)
        self.assertIn("explicit signup repair is required", entrypoint)

    def test_no_vpn_entrypoint_uses_inert_readiness_sentinel(self) -> None:
        entrypoint = (ROOT / "myst/myst-client-entrypoint.sh").read_text(encoding="utf-8")
        no_vpn_branch = entrypoint.split("# ── Optional VPN bypass", 1)[1].split(
            "# ── VPN-enabled daemon lifecycle", 1
        )[0]

        self.assertIn('if [ "${MYST_VPN_ENABLED:-true}" = "false" ]', no_vpn_branch)
        self.assertIn("sleep infinity &", no_vpn_branch)
        self.assertIn('wait "$svc_pid"', no_vpn_branch)
        self.assertNotIn("docker-entrypoint.sh", no_vpn_branch)
        self.assertNotIn("myst-route-reconciliation.sh", no_vpn_branch)
        self.assertNotIn('set -- "$@" daemon', no_vpn_branch)

    def test_signup_compose_model_has_no_restart_and_explicit_setup_mode(self) -> None:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                "private-onyx-myst-signup",
                "--env-file",
                "stack.versions.env",
                "--env-file",
                "/dev/null",
                "-f",
                "myst/docker-compose.yaml",
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            env={
                "PATH": os.environ["PATH"],
                "MYST_SETUP_ONLY": "true",
                "MYST_AUTO_CONNECT": "false",
                "MYST_RESTART_POLICY": "no",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        myst = json.loads(result.stdout)["services"]["myst-client"]
        self.assertEqual(myst["restart"], "no")
        self.assertEqual(myst["environment"]["MYST_SETUP_ONLY"], "true")
        self.assertEqual(myst["environment"]["MYST_AUTO_CONNECT"], "false")

    def test_build_context_excludes_private_and_large_local_state(self) -> None:
        ignore_paths = (ROOT / ".dockerignore", ROOT / ".containerignore")
        contents = [
            path.read_text(encoding="utf-8").splitlines() for path in ignore_paths
        ]
        self.assertEqual(contents[0], contents[1])
        ignored = {
            line.strip()
            for line in contents[0]
            if line.strip() and not line.startswith("#")
        }
        self.assertTrue(
            {
                ".env.wrapper",
                "docker-data",
                "doc-drop",
                "embedserv/models",
                "embedserv/*.pid",
            }
            <= ignored
        )
        self.assertTrue({"onyx/onyx_data", "reference_repos", ".git"} <= ignored)

        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        tor_build = makefile.split("\ntor-build:", 1)[1].split(
            "\n\ntor-config-ready:", 1
        )[0]
        self.assertTrue(tor_build.rstrip().endswith("\t\ttor"))

    def test_podman_compose_is_pinned_to_the_forwarded_unix_socket(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("export DOCKER_HOST := unix://$(DOCKER_SOCK_PATH)", makefile)
        self.assertIn("export CONTAINER_BIN", makefile)

    def test_stack_start_preserves_integrated_myst_container(self) -> None:
        makefile = (ROOT / "Makefile").read_text()
        guard = (ROOT / "myst/signup_guard.py").read_text()
        self.assertIn('project == "onyx" and not setup_only', guard)
        self.assertIn('--classify', makefile)
        self.assertIn(
            "Integrated Onyx Myst container exists; preserving its routing namespace.",
            makefile,
        )

    def test_full_start_stages_one_embedding_readiness_call(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        full_start = makefile.split("up-full:", 2)[2].split("\n\n", 1)[0]
        self.assertIn("up -d --wait --wait-timeout 420 local-embedding-shim", full_start)
        self.assertIn("embedding-ready-once", full_start)
        self.assertTrue(
            full_start.rstrip().endswith("up -d --wait --wait-timeout 420")
        )
        self.assertLess(
            full_start.index("local-embedding-shim"),
            full_start.index("embedding-ready-once"),
        )
        ready_recipe = makefile.split("embedding-ready-once:", 1)[1].split(
            "\n\n", 1
        )[0]
        self.assertEqual(ready_recipe.count("/ready"), 1)
        self.assertNotIn("while", ready_recipe)
        self.assertIn("timeout=None", ready_recipe)
        self.assertIn("can take several minutes", ready_recipe)
        self.assertIn("Press Ctrl-C to stop waiting", ready_recipe)
        self.assertNotIn("-T 35", ready_recipe)
        lite_start = makefile.split("up-lite:", 2)[2].split("\n\n", 1)[0]
        self.assertNotIn("embedding-ready-once", lite_start)
        self.assertNotIn("/ready", lite_start)

    def test_container_capability_gate_is_a_start_prerequisite(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("Docker Engine 25.0+", makefile)
        self.assertIn("Docker Compose 2.20.2+", makefile)
        capability_target = makefile.split(
            "check-container-health-capability:", 1
        )[1].split("\n\n", 1)[0]
        self.assertIn("startup_health.py check", capability_target)
        self.assertNotIn("Podman startup-health has not passed", makefile)
        for target in ("up-lite:", "up-full:"):
            definitions = [
                line for line in makefile.splitlines()
                if line.startswith(target)
            ]
            self.assertTrue(
                any("check-container-health-capability" in line for line in definitions)
            )

    def test_podman_create_configure_start_sequence_is_explicit(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        lite_definition = next(
            line for line in makefile.splitlines()
            if line.startswith("up-lite: wrapper-config-preflight")
        )
        lite_start = makefile.split(lite_definition, 1)[1].split("\n\n", 1)[0]
        self.assertLess(lite_start.index(" compose "), lite_start.index("startup_health.py configure"))
        self.assertLess(lite_start.index("startup_health.py configure"), lite_start.index("up -d --wait"))
        self.assertLess(
            lite_start.index("up --no-start --no-deps netns-holder"),
            lite_start.index(
                "up --no-start --no-deps --force-recreate "
                "onyx-host-egress-proxy onyx-host-egress-bridge"
            ),
        )

        full_definition = next(
            line for line in makefile.splitlines()
            if line.startswith("up-full: wrapper-config-preflight")
        )
        full_start = makefile.split(full_definition, 1)[1].split("\n\n", 1)[0]
        self.assertEqual(full_start.count("startup_health.py configure"), 2)
        self.assertEqual(full_start.count("--skip-capability-check"), 2)
        self.assertEqual(lite_start.count("--skip-capability-check"), 1)
        self.assertIn("create local-embedding-shim", full_start)
        self.assertLess(
            full_start.index("create local-embedding-shim"),
            full_start.index("up -d --wait --wait-timeout 420 local-embedding-shim"),
        )
        self.assertEqual(lite_start.count("--wait-timeout 420"), 1)
        self.assertEqual(full_start.count("--wait-timeout 420"), 2)
        self.assertEqual(lite_start.count("assert-healthy"), 1)
        self.assertEqual(full_start.count("assert-healthy"), 1)
        for start in (lite_start, full_start):
            self.assertIn("--service onyx-host-egress-bridge", start)
        self.assertLess(
            full_start.index("up --no-start --no-deps netns-holder"),
            full_start.index(
                "up --no-start --no-deps --force-recreate "
                "onyx-host-egress-proxy onyx-host-egress-bridge"
            ),
        )

    def test_stack_start_prerequisites_are_serialized(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn(".NOTPARALLEL: up-lite up-full", makefile)

    def test_podman_never_selects_executor_network_overlay(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        selection = makefile.split("CODE_INTERPRETER_NETWORK_SUFFIX :=", 1)[1].split(
            "# When EGRESS_UPSTREAM_PROXY_URL", 1
        )[0]
        self.assertIn("ifneq ($(PODMAN_SELECTED),true)", selection)

    def test_podman_has_no_vpn_socket_suppression_overlay(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertNotIn("PODMAN_VPN_OVERRIDE_FILE", makefile)
        self.assertNotIn("PODMAN_VPN_COMPOSE_SUFFIX", makefile)
        self.assertNotIn("VPN_AUTOHEAL", makefile)
        self.assertFalse((ROOT / "docker-compose.podman-vpn.yml").exists())

    def test_embedding_proxy_uses_absolute_identity_without_child_record(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        start = makefile.split("embedserv-start-if-installed:", 1)[1].split(
            "embedserv-stop-if-started:", 1
        )[0]
        self.assertIn('proxy_script="$(PWD)/$(EMBEDSERV_DIR)/idle_embedding_proxy.py"', start)
        self.assertIn('"$(PWD)/$(HOST_PROCESS_MANAGER)" start', start)
        self.assertIn("--identity", start)
        self.assertIn("--fingerprint-file", start)
        self.assertNotIn("--allow-untracked-listener", start)
        self.assertNotIn("--child-pid-file", start)
        self.assertIn("--require-executable", start)
        self.assertIn("--require-directory", start)
        stop_target = makefile.split("embedserv-stop-if-started:", 1)[1].split(
            "embedserv-stop-after-custom-ready:", 1
        )[0]
        self.assertIn('"$(PWD)/$(HOST_PROCESS_MANAGER)" stop', stop_target)
        self.assertLess(
            stop_target.index('if [ ! -e "$(EMBEDSERV_PID_FILE)" ]'),
            stop_target.index('"$(PWD)/$(HOST_PROCESS_MANAGER)" stop'),
        )
        self.assertIn("--identity", stop_target)
        stop = makefile.split("down-full:", 1)[1].split("ps-lite:", 1)[0]
        self.assertNotIn("embedserv-cleanup-recorded-child", stop)
        self.assertNotIn("embedserv-serve:", makefile)

    def test_podman_full_start_manages_host_document_server(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        full_prerequisites = next(
            line
            for line in makefile.splitlines()
            if line.startswith("up-full: wrapper-config-preflight")
        )
        self.assertIn("$(FULL_MODE_HOST_PROCESS_TARGETS)", full_prerequisites)
        selection = makefile.split(
            "FULL_MODE_HOST_PROCESS_TARGETS :=", 1
        )[1].split(".PHONY:", 1)[0]
        self.assertIn("embedserv-start-if-installed", selection)
        self.assertIn("ifeq ($(PODMAN_SELECTED),true)", selection)
        self.assertIn(
            "FULL_MODE_HOST_PROCESS_TARGETS += podman-doc-server-start", selection
        )
        start_target = makefile.split("podman-doc-server-start:", 1)[1].split(
            "\n\n", 1
        )[0]
        self.assertIn("doc_drop_webserver.py", start_target)
        self.assertIn('"$(PWD)/$(HOST_PROCESS_MANAGER)" start', start_target)
        self.assertIn("--loopback-peers-only", start_target)
        self.assertIn("--identity", start_target)
        self.assertIn("--fingerprint-file", start_target)
        self.assertIn("/_health", start_target)
        self.assertIn("PODMAN_DOC_SERVER_PID_FILE", makefile)
        self.assertIn("podman-doc-server-stop-if-started", makefile)
        self.assertNotIn("stage-podman-full-docs", makefile)
        self.assertNotIn("PODMAN_RAG_DOC_VOLUME", makefile)

    def test_evaluated_host_process_selection_matches_engine(self) -> None:
        selections: dict[str, str] = {}
        for engine in ("docker", "podman"):
            result = subprocess.run(
                [
                    "make",
                    "-np",
                    "help",
                    f"CONTAINER_BIN={engine}",
                    "ENV_FILE=/dev/null",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            line = next(
                line
                for line in result.stdout.splitlines()
                if line.startswith("FULL_MODE_HOST_PROCESS_TARGETS :=")
            )
            selections[engine] = line.split(":=", 1)[1].strip()

        self.assertEqual(selections["docker"], "embedserv-start-if-installed")
        self.assertEqual(
            selections["podman"],
            "embedserv-start-if-installed podman-doc-server-start",
        )

    def test_evaluated_embedding_selection_matrix_is_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            empty = root / "empty.env"
            empty.write_text("", encoding="utf-8")
            bundled = self._make_database(empty)
            self.assertEqual(
                self._simple_make_value(bundled, "EMBEDSERV_MODE"), "bundled"
            )
            self.assertEqual(
                self._simple_make_value(
                    bundled, "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL"
                ),
                "http://host.docker.internal:3210/v1/embeddings",
            )

            custom = root / "custom.env"
            custom.write_text(
                "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL="
                "http://host.docker.internal:8337/v1/embeddings\n"
                "ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL=model$file\n"
                "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL=served$file\n",
                encoding="utf-8",
            )
            custom_database = self._make_database(custom)
            self.assertEqual(
                self._simple_make_value(custom_database, "EMBEDSERV_MODE"),
                "custom",
            )
            self.assertEqual(
                self._simple_make_value(
                    custom_database, "ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL"
                ),
                "model$$file",
            )
            self.assertEqual(
                self._simple_make_value(
                    custom_database, "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL"
                ),
                "served$$file",
            )

            overridden = self._make_database(
                custom,
                environment={
                    "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL": (
                        "http://host.docker.internal:3210/v1/embeddings"
                    ),
                    "ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL": "environment$model",
                },
                assignments=(
                    "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL=command$model",
                ),
            )
            self.assertEqual(
                self._simple_make_value(overridden, "EMBEDSERV_MODE"),
                "bundled",
            )
            self.assertEqual(
                self._simple_make_value(
                    overridden, "ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL"
                ),
                "environment$$model",
            )
            self.assertEqual(
                self._simple_make_value(
                    overridden, "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL"
                ),
                "command$$model",
            )

    def test_embedding_dollars_survive_export_and_recursive_make(self) -> None:
        names = (
            "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL",
            "ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL",
            "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings_file = root / "wrapper.env"
            probe_makefile = root / "probe.mk"
            probe_makefile.write_text(
                f"include {ROOT / 'Makefile'}\n"
                ".PHONY: probe probe-child\n"
                "probe:\n"
                f'\t@$(MAKE) --no-print-directory -f "{probe_makefile}" probe-child\n'
                "probe-child:\n"
                "\t@python3 -c 'import json, os; "
                f"print(json.dumps([os.environ[name] for name in {json.dumps(names)}]))'\n",
                encoding="utf-8",
            )
            clean_environment = dict(os.environ)
            for name in names:
                clean_environment.pop(name, None)

            cases = (
                (
                    "file",
                    (
                        "http://host.docker.internal:8337/v1/embeddings?tag=$file",
                        "file$model",
                        "file$served",
                    ),
                    {},
                    (),
                ),
                (
                    "environment",
                    (
                        "http://host.docker.internal:8337/v1/embeddings?tag=$environment",
                        "environment$model",
                        "environment$served",
                    ),
                    {
                        names[0]: (
                            "http://host.docker.internal:8337/"
                            "v1/embeddings?tag=$environment"
                        ),
                        names[1]: "environment$model",
                        names[2]: "environment$served",
                    },
                    (),
                ),
                (
                    "command line",
                    (
                        "http://host.docker.internal:8337/v1/embeddings?tag=$command",
                        "command$model",
                        "command$served",
                    ),
                    {},
                    (
                        f"{names[0]}=http://host.docker.internal:8337/"
                        "v1/embeddings?tag=$command",
                        f"{names[1]}=command$model",
                        f"{names[2]}=command$served",
                    ),
                ),
            )
            for origin, expected, environment_overrides, assignments in cases:
                with self.subTest(origin=origin):
                    settings_file.write_text(
                        "\n".join(
                            f'{name}="{value}"'
                            for name, value in zip(names, expected, strict=True)
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    environment = clean_environment | environment_overrides
                    result = subprocess.run(
                        [
                            "make",
                            "--no-print-directory",
                            "-f",
                            str(probe_makefile),
                            "probe",
                            f"ENV_FILE={settings_file}",
                            *assignments,
                        ],
                        cwd=ROOT,
                        env=environment,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(tuple(json.loads(result.stdout)), expected)

    def test_lite_and_custom_embedding_skip_unused_host_manager(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        lite_prerequisites = next(
            line
            for line in makefile.splitlines()
            if line.startswith("up-lite: wrapper-config-preflight")
        )
        self.assertNotIn("FULL_MODE_HOST_PROCESS_TARGETS", lite_prerequisites)
        self.assertNotIn("embedserv-start-if-installed", lite_prerequisites)
        self.assertNotIn("podman-doc-server-start", lite_prerequisites)

        start = makefile.split("embedserv-start-if-installed:", 1)[1].split(
            "embedserv-stop-if-started:", 1
        )[0]
        custom_branch = start.split(
            'if [ "$(EMBEDSERV_MODE)" = "custom" ]; then', 1
        )[1].split("\tfi; \\\n", 1)[0]
        self.assertNotIn("embedserv-stop-if-started", custom_branch)
        self.assertNotIn('"$(PWD)/$(HOST_PROCESS_MANAGER)" start', custom_branch)
        full = makefile.split(
            "up-full: wrapper-config-preflight", 1
        )[1].split("embedding-ready-once:", 1)[0]
        full_lines = [
            line.strip()
            for line in full.splitlines()
            if line.startswith("\t@")
        ]
        staged_start = next(
            line
            for line in full_lines
            if "up -d --wait --wait-timeout 420 local-embedding-shim" in line
        )
        readiness = next(
            line
            for line in full_lines
            if "$(MAKE) --no-print-directory embedding-ready-once" in line
        )
        cleanup = next(
            line
            for line in full_lines
            if "$(MAKE) --no-print-directory embedserv-stop-after-custom-ready"
            in line
        )
        self.assertLess(
            full_lines.index(staged_start),
            full_lines.index(readiness),
        )
        self.assertLess(
            full_lines.index(readiness),
            full_lines.index(cleanup),
        )
        for line in (staged_start, readiness, cleanup):
            self.assertTrue(line.startswith("@"))
            self.assertNotIn("|| true", line)
        self.assertNotIn(";", readiness)
        self.assertNotIn(";", cleanup)

    def test_stack_targets_use_canonical_embedding_url_without_legacy_boolean(
        self,
    ) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertNotIn("EGRESS_PROXY_BUNDLED_MLX_HOST_ACCESS", makefile)
        self.assertNotIn("FULL_BUNDLED_MLX_HOST_ACCESS", makefile)
        self.assertIn("export ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL", makefile)
        full_prerequisites = next(
            line
            for line in makefile.splitlines()
            if line.startswith("up-full: wrapper-config-preflight")
        )
        self.assertIn("$(FULL_MODE_HOST_PROCESS_TARGETS)", full_prerequisites)

    def test_clean_teep_start_does_not_execute_host_manager(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            env_file = temporary / "wrapper.env"
            env_file.write_text(
                "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL="
                "http://host.docker.internal:8337/v1/embeddings\n",
                encoding="utf-8",
            )
            environment = dict(os.environ)
            for name in (
                "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL",
                "ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL",
                "ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL",
            ):
                environment.pop(name, None)
            result = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "embedserv-start-if-installed",
                    f"ENV_FILE={env_file}",
                    "HOST_PROCESS_MANAGER=/must/not/be/executed.py",
                    f"EMBEDSERV_PID_FILE={temporary / 'serve.pid'}",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("custom upstream; not starting bundled MLX server", result.stdout)

    def test_embedding_mutations_stop_at_malformed_wrapper_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = root / "invalid.env"
            invalid.write_text(
                'ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL="unterminated\n',
                encoding="utf-8",
            )
            for target in ("embedserv-install", "embedserv-verify-model"):
                with self.subTest(target=target):
                    result = subprocess.run(
                        [
                            "make",
                            "--no-print-directory",
                            target,
                            f"ENV_FILE={invalid}",
                            f"EMBEDSERV_VENV={root / 'must-not-exist'}",
                            f"EMBEDSERV_MODEL_CACHE={root / 'models'}",
                        ],
                        cwd=ROOT,
                        env={
                            key: value
                            for key, value in os.environ.items()
                            if not key.startswith("ONYX_RAG_EMBEDDING_")
                        },
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("invalid wrapper setting", result.stderr)
                    self.assertFalse((root / "must-not-exist").exists())
                    self.assertFalse((root / "models").exists())

    def test_missing_default_embedding_server_reports_setup_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            env_file = temporary / "wrapper.env"
            env_file.write_text("", encoding="utf-8")
            result = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "embedserv-start-if-installed",
                    f"ENV_FILE={env_file}",
                    f"EMBEDSERV_VENV={temporary / 'missing-venv'}",
                    f"EMBEDSERV_MODEL_CACHE={temporary / 'missing-models'}",
                    "HOST_PROCESS_MANAGER=/must/not/be/executed.py",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("full mode has no usable embedding endpoint", output)
        self.assertIn("On macOS, run: make embedserv-install", output)
        self.assertIn(
            'ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL="http://host.docker.internal:8337/v1/embeddings"',
            output,
        )
        self.assertIn(
            'ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL="neardirect:Qwen/Qwen3-Embedding-0.6B"',
            output,
        )
        self.assertIn("custom OpenAI-compatible /v1/embeddings endpoint", output)
        self.assertNotIn("required executable is unavailable", output)

    def test_embedserv_install_finishes_with_model_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            env_file = temporary / "wrapper.env"
            env_file.write_text("", encoding="utf-8")
            fake_bin = temporary / "bin"
            fake_bin.mkdir()
            fake_uv = fake_bin / "uv"
            fake_uv.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_uv.chmod(0o755)

            venv = temporary / "venv"
            (venv / "bin").mkdir(parents=True)
            fake_python = venv / "bin" / "python"
            fake_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_python.chmod(0o755)
            fake_hf = venv / "bin" / "hf"
            fake_hf.write_text(
                "#!/bin/sh\necho model-integrity-verified\n", encoding="utf-8"
            )
            fake_hf.chmod(0o755)

            model_cache = temporary / "models"
            (model_cache / "majentik/harrier-oss-v1-0.6b-MLX-8bit").mkdir(
                parents=True
            )
            relative_venv = os.path.relpath(venv, ROOT)
            relative_models = os.path.relpath(model_cache, ROOT)
            result = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "embedserv-install",
                    f"ENV_FILE={env_file}",
                    f"EMBEDSERV_VENV={relative_venv}",
                    f"EMBEDSERV_MODEL_CACHE={relative_models}",
                    "EMBEDSERV_REQUIREMENTS=/dev/null",
                    f"EMBEDSERV_DIR={os.path.relpath(temporary / 'embedserv', ROOT)}",
                ],
                cwd=ROOT,
                env={**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
                capture_output=True,
                text=True,
                check=False,
            )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("model-integrity-verified", output)
        self.assertIn("Model ready and verified", output)

    def test_embedserv_verify_requires_an_existing_installation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            env_file = temporary / "wrapper.env"
            env_file.write_text("", encoding="utf-8")
            result = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "embedserv-verify-model",
                    f"ENV_FILE={env_file}",
                    f"EMBEDSERV_VENV={temporary / 'missing-venv'}",
                    f"EMBEDSERV_MODEL_CACHE={temporary / 'missing-models'}",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("Run 'make embedserv-install' first", output)

    def test_host_manager_path_and_inactive_stop_guards_are_explicit(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn(
            "HOST_PROCESS_MANAGER := $(EMBEDSERV_DIR)/host_process_manager.py",
            makefile,
        )
        self.assertNotIn("\n\t\thost_process_manager.py \\\n", makefile)
        podman_stop = makefile.split(
            "podman-doc-server-stop-if-started:", 1
        )[1].split("\n\n", 1)[0]
        self.assertLess(
            podman_stop.index('if [ ! -e "$(PODMAN_DOC_SERVER_PID_FILE)" ]'),
            podman_stop.index('"$(PWD)/$(HOST_PROCESS_MANAGER)" stop'),
        )

    def test_podman_shared_database_preflights_are_unconditional(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        lite_prerequisites = next(
            line
            for line in makefile.splitlines()
            if line.startswith("up-lite: wrapper-config-preflight")
        )
        full_prerequisites = next(
            line
            for line in makefile.splitlines()
            if line.startswith("up-full: wrapper-config-preflight")
        )
        self.assertIn("prepare-podman-postgres-data", lite_prerequisites)
        self.assertIn("prepare-podman-postgres-data", full_prerequisites)
        self.assertIn("prepare-podman-opensearch-data", full_prerequisites)
        self.assertNotIn("prepare-podman-opensearch-data", lite_prerequisites)
        self.assertNotIn("PODMAN_SHARE_DOCKER_", makefile)
        self.assertLess(
            lite_prerequisites.index("onyx-image-ready"),
            lite_prerequisites.index("prepare-podman-postgres-data"),
        )
        self.assertLess(
            full_prerequisites.index("onyx-image-ready"),
            full_prerequisites.index("prepare-podman-postgres-data"),
        )
        self.assertIn("up-lite: PODMAN_START_FILES=$(LITE_FILES)", makefile)
        self.assertIn("up-full: PODMAN_START_FILES=$(FULL_FILES)", makefile)
        postgres_preflight = makefile.split(
            "prepare-podman-postgres-data:", 1
        )[1].split("\n\n", 1)[0]
        self.assertIn("initialize-postgres", postgres_preflight)
        self.assertIn("COMPOSE_FILE=$(PODMAN_START_FILES)", postgres_preflight)

    def test_podman_keep_id_containers_are_created_serially(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        lite_recipe = makefile.split(
            "up-lite: wrapper-config-preflight", 1
        )[1].split("\n\nup-full:", 1)[0]
        full_recipe = makefile.split(
            "up-full: wrapper-config-preflight", 1
        )[1].split("\n\nembedding-ready-once:", 1)[0]

        lite_postgres = 'up --no-start --no-deps relational_db'
        lite_tor = 'up --no-start --no-deps tor'
        lite_graph = 'compose $(ONYX_COMPOSE_ENV_FILES) create\n'
        self.assertLess(lite_recipe.index(lite_postgres), lite_recipe.index(lite_tor))
        self.assertLess(lite_recipe.index(lite_tor), lite_recipe.index(lite_graph))

        full_postgres = 'up --no-start --no-deps relational_db'
        full_opensearch = 'up --no-start --no-deps opensearch'
        full_tor = 'up --no-start --no-deps tor'
        full_shim = 'create local-embedding-shim'
        full_graph = 'compose $(ONYX_COMPOSE_ENV_FILES) create\n'
        self.assertLess(
            full_recipe.index(full_postgres), full_recipe.index(full_opensearch)
        )
        self.assertLess(full_recipe.index(full_opensearch), full_recipe.index(full_tor))
        self.assertLess(full_recipe.index(full_tor), full_recipe.index(full_shim))
        self.assertLess(full_recipe.index(full_shim), full_recipe.index(full_graph))

    def test_shared_data_engine_claim_wraps_stack_lifecycle(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        claim_target = makefile.split("claim-shared-data-engine:", 1)[1].split(
            "\n\n", 1
        )[0]
        self.assertNotIn("wrapper-config-preflight", claim_target)
        self.assertIn(
            "SHARED_DATA_GUARD_ENV := $(if $(filter true,$(PODMAN_SELECTED)),env -u DOCKER_HOST,)",
            makefile,
        )
        self.assertIn(
            "@$(SHARED_DATA_GUARD_ENV) python3 podman/shared_data_engine.py claim",
            makefile,
        )
        for target in ("up-lite:", "up-full:"):
            definitions = [
                line for line in makefile.splitlines() if line.startswith(target)
            ]
            self.assertTrue(
                any("claim-shared-data-engine" in line for line in definitions)
            )
            self.assertTrue(
                any("wrapper-config-preflight" in line for line in definitions)
            )
        for target in ("down-lite:", "down-full:"):
            recipe = makefile.split(target, 1)[1].split("\n\n", 1)[0]
            self.assertIn("release-shared-data-engine", recipe)

    def test_podman_excludes_socket_only_code_interpreter_and_pulls_directly(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn(
            "ONYX_STACK_REQUIRED_IMAGES := $(ONYX_BACKEND_IMAGE) $(ONYX_WEB_SERVER_IMAGE)",
            makefile,
        )
        onyx_build = makefile.split("onyx-build:", 1)[1].split("\n\nmyst-image-ready:", 1)[0]
        podman_build = onyx_build.split("else", 1)[0]
        self.assertIn('"$(CONTAINER_BIN)" pull', podman_build)
        self.assertNotIn("ONYX_INSTALL_WRAPPER", podman_build)


if __name__ == "__main__":
    unittest.main()
