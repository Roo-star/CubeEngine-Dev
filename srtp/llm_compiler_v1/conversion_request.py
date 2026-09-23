"""Typed Inspector requests share the natural-language conversion pipeline."""
import uuid
from .contracts import DESIGN_INTENT_VERSION


def inspector_intent(dimensions, *, project_id, source_manifest_hash):
    if not isinstance(dimensions, dict) or not dimensions:
        raise ValueError('Inspector conversion needs target dimensions')
    changes = []
    for axis, extent in dimensions.items():
        if axis not in ('x','y','z') or type(extent) is not int or not 1 <= extent <= 256:
            raise ValueError('Target dimensions require x/y/z integer extents within 1..256')
        changes.append({'kind':'set_extent','axis':axis,'value':extent})
    token = uuid.uuid4().hex[:12]
    return {'intent_version':DESIGN_INTENT_VERSION,'intent_id':'intent:' + token,
        'conversation_id':'conversation:' + token,'turn_id':'turn:' + token,
        'project_id':project_id,'source_manifest_hash':source_manifest_hash,
        'original_text':'Inspector target: ' + ', '.join(k+'='+str(v) for k,v in dimensions.items()),
        'language':'en','operation':'transform','scope':['rule','scene','asset','input'],
        'preserve':['source visual identity','unaffected source rules','game lifecycle','score and outcome feedback'],
        'changes':changes,'constraints':['Use one authoritative Rule state for input, visuals and AI'],
        'resolved_references':[],'assumptions':[],'conflicts':[],'unresolved':[],
        'requires_confirmation':False,'status':'proposed','target_base':None}
