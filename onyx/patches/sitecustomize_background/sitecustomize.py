"""Background-worker runtime patches for stock Onyx containers.

Loaded automatically by Python when this directory is on PYTHONPATH.
"""

from __future__ import annotations

import os
from datetime import datetime
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse


FRESHNESS_META_VERSION_KEY = "_wrapper_http_freshness_version"
FRESHNESS_LAST_MODIFIED_KEY = "_wrapper_http_last_modified"
FRESHNESS_CONTENT_LENGTH_KEY = "_wrapper_http_content_length"
FRESHNESS_SOURCE_KEY = "_wrapper_http_freshness_source"
FRESHNESS_UNCHANGED_KEY = "_wrapper_http_freshness_unchanged"
FRESHNESS_UNREADABLE_KEY = "_wrapper_http_freshness_unreadable"
FRESHNESS_HTTP_STATUS_KEY = "_wrapper_http_status"
FRESHNESS_VERSION = "1"
TERMINAL_HTTP_STATUS_SKIP_CODES = frozenset({401, 403, 404})
_PATCH_LOGGER = None
_INDEXING_SKIP_PATCHED = False
_LOG_ONCE_KEYS: set[str] = set()


def _apply_playwright_helper_proxy_patch() -> None:
    try:
        from wrapper_env_patches import apply_playwright_helper_proxy_patch

        apply_playwright_helper_proxy_patch()
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize_background: failed to patch Playwright proxy: {e}",
            flush=True,
        )
        if _strict_mode():
            raise


