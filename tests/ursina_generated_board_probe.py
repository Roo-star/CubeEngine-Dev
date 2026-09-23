"""Render and pick the unchanged real model target that opened as a blank window."""
from pathlib import Path
import hashlib
import json


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    from panda3d.core import loadPrcFileData, Filename, Point2
    loadPrcFileData('', 'window-type offscreen\nwin-size 1000 700\naudio-library-name null')
    from ursina import application, camera, scene, window, raycast, Vec3, time
    from direct.showbase.ShowBase import ShowBase
    app = ShowBase(windowType='offscreen')
    application.base = app
    scene.set_up()
    camera._cam = app.camera
    camera._cam.reparent_to(camera)
    camera.render = app.render
    scene.camera = camera
    window.aspect_ratio = 1000/700
    camera.set_up()
    app.set_background_color(.07, .086, .118, 1)
    from srtp.project_viewer import ProjectHost
    from srtp.ursina_scene_backend import UrsinaSceneBackend
    from srtp.project_camera import ProjectCameraRig, playable_bounds, bounds_in_view
    root = Path(__file__).resolve().parents[1]
    fixture = root/'tests/fixtures/generated_tictactoe_target_20260923'
    manifest = args.manifest or fixture/'project.manifest.json'
    expected_hash = (hashlib.sha256(manifest.read_bytes()).hexdigest() if args.manifest else
        json.loads((fixture/'origin.json').read_text())['manifest_sha256'])
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == expected_hash
    output = args.output or root/'.cubeengine_llm/backend_checks'
    output.mkdir(parents=True,exist_ok=True)
    host = ProjectHost(manifest)
    backend = UrsinaSceneBackend(host.presentation, output/'generated_assets')
    try:
        bounds = playable_bounds(backend)
        rig = ProjectCameraRig(backend)
        assert bounds_in_view(bounds)
        cells = {tuple(e.rule_context['coordinate']):e for e in backend.entities.values()
                 if 'coordinate' in e.rule_context}
        assert len(cells) == 27
        from srtp.volume_layout import validate_volume, PITCH
        assert validate_volume(host.presentation)[0]['cells']==27
        for coordinate, entity in cells.items():
            assert (entity.world_position-Vec3(*[(v-1)*PITCH for v in coordinate])).length()<.0001
        assert cells[(1,1,1)].world_position.length()<.0001
        # Real renderer raycasting, not fabricated event coordinates. Layer
        # selection must let a pointer ray reach every cell, including center.
        for coordinate, entity in cells.items():
            backend.selected_layer = coordinate[2]
            backend.sync(incremental=True)
            direction = (entity.world_position-camera.world_position).normalized()
            hit = raycast(camera.world_position, direction, distance=100, ignore=[])
            assert hit.hit, ('unpickable', coordinate)
            actual = tuple(backend.pick_context(hit.entity).get('coordinate',()))
            assert actual == coordinate, ('wrong cell', coordinate, actual)
        backend.selected_layer = None
        backend.sync(incremental=True)
        for coordinate in [(0,0,0),(0,1,0)]:
            assert host.mouse('mouse.button.primary', {'coordinate':coordinate}).accepted
            host.refresh_scene(); backend.sync(incremental=True)
        backend.advance_visuals(0)
        outlines=[e for e in scene.entities if getattr(e,'volume_outline',False) and not e.is_empty()]
        assert len(outlines)==27
        assert all(e.model.get_parent()==e for e in outlines), 'Cached meshes must be copied, not moved between cells'
        for _ in range(4):
            app.graphicsEngine.renderFrame()
        screenshot = output/'generated_target_playable.png'
        assert app.win.saveScreenshot(Filename.from_os_specific(str(screenshot)))
        from PIL import Image
        pixels = list(Image.open(screenshot).convert('RGB').getdata())
        blue = sum(b>180 and g>150 and g>r+70 and r<120 for r,g,b in pixels)
        amber = sum(r>160 and 80<g<220 and b<140 for r,g,b in pixels)
        assert blue > 20 and amber > 20, ('X/O hidden inside opaque cells', blue, amber)
        # Restore must update EditorCamera's interpolation state, otherwise the
        # next update silently rotates away from the restored board again.
        rig.editor.rotation = (15, 45, 0)
        rig.editor.smoothing_helper.rotation = (15, 45, 0)
        rig.restore()
        time.dt = .016
        rig.editor.update()
        assert bounds_in_view(bounds)
        assert tuple(rig.editor.smoothing_helper.rotation) == tuple(rig.editor.rotation)
        for coordinate in [(1,1,1),(0,2,0),(2,2,2)]:
            assert host.mouse('mouse.button.primary', {'coordinate':coordinate}).accepted
        assert host.controller.snapshot().terminal
        assert host.controller.verify_replay()['passed']
        assert host.key('keyboard.key.r').accepted
        assert not host.controller.snapshot().terminal
        assert hashlib.sha256(manifest.read_bytes()).hexdigest() == expected_hash
        print(json.dumps({'actual_model_bundle_unchanged':True, 'instantiated_cells':27,
            'all_cells_ray_pickable':True, 'space_diagonal_win':True, 'restart':True,
            'marker_pixels':{'blue':blue,'amber':amber}, 'screenshot':str(screenshot)}))
    finally:
        backend.close(); host.close(); app.destroy()


if __name__ == '__main__':
    main()
