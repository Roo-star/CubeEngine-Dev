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
        self.editor.hotkeys = dict.fromkeys(self.editor.hotkeys, None)
        self.initial_pose = (tuple(self.editor.position), tuple(self.editor.rotation), tuple(camera.position))
        self.initial_lens = (camera.orthographic, camera.fov)
        if not bounds_in_view(bounds):
            raise ValueError('Initial camera could not frame the compiled board')

    def restore(self):
        from ursina import camera
        self.editor.position, self.editor.rotation, camera.position = self.initial_pose
        self.editor.smoothing_helper.rotation = self.editor.rotation
        camera.rotation = (0, 0, 0)
        camera.orthographic, camera.fov = self.initial_lens
        self.editor.target_z = camera.z
        self.editor.target_fov = camera.fov
