"""API-service-owned wrapper bootstrap."""

from __future__ import annotations

from contextlib import redirect_stdout
import os
import sys


def _strict() -> bool:
    return os.environ.get("WRAPPER_PATCH_STRICT", "true").lower() in {
        "1", "true", "yes", "on"
    }


def _install() -> None:
    from wrapper_env_patches import apply_code_interpreter_network_description_patches
    from wrapper_env_patches import apply_coding_agent_final_answer_fallback_patch
    from wrapper_env_patches import apply_coding_agent_repo_download_limit_patch
    from wrapper_env_patches import apply_configured_inference_proxy_patch
    from wrapper_env_patches import apply_deep_research_chat_agent_tools_patch
    from wrapper_env_patches import apply_embedding_tokenizer_alias_patch
    from wrapper_env_patches import apply_internal_search_context_patches
    from wrapper_env_patches import apply_llm_max_tokens_override_patch
    from wrapper_env_patches import apply_mcp_egress_proxy_patch
    from wrapper_env_patches import apply_native_reasoning_detection_override_patch
    from wrapper_env_patches import apply_open_url_char_limit_patches
    from wrapper_env_patches import apply_playwright_helper_proxy_patch
    from wrapper_env_patches import apply_preserve_tool_results_patch
    from wrapper_env_patches import apply_python_file_link_prompt_patches
    from wrapper_env_patches import apply_python_package_capability_patches
    from wrapper_env_patches import apply_reasoning_content_preservation_patch
    from wrapper_env_patches import apply_reasoning_mode_trace_patch
    from wrapper_env_patches import apply_vllm_glm_auto_tool_choice_patch
    from open_url_failure_reporting_patch import (
        install as install_open_url_failure_reporting,
    )
    from lite_open_url_availability_patch import (
        install as install_lite_open_url_availability,
    )
    from onyx_crawler_egress_patch import install as install_onyx_crawler
    from onyx_crawler_egress_patch import use_obscura_browser

    apply_embedding_tokenizer_alias_patch()
    apply_llm_max_tokens_override_patch()
    apply_open_url_char_limit_patches()
    apply_coding_agent_repo_download_limit_patch()
    apply_configured_inference_proxy_patch()
    apply_mcp_egress_proxy_patch()
    apply_playwright_helper_proxy_patch()
    apply_internal_search_context_patches()
    apply_native_reasoning_detection_override_patch()
    apply_python_file_link_prompt_patches()
    apply_python_package_capability_patches()
    apply_code_interpreter_network_description_patches()
    apply_vllm_glm_auto_tool_choice_patch()
    apply_reasoning_mode_trace_patch()
    apply_deep_research_chat_agent_tools_patch()
    apply_reasoning_content_preservation_patch()
    apply_coding_agent_final_answer_fallback_patch()
    apply_preserve_tool_results_patch()
    if os.environ.get("ONYX_FORCE_OPEN_URL_AVAILABLE", "false").lower() in {
        "1", "true", "yes", "on"
    }:
        # Install before the transport/failure-reporting patches wrap run(), so
        # this patch can validate the pinned upstream crawler/index contract.
        install_lite_open_url_availability()
    install_open_url_failure_reporting()
    if use_obscura_browser():
        from obscura_crawler_patch import install as install_obscura_crawler

        install_obscura_crawler()
    else:
        install_onyx_crawler()


try:
    # Onyx's isolated-process protocol reserves child stdout for one pickled
    # result. Python imports sitecustomize before running that child module, so
    # every wrapper startup diagnostic must use stderr or it corrupts PDF and
    # other isolated-call results. Docker captures stderr in the same service
    # log stream, preserving operator visibility.
    with redirect_stdout(sys.stderr):
        _install()
except Exception as exc:
    print(
        f"sitecustomize_api_server: patch initialization failed: {exc}",
        file=sys.stderr,
        flush=True,
    )
    if _strict():
        raise
