"""Single-navigation client for the wrapper's pinned Obscura CDP service."""

from .client import (
    BodyClassification,
    FetchFailure,
    FetchResult,
    ObscuraSession,
    ObscuraClientError,
    fetch,
    fetch_sync,
    is_text_like_content_type,
    normalize_public_url,
    validate_wait_until,
)

__all__ = [
    "BodyClassification",
    "FetchFailure",
    "FetchResult",
    "ObscuraSession",
    "ObscuraClientError",
    "fetch",
    "fetch_sync",
    "is_text_like_content_type",
    "normalize_public_url",
    "validate_wait_until",
]
