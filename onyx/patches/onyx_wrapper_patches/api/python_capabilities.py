"""api python capabilities patch implementation."""

from __future__ import annotations

import os
from onyx_wrapper_patches.common.config import _raise_if_strict
from onyx_wrapper_patches.common.config import _replace_or_warn


def _is_code_interpreter_network_enabled() -> bool:
    return os.environ.get("ONYX_CODE_INTERPRETER_ENABLE_NETWORK", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )



_PYTHON_PACKAGE_LIST = (
    "numpy, pandas, scipy, sympy, matplotlib, seaborn, scikit-learn, "
    "scikit-image, opencv-python, xgboost, openpyxl, pdfplumber, pypdf, "
    "python-docx, python-pptx, fpdf2, reportlab, svglib, pydantic, Pillow"
)



def apply_python_package_capability_patches() -> None:
    """Advertise the executor's wrapper-maintained pre-installed package set."""
    package_sentence = f"Pre-installed packages include ({_PYTHON_PACKAGE_LIST})."

    try:
        from onyx.tools.tool_implementations.python.python_tool import PythonTool

        PythonTool.DESCRIPTION = _replace_or_warn(
            owner_name="PythonTool.DESCRIPTION package capabilities",
            current=PythonTool.DESCRIPTION,
            old="Execute Python code in an isolated sandbox environment.",
            new=(
                "Execute Python code in an isolated sandbox environment. "
                + package_sentence
            ),
        )
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed to patch PythonTool.DESCRIPTION packages: {e}",
            flush=True,
        )
        _raise_if_strict()

    try:
        from onyx.prompts import tool_prompts

        tool_prompts.PYTHON_TOOL_GUIDANCE = _replace_or_warn(
            owner_name="PYTHON_TOOL_GUIDANCE package capabilities",
            current=tool_prompts.PYTHON_TOOL_GUIDANCE,
            old=(
                "Use `openpyxl` to read and write Excel files. You have access "
                "to libraries like numpy, pandas, scipy, matplotlib, and PIL."
            ),
            new=(
                "Use `openpyxl` to read and write Excel files. " + package_sentence
            ),
        )
    except Exception as e:  # pragma: no cover
        print(
            f"sitecustomize: failed to patch PYTHON_TOOL_GUIDANCE packages: {e}",
            flush=True,
        )
        _raise_if_strict()



_RESTRICTED_NETWORK_TEXT = (
    "Network access is available through a restricted HTTP/HTTPS proxy. "
    "Direct socket access to the internet and direct access to stack-internal "
    "services are blocked. Internal/private network targets and direct "
    "search-engine URLs are blocked by the proxy."
)



def apply_code_interpreter_network_description_patches() -> None:
    """Update tool descriptions, system-prompt guidance, and coding-agent
    prompts when executor pods use the restricted egress network.

    By default, Onyx's code-interpreter selects ``--network none`` on every
    executor pod, so the Python tool, BashTool, and coding-agent bash sessions
    have no network access. The upstream tool descriptions, the
    ``PYTHON_TOOL_GUIDANCE`` system prompt, and the coding-agent system prompts
    all advertise this ("network-restricted", "no network access",
    "network-isolated sandbox", "Internet access for this session is
    disabled").

    When ``ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true`` is set on the api_server
    container, the wrapper configures upstream's native Docker network and run-
    argument settings to attach pods to a dedicated internal network and inject
    its local policy proxy. The deterministic and pinned-image suites validate
    that native contract outside the running service. This function updates the
    api_server-side descriptions and prompts so the LLM is told it has network
    access and can use network commands (curl, pip install, etc.) — otherwise
    the LLM would continue to avoid network commands based on the stale
    "no network" descriptions.
    """
    if not _is_code_interpreter_network_enabled():
        return

    # ── PythonTool description ──────────────────────────────────────────
    # The unconditional package patch has already extended the upstream
    # description. Replace its complete article/sandbox phrase with the network
    # and pip capability text so the package list retains one owner.
    try:
        from onyx.tools.tool_implementations.python.python_tool import PythonTool

        PythonTool.DESCRIPTION = _replace_or_warn(
            owner_name="PythonTool.DESCRIPTION",
            current=PythonTool.DESCRIPTION,
            old="an isolated sandbox environment.",
            new=(
                "a sandbox environment. "
                + _RESTRICTED_NETWORK_TEXT
                + " pip package installation is NOT "
                "supported. For tasks requiring "
                "additional packages or complex multi-step workflows, invoke the "
                "code agent instead."
            ),
        )
    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch PythonTool.DESCRIPTION: {e}", flush=True)
        _raise_if_strict()

    # ── PYTHON_TOOL_GUIDANCE system prompt ──────────────────────────────
    # This is the per-tool guidance injected into the system prompt. Upstream
    # it says "Internet access for this session is disabled. Do not make
    # external web requests or API calls as they will fail." — we replace that
    # with VPN access info, the pip restriction, and a recommendation to use
    # the code agent for involved tasks. The unconditional package patch owns
    # the package list. Patched before prompt_utils.py imports it by name.
    try:
        from onyx.prompts import tool_prompts

        tool_prompts.PYTHON_TOOL_GUIDANCE = _replace_or_warn(
            owner_name="PYTHON_TOOL_GUIDANCE",
            current=tool_prompts.PYTHON_TOOL_GUIDANCE,
            old="Internet access for this session is disabled. Do not make external web requests or API calls as they will fail.",
            new=(
                _RESTRICTED_NETWORK_TEXT + " "
                "pip package installation is NOT supported. "
                "For tasks requiring additional packages or complex multi-step workflows, invoke the code agent instead."
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
                "Execute a bash command inside a sandboxed session. "
                + _RESTRICTED_NETWORK_TEXT
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
                _RESTRICTED_NETWORK_TEXT
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
            new="sandbox with restricted proxy-only network access",
        )
        ca_prompts.CODING_AGENT_PROMPT = _replace_or_warn(
            owner_name="CODING_AGENT_PROMPT network commands",
            current=ca_prompts.CODING_AGENT_PROMPT,
            old=(
                "Avoid:\n"
                "- Network commands (`curl`, `pip install`, `npm install`, `git pull`) "
                "— the sandbox has no network.\n"
            ),
            new=(
                "Network access:\n"
                "- Network commands (`curl`, `pip install`, `npm install`, `git pull`) "
                "may use the restricted HTTP/HTTPS proxy. Direct sockets, "
                "private/internal targets, and direct search-engine URLs are blocked."
                + "\n\nAvoid:\n"
            ),
        )

        ca_prompts.CODING_AGENT_PROMPT_REASONING = _replace_or_warn(
            owner_name="CODING_AGENT_PROMPT_REASONING network sandbox",
            current=ca_prompts.CODING_AGENT_PROMPT_REASONING,
            old="network-isolated sandbox",
            new="sandbox with restricted proxy-only network access",
        )
        ca_prompts.CODING_AGENT_PROMPT_REASONING = _replace_or_warn(
            owner_name="CODING_AGENT_PROMPT_REASONING network access",
            current=ca_prompts.CODING_AGENT_PROMPT_REASONING,
            old="No network.",
            new=_RESTRICTED_NETWORK_TEXT,
        )

    except Exception as e:  # pragma: no cover
        print(f"sitecustomize: failed to patch coding-agent prompts: {e}", flush=True)
        _raise_if_strict()
