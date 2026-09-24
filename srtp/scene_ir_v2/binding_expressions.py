"""Bounded, pure presentation expressions shared by authoring and execution.

No game names, source rewriting, eval, callbacks from model code, or state writes.
Reads use existing Scene source descriptors. This composes those capabilities
rather than adding a new renderer property for every game-specific condition.
"""
import math
from copy import deepcopy
from collections.abc import Mapping

MAX_DEPTH = 16
MAX_NODES = 128
# Arity and operand types are the single registry for generation and validation.
OPERATORS = {
    'all': (1, 16, 'boolean'), 'any': (1, 16, 'boolean'), 'not': (1, 1, 'boolean'),
    'eq': (2, 2, 'value'), 'ne': (2, 2, 'value'),
    'lt': (2, 2, 'number'), 'le': (2, 2, 'number'),
    'gt': (2, 2, 'number'), 'ge': (2, 2, 'number'),
    'add': (2, 16, 'number'), 'subtract': (2, 2, 'number'),
    'multiply': (2, 16, 'number'), 'divide': (2, 2, 'number'),
    'modulo': (2, 2, 'number'), 'floor': (1, 1, 'number'),
    'min': (1, 16, 'number'), 'max': (1, 16, 'number'),
    'if': (3, 3, 'conditional'),
}


def contract():
    return {'limits': {'depth': MAX_DEPTH, 'nodes': MAX_NODES},
            'literal': {'op': 'literal', 'value': 'JSON scalar'},
            'read': {'op': 'read', 'source': 'state / flow / entity_component / interaction descriptor'},
            'operators': {op: {'min_args': lo, 'max_args': hi, 'operands': kind}
                          for op, (lo, hi, kind) in OPERATORS.items()},
            'evaluation': 'Pure; all/any/if short-circuit. Missing references and invalid types fail explicitly. No gameplay effects.'}


def schema():
    # Deep validation is bounded by validate_expression before evaluation. Keep
    # the prompt compact; duplicating a recursive schema per binding is costly.
    return {'type': 'object', 'required': ['op'], 'additionalProperties': False,
            'properties': {'op': {'enum': ['literal', 'read', *OPERATORS]},
                           'value': {'type': ['null', 'boolean', 'number', 'string']},
                           'source': {'type': 'object'},
                           'args': {'type': 'array', 'items': {'type': 'object'}, 'maxItems': 16}},
            'description': 'Use backend_profile.presentation_expression for recursive arity/types. Reads are existing Scene sources; no arbitrary Python or Rule effects.'}


def validate_expression(value, validate_read, path='/expression'):
    issues = []
    reads = []
    count = 0
    def visit(node, pointer, depth):
        nonlocal count
        count += 1
        if depth > MAX_DEPTH or count > MAX_NODES:
            issues.append(pointer + ': presentation expression exceeds depth/node budget')
            return
        if not isinstance(node, Mapping):
            issues.append(pointer + ': expression must be an object'); return
        op = node.get('op')
        if op == 'literal':
            if set(node) != {'op', 'value'}:
                issues.append(pointer + ': literal requires only op/value'); return
            literal = node['value']
            if literal is not None and type(literal) not in (str, int, float, bool):
                issues.append(pointer + ': literal must be a JSON scalar')
            elif type(literal) in (int, float) and not math.isfinite(literal):
                issues.append(pointer + ': literal number must be finite')
        elif op == 'read':
            source = node.get('source')
            if set(node) != {'op', 'source'} or not isinstance(source, Mapping):
                issues.append(pointer + ': read requires only op/source'); return
            if source.get('kind') not in ('state', 'flow', 'entity_component', 'interaction'):
                issues.append(pointer + ': read requires an existing non-expression Scene source'); return
            issues.extend(validate_read(source, pointer + '/source'))
            reads.append(source)
        elif isinstance(op, str) and op in OPERATORS:
            lo, hi, _ = OPERATORS[op]
            args = node.get('args')
            if set(node) != {'op', 'args'} or not isinstance(args, (list, tuple)) or not lo <= len(args) <= hi:
                issues.append(pointer + ': '+op+' requires '+str(lo)+'..'+str(hi)+' args'); return
            for index, child in enumerate(args):
                visit(child, pointer+'/args/'+str(index), depth+1)
        else:
            issues.append(pointer + ': unsupported presentation operator '+str(op))
    visit(value, path, 1)
    return issues, reads


def evaluate_expression(node, read):
    op = node['op']
    if op == 'literal': return deepcopy(node['value'])
    if op == 'read': return read(node['source'])
    def boolean(value):
        if type(value) is not bool: raise ValueError('Presentation '+op+' requires booleans, not truthy values')
        return value
    args = node['args']
    if op == 'if':
        return evaluate_expression(args[1] if boolean(evaluate_expression(args[0], read)) else args[2], read)
    if op == 'all': return all(boolean(evaluate_expression(arg, read)) for arg in args)
    if op == 'any': return any(boolean(evaluate_expression(arg, read)) for arg in args)
    values = [evaluate_expression(arg, read) for arg in args]
    if op == 'not': return not boolean(values[0])
    if op in ('eq', 'ne'):
        equal = values[0] == values[1] and (type(values[0]) is not bool or type(values[1]) is bool) and (type(values[1]) is not bool or type(values[0]) is bool)
        return equal if op == 'eq' else not equal
    if not all(type(value) in (int, float) and math.isfinite(value) for value in values):
        raise ValueError('Presentation '+op+' requires finite numbers')
    if op in ('divide', 'modulo') and values[1] == 0:
        raise ValueError('Presentation '+op+' divisor must not be zero')
    if op == 'lt': return values[0] < values[1]
    if op == 'le': return values[0] <= values[1]
    if op == 'gt': return values[0] > values[1]
    if op == 'ge': return values[0] >= values[1]
    if op == 'add': result = sum(values)
    elif op == 'subtract': result = values[0] - values[1]
    elif op == 'multiply': result = math.prod(values)
    elif op == 'divide': result = values[0] / values[1]
    elif op == 'modulo': result = values[0] % values[1]
    elif op == 'floor': result = math.floor(values[0])
    elif op == 'min': result = min(values)
    elif op == 'max': result = max(values)
    else: raise ValueError('Unsupported presentation operator '+str(op))
    if not math.isfinite(result): raise ValueError('Presentation arithmetic produced a non-finite number')
    return result


def infer_type(node, read_type):
    """Reject known type errors even in branches which are initially inactive."""
    op=node['op']
    if op=='read':return read_type(node['source'])
    if op=='literal':
        value=node['value']
        return 'boolean' if type(value) is bool else 'number' if type(value) in (int,float) else 'string' if isinstance(value,str) else 'null'
    types=[infer_type(arg,read_type) for arg in node['args']]
    expected=OPERATORS[op][2]
    if expected in ('boolean','number') and any(t not in (None,expected) for t in types):
        raise ValueError('Presentation '+op+' requires '+expected+' operands; inferred '+str(types))
    if op=='if':
        if types[0] not in (None,'boolean'):raise ValueError('Presentation if condition must be boolean')
        return types[1] if types[1]==types[2] else None
    if op in ('all','any','not','eq','ne','lt','le','gt','ge'):return 'boolean'
    return 'number'
