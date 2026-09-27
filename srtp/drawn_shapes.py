"""Static extraction of pygame.draw pictures into ``vector_shape`` recipes.

Games often draw their pieces and cells with ``pygame.draw`` instead of
shipping image files. This reads those calls from the AST (the game is never
imported or executed) and resolves their arguments against literal module
constants and local assignments. Positions that depend on loop variables are
kept symbolic, so a piece drawn relative to its cell rectangle is expressed in
that rectangle's local frame. A call whose values cannot be proven is reported
as unresolved and its group gets no recipe; nothing is guessed.
"""
import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

DRAW_OPS = ('rect', 'circle', 'ellipse', 'line', 'lines', 'aaline', 'aalines', 'polygon')
DESCRIPTOR = 'application/vnd.cubeengine.presentation+json'
DEFAULT_DEPTH = 0.1
MAX_GROUPS = 64
_UNSUPPORTED_KEYWORDS = {'draw_top_right', 'draw_top_left', 'draw_bottom_left', 'draw_bottom_right',
                         'border_top_left_radius', 'border_top_right_radius',
                         'border_bottom_left_radius', 'border_bottom_right_radius'}


class Unknown:
    def __init__(self, text):
        self.text = text


@dataclass(frozen=True)
class Sym:
    """``base + offset`` where ``base`` is an unresolved source expression."""
    base: str
    offset: float


@dataclass(frozen=True)
class RectVal:
    x: object
    y: object
    w: float
    h: float
    origin: str  # source expression that created it, for provenance

    def attribute(self, name):
        cx, cy = _add(self.x, self.w / 2), _add(self.y, self.h / 2)
        right, bottom = _add(self.x, self.w), _add(self.y, self.h)
        table = {'x': self.x, 'left': self.x, 'y': self.y, 'top': self.y, 'right': right, 'bottom': bottom,
                 'w': self.w, 'width': self.w, 'h': self.h, 'height': self.h, 'centerx': cx, 'centery': cy,
                 'center': (cx, cy), 'topleft': (self.x, self.y), 'topright': (right, self.y),
                 'bottomleft': (self.x, bottom), 'bottomright': (right, bottom), 'midtop': (cx, self.y),
                 'midbottom': (cx, bottom), 'midleft': (self.x, cy), 'midright': (right, cy),
                 'size': (self.w, self.h)}
        return table.get(name)


MODEL_POLICY = (
    'Each derivation was extracted statically from pygame.draw calls; coordinates, sizes, widths and colours '
    'are the source values in the frame rectangle. To show that picture, copy the derivation into /derivations '
    'unchanged and point a role resource at its id. Only depth/axis/size are presentation choices; depth grows '
    'with frame.draw_order so a piece drawn over its cell stays in front when both share a node. A group '
    'with unresolved entries has no recipe: do not invent its geometry; cite it in unresolved instead.'
)


def drawings_for_model(groups, *, max_chars=24000):
    """Bounded view for model prompts: recipes first, then unresolved groups."""
    import json
    ordered = sorted(groups, key=lambda g: g['derivation'] is None)
    selected, size = [], 0
    for group in ordered:
        size += len(json.dumps(group, ensure_ascii=False))
        if size > max_chars:
            break
        selected.append(group)
    return {'version': 'source-drawings/1', 'policy': MODEL_POLICY, 'groups': selected,
            'truncated': len(selected) < len(groups)}


def discover_drawn_shapes(files, *, max_groups=MAX_GROUPS):
    """Return drawing groups with provenance and, when proven, a derivation."""
    groups = []
    for relative, path in sorted(files.items()):
        if PurePosixPath(relative).suffix.lower() != '.py' or len(groups) >= max_groups:
            continue
        raw = path.read_bytes()
        text = raw.decode('utf-8-sig', errors='replace')
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            continue
        extractor = _FileExtractor(relative, hashlib.sha256(raw).hexdigest(), text, tree)
        groups.extend(extractor.groups()[:max_groups - len(groups)])
    return groups


