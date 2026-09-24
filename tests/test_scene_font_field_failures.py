"""Offline regression of Sept 24 failures; repairs here are authored test data."""
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from srtp.llm_compiler_v1.source_workspace import SourceWorkspace
from srtp.llm_compiler_v1.behavior_runtime import test_runtime
from srtp.scene_ir_v2 import new_scene_ir, seal_scene_ir, compile_scene_ir, SceneCompileError
from srtp.scene_ir_v2.compiler import _apply_binding_transform
from srtp.asset_ir_v2 import compile_asset_ir
from srtp.scene_presentation import ScenePresentation
from srtp.pointer_gesture import PointerGesture
from srtp.system_fonts import system_font_path

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tests/fixtures/field_runs_20260924/minesweeper_scene'


def field_scene():
    rule=json.loads((FIX/'rule.json').read_text(encoding='utf-8'))
    asset=json.loads((FIX/'asset.json').read_text(encoding='utf-8'))
    payload=json.loads((FIX/'rejected_scene.json').read_text(encoding='utf-8'))
    root=ROOT/'srtp/reference_games/pygame_minesweeper'
    workspace=SourceWorkspace(root,root/'run_game.py')
    scene=new_scene_ir('scene:game.field')
    scene.update(workspace.visuals.resolve(payload['definition']))
    scene['dependencies']={
        'rule_ir':{k:rule[k] for k in ('document_id','content_hash')},
        'asset_ir':{k:asset[k] for k in ('document_id','content_hash')},'extensions':[]}
    return rule,asset,scene,root


def authored_scene_repair(scene):
    """An explicit backend test repair, never written to a user's manifest/cache."""
    scene=deepcopy(scene)
    scene['nodes']=[n for n in scene['nodes'] if n['id']!='scene:timer.live']
    scene['bindings']=[b for b in scene['bindings'] if 'timer' not in b['target']['node']]
    digits=['asset:score.'+name for name in ('zero','one','two','three','four','five','six','seven','eight','nine')]
    for place,name in enumerate(('ones','tens','hundreds')):
        scene['bindings'].append({'id':'scene:binding.timer.'+name,'name':'Atlas digit',
            'source':{'kind':'state','scope':'global','variable':'rule:state.seconds'},
            'target':{'selector':'node','node':'scene:timer.'+name,'component':'digit','property':'texture'},
            'transform':{'kind':'digit','place':place,'minimum':0,'maximum':999,'values':digits}})
    def children(node):
        yield node
        for child in node.get('children',[]):yield from children(child)
    for node in children(scene['prefabs'][0]['root']):
        for c in node['components']:
            if c['id']=='cover_sprite':
                c['properties']['variants']['unopened']['pressed_style']={'texture':'asset:tile.empty'}
                c['properties']['variants']['question']['pressed_style']={'texture':'asset:tile.question-click'}
    face=next(n for n in scene['nodes'] if n['id']=='scene:face')['components'][0]['properties']
    face['pressed_style']={'texture':'asset:face.smile-click'}
    # A second renderer represents board-wide excited feedback; separate it
    # from direct face-button feedback so the source's two press states differ.
    face['variants']['playing']['pressed_style']={'texture':'asset:face.smile-click'}
    for label,source,target in [
        ('cell',{'kind':'interaction','property':'pressed','scope':'target','control':'mouse.button.primary'},
         {'selector':'topology_sites','node':'scene:board','visualizer':'sites','component':'cover_sprite','property':'pressed'}),
        ('face',{'kind':'interaction','property':'pressed','scope':'target','control':'mouse.button.primary'},
         {'selector':'node','node':'scene:face','component':'face_sprite','property':'pressed'})]:
        scene['bindings'].append({'id':'scene:binding.press.'+label,'name':'Pressed feedback',
            'source':source,'target':target,'transform':{'kind':'direct'}})
    # Use a separate, reversible visual override selected by board press.
    excited=deepcopy(next(n for n in scene['nodes'] if n['id']=='scene:face'))
    excited['id']='scene:face.excited';excited['components']=excited['components'][:1]
    props=excited['components'][0]['properties']
    props.clear();props.update(geometry='builtin:quad',visible=False,texture='asset:face.excited',scale=[1.5,1.5,.01])
    excited['transform']['translation'][2]-=.01
    scene['nodes'].append(excited)
    scene['bindings'].append({'id':'scene:binding.press.excited','name':'Board press face',
        'source':{'kind':'interaction','property':'pressed','scope':'any','control':'mouse.button.primary','node':'scene:board'},
        'target':{'selector':'node','node':excited['id'],'component':'face_sprite','property':'visible'},
        'transform':{'kind':'direct'}})
    # This authored test repair has supplied all three previously missing
    # facilities. Original rejected fixture still retains every unresolved item.
    scene['unresolved']=[]
    return seal_scene_ir(scene)


