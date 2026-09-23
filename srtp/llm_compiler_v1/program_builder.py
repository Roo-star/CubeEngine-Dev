"""Small deterministic authoring layer; never executes model Python.

The model supplies semantic definitions and may use {"expr": "..."} for pure
expressions. The engine builds ASTs, patch envelopes, revisions and references.
"""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from copy import deepcopy
from typing import Any, Mapping

from .contracts import LLM_PROPOSAL_VERSION


ENGINE_OWNED_FIELDS = frozenset(('ir_version', 'document_id', 'revision', 'content_hash', 'provenance', 'dependencies'))


class DefinitionValidationError(ValueError):
    def __init__(self, diagnostics):
        # Collect the entire pass before bounding feedback size. Never silently
        # claim the first truncated errors are the full validation result.
        diagnostics=list(dict.fromkeys(diagnostics))
        self.diagnostics = diagnostics[:256]
        if len(diagnostics)>256:
            self.diagnostics.append('Further validation diagnostics omitted: '+str(len(diagnostics)-256))
        super().__init__('; '.join(self.diagnostics))


def authoring_schema(document_schema):
    """Describe the model's definition, not the engine's sealed document."""
    schema = deepcopy(document_schema)
    schema.pop('$id', None)
    schema['title'] = 'Model-authored definition (engine-owned envelope excluded)'
    schema['properties'] = {k:v for k,v in schema.get('properties', {}).items() if k not in ENGINE_OWNED_FIELDS}
    schema['required'] = [k for k in schema.get('required', []) if k not in ENGINE_OWNED_FIELDS]
    # The on-disk schema intentionally has a permissive expression object;
    # generation needs the actual runtime vocabulary plus our compact syntax.
    definitions = schema.get('$defs', {})
    if 'prefabNode' in definitions:
        from srtp.scene_ir_v2.component_contracts import enrich_schema
        enrich_schema(schema)
    if 'derivation' in definitions:
        from srtp.asset_ir_v2.recipe_contracts import enrich_schema
        enrich_schema(schema)
    if 'parameterSource' in definitions:
        from srtp.input_adapter_contract import enrich_schema
        enrich_schema(schema)
    if 'sourceAsset' in definitions:
        source = definitions['sourceAsset']['properties']['source']
        source['required'] = ['uri']
        source['description'] = 'Use an indexed project URI or an advertised runtime asset URI. Hash and byte size are measured by the engine.'
    if 'expression' in definitions:
        from srtp.ir_v2.types import BUILTIN_TYPES
        definitions['typeRef'] = {'anyOf':[{'enum':sorted(BUILTIN_TYPES)},
            {'type':'string','pattern':r'^rule:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'}],
            'description':'Builtin value type or ID declared in types. For topology_site use the type of ONE CELL (e.g. core:int); core:grid does not exist.'}
        from srtp.ir_v2.expression_contracts import expression_schema,outcome_schema
        definitions['expression'] = expression_schema()
        definitions['outcome']['properties']['result'] = outcome_schema()
        if 'command' in definitions:
            properties = definitions['command'].setdefault('properties', {})
            for key in ('target','scope','value','coordinate','entity','at','domain','condition','payload',
                        'delay_ticks','off','on','schedule_id','phase','query'):
                properties[key] = {'$ref':'#/$defs/expression'}
            properties['effects'] = {'type':'array','items':{'$ref':'#/$defs/command'}}
            from srtp.ir_v2.command_contracts import command_schema
            definitions['command'].update(command_schema())
            distribution_fields = {'uniform_int':('minimum','maximum'), 'choice':('values',),
                'bernoulli':('numerator','denominator'), 'weighted_choice':('values','weights'),
                'shuffle':('values',), 'sample':('values','count')}
            definitions['distribution'] = {'oneOf':[
                {'type':'object','required':['kind',*fields], 'properties':dict(kind={'const':kind},
                    **{name:{'$ref':'#/$defs/expression'} for name in fields}), 'additionalProperties':False}
                for kind,fields in distribution_fields.items()]}
    return schema


