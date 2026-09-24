"""Render saved Rule/Asset with explicitly authored Scene repair, not a live result."""
from pathlib import Path


def main():
    from panda3d.core import loadPrcFileData, Filename
    loadPrcFileData('', 'window-type offscreen\nwin-size 900 700\naudio-library-name null')
    from ursina import application, camera, scene, window
    from direct.showbase.ShowBase import ShowBase
    app=ShowBase(windowType='offscreen');application.base=app;scene.set_up()
    camera._cam=app.camera;camera._cam.reparent_to(camera);camera.render=app.render
    scene.camera=camera;window.aspect_ratio=900/700;camera.set_up()
    from tests.test_scene_font_field_failures import field_scene
    from tests.test_presentation_composition import conditional_scene_payload
    from srtp.asset_ir_v2 import compile_asset_ir
    from srtp.scene_ir_v2 import compile_scene_ir, seal_scene_ir
    from srtp.llm_compiler_v1.behavior_runtime import test_runtime
    from srtp.scene_presentation import ScenePresentation
    from srtp.ursina_scene_backend import UrsinaSceneBackend
    from srtp.project_camera import ProjectCameraRig
    from srtp.pointer_gesture import PointerGesture
    rule,asset,document,root=field_scene();assets=compile_asset_ir(asset,root)
    document.update(conditional_scene_payload()['definition'])
    compiled=compile_scene_ir(seal_scene_ir(document),rule_document=rule,asset_catalog=assets)
    graph=ScenePresentation(compiled,assets);projection=compiled.create_projection_session()
    runtime=test_runtime(rule,{});runtime.state.globals['rule:state.seconds']=123
    graph.synchronize(projection,runtime.state);assert not graph.diagnostics()
    output=Path(__file__).resolve().parents[1]/'.cubeengine_llm/backend_checks/minesweeper_feedback'
    backend=UrsinaSceneBackend(graph,output/'assets')
    try:
        backend.sync();rig=ProjectCameraRig(backend)
        selected=next(e for e in backend.entities.values() if tuple(e.rule_context.get('coordinate',()))==(0,0) and e.collider)
        context=backend.pick_context(selected)
        gestures=PointerGesture();before=runtime.state.state_hash()
        images=[]
        for phase in ('idle','pressed','cancelled'):
            if phase=='pressed':gestures.press('mouse.button.primary',(0,0),context)
            if phase=='cancelled':gestures.move((.1,0))
            graph.synchronize(projection,runtime.state,gestures.presentation_state(context))
            backend.sync(incremental=True)
            for _ in range(3):app.graphicsEngine.renderFrame()
            screenshot=output/(phase+'.png')
            assert app.win.saveScreenshot(Filename.from_os_specific(str(screenshot)))
            from PIL import Image
            images.append(Image.open(screenshot).convert('RGB').tobytes())
        assert images[0]!=images[1], 'Press must change rendered pixels'
        assert images[0]==images[2], 'Cancelled press must restore the rendered game'
        assert before==runtime.state.state_hash()
        coordinate=tuple(context['coordinate'])
        # Pressing already-open or flagged sites must not change the cover
        # renderer. Check cover properties and full-frame rendered feedback.
        cover_pixels=[]
        for cover in (0,1,2,3):
            runtime.state.grids['rule:state.cover'][coordinate]=cover
            current=[]
            for pressed in (False,True):
                gesture={'hovered':context,'pressed':{'mouse.button.primary':context}} if pressed else {}
                graph.synchronize(projection,runtime.state,gesture)
                for node_id,node in graph.nodes.items():
                    if tuple(node.get('rule_context',{}).get('coordinate',()))!=coordinate:continue
                    for component,expected in (('press_unopened_sprite',0),('press_question_sprite',2)):
                        if component in node['components']:
                            assert graph.renderer(node_id,component)['visible']==(pressed and cover==expected)
                backend.sync(incremental=True)
                for _ in range(3):app.graphicsEngine.renderFrame()
                screenshot=output/('cover_'+str(cover)+'_'+str(int(pressed))+'.png')
                assert app.win.saveScreenshot(Filename.from_os_specific(str(screenshot)))
                current.append(Image.open(screenshot).convert('RGB').tobytes())
            cover_pixels.append(current[0]!=current[1])
        # The face also changes on press, including locked cells; properties
        # above isolate the cell semantics, pixels prove the renderer executes.
        assert cover_pixels[0] and cover_pixels[2]
        print({'saved_rule_asset_replay':True,'scene_repair':'authored test only',
               'rendered_nodes':len(backend.entities),'press_cancel_pixels_verified':True,
               'conditional_cover_states':4,'api_calls':0})
    finally:
        runtime.close();backend.close();app.destroy()


if __name__=='__main__':main()
