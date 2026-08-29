from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import patch


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "onyx"
    / "patches"
    / "sitecustomize_background"
    / "sitecustomize.py"
)


def _compatible_get_docs_to_update(
    documents,
    db_docs,
    ignore_timestamp_gate=False,
    ignore_content_hash_gate=False,
):
    id_update_time_map = {}
    if ignore_timestamp_gate or ignore_content_hash_gate:
        id_update_time_map = {doc.id: doc for doc in db_docs}
    for doc in documents:
        doc.content_hash()
    for db_doc in db_docs:
        _ = db_doc.content_hash
    return documents, id_update_time_map


def _drifted_get_docs_to_update(documents):
    return documents


def _compatible_beat_tick(self):
    self._liveness_probe_path.touch()
    self._try_updating_schedule()
    self._last_reload = now  # noqa: F821


class WebConnectorEgressPatchTests(unittest.TestCase):
    def _load_patched_modules(
        self,
        level: str,
        *,
        freshness: bool = False,
        head_response=None,
    ):
        requests_module = ModuleType("requests")
        sessions_module = ModuleType("requests.sessions")
        calls: list[tuple[str, str, dict]] = []
        playwright_proxies: list[str | None] = []
        validations: list[tuple[str, dict]] = []
        scrape_calls: list[str] = []

        class Session:
            def __init__(self):
                self.trust_env = True

            def request(self, method, url, **kwargs):
                calls.append((method, url, dict(kwargs)))
                return SimpleNamespace()

        sessions_module.Session = Session
        requests_module.sessions = sessions_module
        if head_response is None:
            head_response = SimpleNamespace(
                status_code=200,
                headers={"content-type": "application/pdf"},
                url="http://doc-drop-web:8091/example.pdf",
            )
        requests_module.head = lambda *args, **kwargs: head_response

        connector_module = ModuleType("onyx.connectors.web.connector")

        class WebConnector:
            def __init__(self, base_url: str, web_connector_type: str = "recursive"):
                self.to_visit_list = [base_url]
                if web_connector_type == "sitemap":
                    connector_module.protected_url_check(base_url)
                    Session().request("GET", base_url, timeout=30)

            def load_from_state(self, slim=False):
                del slim
                Session().request("HEAD", self.to_visit_list[0], timeout=30)
                Session().request(
                    "GET", "https://subresource.example/asset.js", timeout=30
                )
                yield "loaded"

            def _do_scrape(self, index, initial_url, session_ctx, slim=False):
                del index, session_ctx, slim
                if False:  # Pinned source-contract markers; never executed here.
                    head_response = requests.head(  # noqa: F821
                        initial_url,
                        headers=DEFAULT_HEADERS,  # noqa: F821
                        allow_redirects=True,
                        timeout=30,
                    )
                    content_type = head_response.headers.get("content-type")
                    is_pdf = is_pdf_resource(initial_url, content_type)  # noqa: F821
                    response = requests.get(initial_url)  # noqa: F821
                    extract_pdf_text(response.content)  # noqa: F821
                    result = ScrapeResult()  # noqa: F821
                    result.doc = Document(  # noqa: F821
                        id=initial_url,
                        sections=[],
                        source="web",
                        semantic_identifier=initial_url,
                        metadata={},
                    )
                    return result, is_pdf
                scrape_calls.append(initial_url)
                result = connector_module.ScrapeResult()
                result.doc = ConnectorDocument(
                    id=initial_url,
                    sections=[],
                    source="web",
                    semantic_identifier=initial_url,
                    metadata={},
                )
                return result

        class ScrapeResult:
            doc = None
            retry = False

            def __init__(self):
                self.doc = None
                self.retry = False

        class ConnectorDocument:
            model_fields = {
                name: object()
                for name in (
                    "id",
                    "sections",
                    "source",
                    "semantic_identifier",
                    "metadata",
                    "doc_metadata",
                    "doc_updated_at",
                )
            }

            def __init__(self, **kwargs):
                self.id = kwargs.get("id")
                self.sections = kwargs.get("sections", [])
                self.source = kwargs.get("source")
                self.semantic_identifier = kwargs.get("semantic_identifier")
                self.metadata = kwargs.get("metadata", {})
                self.doc_metadata = kwargs.get("doc_metadata")
                self.doc_updated_at = kwargs.get("doc_updated_at")

            def content_hash(self):
                return "new-content-hash"

        class DatabaseDocument:
            id = None
            doc_updated_at = None
            doc_metadata = None
            content_hash = None

        def get_docs_to_update(
            documents,
            db_docs,
            ignore_timestamp_gate=False,
            ignore_content_hash_gate=False,
        ):
            id_update_time_map = {doc.id: doc for doc in db_docs}
            if ignore_timestamp_gate or ignore_content_hash_gate:
                id_update_time_map.clear()
            for doc in documents:
                doc.content_hash()
            for db_doc in db_docs:
                _ = db_doc.content_hash
            return documents

        connector_module.WebConnector = WebConnector
        connector_module.ScrapeResult = ScrapeResult
        connector_module.protected_url_check = lambda url: None

        models_module = ModuleType("onyx.server.security.models")

        class SSRFProtectionLevel:
            VALIDATE_ALL = "validate_all"

        models_module.SSRFProtectionLevel = SSRFProtectionLevel
        models_module.web_connector_ssrf_enforced = (
            lambda current: current == SSRFProtectionLevel.VALIDATE_ALL
        )

        store_module = ModuleType("onyx.server.security.store")
        store_module.get_security_settings = lambda: SimpleNamespace(
            ssrf_protection_level=level
        )

        url_module = ModuleType("onyx.utils.url")

        def validate_outbound_http_url(url, **kwargs):
            validations.append((url, dict(kwargs)))
            return url

        url_module.validate_outbound_http_url = validate_outbound_http_url

        app_configs_module = ModuleType("onyx.configs.app_configs")
        app_configs_module.REQUEST_TIMEOUT_SECONDS = 30
        app_configs_module.DISABLE_TELEMETRY = True
        constants_module = ModuleType("onyx.configs.constants")
        constants_module.DocumentSource = SimpleNamespace(WEB="web")
        connector_models_module = ModuleType("onyx.connectors.models")
        connector_models_module.Document = ConnectorDocument
        db_models_module = ModuleType("onyx.db.models")
        db_models_module.Document = DatabaseDocument
        indexing_pipeline_module = ModuleType("onyx.indexing.indexing_pipeline")
        indexing_pipeline_module.get_docs_to_update = get_docs_to_update
        logger_module = ModuleType("onyx.utils.logger")
        logger_module.setup_logger = lambda *args, **kwargs: SimpleNamespace(
            debug=lambda *args, **kwargs: None,
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        )
        playwright_fetch_module = ModuleType("onyx.utils.playwright_fetch")
        playwright_fetch_module.DEFAULT_HEADERS = {"user-agent": "test"}
        web_content_module = ModuleType("onyx.utils.web_content")
        web_content_module.is_pdf_resource = lambda url, content_type: (
            str(url).endswith(".pdf") or content_type == "application/pdf"
        )

        wrapper_module = ModuleType("wrapper_env_patches")
        wrapper_module.apply_embedding_tokenizer_alias_patch = lambda: None
        wrapper_module.apply_playwright_helper_proxy_patch = lambda: None
        wrapper_module.apply_configured_inference_proxy_patch = lambda: None
        wrapper_module._validated_fixed_proxy_url = (
            lambda env_name, expected_host: os.environ[env_name]
        )

        @contextmanager
        def select_playwright_proxy(proxy_url):
            playwright_proxies.append(proxy_url)
            yield

        wrapper_module.select_playwright_proxy = select_playwright_proxy

        onyx_module = ModuleType("onyx")
        background_module = ModuleType("onyx.background")
        celery_module = ModuleType("onyx.background.celery")
        apps_module = ModuleType("onyx.background.celery.apps")
        app_base_module = ModuleType("onyx.background.celery.apps.app_base")
        beat_app_module = ModuleType("onyx.background.celery.apps.beat")
        liveness_probe = type("LivenessProbe", (), {})
        app_base_module.LivenessProbe = liveness_probe
        app_base_module.get_bootsteps = lambda: [liveness_probe]
        beat_app_module.DynamicTenantScheduler = type(
            "DynamicTenantScheduler",
            (),
            {"RELOAD_INTERVAL": 60, "tick": _compatible_beat_tick},
        )
        beat_app_module.task_logger = SimpleNamespace(
            debug=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        )
        apps_module.app_base = app_base_module
        apps_module.beat = beat_app_module
        tasks_module = ModuleType("onyx.background.celery.tasks")
        beat_schedule_module = ModuleType(
            "onyx.background.celery.tasks.beat_schedule"
        )
        task_ids = SimpleNamespace(
            MONITOR_CELERY_QUEUES="monitor-celery-queues-task",
            MONITOR_BACKGROUND_PROCESSES="monitor-background-processes-task",
            MONITOR_PROCESS_MEMORY="monitor-process-memory-task",
            CELERY_BEAT_HEARTBEAT="celery-beat-heartbeat-task",
            EMIT_VERSION_TELEMETRY="emit-version-telemetry-task",
            CLEANUP_IDLE_SANDBOXES="cleanup-idle-sandboxes-task",
            SCHEDULED_TASKS_DISPATCH_DUE="dispatch-due-scheduled-tasks-task",
            SCHEDULED_TASKS_CLEANUP_STUCK="cleanup-stuck-scheduled-runs-task",
        )
        queue_ids = SimpleNamespace(MONITORING="monitoring")
        constants_module.OnyxCeleryTask = task_ids
        constants_module.OnyxCeleryQueues = queue_ids
        schedule_specs = {
            "check-for-user-file-processing": timedelta(seconds=20),
            "check-for-user-file-project-sync": timedelta(seconds=20),
            "check-for-user-file-delete": timedelta(seconds=20),
            "check-for-indexing": timedelta(seconds=15),
            "check-for-port": timedelta(seconds=30),
            "check-for-connector-deletion": timedelta(seconds=20),
            "check-for-vespa-sync": timedelta(seconds=20),
            "check-for-pruning": timedelta(seconds=20),
            "check-for-incognito-file-cleanup": timedelta(minutes=10),
            "check-for-checkpoint-cleanup": timedelta(hours=1),
            "check-for-index-attempt-cleanup": timedelta(minutes=30),
            "check-for-hierarchy-fetching": timedelta(hours=1),
        }
        removal_specs = {
            "monitor-celery-queues": (task_ids.MONITOR_CELERY_QUEUES, timedelta(seconds=10)),
            "monitor-background-processes": (task_ids.MONITOR_BACKGROUND_PROCESSES, timedelta(minutes=5)),
            "monitor-process-memory": (task_ids.MONITOR_PROCESS_MEMORY, timedelta(minutes=5)),
            "celery-beat-heartbeat": (task_ids.CELERY_BEAT_HEARTBEAT, timedelta(minutes=1)),
            "emit-version-telemetry": (task_ids.EMIT_VERSION_TELEMETRY, timedelta(hours=1)),
            "cleanup-idle-sandboxes": (task_ids.CLEANUP_IDLE_SANDBOXES, timedelta(minutes=1)),
            "dispatch-due-scheduled-tasks": (task_ids.SCHEDULED_TASKS_DISPATCH_DUE, timedelta(seconds=30)),
            "cleanup-stuck-scheduled-runs": (task_ids.SCHEDULED_TASKS_CLEANUP_STUCK, timedelta(hours=1)),
        }
        beat_schedule_module.beat_task_templates = [
            {"name": name, "task": name + "-task", "schedule": cadence, "options": {}}
            for name, cadence in schedule_specs.items()
        ] + [
            {"name": name, "task": task_id, "schedule": cadence, "options": {"queue": queue_ids.MONITORING} if name.startswith("monitor-") or name == "emit-version-telemetry" else {}}
            for name, (task_id, cadence) in removal_specs.items()
            if name not in {"monitor-celery-queues", "monitor-process-memory", "celery-beat-heartbeat"}
        ]
        beat_schedule_module.tasks_to_schedule = [
            {"name": task["name"], "task": task["task"], "schedule": task["schedule"], "options": dict(task["options"])}
            for task in beat_schedule_module.beat_task_templates
        ] + [
            {"name": name, "task": task_id, "schedule": cadence, "options": {"queue": queue_ids.MONITORING} if name.startswith("monitor-") or name == "emit-version-telemetry" else {}}
            for name, (task_id, cadence) in removal_specs.items()
            if name in {"monitor-celery-queues", "monitor-process-memory", "celery-beat-heartbeat"}
        ]
        beat_schedule_module.get_tasks_to_schedule = lambda: beat_schedule_module.tasks_to_schedule
        tasks_module.beat_schedule = beat_schedule_module
        celery_module.apps = apps_module
        celery_module.tasks = tasks_module
        background_module.celery = celery_module
        connectors_module = ModuleType("onyx.connectors")
        web_module = ModuleType("onyx.connectors.web")
        server_module = ModuleType("onyx.server")
        security_module = ModuleType("onyx.server.security")
        utils_module = ModuleType("onyx.utils")
        configs_module = ModuleType("onyx.configs")
        db_module = ModuleType("onyx.db")
        indexing_module = ModuleType("onyx.indexing")
        shared_configs_module = ModuleType("shared_configs")
        shared_configs_configs_module = ModuleType("shared_configs.configs")
        shared_configs_configs_module.MULTI_TENANT = False
        shared_configs_module.configs = shared_configs_configs_module
        web_module.connector = connector_module
        connectors_module.web = web_module
        security_module.models = models_module
        security_module.store = store_module
        server_module.security = security_module
        utils_module.url = url_module
        onyx_module.connectors = connectors_module
        onyx_module.background = background_module
        onyx_module.server = server_module
        onyx_module.utils = utils_module
        onyx_module.configs = configs_module
        onyx_module.db = db_module
        onyx_module.indexing = indexing_module

        fake_modules = {
            "requests": requests_module,
            "requests.sessions": sessions_module,
            "wrapper_env_patches": wrapper_module,
            "onyx": onyx_module,
            "onyx.background": background_module,
            "onyx.background.celery": celery_module,
            "onyx.background.celery.apps": apps_module,
            "onyx.background.celery.apps.app_base": app_base_module,
            "onyx.background.celery.apps.beat": beat_app_module,
            "onyx.background.celery.tasks": tasks_module,
            "onyx.background.celery.tasks.beat_schedule": beat_schedule_module,
            "onyx.connectors": connectors_module,
            "onyx.connectors.web": web_module,
            "onyx.connectors.web.connector": connector_module,
            "onyx.connectors.models": connector_models_module,
            "onyx.configs": configs_module,
            "onyx.configs.app_configs": app_configs_module,
            "onyx.configs.constants": constants_module,
            "onyx.db": db_module,
            "onyx.db.models": db_models_module,
            "onyx.indexing": indexing_module,
            "onyx.indexing.indexing_pipeline": indexing_pipeline_module,
            "onyx.server": server_module,
            "onyx.server.security": security_module,
            "onyx.server.security.models": models_module,
            "onyx.server.security.store": store_module,
            "onyx.utils": utils_module,
            "onyx.utils.url": url_module,
            "onyx.utils.logger": logger_module,
            "onyx.utils.playwright_fetch": playwright_fetch_module,
            "onyx.utils.web_content": web_content_module,
            "shared_configs": shared_configs_module,
            "shared_configs.configs": shared_configs_configs_module,
        }
        env = {
            "WRAPPER_PATCH_STRICT": "true",
            "ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_ENABLED": str(freshness).lower(),
            "ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_HOSTS": "doc-drop-web",
            "ONYX_WEB_CONNECTOR_PUBLIC_HTTP_PROXY_URL": (
                "http://onyx-public-egress-bridge:3128"
            ),
            "ONYX_WEB_CONNECTOR_HOST_HTTP_PROXY_URL": (
                "http://onyx-host-egress-bridge:3128"
            ),
            "ONYX_WEB_CONNECTOR_INTERNAL_BASE_URL": "http://doc-drop-web:8091/",
        }

        spec = importlib.util.spec_from_file_location(
            "sitecustomize_background_egress_under_test", MODULE_PATH
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, env, clear=True), patch.dict(
            sys.modules, fake_modules
        ):
            spec.loader.exec_module(module)

        self.loaded_patch_module = module
        self.scrape_calls = scrape_calls
        self.beat_schedule_module = beat_schedule_module
        self.beat_app_module = beat_app_module
        self.app_base_module = app_base_module
        return connector_module, calls, playwright_proxies, validations

    def test_sleepy_background_schedule_and_bootsteps_are_effective(self) -> None:
        self._load_patched_modules("validate_all")
        schedule = self.beat_schedule_module.get_tasks_to_schedule()
        by_name = {task["name"]: task for task in schedule}
        discovery = {
            "check-for-user-file-processing",
            "check-for-user-file-project-sync",
            "check-for-user-file-delete",
            "check-for-indexing",
            "check-for-port",
            "check-for-connector-deletion",
            "check-for-vespa-sync",
            "check-for-pruning",
        }
        for name in discovery:
            self.assertEqual(by_name[name]["schedule"], timedelta(minutes=5))
        self.assertEqual(by_name["check-for-checkpoint-cleanup"]["schedule"], timedelta(hours=1))
        self.assertEqual(by_name["check-for-incognito-file-cleanup"]["schedule"], timedelta(minutes=10))
        self.assertEqual(by_name["check-for-index-attempt-cleanup"]["schedule"], timedelta(minutes=30))
        self.assertEqual(by_name["check-for-hierarchy-fetching"]["schedule"], timedelta(hours=1))
        for removed in (
            "monitor-celery-queues",
            "emit-version-telemetry",
            "monitor-background-processes",
            "monitor-process-memory",
            "celery-beat-heartbeat",
            "cleanup-idle-sandboxes",
            "dispatch-due-scheduled-tasks",
            "cleanup-stuck-scheduled-runs",
        ):
            self.assertNotIn(removed, by_name)
        self.assertEqual(
            self.beat_app_module.DynamicTenantScheduler.RELOAD_INTERVAL, 300
        )
        self.assertIs(
            self.beat_app_module.DynamicTenantScheduler.tick,
            _compatible_beat_tick,
        )
        self.assertEqual(self.app_base_module.get_bootsteps(), [])

    def test_sitemap_constructor_uses_saved_level_public_proxy(self) -> None:
        connector, calls, _, validations = self._load_patched_modules("validate_all")
        connector.WebConnector(
            "https://docs.example/sitemap.xml", web_connector_type="sitemap"
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0][2]["proxies"],
            {
                "http": "http://onyx-public-egress-bridge:3128",
                "https": "http://onyx-public-egress-bridge:3128",
            },
        )
        self.assertEqual(
            validations,
            [
                (
                    "https://docs.example/sitemap.xml",
                    {
                        "allow_private_network": False,
                        "block_loopback_and_link_local": True,
                        "resolve_dns": False,
                    },
                )
            ],
        )

    def test_doc_drop_and_crawl_subresources_use_host_proxy(self) -> None:
        connector, calls, playwright_proxies, validations = self._load_patched_modules(
            "validate_all"
        )
        instance = connector.WebConnector(
            "http://doc-drop-web:8091/", web_connector_type="sitemap"
        )
        self.assertEqual(list(instance.load_from_state()), ["loaded"])

        expected = {
            "http": "http://onyx-host-egress-bridge:3128",
            "https": "http://onyx-host-egress-bridge:3128",
        }
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call[2]["proxies"] == expected for call in calls))
        self.assertEqual(
            playwright_proxies, ["http://onyx-host-egress-bridge:3128"]
        )
        self.assertEqual(validations, [])

    def test_private_enabled_connector_uses_host_proxy(self) -> None:
        for level in ("validate_llm", "allow_private", "disabled"):
            with self.subTest(level=level):
                connector, calls, playwright_proxies, _ = self._load_patched_modules(
                    level
                )
                instance = connector.WebConnector("http://nas.home/docs")
                self.assertEqual(list(instance.load_from_state()), ["loaded"])

                expected = {
                    "http": "http://onyx-host-egress-bridge:3128",
                    "https": "http://onyx-host-egress-bridge:3128",
                }
                self.assertTrue(all(call[2]["proxies"] == expected for call in calls))
                self.assertEqual(
                    playwright_proxies, ["http://onyx-host-egress-bridge:3128"]
                )

    def test_pdf_freshness_sentinels_skip_only_when_safe(self) -> None:
        self._load_patched_modules("validate_all")
        module = self.loaded_patch_module
        updated_at = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
        last_modified = "Sun, 19 Jul 2026 12:00:00 GMT"
        db_doc = SimpleNamespace(
            id="unchanged",
            doc_updated_at=updated_at,
            doc_metadata=module._freshness_metadata(
                {},
                last_modified_raw=last_modified,
                content_length="123",
            ),
        )
        unchanged = SimpleNamespace(
            id="unchanged",
            doc_updated_at=updated_at,
            doc_metadata=module._unchanged_freshness_metadata(
                last_modified_raw=last_modified,
                content_length="123",
            ),
        )
        unreadable = SimpleNamespace(
            id="unreadable",
            doc_updated_at=None,
            doc_metadata=module._unreadable_freshness_metadata(
                status_code=404,
                last_modified_raw=None,
                content_length=None,
            ),
        )
        stale = SimpleNamespace(
            id="stale",
            doc_updated_at=updated_at,
            doc_metadata=module._unchanged_freshness_metadata(
                last_modified_raw=last_modified,
                content_length="999",
            ),
        )
        ordinary = SimpleNamespace(id="ordinary", doc_metadata={})

        passthrough, skipped = module._filter_freshness_sentinels(
            [unchanged, unreadable, ordinary], [db_doc]
        )

        self.assertEqual(skipped, 2)
        self.assertEqual([doc.id for doc in passthrough], ["ordinary"])
        with self.assertRaisesRegex(RuntimeError, "refusing to index"):
            module._filter_freshness_sentinels([stale], [db_doc])

    def test_pdf_freshness_validates_indexing_source_and_signature(self) -> None:
        self._load_patched_modules("validate_all")
        module = self.loaded_patch_module
        module._validate_callable_contract(
            _compatible_get_docs_to_update,
            name="get_docs_to_update",
            expected_parameters=module._INDEXING_FRESHNESS_PARAMETERS,
            source_markers=module._INDEXING_FRESHNESS_SOURCE_MARKERS,
        )

        with self.assertRaisesRegex(RuntimeError, "signature changed"):
            module._validate_callable_contract(
                _drifted_get_docs_to_update,
                name="get_docs_to_update",
                expected_parameters=module._INDEXING_FRESHNESS_PARAMETERS,
                source_markers=module._INDEXING_FRESHNESS_SOURCE_MARKERS,
            )

    def test_pdf_freshness_validates_document_models(self) -> None:
        self._load_patched_modules("validate_all")
        module = self.loaded_patch_module

        class ConnectorDocument:
            model_fields = {
                name: object()
                for name in (
                    "id",
                    "sections",
                    "source",
                    "semantic_identifier",
                    "metadata",
                    "doc_metadata",
                    "doc_updated_at",
                )
            }

        class DatabaseDocument:
            id = None
            doc_updated_at = None
            doc_metadata = None
            content_hash = None

        class ScrapeResult:
            doc = None
            retry = False

        module._validate_freshness_model_contracts(
            ConnectorDocument, DatabaseDocument, ScrapeResult
        )
        del ConnectorDocument.model_fields["doc_metadata"]
        with self.assertRaisesRegex(RuntimeError, "missing fields"):
            module._validate_freshness_model_contracts(
                ConnectorDocument, DatabaseDocument, ScrapeResult
            )

    def test_installed_pdf_freshness_handles_head_decisions(self) -> None:
        host_patch = patch.dict(
            os.environ,
            {"ONYX_WEB_CONNECTOR_HTTP_FRESHNESS_HOSTS": "doc-drop-web"},
        )
        host_patch.start()
        self.addCleanup(host_patch.stop)
        last_modified = "Sun, 19 Jul 2026 12:00:00 GMT"
        updated_at = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
        good_head = SimpleNamespace(
            status_code=200,
            headers={
                "content-type": "application/pdf",
                "last-modified": last_modified,
                "content-length": "123",
            },
            url="http://doc-drop-web:8091/example.pdf",
        )
        connector, *_ = self._load_patched_modules(
            "validate_all", freshness=True, head_response=good_head
        )
        module = self.loaded_patch_module
        matching_db_doc = SimpleNamespace(
            id="http://doc-drop-web:8091/example.pdf",
            doc_updated_at=updated_at,
            doc_metadata=module._freshness_metadata(
                {},
                last_modified_raw=last_modified,
                content_length="123",
            ),
            content_hash="old-content-hash",
            chunk_count=1,
        )
        module._get_db_document = lambda document_id: matching_db_doc
        session = SimpleNamespace(last_error=None)

        result = connector.WebConnector(
            "http://doc-drop-web:8091/example.pdf"
        )._do_scrape(0, "http://doc-drop-web:8091/example.pdf", session)
        self.assertEqual(self.scrape_calls, [])
        self.assertEqual(
            result.doc.doc_metadata[module.FRESHNESS_UNCHANGED_KEY],
            module.FRESHNESS_VERSION,
        )

        module._get_db_document = lambda document_id: SimpleNamespace(
            **{
                **vars(matching_db_doc),
                "doc_metadata": module._freshness_metadata(
                    {},
                    last_modified_raw=last_modified,
                    content_length="999",
                ),
            }
        )
        result = connector.WebConnector(
            "http://doc-drop-web:8091/example.pdf"
        )._do_scrape(0, "http://doc-drop-web:8091/example.pdf", session)
        self.assertEqual(self.scrape_calls, ["http://doc-drop-web:8091/example.pdf"])
        self.assertEqual(result.doc.doc_updated_at, updated_at)

        missing_head = SimpleNamespace(
            status_code=200,
            headers={"content-type": "application/pdf"},
            url="http://doc-drop-web:8091/missing.pdf",
        )
        connector, *_ = self._load_patched_modules(
            "validate_all", freshness=True, head_response=missing_head
        )
        connector.WebConnector(
            "http://doc-drop-web:8091/missing.pdf"
        )._do_scrape(0, "http://doc-drop-web:8091/missing.pdf", session)
        self.assertEqual(self.scrape_calls, ["http://doc-drop-web:8091/missing.pdf"])

        terminal_head = SimpleNamespace(
            status_code=404,
            headers={"content-type": "application/pdf"},
            url="http://doc-drop-web:8091/gone.pdf",
        )
        connector, *_ = self._load_patched_modules(
            "validate_all", freshness=True, head_response=terminal_head
        )
        result = connector.WebConnector(
            "http://doc-drop-web:8091/gone.pdf"
        )._do_scrape(0, "http://doc-drop-web:8091/gone.pdf", session)
        self.assertEqual(self.scrape_calls, [])
        self.assertEqual(
            result.doc.doc_metadata[self.loaded_patch_module.FRESHNESS_UNREADABLE_KEY],
            self.loaded_patch_module.FRESHNESS_VERSION,
        )

        connector, *_ = self._load_patched_modules(
            "validate_all", freshness=True, head_response=good_head
        )
        connector.WebConnector("https://public.example/example.pdf")._do_scrape(
            0, "https://public.example/example.pdf", session
        )
        self.assertEqual(self.scrape_calls, ["https://public.example/example.pdf"])


if __name__ == "__main__":
    unittest.main()
