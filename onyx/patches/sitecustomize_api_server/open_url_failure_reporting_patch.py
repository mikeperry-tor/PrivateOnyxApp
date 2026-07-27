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


def install() -> None:
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
