"""Engine fixes found by the 2026-10-01 paid 2048 run (see docs/LLM_ACCEPTANCE_2048_20261001.json)."""
import json
import tempfile
import unittest
from pathlib import Path


class RepeatedReplyTests(unittest.TestCase):
    def test_one_object_repeated_identically_is_that_object(self):
        from srtp.llm_compiler_v1.client import _repeated_object
        reply = {'tool_requests': [{'tool': 'check_expression', 'expression': {'expr': '1 + 1'}}]}
        text = json.dumps(reply) * 3
        self.assertEqual(_repeated_object(text), reply)
        self.assertIsNone(_repeated_object(json.dumps(reply) + json.dumps({'other': 1})), 'different objects stay rejected')
        self.assertIsNone(_repeated_object(json.dumps(reply) + ' trailing text'))
        self.assertIsNone(_repeated_object(json.dumps(reply)), 'a single object goes through the normal parser')


class StagedSelfCheckTests(unittest.TestCase):
    def test_tool_requests_are_answered_without_using_an_attempt(self):
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        from srtp.source_importer import SourceGameImporter
        from tests.test_engine_conversion_pipeline import reference_definition, tests_for_board
        from tests.test_llm_budget import fontless_tictactoe
        seen = []

        def chat(messages, **kwargs):
            payload = json.loads(messages[1]['content'])
            stage = payload['stage']
            last = json.loads(messages[-1]['content'])
            seen.append((stage, 'tool_results' in last))
            if stage == 'rule_ir' and 'tool_results' not in last:
                return json.dumps({'tool_requests': [
                    {'tool': 'check_expression', 'expr': "grid.equals('rule:state.board_cell', param.target, 0)",
                     'as': 'condition', 'parameters': {'target': 'core:coord'}}]})
            return json.dumps({'definition': reference_definition(stage),
                               'evidence': [payload['evidence_pack']['evidence'][0]['evidence_id']],
                               'behavior_tests': tests_for_board() if stage == 'rule_ir' else []})

        with tempfile.TemporaryDirectory() as tmp:
            package = SourceGameImporter().import_path(fontless_tictactoe(Path(tmp)))
            compiler = SourceToIRCompiler(chat_fn=chat, max_repairs=0)
            compiler.use_source_oracle = False
            report = compiler.compile(package, out_dir=Path(tmp) / 'out')
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(seen[:2], [('rule_ir', False), ('rule_ir', True)], 'the tool round stayed in one attempt')
        rule_attempts = [s for s in report.compilation_trace['stages'] if s['stage'] == 'rule_ir']
        self.assertEqual([s['passed'] for s in rule_attempts], [True])


def _compile_tictactoe(rule_reply_edit, tmp):
    from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
    from srtp.source_importer import SourceGameImporter
    from tests.test_engine_conversion_pipeline import reference_definition, tests_for_board
    from tests.test_llm_budget import fontless_tictactoe

    def chat(messages, **kwargs):
        payload = json.loads(messages[1]['content'])
        stage = payload['stage']
        reply = {'definition': reference_definition(stage),
                 'evidence': [payload['evidence_pack']['evidence'][0]['evidence_id']],
                 'behavior_tests': tests_for_board() if stage == 'rule_ir' else []}
        return json.dumps(rule_reply_edit(reply) if stage == 'rule_ir' else reply)

    package = SourceGameImporter().import_path(fontless_tictactoe(Path(tmp)))
    compiler = SourceToIRCompiler(chat_fn=chat, max_repairs=0)
    compiler.use_source_oracle = False
    return compiler.compile(package, out_dir=Path(tmp) / 'out')


