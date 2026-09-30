"""Offline checks for the Agentic integration without a model request."""
import json
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from srtp.depth_view import visible_in_depth
from srtp.ir_contracts import _document_schema, errors
from srtp.ir_v2 import compile_rule_ir, seal_rule_ir
from srtp.llm_compiler_v1.behavior_runtime import BehaviorReplay
from srtp.llm_compiler_v1.staged import run_behavior_tests
from srtp.llm_compiler_v1.backend_contract import check_alignment, profile
from srtp.llm_compiler_v1.program_builder import expression
from srtp.llm_compiler_v1.program_builder import authoring_schema
from srtp.llm_compiler_v1.source_workspace import SourceWorkspace
from srtp.scene_ir_v2 import compile_scene_ir, seal_scene_ir, identity_transform
from srtp.asset_ir_v2 import compile_asset_ir
from srtp.scene_presentation import ScenePresentation
from tests.test_rule_ir_v2_event_time import _event_document, _literal, _system

ROOT = Path(__file__).resolve().parents[1]


def timed_rule():
    rule = _event_document('fixed_tick', 'fixed_tick', 60)
    rule['flow']['initial_phase'] = 'rule:phase.update'
    rule['flow']['scheduler']['max_catch_up_ticks'] = 8
    rule['state']['variables'] = [
        {'id': 'rule:state.screen', 'name': 'Screen', 'scope': 'global',
         'type': 'core:string', 'initial': _literal('menu')}]
    rule['parameters'] = [{'id': 'rule:parameter.skin', 'key': 'skin', 'name': 'Skin',
                           'type': 'core:string', 'default': _literal('light'),
                           'choices': ['light', 'dark']}]
    rule['actions'] = [
        {'id': 'rule:action.start', 'name': 'Start', 'actor': _literal('rule:participant.operator'),
         'parameters': [], 'precondition': expression("state.get('rule:state.screen') == 'menu'"),
         'effects': [{'op': 'state.set', 'target': _literal('rule:state.screen'),
                      'value': _literal('wait')},
                     {'op': 'event.schedule', 'event': 'rule:event.delayed',
                      'delay_ticks': _literal(60), 'payload': _literal({})}],
         'timing': {'phase': 'rule:phase.update'}, 'encoding': {'kind': 'finite_catalogue'}},
        {'id': 'rule:action.ask', 'name': 'Ask', 'actor': _literal('rule:participant.operator'),
         'parameters': [], 'precondition': expression("state.get('rule:state.screen') == 'play'"),
         'effects': [{'op': 'state.set', 'target': _literal('rule:state.screen'),
                      'value': _literal('done')}],
         'timing': {'phase': 'rule:phase.update'}, 'encoding': {'kind': 'finite_catalogue'}}]
    rule['systems'] = [_system('rule:system.ready', {'kind': 'event', 'event': 'rule:event.delayed'},
                               [{'op': 'state.set', 'target': _literal('rule:state.screen'),
                                 'value': _literal('play')}])]
    return seal_rule_ir(rule)


