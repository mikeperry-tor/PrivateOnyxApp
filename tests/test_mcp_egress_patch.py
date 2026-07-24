from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from enum import Enum
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "onyx"
    / "patches"
    / "shared"
    / "wrapper_env_patches.py"
)


def _load_wrapper_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "wrapper_env_patches_mcp_under_test", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MCPProxyPatchTests(unittest.TestCase):
    @staticmethod
    def _fake_modules(level_name: str):
        class SSRFProtectionLevel(Enum):
            VALIDATE_ALL = "validate_all"
            VALIDATE_LLM = "validate_llm"
            ALLOW_PRIVATE_NETWORK = "allow_private_network"
            DISABLED = "disabled"

        state = SimpleNamespace(level=SSRFProtectionLevel[level_name])
        transports: list[object] = []
        clients: list[dict] = []

        httpx_module = ModuleType("httpx")

        class AsyncHTTPTransport:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                transports.append(self)

        class AsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                clients.append(kwargs)

        class Timeout:
            def __init__(self, timeout, **kwargs):
                self.timeout = timeout
                self.kwargs = kwargs

        httpx_module.AsyncHTTPTransport = AsyncHTTPTransport
        httpx_module.AsyncClient = AsyncClient
        httpx_module.Timeout = Timeout

        onyx = ModuleType("onyx")
        server = ModuleType("onyx.server")
        security = ModuleType("onyx.server.security")
        models = ModuleType("onyx.server.security.models")
        store = ModuleType("onyx.server.security.store")
        tools = ModuleType("onyx.tools")
        implementations = ModuleType("onyx.tools.tool_implementations")
        mcp_package = ModuleType("onyx.tools.tool_implementations.mcp")
        mcp_ssrf = ModuleType("onyx.tools.tool_implementations.mcp.mcp_ssrf")

        models.SSRFProtectionLevel = SSRFProtectionLevel
        store.get_security_settings = lambda: SimpleNamespace(
            ssrf_protection_level=state.level
        )

        def original_factory(headers=None, timeout=None, auth=None):
            del headers, timeout, auth
            return None

        mcp_ssrf.mcp_ssrf_httpx_client_factory = original_factory
        mcp_ssrf._MCP_DEFAULT_TIMEOUT = 30.0
        mcp_ssrf._MCP_DEFAULT_SSE_READ_TIMEOUT = 300.0
        mcp_ssrf.validate_mcp_outbound_url = Mock(
            side_effect=AssertionError(
                "wrapper transport must leave destination enforcement to egress"
            )
        )
        mcp_package.mcp_ssrf = mcp_ssrf

        modules = {
            "httpx": httpx_module,
            "onyx": onyx,
            "onyx.server": server,
            "onyx.server.security": security,
            "onyx.server.security.models": models,
            "onyx.server.security.store": store,
            "onyx.tools": tools,
            "onyx.tools.tool_implementations": implementations,
            "onyx.tools.tool_implementations.mcp": mcp_package,
            "onyx.tools.tool_implementations.mcp.mcp_ssrf": mcp_ssrf,
        }
        return modules, mcp_ssrf, state, transports, clients, SSRFProtectionLevel

    def _apply(self, level_name: str):
        wrapper = _load_wrapper_module()
        modules, mcp_ssrf, state, transports, clients, levels = self._fake_modules(
            level_name
        )
        env = {
            "WRAPPER_PATCH_STRICT": "true",
            "ONYX_MCP_PUBLIC_HTTP_PROXY_URL": "http://onyx-public-egress-bridge:3128",
            "ONYX_MCP_HOST_HTTP_PROXY_URL": "http://onyx-host-egress-bridge:3128",
        }
        with patch.dict(os.environ, env, clear=True), patch.dict(
            sys.modules, modules
        ):
            wrapper.apply_mcp_egress_proxy_patch()
            factory = mcp_ssrf.mcp_ssrf_httpx_client_factory
            client = factory(headers={"x-test": "1"}, auth="auth")
        return client, state, transports, clients, levels, mcp_ssrf

    def test_strict_level_selects_public_proxy_without_wrapper_validation(self) -> None:
        for level in ("VALIDATE_ALL", "VALIDATE_LLM"):
            with self.subTest(level=level):
                client, _state, transports, clients, _levels, mcp_ssrf = self._apply(
                    level
                )
                self.assertEqual(
                    transports[-1].kwargs["proxy"],
                    "http://onyx-public-egress-bridge:3128",
                )
                self.assertIs(client.kwargs["transport"], transports[-1])
                self.assertFalse(client.kwargs["trust_env"])
                self.assertTrue(client.kwargs["follow_redirects"])
                self.assertEqual(clients[-1]["headers"], {"x-test": "1"})
                mcp_ssrf.validate_mcp_outbound_url.assert_not_called()

    def test_private_level_selects_host_proxy_and_egress_remains_authoritative(
        self,
    ) -> None:
        for level in ("ALLOW_PRIVATE_NETWORK", "DISABLED"):
            with self.subTest(level=level):
                client, state, transports, _clients, levels, mcp_ssrf = self._apply(
                    level
                )
                self.assertEqual(
                    transports[-1].kwargs["proxy"],
                    "http://onyx-host-egress-bridge:3128",
                )
                state.level = levels.DISABLED
                self.assertIs(client.kwargs["transport"], transports[-1])
                mcp_ssrf.validate_mcp_outbound_url.assert_not_called()


if __name__ == "__main__":
    unittest.main()
