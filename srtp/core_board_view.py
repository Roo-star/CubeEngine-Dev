"""Isometric / orbitable 3D board preview for Project Session (Dear PyGui drawlist)."""

from __future__ import annotations

import math
from typing import Any, List, Optional, Sequence, Tuple

Coord = Tuple[int, ...]
HitCell = Tuple[Coord, float, float, float]  # coord, screen_x, screen_y, depth


class MappingCamera:
    __slots__ = (
        "origin_x", "origin_y", "scale", "yaw", "pitch", "cx", "cy", "cz",
    )

    def __init__(
        self,
        *,
        origin_x: float,
        origin_y: float,
        scale: float,
        yaw: float,
        pitch: float,
        cx: float,
        cy: float,
        cz: float,
    ) -> None:
        self.origin_x = origin_x
        self.origin_y = origin_y
        self.scale = scale
        self.yaw = yaw
        self.pitch = pitch
        self.cx = cx
        self.cy = cy
        self.cz = cz


def project_point(
    x: float,
    y: float,
    z: float,
    *,
    origin_x: float,
    origin_y: float,
    scale: float,
    yaw: float,
    pitch: float,
    cx: float,
    cy: float,
    cz: float,
) -> Tuple[float, float, float]:
    """Project board coordinates to screen; returns (sx, sy, depth)."""

    wx = float(x) - cx
    wy = float(y) - cy
    wz = float(z) - cz

    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    rx = wx * cos_y - wy * sin_y
    ry = wx * sin_y + wy * cos_y
    rz = wz

    cos_p, sin_p = math.cos(pitch), math.sin(pitch)
    ty = ry * cos_p - rz * sin_p
    tz = ry * sin_p + rz * cos_p
    tx = rx

    sx = origin_x + tx * scale
    sy = origin_y - tz * scale
    return sx, sy, ty


def _cube_corners(
    x: int, y: int, z: int, *, camera: MappingCamera,
) -> List[Tuple[float, float, float]]:
    corners = []
    for dz in (0.0, 1.0):
        for dy in (0.0, 1.0):
            for dx in (0.0, 1.0):
                corners.append(project_point(
                    x + dx, y + dy, z + dz,
                    origin_x=camera.origin_x, origin_y=camera.origin_y,
                    scale=camera.scale, yaw=camera.yaw, pitch=camera.pitch,
                    cx=camera.cx, cy=camera.cy, cz=camera.cz,
                ))
    return corners


def _shade(rgb: Sequence[int], factor: float) -> Tuple[int, int, int, int]:
    return (
        max(0, min(255, int(rgb[0] * factor))),
        max(0, min(255, int(rgb[1] * factor))),
        max(0, min(255, int(rgb[2] * factor))),
        255,
    )


def cell_base_rgb(value: int) -> Tuple[int, int, int]:
    if value == 2:  # head
        return (72, 168, 255)
    if value == 1:  # body / P1
        return (37, 104, 164)
    if value == -1:  # food / P2
        return (214, 78, 92)
    if value > 0:
        return (48, 126, 195)
    if value < 0:
        return (191, 91, 103)
    return (58, 64, 76)


def camera_aligned_arrow_control(control: str, yaw: float) -> str:
    """Remap arrow-key controls so screen up/left follow the current camera yaw.

    PageUp/PageDown and non-arrow controls are returned unchanged.
    """

    screen_intent = {
        "keyboard.key.arrow_up": (0.0, 1.0),
        "keyboard.key.arrow_down": (0.0, -1.0),
        "keyboard.key.arrow_right": (1.0, 0.0),
        "keyboard.key.arrow_left": (-1.0, 0.0),
    }.get(control)
    if screen_intent is None:
        return control
    sx, sy = screen_intent
    # Screen-right / screen-up basis on the board XY plane (matches project_point).
    right_x, right_y = math.cos(yaw), -math.sin(yaw)
    up_x, up_y = math.sin(yaw), math.cos(yaw)
    world_x = right_x * sx + up_x * sy
    world_y = right_y * sx + up_y * sy
    if abs(world_x) >= abs(world_y):
        return "keyboard.key.arrow_right" if world_x >= 0 else "keyboard.key.arrow_left"
    return "keyboard.key.arrow_down" if world_y >= 0 else "keyboard.key.arrow_up"


