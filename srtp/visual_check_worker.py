"""Render a compiled Rule/Asset/Scene offscreen and check what a player would see.

Run only in a separate process (see llm_compiler_v1.visual_gate). The player's
own presentation (ProjectHost settings), backend and camera framing are used,
offscreen. Checks are deliberately
coarse so that only clearly broken pictures fail:

* the board is on screen and is not just background;
* one legal cell action visibly changes that cell (a piece mapping that is
  invisible, hidden or unmapped fails);
* in a 3D volume the cells are not flat plates;
* HUD text overlapping the board is a warning (the viewer moves the view).

When the Scene hides the board in the initial state (a source title or menu
screen), the checks run on the first state reachable by a short sequence of
legal actions in which the board is shown; if none is found, the board checks
are skipped with a warning rather than failing a faithful menu screen.

Usage: python -m srtp.visual_check_worker PLAN.json RESULT.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _frame(app):
    import numpy as np
    for _ in range(3):
        app.step()
    texture = app.win.getScreenshot()
    data = np.frombuffer(texture.getRamImageAs('RGB'), np.uint8).reshape(texture.getYSize(), texture.getXSize(), 3)
    return data[::-1].astype(np.int16)  # row 0 = top


def _pixels(rect, frame):
    """camera.ui rectangle -> clipped pixel box (x0, y0, x1, y1)."""
    height, width = frame.shape[:2]
    aspect = width / height
    x0 = int((rect[0] / aspect + .5) * width)
    x1 = int((rect[2] / aspect + .5) * width)
    y0 = int((.5 - rect[3]) * height)
    y1 = int((.5 - rect[1]) * height)
    return max(0, x0), max(0, y0), min(width, x1), min(height, y1)


def _save(frame, path):
    """Keep the checked frame for inspection (best effort)."""
    try:
        import numpy as np
        from PIL import Image
        Image.fromarray(np.clip(frame, 0, 255).astype('uint8')).save(str(path))
    except Exception:  # noqa: BLE001 - frames are evidence only
        pass


def _background(frame):
    import numpy as np
    height, width = frame.shape[:2]
    border = np.concatenate([frame[:height // 20].reshape(-1, 3), frame[-height // 20:].reshape(-1, 3),
                             frame[:, :width // 20].reshape(-1, 3), frame[:, -width // 20:].reshape(-1, 3)])
    return np.median(border, axis=0)


def _shown_fraction(presentation):
    """Share of board-site nodes that are active (with their ancestors and layer) in the presentation."""
    from srtp.input_pointer_contract import scene_parent
    sites = [node_id for visualizer in presentation.scene.topology_sites.values() for node_id in visualizer.values()]

    def shown(node_id):
        node = presentation.nodes.get(node_id)
        renderers = [c for c in (node or {}).get('components', {}).values() if c.get('type') == 'renderer']
        if renderers and not any(c.get('enabled', True) for c in renderers):
            return False
        while node:
            if not node.get('active', True) or not presentation.layers.get(node.get('layer'), {}).get('visible', True):
                return False
            node = presentation.nodes.get(scene_parent(node))
        return True
    return sum(1 for node_id in sites if shown(node_id)) / float(len(sites)) if sites else 1.0


def _path_to_board(rule, presentation, projection, *, depth=4, budget=80):
    """Shortest legal action-code path (breadth first, distinct states) after which the board is shown."""
    from srtp.ir_v2 import compile_rule_ir
    from srtp.session_random import session_sources

    def replay(path):
        runtime = compile_rule_ir(rule, random_sources=session_sources(rule))
        for code in path:
            runtime.apply_action(code)
        return runtime
    frontier, seen, tried = [()], set(), 0
    for _ in range(depth):
        following = []
        for path in frontier:
            runtime = replay(path)
            try:
                for action in runtime.legal_actions():
                    if tried >= budget:
                        return None
                    tried += 1
                    candidate = replay(path + (action.code,))
                    try:
                        key = candidate.state.state_hash()
                        if key in seen:
                            continue
                        seen.add(key)
                        presentation.synchronize(projection, candidate.state)
                        if _shown_fraction(presentation) >= 0.5:
                            return path + (action.code,)
                        following.append(path + (action.code,))
                    finally:
                        candidate.close()
            finally:
                runtime.close()
        frontier = following
    return None


def main(plan_path: str, result_path: str) -> int:
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8'))
    result = {'status': 'error', 'errors': [], 'warnings': [], 'facts': {}}
    try:
        _check(plan, result)
    except Exception as error:  # noqa: BLE001 - the harness cannot judge; the caller does not block on this
        result['status'] = 'error'
        result['reason'] = '{0}: {1}'.format(type(error).__name__, error)
    Path(result_path).write_text(json.dumps(result), encoding='utf-8')
    return 0


def _check(plan, result):
    import numpy as np
    from panda3d.core import loadPrcFileData, Filename
    loadPrcFileData('', 'window-type offscreen\naudio-library-name null')
    from srtp.asset_ir_v2 import compile_asset_ir
    from srtp.ir_v2 import compile_rule_ir
    from srtp.scene_ir_v2 import compile_scene_ir
    from srtp.scene_presentation import ScenePresentation
    from srtp.session_random import session_sources
    rule, asset, scene_doc = plan['rule_ir'], plan['asset_ir'], plan['scene_ir']
    spatial = bool(plan.get('spatial'))
    runtime = compile_rule_ir(rule, random_sources=session_sources(rule))
    assets = compile_asset_ir(asset, project_root=Path(plan['asset_root']))
    scene = compile_scene_ir(scene_doc, rule_document=rule, asset_catalog=assets)
    # Exactly as the Workbench player (ProjectHost) presents a bundle, including
    # its legacy appearance conventions: the check is about what a player sees.
    presentation = ScenePresentation(scene, assets, legacy_appearance=True, volume_rule=rule if spatial else None)
    projection = scene.create_projection_session()
    presentation.synchronize(projection, runtime.state)
    board_hidden = False
    if _shown_fraction(presentation) < 0.5:
        # A faithful title/menu screen may hide the board: check the first state that shows it.
        path = _path_to_board(rule, ScenePresentation(scene, assets, legacy_appearance=True,
                                                      volume_rule=rule if spatial else None),
                              scene.create_projection_session())
        if path is None:
            board_hidden = True
            result['warnings'].append('the board is hidden in the initial state and no short legal action sequence '
                                      'shows it; board checks skipped')
        else:
            for code in path:
                runtime.apply_action(code)
            presentation.synchronize(projection, runtime.state)
            result['facts']['board_shown_after'] = [runtime.all_actions()[code].action_id for code in path]
    from ursina import Ursina, window
    from srtp.ursina_scene_backend import UrsinaSceneBackend, rgba255
    app = Ursina(development_mode=False)
    window.color = rgba255(20, 25, 36)
    backend = UrsinaSceneBackend(presentation, Path(plan['cache']))
    from srtp.project_camera import ProjectCameraRig, board_rect, overlay_rects, playable_bounds, _intersects
    rig = ProjectCameraRig(backend)
    before = _frame(app)
    out = Path(plan['cache'])
    _save(before, out / 'visual_check_initial.png')
    background = _background(before)
    bounds = playable_bounds(backend)
    rect = board_rect(bounds)
    height, width = before.shape[:2]
    if board_hidden:
        result['status'] = 'passed'
        return
    if rect is None:
        result['errors'].append('the board cannot be projected into the view')
    else:
        x0, y0, x1, y1 = _pixels(rect, before)
        area = max(0, x1 - x0) * max(0, y1 - y0)
        result['facts']['board_screen_fraction'] = round(area / float(width * height), 4)
        if area < 0.01 * width * height:
            result['errors'].append('the board covers {0:.1%} of the view; it is off screen or too small'.format(
                area / float(width * height)))
        else:
            drawn = (np.abs(before[y0:y1, x0:x1] - background).max(axis=2) > 12).mean()
            result['facts']['board_drawn_fraction'] = round(float(drawn), 4)
            if drawn < 0.03:
                result['errors'].append('the board area shows only background ({0:.1%} drawn): cells are invisible'
                                        .format(float(drawn)))
        overlaps = [r for r in overlay_rects(backend) if _intersects(rect, r)]
        if overlaps:
            result['warnings'].append('{0} HUD text item(s) overlap the board after framing'.format(len(overlaps)))
        result['facts']['view_adjusted'] = bool(getattr(rig, 'overlay_adjusted', False))
    _check_move(runtime, presentation, projection, backend, app, before, result, out)
    if spatial:
        _check_volume(presentation, backend, result)
    result['status'] = 'failed' if result['errors'] else 'passed'


def _check_move(runtime, presentation, projection, backend, app, before, result, out):
    """Apply one legal cell action and require that cell to change on screen."""
    import numpy as np
    from panda3d.core import Filename
    from ursina import scene as world
    from srtp.project_camera import board_rect
    sites = {}
    for visualizer in presentation.scene.topology_sites.values():
        for coordinate, node_id in visualizer.items():
            sites[tuple(coordinate)] = node_id
    choice = None
    for action in runtime.legal_actions():
        parameters = dict(action.parameters)
        coordinates = [tuple(v) for v in parameters.values() if isinstance(v, (tuple, list)) and tuple(v) in sites]
        if len(parameters) == 1 and coordinates:
            choice, coordinate = action, coordinates[0]
            break
    if choice is None:
        result['facts']['move_check'] = 'skipped: no legal single-cell action'
        return
    entity = backend.entities.get(sites[coordinate])
    bounds = entity.get_tight_bounds(world) if entity is not None else None
    if not bounds:
        result['errors'].append('cell {0} has no rendered geometry'.format(list(coordinate)))
        return
    runtime.apply_action(choice)
    presentation.synchronize(projection, runtime.state)
    backend.sync(incremental=True)
    after = _frame(app)
    _save(after, out / 'visual_check_after_move.png')
    rect = board_rect(bounds)
    if rect is None:
        return
    x0, y0, x1, y1 = _pixels(rect, before)
    if x1 <= x0 or y1 <= y0:
        result['errors'].append('cell {0} is outside the view'.format(list(coordinate)))
        return
    changed = float((np.abs(after[y0:y1, x0:x1] - before[y0:y1, x0:x1]).max(axis=2) > 24).mean())
    result['facts']['move'] = {'action': choice.action_id, 'cell': list(coordinate), 'changed_fraction': round(changed, 4)}
    if changed < 0.005:
        result['errors'].append('{0} at cell {1} changed nothing on screen: the state it sets has no visible '
                                'appearance (check the piece variant mapping, visibility and draw order; a piece inside an opaque '
                                'cell is hidden - use spatial_role cell_shell for the cell)'.format(
                                    choice.action_id, list(coordinate)))


def _check_volume(presentation, backend, result):
    from ursina import scene as world
    ratios = []
    for visualizer in presentation.scene.topology_sites.values():
        for node_id in visualizer.values():
            entity = backend.entities.get(node_id)
            bounds = entity.get_tight_bounds(world) if entity is not None else None
            if bounds:
                extent = sorted(abs(bounds[1][axis] - bounds[0][axis]) for axis in range(3))
                if extent[2] > 0:
                    ratios.append(extent[0] / extent[2])
    if ratios:
        ratios.sort()
        median = ratios[len(ratios) // 2]
        result['facts']['cell_thickness_ratio'] = round(median, 3)
        if median < 0.2:
            result['errors'].append('3D cells render as flat plates (thickness {0:.0%} of width); give each cell a '
                                    'volume (cell_shell) so layers do not fuse'.format(median))


if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2]))
