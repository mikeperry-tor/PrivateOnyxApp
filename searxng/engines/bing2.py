# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bing Web engine backed by one direct Obscura navigation.

This engine is intentionally configured as a last-resort SearXNG engine in the
wrapper settings. Bing often returns broad, noisy matches, but it can still
provide coverage when stricter engines are blocked or sparse. The companion
SearXNG scoring patch keeps Bing-only results in a fallback tier while allowing
Bing to confirm results found by other engines.

"""

from __future__ import annotations

import base64
import typing as t
from urllib.parse import parse_qs, urlencode, urlparse

from lxml import html

from searx.exceptions import SearxEngineCaptchaException
from searx.utils import eval_xpath, eval_xpath_getindex, eval_xpath_list, extract_text

from searx.engines import _obscura  # type: ignore  # noqa: E402

if t.TYPE_CHECKING:
    from searx.search.processors import RequestParams

engine_type = "offline"

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
no_results_xpath = '//*[@id="b_results"]//*[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "there are no results")]'
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
visible_text_xpath = (
    "//body//text()[not(ancestor::script) and not(ancestor::style) "
    "and not(ancestor::template) and not(ancestor::noscript)]"
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


def search(query: str, params: "RequestParams"):
    """Build the Bing search URL and navigate it once through Obscura."""
    query_args: dict[str, str | int] = {
        "q": query,
        "adlt": _safesearch_map.get(params.get("safesearch", 0), "off"),
        "setlang": "en",
    }
    if params["pageno"] > 1:
        query_args["first"] = (params["pageno"] - 1) * 10 + 1

    target_url = f"{base_url}/search?{urlencode(query_args)}"
    return _parse_html(
        _obscura.navigate(
            "bing2",
            target_url,
            params.get(_obscura.RESERVATION_PARAM),
        )
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
    visible_text = " ".join(
        " ".join(eval_xpath_list(dom, visible_text_xpath)).split()
    ).lower()
    if eval_xpath(dom, captcha_xpath) or (
        "one last step" in visible_text
        and "solve the challenge below to continue" in visible_text
    ):
        raise SearxEngineCaptchaException(
            message="bing2: Bing returned a captcha / verification page",
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


def _parse_html(text: str) -> list[dict[str, t.Any]]:
    if not text:
        _obscura.parser_mismatch("bing2", text, "empty DOM")

    dom = html.fromstring(text)
    _raise_if_captcha(dom)

    results: list[dict[str, t.Any]] = []
    result_nodes = eval_xpath_list(dom, results_xpath)
    if not result_nodes:
        if eval_xpath(dom, no_results_xpath):
            return []
        _obscura.parser_mismatch("bing2", text, "zero b_algo cards")

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
        _obscura.parser_mismatch("bing2", text, "no valid organic rows")

    return results
