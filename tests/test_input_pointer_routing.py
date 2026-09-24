"""Generic input patterns plus saved field replay. No paid model calls.

The accepted Scene is the user's real new output. Input repair below is
explicitly authored test data, never installed in a user bundle or cache.
"""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.input_ir_v2 import new_input_ir,seal_input_ir,compile_input_ir,PhysicalInputEvent,InputCompileError
from srtp.input_pointer_contract import pointer_data,pointer_matches,pointer_filter_errors,scene_pick_context
from srtp.pointer_gesture import PointerGesture
from srtp.llm_compiler_v1.behavior_runtime import test_runtime
from srtp.scene_ir_v2 import compile_scene_ir
from srtp.asset_ir_v2 import compile_asset_ir
from srtp.project_manifest_v2.compiler import ProjectSession

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'srtp/reference_games/pygame_minesweeper'
FIELD=json.loads((ROOT/'tests/fixtures/field_runs_20260924/minesweeper_input.json').read_text(encoding='utf-8'))


def authored_input_repair():
    response=deepcopy(FIELD['rejected_input']);definition=response['definition']
    for context in definition['contexts']:
        if context['id']=='input:context.board':context['consume_policy']='first_legal'
    for binding in definition['bindings']:
        if binding['trigger']['device']=='mouse':
            binding['trigger']['pointer']={'node':'scene:board','subtree':True,'gesture':'click'}
    face=deepcopy(definition['bindings'][1])
    face.update(id='input:binding.face-restart',name='Source restart button',slot='primary',priority=200)
    face['trigger']={'kind':'control','device':'mouse','control':'mouse.button.primary','phase':'release',
        'modifiers':[],'modifier_policy':'exact','pointer':{'node':'scene:face','gesture':'click'}}
    definition['bindings'].append(face)
    definition['unresolved']=[]
    return response


def input_document(response=None):
    doc=new_input_ir('input:game.pointer-field')
    doc.update((response or authored_input_repair())['definition'])
    doc['dependencies']={'rule_ir':{k:FIELD['documents']['rule_ir'][k] for k in ('document_id','content_hash')},'extensions':[]}
    return seal_input_ir(doc)


class PointerRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rule=FIELD['documents']['rule_ir'];cls.assets=compile_asset_ir(FIELD['documents']['asset_ir'],SOURCE)
        cls.scene=compile_scene_ir(FIELD['documents']['scene_ir'],rule_document=cls.rule,asset_catalog=cls.assets)
        cls.inputs=compile_input_ir(input_document(),rule_document=cls.rule)

    def session(self):
        runtime=test_runtime(self.rule,{})
        session=ProjectSession(runtime,self.scene,self.assets,self.inputs);self.addCleanup(session.close)
        return session

    def cell(self):
        node=next(n for n in self.scene.nodes if tuple(n.rule_context.get('coordinate',()))==(0,0) and any(c.kind=='collider' for c in n.components))
        return scene_pick_context(self.scene.nodes_by_id,node.identifier,node.rule_context)

    def event(self,sequence,context,control='mouse.button.primary',phase='release',click=True):
        return PhysicalInputEvent(sequence,'mouse',control,phase,position=(0,0),data=pointer_data(context,click=click))

    def test_original_unresolved_remains_rejected(self):
        with self.assertRaisesRegex(InputCompileError,'face-restart'):
            compile_input_ir(input_document(FIELD['rejected_input']),rule_document=self.rule)

    def test_first_legal_skips_illegal_high_priority_and_commits_only_one(self):
        for cover,wanted in ((0,'rule:action.reveal'),(2,'rule:action.clear_question')):
            with self.subTest(cover=cover):
                session=self.session();session.rule_runtime.state.grids['rule:state.cover'][(0,0)]=cover
                result=session.handle_input(self.event(1,self.cell()))
                self.assertEqual(len(result.transitions),1);self.assertFalse(result.rejections)
                self.assertEqual(result.dispatch.intents[0].rule_action_request.action_id,wanted)
                if cover==2:self.assertEqual(session.rule_runtime.state.grids['rule:state.cover'][(0,0)],0)
                self.assertFalse(result.host_commands)

    def test_confirmed_click_required_and_filtered_button_never_restarts_board(self):
        session=self.session();before=session.rule_runtime.state.state_hash()
        for i,context in enumerate(({},self.cell(),{'node_id':'scene:face.other'}),1):
            result=session.handle_input(self.event(i,context,click=False))
            self.assertFalse(result.transitions or result.host_commands)
        self.assertEqual(before,session.rule_runtime.state.state_hash())
        session.rule_runtime.state.globals['rule:state.result']=1
        result=session.handle_input(self.event(4,{'node_id':'scene:face'}))
        self.assertEqual(result.host_commands,('restart',));self.assertFalse(result.transitions)

    def test_drag_other_target_overlay_and_clear_cancel_before_rule_dispatch(self):
        session=self.session();gesture=PointerGesture();cell=self.cell();before=session.rule_runtime.state.state_hash()
        for end,drag,clear in (({'node_id':'scene:face'},False,False),({},False,False),(cell,True,False),(cell,False,True)):
            gesture.press('mouse.button.primary',(0,0),cell)
            if drag:gesture.move((.1,0))
            if clear:gesture.clear()
            self.assertIsNone(gesture.release('mouse.button.primary',(0,0),end))
        self.assertEqual(before,session.rule_runtime.state.state_hash())

    def test_neutral_target_filters_and_disjoint_same_priority_buttons(self):
        for i in range(15):
            name='scene:control.'+chr(97+i)
            self.assertTrue(pointer_matches({'node':name,'gesture':'click'},pointer_data({'node_id':name},click=True)))
            self.assertFalse(pointer_matches({'node':name},pointer_data({'node_id':name+'.child'},click=True)))
            self.assertTrue(pointer_matches({'node':name,'subtree':True},pointer_data({'node_id':'scene:unrelated-name','node_path':['scene:unrelated-name',name]},click=True)))
            self.assertFalse(pointer_matches({'node':name,'subtree':True},pointer_data({'node_id':name+'.not-a-child','node_path':[name+'.not-a-child']},click=True)))
            self.assertFalse(pointer_matches({'topology':'rule:grid.a'},pointer_data({'node_id':name,'rule_topology':'rule:grid.b'},click=True)))
        doc=input_document();face=doc['bindings'][-1];other=deepcopy(face)
        other.update(id='input:binding.second-control',name='Another button')
        other['trigger']['pointer']['node']='scene:another-control'
        doc['bindings'].append(other)
        compile_input_ir(seal_input_ir(doc),rule_document=self.rule)

    def test_malformed_filters_and_first_legal_without_rule_runtime_rejected(self):
        for condition in ({'node':3},{'gesture':'drag'},{'subtree':True},{'node':'face'},{'unknown':1}):
            self.assertTrue(pointer_filter_errors(condition,'/pointer'))
        with self.assertRaisesRegex(ValueError,'authoritative Rule runtime'):
            self.inputs.create_router().dispatch(self.event(1,self.cell()))

    def test_production_input_gate_checks_the_button_even_with_keyboard_fallback(self):
        from srtp.llm_compiler_v1.input_acceptance import verify_host_routes
        checked=verify_host_routes(self.inputs,self.rule,self.scene,self.assets,FIELD['behavior_tests'])
        self.assertIn('input:binding.face-restart',checked)
        bad=input_document();bad['bindings'][-1]['trigger']['pointer']['node']='scene:absent'
        with self.assertRaisesRegex(ValueError,'unknown Scene node'):
            verify_host_routes(compile_input_ir(seal_input_ir(bad),rule_document=self.rule),self.rule,self.scene,self.assets,FIELD['behavior_tests'])

    def test_real_project_host_click_restart_and_cancellation(self):
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        from srtp.llm_compiler_v1.approval import approve_llm_manifest_file
        from srtp.source_importer import SourceGameImporter
        from srtp.project_viewer import ProjectHost
        calls=[]
        def chat(messages,**kwargs):
            payload=json.loads(messages[-1]['content']);slot=payload['stage'];calls.append(slot)
            if slot=='input_ir':return json.dumps(authored_input_repair())
            return json.dumps({'definition':FIELD['documents'][slot],
                'evidence':[payload['evidence_pack']['evidence'][0]['evidence_id']],
                'behavior_tests':FIELD['behavior_tests'] if slot=='rule_ir' else []})
        with tempfile.TemporaryDirectory() as tmp:
            package=SourceGameImporter().import_path(SOURCE/'run_game.py')
            report=SourceToIRCompiler(chat_fn=chat,max_repairs=0).compile(package,out_dir=Path(tmp)/'source')
            self.assertTrue(report.ok,report.diagnostics)
            path=Path(report.output_dir)/'project.manifest.json';approve_llm_manifest_file(path)
            host=ProjectHost(path)
            try:
                marked=host.mouse_click('mouse.button.secondary',self.cell());self.assertTrue(marked.accepted,marked.message)
                previous=host.controller.sessions[host.controller.active_key]
                self.assertEqual(previous.rule_runtime.state.grids['rule:state.cover'][(0,0)],1)
                reset=host.mouse_click('mouse.button.primary',{'node_id':'scene:face'})
                self.assertTrue(reset.accepted,reset.message);self.assertEqual(reset.host_commands,('restart',))
                current=host.controller.sessions[host.controller.active_key]
                self.assertIsNot(previous,current);self.assertEqual(current.rule_runtime.state.grids['rule:state.cover'][(0,0)],0)
                self.assertFalse(host.mouse('mouse.button.primary',{'node_id':'scene:face'},'release').accepted)
            finally:host.close()
        self.assertEqual(calls,['rule_ir','asset_ir','scene_ir','input_ir'])


if __name__=='__main__':unittest.main()