class _FileExtractor:
    def __init__(self, relative, digest, text, tree):
        self.relative, self.digest, self.text, self.tree = relative, digest, text, tree
        self.aliases = _aliases(tree)
        self.constants = {}
        self.class_attributes = {}
        module = _Scope(self, None, [])
        for statement in tree.body:
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
                value = module.value(statement.value, statement.lineno)
                if not isinstance(value, Unknown):
                    self.constants[statement.targets[0].id] = value
        self.stem = re.sub(r'[^a-z0-9]+', '_', PurePosixPath(relative).with_suffix('').as_posix().lower()).strip('_') or 'source'
        if not self.stem[0].isalpha():
            self.stem = 's' + self.stem
        self.owner_class = {}
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            methods = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            for method in methods:
                self.owner_class[id(method)] = cls.name
            self.class_attributes[cls.name] = self._self_attributes(methods)

    def _self_attributes(self, methods):
        """self.x = <literal> in __init__, when x is assigned exactly once in the class."""
        counts, found = {}, {}
        for method in methods:
            for node in _scope_nodes(method):
                targets = node.targets if isinstance(node, ast.Assign) else (
                    [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign)) else [])
                for target in targets:
                    for item in ast.walk(target):
                        if isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name) and item.value.id == 'self':
                            counts[item.attr] = counts.get(item.attr, 0) + 1
                            if method.name == '__init__' and isinstance(node, ast.Assign) and target is item:
                                found[item.attr] = (node, method)
        result = {}
        for name, (node, method) in found.items():
            if counts[name] == 1:
                value = _Scope(self, method, _parameter_names(method)).value(node.value, node.lineno)
                if not isinstance(value, (Unknown, RectVal)):
                    result[name] = value
        return result

    def qualified(self, node):
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return self.qualified(node.value) + '.' + node.attr
        return ''

    def draw_op(self, node):
        if not isinstance(node, ast.Call):
            return None
        head, _, op = self.qualified(node.func).rpartition('.')
        return op if op in DRAW_OPS and head == 'pygame.draw' else None

    def groups(self):
        result = []
        scopes = [(n, n.name) for n in ast.walk(self.tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for function, name in [(self.tree, '<module>')] + scopes:
            parameters = [] if function is self.tree else _parameter_names(function)
            scope = _Scope(self, function, parameters, self.owner_class.get(id(function)))
            pending = []
            for body, parents in _bodies(function):
                calls = [s.value for s in body if isinstance(s, ast.Expr) and self.draw_op(s.value)]
                if calls:
                    pending.append(self._group(scope, name, calls, parents))
            # Any rectangle drawn or built in this function may frame a group
            # (a piece centred in the cell drawn by a sibling call).
            candidates = scope.all_rects() + [r for _, _, rects in pending for r in rects]
            finished = [self._finish(group, shapes, rects, candidates) for group, shapes, rects in pending]
            _stack_by_draw_order(finished)
            result.extend(finished)
        return result

    def _group(self, scope, function, calls, parents):
        shapes, facts, unresolved, rects = [], [], [], []
        for call in calls:
            op = self.draw_op(call)
            fact = {'line': call.lineno, 'operation': 'pygame.draw.' + op,
                    'source': ast.get_source_segment(self.text, call)}
            try:
                shape, used = _shape(op, call, scope)
            except _Unproven as exc:
                unresolved.append({'line': call.lineno, 'reason': str(exc)})
                fact['resolved'] = None
            else:
                shapes.append(shape)
                rects.extend(used)
                fact['resolved'] = _public(shape)
            facts.append(fact)
        start, end = calls[0].lineno, calls[-1].end_lineno
        group = {
            'id': 'drawn:' + hashlib.sha256('{0}:{1}:{2}'.format(self.relative, start, end).encode()).hexdigest()[:16],
            'source': {'path': self.relative, 'file_sha256': self.digest, 'line_start': start, 'line_end': end,
                       'function': function},
            'conditions': parents['conditions'], 'loops': parents['loops'],
            'calls': facts, 'unresolved': unresolved, 'frame': None, 'derivation': None,
        }
        return group, shapes, rects

    def _finish(self, group, shapes, rects, candidates):
        if group['unresolved']:
            return group
        start, end = group['source']['line_start'], group['source']['line_end']
        frame = _frame(shapes, rects, candidates)
        if isinstance(frame, str):
            group['unresolved'].append({'line': start, 'reason': frame})
            return group
        (fx, fy, fw, fh), description = frame
        local = [_localize(shape, fx, fy) for shape in shapes]
        settings = {'canvas': [fw, fh], 'shapes': local, 'depth': DEFAULT_DEPTH, 'axis': 'z',
                    'size': [fw / max(fw, fh), fh / max(fw, fh)]}
        from .vector_geometry import extrude_shapes
        try:
            extrude_shapes(settings)
        except (ValueError, KeyError, TypeError) as exc:
            group['unresolved'].append({'line': start, 'reason': 'drawing is not renderable as vector_shape: ' + str(exc)})
            return group
        group['frame'] = description
        slug = 'shape.{0}.l{1}'.format(self.stem, start)
        group['derivation'] = {
            'id': 'asset:' + slug, 'name': 'Source drawing {0}:{1}-{2}'.format(self.relative, start, end),
            'kind': 'model', 'media_type': DESCRIPTOR, 'strategy': 'vector_shape', 'inputs': [],
            'settings': settings,
            'expected_content_hash': '', 'license_policy': 'inherit',
        }
        return group


def _stack_by_draw_order(groups):
    """Pictures drawn later in the same frame (a piece over its cell) are thicker,
    so they stay in front when a Scene overlays them; the source order is kept."""
    ranks = {}
    for group in sorted(groups, key=lambda g: g['source']['line_start']):
        if group['derivation'] is None:
            continue
        key = json_key(group['frame'])
        rank = ranks.get(key, 0)
        ranks[key] = rank + 1
        group['frame']['draw_order'] = rank
        group['derivation']['settings']['depth'] = round(DEFAULT_DEPTH * (1 + 0.5 * rank), 6)


def json_key(frame):
    return (frame.get('kind'), frame.get('expression'), tuple(frame.get('size') or ()), repr(frame.get('origin')))


class _Unproven(ValueError):
    pass


class _Scope:
    """Values visible at a line: module constants plus prior local assignments."""

    def __init__(self, extractor, function, parameters, owner_class=None):
        self.extractor = extractor
        self.unknown = set(parameters)  # names whose value is never proven
        self.self_attributes = extractor.class_attributes.get(owner_class, {})
        self.assignments = {}
        self.cache = {}
        self.rects = {}
        self.last_rects = []
        if function is not None:
            for node in _scope_nodes(function):
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    self.assignments.setdefault(node.targets[0].id, []).append(node)
                    continue
                targets = []
                if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension, ast.AugAssign, ast.AnnAssign)):
                    targets = [node.target]
                elif isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, (ast.With, ast.AsyncWith)):
                    targets = [item.optional_vars for item in node.items if item.optional_vars is not None]
                elif isinstance(node, ast.NamedExpr):
                    targets = [node.target]
                for target in targets:
                    self.unknown.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))

    def all_rects(self):
        return list(self.rects.values())

    def value(self, node, line):
        text = ast.unparse(node)
        if isinstance(node, ast.Constant) and isinstance(node.value, (bool, int, float, str)):
            return node.value
        if isinstance(node, (ast.Tuple, ast.List)):
            return tuple(self.value(item, line) for item in node.elts)
        if isinstance(node, ast.Name):
            return self._name(node.id, line, text)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            inner = self.value(node.operand, line)
            return -inner if _number(inner) else Unknown(text)
        if isinstance(node, ast.BinOp):
            return _binop(node.op, self.value(node.left, line), self.value(node.right, line), text)
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == 'self' and node.attr in self.self_attributes:
                return self.self_attributes[node.attr]
            owner = self.value(node.value, line)
            if isinstance(owner, RectVal):
                found = owner.attribute(node.attr)
                if found is not None:
                    self.last_rects.append(owner)
                    return found
            return Unknown(text)
        if isinstance(node, ast.Subscript):
            owner, key = self.value(node.value, line), self.value(node.slice, line)
            if isinstance(owner, tuple) and isinstance(key, int) and -len(owner) <= key < len(owner):
                return owner[key]
            if isinstance(owner, dict) and isinstance(key, (str, int)) and key in owner:
                return owner[key]
            return Unknown(text)
        if isinstance(node, ast.Dict):
            keys = [self.value(k, line) if k is not None else Unknown('**') for k in node.keys]
            values = [self.value(v, line) for v in node.values]
            if all(isinstance(k, (str, int)) for k in keys):
                return dict(zip(keys, values))
            return Unknown(text)
        if isinstance(node, ast.Call):
            return self._call(node, line, text)
        return Unknown(text)

    def _name(self, name, line, text):
        if name in self.unknown:
            return Unknown(text)
        candidates = [n for n in self.assignments.get(name, []) if n.lineno < line]
        if candidates:
            node = max(candidates, key=lambda n: n.lineno)
            if id(node) not in self.cache:
                self.cache[id(node)] = Unknown(text)  # break self-reference cycles
                value = self.value(node.value, node.lineno)
                if isinstance(value, RectVal):
                    value = RectVal(value.x, value.y, value.w, value.h, name + '@L' + str(node.lineno))
                    self.rects[id(node)] = value
                self.cache[id(node)] = value
            return self.cache[id(node)]
        return self.extractor.constants.get(name, Unknown(text))

    def _call(self, node, line, text):
        function = self.extractor.qualified(node.func)
        args = [self.value(a, line) for a in node.args]
        if function in ('pygame.Rect', 'pygame.rect.Rect', 'pygame.FRect', 'pygame.rect.FRect'):
            if len(args) == 2 and all(isinstance(a, tuple) and len(a) == 2 for a in args):
                args = [*args[0], *args[1]]
            if len(args) == 1 and isinstance(args[0], tuple) and len(args[0]) == 4:
                args = list(args[0])
            return _rect(args, text)
        if isinstance(node.func, ast.Attribute) and node.func.attr in ('inflate', 'move') and len(args) in (1, 2):
            owner = self.value(node.func.value, line)
            delta = args[0] if len(args) == 1 else tuple(args)
            if isinstance(owner, RectVal) and isinstance(delta, tuple) and len(delta) == 2 and all(map(_number, delta)):
                dx, dy = delta
                if node.func.attr == 'move':
                    return RectVal(_add(owner.x, dx), _add(owner.y, dy), owner.w, owner.h, text)
                return RectVal(_add(owner.x, -dx / 2), _add(owner.y, -dy / 2), owner.w + dx, owner.h + dy, text)
            return Unknown(text)
        if function in ('int', 'round') and len(args) == 1:
            value = args[0]
            cast = int if function == 'int' else round
            if _number(value):
                return cast(value)
            if isinstance(value, Sym):
                # Exact when the unresolved base is integral (grid index * size).
                return Sym(value.base, cast(value.offset))
            return Unknown(text)
        if function in ('min', 'max') and args and all(map(_number, args)):
            return (min if function == 'min' else max)(args)
        if function in ('pygame.Color', 'pygame.color.Color') and len(args) == 1 and isinstance(args[0], str):
            return args[0]
        if function in ('pygame.Color', 'pygame.color.Color') and len(args) in (3, 4) and all(map(_number, args)):
            return tuple(args)
        return Unknown(text)


