"""Recipes the installed presentation backend can actually execute."""
from copy import deepcopy
from srtp.ir_contracts import obj,array,enum,POSITIVE,POS3,BOOL,ASSET,errors

RECIPES={
    'identity':obj({}),
    'atlas_region':obj({'x':{'type':'integer','minimum':0},'y':{'type':'integer','minimum':0},
        'width':{'type':'integer','minimum':1},'height':{'type':'integer','minimum':1}},('x','y','width','height')),
    'billboard':obj({'facing':enum('camera','axis','fixed'),'size':array(POSITIVE,2),'double_sided':BOOL},('facing','size','double_sided')),
    'extrusion':obj({'depth':POSITIVE,'axis':enum('x','y','z'),'size':array(POSITIVE,2),
        'alpha_cutoff':{'type':'integer','minimum':1,'maximum':255}},('depth','axis')),
    'cube_face_projection':obj({'faces':{'anyOf':[{'const':'all'},array(enum('front','back','left','right','top','bottom'),minItems=1,uniqueItems=True)]},
        'uv_policy':enum('stretch','contain','tile'),'dimensions':POS3},('faces','uv_policy')),
    'procedural_mesh':obj({'primitive':enum('cube','sphere','cylinder','plane'),'dimensions':POS3},('primitive','dimensions')),
}
DESCRIPTOR='application/vnd.cubeengine.presentation+json'


def enrich_schema(schema):
    base=schema['$defs']['derivation']; variants=[]
    for strategy,settings in RECIPES.items():
        item=deepcopy(base); p=item['properties']
        p['strategy']={'const':strategy}; p['settings']=deepcopy(settings)
        p['inputs']=array(ASSET,0 if strategy=='procedural_mesh' else 1)
        p.pop('extension',None)
        if strategy=='atlas_region': p.update(kind={'const':'image'},media_type={'const':'image/png'})
        elif strategy!='identity': p.update(kind={'const':'model'},media_type={'const':DESCRIPTOR})
        variants.append(item)
    schema['$defs']['derivation']={'oneOf':variants}
    return schema


def backend_diagnostics(document):
    result=[]
    for i,item in enumerate(document.get('derivations',[])):
        if not isinstance(item,dict): continue
        strategy=item.get('strategy'); path='/derivations/'+str(i)
        if strategy not in RECIPES:
            result.append(path+'/strategy: not executable by current Ursina backend; supported '+', '.join(RECIPES))
        else:
            result.extend(errors(item.get('settings'),RECIPES[strategy],path+'/settings'))
    return result
