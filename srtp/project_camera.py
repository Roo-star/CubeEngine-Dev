"""Frame compiled geometry, keeping valid authored views and repairing empty ones.

This is a viewer policy, not a rewrite of model output or game rules.
"""
from itertools import product
import math


def playable_bounds(backend):
    from ursina import scene, Vec3
    graph = backend.presentation
    candidates = [entity for node_id, entity in backend.entities.items()
                  if backend._node_enabled(node_id) and entity.rule_context]
    if not candidates:
        candidates = [entity for node_id, entity in backend.entities.items()
                      if backend._node_enabled(node_id) and any(
                          c['type'] == 'renderer' and c['enabled']
                          for c in graph.nodes[node_id]['components'].values())]
    bounds = [entity.get_tight_bounds(scene) for entity in candidates]
    bounds = [value for value in bounds if value]
    if not bounds:
        raise ValueError('Scene contains no visible game geometry to frame')
    low = Vec3(*(min(value[0][axis] for value in bounds) for axis in range(3)))
    high = Vec3(*(max(value[1][axis] for value in bounds) for axis in range(3)))
    return low, high


def bounds_in_view(bounds, margin=1.0):
    from ursina import camera, scene
    from panda3d.core import Point2, Point3
    low, high = bounds
    for corner in product(*zip(low, high)):
        position = camera._cam.get_relative_point(scene, Point3(*corner))
        projected = Point2()
        if not camera.lens.project(position, projected):
            return False
        # Ursina configures Panda for Y-up, Z-forward coordinates.
        if not camera.lens.get_near() < position.z < camera.lens.get_far():
            return False
        if max(abs(projected.x), abs(projected.y)) > margin:
            return False
    return True


def overlay_rects(backend):
    """Screen rectangles (x0, y0, x1, y1 in camera.ui units) of visible overlay text/backgrounds."""
    from ursina import camera
    rects = []
    for host in getattr(backend, 'overlay_hosts', []) or []:
        if not host or not host.enabled:
            continue
        bounds = host.get_tight_bounds(camera.ui)
        if bounds:
            low, high = bounds
            if high[0] - low[0] > 1e-4 and high[1] - low[1] > 1e-4:
                rects.append((low[0], low[1], high[0], high[1]))
    return rects


def board_rect(bounds):
    """The board's projected rectangle in camera.ui units, or None when a corner is not projectable."""
    from ursina import camera, scene, window
    from panda3d.core import Point2, Point3
    low, high = bounds
    xs, ys = [], []
    for corner in product(*zip(low, high)):
        position = camera._cam.get_relative_point(scene, Point3(*corner))
        projected = Point2()
        if not camera.lens.project(position, projected):
            return None
        xs.append(projected.x * window.aspect_ratio / 2)
        ys.append(projected.y / 2)
    return (min(xs), min(ys), max(xs), max(ys))


def _intersects(a, b, gap=.01):
    return a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap


