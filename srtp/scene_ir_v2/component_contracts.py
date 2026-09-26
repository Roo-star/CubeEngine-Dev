"""Ursina executable component and projection contracts (no game-specific IDs)."""
from copy import deepcopy
from srtp.ir_contracts import obj,array,enum,NUMBER,BOOL,TEXT,POSITIVE,NORMAL,VEC3,POS3,COLOR,ASSET,RULE,SCENE,LOCAL,errors
from .binding_expressions import schema as expression_schema, validate_expression

BUILTIN_GEOMETRY=('cube','sphere','cylinder','plane','quad')
COLLIDERS=('box','sphere')
LIGHTS=('ambient','directional','point')
BINDABLE={
    'renderer':('visible','geometry','material','texture','color','opacity','variant','text','text_color','scale','pressed'),
    'camera':('active','fov','orthographic_size','near_clip','far_clip'),
    'light':('color','intensity'), 'collider':('selectable','is_trigger'),
    'ui_canvas':('visible','text','color','scale','background'),
    'audio_source':('playing','volume','trigger','clip','loop'), 'authoring_marker':(),
}
GEOMETRY={'anyOf':[enum(*('builtin:'+v for v in BUILTIN_GEOMETRY)),ASSET]}
MARKER={'anyOf':[{'type':'null'},obj({'kind':enum('cross','ring','sphere'),'color':COLOR,
    'size':{'type':'number','exclusiveMinimum':0,'maximum':10},'billboard':BOOL,
    'placement':enum('cell_center','surface')},('kind',),
    description='cell_center size is in logical node/cell units, independent of the carrier renderer scale. surface markers inherit renderer scale.') ]}
ANIMATION=obj({'frames':array(ASSET,minItems=1),'fps':{'type':'number','exclusiveMinimum':0,'maximum':240},
    'loop':BOOL,'playing':BOOL},('frames','fps'))
RENDER_FIELDS={'geometry':GEOMETRY,'visible':BOOL,'color':COLOR,'opacity':NORMAL,'material':ASSET,'texture':ASSET,
    'font':ASSET,'scale':POS3,'text':TEXT,'text_color':COLOR,'text_scale':POSITIVE,'text_billboard':BOOL,
    'marker':MARKER,'animation':ANIMATION,'variant':TEXT,
    'spatial_role':enum('cell_shell','content','source_backdrop','world_decoration'),'shell_color':COLOR}
PRESS_STYLE=obj(deepcopy(RENDER_FIELDS),description='Visual overrides while a valid pointer press is held; release/cancel restores the current Rule variant.')
RENDER_FIELDS.update(pressed=BOOL,pressed_style=PRESS_STYLE)
RENDERER=obj(dict(RENDER_FIELDS,variants={'type':'object','additionalProperties':obj(RENDER_FIELDS)}),('geometry','visible'))
COLLIDER={'oneOf':[
    obj({'shape':{'const':'box'},'size':POS3,'is_trigger':BOOL,'selectable':BOOL},('shape','size','is_trigger','selectable')),
    obj({'shape':{'const':'sphere'},'radius':POSITIVE,'is_trigger':BOOL,'selectable':BOOL},('shape','radius','is_trigger','selectable'))]}
CAMERA={'description':'Ursina coordinates: +X right, +Y up, camera looks along local +Z at zero rotation. A camera at negative Z with rotation [0,0,0] looks toward the origin; at positive Z use yaw 180. Frame all playable geometry. orthographic_size is viewport height in world units.', 'oneOf':[
    obj({'projection':{'const':kind},'near_clip':POSITIVE,'far_clip':POSITIVE,'active':BOOL,
         parameter:({'type':'number','minimum':1,'exclusiveMaximum':180} if kind=='perspective' else {'type':'number','minimum':1})},
        ('projection','near_clip','far_clip','active',parameter))
    for kind,parameter in [('perspective','fov'),('orthographic','orthographic_size')]]}
