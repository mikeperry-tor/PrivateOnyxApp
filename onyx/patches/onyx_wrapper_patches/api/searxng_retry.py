"""api searxng retry patch implementation."""

from __future__ import annotations

import inspect
from onyx_wrapper_patches.common.config import _raise_if_strict
from onyx_wrapper_patches.common.config import _warn_or_raise


def apply_searxng_single_attempt_patch() -> None:
    """Send each Onyx web-search query to SearXNG exactly once.

    This unwraps only SearXNGClient.search.  The open_url crawler and its
    transport-specific recovery behavior are separate and remain unchanged.
    """
    try:
        from onyx.tools.tool_implementations.web_search.clients.searxng_client import (
            SearXNGClient,
        )
    except Exception as exc:
        print(f"sitecustomize: failed importing SearXNGClient: {exc}", flush=True)
        _raise_if_strict()
        return

    current = SearXNGClient.search
    source = inspect.getsource(current)
    required = (
        "@retry_builder(tries=3, delay=1, backoff=2)",
        "requests.post(",
        "response.raise_for_status()",
        'results.get("results", [])',
    )
    missing = [fragment for fragment in required if fragment not in source]
    if missing:
        _warn_or_raise(
            "SearXNGClient.search no longer matches the pinned retry shape; "
            f"missing fragments: {missing!r}"
        )
        return

    retry_layers = 0
    single_attempt = current
    while hasattr(single_attempt, "__wrapped__"):
        retry_layers += 1
        single_attempt = single_attempt.__wrapped__
    if retry_layers != 2 or hasattr(single_attempt, "__wrapped__"):
        _warn_or_raise(
            "SearXNGClient.search retry wrapper depth changed; "
            f"expected 2 layers, found {retry_layers}"
        )
        return
    if inspect.signature(single_attempt) != inspect.signature(current):
        _warn_or_raise(
            "SearXNGClient.search unwrapped signature does not match the public method"
        )
        return

    SearXNGClient.search = single_attempt
    print(
        "sitecustomize: removed Onyx SearXNG HTTP-request retries "
        "(open_url unchanged)",
        flush=True,
    )
