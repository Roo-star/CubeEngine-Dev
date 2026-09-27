"""Engine-side hardening found by replaying saved paid replies (no model calls)."""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TICTACTOE = ROOT / 'srtp/reference_games/pygame_tictactoe/main.py'
RUNS = ROOT / 'artifacts'
HAS_PYGAME = importlib.util.find_spec('pygame') is not None


def _ir(run, kind):
    folder = RUNS / run / 'ir'
    return json.loads(next(folder.glob('*{0}*'.format(kind))).read_text(encoding='utf-8'))


class FunctionContractTests(unittest.TestCase):
    def test_every_runtime_function_has_named_arguments(self):
        from srtp.ir_v2.function_docs import undocumented
        from srtp.ir_v2.runtime import _core_functions
        self.assertEqual(undocumented(_core_functions(None)), [])

    def test_both_pipelines_show_argument_meaning(self):
        from srtp.ir_v2.function_docs import function_catalog
        from srtp.ir_v2.runtime import _core_functions
        from srtp.llm_compiler_v1.agent_tools import rule_function_catalog
        staged = function_catalog(_core_functions(None))['core:sequence.merge_equal']
        self.assertEqual(staged['signature'], 'sequence.merge_equal(values, empty_value, multiplier)')
        self.assertIn('NOT a length', staged['doc'])
        agentic = next(f for f in rule_function_catalog()['functions'] if f['name'] == 'core:grid.state_at_coordinate')
        self.assertEqual(agentic['signature'], 'grid.state_at_coordinate(grid_state_id, value, coordinate)')
        self.assertIn('[value]', next(f for f in rule_function_catalog()['functions']
                                      if f['name'] == 'core:grid.has_line')['signature'])


class SceneCompletionTests(unittest.TestCase):
    def scene(self):
        return {'nodes': [
            {'id': 'scene:node.board', 'components': [{'id': 'sites', 'type': 'topology_visualizer', 'properties': {
                'topology': 'rule:topology.board', 'rule_topology': 'rule:topology.board', 'prefab': 'scene:prefab.cell'}}]},
            {'id': 'scene:node.camera', 'components': [{'id': 'camera', 'type': 'camera', 'properties': {
                'projection': 'perspective', 'fov_deg': 60}}]}],
            'prefabs': [{'id': 'scene:prefab.cell', 'root': {'local_id': 'root', 'components': [
                {'id': 'renderer', 'type': 'renderer', 'properties': {'geometry': 'builtin:cube', 'visible': True}}]}}]}

    def test_renamed_fields_are_dropped_or_renamed_and_logged(self):
        from srtp.llm_compiler_v1.scene_completion import migrate_aliases
        log = []
        result = migrate_aliases(self.scene(), log)
        self.assertNotIn('topology', result['nodes'][0]['components'][0]['properties'])
        self.assertEqual(result['nodes'][1]['components'][0]['properties'], {'projection': 'perspective', 'fov': 60})
        self.assertEqual(len(log), 2)
        self.assertIn('dropped topology', log[0])

    def test_mouse_input_gets_a_pickable_cell_only_when_nothing_is_selectable(self):
        from srtp.llm_compiler_v1.scene_completion import complete_scene
        mouse = {'bindings': [{'trigger': {'kind': 'control', 'device': 'mouse', 'control': 'mouse.button.primary'}}]}
        keys = {'bindings': [{'trigger': {'kind': 'control', 'device': 'keyboard', 'control': 'key.space'}}]}
        completed, log = complete_scene(self.scene(), mouse)
        colliders = [c for c in completed['prefabs'][0]['root']['components'] if c['type'] == 'collider']
        self.assertEqual(colliders[0]['properties'],
                         {'shape': 'box', 'size': [1, 1, 1], 'is_trigger': False, 'selectable': True})
        self.assertTrue(any('collider' in item for item in log))
        again, log = complete_scene(completed, mouse)
        self.assertEqual(again, completed)
        self.assertEqual(log, [])
        untouched, _ = complete_scene(self.scene(), keys)
        self.assertFalse(any(c['type'] == 'collider' for c in untouched['prefabs'][0]['root']['components']))


