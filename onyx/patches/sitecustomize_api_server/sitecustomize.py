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
    from onyx_wrapper_patches.api.python_capabilities import apply_code_interpreter_network_description_patches
    from onyx_wrapper_patches.api.prompt_stability import apply_agent_prompt_stability_patches
    from onyx_wrapper_patches.api.prompt_stability import validate_agent_prompt_stability_patches
    from onyx_wrapper_patches.api.python_artifacts import apply_chat_file_id_validation_patch
    from onyx_wrapper_patches.api.coding_final_answer import apply_coding_agent_final_answer_fallback_patch
    from onyx_wrapper_patches.shared.inference_proxy import apply_configured_inference_proxy_patch
    from onyx_wrapper_patches.api.deep_research import apply_deep_research_chat_agent_tools_patch
    from onyx_wrapper_patches.shared.embedding_tokenizer import apply_embedding_tokenizer_alias_patch
    from onyx_wrapper_patches.api.retrieval_limits import apply_internal_search_context_patches
    from onyx_wrapper_patches.api.deep_research import apply_deep_research_output_limit_patch
    from onyx_wrapper_patches.api.model_limits import apply_llm_max_tokens_override_patch
    from onyx_wrapper_patches.api.mcp_egress import apply_mcp_egress_proxy_patch
    from onyx_wrapper_patches.api.inference_continuation import apply_midstream_inference_continuation_patch
    from onyx_wrapper_patches.api.reasoning import apply_native_reasoning_detection_override_patch
    from onyx_wrapper_patches.api.tool_calls import apply_native_tool_calls_only_patch
    from onyx_wrapper_patches.api.retrieval_limits import apply_open_url_char_limit_patches
    from onyx_wrapper_patches.shared.playwright_proxy import apply_playwright_helper_proxy_patch
    from onyx_wrapper_patches.api.tool_result_history import apply_preserve_tool_results_patch
    from onyx_wrapper_patches.api.python_artifacts import apply_python_file_link_enforcement_patches
    from onyx_wrapper_patches.api.python_artifacts import apply_python_file_link_prompt_patches
    from onyx_wrapper_patches.api.python_capabilities import apply_python_package_capability_patches
    from onyx_wrapper_patches.api.reasoning import apply_reasoning_content_preservation_patch
    from onyx_wrapper_patches.api.reasoning import apply_reasoning_mode_trace_patch
    from onyx_wrapper_patches.api.searxng_retry import apply_searxng_single_attempt_patch
    from onyx_wrapper_patches.api.tool_calls import apply_vllm_glm_auto_tool_choice_patch
    from onyx_wrapper_patches.api.open_url import (
        install_failure_reporting as install_open_url_failure_reporting,
    )
    from onyx_wrapper_patches.api.open_url import install_url_limit as install_open_url_limit
    from onyx_wrapper_patches.api.github_egress_patch import install as install_github_egress
    from onyx_wrapper_patches.api.model_display_name_patch import install as install_model_display_names
    from onyx_wrapper_patches.api.config import use_obscura_browser
    from onyx_wrapper_patches.api.url_identity_preservation_patch import (
        install as install_url_identity_preservation,
    )
    from onyx_wrapper_patches.api.webui_reconnect_status_patch import (
        install as install_webui_reconnect_status,
    )

    # Foundational adapters precede consumers that bind their upstream symbols.
    apply_embedding_tokenizer_alias_patch()
    apply_llm_max_tokens_override_patch()
    apply_open_url_char_limit_patches()
    apply_configured_inference_proxy_patch()
    install_model_display_names()
    apply_mcp_egress_proxy_patch()
    apply_playwright_helper_proxy_patch()
    install_github_egress()
    apply_internal_search_context_patches()
    # Earlier inference imports bind reasoning aliases; the bounded identity
    # repair in reasoning updates those already-loaded consumers as well.
    apply_native_reasoning_detection_override_patch()
    apply_python_file_link_prompt_patches()
    apply_python_package_capability_patches()
    apply_code_interpreter_network_description_patches()
    apply_vllm_glm_auto_tool_choice_patch()
    apply_reasoning_mode_trace_patch()
    apply_deep_research_chat_agent_tools_patch()
    # Preserve the wrapper order: reasoning, native tools, continuation,
    # final-answer recovery, then retained tool history.
    apply_reasoning_content_preservation_patch()
    apply_native_tool_calls_only_patch()
    apply_midstream_inference_continuation_patch()
    apply_coding_agent_final_answer_fallback_patch()
    apply_preserve_tool_results_patch()
    apply_python_file_link_enforcement_patches()
    apply_deep_research_output_limit_patch()
    # Source reconstruction sees the completed research/artifact prompt edits.
    # Existing reconstruction effects are covered by composition tests.
    apply_agent_prompt_stability_patches()
    apply_chat_file_id_validation_patch()
    apply_searxng_single_attempt_patch()
    install_webui_reconnect_status()
    install_url_identity_preservation()
    # Identity precedes failure reporting; the limit wrapper captures that
    # implementation and must remain outside it.
    install_open_url_failure_reporting()
    install_open_url_limit()
    if use_obscura_browser():
        from onyx_wrapper_patches.api.obscura_crawler_patch import install as install_obscura_crawler

        install_obscura_crawler()
    else:
        from onyx_wrapper_patches.api.onyx_crawler_egress_patch import install as install_onyx_crawler

        install_onyx_crawler()
    # Check final globals after the selected crawler and all source rewrites.
    validate_agent_prompt_stability_patches()


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
        # CPython's site loader catches ordinary sitecustomize exceptions and
        # continues startup. Exit directly so a strict privacy/runtime patch
        # failure cannot leave an apparently healthy unpatched service.
        os._exit(78)
