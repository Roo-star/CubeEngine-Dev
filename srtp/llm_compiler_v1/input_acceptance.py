"""Exercise host-shaped events and actual Scene picking metadata before publish."""
import json

from srtp.input_ir_v2 import PhysicalInputEvent, InputDispatchError
from srtp.ir_v2 import compile_rule_ir
from srtp.scene_presentation import ScenePresentation
from srtp.session_random import session_sources
from .behavior_runtime import test_runtime
from srtp.input_pointer_contract import pointer_data,scene_pick_context,scene_parent


def _pick_data(graph):
    for node_id,node in graph.nodes.items():
        current=node
        while current:
            if not current.get('active',True) or not graph.layers.get(current['layer'],{}).get('visible',True):
                break
            current=graph.nodes.get(scene_parent(current))
        else:
            collider=next((c for c in node['components'].values() if c['type']=='collider' and c['enabled']),None)
            if graph.layers.get(node['layer'],{}).get('pickable',True) and collider and collider['properties']['selectable']:
                context=scene_pick_context(graph.nodes,node_id,node.get('rule_context',{}))
                yield pointer_data(context,click=True)


def verify_host_routes(compiled,rule,scene,assets,tests=(),*,spatial=False):
    pending={intent.id:(intent,None) for intent in compiled.intents if intent.required}
    # A keyboard alternative must not hide an unreachable authored scene button.
    for binding in compiled.bindings:
        pointer=binding.trigger.get('pointer')
        if binding.enabled and pointer:
            node=pointer.get('node')
            if node and node not in scene.nodes_by_id:
                raise ValueError('Pointer binding references unknown Scene node: '+node)
            pending[binding.id]=(compiled.intents_by_id[binding.intent_id],binding.id)
    checked=[]; failures={}
    # Pickable Scene instances may appear only after a transition. Replay saved
    # behavior states rather than inventing picked coordinates/entity IDs.
    for case in tests or [{'steps':[]}]:
        runtime=test_runtime(rule,case)
        graph=ScenePresentation(scene,assets,volume_rule=rule if spatial else None); projection=scene.create_projection_session()
        try:
            for step in [None]+case.get('steps',[]):
                if step:
                    if 'advance_ns' in step: runtime.advance_time_ns(step['advance_ns'])
                    elif step.get('accepted'):
                        candidates=[a for a in runtime.all_actions() if a.action_id==step['action'] and
                            json.dumps(dict(a.parameters),sort_keys=True)==json.dumps(step.get('parameters',{}),sort_keys=True)]
                        if len(candidates)!=1: raise ValueError('Input replay action unavailable')
                        runtime.apply_action(candidates[0])
                graph.synchronize(projection,runtime.state)
                mouse_data=None
                for obligation,(intent,required_binding) in list(pending.items()):
                    intent_id=intent.id
                    matched=False
                    for binding in compiled.bindings:
                        if binding.intent_id!=intent_id or not binding.enabled: continue
                        if required_binding and binding.id!=required_binding:continue
                        context=compiled.contexts_by_id[binding.context_id]
                        if not context.enabled_by_default or context.focus not in ('global','viewport'): continue
                        trigger=binding.trigger
                        if trigger.get('kind')!='control': continue
                        if trigger['device']=='mouse':
                            if mouse_data is None: mouse_data=list(_pick_data(graph))
                            events=mouse_data
                        else: events=[{}]
                        for data in events:
                            # Same default value (1), screen position and data as
                            # ProjectHost; never synthesize an analog or key pick.
                            event=PhysicalInputEvent(1,trigger['device'],trigger['control'],trigger['phase'],
                                position=(0,0) if trigger['device']=='mouse' else None,data=data)
                            try:
                                dispatched=compiled.create_router().dispatch(event,focus='viewport',rule_runtime=runtime)
                                for resolved in dispatched.intents:
                                    if required_binding and resolved.binding_id!=required_binding:continue
                                    if resolved.intent_id==intent_id and resolved.target_kind=='host_command':
                                        matched=True
                                    if resolved.intent_id==intent_id and resolved.rule_action_request:
                                        action=resolved.rule_action_request.resolve(runtime)
                                        if runtime.is_legal(action): matched=True
                                        else: failures[obligation]='Route resolves to a Rule action that is illegal in this state'
                            except InputDispatchError as exc: failures[obligation]=str(exc)
                            if matched: break
                        if matched: break
                    if matched:
                        checked.append(obligation); del pending[obligation]
                if not pending: return checked
        finally: runtime.close()
    raise ValueError('Required intents not reachable through Workbench events and Scene picking in behavior traces: '+
        '; '.join(key+(' ('+failures[key]+')' if key in failures else '') for key in pending))
