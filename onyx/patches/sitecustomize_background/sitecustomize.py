"""Background-service bootstrap: exact exclusions and ordered installation."""

from __future__ import annotations

import os
import sys
from contextlib import redirect_stdout

_CONTROL_PROCESS_ARGV0 = frozenset(
    {
        "/app/wrapper-background-entrypoint.py",
        "/app/wrapper-beat-liveness-watchdog.py",
        "/usr/bin/supervisord",
    }
)


def _apply_playwright_helper_proxy_patch() -> None:
    try:
        from onyx_wrapper_patches.shared.playwright_proxy import apply_playwright_helper_proxy_patch

        apply_playwright_helper_proxy_patch()
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize_background: failed to patch Playwright proxy: {e}",
            flush=True,
        )
        if _strict():
            raise


def _apply_configured_inference_proxy_patch() -> None:
    try:
        from onyx_wrapper_patches.shared.inference_proxy import apply_configured_inference_proxy_patch

        apply_configured_inference_proxy_patch()
    except Exception as e:  # pragma: no cover
        print(
            "sitecustomize_background: failed to patch configured inference egress: "
            f"{e}",
            flush=True,
        )
        if _strict():
            raise


def _apply_embedding_tokenizer_alias_patch() -> None:
    try:
        from onyx_wrapper_patches.shared.embedding_tokenizer import apply_embedding_tokenizer_alias_patch

        apply_embedding_tokenizer_alias_patch()
    except Exception as e:  # pragma: no cover
        print(
            "sitecustomize_background: failed to patch embedding tokenizer alias: "
            f"{e}",
            flush=True,
        )
        if _strict():
            raise


def _install() -> None:
    from onyx_wrapper_patches.background.resource_policy import _apply_sleepy_background_patch
    from onyx_wrapper_patches.background.web_connector_egress import _apply_web_connector_egress_patch
    from onyx_wrapper_patches.background.document_freshness import _apply_web_connector_http_freshness_patch

    _apply_embedding_tokenizer_alias_patch()
    _apply_sleepy_background_patch()
    _apply_playwright_helper_proxy_patch()
    _apply_configured_inference_proxy_patch()
    _apply_web_connector_egress_patch()
    _apply_web_connector_http_freshness_patch()


def _strict() -> bool:
    return os.environ.get("WRAPPER_PATCH_STRICT", "true").lower() in {
        "1", "true", "yes", "on"
    }


def _is_background_control_process() -> bool:
    """Keep strict application patches out of exact wrapper control programs."""
    return bool(sys.argv) and sys.argv[0] in _CONTROL_PROCESS_ARGV0


if not _is_background_control_process():
    try:
        with redirect_stdout(sys.stderr):
            _install()
    except Exception as exc:
        print(
            f"sitecustomize_background: patch initialization failed: {exc}",
            file=sys.stderr,
            flush=True,
        )
        if _strict():
            # The site loader suppresses ordinary exceptions from
            # sitecustomize. A direct exit is required for fail-closed startup.
            os._exit(78)
