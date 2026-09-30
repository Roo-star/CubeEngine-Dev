"""Offline replay of the reported 2048 Source Input failure.

The saved rejected definition remains unchanged in the fixture. The repair is
authored in memory to exercise the adapter; no model response is rewritten.
"""
import json
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.input_ir_v2 import PhysicalInputEvent, compile_input_ir, new_input_ir, seal_input_ir
from srtp.input_pointer_contract import pointer_data
from srtp.llm_compiler_v1.behavior_runtime import test_runtime
from srtp.llm_compiler_v1.static_facts import collect_static_facts, enforce_locked
from srtp.source_importer import SourceGameImporter


ROOT = Path(__file__).resolve().parents[1]
FIELD = json.loads((ROOT / 'tests/fixtures/field_runs_20260930/2048_source_input_failure.json').read_text(encoding='utf-8'))


def repaired_input():
    definition = deepcopy(FIELD['rejected_input'])
    definition['unresolved'] = []
    original_quit = next(intent for intent in definition['intents'] if intent['id'] == 'input:intent.quit')
    original_quit['id'] = 'input:intent.quit-q'
    original_quit['target']['when'] = {'state': 'rule:state.stage', 'one_of': [0, 1]}
    definition['intents'].append(dict(original_quit, id='input:intent.quit-n',
        target={'kind': 'host_command', 'command': 'quit',
                'when': {'state': 'rule:state.stage', 'one_of': [2, 3, 4]}}))
    for binding in definition['bindings']:
        if binding['id'] == 'input:binding.q-quit':
            binding['intent'] = 'input:intent.quit-q'
        if binding['id'] == 'input:binding.n-quit':
            binding['intent'] = 'input:intent.quit-n'
            binding['priority'] = 50
    definition['intents'].append({
        'id': 'input:intent.clear-menu', 'name': 'Clear menu selection',
        'value_type': 'digital', 'required': True,
        'target': {'kind': 'rule_action', 'action': 'rule:action.clear_menu_selection', 'parameters': {}}})
    template = next(binding for binding in definition['bindings'] if binding['id'] == 'input:binding.light')
    def add(identifier, intent, pointer):
        binding = deepcopy(template)
        binding.update(id=identifier, name=identifier, intent=intent)
        binding['trigger']['pointer'] = pointer
        definition['bindings'].append(binding)
    add('input:binding.clear-menu', 'input:intent.clear-menu', {'gesture': 'background_click'})
    for direction in ('up', 'down', 'left', 'right'):
        add('input:binding.swipe-' + direction, 'input:intent.' + direction,
            {'gesture': 'swipe', 'direction': direction, 'min_distance_px': 24})
    return definition


class Game2048InputRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rule = FIELD['rule_ir']
        package = SourceGameImporter().import_path(ROOT / 'srtp/reference_games/pygame_2048/main.py')
        cls.facts = collect_static_facts(package)
        locked, _ = enforce_locked('input_ir', repaired_input(), cls.facts)
        cls.assert_guard = next(intent['target']['when'] for intent in locked['intents']
            if intent['id'] == 'input:action.intent.quit')
        document = new_input_ir('input:game.2048-replay')
        document.update(locked)
        document['dependencies'] = {'rule_ir': {
            'document_id': cls.rule['document_id'], 'content_hash': cls.rule['content_hash']},
            'extensions': []}
        cls.compiled = compile_input_ir(seal_input_ir(document), rule_document=cls.rule)

    def event(self, control, data=None):
        device = 'mouse' if control.startswith('mouse.') else 'keyboard'
        phase = 'release' if device == 'mouse' else 'press'
        return PhysicalInputEvent(1, device, control, phase,
            position=(0, 0) if device == 'mouse' else None, data=data or {})

    def route(self, runtime, event):
        return self.compiled.create_router().dispatch(event, focus='viewport', rule_runtime=runtime).intents

    def test_locked_q_is_guarded_and_prompt_keys_match_source_stages(self):
        self.assertEqual(self.assert_guard, {'state': 'rule:state.stage', 'one_of': [0, 1]})
        for stage in range(5):
            with self.subTest(stage=stage):
                runtime = test_runtime(self.rule, {})
                self.addCleanup(runtime.close)
                runtime.state.globals['rule:state.stage'] = stage
                for key, allowed in (('q', stage in (0, 1)), ('n', stage in (2, 3, 4))):
                    routes = self.route(runtime, self.event('keyboard.key.' + key))
                    self.assertEqual(any(item.target_kind == 'host_command' for item in routes), allowed)

    def test_blank_click_and_swipes_route_without_hijacking_buttons(self):
        runtime = test_runtime(self.rule, {})
        self.addCleanup(runtime.close)
        blank = self.route(runtime, self.event('mouse.button.primary',pointer_data({},gesture='background_click')))
        self.assertEqual([r.intent_id for r in blank], ['input:intent.clear-menu'])
        play = self.route(runtime, self.event('mouse.button.primary',pointer_data(
            {'node_id': 'scene:menu.play'},gesture='click')))
        self.assertNotIn('input:intent.clear-menu', [r.intent_id for r in play])
        from srtp.input_pointer_contract import pointer_matches
        for direction in ('up', 'down', 'left', 'right'):
            with self.subTest(direction=direction):
                binding = next(b for b in self.compiled.bindings if b.id == 'input:binding.swipe-' + direction)
                condition = binding.trigger['pointer']
                self.assertFalse(pointer_matches(condition,pointer_data(
                    {},gesture='swipe',direction=direction,distance_px=23)))
                self.assertTrue(pointer_matches(condition,pointer_data(
                    {},gesture='swipe',direction=direction,distance_px=24)))


if __name__ == '__main__':
    unittest.main()
