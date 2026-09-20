"""Expose final per-URL failures when an open_url batch partly succeeds."""

from __future__ import annotations

import contextvars
import inspect
from dataclasses import dataclass, field
import threading


@dataclass
class _FailureState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    failures: list[object] = field(default_factory=list)

    def record(self, failures) -> None:
        with self.lock:
            self.failures = list(failures)

    def snapshot(self) -> list[object]:
        with self.lock:
            return list(self.failures)


_STATE: contextvars.ContextVar[_FailureState | None] = contextvars.ContextVar(
    "wrapper_open_url_failure_state", default=None
)


def _append_partial_failure_report(response, failures, build_failure_message):
    if not failures or response.rich_response is None:
        return response
    failure_report = build_failure_message(
        missing_document_ids=[], failed_web_fetches=failures
    )
    response.llm_facing_response = (
        response.llm_facing_response
        + "\n\nPartial open_url failure report: "
        + failure_report
    )
    return response


def record_failures(failures) -> None:
    """Record final post-fallback failures for a specialized merge patch."""
    state = _STATE.get()
    if state is not None:
        state.record(failures)


def install_failure_reporting() -> None:
    from onyx.tools.tool_implementations.open_url import open_url_tool

    if getattr(open_url_tool.OpenURLTool, "_wrapper_failure_reporting_patch", False):
        return

    run_source = inspect.getsource(open_url_tool.OpenURLTool.run)
    merge_source = inspect.getsource(
        open_url_tool.OpenURLTool._merge_indexed_and_crawled_results
    )
    if (
        "failed_web_fetches = self._fallback_link_lookup(" not in run_source
        or "self._merge_indexed_and_crawled_results(" not in run_source
        or "failed_web_fetches" not in merge_source
    ):
        raise RuntimeError("OpenURL mixed-result failure-reporting source drift")

    original_run = open_url_tool.OpenURLTool.run
    original_merge = open_url_tool.OpenURLTool._merge_indexed_and_crawled_results

    def _run(self, *args, **kwargs):
        state = _FailureState()
        token = _STATE.set(state)
        try:
            response = original_run(self, *args, **kwargs)
            return _append_partial_failure_report(
                response,
                state.snapshot(),
                open_url_tool._build_failure_message,
            )
        finally:
            _STATE.reset(token)

    def _merge_results(
        self,
        indexed_sections,
        crawled_sections,
        url_to_doc_id,
        all_urls,
        failed_web_fetches,
    ):
        record_failures(failed_web_fetches)
        return original_merge(
            self,
            indexed_sections,
            crawled_sections,
            url_to_doc_id,
            all_urls,
            failed_web_fetches,
        )

    open_url_tool.OpenURLTool.run = _run
    open_url_tool.OpenURLTool._merge_indexed_and_crawled_results = _merge_results
    open_url_tool.OpenURLTool._wrapper_failure_reporting_original_run = original_run
    open_url_tool.OpenURLTool._wrapper_failure_reporting_patch = True
    print(
        "sitecustomize_api_server: installed mixed-result open_url failure reporting",
        flush=True,
    )


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


def install_url_limit() -> None:
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
