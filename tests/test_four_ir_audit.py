"""Cross-game contract/runtime regressions. Explicit fixtures; no model API."""
import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from PIL import Image
from srtp.ir_contracts import errors
from srtp.llm_compiler_v1.program_builder import (
    expression, definition_proposal, authoring_schema, DefinitionValidationError,
)
from srtp.llm_compiler_v1.staged import SCHEMAS, _execute_stage
from srtp.ir_v2.expression import ExpressionEvaluator, EvaluationContext, ExpressionError
from srtp.ir_v2 import seal_rule_ir, compile_rule_ir
from srtp.asset_ir_v2 import compile_asset_ir, seal_asset_ir, AssetCompileError
from srtp.scene_ir_v2 import new_scene_ir, seal_scene_ir, compile_scene_ir, identity_transform
from srtp.input_ir_v2 import seal_input_ir
from srtp.input_adapter_contract import backend_diagnostics as input_errors
from tests.test_asset_ir_v2 import asset_fixture
from tests.test_engine_conversion_pipeline import ROOT, reference_definition, tests_for_board


def documents(variant='source'):
    return {slot:json.loads((ROOT/('artifacts/tictactoe_'+variant)/
        (slot.replace('_ir','')+'.'+slot.replace('_ir','')+'-ir.json')).read_text()) for slot in SCHEMAS}


