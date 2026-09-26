"""Ordered optional registration; importing this module does no work."""

import functools
import inspect
import os
import sys

_client = None
_installed = False


def payment_client():
    if _client is None:
        raise RuntimeError("x402exa_not_configured")
    return _client


def install():
    global _client, _installed
    from x402_config import KEY_NAME, enabled

    if _installed or not enabled(os.environ.get(KEY_NAME), os.environ.get("SEARXNG_ROUND_ROBIN", "false")):
        return
    if os.environ.get("GRANIAN_WORKERS") != "1":
        raise ValueError("x402exa requires GRANIAN_WORKERS=1")
    from searx.engines._x402_payment import PaymentClient, PROXY

    if os.environ.get("SEARXNG_X402_PROXY") != PROXY:
        raise ValueError("invalid x402exa topology")
    import searx
    import searx.engines

    if searx.engines.engines or "searx.search" in sys.modules:
        raise RuntimeError("x402exa registration must precede engine/search loading")
    _client = PaymentClient(os.environ[KEY_NAME])
    searx.settings["engines"].append({
        "name": "x402exa", "engine": "x402exa", "shortcut": "x402exa",
        "categories": ["general"], "timeout": 60, "disabled": False,
    })
    _installed = True


def install_locale_adapter():
    if not _installed:
        return
    import searx.webadapter as adapter
    if "searx.webapp" in sys.modules:
        raise RuntimeError("x402exa locale adapter installed too late")
    original = adapter.get_search_query_from_webapp
    source = inspect.getsource(original)
    if (list(inspect.signature(original).parameters) != ["preferences", "form"]
            or "selected_locale = query_lang" not in source or "selected_locale," not in source):
        raise RuntimeError("SearXNG locale contract changed")

    @functools.wraps(original)
    def wrapped(preferences, form):
        result = original(preferences, form)
        result[0]._wrapper_selected_locale = result[4]
        return result

    adapter.get_search_query_from_webapp = wrapped
