"""Background-worker runtime patches for stock Onyx containers.

Loaded automatically by Python when this directory is on PYTHONPATH.
"""

from __future__ import annotations

import functools
import inspect
import os
import sys
from contextlib import redirect_stdout
from contextvars import ContextVar
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from urllib.parse import urlsplit
from urllib.parse import urlunsplit


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
_WEB_CONNECTOR_PROXY: ContextVar[str | None] = ContextVar(
    "wrapper_web_connector_proxy", default=None
)
_CONTROL_PROCESS_ARGV0 = frozenset(
    {
        "/app/wrapper-background-entrypoint.py",
        "/app/wrapper-beat-liveness-watchdog.py",
        "/usr/bin/supervisord",
    }
)

_INDEXING_FRESHNESS_PARAMETERS = (
    "documents",
    "db_docs",
    "ignore_timestamp_gate",
    "ignore_content_hash_gate",
)
_INDEXING_FRESHNESS_SOURCE_MARKERS = (
    "id_update_time_map",
    "ignore_timestamp_gate",
    "ignore_content_hash_gate",
    "doc.content_hash()",
    "db_doc.content_hash",
)
_WEB_SCRAPE_PARAMETERS = (
    "self",
    "index",
    "initial_url",
    "session_ctx",
    "slim",
)
_WEB_SCRAPE_SOURCE_MARKERS = (
    "requests.head(",
    "is_pdf_resource(initial_url, content_type)",
    "requests.get(",
    "extract_pdf_text(response.content)",
    "result.doc = Document(",
)


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


def _apply_configured_inference_proxy_patch() -> None:
    try:
        from wrapper_env_patches import apply_configured_inference_proxy_patch

        apply_configured_inference_proxy_patch()
    except Exception as e:  # pragma: no cover
        print(
            "sitecustomize_background: failed to patch configured inference egress: "
            f"{e}",
            flush=True,
        )
        if _strict_mode():
            raise


def _apply_embedding_tokenizer_alias_patch() -> None:
    try:
        from wrapper_env_patches import apply_embedding_tokenizer_alias_patch

        apply_embedding_tokenizer_alias_patch()
    except Exception as e:  # pragma: no cover
        print(
            "sitecustomize_background: failed to patch embedding tokenizer alias: "
            f"{e}",
            flush=True,
        )
        if _strict_mode():
            raise