def _shape(op, call, scope):
    scope.last_rects = []
    line = call.lineno

    def argument(index, name, default=_Unproven):
        node = call.args[index] if len(call.args) > index else next((k.value for k in call.keywords if k.arg == name), None)
        if node is None:
            if default is _Unproven:
                raise _Unproven('missing argument ' + name)
            return default
        value = scope.value(node, line)
        if isinstance(value, Unknown):
            raise _Unproven('{0} depends on {1!r}, which is not statically known'.format(name, value.text))
        return value

    unsupported = sorted(k.arg for k in call.keywords if k.arg in _UNSUPPORTED_KEYWORDS)
    if unsupported:
        raise _Unproven('unsupported pygame.draw options: ' + ', '.join(unsupported))
    color = _color(argument(1, 'color'))
    if op == 'rect':
        rect = argument(2, 'rect')
        if isinstance(rect, RectVal):
            scope.last_rects.append(rect)
            x, y, w, h = rect.x, rect.y, rect.w, rect.h
        elif isinstance(rect, tuple) and len(rect) == 4 and _number(rect[2]) and _number(rect[3]):
            x, y, w, h = [_coordinate(v) for v in rect[:2]] + list(rect[2:])
            scope.last_rects.append(RectVal(x, y, w, h, 'rect argument line {0}'.format(line)))
        else:
            raise _Unproven('rect argument is not a statically known rectangle')
        shape = {'op': 'rect', 'rect': [x, y, w, h], 'width': _nonnegative(argument(3, 'width', 0)),
                 'border_radius': _nonnegative(argument(4, 'border_radius', 0))}
    elif op == 'circle':
        center, radius = _point(argument(2, 'center')), argument(3, 'radius')
        if not _number(radius) or radius <= 0:
            raise _Unproven('circle radius must be a known positive number')
        shape = {'op': 'circle', 'center': center, 'radius': radius, 'width': _nonnegative(argument(4, 'width', 0))}
    elif op == 'ellipse':
        rect = argument(2, 'rect')
        if isinstance(rect, RectVal):
            scope.last_rects.append(rect)
            rect = (rect.x, rect.y, rect.w, rect.h)
        if not (isinstance(rect, tuple) and len(rect) == 4 and _number(rect[2]) and _number(rect[3])):
            raise _Unproven('ellipse rect is not a statically known rectangle')
        shape = {'op': 'ellipse', 'rect': [_coordinate(v) for v in rect[:2]] + list(rect[2:]), 'width': _nonnegative(argument(3, 'width', 0))}
    elif op in ('line', 'aaline'):
        shape = {'op': 'line', 'points': [_point(argument(2, 'start_pos')), _point(argument(3, 'end_pos'))],
                 'width': 1 if op == 'aaline' else _nonnegative(argument(4, 'width', 1))}
    elif op in ('lines', 'aalines'):
        closed, points = argument(2, 'closed'), argument(3, 'points')
        if not isinstance(points, tuple) or len(points) < 2:
            raise _Unproven('lines points are not a statically known sequence')
        shape = {'op': 'lines', 'closed': bool(closed), 'points': [_point(p) for p in points],
                 'width': 1 if op == 'aalines' else _nonnegative(argument(4, 'width', 1))}
    else:
        points = argument(2, 'points')
        if not isinstance(points, tuple) or len(points) < 3:
            raise _Unproven('polygon points are not a statically known sequence')
        shape = {'op': 'polygon', 'points': [_point(p) for p in points], 'width': _nonnegative(argument(3, 'width', 0))}
    shape['color'] = color
    return shape, list(scope.last_rects)


