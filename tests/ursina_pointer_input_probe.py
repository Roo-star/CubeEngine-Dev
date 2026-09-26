"""Real saved Scene picking -> authored test Input -> actual Rule runtime.

Not live Input generation; no user manifests/cache are written.
"""
from pathlib import Path


def main():
    from panda3d.core import loadPrcFileData,Filename
    loadPrcFileData('', 'window-type offscreen\nwin-size 900 700\naudio-library-name null')
    from ursina import application,camera,scene,window,raycast,Vec3
    from direct.showbase.ShowBase import ShowBase
    app=ShowBase(windowType='offscreen');application.base=app;scene.set_up()
    camera._cam=app.camera;camera._cam.reparent_to(camera);camera.render=app.render
    scene.camera=camera;window.aspect_ratio=900/700;camera.set_up()
    from tests.test_input_pointer_routing import FIELD,SOURCE,input_document
    from srtp.asset_ir_v2 import compile_asset_ir
    from srtp.scene_ir_v2 import compile_scene_ir
    from srtp.input_ir_v2 import compile_input_ir,PhysicalInputEvent
    from srtp.llm_compiler_v1.behavior_runtime import test_runtime
    from srtp.project_manifest_v2.compiler import ProjectSession
    from srtp.input_pointer_contract import pointer_data
    from srtp.scene_presentation import ScenePresentation
    from srtp.ursina_scene_backend import UrsinaSceneBackend
    from srtp.project_camera import ProjectCameraRig
    from PIL import Image
    rule=FIELD['documents']['rule_ir'];assets=compile_asset_ir(FIELD['documents']['asset_ir'],SOURCE)
    compiled=compile_scene_ir(FIELD['documents']['scene_ir'],rule_document=rule,asset_catalog=assets)
    runtime=test_runtime(rule,{});inputs=compile_input_ir(input_document(),rule_document=rule)
    session=ProjectSession(runtime,compiled,assets,inputs)
    graph=ScenePresentation(compiled,assets);projection=compiled.create_projection_session()
    graph.synchronize(projection,runtime.state)
    output=Path(__file__).resolve().parents[1]/'.cubeengine_llm/backend_checks/input_pointer'
    backend=UrsinaSceneBackend(graph,output/'assets')
    try:
        ProjectCameraRig(backend)
        def snapshot(name):
            graph.synchronize(projection,runtime.state);backend.sync(incremental=True)
            for _ in range(3):app.graphicsEngine.renderFrame()
            path=output/(name+'.png');assert app.win.saveScreenshot(Filename.from_os_specific(str(path)))
            return Image.open(path).convert('RGB').tobytes()
        before=snapshot('initial')
        contexts={}
        for label,entity in [('button',backend.entities['scene:face']),
                             ('cell',next(e for e in backend.entities.values() if tuple(e.rule_context.get('coordinate',()))==(0,0) and e.collider))]:
            hit=raycast(entity.world_position+Vec3(0,0,-50),Vec3(0,0,1),distance=100)
            assert hit.hit,label+' ray missed'
            context=backend.pick_context(hit.entity)
            assert context['node_id']==entity.scene_node_id,(label,context)
            contexts[label]=context
        event=PhysicalInputEvent(1,'mouse','mouse.button.secondary','press',data=pointer_data(contexts['cell'],click=True))
        marked=session.handle_input(event)
        assert len(marked.transitions)==1 and not marked.host_commands
        assert runtime.state.grids['rule:state.cover'][(0,0)]==1
        after=snapshot('flagged');assert before!=after,'Click must change rendered pixels'
        event=PhysicalInputEvent(2,'mouse','mouse.button.primary','release',data=pointer_data(contexts['button'],click=True))
        reset=session.handle_input(event)
        assert reset.host_commands==('restart',) and not reset.transitions
        print({'actual_saved_scene':True,'input':'explicit authored test repair','raycast_targets':list(contexts),
               'board_mark_pixels_changed':True,'button_restart_dispatched':True,'api_calls':0})
    finally:backend.close();session.close();app.destroy()


if __name__=='__main__':main()
