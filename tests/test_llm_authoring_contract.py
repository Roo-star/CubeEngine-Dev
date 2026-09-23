"""Regression for full-document model replies and contradictory authoring schemas."""
import json
import unittest
from copy import deepcopy

from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
from srtp.llm_compiler_v1.program_builder import authoring_schema, definition_proposal, ENGINE_OWNED_FIELDS
from srtp.llm_compiler_v1.staged import SCHEMAS
from srtp.source_importer import SourceGameImporter
from tests.test_engine_conversion_pipeline import ROOT, reference_definition, tests_for_board


class AuthoringContractTests(unittest.TestCase):
    def test_generation_contract_matches_runtime_operand_shapes(self):
        from srtp.ir_v2.command_contracts import COMMAND_CONTRACTS
        from srtp.ir_v2.types import BUILTIN_TYPES
        schema = authoring_schema(json.loads((ROOT/'srtp'/SCHEMAS['rule_ir']).read_text()))
        types = schema['$defs']['typeRef']['anyOf'][0]['enum']
        self.assertEqual(set(types), BUILTIN_TYPES)
        self.assertNotIn('core:grid', types)
        variants = {v['properties']['op']['const']:v for v in schema['$defs']['command']['oneOf']}
        self.assertEqual(set(variants), set(COMMAND_CONTRACTS))
        self.assertEqual(set(variants['foreach']['required']), {'op','query','as','effects'})
        self.assertNotIn('domain', variants['foreach']['properties'])
        self.assertEqual(variants['grid.set']['properties']['state'], {'$ref':'#/$defs/ruleId'})
        self.assertNotIn('target', variants['grid.set']['properties'])
        self.assertEqual(variants['state.set']['properties']['target'], {'$ref':'#/$defs/expression'})

    @staticmethod
    def malformed_definition(fix_type=False):
        definition = reference_definition('rule_ir')
        if not fix_type:
            definition['state']['variables'][0]['type'] = 'core:grid'
        definition['state']['initial_effects'] = [{
            'op':'foreach', 'domain':{'expr':"topology.sites('rule:topology.board')"},
            'scope':{'expr':'var.cell'}, 'effects':[
                {'op':'grid.set','target':{'expr':"'rule:state.board_cell'"},
                 'coordinate':{'expr':'var.cell'},'value':{'expr':'0'}}]}]
        return definition

    def test_all_current_errors_reach_one_repair_and_history_is_separate(self):
        package = SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_tictactoe/main.py')
        calls = []
        def chat(messages, **kwargs):
            payload = json.loads(messages[-1]['content']); calls.append(payload)
            return json.dumps({'definition':self.malformed_definition(fix_type=len(calls)>1),
                'evidence':[payload['evidence_pack']['evidence'][0]['evidence_id']]})
        report = SourceToIRCompiler(chat_fn=chat,max_repairs=1).compile(package)
        self.assertFalse(report.ok)
        feedback = '\n'.join(calls[1]['repair_diagnostics'])
        for pointer in ('/state/variables/0/type', '/initial_effects/0/query',
                        '/initial_effects/0/as', '/initial_effects/0/effects/0/state'):
            self.assertIn(pointer, feedback)
        self.assertEqual(len(calls[1]['repair_history']), 1)
        self.assertNotIn('/state/variables/0/type', '\n'.join(report.diagnostics))
        self.assertIn('/state/variables/0/type', '\n'.join(report.compilation_trace['stages'][0]['diagnostics']))
        self.assertEqual(report.documents['rule_ir']['actions'], [], 'Rejected stage must not be published')

    def test_literal_initial_types_checked_with_structural_errors(self):
        package = SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_tictactoe/main.py')
        def chat(messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            definition = self.malformed_definition(fix_type=True)
            definition['state']['variables'][0].update(type='core:int', initial={'expr':'false'})
            return json.dumps({'definition':definition,
                'evidence':[payload['evidence_pack']['evidence'][0]['evidence_id']]})
        report = SourceToIRCompiler(chat_fn=chat,max_repairs=0).compile(package)
        self.assertFalse(report.ok)
        self.assertIn('/variables/0/initial', '\n'.join(report.diagnostics))
        self.assertIn('/initial_effects/0/query', '\n'.join(report.diagnostics))

    def test_repair_with_real_foreach_runs_before_remaining_stages(self):
        package = SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_tictactoe/main.py')
        calls = []
        def chat(messages, **kwargs):
            payload = json.loads(messages[-1]['content']); slot = payload['stage']; calls.append(slot)
            definition = reference_definition(slot)
            if len(calls) == 1:
                definition = self.malformed_definition()
            elif slot == 'rule_ir':
                definition['state']['initial_effects'] = [{
                    'op':'foreach','query':{'expr':"topology.sites('rule:topology.board')"},
                    'as':'cell','effects':[{'op':'grid.set','state':'rule:state.board_cell',
                                          'coordinate':{'expr':'var.cell'},'value':{'expr':'0'}}]}]
            return json.dumps({'definition':definition,
                'evidence':[payload['evidence_pack']['evidence'][0]['evidence_id']],
                'behavior_tests':tests_for_board() if slot=='rule_ir' else []})
        report = SourceToIRCompiler(chat_fn=chat,max_repairs=1).compile(package)
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(calls, ['rule_ir','rule_ir','asset_ir','scene_ir','input_ir'])
        self.assertTrue(report.compilation_trace['stages'][1]['checks'][0]['passed'])

    def test_boolean_spellings_and_precise_expression_feedback(self):
        from srtp.llm_compiler_v1.program_builder import lower
        self.assertEqual(lower({'expr':'true'}),{'op':'literal','value':True})
        self.assertEqual(lower({'expr':'false'}),{'op':'literal','value':False})
        self.assertEqual(lower({'expr':"state.get('rule:state.player')"})['function'],'core:state.get')
        with self.assertRaisesRegex(ValueError,'/actions/0/actor.*unknown.read'):
            lower({'actions':[{'actor':{'expr':"unknown.read('rule:state.player')"}}]})

    def test_schema_excludes_envelope_and_describes_actual_expression_dialect(self):
        for slot,path in SCHEMAS.items():
            raw=json.loads((ROOT/'srtp'/path).read_text(encoding='utf-8')); before=deepcopy(raw)
            schema=authoring_schema(raw)
            self.assertFalse(set(schema['properties']) & ENGINE_OWNED_FIELDS)
            self.assertFalse(set(schema['required']) & ENGINE_OWNED_FIELDS)
            self.assertEqual(raw,before)
            if slot=='rule_ir':
                variants=schema['$defs']['expression']['oneOf']
                operations={v['properties']['op']['const'] for v in variants if 'op' in v['properties']}
                self.assertNotIn('state.get',operations)
                self.assertIn('expr',variants[-1]['required'])
                self.assertEqual(schema['$defs']['command']['properties']['target'],{'$ref':'#/$defs/expression'})

    def test_full_documents_use_engine_pins_without_wasting_repair_attempts(self):
        package=SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_tictactoe/main.py')
        calls=[]
        def chat(messages, **kwargs):
            payload=json.loads(messages[-1]['content']); slot=payload['stage']; calls.append(slot)
            self.assertFalse(set(payload['schema']['required']) & ENGINE_OWNED_FIELDS)
            definition=reference_definition(slot)
            definition.update(ir_version='wrong',document_id='model:wrong',revision=999,content_hash='untrusted',
                              provenance={'approved':True},dependencies={'rule_ir':{'content_hash':'untrusted'}})
            return json.dumps({'definition':definition,'evidence':[payload['evidence_pack']['evidence'][0]['evidence_id']],
                               'behavior_tests':tests_for_board() if slot=='rule_ir' else []})
        report=SourceToIRCompiler(chat_fn=chat,max_repairs=0).compile(package)
        self.assertTrue(report.ok,report.diagnostics)
        self.assertFalse(report.compile_ready)
        self.assertEqual(len(calls),4)
        for doc in report.documents.values():
            self.assertNotEqual(doc['document_id'],'model:wrong')
            self.assertNotEqual(doc['content_hash'],'untrusted')
            self.assertNotIn('approved',doc['provenance'])
        for row in report.compilation_trace['stages']:
            self.assertEqual(row['ignored_engine_fields'],sorted(ENGINE_OWNED_FIELDS))
        self.assertEqual(report.documents['scene_ir']['dependencies']['rule_ir']['content_hash'],
                         report.documents['rule_ir']['content_hash'])

    def test_unknown_semantics_and_empty_payload_still_fail(self):
        args=dict(slot='rule_ir',documents={'rule_ir':{'actions':[]}},evidence_pack={},job_id='test')
        for definition,pattern in [({'invented_gameplay':True},'unknown fields'),
                                   ({'content_hash':'ignored'},'nonempty semantic')]:
            with self.subTest(definition=definition), self.assertRaisesRegex(ValueError,pattern):
                definition_proposal({'definition':definition},**args)

    def test_invalid_rule_not_accepted_after_removing_envelope(self):
        package=SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_tictactoe/main.py')
        def chat(messages,**kwargs):
            payload=json.loads(messages[-1]['content'])
            definition=reference_definition('rule_ir'); definition['content_hash']='ignored'
            definition['actions'][0]['actor']={'op':'state.get','args':['rule:state.current_player']}
            return json.dumps({'definition':definition,'evidence':[payload['evidence_pack']['evidence'][0]['evidence_id']],
                               'behavior_tests':tests_for_board()})
        report=SourceToIRCompiler(chat_fn=chat,max_repairs=0).compile(package)
        self.assertFalse(report.ok)
        self.assertIn('Unsupported expression operation',' '.join(report.diagnostics))


if __name__=='__main__': unittest.main()