def _apply_sleepy_background_patch() -> None:
    """Strictly reduce idle-only background scheduling and liveness work."""
    try:
        from onyx.background.celery.apps import app_base
        from onyx.background.celery.apps import beat as beat_app
        from onyx.background.celery.tasks import beat_schedule
        from onyx.configs.app_configs import DISABLE_TELEMETRY
        from onyx.configs.constants import OnyxCeleryQueues
        from onyx.configs.constants import OnyxCeleryTask
        from shared_configs.configs import MULTI_TENANT

        if MULTI_TENANT:
            raise RuntimeError("sleepy background policy requires MULTI_TENANT=false")
        if not DISABLE_TELEMETRY:
            raise RuntimeError(
                "sleepy background policy requires DISABLE_TELEMETRY=true"
            )

        discovery = {
            "check-for-user-file-processing": timedelta(seconds=20),
            "check-for-user-file-project-sync": timedelta(seconds=20),
            "check-for-user-file-delete": timedelta(seconds=20),
            "check-for-indexing": timedelta(seconds=15),
            "check-for-port": timedelta(seconds=30),
            "check-for-connector-deletion": timedelta(seconds=20),
            "check-for-vespa-sync": timedelta(seconds=20),
            "check-for-pruning": timedelta(seconds=20),
        }
        for name, old_schedule in discovery.items():
            matches = [
                task for task in beat_schedule.tasks_to_schedule
                if task.get("name") == name
            ]
            if len(matches) != 1 or matches[0].get("schedule") != old_schedule:
                raise RuntimeError(
                    f"expected one {name} schedule at {old_schedule}, found {matches!r}"
                )
            matches[0]["schedule"] = timedelta(minutes=5)
            template_matches = [
                task for task in beat_schedule.beat_task_templates
                if task.get("name") == name
            ]
            if len(template_matches) != 1 or template_matches[0].get("schedule") != old_schedule:
                raise RuntimeError(f"unexpected {name} beat template contract")
            template_matches[0]["schedule"] = timedelta(minutes=5)

        removals = {
            "monitor-celery-queues": (
                OnyxCeleryTask.MONITOR_CELERY_QUEUES,
                timedelta(seconds=10),
            ),
            "monitor-background-processes": (
                OnyxCeleryTask.MONITOR_BACKGROUND_PROCESSES,
                timedelta(minutes=5),
            ),
            "monitor-process-memory": (
                OnyxCeleryTask.MONITOR_PROCESS_MEMORY,
                timedelta(minutes=5),
            ),
            "celery-beat-heartbeat": (
                OnyxCeleryTask.CELERY_BEAT_HEARTBEAT,
                timedelta(minutes=1),
            ),
            "emit-version-telemetry": (
                OnyxCeleryTask.EMIT_VERSION_TELEMETRY,
                timedelta(hours=1),
            ),
        }
        craft_enabled = os.environ.get("ENABLE_CRAFT", "false").lower() in {
            "1", "true", "yes", "on"
        }
        if not craft_enabled:
            removals.update(
                {
                    "cleanup-idle-sandboxes": (
                        OnyxCeleryTask.CLEANUP_IDLE_SANDBOXES,
                        timedelta(minutes=1),
                    ),
                    "dispatch-due-scheduled-tasks": (
                        OnyxCeleryTask.SCHEDULED_TASKS_DISPATCH_DUE,
                        timedelta(seconds=30),
                    ),
                    "cleanup-stuck-scheduled-runs": (
                        OnyxCeleryTask.SCHEDULED_TASKS_CLEANUP_STUCK,
                        timedelta(hours=1),
                    ),
                }
            )

        for name, (task_id, cadence) in removals.items():
            matches = [
                task for task in beat_schedule.tasks_to_schedule
                if task.get("name") == name
            ]
            if (
                len(matches) != 1
                or matches[0].get("task") != task_id
                or matches[0].get("schedule") != cadence
            ):
                raise RuntimeError(f"unexpected materialized {name} schedule contract")
            beat_schedule.tasks_to_schedule[:] = [
                task for task in beat_schedule.tasks_to_schedule
                if task.get("name") != name
            ]
            template_matches = [
                task for task in beat_schedule.beat_task_templates
                if task.get("name") == name
            ]
            if template_matches:
                if (
                    len(template_matches) != 1
                    or template_matches[0].get("task") != task_id
                    or template_matches[0].get("schedule") != cadence
                ):
                    raise RuntimeError(f"unexpected {name} beat template contract")
                beat_schedule.beat_task_templates[:] = [
                    task for task in beat_schedule.beat_task_templates
                    if task.get("name") != name
                ]

        unexpected_conditional = {
            "check-for-doc-permissions-sync",
            "check-for-external-group-sync",
            "check-for-auto-llm-update",
            "migrate-chunks-from-vespa-to-opensearch",
        }
        materialized_names = {
            task.get("name") for task in beat_schedule.tasks_to_schedule
        }
        unexpected = materialized_names & unexpected_conditional
        if unexpected:
            raise RuntimeError(
                f"unexpected conditional background schedules: {sorted(unexpected)}"
            )
        removed_names = set(removals)
        if materialized_names & removed_names:
            raise RuntimeError("removed background schedules remain materialized")
        if any(
            task.get("options", {}).get("queue") == OnyxCeleryQueues.MONITORING
            for task in beat_schedule.tasks_to_schedule
        ):
            raise RuntimeError("a self-hosted task still targets the monitoring queue")
        if beat_schedule.get_tasks_to_schedule() is not beat_schedule.tasks_to_schedule:
            raise RuntimeError("get_tasks_to_schedule no longer returns the materialized list")

        housekeeping = {
            "check-for-incognito-file-cleanup": timedelta(minutes=10),
            "check-for-checkpoint-cleanup": timedelta(hours=1),
            "check-for-index-attempt-cleanup": timedelta(minutes=30),
            "check-for-hierarchy-fetching": timedelta(hours=1),
        }
        effective_by_name = {
            task.get("name"): task for task in beat_schedule.tasks_to_schedule
        }
        expected_names = set(discovery) | set(housekeeping)
        if set(effective_by_name) != expected_names:
            raise RuntimeError(
                "unclassified self-hosted background schedules: "
                f"expected={sorted(expected_names)!r} "
                f"actual={sorted(effective_by_name)!r}"
            )
        for name, cadence in housekeeping.items():
            if effective_by_name[name].get("schedule") != cadence:
                raise RuntimeError(f"unexpected {name} housekeeping cadence")

        if beat_app.DynamicTenantScheduler.RELOAD_INTERVAL != 60:
            raise RuntimeError("unexpected Beat scheduler reload interval")
        beat_app.DynamicTenantScheduler.RELOAD_INTERVAL = 300

        bootsteps = app_base.get_bootsteps()
        if bootsteps != [app_base.LivenessProbe]:
            raise RuntimeError(f"unexpected worker bootsteps: {bootsteps!r}")
        app_base.get_bootsteps = lambda: []

        print(
            "sitecustomize_background: installed strict sleepy background policy",
            flush=True,
        )
    except Exception as e:  # pragma: no cover
        print(
            "sitecustomize_background: failed to patch sleepy background policy: "
            f"{e}",
            flush=True,
        )
        if _strict_mode():
            raise


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
        from wrapper_env_patches import _validated_fixed_proxy_url
        from wrapper_env_patches import select_playwright_proxy
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
            kwargs["proxies"] = (
                {"http": selected, "https": selected} if selected else {}
            )
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


