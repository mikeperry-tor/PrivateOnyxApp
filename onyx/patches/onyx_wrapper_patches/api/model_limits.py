"""api model limits patch implementation."""

from __future__ import annotations

import functools
import inspect
from onyx_wrapper_patches.common.config import _raise_if_strict
from onyx_wrapper_patches.common.config import _warn_or_raise


def apply_llm_max_tokens_override_patch() -> None:
    """Make GEN_AI_MAX_TOKENS override DB and provider context limits.

    Onyx lets GEN_AI_MAX_TOKENS override provider/LiteLLM fallback metadata,
    but the normal chat construction path checks stored
    ModelConfiguration.max_input_tokens first. The wrapper exposes this as an
    admin override, so a configured value must win before the DB lookup too.
    """

    try:
        from onyx.configs.model_configs import GEN_AI_MAX_TOKENS
        from onyx.llm import factory
        from onyx.llm import utils as llm_utils
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing LLM max-token override modules: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    if not GEN_AI_MAX_TOKENS:
        return

    try:
        configured_source = inspect.getsource(
            factory._get_model_configured_max_input_tokens
        )
        provider_source = inspect.getsource(
            llm_utils.get_max_input_tokens_from_llm_provider
        )
    except Exception as e:  # pragma: no cover
        _warn_or_raise(f"could not inspect LLM max-token helpers: {e}")
        return

    if "model_configuration.max_input_tokens" not in configured_source:
        _warn_or_raise(
            "factory._get_model_configured_max_input_tokens no longer "
            "contains the expected DB max_input_tokens lookup"
        )
        return
    if "model_configuration.max_input_tokens" not in provider_source:
        _warn_or_raise(
            "llm_utils.get_max_input_tokens_from_llm_provider no longer "
            "contains the expected DB max_input_tokens lookup"
        )
        return

    original_get_model_configured_max_input_tokens = (
        factory._get_model_configured_max_input_tokens
    )
    original_get_max_input_tokens_from_llm_provider = (
        llm_utils.get_max_input_tokens_from_llm_provider
    )

    @functools.wraps(original_get_model_configured_max_input_tokens)
    def _override_model_configured_max_input_tokens(*args, **kwargs):
        return GEN_AI_MAX_TOKENS

    @functools.wraps(original_get_max_input_tokens_from_llm_provider)
    def _override_max_input_tokens_from_llm_provider(*args, **kwargs):
        return GEN_AI_MAX_TOKENS

    factory._get_model_configured_max_input_tokens = (
        _override_model_configured_max_input_tokens
    )
    factory.get_max_input_tokens_from_llm_provider = (
        _override_max_input_tokens_from_llm_provider
    )
    llm_utils.get_max_input_tokens_from_llm_provider = (
        _override_max_input_tokens_from_llm_provider
    )
    print(
        "sitecustomize: forcing LLM max input tokens to "
        f"GEN_AI_MAX_TOKENS={GEN_AI_MAX_TOKENS}",
        flush=True,
    )
