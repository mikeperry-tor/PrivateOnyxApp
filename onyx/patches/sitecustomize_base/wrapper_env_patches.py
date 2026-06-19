"""Environment-driven runtime patches for stock Onyx containers.

This module is imported by sitecustomize so wrapper-level env vars can
adjust hardcoded limits without rebuilding images.
"""

from __future__ import annotations

import os


# We cannot remove truncation logic entirely without editing upstream code, so
# for "unlimited" we use a very large budget that won't be hit in practice.
EFFECTIVE_UNLIMITED_CHARS = 2_000_000_000


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


def apply_open_url_char_limit_patches() -> None:
    per_url = _parse_positive_int("OPEN_URL_MAX_CHARS_PER_URL")
    across_urls = _parse_positive_int("OPEN_URL_MAX_CHARS_ACROSS_URLS")

    if per_url is None and across_urls is None:
        return

    try:
        from onyx.tools.tool_implementations.web_search import utils as ws_utils
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed importing web_search.utils: {e}", flush=True)
        return

    if per_url is not None:
        ws_utils.MAX_CHARS_PER_URL = per_url
        ws_utils.truncate_search_result_content.__defaults__ = (per_url,)
        ws_utils._truncate_content_around_snippet.__defaults__ = (per_url,)

    if across_urls is None:
        across_urls = 10 * ws_utils.MAX_CHARS_PER_URL

    try:
        from onyx.tools.tool_implementations.open_url import open_url_tool
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed importing open_url_tool: {e}", flush=True)
        return

    open_url_tool.MAX_CHARS_ACROSS_URLS = across_urls
    open_url_tool._convert_sections_to_llm_string_with_citations.__defaults__ = (
        across_urls,
    )

    print(
        "sitecustomize: applied open_url char limit patch "
        f"(per_url={ws_utils.MAX_CHARS_PER_URL}, across_urls={across_urls})",
        flush=True,
    )


def _is_vpn_routed() -> bool:
    return os.environ.get("CODE_INTERPRETER_VPN_ROUTED", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def apply_code_interpreter_network_description_patches() -> None:
    """Update tool descriptions and coding-agent prompts when the
    code-interpreter executor pods are VPN-routed.

    By default, Onyx's code-interpreter hardcodes ``--network none`` on every
    executor pod, so the Python tool, BashTool, and coding-agent bash sessions
    have no network access. The upstream tool descriptions and coding-agent
    system prompts all advertise this ("network-restricted", "no network
    access", "network-isolated sandbox").

    When ``CODE_INTERPRETER_VPN_ROUTED=true`` is set on the api_server
    container, a companion sitecustomize patch in the code-interpreter
    container rewrites ``--network none`` to ``--network container:<self>``,
    giving executor pods VPN-routed internet access. This function updates the
    api_server-side descriptions and prompts so the LLM is told it has network
    access and can use network commands (curl, pip install, etc.) — otherwise
    the LLM would continue to avoid network commands based on the stale
    "no network" descriptions.
    """
    if not _is_vpn_routed():
        return

    # ── PythonTool ──────────────────────────────────────────────────────
    try:
        from onyx.tools.tool_implementations.python.python_tool import PythonTool

        PythonTool.DESCRIPTION = PythonTool.DESCRIPTION.replace(
            "Execute Python code in an isolated sandbox environment.",
            "Execute Python code in a sandbox environment with internet access "
            "via VPN. Network operations (requests, urllib, pip, etc.) are "
            "permitted and egress through the VPN tunnel."
        )
        print("sitecustomize: patched PythonTool.DESCRIPTION for VPN routing", flush=True)
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch PythonTool.DESCRIPTION: {e}", flush=True)

    # ── BashTool ────────────────────────────────────────────────────────
    try:
        from onyx.tools.tool_implementations.bash.bash_tool import BashTool

        BashTool.DESCRIPTION = BashTool.DESCRIPTION.replace(
            "Execute a bash command inside an isolated, network-restricted session.",
            "Execute a bash command inside a session with internet access via "
            "VPN. Network commands (curl, wget, pip install, npm install, git "
            "clone, etc.) are permitted and egress through the VPN tunnel."
        )
        print("sitecustomize: patched BashTool.DESCRIPTION for VPN routing", flush=True)
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch BashTool.DESCRIPTION: {e}", flush=True)

    # ── Coding agent bash tool description ──────────────────────────────
    # Upstream: "Run a bash command in the sandboxed session containing the
    # checked-out repository. The session has no network access. Use commands
    # like `ls`, `cat`, `grep -r`, `find`, `wc -l`, etc. to inspect the code.
    # Filesystem state persists across calls within the same session."
    # We replace "The session has no network access." with VPN access info.
    # Using .replace() preserves the rest of the description.
    try:
        from onyx.coding_agent import mock_tools

        mock_tools.BASH_TOOL_DESCRIPTION["function"]["description"] = (
            "Run a bash command in the sandboxed session containing the "
            "checked-out repository. The session has internet access via VPN. "
            "Network commands (curl, pip install, npm install, git clone, "
            "etc.) are permitted and egress through the VPN tunnel. Use "
            "commands like `ls`, `cat`, `grep -r`, `find`, `wc -l`, etc. to "
            "inspect the code. Filesystem state persists across calls within "
            "the same session."
        )
        print(
            "sitecustomize: patched coding-agent BASH_TOOL_DESCRIPTION for VPN routing",
            flush=True,
        )
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed to patch coding-agent BASH_TOOL_DESCRIPTION: {e}",
            flush=True,
        )

    # ── Coding agent system prompts ─────────────────────────────────────
    # The prompts are module-level string constants. They are imported by name
    # in fake_tools/coding_agent.py, so we must patch the module attributes
    # BEFORE that import happens (sitecustomize runs at interpreter startup,
    # before any onyx module is imported by the application).
    try:
        from onyx.prompts.coding_agent import coding_agent as ca_prompts

        ca_prompts.CODING_AGENT_PROMPT = ca_prompts.CODING_AGENT_PROMPT.replace(
            "network-isolated sandbox",
            "sandbox with VPN-routed internet access",
        ).replace(
            "Network commands (`curl`, `pip install`, `npm install`, `git pull`) — the sandbox has no network.",
            "Network commands (`curl`, `pip install`, `npm install`, `git pull`) are permitted — the sandbox has VPN-routed internet access.",
        )

        ca_prompts.CODING_AGENT_PROMPT_REASONING = (
            ca_prompts.CODING_AGENT_PROMPT_REASONING.replace(
                "network-isolated sandbox",
                "sandbox with VPN-routed internet access",
            ).replace(
                "No network.",
                "VPN-routed internet access is available.",
            )
        )

        print(
            "sitecustomize: patched coding-agent prompts for VPN routing",
            flush=True,
        )
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch coding-agent prompts: {e}", flush=True)
