"""Environment-driven runtime patches for stock Onyx containers.

This module is imported by sitecustomize so wrapper-level env vars can
adjust hardcoded limits without rebuilding images.
"""

from __future__ import annotations

import functools
import inspect
import json
import os


# We cannot remove truncation logic entirely without editing upstream code, so
# for "unlimited" we use a very large budget that won't be hit in practice.
EFFECTIVE_UNLIMITED_CHARS = 2_000_000_000


def _strict_mode() -> bool:
    return os.environ.get("WRAPPER_PATCH_STRICT", "true").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _warn_or_raise(message: str) -> None:
    print(f"sitecustomize: WARNING: {message}", flush=True)
    if _strict_mode():
        raise RuntimeError(message)


def _raise_if_strict() -> None:
    if _strict_mode():
        raise


def _replace_or_warn(
    *,
    owner_name: str,
    current: str,
    old: str,
    new: str,
) -> str:
    replaced = current.replace(old, new)
    if replaced == current:
        _warn_or_raise(
            f"{owner_name} patch did not match expected upstream text"
        )
    else:
        print(f"sitecustomize: patched {owner_name}", flush=True)
    return replaced


def _set_single_default(function, value: int, function_name: str) -> None:
    signature = inspect.signature(function)
    if not signature.parameters:
        _warn_or_raise(f"{function_name} has no parameters; cannot patch default")
        return

    defaults = function.__defaults__
    if defaults is None or len(defaults) != 1:
        _warn_or_raise(
            f"{function_name} expected one positional default, found {defaults!r}; "
            f"signature={signature}"
        )
        return

    function.__defaults__ = (value,)


def _parse_positive_int(var_name: str) -> int | None:
    raw = os.environ.get(var_name)
    if not raw:
        return None

    try:
        value = int(raw)
    except ValueError:
        print(
            f"sitecustomize: ignoring {var_name}={raw!r} (must be integer)",
            flush=True,
        )
        return None

    if value == 0:
        print(
            f"sitecustomize: {var_name}=0 -> using effectively unlimited budget "
            f"({EFFECTIVE_UNLIMITED_CHARS})",
            flush=True,
        )
        return EFFECTIVE_UNLIMITED_CHARS

    if value < 0:
        print(
            f"sitecustomize: ignoring {var_name}={raw!r} (must be >= 0)",
            flush=True,
        )
        return None

    return value


def _parse_int_at_least(var_name: str, minimum: int) -> int | None:
    raw = os.environ.get(var_name)
    if not raw:
        return None

    try:
        value = int(raw)
    except ValueError:
        print(
            f"sitecustomize: ignoring {var_name}={raw!r} (must be integer)",
            flush=True,
        )
        return None

    if value < minimum:
        print(
            f"sitecustomize: ignoring {var_name}={raw!r} "
            f"(must be >= {minimum})",
            flush=True,
        )
        return None

    return value


def _set_pydantic_field_default(model_cls, field_name: str, value: int) -> None:
    try:
        model_cls.model_fields[field_name].default = value
        model_cls.model_rebuild(force=True)
    except Exception as e:  # pragma: no cover
        _warn_or_raise(
            f"failed patching {model_cls.__name__}.{field_name} default: {e}"
        )


