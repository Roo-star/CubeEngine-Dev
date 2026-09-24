"""Shared host picking data and declarative pointer filters; no game IDs."""
from srtp.ir_contracts import obj, BOOL, errors
from collections.abc import Mapping

POINTER_FILTER = obj({
    'node': {'type':'string','pattern':r'^scene:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'},
    'subtree': BOOL,
    'topology': {'type':'string','pattern':r'^rule:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'},
    'gesture': {'const':'click'},
}, minProperties=1)


def pointer_filter_errors(value, path):
    result=errors(value,POINTER_FILTER,path)
    if isinstance(value,dict) and not value:result.append(path+': requires at least one pointer condition')
    if isinstance(value,dict) and 'subtree' in value and 'node' not in value:
        result.append(path+'/subtree: requires node')
    return result


def pointer_data(context, *, click=False):
    data={'pointer_click':bool(click)}
    for source,target in (('node_id','scene_node_id'),('rule_topology','rule_topology'),
                          ('coordinate','rule_coordinate'),('entity_id','rule_entity_id'),('node_path','scene_node_path')):
        if source in context:
            data[target]=list(context[source]) if source=='coordinate' else context[source]
    return data


def scene_parent(node):
    """Semantic ancestry survives world-space reparenting by volume layout."""
    return node.get('logical_parent',node.get('parent')) if isinstance(node,Mapping) else node.parent


def scene_pick_context(nodes,node_id,context=None):
    """Scene parent links, not naming conventions, establish subtree membership."""
    result=dict(context or {},node_id=node_id);path=[];current=node_id
    while current and current not in path:
        path.append(current);node=nodes.get(current)
        if node is None:break
        current=scene_parent(node)
    result['node_path']=path
    return result


def pointer_matches(condition,data):
    if condition.get('gesture')=='click' and data.get('pointer_click') is not True:return False
    if 'topology' in condition and data.get('rule_topology')!=condition['topology']:return False
    if 'node' in condition:
        node=data.get('scene_node_id','');wanted=condition['node']
        path=data.get('scene_node_path',())
        if not isinstance(node,str) or not (node==wanted or condition.get('subtree',False) and isinstance(path,(list,tuple)) and wanted in path):
            return False
    return True


def pointer_filters_disjoint(first,second):
    if first.get('topology') and second.get('topology') and first['topology']!=second['topology']:return True
    a,b=first.get('node'),second.get('node')
    if not a or not b or a==b:return False
    # Input alone does not own the Scene tree. Conservatively report overlap
    # when either subtree could contain the other declared node.
    return not (first.get('subtree') or second.get('subtree'))
