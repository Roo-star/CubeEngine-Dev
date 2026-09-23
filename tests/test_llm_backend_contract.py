"""Independent contract matrix and original paid response replay; no network."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from srtp.ir_contracts import errors
from srtp.scene_ir_v2.component_contracts import COMPONENTS,component_schema,backend_diagnostics
from srtp.asset_ir_v2.recipe_contracts import RECIPES,backend_diagnostics as asset_diagnostics
from srtp.input_adapter_contract import KEY_NAMES,keyboard_event,backend_diagnostics as input_diagnostics
from srtp.llm_compiler_v1.backend_contract import check_alignment
from srtp.llm_compiler_v1.program_builder import authoring_schema,definition_proposal,DefinitionValidationError
from srtp.llm_compiler_v1.compiler import SourceToIRCompiler,bootstrap_documents
from srtp.llm_compiler_v1.staged import SCHEMAS,_execute_stage,run_behavior_tests
from srtp.llm_compiler_v1.evidence import build_evidence_pack
from srtp.llm_compiler_v1.source_workspace import SourceWorkspace
from srtp.llm_compiler_v1.validation import validate_and_apply_proposal
from srtp.source_importer import SourceGameImporter
from tests.test_engine_conversion_pipeline import ROOT,reference_definition,tests_for_board

MATRIX=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]
COMPONENT_CASES={
    'renderer':{'geometry':'builtin:cube','visible':True,'marker':None,'color':[.2,.3,.4,1]},
    'collider':{'shape':'box','size':[1,1,1],'is_trigger':False,'selectable':True},
    'camera':{'projection':'perspective','near_clip':.1,'far_clip':100,'active':True,'fov':60},
    'light':{'kind':'ambient','intensity':1,'color':[1,1,1]},
    'topology_visualizer':{'rule_topology':'rule:topology.board','prefab':'scene:prefab.cell','index_to_world':MATRIX},
    'rule_entity_visualizer':{'rule_entity_type':'rule:entity.piece','prefab':'scene:prefab.cell','index_to_world':MATRIX},
    'ui_canvas':{'mode':'overlay','text':'Score','position':[-.5,.4],'size':[.3,.1],'scale':1,'background':[.1,.1,.1,1]},
    'audio_source':{'clip':'asset:audio.test','volume':.5,'loop':False,'playing':False,'trigger':0},
    'authoring_marker':{'label':'test'},
}
RECIPE_CASES={
    'identity':{},'atlas_region':{'x':0,'y':0,'width':1,'height':1},
    'billboard':{'size':[1,1],'facing':'camera','double_sided':True},
    'extrusion':{'depth':.1,'axis':'z'},'cube_face_projection':{'faces':'all','uv_policy':'stretch'},
    'procedural_mesh':{'primitive':'cube','dimensions':[1,1,1]},
}


class BackendContractTests(unittest.TestCase):
    def setUp(self):
        self.package=SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_tictactoe/main.py')
        self.evidence=build_evidence_pack(self.package)
        self.docs=bootstrap_documents(title=self.package.title,source_package_hash=self.evidence['source_package_hash']).documents
        self.workspace=SourceWorkspace(Path(self.package.root),Path(self.package.entrypoint))

    def apply(self,payload,slot):
        proposal=definition_proposal(payload,slot=slot,documents=self.docs,evidence_pack=self.evidence,job_id='job:contract',
            source_root=Path(self.package.root),visual_catalog=self.workspace.visuals)
        applied=validate_and_apply_proposal(proposal,self.docs,evidence_pack=self.evidence,source_root=Path(self.package.root))
        self.assertTrue(applied.ok,applied.diagnostics); self.docs=applied.documents

    def test_every_scene_component_has_executable_property_contract(self):
        from srtp.scene_ir_v2.scene_ir import _validate_component_properties
        self.assertEqual(set(COMPONENT_CASES),set(COMPONENTS))
        for kind,props in COMPONENT_CASES.items():
            with self.subTest(kind=kind):
                self.assertFalse(errors(props,COMPONENTS[kind]))
                diagnostics=[]; _validate_component_properties(kind,props,'/component',diagnostics)
                self.assertFalse(diagnostics,diagnostics)
                if kind!='authoring_marker':
                    wrong=dict(props,invented_field=1)
                    self.assertTrue(errors(wrong,COMPONENTS[kind]))
        for props in [{'shape':'sphere','radius':1,'is_trigger':False,'selectable':True},
                      {'shape':'box','size':[1,1,1],'is_trigger':False,'selectable':True}]:
            self.assertFalse(errors(props,COMPONENTS['collider']))
        for wrong in ('cube','capsule','mesh'):
            self.assertTrue(errors(dict(COMPONENT_CASES['collider'],shape=wrong),COMPONENTS['collider']))

    def test_model_schema_includes_same_contract_plus_explicit_color_lowering(self):
        schema=authoring_schema(json.loads((ROOT/'srtp'/SCHEMAS['scene_ir']).read_text()))
        components=schema['$defs']['component']
        for kind,props in COMPONENT_CASES.items():
            self.assertFalse(errors({'id':'test','type':kind,'enabled':True,'properties':props},components))
        renderer={'id':'renderer','type':'renderer','enabled':True,'properties':
            dict(COMPONENT_CASES['renderer'],color={'source_visual':'visual:test'})}
        self.assertFalse(errors(renderer,components))
        self.assertTrue(errors(renderer,component_schema()),'Runtime requires resolved color, not authoring shorthand')
        variants={v['properties']['type']['const']:v for v in components['oneOf']}
        self.assertIn('required',variants['collider']['properties']['properties']['oneOf'][0])
        self.assertTrue(schema['$defs']['binding']['properties']['source']['oneOf'])

    def test_recipe_contract_matrix_and_backend_exclusions(self):
        self.assertEqual(set(RECIPE_CASES),set(RECIPES))
        for strategy,settings in RECIPE_CASES.items():
            self.assertFalse(errors(settings,RECIPES[strategy]))
            self.assertFalse(asset_diagnostics({'derivations':[{'strategy':strategy,'settings':settings}]}))
            self.assertTrue(errors(dict(settings,invented_field=True),RECIPES[strategy]))
        for strategy in ('mesh_substitution','custom_renderer'):
            self.assertTrue(asset_diagnostics({'derivations':[{'strategy':strategy,'settings':{}}]}))

    def test_original_response_reports_all_independent_scene_errors_in_one_pass(self):
        fixture=json.loads((ROOT/'tests/fixtures/llm_scene_failure_20260923.json').read_text(encoding='utf-8'))
        for slot in ('rule_ir','asset_ir'): self.apply(fixture['accepted_stages'][slot],slot)
        with self.assertRaises(DefinitionValidationError) as raised:
            self.apply(fixture['rejected_scene'],'scene_ir')
        issues=raised.exception.diagnostics; text='\n'.join(issues)
        for field in ('/shape','/is_trigger','/selectable','/near_clip','/orthographic_size','/rule_topology','/index_to_world','/bindings/0/source','/bindings/0/target','/bindings/0/transform'):
            self.assertIn(field,text)
        self.assertGreater(len(issues),20)
        self.assertEqual(self.docs['scene_ir']['nodes'],[],'Rejected scene must not replace accepted documents')

    def test_paid_rule_winner_state_no_longer_silently_loses_winner(self):
        from srtp.ir_v2 import compile_rule_ir
        fixture=json.loads((ROOT/'tests/fixtures/llm_scene_failure_20260923.json').read_text(encoding='utf-8'))
        payload=fixture['accepted_stages']['rule_ir']; original=deepcopy(payload)
        self.apply(payload,'rule_ir'); self.assertEqual(payload,original)
        self.assertNotIn('winner_state',self.docs['rule_ir']['outcomes'][0]['result'])
        self.assertEqual(len(run_behavior_tests(self.docs['rule_ir'],payload['behavior_tests'])),3)
        runtime=compile_rule_ir(self.docs['rule_ir'])
        try:
            for step in payload['behavior_tests'][1]['steps']:
                if not step['accepted']: continue
                action=next(a for a in runtime.all_actions() if a.action_id==step['action'] and dict(a.parameters)=={k:tuple(v) if isinstance(v,list) else v for k,v in step['parameters'].items()})
                runtime.apply_action(action)
            self.assertEqual(runtime.evaluate_outcome().winners,(1,))
        finally: runtime.close()

    def test_rule_expression_shapes_cover_all_runtime_ops(self):
        from srtp.ir_v2.expression_contracts import expression_schema
        from srtp.ir_v2.rule_ir import _EXPRESSION_OPS
        schema=expression_schema(False); ops={s['properties']['op']['const'] for s in schema['oneOf']}
        self.assertEqual(ops,_EXPRESSION_OPS)
        root={'$defs':{'expression':schema}}
        self.assertTrue(errors({'op':'call','function':'core:state.get'},schema,root=root))
        self.assertTrue(errors({'op':'not','args':[]},schema,root=root))
        self.assertFalse(errors({'op':'not','args':[{'op':'literal','value':False}]},schema,root=root))

    def test_keyboard_profile_matches_real_event_adapter_including_page_up(self):
        for key,name in KEY_NAMES.items():
            self.assertEqual(keyboard_event(key),('keyboard.key.'+name,'press'))
            self.assertEqual(keyboard_event(key+' up'),('keyboard.key.'+name,'release'))
        self.assertIsNone(keyboard_event('scroll up'))
        self.assertTrue(input_diagnostics({'intents':[],'bindings':[{'trigger':{'kind':'control','device':'gamepad','control':'gamepad.button.a','phase':'press'}}]}))

    def test_contract_drift_blocks_before_any_model_call(self):
        self.assertFalse(check_alignment())
        calls=[]
        with patch('srtp.llm_compiler_v1.backend_contract.check_alignment',return_value=['synthetic drift']):
            report=SourceToIRCompiler(chat_fn=lambda **kw:calls.append(kw)).compile(self.package)
        self.assertEqual(calls,[]); self.assertEqual(report.attempts,0)
        self.assertIn('contract preflight',report.diagnostics[0])

    def test_scene_gate_replays_rule_transitions_not_only_initial_state(self):
        from srtp.scene_ir_v2 import seal_scene_ir
        for slot in ('rule_ir','asset_ir','scene_ir'):
            self.apply({'definition':reference_definition(slot),'evidence':[self.evidence['evidence'][0]['evidence_id']]},slot)
        report=_execute_stage('scene_ir',self.docs,Path(self.package.root),tests_for_board())
        self.assertGreater(report[0]['replayed_frames'],1)
        # Color initially valid, invalid only when Rule changes the cell value.
        scene=deepcopy(self.docs['scene_ir'])
        binding=scene['bindings'][0]; binding['target']['property']='color'
        binding['transform']={'kind':'map','cases':[{'equals':0,'value':[.2,.2,.2,1]},
            {'equals':1,'value':[999,0,0,1]},{'equals':2,'value':[0,0,0,1]}]}
        self.docs['scene_ir']=seal_scene_ir(scene)
        with self.assertRaisesRegex(ValueError,'Scene replay'):
            _execute_stage('scene_ir',self.docs,Path(self.package.root),tests_for_board())

    def test_input_gate_uses_actual_pickable_scene_and_host_event_data(self):
        from srtp.scene_ir_v2 import seal_scene_ir
        from srtp.input_ir_v2 import seal_input_ir
        for slot in ('rule_ir','asset_ir','scene_ir','input_ir'):
            self.apply({'definition':reference_definition(slot),'evidence':[self.evidence['evidence'][0]['evidence_id']]},slot)
        report=_execute_stage('input_ir',self.docs,Path(self.package.root),tests_for_board())
        self.assertTrue(report[0]['physical_routes'])
        original=deepcopy(self.docs)
        for component in self.docs['scene_ir']['prefabs'][0]['root']['components']:
            if component['type']=='collider': component['properties']['selectable']=False
        self.docs['scene_ir']=seal_scene_ir(self.docs['scene_ir'])
        with self.assertRaisesRegex(ValueError,'not reachable'):
            _execute_stage('input_ir',self.docs,Path(self.package.root),tests_for_board())
        self.docs=original
        # A key event cannot provide a picked grid coordinate. Synthetic input
        # tests must not supply data that the real ProjectHost never sends.
        for binding in self.docs['input_ir']['bindings']:
            binding['trigger'].update(device='keyboard',control='keyboard.key.space')
        self.docs['input_ir']=seal_input_ir(self.docs['input_ir'])
        with self.assertRaisesRegex(ValueError,'event.data.rule_coordinate'):
            _execute_stage('input_ir',self.docs,Path(self.package.root),tests_for_board())

    def test_state_dependent_empty_catalogue_is_not_mistaken_for_working_input(self):
        rule=reference_definition('rule_ir'); delayed=deepcopy(rule['actions'][0])
        delayed['id']='rule:action.inspect'; delayed['name']='Inspect after placement'
        domain=delayed['parameters'][0]['domain']
        delayed['parameters'][0]['domain']={'op':'if','condition':
            {'op':'eq','args':[{'op':'call','function':'core:grid.get','args':[
                {'op':'literal','value':'rule:state.board_cell'},{'op':'literal','value':[0,0]}]},
                {'op':'literal','value':1}]},'then':domain,'else':{'op':'list','items':[]}}
        rule['actions'].append(delayed)
        for slot in ('rule_ir','asset_ir','scene_ir','input_ir'):
            definition=rule if slot=='rule_ir' else reference_definition(slot)
            if slot=='input_ir': definition['intents'][0]['target']['action']='rule:action.inspect'
            self.apply({'definition':definition,'evidence':[self.evidence['evidence'][0]['evidence_id']]},slot)
        with self.assertRaisesRegex(ValueError,'not reachable'):
            _execute_stage('input_ir',self.docs,Path(self.package.root),[])
        with self.assertRaisesRegex(ValueError,'not reachable'):
            _execute_stage('input_ir',self.docs,Path(self.package.root),tests_for_board())

    def test_binding_property_is_checked_against_actual_component_kind(self):
        definition=reference_definition('scene_ir')
        target=definition['bindings'][0]['target']; target['property']='fov'
        issues=backend_diagnostics(definition)
        self.assertTrue(any('not implemented for renderer' in issue for issue in issues),issues)

    def test_rejected_paid_stage_is_revalidated_before_first_resumed_repair(self):
        calls=[]; scene_attempts=0
        def chat(messages,**kwargs):
            nonlocal scene_attempts
            payload=json.loads(messages[-1]['content']); slot=payload['stage']; calls.append(slot)
            definition=reference_definition(slot)
            if slot=='scene_ir':
                scene_attempts+=1
                if scene_attempts==1:
                    definition['prefabs'][0]['root']['components'][1]['properties']['shape']='cube'
                else:
                    self.assertTrue(payload['repair_diagnostics'])
                    self.assertIn('/shape','\n'.join(payload['repair_diagnostics']))
                    self.assertEqual(payload['previous_definition']['definition']['prefabs'][0]['root']['components'][1]['properties']['shape'],'cube')
            return json.dumps({'definition':definition,'evidence':[self.evidence['evidence'][0]['evidence_id']],
                'behavior_tests':tests_for_board() if slot=='rule_ir' else []})
        with tempfile.TemporaryDirectory() as tmp:
            destination=Path(tmp)/'source'
            first=SourceToIRCompiler(chat_fn=chat,max_repairs=0).compile(self.package,out_dir=destination)
            self.assertFalse(first.ok)
            checkpoint=json.loads(destination.with_name('source.stages.json').read_text())
            self.assertEqual(set(checkpoint['stages']),{'rule_ir','asset_ir'})
            self.assertEqual(set(checkpoint['rejected_stages']),{'scene_ir'})
            resumed=SourceToIRCompiler(chat_fn=chat,max_repairs=0).compile(self.package,out_dir=destination)
            self.assertTrue(resumed.ok,resumed.diagnostics)
            self.assertEqual(calls,['rule_ir','asset_ir','scene_ir','scene_ir','input_ir'])
            checkpoint=json.loads(destination.with_name('source.stages.json').read_text())
            self.assertFalse(checkpoint['rejected_stages'])


if __name__=='__main__': unittest.main()