def expression(text: str):
    if not isinstance(text, str) or len(text) > 16_000:
        raise ValueError('Expression must be a bounded string')
    from srtp.ir_v2.runtime import _core_functions
    functions = _core_functions(None)
    def name(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return name(node.value) + '.' + node.attr
        raise ValueError('Only named pure functions/references are supported')
    def emit(node):
        if isinstance(node, ast.Constant):
            return {'op': 'literal', 'value': node.value}
        if isinstance(node, ast.Name) and node.id in ('true', 'false', 'null'):
            return {'op':'literal', 'value':{'true':True,'false':False,'null':None}[node.id]}
        if isinstance(node, (ast.List, ast.Tuple)):
            return {'op': 'list', 'items': [emit(v) for v in node.elts]}
        if isinstance(node, ast.Attribute):
            path = name(node)
            if path.startswith('param.'):
                return {'op': 'param', 'name': path[6:]}
            if path.startswith('var.'):
                return {'op': 'var', 'name': path[4:]}
            if path.startswith(('flow.', 'state.')):
                return {'op': 'ref', 'path': path}
            raise ValueError('Unknown expression reference: ' + path)
        if isinstance(node, ast.BoolOp):
            return {'op': 'and' if isinstance(node.op, ast.And) else 'or', 'args': [emit(v) for v in node.values]}
        if isinstance(node, ast.BinOp):
            operators = {ast.Add:'add', ast.Sub:'sub', ast.Mult:'mul', ast.FloorDiv:'div', ast.Mod:'mod'}
            if type(node.op) in operators:
                def numeric(child):
                    result=emit(child)
                    # Python source idiom: sum of comparisons counts matches.
                    # Lower explicitly; the typed IR remains strict about bools.
                    if isinstance(child,(ast.Compare,ast.BoolOp)) or (isinstance(child,ast.UnaryOp) and isinstance(child.op,ast.Not)):
                        return {'op':'if','condition':result,'then':{'op':'literal','value':1},'else':{'op':'literal','value':0}}
                    return result
                return {'op':operators[type(node.op)], 'args':[numeric(node.left),numeric(node.right)]}
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub)):
            return {'op':'not' if isinstance(node.op, ast.Not) else 'neg', 'args':[emit(node.operand)]}
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            operators = {ast.Eq:'eq', ast.NotEq:'ne', ast.Lt:'lt', ast.LtE:'lte', ast.Gt:'gt', ast.GtE:'gte'}
            if type(node.ops[0]) in operators:
                return {'op':operators[type(node.ops[0])], 'args':[emit(node.left),emit(node.comparators[0])]}
        if isinstance(node, ast.IfExp):
            return {'op':'if', 'condition':emit(node.test), 'then':emit(node.body), 'else':emit(node.orelse)}
        if isinstance(node, ast.Call) and not node.keywords:
            function = name(node.func)
            args = [emit(v) for v in node.args]
            if function in ('len', 'abs', 'min', 'max', 'coalesce'):
                return {'op': 'count' if function == 'len' else function, 'args': args}
            reference = 'core:' + function
            if reference not in functions:
                raise ValueError('No runtime implementation for function ' + function)
            spec = functions[reference]
            if (not spec.variadic and len(args) != len(spec.argument_types)) or (spec.variadic and len(args) < len(spec.argument_types)):
                raise ValueError('Incorrect argument count for ' + function)
            return {'op':'call', 'function':reference, 'args':args}
        raise ValueError('Unsupported expression syntax: ' + type(node).__name__)
    return emit(ast.parse(text, mode='eval').body)


def lower(value: Any, pointer=''):
    if isinstance(value, Mapping):
        if set(value) == {'expr'}:
            try:
                return expression(value['expr'])
            except (ValueError, SyntaxError) as error:
                raise ValueError('Expression at {0}: {1}; received {2!r}'.format(pointer or '/',error,value['expr'])) from error
        return {k: lower(v, pointer + '/' + str(k).replace('~','~0').replace('/','~1')) for k,v in value.items()}
    if isinstance(value, list):
        return [lower(v, pointer + '/' + str(index)) for index,v in enumerate(value)]
    return deepcopy(value)