def _truncate_text_with_notice(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 0:
        return ""

    notice_template = (
        "\n... [internal search content truncated, {omitted} characters omitted]"
    )
    # Compute the suffix using a first-pass omitted count, then recompute once
    # because suffix length can change with digit count.
    suffix = notice_template.format(omitted=len(text))
    keep = max(0, max_chars - len(suffix))
    suffix = notice_template.format(omitted=len(text) - keep)
    keep = max(0, max_chars - len(suffix))
    return text[:keep] + suffix


def apply_internal_search_context_patches() -> None:
    """Apply wrapper limits to Onyx internal search context payloads.

    Onyx v4.1.7 has two useful but awkwardly named/internal controls:
    ``NUM_RETURNED_HITS`` is a candidate/result-section count, while
    ``MAX_CHUNKS_FED_TO_CHAT`` is used as both a rough token budget and a final
    section count. The wrapper exposes clearer env names and adds character
    caps after Onyx's context expansion step, where sections can grow beyond
    the nominal chunk count.
    """

    candidate_sections = _parse_int_at_least(
        "ONYX_RAG_INTERNAL_SEARCH_MAX_CANDIDATE_SECTIONS", 1
    )
    context_sections = _parse_int_at_least(
        "ONYX_RAG_INTERNAL_SEARCH_MAX_CONTEXT_SECTIONS", 1
    )
    max_chars_per_result = _parse_int_at_least(
        "ONYX_RAG_INTERNAL_SEARCH_MAX_CONTENT_CHARS_PER_RESULT", 0
    )
    max_total_chars = _parse_int_at_least(
        "ONYX_RAG_INTERNAL_SEARCH_MAX_TOTAL_CONTENT_CHARS", 0
    )

    if (
        candidate_sections is None
        and context_sections is None
        and max_chars_per_result is None
        and max_total_chars is None
    ):
        return

    if candidate_sections is not None or context_sections is not None:
        try:
            from onyx.configs import chat_configs
            from onyx.tools import models as tool_models
        except Exception as e:  # pragma: no cover
            print(
                f"sitecustomize: failed importing search limit modules: {e}",
                flush=True,
            )
            _raise_if_strict()
            return

        if candidate_sections is not None:
            chat_configs.NUM_RETURNED_HITS = candidate_sections
            tool_models.NUM_RETURNED_HITS = candidate_sections
            _set_pydantic_field_default(
                tool_models.SearchToolOverrideKwargs,
                "num_hits",
                candidate_sections,
            )

        if context_sections is not None:
            chat_configs.MAX_CHUNKS_FED_TO_CHAT = context_sections
            tool_models.MAX_CHUNKS_FED_TO_CHAT = context_sections
            _set_pydantic_field_default(
                tool_models.SearchToolOverrideKwargs,
                "max_llm_chunks",
                context_sections,
            )

    if max_chars_per_result is not None or max_total_chars is not None:
        try:
            from onyx.tools.tool_implementations import utils as tool_utils
        except Exception as e:  # pragma: no cover
            print(
                f"sitecustomize: failed importing search formatter module: {e}",
                flush=True,
            )
            _raise_if_strict()
            return

        original_convert = tool_utils.convert_inference_sections_to_llm_string

        @functools.wraps(original_convert)
        def _limited_convert_inference_sections_to_llm_string(*args, **kwargs):
            docs_str, citation_mapping = original_convert(*args, **kwargs)
            if not docs_str:
                return docs_str, citation_mapping

            try:
                payload = json.loads(docs_str)
                results = payload.get("results", [])
            except Exception as e:
                print(
                    f"sitecustomize: failed parsing internal search payload: {e}",
                    flush=True,
                )
                if _strict_mode():
                    raise
                return docs_str, citation_mapping

            remaining_total = max_total_chars
            for entry in results:
                content = entry.get("content")
                if not isinstance(content, str):
                    continue

                cap = len(content)
                if max_chars_per_result is not None:
                    cap = min(cap, max_chars_per_result)
                if remaining_total is not None:
                    cap = min(cap, remaining_total)

                limited_content = _truncate_text_with_notice(content, cap)
                entry["content"] = limited_content

                if remaining_total is not None:
                    remaining_total = max(0, remaining_total - len(limited_content))

            return (
                json.dumps(payload, indent=2, ensure_ascii=False),
                citation_mapping,
            )

        tool_utils.convert_inference_sections_to_llm_string = (
            _limited_convert_inference_sections_to_llm_string
        )

        # search_tool imports the formatter by name. Patch the imported symbol
        # too if the module is already importable during startup.
        try:
            from onyx.tools.tool_implementations.search import search_tool

            search_tool.convert_inference_sections_to_llm_string = (
                _limited_convert_inference_sections_to_llm_string
            )
        except Exception as e:  # pragma: no cover
            print(
                f"sitecustomize: search_tool formatter patch deferred/unavailable: {e}",
                flush=True,
            )
            if _strict_mode():
                raise

    print(
        "sitecustomize: applied internal search context limits "
        f"(candidate_sections={candidate_sections}, "
        f"context_sections={context_sections}, "
        f"max_chars_per_result={max_chars_per_result}, "
        f"max_total_chars={max_total_chars})",
        flush=True,
    )


def apply_open_url_char_limit_patches() -> None:
    per_url = _parse_positive_int("ONYX_OPEN_URL_MAX_CHARS_PER_URL")
    across_urls = _parse_positive_int("ONYX_OPEN_URL_MAX_TOTAL_CHARS")

    if per_url is None and across_urls is None:
        return

    try:
        from onyx.tools.tool_implementations.web_search import utils as ws_utils
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed importing web_search.utils: {e}", flush=True)
        _raise_if_strict()
        return

    if per_url is not None:
        ws_utils.MAX_CHARS_PER_URL = per_url
        _set_single_default(
            ws_utils.truncate_search_result_content,
            per_url,
            "web_search.utils.truncate_search_result_content",
        )
        _set_single_default(
            ws_utils._truncate_content_around_snippet,
            per_url,
            "web_search.utils._truncate_content_around_snippet",
        )

    if across_urls is None:
        across_urls = 10 * ws_utils.MAX_CHARS_PER_URL

    try:
        from onyx.tools.tool_implementations.open_url import open_url_tool
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed importing open_url_tool: {e}", flush=True)
        _raise_if_strict()
        return

    open_url_tool.MAX_CHARS_ACROSS_URLS = across_urls
    _set_single_default(
        open_url_tool._convert_sections_to_llm_string_with_citations,
        across_urls,
        "open_url_tool._convert_sections_to_llm_string_with_citations",
    )

    print(
        "sitecustomize: applied open_url char limit patch "
        f"(per_url={ws_utils.MAX_CHARS_PER_URL}, across_urls={across_urls})",
        flush=True,
    )


def _is_code_interpreter_network_enabled() -> bool:
    return os.environ.get("ONYX_CODE_INTERPRETER_ENABLE_NETWORK", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


# Hint text for the Python tool: crw Firecrawl API only (no CDP browser, no
# SearXNG). The Python tool is for data analysis / code execution, not browser
# automation, so we only mention the scraping API.
_PYTHON_SERVICE_HINTS = (
    " Additionally, the sandbox shares a network namespace with a local Firecrawl-compatible "
    "REST API at http://127.0.0.1:3010 exposing /v1/scrape, /v1/crawl, /v1/map, "
    "and /v1/search, all backed by a stealth headless browser. All outbound "
    "browser traffic egresses through the VPN tunnel."
)

# Hint text for the coding agent (BashTool + system prompt): crw Firecrawl API
# + CDP browser (no SearXNG). The coding agent benefits from direct browser
# automation for investigating web-accessible repos and docs.
_CODING_AGENT_SERVICE_HINTS = (
    " Additionally, the sandbox shares a network namespace with local scraping services: "
    "a Firecrawl-compatible REST API at http://127.0.0.1:3010 exposing "
    "/v1/scrape, /v1/crawl, /v1/map, and /v1/search, all backed by a stealth "
    "headless browser. Direct control of the stealth browser is available via "
    "Chrome DevTools Protocol (CDP) browser at ws://127.0.0.1:9222/devtools/browser. "
    "All outbound browser traffic egresses through the VPN tunnel."
)


def apply_code_interpreter_network_description_patches() -> None:
    """Update tool descriptions, system-prompt guidance, and coding-agent
    prompts when the code-interpreter executor pods are VPN-routed.

    By default, Onyx's code-interpreter hardcodes ``--network none`` on every
    executor pod, so the Python tool, BashTool, and coding-agent bash sessions
    have no network access. The upstream tool descriptions, the
    ``PYTHON_TOOL_GUIDANCE`` system prompt, and the coding-agent system prompts
    all advertise this ("network-restricted", "no network access",
    "network-isolated sandbox", "Internet access for this session is
    disabled").

    When ``ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`` is set on the api_server
    container, a companion sitecustomize patch in the code-interpreter
    container rewrites ``--network none`` to ``--network container:<self>``,
    giving executor pods VPN-routed internet access. This function updates the
    api_server-side descriptions and prompts so the LLM is told it has network
    access and can use network commands (curl, pip install, etc.) — otherwise
    the LLM would continue to avoid network commands based on the stale
    "no network" descriptions.
    """
    if not _is_code_interpreter_network_enabled():
        return

    # ── PythonTool description ──────────────────────────────────────────
    # Upstream: "Execute Python code in an isolated sandbox environment."
    # We replace "isolated sandbox environment" with VPN access info, the pip
    # restriction, the package list, and a code-agent recommendation. Using
    # .replace() preserves any future upstream additions to the description.
    try:
        from onyx.tools.tool_implementations.python.python_tool import PythonTool

        PythonTool.DESCRIPTION = _replace_or_warn(
            owner_name="PythonTool.DESCRIPTION",
            current=PythonTool.DESCRIPTION,
            old="Execute Python code in an isolated sandbox environment.",
            new=(
                "Execute Python code in a sandbox environment with internet access "
                "via VPN. Network operations (requests, urllib, etc.) are permitted "
                "and egress through the VPN tunnel. pip package installation is NOT "
                "supported — only the pre-installed scientific stack is available "
                "(numpy, pandas, scipy, matplotlib, seaborn, scikit-learn, "
                "scikit-image, opencv-python, xgboost, openpyxl, pdfplumber, pypdf, "
                "python-docx, python-pptx, fpdf2, pydantic). For tasks requiring "
                "additional packages or complex multi-step workflows, invoke the "
                "code agent instead." + _PYTHON_SERVICE_HINTS
            ),
        )
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch PythonTool.DESCRIPTION: {e}", flush=True)
        _raise_if_strict()

    # ── PYTHON_TOOL_GUIDANCE system prompt ──────────────────────────────
    # This is the per-tool guidance injected into the system prompt. Upstream
    # it says "Internet access for this session is disabled. Do not make
    # external web requests or API calls as they will fail." — we replace that
    # with VPN access info, the pip restriction, the package list, and a
    # recommendation to use the code agent for involved tasks. Patched at the
    # module level before prompt_utils.py imports it by name.
    try:
        from onyx.prompts import tool_prompts

        tool_prompts.PYTHON_TOOL_GUIDANCE = _replace_or_warn(
            owner_name="PYTHON_TOOL_GUIDANCE",
            current=tool_prompts.PYTHON_TOOL_GUIDANCE,
            old="Internet access for this session is disabled. Do not make external web requests or API calls as they will fail.",
            new=(
                "Internet access is available via VPN — external web requests and API calls are permitted and egress through the VPN tunnel. "
                "pip package installation is NOT supported; only the pre-installed scientific stack is available "
                "(numpy, pandas, scipy, matplotlib, seaborn, scikit-learn, scikit-image, opencv-python, xgboost, openpyxl, pdfplumber, pypdf, python-docx, python-pptx, fpdf2, pydantic). "
                "For tasks requiring additional packages or complex multi-step workflows, invoke the code agent instead."
                + _PYTHON_SERVICE_HINTS
            ),
        )
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch PYTHON_TOOL_GUIDANCE: {e}", flush=True)
        _raise_if_strict()

    # ── BashTool description ────────────────────────────────────────────
    # Upstream: "Execute a bash command inside an isolated, network-restricted
    # session." We replace "isolated, network-restricted session" with VPN
    # access info. Using .replace() preserves any future upstream additions.
    try:
        from onyx.tools.tool_implementations.bash.bash_tool import BashTool

        BashTool.DESCRIPTION = _replace_or_warn(
            owner_name="BashTool.DESCRIPTION",
            current=BashTool.DESCRIPTION,
            old="Execute a bash command inside an isolated, network-restricted session.",
            new=(
                "Execute a bash command inside a session with internet access via "
                "VPN. Network commands (curl, wget, pip install, npm install, git "
                "clone, etc.) are permitted and egress through the VPN tunnel."
                + _CODING_AGENT_SERVICE_HINTS
            ),
        )
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch BashTool.DESCRIPTION: {e}", flush=True)
        _raise_if_strict()

    # ── Coding agent bash tool description ──────────────────────────────
    # Upstream: "Run a bash command in the sandboxed session containing the
    # checked-out repository. The session has no network access. Use commands
    # like `ls`, `cat`, `grep -r`, `find`, `wc -l`, etc. to inspect the code.
    # Filesystem state persists across calls within the same session."
    # We replace "The session has no network access." with VPN access info.
    # Using .replace() preserves the rest of the description.
    try:
        from onyx.coding_agent import mock_tools

        _desc = mock_tools.BASH_TOOL_DESCRIPTION["function"]["description"]
        mock_tools.BASH_TOOL_DESCRIPTION["function"]["description"] = _replace_or_warn(
            owner_name="coding-agent BASH_TOOL_DESCRIPTION",
            current=_desc,
            old="The session has no network access.",
            new=(
                "The session has internet access via VPN. Network commands (curl, "
                "pip install, npm install, git clone, etc.) are permitted and "
                "egress through the VPN tunnel." + _CODING_AGENT_SERVICE_HINTS
            ),
        )
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed to patch coding-agent BASH_TOOL_DESCRIPTION: {e}",
            flush=True,
        )
        _raise_if_strict()

    # ── Coding agent system prompts ─────────────────────────────────────
    # The prompts are module-level string constants. They are imported by name
    # in fake_tools/coding_agent.py, so we must patch the module attributes
    # BEFORE that import happens (sitecustomize runs at interpreter startup,
    # before any onyx module is imported by the application).
    try:
        from onyx.prompts.coding_agent import coding_agent as ca_prompts

        ca_prompts.CODING_AGENT_PROMPT = _replace_or_warn(
            owner_name="CODING_AGENT_PROMPT network sandbox",
            current=ca_prompts.CODING_AGENT_PROMPT,
            old="network-isolated sandbox",
            new="sandbox with VPN-routed internet access",
        )
        ca_prompts.CODING_AGENT_PROMPT = _replace_or_warn(
            owner_name="CODING_AGENT_PROMPT network commands",
            current=ca_prompts.CODING_AGENT_PROMPT,
            old="Network commands (`curl`, `pip install`, `npm install`, `git pull`) — the sandbox has no network.",
            new=(
                "Network commands (`curl`, `pip install`, `npm install`, `git pull`) are permitted — the sandbox has VPN-routed internet access."
                + _CODING_AGENT_SERVICE_HINTS
            ),
        )

        ca_prompts.CODING_AGENT_PROMPT_REASONING = _replace_or_warn(
            owner_name="CODING_AGENT_PROMPT_REASONING network sandbox",
            current=ca_prompts.CODING_AGENT_PROMPT_REASONING,
            old="network-isolated sandbox",
            new="sandbox with VPN-routed internet access",
        )
        ca_prompts.CODING_AGENT_PROMPT_REASONING = _replace_or_warn(
            owner_name="CODING_AGENT_PROMPT_REASONING network access",
            current=ca_prompts.CODING_AGENT_PROMPT_REASONING,
            old="No network.",
            new="VPN-routed internet access is available."
            + _CODING_AGENT_SERVICE_HINTS,
        )

    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch coding-agent prompts: {e}", flush=True)
        _raise_if_strict()


def apply_firecrawl_wait_for_patch() -> None:
    """Patch FirecrawlClient to omit `waitFor` from /v2/scrape payloads.

    Onyx's FirecrawlClient sends ``{url, formats: ["markdown"]}`` with no
    ``waitFor`` field. This patch monkey-patches
    ``FirecrawlClient._get_webpage_content`` to ensure no ``waitFor`` field
    is ever sent, even if a future upstream change adds one.

    A ``waitFor`` field would make CRW do a blind fixed sleep after
    ``Page.loadEventFired`` before extracting content. This is actively
    harmful: it wastes time on fast pages (always sleeps the full duration
    even when the page is ready) and is insufficient on slow pages (Tor/
    VPN loads can take 20-40s, far exceeding any reasonable fixed sleep).
    Instead, page load waiting is handled by the CDP shim's ``waitUntil``
    injection (``OBSCURA_BROWSER_WAIT_UNTIL_SEARCH=networkidle2``), which makes obscura
    adaptively wait for network silence before returning the nav response.
    CRW then uses its smart heuristics (SPA selector poll, content
    stability, challenge retry) for any remaining post-navigate work.

    See ``docs/request_handling.md`` §1.6 for the full wait strategy.
    """
    try:
        from onyx.tools.tool_implementations.open_url.firecrawl import FirecrawlClient
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed importing FirecrawlClient: {e}", flush=True)
        _raise_if_strict()
        return

    def _patched_get_webpage_content(self, url: str):
        # No waitFor field — page load waiting is handled by the CDP shim's
        # waitUntil injection (OBSCURA_BROWSER_WAIT_UNTIL_SEARCH=networkidle2). CRW uses
        # its smart SPA selector poll + content stability heuristics for
        # any remaining post-navigate work instead of a blind fixed sleep.
        payload: dict = {
            "url": url,
            "formats": ["markdown"],
        }
        import requests as _requests
        response = _requests.post(
            self._base_url,
            headers=self._headers,
            json=payload,
            timeout=self._timeout_seconds,
        )

        if response.status_code != 200:
            try:
                error_payload = response.json()
            except Exception:
                error_payload = response.text
            self._last_error = (
                error_payload if isinstance(error_payload, str) else str(error_payload)
            )
            if 400 <= response.status_code < 500:
                from onyx.tools.tool_implementations.open_url.models import WebContent
                return WebContent(
                    title="",
                    link=url,
                    full_content="",
                    published_date=None,
                    scrape_successful=False,
                )
            raise ValueError(
                f"Firecrawl fetch failed with status {response.status_code}."
            )
        else:
            self._last_error = None

        response_json = response.json()
        extracted = self._extract_content_fields(response_json, url)
        from onyx.tools.tool_implementations.open_url.models import WebContent
        return WebContent(
            title=extracted.title,
            link=url,
            full_content=extracted.text,
            published_date=extracted.published_date,
            scrape_successful=bool(extracted.text),
        )

    FirecrawlClient._get_webpage_content = _patched_get_webpage_content
    print(
        f"sitecustomize: patched FirecrawlClient._get_webpage_content "
        f"(waitFor omitted, using CDP shim waitUntil + CRW heuristics)",
        flush=True,
    )