class SceneFontFieldTests(unittest.TestCase):
    def test_turtle_exact_arial_bold_is_indexed_and_portable(self):
        root=ROOT/'srtp/reference_games/turtle_connect_complete'
        workspace=SourceWorkspace(root,root/'connect_complete.py')
        self.assertEqual(workspace.preflight_errors,[])
        font=next(a for a in workspace.runtime_assets if a['metadata']['requested_family']=='Arial')
        self.assertEqual(font['source']['uri'],'runtime://system/font/arial/1/0')
        self.assertEqual(font['metadata']['source_references'][0]['span']['line_start'],69)
        from srtp.asset_ir_v2 import new_asset_ir,seal_asset_ir
        from srtp.bundle_assets import copy_bundle_assets,asset_root
        doc=new_asset_ir('asset:game.turtlefont');doc.update(assets=[font],unresolved=[]);doc=seal_asset_ir(doc)
        with tempfile.TemporaryDirectory() as tmp:
            bundle=Path(tmp)/'bundle';bundle.mkdir();copy_bundle_assets(doc,root,bundle)
            with patch('srtp.system_fonts.system_font_path',side_effect=AssertionError('Must use bundle')):
                self.assertEqual(compile_asset_ir(doc,asset_root(bundle)).resource(font['id']).content_hash,font['source']['content_hash'])

    def test_windows_registry_integer_setting_is_not_a_path(self):
        if sys.platform!='win32':self.skipTest('Windows registry regression')
        import winreg
        path=system_font_path('runtime://system/font/arial/1/0')
        class Key:
            def __enter__(self):return self
            def __exit__(self,*args):pass
        rows=[('Font setting',1,winreg.REG_DWORD),('Arial Bold',str(path),winreg.REG_SZ)]
        # The old pygame scanner raises exactly the screenshot's TypeError.
        from pygame import sysfont
        with patch.object(winreg,'OpenKey',return_value=Key()),patch.object(winreg,'QueryInfoKey',return_value=(0,2,0)),patch.object(winreg,'EnumValue',side_effect=lambda key,i:rows[i]):
            with self.assertRaisesRegex(TypeError,'expected str, bytes or os.PathLike object, not int'):
                sysfont.initsysfonts_win32()
            self.assertEqual(system_font_path('runtime://system/font/arial/1/0'),path)

    def test_digits_clamp_pad_and_preserve_original_atlas(self):
        for value,expected in [(0,'000'),(7,'007'),(99,'099'),(100,'100'),(999,'999'),(1000,'999'),(-1,'000')]:
            with self.subTest(value=value):
                text=''.join(str(_apply_binding_transform({'kind':'digit','place':p,'minimum':0,'maximum':999},value)) for p in (2,1,0))
                self.assertEqual(text,expected)
                self.assertEqual(_apply_binding_transform({'kind':'integer_format','width':3,'minimum':0,'maximum':999},value),expected)
        with self.assertRaises(ValueError):_apply_binding_transform({'kind':'digit','place':0},float('nan'))

    def test_new_generation_contract_and_bad_display_fields(self):
        from srtp.llm_compiler_v1.backend_contract import check_alignment,profile
        from srtp.scene_ir_v2 import validate_scene_ir
        self.assertEqual(check_alignment(),[])
        self.assertIn('digit',profile()['scene_binding_transforms'])
        _,_,scene,_=field_scene();scene=authored_scene_repair(scene)
        binding=next(b for b in scene['bindings'] if b['transform']['kind']=='digit')
        for change in ({'place':-1},{'values':[1,2]},{'minimum':{}},{'minimum':10,'maximum':1}):
            malformed=deepcopy(scene)
            next(b for b in malformed['bindings'] if b['id']==binding['id'])['transform'].update(change)
            self.assertTrue(any(d.severity=='error' for d in validate_scene_ir(malformed)))

    def test_2048_worker_preflight_reaches_model_boundary_without_api(self):
        from srtp.source_importer import SourceGameImporter
        from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
        from srtp.llm_compiler_v1.client import LLMTransportError
        from concurrent.futures import ThreadPoolExecutor
        package=SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_2048/main.py')
        seen=[]
        def offline(messages,**kwargs):
            payload=json.loads(messages[-1]['content'])
            seen.extend(payload['source_workspace']['runtime_assets'])
            raise LLMTransportError('Offline stop at model boundary; no request sent')
        with tempfile.TemporaryDirectory() as tmp,ThreadPoolExecutor(max_workers=1) as pool:
            result=pool.submit(lambda:SourceToIRCompiler(chat_fn=offline).compile(package,out_dir=Path(tmp)/'source')).result(timeout=30)
            self.assertIn('Offline stop',result.diagnostics[0])
            self.assertTrue((Path(result.output_dir)/'report.json').is_file())
        self.assertTrue(any(a['source']['uri']=='runtime://pygame/sysfont/verdana/1/0' for a in seen))

    def test_original_scene_remains_rejected_until_semantics_are_supplied(self):
        rule,asset,scene,root=field_scene()
        self.assertEqual(len(scene['unresolved']),3)
        with self.assertRaisesRegex(SceneCompileError,'unresolved'):
            compile_scene_ir(seal_scene_ir(scene),rule_document=rule,asset_catalog=compile_asset_ir(asset,root))

    def test_real_rule_assets_support_live_digits_and_cancelled_press(self):
        rule,asset,scene,root=field_scene()
        assets=compile_asset_ir(asset,root)
        compiled=compile_scene_ir(authored_scene_repair(scene),rule_document=rule,asset_catalog=assets)
        runtime=test_runtime(rule,{})
        graph=ScenePresentation(compiled,assets);projection=compiled.create_projection_session()
        graph.synchronize(projection,runtime.state)
        self.assertEqual(graph.diagnostics(),[])
        for seconds,names in [(7,('zero','zero','seven')),(123,('one','two','three')),(1024,('nine','nine','nine'))]:
            runtime.state.globals['rule:state.seconds']=seconds
            graph.synchronize(projection,runtime.state)
            for name,digit in zip(('hundreds','tens','ones'),names):
                self.assertEqual(graph.renderer('scene:timer.'+name,'digit')['texture'],'asset:score.'+digit)
        cell=next(n for n in graph.nodes if 'cover_sprite' in graph.nodes[n]['components'])
        context=dict(graph.nodes[cell]['rule_context'],node_id=cell)
        gestures=PointerGesture();before=runtime.state.state_hash()
        gestures.press('mouse.button.primary',(0,0),context)
        graph.synchronize(projection,runtime.state,gestures.presentation_state(context))
        self.assertEqual(graph.renderer(cell,'cover_sprite')['texture'],'asset:tile.empty')
        self.assertTrue(graph.renderer('scene:face.excited','face_sprite')['visible'])
        gestures.move((.1,0))
        graph.synchronize(projection,runtime.state,gestures.presentation_state(context))
        self.assertEqual(graph.renderer(cell,'cover_sprite')['texture'],'asset:tile.unopened')
        self.assertFalse(graph.renderer('scene:face.excited','face_sprite')['visible'])
        self.assertIsNone(gestures.release('mouse.button.primary',(.1,0),context))
        face={'node_id':'scene:face'};gestures.press('mouse.button.primary',(0,0),face)
        graph.synchronize(projection,runtime.state,gestures.presentation_state(face))
        self.assertEqual(graph.renderer('scene:face','face_sprite')['texture'],'asset:face.smile-click')
        gestures.clear();graph.synchronize(projection,runtime.state,{})
        self.assertEqual(graph.renderer('scene:face','face_sprite')['texture'],'asset:face.smile')
        self.assertEqual(runtime.state.state_hash(),before)
        coord=tuple(context['coordinate']);runtime.state.grids['rule:state.cover'][coord]=2
        gestures.press('mouse.button.primary',(0,0),context)
        graph.synchronize(projection,runtime.state,gestures.presentation_state(context))
        self.assertEqual(graph.renderer(cell,'cover_sprite')['texture'],'asset:tile.question-click')
        gestures.clear();graph.synchronize(projection,runtime.state,{})
        self.assertEqual(graph.renderer(cell,'cover_sprite')['texture'],'asset:tile.question')


if __name__=='__main__':unittest.main()
