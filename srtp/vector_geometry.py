"""Deterministic extrusion of 2D vector drawings (pygame.draw semantics).

Settings are the ``vector_shape`` Asset recipe: shapes in source canvas pixels
(y down), colours normalized RGBA. Each shape becomes a closed prism; later
shapes are slightly thicker so draw order survives as depth without z-fighting.
No source code is executed; this only tessellates declared geometry.
"""
import math

MAX_TRIANGLES = 200_000
_OUTLINE_OPS = ('rect', 'circle', 'ellipse', 'polygon')


def extrude_shapes(settings):
    canvas = settings['canvas']
    depth = settings['depth']
    axis = settings.get('axis', 'z')
    segments = settings.get('segments', 48)
    if axis not in ('x', 'y', 'z') or not _positive(depth):
        raise ValueError('vector_shape requires a positive finite depth and x/y/z axis')
    if len(canvas) != 2 or not all(_positive(v) for v in canvas):
        raise ValueError('vector_shape canvas requires two positive sizes')
    if type(segments) is not int or not 8 <= segments <= 128:
        raise ValueError('vector_shape segments must be an integer 8..128')
    width, height = canvas
    size = settings.get('size') or [width / max(width, height), height / max(width, height)]
    if len(size) != 2 or not all(_positive(v) for v in size):
        raise ValueError('vector_shape size requires two positive values')
    shapes = settings['shapes']
    if not shapes:
        raise ValueError('vector_shape requires at least one shape')
    step = depth * 0.5 / len(shapes)

    def place(x, y, z):
        u, v = (x / width - .5) * size[0], (.5 - y / height) * size[1]
        return (u, v, z) if axis == 'z' else ((z, v, u) if axis == 'x' else (u, z, v))

    vertices, triangles, colors = [], [], []
    for index, shape in enumerate(shapes):
        rgba = _color(shape.get('color'))
        front, back = -depth / 2 - index * step, depth / 2 + index * step
        for fill, loops in _solids(shape, segments):
            for a, b, c in fill:
                _face(vertices, triangles, colors, [place(*a, front), place(*b, front), place(*c, front)], rgba)
                _face(vertices, triangles, colors, [place(*c, back), place(*b, back), place(*a, back)], rgba)
            for loop in loops:
                for i, a in enumerate(loop):
                    b = loop[(i + 1) % len(loop)]
                    if a == b:
                        continue
                    _face(vertices, triangles, colors, [place(*a, front), place(*b, front),
                                                        place(*b, back), place(*a, back)], rgba)
        if len(triangles) > MAX_TRIANGLES:
            raise ValueError('vector_shape exceeds {0} triangles; lower segments or shape count'.format(MAX_TRIANGLES))
    if not triangles:
        raise ValueError('vector_shape produced no visible geometry')
    return {'vertices': vertices, 'triangles': triangles, 'colors': colors, 'shapes': len(shapes)}


def shape_outline_2d(settings):
    """Filled 2D triangles per shape in canvas pixels, in draw order (for checks)."""
    segments = settings.get('segments', 48)
    return [(_color(shape.get('color')), [tri for fill, _ in _solids(shape, segments) for tri in fill])
            for shape in settings['shapes']]


