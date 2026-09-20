"""Expose OpenAI-compatible model IDs in labels without changing inference IDs."""

from __future__ import annotations

import functools
import inspect


def _display_name(label, model_id):
    # ID-only providers need no repeated label; preserve malformed metadata for
    # upstream validation rather than substituting a successful-looking value.
    if not isinstance(model_id, str) or not model_id:
        return label
    if not isinstance(label, str) or not label or label == model_id:
        return label
    suffix = f" ({model_id})"
    return label if label.endswith(suffix) else label + suffix


def _validate_target(function, parameters, markers):
    if tuple(inspect.signature(function).parameters) != parameters:
        raise RuntimeError("Model display-name patch signature changed")
    source = inspect.getsource(function)
    for marker in markers:
        if marker not in source:
            raise RuntimeError("Model display-name patch source changed: " + marker)


def install() -> None:
    from onyx.llm.constants import LlmProviderNames
    from onyx.server.manage.llm import api, models

    original_response = api._get_openai_compatible_server_response
    if getattr(original_response, "_wrapper_model_display_names", False):
        return
    original_view = models.ModelConfigurationView.from_model.__func__
    _validate_target(original_response, ("api_base", "api_key"), (
        "return _get_openai_compatible_models_response(",
        'source_name="OpenAI-Compatible",',
    ))
    _validate_target(api.get_openai_compatible_server_available_models,
                     ("request", "_", "db_session"), (
        "response_json = _get_openai_compatible_server_response(",
        'model_id = model.get("id", "")',
        'model_name = model.get("name", model_id)',
        "name=model_id,", "display_name=model_name,",
        "display_name=r.display_name,",
    ))
    _validate_target(original_view, (
        "cls", "model_configuration_model", "provider_name",
        "use_stored_display_name", "custom_config", "deployment_name",
    ), (
        "name=model_configuration_model.name,",
        "display_name=model_configuration_model.display_name,",
        "custom_display_name=model_configuration_model.custom_display_name,",
    ))

    @functools.wraps(original_response)
    def response(api_base, api_key=None):
        result = original_response(api_base, api_key)
        data = result.get("data")
        if not isinstance(data, list):
            return result
        return {**result, "data": [
            {**entry, "name": _display_name(entry["name"], entry.get("id"))}
            if isinstance(entry, dict) and "name" in entry else entry
            for entry in data
        ]}

    @functools.wraps(original_view)
    def view(cls, model_configuration_model, provider_name,
             use_stored_display_name=False, custom_config=None, deployment_name=None):
        result = original_view(
            cls, model_configuration_model, provider_name,
            use_stored_display_name, custom_config, deployment_name,
        )
        if provider_name == LlmProviderNames.OPENAI_COMPATIBLE:
            result.display_name = _display_name(result.display_name, result.name)
        return result

    response._wrapper_model_display_names = True
    api._get_openai_compatible_server_response = response
    models.ModelConfigurationView.from_model = classmethod(view)
    print("sitecustomize_api_server: included API IDs in OpenAI-compatible model labels")
