from __future__ import annotations

import asyncio
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

        class AsyncBaseTransport:
            pass

        class AsyncHTTPTransport:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.requests = []
                self.closed = False
                transports.append(self)

            async def handle_async_request(self, request):
                self.requests.append(request)
                return SimpleNamespace(delegated_request=request)

            async def aclose(self):
                self.closed = True

        class AsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                clients.append(kwargs)

        class Timeout:
            def __init__(self, timeout, **kwargs):
                self.timeout = timeout
                self.kwargs = kwargs

        class URL(str):
            pass

        class Response:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        httpx_module.AsyncBaseTransport = AsyncBaseTransport
        httpx_module.AsyncHTTPTransport = AsyncHTTPTransport
        httpx_module.AsyncClient = AsyncClient
        httpx_module.Timeout = Timeout
        httpx_module.URL = URL
        httpx_module.Response = Response

        onyx = ModuleType("onyx")
        server = ModuleType("onyx.server")
        security = ModuleType("onyx.server.security")
        models = ModuleType("onyx.server.security.models")
        store = ModuleType("onyx.server.security.store")
        features = ModuleType("onyx.server.features")
        mcp_package = ModuleType("onyx.server.features.mcp")
        mcp_ssrf = ModuleType("onyx.server.features.mcp.ssrf")
        mcp_client = ModuleType("onyx.server.features.mcp.client")
        mcp_oauth = ModuleType("onyx.server.features.mcp.oauth")

        models.SSRFProtectionLevel = SSRFProtectionLevel
        store.get_security_settings = lambda: SimpleNamespace(
            ssrf_protection_level=state.level
        )

        def original_factory(headers=None, timeout=None, auth=None):
            del headers, timeout, auth
            return None

        class OriginalOAuthChallengeTransport:
            def __init__(self, server_url, metadata_url):
                self._server_url = httpx_module.URL(server_url)
                self._metadata_url = metadata_url
                self._challenged = False
                self._delegate = AsyncHTTPTransport()

            async def handle_async_request(self, request):
                if request.method == "GET" and request.url == self._server_url:
                    if not self._challenged:
                        self._challenged = True
                        return httpx_module.Response(
                            status_code=401,
                            headers={
                                "WWW-Authenticate": (
                                    f'Bearer resource_metadata="{self._metadata_url}"'
                                )
                            },
                            request=request,
                        )
                    return httpx_module.Response(status_code=204, request=request)
                return await self._delegate.handle_async_request(request)

        def original_challenge_factory(server_url, metadata_url, auth, timeout):
            return AsyncClient(
                auth=auth,
                follow_redirects=True,
                timeout=timeout,
                transport=OriginalOAuthChallengeTransport(server_url, metadata_url),
            )

        mcp_ssrf.mcp_ssrf_httpx_client_factory = original_factory
        mcp_ssrf.mcp_oauth_challenge_httpx_client_factory = (
            original_challenge_factory
        )
        mcp_ssrf._OAuthChallengeTransport = OriginalOAuthChallengeTransport
        mcp_ssrf._MCP_DEFAULT_TIMEOUT = 30.0
        mcp_ssrf._MCP_DEFAULT_SSE_READ_TIMEOUT = 300.0
        mcp_ssrf.validate_mcp_outbound_url = Mock(
            side_effect=AssertionError(
                "wrapper transport must leave destination enforcement to egress"
            )
        )
        mcp_package.mcp_ssrf = mcp_ssrf
        mcp_client.mcp_ssrf_httpx_client_factory = original_factory
        mcp_oauth.mcp_ssrf_httpx_client_factory = original_factory
        mcp_oauth.mcp_oauth_challenge_httpx_client_factory = (
            original_challenge_factory
        )

        modules = {
            "httpx": httpx_module,
            "onyx": onyx,
            "onyx.server": server,
            "onyx.server.features": features,
            "onyx.server.features.mcp": mcp_package,
            "onyx.server.features.mcp.ssrf": mcp_ssrf,
            "onyx.server.features.mcp.client": mcp_client,
            "onyx.server.features.mcp.oauth": mcp_oauth,
            "onyx.server.security": security,
            "onyx.server.security.models": models,
            "onyx.server.security.store": store,
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
            self.assertIs(
                modules[
                    "onyx.server.features.mcp.client"
                ].mcp_ssrf_httpx_client_factory,
                factory,
            )
            self.assertIs(
                modules[
                    "onyx.server.features.mcp.oauth"
                ].mcp_ssrf_httpx_client_factory,
                factory,
            )
            challenge_factory = (
                mcp_ssrf.mcp_oauth_challenge_httpx_client_factory
            )
            self.assertIs(
                modules[
                    "onyx.server.features.mcp.oauth"
                ].mcp_oauth_challenge_httpx_client_factory,
                challenge_factory,
            )
            client = factory(headers={"x-test": "1"}, auth="auth")
            challenge_client = challenge_factory(
                "https://mcp.example.test",
                "https://mcp.example.test/.well-known/oauth",
                "oauth-auth",
                modules["httpx"].Timeout(30),
            )
        return (
            client,
            challenge_client,
            state,
            transports,
            clients,
            levels,
            mcp_ssrf,
        )

    def test_strict_level_selects_public_proxy_without_wrapper_validation(self) -> None:
        for level in ("VALIDATE_ALL", "VALIDATE_LLM"):
            with self.subTest(level=level):
                (
                    client,
                    challenge_client,
                    _state,
                    transports,
                    clients,
                    _levels,
                    mcp_ssrf,
                ) = self._apply(level)
                self.assertEqual(
                    client.kwargs["transport"].kwargs["proxy"],
                    "http://onyx-public-egress-bridge:3128",
                )
                self.assertEqual(
                    challenge_client.kwargs["transport"]._delegate.kwargs["proxy"],
                    "http://onyx-public-egress-bridge:3128",
                )
                self.assertIn(client.kwargs["transport"], transports)
                self.assertFalse(client.kwargs["trust_env"])
                self.assertFalse(challenge_client.kwargs["trust_env"])
                self.assertTrue(client.kwargs["follow_redirects"])
                self.assertEqual(client.kwargs["headers"], {"x-test": "1"})
                mcp_ssrf.validate_mcp_outbound_url.assert_not_called()

    def test_private_level_selects_host_proxy_and_egress_remains_authoritative(
        self,
    ) -> None:
        for level in ("ALLOW_PRIVATE_NETWORK", "DISABLED"):
            with self.subTest(level=level):
                (
                    client,
                    challenge_client,
                    state,
                    transports,
                    _clients,
                    levels,
                    mcp_ssrf,
                ) = self._apply(level)
                self.assertEqual(
                    client.kwargs["transport"].kwargs["proxy"],
                    "http://onyx-host-egress-bridge:3128",
                )
                self.assertEqual(
                    challenge_client.kwargs["transport"]._delegate.kwargs["proxy"],
                    "http://onyx-host-egress-bridge:3128",
                )
                state.level = levels.DISABLED
                self.assertIn(client.kwargs["transport"], transports)
                mcp_ssrf.validate_mcp_outbound_url.assert_not_called()

    def test_oauth_challenge_state_machine_delegates_and_closes_transport(self) -> None:
        (
            _client,
            challenge_client,
            _state,
            _transports,
            _clients,
            _levels,
            _mcp_ssrf,
        ) = self._apply("VALIDATE_ALL")
        transport = challenge_client.kwargs["transport"]
        server_url = type(transport._server_url)("https://mcp.example.test")
        first_request = SimpleNamespace(method="GET", url=server_url)
        second_request = SimpleNamespace(method="GET", url=server_url)
        delegated_request = SimpleNamespace(method="POST", url=server_url)

        async def exercise():
            first = await transport.handle_async_request(first_request)
            second = await transport.handle_async_request(second_request)
            delegated = await transport.handle_async_request(delegated_request)
            await transport.aclose()
            return first, second, delegated

        first, second, delegated = asyncio.run(exercise())
        self.assertEqual(first.kwargs["status_code"], 401)
        self.assertIn("resource_metadata=", first.kwargs["headers"]["WWW-Authenticate"])
        self.assertEqual(second.kwargs["status_code"], 204)
        self.assertIs(delegated.delegated_request, delegated_request)
        self.assertEqual(transport._delegate.requests, [delegated_request])
        self.assertTrue(transport._delegate.closed)


if __name__ == "__main__":
    unittest.main()