class ProjectCameraRig:
    def __init__(self, backend):
        from ursina import camera, EditorCamera, Vec3, scene
        self.notice = ''
        bounds = playable_bounds(backend)
        low, high = bounds
        center = (low + high) / 2
        radius = max((high - low).length() / 2, .5)
        authored = any(c['type'] == 'camera' and c['enabled'] and c['properties'].get('active')
                       and backend._node_enabled(node_id)
                       for node_id, node in backend.presentation.nodes.items()
                       for c in node['components'].values())
        position, rotation = Vec3(camera.world_position), Vec3(camera.world_rotation)
        valid = authored and bounds_in_view(bounds, .95)
        if valid:
            distance = max((center - position).dot(camera.forward), radius)
            pivot = position + camera.forward * distance
        else:
            # Keep the authored viewing side, but aim at the playable geometry.
            # Decorative backgrounds and HUD cannot inflate the orbit bounds.
            direction = (position - center) if authored else Vec3(-.5, .5, -1)
            if direction.length() < .001:
                direction = Vec3(-.5, .5, -1)
            direction.normalize()
            if (high.z - low.z) > .2 * max(high - low) and abs(direction.z) > .95:
                direction += Vec3(.3, .3, 0)
                direction.normalize()
            camera.parent = scene
            camera.rotation = (0, 0, 0)
            if camera.orthographic:
                camera.fov = radius * 2.5
                distance = radius * 3 + 2
            else:
                half_fov = min(camera.lens.get_fov()) * math.pi / 360
                distance = radius / math.sin(half_fov) * 1.25
            camera.world_position = center + direction * distance
            camera.look_at(center)
            camera.world_rotation_z = 0
            rotation = Vec3(camera.world_rotation)
            pivot = center
            camera.clip_plane_near = min(.1, distance / 100)
            camera.clip_plane_far = max(camera.clip_plane_far, distance + radius * 4)
            if authored:
                self.notice = 'View adjusted: authored camera did not frame the board.'
        self.editor = EditorCamera(position=pivot, rotation=rotation)
        camera.position = (0, 0, -distance)
        camera.rotation = (0, 0, 0)
        self.editor.smoothing_helper.rotation = rotation
        self.editor.target_z = -distance
        self.editor.target_fov = camera.fov
        # Disable editor shortcuts: Ursina 5 names them `hotkeys`, Ursina 7+ `shortcuts`.
        for name in ('hotkeys', 'shortcuts'):
            if isinstance(getattr(self.editor, name, None), dict):
                setattr(self.editor, name, dict.fromkeys(getattr(self.editor, name), None))
        self.overlay_adjusted = self._clear_overlays(bounds, overlay_rects(backend))
        self.initial_pose = (tuple(self.editor.position), tuple(self.editor.rotation), tuple(camera.position))
        self.initial_lens = (camera.orthographic, camera.fov)
        self.volume_center = tuple(center)
        self._depth_overview = None
        if not bounds_in_view(bounds):
            raise ValueError('Initial camera could not frame the compiled board')

    def _clear_overlays(self, bounds, overlays, steps=40):
        """Pan/zoom out until the board does not sit under Scene overlay text (HUD, prompts)."""
        from ursina import camera
        if not overlays:
            return False
        moved = False
        for _ in range(steps):
            board = board_rect(bounds)
            hits = [rect for rect in overlays if board and _intersects(board, rect)]
            if not board or not hits:
                break
            centre = (board[1] + board[3]) / 2
            push = sum(-1 if (rect[1] + rect[3]) / 2 > centre else 1 for rect in hits)  # text above: move board down
            if camera.orthographic:
                world_per_ui = camera.fov
                camera.fov *= 1.04
                self.editor.target_fov = camera.fov
            else:
                distance = -camera.z
                world_per_ui = 2 * distance * math.tan(math.radians(min(camera.lens.get_fov())) / 2)
                camera.z *= 1.04
                self.editor.target_z = camera.z
            self.editor.world_position -= camera.up * (0.03 * (1 if push > 0 else -1) * world_per_ui)
            moved = True
        if moved:
            self.notice = (self.notice + ' ' if self.notice else '') + 'View adjusted to keep the HUD clear of the board.'
        return moved

    def restore(self):
        from ursina import camera
        self.editor.position, self.editor.rotation, camera.position = self.initial_pose
        self.editor.smoothing_helper.rotation = self.editor.rotation
        camera.rotation = (0, 0, 0)
        camera.orthographic, camera.fov = self.initial_lens
        self.editor.target_z = camera.z
        self.editor.target_fov = camera.fov
        self._depth_overview = None

    def focus_depth(self, layer):
        """Face an optional depth slice and restore the previous orbit on exit."""
        from ursina import camera
        if layer is None:
            if self._depth_overview is None:
                return
            position, rotation, camera_position, orthographic, fov = self._depth_overview
            self.editor.position = position
            self.editor.rotation = rotation
            self.editor.smoothing_helper.rotation = rotation
            camera.position = camera_position
            camera.rotation = (0, 0, 0)
            camera.orthographic, camera.fov = orthographic, fov
            self.editor.target_z, self.editor.target_fov = camera.z, fov
            self._depth_overview = None
            return
        if self._depth_overview is None:
            self._depth_overview = (tuple(self.editor.position), tuple(self.editor.rotation),
                                    tuple(camera.position), camera.orthographic, camera.fov)
        self.editor.position = self.volume_center
        self.editor.rotation = (0, 0, 0)
        self.editor.smoothing_helper.rotation = (0, 0, 0)
        camera.position = self.initial_pose[2]
        camera.rotation = (0, 0, 0)
        camera.orthographic, camera.fov = self.initial_lens
        self.editor.target_z, self.editor.target_fov = camera.z, camera.fov
