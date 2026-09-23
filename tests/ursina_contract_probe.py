"""Actual backend execution matrix. Fixtures are deliberately not LLM output."""
import json
import tempfile
from copy import deepcopy
from pathlib import Path


def main():
    from panda3d.core import loadPrcFileData, OrthographicLens
    loadPrcFileData('', 'window-type offscreen\nwin-size 640 480\naudio-library-name null')
    from ursina import application, camera, scene, window
    from direct.showbase.ShowBase import ShowBase
    app=ShowBase(windowType='offscreen'); application.base=app
    scene.set_up(); camera._cam=app.camera; camera._cam.reparent_to(camera)
    camera.render=app.render; scene.camera=camera; window.aspect_ratio=640/480; camera.set_up()
    from tests.test_asset_ir_v2 import asset_fixture,scene_fixture
    from srtp.asset_ir_v2 import compile_asset_ir,seal_asset_ir
    from srtp.scene_ir_v2 import compile_scene_ir,seal_scene_ir
    from srtp.scene_presentation import ScenePresentation
    from srtp.ursina_scene_backend import UrsinaSceneBackend
    from srtp.scene_ir_v2.component_contracts import backend_diagnostics
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp); assets,_=asset_fixture(root)
        recipes=[('billboard',{'facing':f,'size':[1,1],'double_sided':True}) for f in ('camera','axis','fixed')]
        recipes += [('extrusion',{'depth':.2,'axis':axis}) for axis in ('x','y','z')]
        recipes += [('cube_face_projection',{'faces':'all','uv_policy':uv}) for uv in ('stretch','contain','tile')]
        recipes += [('procedural_mesh',{'primitive':p,'dimensions':[1,1,1]}) for p in ('cube','sphere','cylinder','plane')]
        for i,(strategy,settings) in enumerate(recipes):
            assets['derivations'].append({'id':'asset:model.test_'+str(i),'name':strategy,'kind':'model',
                'media_type':'application/vnd.cubeengine.presentation+json','strategy':strategy,
                'inputs':[] if strategy=='procedural_mesh' else ['asset:image.tile_covered'],
                'settings':settings,'expected_content_hash':'','license_policy':'inherit'})
        assets=seal_asset_ir(assets); doc=scene_fixture(assets); template=deepcopy(doc['nodes'][0]); doc['nodes']=[]
        def add(name,kind,props,parent=None):
            node=deepcopy(template); node['id']='scene:node.'+name; node['parent']=parent
            node['components']=[{'id':name,'type':kind,'enabled':True,'properties':props}]
            doc['nodes'].append(node)
            return node
        for i in range(len(recipes)):
            n=add('recipe_'+str(i),'renderer',{'geometry':'asset:model.test_'+str(i),'visible':True})
            n['transform']['translation']=[i%5-2,i//5-1,0]
        for shape in ('box','sphere'):
            add(shape,'collider',dict(shape=shape,selectable=True,is_trigger=False,
                **({'size':[1,1,1]} if shape=='box' else {'radius':.5})))
        for kind in ('ambient','directional','point'):
            add(kind,'light',{'kind':kind,'color':[1,1,1],'intensity':.5})
        n=add('camera','camera',{'projection':'orthographic','orthographic_size':8,'near_clip':.1,'far_clip':100,'active':True})
        n['transform']['translation']=[0,0,-8]
        add('group','authoring_marker',{'note':'editor metadata'})
        add('hud','ui_canvas',{'mode':'overlay','text':'Contract test','position':[-.4,.4],
            'background':[.1,.1,.1,1],'size':[.3,.08],'scale':1},'scene:node.group')
        doc=seal_scene_ir(doc); assert not backend_diagnostics(doc),backend_diagnostics(doc)
        catalog=compile_asset_ir(assets,project_root=root)
        graph=ScenePresentation(compile_scene_ir(doc,asset_catalog=catalog),catalog)
        assert not graph.diagnostics(),graph.diagnostics()
        backend=UrsinaSceneBackend(graph,root/'cache')
        try:
            assert backend.entities['scene:node.box'].collider is not None
            assert backend.entities['scene:node.sphere'].collider is not None
            assert isinstance(camera.lens,OrthographicLens)
            assert abs(camera.clip_plane_near-.1)<.001 and camera.clip_plane_far==100
            for kind in ('ambient','directional','point'): assert ('scene:node.'+kind,kind) in backend.components
            key=('scene:node.hud','hud'); assert backend.components[key].enabled
            for active in (False,True):
                graph.apply([{'op':'set_property','node_id':'scene:node.group','payload':{'property':'active','value':active}}])
                backend.sync(incremental=True)
                assert bool(backend.components[key].enabled)==active
            for _ in range(3): app.graphicsEngine.renderFrame()
            overlay=backend.components[key]; backend.close()
            assert overlay.is_empty(), 'Overlay must be destroyed when bundle closes'
            print(json.dumps({'actual_geometry_recipe_variants':len(recipes),'picking_shapes':['box','sphere'],
                'lights':['ambient','directional','point'],'camera':'orthographic lens and clip planes',
                'overlay_parent_visibility_and_cleanup':True,'fixture_not_model_output':True}))
        finally:
            backend.close(); app.destroy()


if __name__=='__main__': main()
