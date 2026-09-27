"""Entry-level repair: only failing entries are regenerated; accepted entries stay verbatim."""
import json
import shutil
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.llm_compiler_v1.entry_repair import failing_entries, merge_entry_fixes

BROKEN_EFFECT = {'op': 'state.set', 'value': {'op': 'literal', 'value': 1}}


class EntryMappingTests(unittest.TestCase):
    def test_diagnostics_map_to_entries_or_fall_back(self):
        definition = {'actions': [{'id': 'rule:action.a'}, {'id': 'rule:action.b'}],
                      'state': {'variables': [{'id': 'rule:state.v'}]}}
        plan = failing_entries('rule_ir', definition, [
            'rule_ir invalid at /actions/1/effects/0/target: state.set requires target',
            'Expression at /actions/1/precondition: bad', 'rule_ir invalid at /state/variables/0/initial: type'])
        self.assertEqual([(row['field'], row['id'], len(row['diagnostics'])) for row in plan],
                         [('actions', 'rule:action.b', 2), ('state.variables', 'rule:state.v', 1)])
        self.assertIsNone(failing_entries('rule_ir', definition, ['compile gate: Rule compiler: no mechanic']))
        self.assertIsNone(failing_entries('rule_ir', definition, ['rule_ir invalid at /actions/7/effects: x']))

    def test_merge_replaces_appends_and_removes_by_id(self):
        definition = {'actions': [{'id': 'rule:action.a', 'v': 1}, {'id': 'rule:action.b', 'v': 1}],
                      'state': {'variables': [{'id': 'rule:state.v'}], 'information_model': 'perfect'}}
        merged = merge_entry_fixes(definition, {'actions': [{'id': 'rule:action.b', 'v': 2}, {'id': 'rule:action.c'}],
                                                'state.variables': [{'id': 'rule:state.w'}]},
                                   {'actions': ['rule:action.a']})
        self.assertEqual(merged['actions'], [{'id': 'rule:action.b', 'v': 2}, {'id': 'rule:action.c'}])
        self.assertEqual(merged['state'], {'variables': [{'id': 'rule:state.v'}, {'id': 'rule:state.w'}],
                                           'information_model': 'perfect'})
        with self.assertRaisesRegex(ValueError, 'needs its id'):
            merge_entry_fixes(definition, {'actions': [{'v': 3}]})
        with self.assertRaisesRegex(ValueError, 'no such list'):
            merge_entry_fixes(definition, {'outcomes': [{'id': 'rule:outcome.x'}]})


class StagedEntryRepairTests(unittest.TestCase):
    def test_only_the_failing_action_is_regenerated(self):
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        from srtp.source_importer import SourceGameImporter
        from tests.test_engine_conversion_pipeline import reference_definition, tests_for_board
        from tests.test_llm_budget import fontless_tictactoe
        requests = []

        def chat(messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            requests.append(payload)
            evidence = [payload['evidence_pack']['evidence'][0]['evidence_id']]
            definition = reference_definition(payload['stage'])
            if payload['stage'] != 'rule_ir':
                return json.dumps({'definition': definition, 'evidence': evidence, 'behavior_tests': []})
            if 'entry_repair' in payload:
                return json.dumps({'entry_fixes': {'actions': [definition['actions'][0]]}, 'evidence': evidence})
            definition['actions'][0]['effects'].append(BROKEN_EFFECT)
            return json.dumps({'definition': definition, 'evidence': evidence, 'behavior_tests': tests_for_board()})

        with tempfile.TemporaryDirectory() as tmp:
            package = SourceGameImporter().import_path(fontless_tictactoe(tmp))
            report = SourceToIRCompiler(chat_fn=chat, max_repairs=1).compile(package, out_dir=Path(tmp) / 'out')
        self.assertTrue(report.ok, report.diagnostics)
        rule_requests = [r for r in requests if r['stage'] == 'rule_ir']
        self.assertEqual(len(rule_requests), 2)
        repair = rule_requests[1]['entry_repair']
        self.assertEqual([(row['field'], row['id']) for row in repair['failing_entries']], [('actions', 'rule:action.place')])
        self.assertIn('state.set requires target', repair['failing_entries'][0]['diagnostics'][0])
        passed = next(row for row in report.compilation_trace['stages'] if row['stage'] == 'rule_ir' and row['passed'])
        self.assertEqual(passed['entry_repair'], ['actions:rule:action.place'])
        self.assertEqual(passed['behavior_tests'], tests_for_board(), 'kept from the first reply')
        reference = reference_definition('rule_ir')
        self.assertEqual(report.documents['rule_ir']['topologies'], reference['topologies'])


class AgenticEntryRepairTests(unittest.TestCase):
    def test_worker_returns_only_the_corrected_entry(self):
        from srtp.llm_compiler_v1.agentic import AgenticSourceToIRCompiler
        from tests.test_llm_agentic_compiler import (FILE, TICTACTOE, _PASS, _ScriptedChat, _asset_patch, _input_patch,
                                                     _rule_patch, _scene_patch, _spec)
        good = _rule_patch()
        broken = deepcopy(good)
        actions = next(op for op in broken['operations'] if op['path'] == '/actions')
        actions['value'][0]['effects'].append(BROKEN_EFFECT)
        fixed_action = next(op for op in good['operations'] if op['path'] == '/actions')['value'][0]
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source'
            source.mkdir()
            shutil.copy(TICTACTOE, source / FILE)
            chat = _ScriptedChat([{'action': 'finish', 'spec': _spec()}, broken,
                                  {'entry_fixes': {'actions': [fixed_action]}},
                                  _asset_patch(), _scene_patch(), _input_patch(), _PASS])
            compiler = AgenticSourceToIRCompiler(chat_fn=chat)
            report = compiler.compile_path(source, out_dir=Path(tmp) / 'out', title='Tic Tac Toe 2D')
        self.assertTrue(report.ok, report.diagnostics)
        repair = json.loads(chat.requests[2][-1]['content'])
        self.assertEqual([(row['field'], row['id']) for row in repair['failing_entries']],
                         [('actions', fixed_action['id'])])
        self.assertIn('Only the entries in failing_entries failed', repair['instruction'])
        accepted = compiler.last_job.workers['rule_ir'].envelope['operations']
        by_path = {op['path']: op for op in accepted}
        for op in good['operations']:
            if op['path'] not in ('/actions',):
                self.assertEqual(by_path[op['path']]['value'], op['value'], op['path'])


if __name__ == '__main__':
    unittest.main()
