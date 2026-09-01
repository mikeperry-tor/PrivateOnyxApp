from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "onyx/patches/sitecustomize_api_server/webui_reconnect_status_patch.py"
)
SPEC = importlib.util.spec_from_file_location(
    "webui_reconnect_status_patch_under_test", MODULE_PATH
)
assert SPEC and SPEC.loader
patch_module = importlib.util.module_from_spec(SPEC)


class HTTPException(Exception):
    def __init__(self, *, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class BaseModel:
    def __init__(self, **values):
        for key, value in values.items():
            setattr(self, key, value)


fastapi = types.ModuleType("fastapi")
fastapi.Depends = lambda dependency: dependency
fastapi.HTTPException = HTTPException
pydantic = types.ModuleType("pydantic")
pydantic.BaseModel = BaseModel
original_modules = {name: sys.modules.get(name) for name in ("fastapi", "pydantic")}
try:
    sys.modules["fastapi"] = fastapi
    sys.modules["pydantic"] = pydantic
    SPEC.loader.exec_module(patch_module)
finally:
    for name, original in original_modules.items():
        if original is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original


SESSION_ID = UUID("11111111-1111-4111-8111-111111111111")


class TransientCacheError(Exception):
    pass


class Router:
    def __init__(self):
        self.prefix = "/chat"
        self.routes = []

    def add_api_route(self, path, endpoint, *, methods, name, **_kwargs):
        self.routes.append(
            SimpleNamespace(
                path=f"{self.prefix}{path}",
                methods=set(methods),
                endpoint=endpoint,
                name=name,
            )
        )


class Query:
    def __init__(self, latest):
        self.latest = latest

    def filter(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def first(self):
        return self.latest


class DescColumn:
    def desc(self):
        return self


class ChatMessageModel:
    chat_session_id = object()
    id = DescColumn()


class WebUIReconnectStatusPatchTests(unittest.TestCase):
    def resolve(
        self,
        *,
        processing=False,
        run_id=None,
        exists_error=None,
        get_error=None,
    ):
        calls: list[str] = []

        def is_processing(_session_id, _cache):
            calls.append("exists")
            if exists_error is not None:
                raise exists_error
            return processing

        def get_run(_session_id, _cache):
            calls.append("get")
            if get_error is not None:
                raise get_error
            return run_id

        result = patch_module._reliable_current_run(
            session_id=SESSION_ID,
            cache=object(),
            get_processing_run_id=get_run,
            is_chat_session_processing=is_processing,
            transient_errors=(TransientCacheError,),
        )
        return result, calls

    def test_reliable_idle_and_active_results(self) -> None:
        idle, idle_calls = self.resolve(processing=False, run_id=None)
        self.assertIsNone(idle)
        self.assertEqual(idle_calls, ["get", "exists"])

        active, active_calls = self.resolve(processing=True, run_id=23)
        self.assertEqual(active.run_id, 23)
        self.assertEqual(active_calls, ["get"])

    def test_present_fence_without_run_id_is_retryable_not_complete(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            self.resolve(processing=True, run_id=None)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(
            caught.exception.detail,
            "Chat recovery status is temporarily unavailable",
        )

    def test_transient_cache_errors_are_retryable(self) -> None:
        for kwargs in (
            {"exists_error": TransientCacheError("redis unavailable")},
            {"get_error": TransientCacheError("postgres unavailable")},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(HTTPException) as caught:
                    self.resolve(**kwargs)
                self.assertEqual(caught.exception.status_code, 503)

    def test_unexpected_cache_errors_remain_visible(self) -> None:
        failure = RuntimeError("programming defect")
        with self.assertRaises(RuntimeError) as caught:
            self.resolve(get_error=failure)
        self.assertIs(caught.exception, failure)

    def test_pending_reservation_requires_exact_unerrored_placeholder(self) -> None:
        for latest, expected in (
            (SimpleNamespace(message=patch_module._RESERVED_MESSAGE, error=None), True),
            (SimpleNamespace(message=patch_module._RESERVED_MESSAGE, error="failed"), False),
            (SimpleNamespace(message="settled answer", error=None), False),
            (None, False),
        ):
            with self.subTest(latest=latest):
                db_session = SimpleNamespace(query=lambda _model: Query(latest))
                self.assertEqual(
                    patch_module._has_pending_reservation(
                        session_id=SESSION_ID,
                        db_session=db_session,
                        chat_message_model=ChatMessageModel,
                    ),
                    expected,
                )

    def test_resumable_requires_an_active_run_and_buffer_metadata(self) -> None:
        calls: list[tuple[UUID, int]] = []

        def has_buffer(_cache, session_id, run_id):
            calls.append((session_id, run_id))
            return True

        self.assertFalse(
            patch_module._reliable_resumable(
                session_id=SESSION_ID,
                current_run=None,
                cache=object(),
                has_stream_buffer=has_buffer,
                transient_errors=(TransientCacheError,),
            )
        )
        self.assertEqual(calls, [])
        self.assertTrue(
            patch_module._reliable_resumable(
                session_id=SESSION_ID,
                current_run=patch_module.WebUIReconnectCurrentRun(run_id=23),
                cache=object(),
                has_stream_buffer=has_buffer,
                transient_errors=(TransientCacheError,),
            )
        )
        self.assertEqual(calls, [(SESSION_ID, 23)])

    def test_resumable_cache_failure_is_retryable(self) -> None:
        def has_buffer(*_args):
            raise TransientCacheError("cache unavailable")

        with self.assertRaises(HTTPException) as caught:
            patch_module._reliable_resumable(
                session_id=SESSION_ID,
                current_run=patch_module.WebUIReconnectCurrentRun(run_id=23),
                cache=object(),
                has_stream_buffer=has_buffer,
                transient_errors=(TransientCacheError,),
            )
        self.assertEqual(caught.exception.status_code, 503)

    def test_api_bootstrap_installs_patch(self) -> None:
        bootstrap = (
            ROOT / "onyx/patches/sitecustomize_api_server/sitecustomize.py"
        ).read_text(encoding="utf-8")
        self.assertIn("from webui_reconnect_status_patch import", bootstrap)
        self.assertIn("install_webui_reconnect_status()", bootstrap)

    def test_status_route_does_not_load_full_session_history(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("chat_backend.get_chat_session_by_id(", source)
        self.assertNotIn("detail = chat_backend.get_chat_session(", source)

    def test_install_validates_the_router_prefixed_path(self) -> None:
        names = (
            "onyx",
            "onyx.cache",
            "onyx.cache.interface",
            "onyx.chat",
            "onyx.chat.chat_processing_checker",
            "onyx.chat.stream_buffer",
            "onyx.db",
            "onyx.db.chat",
            "onyx.db.models",
            "onyx.server",
            "onyx.server.query_and_chat",
            "onyx.server.query_and_chat.chat_backend",
        )
        modules = {name: types.ModuleType(name) for name in names}
        for name in (
            "onyx",
            "onyx.cache",
            "onyx.chat",
            "onyx.db",
            "onyx.server",
            "onyx.server.query_and_chat",
        ):
            modules[name].__path__ = []

        modules["onyx.cache.interface"].CACHE_TRANSIENT_ERRORS = (
            TransientCacheError,
        )
        checker = modules["onyx.chat.chat_processing_checker"]
        checker.get_processing_run_id = lambda *_args: None
        checker.is_chat_session_processing = lambda *_args: False
        def has_stream_buffer(cache, chat_session_id, run_id):
            return False

        modules["onyx.chat.stream_buffer"].has_stream_buffer = has_stream_buffer

        db_chat = modules["onyx.db.chat"]
        db_chat.reserve_message_id = lambda: None
        db_chat.reserve_multi_model_message_ids = lambda: None
        modules["onyx.db.models"].ChatMessage = ChatMessageModel

        chat_backend = modules["onyx.server.query_and_chat.chat_backend"]
        chat_backend.router = Router()
        chat_backend.Permission = SimpleNamespace(READ_CHAT="read-chat")
        chat_backend.require_permission = lambda *args, **kwargs: (args, kwargs)
        chat_backend.get_session = object()
        chat_backend.get_chat_session = lambda: None
        chat_backend.get_chat_session_by_id = lambda **_kwargs: None
        chat_backend.get_cache_backend = lambda: object()

        audited_source = (
            "def get_chat_session():\n"
            "    try:\n"
            "        pass\n"
            "    except Exception:\n"
            "        logger.exception(\n"
            '            "An error occurred while checking if the chat session is processing"\n'
            "        )\n"
            '    marker = "Response was terminated prior to completion, try regenerating."\n'
        )
        with patch.dict(sys.modules, modules, clear=False), patch.object(
            patch_module.inspect, "getsource", return_value=audited_source
        ):
            patch_module.install()

        routes = chat_backend.router.routes
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].path, "/chat/reconnect-status/{session_id}")
        self.assertEqual(routes[0].methods, {"GET"})


if __name__ == "__main__":
    unittest.main()
