"""Reusable presentation methods extracted from the accepted native references.

No game routing or rules. Explicit roles distinguish a transparent container
from the artwork it contains, so lifting cannot erase source visual content.
"""
from copy import deepcopy

PATTERN_VERSION = 'cubeengine.presentation-patterns/1'
SPATIAL_ROLES = ('cell_shell', 'content', 'source_backdrop', 'world_decoration')


def model_contract():
    return {
        'version': PATTERN_VERSION,
        'roles': {
            'cell_shell': 'Transparent selectable grid container. Use separate content renderers for opaque tiles, atlas faces, sprites and meshes.',
            'content': 'Preserve authored geometry, texture, material, animation, opacity and scale. Never replace source artwork with a generic cube.',
            'source_backdrop': 'A 2D background/frame intentionally omitted from the spatial viewport; keep the source scene intact.',
            'world_decoration': 'Intentional 3D world decoration, preserved by volume lowering.'},
        'glass_with_solid_piece': 'Compact unit pitch, 0.9 cell width; low-alpha glass shell, opaque centered 3D marker. Occupancy tints the shell. Do not billboard or move a solid piece to the cell surface.',
        'source_surface': 'Reference original asset/atlas region and preserve every state-to-surface mapping. Use cube_face_projection for source tile faces; do not infer atlas indices from a different game.',
        'numbered_tile': 'Preserve source value->background/text/font mappings; fit large values without clipping. Tile content stays opaque when grid containers are transparent.',
        'layer_focus': 'Focus changes picking/emphasis only. Off-layer pieces, revealed values, flags and result state must remain visible.',
        'quality_dimensions': ['source_behavior_equivalence', 'spatial_rules', 'visual_state_coverage',
            'source_asset_fidelity', 'camera_and_internal_picking', 'lifecycle_and_feedback',
            'gesture_conflicts', 'performance', 'fresh_pipeline_provenance'],
        'not_a_success_claim': 'Native reference feature parity requires independent behavior and rendered-frame evidence, not only successful JSON/IR compilation.'}


def lower_cell_renderer(properties, *, legacy=False):
    result = deepcopy(properties)
    role = result.get('spatial_role')
    # Explicit content always wins, including textured/source-mesh content on
    # a topology root. Legacy adaptation applies only to a bare builtin cube.
    legacy_shell = legacy and role is None and result.get('geometry')=='builtin:cube' and not result.get('texture')
    if role != 'cell_shell' and not legacy_shell:
        return result
    result['volume_cell'] = True
    result['mesh'] = {'primitive':'cube','dimensions':[1,1,1]}
    result['scale'] = [.9,.9,.9]
    marker = result.get('marker')
    if marker:
        result['color'] = list(marker.get('color', result.get('color',[.47,.65,.88,1])))
        result['opacity'] = 95/255
        result['marker'] = dict(marker, placement=marker.get('placement','cell_center'))
        if result['marker']['placement']=='cell_center':
            result['marker']['billboard'] = False
    else:
        result['color'] = list(result.get('shell_color',[120/255,165/255,225/255,1]))
        result['opacity'] = 60/255
    return result