class PatchPruningTests(unittest.TestCase):
    def test_only_absent_object_keys_are_pruned_in_order(self):
        from srtp.llm_compiler_v1.normalize import prune_satisfied_removes
        document = {'nodes': [{'properties': {'x': 1}}], 'meta': {}}
        operations = [{'op': 'remove', 'path': '/nodes/0/properties/topology'},
                      {'op': 'remove', 'path': '/nodes/0/properties/x'},
                      {'op': 'remove', 'path': '/nodes/0/properties/x'},
                      {'op': 'remove', 'path': '/nodes/3'},
                      {'op': 'add', 'path': '/meta/k', 'value': 2}]
        kept, notes = prune_satisfied_removes(document, operations)
        self.assertEqual(kept, [operations[1], operations[3], operations[4]],
                         'a missing array index stays for the real applier to reject')
        self.assertEqual(len(notes), 2)
        self.assertEqual(document, {'nodes': [{'properties': {'x': 1}}], 'meta': {}})


class OutcomeOrderProbeTests(unittest.TestCase):
    def test_outcome_reading_the_current_actor_is_explained(self):
        from srtp.llm_compiler_v1.agent_tools import behavior_probe
        rule = _ir('agentic_oneshot_20260925/tictactoe_source', 'rule')
        report = behavior_probe(rule)
        self.assertEqual(report.facts['after_turn_actor_outcomes'], ['rule:outcome.win'])
        self.assertTrue(any('NEXT participant' in item for item in report.warnings))
        fixed = deepcopy(rule)
        fixed['outcomes'] = [o for o in fixed['outcomes'] if o['id'] != 'rule:outcome.win']
        self.assertEqual(behavior_probe(fixed).facts['after_turn_actor_outcomes'], [])


class SourceChangeCheckTests(unittest.TestCase):
    def _job(self, root, bundle):
        from srtp.llm_compiler_v1.agentic import AgentTrace, _LiftJob
        job = _LiftJob.__new__(_LiftJob)
        job.source_bundle_dir = Path(bundle)
        job.package = type('Package', (), {'root': str(root)})()
        job.trace = AgentTrace()
        return job

    def test_cited_files_decide_whether_the_source_changed(self):
        bundle = RUNS / 'agentic_20260925/tictactoe_source_run2'
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(TICTACTOE, Path(tmp) / 'main.py')
            self.assertTrue(self._job(tmp, bundle)._source_files_unchanged())
            with open(Path(tmp) / 'main.py', 'a', encoding='utf-8') as handle:
                handle.write('\n# edited\n')
            self.assertFalse(self._job(tmp, bundle)._source_files_unchanged())
            self.assertFalse(self._job(tmp, Path(tmp))._source_files_unchanged(), 'no citations: no claim')


@unittest.skipUnless(HAS_PYGAME, 'the source oracle needs pygame')
class SavedReplyReplayTests(unittest.TestCase):
    """The saved paid replies, fed through today's pipeline."""

    def test_saved_target_lift_now_passes_with_fewer_calls(self):
        from srtp.llm_compiler_v1.replay import replay_run
        row = replay_run(RUNS / 'agentic_20260925/tictactoe_target_run3', source=TICTACTOE,
                         source_bundle=RUNS / 'agentic_20260925/tictactoe_source_run2')
        self.assertTrue(row['ok'], row)
        self.assertLess(row['used'], row['saved_replies'])

    def test_saved_one_shot_source_bug_is_caught_by_the_oracle(self):
        from srtp.llm_compiler_v1.replay import replay_run
        row = replay_run(RUNS / 'agentic_oneshot_20260925/tictactoe_source', source=TICTACTOE)
        self.assertEqual(row['oracle'], 'diverged')
        self.assertEqual(row['exhausted'], 'rule_ir', 'the review asked for a Rule repair')



