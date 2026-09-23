"""Lifecycle commands tested against the actual paid model's saved game.

The original failed Input remains failed. A labelled test-only copy adds the
new host command targets; it is never written to the user's compilation cache.
"""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
from srtp.llm_compiler_v1.approval import approve_llm_manifest_file
from srtp.llm_compiler_v1.backend_contract import profile
from srtp.input_ir_v2 import validate_input_ir, compile_input_ir, new_input_ir
from srtp.project_viewer import ProjectHost
from srtp.source_importer import SourceGameImporter
from tests.test_engine_conversion_pipeline import ROOT


class HostCommandTests(unittest.TestCase):
    def setUp(self):
        self.fixture=json.loads((ROOT/'tests/fixtures/llm_input_failure_20260923.json').read_text(encoding='utf-8'))
        self.package=SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_tictactoe/main.py')

    def test_original_model_report_remains_blocked_not_silently_cleared(self):
        def chat(messages,**kwargs):
            slot=json.loads(messages[-1]['content'])['stage']
            return json.dumps(self.fixture['rejected_input'] if slot=='input_ir' else self.fixture['accepted_stages'][slot])
        result=SourceToIRCompiler(chat_fn=chat,max_repairs=0).compile(self.package)
        self.assertFalse(result.ok)
        self.assertIn('unresolved',' '.join(result.diagnostics))
        self.assertTrue(self.fixture['rejected_input']['definition']['unresolved'][0]['required'])

    def lifecycle_definition(self):
        response=deepcopy(self.fixture['rejected_input'])
        definition=response['definition']; definition['unresolved']=[]
        for intent in definition['intents']:
            if intent['target'].get('action')=='rule:action.restart':
                intent['target']={'kind':'host_command','command':'restart'}
        definition['intents'].append({'id':'input:intent.quit','name':'Quit player','required':True,
            'value_type':'digital','target':{'kind':'host_command','command':'quit'}})
        binding=deepcopy(definition['bindings'][1]); binding.update(id='input:binding.quit',name='Quit',intent='input:intent.quit')
        binding['trigger']['control']='keyboard.key.escape'; definition['bindings'].append(binding)
        return response

    def test_host_restart_after_terminal_and_quit_do_not_forge_rule_actions(self):
        self.assertEqual(set(profile()['host_commands']),{'quit','restart'})
        supplied=[]
        def chat(messages,**kwargs):
            payload=json.loads(messages[-1]['content']); slot=payload['stage']; supplied.append(slot)
            if slot=='input_ir':
                kinds={s['properties']['kind']['const'] for s in payload['schema']['$defs']['target']['oneOf']}
                self.assertEqual(kinds,{'rule_action','host_command'})
                return json.dumps(self.lifecycle_definition())
            return json.dumps(self.fixture['accepted_stages'][slot])
        with tempfile.TemporaryDirectory() as temporary:
            report=SourceToIRCompiler(chat_fn=chat,max_repairs=0).compile(self.package,out_dir=Path(temporary)/'source')
            self.assertTrue(report.ok,report.diagnostics)
            manifest=Path(report.output_dir)/'project.manifest.json'; approve_llm_manifest_file(manifest)
            host=ProjectHost(manifest)
            try:
                for sequence in [[(0,0),(0,1),(1,0),(1,1),(2,0)],
                    [(0,0),(1,0),(2,0),(1,1),(0,1),(2,1),(1,2),(0,2),(2,2)]]:
                    for coordinate in sequence:
                        self.assertTrue(host.mouse('mouse.button.primary',{'coordinate':coordinate}).accepted)
                        host.refresh_scene()
                    self.assertTrue(host.controller.snapshot().terminal)
                    self.assertFalse(host.mouse('mouse.button.primary',{'coordinate':(0,0)}).accepted)
                    restart=host.key('keyboard.key.r')
                    self.assertTrue(restart.accepted,restart.message)
                    self.assertEqual(restart.host_commands,('restart',))
                    host.refresh_scene()
                    self.assertFalse(host.controller.snapshot().terminal)
                    self.assertFalse(host.presentation.diagnostics())
                before=host.controller.snapshot().state_hash
                quit_result=host.key('keyboard.key.escape')
                self.assertTrue(quit_result.accepted)
                self.assertTrue(host.quit_requested)
                self.assertEqual(quit_result.transition_count,0)
                self.assertEqual(host.controller.snapshot().state_hash,before)
            finally: host.close()
        self.assertEqual(supplied,['rule_ir','asset_ir','scene_ir','input_ir'])

    def test_host_commands_are_explicit_allowlisted_digital_requests(self):
        for target in [{'kind':'host_command','command':'shell'},
                       {'kind':'host_command','command':'quit','path':'anything'},
                       {'kind':'host_command','command':'restart','parameters':{}}]:
            doc=new_input_ir('input:test.host','Host test')
            doc['intents']=[{'id':'input:intent.test','name':'Test','required':True,'value_type':'digital','target':target}]
            self.assertTrue(any(e.code=='intent.host_command' for e in validate_input_ir(doc)))


if __name__=='__main__': unittest.main()
