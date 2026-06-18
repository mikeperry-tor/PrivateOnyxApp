# SPDX-License-Identifier: AGPL-3.0-or-later
"""Google WEB engine (stealth-browser backed via crw + obscura).

This is a *stub* SearXNG engine that fetches Google's SERP through the local
crw Firecrawl-compatible scraper, which in turn drives the obscura stealth CDP
browser.  It exists because the stock ``google`` engine is blocked by Google's
bot protection on most non-residential exit IPs (HTTP 429 / CAPTCHA).

See:
    - Discussion #5651: https://github.com/searxng/searxng/discussions/5651
    - ``searxng/engines/_crw.py`` for the scrape helper.

The request flow reuses SearXNG's own HTTP client: ``request()`` rewrites the
outbound params so the POST goes to crw's ``/v1/scrape`` with the target Google
URL in the JSON body.  ``response()`` decodes the crw envelope and parses the
rendered Google DOM with XPath.

.. note::

    Google's anti-bot is aggressive and IP-reputation based.  Even with
    obscura's stealth mode, a rate-limited VPN exit IP will still get 429s.
    This engine degrades gracefully (returns no results) in that case rather
    than raising, so it doesn't drag down the aggregate result set.
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
    "website": "https://www.google.com",
    "wikidata_id": "Q9366",
    "official_api_documentation": "https://developers.google.com/custom-search/",
    "use_official_api": False,
    "require_api_key": False,
    "results": "HTML",
}

# engine dependent config
categories = ["general", "web"]
paging = True
max_page = 5
time_range_support = False
safesearch = False
language_support = False

time_range_dict = {"day": "d", "week": "w", "month": "m", "year": "y"}

# Google SERP result containers.  Google rotates class names frequently; these
# selectors target the stable structural anchors (data-ved attribute on result
# links + the h3 title).  This mirrors what the stock google.py does but is
# intentionally lenient: we accept several known result-block shapes.
results_xpath = '//div[contains(@class, "g")]//a[./h3] | //div[@data-ved]//a[./h3]'
title_xpath = ".//h3"
# content node is best-effort; Google buries snippets in varying containers.
content_xpath = './/div[contains(@class, "VwiC3b") or contains(@class, "IsZvec")]'


def request(query: str, params: "OnlineParams") -> None:
    """Build the Google search URL and hand it to crw for stealth rendering."""
    start = (params["pageno"] - 1) * 10
    query_args: t.Dict[str, str] = {"q": query, "hl": "en"}
    if start:
        query_args["start"] = str(start)
    if params.get("time_range") in time_range_dict:
        query_args["tbs"] = "qdr:" + time_range_dict[params["time_range"]]

    target_url = "https://www.google.com/search?" + urlencode(query_args)
    _crw.crw_scrape_request(params, target_url)


def response(resp: "SXNG_Response"):
    """Parse the rendered Google SERP HTML returned by crw."""
    text = _crw.extract_crw_html(resp)
    if not text:
        return []

    results = []
    dom = html.fromstring(text)

    for result in eval_xpath_list(dom, results_xpath):
        title_nodes = eval_xpath(result, title_xpath)
        if not title_nodes:
            continue
        title = extract_text(title_nodes[0])
        if not title:
            continue

        raw_url = result.get("href")
        if not raw_url:
            continue
        # strip Google's /url?q= redirector
        url = raw_url
        if raw_url.startswith("/url?q="):
            url = raw_url[7:].split("&sa=")[0]
            from urllib.parse import unquote
            url = unquote(url)

        content_nodes = eval_xpath(result, content_xpath)
        content = extract_text(content_nodes[0]) if content_nodes else ""

        results.append({"url": url, "title": title, "content": content})

    return results
