"""Wrapper-side runtime monkey patches for Onyx containers.

Loaded automatically by Python when this directory is on PYTHONPATH.
"""

from __future__ import annotations


def _apply_base_env_patches() -> None:
    try:
        from wrapper_env_patches import apply_code_interpreter_network_description_patches
        from wrapper_env_patches import apply_open_url_char_limit_patches

        apply_open_url_char_limit_patches()
        apply_code_interpreter_network_description_patches()
    except Exception as e:  # pragma: no cover
        # In lite mode this should usually succeed because base patch path is
        # included after this directory. If not, proceed with lite-only patches.
        print(f"sitecustomize: base env patches unavailable: {e}", flush=True)


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


_apply_base_env_patches()
_force_open_url_available()
