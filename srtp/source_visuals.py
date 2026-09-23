"""Source-derived visual facts, with exact file/hash/location provenance.

No imported game is executed. Dynamic expressions remain observations requiring
interpretation; only literal colours are eligible for deterministic substitution.
"""
import ast
import hashlib
import json
from copy import deepcopy


def _rgba(value):
    if isinstance(value, str):
        try:
            from PIL import ImageColor
            value = ImageColor.getcolor(value, 'RGBA')
        except (ValueError, TypeError):
            return None
    if not isinstance(value, (list, tuple)) or len(value) not in (3, 4):
        return None
    if not all(type(v) is int and 0 <= v <= 255 for v in value):
        return None
    return [v / 255 for v in value] + ([1.0] if len(value) == 3 else [])


class VisualCatalog:
    def __init__(self, root, files):
        self.root = root.resolve()
        self.facts = {}
        self.truncated = False
        for relative, path in files.items():
            if len(self.facts) >= 768:
                self.truncated = True
                break
            if path.suffix not in ('.py', '.json'):
                continue
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            text = raw.decode('utf-8-sig', errors='replace')
            def add(kind, anchor, value, line=None, expression=None):
                if len(self.facts) >= 768:
                    self.truncated = True
                    return None
                identifier = 'visual:' + hashlib.sha256((relative + ':' + kind + ':' + anchor).encode()).hexdigest()[:20]
                self.facts[identifier] = {'id': identifier, 'kind': kind, 'value': value,
                    'source': {'path': relative, 'file_sha256': digest, 'anchor': anchor,
                               'line': line, 'expression': expression}}
                return identifier
            try:
                if path.suffix == '.json':
                    def visit(value, pointer='', visual=False):
                        if _rgba(value) and visual:
                            add('color', pointer, _rgba(value))
                        elif isinstance(value, dict):
                            for key, child in value.items():
                                visit(child, pointer + '/' + str(key).replace('~','~0').replace('/','~1'),
                                      visual or any(s in str(key).lower() for s in ('color', 'colour', 'palette')))
                        elif isinstance(value, list):
                            for i, child in enumerate(value):
                                visit(child, pointer + '/' + str(i), visual)
                    visit(json.loads(text))
                    continue
                tree = ast.parse(text)
            except (ValueError, SyntaxError, RecursionError):
                continue
            constants = {}
            # Only module-level literal constants can resolve name references.
            for statement in tree.body:
                if isinstance(statement, ast.Assign):
                    try:
                        value = ast.literal_eval(statement.value)
                    except (ValueError, TypeError, SyntaxError):
                        continue
                    for target in statement.targets:
                        if isinstance(target, ast.Name):
                            constants[target.id] = value
                            if _rgba(value) and any(s in target.id.lower() for s in ('color','colour','rgb')):
                                add('color', target.id, _rgba(value), statement.lineno, ast.get_source_segment(text, statement.value))
            def literal(node):
                if isinstance(node, ast.Name):
                    return constants.get(node.id)
                try:
                    return ast.literal_eval(node)
                except (ValueError, TypeError, SyntaxError):
                    return None
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = ast.unparse(node.func)
                short = function.rsplit('.', 1)[-1]
                color_index = None
                if '.draw.' in function and short in ('rect','circle','line','lines','polygon','ellipse','aaline'):
                    color_index = 1
                elif short == 'fill':
                    color_index = 0
                elif short == 'render':
                    color_index = 2
                elif short in ('color', 'pencolor', 'fillcolor', 'bgcolor'):
                    color_index = 0
                elif short == 'dot':
                    color_index = 1
                if color_index is not None:
                    color_id = None
                    if len(node.args) > color_index:
                        rgba = _rgba(literal(node.args[color_index]))
                        if rgba:
                            color_id = add('color', 'line:{0}:{1}:color'.format(node.lineno,node.col_offset), rgba,
                                           node.lineno, ast.get_source_segment(text,node.args[color_index]))
                    add('draw', 'line:{0}:{1}'.format(node.lineno,node.col_offset),
                        {'operation':function, 'color':color_id,
                         'arguments':[ast.get_source_segment(text,arg)[:300] for arg in node.args[:8]]}, node.lineno)
                elif '.font.' in function or '.image.load' in function or '.mixer.Sound' in function:
                    add('font' if '.font.' in function else 'resource_load',
                        'line:{0}:{1}'.format(node.lineno,node.col_offset),
                        {'operation':function,'arguments':[literal(arg) for arg in node.args[:4]]}, node.lineno)

    def to_mapping(self, max_chars=32000):
        facts, size = [], 0
        for fact in self.facts.values():
            size += len(json.dumps(fact, ensure_ascii=False))
            if size > max_chars:
                break
            facts.append(fact)
        return {'version':'source-visuals/1', 'facts':facts, 'truncated':self.truncated or len(facts)<len(self.facts),
                'policy':'Literal values are verified; dynamic draw expressions require interpretation. No fidelity certification.'}

    def resolve(self, value, used=None, pointer=''):
        """Lower an explicit visual reference to its measured value, not a guess."""
        if isinstance(value, dict):
            if set(value) == {'source_visual'}:
                fact = self.facts.get(value['source_visual'])
                if not fact or fact['kind'] != 'color':
                    raise ValueError('source_visual requires a known source colour ID')
                path = (self.root/fact['source']['path']).resolve()
                if not path.is_relative_to(self.root) or hashlib.sha256(path.read_bytes()).hexdigest() != fact['source']['file_sha256']:
                    raise ValueError('Source visual evidence changed; rebuild the visual catalogue')
                if used is not None:
                    used.append({'target':pointer, **deepcopy(fact)})
                return deepcopy(fact['value'])
            return {k:self.resolve(v,used,pointer+'/'+str(k).replace('~','~0').replace('/','~1')) for k,v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v,used,pointer+'/'+str(i)) for i,v in enumerate(value)]
        return deepcopy(value)
