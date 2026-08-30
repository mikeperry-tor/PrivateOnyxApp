from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import ModuleType
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
        "wrapper_env_patches_repo_limit_under_test",
        MODULE_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_onyx_modules() -> tuple[dict[str, ModuleType], ModuleType]:
    onyx_module = ModuleType("onyx")
    utils_module = ModuleType("onyx.utils")
    github_module = ModuleType("onyx.utils.github")
    tools_module = ModuleType("onyx.tools")
    fake_tools_module = ModuleType("onyx.tools.fake_tools")
    coding_agent_module = ModuleType("onyx.tools.fake_tools.coding_agent")

    coding_agent_module.CODING_AGENT_GITHUB_MAX_REPO_BYTES = 500 * 1024 * 1024

    def download_github_archive(
        source,
        revision: str,
        authorization_header: str | None = None,
        *,
        max_size_bytes: int,
        timeout: float | tuple[float, float] = 30,
    ) -> bytes:
        del source, revision, authorization_header, max_size_bytes, timeout
        return b""

    def _setup_session():
        download_github_archive(
            object(),
            "HEAD",
            max_size_bytes=CODING_AGENT_GITHUB_MAX_REPO_BYTES,
        )

    coding_agent_module._setup_session = _setup_session
    coding_agent_module.download_github_archive = download_github_archive
    coding_agent_module.CODING_AGENT_GITHUB_MAX_REPO_BYTES = 500 * 1024 * 1024
    github_module.download_github_archive = download_github_archive
    fake_tools_module.coding_agent = coding_agent_module
    tools_module.fake_tools = fake_tools_module
    utils_module.github = github_module
    onyx_module.tools = tools_module
    onyx_module.utils = utils_module
    return (
        {
            "onyx": onyx_module,
            "onyx.tools": tools_module,
            "onyx.tools.fake_tools": fake_tools_module,
            "onyx.tools.fake_tools.coding_agent": coding_agent_module,
            "onyx.utils": utils_module,
            "onyx.utils.github": github_module,
        },
        coding_agent_module,
    )


class CodingAgentRepoDownloadLimitTests(unittest.TestCase):
    def test_default_limit_matches_code_interpreter_default(self) -> None:
        wrapper = _load_wrapper_module()
        fake_modules, coding_agent_module = _fake_onyx_modules()

        with patch.dict(
            os.environ, {"WRAPPER_PATCH_STRICT": "true"}, clear=True
        ), patch.dict(sys.modules, fake_modules):
            wrapper.apply_coding_agent_repo_download_limit_patch()

        expected = 1000 * 1024 * 1024
        self.assertEqual(
            coding_agent_module.CODING_AGENT_GITHUB_MAX_REPO_BYTES, expected
        )

    def test_limit_rewrites_constant_and_bound_default(self) -> None:
        wrapper = _load_wrapper_module()
        fake_modules, coding_agent_module = _fake_onyx_modules()
        env = {
            "WRAPPER_PATCH_STRICT": "true",
            "ONYX_CODE_INTERPRETER_MAX_FILE_SIZE_MB": "64",
        }

        with patch.dict(os.environ, env, clear=True), patch.dict(
            sys.modules, fake_modules
        ):
            wrapper.apply_coding_agent_repo_download_limit_patch()

        expected = 64 * 1024 * 1024
        self.assertEqual(
            coding_agent_module.CODING_AGENT_GITHUB_MAX_REPO_BYTES, expected
        )

    def test_non_positive_limit_fails_in_strict_mode(self) -> None:
        wrapper = _load_wrapper_module()
        env = {
            "WRAPPER_PATCH_STRICT": "true",
            "ONYX_CODE_INTERPRETER_MAX_FILE_SIZE_MB": "0",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "greater than zero"):
                wrapper.apply_coding_agent_repo_download_limit_patch()

    def test_signature_drift_fails_in_strict_mode(self) -> None:
        wrapper = _load_wrapper_module()
        fake_modules, _ = _fake_onyx_modules()

        def changed_download(source, max_bytes: int = 1) -> bytes:
            del source, max_bytes
            return b""

        fake_modules["onyx.utils.github"].download_github_archive = changed_download
        env = {
            "WRAPPER_PATCH_STRICT": "true",
            "ONYX_CODE_INTERPRETER_MAX_FILE_SIZE_MB": "64",
        }
        with patch.dict(os.environ, env, clear=True), patch.dict(
            sys.modules, fake_modules
        ):
            with self.assertRaisesRegex(RuntimeError, "parameters changed"):
                wrapper.apply_coding_agent_repo_download_limit_patch()


