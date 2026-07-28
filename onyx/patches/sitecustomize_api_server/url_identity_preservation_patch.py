"""Preserve complete URL identity through Onyx web-search and open_url."""

from __future__ import annotations

import inspect
from urllib.parse import urlparse, urlunparse


def _preserve_url(url: str) -> str:
    """Return a display, citation, or crawl URL without losing identity."""
    return url


def _canonical_url_with_complete_identity(url: str) -> str | None:
    """Apply Onyx's default canonical formatting without dropping identity."""
    parsed = urlparse(url)
    if not parsed.netloc:
        return None
    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def install() -> None:
    from onyx.tools.tool_implementations.open_url import models as open_url_models
    from onyx.tools.tool_implementations.open_url import open_url_tool
    from onyx.tools.tool_implementations.open_url import url_normalization
    from onyx.tools.tool_implementations.web_search import models as web_search_models
    from onyx.utils import url as url_utils

    if getattr(url_utils, "_wrapper_url_identity_preservation_patch", False):
        return

    web_normalize_link = web_search_models.WebSearchResult.normalize_link
    content_normalize_link = open_url_models.WebContent.normalize_link
    web_normalize_link_source = inspect.getsource(web_normalize_link)
    content_normalize_link_source = inspect.getsource(content_normalize_link)
    generic_normalizer_source = inspect.getsource(url_utils.normalize_url)
    canonical_normalizer_source = inspect.getsource(
        url_normalization._default_url_normalizer
    )
    if (
        tuple(inspect.signature(web_normalize_link).parameters) != ("v",)
        or tuple(inspect.signature(content_normalize_link).parameters) != ("v",)
        or "return normalize_url(v)" not in web_normalize_link_source
        or "return normalize_url(v)" not in content_normalize_link_source
        or "without query string and fragment" not in generic_normalizer_source
        or "query = \"\"  # Query string (removed)" not in canonical_normalizer_source
        or "fragment = \"\"  # Fragment/hash (removed)" not in canonical_normalizer_source
        or web_search_models.normalize_url is not url_utils.normalize_url
        or open_url_models.normalize_url is not url_utils.normalize_url
        or open_url_tool.normalize_web_content_url is not url_utils.normalize_url
    ):
        raise RuntimeError("Onyx URL identity normalization source drift")

    # Patch the utility and every already-imported alias. The separate open_url
    # connector-specific normalizers remain authoritative, but their generic
    # fallback now preserves query and fragment identity while retaining the
    # pinned scheme/host/trailing-slash formatting.
    url_utils.normalize_url = _preserve_url
    web_search_models.normalize_url = _preserve_url
    open_url_models.normalize_url = _preserve_url
    open_url_tool.normalize_web_content_url = _preserve_url
    url_normalization._default_url_normalizer = _canonical_url_with_complete_identity
    url_utils._wrapper_url_identity_preservation_patch = True
    print(
        "sitecustomize_api_server: preserving complete URL identity",
        flush=True,
    )