MATRIX=dict(array(NUMBER,16),description='16 row-major affine numbers; last row must be [0,0,0,1]. Applies to both prefab geometry and site positions: off-diagonal terms shear/rotate cells too. Use orthogonal axes for a regular 3D volume. Not an origin/axes object.')
COMPONENTS={
    'renderer':RENDERER,'collider':COLLIDER,'camera':CAMERA,
    'light':obj({'kind':enum(*LIGHTS),'color':COLOR,'intensity':{'type':'number','minimum':0}},('kind','color','intensity')),
    'topology_visualizer':obj({'rule_topology':RULE,'prefab':SCENE,'index_to_world':MATRIX},('rule_topology','prefab','index_to_world')),
    'rule_entity_visualizer':obj({'rule_entity_type':RULE,'prefab':SCENE,'index_to_world':MATRIX,'coordinate_component':LOCAL},('rule_entity_type','prefab','index_to_world')),
    'ui_canvas':obj({'mode':enum('overlay','world'),'visible':BOOL,'text':TEXT,'font':ASSET,'color':COLOR,
        'position':{'anyOf':[array(NUMBER,2),VEC3]},'origin':array(NUMBER,2),'scale':POSITIVE,
        'background':COLOR,'size':array(POSITIVE,2)},('mode',),
        description='Overlay uses Ursina normalized coordinates (viewport height 1), not source pixels. Text scale is a multiplier of Ursina default text height (about 0.025), NOT the desired normalized height: desired pixel height H at viewport V uses scale=H/(V*0.025). size is a two-number background rectangle.'),
    'audio_source':obj({'clip':ASSET,'volume':NORMAL,'loop':BOOL,'playing':BOOL,'trigger':{'type':'integer','minimum':0}},('clip',)),
    'authoring_marker':obj({},extra=True,description='Editor-only metadata; does not render in the game.'),
}

BINDING_SOURCE={'oneOf':[
    obj({'kind':{'const':'state'},'scope':enum('global','participant','topology_site','entity'),'variable':RULE,'participant':RULE},('kind','scope','variable')),
    obj({'kind':{'const':'flow'},'property':enum('current_actor','phase','tick','turn')},('kind','property')),
    obj({'kind':{'const':'entity_component'},'component':LOCAL},('kind','component')),
    obj({'kind':{'const':'interaction'},'property':enum('pressed','hovered'),
         'scope':enum('target','any'),'control':enum('mouse.button.primary','mouse.button.secondary'),
         'node':SCENE},('kind','property','scope')),
    obj({'kind':{'const':'expression'},'expression':expression_schema()},('kind','expression'))]}


def binding_source_errors(value, path):
    result=errors(value,BINDING_SOURCE,path)
    if isinstance(value,dict) and value.get('kind')=='expression':
        issues,_=validate_expression(value.get('expression'),
            lambda source,p:errors(source,BINDING_SOURCE,p),path+'/expression')
        result.extend(issues)
    return result
BINDING_TARGET={'oneOf':[
    obj({'selector':{'const':selector},'node':SCENE,'property':enum('active') if not component else enum(*sorted(set(v for values in BINDABLE.values() for v in values))),
         **({'component':LOCAL} if component else {}), **({'visualizer':LOCAL} if selector!='node' else {})},
        ('selector','node','property')+(('component',) if component else ())+(('visualizer',) if selector!='node' else ()))
    for selector in ('node','topology_sites','entity_nodes') for component in (False,True)]}
BINDING_TRANSFORM={'oneOf':[
    obj({'kind':{'const':'direct'}},('kind',)),obj({'kind':{'const':'not'}},('kind',)),
    obj({'kind':{'const':'map'},'cases':array(obj({'equals':{},'value':{}},('equals','value'))),'default':{}},('kind','cases')),
    obj({'kind':{'const':'numeric'},'multiply':NUMBER,'add':NUMBER},('kind',)),
    obj({'kind':{'const':'digit'},'place':{'type':'integer','minimum':0,'maximum':9},
         'minimum':{'type':'integer','minimum':0},'maximum':{'type':'integer','minimum':0},
         'values':array({},10)},('kind','place'),
        description='Floor, clamp then extract decimal digit at place 0=units, 1=tens, 2=hundreds. Optional values is exactly ten atlas texture IDs or values indexed by that digit.'),
    obj({'kind':{'const':'integer_format'},'width':{'type':'integer','minimum':1,'maximum':16},
         'minimum':{'type':'integer'},'maximum':{'type':'integer'}},('kind','width'),
        description='Floor, clamp, then zero-pad an integer for text. For source sprite digits use digit with original atlas IDs instead.'),
    obj({'kind':{'const':'format'},'template':dict(TEXT,description='Exactly one {value} token.')},('kind','template'))]}


