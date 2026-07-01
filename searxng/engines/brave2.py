# SPDX-License-Identifier: AGPL-3.0-or-later
"""Brave Search engine (stealth-browser backed via crw + obscura).

Brave's SERP is a SvelteKit single-page app: the stock ``brave`` engine fetches
HTML directly and Brave rate-limits / returns a JS shell with no results.  This
stub routes the search through crw, which renders the page with obscura (a
stealth headless browser) so the SvelteKit app hydrates and the organic result
cards are present in the DOM.

See ``searxng/engines/_crw.py`` for the scrape helper and architecture.
"""

import typing as t
from urllib.parse import urlencode

from lxml import html

from searx.utils import eval_xpath, eval_xpath_list, extract_text

from searx.engines import _crw  # type: ignore  # noqa: E402

if t.TYPE_CHECKING:
    from searx.extended_types import SXNG_Response
    from searx.search.processors import OnlineParams

about = {
    "website": "https://search.brave.com",
    "wikidata_id": "Q1067723",
    "official_api_documentation": "https://api.search.brave.com/",
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

time_range_dict = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}

# Each organic result is <div class="snippet ..." data-type="web" data-pos="N">.
results_xpath = '//div[@data-type="web"]'
# The clickable title link carries the real result href.
link_xpath = './/a[contains(@class, "l1")]'
# Title text lives in a div whose class contains "title".
title_xpath = './/div[contains(@class, "title")]'
# Snippet description (may be absent when Brave lazy-loads it).
content_xpath = './/p[contains(@class, "snippet-description")] | .//div[contains(@class, "snippet-description")]'
# Visible URL (cite element).
url_xpath = './/cite[contains(@class, "snippet-url")]'


def request(query: str, params: "OnlineParams") -> None:
    """Build the Brave search URL and hand it to crw for stealth rendering."""
    query_args: t.Dict[str, str] = {"q": query}
    if params.get("time_range") in time_range_dict:
        query_args["tf"] = time_range_dict[params["time_range"]]
    if params["pageno"] > 1:
        query_args["offset"] = str((params["pageno"] - 1) * 10)

    target_url = "https://search.brave.com/search?" + urlencode(query_args)
    _crw.crw_scrape_request(params, target_url)


def response(resp: "SXNG_Response"):
    """Parse the rendered Brave SERP HTML returned by crw."""
    text = _crw.extract_crw_html(resp)
    if not text:
        _crw.raise_no_results(
            "brave2",
            reason="empty CRW HTML",
            html_text=text,
        )

    results = []
    dom = html.fromstring(text)

    result_nodes = eval_xpath_list(dom, results_xpath)
    if not result_nodes:
        _crw.raise_no_results(
            "brave2",
            reason="result XPath matched zero web cards",
            html_text=text,
        )

    for result in result_nodes:
        link_nodes = eval_xpath(result, link_xpath)
        if not link_nodes:
            # fall back to the first http link in the card
            link_nodes = eval_xpath(result, './/a[starts-with(@href, "http")]')
        if not link_nodes:
            continue
        url = link_nodes[0].get("href")
        if not url:
            continue

        title_nodes = eval_xpath(result, title_xpath)
        title = extract_text(title_nodes[0]) if title_nodes else ""
        if not title:
            continue

        content_nodes = eval_xpath(result, content_xpath)
        content = extract_text(content_nodes[0]) if content_nodes else ""

        results.append({"url": url, "title": title, "content": content})

    if not results:
        _crw.raise_no_results(
            "brave2",
            reason="web cards matched but no valid organic rows were extracted",
            html_text=text,
        )

    return results
