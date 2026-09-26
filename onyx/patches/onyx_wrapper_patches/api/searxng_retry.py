"""api searxng retry patch implementation."""

from __future__ import annotations

import inspect
from onyx_wrapper_patches.common.config import _raise_if_strict
from onyx_wrapper_patches.common.config import _warn_or_raise


def apply_searxng_connection_probe_patch() -> None:
    """Validate local identity and JSON access without executing a search."""
    from onyx.tools.tool_implementations.web_search.clients import searxng_client as module
    client = module.SearXNGClient
    for method, markers in (
        (client.test_connection, ('requests.get(', 'config.get("brand", {}).get("GIT_URL")', 'self._test_json_mode()')),
        (client._test_json_mode, ('"q": "test"', 'requests.post(', 'timeout=5')),
    ):
        if list(inspect.signature(method).parameters) != ["self"] or any(
            marker not in inspect.getsource(method) for marker in markers
        ):
            _warn_or_raise("SearXNG connection probe source contract changed")
            return

    def fail(detail):
        raise module.HTTPException(status_code=400, detail=detail) from None

    def test_json(self):
        try:
            response = module.requests.post(
                f"{self._searxng_base_url}/search", data={"format": "json", "q": ""},
                timeout=(5, 5), allow_redirects=False,
            )
            if response.status_code == 403:
                fail("SearXNG denied JSON access; enable json in search.formats and check access policy.")
            if (response.status_code != 400
                    or response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json"
                    or response.json() != {"error": "No query"}):
                fail("SearXNG returned an unexpected empty-query JSON response.")
        except (module.requests.RequestException, ValueError):
            fail("SearXNG JSON connection validation failed.")

    def test_connection(self):
        try:
            response = module.requests.get(
                f"{self._searxng_base_url}/config", timeout=(5, 5), allow_redirects=False,
            )
            if response.status_code != 200:
                fail("SearXNG configuration endpoint is unavailable.")
            config = response.json()
            if not isinstance(config, dict) or not isinstance(config.get("brand"), dict) or config["brand"].get("GIT_URL") != "https://github.com/searxng/searxng":
                fail("This does not appear to be a SearXNG instance.")
        except (module.requests.RequestException, ValueError):
            fail("SearXNG identity validation failed.")
        self._test_json_mode()
        return {"status": "ok"}

    client.test_connection = test_connection
    client._test_json_mode = test_json
    print("sitecustomize: installed non-search SearXNG connection probe", flush=True)


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
