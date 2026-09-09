from __future__ import annotations

import functools
import linecache
import sys
import unittest
from types import ModuleType
from unittest.mock import patch

from test_shared_agent_patch_contracts import _load_wrapper


def _foreign_decorator(function):
    @functools.wraps(function)
    def timed(*args, **kwargs):
        return function(*args, **kwargs)
    return timed


class PromptStabilityContracts(unittest.TestCase):
    def test_decorated_rebuilds_retain_execution_globals(self):
        wrapper = _load_wrapper()
        module = ModuleType('decorated_loop_fixture')
        module.__dict__.update(decorate=_foreign_decorator, budget=37)
        source = '@decorate\ndef loop(value=budget):\n    return value + 1\n'
        filename = '<decorated-loop-fixture>'
        linecache.cache[filename] = (len(source), None, source.splitlines(True), filename)
        exec(compile(source, filename, 'exec'), module.__dict__)
        for old, new in [('value + 1', 'value + 2'), ('value + 2', 'value + 3')]:
            wrapper._patch_function_source(module=module, function_name='loop',
                replacements={old: new}, patch_name='decorated source composition')
        self.assertEqual(module.loop(), 40)
        self.assertIs(module.loop._wrapper_source_globals, module.__dict__)

    def test_exact_count_rejects_zero_and_duplicate_source(self):
        wrapper = _load_wrapper()
        for source in ['changed', 'anchor anchor']:
            with self.subTest(source=source), self.assertRaisesRegex(RuntimeError, 'exactly 1'):
                wrapper._prompt_stability_replace(source, 'anchor', 'stable', 'fixture')
        self.assertEqual(wrapper._prompt_stability_replace('anchor', 'anchor', 'stable', 'fixture'), 'stable')

    def test_accumulated_source_and_order_are_required(self):
        wrapper = _load_wrapper()
        module = ModuleType('loop_fixture')
        exec('def loop():\n    return 1\n', module.__dict__)
        with self.assertRaisesRegex(RuntimeError, 'must follow existing loop patches'):
            wrapper._patch_investigation_source(module, 'loop', {'return 1': 'return 2'})
        module.loop._wrapper_patched_source = 'def loop():\n    return 1 + 10\n'
        wrapper._patch_investigation_source(module, 'loop', {'return 1': 'return 2'})
        self.assertEqual(module.loop(), 12)
        with self.assertRaisesRegex(RuntimeError, 'exactly 1'):
            wrapper._patch_investigation_source(module, 'loop', {'return 1': 'return 2'})
        module.loop._wrapper_patched_source += '# return 2\n'
        with self.assertRaisesRegex(RuntimeError, 'exactly 1'):
            wrapper._patch_investigation_source(module, 'loop', {'return 2': 'return 3'})

    def test_actual_selected_tools_drive_internal_search_tuning(self):
        wrapper = _load_wrapper()
        source = "        include_internal_search_tunings = SearchTool.NAME in allowed_tool_names\n"
        replacement = wrapper._PROMPT_STABILITY_DR_REPLACEMENTS[source].strip()
        from types import SimpleNamespace
        for selected, expected in [([], False), (["web_search"], False), (["internal_search"], True)]:
            namespace = dict(SearchTool=SimpleNamespace(NAME="internal_search"),
                             allowed_tool_names={"internal_search", "web_search"},
                             allowed_tools=[SimpleNamespace(name=name) for name in selected])
            exec(replacement, namespace)
            self.assertEqual(namespace["include_internal_search_tunings"], expected)

    def test_final_validation_rejects_source_constants_and_application_aliases(self):
        wrapper = _load_wrapper()
        modules = {}
        for path in ["onyx.chat.llm_loop", "onyx.chat.process_message", "onyx.chat.prompt_utils",
                     "onyx.deep_research.dr_loop", "onyx.tools.fake_tools.research_agent",
                     "onyx.tools.fake_tools.coding_agent"]:
            parts = path.split('.')
            for n in range(1, len(parts)+1):
                name = '.'.join(parts[:n])
                modules.setdefault(name, ModuleType(name))
                if n > 1:
                    setattr(modules['.'.join(parts[:n-1])], parts[n-1], modules[name])
        caller = modules['onyx.chat.process_message']
        for path, name in [('onyx.chat.llm_loop', 'run_llm_loop'),
                           ('onyx.deep_research.dr_loop', 'run_deep_research_llm_loop'),
                           ('onyx.tools.fake_tools.research_agent', 'run_research_agent_call')]:
            module = modules[path]
            exec(f'def {name}(): pass', module.__dict__)
            function = getattr(module, name)
            function._wrapper_patched_source = 'retained'
            setattr(caller, name, function)
            wrapper._PROMPT_STABILITY_FUNCTIONS.append((module, name, function, 'retained'))
        owner = ModuleType('owner')
        owner.PROMPT = 'stable'
        wrapper._PROMPT_STABILITY_CONSTANTS = [(owner, owner, 'PROMPT', 'stable')]*9
        with patch.dict(sys.modules, modules):
            for name in ['run_llm_loop', 'run_deep_research_llm_loop']:
                with patch.object(caller, name, lambda: None):
                    with self.assertRaisesRegex(RuntimeError, 'stale application caller binding'):
                        wrapper.validate_agent_prompt_stability_patches()
            with patch.object(owner, 'PROMPT', 'stale'):
                with self.assertRaisesRegex(RuntimeError, 'final prompt binding drift'):
                    wrapper.validate_agent_prompt_stability_patches()
            with patch.object(caller.run_llm_loop, '_wrapper_patched_source', 'changed'):
                with self.assertRaisesRegex(RuntimeError, 'final prompt stability source/binding drift'):
                    wrapper.validate_agent_prompt_stability_patches()

    def test_constant_consumer_drift_fails(self):
        wrapper = _load_wrapper()
        owner, consumer = ModuleType('owner'), ModuleType('consumer')
        owner.PROMPT, consumer.PROMPT = 'current', 'stale'
        with self.assertRaisesRegex(RuntimeError, 'stale prompt consumer'):
            wrapper._patch_investigation_constant(owner, consumer, 'PROMPT', 'current', 'stable')
        consumer.PROMPT = owner.PROMPT
        wrapper._patch_investigation_constant(owner, consumer, 'PROMPT', 'current', 'stable')
        self.assertEqual(owner.PROMPT, consumer.PROMPT)
        self.assertEqual(consumer.PROMPT, 'stable')

    def test_both_prompt_variants_preserve_configured_budgets_and_bindings(self):
        wrapper = _load_wrapper()
        modules = {}
        paths = [
            'onyx.chat.llm_loop', 'onyx.chat.prompt_utils', 'onyx.deep_research.dr_loop',
            'onyx.prompts.tool_prompts', 'onyx.prompts.deep_research.orchestration_layer',
            'onyx.prompts.deep_research.research_agent', 'onyx.prompts.deep_research.dr_tool_prompts',
            'onyx.prompts.coding_agent.coding_agent', 'onyx.tools.fake_tools.research_agent',
            'onyx.tools.fake_tools.coding_agent',
        ]
        for path in paths:
            parts = path.split('.')
            for n in range(1, len(parts)+1):
                name = '.'.join(parts[:n])
                if name not in modules:
                    modules[name] = ModuleType(name)
                    modules[name].__path__ = []
                if n > 1:
                    setattr(modules['.'.join(parts[:n-1])], parts[n-1], modules[name])
        def bind(owner, consumer, name, value):
            setattr(modules[owner], name, value)
            setattr(modules[consumer], name, value)
        bind(paths[3], paths[1], 'OPEN_URLS_GUIDANCE',
             'You should almost always use open_url after a web_search call. User URLs.')
        for suffix in ['', '_REASONING']:
            bind(paths[4], paths[2], 'ORCHESTRATOR_PROMPT'+suffix,
                 'You have currently used {current_cycle_count} of {max_cycles} max research cycles.')
            bind(paths[5], paths[8], 'RESEARCH_AGENT_PROMPT'+suffix,
                 'You are on cycle {current_cycle_count} of 37.')
            bind(paths[7], paths[9], 'CODING_AGENT_PROMPT'+suffix,
                 'Budget: 20 cycles (you are on cycle {current_cycle_count}).')
            tail = ' and sometimes after reasoning with the think_tool tool' if not suffix else ''
            bind(paths[6], paths[8], 'OPEN_URLS_TOOL_DESCRIPTION'+suffix,
                 '## open_urls\nUse `open_urls`. You should almost always use open_urls after a web_search call'+tail+'.')
        with patch.dict(sys.modules, modules), patch.object(wrapper, '_patch_investigation_source'):
            wrapper.apply_agent_prompt_stability_patches()
        self.assertEqual(len(wrapper._PROMPT_STABILITY_CONSTANTS), 9)
        for owner, consumer, name, value in wrapper._PROMPT_STABILITY_CONSTANTS:
            self.assertEqual(getattr(owner, name), getattr(consumer, name))
            self.assertEqual(value.format(current_cycle_count=0, max_cycles=19),
                             value.format(current_cycle_count=1, max_cycles=19))
            if name.startswith('RESEARCH_AGENT_PROMPT'):
                self.assertIn('37', value)
            if name.startswith('CODING_AGENT_PROMPT'):
                self.assertIn('20', value)
            if name.startswith('OPEN_URLS'):
                self.assertNotIn('open_urls', value)
                self.assertIn('snippets completely answer the query', value)
            if name == 'OPEN_URLS_TOOL_DESCRIPTION':
                self.assertIn('think_tool', value)


if __name__ == '__main__':
    unittest.main()
