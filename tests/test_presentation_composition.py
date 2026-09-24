"""Pattern-level, field-replay and production-stage tests; no live model calls."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.scene_ir_v2.binding_expressions import OPERATORS, validate_expression, evaluate_expression
from srtp.scene_ir_v2.component_contracts import binding_source_errors
from srtp.scene_ir_v2 import compile_scene_ir, seal_scene_ir, SceneCompileError
from srtp.scene_presentation import ScenePresentation
from srtp.asset_ir_v2 import compile_asset_ir
from srtp.llm_compiler_v1.behavior_runtime import test_runtime
from srtp.llm_compiler_v1.source_workspace import SourceWorkspace
from tests.test_scene_font_field_failures import ROOT,FIX,field_scene


def literal(value):return {'op':'literal','value':value}
def operation(op,*args):return {'op':op,'args':list(args)}
def read(source):return {'op':'read','source':source}


def conditional_scene_payload():
    """Explicitly authored test repair of saved model output, never a product bundle."""
    payload=json.loads((FIX/'rejected_conditional_scene.json').read_text(encoding='utf-8'))
    scene=payload['definition']
    for binding in scene['bindings']:
        if binding['id'] not in ('scene:binding.unopened-press','scene:binding.question-press'):continue
        expected=0 if binding['id'].endswith('unopened-press') else 2
        expression=operation('all',read(binding['source']),
            operation('eq',read({'kind':'state','scope':'topology_site','variable':'rule:state.cover'}),literal(expected)),
            operation('eq',read({'kind':'state','scope':'global','variable':'rule:state.result'}),literal(0)))
        binding['source']={'kind':'expression','expression':expression}
    scene['unresolved']=[];payload['unresolved']=[]
    root=ROOT/'srtp/reference_games/pygame_minesweeper'
    # Use the production visual-evidence resolver just as the builder does;
    # saved model responses can contain source_visual descriptors, not RGBA.
    payload['definition']=SourceWorkspace(root,root/'run_game.py').visuals.resolve(scene)
    return payload


class PresentationCompositionTests(unittest.TestCase):
    def test_entire_operator_registry_with_independent_expected_values(self):
        cases={
            'all':([True,False],False),'any':([False,True],True),'not':([True],False),
            'eq':([3,3],True),'ne':([3,4],True),'lt':([2,3],True),'le':([3,3],True),
            'gt':([4,3],True),'ge':([3,3],True),'add':([2,3,4],9),'subtract':([3,5],-2),
            'multiply':([2,3,4],24),'divide':([7,2],3.5),'modulo':([7,3],1),
            'floor':([2.9],2),'min':([3,2,5],2),'max':([3,2,5],5),'if':([False,10,20],20)}
        self.assertEqual(set(cases),set(OPERATORS))
        for op,(values,wanted) in cases.items():
            expr=operation(op,*(literal(v) for v in values))
            self.assertEqual(validate_expression(expr,binding_source_errors)[0],[])
            self.assertEqual(evaluate_expression(expr,lambda _:None),wanted,op)
        invalid=operation('divide',literal(1),literal(0))
        self.assertEqual(evaluate_expression(operation('if',literal(False),invalid,literal(7)),lambda _:None),7)

    def test_expressions_are_bounded_typed_and_read_only(self):
        for value in ({'op':'state.set','args':[]},{'op':'eval','value':'open("file")'},
                      operation('all',literal(True),{'op':'read','source':{'kind':'expression'}}),
                      {'op':'literal','value':float('inf')}, {'op':{'unexpected':1}}):
            self.assertTrue(validate_expression(value,binding_source_errors)[0])
        deep=literal(True)
        for _ in range(20):deep=operation('not',deep)
        self.assertTrue(validate_expression(deep,binding_source_errors)[0])
        for expr in (operation('all',literal(1)),operation('divide',literal(1),literal(0))):
            with self.assertRaises(ValueError):evaluate_expression(expr,lambda _:None)

    def test_neutral_patterns_are_independent_of_identifiers_and_state_encodings(self):
        # Locked buttons, turn gating, hidden cards and charge bars all use the
        # same composition machinery. No game names or specific encodings enter it.
        for index in range(20):
            enabled='rule:state.allowed'+str(index);amount='rule:state.value'+str(index)
            reads={enabled:True,amount:index}
            flag=read({'kind':'state','scope':'global','variable':enabled})
            number=read({'kind':'state','scope':'global','variable':amount})
            lookup=lambda source:reads[source['variable']]
            for active in (False,True):
                reads[enabled]=active
                patterns=[(operation('all',flag,operation('gt',number,literal(5))),active and index>5),
                          (operation('if',flag,literal('front'),literal('back')),'front' if active else 'back'),
                          (operation('min',literal(1),operation('divide',number,literal(10))),min(1,index/10)),
                          (operation('any',flag,operation('eq',number,literal(0))),active or index==0)]
                for expr,wanted in patterns:
                    self.assertEqual(validate_expression(expr,binding_source_errors)[0],[])
                    self.assertEqual(evaluate_expression(expr,lookup),wanted)

    def test_actual_field_state_interaction_cross_product(self):
        rule,asset,scene,root=field_scene();scene.update(conditional_scene_payload()['definition'])
        scene=seal_scene_ir(scene);assets=compile_asset_ir(asset,root)
        compiled=compile_scene_ir(scene,rule_document=rule,asset_catalog=assets)
        runtime=test_runtime(rule,{});self.addCleanup(runtime.close)
        graph=ScenePresentation(compiled,assets);projection=compiled.create_projection_session()
        graph.synchronize(projection,runtime.state)
        targets={c:next(n for n,node in graph.nodes.items() if c in node['components'])
                 for c in ('press_unopened_sprite','press_question_sprite')}
        first=graph.nodes[targets['press_unopened_sprite']]
        coordinate=tuple(first['rule_context']['coordinate'])
        context=dict(first['rule_context'],node_id=targets['press_unopened_sprite'])
        for cover in range(7):
            for result in range(3):
                for phase in ('idle','primary','secondary'):
                    runtime.state.grids['rule:state.cover'][coordinate]=cover
                    runtime.state.globals['rule:state.result']=result
                    before=runtime.state.state_hash()
                    interaction={} if phase=='idle' else {'hovered':context,'pressed':{'mouse.button.'+phase:context}}
                    graph.synchronize(projection,runtime.state,interaction)
                    self.assertFalse(graph.diagnostics())
                    for component,expected_cover in [('press_unopened_sprite',0),('press_question_sprite',2)]:
                        self.assertEqual(graph.renderer(targets[component],component)['visible'],
                            phase=='primary' and cover==expected_cover and result==0,(cover,result,phase,component))
                    self.assertEqual(runtime.state.state_hash(),before)

    def test_inactive_expression_branch_still_validates_state_reference(self):
        rule,asset,scene,root=field_scene();scene.update(conditional_scene_payload()['definition'])
        binding=next(b for b in scene['bindings'] if b['source']['kind']=='expression')
        binding['source']['expression']=operation('if',literal(False),
            read({'kind':'state','scope':'global','variable':'rule:state.absent'}),literal(False))
        with self.assertRaisesRegex(SceneCompileError,'unknown Rule state'):
            compile_scene_ir(seal_scene_ir(scene),rule_document=rule,asset_catalog=compile_asset_ir(asset,root))

    def test_different_missing_capabilities_have_different_repair_feedback(self):
        rule,asset,scene,root=field_scene();assets=compile_asset_ir(asset,root);messages=[]
        for index in range(2):
            bad=deepcopy(scene);bad['unresolved']=[{'path':'/nodes/test','reason':'Missing capability '+str(index),'required':True,'owner':'backend'}]
            with self.assertRaises(SceneCompileError) as error:compile_scene_ir(seal_scene_ir(bad),rule_document=rule,asset_catalog=assets)
            messages.append(str(error.exception))
        self.assertNotEqual(*messages)
        self.assertIn('Missing capability 1',messages[1])

    def test_cached_source_reads_are_rehydrated_only_with_current_hash(self):
        root=ROOT/'srtp/reference_games/pygame_minesweeper'
        workspace=SourceWorkspace(root,root/'run_game.py')
        references=conditional_scene_payload()['evidence']
        self.assertGreater(workspace.restore_evidence_reads(references),5)
        context=workspace.initial_context()
        self.assertTrue(any(s['path']=='minesweeper/user_interface_board.py' for s in context['source']))
        fresh=SourceWorkspace(root,root/'run_game.py')
        self.assertEqual(fresh.restore_evidence_reads([references[0].split('@')[0]+'@'+'0'*64,'../../.env:1-2@'+'0'*64]),0)

    def test_production_repair_loop_distinguishes_progress_and_publishes_test_bundle(self):
        from srtp.source_importer import SourceGameImporter
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        from tests.test_engine_conversion_pipeline import reference_definition,tests_for_board
        package=SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_tictactoe/main.py')
        requests=[];scene_attempts=0
        def chat(messages,**kwargs):
            nonlocal scene_attempts
            payload=json.loads(messages[-1]['content']);slot=payload['stage'];requests.append(payload)
            definition=reference_definition(slot)
            if slot=='scene_ir':
                scene_attempts+=1
                if scene_attempts<3:
                    definition['unresolved']=[{'path':'/nodes/view','reason':'Missing feature '+str(scene_attempts),'owner':'test','required':True}]
                else:
                    # Conditional feedback is part of the output accepted by
                    # the real builder, validator, runtime and saved artifact.
                    old=definition['bindings'][0]['source']
                    definition['bindings'][0]['source']={'kind':'expression','expression':
                        operation('if',operation('eq',literal(1),literal(1)),read(old),literal(0))}
            return json.dumps({'definition':definition,'evidence':[payload['evidence_pack']['evidence'][0]['evidence_id']],
                'behavior_tests':tests_for_board() if slot=='rule_ir' else []})
        with tempfile.TemporaryDirectory() as tmp:
            result=SourceToIRCompiler(chat_fn=chat,max_repairs=2).compile(package,out_dir=Path(tmp)/'source')
            self.assertTrue(result.ok,result.diagnostics)
            self.assertTrue((Path(result.output_dir)/'project.manifest.json').is_file())
        repairs=[p for p in requests if p['stage']=='scene_ir']
        self.assertEqual(len(repairs),3)
        self.assertIn('Missing feature 1',' '.join(repairs[1]['repair_diagnostics']))
        self.assertIn('Missing feature 2',' '.join(repairs[2]['repair_diagnostics']))
        self.assertIn('presentation_expression',repairs[2]['backend_profile'])

    def test_wrong_type_in_inactive_branch_is_rejected_at_compile(self):
        rule,asset,scene,root=field_scene();scene.update(conditional_scene_payload()['definition'])
        binding=next(b for b in scene['bindings'] if b['source']['kind']=='expression')
        binding['source']['expression']=operation('if',literal(False),operation('all',literal(5)),literal(False))
        with self.assertRaisesRegex(SceneCompileError,'boolean operands'):
            compile_scene_ir(seal_scene_ir(scene),rule_document=rule,asset_catalog=compile_asset_ir(asset,root))

    def test_single_scene_acceptance_blocks_additional_source_roundtrip(self):
        import os
        import httpx
        from unittest.mock import patch
        from tests.run_single_scene_acceptance import SingleRequestClient
        from tests.test_openrouter_transport import completed
        from srtp.llm_compiler_v1.client import LLMTransportError
        requests=[]
        def handle(request):
            body=json.loads(request.content);requests.append(body)
            self.assertEqual(body['provider']['max_price'],{'prompt':2,'completion':10})
            self.assertEqual(body['max_output_tokens'],16384)
            return completed({'source_requests':[{'path':'rules.py'}]})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'main.py').write_text('import rules\n',encoding='utf-8')
            (root/'rules.py').write_text('VALUE=1\n',encoding='utf-8')
            workspace=SourceWorkspace(root,root/'main.py')
            with patch.dict(os.environ,{'OPENROUTER_API_KEY':'offline-test-key'},clear=True),patch('srtp.llm_compiler_v1.client.load_compiler_env'):
                with SingleRequestClient(transport=httpx.MockTransport(handle),max_tokens=16384) as client:
                    with self.assertRaisesRegex(LLMTransportError,'Stopped before another paid request'):
                        workspace.chat(client,[{'role':'system','content':'Test'},{'role':'user','content':'{}'}])
                    self.assertEqual(client.http_requests,1)
                    self.assertEqual(client.responses,[{'source_requests':[{'path':'rules.py'}]}])
                    self.assertEqual(requests[0]['input'][-1]['role'],'user')
                    self.assertEqual(json.loads(requests[0]['input'][-1]['content'])['request_budget']['remaining_http_requests'],1)
        self.assertEqual(len(requests),1)

    def test_pointer_feedback_is_scoped_to_its_board(self):
        from srtp.scene_ir_v2.compiler import _read_interaction
        source={'kind':'interaction','property':'pressed','scope':'target','control':'mouse.button.primary'}
        target={'node_id':'scene:board.b.site.0.overlay','coordinate':(0,0),'rule_topology':'rule:board.b'}
        other={'node_id':'scene:board.a.site.0','coordinate':(0,0),'rule_topology':'rule:board.a'}
        self.assertFalse(_read_interaction(source,target,{'pressed':{'mouse.button.primary':other}}))
        same=dict(other,node_id='scene:board.b.site.0',rule_topology='rule:board.b')
        self.assertTrue(_read_interaction(source,target,{'pressed':{'mouse.button.primary':same}}))

    def test_transient_gate_catches_an_error_absent_at_startup(self):
        from srtp.llm_compiler_v1.presentation_acceptance import verify_transient_presentation
        rule,asset,scene,root=field_scene();scene.update(conditional_scene_payload()['definition'])
        binding=next(b for b in scene['bindings'] if b['source']['kind']=='expression')
        pressed=read({'kind':'interaction','property':'pressed','scope':'target','control':'mouse.button.primary'})
        binding['source']['expression']=operation('if',pressed,literal('invalid-visible'),literal(False))
        assets=compile_asset_ir(asset,root);compiled=compile_scene_ir(seal_scene_ir(scene),rule_document=rule,asset_catalog=assets)
        runtime=test_runtime(rule,{});self.addCleanup(runtime.close)
        graph=ScenePresentation(compiled,assets);projection=compiled.create_projection_session()
        graph.synchronize(projection,runtime.state);self.assertFalse(graph.diagnostics())
        before=runtime.state.state_hash()
        with self.assertRaisesRegex(ValueError,'Transient presentation'):
            verify_transient_presentation(compiled,graph,projection,runtime.state)
        self.assertEqual(before,runtime.state.state_hash())


if __name__=='__main__':unittest.main()
