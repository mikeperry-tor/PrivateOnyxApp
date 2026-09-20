"""Background-owned web connector egress implementation."""

from __future__ import annotations

import functools
import inspect
import os
from contextvars import ContextVar
from urllib.parse import urlsplit

from onyx_wrapper_patches.background.config import _raise_if_strict

_WEB_CONNECTOR_PROXY: ContextVar[str | None] = ContextVar(
    "wrapper_web_connector_proxy", default=None
)


def _apply_web_connector_egress_patch() -> None:
    """Select public/host policy for every Web Connector request path."""
    try:
        import inspect
        import requests

        from onyx.connectors.web import connector as web_connector
        from onyx.server.security.models import SSRFProtectionLevel
        from onyx.server.security.models import web_connector_ssrf_enforced
        from onyx.server.security.store import get_security_settings
        from onyx.utils.url import validate_outbound_http_url
        from onyx_wrapper_patches.common.config import _validated_fixed_proxy_url
        from onyx_wrapper_patches.shared.playwright_proxy import select_playwright_proxy
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize_background: failed importing Web connector egress deps: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    public_proxy = _validated_fixed_proxy_url(
        "ONYX_WEB_CONNECTOR_PUBLIC_HTTP_PROXY_URL", "onyx-public-egress-bridge"
    )
    host_proxy = _validated_fixed_proxy_url(
        "ONYX_WEB_CONNECTOR_HOST_HTTP_PROXY_URL", "onyx-host-egress-bridge"
    )
    internal_base = urlsplit(
        os.environ.get("ONYX_WEB_CONNECTOR_INTERNAL_BASE_URL", "").strip()
    )
    try:
        internal_port = internal_base.port
    except ValueError:
        internal_port = None
    if (
        internal_base.scheme != "http"
        or internal_base.hostname != "doc-drop-web"
        or internal_port != 8091
        or internal_base.username is not None
        or internal_base.password is not None
        or internal_base.path not in {"", "/"}
        or internal_base.query
        or internal_base.fragment
    ):
        raise RuntimeError("ONYX_WEB_CONNECTOR_INTERNAL_BASE_URL is invalid")

    def _is_internal_doc_drop(url: str) -> bool:
        parsed = urlsplit(url)
        try:
            parsed_port = parsed.port
        except ValueError:
            return False
        prefix = internal_base.path.rstrip("/") + "/"
        return (
            parsed.scheme == internal_base.scheme
            and parsed.hostname == internal_base.hostname
            and parsed_port == internal_port
            and parsed.username is None
            and parsed.password is None
            and (parsed.path + ("/" if not parsed.path else "")).startswith(prefix)
        )

    def _selected_proxy(url: str) -> str:
        if _is_internal_doc_drop(url):
            return host_proxy
        level = get_security_settings().ssrf_protection_level
        return (
            public_proxy
            if level == SSRFProtectionLevel.VALIDATE_ALL
            else host_proxy
        )

    original_request = requests.sessions.Session.request

    def _proxy_selected_request(self, method, url, **kwargs):  # noqa: ANN001
        selected = _WEB_CONNECTOR_PROXY.get()
        if selected is None:
            return original_request(self, method, url, **kwargs)
        previous_trust_env = self.trust_env
        self.trust_env = False
        try:
            kwargs["proxies"] = {"http": selected, "https": selected}
            return original_request(self, method, url, **kwargs)
        finally:
            self.trust_env = previous_trust_env

    requests.sessions.Session.request = _proxy_selected_request

    def _structural_protected_url_check(url: str) -> None:
        if _is_internal_doc_drop(url):
            return
        level = get_security_settings().ssrf_protection_level
        strict = web_connector_ssrf_enforced(level)
        validate_outbound_http_url(
            url,
            allow_private_network=not strict,
            block_loopback_and_link_local=True,
            resolve_dns=False,
        )

    web_connector.protected_url_check = _structural_protected_url_check

    original_init = web_connector.WebConnector.__init__
    init_signature = inspect.signature(original_init)
    if "base_url" not in init_signature.parameters:
        raise RuntimeError(
            f"WebConnector.__init__ signature changed: {init_signature}"
        )

    @functools.wraps(original_init)
    def _patched_init(self, *args, **kwargs):  # noqa: ANN001
        bound = init_signature.bind_partial(self, *args, **kwargs)
        base_url = bound.arguments.get("base_url")
        selected = (
            _selected_proxy(base_url) if isinstance(base_url, str) else host_proxy
        )
        token = _WEB_CONNECTOR_PROXY.set(selected)
        try:
            return original_init(self, *args, **kwargs)
        finally:
            _WEB_CONNECTOR_PROXY.reset(token)

    web_connector.WebConnector.__init__ = _patched_init

    original_load = web_connector.WebConnector.load_from_state
    signature = inspect.signature(original_load)
    if tuple(signature.parameters) != ("self", "slim"):
        raise RuntimeError(
            f"WebConnector.load_from_state signature changed: {signature}"
        )

    def _patched_load_from_state(self, slim=False):  # noqa: ANN001
        if not self.to_visit_list:
            yield from original_load(self, slim=slim)
            return
        initial_url = self.to_visit_list[0]
        selected = _selected_proxy(initial_url)
        token = _WEB_CONNECTOR_PROXY.set(selected)
        try:
            with select_playwright_proxy(selected):
                yield from original_load(self, slim=slim)
        finally:
            _WEB_CONNECTOR_PROXY.reset(token)

    web_connector.WebConnector.load_from_state = _patched_load_from_state
    print(
        "sitecustomize_background: routed Web Connector construction and crawl "
        "through fixed saved-level egress",
        flush=True,
    )
