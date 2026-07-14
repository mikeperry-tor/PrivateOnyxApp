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


if __name__ == "__main__":
    unittest.main()