def _frame(shapes, used_rects, scope_rects):
    """Pick the source rectangle the group is drawn in (or its bounding box)."""
    xs, ys = [], []
    for shape in shapes:
        for x, y in _anchor_points(shape):
            xs.append(x)
            ys.append(y)
    x_bases = {v.base for v in xs if isinstance(v, Sym)}
    y_bases = {v.base for v in ys if isinstance(v, Sym)}
    if not x_bases and not y_bases:
        left, top, right, bottom = _bounds(shapes)
        return (left, top, right - left, bottom - top), {'kind': 'bounding_box', 'origin': [left, top]}
    if len(x_bases) != 1 or len(y_bases) != 1 or any(_number(v) for v in xs + ys):
        return 'drawing mixes positions relative to different or unknown origins'
    base = (x_bases.pop(), y_bases.pop())
    for rect in used_rects + scope_rects:
        if isinstance(rect.x, Sym) and isinstance(rect.y, Sym) and (rect.x.base, rect.y.base) == base:
            return (rect.x.offset, rect.y.offset, rect.w, rect.h), {
                'kind': 'source_rect', 'expression': rect.origin, 'size': [rect.w, rect.h],
                # Screen origin = base + offset per axis (base: the unresolved source expression).
                'origin': [{'base': rect.x.base, 'offset': _round(rect.x.offset)},
                           {'base': rect.y.base, 'offset': _round(rect.y.offset)}]}
    return 'no statically sized rectangle shares the drawing origin ({0}, {1})'.format(*base)


