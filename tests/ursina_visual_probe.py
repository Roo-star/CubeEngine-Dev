"""Real offscreen renderer checks using labelled asset fixtures, not LLM output."""
import json
import tempfile
import wave
import hashlib
from copy import deepcopy
from pathlib import Path


def main():
    from panda3d.core import loadPrcFileData, PNMImage
    loadPrcFileData('', 'window-type offscreen\nwin-size 640 480\naudio-library-name null')
    from ursina import application, camera, scene, window
    from direct.showbase.ShowBase import ShowBase
    app=ShowBase(windowType='offscreen'); application.base=app
    scene.set_up(); camera._cam=app.camera; camera._cam.reparent_to(camera)
    camera.render=app.render; scene.camera=camera; window.aspect_ratio=640/480; camera.set_up()
    from tests.test_asset_ir_v2 import asset_fixture, scene_fixture, _license
    from srtp.asset_ir_v2 import seal_asset_ir, compile_asset_ir
    from srtp.scene_ir_v2 import seal_scene_ir, compile_scene_ir
    from srtp.scene_presentation import ScenePresentation
    from srtp.ursina_scene_backend import UrsinaSceneBackend
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp); assets,_=asset_fixture(root)
        blue=deepcopy(assets['derivations'][0]); blue['id']='asset:image.blue'; blue['settings']['x']=8
        mesh=deepcopy(assets['derivations'][1]); mesh.update(id='asset:model.extruded',kind='model',
            strategy='extrusion',inputs=['asset:image.tile_atlas'],settings={'depth':.4,'axis':'z'})
        assets['derivations'].extend([blue,mesh])
        audio=root/'tone.wav'
        with wave.open(str(audio),'wb') as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(8000); stream.writeframes(b'\0\0'*800)
        data=audio.read_bytes()
        assets['assets'].append({'id':'asset:audio.tone','name':'Tone','kind':'audio','media_type':'audio/wav',
            'source':{'uri':'project://tone.wav','content_hash':hashlib.sha256(data).hexdigest(),'byte_size':len(data)},
            'license':_license(),'importer':{'capability':'cubeengine.raw-file','version':'1.0','settings':{}},'metadata':{}})
        assets=seal_asset_ir(assets)
        doc=scene_fixture(assets)
        node=doc['nodes'][0]; props=node['components'][0]['properties']
        props.pop('material'); props.update(geometry='builtin:quad',
            animation={'frames':['asset:image.tile_covered','asset:image.blue'],'fps':2,'loop':True})
        node['transform']['translation']=[-1,0,0]
        extruded=deepcopy(node); extruded['id']='scene:node.extruded'; extruded['transform']['translation']=[1,0,0]
        extruded['components'][0]['properties']={'geometry':'asset:model.extruded','visible':True}
        node['components'].append({'id':'sound','type':'audio_source','enabled':True,
                                  'properties':{'clip':'asset:audio.tone','volume':.5,'trigger':0}})
        doc['nodes'].append(extruded); doc=seal_scene_ir(doc)
        compiled_assets=compile_asset_ir(assets,project_root=root)
        graph=ScenePresentation(compile_scene_ir(doc,asset_catalog=compiled_assets),compiled_assets)
        assert not graph.diagnostics(),graph.diagnostics()
        backend=UrsinaSceneBackend(graph,root/'cache')
        try:
            camera.position=(0,0,-6); camera.rotation=(0,0,0)
            app.set_background_color(.05,.05,.05,1)
            def pixels():
                for _ in range(3): app.graphicsEngine.renderFrame()
                image=PNMImage(); app.win.getScreenshot(image)
                red=blue=0
                for y in range(image.getYSize()):
                    for x in range(image.getXSize()):
                        r,g,b=image.getXel(x,y)
                        red+=int(r>.3 and r>b*1.5); blue+=int(b>.3 and b>r*1.5)
                return red,blue
            before=pixels(); backend.advance_visuals(.51); after=pixels()
            assert before[0]>after[0]+500 and after[1]>before[1]+500,(before,after)
            assert len(backend.meshes)==1
            meshdata=graph.geometry('asset:model.extruded')['mesh_data']
            assert meshdata['opaque_pixels']==128
            sound=backend.components[('scene:node.tile','sound')]
            assert sound.clip is not None
            print(json.dumps({'renderer':'Panda GraphicsBuffer / Ursina','initial_red_blue_pixels':before,
                'animated_red_blue_pixels':after,'source_mesh_pixels':128,'audio_loaded':True}))
        finally:
            backend.close(); app.destroy()


if __name__=='__main__': main()
