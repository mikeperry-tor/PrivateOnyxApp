from __future__ import annotations

import importlib.util
import inspect
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "onyx/patches/sitecustomize_api_server/enterprise_settings_compat_patch.py"
)
INTRODUCING_COMMIT = "a1a60b5cf07969dc4b3cb2b23be0d5d378bf042e"


def _load_patch():
    spec = importlib.util.spec_from_file_location(
        "enterprise_settings_compat_patch_under_test", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Route:
    def __init__(self, path: str, methods: set[str], endpoint=None):
        self.path = path
        self.methods = methods
        self.endpoint = endpoint


class _Router:
    def __init__(self, routes: list[_Route] | None = None):
        self.routes = list(routes or [])

    def add_api_route(self, path, endpoint, *, methods, **_kwargs):
        self.routes.append(_Route(path, set(methods), endpoint))


def _get_application_source(*, include_state_router: bool = True) -> str:
    state_line = (
        "    include_router_with_global_prefix_prepended(application, state_router)\n"
        if include_state_router
        else ""
    )
    return (
        "def get_application():\n"
        "    application = object()\n"
        f"{state_line}"
        "    check_router_auth(application)\n"
        "    return application\n"
    )


def _fake_modules(
    *,
    enterprise_enabled: bool = False,
    license_enabled: bool = False,
    existing_route: bool = False,
    include_state_router: bool = True,
):
    module_names = (
        "onyx",
        "onyx.configs",
        "onyx.configs.app_configs",
        "onyx.main",
        "onyx.server",
        "onyx.server.auth_check",
        "onyx.server.manage",
        "onyx.server.manage.get_state",
        "onyx.utils",
        "onyx.utils.variable_functionality",
    )
    modules = {name: types.ModuleType(name) for name in module_names}
    for name, module in modules.items():
        if "." not in name or name.rsplit(".", 1)[1] in {
            "configs",
            "server",
            "manage",
            "utils",
        }:
            module.__path__ = []

    app_configs = modules["onyx.configs.app_configs"]
    app_configs.ENTERPRISE_EDITION_ENABLED = enterprise_enabled

    state_router = _Router(
        [_Route("/enterprise-settings", {"GET"})] if existing_route else []
    )
    modules["onyx.server.manage.get_state"].router = state_router

    public_specs: list[tuple[str, set[str]]] = []

    def check_router_auth(_application, public_endpoint_specs=public_specs):
        return None

    auth_check = modules["onyx.server.auth_check"]
    auth_check.PUBLIC_ENDPOINT_SPECS = public_specs
    auth_check.check_router_auth = check_router_auth

    class _GlobalVersion:
        def is_ee_version(self):
            return enterprise_enabled or license_enabled

    variable_functionality = modules["onyx.utils.variable_functionality"]
    variable_functionality._LICENSE_ENFORCEMENT_ENABLED = license_enabled
    variable_functionality.global_version = _GlobalVersion()

    namespace: dict[str, object] = {
        "include_router_with_global_prefix_prepended": lambda *_args: None,
        "state_router": state_router,
        "check_router_auth": check_router_auth,
    }
    source = _get_application_source(include_state_router=include_state_router)
    exec(source, namespace)
    get_application = namespace["get_application"]
    get_application.__source__ = source

    main_module = modules["onyx.main"]
    main_module.get_application = get_application
    main_module.app = get_application
    main_module.state_router = state_router
    main_module.other_router = state_router
    main_module.class_with_routes_property = type(
        "ClassWithRoutesProperty", (), {"routes": property(lambda self: [])}
    )

    return modules, state_router, public_specs


def _install_with_fake_source(patch_module, modules):
    real_getsource = inspect.getsource

    def getsource(subject):
        source = getattr(subject, "__source__", None)
        return source if source is not None else real_getsource(subject)

    with patch.dict(sys.modules, modules, clear=False), patch.object(
        patch_module.inspect, "getsource", side_effect=getsource
    ):
        patch_module.install()


class EnterpriseSettingsCompatPatchTests(unittest.TestCase):
    def test_neutral_response_matches_v465_webui_contract(self):
        patch_module = _load_patch()
        self.assertEqual(
            patch_module.REGRESSION_INTRODUCING_COMMIT,
            INTRODUCING_COMMIT,
        )
        self.assertEqual(
            patch_module.neutral_enterprise_settings(),
            {
                "application_name": None,
                "use_custom_logo": False,
                "use_custom_logotype": False,
                "logo_display_style": None,
                "custom_nav_items": [],
                "two_lines_for_chat_header": None,
                "custom_lower_disclaimer_content": None,
                "custom_header_content": None,
                "custom_popup_header": None,
                "custom_popup_content": None,
                "enable_consent_screen": None,
                "consent_screen_prompt": None,
                "show_first_visit_notice": None,
                "custom_greeting_message": None,
                "custom_login_subtitle": None,
                "custom_help_link_url": None,
                "custom_help_link_label": None,
                "hide_onyx_branding": None,
            },
        )

    def test_installs_exact_route_and_public_audit_spec(self):
        patch_module = _load_patch()
        modules, state_router, public_specs = _fake_modules()

        _install_with_fake_source(patch_module, modules)

        matching = [r for r in state_router.routes if r.path == "/enterprise-settings"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].methods, {"GET"})
        self.assertEqual(
            matching[0].endpoint(), patch_module.neutral_enterprise_settings()
        )
        self.assertEqual(public_specs, [("/enterprise-settings", {"GET"})])

        _install_with_fake_source(patch_module, modules)
        self.assertEqual(len(state_router.routes), 1)
        self.assertEqual(len(public_specs), 1)

    def test_refuses_ee_or_license_enabled_runtime(self):
        for options in (
            {"enterprise_enabled": True},
            {"license_enabled": True},
        ):
            with self.subTest(options=options):
                patch_module = _load_patch()
                modules, _router, _specs = _fake_modules(**options)
                with self.assertRaisesRegex(RuntimeError, "must not run with EE"):
                    _install_with_fake_source(patch_module, modules)

    def test_existing_upstream_route_requires_patch_removal_audit(self):
        patch_module = _load_patch()
        modules, _router, _specs = _fake_modules(existing_route=True)
        with self.assertRaisesRegex(RuntimeError, "audit and remove"):
            _install_with_fake_source(patch_module, modules)

    def test_application_or_auth_audit_drift_fails_closed(self):
        patch_module = _load_patch()
        modules, _router, _specs = _fake_modules(include_state_router=False)
        with self.assertRaisesRegex(RuntimeError, "source drifted"):
            _install_with_fake_source(patch_module, modules)

    def test_api_bootstrap_installs_patch(self):
        bootstrap = (
            ROOT / "onyx/patches/sitecustomize_api_server/sitecustomize.py"
        ).read_text()
        self.assertIn("from enterprise_settings_compat_patch import", bootstrap)
        self.assertIn("install_enterprise_settings_compat()", bootstrap)


if __name__ == "__main__":
    unittest.main()