def _localize(shape, fx, fy):
    def point(p):
        return [_round(_offset(p[0]) - fx), _round(_offset(p[1]) - fy)]
    result = {'op': shape['op']}
    if 'rect' in shape:
        x, y, w, h = shape['rect']
        result['rect'] = point((x, y)) + [_round(w), _round(h)]
    if 'center' in shape:
        result['center'] = point(shape['center'])
        result['radius'] = _round(shape['radius'])
    if 'points' in shape:
        result['points'] = [point(p) for p in shape['points']]
    for key in ('closed', 'width', 'border_radius'):
        if key in shape:
            result[key] = shape[key]
    result['color'] = shape['color']
    return result


def _anchor_points(shape):
    if 'rect' in shape:
        x, y, w, h = shape['rect']
        return [(x, y), (_add(x, w), _add(y, h))]
    if 'center' in shape:
        return [tuple(shape['center'])]
    return [tuple(p) for p in shape['points']]


def _bounds(shapes):
    xs, ys = [], []
    for shape in shapes:
        pad = shape.get('width', 0) / 2 if shape['op'] in ('line', 'lines') else 0
        if shape['op'] == 'circle':
            (cx, cy), r = shape['center'], shape['radius']
            xs += [cx - r, cx + r]
            ys += [cy - r, cy + r]
            continue
        for x, y in _anchor_points(shape):
            xs += [x - pad, x + pad]
            ys += [y - pad, y + pad]
    return min(xs), min(ys), max(xs), max(ys)


