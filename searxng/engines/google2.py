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
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse

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

# Google SERP result links.  Google rotates class names frequently; the most
# stable hook across classic and udm=14 ("Web") SERPs is still a title anchor
# containing an h3.  Card/snippet lookup happens from the anchor's ancestor
# below, because the snippet is often a sibling of the title link.
results_xpath = '//a[@href][.//h3]'
title_xpath = ".//h3"
result_container_xpath = (
    './ancestor::div[contains(concat(" ", normalize-space(@class), " "), " g ")'
    ' or contains(@class, "MjjYud")'
    ' or contains(@class, "N54PNb")'
    ' or contains(@class, "yuRUbf")'
    " or @data-ved][1]"
)
# Content nodes are best-effort; Google buries snippets in varying containers.
content_xpath = (
    './/div[contains(@class, "VwiC3b")'
    ' or contains(@class, "IsZvec")'
    ' or contains(@class, "kb0PBd")'
    ' or contains(@class, "ITZIwc")'
    " or @data-sncf]"
)


def request(query: str, params: "OnlineParams") -> None:
    """Build the Google search URL and hand it to crw for stealth rendering."""
    start = (params["pageno"] - 1) * 10
    query_args: t.Dict[str, str] = {"q": query, "hl": "en", "udm": "14"}
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

        url = _normalize_result_url(result.get("href"))
        if not url:
            continue

        container_nodes = eval_xpath(result, result_container_xpath)
        content_root = container_nodes[0] if container_nodes else result
        content_nodes = eval_xpath(content_root, content_xpath)
        content = extract_text(content_nodes[0]) if content_nodes else ""

        results.append({"url": url, "title": title, "content": content})

    return results


def _normalize_result_url(raw_url: str | None) -> str:
    """Return an external result URL, or an empty string for Google chrome."""
    if not raw_url:
        return ""

    if raw_url.startswith("/url?"):
        query = parse_qs(urlparse(raw_url).query)
        raw_url = (query.get("q") or query.get("url") or [""])[0]
    elif raw_url.startswith("/"):
        raw_url = urljoin("https://www.google.com", raw_url)

    url = unquote(raw_url)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""

    # Skip Google SERP/navigation links.  Do not blanket-drop every google.com
    # hostname: docs.google.com, support.google.com, etc. can be real results.
    host = parsed.netloc.split("@")[-1].split(":")[0].lower()
    if host in {"www.google.com", "google.com"} and parsed.path.startswith(
        ("/search", "/sorry", "/preferences")
    ):
        return ""

    return url
