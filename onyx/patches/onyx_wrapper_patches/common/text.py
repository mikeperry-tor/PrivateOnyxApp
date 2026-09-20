"""common text patch implementation."""

from __future__ import annotations



def _truncate_text_with_notice(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 0:
        return ""

    notice_template = (
        "\n... [internal search content truncated, {omitted} characters omitted]"
    )
    # Compute the suffix using a first-pass omitted count, then recompute once
    # because suffix length can change with digit count.
    suffix = notice_template.format(omitted=len(text))
    if len(suffix) >= max_chars:
        return text[:max_chars]
    keep = max(0, max_chars - len(suffix))
    suffix = notice_template.format(omitted=len(text) - keep)
    if len(suffix) >= max_chars:
        return text[:max_chars]
    keep = max(0, max_chars - len(suffix))
    return text[:keep] + suffix