class FourIRAuditTests(unittest.TestCase):
    def test_typed_identifiers_and_operators_never_escape_as_python_container_errors(self):
        docs=documents()
        wanted={'id','parent','layer','kind','type','op','control','variable','context','intent','geometry','clip','texture','font'}
        def paths(value,path=()):
            if isinstance(value,dict):
                for key,child in value.items():
                    if key in wanted and isinstance(child,str): yield path+(key,)
                    yield from paths(child,path+(key,))
            elif isinstance(value,list):
                for i,child in enumerate(value): yield from paths(child,path+(i,))
        for slot in SCHEMAS:
            original=reference_definition(slot)
            for path in paths(original):
                definition=deepcopy(original); target=definition
                for part in path[:-1]:target=target[part]
                target[path[-1]]={'malformed':['value']}
                with self.subTest(slot=slot,path=path), self.assertRaises(DefinitionValidationError):
                    definition_proposal({'definition':definition},slot=slot,documents=docs,
                        evidence_pack={'source_package_hash':'a'*64},job_id='job:mutation')

    def test_floor_division_preserves_python_for_positive_negative_and_nested_operands(self):
        evaluator=ExpressionEvaluator()
        ast=expression('param.n // param.d')
        for n in (-101,-7,-1,0,1,7,101):
            for d in (-8,-3,2,5):
                with self.subTest(n=n,d=d):
                    self.assertEqual(evaluator.evaluate(ast,EvaluationContext(parameters={'n':n,'d':d})),n//d)
        self.assertEqual(evaluator.evaluate(expression('(13 // 3) // 3')),1)
        with self.assertRaises(ExpressionError): evaluator.evaluate(expression('1 // 0'))
        with self.assertRaises(ExpressionError):
            evaluator.evaluate({'op':'div','args':[{'op':'literal','value':7},{'op':'literal','value':3}]})

    def test_all_four_ir_replies_collect_invalid_wire_shapes_before_domain_operations(self):
        docs=documents()
        cases={
            'rule_ir': {'actions':[{'id':{}}], 'topologies':[{'id':[]}]},
            'asset_ir': {'assets':[{'id':{},'source':{'uri':123}}], 'derivations':[{'id':[]}]},
            'scene_ir': {'nodes':[{'id':{},'parent':123}], 'bindings':[{'id':[]}]},
            'input_ir': {'intents':[{'id':{}}], 'bindings':[{'id':[]}]},
        }
        for slot,definition in cases.items():
            with self.subTest(slot=slot), self.assertRaises(DefinitionValidationError) as caught:
                definition_proposal({'definition':definition},slot=slot,documents=docs,
                    evidence_pack={},job_id='job:shape-audit')
            text='\n'.join(caught.exception.diagnostics)
            for collection in definition: self.assertIn('/'+collection+'/0/',text)
            self.assertNotIn('unhashable',text)
        # Partial semantic replacements remain supported; never require the
        # model to resend the entire document just to repair one root field.
        definition_proposal({'definition':{'metadata':docs['rule_ir']['metadata']},'evidence':[]},
            slot='rule_ir',documents=docs,evidence_pack={'source_package_hash':'a'*64},job_id='job:partial')

    def test_asset_recipe_rejects_non_image_and_extrusion_limit_in_asset_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); base,image_path=asset_fixture(root)
            # Start from the same input bytes for all three image recipes.
            for strategy,settings in (
                ('billboard',{'size':[1,1],'facing':'camera','double_sided':True}),
                ('extrusion',{'depth':.2,'axis':'z','alpha_cutoff':1}),
                ('cube_face_projection',{'faces':'all','uv_policy':'stretch'}),
            ):
                doc=deepcopy(base); doc['roles']=[]; doc['presentation_mappings']=[]
                item=deepcopy(doc['derivations'][-1])
                item.update(strategy=strategy,inputs=[doc['assets'][0]['id']],settings=settings,kind='model')
                doc['derivations']=[item]
                catalog=compile_asset_ir(seal_asset_ir(doc),root)
                self.assertEqual(catalog.resource(item['id']).derivation_strategy,strategy)
                bad=deepcopy(doc); data_path=root/'neutral.json'; data_path.write_text('{}')
                source=bad['assets'][0]
                source.update(kind='data',media_type='application/json',importer={'capability':'cubeengine.json','version':'1.0','settings':{}})
                source['source']={'uri':'project://neutral.json','byte_size':2,'content_hash':hashlib.sha256(b'{}').hexdigest()}
                with self.subTest(strategy=strategy), self.assertRaisesRegex(AssetCompileError,'image'):
                    compile_asset_ir(seal_asset_ir(bad),root)
            doc=deepcopy(base); doc['roles']=[]; doc['presentation_mappings']=[]
            item=deepcopy(doc['derivations'][-1]); item.update(strategy='extrusion',kind='model',
                inputs=[doc['assets'][0]['id']],settings={'depth':.2,'axis':'z','alpha_cutoff':0})
            doc['derivations']=[item]
            with self.assertRaisesRegex(AssetCompileError,'alpha_cutoff'):
                compile_asset_ir(seal_asset_ir(doc),root)
            Image.new('RGBA',(129,128),(10,20,30,255)).save(image_path)
            data=image_path.read_bytes(); doc['assets'][0]['source'].update(byte_size=len(data),content_hash=hashlib.sha256(data).hexdigest())
            item['settings']['alpha_cutoff']=1
            with self.assertRaisesRegex(AssetCompileError,'16384'):
                compile_asset_ir(seal_asset_ir(doc),root)

    def test_scene_interaction_uses_parent_tree_not_names(self):
        rule=documents()['rule_ir']; scene=new_scene_ir('scene:audit.hierarchy')
        scene['unresolved']=[]; scene['dependencies']['rule_ir']={k:rule[k] for k in ('document_id','content_hash')}
        def node(identifier,parent=None,components=None):
            return {'id':identifier,'name':identifier,'parent':parent,'active':True,
                'layer':'scene:layer.runtime','transform':identity_transform(),'components':components or []}
        scene['nodes']=[node('scene:panel'),node('scene:renamed-button','scene:panel'),
            node('scene:panel.unrelated'),node('scene:feedback',components=[{'id':'visual','type':'renderer','enabled':True,
                'properties':{'geometry':'builtin:cube','visible':True,'pressed':False}}])]
        scene['bindings']=[{'id':'scene:binding.feedback','name':'Feedback',
            'source':{'kind':'interaction','property':'pressed','scope':'any','node':'scene:panel'},
            'target':{'selector':'node','node':'scene:feedback','component':'visual','property':'pressed'},
            'transform':{'kind':'direct'}}]
        compiled=compile_scene_ir(seal_scene_ir(scene),rule_document=rule)
        projection=compiled.create_projection_session(); runtime=compile_rule_ir(rule); self.addCleanup(runtime.close)
        before=runtime.state.state_hash()
        for node_id,wanted in (('scene:renamed-button',True),('scene:panel.unrelated',False)):
            delta=projection.synchronize(runtime.state,{'pressed':{'mouse.button.primary':{'node_id':node_id}}})
            self.assertEqual(delta.commands[-1].payload['value'],wanted)
        self.assertEqual(before,runtime.state.state_hash())

    def test_input_contract_excludes_unemitted_device_and_keyboard_pointer_pairs(self):
        schema=authoring_schema(json.loads((ROOT/'srtp'/SCHEMAS['input_ir']).read_text()))
        trigger=schema['$defs']['trigger']; root=schema
        for device,control in (('mouse','keyboard.key.r'),('keyboard','mouse.button.primary'),('gamepad','keyboard.key.r')):
            value={'kind':'control','device':device,'control':control,'phase':'press','modifiers':[],'modifier_policy':'exact'}
            self.assertTrue(errors(value,trigger,root=root))
            self.assertTrue(input_errors({'bindings':[{'trigger':value}],'intents':[]}))
        value={'kind':'control','device':'keyboard','control':'keyboard.key.r','phase':'press',
            'modifiers':[],'modifier_policy':'exact','pointer':{'gesture':'click'}}
        self.assertTrue(errors(value,trigger,root=root))
        self.assertTrue(input_errors({'bindings':[{'trigger':value}],'intents':[]}))
        self.assertTrue(input_errors({'contexts':[{'focus':'ui'}]}))
        self.assertTrue(input_errors({'intents':[{'value_type':'text','target':{'kind':'host_command','command':'quit'}}]}))
        self.assertEqual(schema['$defs']['context']['properties']['focus']['enum'],['global','viewport'])
        self.assertNotIn('text',schema['$defs']['intent']['properties']['value_type']['enum'])

    def test_input_gate_does_not_accept_a_route_whose_action_is_always_illegal(self):
        docs=documents(); docs['rule_ir']['actions'][0]['precondition']={'op':'literal','value':False}
        docs['rule_ir']=seal_rule_ir(docs['rule_ir'])
        for slot,sealer in (('scene_ir',seal_scene_ir),('input_ir',seal_input_ir)):
            docs[slot]['dependencies']['rule_ir']={k:docs['rule_ir'][k] for k in ('document_id','content_hash')}
            docs[slot]=sealer(docs[slot])
        with self.assertRaisesRegex(ValueError,'illegal in this state'):
            _execute_stage('input_ir',docs,ROOT,[])

    def test_volume_layout_preserves_logical_picking_and_parent_visibility(self):
        from srtp.scene_presentation import ScenePresentation
        from srtp.input_pointer_contract import scene_pick_context,pointer_data,pointer_matches
        from srtp.llm_compiler_v1.input_acceptance import _pick_data
        docs=documents('target')
        assets=compile_asset_ir(docs['asset_ir'],ROOT)
        scene=compile_scene_ir(docs['scene_ir'],rule_document=docs['rule_ir'],asset_catalog=assets)
        graph=ScenePresentation(scene,assets,legacy_appearance=True,volume_rule=docs['rule_ir'])
        sites=next(iter(scene.topology_sites.values())); cell=next(iter(sites.values()))
        logical_parent=scene.nodes_by_id[cell].parent
        self.assertIsNone(graph.nodes[cell]['parent'],'Grid coordinates remain in common world space')
        context=scene_pick_context(graph.nodes,cell,graph.nodes[cell]['rule_context'])
        self.assertIn(logical_parent,context['node_path'])
        condition={'node':logical_parent,'subtree':True,'gesture':'click'}
        self.assertTrue(pointer_matches(condition,pointer_data(context,click=True)))
        for binding in docs['input_ir']['bindings']:
            if binding['trigger'].get('device')=='mouse': binding['trigger']['pointer']=deepcopy(condition)
        docs['input_ir']=seal_input_ir(docs['input_ir'])
        result=_execute_stage('input_ir',docs,ROOT,tests_for_board(3),spatial=True)
        self.assertTrue(result[0]['physical_routes'])
        self.assertTrue(list(_pick_data(graph)))
        graph.nodes[logical_parent]['active']=False
        self.assertFalse(list(_pick_data(graph)),'Hidden logical board must not remain pickable after volume lowering')

    def test_spatial_plan_is_repaired_before_any_downstream_generation(self):
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        from srtp.llm_compiler_v1.approval import approve_llm_manifest_file
        from srtp.source_importer import SourceGameImporter
        package=SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_tictactoe/main.py')
        target_calls=[]
        def chat(messages,**kwargs):
            payload=json.loads(messages[-1]['content']); slot=payload['stage']; target=bool(payload['design_intent'])
            if target:target_calls.append(slot)
            response={'definition':reference_definition(slot,'target' if target else 'source'),
                'evidence':[payload['evidence_pack']['evidence'][0]['evidence_id']],
                'behavior_tests':tests_for_board(3 if target else 2) if slot=='rule_ir' else []}
            if target: response['plan']={}
            return json.dumps(response)
        with tempfile.TemporaryDirectory() as tmp:
            compiler=SourceToIRCompiler(chat_fn=chat,max_repairs=0)
            source=compiler.compile(package,out_dir=Path(tmp)/'source')
            self.assertTrue(source.ok,source.diagnostics)
            approve_llm_manifest_file(Path(source.output_dir)/'project.manifest.json')
            result=compiler.compile_spatial_lift(package,source_bundle_dir=Path(source.output_dir),
                target_dimensions={'x':3,'y':3,'z':3},out_dir=Path(tmp)/'target')
            self.assertFalse(result.ok); self.assertEqual(target_calls,['rule_ir'])
            self.assertIn('Spatial plan','\n'.join(result.diagnostics))
            self.assertIn('topology','\n'.join(result.diagnostics))


if __name__ == '__main__': unittest.main()