def _env_enabled(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _strict_mode() -> bool:
    return _env_enabled("WRAPPER_PATCH_STRICT", True)


def _raise_if_strict() -> None:
    if _strict_mode():
        raise


def _allowed_hosts() -> set[str]:
    raw = os.environ.get(
        "ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_HOSTS",
        "localhost,127.0.0.1,::1",
    )
    return {host.strip().lower() for host in raw.split(",") if host.strip()}


def _debug_enabled() -> bool:
    return _env_enabled("ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_DEBUG", False)


def _is_allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return host in _allowed_hosts()


def _parse_last_modified(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)

    # HTTP-date has second precision. Match stdlib http.server's comparison.
    return parsed.replace(microsecond=0)


def _normal_content_length(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped if stripped.isdigit() else None


def _hash_for_log(value: str | None) -> str:
    return value[:12] if value else "-"


def _log(level: str, message: str, *args) -> None:
    prefix = "sitecustomize_background: "
    logger = _PATCH_LOGGER
    if logger is not None:
        try:
            getattr(logger, level)(prefix + message, *args)
            return
        except Exception:
            pass

    try:
        rendered = message % args if args else message
    except Exception:
        rendered = message
    print(prefix + rendered, flush=True)


def _log_exception(message: str, *args) -> None:
    prefix = "sitecustomize_background: "
    logger = _PATCH_LOGGER
    if logger is not None:
        try:
            logger.exception(prefix + message, *args)
            return
        except Exception:
            pass

    try:
        rendered = message % args if args else message
    except Exception:
        rendered = message
    print(prefix + rendered, flush=True)


def _log_once(key: str, level: str, message: str, *args) -> None:
    if key in _LOG_ONCE_KEYS:
        return
    _LOG_ONCE_KEYS.add(key)
    _log(level, message, *args)


def _log_debug(message: str, *args) -> None:
    if _debug_enabled():
        _log("info", message, *args)


def _freshness_metadata(
    existing: dict | None,
    *,
    last_modified_raw: str,
    content_length: str,
) -> dict:
    metadata = dict(existing or {})
    metadata[FRESHNESS_META_VERSION_KEY] = FRESHNESS_VERSION
    metadata[FRESHNESS_LAST_MODIFIED_KEY] = last_modified_raw
    metadata[FRESHNESS_CONTENT_LENGTH_KEY] = content_length
    metadata[FRESHNESS_SOURCE_KEY] = "http-head"
    return metadata


def _unchanged_freshness_metadata(
    *,
    last_modified_raw: str,
    content_length: str,
) -> dict:
    metadata = _freshness_metadata(
        {},
        last_modified_raw=last_modified_raw,
        content_length=content_length,
    )
    metadata[FRESHNESS_UNCHANGED_KEY] = FRESHNESS_VERSION
    return metadata


def _unreadable_freshness_metadata(
    *,
    status_code: int,
    last_modified_raw: str | None,
    content_length: str | None,
) -> dict:
    metadata = {}
    metadata[FRESHNESS_META_VERSION_KEY] = FRESHNESS_VERSION
    metadata[FRESHNESS_SOURCE_KEY] = "http-head"
    metadata[FRESHNESS_UNREADABLE_KEY] = FRESHNESS_VERSION
    metadata[FRESHNESS_HTTP_STATUS_KEY] = str(status_code)
    if last_modified_raw:
        metadata[FRESHNESS_LAST_MODIFIED_KEY] = last_modified_raw
    if content_length:
        metadata[FRESHNESS_CONTENT_LENGTH_KEY] = content_length
    return metadata


def _metadata_matches_freshness(
    metadata: dict,
    *,
    last_modified_raw: str,
    content_length: str,
) -> bool:
    return (
        metadata.get(FRESHNESS_META_VERSION_KEY) == FRESHNESS_VERSION
        and metadata.get(FRESHNESS_LAST_MODIFIED_KEY) == last_modified_raw
        and metadata.get(FRESHNESS_CONTENT_LENGTH_KEY) == content_length
    )


def _doc_matches_freshness(
    db_doc,
    *,
    last_modified_dt: datetime,
    last_modified_raw: str,
    content_length: str,
) -> bool:
    if db_doc is None or db_doc.doc_updated_at is None:
        return False

    db_metadata = dict(db_doc.doc_metadata or {})
    return (
        db_doc.doc_updated_at.astimezone(timezone.utc).replace(microsecond=0)
        == last_modified_dt
        and _metadata_matches_freshness(
            db_metadata,
            last_modified_raw=last_modified_raw,
            content_length=content_length,
        )
    )


def _semantic_identifier(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    name = path.rsplit("/", 1)[-1]
    return name or url


def _get_db_document(document_id: str):
    from onyx.db.engine.sql_engine import get_session_with_current_tenant
    from onyx.db.models import Document as DbDocument

    with get_session_with_current_tenant() as db_session:
        return db_session.get(DbDocument, document_id)


def _seed_db_freshness(
    document_id: str,
    *,
    doc_updated_at: datetime,
    doc_metadata: dict,
) -> None:
    from sqlalchemy import update

    from onyx.db.engine.sql_engine import get_session_with_current_tenant
    from onyx.db.models import Document as DbDocument

    with get_session_with_current_tenant() as db_session:
        db_session.execute(
            update(DbDocument)
            .where(DbDocument.id == document_id)
            .values(doc_updated_at=doc_updated_at, doc_metadata=doc_metadata)
        )
        db_session.commit()


def _apply_indexing_freshness_skip_patch() -> None:
    """Keep wrapper PDF skip sentinels out of forced reindex paths.

    Normal indexing already skips unchanged docs through Onyx's doc_updated_at
    gate. Targeted reindex disables that gate, so an unchanged PDF sentinel
    with empty sections would otherwise be indexed as empty content. This
    wrapper-specific marker is only produced after trusted HTTP validators
    match the DB document or after a trusted doc-drop host returns a terminal
    unreadable status, so it is safe to skip even when ignore_time_skip is true.
    """
    global _INDEXING_SKIP_PATCHED

    try:
        from onyx.indexing import indexing_pipeline
    except Exception as e:  # pragma: no cover
        _log(
            "warning",
            "failed importing indexing pipeline for PDF freshness sentinel "
            "skip patch; pre-download skipping disabled error=%s",
            e,
        )
        _raise_if_strict()
        return

    original_get_docs_to_update = indexing_pipeline.get_docs_to_update

    def _patched_get_docs_to_update(  # noqa: ANN001
        documents,
        db_docs,
        ignore_timestamp_gate=False,
    ):
        db_doc_by_id = {db_doc.id: db_doc for db_doc in db_docs}
        passthrough_documents = []
        skipped = 0

        for doc in documents:
            metadata = dict(doc.doc_metadata or {})
            if metadata.get(FRESHNESS_UNREADABLE_KEY) == FRESHNESS_VERSION:
                skipped += 1
                continue

            if metadata.get(FRESHNESS_UNCHANGED_KEY) != FRESHNESS_VERSION:
                passthrough_documents.append(doc)
                continue

            db_doc = db_doc_by_id.get(doc.id)
            last_modified_raw = metadata.get(FRESHNESS_LAST_MODIFIED_KEY)
            content_length = metadata.get(FRESHNESS_CONTENT_LENGTH_KEY)
            if (
                doc.doc_updated_at is not None
                and isinstance(last_modified_raw, str)
                and isinstance(content_length, str)
                and _doc_matches_freshness(
                    db_doc,
                    last_modified_dt=doc.doc_updated_at,
                    last_modified_raw=last_modified_raw,
                    content_length=content_length,
                )
            ):
                skipped += 1
                continue

            _log_once(
                "sentinel_mismatch",
                "warning",
                "PDF freshness sentinel did not match DB validators; "
                "allowing normal indexing for url=%s",
                doc.id,
            )
            passthrough_documents.append(doc)

        if skipped:
            _log_debug(
                "skipped %s PDF freshness sentinels before indexing",
                skipped,
            )

        return original_get_docs_to_update(
            passthrough_documents,
            db_docs,
            ignore_timestamp_gate=ignore_timestamp_gate,
        )

    indexing_pipeline.get_docs_to_update = _patched_get_docs_to_update
    _INDEXING_SKIP_PATCHED = True
    _log("info", "patched indexing pipeline for PDF freshness sentinels")


def _apply_web_connector_http_freshness_patch() -> None:
    if not _env_enabled("ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_ENABLED", True):
        return

    try:
        import requests

        from onyx.configs.app_configs import REQUEST_TIMEOUT_SECONDS
        from onyx.configs.constants import DocumentSource
        from onyx.connectors.models import Document
        from onyx.connectors.web import connector as web_connector
        from onyx.utils.logger import setup_logger
        from onyx.utils.playwright_fetch import DEFAULT_HEADERS
        from onyx.utils.web_content import is_pdf_resource
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize_background: failed importing Web connector patch deps: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    global _PATCH_LOGGER
    try:
        _PATCH_LOGGER = setup_logger("sitecustomize_background")
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize_background: failed configuring logger: {e}",
            flush=True,
        )

    original_do_scrape = web_connector.WebConnector._do_scrape
    _apply_indexing_freshness_skip_patch()

    def _patched_do_scrape(self, index, initial_url, session_ctx, slim=False):  # noqa: ANN001
        if slim or not _is_allowed_url(initial_url):
            return original_do_scrape(self, index, initial_url, session_ctx, slim=slim)

        last_modified_raw = None
        last_modified_dt = None
        content_length = None
        is_pdf = False

        try:
            head_response = requests.head(
                initial_url,
                headers=DEFAULT_HEADERS,
                allow_redirects=True,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            content_type = head_response.headers.get("content-type")
            is_pdf = is_pdf_resource(initial_url, content_type)
            last_modified_raw = head_response.headers.get("last-modified")
            last_modified_dt = _parse_last_modified(last_modified_raw)
            content_length = _normal_content_length(
                head_response.headers.get("content-length")
            )
            if (
                _INDEXING_SKIP_PATCHED
                and is_pdf
                and head_response.status_code in TERMINAL_HTTP_STATUS_SKIP_CODES
            ):
                result = web_connector.ScrapeResult()
                result.doc = Document(
                    id=initial_url,
                    sections=[],
                    source=DocumentSource.WEB,
                    semantic_identifier=_semantic_identifier(initial_url),
                    metadata={},
                    doc_metadata=_unreadable_freshness_metadata(
                        status_code=head_response.status_code,
                        last_modified_raw=last_modified_raw,
                        content_length=content_length,
                    ),
                    doc_updated_at=last_modified_dt,
                )
                session_ctx.last_error = (
                    f"Skipped indexing {initial_url} due to HTTP "
                    f"{head_response.status_code} response"
                )
                _log_debug(
                    "skipped unreadable PDF before download url=%s status=%s "
                    "last_modified=%s content_length=%s",
                    initial_url,
                    head_response.status_code,
                    last_modified_raw,
                    content_length,
                )
                return result

            if (
                is_pdf
                and head_response.status_code in TERMINAL_HTTP_STATUS_SKIP_CODES
            ):
                _log_once(
                    "indexing_unreadable_skip_patch_missing",
                    "warning",
                    "PDF freshness saw terminal HTTP %s, but indexing sentinel "
                    "skip patch is unavailable; falling back to normal scrape.",
                    head_response.status_code,
                )

            if head_response.status_code >= 400:
                _log_once(
                    f"head_status_{head_response.status_code}",
                    "warning",
                    "PDF freshness HEAD returned HTTP %s; falling back to normal "
                    "scrape for this and similar URLs. sample_url=%s final_url=%s",
                    head_response.status_code,
                    initial_url,
                    head_response.url,
                )
                return original_do_scrape(
                    self, index, initial_url, session_ctx, slim=slim
                )
        except Exception:
            # Preserve upstream retry/error behavior. The original implementation
            # will repeat the HEAD request and surface any failure normally.
            _log_exception(
                "PDF freshness HEAD check failed; falling back to normal scrape "
                "url=%s",
                initial_url,
            )
            return original_do_scrape(self, index, initial_url, session_ctx, slim=slim)

        if not is_pdf:
            return original_do_scrape(self, index, initial_url, session_ctx, slim=slim)

        if last_modified_dt is None or content_length is None:
            _log_once(
                "missing_http_validators",
                "warning",
                "PDF freshness miss url=%s reason=missing_http_validators "
                "last_modified=%s content_length=%s; falling back to normal "
                "scrape. Further missing-validator messages are suppressed.",
                initial_url,
                last_modified_raw,
                content_length,
            )
        else:
            try:
                db_doc = _get_db_document(initial_url)
                db_metadata = dict(db_doc.doc_metadata or {}) if db_doc else {}
                if (
                    _INDEXING_SKIP_PATCHED
                    and _doc_matches_freshness(
                        db_doc,
                        last_modified_dt=last_modified_dt,
                        last_modified_raw=last_modified_raw,
                        content_length=content_length,
                    )
                ):
                    result = web_connector.ScrapeResult()
                    result.doc = Document(
                        id=initial_url,
                        sections=[],
                        source=DocumentSource.WEB,
                        semantic_identifier=_semantic_identifier(initial_url),
                        metadata={},
                        doc_metadata=_unchanged_freshness_metadata(
                            last_modified_raw=last_modified_raw,
                            content_length=content_length,
                        ),
                        doc_updated_at=last_modified_dt,
                    )
                    _log_debug(
                        "skipped unchanged PDF before download url=%s "
                        "last_modified=%s content_length=%s hash=%s",
                        initial_url,
                        last_modified_raw,
                        content_length,
                        _hash_for_log(db_doc.content_hash),
                    )
                    return result
                if not _INDEXING_SKIP_PATCHED and _doc_matches_freshness(
                    db_doc,
                    last_modified_dt=last_modified_dt,
                    last_modified_raw=last_modified_raw,
                    content_length=content_length,
                ):
                    _log_once(
                        "indexing_skip_patch_missing",
                        "warning",
                        "PDF freshness validators match, but indexing sentinel "
                        "skip patch is unavailable; falling back to normal scrape.",
                    )
                if db_doc is None:
                    _log_debug(
                        "PDF freshness miss url=%s reason=db_doc_missing "
                        "last_modified=%s content_length=%s",
                        initial_url,
                        last_modified_raw,
                        content_length,
                    )
                elif db_doc.doc_updated_at is None:
                    _log_debug(
                        "PDF freshness miss url=%s reason=doc_updated_at_missing "
                        "chunks=%s hash=%s last_modified=%s content_length=%s",
                        initial_url,
                        getattr(db_doc, "chunk_count", None),
                        _hash_for_log(db_doc.content_hash),
                        last_modified_raw,
                        content_length,
                    )
                elif (
                    db_doc.doc_updated_at.astimezone(timezone.utc).replace(
                        microsecond=0
                    )
                    != last_modified_dt
                ):
                    _log_debug(
                        "PDF freshness miss url=%s reason=doc_updated_at_mismatch "
                        "db_doc_updated_at=%s http_last_modified=%s "
                        "content_length=%s hash=%s",
                        initial_url,
                        db_doc.doc_updated_at,
                        last_modified_raw,
                        content_length,
                        _hash_for_log(db_doc.content_hash),
                    )
                elif db_metadata.get(FRESHNESS_META_VERSION_KEY) != FRESHNESS_VERSION:
                    _log_debug(
                        "PDF freshness miss url=%s reason=freshness_version_missing "
                        "db_version=%s expected_version=%s last_modified=%s "
                        "content_length=%s hash=%s",
                        initial_url,
                        db_metadata.get(FRESHNESS_META_VERSION_KEY),
                        FRESHNESS_VERSION,
                        last_modified_raw,
                        content_length,
                        _hash_for_log(db_doc.content_hash),
                    )
                elif db_metadata.get(FRESHNESS_LAST_MODIFIED_KEY) != last_modified_raw:
                    _log_debug(
                        "PDF freshness miss url=%s reason=last_modified_metadata_mismatch "
                        "db_last_modified=%s http_last_modified=%s "
                        "content_length=%s hash=%s",
                        initial_url,
                        db_metadata.get(FRESHNESS_LAST_MODIFIED_KEY),
                        last_modified_raw,
                        content_length,
                        _hash_for_log(db_doc.content_hash),
                    )
                else:
                    _log_debug(
                        "PDF freshness miss url=%s reason=content_length_metadata_mismatch "
                        "db_content_length=%s http_content_length=%s "
                        "last_modified=%s hash=%s",
                        initial_url,
                        db_metadata.get(FRESHNESS_CONTENT_LENGTH_KEY),
                        content_length,
                        last_modified_raw,
                        _hash_for_log(db_doc.content_hash),
                    )
            except Exception as e:
                _log_exception(
                    "freshness lookup failed for url=%s error=%s",
                    initial_url,
                    e,
                )

        result = original_do_scrape(self, index, initial_url, session_ctx, slim=slim)

        if (
            result.doc is None
            or last_modified_dt is None
            or content_length is None
            or result.doc.id is None
        ):
            _log_debug(
                "PDF freshness cannot seed after scrape url=%s result_doc=%s "
                "last_modified=%s content_length=%s result_doc_id=%s",
                initial_url,
                result.doc is not None,
                last_modified_raw,
                content_length,
                getattr(result.doc, "id", None) if result.doc is not None else None,
            )
            return result

        freshness_metadata = _freshness_metadata(
            result.doc.doc_metadata,
            last_modified_raw=last_modified_raw or last_modified_dt.isoformat(),
            content_length=content_length,
        )

        try:
            db_doc = _get_db_document(result.doc.id)
            current_hash_without_freshness = result.doc.content_hash()
            if (
                db_doc is not None
                and db_doc.content_hash is not None
                and db_doc.content_hash == current_hash_without_freshness
            ):
                _seed_db_freshness(
                    result.doc.id,
                    doc_updated_at=last_modified_dt,
                    doc_metadata=freshness_metadata,
                )
                result.doc.doc_updated_at = last_modified_dt
                # Leave doc_metadata unchanged on the returned doc so downstream
                # content-hash comparison still matches the currently indexed doc.
                _log_debug(
                    "seeded unchanged PDF freshness url=%s last_modified=%s "
                    "content_length=%s hash=%s",
                    result.doc.id,
                    last_modified_raw,
                    content_length,
                    _hash_for_log(db_doc.content_hash),
                )
                return result
            if db_doc is None:
                _log_debug(
                    "parsed PDF treated as new document url=%s last_modified=%s "
                    "content_length=%s parsed_hash=%s",
                    result.doc.id,
                    last_modified_raw,
                    content_length,
                    _hash_for_log(current_hash_without_freshness),
                )
            else:
                _log_debug(
                    "parsed PDF content hash differs; allowing reindex url=%s "
                    "db_hash=%s parsed_hash=%s last_modified=%s content_length=%s",
                    result.doc.id,
                    _hash_for_log(db_doc.content_hash),
                    _hash_for_log(current_hash_without_freshness),
                    last_modified_raw,
                    content_length,
                )
        except Exception as e:
            _log_exception(
                "freshness seed failed for url=%s error=%s",
                result.doc.id,
                e,
            )

        result.doc.doc_updated_at = last_modified_dt
        result.doc.doc_metadata = freshness_metadata
        return result

    web_connector.WebConnector._do_scrape = _patched_do_scrape
    _log(
        "info",
        "patched WebConnector PDF freshness hosts=%s",
        ",".join(sorted(_allowed_hosts())),
    )


_apply_playwright_helper_proxy_patch()
_apply_web_connector_http_freshness_patch()
