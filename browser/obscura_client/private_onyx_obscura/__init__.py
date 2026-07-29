"""Audited navigation clients for the wrapper's pinned Obscura CDP service."""

from .client import (
    BodyClassification,
    FetchFailure,
    FetchResult,
    ObscuraSession,
    ObscuraClientError,
    SearchBrowserSession,
    SearchInteractionSpec,
    SearchSubmissionResult,
    TextEntryMode,
    fetch,
    fetch_sync,
    is_text_like_content_type,
    normalize_public_url,
    submit_search,
    validate_wait_until,
)

__all__ = [
    "BodyClassification",
    "FetchFailure",
    "FetchResult",
    "ObscuraSession",
    "ObscuraClientError",
    "SearchBrowserSession",
    "SearchInteractionSpec",
    "SearchSubmissionResult",
    "TextEntryMode",
    "fetch",
    "fetch_sync",
    "is_text_like_content_type",
    "normalize_public_url",
    "submit_search",
    "validate_wait_until",
]
