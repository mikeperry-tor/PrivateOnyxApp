# SPDX-License-Identifier: AGPL-3.0-or-later
"""Startpage web engine backed by homepage form submission in Obscura.

Startpage proxies Google results but wraps them behind its own anti-bot layer
(``startpage.com``).  The stock ``startpage`` engine scrapes a pre-hydration
React JSON blob (``React.createElement(UIStartpage.AppSerpWeb, ...)``) from the
raw HTML; on datacenter / VPN exit IPs Startpage frequently serves a JS
challenge or empty shell instead, yielding zero results. This engine renders
the page directly in Obscura so the React app hydrates and result cards are present in
the post-render DOM, which we then parse with XPath.

Only the ``web`` category is implemented here (the high-value target).  The
stock ``startpage`` / ``startpage news`` / ``startpage images`` entries are
disabled in settings.yml so SearXNG does not double-query Startpage.

"""

import typing as t
from urllib.parse import unquote, urlparse, parse_qs

from lxml import html

from searx.exceptions import SearxEngineCaptchaException
from searx.utils import eval_xpath, eval_xpath_list, extract_text

from searx.engines import _obscura  # type: ignore  # noqa: E402

if t.TYPE_CHECKING:
    from searx.search.processors import RequestParams

engine_type = "offline"

about = {
    "website": "https://www.startpage.com",
    "wikidata_id": "Q498428",
    "official_api_documentation": None,
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

# Startpage post-hydration DOM (obscura renders the React app):
#
#   <div class="result css-XXXXX">              ← organic result container
#     ...
#     <a class="result-title result-link ..."
#        data-testid="gl-title-link"
#        href="https://example.com/...">
#       <style>...</style>                       ← inline CSS (must NOT leak into title)
#       <h2 class="wgl-title ...">Title text</h2>
#     </a>
#     ...
#     <div class="description css-XXXXX">Snippet text</div>
#   </div>
#
# Sponsored results use class "a-bg-result"; match the organic "result" class
# as a class token so both "result css-..." and a bare "result" class work.
results_xpath = (
    '//div[contains(concat(" ", normalize-space(@class), " "), " result ")'
    ' and not(contains(concat(" ", normalize-space(@class), " "), " a-bg-result "))]'
)
no_results_xpath = '//*[@data-testid="no-results" or contains(concat(" ", normalize-space(@class), " "), " no-results ")]'
# The clickable title link carries the real result href.
link_xpath = './/a[@data-testid="gl-title-link"]'
# Fallback for layout variants where data-testid is empty.
link_fallback_xpath = './/a[contains(@class, "result-title")]'
# Title text lives in an <h2> inside the link.  We must NOT use extract_text()
# on the <a> itself because it contains inline <style> tags whose CSS text
# would leak into the title (lxml's method="text" does not skip <style>).
title_xpath = './/h2'
# Snippet / description text.
content_xpath = './/*[contains(@class, "description")]'
captcha_xpath = (
    '//title[contains(translate(normalize-space(.), '
    '"ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "startpage captcha")]'
    ' | //meta[contains(translate(@content, '
    '"ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "startpage\'s captcha page")]'
    ' | //form[contains(@action, "/sp/captcha")]'
)
anubis_verification_xpath = (
    '//script[contains(@src, "/.within.website/x/cmd/anubis/")]'
    ' and //*[contains(concat(" ", normalize-space(@class), " "), " sp-message ")'
    ' and contains(translate(normalize-space(.), '
    '"ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "verifying your request")]'
)


def search(query: str, params: "RequestParams"):
    """Submit the Startpage homepage form through Obscura."""
    fixed_fields: list[tuple[str, str]] = [("cat", "web")]
    # Startpage paginates via the `page` parameter (1-based).
    if params["pageno"] > 1:
        fixed_fields.append(("page", str(params["pageno"])))

    return _parse_html(
        _obscura.submit_search(
            "startpage2",
            query,
            tuple(fixed_fields),
            params.get(_obscura.PRE_NAVIGATION_GUARD_PARAM),
        )
    )


def _strip_startpage_redirect(href: str) -> str:
    """Startpage wraps result links in a ``/sp/redirect?...`` tracker.

    The real URL is carried in the ``url`` query parameter (percent-encoded).
    Unwrap it so SearXNG (and the tracker_url_remover plugin) sees the true
    destination.  If the href is already a direct http(s) link, return it as-is.
    """
    if href.startswith("http"):
        return href
    if "/sp/redirect" in href or "url=" in href:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        real = qs.get("url", [None])[0]
        if real:
            return unquote(real)
    return href


def _raise_if_captcha(dom) -> None:
    """Startpage can return a captcha page with HTTP 200."""
    if eval_xpath(dom, captcha_xpath) or eval_xpath(dom, anubis_verification_xpath):
        raise SearxEngineCaptchaException(
            message="startpage2: Startpage returned a captcha page",
        )


def _parse_html(text: str):
    if not text:
        _obscura.parser_mismatch("startpage2", text, "empty DOM")

    results = []
    dom = html.fromstring(text)
    _raise_if_captcha(dom)

    result_nodes = eval_xpath_list(dom, results_xpath)
    if not result_nodes:
        if eval_xpath(dom, no_results_xpath):
            return []
        _obscura.parser_mismatch("startpage2", text, "zero organic cards")

    for result in result_nodes:
        link_nodes = eval_xpath(result, link_xpath)
        if not link_nodes:
            link_nodes = eval_xpath(result, link_fallback_xpath)
        if not link_nodes:
            continue
        raw_url = link_nodes[0].get("href")
        if not raw_url:
            continue
        url = _strip_startpage_redirect(raw_url)
        if not url.startswith("http"):
            continue

        # Title: prefer the <h2> inside the link to avoid <style> CSS leakage.
        title_nodes = eval_xpath(link_nodes[0], title_xpath)
        if not title_nodes:
            title_nodes = eval_xpath(result, './/h2')
        title = extract_text(title_nodes[0]) if title_nodes else ""
        if not title:
            continue

        content_nodes = eval_xpath(result, content_xpath)
        content = extract_text(content_nodes[0]) if content_nodes else ""

        results.append({"url": url, "title": title, "content": content})

    if not results:
        _obscura.parser_mismatch("startpage2", text, "no valid organic rows")

    return results
