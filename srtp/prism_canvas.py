"""Projection and picking for the Workbench's live, abstract Rule state view.

This view never replaces the Scene renderer: Play still opens the original
runtime or Ursina. Hidden-information state is deliberately not visualized.
"""
from itertools import product
import math


def project(point, yaw=.72, pitch=.52):
    x, y, z = point
    horizontal = x * math.cos(yaw) - y * math.sin(yaw)
    depth = x * math.sin(yaw) + y * math.cos(yaw)
    return horizontal, depth * math.sin(pitch) - z * math.cos(pitch), depth * math.cos(pitch) + z * math.sin(pitch)


def scene_geometry(dimensions, width, height, yaw=.72, pitch=.52, zoom=1.0, layer=None):
    dims = tuple(dimensions) + (1,) * (3 - len(dimensions))
    if len(dims) != 3 or any(not isinstance(v, int) or v <= 0 for v in dims):
        return []
    if math.prod(dims) > 512:
        return []  # Bound editor work; the full project still opens in Ursina.
    corners = [project(p, yaw, pitch) for p in product(*[(0, n) for n in dims])]
    xs, ys = [p[0] for p in corners], [p[1] for p in corners]
    scale = min(max(80, width - 130) / max(max(xs) - min(xs), 1),
                max(80, height - 125) / max(max(ys) - min(ys), 1)) * min(1.7, max(.55, zoom))
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2

    def screen(p):
        a, b, depth = project(p, yaw, pitch)
        return (width / 2 + (a - cx) * scale, height / 2 + 12 + (b - cy) * scale, depth)

    cells = []
    for x, y, z in product(*[range(n) for n in dims]):
        vertices = [screen((x + dx, y + dy, z + dz)) for dx, dy, dz in
                    ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                     (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))]
        center = screen((x + .5, y + .5, z + .5))
        cells.append({"coordinate": (x, y, z)[:len(dimensions)], "points": vertices,
                      "center": center, "visible": layer is None or z == layer,
                      "radius": max(8, min(23, scale * .22))})
    return sorted(cells, key=lambda cell: cell["center"][2])


def pick_cell(cells, point):
    """Pick the nearest visible site's marker, frontmost on a depth tie."""
    hits = []
    for cell in cells:
        x, y, depth = cell["center"]
        distance = math.hypot(point[0] - x, point[1] - y)
        if cell["visible"] and distance <= cell["radius"]:
            hits.append((distance, -depth, cell["coordinate"]))
    return min(hits)[2] if hits else None


def grid_value(grid, coordinate):
    for index in coordinate:
        grid = grid[index]
    return grid


def draw_state(dpg, parent, state, size, *, yaw, pitch, zoom, layer, opacity,
               markers=None, legal=(), hidden=False, hover=None):
    width, height = size
    cells = scene_geometry(state.dimensions, width, height, yaw, pitch, zoom, layer)
    dpg.delete_item(parent, children_only=True)
    for x in range(22, int(width), 24):
        for y in range(65, int(height) - 42, 24):
            dpg.draw_circle((x, y), .65, color=(94, 99, 119, 40), fill=(94, 99, 119, 40), parent=parent)
    edges = ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7))
    for cell in cells:
        vertices, coord = cell["points"], cell["coordinate"]
        alpha = int((75 if cell["visible"] else 9) * opacity)
        for face in ((4, 5, 6, 7), (1, 2, 6, 5), (3, 2, 6, 7)):
            dpg.draw_polygon([vertices[i][:2] for i in face], color=(0, 0, 0, 0),
                             fill=(177, 169, 221, int(alpha * .075)), parent=parent)
        for a, b in edges:
            dpg.draw_line(vertices[a][:2], vertices[b][:2], color=(145, 147, 176, alpha), parent=parent)
        if not cell["visible"]:
            continue
        x, y, _ = cell["center"]
        value = grid_value(state.grid, coord)
        glyph, color = (markers or {}).get(str(value), (str(value), (190, 193, 210)))
        if hidden:
            glyph = ""
        if glyph and glyph != "0":
            for radius, a in ((17, 9), (13, 15), (9, 20)):
                dpg.draw_circle((x, y), radius, color=(*color, 0), fill=(*color, a), parent=parent)
            if glyph.upper() == "X":
                dpg.draw_line((x - 7, y - 8), (x + 7, y + 8), color=color, thickness=3, parent=parent)
                dpg.draw_line((x - 7, y + 8), (x + 7, y - 8), color=color, thickness=3, parent=parent)
            elif glyph.upper() == "O":
                dpg.draw_circle((x, y), 9, color=color, thickness=3, parent=parent)
            else:
                dpg.draw_text((x - 5, y - 9), glyph[:4], color=color, size=17, parent=parent)
        elif coord in legal and not hidden:
            dpg.draw_circle((x, y), 2, color=(117, 205, 220, 150), fill=(117, 205, 220, 90), parent=parent)
        if coord == hover and not hidden:
            dpg.draw_circle((x, y), cell["radius"], color=(181, 161, 255), thickness=1.5, parent=parent)
    if not cells:
        dpg.draw_text((35, height / 2), "此拓樸請使用正式試玩視窗查看。", color=(171, 175, 193), size=17, parent=parent)
    return cells
