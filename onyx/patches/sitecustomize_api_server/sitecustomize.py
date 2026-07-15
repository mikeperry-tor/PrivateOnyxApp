"""API-service-owned wrapper bootstrap."""

from __future__ import annotations

import os


def _strict() -> bool:
    return os.environ.get("WRAPPER_PATCH_STRICT", "true").lower() in {
        "1", "true", "yes", "on"
    }


try:
    from wrapper_env_patches import apply_code_interpreter_network_description_patches
    from wrapper_env_patches import apply_coding_agent_final_answer_fallback_patch
    from wrapper_env_patches import apply_coding_agent_repo_download_limit_patch
    from wrapper_env_patches import apply_configured_inference_proxy_patch
    from wrapper_env_patches import apply_deep_research_chat_agent_tools_patch
    from wrapper_env_patches import apply_internal_search_context_patches
    from wrapper_env_patches import apply_llm_max_tokens_override_patch
    from wrapper_env_patches import apply_mcp_egress_proxy_patch
    from wrapper_env_patches import apply_native_reasoning_detection_override_patch
    from wrapper_env_patches import apply_open_url_char_limit_patches
    from wrapper_env_patches import apply_playwright_helper_proxy_patch
    from wrapper_env_patches import apply_preserve_tool_results_patch
    from wrapper_env_patches import apply_reasoning_content_preservation_patch
    from wrapper_env_patches import apply_reasoning_mode_trace_patch
    from wrapper_env_patches import apply_vllm_glm_auto_tool_choice_patch
    from obscura_crawler_patch import install as install_obscura_crawler

    apply_llm_max_tokens_override_patch()
    apply_open_url_char_limit_patches()
    apply_coding_agent_repo_download_limit_patch()
    apply_configured_inference_proxy_patch()
    apply_mcp_egress_proxy_patch()
    apply_playwright_helper_proxy_patch()
    apply_internal_search_context_patches()
    apply_native_reasoning_detection_override_patch()
    apply_code_interpreter_network_description_patches()
    apply_vllm_glm_auto_tool_choice_patch()
    apply_reasoning_mode_trace_patch()
    apply_deep_research_chat_agent_tools_patch()
    apply_reasoning_content_preservation_patch()
    apply_coding_agent_final_answer_fallback_patch()
    apply_preserve_tool_results_patch()
    install_obscura_crawler()

    if os.environ.get("ONYX_FORCE_OPEN_URL_AVAILABLE", "false").lower() in {
        "1", "true", "yes", "on"
    }:
        from onyx.tools.tool_implementations.open_url.open_url_tool import OpenURLTool
        OpenURLTool.is_available = classmethod(lambda cls, db_session: True)
except Exception as exc:
    print(f"sitecustomize_api_server: patch initialization failed: {exc}", flush=True)
    if _strict():
        raise
