"""GitHub downloads use public egress without application-side DNS checks."""

from __future__ import annotations

import inspect
import os

PUBLIC_PROXY_URL = "http://onyx-public-egress-bridge:3128"


def _validate_proxy() -> None:
    if os.environ.get("ONYX_HELPER_HTTP_PROXY_URL") != PUBLIC_PROXY_URL:
        raise RuntimeError(
            "GitHub egress requires ONYX_HELPER_HTTP_PROXY_URL=" + PUBLIC_PROXY_URL
        )


def _public_get(url, headers=None, timeout=15, follow_redirects=True, *, stream=False):
    import requests

    _validate_proxy()
    with requests.Session() as session:
        # Environment proxies, netrc credentials and NO_PROXY must not change
        # this route, including when Requests follows an archive redirect.
        session.trust_env = False
        session.proxies = {"http": PUBLIC_PROXY_URL, "https": PUBLIC_PROXY_URL}
        session.max_redirects = 10
        return session.get(
            url,
            headers=headers,
            timeout=timeout,
            stream=stream,
            allow_redirects=follow_redirects,
        )


def _validate_target(github, onyx_url) -> None:
    if github.ssrf_safe_get is not onyx_url.ssrf_safe_get:
        raise RuntimeError("GitHub ssrf_safe_get import changed")
    signature = inspect.signature(github._github_get)
    expected = ("url", "authorization", "stream", "follow_redirects", "timeout")
    parameters = list(signature.parameters.values())
    if (
        tuple(signature.parameters) != expected
        or parameters[0].kind != inspect.Parameter.POSITIONAL_OR_KEYWORD
        or any(p.kind != inspect.Parameter.KEYWORD_ONLY for p in parameters[1:])
        or tuple(p.default for p in parameters)
        != (inspect.Parameter.empty, None, False, True, 30)
    ):
        raise RuntimeError("GitHub _github_get signature changed")
    source = inspect.getsource(github._github_get)
    for marker in (
        "return ssrf_safe_get(", "headers=headers,", "timeout=timeout,",
        "stream=stream,", "follow_redirects=follow_redirects,",
        "except SSRFException", "except requests.Timeout", "except requests.RequestException",
    ):
        if marker not in source:
            raise RuntimeError("GitHub _github_get source changed: " + marker)
    if onyx_url.MAX_REDIRECTS != 10:
        raise RuntimeError("GitHub redirect limit changed")


def install() -> None:
    from onyx.utils import github
    from onyx.utils import url as onyx_url

    _validate_proxy()
    if github.ssrf_safe_get is _public_get:
        return
    _validate_target(github, onyx_url)
    github.ssrf_safe_get = _public_get
    print("sitecustomize_api_server: routed GitHub downloads through public egress without local DNS validation")
