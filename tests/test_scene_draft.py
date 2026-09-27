"""Engine-drafted Source Scene: built from measured source facts, offered only when every gate passes."""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from srtp.llm_compiler_v1.scene_draft import build_scene_draft, measure_source

ROOT = Path(__file__).resolve().parents[1]
TICTACTOE = ROOT / 'srtp/reference_games/pygame_tictactoe/main.py'
FILES = {'main.py': TICTACTOE}
RULES = {
    'turn_based': ROOT / 'tests/fixtures/field_runs_20260927/tictactoe_rule_without_winner_state.json',
    'agentic_enum': ROOT / 'tests/fixtures/field_runs_20260925/tictactoe_agentic_source/rule.json',
}
HAS_URSINA = importlib.util.find_spec('ursina') is not None


def _asset_for(package):
    """The locked Asset facts the Asset stage always carries (drawings and fonts)."""
    from srtp.llm_compiler_v1.static_facts import collect_static_facts
    facts = collect_static_facts(package)
    return {'derivations': [{'id': d['id'], 'strategy': d['strategy']} for d in facts.derivations],
            'assets': [{'id': a['id'], 'kind': a['kind']} for a in facts.assets]}


def _package(path):
    from srtp.source_importer import SourceGameImporter
    return SourceGameImporter().import_path(path)


class MeasurementTests(unittest.TestCase):
    def test_window_fill_and_texts_come_from_the_source(self):
        measured = measure_source(FILES)
        self.assertEqual(measured['window'], [480.0, 570.0])
        self.assertEqual(len(measured['fill']), 4)
        status, help_text = measured['texts']
        self.assertEqual((status['font_size'], status['position'], status['phrases']),
                         (36.0, [30.0, 30.0], ['Winner: ', 'Draw', 'Turn: ']))
        self.assertEqual(help_text['text'], 'Click an empty cell. R: restart. Esc: quit.')


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.asset = _asset_for(_package(TICTACTOE))

    def draft(self, rule, files=FILES):
        return build_scene_draft({'rule_ir': rule, 'asset_ir': self.asset}, files, 'Tic Tac Toe')

    def test_paid_rules_of_every_turn_style_get_a_draft(self):
        for name, path in RULES.items():
            with self.subTest(rule=name):
                draft = self.draft(json.loads(path.read_text(encoding='utf-8')))
                self.assertIsNotNone(draft.definition, draft.reasons)
                piece = draft.definition['bindings'][0]
                self.assertEqual([case['equals'] for case in piece['transform']['cases']], [0, 1, 2])
                status = next(b for b in draft.definition['bindings'] if b['id'] == 'scene:binding.status')
                text = json.dumps(status['source'])
                self.assertIn('"terminal"', text)
                self.assertIn('Winner: X', text)
                self.assertIn('Turn: X', text)
                root = draft.definition['prefabs'][0]['root']
                self.assertEqual(root['transform']['rotation_euler_deg'], [180, 0, 0], 'y-up art stays upright')

    def test_unmapped_state_values_leave_the_scene_to_the_model(self):
        rule = json.loads(RULES['agentic_enum'].read_text(encoding='utf-8'))
        rule['types'][0]['values'] = dict(rule['types'][0]['values'], captured=3)
        draft = self.draft(rule)
        self.assertIsNone(draft.definition)
        self.assertIn('no source drawing for state value(s) [3]', draft.reasons[0])

    def test_renamed_draw_first_source_is_measured_the_same_way(self):
        from tests.test_source_oracle import _renamed_source
        with tempfile.TemporaryDirectory() as tmp:
            package = _renamed_source(tmp)
            files = {name: Path(package.root) / name for name in package.files if str(name).endswith('.py')}
            draft = build_scene_draft({'rule_ir': json.loads(RULES['turn_based'].read_text(encoding='utf-8')),
                                       'asset_ir': _asset_for(package)}, files, 'Noughts')
        self.assertIsNotNone(draft.definition, draft.reasons)
        self.assertTrue(any('pitch [140, 140]px' in note for note in draft.notes))

    def test_games_outside_the_pattern_get_no_draft(self):
        package = _package(ROOT / 'srtp/reference_games/pygame_minesweeper/run_game.py')
        files = {name: Path(package.root) / name for name in package.files if str(name).endswith('.py')}
        rule = json.loads(RULES['turn_based'].read_text(encoding='utf-8'))
        draft = build_scene_draft({'rule_ir': rule, 'asset_ir': _asset_for(package)}, files, 'Minesweeper')
        self.assertIsNone(draft.definition)
        self.assertTrue(draft.reasons)


