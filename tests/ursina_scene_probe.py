"""Manual headless backend QA: python -m tests.ursina_scene_probe.

Uses a labelled reference contract, not generated output or product acceptance.
Bootstraps Ursina's renderer on Panda's offscreen buffer because this installed
Ursina release assumes a mouse/keyboard window in its application constructor.
"""
from pathlib import Path


def main():
    from panda3d.core import loadPrcFileData, Filename
    loadPrcFileData('', 'window-type offscreen\nwin-size 900 650\naudio-library-name null')
    from ursina import application, camera, scene, Text, Entity, window
    from direct.showbase.ShowBase import ShowBase
    app = ShowBase(windowType='offscreen')
    application.base = app
    scene.set_up()
    camera._cam = app.camera
    camera._cam.reparent_to(camera)
    camera.render = app.render
    scene.camera = camera
    window.aspect_ratio = 900 / 650
    camera.set_up()
    app.set_background_color(.07, .086, .118, 1)
    Text.default_font = Filename.from_os_specific('C:/Windows/Fonts/arial.ttf').get_fullpath()
    from srtp.project_viewer import ProjectHost
    from srtp.ursina_scene_backend import UrsinaSceneBackend
    root = Path(__file__).resolve().parents[1]
    host = ProjectHost(root/'artifacts/tictactoe_target/project.manifest.json')
    assert host.mouse('mouse.button.primary', {'coordinate':(1,1,1)}).accepted
    assert host.mouse('mouse.button.primary', {'coordinate':(0,0,0)}).accepted
    host.refresh_scene()
    state = host.controller.sessions[host.controller.active_key].rule_runtime.state
    before = state.state_hash()
    output = root/'.cubeengine_llm/backend_checks'
    assert not host.presentation.diagnostics()
    backend = UrsinaSceneBackend(host.presentation, output/'assets')
    try:
        backend.selected_layer = 1
        backend.sync()
        center = next(e for e in backend.entities.values() if tuple(e.rule_context.get('coordinate', ())) == (1,1,1))
        collider = center.collider
        backend.sync(incremental=True)
        assert center.collider is collider, 'Unchanged colliders must not be rebuilt on each refresh'
        backend.hover(center)
        assert backend.hover_outline.enabled
        backend.hover(None)
        assert not backend.hover_outline.enabled
        from ursina import raycast
        hit = raycast((0,0,-10), (0,0,1), distance=20, ignore=[])
        assert hit.hit and tuple(backend.pick_context(hit.entity)['coordinate']) == (1,1,1), 'Ray must reach the inner cell'
        pivot = Entity(position=(1,1,1), rotation=(22,-32,0))
        camera.parent = pivot
        camera.position = (0,0,-11)
        for _ in range(5):
            app.graphicsEngine.renderFrame()
        path = output/'scene_asset_backend.png'
        assert app.win.saveScreenshot(Filename.from_os_specific(str(path)))
        assert state.state_hash() == before
        assert sum(e.collider is not None for e in backend.entities.values()) == 9
        cells = [e for e in backend.entities.values() if e.rule_context.get('coordinate')]
        assert len(set(tuple(e.world_position) for e in cells)) == 27
        assert host.presentation_notice
        assert host.controller.verify_replay()['passed']
        old_serial = host.presentation.change_serial
        host.refresh_scene()
        assert host.presentation.change_serial == old_serial
        host.controller.reset(); host.refresh_scene(); backend.sync(incremental=True)
        assert host.presentation.change_serial > old_serial
        print({'rendered_nodes':len(backend.entities), 'screenshot':str(path),
               'rule_state_unchanged':True, 'window_type':type(app.win).__name__})
    finally:
        backend.close()
        host.close()
        app.destroy()


if __name__ == '__main__':
    main()