class AgenticSrtpIntegrationTests(unittest.TestCase):
    def test_existing_agentic_font_contract_keeps_2048_font_uris(self):
        for schema in (_document_schema('asset_ir'), authoring_schema(_document_schema('asset_ir'))):
            source = schema['$defs']['sourceAsset']['properties']['source']['properties']['uri']
            self.assertEqual(errors('runtime://pygame/sysfont/verdana/1/0', source), [])
            self.assertEqual(errors('runtime://system/font/arial/0/0', source), [])
        root = ROOT / 'srtp/reference_games/pygame_2048'
        workspace = SourceWorkspace(root, root / 'main.py')
        self.assertFalse(workspace.preflight_errors)
        self.assertTrue(any(asset['kind'] == 'font' for asset in workspace.runtime_assets))

    def test_optional_slice_preserves_default_and_hides_descendants(self):
        nodes = {'scene:hud': {'parent': None, 'rule_context': {}},
                 'scene:near': {'parent': None, 'rule_context': {'coordinate': [0, 0, 0]}},
                 'scene:far': {'parent': None, 'rule_context': {'coordinate': [0, 0, 1]}},
                 'scene:far.label': {'parent': 'scene:far', 'rule_context': {}}}
        graph = SimpleNamespace(nodes=nodes, volume_layout={'grid': True})
        before = deepcopy(nodes)
        self.assertTrue(all(visible_in_depth(graph, key, None) for key in nodes))
        self.assertTrue(visible_in_depth(graph, 'scene:near', 0))
        self.assertFalse(visible_in_depth(graph, 'scene:far', 0))
        self.assertFalse(visible_in_depth(graph, 'scene:far.label', 0))
        self.assertTrue(visible_in_depth(graph, 'scene:hud', 0))
        self.assertEqual(nodes, before)

    def test_clock_action_precedes_attached_time_and_drains_backlog(self):
        runtime = compile_rule_ir(timed_rule()); self.addCleanup(runtime.close)
        replay = BehaviorReplay(runtime)
        replay.execute({'action': 'rule:action.start', 'accepted': True})
        replay.execute({'advance_ticks': 59})
        replay.execute({'action': 'rule:action.ask', 'accepted': False, 'advance_ticks': 1})
        self.assertEqual(runtime.state.tick, 60)
        self.assertEqual(runtime.state.globals['rule:state.screen'], 'play')
        replay.execute({'action': 'rule:action.ask', 'accepted': True})
        self.assertEqual(runtime.state.globals['rule:state.screen'], 'done')
        another = compile_rule_ir(timed_rule()); self.addCleanup(another.close)
        second = BehaviorReplay(another)
        second.execute({'action': 'rule:action.start', 'accepted': True})
        second.execute({'advance_ns': 1_000_000_000})
        self.assertEqual(another.state.tick, 60)

    def test_timed_failures_are_reported_together(self):
        cases = [
            {'name': 'too early', 'steps': [
                {'action': 'rule:action.start', 'accepted': True},
                {'advance_ticks': 59}, {'action': 'rule:action.ask', 'accepted': True}],
             'expect': {'tick': 59}},
            {'name': 'wrong boundary', 'steps': [
                {'action': 'rule:action.start', 'accepted': True},
                {'advance_ticks': 60}, {'action': 'rule:action.ask', 'accepted': True}],
             'expect': {'tick': 59}}]
        with self.assertRaises(ValueError) as caught:
            run_behavior_tests(timed_rule(), cases)
        self.assertIn('too early', str(caught.exception))
        self.assertIn('wrong boundary', str(caught.exception))

    def test_scene_reads_resolved_parameter_and_agentic_flow(self):
        rule = timed_rule()
        asset = json.loads((ROOT / 'artifacts/tictactoe_source/asset.asset-ir.json').read_text())
        scene = json.loads((ROOT / 'artifacts/tictactoe_source/scene.scene-ir.json').read_text())
        scene['prefabs'], scene['bindings'], scene['nodes'] = [], [], []
        scene['dependencies']['rule_ir'] = {key: rule[key] for key in ('document_id', 'content_hash')}
        layer = scene['layers'][0]['id']
        scene['nodes'] = [{'id': 'scene:status', 'name': 'Status', 'parent': None, 'active': True,
                           'layer': layer, 'transform': identity_transform(),
                           'components': [{'id': 'caption', 'type': 'ui_canvas', 'enabled': True,
                                           'properties': {'mode': 'overlay', 'text': '', 'visible': True,
                                                          'scale': 2, 'position': [-.3, .1]}}]}]
        scene['bindings'] = [{'id': 'scene:binding.skin', 'name': 'Skin',
                              'source': {'kind': 'parameter', 'parameter': 'rule:parameter.skin'},
                              'target': {'selector': 'node', 'node': 'scene:status',
                                         'component': 'caption', 'property': 'text'},
                              'transform': {'kind': 'direct'}}]
        assets = compile_asset_ir(asset, ROOT)
        compiled = compile_scene_ir(seal_scene_ir(scene), rule_document=rule, asset_catalog=assets)
        runtime = compile_rule_ir(rule, parameter_values={'rule:parameter.skin': 'dark'})
        self.addCleanup(runtime.close)
        graph = ScenePresentation(compiled, assets)
        graph.synchronize(compiled.create_projection_session(), runtime.state)
        self.assertEqual(graph.nodes['scene:status']['components']['caption']['properties']['text'], 'dark')
        self.assertEqual(check_alignment(), [])
        self.assertIn('depth_view', profile())


if __name__ == '__main__':
    unittest.main()