class StagedAbsorbsDeterministicMistakesTests(unittest.TestCase):
    def test_renamed_fields_missing_evidence_and_collider_cost_no_repair(self):
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        from srtp.source_importer import SourceGameImporter
        from tests.test_engine_conversion_pipeline import reference_definition, tests_for_board
        from tests.test_llm_budget import fontless_tictactoe
        calls = []

        def chat(messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            calls.append(payload['stage'])
            definition = reference_definition(payload['stage'])
            evidence = [payload['evidence_pack']['evidence'][0]['evidence_id']]
            if payload['stage'] == 'scene_ir':
                evidence = []
                for node in definition.get('nodes', []):
                    for component in node.get('components', []):
                        if component['type'] == 'topology_visualizer':
                            component['properties']['topology'] = component['properties']['rule_topology']
                for prefab in definition.get('prefabs', []):
                    prefab['root']['components'] = [c for c in prefab['root']['components'] if c['type'] != 'collider']
            return json.dumps({'definition': definition, 'evidence': evidence,
                               'behavior_tests': tests_for_board() if payload['stage'] == 'rule_ir' else []})

        with tempfile.TemporaryDirectory() as tmp:
            package = SourceGameImporter().import_path(fontless_tictactoe(Path(tmp)))
            compiler = SourceToIRCompiler(chat_fn=chat, max_repairs=1)
            compiler.use_source_oracle = False
            report = compiler.compile(package, out_dir=Path(tmp) / 'out')
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(calls, ['rule_ir', 'asset_ir', 'scene_ir', 'input_ir'])
        scene_patch = report.proposal['patches']['scene_ir'][0]
        self.assertTrue(any('engine cited verified evidence' in a for a in scene_patch['assumptions']))
        self.assertTrue(any('dropped topology' in a for a in scene_patch['assumptions']))



class VariantHintTests(unittest.TestCase):
    def test_missing_appearance_gets_the_source_drawings_per_state(self):
        from srtp.llm_compiler_v1.static_facts import collect_static_facts, enrich_diagnostics
        from srtp.source_importer import SourceGameImporter
        facts = collect_static_facts(SourceGameImporter().import_path(TICTACTOE))
        drawn = {d['id']: d.get('drawn_when') for d in facts.to_model()['derivations']}
        self.assertEqual(drawn['asset:shape.main.l76'], ['player == 1'])
        self.assertEqual(drawn['asset:shape.main.l79'], ['not (player == 1)', 'player == 2'])
        gap = 'compile gate: Scene presentation: n/renderer: Renderer has a state variant but no appearance mapping: n'
        hinted = enrich_diagnostics([gap], facts)
        self.assertEqual(hinted[0], gap)
        self.assertIn('asset:shape.main.l76 is drawn when player == 1', hinted[1])
        self.assertIn('"geometry"', hinted[1])
        self.assertEqual(enrich_diagnostics(['other problem'], facts), ['other problem'])



class BooleanStateConditionTests(unittest.TestCase):
    """state.get('<id>') of a core:bool state is a valid condition (found in the 2026-09-27 paid run)."""

    def rule(self):
        return json.loads((ROOT / 'tests/fixtures/field_runs_20260925/tictactoe_agentic_source/rule.json')
                          .read_text(encoding='utf-8'))

    def test_only_a_literal_boolean_state_read_is_refined(self):
        from srtp.ir_v2.expression import ExpressionEvaluator
        from srtp.ir_v2.runtime import _condition_type, _core_functions
        evaluator = ExpressionEvaluator(_core_functions(None))
        env = {'rule:state.running': 'core:bool', 'rule:state.count': 'core:int'}

        def read(identifier):
            return {'op': 'call', 'function': 'core:state.get', 'args': [{'op': 'literal', 'value': identifier}]}
        self.assertEqual(_condition_type(evaluator, read('rule:state.running'), env), 'core:bool')
        self.assertEqual(_condition_type(evaluator, read('rule:state.count'), env), 'core:any')
        self.assertEqual(evaluator.infer_type(read('rule:state.running'), env), 'core:any', 'plain inference unchanged')

    def test_rule_compiler_accepts_a_boolean_flag_outcome(self):
        from srtp.ir_v2 import compile_rule_ir, seal_rule_ir
        rule = self.rule()
        rule['outcomes'][0]['condition'] = {'op': 'call', 'function': 'core:state.get',
                                            'args': [{'op': 'literal', 'value': 'rule:state.running'}]}
        compiled = compile_rule_ir(seal_rule_ir(rule))
        getattr(compiled, 'close', lambda: None)()
        rule['outcomes'][0]['condition']['args'][0]['value'] = 'rule:state.current_player'
        with self.assertRaisesRegex(Exception, 'outcome condition must type-check as core:bool'):
            compile_rule_ir(seal_rule_ir(rule))

    @unittest.skipUnless(HAS_PYGAME, 'the source oracle needs pygame')
    def test_paid_reply_rejected_on_20260927_now_passes_first_time(self):
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        from srtp.source_importer import SourceGameImporter
        saved = json.loads((ROOT / 'tests/fixtures/field_runs_20260927/tictactoe_staged_source.json')
                           .read_text(encoding='utf-8'))
        calls = []

        def chat(messages, **kwargs):
            stage = json.loads(messages[-1]['content'])['stage']
            calls.append(stage)
            return json.dumps(saved['rule_ir_rejected'] if stage == 'rule_ir' else saved['accepted'][stage])

        compiler = SourceToIRCompiler(chat_fn=chat, max_repairs=0)
        compiler.use_source_oracle = True
        with tempfile.TemporaryDirectory() as tmp:
            report = compiler.compile(SourceGameImporter().import_path(TICTACTOE), out_dir=Path(tmp) / 'out')
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(calls, ['rule_ir', 'asset_ir', 'scene_ir', 'input_ir'])
        self.assertEqual(report.compilation_trace['source_oracle']['status'], 'passed')



class LiftCellRoleTests(unittest.TestCase):
    """A carried-over 2D cell background must not tile into flat plates in the volume."""

    def scene(self, piece_variants):
        return {'nodes': [{'id': 'scene:node.board', 'components': [{'id': 'sites', 'type': 'topology_visualizer',
                                                                      'properties': {'prefab': 'scene:prefab.cell'}}]}],
                'prefabs': [{'id': 'scene:prefab.cell', 'root': {'local_id': 'cell', 'components': [
                    {'id': 'background', 'type': 'renderer', 'properties': {
                        'geometry': 'asset:shape.bg', 'visible': True, 'spatial_role': 'content',
                        'variants': {'empty': {'pressed_style': {'color': [1, 1, 1, 1]}}}}},
                    {'id': 'piece', 'type': 'renderer', 'properties': {
                        'geometry': 'asset:shape.x', 'visible': False, 'variants': piece_variants}}]}}]}

    def test_background_becomes_the_shell_and_pieces_stay_content(self):
        from srtp.llm_compiler_v1.scene_completion import lift_cell_roles
        prefabs, notes = lift_cell_roles(self.scene({'x': {'visible': True}, 'empty': {'visible': False}}))
        roles = {c['id']: c['properties'].get('spatial_role') for c in prefabs[0]['root']['components']}
        self.assertEqual(roles, {'background': 'cell_shell', 'piece': None})
        self.assertEqual(len(notes), 1)

    def test_drawn_pieces_get_real_thickness(self):
        from srtp.llm_compiler_v1.scene_completion import lift_cell_roles
        assets = {'derivations': [{'id': 'asset:shape.x', 'strategy': 'vector_shape', 'settings': {'depth': 0.1}},
                                  {'id': 'asset:shape.o', 'strategy': 'vector_shape', 'settings': {'depth': 0.25}}]}
        prefabs, notes = lift_cell_roles(self.scene({'x': {'visible': True},
                                                     'o': {'geometry': 'asset:shape.o', 'visible': True}}), assets)
        piece = prefabs[0]['root']['components'][1]['properties']
        self.assertEqual(piece['scale'], [0.8, 0.8, 1.6], 'deepest drawing (0.25) reaches 0.5 cell')
        self.assertTrue(any('thickened' in note for note in notes))

    def test_single_look_tiles_are_left_alone(self):
        from srtp.llm_compiler_v1.scene_completion import lift_cell_roles
        prefabs, notes = lift_cell_roles(self.scene({'x': {'color': [1, 0, 0, 1]}}))
        self.assertEqual(notes, [], 'no renderer switches shape/visibility: nothing to treat as a piece')



class WorkbenchPreviewAndRunsTests(unittest.TestCase):
    def _bundle(self, tmp, phase, pointer):
        """Staged Source from reference definitions, with the placement binding's trigger replaced."""
        from srtp.llm_compiler_v1.approval import approve_llm_manifest_file
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        from srtp.source_importer import SourceGameImporter
        from tests.test_engine_conversion_pipeline import reference_definition, tests_for_board
        from tests.test_llm_budget import fontless_tictactoe

        def chat(messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            definition = reference_definition(payload['stage'])
            if payload['stage'] == 'input_ir':
                trigger = definition['bindings'][0]['trigger']
                trigger['phase'] = phase
                if pointer:
                    trigger['pointer'] = pointer
            return json.dumps({'definition': definition,
                               'evidence': [payload['evidence_pack']['evidence'][0]['evidence_id']],
                               'behavior_tests': tests_for_board() if payload['stage'] == 'rule_ir' else []})
        compiler = SourceToIRCompiler(chat_fn=chat, max_repairs=0)
        compiler.use_source_oracle = False
        report = compiler.compile(SourceGameImporter().import_path(fontless_tictactoe(Path(tmp))),
                                  out_dir=Path(tmp) / 'out')
        self.assertTrue(report.ok, report.diagnostics)
        manifest = Path(report.output_dir) / 'project.manifest.json'
        approve_llm_manifest_file(manifest, designer_id='test')
        return manifest

    def test_preview_cell_click_reaches_press_and_confirmed_click_bindings(self):
        from srtp.project_viewer import ProjectHost
        for phase, pointer in (('press', None), ('release', {'topology': 'rule:topology.board', 'gesture': 'click'})):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                host = ProjectHost(self._bundle(tmp, phase, pointer))
                try:
                    results = [host.click(c) for c in [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0)]]
                    self.assertEqual([r.code for r in results], ['transition_committed'] * 5)
                    self.assertTrue(host.controller.snapshot().terminal)
                    self.assertFalse(host.click((2, 2)).accepted, 'no move after the end')
                finally:
                    host.close()

    def test_every_conversion_run_gets_its_own_folder_and_shares_paid_stages(self):
        import os
        import time
        from srtp.workbench import SrtpWorkbench
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bench = SrtpWorkbench.__new__(SrtpWorkbench)
            bench._conversion_root = lambda: root
            first, second = bench._new_run_dir('source'), None
            first.mkdir()
            second = bench._new_run_dir('source')
            self.assertNotEqual(first, second)
            self.assertTrue(first.name.startswith('source_') and second.name.startswith('source_'))
            self.assertEqual(bench._stage_checkpoint('source'), root / 'source.stages.json')
            old = root / 'source'
            for folder in (old, first):
                folder.mkdir(exist_ok=True)
                (folder / 'project.manifest.json').write_text('{}', encoding='utf-8')
            os.utime(old / 'project.manifest.json', (time.time() - 100, time.time() - 100))
            self.assertEqual(bench._latest_run('source'), first)
            self.assertEqual(bench._latest_run('target'), root / 'target', 'nothing yet: the old default path')



class RuleOutcomeBindingTests(unittest.TestCase):
    """Scene can show win/draw from the Rule outcome; a paid run (2026-09-27) failed without this."""

    RULE = ROOT / 'tests/fixtures/field_runs_20260927/tictactoe_rule_without_winner_state.json'

    def test_outcome_properties_follow_play(self):
        from srtp.ir_v2 import compile_rule_ir
        from srtp.scene_ir_v2.compiler import _read_binding_source
        runtime = compile_rule_ir(json.loads(self.RULE.read_text(encoding='utf-8')))

        def read(name):
            return _read_binding_source({'kind': 'flow', 'property': name}, runtime.state, {})
        self.assertEqual((read('terminal'), read('outcome_status'), read('winner'), read('outcome')),
                         (False, 'ongoing', '', ''))
        for coordinate in [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0)]:
            action = next(a for a in runtime.legal_actions() if tuple(next(iter(dict(a.parameters).values()))) == coordinate)
            runtime.apply_action(action)
        self.assertEqual((read('terminal'), read('outcome_status'), read('winner'), read('outcome')),
                         (True, 'x_win', 'rule:participant.x', 'rule:outcome.x_win'))
        branch = runtime.state.clone()
        with self.assertRaises(Exception):
            _read_binding_source({'kind': 'flow', 'property': 'terminal'}, branch, {})

    def test_contracts_accept_and_type_the_new_properties(self):
        from srtp.scene_ir_v2.component_contracts import binding_source_errors
        for name in ('terminal', 'outcome_status', 'winner', 'outcome'):
            self.assertEqual(binding_source_errors({'kind': 'flow', 'property': name}, '/s'), [])
        self.assertTrue(binding_source_errors({'kind': 'flow', 'property': 'result'}, '/s'))
        from srtp.llm_compiler_v1.backend_contract import profile
        self.assertIn('flow', profile()['rule_outcome_bindings'])


if __name__ == '__main__':
    unittest.main()