class RequiredUnresolvedDiagnosisTests(unittest.TestCase):
    def test_the_blocking_item_is_named_and_the_rest_is_checked(self):
        def declare(reply):
            reply['definition']['unresolved'] = [{'path': '/actions/rule:action.place', 'required': True,
                                                  'owner': 'importer_or_llm', 'reason': 'ordering question'}]
            return reply
        with tempfile.TemporaryDirectory() as tmp:
            report = _compile_tictactoe(declare, tmp)
        self.assertFalse(report.ok)
        diagnostics = report.compilation_trace['stages'][0]['diagnostics']
        self.assertIn('rule_ir /unresolved/0 is required', diagnostics[0])
        self.assertIn('/actions/rule:action.place', diagnostics[0])
        self.assertIn('every other rule_ir gate and all 1 behavior_tests passed', diagnostics[1])

    def test_other_failures_are_reported_alongside(self):
        def declare_and_break(reply):
            reply = declare_and_break.base(reply)
            reply['behavior_tests'][0]['expect']['terminal'] = True
            return reply
        declare_and_break.base = lambda reply: dict(reply, definition=dict(reply['definition'], unresolved=[
            {'path': '/outcomes', 'required': True, 'owner': 'importer_or_llm', 'reason': 'unsure'}]))
        with tempfile.TemporaryDirectory() as tmp:
            report = _compile_tictactoe(declare_and_break, tmp)
        diagnostics = report.compilation_trace['stages'][0]['diagnostics']
        self.assertIn('these checks still fail', diagnostics[1])
        self.assertIn('terminal was False, expected True', diagnostics[1])


class UnresolvedReviewTests(unittest.TestCase):
    def test_a_declaration_only_failure_is_repaired_by_a_short_review(self):
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        from srtp.source_importer import SourceGameImporter
        from tests.test_engine_conversion_pipeline import reference_definition, tests_for_board
        from tests.test_llm_budget import fontless_tictactoe
        payloads = []
        extra = dict(tests_for_board()[0], name='extra assertion from the review')

        def chat(messages, **kwargs):
            payload = json.loads(messages[1]['content'])
            stage = payload['stage']
            evidence = [payload['evidence_pack']['evidence'][0]['evidence_id']]
            if stage == 'rule_ir':
                payloads.append(payload)
                if len(payloads) == 2:
                    return json.dumps({'unresolved': [], 'behavior_tests': [extra], 'evidence': evidence})
                definition = reference_definition(stage)
                definition['unresolved'] = [{'path': '/actions/rule:action.place', 'required': True,
                                             'owner': 'importer_or_llm', 'reason': 'ordering question'}]
                return json.dumps({'definition': definition, 'evidence': evidence, 'behavior_tests': tests_for_board()})
            return json.dumps({'definition': reference_definition(stage), 'evidence': evidence, 'behavior_tests': []})

        with tempfile.TemporaryDirectory() as tmp:
            package = SourceGameImporter().import_path(fontless_tictactoe(Path(tmp)))
            compiler = SourceToIRCompiler(chat_fn=chat, max_repairs=1)
            compiler.use_source_oracle = False
            report = compiler.compile(package, out_dir=Path(tmp) / 'out')
        self.assertTrue(report.ok, report.diagnostics)
        self.assertNotIn('unresolved_review', payloads[0])
        self.assertEqual(payloads[1]['unresolved_review']['declared'][0]['reason'], 'ordering question')
        accepted = [s for s in report.compilation_trace['stages'] if s['stage'] == 'rule_ir' and s['passed']][0]
        self.assertEqual(accepted['unresolved_review'], 0)
        self.assertEqual([t['name'] for t in accepted['behavior_tests']],
                         [tests_for_board()[0]['name'], 'extra assertion from the review'])
        self.assertEqual(report.documents['rule_ir']['unresolved'], [])
        self.assertEqual(report.documents['rule_ir']['actions'], reference_rule_actions())


def reference_rule_actions():
    from tests.test_engine_conversion_pipeline import reference_definition
    return reference_definition('rule_ir')['actions']