def _rewrite_doc_drop_display_links(result):  # noqa: ANN001
    """Rewrite only returned section links; retain internal document identity."""
    internal_raw = os.environ.get("ONYX_WEB_CONNECTOR_INTERNAL_BASE_URL", "").strip()
    display_raw = os.environ.get("ONYX_WEB_CONNECTOR_DISPLAY_BASE_URL", "").strip()
    if not internal_raw or not display_raw or getattr(result, "doc", None) is None:
        return result
    internal = urlsplit(internal_raw)
    display = urlsplit(display_raw)
    if (
        internal.scheme != "http"
        or internal.hostname != "doc-drop-web"
        or internal.port != 8091
        or display.scheme not in {"http", "https"}
        or display.hostname not in {"localhost", "127.0.0.1", "::1"}
    ):
        raise RuntimeError("invalid doc-drop internal/display base URL configuration")
    internal_prefix = internal.path.rstrip("/") + "/"
    display_prefix = display.path.rstrip("/")
    for section in result.doc.sections:
        link = getattr(section, "link", None)
        if not link:
            continue
        parsed = urlsplit(link)
        if (
            parsed.scheme != internal.scheme
            or parsed.hostname != internal.hostname
            or parsed.port != internal.port
            or not parsed.path.startswith(internal_prefix)
        ):
            continue
        section.link = urlunsplit(
            (
                display.scheme,
                display.netloc,
                display_prefix + parsed.path,
                parsed.query,
                parsed.fragment,
            )
        )
    return result


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


def _validate_callable_contract(
    callable_obj,  # noqa: ANN001
    *,
    name: str,
    expected_parameters: tuple[str, ...],
    source_markers: tuple[str, ...],
) -> None:
    parameters = tuple(inspect.signature(callable_obj).parameters)
    if parameters != expected_parameters:
        raise RuntimeError(
            f"{name} signature changed: expected {expected_parameters!r}, "
            f"got {parameters!r}"
        )
    try:
        source = inspect.getsource(callable_obj)
    except (OSError, TypeError) as e:
        raise RuntimeError(f"could not inspect {name} source") from e
    missing = [marker for marker in source_markers if marker not in source]
    if missing:
        raise RuntimeError(
            f"{name} source contract changed; missing markers: {missing!r}"
        )


