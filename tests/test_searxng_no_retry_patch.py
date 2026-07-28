from __future__ import annotations

import functools
import importlib.util
import inspect
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "onyx/patches/shared/wrapper_env_patches.py"


def _load_wrapper():
    spec = importlib.util.spec_from_file_location(
        "wrapper_env_patches_searxng_retry_under_test", PATCH_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _single_attempt(self, query: str) -> list:
    response = requests.post(  # noqa: F821
        f"{self._searxng_base_url}/search",
        data={"q": query, "format": "json"},
    )
    response.raise_for_status()
    results = response.json()
    result_list = results.get("results", [])
    return result_list


def _retry_builder_stub(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        return function(*args, **kwargs)

    return wrapped


class SearxngNoRetryPatchTests(unittest.TestCase):
    def test_api_bootstrap_installs_patch(self):
        bootstrap = (
            ROOT / "onyx/patches/sitecustomize_api_server/sitecustomize.py"
        ).read_text()
        self.assertIn(
            "from wrapper_env_patches import apply_searxng_single_attempt_patch",
            bootstrap,
        )
        self.assertIn("apply_searxng_single_attempt_patch()", bootstrap)

    def test_patch_unwraps_only_the_whole_search_retry(self):
        wrapper = _load_wrapper()
        client_module = types.ModuleType(
            "onyx.tools.tool_implementations.web_search.clients.searxng_client"
        )

        class SearXNGClient:
            pass

        retried = _retry_builder_stub(_retry_builder_stub(_single_attempt))
        retried.__source__ = (
            "@retry_builder(tries=3, delay=1, backoff=2)\n"
            "def search(self, query: str):\n"
            "    response = requests.post(...)\n"
            "    response.raise_for_status()\n"
            '    results.get("results", [])\n'
        )
        SearXNGClient.search = retried
        client_module.SearXNGClient = SearXNGClient

        modules = {
            "onyx": types.ModuleType("onyx"),
            "onyx.tools": types.ModuleType("onyx.tools"),
            "onyx.tools.tool_implementations": types.ModuleType(
                "onyx.tools.tool_implementations"
            ),
            "onyx.tools.tool_implementations.web_search": types.ModuleType(
                "onyx.tools.tool_implementations.web_search"
            ),
            "onyx.tools.tool_implementations.web_search.clients": types.ModuleType(
                "onyx.tools.tool_implementations.web_search.clients"
            ),
            (
                "onyx.tools.tool_implementations.web_search.clients."
                "searxng_client"
            ): client_module,
        }
        for module in modules.values():
            module.__path__ = []

        real_getsource = inspect.getsource

        def getsource(subject):
            source = getattr(subject, "__source__", None)
            return source if source is not None else real_getsource(subject)

        with patch.dict(sys.modules, modules, clear=False), patch.object(
            wrapper.inspect, "getsource", side_effect=getsource
        ):
            wrapper.apply_searxng_single_attempt_patch()

        self.assertIs(SearXNGClient.search, _single_attempt)
        self.assertFalse(hasattr(SearXNGClient.search, "__wrapped__"))


if __name__ == "__main__":
    unittest.main()