def _solids(shape, segments):
    """Return [(triangles, boundary_loops)] for one pygame-style shape."""
    op = shape.get('op')
    width = shape.get('width', 1 if op in ('line', 'lines') else 0)
    if not isinstance(width, (int, float)) or isinstance(width, bool) or width < 0 or not math.isfinite(width):
        raise ValueError('vector_shape width must be a finite number >= 0')
    if op == 'line':
        return [_segment(*shape['points'], max(width, 1))]
    if op == 'lines':
        points = [tuple(p) for p in shape['points']]
        pairs = list(zip(points, points[1:])) + ([(points[-1], points[0])] if shape.get('closed') else [])
        return [_segment(a, b, max(width, 1)) for a, b in pairs]
    if op == 'polygon':
        points = _dedupe([tuple(p) for p in shape['points']])
        if len(points) < 3:
            raise ValueError('vector_shape polygon needs three distinct points')
        if width > 0:
            return [_segment(a, points[(i + 1) % len(points)], width) for i, a in enumerate(points)]
        if not _simple(points):
            raise ValueError('vector_shape polygon is self-intersecting; only simple polygons can be filled')
        return [(_ear_clip(points), [points])]
    if op == 'rect':
        x, y, w, h = shape['rect']
        if w <= 0 or h <= 0:
            raise ValueError('vector_shape rect requires positive width and height')
        radius = max(0, min(shape.get('border_radius', 0), w / 2, h / 2))
        corners = max(2, segments // 4)
        outer = _rounded_rect(x, y, w, h, radius, corners)
        if width <= 0 or 2 * width >= min(w, h):
            return [_convex(outer)]
        inner = _rounded_rect(x + width, y + width, w - 2 * width, h - 2 * width, max(0, radius - width), corners)
        return [_ring(outer, inner)]
    if op in ('circle', 'ellipse'):
        if op == 'circle':
            (cx, cy), rx = shape['center'], shape['radius']
            ry = rx
        else:
            x, y, w, h = shape['rect']
            cx, cy, rx, ry = x + w / 2, y + h / 2, w / 2, h / 2
        if rx <= 0 or ry <= 0:
            raise ValueError('vector_shape {0} requires a positive radius'.format(op))
        outer = _ellipse(cx, cy, rx, ry, segments)
        if width <= 0 or width >= min(rx, ry):
            return [_convex(outer)]
        return [_ring(outer, _ellipse(cx, cy, rx - width, ry - width, segments))]
    raise ValueError('vector_shape op must be one of line, lines, polygon, rect, circle, ellipse')


def _segment(a, b, width):
    (ax, ay), (bx, by) = a, b
    length = math.hypot(bx - ax, by - ay)
    half = width / 2
    if length == 0:
        return _convex([(ax - half, ay - half), (ax + half, ay - half), (ax + half, ay + half), (ax - half, ay + half)])
    nx, ny = -(by - ay) / length * half, (bx - ax) / length * half
    return _convex([(ax + nx, ay + ny), (bx + nx, by + ny), (bx - nx, by - ny), (ax - nx, ay - ny)])


def _convex(points):
    points = _dedupe(points)
    return ([(points[0], points[i], points[i + 1]) for i in range(1, len(points) - 1)], [points])


def _ring(outer, inner):
    count = len(outer)
    triangles = []
    for i in range(count):
        j = (i + 1) % count
        triangles += [(outer[i], outer[j], inner[j]), (outer[i], inner[j], inner[i])]
    return triangles, [outer, inner]


def _rounded_rect(x, y, w, h, radius, corners):
    centers = ((x + w - radius, y + radius, -90), (x + w - radius, y + h - radius, 0),
               (x + radius, y + h - radius, 90), (x + radius, y + radius, 180))
    points = []
    for cx, cy, start in centers:
        for k in range(corners + 1):
            angle = math.radians(start + 90 * k / corners)
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


def _ellipse(cx, cy, rx, ry, segments):
    return [(cx + rx * math.cos(2 * math.pi * i / segments), cy + ry * math.sin(2 * math.pi * i / segments))
            for i in range(segments)]


def _ear_clip(points):
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))
    remaining = list(points) if area > 0 else list(reversed(points))
    triangles = []
    guard = len(remaining) ** 2 + 8
    while len(remaining) > 3 and guard:
        guard -= 1
        for i in range(len(remaining)):
            a, b, c = remaining[i - 1], remaining[i], remaining[(i + 1) % len(remaining)]
            if _cross(a, b, c) <= 0:
                continue
            if any(_inside(p, a, b, c) for p in remaining if p not in (a, b, c)):
                continue
            triangles.append((a, b, c))
            remaining.pop(i)
            break
        else:
            raise ValueError('vector_shape polygon is self-intersecting or degenerate')
    if len(remaining) == 3:
        triangles.append(tuple(remaining))
    return triangles


def _simple(points):
    edges = list(zip(points, points[1:] + points[:1]))
    for i, (a, b) in enumerate(edges):
        for j in range(i + 1, len(edges)):
            if j == i + 1 or (i == 0 and j == len(edges) - 1):
                continue
            c, d = edges[j]
            if _segments_cross(a, b, c, d):
                return False
    return True


def _segments_cross(a, b, c, d):
    d1, d2, d3, d4 = _cross(c, d, a), _cross(c, d, b), _cross(a, b, c), _cross(a, b, d)
    if ((d1 > 0) != (d2 > 0)) and d1 and d2 and ((d3 > 0) != (d4 > 0)) and d3 and d4:
        return True
    return any(v == 0 and _on_segment(p, q, r) for v, p, q, r in
               ((d1, c, d, a), (d2, c, d, b), (d3, a, b, c), (d4, a, b, d)))


def _on_segment(p, q, r):
    return min(p[0], q[0]) <= r[0] <= max(p[0], q[0]) and min(p[1], q[1]) <= r[1] <= max(p[1], q[1])


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _inside(p, a, b, c):
    return _cross(a, b, p) >= 0 and _cross(b, c, p) >= 0 and _cross(c, a, p) >= 0


def _dedupe(points):
    result = [p for i, p in enumerate(points) if p != points[i - 1]] if len(points) > 1 else list(points)
    return result or list(points[:1])


def _face(vertices, triangles, colors, corners, rgba):
    offset = len(vertices)
    vertices.extend(corners)
    colors.extend([rgba] * len(corners))
    triangles.extend((offset, offset + i, offset + i + 1) for i in range(1, len(corners) - 1))


def _color(value):
    if not isinstance(value, (list, tuple)) or len(value) not in (3, 4) or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in value):
        raise ValueError('vector_shape color requires three or four normalized values')
    return tuple(value) + ((1.0,) if len(value) == 3 else ())


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
