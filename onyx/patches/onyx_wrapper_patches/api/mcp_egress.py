"""api mcp egress patch implementation."""

from __future__ import annotations

import inspect
import sys
from typing import Any
from onyx_wrapper_patches.common.config import _raise_if_strict
from onyx_wrapper_patches.common.config import _validated_fixed_proxy_url
from onyx_wrapper_patches.common.config import _warn_or_raise


def apply_mcp_egress_proxy_patch() -> None:
    """Select an explicit public/host MCP transport from saved SSRF policy."""

    public_proxy = _validated_fixed_proxy_url(
        "ONYX_MCP_PUBLIC_HTTP_PROXY_URL", "onyx-public-egress-bridge"
    )
    host_proxy = _validated_fixed_proxy_url(
        "ONYX_MCP_HOST_HTTP_PROXY_URL", "onyx-host-egress-bridge"
    )
    try:
        import httpx

        from onyx.server.security.models import SSRFProtectionLevel
        from onyx.server.security.store import get_security_settings
        from onyx.server.features.mcp import ssrf as mcp_ssrf
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed importing MCP egress patch deps: {e}", flush=True)
        _raise_if_strict()
        return

    original_factory = mcp_ssrf.mcp_ssrf_httpx_client_factory
    signature = inspect.signature(original_factory)
    if tuple(signature.parameters) != ("headers", "timeout", "auth"):
        _warn_or_raise(f"MCP HTTP client factory signature changed: {signature}")
        return

    original_challenge_factory = mcp_ssrf.mcp_oauth_challenge_httpx_client_factory
    challenge_signature = inspect.signature(original_challenge_factory)
    if tuple(challenge_signature.parameters) != (
        "server_url",
        "metadata_url",
        "auth",
        "timeout",
    ):
        _warn_or_raise(
            "MCP OAuth challenge HTTP client factory signature changed: "
            f"{challenge_signature}"
        )
        return
    challenge_transport = mcp_ssrf._OAuthChallengeTransport
    if tuple(inspect.signature(challenge_transport.__init__).parameters) != (
        "self",
        "server_url",
        "metadata_url",
    ) or tuple(inspect.signature(challenge_transport.handle_async_request).parameters) != (
        "self",
        "request",
    ):
        _warn_or_raise("MCP OAuth challenge transport signature changed")
        return
    try:
        challenge_source = inspect.getsource(
            challenge_transport.handle_async_request
        )
    except (OSError, TypeError) as e:
        _warn_or_raise(f"could not inspect MCP OAuth challenge transport: {e}")
        return
    for marker in (
        'request.method == "GET"',
        "request.url == self._server_url",
        "status_code=401",
        '"WWW-Authenticate"',
        "status_code=204",
        "self._delegate.handle_async_request(request)",
    ):
        if marker not in challenge_source:
            _warn_or_raise(
                "MCP OAuth challenge transport source contract changed; "
                f"missing {marker!r}"
            )
            return

    def _selected_transport():
        level = get_security_settings().ssrf_protection_level
        use_host = level in {
            SSRFProtectionLevel.ALLOW_PRIVATE_NETWORK,
            SSRFProtectionLevel.DISABLED,
        }
        return httpx.AsyncHTTPTransport(proxy=host_proxy if use_host else public_proxy)

    def _patched_factory(headers=None, timeout=None, auth=None):  # noqa: ANN001
        kwargs: dict[str, Any] = {
            "follow_redirects": True,
            # The selected policy and route broker validate every initial and
            # SDK-derived destination. Keep the Onyx transport deliberately
            # limited to explicit route selection so destination policy is not
            # duplicated here with subtly different hostname/DNS semantics.
            "transport": _selected_transport(),
            "trust_env": False,
            "timeout": timeout
            or httpx.Timeout(
                mcp_ssrf._MCP_DEFAULT_TIMEOUT,
                read=mcp_ssrf._MCP_DEFAULT_SSE_READ_TIMEOUT,
            ),
        }
        if headers is not None:
            kwargs["headers"] = headers
        if auth is not None:
            kwargs["auth"] = auth
        return httpx.AsyncClient(**kwargs)

    class _PatchedOAuthChallengeTransport(httpx.AsyncBaseTransport):
        """Preserve Onyx's synthetic OAuth challenge over the selected route."""

        def __init__(self, server_url, metadata_url):  # noqa: ANN001
            self._server_url = httpx.URL(server_url)
            self._metadata_url = metadata_url
            self._challenged = False
            self._delegate = _selected_transport()

        async def handle_async_request(self, request):  # noqa: ANN001
            if request.method == "GET" and request.url == self._server_url:
                if not self._challenged:
                    self._challenged = True
                    return httpx.Response(
                        status_code=401,
                        headers={
                            "WWW-Authenticate": (
                                f'Bearer resource_metadata="{self._metadata_url}"'
                            )
                        },
                        request=request,
                    )
                return httpx.Response(status_code=204, request=request)
            return await self._delegate.handle_async_request(request)

        async def aclose(self) -> None:
            await self._delegate.aclose()

    def _patched_challenge_factory(
        server_url, metadata_url, auth, timeout  # noqa: ANN001
    ):
        return httpx.AsyncClient(
            auth=auth,
            follow_redirects=True,
            timeout=timeout,
            transport=_PatchedOAuthChallengeTransport(server_url, metadata_url),
            trust_env=False,
        )

    mcp_ssrf.mcp_ssrf_httpx_client_factory = _patched_factory
    mcp_ssrf.mcp_oauth_challenge_httpx_client_factory = _patched_challenge_factory
    # The client module imports the factory by name; update it if source import
    # order caused it to be cached while applying this startup patch.
    for module_name in (
        "onyx.server.features.mcp.client",
        "onyx.server.features.mcp.oauth",
    ):
        cached_module = sys.modules.get(module_name)
        if cached_module is not None:
            setattr(
                cached_module,
                "mcp_ssrf_httpx_client_factory",
                _patched_factory,
            )
            if module_name == "onyx.server.features.mcp.oauth":
                setattr(
                    cached_module,
                    "mcp_oauth_challenge_httpx_client_factory",
                    _patched_challenge_factory,
                )
    print(
        "sitecustomize: routed MCP/OAuth HTTP through saved-level-selected fixed egress",
        flush=True,
    )
