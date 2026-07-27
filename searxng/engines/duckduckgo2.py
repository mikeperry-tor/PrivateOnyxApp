# SPDX-License-Identifier: AGPL-3.0-or-later
"""DuckDuckGo HTML engine backed by one direct Obscura navigation.

The stock ``duckduckgo`` engine hits captchas on most exit IPs.  This stub uses
DuckDuckGo's lightweight HTML endpoint (``html.duckduckgo.com/html/``) rendered
through Obscura. The HTML endpoint is server-side rendered, while the browser
path retains the wrapper's fingerprint and routing policy.
"""

import typing as t
from urllib.parse import urlencode, unquote

from lxml import html

from searx.exceptions import SearxEngineCaptchaException
from searx.utils import eval_xpath, eval_xpath_list, extract_text

from searx.engines import _obscura  # type: ignore  # noqa: E402

if t.TYPE_CHECKING:
    from searx.search.processors import RequestParams

engine_type = "offline"

about = {
    "website": "https://duckduckgo.com",
    "wikidata_id": "Q1280567",
    "official_api_documentation": "https://duckduckgo.com/api",
    "use_official_api": False,
    "require_api_key": False,
    "results": "HTML",
}

categories = ["general", "web"]
paging = True
max_page = 5
time_range_support = False
safesearch = False
language_support = False

# DDG HTML endpoint result cards: <div class="result results_links ... web-result">
results_xpath = '//div[contains(@class, "result") and contains(@class, "web-result")]'
no_results_xpath = '//*[contains(concat(" ", normalize-space(@class), " "), " no-results ")]'
link_xpath = './/a[contains(@class, "result__a")]'
snippet_xpath = './/a[contains(@class, "result__snippet")] | .//*[contains(@class, "result__snippet")]'
captcha_xpath = (
    '//form[contains(translate(@action, '
    '"ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), '
    '"duckduckgo.com/anomaly.js") '
    'or contains(translate(@action, '
    '"ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "cc=botnet")]'
)


def search(query: str, params: "RequestParams"):
    """Build the DuckDuckGo HTML search URL and navigate it once."""
    query_args: t.Dict[str, str] = {"q": query}
    # DDG HTML endpoint paginates via the next page form, not a clean param;
    # we pass the query and let pageno>1 be a best-effort no-op (DDG HTML
    # returns ~30 results on the first page, enough for SearXNG aggregation).
    target_url = "https://html.duckduckgo.com/html/?" + urlencode(query_args)
    return _parse_html(
        _obscura.navigate(
            "duckduckgo2",
            target_url,
            params.get(_obscura.RESERVATION_PARAM),
        )
    )


def _strip_ddg_redirect(href: str) -> str:
    """DuckDuckGo wraps result links in ``/l/?uddg=<encoded>``.  Unwrap them."""
    if "uddg=" in href:
        # parse the uddg query param
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        uddg = qs.get("uddg", [None])[0]
        if uddg:
            return unquote(uddg)
    return href


def _parse_html(text: str):
    if not text:
        _obscura.parser_mismatch("duckduckgo2", text, "empty DOM")

    results = []
    dom = html.fromstring(text)
    if eval_xpath(dom, captcha_xpath):
        raise SearxEngineCaptchaException(
            message="duckduckgo2: DuckDuckGo returned a verification challenge",
        )

    result_nodes = eval_xpath_list(dom, results_xpath)
    if not result_nodes:
        if eval_xpath(dom, no_results_xpath):
            return []
        _obscura.parser_mismatch("duckduckgo2", text, "zero result cards")

    for result in result_nodes:
        link_nodes = eval_xpath(result, link_xpath)
        if not link_nodes:
            continue
        raw_url = link_nodes[0].get("href")
        if not raw_url:
            continue
        url = _strip_ddg_redirect(raw_url)
        if not url.startswith("http"):
            continue

        title = extract_text(link_nodes[0])
        if not title:
            continue

        snippet_nodes = eval_xpath(result, snippet_xpath)
        content = extract_text(snippet_nodes[0]) if snippet_nodes else ""

        results.append({"url": url, "title": title, "content": content})

    if not results:
        _obscura.parser_mismatch("duckduckgo2", text, "no valid organic rows")

    return results
