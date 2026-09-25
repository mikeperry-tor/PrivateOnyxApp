"""Opt-in deterministic browser recovery and upload validation using an approved disposable user.

Run in the API image with PYTHONPATH cleared and an owner-only JSON credential
file containing email/password/user_id. The account must have no existing data.
Only this test's sessions and uploads are deleted; user revocation is separate.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright, expect

from webui_recovery_fixture import ANSWER, CASES, PARAGRAPHS, fixture_api, wait_until


def validate(credential_file: Path) -> None:
    auth = json.loads(credential_file.read_text())
    assert auth['email'].startswith('onyx-browser-validation-')
    sessions, files = set(), set()
    with fixture_api() as (fixture_origin, diagnostics), sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
        context = browser.new_context(proxy={'server': fixture_origin})
        page = context.new_page()
        case_requests = []
        resume_responses = []
        page.on('response', lambda response: resume_responses.append(response.status)
                if '/resume-stream' in response.url else None)
        page.on('request', lambda request: case_requests.append(request.url)
                if 'reconnect-status/' in request.url or '/resume-stream' in request.url else None)

        def api(method, path, **kwargs):
            response = requests.fetch('http://nginx/api' + path, method=method, **kwargs)
            assert response.ok, (method, path, response.status)
            return response

        try:
            page.goto('http://nginx/auth/login', wait_until='networkidle')
            page.locator('input[name="email"]').fill(auth['email'])
            page.locator('input[name="password"]').fill(auth['password'])
            page.locator('button[type="submit"]').click()
            page.wait_for_url('**/app**', timeout=60000)
            page.wait_for_load_state('networkidle')
            requests = playwright.request.new_context(storage_state=context.storage_state())
            assert api('GET', '/me').json()['id'] == auth['user_id']
            assert api('GET', '/chat/get-user-chat-sessions').json()['sessions'] == []
            name_prompt = page.get_by_role('group', name='non-admin-name-prompt')
            if name_prompt.count():
                name_prompt.locator('input').fill('Browser validation')
                name_prompt.get_by_role('button', name='Save', exact=True).click()
            print('AUTHENTICATED_WEBUI_LOGIN_OK', flush=True)

            def route_chat(route):
                payload = route.request.post_data_json if route.request.method == 'POST' else None
                if route.request.url.endswith('/send-chat-message'):
                    assert payload['chat_session_id'] in sessions
                    payload['allowed_tool_ids'] = []
                    sends.append(payload['chat_session_id'])
                route.continue_(post_data=json.dumps(payload) if payload is not None else None)

            page.route('http://nginx/api/chat/**', route_chat)
            for case in CASES:
                session_id = api('POST', '/chat/create-chat-session', data={
                    'description': 'wrapper-recovery-' + case,
                }).json()['chat_session_id']
                sessions.add(session_id)
                sends = []
                case_requests.clear()
                resume_responses.clear()
                page.goto('http://nginx/app?chatId=' + session_id, wait_until='networkidle')
                tick = lambda seconds: page.wait_for_timeout(seconds * 1000)
                try:
                    textbox = page.locator('[contenteditable="true"][role="textbox"]:visible').first
                    textbox.fill('wrapper-recovery-' + case)
                    textbox.press('Enter')
                    wait_until(lambda: (diagnostics / (case + '.paused')).exists(), tick=tick)
                    # Prove the browser received partial content before cutting
                    # transport; completion is held by an explicit gate.
                    expect(page.get_by_text(PARAGRAPHS[0], exact=True)).to_be_visible(timeout=30000)
                    status = api('GET', '/chat/reconnect-status/' + session_id).json()
                    assert status['current_run'] and status['resumable'], status
                    case_requests.clear()
                    context.set_offline(True)
                    page.wait_for_function('navigator.onLine === false')
                    # Chromium offline emulation can leave existing sockets alive.
                    # Sever the actual send connection before provider completion.
                    (diagnostics / (case + '.disconnect')).touch()
                    wait_until(lambda: (diagnostics / (case + '.disconnected')).exists(), tick=tick)
                    if case == 'complete_offline':
                        (diagnostics / (case + '.release')).touch()
                        wait_until(lambda: not api('GET', '/chat/reconnect-status/' + session_id).json()['current_run'],
                                   timeout=60, tick=tick)
                        expect(page.get_by_text(PARAGRAPHS[-1], exact=True)).not_to_be_visible()
                    context.set_offline(False)
                    wait_until(lambda: any('reconnect-status/' in url for url in case_requests), tick=tick)
                    if case == 'resume_running':
                        wait_until(lambda: 200 in resume_responses, tick=tick)
                        expect(page.get_by_text(PARAGRAPHS[0], exact=True)).to_be_visible()
                        (diagnostics / (case + '.release')).touch()
                    wait_until(lambda: not api('GET', '/chat/reconnect-status/' + session_id).json()['current_run'],
                               timeout=60, tick=tick)
                    completed = page.get_by_test_id('onyx-ai-message')
                    expect(completed).to_have_count(1, timeout=60000)
                    expect(completed.locator('p')).to_have_text(list(PARAGRAPHS), timeout=60000)
                    assert any('reconnect-status/' in url for url in case_requests)
                    assert sends == [session_id], 'Recovery sent the prompt again'
                    saved = api('GET', '/chat/get-chat-session/' + session_id).json()
                    assistants = [m for m in saved['messages'] if m['message_type'] == 'assistant']
                    assert len(assistants) == 1 and not assistants[0]['error']
                    assert assistants[0]['message'] == ANSWER
                    page.reload(wait_until='networkidle')
                    expect(page.get_by_test_id('onyx-ai-message').locator('p')).to_have_text(list(PARAGRAPHS))
                    print('AUTHENTICATED_DETERMINISTIC_RECOVERY_OK', case, flush=True)
                except BaseException:
                    context.set_offline(False)
                    evidence = {'case': case, 'requests': case_requests, 'sends': sends,
                                'resume_responses': resume_responses,
                                'body': page.locator('body').inner_text(),
                                'rendered': page.get_by_test_id('onyx-ai-message').all_text_contents(),
                                'status': api('GET', '/chat/reconnect-status/' + session_id).json(),
                                'saved': api('GET', '/chat/get-chat-session/' + session_id).json()}
                    (diagnostics / (case + '.json')).write_text(json.dumps(evidence, indent=2))
                    raise
            page.unroute('http://nginx/api/chat/**', route_chat)

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
                response = requests.get('http://nginx' + path)
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