def _draw_volume_lattice(
    dpg: Any,
    drawlist: Any,
    *,
    camera: MappingCamera,
    x_size: int,
    y_size: int,
    z_size: int,
) -> None:
    """Draw the full X×Y×Z cell lattice (not just the z=0 floor)."""

    def pt(x: float, y: float, z: float) -> Tuple[float, float]:
        sx, sy, _ = project_point(
            x, y, z,
            origin_x=camera.origin_x, origin_y=camera.origin_y,
            scale=camera.scale, yaw=camera.yaw, pitch=camera.pitch,
            cx=camera.cx, cy=camera.cy, cz=camera.cz,
        )
        return sx, sy

    # Horizontal grids on every layer face (z = 0 .. z_size).
    for z in range(z_size + 1):
        alpha = 140 + int(70 * z / max(z_size, 1))
        color = (52, 62, 78, min(230, alpha)) if z < z_size else (78, 96, 122, 230)
        thickness = 1 if z < z_size else 2
        for x in range(x_size + 1):
            a, b = pt(x, 0, z), pt(x, y_size, z)
            dpg.draw_line(a, b, color=color, thickness=thickness, parent=drawlist)
        for y in range(y_size + 1):
            a, b = pt(0, y, z), pt(x_size, y, z)
            dpg.draw_line(a, b, color=color, thickness=thickness, parent=drawlist)

    # Vertical posts at every lattice column.
    post = (64, 78, 98, 170)
    for x in range(x_size + 1):
        for y in range(y_size + 1):
            a, b = pt(x, y, 0), pt(x, y, z_size)
            dpg.draw_line(a, b, color=post, thickness=1, parent=drawlist)

    # Outer volume cage (emphasize the 20×20×3 bounds).
    cage = (112, 169, 232, 255)
    corners = [
        (0, 0, 0), (x_size, 0, 0), (x_size, y_size, 0), (0, y_size, 0),
        (0, 0, z_size), (x_size, 0, z_size), (x_size, y_size, z_size), (0, y_size, z_size),
    ]
    edges = (
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    )
    for i, j in edges:
        dpg.draw_line(pt(*corners[i]), pt(*corners[j]), color=cage, thickness=2, parent=drawlist)


def draw_board(
    dpg: Any,
    parent: str,
    *,
    grid: Any,
    dimensions: Sequence[int],
    legal: Sequence[Coord],
    yaw: float,
    pitch: float,
    zoom: float,
    canvas_width: int = 760,
    canvas_height: int = 460,
    tag: str = "srtp_core_drawlist",
) -> List[HitCell]:
    """Render an orbitable orthographic 3D board into a Dear PyGui drawlist."""

    x_size = int(dimensions[0])
    y_size = int(dimensions[1])
    z_size = int(dimensions[2]) if len(dimensions) == 3 else 1
    rank3 = len(dimensions) == 3
    legal_set = set(tuple(item) for item in legal)

    cx = (x_size - 1) * 0.5
    cy = (y_size - 1) * 0.5
    cz = (z_size - 1) * 0.5
    # Fit the full volume, not only the floor diagonal.
    span = max(x_size + y_size, x_size + z_size, y_size + z_size, 8)
    base_scale = min(canvas_width, canvas_height) / float(span)
    scale = max(6.0, base_scale * float(zoom))
    camera = MappingCamera(
        origin_x=canvas_width * 0.5,
        origin_y=canvas_height * 0.58,
        scale=scale,
        yaw=yaw,
        pitch=pitch,
        cx=cx,
        cy=cy,
        cz=cz,
    )

    drawlist = dpg.add_drawlist(
        width=canvas_width, height=canvas_height, parent=parent, tag=tag,
    )
    dpg.draw_rectangle(
        (0, 0), (canvas_width, canvas_height),
        color=(22, 24, 30, 255), fill=(22, 24, 30, 255), parent=drawlist,
    )

    _draw_volume_lattice(
        dpg, drawlist, camera=camera,
        x_size=x_size, y_size=y_size, z_size=z_size,
    )

    voxels: List[Tuple[int, int, int, int, Coord, float]] = []
    for z in range(z_size):
        for y in range(y_size):
            for x in range(x_size):
                coord: Coord = (x, y, z) if rank3 else (x, y)
                value = int(_nested(grid, coord))
                if value == 0 and coord not in legal_set:
                    continue
                _sx, _sy, depth = project_point(
                    x + 0.5, y + 0.5, z + 0.5,
                    origin_x=camera.origin_x, origin_y=camera.origin_y,
                    scale=scale, yaw=yaw, pitch=pitch, cx=cx, cy=cy, cz=cz,
                )
                voxels.append((x, y, z, value, coord, depth))

    voxels.sort(key=lambda item: item[5])
    hits: List[HitCell] = []
    for x, y, z, value, coord, depth in voxels:
        rgb = cell_base_rgb(value)
        if value == 0 and coord in legal_set:
            _draw_cube_wire(dpg, drawlist, x, y, z, camera, (91, 196, 220, 255))
        else:
            _draw_cube_solid(dpg, drawlist, x, y, z, camera, rgb)
        sx, sy, _ = project_point(
            x + 0.5, y + 0.5, z + 0.5,
            origin_x=camera.origin_x, origin_y=camera.origin_y,
            scale=scale, yaw=yaw, pitch=pitch, cx=cx, cy=cy, cz=cz,
        )
        hits.append((coord, sx, sy, depth))

    dpg.draw_text(
        (12, 12),
        "3D volume  ·  right-drag: orbit  ·  wheel: zoom  ·  arrows follow camera",
        color=(145, 155, 172, 255), size=15, parent=drawlist,
    )
    dpg.draw_text(
        (12, canvas_height - 22),
        "Grid  {0} × {1} × {2}".format(x_size, y_size, z_size),
        color=(112, 169, 232, 255), size=14, parent=drawlist,
    )
    return hits


