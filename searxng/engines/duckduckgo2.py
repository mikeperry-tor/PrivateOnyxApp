# SPDX-License-Identifier: AGPL-3.0-or-later
"""DuckDuckGo (HTML) engine (stealth-browser backed via crw + obscura).

The stock ``duckduckgo`` engine hits captchas on most exit IPs.  This stub uses
DuckDuckGo's lightweight HTML endpoint (``html.duckduckgo.com/html/``) rendered
through crw + obscura.  The HTML endpoint is server-side rendered, so it works
even via plain HTTP, but routing it through obscura still defeats the captcha
wall that DDG throws at datacenter / VPN IPs.

See ``searxng/engines/_crw.py`` for the scrape helper and architecture.
"""

import typing as t
from urllib.parse import urlencode, unquote

from lxml import html

from searx.utils import eval_xpath, eval_xpath_list, extract_text

from searx.engines import _crw  # type: ignore  # noqa: E402

if t.TYPE_CHECKING:
    from searx.extended_types import SXNG_Response
    from searx.search.processors import OnlineParams

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
link_xpath = './/a[contains(@class, "result__a")]'
snippet_xpath = './/a[contains(@class, "result__snippet")] | .//*[contains(@class, "result__snippet")]'


def request(query: str, params: "OnlineParams") -> None:
    """Build the DuckDuckGo HTML search URL and hand it to crw."""
    query_args: t.Dict[str, str] = {"q": query}
    # DDG HTML endpoint paginates via the next page form, not a clean param;
    # we pass the query and let pageno>1 be a best-effort no-op (DDG HTML
    # returns ~30 results on the first page, enough for SearXNG aggregation).
    target_url = "https://html.duckduckgo.com/html/?" + urlencode(query_args)
    _crw.crw_scrape_request(params, target_url)


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


def response(resp: "SXNG_Response"):
    """Parse the rendered DuckDuckGo HTML SERP returned by crw."""
    text = _crw.extract_crw_html(resp)
    if not text:
        _crw.raise_no_results(
            "duckduckgo2",
            reason="empty CRW HTML",
            html_text=text,
        )

    results = []
    dom = html.fromstring(text)

    result_nodes = eval_xpath_list(dom, results_xpath)
    if not result_nodes:
        _crw.raise_no_results(
            "duckduckgo2",
            reason="result XPath matched zero web-result cards",
            html_text=text,
        )

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
        _crw.raise_no_results(
            "duckduckgo2",
            reason="web-result cards matched but no valid organic rows were extracted",
            html_text=text,
        )

    return results
