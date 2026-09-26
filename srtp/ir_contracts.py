"""Small declarative contracts shared by generation and backend validation.

This checks the JSON Schema subset used below. It is not a replacement for
cross-document IR type/reference checks or executable acceptance tests.
"""
import math
import re
import json
from pathlib import Path
from functools import lru_cache
from copy import deepcopy
from collections.abc import Mapping


def obj(properties, required=(), extra=False, **kw):
    return dict(type='object', properties=properties, required=list(required), additionalProperties=extra, **kw)


def array(items, size=None, **kw):
    return dict(type='array', items=items, **({'minItems':size,'maxItems':size} if size is not None else {}), **kw)


def enum(*values):
    return {'enum':list(values)}


NUMBER={'type':'number'}
BOOL={'type':'boolean'}
TEXT={'type':'string'}
POSITIVE={'type':'number','exclusiveMinimum':0}
NORMAL={'type':'number','minimum':0,'maximum':1}
VEC3=array(NUMBER,3)
POS3=array(POSITIVE,3)
COLOR={'anyOf':[array(NORMAL,3),array(NORMAL,4)]}
ASSET={'type':'string','pattern':r'^asset:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'}
RULE={'type':'string','pattern':r'^rule:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'}
SCENE={'type':'string','pattern':r'^scene:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'}
LOCAL={'type':'string','pattern':r'^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'}

IR_SCHEMA_FILES = {
    'rule_ir':'ir_v2/rule-ir-v2.schema.json', 'asset_ir':'asset_ir_v2/asset-ir-v2.schema.json',
    'scene_ir':'scene_ir_v2/scene-ir-v2.schema.json', 'input_ir':'input_ir_v2/input-ir-v2.schema.json',
}


@lru_cache(maxsize=4)
def _document_schema(slot):
    return json.loads((Path(__file__).parent / IR_SCHEMA_FILES[slot]).read_text(encoding='utf-8'))


def document_shape_errors(slot, document, *, authoring=False):
    """Guard typed model data before domain validators index/hash its fields.

    The stored wire schema stays the source of truth. Authoring may omit only
    asset size/hash, which the builder measures from the original bytes.
    """
    schema = _document_schema(slot)
    if authoring and slot in ('asset_ir', 'rule_ir'):
        schema = deepcopy(schema)
        if slot == 'asset_ir':
            source = schema['$defs']['sourceAsset']['properties']['source']
            source['required'] = ['uri']
            for field in ('content_hash','byte_size'): source['properties'][field] = {}
        else:
            # Compact expressions have not been lowered yet at this boundary.
            expression = schema['$defs']['expression']
            schema['$defs']['expression'] = {'anyOf':[expression, obj({'expr':TEXT},('expr',))]}
    return errors(document, schema)


def errors(value, schema, path='', root=None):
    """Return every independent structural error with an actionable pointer."""
    root = root or schema
    if '$ref' in schema:
        target=root
        for part in schema['$ref'][2:].split('/'):
            target=target[part]
        return errors(value,target,path,root)
    result=[]
    for kind in ('oneOf','anyOf'):
        if kind not in schema:
            continue
        choices=schema[kind]
        # Select discriminated variants so callers see missing fields, not
        # dozens of irrelevant errors from the other component kinds.
        if isinstance(value,Mapping):
            matched=[s for s in choices if any('const' in p and value.get(k)==p['const']
                for k,p in s.get('properties',{}).items())]
            if len(matched)==1:
                return errors(value,matched[0],path,root)
        attempts=[errors(value,s,path,root) for s in choices]
        if any(not e for e in attempts):
            return []
        return min(attempts,key=len) if attempts else [path+': no allowed schema alternative']
    if 'const' in schema and value != schema['const']:
        return [path+': expected '+repr(schema['const'])]
    if 'enum' in schema and value not in schema['enum']:
        return [path+': allowed values '+repr(schema['enum'])]
    kind=schema.get('type')
    correct={'object':isinstance(value,Mapping),'array':isinstance(value,(list,tuple)),
        'string':isinstance(value,str),'boolean':type(value) is bool,
        'integer':type(value) is int,'number':type(value) in (float,int) and math.isfinite(value),
        'null':value is None}
    if kind and not (any(correct[k] for k in kind) if isinstance(kind,list) else correct[kind]):
        return [path+': expected '+str(kind)]
    if isinstance(value,Mapping):
        if len(value)<schema.get('minProperties',0) or len(value)>schema.get('maxProperties',float('inf')):
            result.append(path+': incorrect object property count')
        properties=schema.get('properties',{})
        for key in schema.get('required',[]):
            if key not in value: result.append(path+'/'+key+': required')
        for key,child in value.items():
            rule=properties.get(key,schema.get('additionalProperties',True))
            if rule is False: result.append(path+'/'+str(key)+': unsupported field; allowed '+', '.join(properties))
            elif isinstance(rule,dict): result.extend(errors(child,rule,path+'/'+str(key),root))
    elif isinstance(value,(list,tuple)):
        if len(value)<schema.get('minItems',0) or len(value)>schema.get('maxItems',float('inf')):
            result.append(path+': incorrect array length')
        if schema.get('uniqueItems') and any(item in value[:i] for i,item in enumerate(value)):
            result.append(path+': items must be unique')
        for i,child in enumerate(value): result.extend(errors(child,schema.get('items',{}),path+'/'+str(i),root))
    elif isinstance(value,str):
        if len(value)<schema.get('minLength',0): result.append(path+': string too short')
        if len(value)>schema.get('maxLength',float('inf')): result.append(path+': string too long')
        if 'pattern' in schema and not re.search(schema['pattern'],value): result.append(path+': must match '+schema['pattern'])
    elif type(value) in (int,float):
        if not math.isfinite(value): result.append(path+': number must be finite')
        for key,failed in [('minimum',value<schema.get('minimum',-float('inf'))),
                           ('maximum',value>schema.get('maximum',float('inf'))),
                           ('exclusiveMinimum',value<=schema.get('exclusiveMinimum',-float('inf'))),
                           ('exclusiveMaximum',value>=schema.get('exclusiveMaximum',float('inf')))]:
            if failed: result.append(path+': violates '+key+' '+str(schema[key]))
    return result
