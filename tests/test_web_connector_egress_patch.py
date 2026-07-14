from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import patch


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "onyx"
    / "patches"
    / "sitecustomize_background"
    / "sitecustomize.py"
)


class WebConnectorEgressPatchTests(unittest.TestCase):
    def _load_patched_modules(self, level: str):
        requests_module = ModuleType("requests")
        sessions_module = ModuleType("requests.sessions")
        calls: list[tuple[str, str, dict]] = []
        playwright_proxies: list[str | None] = []
        validations: list[tuple[str, dict]] = []

        class Session:
            def __init__(self):
                self.trust_env = True

            def request(self, method, url, **kwargs):
                calls.append((method, url, dict(kwargs)))
                return SimpleNamespace()

        sessions_module.Session = Session
        requests_module.sessions = sessions_module

        connector_module = ModuleType("onyx.connectors.web.connector")

        class WebConnector:
            def __init__(self, base_url: str, web_connector_type: str = "recursive"):
                self.to_visit_list = [base_url]
                if web_connector_type == "sitemap":
                    connector_module.protected_url_check(base_url)
                    Session().request("GET", base_url, timeout=30)

            def load_from_state(self, slim=False):
                del slim
                Session().request("HEAD", self.to_visit_list[0], timeout=30)
                Session().request(
                    "GET", "https://subresource.example/asset.js", timeout=30
                )
                yield "loaded"

        connector_module.WebConnector = WebConnector
        connector_module.protected_url_check = lambda url: None

        models_module = ModuleType("onyx.server.security.models")

        class SSRFProtectionLevel:
            VALIDATE_ALL = "validate_all"

        models_module.SSRFProtectionLevel = SSRFProtectionLevel
        models_module.web_connector_ssrf_enforced = (
            lambda current: current == SSRFProtectionLevel.VALIDATE_ALL
        )

        store_module = ModuleType("onyx.server.security.store")
        store_module.get_security_settings = lambda: SimpleNamespace(
            ssrf_protection_level=level
        )

        url_module = ModuleType("onyx.utils.url")

        def validate_outbound_http_url(url, **kwargs):
            validations.append((url, dict(kwargs)))
            return url

        url_module.validate_outbound_http_url = validate_outbound_http_url

        wrapper_module = ModuleType("wrapper_env_patches")
        wrapper_module.apply_playwright_helper_proxy_patch = lambda: None
        wrapper_module.apply_configured_inference_proxy_patch = lambda: None
        wrapper_module._validated_fixed_proxy_url = (
            lambda env_name, expected_host: os.environ[env_name]
        )

        @contextmanager
        def select_playwright_proxy(proxy_url):
            playwright_proxies.append(proxy_url)
            yield

        wrapper_module.select_playwright_proxy = select_playwright_proxy

        onyx_module = ModuleType("onyx")
        connectors_module = ModuleType("onyx.connectors")
        web_module = ModuleType("onyx.connectors.web")
        server_module = ModuleType("onyx.server")
        security_module = ModuleType("onyx.server.security")
        utils_module = ModuleType("onyx.utils")
        web_module.connector = connector_module
        connectors_module.web = web_module
        security_module.models = models_module
        security_module.store = store_module
        server_module.security = security_module
        utils_module.url = url_module
        onyx_module.connectors = connectors_module
        onyx_module.server = server_module
        onyx_module.utils = utils_module

        fake_modules = {
            "requests": requests_module,
            "requests.sessions": sessions_module,
            "wrapper_env_patches": wrapper_module,
            "onyx": onyx_module,
            "onyx.connectors": connectors_module,
            "onyx.connectors.web": web_module,
            "onyx.connectors.web.connector": connector_module,
            "onyx.server": server_module,
            "onyx.server.security": security_module,
            "onyx.server.security.models": models_module,
            "onyx.server.security.store": store_module,
            "onyx.utils": utils_module,
            "onyx.utils.url": url_module,
        }
        env = {
            "WRAPPER_PATCH_STRICT": "true",
            "ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_ENABLED": "false",
            "ONYX_WEB_CONNECTOR_PUBLIC_HTTP_PROXY_URL": (
                "http://onyx-public-egress-bridge:3128"
            ),
            "ONYX_WEB_CONNECTOR_HOST_HTTP_PROXY_URL": (
                "http://onyx-host-egress-bridge:3128"
            ),
            "ONYX_WEB_CONNECTOR_INTERNAL_BASE_URL": "http://doc-drop-web:8091/",
        }

        spec = importlib.util.spec_from_file_location(
            "sitecustomize_background_egress_under_test", MODULE_PATH
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, env, clear=True), patch.dict(
            sys.modules, fake_modules
        ):
            spec.loader.exec_module(module)

        return connector_module, calls, playwright_proxies, validations

    def test_sitemap_constructor_uses_saved_level_public_proxy(self) -> None:
        connector, calls, _, validations = self._load_patched_modules("validate_all")
        connector.WebConnector(
            "https://docs.example/sitemap.xml", web_connector_type="sitemap"
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0][2]["proxies"],
            {
                "http": "http://onyx-public-egress-bridge:3128",
                "https": "http://onyx-public-egress-bridge:3128",
            },
        )
        self.assertEqual(
            validations,
            [
                (
                    "https://docs.example/sitemap.xml",
                    {
                        "allow_private_network": False,
                        "block_loopback_and_link_local": True,
                        "resolve_dns": False,
                    },
                )
            ],
        )

    def test_doc_drop_and_crawl_subresources_use_host_proxy(self) -> None:
        connector, calls, playwright_proxies, validations = self._load_patched_modules(
            "validate_all"
        )
        instance = connector.WebConnector(
            "http://doc-drop-web:8091/", web_connector_type="sitemap"
        )
        self.assertEqual(list(instance.load_from_state()), ["loaded"])

        expected = {
            "http": "http://onyx-host-egress-bridge:3128",
            "https": "http://onyx-host-egress-bridge:3128",
        }
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call[2]["proxies"] == expected for call in calls))
        self.assertEqual(
            playwright_proxies, ["http://onyx-host-egress-bridge:3128"]
        )
        self.assertEqual(validations, [])

    def test_private_enabled_connector_uses_host_proxy(self) -> None:
        connector, calls, playwright_proxies, _ = self._load_patched_modules(
            "allow_private"
        )
        instance = connector.WebConnector("http://nas.home/docs")
        self.assertEqual(list(instance.load_from_state()), ["loaded"])

        expected = {
            "http": "http://onyx-host-egress-bridge:3128",
            "https": "http://onyx-host-egress-bridge:3128",
        }
        self.assertTrue(all(call[2]["proxies"] == expected for call in calls))
        self.assertEqual(
            playwright_proxies, ["http://onyx-host-egress-bridge:3128"]
        )


if __name__ == "__main__":
    unittest.main()
