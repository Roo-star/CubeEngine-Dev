"""Engine-owned volume projection for spatially lifted rectangular grids.

Layout is derived from Rule coordinates, never a game name or model-authored
layer montage. Source documents are immutable; this lowers the presentation.
"""
from math import prod

VERSION = 'cubeengine.volume-grid/1'
PITCH = 1.0


def matrix_for(coordinate, extents, offset=0):
    position = [(coordinate[i]-(extents[i]-1)/2)*PITCH for i in range(3)]
    position[0] += offset
    return [1,0,0,position[0], 0,1,0,position[1], 0,0,1,position[2], 0,0,0,1]


def project_volume(graph, rule):
    grids = {t['id']:tuple(a['extent'] for a in t['axes']) for t in rule.get('topologies', [])
             if t['kind']=='rect_grid' and len(t.get('axes', []))==3}
    if not grids:
        return
    maps = {}
    offset = 0
    for key, sites in graph.scene.topology_sites.items():
        if not sites:
            continue
        sample = graph.nodes[next(iter(sites.values()))]
        topology = sample.get('rule_context', {}).get('rule_topology')
        if topology not in grids:
            continue
        extents = grids[topology]
        if len(sites) != prod(extents):
            raise ValueError('Volume layout requires one Scene instance per Rule grid cell')
        maps[topology] = (extents, offset)
        for coordinate, node_id in sites.items():
            node = graph.nodes[node_id]
            desired = matrix_for(coordinate, extents, offset)
            if node.get('local_matrix') != desired or node.get('parent') is not None:
                node['parent'] = None
                node['local_matrix'] = desired
                graph.dirty_nodes.add(node_id)
                graph.change_serial += 1
            node['volume_cell'] = True
            # Colliders describe grid cells, independent of source tile depth.
            for component in node['components'].values():
                if component['type']=='collider':
                    component['properties'] = dict(component['properties'], shape='box', size=[1,1,1])
                    component['properties'].pop('radius', None)
        offset += (extents[0]+2)*PITCH
    if set(grids)-set(maps):
        raise ValueError('Spatial Lift volume is missing a topology_visualizer for: '+', '.join(sorted(set(grids)-set(maps))))
    # Entity visualizers must share the board's physical coordinate system.
    entity_types = {e['id']:e for e in rule.get('state', {}).get('entity_types', [])}
    for descriptor in graph.scene.entity_visualizers.values():
        entity_type = entity_types.get(descriptor['entity_type'], {})
        topology = entity_type.get('topology')
        if topology not in maps and len(maps)==1:
            topology = next(iter(maps))
        if topology not in maps:
            raise ValueError('Volume entity visualizer needs an unambiguous grid topology')
        # Dynamic instances carry entity IDs; coordinate comes from state in
        # ScenePresentation.synchronize, not from stale authored transforms.
        graph.volume_entity_maps[descriptor['entity_type']] = maps[topology]
    graph.volume_layout = {'version':VERSION, 'grids':{k:list(v[0]) for k,v in maps.items()}, 'pitch':PITCH}
    for node_id, node in graph.nodes.items():
        if node.get('rule_context', {}).get('coordinate') or node.get('rule_context', {}).get('entity_id'):
            continue
        # Static source planes/labels are not the 3D playfield. HUD, lights,
        # audio and all rule-bound prefab content remain in their own roles.
        for component in node['components'].values():
            role=component['properties'].get('spatial_role')
            omit=component['type']=='camera' or (component['type']=='renderer' and
                (role=='source_backdrop' or (role is None and graph.legacy_appearance)))
            if omit and component['enabled']:
                component['enabled'] = False
                graph.dirty_nodes.add(node_id)
                graph.change_serial += 1


def validate_volume(graph):
    if not graph.volume_layout:
        return []
    count = 0
    for sites in graph.scene.topology_sites.values():
        roots = [graph.nodes[n] for n in sites.values() if graph.nodes[n].get('volume_cell')]
        for node in roots:
            matrix = node['local_matrix']
            if any(matrix[i] != value for i,value in enumerate([1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]) if i not in (3,7,11)):
                raise ValueError('Volume cells cannot be sheared, flattened or parent-transformed')
            if node['parent'] is not None:
                raise ValueError('Volume cell must be positioned in shared world space')
        positions = {(n['local_matrix'][3],n['local_matrix'][7],n['local_matrix'][11]) for n in roots}
        if len(positions)!=len(roots):
            raise ValueError('Volume cells overlap at the same coordinate')
        count += len(roots)
    return [{'volume_layout':VERSION,'cells':count,'orthogonal_world_axes':True}]
