# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bing Web engine (stealth-browser backed via crw + obscura).

This engine is intentionally configured as a last-resort SearXNG engine in the
wrapper settings. Bing often returns broad, noisy matches, but it can still
provide coverage when stricter engines are blocked or sparse. The companion
SearXNG scoring patch keeps Bing-only results in a fallback tier while allowing
Bing to confirm results found by other engines.

See ``searxng/engines/_crw.py`` for the scrape helper and architecture.
"""

from __future__ import annotations

import base64
import typing as t
from urllib.parse import parse_qs, urlencode, urlparse

from lxml import html

from searx.exceptions import SearxEngineCaptchaException
from searx.utils import eval_xpath, eval_xpath_getindex, eval_xpath_list, extract_text

from searx.engines import _crw  # type: ignore  # noqa: E402

if t.TYPE_CHECKING:
    from searx.extended_types import SXNG_Response
    from searx.search.processors import OnlineParams

about: dict[str, t.Any] = {
    "website": "https://www.bing.com",
    "wikidata_id": "Q182496",
    "official_api_documentation": "https://github.com/MicrosoftDocs/bing-docs",
    "use_official_api": False,
    "require_api_key": False,
    "results": "HTML",
}

categories = ["general", "web"]
paging = True
max_page = 5
time_range_support = False
safesearch = True
language_support = False

_safesearch_map: dict[int, str] = {
    0: "off",
    1: "moderate",
    2: "strict",
}

base_url = "https://www.bing.com"

# Organic Bing results are normally:
#
#   <ol id="b_results">
#     <li class="b_algo">
#       <h2><a href="...">Title</a></h2>
#       <div class="b_caption"><p>Snippet</p></div>
#     </li>
#   </ol>
#
# Keep the result container narrow. Bing mixes knowledge cards, ads, videos, and
# inline widgets into the same page; accepting only b_algo cards avoids most of
# that noise before the last-resort ranking tier even sees it. Some answer
# widgets still masquerade as b_algo cards, so response() applies an additional
# non-web-result filter before extracting the title and link.
results_xpath = '//ol[@id="b_results"]/li[contains(concat(" ", normalize-space(@class), " "), " b_algo ")]'
link_xpath = ".//h2/a[@href]"
content_xpath = (
    './/div[contains(concat(" ", normalize-space(@class), " "), " b_caption ")]//p'
    ' | .//p[contains(@class, "b_lineclamp")]'
    ' | .//div[contains(@class, "b_snippet")]'
    ' | .//p'
)
captcha_xpath = (
    '//title[contains(translate(normalize-space(.), '
    '"ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "captcha")]'
    ' | //form[contains(translate(@action, '
    '"ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "captcha")]'
    ' | //form[contains(translate(@action, '
    '"ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "turing")]'
    ' | //input[contains(translate(@name, '
    '"ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "captcha")]'
)
_xpath_upper = '"ABCDEFGHIJKLMNOPQRSTUVWXYZ"'
_xpath_lower = '"abcdefghijklmnopqrstuvwxyz"'
_non_web_marker_condition = " or ".join(
    [
        f'contains(translate(@class, {_xpath_upper}, {_xpath_lower}), "b_ans")',
        f'contains(translate(@class, {_xpath_upper}, {_xpath_lower}), "b_dict")',
        f'contains(translate(@class, {_xpath_upper}, {_xpath_lower}), "b_entitytp")',
        f'contains(translate(@class, {_xpath_upper}, {_xpath_lower}), "dictionary")',
        f'contains(translate(@class, {_xpath_upper}, {_xpath_lower}), "l_ecrd")',
        f'contains(translate(@id, {_xpath_upper}, {_xpath_lower}), "dictionary")',
        f'contains(translate(@data-appns, {_xpath_upper}, {_xpath_lower}), "dictionary")',
        f'contains(translate(@data-tag, {_xpath_upper}, {_xpath_lower}), "dictionary")',
        f'contains(translate(@aria-label, {_xpath_upper}, {_xpath_lower}), "dictionary")',
    ]
)
non_web_result_block_xpath = (
    f"self::*[{_non_web_marker_condition}] | .//*[{_non_web_marker_condition}]"
)


def request(query: str, params: "OnlineParams") -> None:
    """Build the Bing search URL and hand it to crw for stealth rendering."""
    query_args: dict[str, str | int] = {
        "q": query,
        "adlt": _safesearch_map.get(params.get("safesearch", 0), "off"),
        "setlang": "en",
    }
    if params["pageno"] > 1:
        query_args["first"] = (params["pageno"] - 1) * 10 + 1

    target_url = f"{base_url}/search?{urlencode(query_args)}"
    _crw.crw_scrape_request(
        params,
        target_url,
        extra_headers={"Accept-Language": "en-US,en;q=0.9"},
    )


def _strip_bing_redirect(href: str) -> str:
    """Unwrap Bing ``/ck/a`` redirect links when they carry a real target."""
    parsed = urlparse(href)
    if parsed.netloc not in ("www.bing.com", "bing.com"):
        return href
    if parsed.path != "/ck/a":
        return href

    qs = parse_qs(parsed.query)
    u_values = qs.get("u")
    if not u_values:
        return href

    u_val = u_values[0]
    if not u_val.startswith("a1"):
        return href

    encoded = u_val[2:]
    encoded += "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(encoded).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return href


def _valid_result_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.netloc in ("www.bing.com", "bing.com") and parsed.path in (
        "/search",
        "/ck/a",
        "/aclick",
    ):
        return False
    return True


def _raise_if_captcha(dom) -> None:
    """Bing can return a human-verification page with HTTP 200."""
    if eval_xpath(dom, captcha_xpath):
        raise SearxEngineCaptchaException(
            message="bing2: Bing returned a captcha / verification page via crw",
        )


def _is_non_web_result_block(item) -> bool:
    """Return True for Bing dictionary/answer widgets that mimic web results."""
    return bool(eval_xpath(item, non_web_result_block_xpath))


def _extract_content(item) -> str:
    content_els = eval_xpath(item, content_xpath)
    for node in content_els:
        for icon in node.xpath('.//span[contains(@class, "algoSlug_icon")]'):
            # Do not remove the node: lxml stores the following snippet text in
            # the icon's tail, and removing the element would drop that tail.
            icon.text = ""
    return extract_text(content_els)


def response(resp: "SXNG_Response") -> list[dict[str, t.Any]]:
    """Parse the rendered Bing SERP HTML returned by crw."""
    text = _crw.extract_crw_html(resp)
    if not text:
        _crw.raise_no_results(
            "bing2",
            reason="empty CRW HTML",
            html_text=text,
        )

    dom = html.fromstring(text)
    _raise_if_captcha(dom)

    results: list[dict[str, t.Any]] = []
    result_nodes = eval_xpath_list(dom, results_xpath)
    if not result_nodes:
        _crw.raise_no_results(
            "bing2",
            reason="result XPath matched zero b_algo cards",
            html_text=text,
        )

    for item in result_nodes:
        if _is_non_web_result_block(item):
            continue

        link = eval_xpath_getindex(item, link_xpath, 0, None)
        if link is None:
            continue

        raw_url = link.attrib.get("href", "")
        url = _strip_bing_redirect(raw_url)
        title = extract_text(link)

        if not title or not _valid_result_url(url):
            continue

        results.append(
            {
                "url": url,
                "title": title,
                "content": _extract_content(item),
            }
        )

    if not results:
        _crw.raise_no_results(
            "bing2",
            reason="b_algo cards matched but no valid organic rows were extracted",
            html_text=text,
        )

    return results
