"""Opt-in stock-WebUI test with fault-injected inference packets and saved history.

Run in the API container with PYTHONPATH cleared and an approved disposable
browser user's owner-only credential file. A separate patched Python process
injects provider failures into its own LLM instance, translates them with Onyx,
and saves only test-owned messages using native DB helpers. Playwright replaces
only this user's test send response and releases its packets incrementally.
This tests the real browser reducer/CSP and DB reload, not live provider faults
or the full HTTP send/persistence pipeline. No running application is patched.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import sys


def fixture_worker():
    from uuid import UUID
    from litellm.exceptions import Timeout
    from onyx.chat.chat_state import ChatStateContainer
    from onyx.chat.llm_step import run_llm_step_pkt_generator
    from onyx.configs.constants import MessageType
    from onyx.db.chat import create_new_chat_message, get_or_create_root_message
    from onyx.db.engine.sql_engine import SqlEngine, get_session_with_current_tenant
    from onyx.db.models import ChatSession, User
    from onyx.llm import model_response
    from onyx.llm.model_response import Delta, ModelResponseStream, StreamingChoice
    from onyx.llm.models import ToolChoiceOptions
    from onyx.llm.multi_llm import LitellmLLM
    from onyx.server.query_and_chat.streaming_models import Placement, OverallStop, Packet

    request = json.load(sys.stdin)
    case = request['case']
    assert case in ('progress', 'exhausted', 'reasoning', 'finalization', 'tool_text')
    assert getattr(LitellmLLM.stream, '_wrapper_midstream_continuation', False)
    llm = LitellmLLM(api_key=None, model_provider='wrapper-contract-provider',
                    model_name='wrapper-contract-model', max_input_tokens=4096, timeout=1)
    calls = 0

    def chunk(content=None, reasoning=None, finish=None):
        return ModelResponseStream(id='browser-contract', created='now',
            choice=StreamingChoice(finish_reason=finish,
                                   delta=Delta(content=content, reasoning_content=reasoning)))

    def completion(**kwargs):
        nonlocal calls
        calls += 1
        attempt = calls

        def stream():
            if case == 'reasoning':
                yield chunk(reasoning='Synthetic reasoning segment. ')
                if attempt == 1:
                    raise Timeout('synthetic interruption', model='fixture', llm_provider='fixture')
                yield chunk('Amber otter completed after reasoning recovery.', finish='stop')
                return
            if case == 'exhausted' and attempt > 1:
                raise Timeout('synthetic interruption', model='fixture', llm_provider='fixture')
            text = f'Amber otter segment {attempt}. '
            if case == 'tool_text':
                text += '\n\n```xml\n<tool_call>{"name": "broken", "arguments":\n```\n'
            yield chunk(text, finish='stop' if case == 'finalization' else None)
            if case == 'finalization':
                raise ValueError('synthetic finalizer failure')
            if attempt < (4 if case == 'progress' else 2):
                raise Timeout('synthetic interruption', model='fixture', llm_provider='fixture')
            yield chunk('Finished synthetic response.', finish='stop')
        return stream()

    llm._completion = completion
    original = model_response.from_litellm_model_response_stream
    model_response.from_litellm_model_response_stream = lambda value: value
    state = ChatStateContainer()
    packets = []
    try:
        gen = run_llm_step_pkt_generator(history=[], tool_definitions=[],
            tool_choice=ToolChoiceOptions.AUTO, llm=llm, placement=Placement(turn_index=0),
            state_container=state, citation_processor=None)
        while True:
            try:
                packets.append(next(gen).model_dump(mode='json'))
            except StopIteration as stop:
                result, _ = stop.value
                break
    finally:
        model_response.from_litellm_model_response_stream = original
    kinds = [p['obj']['type'] for p in packets]
    assert kinds.count('message_start') == 1 and 'error' not in kinds
    assert state.get_answer_tokens() == result.answer
    assert calls == {'progress': 4, 'exhausted': 3, 'reasoning': 2,
                     'finalization': 1, 'tool_text': 2}[case], calls
    packets.append(Packet(placement=Placement(turn_index=0), obj=OverallStop()).model_dump(mode='json'))
    SqlEngine.init_engine(pool_size=1, max_overflow=0)
    with get_session_with_current_tenant() as db:
        user = db.get(User, UUID(request['user_id']))
        session = db.get(ChatSession, UUID(request['session_id']))
        assert user and user.email.startswith('onyx-browser-validation-') and not user.is_superuser
        assert session and session.user_id == user.id
        assert session.description == 'webui-inference-contract-' + case
        root = get_or_create_root_message(session.id, db)
        assert root.latest_child_message_id is None, 'fixture session must be empty'
        human = create_new_chat_message(session.id, root, request['message'], 10, MessageType.USER, db)
        answer = create_new_chat_message(session.id, human, result.answer, 100,
                                        MessageType.ASSISTANT, db, reasoning_tokens=result.reasoning)
        ids = {'user_message_id': human.id, 'reserved_assistant_message_id': answer.id}
    print('WEBUI_FIXTURE=' + json.dumps({'packets': [ids, *packets], 'answer': result.answer}))


# Install before the companion; delay only explicitly marked synthetic sends.
DELAY_STREAM = """(() => {
 const original = window.fetch;
 window.fetch = async function(input, init) {
  const response = await original.apply(this, arguments);
  if (!response.headers.get('x-private-onyx-test-stream')) return response;
  const lines = (await response.text()).trim().split('\\n');
  window.__fixtureProgress = 0;
  const body = new ReadableStream({async start(controller) {
   for (const line of lines) {
    controller.enqueue(new TextEncoder().encode(line + '\\n'));
    window.__fixtureProgress++;
    await new Promise(resolve => setTimeout(resolve, 250));
   }
   controller.close();
   window.__fixtureDone = true;
  }});
  return new Response(body, {status: response.status, headers: response.headers});
 };
})();"""


def browser_check(credential_file):
    from playwright.sync_api import sync_playwright
    auth = json.loads(credential_file.read_text())
    assert auth['email'].startswith('onyx-browser-validation-')
    sessions = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
        context = browser.new_context()
        context.add_init_script(DELAY_STREAM)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def api(method, path, **kwargs):
            response = context.request.fetch('http://nginx/api' + path, method=method, **kwargs)
            assert response.ok, (path, response.status)
            return response

        try:
            page.goto('http://nginx/auth/login', wait_until='networkidle')
            page.locator('input[name="email"]').fill(auth['email'])
            page.locator('input[name="password"]').fill(auth['password'])
            page.locator('button[type="submit"]').click()
            page.wait_for_url('**/app**', timeout=60000)
            assert api('GET', '/me').json()['id'] == auth['user_id']
            for case in ('progress', 'exhausted', 'reasoning', 'finalization', 'tool_text'):
                session_id = api('POST', '/chat/create-chat-session', data={
                    'description': 'webui-inference-contract-' + case,
                }).json()['chat_session_id']
                sessions.append(session_id)
                fixture = {}
                sends = []

                def intercept(route):
                    payload = route.request.post_data_json
                    assert payload['chat_session_id'] == session_id
                    sends.append(payload)
                    assert len(sends) == 1, 'unexpected automatic chat resubmission'
                    result = subprocess.run([sys.executable, __file__, '--fixture-worker'],
                        input=json.dumps({'case': case, 'session_id': session_id,
                                          'user_id': auth['user_id'], 'message': payload['message']}),
                        env={**os.environ, 'PYTHONPATH': '/app/wrapper-patches-api:/app/obscura-client:/app'},
                        capture_output=True, text=True, timeout=90)
                    assert result.returncode == 0, result.stderr[-3000:]
                    rows = [line.removeprefix('WEBUI_FIXTURE=') for line in result.stdout.splitlines()
                            if line.startswith('WEBUI_FIXTURE=')]
                    assert len(rows) == 1
                    fixture.update(json.loads(rows[0]))
                    route.fulfill(status=200, headers={'content-type': 'application/json',
                        'x-private-onyx-test-stream': '1'},
                        body=''.join(json.dumps(p) + '\n' for p in fixture['packets']))

                page.route('**/api/chat/send-chat-message', intercept)
                page.goto('http://nginx/app?chatId=' + session_id, wait_until='networkidle')
                name_prompt = page.get_by_role('group', name='non-admin-name-prompt')
                if name_prompt.count():
                    name_prompt.locator('input').fill('Browser validation')
                    name_prompt.get_by_role('button', name='Save', exact=True).click()
                textbox = page.locator('[contenteditable="true"][role="textbox"]').first
                textbox.fill('Synthetic inference continuation rendering test: ' + case)
                textbox.press('Enter')
                page.wait_for_function('window.__fixtureProgress > 0', timeout=90000)
                seen_partial = False
                while not page.evaluate('window.__fixtureDone === true'):
                    text = page.locator('body').inner_text()
                    if 'Amber otter' in text:
                        seen_partial = True
                    if seen_partial:
                        assert 'Amber otter' in text, 'partial answer disappeared during continuation'
                    page.wait_for_timeout(80)
                assert seen_partial
                completed = page.get_by_test_id('onyx-ai-message').first
                completed.wait_for(state='visible', timeout=30000)
                live = completed.inner_text()
                assert 'Amber otter' in live
                if case == 'progress':
                    assert live.count('inference stream was interrupted') == 3
                    assert 'segment 4' in live
                if case == 'exhausted':
                    assert 'Recovery also failed' in live
                if case == 'finalization':
                    assert 'stream finalization then failed' in live
                if case == 'tool_text':
                    assert '<tool_call>' in live and '"broken"' in live
                saved = api('GET', '/chat/get-chat-session/' + session_id).json()
                answers = [m for m in saved['messages'] if m['message_type'] == 'assistant']
                assert len(answers) == 1 and answers[0]['message'] == fixture['answer']
                page.unroute('**/api/chat/send-chat-message', intercept)
                page.reload(wait_until='networkidle')
                completed = page.get_by_test_id('onyx-ai-message').first
                completed.wait_for(state='visible', timeout=30000)
                # Native history lacks the synthetic stream's browser-measured
                # duration; compare content without that one presentation label.
                content = lambda text: re.sub(r'^Thought for [^\n]+\n', '', text)
                assert content(completed.inner_text()) == content(live), (case, 'saved reload differs from live rendering')
                if case == 'reasoning':
                    assert 'reasoning stream was interrupted' in answers[0]['reasoning_tokens']
                if case == 'exhausted':
                    textbox = page.locator('[contenteditable="true"][role="textbox"]').first
                    textbox.fill('Reply only with recovery followup ready. Do not use tools or save memories.')
                    textbox.press('Enter')
                    followup = page.get_by_test_id('onyx-ai-message').nth(1)
                    followup.wait_for(state='visible', timeout=240000)
                    assert 'recovery followup ready' in followup.inner_text().lower()
                    print('WEBUI_REAL_FOLLOWUP_AFTER_EXHAUSTED_RECOVERY_OK', flush=True)
                assert not errors, errors
                print('WEBUI_INFERENCE_RECOVERY_OK', case, flush=True)
        finally:
            for session_id in sessions:
                api('DELETE', '/chat/delete-chat-session/' + session_id + '?hard_delete=true')
            browser.close()
            print('Removed synthetic inference fixture sessions', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture-worker', action='store_true')
    parser.add_argument('--credential-file', type=Path)
    args = parser.parse_args()
    if args.fixture_worker:
        fixture_worker()
    else:
        assert args.credential_file is not None
        browser_check(args.credential_file)