def _bodies(function):
    """Yield (statement list, enclosing conditions/loops) inside one function."""
    def visit(body, conditions, loops):
        yield body, {'conditions': conditions, 'loops': loops}
        for statement in body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(statement, ast.If):
                test = ast.unparse(statement.test)
                yield from visit(statement.body, [{'test': test, 'branch': 'then', 'line': statement.lineno}] + conditions, loops)
                yield from visit(statement.orelse, [{'test': test, 'branch': 'else', 'line': statement.lineno}] + conditions, loops)
            elif isinstance(statement, (ast.For, ast.AsyncFor)):
                loop = {'target': ast.unparse(statement.target), 'iter': ast.unparse(statement.iter), 'line': statement.lineno}
                yield from visit(statement.body, conditions, [loop] + loops)
            elif isinstance(statement, ast.While):
                yield from visit(statement.body, conditions, [{'target': None, 'iter': 'while ' + ast.unparse(statement.test),
                                                               'line': statement.lineno}] + loops)
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                yield from visit(statement.body, conditions, loops)
            elif isinstance(statement, ast.Try):
                for block in (statement.body, statement.orelse, statement.finalbody):
                    yield from visit(block, conditions, loops)
    yield from visit(function.body, [], [])


def _scope_nodes(scope):
    """Nodes of one function/module body, excluding nested function and class bodies."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


def _aliases(tree):
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split('.')[0]] = item.name if item.asname else item.name.split('.')[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                aliases[item.asname or item.name] = node.module + '.' + item.name
    return aliases


def _parameter_names(function):
    args = function.args
    names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
    names += [a.arg for a in (args.vararg, args.kwarg) if a is not None]
    return names


def _binop(op, left, right, text):
    if _number(left) and _number(right):
        try:
            return {ast.Add: lambda: left + right, ast.Sub: lambda: left - right, ast.Mult: lambda: left * right,
                    ast.Div: lambda: left / right, ast.FloorDiv: lambda: left // right,
                    ast.Mod: lambda: left % right}[type(op)]()
        except (KeyError, ZeroDivisionError):
            return Unknown(text)
    if isinstance(op, (ast.Add, ast.Sub)):
        sign = 1 if isinstance(op, ast.Add) else -1
        if isinstance(left, (Sym, Unknown)) and _number(right):
            return _add(_symbolic(left), sign * right)
        if _number(left) and isinstance(right, (Sym, Unknown)) and sign == 1:
            return _add(_symbolic(right), left)
        if isinstance(left, Sym) and isinstance(right, Sym) and left.base == right.base and sign == -1:
            return left.offset - right.offset
    return Unknown(text)


def _symbolic(value):
    return value if isinstance(value, Sym) else Sym(value.text, 0)


def _add(value, delta):
    if _number(value):
        return value + delta
    if isinstance(value, Sym):
        return Sym(value.base, value.offset + delta)
    return Unknown('?')


def _rect(args, text):
    if len(args) != 4 or not _number(args[2]) or not _number(args[3]) or args[2] <= 0 or args[3] <= 0:
        return Unknown(text)
    x, y = [(_symbolic(v) if isinstance(v, Unknown) else v) for v in args[:2]]
    if not all(_number(v) or isinstance(v, Sym) for v in (x, y)):
        return Unknown(text)
    return RectVal(x, y, args[2], args[3], text)


def _point(value):
    if isinstance(value, tuple) and len(value) == 2:
        return [_coordinate(v) for v in value]
    raise _Unproven('point is not statically known')


def _coordinate(value):
    """A coordinate may stay relative to an unresolved origin; framing checks it."""
    if _number(value) or isinstance(value, Sym):
        return value
    if isinstance(value, Unknown):
        return Sym(value.text, 0)
    raise _Unproven('coordinate is not a number')


def _color(value):
    if isinstance(value, str):
        try:
            from PIL import ImageColor
            value = ImageColor.getcolor(value, 'RGBA')
        except (ValueError, TypeError):
            raise _Unproven('colour name {0!r} is not recognized'.format(value))
    if isinstance(value, tuple) and len(value) in (3, 4) and all(isinstance(v, int) and 0 <= v <= 255 for v in value):
        return [round(v / 255, 6) for v in value] + ([1.0] if len(value) == 3 else [])
    raise _Unproven('colour is not a statically known RGB value')


def _nonnegative(value):
    if not _number(value) or value < 0:
        raise _Unproven('width/radius must be a known number >= 0')
    return value


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _offset(value):
    return value.offset if isinstance(value, Sym) else value


def _round(value):
    value = float(value)
    return int(value) if value.is_integer() else round(value, 4)


def _public(shape):
    def plain(value):
        if isinstance(value, Sym):
            return '{0} + {1}'.format(value.base, _round(value.offset)) if value.offset else value.base
        if isinstance(value, (list, tuple)):
            return [plain(v) for v in value]
        return value
    return {k: plain(v) for k, v in shape.items()}
