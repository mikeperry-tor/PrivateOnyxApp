"""shared inference proxy patch implementation."""

from __future__ import annotations

import functools
import inspect
import os
from typing import Any
from urllib.parse import urlsplit
from onyx_wrapper_patches.common.config import _raise_if_strict
from onyx_wrapper_patches.common.config import _validated_fixed_proxy_url
from onyx_wrapper_patches.common.config import _warn_or_raise


def apply_configured_inference_proxy_patch() -> None:
    """Give supported configured chat bases one explicit controlled client."""

    proxy_url = _validated_fixed_proxy_url(
        "ONYX_CONFIGURED_INFERENCE_HTTP_PROXY_URL", "onyx-host-egress-bridge"
    )
    internal_base_url = os.environ.get(
        "ONYX_CONFIGURED_INFERENCE_INTERNAL_BASE_URL", ""
    ).strip()
    internal_base = urlsplit(internal_base_url)
    try:
        internal_port = internal_base.port
    except ValueError:
        internal_port = None
    if (
        internal_base.scheme != "http"
        or internal_base.hostname != "teep"
        or internal_port != 8337
        or internal_base.username is not None
        or internal_base.password is not None
        or internal_base.path.rstrip("/") != "/v1"
        or internal_base.query
        or internal_base.fragment
    ):
        raise RuntimeError(
            "ONYX_CONFIGURED_INFERENCE_INTERNAL_BASE_URL must be exactly "
            "http://teep:8337/v1"
        )
    try:
        import httpx
        import openai
        from litellm import HTTPHandler

        from onyx.llm import multi_llm
        from onyx.llm.api_surfaces import LlmApiSurface
        from onyx.llm.constants import LlmProviderNames
        from onyx.server.manage.llm import api as llm_api
        from onyx.utils.url import validate_outbound_http_url
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing configured inference patch deps: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    original_init = multi_llm.LitellmLLM.__init__
    original_completion = multi_llm.LitellmLLM._completion
    original_models_response = llm_api._get_openai_compatible_models_response
    completion_signature = inspect.signature(original_completion)
    if "client" not in completion_signature.parameters:
        _warn_or_raise(
            f"LitellmLLM._completion no longer accepts client: {completion_signature}"
        )
        return
    models_response_signature = inspect.signature(original_models_response)
    if tuple(models_response_signature.parameters) != (
        "url",
        "source_name",
        "api_key",
    ):
        _warn_or_raise(
            "configured inference model-discovery signature changed: "
            f"{models_response_signature}"
        )
        return
    try:
        models_response_source = inspect.getsource(original_models_response)
    except (OSError, TypeError) as e:
        _warn_or_raise(
            "could not inspect configured inference model-discovery source: "
            f"{e}"
        )
        return
    for marker in (
        "response = httpx.get(url, headers=headers, timeout=10.0)",
        "response.raise_for_status()",
        "except httpx.HTTPStatusError as e:",
        "except httpx.RequestError as e:",
    ):
        if marker not in models_response_source:
            _warn_or_raise(
                "configured inference model-discovery source contract changed; "
                f"missing {marker!r}"
            )
            return

    supported = {
        str(LlmProviderNames.OPENAI),
        str(LlmProviderNames.OPENAI_COMPATIBLE),
        str(LlmProviderNames.BIFROST),
        str(LlmProviderNames.LITELLM_PROXY),
        str(LlmProviderNames.LM_STUDIO),
        str(LlmProviderNames.OLLAMA_CHAT),
        str(LlmProviderNames.PORTKEY),
    }

    @functools.wraps(original_init)
    def _patched_init(self, *args, **kwargs):  # noqa: ANN001
        original_init(self, *args, **kwargs)
        self._wrapper_configured_inference_client = None
        self._wrapper_configured_inference_http_client = None
        if not self._api_base:
            return
        validate_outbound_http_url(
            self._api_base,
            allow_private_network=True,
            block_loopback_and_link_local=True,
            resolve_dns=False,
        )
        provider = str(self._model_provider)
        if provider not in supported:
            raise RuntimeError(
                "configured inference api_base is not proxy-covered for provider "
                f"{provider!r} at the pinned Onyx/LiteLLM version"
            )
        configured_base = urlsplit(self._api_base)
        is_internal_teep = (
            configured_base.scheme == internal_base.scheme
            and configured_base.hostname == internal_base.hostname
            and configured_base.port == internal_base.port
            and configured_base.path.rstrip("/") == internal_base.path.rstrip("/")
            and configured_base.username is None
            and configured_base.password is None
            and not configured_base.query
            and not configured_base.fragment
        )
        client_kwargs: dict[str, Any] = {
            "trust_env": False,
            "timeout": self._timeout,
        }
        if not is_internal_teep:
            client_kwargs["proxy"] = proxy_url
        http_client = httpx.Client(**client_kwargs)
        if self._api_surface is LlmApiSurface.ANTHROPIC_MESSAGES:
            if provider != str(LlmProviderNames.PORTKEY):
                raise RuntimeError(
                    "configured Anthropic-compatible inference is not "
                    f"proxy-covered for provider {provider!r}"
                )
            inference_client = HTTPHandler(
                timeout=self._timeout,
                client=http_client,
            )
        else:
            inference_client = openai.OpenAI(
                api_key=self._api_key or "not-needed",
                base_url=self._api_base,
                http_client=http_client,
            )
        self._wrapper_configured_inference_http_client = http_client
        self._wrapper_configured_inference_client = inference_client

    @functools.wraps(original_completion)
    def _patched_completion(self, *args, **kwargs):  # noqa: ANN001
        configured_client = getattr(
            self, "_wrapper_configured_inference_client", None
        )
        if configured_client is None:
            return original_completion(self, *args, **kwargs)
        bound = completion_signature.bind_partial(self, *args, **kwargs)
        bound.arguments["client"] = configured_client
        return original_completion(*bound.args, **bound.kwargs)

    def _is_internal_teep_models_url(url: str) -> bool:
        parsed = urlsplit(url)
        try:
            parsed_port = parsed.port
        except ValueError:
            return False
        return (
            parsed.scheme == internal_base.scheme
            and parsed.hostname == internal_base.hostname
            and parsed_port == internal_base.port
            and parsed.path.rstrip("/")
            == f"{internal_base.path.rstrip('/')}/models"
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )

    @functools.wraps(original_models_response)
    def _patched_models_response(
        url: str,
        source_name: str,
        api_key: str | None = None,
    ) -> dict:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://onyx.app",
            "X-Title": "Onyx",
        }
        if not api_key:
            headers.pop("Authorization")

        client_kwargs: dict[str, Any] = {"trust_env": False}
        if not _is_internal_teep_models_url(url):
            client_kwargs["proxy"] = proxy_url
        try:
            with httpx.Client(**client_kwargs) as client:
                response = client.get(url, headers=headers, timeout=10.0)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise llm_api.OnyxError(
                    llm_api.OnyxErrorCode.VALIDATION_ERROR,
                    "Authentication failed: invalid or missing API key for "
                    f"{source_name}.",
                )
            if e.response.status_code == 404:
                raise llm_api.OnyxError(
                    llm_api.OnyxErrorCode.VALIDATION_ERROR,
                    f"{source_name} models endpoint not found at {url}. "
                    "Please verify the API base URL.",
                )
            raise llm_api.OnyxError(
                llm_api.OnyxErrorCode.BAD_GATEWAY,
                f"Failed to fetch {source_name} models: {e}",
            )
        except httpx.RequestError as e:
            llm_api.logger.warning(
                "Could not reach OpenAI-compatible models endpoint",
                extra={"source": source_name, "url": url, "error": str(e)},
                exc_info=True,
            )
            raise llm_api.OnyxError(
                llm_api.OnyxErrorCode.VALIDATION_ERROR,
                f"Could not reach {source_name} at {url}. Check that the URL is "
                f"correct and reachable from Onyx ({type(e).__name__}).",
            )
        except ValueError as e:
            llm_api.logger.warning(
                "Received invalid model response from OpenAI-compatible endpoint",
                extra={"source": source_name, "url": url, "error": str(e)},
                exc_info=True,
            )
            raise llm_api.OnyxError(
                llm_api.OnyxErrorCode.BAD_GATEWAY,
                f"Failed to fetch {source_name} models: {e}",
            )

    multi_llm.LitellmLLM.__init__ = _patched_init
    multi_llm.LitellmLLM._completion = _patched_completion
    llm_api._get_openai_compatible_models_response = _patched_models_response
    print(
        "sitecustomize: routed supported configured inference bases and model "
        "discovery through fixed host egress with an exact internal Teep exception",
        flush=True,
    )
