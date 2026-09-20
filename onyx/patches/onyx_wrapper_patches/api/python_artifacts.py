"""api python artifacts patch implementation."""

from __future__ import annotations

import functools
import importlib
import inspect
import json
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID
from onyx_wrapper_patches.common.config import _raise_if_strict
from onyx_wrapper_patches.common.config import _replace_or_warn
from onyx_wrapper_patches.common.config import _warn_or_raise
from onyx_wrapper_patches.common.source import _patch_function_source
from onyx_wrapper_patches.common.source import _prompt_stability_replace


_CHAT_FILE_PATH_RE = re.compile(r"^/api/chat/file/[^/?#]+$")



_CHAT_FILE_MARKDOWN_CANDIDATE_LIMIT = 4096



def _relative_chat_file_destination(destination: str) -> tuple[str, str] | None:
    """Return one browser-current-origin chat-file URL, or ``None``.

    Markdown destinations produced by older Onyx versions may contain the
    configured ``WEB_DOMAIN``. Only the endpoint path is authoritative: using
    it as a relative URL keeps localhost, Tailscale, and onion frontends
    interchangeable within the same running stack.
    """
    candidate = destination.strip()
    if candidate.startswith("<") and candidate.endswith(">"):
        candidate = candidate[1:-1].strip()
    if not candidate or any(character.isspace() for character in candidate):
        return None

    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None

    if parsed.scheme and parsed.scheme.lower() not in {"http", "https"}:
        return None
    if not parsed.scheme and not parsed.netloc and not candidate.startswith("/"):
        return None
    if not _CHAT_FILE_PATH_RE.fullmatch(parsed.path):
        return None

    relative = parsed.path
    if parsed.query:
        relative += f"?{parsed.query}"
    if parsed.fragment:
        relative += f"#{parsed.fragment}"
    return relative, parsed.path.rsplit("/", 1)[-1]



def _markdown_link_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")



def _generated_chat_file_filenames(tool_calls: Any) -> dict[str, str]:
    """Map generated chat-file IDs to their authoritative stored filenames."""
    filenames: dict[str, str] = {}
    for tool_call in tool_calls or ():
        generated_files = getattr(tool_call, "generated_files", None)
        if not generated_files:
            response = getattr(tool_call, "tool_call_response", None)
            try:
                response_data = json.loads(response) if isinstance(response, str) else response
            except (TypeError, json.JSONDecodeError):
                response_data = None
            if isinstance(response_data, dict):
                generated_files = response_data.get("generated_files")

        for generated_file in generated_files or ():
            if isinstance(generated_file, dict):
                filename = generated_file.get("filename")
                file_link = generated_file.get("file_link")
            else:
                filename = getattr(generated_file, "filename", None)
                file_link = getattr(generated_file, "file_link", None)
            if not isinstance(filename, str) or not isinstance(file_link, str):
                continue
            destination = _relative_chat_file_destination(file_link)
            if destination is not None:
                _, file_id = destination
                filenames[file_id] = filename
    return filenames



def _canonical_generated_chat_file_id(
    candidate: str, filenames: dict[str, str]
) -> str:
    """Recover an underscore-corrupted UUID only from authoritative metadata."""
    if candidate in filenames or "_" not in candidate:
        return candidate
    try:
        candidate_uuid = UUID(candidate.replace("_", ""))
    except ValueError:
        return candidate

    matches: list[str] = []
    for known_id in filenames:
        try:
            if UUID(known_id) == candidate_uuid:
                matches.append(known_id)
        except ValueError:
            continue
    return matches[0] if len(matches) == 1 else candidate



