"""Offscreen Ursina check that extracted pygame.draw pictures render in 3D.

Uses the tictactoe cell/X/O recipes extracted from the real source (no model
output). Run with pygame/Ursina installed: python -m tests.ursina_vector_shape_probe
"""
import json
import tempfile
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    from panda3d.core import loadPrcFileData, PNMImage
    loadPrcFileData('', 'window-type offscreen\nwin-size 640 480\naudio-library-name null')
    from ursina import application, camera, scene, window
    from direct.showbase.ShowBase import ShowBase
    app = ShowBase(windowType='offscreen'); application.base = app
    scene.set_up(); camera._cam = app.camera; camera._cam.reparent_to(camera)
    camera.render = app.render; scene.camera = camera; window.aspect_ratio = 640 / 480; camera.set_up()
    from srtp.asset_ir_v2 import new_asset_ir, seal_asset_ir, compile_asset_ir
    from srtp.scene_ir_v2 import new_scene_ir, seal_scene_ir, compile_scene_ir, identity_transform
    from srtp.scene_presentation import ScenePresentation
    from srtp.ursina_scene_backend import UrsinaSceneBackend
    from tests.test_vector_shapes import groups_in, TICTACTOE
    drawn = {g['source']['line_start']: g['derivation'] for g in groups_in(TICTACTOE)}
    asset = new_asset_ir('asset:game.vector_probe', 'Vector probe')
    asset.update(derivations=[drawn[73], drawn[76], drawn[79]], roles=[], unresolved=[])
    asset = seal_asset_ir(asset)
    document = new_scene_ir('scene:game.vector_probe', 'Vector probe')
    document['dependencies']['asset_ir'] = {k: asset[k] for k in ('document_id', 'content_hash')}
    nodes = []
    for index, (key, geometries) in enumerate((('cell', [73]), ('x', [73, 76]), ('o', [73, 79]))):
        transform = identity_transform()
        transform['translation'] = [(index - 1) * 1.2, 0, 0]
        nodes.append({'id': 'scene:node.' + key, 'name': key, 'parent': None, 'active': True,
                      'layer': 'scene:layer.runtime', 'transform': transform,
                      'components': [{'id': 'r{0}'.format(i), 'type': 'renderer', 'enabled': True,
                                      'properties': {'geometry': drawn[line]['id'], 'visible': True}}
                                     for i, line in enumerate(geometries)]})
    document.update(nodes=nodes, unresolved=[])
    document = seal_scene_ir(document)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        catalog = compile_asset_ir(asset, project_root=root)
        graph = ScenePresentation(compile_scene_ir(document, asset_catalog=catalog), catalog)
        assert not graph.diagnostics(), graph.diagnostics()
        backend = UrsinaSceneBackend(graph, root / 'cache')
        try:
            camera.position = (0, 0, -4); camera.rotation = (0, 0, 0)
            app.set_background_color(0, 0, 0, 1)
            for _ in range(3):
                app.graphicsEngine.renderFrame()
            image = PNMImage(); app.win.getScreenshot(image)
            targets = {'cell': (42, 54, 72), 'x': (70, 190, 250), 'o': (250, 170, 75)}
            counts = dict.fromkeys(targets, 0)
            for y in range(image.getYSize()):
                for x in range(image.getXSize()):
                    pixel = [v * 255 for v in image.getXel(x, y)]
                    for name, colour in targets.items():
                        if all(abs(p - c) < 12 for p, c in zip(pixel, colour)):
                            counts[name] += 1
            assert counts['cell'] > 5000 and counts['x'] > 300 and counts['o'] > 300, counts
            print(json.dumps({'renderer': 'Panda GraphicsBuffer / Ursina', 'source_colour_pixels': counts}))
        finally:
            backend.close(); app.destroy()


if __name__ == '__main__':
    main()
