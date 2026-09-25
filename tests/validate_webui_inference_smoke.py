"""Opt-in small live-model smoke, separate from deterministic recovery gates.

Run inside the API container with PYTHONPATH cleared and --credential-file
pointing to the approved disposable browser user's owner-only JSON file.
Checks one real configured-provider answer, persistence and browser reload;
model wording, formatting and item counts are deliberately unconstrained.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from playwright.sync_api import sync_playwright, expect

from webui_recovery_fixture import wait_until


def validate(credential_file: Path):
    auth = json.loads(credential_file.read_text())
    assert auth['email'].startswith('onyx-browser-validation-')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
        context = browser.new_context()
        page = context.new_page()
        session_id = None

        def api(method, path, **kwargs):
            response = context.request.fetch('http://nginx/api' + path, method=method, **kwargs)
            assert response.ok, (path, response.status)
            return response.json()

        try:
            page.goto('http://nginx/auth/login', wait_until='networkidle')
            page.locator('input[name="email"]').fill(auth['email'])
            page.locator('input[name="password"]').fill(auth['password'])
            page.locator('button[type="submit"]').click()
            page.wait_for_url('**/app**', timeout=60000)
            assert api('GET', '/me')['id'] == auth['user_id']
            name_prompt = page.get_by_role('group', name='non-admin-name-prompt')
            if name_prompt.count():
                name_prompt.locator('input').fill('Browser validation')
                name_prompt.get_by_role('button', name='Save', exact=True).click()
            session_id = api('POST', '/chat/create-chat-session', data={
                'description': 'wrapper-live-inference-smoke',
            })['chat_session_id']
            page.goto('http://nginx/app?chatId=' + session_id, wait_until='networkidle')
            textbox = page.locator('[contenteditable="true"][role="textbox"]').first
            textbox.fill('Briefly describe an otter in one sentence. Do not use tools or save memories.')
            textbox.press('Enter')
            completed = page.get_by_test_id('onyx-ai-message')
            expect(completed).to_have_count(1, timeout=240000)
            wait_until(lambda: settled(api, session_id), timeout=240,
                       tick=lambda seconds: page.wait_for_timeout(seconds * 1000))
            answers = assistants(api('GET', '/chat/get-chat-session/' + session_id))
            assert len(answers) == 1 and not answers[0]['error'] and answers[0]['message'].strip()
            assert completed.inner_text().strip(), 'Empty rendered answer'
            page.reload(wait_until='networkidle')
            expect(page.get_by_test_id('onyx-ai-message')).to_have_count(1, timeout=30000)
            reloaded = assistants(api('GET', '/chat/get-chat-session/' + session_id))
            assert reloaded[0]['message'] == answers[0]['message']
            assert page.get_by_test_id('onyx-ai-message').inner_text().strip()
            print('WEBUI_LIVE_INFERENCE_SMOKE_OK', flush=True)
        except BaseException:
            if session_id:
                directory = Path(tempfile.mkdtemp(prefix='onyx-live-inference-smoke-'))
                (directory / 'saved.json').write_text(json.dumps(
                    api('GET', '/chat/get-chat-session/' + session_id), indent=2))
                print(f'Live inference diagnostics: {directory}', flush=True)
            raise
        finally:
            if session_id:
                api('POST', '/chat/stop-chat-session/' + session_id)
                api('DELETE', '/chat/delete-chat-session/' + session_id + '?hard_delete=true')
            browser.close()


def assistants(saved):
    return [message for message in saved['messages'] if message['message_type'] == 'assistant']


def settled(api, session_id):
    status = api('GET', '/chat/reconnect-status/' + session_id)
    return not status['current_run'] and not status['pending_reservation']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credential-file', type=Path, required=True)
    validate(parser.parse_args().credential_file)
