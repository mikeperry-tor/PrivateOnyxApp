"""api tool result history patch implementation."""

from __future__ import annotations

import functools
import inspect
import json
from onyx_wrapper_patches.common.config import _env_flag_enabled
from onyx_wrapper_patches.common.config import _raise_if_strict
from onyx_wrapper_patches.common.config import _warn_or_raise


def apply_preserve_tool_results_patch() -> None:
    """Optionally preserve prior-turn tool responses in future LLM context.

    Upstream Onyx reconstructs previous assistant tool-call history with a
    placeholder for every non-image tool response. When enabled, keep the
    saved tool_call_response instead and recompute response token counts so
    context trimming reflects the larger payload.
    """

    if not _env_flag_enabled("ONYX_AGENT_PRESERVE_TOOL_RESULTS"):
        return

    try:
        from onyx.chat import chat_utils
        from onyx.configs.constants import MessageType
        from onyx.prompts.chat_prompts import TOOL_CALL_RESPONSE_CROSS_MESSAGE
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing tool-result preservation modules: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    try:
        source = inspect.getsource(chat_utils._build_tool_call_response_history_message)
    except Exception as e:  # pragma: no cover
        _warn_or_raise(
            "could not inspect chat_utils._build_tool_call_response_history_message: "
            f"{e}"
        )
        return

    if "TOOL_CALL_RESPONSE_CROSS_MESSAGE" not in source:
        _warn_or_raise(
            "chat_utils._build_tool_call_response_history_message no longer "
            "contains the expected placeholder behavior"
        )
        return

    helper_signature = inspect.signature(
        chat_utils._build_tool_call_response_history_message
    )
    if tuple(helper_signature.parameters) != (
        "tool_name",
        "generated_images",
        "tool_call_response",
    ):
        _warn_or_raise(
            "chat_utils._build_tool_call_response_history_message signature "
            f"changed; found {helper_signature}"
        )
        return

    original_convert_chat_history = chat_utils.convert_chat_history
    convert_signature = inspect.signature(original_convert_chat_history)
    if tuple(convert_signature.parameters) != (
        "chat_history",
        "files",
        "context_image_files",
        "additional_context",
        "token_counter",
        "tool_id_to_name_map",
    ):
        _warn_or_raise(
            "chat_utils.convert_chat_history signature changed; "
            f"found {convert_signature}"
        )
        return

    def _preserving_tool_call_response_history_message(
        tool_name: str,
        generated_images: list[dict] | None,
        tool_call_response: str | None,
    ) -> str:
        if tool_name == chat_utils.IMAGE_GENERATION_TOOL_NAME:
            if generated_images:
                llm_image_context: list[dict[str, str]] = []
                for image in generated_images:
                    file_id = image.get("file_id")
                    revised_prompt = image.get("revised_prompt")
                    if not isinstance(file_id, str):
                        continue

                    llm_image_context.append(
                        {
                            "file_id": file_id,
                            "revised_prompt": (
                                revised_prompt
                                if isinstance(revised_prompt, str)
                                else ""
                            ),
                        }
                    )

                if llm_image_context:
                    return json.dumps(llm_image_context)

        if tool_call_response:
            return tool_call_response

        return TOOL_CALL_RESPONSE_CROSS_MESSAGE

    @functools.wraps(original_convert_chat_history)
    def _preserving_convert_chat_history(*args, **kwargs):
        result = original_convert_chat_history(*args, **kwargs)

        token_counter = kwargs.get("token_counter")
        if token_counter is None and len(args) >= 5:
            token_counter = args[4]
        if token_counter is None:
            _warn_or_raise("convert_chat_history token_counter not found")
            return result

        for msg in result.simple_messages:
            if msg.message_type == MessageType.TOOL_CALL_RESPONSE:
                msg.token_count = token_counter(msg.message)

        return result

    chat_utils._build_tool_call_response_history_message = (
        _preserving_tool_call_response_history_message
    )
    chat_utils.convert_chat_history = _preserving_convert_chat_history
    print(
        "sitecustomize: preserving previous tool-call responses in chat history",
        flush=True,
    )
