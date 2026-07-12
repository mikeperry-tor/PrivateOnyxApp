"""Wrapper-side runtime monkey patches for Onyx containers.

Loaded automatically by Python when this directory is on PYTHONPATH.
"""

from __future__ import annotations

import os


def _strict_mode() -> bool:
    return os.environ.get("WRAPPER_PATCH_STRICT", "true").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _apply_base_env_patches() -> None:
    try:
        from wrapper_env_patches import apply_code_interpreter_network_description_patches
        from wrapper_env_patches import apply_coding_agent_final_answer_fallback_patch
        from wrapper_env_patches import apply_llm_max_tokens_override_patch
        from wrapper_env_patches import apply_native_reasoning_detection_override_patch
        from wrapper_env_patches import apply_open_url_char_limit_patches
        from wrapper_env_patches import apply_preserve_tool_results_patch
        from wrapper_env_patches import apply_reasoning_content_preservation_patch
        from wrapper_env_patches import apply_reasoning_mode_trace_patch

        apply_llm_max_tokens_override_patch()
        apply_open_url_char_limit_patches()
        apply_native_reasoning_detection_override_patch()
        apply_code_interpreter_network_description_patches()
        apply_reasoning_mode_trace_patch()
        apply_reasoning_content_preservation_patch()
        apply_coding_agent_final_answer_fallback_patch()
        apply_preserve_tool_results_patch()
    except Exception as e:  # pragma: no cover
        # In lite mode this should usually succeed because base patch path is
        # included after this directory. If not, proceed with lite-only patches.
        print(f"sitecustomize: base env patches unavailable: {e}", flush=True)
        if _strict_mode():
            raise


def _force_open_url_available() -> None:
    try:
        from onyx.tools.tool_implementations.open_url.open_url_tool import OpenURLTool

        def _always_available(cls, db_session):  # noqa: ANN001
            return True

        OpenURLTool.is_available = classmethod(_always_available)
        print("sitecustomize: patched OpenURLTool.is_available -> True", flush=True)
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch OpenURLTool: {e}", flush=True)
        if _strict_mode():
            raise


_apply_base_env_patches()
_force_open_url_available()
