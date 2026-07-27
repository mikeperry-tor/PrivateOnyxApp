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
    exceptions = types.ModuleType("searx.exceptions")

    class SearxEngineCaptchaException(RuntimeError):
        def __init__(self, message: str):
            super().__init__(message)

    exceptions.SearxEngineCaptchaException = SearxEngineCaptchaException
    utils = types.ModuleType("searx.utils")
    utils.eval_xpath = lambda node, expression: node.xpath(expression)
    utils.eval_xpath_getindex = (
        lambda node, expression, index, default=None: (
            node.xpath(expression)[index]
            if len(node.xpath(expression)) > index
            else default
        )
    )
    utils.eval_xpath_list = lambda node, expression: list(node.xpath(expression))
    utils.extract_text = lambda node: " ".join(
        "".join(
            item
            for current in (node if isinstance(node, list) else [node])
            for item in current.itertext()
        ).split()
    )
    sys.modules.update(
        {
            "searx": searx,
            "searx.engines": engines,
            "searx.engines._obscura": obscura_module,
            "searx.exceptions": exceptions,
            "searx.utils": utils,
        }
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
        cls.bing = _load_engine("bing2")
        cls.duckduckgo = _load_engine("duckduckgo2")
        cls.startpage = _load_engine("startpage2")

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

    def test_bing_visible_one_last_step_is_typed_captcha(self):
        challenge = """
        <html><head><title>Search</title></head><body>
          <main><h1>One last step</h1>
          <p>Please solve the challenge below to continue</p></main>
        </body></html>
        """
        with self.assertRaisesRegex(RuntimeError, "verification page"):
            self.bing._parse_html(challenge, "example")

        script_only = """
        <html><body><ol id='b_results'>
          <li class='b_algo'><h2><a href='https://example.com/'>Result</a></h2>
          <p>Snippet</p></li></ol>
          <script>One last step. Please solve the challenge below to continue</script>
        </body></html>
        """
        self.assertEqual(
            self.bing._parse_html(script_only, "result")[0]["url"],
            "https://example.com/",
        )

    def test_bing_rejects_structurally_valid_unrelated_results(self):
        unrelated = """
        <html><body><ol id='b_results'>
          <li class='b_algo'><h2><a href='https://example.com/beef'>
            Beef Wellington Recipe
          </a></h2><div class='b_caption'><p>
            Wrap beef tenderloin and mushrooms in pastry.
          </p></div></li>
        </ol></body></html>
        """
        with self.assertRaisesRegex(ParserMismatch, "do not match query"):
            self.bing._parse_html(unrelated, "Sveriges riksbank styrränta")

    def test_bing_filters_dictionary_domain_labels(self):
        result_and_dictionary = """
        <html><body><ol id='b_results'>
          <li class='b_algo'><h2><a href='https://dictionary.example/integritet'>
            Integritet definition
          </a></h2><a aria-label='dictionary.example'>Source</a>
          <div class='b_caption'><p>A Swedish word.</p></div></li>
          <li class='b_algo'><h2><a href='https://example.com/integritet'>
            Integritet in privacy research
          </a></h2><p>Integritet is relevant to this research.</p></li>
        </ol></body></html>
        """
        self.assertEqual(
            self.bing._parse_html(result_and_dictionary, "integritet research"),
            [
                {
                    "url": "https://example.com/integritet",
                    "title": "Integritet in privacy research",
                    "content": "Integritet is relevant to this research.",
                }
            ],
        )

    def test_bing_still_filters_answer_widget_markers(self):
        result_and_widget = """
        <html><body><ol id='b_results'>
          <li class='b_algo b_ans'><h2><a href='https://example.com/widget'>
            Generic definition
          </a></h2></li>
          <li class='b_algo'><h2><a href='https://example.com/integrity'>
            Integrity in engineering
          </a></h2><p>Integrity protects the complete system.</p></li>
        </ol></body></html>
        """
        self.assertEqual(
            self.bing._parse_html(result_and_widget, "system integrity"),
            [
                {
                    "url": "https://example.com/integrity",
                    "title": "Integrity in engineering",
                    "content": "Integrity protects the complete system.",
                }
            ],
        )

    def test_duckduckgo_anomaly_form_is_typed_captcha(self):
        challenge = """
        <html><head><title>DuckDuckGo</title></head><body>
          <form action="//duckduckgo.com/anomaly.js?sv=html&amp;cc=botnet">
            <p>Unfortunately, bots use DuckDuckGo too.</p>
          </form>
        </body></html>
        """
        with self.assertRaisesRegex(RuntimeError, "verification challenge"):
            self.duckduckgo._parse_html(challenge)

        result = """
        <html><body><div class="result results_links web-result">
          <a class="result__a"
             href="/l/?uddg=https%3A%2F%2Fexample.com%2F">Example</a>
          <a class="result__snippet">Snippet</a>
        </div></body></html>
        """
        self.assertEqual(
            self.duckduckgo._parse_html(result),
            [{"url": "https://example.com/", "title": "Example", "content": "Snippet"}],
        )

    def test_startpage_sanitized_result_and_captcha(self):
        result = """
        <html><body><div class="result css-test">
          <a data-testid="gl-title-link" href="https://example.com/">
            <style>.hidden { color: red }</style><h2>Example</h2>
          </a>
          <div class="description">Snippet</div>
        </div></body></html>
        """
        self.assertEqual(
            self.startpage._parse_html(result),
            [{"url": "https://example.com/", "title": "Example", "content": "Snippet"}],
        )
        with self.assertRaisesRegex(RuntimeError, "captcha page"):
            self.startpage._parse_html(
                '<html><body><form action="/sp/captcha"></form></body></html>'
            )

    def test_every_custom_engine_declares_offline_contract(self):
        for path in sorted((ROOT / "searxng/engines").glob("*2.py")):
            source = path.read_text()
            self.assertIn('engine_type = "offline"', source, path.name)
            self.assertIn("_obscura.navigate(", source, path.name)
            self.assertNotIn("searx.network", source, path.name)


if __name__ == "__main__":
    unittest.main()