class StagedDraftTests(unittest.TestCase):
    def setUp(self):
        from tests.test_llm_budget import fontless_tictactoe
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.package = _package(fontless_tictactoe(self.tmp))
        self.requests = []

    def tearDown(self):
        self._tmp.cleanup()

    def _compile(self, scene_replies, mode='review', max_repairs=1):
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        from tests.test_engine_conversion_pipeline import reference_definition, tests_for_board

        def chat(messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            self.requests.append(payload)
            stage = payload['stage']
            reply = {'definition': reference_definition(stage),
                     'evidence': [payload['evidence_pack']['evidence'][0]['evidence_id']],
                     'behavior_tests': tests_for_board() if stage == 'rule_ir' else []}
            if stage == 'scene_ir':
                reply = scene_replies.pop(0)(reply, payload)
            return json.dumps(reply)
        compiler = SourceToIRCompiler(chat_fn=chat, max_repairs=max_repairs)
        compiler.use_source_oracle = False
        compiler.scene_draft_mode = mode
        return compiler.compile(self.package, out_dir=self.tmp / 'out')

    def stages(self):
        return [p['stage'] for p in self.requests]

    def test_model_review_can_accept_the_validated_draft(self):
        report = self._compile([lambda reply, payload: {'accept_engine_draft': True}])
        self.assertTrue(report.ok, report.diagnostics)
        scene_payload = next(p for p in self.requests if p['stage'] == 'scene_ir')
        self.assertTrue(scene_payload['engine_draft']['passes_every_gate'])
        self.assertEqual(report.compilation_trace['scene_draft']['used'], 'model_review_accepted')
        self.assertIn('scene:board', [n['id'] for n in report.documents['scene_ir']['nodes']])

    def test_review_edits_only_the_entries_it_names(self):
        def edit(reply, payload):
            camera = copy.deepcopy(next(n for n in payload['engine_draft']['definition']['nodes']
                                        if n['id'] == 'scene:camera'))
            camera['name'] = 'Reviewed view'
            return {'entry_fixes': {'nodes': [camera]}}
        report = self._compile([edit])
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(report.compilation_trace['scene_draft']['used'], 'model_review_edited')
        names = {n['id']: n['name'] for n in report.documents['scene_ir']['nodes']}
        self.assertEqual((names['scene:camera'], names['scene:board']), ('Reviewed view', 'Board'))

    def test_rejected_edits_fall_back_to_the_validated_draft(self):
        def broken(reply, payload):
            reply['definition']['bindings'] = 'not a list'
            return reply
        report = self._compile([broken, broken])
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(self.stages().count('scene_ir'), 2, 'both model attempts were made first')
        self.assertEqual(report.compilation_trace['scene_draft']['used'], 'engine_draft_after_rejected_edits')

    def test_a_validated_draft_caps_paid_review_attempts(self):
        def broken(reply, payload):
            reply['definition']['bindings'] = 'not a list'
            return reply
        report = self._compile([broken, broken, broken, broken], max_repairs=3)
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(self.stages().count('scene_ir'), 2, 'two review attempts, not four')

    def test_accept_mode_needs_no_scene_call(self):
        report = self._compile([], mode='accept')
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(self.stages(), ['rule_ir', 'asset_ir', 'input_ir'])
        self.assertEqual(report.compilation_trace['scene_draft']['used'], 'engine_draft_only')

    def test_off_mode_is_the_previous_behaviour(self):
        report = self._compile([lambda reply, payload: reply], mode='off')
        self.assertTrue(report.ok, report.diagnostics)
        self.assertNotIn('engine_draft', next(p for p in self.requests if p['stage'] == 'scene_ir'))


@unittest.skipUnless(HAS_URSINA, 'the rendered-frame check needs Ursina')
class VisualGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from srtp.bundle_assets import asset_root
        from srtp.llm_compiler_v1.approval import approve_llm_manifest_file
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        from tests.test_engine_conversion_pipeline import reference_definition, tests_for_board
        from tests.test_llm_budget import fontless_tictactoe
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.package = _package(fontless_tictactoe(tmp))

        def chat(messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            stage = payload['stage']
            return json.dumps({'definition': reference_definition(stage),
                               'evidence': [payload['evidence_pack']['evidence'][0]['evidence_id']],
                               'behavior_tests': tests_for_board() if stage == 'rule_ir' else []})
        compiler = SourceToIRCompiler(chat_fn=chat, max_repairs=0)
        compiler.use_source_oracle = False
        compiler.scene_draft_mode = 'accept'
        cls.source = compiler.compile(cls.package, out_dir=tmp / 'source')
        approve_llm_manifest_file(Path(cls.source.output_dir) / 'project.manifest.json', designer_id='test')
        cls.target = compiler.compile_spatial_lift(cls.package, source_bundle_dir=Path(cls.source.output_dir),
                                                   target_dimensions={'x': 3, 'y': 3, 'z': 3}, out_dir=tmp / 'target')
        cls.root = asset_root(Path(cls.package.root))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def check(self, documents, spatial=False):
        from srtp.llm_compiler_v1.visual_gate import run_visual_check
        return run_visual_check(documents, self.root, spatial=spatial)

    def test_compiled_source_and_lift_pass(self):
        self.assertTrue(self.source.ok and self.target.ok, (self.source.diagnostics, self.target.diagnostics))
        for report, spatial in ((self.source, False), (self.target, True)):
            result = self.check(report.documents, spatial)
            self.assertEqual(result['status'], 'passed', result)
            self.assertGreater(result['facts']['move']['changed_fraction'], 0.005)

    def test_hidden_pieces_fail_with_the_cell_named(self):
        from srtp.scene_ir_v2 import seal_scene_ir
        documents = copy.deepcopy(self.source.documents)
        piece = documents['scene_ir']['prefabs'][0]['root']['children'][1]['components'][0]['properties']
        piece['variants'] = {name: {'visible': False} for name in piece['variants']}
        documents['scene_ir'] = seal_scene_ir(documents['scene_ir'])
        result = self.check(documents)
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('changed nothing on screen', result['errors'][0])

    def test_flat_volume_cells_fail(self):
        from srtp.scene_ir_v2 import seal_scene_ir
        documents = copy.deepcopy(self.target.documents)

        def flatten(node):
            for component in node['components']:
                if component['properties'].get('spatial_role') == 'cell_shell':
                    component['properties']['spatial_role'] = 'content'
                    flattened.append(component['id'])
            for child in node.get('children') or []:
                flatten(child)
        flattened = []
        for prefab in documents['scene_ir']['prefabs']:
            flatten(prefab['root'])
        self.assertTrue(flattened, 'the lifted Scene has a cell shell to remove')
        documents['scene_ir'] = seal_scene_ir(documents['scene_ir'])
        result = self.check(documents, spatial=True)
        self.assertEqual(result['status'], 'failed', result)


if __name__ == '__main__':
    unittest.main()
