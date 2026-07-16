"""Keep the stock Onyx crawler on the wrapper's public egress policy."""

from __future__ import annotations

import contextvars
import inspect
import os
from urllib.parse import urljoin, urlsplit

from private_onyx_obscura import ObscuraClientError
from private_onyx_obscura import normalize_public_url

PUBLIC_PROXY_URL = "http://onyx-public-egress-bridge:3128"
MAX_REDIRECTS = 10
_CRAWLER_PLAYWRIGHT_VALIDATION: contextvars.ContextVar[bool] = (
    contextvars.ContextVar("wrapper_crawler_playwright_validation", default=False)
)


def use_obscura_browser() -> bool:
    raw = os.environ.get("ONYX_AGENT_USE_OBSCURA_BROWSER", "false").strip().lower()
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise RuntimeError("ONYX_AGENT_USE_OBSCURA_BROWSER must be exactly true or false")


def _allow_http() -> bool:
    raw = os.environ.get("EGRESS_ALLOW_HTTP_URLS", "false").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError("EGRESS_ALLOW_HTTP_URLS must be a boolean")


def _validate_proxy() -> str:
    proxy_url = os.environ.get("ONYX_HELPER_HTTP_PROXY_URL", "").strip()
    parsed = urlsplit(proxy_url)
    try:
        port = parsed.port
    except ValueError:
        port = None
    if (
        proxy_url != PUBLIC_PROXY_URL
        or parsed.scheme != "http"
        or parsed.hostname != "onyx-public-egress-bridge"
        or port != 3128
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(
            "ONYX_HELPER_HTTP_PROXY_URL must be exactly " + PUBLIC_PROXY_URL
        )
    return proxy_url


def _normalize(url: str, *, ssrf_exception_type: type[Exception]) -> str:
    try:
        normalized, _fragment = normalize_public_url(url, allow_http=_allow_http())
        return normalized
    except ObscuraClientError as exc:
        raise ssrf_exception_type(str(exc)) from exc


def _proxied_get(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float | tuple[float, float] = 15,
    follow_redirects: bool = True,
    allow_private_network: bool = False,
    *,
    _session_factory=None,
    _ssrf_exception_type: type[Exception] = RuntimeError,
    **kwargs,
):
    """A requests-compatible GET with structural validation and proxy-owned DNS."""
    del allow_private_network  # The LLM-controlled crawler is always public-only.
    if "allow_redirects" in kwargs or "proxies" in kwargs:
        raise RuntimeError("crawler request routing arguments are wrapper-owned")

    proxy_url = _validate_proxy()
    current_url = _normalize(url, ssrf_exception_type=_ssrf_exception_type)
    if _session_factory is None:
        import requests

        _session_factory = requests.Session
    session = _session_factory()
    session.trust_env = False
    try:
        for redirect_count in range(MAX_REDIRECTS + 1):
            response = session.get(
                current_url,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
                proxies={"http": proxy_url, "https": proxy_url},
                **kwargs,
            )
            if not follow_redirects or not response.is_redirect:
                session.close()
                return response
            location = response.headers.get("Location")
            if not location:
                session.close()
                return response
            if redirect_count == MAX_REDIRECTS:
                response.close()
                raise _ssrf_exception_type(
                    f"Too many redirects (max {MAX_REDIRECTS})"
                )
            next_url = urljoin(current_url, location)
            response.close()
            current_url = _normalize(
                next_url, ssrf_exception_type=_ssrf_exception_type
            )
    except Exception:
        session.close()
        raise


def install() -> None:
    """Install the public-only transport around the pinned stock crawler."""
    _validate_proxy()
    _allow_http()

    from onyx.tools.tool_implementations.open_url import onyx_web_crawler
    from onyx.utils import playwright_fetch
    from onyx.utils import url as onyx_url

    if getattr(onyx_web_crawler, "_wrapper_public_egress_patched", False):
        return

    crawler_source = inspect.getsource(onyx_web_crawler.OnyxWebCrawler)
    required_shapes = (
        "ThreadPoolExecutor",
        "ssrf_safe_get(",
        "fetch_rendered_html(",
        "allow_private_network=not self._should_validate_ssrf()",
    )
    missing = [shape for shape in required_shapes if shape not in crawler_source]
    if missing:
        raise RuntimeError(
            "stock Onyx crawler source changed; missing expected shapes: "
            + ", ".join(missing)
        )
    if onyx_web_crawler.ssrf_safe_get is not onyx_url.ssrf_safe_get:
        raise RuntimeError("stock Onyx crawler ssrf_safe_get import changed")
    if (
        onyx_web_crawler.fetch_rendered_html
        is not playwright_fetch.fetch_rendered_html
    ):
        raise RuntimeError("stock Onyx crawler Playwright import changed")
    if (
        playwright_fetch.validate_outbound_http_url
        is not onyx_url.validate_outbound_http_url
    ):
        raise RuntimeError("stock Onyx Playwright URL validator import changed")

    original_fetch_rendered_html = playwright_fetch.fetch_rendered_html
    original_validate = playwright_fetch.validate_outbound_http_url
    ssrf_exception_type = onyx_url.SSRFException

    def crawler_get(*args, **kwargs):
        kwargs["_ssrf_exception_type"] = ssrf_exception_type
        return _proxied_get(*args, **kwargs)

    def scoped_validate(url: str, **kwargs):
        if not _CRAWLER_PLAYWRIGHT_VALIDATION.get():
            return original_validate(url, **kwargs)
        del kwargs
        return _normalize(url, ssrf_exception_type=ssrf_exception_type)

    def crawler_fetch_rendered_html(*args, **kwargs):
        token = _CRAWLER_PLAYWRIGHT_VALIDATION.set(True)
        try:
            return original_fetch_rendered_html(*args, **kwargs)
        finally:
            _CRAWLER_PLAYWRIGHT_VALIDATION.reset(token)

    onyx_web_crawler.ssrf_safe_get = crawler_get
    onyx_web_crawler.fetch_rendered_html = crawler_fetch_rendered_html
    playwright_fetch.validate_outbound_http_url = scoped_validate
    onyx_web_crawler._wrapper_public_egress_patched = True
    print(
        "sitecustomize_api_server: installed proxied stock Onyx crawler "
        "with public-only requests and Playwright fallback",
        flush=True,
    )
