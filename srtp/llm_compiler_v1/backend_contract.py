"""The executable backend profile delivered to every model stage."""
from srtp.scene_ir_v2.component_contracts import COMPONENTS,BUILTIN_GEOMETRY,COLLIDERS,LIGHTS,BINDABLE
from srtp.asset_ir_v2.recipe_contracts import RECIPES
from srtp.input_adapter_contract import KEY_CONTROLS,MOUSE_CONTROLS

PROFILE_VERSION='cubeengine.ursina-authoring/6'


def profile():
    from srtp.presentation_patterns import model_contract
    return {'version':PROFILE_VERSION,'backend':'ProjectHost + UrsinaSceneBackend',
        'presentation_patterns':model_contract(),
        'session_random':'Host supplies integer seeds for seed_policy=session. Compilation uses deterministic seed 0; interactive sessions retain fresh seeds for replay. Do not add a fixed seed to repair a session declaration.',
        'algorithm_methods':{
            'grid.flood_region':'Arguments: state_id, start_coordinate, through_values, blocked_values, diagonal, include_boundary, blocked_coordinates. Returns a finite connected region, expands through through_values, includes nonblocked boundary if requested. Can implement zero-area opening with numbered boundary.',
            'sequence.merge_equal':'Arguments: values, empty_value, multiplier (NOT output length). Example: sequence.merge_equal([2,2,0,0],0,2) => [4,0,0,0]. Compress, merge each adjacent equal pair once, pad to the input length; score via sequence.merge_score with the same args. Match the multiplier to source arithmetic.',
            'topology.neighbors':'Arguments: topology_id, coordinate, include_diagonals. Use to compute counts and source first-click protected region; random.sample/draw plus foreach can relocate and recompute state.',
            'visibility':'Authoritative local rules know the complete board. Scene variants can conceal unrevealed values while displaying cover/flag states. This supports local hidden presentation, NOT secure per-player observations or hidden-information AI; those remain unsupported.',
            'behavior_tests':'Optional seed selects a deterministic session. Optional fixture {cells:[{state,coordinate,value}],otherwise:value,globals:{state_id:value}} sets a typed test scenario only, never the exported game. Rule/Scene/Input replay share setup. Test normal startup as well as fixtures; fixture-only tests do not establish startup behavior.'},
        'spatial_grid_layout':{'owner':'engine','policy':'cubeengine.volume-grid/1',
            'meaning':'Spatial Lift rank-3 rect_grid uses one continuous centered orthogonal volume. Each logical coordinate maps to one cubic cell at uniform pitch. Layer selection changes picking only, never rearranges the volume.',
            'authoring':'Declare topology_visualizer for every target grid. Author state colours, textures, glyphs, text, lifecycle HUD and entity appearances. Engine owns grid cell shell, 3D position, collider and overview camera; source-world background planes/layer labels do not become 3D playfield geometry. No exploded/side-by-side planes.'},
        'scene_components':list(COMPONENTS),'builtin_geometry':list(BUILTIN_GEOMETRY),
        'collider_shapes':list(COLLIDERS),'light_kinds':list(LIGHTS),'asset_recipes':list(RECIPES),
        'bindable_component_properties':BINDABLE,'bindable_node_properties':['active'],
        'scene_binding_sources':['state','flow','entity_component'],
        'scene_binding_transforms':['direct','not','map','numeric','format'],
        'scene_binding_note':'No Rule AST or arbitrary expression in Scene bindings. For compound status visibility, use nested nodes with separate state bindings, or expose a derived Rule state during Rule generation.',
        'input_controls':{'keyboard':KEY_CONTROLS,'mouse':MOUSE_CONTROLS},
        'input_phases':['press','release'],'input_event_data':['rule_coordinate','rule_entity_id'],
        'host_commands':{'quit':'Close only the current game player; no Rule action required.',
            'restart':'Reset the entire Project session, including after a terminal outcome. Use this for full-game restart; gameplay Rule actions are illegal once terminal.'},
        'host_command_target':{'kind':'host_command','command':'quit or restart'},
        'lifecycle_note':'Preserve source quit/restart controls using explicit digital host_command intents and bindings. Do not mark source Escape-to-quit unresolved for lack of a Rule quit action. Partial gameplay resets remain Rule actions.',
        'action_catalogue':'Action parameter domains are evaluated once at runtime construction. Enumerate the complete finite domain (e.g. every board coordinate); express changing eligibility in action legality, not in the domain. Newly spawned entity IDs cannot dynamically extend the action catalogue.',
        'unsupported_in_this_backend':['mesh/capsule picking collider','spot light','mesh_substitution/custom_renderer',
            'arbitrary Scene binding expressions','screen pixel event_position/event_delta','touch/gamepad/gesture input',
            'hold/repeat/axis/scroll/chord input','node transform property bindings','dynamic action catalogue expansion'],
        'coordinates':'Ursina: +X right, +Y up, zero-rotation camera faces +Z (not OpenGL -Z). Scene transform uses world units; index_to_world is a 16-number row-major affine matrix applied to prefab geometry as well as positions; shear also distorts cells. Overlay UI position uses normalized viewport-height units, not source pixels; text scale is a multiplier of default 0.025-high text, not a normalized height.',
        'initial_view':'The viewer frames playable geometry if an authored camera points away or clips the board, and reports the adjustment. This does not certify visual fidelity or fix authored occlusion. For a 3D board use a volumetric orthogonal layout and an oblique overview, not side-by-side 2D layer panels.'}


def check_alignment():
    """Cheap local gate: never pay for a drifted installed contract set."""
    from srtp.scene_ir_v2.scene_ir import SCENE_COMPILER_CAPABILITIES
    from srtp.asset_ir_v2.asset_ir import ASSET_COMPILER_CAPABILITIES
    from srtp.ir_v2.rule_ir import _EXPRESSION_OPS,_COMMAND_OPS
    from srtp.ir_v2.expression_contracts import expression_schema
    from srtp.ir_v2.command_contracts import COMMAND_CONTRACTS
    from srtp.input_ir_v2 import INPUT_COMPILER_CAPABILITIES
    issues=[]
    if set(COMPONENTS)!=set(SCENE_COMPILER_CAPABILITIES['components']): issues.append('Scene component registry drift')
    if not set(RECIPES)<=set(ASSET_COMPILER_CAPABILITIES['derivation_strategies']): issues.append('Asset recipe registry drift')
    operations={v['properties']['op']['const'] for v in expression_schema(False)['oneOf']}
    if operations!=_EXPRESSION_OPS: issues.append('Rule expression operand contract drift')
    if set(COMMAND_CONTRACTS)!=_COMMAND_OPS: issues.append('Rule command contract drift')
    if set(profile()['host_commands'])!=set(INPUT_COMPILER_CAPABILITIES.get('host_commands',())):
        issues.append('Input host command contract drift; restart the application after upgrading')
    if set(COMPONENTS['renderer']['properties']['spatial_role']['enum'])!=set(profile()['presentation_patterns']['roles']):
        issues.append('Renderer spatial-role schema and generation profile drift')
    return issues


def references(documents):
    rule=documents['rule_ir']
    return {'rule_states':[{k:v[k] for k in ('id','type','scope','topology','entity_type') if k in v}
        for v in rule.get('state',{}).get('variables',[])],
        'rule_topologies':rule.get('topologies',[]),
        'rule_actions':[{'id':a['id'],'parameters':a.get('parameters',[])} for a in rule.get('actions',[])],
        'asset_resources':[{'id':a['id'],'kind':a['kind'],'media_type':a['media_type']}
            for key in ('assets','derivations') for a in documents['asset_ir'].get(key,[])]}
