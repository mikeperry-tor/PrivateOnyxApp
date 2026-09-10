"""Installed-loop captures at the translated Onyx LLM boundary, without providers."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Barrier, Lock
from types import SimpleNamespace
from unittest.mock import patch

import wrapper_env_patches as patches
from onyx.chat import llm_loop
from onyx.chat.chat_state import ChatStateContainer
from onyx.chat.models import ChatMessageSimple, ExtractedContextFiles
from onyx.configs.constants import MessageType, DocumentSource
from onyx.context.search.models import SearchDoc, SearchDocsResponse
from onyx.llm.model_response import (
    ModelResponseStream, StreamingChoice, Delta, ChatCompletionDeltaToolCall, FunctionCall,
)
from onyx.tools.models import ToolResponse, ParallelToolCallResponse, ToolCallKickoff
from onyx.tools.tool_implementations.python.python_tool import PythonTool
from onyx.tools.tool_implementations.web_search.web_search_tool import WebSearchTool
from onyx.tools.tool_implementations.open_url.open_url_tool import OpenURLTool
from onyx.prompts.chat_prompts import (
    DEFAULT_SYSTEM_PROMPT, REQUIRE_CITATION_GUIDANCE, CITATION_REMINDER, FILE_REMINDER,
    OPEN_URL_REMINDER,
)
from onyx.server.query_and_chat.placement import Placement

ARTIFACT = '[research.csv](/api/chat/file/11111111-1111-4111-8111-111111111111)'


def structured(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json', exclude_none=True)
    raise TypeError(type(value))


class ScriptedLLM:
    config = SimpleNamespace(
        model_provider='wrapper-contract-provider', model_name='wrapper-contract-model',
        deployment_name=None, api_base=None, max_input_tokens=131072,
    )

    def __init__(self, steps):
        self.steps = iter(steps)
        self.requests = []

    def stream(self, **kwargs):
        # Serialize now: a live reference can mask mutations to earlier requests.
        from onyx.llm.multi_llm import _prompt_to_dicts
        serialized = dict(kwargs, prompt=_prompt_to_dicts(kwargs['prompt']))
        self.requests.append(json.loads(json.dumps(serialized, default=structured)))
        step = next(self.steps)
        if isinstance(step, (tuple, list)):
            batch = step if isinstance(step, list) else [step]
            call_id = f'call-{len(self.requests)}'
            yield ModelResponseStream(id=call_id, created='now', choice=StreamingChoice(
                delta=Delta(reasoning_content=f'evidence reasoning {call_id}')))
            delta = Delta(tool_calls=[ChatCompletionDeltaToolCall(
                id=call_id if len(batch) == 1 else f'{call_id}-{index}', index=index,
                function=FunctionCall(name=name, arguments=json.dumps(args)))
                for index, (name, args) in enumerate(batch)])
            finish = 'tool_calls'
        else:
            delta, finish = Delta(content=step), 'stop'
        yield ModelResponseStream(id='fixture', created='now', choice=StreamingChoice(
            delta=delta, finish_reason=finish))


def selected_tools():
    # Bypass provider discovery only; use the real installed tool classes and
    # their unmodified ordered definition builders throughout every capture.
    search = object.__new__(WebSearchTool)
    search._id = 1
    search._provider = SimpleNamespace(supports_site_filter=True)
    open_url = OpenURLTool(2, None, None, None, content_provider=SimpleNamespace())
    return [search, open_url, PythonTool(3, None)]


def user_history():
    return [ChatMessageSimple(message='Research this topic and create research.csv.',
                              token_count=12, message_type=MessageType.USER)]


def context_files(**changes):
    return ExtractedContextFiles(**dict(
        file_texts=[], image_files=[], use_as_search_filter=False,
        total_token_count=0, file_metadata=[], uncapped_token_count=None,
    ) | changes)


def tool_effects(*, tool_calls, **kwargs):
    responses = []
    mapping = dict(kwargs['citation_mapping'])
    for call in tool_calls:
        if call.tool_name == WebSearchTool.NAME:
            doc = SearchDoc(document_id='https://example.org/evidence', chunk_ind=0,
                            semantic_identifier='Evidence', link='https://example.org/evidence',
                            blurb='Search evidence', source_type=DocumentSource.WEB,
                            boost=1, hidden=False, metadata={}, match_highlights=[], is_internet=True)
            mapping[1] = doc.document_id
            rich = SearchDocsResponse(search_docs=[doc], citation_mapping=mapping)
            text = json.dumps({'documents': [{'document': 1, 'contents': 'Search evidence'}]})
        elif call.tool_name == PythonTool.NAME:
            rich = None
            text = json.dumps({'generated_files': [{'filename': 'research.csv',
                'file_link': '/api/chat/file/11111111-1111-4111-8111-111111111111',
                'response_markdown': ARTIFACT}]})
        else:
            raise AssertionError(f'unexpected external tool {call.tool_name}')
        responses.append(ToolResponse(rich_response=rich, llm_facing_response=text, tool_call=call))
    return ParallelToolCallResponse(tool_responses=responses, updated_citation_mapping=mapping)


def rendered(messages):
    return '\n'.join(m.get('content') or '' for m in messages)


def assert_python_execution_guidance(request):
    descriptions = [t['function']['description'] for t in request['tools']
                    if t['function']['name'] == PythonTool.NAME]
    assert len(descriptions) == 1
    assert patches._PYTHON_EXECUTION_GUIDANCE in descriptions[0]


def assert_prefix(first, second):
    assert first['prompt'] == second['prompt'][:len(first['prompt'])], 'translated message prefix changed'
    assert len(second['prompt']) > len(first['prompt'])
    assert {k: v for k, v in first.items() if k != 'prompt'} == {
        k: v for k, v in second.items() if k != 'prompt'
    }, 'ordered tool definitions or request options changed'
    calls = [m for m in second['prompt'] if m.get('tool_calls')]
    assert calls and all(m.get('reasoning_content') for m in calls), second['prompt']
    for message in calls:
        for call in message['tool_calls']:
            assert any(m.get('tool_call_id') == call['id'] for m in second['prompt'])


def main_chat(steps=None, base_prompt=DEFAULT_SYSTEM_PROMPT, **overrides):
    model = ScriptedLLM(steps or [
        (WebSearchTool.NAME, {'queries': ['evidence']}),
        (PythonTool.NAME, {'code': "print('fixture')"}),
        'Evidence [1]. ' + ARTIFACT,
    ])
    state, packets = ChatStateContainer(), []
    kwargs = dict(emitter=SimpleNamespace(emit=packets.append), state_container=state,
                  simple_chat_history=user_history(), tools=selected_tools(),
                  custom_agent_prompt=None, context_files=context_files(), persona=None,
                  user_memory_context=None, llm=model, token_counter=lambda s: len(s)//4)
    kwargs.update(overrides)
    with patch.object(llm_loop, 'get_session_with_current_tenant', lambda: nullcontext(None)), \
         patch.object(llm_loop, 'get_default_base_system_prompt', lambda _: base_prompt), \
         patch.object(llm_loop, 'run_tool_calls', tool_effects):
        llm_loop.run_llm_loop(**kwargs)
    return model.requests, state, packets


def validate_main_chat():
    requests, state, packets = main_chat()
    assert len(requests) == 3
    assert_prefix(requests[0], requests[1])
    assert_prefix(requests[1], requests[2])
    initial = rendered(requests[0]['prompt'])
    assert CITATION_REMINDER in initial
    assert REQUIRE_CITATION_GUIDANCE.strip() in initial
    assert 'snippets completely answer the query' in initial
    open_def = requests[0]['tools'][1]['function']
    assert open_def['parameters']['properties']['urls']['maxItems'] == 10
    for request in requests:
        text = rendered(request['prompt'])
        assert FILE_REMINDER not in text and OPEN_URL_REMINDER not in text
        reminders = [i for i, m in enumerate(request['prompt']) if CITATION_REMINDER in (m.get('content') or '')]
        assert len(reminders) == 1
        tool_messages = [i for i, m in enumerate(request['prompt']) if m.get('tool_calls')]
        assert not tool_messages or reminders[0] < min(tool_messages)
    result = next(m for m in requests[2]['prompt'] if m.get('tool_call_id') == 'call-2')
    assert json.loads(result['content'])['generated_files'][0]['response_markdown'] == ARTIFACT
    assert state.get_answer_tokens().count(ARTIFACT) == 1
    assert len(state.get_tool_calls()) == 2
    assert all(t.reasoning_tokens for t in state.get_tool_calls())
    assert any(p.obj.type == 'stop' for p in packets)

    # Fixed subset + task prompt retain the same policy and placement.
    persona = SimpleNamespace(replace_base_system_prompt=False, system_prompt=None,
                              task_prompt='Task: compare the evidence.', datetime_aware=False)
    requests, _, _ = main_chat(persona=persona, tools=selected_tools()[::2])
    for a, b in zip(requests, requests[1:]):
        assert_prefix(a, b)
    assert 'Task: compare the evidence.' in rendered(requests[0]['prompt'])
    assert 'snippets completely answer the query' not in rendered(requests[0]['prompt'])

    for overrides in ({'include_citations': False}, {'tools': [PythonTool(3, None)]}):
        requests, _, _ = main_chat(['Done'], **overrides)
        assert CITATION_REMINDER not in rendered(requests[0]['prompt'])
        assert REQUIRE_CITATION_GUIDANCE.strip() not in rendered(requests[0]['prompt'])
    requests, _, _ = main_chat(['Done'], tools=[], context_files=context_files(file_texts=['context']))
    assert CITATION_REMINDER in rendered(requests[0]['prompt'])
    persona.replace_base_system_prompt, persona.system_prompt = True, 'Replacement system'
    requests, _, _ = main_chat([(PythonTool.NAME, {'code': 'pass'}), 'Done'], persona=persona)
    for request in requests:
        system = request['prompt'][0]['content']
        assert 'Replacement system' in system and 'response_markdown' in system
        assert patches._PYTHON_EXECUTION_GUIDANCE in system
        assert_python_execution_guidance(request)
        assert 'snippets completely answer the query' not in system
        assert REQUIRE_CITATION_GUIDANCE.strip() not in system
        assert FILE_REMINDER not in rendered(request['prompt'])
    requests, _, _ = main_chat([(PythonTool.NAME, {'code': 'pass'}), 'Done'],
                                base_prompt='', custom_agent_prompt='Custom empty-base system')
    for request in requests:
        system = request['prompt'][0]['content']
        assert 'Custom empty-base system' in system and 'response_markdown' in system
        assert patches._PYTHON_EXECUTION_GUIDANCE in system
        assert_python_execution_guidance(request)
        assert 'response_markdown' in request['tools'][2]['function']['description']
        assert 'snippets completely answer the query' not in system
        assert FILE_REMINDER not in rendered(request['prompt'])
    requests, _, _ = main_chat(['Done'], base_prompt='',
                                custom_agent_prompt='Custom empty-base system', tools=[])
    assert requests[0]['prompt'][0]['content'].startswith('Custom empty-base system')
    assert 'response_markdown' not in rendered(requests[0]['prompt'])
    with patch.object(llm_loop, 'MAX_LLM_CYCLES', 2):
        requests, _, _ = main_chat([(PythonTool.NAME, {'code': 'pass'}), 'Done'])
    assert requests[1]['tools'] == []
    terminal = llm_loop.select_reminder_text(
        ran_image_gen=False, just_ran_web_search=False, has_open_url_tool=True,
        out_of_cycles=True, persona_task_prompt=None, include_citation_reminder=True,
        include_file_reminder=False)
    assert terminal in rendered(requests[1]['prompt'])
    assert FILE_REMINDER not in rendered(requests[1]['prompt'])
    from onyx.prompts.chat_prompts import IMAGE_GEN_REMINDER
    assert llm_loop.select_reminder_text(
        ran_image_gen=True, just_ran_web_search=False, has_open_url_tool=True,
        out_of_cycles=False, persona_task_prompt=None, include_citation_reminder=True,
        include_file_reminder=False) == IMAGE_GEN_REMINDER
    requests, _, _ = main_chat(forced_tool_id=1)
    assert len(requests[0]['tools']) == 1 and len(requests[1]['tools']) == 3
    assert_prefix(requests[1], requests[2])


def validate_research():
    from onyx.deep_research import dr_loop
    from onyx.tools.fake_tools import research_agent
    from onyx.prompts.deep_research.dr_tool_prompts import GENERATE_REPORT_TOOL_NAME
    from onyx.prompts.deep_research.orchestration_layer import FIRST_CYCLE_REMINDER
    from onyx.prompts.deep_research.research_agent import OPEN_URL_REMINDER_RESEARCH_AGENT

    model = ScriptedLLM([
        'Research plan', (dr_loop.RESEARCH_AGENT_TOOL_NAME, {research_agent.RESEARCH_AGENT_TASK_KEY: 'Research evidence'}),
        (GENERATE_REPORT_TOOL_NAME, {}),
    ])
    state, packets = ChatStateContainer(), []
    with patch.object(dr_loop, 'run_research_agent_calls', return_value=SimpleNamespace(
            citation_mapping={}, intermediate_reports=['Research evidence report'])), \
         patch.object(dr_loop, '_get_research_agent_tool_id', return_value=9), \
         patch.object(dr_loop, 'generate_final_report', return_value=False):
        dr_loop.run_deep_research_llm_loop(
            emitter=SimpleNamespace(emit=packets.append), state_container=state,
            simple_chat_history=user_history(), tools=selected_tools(), custom_agent_prompt=None,
            llm=model, token_counter=lambda s: len(s)//4, skip_clarification=True,
        )
    assert len(model.requests) == 3
    assert_prefix(model.requests[1], model.requests[2])
    assert all(r['max_tokens'] is None for r in model.requests[1:])
    assert FIRST_CYCLE_REMINDER not in rendered(model.requests[2]['prompt'])
    assert dr_loop.INTERNAL_SEARCH_RESEARCH_TASK_GUIDANCE not in rendered(model.requests[1]['prompt'])

    model = ScriptedLLM([
        (WebSearchTool.NAME, {'queries': ['evidence']}),
        (GENERATE_REPORT_TOOL_NAME, {}),
    ])
    state, packets = ChatStateContainer(), []
    with patch.object(research_agent, '_wrapper_run_tool_calls', tool_effects), \
         patch.object(research_agent, 'generate_intermediate_report', return_value='Report'):
        result = research_agent.run_research_agent_call(
            research_agent_call=ToolCallKickoff(tool_name=dr_loop.RESEARCH_AGENT_TOOL_NAME,
                tool_call_id='research', tool_args={research_agent.RESEARCH_AGENT_TASK_KEY: 'Research evidence'},
                placement=Placement(turn_index=1, tab_index=0)),
            parent_tool_call_id='parent', tools=selected_tools(),
            emitter=SimpleNamespace(emit=packets.append), state_container=state,
            llm=model, is_reasoning_model=True, token_counter=lambda s: len(s)//4, user_identity=None,
        )
    assert result is not None, packets
    assert len(model.requests) == 2
    for request in model.requests:
        assert_python_execution_guidance(request)
    assert_prefix(*model.requests)
    assert all(r['max_tokens'] is None for r in model.requests)
    assert OPEN_URL_REMINDER_RESEARCH_AGENT not in rendered(model.requests[1]['prompt'])
    assert 'snippets completely answer the query' in rendered(model.requests[0]['prompt'])
    assert 'open_urls' not in rendered(model.requests[0]['prompt'])


def validate_concurrent_batches():
    """Keep the installed merger and pools; stub only individual tool execution."""
    from onyx.tools import tool_runner
    from onyx.tools.fake_tools import research_agent
    from onyx.deep_research import dr_loop
    from onyx.prompts.deep_research.dr_tool_prompts import GENERATE_REPORT_TOOL_NAME

    def run_phase(nested):
        # Four invocations rendezvous inside the real tool pool. A global lock
        # around whole invocations would fail this bounded wait instead of
        # accidentally passing a sleep-based concurrency check.
        rendezvous = Barrier(4, timeout=30)
        lock = Lock()
        executed = {}

        def tool_result(tool, call, overrides):
            field = 'queries' if call.tool_name == WebSearchTool.NAME else 'urls'
            values = call.tool_args[field]
            label = values[0].split('/')[3]
            with lock:
                executed.setdefault(label, []).append((call.tool_name, list(values)))
            if call.tool_name == WebSearchTool.NAME and values[0].endswith('/first'):
                rendezvous.wait()
            number = overrides.starting_citation_num
            doc = SearchDoc(document_id=values[0], chunk_ind=0,
                            semantic_identifier=label, link=values[0], blurb=label,
                            source_type=DocumentSource.WEB, boost=1, hidden=False,
                            metadata={}, match_highlights=[], is_internet=True)
            return ToolResponse(tool_call=call,
                rich_response=SearchDocsResponse(search_docs=[doc], citation_mapping={number: values[0]}),
                llm_facing_response=json.dumps({'document': number, 'contents': values[0]}))

        def invoke(index):
            label = f'invocation-{index}'
            base = f'https://example.org/{label}'
            model = ScriptedLLM([
                [(WebSearchTool.NAME, {'queries': [base + '/first']}),
                 (WebSearchTool.NAME, {'queries': [base + '/second']}),
                 (OpenURLTool.NAME, {'urls': [base + '/page']})],
                (WebSearchTool.NAME, {'queries': [base + '/third']}),
                (GENERATE_REPORT_TOOL_NAME, {}) if nested else 'Done',
            ])
            state, packets = ChatStateContainer(), []
            emitter = SimpleNamespace(emit=packets.append)
            if nested:
                result = research_agent.run_research_agent_call(
                    research_agent_call=ToolCallKickoff(tool_name=dr_loop.RESEARCH_AGENT_TOOL_NAME,
                        tool_call_id=label, tool_args={research_agent.RESEARCH_AGENT_TASK_KEY: label},
                        placement=Placement(turn_index=1, tab_index=index)),
                    parent_tool_call_id=label, tools=selected_tools(), emitter=emitter,
                    state_container=state, llm=model, is_reasoning_model=True,
                    token_counter=lambda s: len(s)//4, user_identity=None)
                assert result is not None, packets
            else:
                llm_loop.run_llm_loop(emitter=emitter, state_container=state,
                    simple_chat_history=user_history(), tools=selected_tools(),
                    custom_agent_prompt=None, context_files=context_files(), persona=None,
                    user_memory_context=None, llm=model, token_counter=lambda s: len(s)//4)
                assert len(state.get_tool_calls()) == 3
            assert patches._DEEP_RESEARCH_WORKER_LIMIT.get() is None
            assert len(model.requests) == 3
            for first, second in zip(model.requests, model.requests[1:]):
                assert_prefix(first, second)
            calls = [m for m in model.requests[1]['prompt'] if m.get('tool_calls')][0]['tool_calls']
            assert len(calls) == 2, calls
            search = next(c for c in calls if c['function']['name'] == WebSearchTool.NAME)
            assert json.loads(search['function']['arguments'])['queries'] == [base + '/first', base + '/second']
            responses = [json.loads(m['content']) for m in model.requests[2]['prompt'] if m.get('tool_call_id')]
            assert len(responses) == 3
            assert all(r['contents'].startswith(base + '/') for r in responses), responses
            assert len({r['document'] for r in responses}) == 3, responses
            assert sorted(executed[label]) == sorted([
                (WebSearchTool.NAME, [base + '/first', base + '/second']),
                (OpenURLTool.NAME, [base + '/page']),
                (WebSearchTool.NAME, [base + '/third']),
            ])

        # Install shared substitutions once, before launching threads. Using
        # main_chat() here would race its process-global mock restoration.
        with patch.object(llm_loop, 'get_session_with_current_tenant', lambda: nullcontext(None)), \
             patch.object(llm_loop, 'get_default_base_system_prompt', lambda _: DEFAULT_SYSTEM_PROMPT), \
             patch.object(research_agent, 'generate_intermediate_report', return_value='Report'), \
             patch.object(tool_runner, '_safe_run_single_tool', tool_result), \
             patch.object(WebSearchTool, 'emit_start', lambda *a, **k: None), \
             patch.object(OpenURLTool, 'emit_start', lambda *a, **k: None):
            with ThreadPoolExecutor(max_workers=4) as pool:
                # Reuse invocation threads across waves to catch leaked context.
                list(pool.map(invoke, range(8)))
        assert len(executed) == 8

    run_phase(nested=False)
    run_phase(nested=True)
    print('PINNED_CONCURRENT_BATCH_PROMPT_STABILITY_OK')


def validate_report_output_limits():
    from onyx.chat.citation_processor import DynamicCitationProcessor
    from onyx.tools.fake_tools import research_agent
    from onyx.deep_research import dr_loop

    doc = SearchDoc(document_id='https://example.org/evidence', chunk_ind=0,
                    semantic_identifier='Evidence', link='https://example.org/evidence',
                    blurb='Evidence', source_type=DocumentSource.WEB, boost=1,
                    hidden=False, metadata={}, match_highlights=[], is_internet=True)
    for final in (False, True):
        model = ScriptedLLM(['Evidence [1].'])
        state, packets = ChatStateContainer(), []
        emitter = SimpleNamespace(emit=packets.append)
        if final:
            assert dr_loop.MAX_FINAL_REPORT_TOKENS is None
            dr_loop.generate_final_report(history=user_history(), research_plan='Evidence',
                llm=model, token_counter=lambda s: len(s)//4, state_container=state,
                emitter=emitter, turn_index=1, citation_mapping={1: doc}, user_identity=None)
            assert 'Evidence' in state.get_answer_tokens()
            assert state.get_citation_to_doc()[1].document_id == doc.document_id
        else:
            assert research_agent.MAX_INTERMEDIATE_REPORT_LENGTH_TOKENS is None
            citations = DynamicCitationProcessor()
            citations.update_citation_mapping({1: doc})
            report = research_agent.generate_intermediate_report(research_topic='Evidence',
                history=user_history(), llm=model, token_counter=lambda s: len(s)//4,
                citation_processor=citations, user_identity=None, emitter=emitter,
                placement=Placement(turn_index=1, tab_index=0))
            deltas = [p.obj.content for p in packets if type(p.obj).__name__ == 'IntermediateReportDelta']
            assert report == ''.join(deltas) and 'Evidence' in report
            cited = next(p.obj for p in packets if type(p.obj).__name__ == 'IntermediateReportCitedDocs')
            assert len(cited.cited_docs) == 1
            assert type(packets[-1].obj).__name__ == 'SectionEnd'
        assert len(model.requests) == 1
        request = model.requests[0]
        assert request['max_tokens'] is None
        assert request['tools'] == []
        assert request['timeout_override'] == research_agent.DR_REPORT_LLM_TIMEOUT_S
    print('PINNED_REPORT_OUTPUT_ALLOWANCE_OK')


def validate_constants():
    import string
    patches.validate_agent_prompt_stability_patches()
    for owner, consumer, name, value in patches._PROMPT_STABILITY_CONSTANTS:
        assert getattr(owner, name) == getattr(consumer, name) == value
        fields = {field: 'fixture' for _, field, _, _ in string.Formatter().parse(value) if field}
        fields.update(max_cycles=19, current_cycle_count=0)
        first = value.format(**fields)
        fields['current_cycle_count'] = 1
        assert value.format(**fields) == first
        if name.startswith('ORCHESTRATOR_PROMPT'):
            assert '19' in first
        elif name.startswith('RESEARCH_AGENT_PROMPT'):
            assert str(consumer.MAX_RESEARCH_CYCLES) in first
        elif name.startswith('CODING_AGENT_PROMPT'):
            assert str(owner.MAX_CODING_AGENT_CYCLES) in first


def validate_prompt_stability():
    validate_constants()
    validate_main_chat()
    validate_research()
    validate_concurrent_batches()
    validate_report_output_limits()
    print('PINNED_TRANSLATED_PROMPT_STABILITY_OK')
