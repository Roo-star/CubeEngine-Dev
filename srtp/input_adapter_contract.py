"""The physical controls emitted by ProjectView, also sent to the compiler."""
import string
from copy import deepcopy
from srtp.ir_contracts import obj,enum,TEXT

KEY_NAMES={'up arrow':'arrow_up','down arrow':'arrow_down','left arrow':'arrow_left','right arrow':'arrow_right',
           'page up':'page_up','page down':'page_down','enter':'enter','escape':'escape','tab':'tab',
           'backspace':'backspace','home':'home','end':'end','delete':'delete','insert':'insert','space':'space'}
KEY_CONTROLS=tuple('keyboard.key.'+name for name in sorted(set(KEY_NAMES.values())|set(string.ascii_lowercase+string.digits)))
MOUSE_CONTROLS=('mouse.button.primary','mouse.button.secondary')


def keyboard_event(key):
    phase='release' if key.endswith(' up') and key not in KEY_NAMES else 'press'
    name=key[:-3] if phase=='release' else key
    control='keyboard.key.'+KEY_NAMES.get(name,name)
    return (control,phase) if control in KEY_CONTROLS else None


def parameter_source_schema():
    return {'oneOf':[obj({'source':{'const':'constant'},'value':{}},('source','value'))]+[
        obj({'source':{'const':source},'value_type':TEXT,**({'key':enum('rule_coordinate','rule_entity_id')} if source=='event_data' else {})},
            ('source','value_type')+(('key',) if source=='event_data' else ()))
        for source in ('intent_value','event_value','event_data')]}


def enrich_schema(schema):
    schema['$defs']['parameterSource']=parameter_source_schema()
    trigger=schema['$defs']['trigger']['oneOf'][0]
    choices=[]
    for device,controls in [('keyboard',KEY_CONTROLS),('mouse',MOUSE_CONTROLS)]:
        item=deepcopy(trigger); p=item['properties']; p.update(device={'const':device},control=enum(*controls),phase=enum('press','release'))
        p['modifiers']={'type':'array','maxItems':0}; choices.append(item)
    # The router understands more trigger types; this host emits only these
    # events. Do not advertise virtual controls the player cannot produce.
    schema['$defs']['trigger']={'oneOf':choices}
    schema['$defs']['target']['oneOf']=[s for s in schema['$defs']['target']['oneOf']
        if s['properties']['kind']['const'] in ('rule_action','host_command')]
    return schema


def backend_diagnostics(document):
    result=[]
    for i,b in enumerate(document.get('bindings',[])):
        t=b.get('trigger',{}); path='/bindings/'+str(i)+'/trigger'
        if t.get('kind')!='control': result.append(path+'/kind: ProjectView currently emits control triggers only')
        if t.get('control') not in KEY_CONTROLS+MOUSE_CONTROLS: result.append(path+'/control: not emitted by ProjectView')
        if t.get('phase') not in ('press','release'): result.append(path+'/phase: ProjectView emits press/release only')
        if t.get('modifiers'): result.append(path+'/modifiers: ProjectView does not emit modifier chords')
    for i,intent in enumerate(document.get('intents',[])):
        target=intent.get('target',{}); path='/intents/'+str(i)+'/target'
        if target.get('kind') not in ('rule_action','host_command'):
            result.append(path+'/kind: use rule_action for gameplay or host_command for quit/restart')
        for key,p in target.get('parameters',{}).items():
            if p.get('source') in ('event_position','event_delta'): result.append(path+'/parameters/'+key+': host does not expose screen coordinates; use event_data.rule_coordinate for grid picking')
            if p.get('source')=='event_data' and p.get('key') not in ('rule_coordinate','rule_entity_id'):
                result.append(path+'/parameters/'+key+'/key: host supplies rule_coordinate or rule_entity_id')
    return result
