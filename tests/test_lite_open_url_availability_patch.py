from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
from io import StringIO
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "onyx/patches/sitecustomize_api_server/lite_open_url_availability_patch.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "test_lite_open_url_availability_patch_module", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _CompatibleOpenURLTool:
    @classmethod
    def is_available(cls, db_session):  # noqa: ANN001, ARG003
        from onyx.configs.app_configs import DISABLE_VECTOR_DB

        if DISABLE_VECTOR_DB:
            return False
        return True

    def run(self, urls, override_kwargs):  # noqa: ANN001, ARG002
        return run_functions_tuples_in_parallel(  # noqa: F821
            [
                (_retrieve_indexed_with_filters, (all_requests,)),  # noqa: F821
                (self._fetch_web_content, (urls, override_kwargs.url_snippet_map)),
            ],
            allow_failures=True,
        )

    def _retrieve_indexed_documents_with_filters(self, requests):  # noqa: ANN001
        try:
            return self._document_index.id_based_retrieval(requests)
        except Exception as exc:  # noqa: F841
            return IndexedRetrievalResult(  # noqa: F821
                sections=[], missing_document_ids=[]
            )


class _DriftedAvailabilityOpenURLTool(_CompatibleOpenURLTool):
    @classmethod
    def is_available(cls, db_session):  # noqa: ANN001, ARG003
        return True


class _DriftedCrawlerOpenURLTool(_CompatibleOpenURLTool):
    def run(self, urls, override_kwargs):  # noqa: ANN001, ARG002
        return self._fetch_web_content(urls, {})


def _fake_modules(tool_class, *, vector_db_disabled: bool) -> dict[str, ModuleType]:
    modules = {
        name: ModuleType(name)
        for name in (
            "onyx",
            "onyx.configs",
            "onyx.configs.app_configs",
            "onyx.tools",
            "onyx.tools.tool_implementations",
            "onyx.tools.tool_implementations.open_url",
            "onyx.tools.tool_implementations.open_url.open_url_tool",
        )
    }
    modules["onyx.configs.app_configs"].DISABLE_VECTOR_DB = vector_db_disabled
    open_url_tool = modules[
        "onyx.tools.tool_implementations.open_url.open_url_tool"
    ]
    open_url_tool.OpenURLTool = tool_class
    modules["onyx.tools.tool_implementations.open_url"].open_url_tool = open_url_tool
    return modules


class LiteOpenUrlAvailabilityPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_module()

    def test_installs_only_for_disabled_vector_db_and_is_idempotent(self):
        tool_class = type("OpenURLTool", (_CompatibleOpenURLTool,), {})
        modules = _fake_modules(tool_class, vector_db_disabled=True)
        output = StringIO()
        with patch.dict(sys.modules, modules), redirect_stdout(output):
            self.module.install()
            self.module.install()

        self.assertTrue(tool_class.is_available(None))
        self.assertTrue(tool_class._wrapper_lite_availability_patch)
        self.assertEqual(
            output.getvalue().count(
                "installed lite-mode crawler-backed open_url availability"
            ),
            1,
        )

        enabled_tool_class = type("OpenURLTool", (_CompatibleOpenURLTool,), {})
        enabled_modules = _fake_modules(
            enabled_tool_class, vector_db_disabled=False
        )
        with patch.dict(sys.modules, enabled_modules), self.assertRaises(RuntimeError):
            self.module.install()

    def test_rejects_availability_or_crawler_fallback_source_drift(self):
        for base_class in (
            _DriftedAvailabilityOpenURLTool,
            _DriftedCrawlerOpenURLTool,
        ):
            with self.subTest(base_class=base_class.__name__):
                tool_class = type("OpenURLTool", (base_class,), {})
                modules = _fake_modules(tool_class, vector_db_disabled=True)
                with patch.dict(sys.modules, modules), self.assertRaises(RuntimeError):
                    self.module.install()

    def test_compose_scopes_internal_switch_to_lite_mode(self):
        switch = "ONYX_FORCE_OPEN_URL_AVAILABLE"
        lite = (ROOT / "docker-compose.lite.yml").read_text()
        base = (ROOT / "docker-compose.yaml").read_text()
        full = (ROOT / "docker-compose.full.yml").read_text()
        bootstrap = (
            ROOT / "onyx/patches/sitecustomize_api_server/sitecustomize.py"
        ).read_text()

        self.assertIn(f'{switch}: "true"', lite)
        self.assertNotIn(switch, base)
        self.assertNotIn(switch, full)
        self.assertIn("install_lite_open_url_availability()", bootstrap)
        self.assertNotIn("OpenURLTool.is_available =", bootstrap)
        self.assertLess(
            bootstrap.index("install_lite_open_url_availability()"),
            bootstrap.index("install_open_url_failure_reporting()"),
        )


if __name__ == "__main__":
    unittest.main()
