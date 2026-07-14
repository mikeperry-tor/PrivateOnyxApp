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
    / "sitecustomize_base"
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

    github_module.DEFAULT_MAX_TARBALL_SIZE_BYTES = 500 * 1024 * 1024

    def download_github_repo(
        repo: str,
        github_token: str | None = None,
        max_size_bytes: int = github_module.DEFAULT_MAX_TARBALL_SIZE_BYTES,
    ) -> bytes:
        del repo, github_token, max_size_bytes
        return b""

    github_module.download_github_repo = download_github_repo
    utils_module.github = github_module
    onyx_module.utils = utils_module
    return (
        {
            "onyx": onyx_module,
            "onyx.utils": utils_module,
            "onyx.utils.github": github_module,
        },
        github_module,
    )


class CodingAgentRepoDownloadLimitTests(unittest.TestCase):
    def test_default_limit_matches_code_interpreter_default(self) -> None:
        wrapper = _load_wrapper_module()
        fake_modules, github_module = _fake_onyx_modules()

        with patch.dict(
            os.environ, {"WRAPPER_PATCH_STRICT": "true"}, clear=True
        ), patch.dict(sys.modules, fake_modules):
            wrapper.apply_coding_agent_repo_download_limit_patch()

        expected = 1000 * 1024 * 1024
        self.assertEqual(github_module.DEFAULT_MAX_TARBALL_SIZE_BYTES, expected)
        self.assertEqual(
            github_module.download_github_repo.__defaults__, (None, expected)
        )

    def test_limit_rewrites_constant_and_bound_default(self) -> None:
        wrapper = _load_wrapper_module()
        fake_modules, github_module = _fake_onyx_modules()
        env = {
            "WRAPPER_PATCH_STRICT": "true",
            "ONYX_CODE_INTERPRETER_MAX_FILE_SIZE_MB": "64",
        }

        with patch.dict(os.environ, env, clear=True), patch.dict(
            sys.modules, fake_modules
        ):
            wrapper.apply_coding_agent_repo_download_limit_patch()

        expected = 64 * 1024 * 1024
        self.assertEqual(github_module.DEFAULT_MAX_TARBALL_SIZE_BYTES, expected)
        self.assertEqual(
            github_module.download_github_repo.__defaults__, (None, expected)
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
        fake_modules, github_module = _fake_onyx_modules()

        def changed_download(repo: str, max_bytes: int = 1) -> bytes:
            del repo, max_bytes
            return b""

        github_module.download_github_repo = changed_download
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

    def test_playwright_launch_receives_helper_proxy_without_internal_bypass(
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
        constants_module = ModuleType("onyx.llm.constants")
        utils_module = ModuleType("onyx.utils")
        url_module = ModuleType("onyx.utils.url")
        httpx_module = ModuleType("httpx")
        openai_module = ModuleType("openai")
        clients: list[dict] = []

        class Client:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                clients.append(kwargs)

        class OpenAI:
            def __init__(self, **kwargs):
                self.base_url = kwargs["base_url"]
                self.http_client = kwargs["http_client"]

        class LlmProviderNames:
            OPENAI = "openai"
            OPENAI_COMPATIBLE = "openai_compatible"
            BIFROST = "bifrost"
            LITELLM_PROXY = "litellm_proxy"
            LM_STUDIO = "lm_studio"
            OLLAMA_CHAT = "ollama_chat"

        class LitellmLLM:
            def __init__(
                self,
                api_key,
                model_provider,
                model_name,
                max_input_tokens,
                timeout=30,
                api_base=None,
            ):
                del model_name, max_input_tokens
                self._api_key = api_key
                self._model_provider = model_provider
                self._timeout = timeout
                self._api_base = api_base

            def _completion(self, prompt, client=None):
                return prompt, client

        def validate_outbound_http_url(url, **kwargs):
            del kwargs
            return url

        httpx_module.Client = Client
        openai_module.OpenAI = OpenAI
        constants_module.LlmProviderNames = LlmProviderNames
        multi_llm_module.LitellmLLM = LitellmLLM
        llm_package.multi_llm = multi_llm_module
        url_module.validate_outbound_http_url = validate_outbound_http_url
        utils_module.url = url_module
        onyx_module.llm = llm_package
        onyx_module.utils = utils_module
        return (
            {
                "httpx": httpx_module,
                "openai": openai_module,
                "onyx": onyx_module,
                "onyx.llm": llm_package,
                "onyx.llm.multi_llm": multi_llm_module,
                "onyx.llm.constants": constants_module,
                "onyx.utils": utils_module,
                "onyx.utils.url": url_module,
            },
            multi_llm_module,
            clients,
        )

    def _apply_patch(self):
        wrapper = _load_wrapper_module()
        fake_modules, multi_llm_module, clients = self._fake_modules()
        env = {
            "WRAPPER_PATCH_STRICT": "true",
            "ONYX_CONFIGURED_INFERENCE_HTTP_PROXY_URL": (
                "http://onyx-host-egress-bridge:3128"
            ),
            "ONYX_CONFIGURED_INFERENCE_INTERNAL_BASE_URL": "http://teep:8337/v1",
        }
        return wrapper, fake_modules, multi_llm_module, clients, env

    def test_exact_internal_teep_uses_direct_trust_env_false_client(self) -> None:
        wrapper, fake_modules, multi_llm_module, clients, env = self._apply_patch()
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
        wrapper, fake_modules, multi_llm_module, clients, env = self._apply_patch()
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


if __name__ == "__main__":
    unittest.main()