def _find_unescaped(value: str, character: str, start: int) -> int:
    index = start
    while True:
        index = value.find(character, index)
        if index < 0:
            return -1
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and value[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            return index
        index += 1



def _is_escaped(value: str, index: int) -> bool:
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and value[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1



class _ChatFileMarkdownStream:
    """Incrementally canonicalize Markdown links to generated chat files."""

    def __init__(self, filenames: dict[str, str] | None = None) -> None:
        self._pending = ""
        self._code_ticks: int | None = None
        self._filenames = filenames or {}

    def feed(self, content: str, *, final: bool = False) -> str:
        self._pending += content
        output: list[str] = []

        while self._pending:
            if self._code_ticks is not None:
                tick_index = self._pending.find("`")
                if tick_index < 0:
                    output.append(self._pending)
                    self._pending = ""
                    break
                if tick_index > 0:
                    output.append(self._pending[:tick_index])
                    self._pending = self._pending[tick_index:]
                    continue
                tick_count = len(self._pending) - len(self._pending.lstrip("`"))
                if tick_count == len(self._pending) and not final:
                    break
                output.append(self._pending[:tick_count])
                self._pending = self._pending[tick_count:]
                if tick_count == self._code_ticks:
                    self._code_ticks = None
                continue

            open_index = self._pending.find("[")
            tick_index = self._pending.find("`")
            if tick_index >= 0 and (open_index < 0 or tick_index < open_index):
                if _is_escaped(self._pending, tick_index):
                    output.append(self._pending[: tick_index + 1])
                    self._pending = self._pending[tick_index + 1 :]
                    continue
                if tick_index > 0:
                    output.append(self._pending[:tick_index])
                    self._pending = self._pending[tick_index:]
                    continue
                tick_count = len(self._pending) - len(self._pending.lstrip("`"))
                if tick_count == len(self._pending) and not final:
                    break
                output.append(self._pending[:tick_count])
                self._pending = self._pending[tick_count:]
                self._code_ticks = tick_count
                continue
            if open_index < 0:
                if final:
                    output.append(self._pending)
                    self._pending = ""
                elif self._pending.endswith("!"):
                    output.append(self._pending[:-1])
                    self._pending = "!"
                else:
                    output.append(self._pending)
                    self._pending = ""
                break

            image_marker = (
                open_index > 0
                and self._pending[open_index - 1] == "!"
                and not _is_escaped(self._pending, open_index - 1)
            )
            candidate_start = open_index - 1 if image_marker else open_index
            if candidate_start > 0:
                output.append(self._pending[:candidate_start])
                self._pending = self._pending[candidate_start:]
                continue

            bracket_index = 1 if image_marker else 0
            label_end = _find_unescaped(self._pending, "]", bracket_index + 1)
            if label_end < 0:
                if final or len(self._pending) > _CHAT_FILE_MARKDOWN_CANDIDATE_LIMIT:
                    output.append(self._pending[0])
                    self._pending = self._pending[1:]
                    continue
                break
            if label_end + 1 >= len(self._pending):
                if final:
                    output.append(self._pending)
                    self._pending = ""
                break
            if self._pending[label_end + 1] != "(":
                output.append(self._pending[: label_end + 1])
                self._pending = self._pending[label_end + 1 :]
                continue

            destination_end = _find_unescaped(self._pending, ")", label_end + 2)
            if destination_end < 0:
                if final or len(self._pending) > _CHAT_FILE_MARKDOWN_CANDIDATE_LIMIT:
                    output.append(self._pending[0])
                    self._pending = self._pending[1:]
                    continue
                break

            destination = self._pending[label_end + 2 : destination_end]
            resolved = _relative_chat_file_destination(destination)
            complete = self._pending[: destination_end + 1]
            if resolved is None:
                output.append(complete)
            else:
                relative, file_id = resolved
                canonical_id = _canonical_generated_chat_file_id(
                    file_id, self._filenames
                )
                if canonical_id != file_id:
                    path_length = len(f"/api/chat/file/{file_id}")
                    relative = f"/api/chat/file/{canonical_id}{relative[path_length:]}"
                    file_id = canonical_id
                label_start = bracket_index + 1
                label = (
                    _markdown_link_label(self._filenames[file_id])
                    if file_id in self._filenames
                    else self._pending[label_start:label_end]
                )
                output.append(f"[{label}]({relative})")
            self._pending = self._pending[destination_end + 1 :]

        return "".join(output)

    def flush(self) -> str:
        return self.feed("", final=True)



def _normalize_chat_file_markdown(
    content: str, filenames: dict[str, str] | None = None
) -> str:
    stream = _ChatFileMarkdownStream(filenames)
    return stream.feed(content) + stream.flush()



def _append_python_guidance_to_replacement_prompt(
    prompt: str | None,
    tools: list[Any],
) -> str | None:
    if not any(getattr(tool, "name", None) == "run_python" for tool in tools):
        return prompt

    from onyx.prompts.tool_prompts import PYTHON_TOOL_GUIDANCE
    from onyx.prompts.tool_prompts import TOOL_SECTION_HEADER

    if prompt and PYTHON_TOOL_GUIDANCE in prompt:
        return prompt
    return (prompt or "") + TOOL_SECTION_HEADER + PYTHON_TOOL_GUIDANCE



class _ChatFileMarkdownEmitter:
    def __init__(
        self,
        emitter: Any,
        packet_type: Any,
        delta_type: Any,
        filenames: dict[str, str],
    ) -> None:
        self._emitter = emitter
        self._packet_type = packet_type
        self._delta_type = delta_type
        self._stream = _ChatFileMarkdownStream(filenames)
        self._placement = None

    def _emit_content(self, content: str) -> None:
        if content:
            self._emitter.emit(
                self._packet_type(
                    placement=self._placement,
                    obj=self._delta_type(content=content),
                )
            )

    def emit(self, packet: Any) -> None:
        if isinstance(packet.obj, self._delta_type):
            self._placement = packet.placement
            self._emit_content(self._stream.feed(packet.obj.content))
            return
        self._emit_content(self._stream.flush())
        self._emitter.emit(packet)

    def flush(self) -> None:
        self._emit_content(self._stream.flush())



def apply_python_file_link_enforcement_patches() -> None:
    """Enforce portable generated-file Markdown at every response boundary."""
    try:
        from onyx.chat import llm_loop
        from onyx.server.query_and_chat import session_loading
        from onyx.server.query_and_chat.streaming_models import AgentResponseDelta
        from onyx.server.query_and_chat.streaming_models import Packet
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing Python file-link enforcement targets: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    try:
        run_source = inspect.getsource(llm_loop.run_llm_loop)
        replacement_anchor = (
            "                system_prompt = (\n"
            "                    ChatMessageSimple(\n"
            "                        message=processed_system_prompt,\n"
        )
        if run_source.count(replacement_anchor) != 1:
            _warn_or_raise(
                "replace-base Python guidance patch expected exactly one system-prompt "
                "construction site"
            )
            return
        empty_base_anchor = (
            "                    system_prompt = (\n"
            "                        ChatMessageSimple(\n"
            "                            message=processed_custom_agent_prompt,\n"
        )
        if run_source.count(empty_base_anchor) != 1:
            _warn_or_raise(
                "empty-base Python guidance patch expected exactly one custom-prompt "
                "construction site"
            )
            return
        llm_loop._wrapper_append_python_guidance = (
            _append_python_guidance_to_replacement_prompt
        )
        _patch_function_source(
            module=llm_loop,
            function_name="run_llm_loop",
            patch_name="replace-base Python tool guidance",
            replacements={
                replacement_anchor: (
                    "                processed_system_prompt = "
                    "_wrapper_append_python_guidance(processed_system_prompt, tools)\n"
                    + replacement_anchor
                ),
                empty_base_anchor: (
                    "                    processed_custom_agent_prompt = "
                    "_wrapper_append_python_guidance(processed_custom_agent_prompt, tools)\n"
                    + empty_base_anchor
                ),
            },
        )
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed to patch replace-base Python guidance: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    original_step = llm_loop.run_llm_step
    if getattr(original_step, "_wrapper_chat_file_markdown", False):
        _warn_or_raise("Python file-link stream patch was installed more than once")
        return

    signature = inspect.signature(original_step)
    for required in ("emitter", "state_container"):
        if required not in signature.parameters:
            _warn_or_raise(
                f"run_llm_step no longer has required {required!r} parameter"
            )
            return

    @functools.wraps(original_step)
    def _run_llm_step_with_chat_file_markdown(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        emitter = bound.arguments["emitter"]
        state_container = bound.arguments["state_container"]
        filenames = _generated_chat_file_filenames(
            state_container.get_tool_calls() if state_container is not None else ()
        )
        normalizing_emitter = _ChatFileMarkdownEmitter(
            emitter, Packet, AgentResponseDelta, filenames
        )
        bound.arguments["emitter"] = normalizing_emitter
        try:
            result, has_reasoned = original_step(*bound.args, **bound.kwargs)
        finally:
            normalizing_emitter.flush()

        normalized_answer = (
            _normalize_chat_file_markdown(result.answer, filenames)
            if isinstance(result.answer, str)
            else result.answer
        )
        if normalized_answer != result.answer:
            result = result.model_copy(update={"answer": normalized_answer})
        if state_container is not None and normalized_answer is not None:
            state_container.set_answer_tokens(normalized_answer)
        return result, has_reasoned

    _run_llm_step_with_chat_file_markdown._wrapper_chat_file_markdown = True
    llm_loop.run_llm_step = _run_llm_step_with_chat_file_markdown
    print("sitecustomize: enforced streamed Python chat-file Markdown", flush=True)

    translate_source = inspect.getsource(
        session_loading.translate_assistant_message_to_packets
    )
    history_anchor = "                message_text=chat_message.message,\n"
    if translate_source.count(history_anchor) != 1:
        _warn_or_raise(
            "assistant session loader no longer has exactly one saved message site"
        )
        return
    session_loading._wrapper_normalize_saved_chat_file_markdown = (
        lambda content, tool_calls: _normalize_chat_file_markdown(
            content, _generated_chat_file_filenames(tool_calls)
        )
    )
    _patch_function_source(
        module=session_loading,
        function_name="translate_assistant_message_to_packets",
        patch_name="saved Python chat-file Markdown",
        replacements={
            history_anchor: (
                "                message_text="
                "_wrapper_normalize_saved_chat_file_markdown(\n"
                "                    chat_message.message, chat_message.tool_calls\n"
                "                ),\n"
            )
        },
    )
    session_loading.translate_assistant_message_to_packets._wrapper_chat_file_markdown = (
        True
    )
    print("sitecustomize: normalized saved Python chat-file Markdown", flush=True)



def apply_chat_file_id_validation_patch() -> None:
    """Keep non-UUID chat-file IDs out of the UUID-only UserFile lookup."""
    try:
        from onyx.db import user_file
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed importing chat-file ID validation target: {e}",
            flush=True,
        )
        _raise_if_strict()
        return

    source = inspect.getsource(user_file.get_user_file_by_id)
    anchor = (
        "    return db_session.query(UserFile).filter("
        "UserFile.id == user_file_id).first()\n"
    )
    if source.count(anchor) != 1:
        _warn_or_raise(
            "chat-file UserFile helper no longer has exactly one UUID lookup site"
        )
        return

    user_file._wrapper_is_uuid = lambda value: _is_uuid(value)
    _patch_function_source(
        module=user_file,
        function_name="get_user_file_by_id",
        patch_name="non-UUID chat-file ID guard",
        replacements={
            anchor: (
                "    if not _wrapper_is_uuid(user_file_id):\n"
                "        return None\n"
                + anchor
            )
        },
    )
    user_file.get_user_file_by_id._wrapper_chat_file_id_guard = True
    user_file.get_file_id_by_user_file_id._wrapper_chat_file_id_guard = True
    print("sitecustomize: guarded chat-file UserFile UUID lookup", flush=True)



def _is_uuid(value: Any) -> bool:
    try:
        UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True



_PYTHON_EXECUTION_GUIDANCE = (
    "Each call runs in a fresh, stateless sandbox. Variables, imports, files "
    "(including /tmp and /workspace), and background processes do not survive "
    "between calls. Keep dependent downloads, parsing, computation, and saving "
    "in one script; recreate required inputs in each call unless they are "
    "explicitly supplied as staged chat files. Saving a downloadable artifact "
    "does not preserve the execution environment. Before using version-specific "
    "APIs, check the runtime version and required feature availability in the "
    "same script; do not assume the runtime matches the version being researched."
)



_UPSTREAM_PYTHON_STATELESS_GUIDANCE = (
    "IMPORTANT: each call to this tool runs in a fresh, stateless sandbox. "
    "Variables, imports, and in-memory state from previous calls will NOT be available, "
    "and files written by a previous call will NOT be available in later calls. "
    "Therefore batch multi-step work into a single script per call: e.g. load a "
    "workbook once, read all needed sheets, apply all edits, and save the result "
    "in one execution — not one small step per call."
)



def apply_python_file_link_prompt_patches() -> None:
    """Clarify per-call execution state and make artifact links explicit.

    Upstream constructs absolute ``file_link`` values from one canonical
    ``WEB_DOMAIN``. The WebUI recognizes an ordinary Markdown link whose label
    is an image filename, reduces it to its file ID, and renders it from a
    relative same-origin URL. Returning the relative URL in the first place
    also makes an accidental Markdown image work through any wrapper frontend
    without adding a remote CSP source.

    The rule is present in the function description, stable Python guidance,
    and the result itself. The result supplies a
    ready-to-copy ``response_markdown`` value so the model does not have to
    reconstruct either the label or URL.

    Shared execution guidance distinguishes downloadable artifacts from sandbox
    persistence and reaches research agents through the function description.
    """
    link_instruction = (
        "In the final answer, include every user-requested generated file by "
        "copying its `response_markdown` value exactly. This is an ordinary "
        "Markdown link `[filename](file_link)`. Treat the entire value, including "
        "its filename and opaque per-execution file ID, as immutable: do not "
        "retype, rename, shorten, describe, or alter any character. Never "
        "construct or hard-code a file URL."
    )

    try:
        from onyx.tools.tool_implementations.python.python_tool import PythonTool

        PythonTool.DESCRIPTION = _replace_or_warn(
            owner_name="PythonTool.DESCRIPTION file-link instruction",
            current=PythonTool.DESCRIPTION,
            old="Execute Python code in an isolated sandbox environment.",
            new=(
                "Execute Python code in an isolated sandbox environment. "
                + _PYTHON_EXECUTION_GUIDANCE
                + " "
                + link_instruction
            ),
        )
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed to patch PythonTool.DESCRIPTION file links: {e}",
            flush=True,
        )
        _raise_if_strict()

    try:
        from onyx.prompts import tool_prompts

        tool_prompts.PYTHON_TOOL_GUIDANCE = _prompt_stability_replace(
            label="PYTHON_TOOL_GUIDANCE artifact persistence",
            value=tool_prompts.PYTHON_TOOL_GUIDANCE,
            old="The current directory in the file system can be used to save and persist user files.",
            new="Save user-requested downloadable artifacts in the current directory.",
        )
        tool_prompts.PYTHON_TOOL_GUIDANCE = _prompt_stability_replace(
            label="PYTHON_TOOL_GUIDANCE execution environment",
            value=tool_prompts.PYTHON_TOOL_GUIDANCE,
            old=_UPSTREAM_PYTHON_STATELESS_GUIDANCE,
            new=_PYTHON_EXECUTION_GUIDANCE,
        )
        tool_prompts.PYTHON_TOOL_GUIDANCE = _replace_or_warn(
            owner_name="PYTHON_TOOL_GUIDANCE file-link instruction",
            current=tool_prompts.PYTHON_TOOL_GUIDANCE,
            old=(
                "Use this to give the user a way to download the file OR to "
                "display generated images."
            ),
            new=(
                "Use this to give the user a way to download the file or display "
                "a generated image. "
                + link_instruction
                + " Even for an image, do not substitute Markdown image syntax "
                "(`![filename](file_link)`) for the supplied ordinary link."
            ),
        )
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed to patch PYTHON_TOOL_GUIDANCE file links: {e}",
            flush=True,
        )
        _raise_if_strict()

    try:
        file_utils = importlib.import_module("onyx.file_store.utils")
        python_tool_module = importlib.import_module(
            "onyx.tools.tool_implementations.python.python_tool"
        )
        PythonTool = python_tool_module.PythonTool

        if (
            python_tool_module.build_full_frontend_file_url
            is not file_utils.build_full_frontend_file_url
        ):
            _warn_or_raise(
                "PythonTool file-link helper did not match the expected "
                "build_full_frontend_file_url import"
            )

        original_run = PythonTool.run
        parameters = tuple(inspect.signature(original_run).parameters.values())
        if (
            tuple(parameter.name for parameter in parameters)
            != ("self", "placement", "override_kwargs", "llm_kwargs")
            or parameters[-1].kind is not inspect.Parameter.VAR_KEYWORD
        ):
            _warn_or_raise(
                "PythonTool.run signature changed; cannot add generated-file "
                "response_markdown safely"
            )

        python_tool_module.build_full_frontend_file_url = (
            file_utils.build_frontend_file_url
        )

        @functools.wraps(original_run)
        def _patched_run(self, *args, **kwargs):  # noqa: ANN001
            response = original_run(self, *args, **kwargs)
            parsed = json.loads(response.llm_facing_response)
            generated_files = parsed.get("generated_files")
            if not generated_files:
                return response
            if not isinstance(generated_files, list):
                raise RuntimeError(
                    "PythonTool generated_files result is not a list"
                )

            rich_files = getattr(response.rich_response, "generated_files", None)
            if not isinstance(rich_files, list) or len(rich_files) != len(
                generated_files
            ):
                raise RuntimeError(
                    "PythonTool LLM and rich generated-file results disagree"
                )

            for index, generated_file in enumerate(generated_files):
                if not isinstance(generated_file, dict):
                    raise RuntimeError(
                        "PythonTool generated-file result is not an object"
                    )
                filename = generated_file.get("filename")
                file_link = generated_file.get("file_link")
                if not isinstance(filename, str) or not filename:
                    raise RuntimeError(
                        "PythonTool generated-file result has no filename"
                    )
                if (
                    not isinstance(file_link, str)
                    or not file_link.startswith("/api/chat/file/")
                ):
                    raise RuntimeError(
                        "PythonTool generated-file result has no relative "
                        "same-origin file_link"
                    )

                label = (
                    filename.replace("\\", "\\\\")
                    .replace("[", "\\[")
                    .replace("]", "\\]")
                )
                generated_file["response_markdown"] = f"[{label}]({file_link})"
                rich_files[index].file_link = file_link

            patched_json = json.dumps(parsed, separators=(",", ":"))
            if hasattr(response, "model_copy"):
                return response.model_copy(
                    update={"llm_facing_response": patched_json}
                )
            response.llm_facing_response = patched_json
            return response

        _patched_run._wrapper_python_file_link_patch = True
        PythonTool.run = _patched_run
        print(
            "sitecustomize: patched PythonTool generated-file result links",
            flush=True,
        )
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed to patch PythonTool generated-file results: {e}",
            flush=True,
        )
        _raise_if_strict()