def component_schema():
    return {'oneOf':[obj({'id':LOCAL,'type':{'const':kind},'enabled':BOOL,'properties':deepcopy(schema)},
        ('id','type','enabled','properties')) for kind,schema in COMPONENTS.items()]}


def enrich_schema(schema):
    schema['$defs']['component']=component_schema()
    props=schema['$defs']['binding']['properties']
    props.update(source=deepcopy(BINDING_SOURCE),target=deepcopy(BINDING_TARGET),transform=deepcopy(BINDING_TRANSFORM))
    def allow_measured_color(value):
        if isinstance(value,dict):
            if value==COLOR:
                value['anyOf'].append(obj({'source_visual':TEXT},('source_visual',)))
                return
            for child in value.values(): allow_measured_color(child)
        elif isinstance(value,list):
            for child in value: allow_measured_color(child)
    allow_measured_color(schema['$defs']['component'])
    return schema


def components(document):
    def visit(node,path):
        if not isinstance(node,dict): return
        for index,c in enumerate(node.get('components',[])):
            if isinstance(c,dict): yield path+'/components/'+str(index),c
        for index,child in enumerate(node.get('children',[])):
            yield from visit(child,path+'/children/'+str(index))
    for i,p in enumerate(document.get('prefabs',[])):
        if isinstance(p,dict): yield from visit(p.get('root',{}),'/prefabs/'+str(i)+'/root')
    for i,n in enumerate(document.get('nodes',[])):
        yield from visit(n,'/nodes/'+str(i))


def backend_diagnostics(document):
    result=[]
    nodes={n.get('id'):n for n in document.get('nodes',[]) if isinstance(n,dict) and isinstance(n.get('id'),str)}
    prefabs={p.get('id'):p.get('root',{}) for p in document.get('prefabs',[]) if isinstance(p,dict) and isinstance(p.get('id'),str)}
    for path,component in components(document):
        kind=component.get('type')
        if kind not in COMPONENTS:
            result.append(path+'/type: unsupported Ursina component '+str(kind))
        else:
            result.extend(errors(component.get('properties'),COMPONENTS[kind],path+'/properties'))
    for i,binding in enumerate(document.get('bindings',[])):
        if not isinstance(binding,dict): continue
        for key,schema in [('source',BINDING_SOURCE),('target',BINDING_TARGET),('transform',BINDING_TRANSFORM)]:
            path='/bindings/'+str(i)+'/'+key
            result.extend(binding_source_errors(binding.get(key),path) if key=='source' else errors(binding.get(key),schema,path))
        target=binding.get('target',{})
        if not isinstance(target,dict): continue
        if not target.get('component') and target.get('property')!='active':
            result.append('/bindings/'+str(i)+'/target/property: only active is supported for node property bindings by this backend')
        node=nodes.get(target.get('node')) if isinstance(target.get('node'),str) else None
        if node and target.get('selector')!='node':
            visualizer=next((c for c in node.get('components',[]) if isinstance(c,dict) and c.get('id')==target.get('visualizer')),None)
            node=prefabs.get(visualizer.get('properties',{}).get('prefab')) if visualizer else None
        elif node and isinstance(node.get('prefab'),str): node=prefabs.get(node['prefab'])
        if node and target.get('component'):
            component=next((c for c in node.get('components',[]) if isinstance(c,dict) and c.get('id')==target['component']),None)
            if component and target.get('property') not in BINDABLE.get(component.get('type'),()):
                result.append('/bindings/'+str(i)+'/target/property: not implemented for '+str(component.get('type'))+' by Ursina backend')
    return result
