"""Exercise transient input against each replayed Rule state before publication."""
from srtp.scene_ir_v2.binding_expressions import validate_expression


def verify_transient_presentation(scene, graph, projection, state):
    reads=[]
    for binding in scene.bindings:
        if binding.source['kind']=='interaction':reads.append(binding.source)
        elif binding.source['kind']=='expression':
            _,leaves=validate_expression(binding.source['expression'],lambda *args:[])
            reads.extend(leaf for leaf in leaves if leaf['kind']=='interaction')
    if not reads:return {'input_states':0,'targets':0,'target_sampling':False}
    # Probe actual authored targets/contexts, not names of reference games.
    contexts=[]
    for binding in scene.bindings:
        source=binding.source
        if source['kind'] not in ('interaction','expression'):continue
        targets=list(projection._binding_targets(binding))
        for context in targets:
            if context not in contexts:contexts.append(context)
    sampled=len(contexts)>32
    if sampled:
        # Evenly sample expanded contexts, including both endpoints. This is
        # explicitly reported as sampling, not exhaustive state coverage.
        indexes={round(i*(len(contexts)-1)/31) for i in range(32)}
        contexts=[c for i,c in enumerate(contexts) if i in indexes]
    before=state.state_hash();frames=0
    dirty=set(graph.dirty_nodes)
    try:
        for context in contexts:
            for control in ('mouse.button.primary','mouse.button.secondary'):
                for interaction in ({'hovered':context,'pressed':{control:context}},{}):
                    graph.dirty_nodes.clear()
                    graph.synchronize(projection,state,interaction)
                    dirty.update(graph.dirty_nodes)
                    # The caller has already checked the full static graph and
                    # every variant at this Rule state. Inspect changed nodes
                    # for each transient frame; do not re-enumerate every
                    # unchanged cell/variant for every possible click.
                    errors=graph.diagnostics(graph.dirty_nodes,include_variants=False)
                    if errors:raise ValueError('Transient presentation at '+context['node_id']+': '+'; '.join(errors[:12]))
                    frames+=1
    finally:
        graph.dirty_nodes.update(dirty)
    if state.state_hash()!=before:raise ValueError('Transient presentation mutated Rule state')
    return {'input_states':frames,'targets':len(contexts),'target_sampling':sampled}
