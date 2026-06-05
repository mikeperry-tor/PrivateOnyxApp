"""Wrapper-side runtime monkey patches for Onyx containers.

Loaded automatically by Python when this directory is on PYTHONPATH.
"""

from __future__ import annotations


def _force_open_url_available() -> None:
    try:
        from onyx.tools.tool_implementations.open_url.open_url_tool import OpenURLTool

        def _always_available(cls, db_session):  # noqa: ANN001
            return True

        OpenURLTool.is_available = classmethod(_always_available)
        print("sitecustomize: patched OpenURLTool.is_available -> True", flush=True)
    except Exception as e:  # pragma: no cover
        # Never block startup if patching fails.
        print(f"sitecustomize: failed to patch OpenURLTool: {e}", flush=True)


_force_open_url_available()