class PlaywrightHelperProxyTests(unittest.TestCase):
    @staticmethod
    def _fake_modules():
        onyx_module = ModuleType("onyx")
        utils_module = ModuleType("onyx.utils")
        playwright_module = ModuleType("onyx.utils.playwright_fetch")
        playwright_package = ModuleType("playwright")
        sync_api_module = ModuleType("playwright.sync_api")
        launch_calls: list[dict] = []

        class BrowserType:
            def launch(self, *args, **kwargs):
                del args
                launch_calls.append(kwargs)
                return object()

        class Playwright:
            def __init__(self) -> None:
                self.chromium = BrowserType()

            def stop(self) -> None:
                return None

        class Manager:
            def start(self):
                return Playwright()

        def sync_playwright():
            return Manager()

        playwright_module.sync_playwright = sync_playwright
        sync_api_module.sync_playwright = sync_playwright
        playwright_package.sync_api = sync_api_module
        utils_module.playwright_fetch = playwright_module
        onyx_module.utils = utils_module
        return (
            {
                "onyx": onyx_module,
                "onyx.utils": utils_module,
                "onyx.utils.playwright_fetch": playwright_module,
                "playwright": playwright_package,
                "playwright.sync_api": sync_api_module,
            },
            playwright_module,
            launch_calls,
        )

    def test_playwright_launch_receives_helper_proxy_with_loopback_forced_to_proxy(
        self,
    ) -> None:
        wrapper = _load_wrapper_module()
        fake_modules, playwright_module, launch_calls = self._fake_modules()
        env = {
            "WRAPPER_PATCH_STRICT": "true",
            "ONYX_HELPER_HTTP_PROXY_URL": "http://onyx-public-egress-bridge:3128",
        }

        with patch.dict(os.environ, env, clear=True), patch.dict(
            sys.modules, fake_modules
        ):
            wrapper.apply_playwright_helper_proxy_patch()
            playwright = playwright_module.sync_playwright().start()
            playwright.chromium.launch(headless=True)

        self.assertEqual(
            launch_calls,
            [
                {
                    "headless": True,
                    "proxy": {
                        "server": "http://onyx-public-egress-bridge:3128",
                        "bypass": "<-loopback>",
                    },
                }
            ],
        )

    def test_playwright_existing_proxy_fails_in_strict_mode(self) -> None:
        wrapper = _load_wrapper_module()
        fake_modules, playwright_module, _ = self._fake_modules()
        env = {
            "WRAPPER_PATCH_STRICT": "true",
            "ONYX_HELPER_HTTP_PROXY_URL": "http://onyx-public-egress-bridge:3128",
        }

        with patch.dict(os.environ, env, clear=True), patch.dict(
            sys.modules, fake_modules
        ):
            wrapper.apply_playwright_helper_proxy_patch()
            playwright = playwright_module.sync_playwright().start()
            with self.assertRaisesRegex(RuntimeError, "supplies its own proxy"):
                playwright.chromium.launch(
                    headless=True,
                    proxy={"server": "http://other-proxy:8080"},
                )


