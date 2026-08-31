"""Restore Community Edition login after the pinned v4.6.5 branding regression."""

from __future__ import annotations

import inspect
from typing import Any


REGRESSION_INTRODUCING_COMMIT = (
    "a1a60b5cf07969dc4b3cb2b23be0d5d378bf042e"
)
_ROUTE_PATH = "/enterprise-settings"
_ROUTE_METHODS = {"GET"}
_PATCH_MARKER = "_wrapper_enterprise_settings_compat_patch"


def neutral_enterprise_settings() -> dict[str, Any]:
    """Return the non-sensitive CE branding defaults expected by the WebUI."""
    return {
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
    }


def _fetch_neutral_enterprise_settings() -> dict[str, Any]:
    return neutral_enterprise_settings()


def _route_paths(router: Any) -> list[tuple[str, set[str]]]:
    return [
        (route.path, set(route.methods or set()))
        for route in router.routes
        if hasattr(route, "path") and hasattr(route, "methods")
    ]


def _validate_application_shape(main_module: Any, state_router: Any) -> None:
    if main_module.state_router is not state_router:
        raise RuntimeError("Onyx state router identity drifted")
    if main_module.app is not main_module.get_application:
        raise RuntimeError("Onyx CE application factory selection drifted")

    source = inspect.getsource(main_module.get_application)
    state_marker = (
        "include_router_with_global_prefix_prepended(application, state_router)"
    )
    auth_marker = "check_router_auth(application)"
    if source.count(state_marker) != 1 or source.count(auth_marker) != 1:
        raise RuntimeError("Onyx application route/audit source drifted")
    if source.index(state_marker) >= source.index(auth_marker):
        raise RuntimeError("Onyx state router is no longer audited after inclusion")

    seen_routers: set[int] = set()
    for candidate in vars(main_module).values():
        routes = getattr(candidate, "routes", None)
        if not isinstance(routes, list) or id(candidate) in seen_routers:
            continue
        seen_routers.add(id(candidate))
        if any(path == _ROUTE_PATH for path, _methods in _route_paths(candidate)):
            raise RuntimeError(
                "Onyx now supplies /enterprise-settings; audit and remove the "
                "v4.6.5 compatibility patch"
            )


def install() -> None:
    from onyx.configs.app_configs import ENTERPRISE_EDITION_ENABLED
    import onyx.main as main_module
    from onyx.server.auth_check import PUBLIC_ENDPOINT_SPECS
    from onyx.server.auth_check import check_router_auth
    from onyx.server.manage.get_state import router as state_router
    from onyx.utils.variable_functionality import _LICENSE_ENFORCEMENT_ENABLED
    from onyx.utils.variable_functionality import global_version

    if getattr(state_router, _PATCH_MARKER, False):
        return

    if (
        ENTERPRISE_EDITION_ENABLED
        or _LICENSE_ENFORCEMENT_ENABLED
        or global_version.is_ee_version()
    ):
        raise RuntimeError(
            "enterprise-settings CE compatibility patch must not run with EE enabled"
        )

    default_specs = check_router_auth.__defaults__
    if not default_specs or default_specs[0] is not PUBLIC_ENDPOINT_SPECS:
        raise RuntimeError("Onyx public-route audit default binding drifted")
    if any(
        path == _ROUTE_PATH
        for path, _methods in PUBLIC_ENDPOINT_SPECS
    ):
        raise RuntimeError("Onyx public enterprise-settings route spec already exists")

    _validate_application_shape(main_module, state_router)

    state_router.add_api_route(
        _ROUTE_PATH,
        _fetch_neutral_enterprise_settings,
        methods=sorted(_ROUTE_METHODS),
        response_model=dict[str, Any],
        name="wrapper_ce_enterprise_settings_compat",
    )
    PUBLIC_ENDPOINT_SPECS.append((_ROUTE_PATH, set(_ROUTE_METHODS)))

    installed = [
        methods
        for path, methods in _route_paths(state_router)
        if path == _ROUTE_PATH
    ]
    if installed != [_ROUTE_METHODS]:
        raise RuntimeError("failed to install exact CE enterprise-settings route")
    if PUBLIC_ENDPOINT_SPECS[-1] != (_ROUTE_PATH, _ROUTE_METHODS):
        raise RuntimeError("failed to register exact public route audit spec")

    setattr(state_router, _PATCH_MARKER, True)
    print(
        "sitecustomize_api_server: installed v4.6.5 CE login settings "
        "compatibility endpoint",
        flush=True,
    )