def definition_proposal(payload, *, slot, documents, evidence_pack, job_id, design_intent=None, source_root=None, visual_catalog=None):
    definition = payload.get('definition')
    if not isinstance(definition, Mapping) or not definition:
        raise ValueError('Compilation stage requires a nonempty definition object')
    base = documents[slot]
    unknown = set(definition) - set(base) - ENGINE_OWNED_FIELDS
    if unknown:
        raise ValueError('Definition contains unknown fields: ' + str(sorted(unknown)))
    # Never accept model identity, hashes, approval provenance or dependency
    # pins. Rebuild those from engine state, even if a full IR was returned.
    definition = {key:deepcopy(value) for key,value in definition.items() if key not in ENGINE_OWNED_FIELDS}
    if not definition:
        raise ValueError('Compilation stage requires a nonempty semantic definition')
    citations = payload.get('evidence', [])
    catalog = {e['evidence_id']: e for e in evidence_pack.get('evidence', [])}
    expanded = []
    for citation in citations:
        if isinstance(citation, str):
            if citation in catalog:
                item = catalog[citation]
                expanded.append({k: deepcopy(v) for k,v in item.items() if k != 'snippet'})
            else:
                match = re.fullmatch(r'(.+):(\d+)-(\d+)@([0-9a-f]{64})', citation)
                if not match:
                    raise ValueError('Unknown evidence ID: ' + citation + '; use an evidence_pack ID or the source read citation object.')
                # Normalize syntax only. The existing verifier still checks
                # containment, file hash and line range against actual bytes.
                path, start, end, digest = match.groups()
                expanded.append({'evidence_id':citation, 'path':path, 'file_sha256':digest,
                    'span':{'line_start':int(start),'line_end':int(end)}, 'supports':'/' + slot})
        else:
            expanded.append(deepcopy(citation))
    visual_uses = []
    if visual_catalog is not None:
        definition = visual_catalog.resolve(definition, visual_uses)
    if slot == 'rule_ir':
        # Historical model shorthand has an exact meaning. Lower it to the
        # runtime's existing winners expressions instead of silently ignoring it.
        for outcome in definition.get('outcomes',[]):
            result=outcome.get('result',{})
            if 'winner_state' in result:
                if 'winners' in result:
                    raise ValueError('Outcome cannot specify both winner_state and winners')
                state=result.pop('winner_state')
                variables={v['id']:v for v in definition.get('state',base.get('state',{})).get('variables',[])}
                if not isinstance(state,str) or state not in variables or variables[state].get('scope')!='global':
                    raise ValueError('winner_state must name an existing global Rule state')
                result['winners']=[{'op':'call','function':'core:state.get','args':[{'op':'literal','value':state}]}]
    definition = lower(definition)
    if visual_uses:
        metadata = deepcopy(definition.get('metadata', base.get('metadata', {})))
        metadata['source_visual_bindings'] = visual_uses
        definition['metadata'] = metadata
    if slot == 'rule_ir':
        # Validate the complete candidate before the patch applier's fail-fast
        # boundary, so one repair sees ALL independent semantic diagnostics.
        from srtp.ir_v2 import validate_rule_ir
        candidate = dict(deepcopy(base), **definition)
        candidate['content_hash'] = ''
        errors = ['rule_ir invalid at {0}: {1}'.format(item.path, item.message)
                  for item in validate_rule_ir(candidate) if item.severity == 'error']
        from srtp.ir_contracts import errors as shape_errors
        from srtp.ir_v2.expression_contracts import outcome_schema,expression_schema
        outcome_contract=outcome_schema()
        outcome_root={'$defs':{'expression':expression_schema(compact=False)}}
        for index,outcome in enumerate(candidate.get('outcomes',[])):
            errors.extend(shape_errors(outcome.get('result'),outcome_contract,'/outcomes/'+str(index)+'/result',outcome_root))
        # Literal initial values can be type-checked without running gameplay.
        # Report these alongside structural errors instead of spending another
        # model turn discovering them during runtime construction.
        from srtp.ir_v2.types import BUILTIN_TYPES, RuleTypeRegistry, RuleTypeError
        try:
            registry = RuleTypeRegistry(candidate.get('types', []))
        except RuleTypeError:
            registry = None  # Already reported by validate_rule_ir.
        if registry is not None:
            for index, variable in enumerate(candidate.get('state', {}).get('variables', [])):
                if not isinstance(variable, Mapping):
                    continue
                initial = variable.get('initial')
                type_ref = variable.get('type')
                if not isinstance(type_ref, str) or (type_ref not in BUILTIN_TYPES and type_ref not in registry.definitions):
                    continue  # Unknown type already has its own diagnostic.
                if isinstance(initial, Mapping) and initial.get('op') == 'literal':
                    pointer = '/state/variables/{0}/initial'.format(index)
                    try:
                        registry.validate(initial.get('value'), type_ref, pointer)
                    except RuleTypeError as error:
                        errors.append('rule_ir invalid at {0}: {1}'.format(pointer, error))
        if errors:
            raise DefinitionValidationError(errors)
    if slot == 'asset_ir' and source_root is not None:
        from srtp.bundle_assets import asset_root
        from srtp.runtime_assets import resolve_resource
        root = asset_root(source_root)
        for asset in definition.get('assets', []):
            source = asset.get('source')
            if isinstance(source, str):
                source = {'uri':source if source.startswith(('project://','runtime://')) else 'project://' + source}
                asset['source'] = source
            if not isinstance(source, Mapping):
                raise ValueError('Asset needs an explicit original project source')
            path = resolve_resource(root, source['uri'])
            data = path.read_bytes()
            source.update(content_hash=hashlib.sha256(data).hexdigest(),byte_size=len(data))
    if slot != 'rule_ir':
        from srtp.asset_ir_v2 import validate_asset_ir
        from srtp.scene_ir_v2 import validate_scene_ir
        from srtp.input_ir_v2 import validate_input_ir
        validators={'asset_ir':validate_asset_ir,'scene_ir':validate_scene_ir,'input_ir':validate_input_ir}
        candidate=dict(deepcopy(base),**definition); candidate['content_hash']=''
        if slot in ('scene_ir','input_ir'):
            candidate['dependencies']=deepcopy(base['dependencies'])
            for key in (('rule_ir','asset_ir') if slot=='scene_ir' else ('rule_ir',)):
                candidate['dependencies'][key]={'document_id':documents[key]['document_id'],'content_hash':documents[key]['content_hash']}
        errors=['{0} invalid at {1}: {2}'.format(slot,item.path,item.message)
                for item in validators[slot](candidate) if item.severity=='error']
        from srtp.scene_ir_v2.component_contracts import backend_diagnostics as scene_errors
        from srtp.asset_ir_v2.recipe_contracts import backend_diagnostics as asset_errors
        from srtp.input_adapter_contract import backend_diagnostics as input_errors
        try:
            errors.extend({'asset_ir':asset_errors,'scene_ir':scene_errors,'input_ir':input_errors}[slot](candidate))
        except (TypeError,KeyError,AttributeError):
            if not errors: raise
        if errors:
            raise DefinitionValidationError(errors)
    operations = [{'op':'replace' if key in base else 'add', 'path':'/' + key, 'value': value} for key,value in definition.items()]
    if slot in ('scene_ir','input_ir'):
        dependencies = deepcopy(base['dependencies'])
        for key in (('rule_ir','asset_ir') if slot == 'scene_ir' else ('rule_ir',)):
            dependencies[key] = {'document_id':documents[key]['document_id'], 'content_hash':documents[key]['content_hash']}
        operations.append({'op':'replace','path':'/dependencies','value':dependencies})
    pins = {key:{'document_id':d['document_id'],'revision':d['revision'],'content_hash':d['content_hash']} for key,d in documents.items()}
    proposal = {'proposal_version':LLM_PROPOSAL_VERSION, 'proposal_id':'proposal:' + job_id.replace(':','.') + '.' + slot,
                'job_id':job_id, 'stage':'spatial_lift' if design_intent else 'source_rule_semantics',
                'source_package_hash':evidence_pack['source_package_hash'], 'design_intent':design_intent,
                'base_documents':pins, 'patches':{key:[] for key in documents}}
    proposal['patches'][slot] = [{'document_id':base['document_id'], 'base_revision':base['revision'],
        'base_content_hash':base['content_hash'], 'operations':operations, 'evidence':expanded,
        'assumptions':payload.get('assumptions',[]), 'unresolved':payload.get('unresolved',[])}]
    for key in ('claims','tests','extension_proposals','spatial_lift_options','assumptions','unresolved','clarification_questions'):
        proposal[key] = deepcopy(payload.get(key,[]))
    return proposal
