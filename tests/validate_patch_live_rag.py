"""Manage one synthetic queued Web connector through the real authenticated API.

Use the manifest from prepare_patch_reorg_fixture.py. Each operation records
only fixture-owned IDs/evidence; no existing connector/document is enumerated.
The fixture overlay must already be running before create. Native deletion is
queued; poll until the pair is gone before deleting its credential.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "status", "search", "recrawl", "delete", "cleanup"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--expected-document-text", default="onyx synthetic activation fixture")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    auth = json.loads(args.credential_file.read_text())
    assert manifest["fixture_name"].startswith("onyx-reorg-")
    assert manifest["url"] == f"http://doc-drop-web:8091/{manifest['fixture_name']}/fixture.pdf"

    def save():
        args.manifest.write_text(json.dumps(manifest, indent=2) + "\n")

    def call(method, path, data=None):
        request = urllib.request.Request(args.base_url.rstrip("/") + "/api" + path,
            method=method, headers={"Authorization": "Bearer " + auth["api_key"], "Content-Type": "application/json"},
            data=None if data is None else json.dumps(data).encode())
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            # These routes are scoped to the synthetic fixture; preserve the
            # protocol's reason instead of hiding failed setup/cleanup.
            raise RuntimeError(f"{method} {path}: {exc.code}: {exc.read().decode()}") from exc

    if args.action == "create":
        assert not any(manifest.get(key) for key in ("connector_id", "credential_id", "cc_pair_id")), "fixture already created"
        connector = call("POST", "/manage/admin/connector", {
            "name": manifest["fixture_name"], "source": "web", "input_type": "load_state",
            # Match the documented local-doc connector. The synthetic PDF has
            # no outbound links, so recursion remains scoped to this one URL.
            "connector_specific_config": {"base_url": manifest["url"], "web_connector_type": "recursive"},
            "refresh_freq": None, "prune_freq": None, "access_type": "private", "groups": [],
        })
        manifest["connector_id"] = connector["id"]
        save()
        credential = call("POST", "/manage/credential", {
            "credential_json": {}, "source": "web", "admin_public": False,
            "name": manifest["fixture_name"],
        })
        manifest["credential_id"] = credential["id"]
        save()
        pair = call("PUT", f"/manage/connector/{manifest['connector_id']}/credential/{manifest['credential_id']}", {
            "name": manifest["fixture_name"], "access_type": "private", "groups": [],
        })
        assert pair["success"], pair
        manifest["cc_pair_id"] = pair["data"]
        save()
        # Scope subsequent internal_search to a set containing only this pair.
        document_set = call("POST", "/manage/admin/document-set", {
            "name": manifest["fixture_name"], "description": "Synthetic patch reorganization validation only",
            "cc_pair_ids": [manifest["cc_pair_id"]], "is_public": False,
            "users": [], "groups": [],
        })
        manifest["document_set_id"] = document_set if isinstance(document_set, int) else document_set["id"]
        save()
        print("Queued synthetic connector:", manifest["cc_pair_id"])
    elif args.action == "status":
        result = call("GET", f"/manage/admin/cc-pair/{manifest['cc_pair_id']}/index-attempts")
        (args.manifest.parent / "attempts.json").write_text(json.dumps(result, indent=2))
        for attempt in result["items"]:
            print({key: attempt.get(key) for key in ("id", "status", "new_docs_indexed", "total_docs_indexed", "error_count", "error_msg")})
    elif args.action == "recrawl":
        result = call("POST", "/manage/admin/connector/run-once", {
            "connector_id": manifest["connector_id"], "credential_ids": [manifest["credential_id"]], "from_beginning": True,
        })
        assert result["success"], result
        if result["data"] == 0:
            print("Recrawl not queued: an attempt is still active; native coordination was notified")
        else:
            assert result["data"] == 1, result
            print("Queued one fixture recrawl")
    elif args.action == "search":
        tools = {tool["in_code_tool_id"]: tool["id"] for tool in call("GET", "/tool")}
        tool_id = tools["SearchTool"]
        if not manifest.get("persona_id"):
            persona = call("POST", "/persona", {
                "name": manifest["fixture_name"], "description": "Synthetic scoped retrieval only",
                "document_set_ids": [manifest["document_set_id"]], "tool_ids": [tool_id],
                "is_public": False, "system_prompt": "", "task_prompt": "", "datetime_aware": False,
            })
            manifest["persona_id"] = persona["id"]
            save()
        created = call("POST", "/chat/create-chat-session", {
            "description": manifest["fixture_name"] + " scoped retrieval", "persona_id": manifest["persona_id"],
        })
        session = created["chat_session_id"]
        manifest["search_chat_session_id"] = session
        save()
        try:
            result = call("POST", "/chat/send-chat-message", {
                "chat_session_id": session,
                "message": "Use internal_search to find the synthetic PDF containing the exact phrase onyx synthetic activation fixture. Report the text found in that document, using only the selected document set.",
                "allowed_tool_ids": [tool_id], "forced_tool_id": tool_id,
                "internal_search_filters": {"document_set": [manifest["fixture_name"]]},
                "stream": False,
            })
            evidence_path = args.manifest.parent / f"internal-search-{session}.json"
            evidence_path.write_text(json.dumps(result, indent=2))
            manifest["last_search_evidence"] = str(evidence_path)
            save()
            assert not result.get("error_msg"), result.get("error_msg")
            calls = result.get("tool_calls") or []
            assert calls and all(tool["tool_name"] == "internal_search" for tool in calls), "native internal_search missing"
            assert any(args.expected_document_text in str(tool["tool_result"]) for tool in calls), "current fixture text absent from tool result"
            assert "onyx synthetic activation fixture" in result["answer"].lower(), "fixture absent from answer"
            documents = result.get("top_documents") or []
            assert documents and all(doc["document_id"] == manifest["url"] for doc in documents), "retrieval escaped the fixture scope"
            assert all(doc["link"] != manifest["url"] and doc["link"].endswith("/" + manifest["fixture_name"] + "/fixture.pdf") for doc in documents), "display-only URL rewrite missing"
            print("Scoped native internal_search retrieved the queued synthetic PDF")
        finally:
            call("POST", "/chat/stop-chat-session/" + session)
            call("DELETE", "/chat/delete-chat-session/" + session + "?hard_delete=true")
            manifest["search_chat_session_id"] = None
            save()
    elif args.action == "delete":
        call("POST", "/manage/admin/deletion-attempt", {key: manifest[key] for key in ("connector_id", "credential_id")})
        manifest["deletion_requested"] = True
        save()
        print("Queued deletion of synthetic connector and its indexed document")
    else:
        assert manifest.get("deletion_requested"), "queue native deletion before cleanup"
        # Native deletion owns indexed records and pair/connector removal.
        request = urllib.request.Request(args.base_url.rstrip("/") + f"/api/manage/admin/cc-pair/{manifest['cc_pair_id']}", headers={"Authorization": "Bearer " + auth["api_key"]})
        try:
            with urllib.request.urlopen(request, timeout=30):
                raise RuntimeError("Synthetic pair still exists; native deletion not complete")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404, exc.code
        if manifest.get("document_set_id") is not None:
            call("DELETE", f"/manage/admin/document-set/{manifest['document_set_id']}")
        if manifest.get("persona_id") is not None:
            call("DELETE", f"/persona/{manifest['persona_id']}")
        call("DELETE", f"/manage/admin/credential/{manifest['credential_id']}")
        manifest["api_cleanup_complete"] = True
        save()
        print("Synthetic pair deleted; removed its document set and credential")


if __name__ == "__main__":
    main()
