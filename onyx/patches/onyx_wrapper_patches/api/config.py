"""api config patch implementation."""

from __future__ import annotations

import os

from onyx_wrapper_patches.common.config import _env_flag_default_true


_DEEP_RESEARCH_PROVIDE_CHAT_AGENT_TOOLS = _env_flag_default_true(
    "ONYX_DEEP_RESEARCH_PROVIDE_CHAT_AGENT_TOOLS"
)


def use_obscura_browser() -> bool:
    raw = os.environ.get("ONYX_AGENT_USE_OBSCURA_BROWSER", "false").strip().lower()
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise RuntimeError("ONYX_AGENT_USE_OBSCURA_BROWSER must be exactly true or false")


def parse_document_limit() -> int:
    raw = os.environ.get("ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB", "20")
    if not raw or not raw.isdecimal():
        raise RuntimeError(
            "ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB must be a positive base-10 integer"
        )
    mib = int(raw)
    if mib <= 0 or mib > ((1 << 63) - 1) // (1024 * 1024):
        raise RuntimeError(
            "ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB is outside the supported range"
        )
    return mib * 1024 * 1024

def allow_http() -> bool:
    raw = os.environ.get("EGRESS_ALLOW_HTTP_URLS", "false").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError("EGRESS_ALLOW_HTTP_URLS must be a boolean")

def allow_http_onion() -> bool:
    raw = os.environ.get("EGRESS_ALLOW_HTTP_ONION_URLS", "false").strip().lower()
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise RuntimeError("EGRESS_ALLOW_HTTP_ONION_URLS must be exactly true or false")
