"""Opt-in live test, run on stdin inside api_server; only test-owned files/sessions.

Uses the real Onyx client and configured controller. No saved chats or private
workspaces are read. The caller supplies the controller IP for literal-IP denial.
"""
import json
import os
import shlex
import sys
from onyx.tools.tool_implementations.python.code_interpreter_client import (
    CodeInterpreterClient, StreamResultEvent,
)


def main():
    controller_ip=sys.argv[1]
    file_ids=set()
    session=None
    with CodeInterpreterClient() as client:
        assert client.health().healthy
        try:
            uploaded=client.upload_file(b'private-onyx-isolation-fixture\n','fixture.txt')
            file_ids.add(uploaded)
            assert client.download_file(uploaded)==b'private-onyx-isolation-fixture\n'
            files=[{'path':'fixture.txt','file_id':uploaded}]
            response=client.execute("""from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
assert Path('fixture.txt').read_text() == 'private-onyx-isolation-fixture\\n'
plt.plot([0, 1], [0, 1])
plt.savefig('fixture.png')
plt.close()
print(6 * 7)
_ = Path('result.txt').write_text('42')
""", files=files)
            file_ids.update(item.file_id for item in response.files if item.file_id)
            assert response.exit_code==0 and response.stdout.strip()=='42',response
            for item in response.files:
                if item.path=='result.txt': assert client.download_file(item.file_id)==b'42'
                if item.path=='fixture.png': assert client.download_file(item.file_id).startswith(b'\x89PNG\r\n\x1a\n')
            streamed=list(client.execute_streaming("print('stream-fixture')",timeout_ms=30000))
            results=[item for item in streamed if isinstance(item,StreamResultEvent)]
            assert len(results)==1 and results[0].exit_code==0
            file_ids.update(item.file_id for result in results for item in result.files if item.file_id)
            session=client.create_session(ttl_seconds=120,files=files).session_id
            check=client.execute_bash_in_session(session,"test -f fixture.txt && printf 'bash-fixture'")
            assert check.exit_code==0 and check.stdout=='bash-fixture'
            probe='''import json,os,socket
result={}
for host in ['code-interpreter',CONTROLLER_IP]:
 try:
  s=socket.create_connection((host,8000),timeout=1);s.close();result[host]='connected'
 except OSError: result[host]='denied'
assert all(v=='denied' for v in result.values()),result
print(json.dumps({'controller_denial':result,'proxy_keys':sorted(k for k in os.environ if k.lower() in ('http_proxy','https_proxy','all_proxy','no_proxy'))}))
'''.replace('CONTROLLER_IP',repr(controller_ip))
            check=client.execute_bash_in_session(session,'python -c '+shlex.quote(probe))
            assert check.exit_code==0,check.stderr
            print(check.stdout.strip())
            print('CONTROLLER_CLIENT_INTEGRATION_OK')
        finally:
            if session: client.delete_session(session)
            for file_id in file_ids: client.delete_file(file_id)

if __name__=='__main__': main()
