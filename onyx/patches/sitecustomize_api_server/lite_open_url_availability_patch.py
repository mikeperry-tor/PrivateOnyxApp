"""Keep crawler-backed open_url available when lite mode disables the vector DB."""

from __future__ import annotations

import inspect


def _source(obj: object, label: str) -> str:
    try:
        return inspect.getsource(obj)
    except (OSError, TypeError) as exc:
        raise RuntimeError(
            f"Unable to inspect {label} for lite open_url patch"
        ) from exc


def _validate_upstream_shape(open_url_tool) -> None:
    availability_source = _source(
        open_url_tool.OpenURLTool.is_available,
        "OpenURLTool.is_available",
    )
    run_source = _source(open_url_tool.OpenURLTool.run, "OpenURLTool.run")
    retrieval_source = _source(
        open_url_tool.OpenURLTool._retrieve_indexed_documents_with_filters,
        "OpenURLTool._retrieve_indexed_documents_with_filters",
    )

    availability_markers = (
        "from onyx.configs.app_configs import DISABLE_VECTOR_DB",
        "if DISABLE_VECTOR_DB:",
        "return False",
        "return True",
    )
    if any(marker not in availability_source for marker in availability_markers):
        raise RuntimeError("OpenURL lite-mode availability source drift")

    # Lite mode deliberately exposes only the crawler half of open_url. Verify
    # that an unavailable index remains an allowed sibling failure and cannot
    # prevent the crawler result from being returned.
    run_markers = (
        "(_retrieve_indexed_with_filters, (all_requests,))",
        "(self._fetch_web_content, (urls, override_kwargs.url_snippet_map))",
        "allow_failures=True",
    )
    retrieval_markers = (
        "self._document_index.id_based_retrieval(",
        "except Exception as exc:",
        "return IndexedRetrievalResult(",
    )
    if any(marker not in run_source for marker in run_markers) or any(
        marker not in retrieval_source for marker in retrieval_markers
    ):
        raise RuntimeError("OpenURL crawler/index fallback source drift")


def install() -> None:
    from onyx.configs.app_configs import DISABLE_VECTOR_DB
    from onyx.tools.tool_implementations.open_url import open_url_tool

    tool_class = open_url_tool.OpenURLTool
    if getattr(tool_class, "_wrapper_lite_availability_patch", False):
        return
    if not DISABLE_VECTOR_DB:
        raise RuntimeError(
            "Lite open_url availability patch requires DISABLE_VECTOR_DB=true"
        )

    _validate_upstream_shape(open_url_tool)

    def _crawler_available(cls, db_session):  # noqa: ANN001, ARG001
        return True

    tool_class.is_available = classmethod(_crawler_available)
    tool_class._wrapper_lite_availability_patch = True
    print(
        "sitecustomize_api_server: installed lite-mode crawler-backed "
        "open_url availability",
        flush=True,
    )
