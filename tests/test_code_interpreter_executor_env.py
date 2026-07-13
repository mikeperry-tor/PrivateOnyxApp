from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "onyx"
    / "patches"
    / "sitecustomize_code_interpreter"
    / "sitecustomize.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "code_interpreter_sitecustomize_under_test",
        MODULE_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _env_arg_dict(args: list[str]) -> dict[str, str]:
    assert len(args) % 2 == 0
    env: dict[str, str] = {}
    for flag, assignment in zip(args[0::2], args[1::2], strict=True):
        assert flag == "-e"
        key, value = assignment.split("=", 1)
        env[key] = value
    return env


class CodeInterpreterExecutorEnvTests(unittest.TestCase):
    def test_disabled_network_does_not_inject_proxy(self) -> None:
        env = dict(os.environ)
        env["WRAPPER_PATCH_STRICT"] = "false"

        with patch.dict(os.environ, env, clear=True):
            module = _load_module()
            self.assertEqual(module._executor_env_vars(), [])

    def test_enabled_network_injects_only_restricted_bridge(self) -> None:
        env = dict(os.environ)
        env["WRAPPER_PATCH_STRICT"] = "false"
        env["ONYX_CODE_INTERPRETER_ENABLE_NETWORK"] = "true"
        env["PYTHON_EXECUTOR_DOCKER_NETWORK"] = "onyx-code-interpreter-executor"
        env["ONYX_AGENT_EXECUTOR_HTTP_PROXY_URL"] = "http://executor-egress-bridge:3128"
        env["ONYX_AGENT_EXECUTOR_NO_PROXY"] = "127.0.0.1,localhost,::1"
        env["ONYX_AGENT_OUTBOUND_PROXY_URL"] = "socks5h://host.docker.internal:9150"

        with patch.dict(os.environ, env, clear=True):
            module = _load_module()
            executor_env = _env_arg_dict(module._executor_env_vars())

        self.assertEqual(executor_env["HTTP_PROXY"], "http://executor-egress-bridge:3128")
        self.assertEqual(executor_env["HTTPS_PROXY"], "http://executor-egress-bridge:3128")
        self.assertEqual(executor_env["ALL_PROXY"], "http://executor-egress-bridge:3128")
        self.assertEqual(executor_env["NO_PROXY"], "127.0.0.1,localhost,::1")
        self.assertNotIn("ONYX_AGENT_OUTBOUND_PROXY_URL", executor_env)

    def test_enabled_network_rejects_shared_namespace(self) -> None:
        env = dict(os.environ)
        env["WRAPPER_PATCH_STRICT"] = "false"
        env["ONYX_CODE_INTERPRETER_ENABLE_NETWORK"] = "true"
        env["PYTHON_EXECUTOR_DOCKER_NETWORK"] = "container:onyx-netns-holder-1"
        env["ONYX_AGENT_EXECUTOR_HTTP_PROXY_URL"] = "http://executor-egress-bridge:3128"

        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "dedicated named Docker network"):
                _load_module()


if __name__ == "__main__":
    unittest.main()
