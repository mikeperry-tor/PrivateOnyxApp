from __future__ import annotations

import importlib.util
import inspect
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "onyx/patches/sitecustomize_api_server/url_identity_preservation_patch.py"
)


def _load_patch():
    spec = importlib.util.spec_from_file_location(
        "url_identity_preservation_patch_under_test", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normalizer_source() -> str:
    return """
def normalize_url(url: str) -> str:
    # Reconstruct the URL without query string and fragment
    return url.split("?", 1)[0].split("#", 1)[0]
"""


def _canonical_source() -> str:
    return """
def _default_url_normalizer(url: str) -> str | None:
    query = ""  # Query string (removed)
    fragment = ""  # Fragment/hash (removed)
    return url
"""


def _fake_onyx_modules():
    names = (
        "onyx",
        "onyx.tools",
        "onyx.tools.tool_implementations",
        "onyx.tools.tool_implementations.open_url",
        "onyx.tools.tool_implementations.open_url.models",
        "onyx.tools.tool_implementations.open_url.open_url_tool",
        "onyx.tools.tool_implementations.open_url.url_normalization",
        "onyx.tools.tool_implementations.web_search",
        "onyx.tools.tool_implementations.web_search.models",
        "onyx.utils",
        "onyx.utils.url",
    )
    modules = {name: types.ModuleType(name) for name in names}
    for module in modules.values():
        module.__path__ = []

    url_module = modules["onyx.utils.url"]
    exec(_normalizer_source(), url_module.__dict__)
    url_module.normalize_url.__source__ = _normalizer_source()

    open_models = modules[
        "onyx.tools.tool_implementations.open_url.models"
    ]
    open_models.imported_normalize_url = url_module.normalize_url
    exec(
        """
normalize_url = imported_normalize_url

class WebContent:
    @classmethod
    def normalize_link(cls, v: str) -> str:
        return normalize_url(v)
""",
        open_models.__dict__,
    )
    open_models.WebContent.normalize_link.__func__.__source__ = (
        "def normalize_link(cls, v: str) -> str:\n"
        "    return normalize_url(v)\n"
    )

    web_models = modules[
        "onyx.tools.tool_implementations.web_search.models"
    ]
    web_models.imported_normalize_url = url_module.normalize_url
    exec(
        """
normalize_url = imported_normalize_url

class WebSearchResult:
    @classmethod
    def normalize_link(cls, v: str) -> str:
        return normalize_url(v)
""",
        web_models.__dict__,
    )
    web_models.WebSearchResult.normalize_link.__func__.__source__ = (
        "def normalize_link(cls, v: str) -> str:\n"
        "    return normalize_url(v)\n"
    )

    open_url_tool = modules[
        "onyx.tools.tool_implementations.open_url.open_url_tool"
    ]
    open_url_tool.normalize_web_content_url = url_module.normalize_url

    url_normalization = modules[
        "onyx.tools.tool_implementations.open_url.url_normalization"
    ]
    exec(_canonical_source(), url_normalization.__dict__)
    url_normalization._default_url_normalizer.__source__ = _canonical_source()

    return (
        modules,
        url_module,
        web_models,
        open_models,
        open_url_tool,
        url_normalization,
    )


def _install_with_stub_source(patch_module) -> None:
    real_getsource = inspect.getsource

    def getsource(subject):
        source = getattr(subject, "__source__", None)
        return source if source is not None else real_getsource(subject)

    with patch.object(
        patch_module.inspect,
        "getsource",
        side_effect=getsource,
    ):
        patch_module.install()


class UrlIdentityPreservationPatchTests(unittest.TestCase):
    def test_preserves_identity_in_every_generic_url_path(self):
        patch_module = _load_patch()
        (
            modules,
            url_module,
            web_models,
            open_models,
            open_url_tool,
            url_normalization,
        ) = _fake_onyx_modules()
        destination = (
            "https://NEWS.YCOMBINATOR.COM/item/?id=46850588"
            "&ref=search#comments"
        )

        with patch.dict(sys.modules, modules, clear=False):
            _install_with_stub_source(patch_module)

        self.assertEqual(url_module.normalize_url(destination), destination)
        self.assertEqual(
            web_models.WebSearchResult.normalize_link(destination),
            destination,
        )
        self.assertEqual(
            open_models.WebContent.normalize_link(destination),
            destination,
        )
        self.assertEqual(
            open_url_tool.normalize_web_content_url(destination),
            destination,
        )
        self.assertEqual(
            url_normalization._default_url_normalizer(destination),
            (
                "https://news.ycombinator.com/item?id=46850588"
                "&ref=search#comments"
            ),
        )

    def test_install_is_idempotent(self):
        patch_module = _load_patch()
        modules, url_module, *_rest = _fake_onyx_modules()
        with patch.dict(sys.modules, modules, clear=False):
            _install_with_stub_source(patch_module)
            installed = url_module.normalize_url
            _install_with_stub_source(patch_module)
            self.assertIs(url_module.normalize_url, installed)

    def test_source_drift_fails_closed(self):
        patch_module = _load_patch()
        modules, _url_module, web_models, *_rest = _fake_onyx_modules()

        def changed_validator(value: str) -> str:
            return value

        web_models.WebSearchResult.normalize_link = changed_validator
        with patch.dict(sys.modules, modules, clear=False):
            with self.assertRaisesRegex(RuntimeError, "source drift"):
                _install_with_stub_source(patch_module)

    def test_api_bootstrap_installs_patch(self):
        bootstrap = (
            ROOT / "onyx/patches/sitecustomize_api_server/sitecustomize.py"
        ).read_text()
        self.assertIn(
            "from url_identity_preservation_patch import",
            bootstrap,
        )
        self.assertIn("install_url_identity_preservation()", bootstrap)


if __name__ == "__main__":
    unittest.main()
