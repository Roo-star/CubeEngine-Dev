"""Separate executable compilation from independently evidenced product quality."""
from srtp.scene_ir_v2.component_contracts import components
from srtp.presentation_patterns import PATTERN_VERSION, SPATIAL_ROLES


def assess(documents, stages, *, spatial):
    checks=[]
    def add(identifier,status,evidence):
        checks.append({'id':identifier,'status':status,'evidence':evidence})
    passed={s['stage'] for s in stages if s.get('passed')}
    add('executable_four_ir','pass' if passed=={'rule_ir','asset_ir','scene_ir','input_ir'} else 'fail',
        sorted(passed))
    renderers=[(path,c['properties']) for path,c in components(documents.get('scene_ir',{})) if c.get('type')=='renderer']
    missing=[path for path,p in renderers if p.get('spatial_role') not in SPATIAL_ROLES]
    add('explicit_visual_roles', 'fail' if spatial and missing else 'pass',
        {'missing_roles':missing,'reason':'No automatic flattening of content into grid containers.'})
    # Model-provided traces verify executability, not equivalence to the
    # original game or completeness of the user's design requirements.
    add('source_behavior_equivalence','pending','Needs independent original-source oracle traces, including terminal/reset and rejected input.')
    add('spatial_rule_equivalence','pending' if spatial else 'not_applicable',
        'Needs independent lifted-rule cases (all directions/neighbors/merges as applicable); not one self-generated win.')
    add('source_appearance_fidelity','pending',
        'Needs state-to-asset/font/palette evidence and rendered-frame comparison; asset loading alone is insufficient.')
    add('visible_interactive_states','pending',
        'Needs rendered initial/hover/occupied/terminal frames, internal-cell ray picks, orbit/reset and legible HUD.')
    add('lifecycle_and_controls','pending','Needs source feature inventory, menus/pause/restart/undo/AI where required; unsupported features cannot be omitted silently.')
    add('performance','pending','Needs measured update/render/input latency for the produced scene at target dimensions.')
    add('fresh_pipeline_provenance','pending','Needs new Source and Lift HTTP receipts with no accepted-stage cache, unchanged approved artifact hashes, and its own render capture.')
    return {'version':'cubeengine.product-quality/1','pattern_version':PATTERN_VERSION,
        'scope':'spatial_lift' if spatial else 'source', 'product_ready':False,
        'checks':checks,'statement':'Compile success is not native-reference parity. Pending evidence is not a pass.'}
