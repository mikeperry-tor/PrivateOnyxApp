# SPDX-License-Identifier: AGPL-3.0-or-later
"""Google WEB offline engine backed by one direct Obscura navigation."""

import typing as t
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse

from lxml import html

from searx.utils import eval_xpath, eval_xpath_list, extract_text

from searx.engines import _obscura  # type: ignore  # noqa: E402

if t.TYPE_CHECKING:
    from searx.search.processors import RequestParams

engine_type = "offline"

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
no_results_xpath = '//*[@id="topstuff"]//*[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "did not match any documents")]'
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


def search(query: str, params: "RequestParams"):
    """Build the Google URL, navigate once, and parse its rendered DOM."""
    start = (params["pageno"] - 1) * 10
    query_args: t.Dict[str, str] = {"q": query, "hl": "en", "udm": "14"}
    if start:
        query_args["start"] = str(start)
    if params.get("time_range") in time_range_dict:
        query_args["tbs"] = "qdr:" + time_range_dict[params["time_range"]]

    target_url = "https://www.google.com/search?" + urlencode(query_args)
    return _parse_html(
        _obscura.navigate(
            "google2",
            target_url,
            params.get(_obscura.PRE_NAVIGATION_GUARD_PARAM),
        )
    )


def _parse_html(text: str):
    if not text:
        _obscura.parser_mismatch("google2", text, "empty DOM")

    results = []
    dom = html.fromstring(text)

    result_nodes = eval_xpath_list(dom, results_xpath)
    if not result_nodes:
        if eval_xpath(dom, no_results_xpath):
            return []
        _obscura.parser_mismatch("google2", text, "zero title links")

    for result in result_nodes:
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

    if not results:
        _obscura.parser_mismatch("google2", text, "no valid organic rows")

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
