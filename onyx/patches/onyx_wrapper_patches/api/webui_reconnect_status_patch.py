"""Install a reliable, recovery-only chat run status endpoint."""

from __future__ import annotations

import inspect
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException
from pydantic import BaseModel


_ROUTE_PATH = "/reconnect-status/{session_id}"
_FULL_ROUTE_PATH = "/chat/reconnect-status/{session_id}"
_PATCH_MARKER = "_wrapper_webui_reconnect_status_patch"
_UNAVAILABLE_DETAIL = "Chat recovery status is temporarily unavailable"
_RESERVED_MESSAGE = "Response was terminated prior to completion, try regenerating."


class WebUIReconnectCurrentRun(BaseModel):
    run_id: int


class WebUIReconnectStatus(BaseModel):
    incognito: bool
    current_run: WebUIReconnectCurrentRun | None = None
    pending_reservation: bool = False
    resumable: bool = False


def _route_paths(router: Any) -> list[tuple[str, set[str]]]:
    return [
        (route.path, set(route.methods or set()))
        for route in router.routes
        if hasattr(route, "path") and hasattr(route, "methods")
    ]


def _reliable_current_run(
    *,
    session_id: UUID,
    cache: Any,
    get_processing_run_id: Any,
    is_chat_session_processing: Any,
    transient_errors: tuple[type[Exception], ...],
) -> WebUIReconnectCurrentRun | None:
    try:
        run_id = get_processing_run_id(session_id, cache)
        if run_id is not None:
            return WebUIReconnectCurrentRun(run_id=run_id)
        processing = is_chat_session_processing(session_id, cache)
    except transient_errors as exc:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL) from exc

    # A present fence with no positive run id is either the supported
    # pre-reservation state or an invalid fence. None is authoritative
    # completion, so make the browser retry.
    if processing:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)
    return None


def _has_pending_reservation(
    *, session_id: UUID, db_session: Any, chat_message_model: Any
) -> bool:
    latest = (
        db_session.query(chat_message_model)
        .filter(chat_message_model.chat_session_id == session_id)
        .order_by(chat_message_model.id.desc())
        .first()
    )
    return bool(
        latest
        and latest.message == _RESERVED_MESSAGE
        and latest.error is None
    )


def _reliable_resumable(
    *,
    session_id: UUID,
    current_run: WebUIReconnectCurrentRun | None,
    cache: Any,
    has_stream_buffer: Any,
    transient_errors: tuple[type[Exception], ...],
) -> bool:
    if current_run is None:
        return False
    try:
        return bool(has_stream_buffer(cache, session_id, current_run.run_id))
    except transient_errors as exc:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL) from exc


def install() -> None:
    from onyx.cache.interface import CACHE_TRANSIENT_ERRORS
    from onyx.chat.chat_processing_checker import get_processing_run_id
    from onyx.chat.chat_processing_checker import is_chat_session_processing
    from onyx.chat.stream_buffer import has_stream_buffer
    from onyx.db.chat import reserve_message_id
    from onyx.db.chat import reserve_multi_model_message_ids
    from onyx.db.models import ChatMessage
    from onyx.server.query_and_chat import chat_backend

    router = chat_backend.router
    if getattr(router, _PATCH_MARKER, False):
        return
    if any(path == _FULL_ROUTE_PATH for path, _methods in _route_paths(router)):
        raise RuntimeError(
            "Onyx now supplies the WebUI reconnect status route; audit and "
            "remove the wrapper patch"
        )

    source = inspect.getsource(chat_backend.get_chat_session)
    swallowed_cache_marker = (
        'except Exception:\n        logger.exception(\n'
        '            "An error occurred while checking if the chat session is processing"'
    )
    if source.count(swallowed_cache_marker) != 1:
        raise RuntimeError("Onyx chat-session cache-error handling drifted")
    for reservation_function in (reserve_message_id, reserve_multi_model_message_ids):
        if inspect.getsource(reservation_function).count(_RESERVED_MESSAGE) != 1:
            raise RuntimeError("Onyx reserved-message placeholder drifted")
    if tuple(inspect.signature(has_stream_buffer).parameters) != (
        "cache",
        "chat_session_id",
        "run_id",
    ):
        raise RuntimeError("Onyx stream-buffer readiness probe drifted")

    # Match the stock READ_CHAT dependency exactly.
    read_chat_dependency = chat_backend.require_permission(
        chat_backend.Permission.READ_CHAT, allow_anonymous=True
    )

    def reconnect_status(
        session_id: UUID,
        user: Any = Depends(read_chat_dependency),
        db_session: Any = Depends(chat_backend.get_session),
    ) -> WebUIReconnectStatus:
        # Do not call get_chat_session here: it loads and translates the full
        # message history even though recovery needs only narrow status fields.
        try:
            chat_session = chat_backend.get_chat_session_by_id(
                chat_session_id=session_id,
                user_id=user.id,
                db_session=db_session,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail="Chat session not found"
            ) from exc
        cache = chat_backend.get_cache_backend()
        current_run = _reliable_current_run(
            session_id=session_id,
            cache=cache,
            get_processing_run_id=get_processing_run_id,
            is_chat_session_processing=is_chat_session_processing,
            transient_errors=CACHE_TRANSIENT_ERRORS,
        )
        pending_reservation = current_run is None and _has_pending_reservation(
            session_id=session_id,
            db_session=db_session,
            chat_message_model=ChatMessage,
        )
        resumable = _reliable_resumable(
            session_id=session_id,
            current_run=current_run,
            cache=cache,
            has_stream_buffer=has_stream_buffer,
            transient_errors=CACHE_TRANSIENT_ERRORS,
        )
        return WebUIReconnectStatus(
            incognito=chat_session.incognito_record_mode is not None,
            current_run=current_run,
            pending_reservation=pending_reservation,
            resumable=resumable,
        )

    router.add_api_route(
        _ROUTE_PATH,
        reconnect_status,
        methods=["GET"],
        response_model=WebUIReconnectStatus,
        name="wrapper_webui_reconnect_status",
    )
    installed = [
        methods for path, methods in _route_paths(router) if path == _FULL_ROUTE_PATH
    ]
    if installed != [{"GET"}]:
        raise RuntimeError("failed to install exact WebUI reconnect status route")

    setattr(router, _PATCH_MARKER, True)
    print(
        "sitecustomize_api_server: installed reliable WebUI reconnect status endpoint",
        flush=True,
    )
