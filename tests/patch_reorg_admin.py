"""Create/revoke an explicitly authorized temporary Onyx validation admin key.

Run against a started Docker or Podman API container using Onyx's native database
helpers; requires administrator authorization. The key is never printed or passed as a
command argument. The exclusive credential file has mode 0600. Revoke only after
removing test-owned chat/connector records. The temporary account has persistent
memory disabled so history tests use their own conversation. Existing accounts
are not modified. Docker inherits the caller's selected context.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import uuid


CREATE = '''
import json, sys
from uuid import UUID
from contextlib import redirect_stdout
with redirect_stdout(sys.stderr):
    from onyx.db.engine.sql_engine import SqlEngine, get_session_with_current_tenant
    from onyx.db.api_key import insert_api_key
    from onyx.server.api_key.models import APIKeyArgs
    from onyx.auth.schemas import UserRole
    from onyx.db.models import User
    SqlEngine.init_engine(pool_size=1, max_overflow=0)
    with get_session_with_current_tenant() as session:
        key = insert_api_key(session, APIKeyArgs(name=sys.argv[1], role=UserRole.ADMIN), None)
        user = session.get(User, UUID(str(key.user_id)))
        assert user is not None
        user.use_memories = False
        user.enable_memory_tool = False
        session.commit()
print('ONYX_REORG_KEY=' + key.model_dump_json())
'''

REVOKE = '''
import json, sys
from sqlalchemy import select
from onyx.db.engine.sql_engine import SqlEngine, get_session_with_current_tenant
from onyx.db.api_key import remove_api_key
from onyx.db.models import ApiKey, Connector, Credential, ConnectorCredentialPair, Document, DocumentSet, Persona, ChatSession
SqlEngine.init_engine(pool_size=1, max_overflow=0)
with get_session_with_current_tenant() as session:
    key = session.get(ApiKey, int(sys.argv[1]))
    assert key is not None, 'Temporary key missing'
    assert key.name == sys.argv[2] and str(key.user_id) == sys.argv[3], 'Key ownership mismatch'
    assert key.name.startswith('onyx-reorg-validation-'), 'Not a test-owned key'
    if len(sys.argv) > 4:
        fixture = json.loads(sys.argv[4])
        assert fixture['fixture_name'].startswith('onyx-reorg-')
        assert fixture['url'] == 'http://doc-drop-web:8091/' + fixture['fixture_name'] + '/fixture.pdf'
        assert fixture['api_cleanup_complete']
        for model, field in ((Connector, 'connector_id'), (Credential, 'credential_id'), (DocumentSet, 'document_set_id'), (Document, 'url')):
            assert session.get(model, fixture[field]) is None, 'Native fixture cleanup is still pending: ' + field
        assert session.scalar(select(ConnectorCredentialPair).where(ConnectorCredentialPair.id == fixture['cc_pair_id'])) is None
        from onyx.db.search_settings import get_active_search_settings
        from onyx.document_index.factory import get_default_document_index
        from onyx.document_index.interfaces_new import DocumentSectionRequest
        from onyx.context.search.models import IndexFilters
        active = get_active_search_settings(session)
        index = get_default_document_index(active.primary, active.secondary, session)
        assert not index.id_based_retrieval([DocumentSectionRequest(document_id=fixture['url'])], IndexFilters(access_control_list=None)), 'Synthetic index chunks remain'
        # The public persona DELETE only creates a tombstone. Purge precisely
        # this private fixture after its chats and source set are gone.
        persona = session.get(Persona, fixture['persona_id'])
        if persona is not None:
            assert persona.name == fixture['fixture_name'] and persona.user_id == key.user_id
            assert persona.deleted and not persona.builtin_persona and not persona.is_public
            assert session.scalar(select(ChatSession.id).where(ChatSession.persona_id == persona.id).limit(1)) is None
            session.delete(persona)
            session.flush()
    remove_api_key(session, key.id)
    assert session.get(ApiKey, int(sys.argv[1])) is None
print('ONYX_REORG_REVOKED')
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "revoke"))
    parser.add_argument("credential_file", type=Path)
    parser.add_argument("--container", default="onyx-api_server-1")
    parser.add_argument("--container-bin", choices=("docker", "podman"), default="docker")
    parser.add_argument("--fixture-manifest", type=Path,
                        help="verify native RAG cleanup and purge only its deleted private persona before revocation")
    args = parser.parse_args()
    if args.action == "create":
        # Reserve the destination before committing any database mutation.
        fd = os.open(args.credential_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            name = "onyx-reorg-validation-" + uuid.uuid4().hex
            print("Temporary credential name:", name, flush=True)
            result = subprocess.run([args.container_bin, "exec", args.container, "python", "-c", CREATE, name], capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError("Native key creation failed; inspect API startup diagnostics and resolve the printed test name before retrying")
            rows = [line.removeprefix("ONYX_REORG_KEY=") for line in result.stdout.splitlines() if line.startswith("ONYX_REORG_KEY=")]
            if len(rows) != 1:
                raise RuntimeError("Missing credential result; resolve the printed test name before retrying")
            json.dump(json.loads(rows[0]), output)
        print("Temporary admin key saved to owner-only credential file")
    else:
        key = json.loads(args.credential_file.read_text())
        command = [args.container_bin, "exec", args.container, "python", "-c", REVOKE,
                   str(key["api_key_id"]), key["api_key_name"], key["user_id"]]
        if args.fixture_manifest:
            fixture = json.loads(args.fixture_manifest.read_text())
            # Exclude the private host source path and unrelated local metadata.
            command.append(json.dumps({name: fixture[name] for name in (
                "fixture_name", "url", "api_cleanup_complete", "connector_id",
                "credential_id", "cc_pair_id", "document_set_id", "persona_id",
            )}))
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode or "ONYX_REORG_REVOKED" not in result.stdout:
            raise RuntimeError("Native key revocation failed; credential file retained for cleanup")
        args.credential_file.unlink()
        print("Temporary admin key/account revoked and local secret removed")


if __name__ == "__main__":
    main()