def _validate_freshness_model_contracts(Document, DbDocument, ScrapeResult) -> None:  # noqa: N803, ANN001
    model_fields = getattr(Document, "model_fields", {})
    required_document_fields = {
        "id",
        "sections",
        "source",
        "semantic_identifier",
        "metadata",
        "doc_metadata",
        "doc_updated_at",
    }
    missing_document_fields = required_document_fields.difference(model_fields)
    if missing_document_fields:
        raise RuntimeError(
            "connector Document model contract changed; missing fields: "
            f"{sorted(missing_document_fields)!r}"
        )

    required_db_attributes = {"id", "doc_updated_at", "doc_metadata", "content_hash"}
    missing_db_attributes = {
        name for name in required_db_attributes if not hasattr(DbDocument, name)
    }
    if missing_db_attributes:
        raise RuntimeError(
            "database Document model contract changed; missing attributes: "
            f"{sorted(missing_db_attributes)!r}"
        )

    if not hasattr(ScrapeResult, "doc") or not hasattr(ScrapeResult, "retry"):
        raise RuntimeError("Web connector ScrapeResult contract changed")


def _filter_freshness_sentinels(documents, db_docs):  # noqa: ANN001
    """Return safe passthrough documents and the number of sentinels skipped."""
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

        raise RuntimeError(
            "PDF freshness sentinel no longer matches the stored validators; "
            "refusing to index its empty placeholder so a later crawl can "
            f"perform a full scrape: {doc.id}"
        )

    return passthrough_documents, skipped


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
    if _INDEXING_SKIP_PATCHED:
        return

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
    _validate_callable_contract(
        original_get_docs_to_update,
        name="indexing_pipeline.get_docs_to_update",
        expected_parameters=_INDEXING_FRESHNESS_PARAMETERS,
        source_markers=_INDEXING_FRESHNESS_SOURCE_MARKERS,
    )

    def _patched_get_docs_to_update(  # noqa: ANN001
        documents,
        db_docs,
        ignore_timestamp_gate=False,
        ignore_content_hash_gate=False,
    ):
        passthrough_documents, skipped = _filter_freshness_sentinels(
            documents, db_docs
        )

        if skipped:
            _log_debug(
                "skipped %s PDF freshness sentinels before indexing",
                skipped,
            )

        return original_get_docs_to_update(
            passthrough_documents,
            db_docs,
            ignore_timestamp_gate=ignore_timestamp_gate,
            ignore_content_hash_gate=ignore_content_hash_gate,
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
        from onyx.db.models import Document as DbDocument
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
    if getattr(original_do_scrape, "_private_onyx_pdf_freshness_patch", False):
        return
    _validate_callable_contract(
        original_do_scrape,
        name="WebConnector._do_scrape",
        expected_parameters=_WEB_SCRAPE_PARAMETERS,
        source_markers=_WEB_SCRAPE_SOURCE_MARKERS,
    )
    _validate_freshness_model_contracts(
        Document, DbDocument, web_connector.ScrapeResult
    )
    _apply_indexing_freshness_skip_patch()

    def _scrape_with_display_links(self, index, initial_url, session_ctx, slim=False):  # noqa: ANN001
        return _rewrite_doc_drop_display_links(
            original_do_scrape(self, index, initial_url, session_ctx, slim=slim)
        )

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
                return _scrape_with_display_links(
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
            return _scrape_with_display_links(self, index, initial_url, session_ctx, slim=slim)

        if not is_pdf:
            return _scrape_with_display_links(self, index, initial_url, session_ctx, slim=slim)

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

        result = _scrape_with_display_links(self, index, initial_url, session_ctx, slim=slim)

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

    setattr(_patched_do_scrape, "_private_onyx_pdf_freshness_patch", True)
    web_connector.WebConnector._do_scrape = _patched_do_scrape
    _log(
        "info",
        "patched WebConnector PDF freshness hosts=%s",
        ",".join(sorted(_allowed_hosts())),
    )


def _install() -> None:
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


# Keep stdout clean for Onyx subprocess protocols such as
# onyx.utils.isolated_runner, which imports sitecustomize before writing its
# single pickled result. Docker captures stderr alongside stdout.
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
