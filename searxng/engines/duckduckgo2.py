# SPDX-License-Identifier: AGPL-3.0-or-later
"""DuckDuckGo No-AI engine backed by homepage form submission in Obscura.

The stock ``duckduckgo`` engine hits captchas on most exit IPs.  This stub uses
DuckDuckGo's JavaScript-rendered No-AI endpoint (``noai.duckduckgo.com``)
through Obscura.  No-AI still loads web results from DuckDuckGo's ``d.js``
endpoint, so this engine waits for network idle before parsing the hydrated
organic-result DOM.
"""

import typing as t
from urllib.parse import parse_qs, urlparse

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
paging = False
time_range_support = False
safesearch = False
language_support = False

# No-AI uses DuckDuckGo's React result DOM.  Select only organic rows and stable
# semantic attributes; its generated class names are intentionally not part of
# the parser contract.
results_xpath = '//li[@data-layout="organic"]'
no_results_xpath = (
    '//*[@data-testid="no-results" or @data-layout="no-results"]'
    ' | //*[contains(concat(" ", normalize-space(@class), " "), " no-results ")]'
)
link_xpath = './/a[@data-testid="result-title-a"]'
snippet_xpath = './/*[@data-result="snippet"]'
captcha_xpath = (
    '//form[@id="challenge-form" or contains(translate(@action, '
    '"ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), '
    '"duckduckgo.com/anomaly.js") '
    'or contains(translate(@action, '
    '"ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "cc=botnet")]'
    '| //*[contains(concat(" ", normalize-space(@class), " "), '
    '" anomaly-modal__modal ")]'
)
unfinished_deep_load_xpath = (
    '//link[@id="deep_preload_link" and contains(@href, "links.duckduckgo.com/d.js")]'
    ' | //script[@id="deep_preload_script"'
    ' and contains(@src, "links.duckduckgo.com/d.js")]'
)
redirect_hosts = frozenset(
    {
        "duckduckgo.com",
        "www.duckduckgo.com",
        "noai.duckduckgo.com",
        "links.duckduckgo.com",
    }
)


def search(query: str, params: "RequestParams"):
    """Submit the DuckDuckGo No-AI homepage form."""
    return _parse_html(
        _obscura.submit_search(
            "duckduckgo2",
            query,
            (("ia", "web"),),
            params.get(_obscura.PRE_NAVIGATION_GUARD_PARAM),
        )
    )


def _strip_ddg_redirect(href: str) -> str:
    """DuckDuckGo wraps result links in ``/l/?uddg=<encoded>``.  Unwrap them."""
    parsed = urlparse(href)
    host = (parsed.hostname or "").rstrip(".").lower()
    is_relative_wrapper = (
        not parsed.scheme
        and not parsed.netloc
        and parsed.path.rstrip("/") == "/l"
    )
    is_ddg_wrapper = (
        host in redirect_hosts
        and parsed.path.rstrip("/") == "/l"
        and parsed.scheme in {"", "http", "https"}
    )
    if is_relative_wrapper or is_ddg_wrapper:
        qs = parse_qs(parsed.query)
        uddg = qs.get("uddg", [None])[0]
        if uddg:
            # parse_qs already percent-decodes the wrapper value exactly once.
            # A second unquote would corrupt meaningful escapes in the result
            # URL, including nested URLs, signatures, and encoded separators.
            return uddg
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
        if eval_xpath(dom, unfinished_deep_load_xpath):
            _obscura.parser_mismatch(
                "duckduckgo2",
                text,
                "JavaScript result hydration did not complete",
            )
        _obscura.parser_mismatch("duckduckgo2", text, "zero result cards")

    for result in result_nodes:
        link_nodes = eval_xpath(result, link_xpath)
        if not link_nodes:
            continue
        raw_url = link_nodes[0].get("href")
        if not raw_url:
            continue
        url = _strip_ddg_redirect(raw_url)
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
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
