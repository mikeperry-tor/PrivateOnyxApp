"""Make Onyx's ten-URL open_url limit explicit and fail visibly."""

from __future__ import annotations

import inspect


MAX_URLS_PER_CALL = 10
LIMIT_GUIDANCE = (
    "Accepts at most 10 URLs per call. Split larger sets across additional "
    "open_url calls."
)


def _reject_over_limit(urls: list[str], max_urls: int, exception_type):
    if len(urls) <= max_urls:
        return
    raise exception_type(
        message=(
            f"OpenURL tool received {len(urls)} URLs, but the maximum is "
            f"{max_urls}."
        ),
        llm_facing_message=(
            f"The open_url tool accepts at most {max_urls} URLs per call. "
            "Split the request across additional open_url calls. No URLs from "
            "this call were opened."
        ),
    )


def install() -> None:
    from onyx.tools import models as tool_models
    from onyx.tools.tool_implementations.open_url import open_url_tool

    tool_class = open_url_tool.OpenURLTool
    if getattr(tool_class, "_wrapper_explicit_url_limit_patch", False):
        return

    source_run = getattr(
        tool_class,
        "_wrapper_failure_reporting_original_run",
        tool_class.run,
    )
    run_source = inspect.getsource(source_run)
    definition_source = inspect.getsource(tool_class.tool_definition)
    max_urls_field = tool_models.OpenURLToolOverrideKwargs.model_fields["max_urls"]
    if (
        "if len(urls) > override_kwargs.max_urls:" not in run_source
        or "urls = urls[: override_kwargs.max_urls]" not in run_source
        or '"type": "array"' not in definition_source
        or "URLS_FIELD" not in definition_source
        or max_urls_field.default != MAX_URLS_PER_CALL
        or tool_class.DESCRIPTION
        != "Open and read the content of one or more URLs."
    ):
        raise RuntimeError("OpenURL ten-URL limit source drift")

    original_run = tool_class.run
    original_definition = tool_class.tool_definition

    def _run(self, placement, override_kwargs, **llm_kwargs):
        urls = open_url_tool._normalize_string_list(
            llm_kwargs.get(open_url_tool.URLS_FIELD)
        )
        _reject_over_limit(
            urls,
            override_kwargs.max_urls,
            tool_models.ToolCallException,
        )
        return original_run(self, placement, override_kwargs, **llm_kwargs)

    def _tool_definition(self):
        definition = original_definition(self)
        urls_schema = definition["function"]["parameters"]["properties"][
            open_url_tool.URLS_FIELD
        ]
        urls_schema["maxItems"] = MAX_URLS_PER_CALL
        urls_schema["description"] = (
            str(urls_schema["description"]).rstrip() + " " + LIMIT_GUIDANCE
        )
        return definition

    tool_class.run = _run
    tool_class.tool_definition = _tool_definition
    tool_class.DESCRIPTION = (
        "Open and read the content of one or more URLs. " + LIMIT_GUIDANCE
    )
    tool_class._wrapper_explicit_url_limit_patch = True
    print(
        "sitecustomize_api_server: made the ten-URL open_url limit explicit",
        flush=True,
    )