class CompactRuleViewTests(unittest.TestCase):
    def test_later_stages_see_the_authored_rule_with_identical_ids(self):
        from srtp.llm_compiler_v1.staged import _declared_ids, model_documents
        from tests.test_engine_conversion_pipeline import reference_definition
        with tempfile.TemporaryDirectory() as tmp:
            report = _compile_tictactoe(lambda reply: reply, tmp)
        self.assertTrue(report.ok, report.diagnostics)
        authored = reference_definition('rule_ir')
        accepted = {'rule_ir': {'definition': authored}}
        sealed = report.documents['rule_ir']
        shown = model_documents(report.documents, accepted, 'scene_ir')['rule_ir']
        self.assertIn('authored form', shown['note'])
        self.assertEqual(shown['content_hash'], sealed['content_hash'])
        self.assertEqual(_declared_ids(shown) - {None}, _declared_ids({k: v for k, v in sealed.items()
                                                                      if k != 'provenance'}))
        self.assertIs(model_documents(report.documents, accepted, 'rule_ir')['rule_ir'], sealed, 'not for the Rule stage')
        renamed = dict(authored, actions=[dict(authored['actions'][0], id='rule:action.other')])
        self.assertIs(model_documents(report.documents, {'rule_ir': {'definition': renamed}}, 'scene_ir')['rule_ir'],
                      sealed, 'differing ids fall back to the sealed document')


class EngineOwnedMetadataTests(unittest.TestCase):
    def test_a_missing_or_wrong_source_hash_is_restored_by_the_engine(self):
        def metadata(reply):
            reply['definition']['metadata'] = {'title': 'Model title', 'description': 'model text',
                                               'source_project_hash': '0' * 64}
            return reply
        with tempfile.TemporaryDirectory() as tmp:
            report = _compile_tictactoe(metadata, tmp)
        self.assertTrue(report.ok, report.diagnostics)
        rule = report.documents['rule_ir']['metadata']
        self.assertEqual(rule['title'], 'Model title')
        self.assertEqual(rule['source_project_hash'], report.documents['asset_ir']['metadata']['source_project_hash'])
        self.assertNotEqual(rule['source_project_hash'], '0' * 64)


class LocalDependencyContextTests(unittest.TestCase):
    def test_imported_modules_and_named_data_files_are_sent_complete(self):
        from srtp.llm_compiler_v1 import source_workspace
        from srtp.llm_compiler_v1.source_workspace import SourceWorkspace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'pkg').mkdir()
            files = {'run.py': 'import rules\nfrom pkg import board\nimport pygame\n',
                     'rules.py': 'import json\nCONFIG = json.load(open("settings.json"))\n',
                     'pkg/__init__.py': '', 'pkg/board.py': 'from . import cells\n', 'pkg/cells.py': 'SIZE = 3\n',
                     'settings.json': '{"size": 3}', 'unused.py': 'X = 1\n'}
            for relative, text in files.items():
                (root / relative).write_text(text, encoding='utf-8')
            workspace = SourceWorkspace(root, root / 'run.py')
            self.assertEqual(workspace.local_dependencies(),
                             ['run.py', 'rules.py', 'pkg/__init__.py', 'pkg/board.py', 'settings.json', 'pkg/cells.py'])
            sent = [s['path'] for s in workspace.initial_context()['source']]
            self.assertEqual(sorted(sent), sorted(set(workspace.local_dependencies()) - {'pkg/__init__.py'}),
                             'empty files are not sent')
            self.assertNotIn('unused.py', sent)
            original = source_workspace.DEPENDENCY_CHARS
            try:
                source_workspace.DEPENDENCY_CHARS = 60
                small = SourceWorkspace(root, root / 'run.py')
                self.assertLess(len(small.initial_context()['source']), len(sent), 'the size budget is respected')
            finally:
                source_workspace.DEPENDENCY_CHARS = original


class StepOrderContractTests(unittest.TestCase):
    def test_both_pipelines_state_when_systems_and_outcomes_run(self):
        from srtp.llm_compiler_v1.backend_contract import profile, profile_for
        from srtp.llm_compiler_v1.staged import SYSTEM
        self.assertIn('run to completion in the same step', profile()['step_order'])
        self.assertIn('step_order', profile_for('rule_ir'))
        self.assertIn('assert it in a behavior test instead of marking it unresolved', SYSTEM)


if __name__ == '__main__':
    unittest.main()
