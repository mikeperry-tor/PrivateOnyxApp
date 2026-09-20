"""Authenticated synthetic chat/tool/history smoke; removes only its own session.

The credential file is a JSON object containing an Onyx admin API key in
``api_key``. Evidence contains only the synthetic test's requests/responses.
No existing chat, persona, or provider configuration is changed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import urllib.request
import uuid


def validate(credential_file: Path, evidence: Path, base_url: str, timeout: int) -> None:
    evidence.mkdir(mode=0o700, parents=False, exist_ok=False)
    key = json.loads(credential_file.read_text())["api_key"]

    def call(method, path, data=None):
        request = urllib.request.Request(
            base_url.rstrip("/") + "/api" + path,
            method=method,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            data=None if data is None else json.dumps(data).encode(),
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return json.loads(body) if body else None

    tools = {tool["in_code_tool_id"]: tool["id"] for tool in call("GET", "/tool")}
    # Native policy disables open_url web fetching when web_search is excluded.
    allowed = [tools["WebSearchTool"], tools["OpenURLTool"]]
    created = call("POST", "/chat/create-chat-session", {
        "description": "onyx-reorg-validation " + uuid.uuid4().hex,
    })
    session = created["chat_session_id"]
    (evidence / "session.json").write_text(json.dumps(created))
    try:
        requests = [
            {
                "message": "Use open_url to read https://example.com/ and give its page title. The synthetic phrase for this conversation is amber otter 742. Do not save a persistent memory or use other tools.",
                "allowed_tool_ids": allowed,
                "forced_tool_id": tools["OpenURLTool"],
            },
            {
                "message": "What page title did the tool return, and what exact synthetic test phrase did I give in my previous message? Answer briefly without using tools.",
                "allowed_tool_ids": [],
            },
            {
                "message": "Use open_url to read https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf and report the exact text in this public test PDF. Do not use any other tool.",
                "allowed_tool_ids": allowed,
                "forced_tool_id": tools["OpenURLTool"],
            },
        ]
        for index, request in enumerate(requests, 1):
            request.update(chat_session_id=session, stream=False)
            result = call("POST", "/chat/send-chat-message", request)
            (evidence / f"turn-{index}.json").write_text(json.dumps(result, indent=2))
            assert not result.get("error_msg"), result.get("error_msg")
            assert result.get("answer"), "empty synthetic response"
            if index == 1:
                calls = result.get("tool_calls") or []
                assert calls and all(tool["tool_name"] == "open_url" for tool in calls), "expected only native open_url execution"
                documents = [document for tool in calls for document in json.loads(tool["tool_result"])["results"]]
                assert any(document["url"] == "https://example.com/" and document["title"] == "Example Domain" for document in documents), "native tool result did not contain the fetched page"
                assert "example domain" in result["answer"].lower(), "public page title missing"
            elif index == 2:
                assert "amber otter 742" in result["answer"].lower(), "follow-up lost synthetic history"
                assert "example domain" in result["answer"].lower(), "follow-up lost tool result"
            else:
                calls = result.get("tool_calls") or []
                assert calls and all(tool["tool_name"] == "open_url" for tool in calls)
                assert any("dummy pdf file" in tool["tool_result"].lower() for tool in calls), "PDF text missing from native tool result"
                assert "dummy pdf file" in result["answer"].lower(), "PDF text missing from answer"
            print(f"Synthetic chat turn {index} passed", flush=True)
    finally:
        # A timed-out HTTP client must not leave its test-owned inference running.
        call("POST", "/chat/stop-chat-session/" + session)
        call("DELETE", "/chat/delete-chat-session/" + session + "?hard_delete=true")
        (evidence / "cleaned.json").write_text(json.dumps({"session_deleted": session}))
        print("Removed synthetic chat session", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--evidence-directory", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    validate(args.credential_file, args.evidence_directory, args.base_url, args.timeout)