class ConfiguredInferenceProxyTests(unittest.TestCase):
    @staticmethod
    def _fake_modules():
        onyx_module = ModuleType("onyx")
        llm_package = ModuleType("onyx.llm")
        multi_llm_module = ModuleType("onyx.llm.multi_llm")
        api_surfaces_module = ModuleType("onyx.llm.api_surfaces")
        constants_module = ModuleType("onyx.llm.constants")
        litellm_module = ModuleType("litellm")
        server_package = ModuleType("onyx.server")
        manage_package = ModuleType("onyx.server.manage")
        manage_llm_package = ModuleType("onyx.server.manage.llm")
        manage_llm_api_module = ModuleType("onyx.server.manage.llm.api")
        utils_module = ModuleType("onyx.utils")
        url_module = ModuleType("onyx.utils.url")
        httpx_module = ModuleType("httpx")
        openai_module = ModuleType("openai")
        clients: list[dict] = []
        model_requests: list[dict] = []

        class HTTPStatusError(Exception):
            pass

        class RequestError(Exception):
            pass

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"data": [{"id": "test-model"}]}

        class Client:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                clients.append(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def get(self, url, **kwargs):
                model_requests.append({"url": url, **kwargs})
                return Response()

        class OpenAI:
            def __init__(self, **kwargs):
                self.base_url = kwargs["base_url"]
                self.http_client = kwargs["http_client"]

        class HTTPHandler:
            def __init__(self, **kwargs):
                self.timeout = kwargs["timeout"]
                self.client = kwargs["client"]

        class LlmApiSurface:
            OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"
            ANTHROPIC_MESSAGES = "anthropic_messages"

        class LlmProviderNames:
            OPENAI = "openai"
            OPENAI_COMPATIBLE = "openai_compatible"
            BIFROST = "bifrost"
            LITELLM_PROXY = "litellm_proxy"
            LM_STUDIO = "lm_studio"
            OLLAMA_CHAT = "ollama_chat"
            PORTKEY = "portkey"

        class LitellmLLM:
            def __init__(
                self,
                api_key,
                model_provider,
                model_name,
                max_input_tokens,
                timeout=30,
                api_base=None,
                custom_config=None,
            ):
                del model_name, max_input_tokens
                self._api_key = api_key
                self._model_provider = model_provider
                self._timeout = timeout
                self._api_base = api_base
                self._api_surface = (
                    LlmApiSurface.ANTHROPIC_MESSAGES
                    if (custom_config or {}).get("portkey_api_mode") == "messages"
                    else LlmApiSurface.OPENAI_CHAT_COMPLETIONS
                )

            def _completion(self, prompt, client=None):
                return prompt, client

        def validate_outbound_http_url(url, **kwargs):
            del kwargs
            return url

        def _get_openai_compatible_models_response(
            url: str,
            source_name: str,
            api_key: str | None = None,
        ) -> dict:
            headers = {"Authorization": f"Bearer {api_key}"}
            try:
                response = httpx.get(url, headers=headers, timeout=10.0)  # noqa: F821
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:  # noqa: F821
                raise RuntimeError(source_name) from e
            except httpx.RequestError as e:  # noqa: F821
                raise RuntimeError(source_name) from e

        httpx_module.Client = Client
        httpx_module.HTTPStatusError = HTTPStatusError
        httpx_module.RequestError = RequestError
        litellm_module.HTTPHandler = HTTPHandler
        openai_module.OpenAI = OpenAI
        api_surfaces_module.LlmApiSurface = LlmApiSurface
        constants_module.LlmProviderNames = LlmProviderNames
        multi_llm_module.LitellmLLM = LitellmLLM
        llm_package.multi_llm = multi_llm_module
        url_module.validate_outbound_http_url = validate_outbound_http_url
        manage_llm_api_module._get_openai_compatible_models_response = (
            _get_openai_compatible_models_response
        )
        manage_llm_package.api = manage_llm_api_module
        manage_package.llm = manage_llm_package
        server_package.manage = manage_package
        utils_module.url = url_module
        onyx_module.llm = llm_package
        onyx_module.server = server_package
        onyx_module.utils = utils_module
        return (
            {
                "httpx": httpx_module,
                "litellm": litellm_module,
                "openai": openai_module,
                "onyx": onyx_module,
                "onyx.llm": llm_package,
                "onyx.llm.api_surfaces": api_surfaces_module,
                "onyx.llm.multi_llm": multi_llm_module,
                "onyx.llm.constants": constants_module,
                "onyx.server": server_package,
                "onyx.server.manage": manage_package,
                "onyx.server.manage.llm": manage_llm_package,
                "onyx.server.manage.llm.api": manage_llm_api_module,
                "onyx.utils": utils_module,
                "onyx.utils.url": url_module,
            },
            multi_llm_module,
            manage_llm_api_module,
            clients,
            model_requests,
        )

    def _apply_patch(self):
        wrapper = _load_wrapper_module()
        (
            fake_modules,
            multi_llm_module,
            manage_llm_api_module,
            clients,
            model_requests,
        ) = self._fake_modules()
        env = {
            "WRAPPER_PATCH_STRICT": "true",
            "ONYX_CONFIGURED_INFERENCE_HTTP_PROXY_URL": (
                "http://onyx-host-egress-bridge:3128"
            ),
            "ONYX_CONFIGURED_INFERENCE_INTERNAL_BASE_URL": "http://teep:8337/v1",
        }
        return (
            wrapper,
            fake_modules,
            multi_llm_module,
            manage_llm_api_module,
            clients,
            model_requests,
            env,
        )

    def test_exact_internal_teep_uses_direct_trust_env_false_client(self) -> None:
        (
            wrapper,
            fake_modules,
            multi_llm_module,
            _manage_llm_api_module,
            clients,
            _model_requests,
            env,
        ) = self._apply_patch()
        with patch.dict(os.environ, env, clear=True), patch.dict(
            sys.modules, fake_modules
        ):
            wrapper.apply_configured_inference_proxy_patch()
            multi_llm_module.LitellmLLM(
                api_key="test",
                model_provider="openai_compatible",
                model_name="model",
                max_input_tokens=1000,
                api_base="http://teep:8337/v1",
            )

        self.assertEqual(clients, [{"trust_env": False, "timeout": 30}])

    def test_other_configured_base_uses_fixed_host_proxy(self) -> None:
        (
            wrapper,
            fake_modules,
            multi_llm_module,
            _manage_llm_api_module,
            clients,
            _model_requests,
            env,
        ) = self._apply_patch()
        with patch.dict(os.environ, env, clear=True), patch.dict(
            sys.modules, fake_modules
        ):
            wrapper.apply_configured_inference_proxy_patch()
            multi_llm_module.LitellmLLM(
                api_key="test",
                model_provider="openai_compatible",
                model_name="model",
                max_input_tokens=1000,
                api_base="http://host.docker.internal:1234/v1",
            )

        self.assertEqual(
            clients,
            [
                {
                    "trust_env": False,
                    "timeout": 30,
                    "proxy": "http://onyx-host-egress-bridge:3128",
                }
            ],
        )

    def test_host_and_lan_model_discovery_use_fixed_host_proxy(self) -> None:
        for url in (
            "http://host.docker.internal:1234/v1/models",
            "http://192.168.10.20:3002/v1/models",
            "http://inference.internal:3002/v1/models",
        ):
            with self.subTest(url=url):
                (
                    wrapper,
                    fake_modules,
                    _multi_llm_module,
                    manage_llm_api_module,
                    clients,
                    model_requests,
                    env,
                ) = self._apply_patch()
                with patch.dict(os.environ, env, clear=True), patch.dict(
                    sys.modules, fake_modules
                ):
                    wrapper.apply_configured_inference_proxy_patch()
                    result = manage_llm_api_module._get_openai_compatible_models_response(
                        url=url,
                        source_name="OpenAI-Compatible",
                    )

                self.assertEqual(result, {"data": [{"id": "test-model"}]})
                self.assertEqual(
                    clients,
                    [
                        {
                            "trust_env": False,
                            "proxy": "http://onyx-host-egress-bridge:3128",
                        }
                    ],
                )
                self.assertEqual(
                    model_requests,
                    [
                        {
                            "url": url,
                            "headers": {
                                "HTTP-Referer": "https://onyx.app",
                                "X-Title": "Onyx",
                            },
                            "timeout": 10.0,
                        }
                    ],
                )

    def test_portkey_modes_use_fixed_host_proxy_clients(self) -> None:
        for custom_config in (None, {"portkey_api_mode": "messages"}):
            with self.subTest(custom_config=custom_config):
                (
                    wrapper,
                    fake_modules,
                    multi_llm_module,
                    _manage_llm_api_module,
                    clients,
                    _model_requests,
                    env,
                ) = self._apply_patch()
                with patch.dict(os.environ, env, clear=True), patch.dict(
                    sys.modules, fake_modules
                ):
                    wrapper.apply_configured_inference_proxy_patch()
                    llm = multi_llm_module.LitellmLLM(
                        api_key="test",
                        model_provider="portkey",
                        model_name="model",
                        max_input_tokens=1000,
                        api_base="https://api.portkey.ai/v1",
                        custom_config=custom_config,
                    )

                self.assertEqual(
                    clients,
                    [
                        {
                            "trust_env": False,
                            "timeout": 30,
                            "proxy": "http://onyx-host-egress-bridge:3128",
                        }
                    ],
                )
                self.assertEqual(
                    llm._wrapper_configured_inference_client.__class__.__name__,
                    "HTTPHandler" if custom_config else "OpenAI",
                )

    def test_exact_internal_teep_model_discovery_is_direct(self) -> None:
        (
            wrapper,
            fake_modules,
            _multi_llm_module,
            manage_llm_api_module,
            clients,
            model_requests,
            env,
        ) = self._apply_patch()
        with patch.dict(os.environ, env, clear=True), patch.dict(
            sys.modules, fake_modules
        ):
            wrapper.apply_configured_inference_proxy_patch()
            manage_llm_api_module._get_openai_compatible_models_response(
                url="http://teep:8337/v1/models",
                source_name="OpenAI-Compatible",
                api_key="test-key",
            )

        self.assertEqual(clients, [{"trust_env": False}])
        self.assertEqual(
            model_requests,
            [
                {
                    "url": "http://teep:8337/v1/models",
                    "headers": {
                        "Authorization": "Bearer test-key",
                        "HTTP-Referer": "https://onyx.app",
                        "X-Title": "Onyx",
                    },
                    "timeout": 10.0,
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
