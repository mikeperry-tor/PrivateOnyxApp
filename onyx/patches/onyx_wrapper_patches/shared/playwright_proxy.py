"""shared playwright proxy patch implementation."""

from __future__ import annotations

from onyx_wrapper_patches.common.config import validate_fixed_proxy_value

import inspect
import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlsplit
from onyx_wrapper_patches.common.config import _raise_if_strict
from onyx_wrapper_patches.common.config import _warn_or_raise


_PLAYWRIGHT_PROXY_OVERRIDE: ContextVar[str | None] = ContextVar(
    "wrapper_playwright_proxy_override", default=None
)



@contextmanager
def select_playwright_proxy(proxy_url: str | None):
    """Temporarily select a canonical public/host proxy, or the default helper proxy."""
    if proxy_url not in {
        None,
        "http://onyx-public-egress-bridge:3128",
        "http://onyx-host-egress-bridge:3128",
    }:
        raise RuntimeError("invalid stack-owned Playwright proxy selection")
    token = _PLAYWRIGHT_PROXY_OVERRIDE.set(proxy_url)
    try:
        yield
    finally:
        _PLAYWRIGHT_PROXY_OVERRIDE.reset(token)



def apply_playwright_helper_proxy_patch() -> None:
    """Route Onyx's shared Playwright launcher through the helper policy."""

    proxy_url = os.environ.get("ONYX_HELPER_HTTP_PROXY_URL", "").strip()
    try:
        proxy_url = validate_fixed_proxy_value(proxy_url, "http://onyx-public-egress-bridge:3128")
    except RuntimeError:
        _warn_or_raise(
            "ONYX_HELPER_HTTP_PROXY_URL must be exactly "
            f"http://onyx-public-egress-bridge:3128, found {proxy_url!r}"
        )
        return

    try:
        from onyx.utils import playwright_fetch
        from playwright import sync_api as playwright_sync_api
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed importing playwright_fetch: {e}", flush=True)
        _raise_if_strict()
        return

    if getattr(playwright_fetch, "_wrapper_helper_proxy_patched", False):
        return

    original_sync_playwright = playwright_fetch.sync_playwright
    if playwright_sync_api.sync_playwright is not original_sync_playwright:
        _warn_or_raise(
            "Onyx Playwright helper no longer uses playwright.sync_api's "
            "sync_playwright factory"
        )
        return
    signature = inspect.signature(original_sync_playwright)
    if signature.parameters:
        _warn_or_raise(
            "onyx.utils.playwright_fetch.sync_playwright signature changed; "
            f"found signature={signature}"
        )
        return

    class _BrowserTypeProxy:
        def __init__(self, browser_type) -> None:
            self._browser_type = browser_type

        def __getattr__(self, name: str) -> Any:
            return getattr(self._browser_type, name)

        def launch(self, *args, **kwargs):
            if kwargs.get("proxy") is not None:
                _warn_or_raise(
                    "Onyx Playwright launcher now supplies its own proxy; "
                    "wrapper helper-proxy injection must be reviewed"
                )
                return self._browser_type.launch(*args, **kwargs)
            selected_proxy = _PLAYWRIGHT_PROXY_OVERRIDE.get()
            if selected_proxy is None:
                selected_proxy = proxy_url
            # Chromium otherwise bypasses proxies implicitly for loopback.
            # Keep redirects and subresources on the selected final hop.
            kwargs["proxy"] = {
                "server": selected_proxy,
                "bypass": "<-loopback>",
            }
            return self._browser_type.launch(*args, **kwargs)

    class _PlaywrightProxy:
        def __init__(self, playwright) -> None:
            self._playwright = playwright
            self.chromium = _BrowserTypeProxy(playwright.chromium)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._playwright, name)

    class _PlaywrightContextManagerProxy:
        def __init__(self, manager) -> None:
            self._manager = manager

        def __getattr__(self, name: str) -> Any:
            return getattr(self._manager, name)

        def start(self):
            return _PlaywrightProxy(self._manager.start())

        def __enter__(self):
            return _PlaywrightProxy(self._manager.__enter__())

        def __exit__(self, exc_type, exc_value, traceback):
            return self._manager.__exit__(exc_type, exc_value, traceback)

    def _helper_proxy_sync_playwright():
        return _PlaywrightContextManagerProxy(original_sync_playwright())

    playwright_fetch.sync_playwright = _helper_proxy_sync_playwright
    # Cover connector modules (currently Highspot) that import sync_playwright
    # directly instead of using onyx.utils.playwright_fetch.start_playwright().
    playwright_sync_api.sync_playwright = _helper_proxy_sync_playwright
    playwright_fetch._wrapper_helper_proxy_patched = True
    print(
        "sitecustomize: routed Onyx Playwright through fixed helper proxy",
        flush=True,
    )
