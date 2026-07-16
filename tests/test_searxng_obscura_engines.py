from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "browser" / "obscura_client"))


class ParserMismatch(RuntimeError):
    pass


def _install_searx_stubs(obscura_module):
    searx = types.ModuleType("searx")
    engines = types.ModuleType("searx.engines")
    engines.__path__ = []
    engines._obscura = obscura_module
    utils = types.ModuleType("searx.utils")
    utils.eval_xpath = lambda node, expression: node.xpath(expression)
    utils.eval_xpath_list = lambda node, expression: list(node.xpath(expression))
    utils.extract_text = lambda node: " ".join("".join(node.itertext()).split())
    sys.modules.update(
        {"searx": searx, "searx.engines": engines, "searx.engines._obscura": obscura_module,
         "searx.utils": utils}
    )


def _load_engine(name: str):
    path = ROOT / f"searxng/engines/{name}.py"
    spec = importlib.util.spec_from_file_location(f"searx.engines.{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(importlib.util.find_spec("lxml"), "lxml is installed in the SearXNG image")
class SearxngObscuraEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        obscura = types.ModuleType("searx.engines._obscura")
        obscura.navigate = lambda *_args: ""
        obscura.RESERVATION_PARAM = "_wrapper_obscura_reservation_token"

        def mismatch(engine, _text, reason):
            raise ParserMismatch(f"{engine}: {reason}")

        obscura.parser_mismatch = mismatch
        _install_searx_stubs(obscura)
        cls.obscura = obscura
        cls.google = _load_engine("google2")
        cls.brave = _load_engine("brave2")

    def test_google_sanitized_result_and_no_results(self):
        html = """<html><body><div class='g'><a href='/url?q=https%3A%2F%2Fexample.com%2Fa'><h3>Example</h3></a><div class='VwiC3b'>Snippet</div></div></body></html>"""
        self.assertEqual(
            self.google._parse_html(html),
            [{"url": "https://example.com/a", "title": "Example", "content": "Snippet"}],
        )
        no_results = "<html><div id='topstuff'><p>did not match any documents</p></div></html>"
        self.assertEqual(self.google._parse_html(no_results), [])
        with self.assertRaises(ParserMismatch):
            self.google._parse_html("<html><body>changed provider DOM</body></html>")

    def test_brave_sanitized_result_and_no_results(self):
        html = """<html><body><div data-type='web'><a class='l1' href='https://example.org/'><div class='title'>Result</div></a><p class='snippet-description'>Body</p></div></body></html>"""
        self.assertEqual(
            self.brave._parse_html(html),
            [{"url": "https://example.org/", "title": "Result", "content": "Body"}],
        )
        self.assertEqual(self.brave._parse_html("<html><div id='no-results'/></html>"), [])

    def test_search_calls_direct_navigator_once(self):
        dom = """<html><body><div data-type='web'><a class='l1' href='https://example.org/'><div class='title'>Result</div></a></div></body></html>"""
        with patch.object(self.obscura, "navigate", return_value=dom) as navigate:
            self.brave.search("private query", {"pageno": 1, "time_range": None})
        navigate.assert_called_once()
        engine, target, reservation = navigate.call_args.args
        self.assertEqual(engine, "brave2")
        self.assertIn("q=private+query", target)
        self.assertIsNone(reservation)

    def test_every_custom_engine_declares_offline_contract(self):
        for path in sorted((ROOT / "searxng/engines").glob("*2.py")):
            source = path.read_text()
            self.assertIn('engine_type = "offline"', source, path.name)
            self.assertIn("_obscura.navigate(", source, path.name)
            self.assertNotIn("searx.network", source, path.name)


if __name__ == "__main__":
    unittest.main()