def _nested(grid: Any, coordinate: Sequence[int]) -> Any:
    value = grid
    for index in coordinate:
        value = value[index]
    return value


def _face(
    dpg: Any,
    drawlist: Any,
    points: Sequence[Tuple[float, float, float]],
    indices: Sequence[int],
    color: Tuple[int, int, int, int],
) -> None:
    poly = [(points[i][0], points[i][1]) for i in indices]
    dpg.draw_polygon(
        poly, color=_shade(color[:3], 0.55), fill=color, thickness=1, parent=drawlist,
    )


def _draw_cube_solid(
    dpg: Any,
    drawlist: Any,
    x: int,
    y: int,
    z: int,
    camera: MappingCamera,
    rgb: Tuple[int, int, int],
) -> None:
    c = _cube_corners(x, y, z, camera=camera)
    faces = (
        (0, 1, 3, 2),
        (0, 2, 6, 4),
        (1, 3, 7, 5),
        (0, 1, 5, 4),
        (2, 3, 7, 6),
        (4, 5, 7, 6),
    )
    face_depths = []
    for face in faces:
        depth = sum(c[i][2] for i in face) / 4.0
        face_depths.append((depth, face))
    face_depths.sort(key=lambda item: item[0])
    shades = (0.55, 0.7, 0.78, 0.85, 0.92, 1.05)
    for index, (_depth, face) in enumerate(face_depths):
        factor = shades[min(index, len(shades) - 1)]
        _face(dpg, drawlist, c, face, _shade(rgb, factor))


def _draw_cube_wire(
    dpg: Any,
    drawlist: Any,
    x: int,
    y: int,
    z: int,
    camera: MappingCamera,
    color: Tuple[int, int, int, int],
) -> None:
    c = _cube_corners(x, y, z, camera=camera)
    edges = (
        (0, 1), (1, 3), (3, 2), (2, 0),
        (4, 5), (5, 7), (7, 6), (6, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    )
    for a, b in edges:
        dpg.draw_line(
            (c[a][0], c[a][1]), (c[b][0], c[b][1]),
            color=color, thickness=1, parent=drawlist,
        )


def pick_cell(
    hits: Sequence[HitCell],
    screen_x: float,
    screen_y: float,
    *,
    max_distance: float = 28.0,
) -> Optional[Coord]:
    """Pick nearest projected cell center under the cursor."""

    best: Optional[Coord] = None
    best_score = None
    for coord, sx, sy, depth in hits:
        dist = math.hypot(sx - screen_x, sy - screen_y)
        if dist > max_distance:
            continue
        score = dist - depth * 0.01
        if best_score is None or score < best_score:
            best_score = score
            best = coord
    return best
