"""Opt-in browser recovery and upload smoke using an approved disposable user.

Run in the API image with PYTHONPATH cleared and an owner-only JSON credential
file containing email/password/user_id. The account must have no existing data.
Only this test's sessions and uploads are deleted; user revocation is separate.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import time
from urllib.parse import quote

from playwright.sync_api import sync_playwright


def validate(credential_file: Path) -> None:
    auth = json.loads(credential_file.read_text())
    assert auth['email'].startswith('onyx-browser-validation-')
    sessions, files = set(), set()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
        context = browser.new_context()
        page = context.new_page()
        recovery_requests = []
        page.on('request', lambda request: recovery_requests.append(request.url)
                if 'reconnect-status/' in request.url or '/resume-stream' in request.url else None)

        def api(method, path, **kwargs):
            response = context.request.fetch('http://nginx/api' + path, method=method, **kwargs)
            assert response.ok, (method, path, response.status)
            return response

        try:
            page.goto('http://nginx/auth/login', wait_until='networkidle')
            page.locator('input[name="email"]').fill(auth['email'])
            page.locator('input[name="password"]').fill(auth['password'])
            page.locator('button[type="submit"]').click()
            page.wait_for_url('**/app**', timeout=60000)
            page.wait_for_load_state('networkidle')
            assert api('GET', '/me').json()['id'] == auth['user_id']
            assert api('GET', '/chat/get-user-chat-sessions').json()['sessions'] == []
            name_prompt = page.get_by_role('group', name='non-admin-name-prompt')
            if name_prompt.count():
                name_prompt.locator('input').fill('Browser validation')
                name_prompt.get_by_role('button', name='Save', exact=True).click()
            print('AUTHENTICATED_WEBUI_LOGIN_OK', flush=True)

            textbox = page.locator('[contenteditable="true"][role="textbox"]').first
            textbox.fill('For a synthetic connection recovery test, write exactly 100 Markdown numbered list items. The text of each item must be amber otter recovery. Do not use tools or save memories. No introduction or conclusion.')
            with page.expect_request('**/api/chat/send-chat-message') as sent:
                textbox.press('Enter')
            session_id = sent.value.post_data_json['chat_session_id']
            sessions.add(session_id)
            # Interrupt a real reserved/running turn, not an already finished page.
            deadline = time.monotonic() + 30
            while True:
                status = api('GET', '/chat/reconnect-status/' + session_id).json()
                if status['current_run'] or status['pending_reservation']:
                    break
                assert time.monotonic() < deadline, status
                page.wait_for_timeout(200)
            context.set_offline(True)
            page.wait_for_timeout(2000)
            context.set_offline(False)
            completed = page.get_by_test_id('onyx-ai-message').first
            completed.wait_for(state='visible', timeout=240000)
            rendered = completed.inner_text()
            assert rendered.lower().count('amber otter recovery') == 100, 'incomplete or duplicated rendered response'
            assert completed.locator('li').count() == 100
            assert recovery_requests, 'no status or resume request after interruption'
            saved = api('GET', '/chat/get-chat-session/' + session_id).json()
            assistants = [message for message in saved['messages'] if message['message_type'] == 'assistant']
            assert len(assistants) == 1, len(assistants)
            assert assistants[0]['message'].lower().count('amber otter recovery') == 100
            page.reload(wait_until='networkidle')
            completed = page.get_by_test_id('onyx-ai-message').first
            completed.wait_for(state='visible', timeout=30000)
            assert completed.inner_text().lower().count('amber otter recovery') == 100
            print('AUTHENTICATED_STREAM_OFFLINE_RECOVERY_AND_RELOAD_OK', flush=True)

            png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
            for name, media, content in (
                ('validation.png', 'image/png', png),
                ('validation.svg', 'image/svg+xml', b'<svg xmlns="http://www.w3.org/2000/svg"><text>synthetic upload</text></svg>'),
            ):
                uploaded = api('POST', '/user/projects/file/upload', multipart={
                    'files': {'name': name, 'mimeType': media, 'buffer': content},
                }).json()
                assert not uploaded['rejected_files'], uploaded['rejected_files']
                assert len(uploaded['user_files']) == 1
                file = uploaded['user_files'][0]
                files.add(file['id'])
                path = '/api/chat/file/' + quote(file['file_id'], safe='')
                response = context.request.get('http://nginx' + path)
                assert response.status == 200
                assert response.body() == content
                assert response.headers['x-content-type-options'] == 'nosniff'
                if media == 'image/png':
                    assert response.headers['content-type'].startswith('image/png')
                    loaded = page.evaluate('''path => new Promise(resolve => {
                        const image = new Image();
                        image.onload = () => { image.remove(); resolve(true); };
                        image.onerror = () => { image.remove(); resolve(false); };
                        image.src = path; document.body.appendChild(image);
                    })''', path)
                    assert loaded, 'same-origin uploaded image did not render'
                else:
                    assert response.headers['content-type'].startswith('application/octet-stream')
                    assert response.headers['content-disposition'] == 'attachment'
                    assert 'sandbox' in response.headers['content-security-policy']
            print('AUTHENTICATED_UPLOAD_STORAGE_AND_CSP_OK', flush=True)
        finally:
            context.set_offline(False)
            for session in sessions:
                api('POST', '/chat/stop-chat-session/' + session)
                api('DELETE', '/chat/delete-chat-session/' + session + '?hard_delete=true')
            for file_id in files:
                api('DELETE', '/user/projects/file/' + file_id)
            browser.close()
            print('Removed test chat sessions and queued test-upload deletion', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credential-file', type=Path, required=True)
    validate(parser.parse_args().credential_file)
