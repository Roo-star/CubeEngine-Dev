"""Optional host-owned slice visibility for spatial boards."""

VERSION = 'cubeengine.depth-view/2'


def contract():
    return {
        'version': VERSION,
        'default': 'The existing depth focus changes picking only and keeps every layer visible.',
        'slice': 'The player can optionally show only the selected depth. Other cell artwork, descendants and colliders are hidden without changing Rule state, materials or world positions. Returning to overview restores all layers.',
        'controls': {'next_depth': '[ or ] or Previous/Next', 'toggle_slice': 'Z or Only this layer',
                     'overview': 'V or All layers'},
        'authoring': 'Do not invent Rule actions or Input bindings for these host controls. Keep source artwork and ordinary selectable cell colliders; the player supplies interior access.'}


def visible_in_depth(graph, node_id, selected_layer):
    """Keep non-cell HUD/controls; hide a cell and its descendants off slice."""
    if selected_layer is None or not graph.volume_layout:
        return True
    from .input_pointer_contract import scene_parent
    node = graph.nodes.get(node_id)
    while node:
        coordinate = node.get('rule_context', {}).get('coordinate')
        if coordinate is not None and len(coordinate) > 2:
            return coordinate[2] == selected_layer
        node = graph.nodes.get(scene_parent(node))
    return True
