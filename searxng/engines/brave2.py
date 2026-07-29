# SPDX-License-Identifier: AGPL-3.0-or-later
"""Brave Search engine backed by homepage form submission in Obscura.

Brave's SERP is a SvelteKit single-page app: the stock ``brave`` engine fetches
HTML directly and Brave rate-limits / returns a JS shell with no results.  This
engine renders the page with Obscura so the SvelteKit app hydrates and the organic result
cards are present in the DOM.
"""

import typing as t
from lxml import html

from searx.utils import eval_xpath, eval_xpath_list, extract_text

from searx.engines import _obscura  # type: ignore  # noqa: E402

if t.TYPE_CHECKING:
    from searx.search.processors import RequestParams

engine_type = "offline"

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
no_results_xpath = '//*[@id="no-results" or contains(concat(" ", normalize-space(@class), " "), " no-results ")]'
# The clickable title link carries the real result href.
link_xpath = './/a[contains(@class, "l1")]'
# Title text lives in a div whose class contains "title".
title_xpath = './/div[contains(@class, "title")]'
# Snippet description (may be absent when Brave lazy-loads it).
content_xpath = './/p[contains(@class, "snippet-description")] | .//div[contains(@class, "snippet-description")]'
# Visible URL (cite element).
url_xpath = './/cite[contains(@class, "snippet-url")]'


def search(query: str, params: "RequestParams"):
    """Submit the Brave homepage form and parse the rendered result DOM."""
    fixed_fields: list[tuple[str, str]] = []
    if params.get("time_range") in time_range_dict:
        fixed_fields.append(("tf", time_range_dict[params["time_range"]]))
    if params["pageno"] > 1:
        fixed_fields.append(("offset", str((params["pageno"] - 1) * 10)))

    return _parse_html(
        _obscura.submit_search(
            "brave2",
            query,
            tuple(fixed_fields),
            params.get(_obscura.PRE_NAVIGATION_GUARD_PARAM),
        )
    )


def _parse_html(text: str):
    if not text:
        _obscura.parser_mismatch("brave2", text, "empty DOM")

    results = []
    dom = html.fromstring(text)

    result_nodes = eval_xpath_list(dom, results_xpath)
    if not result_nodes:
        if eval_xpath(dom, no_results_xpath):
            return []
        _obscura.parser_mismatch("brave2", text, "zero web cards")

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
        _obscura.parser_mismatch("brave2", text, "no valid organic rows")

    return results
