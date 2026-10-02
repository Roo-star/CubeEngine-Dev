"""The executable backend profile delivered to every model stage."""
from srtp.scene_ir_v2.component_contracts import COMPONENTS,BUILTIN_GEOMETRY,COLLIDERS,LIGHTS,BINDABLE
from srtp.asset_ir_v2.recipe_contracts import RECIPES
from srtp.input_adapter_contract import KEY_CONTROLS,MOUSE_CONTROLS,HOST_FOCUS,HOST_VALUE_TYPES

PROFILE_VERSION='cubeengine.ursina-authoring/14'


def profile():
    from srtp.presentation_patterns import model_contract
    from srtp.depth_view import contract as depth_contract
    from .behavior_runtime import behavior_step_contract
    from srtp.scene_ir_v2.binding_expressions import contract as expression_contract
    return {'version':PROFILE_VERSION,'backend':'ProjectHost + UrsinaSceneBackend',
        'presentation_patterns':model_contract(),
        'depth_view':depth_contract(),
        'behavior_steps':behavior_step_contract(),
        'session_random':'Host supplies integer seeds for seed_policy=session. Compilation uses deterministic seed 0; interactive sessions retain fresh seeds for replay. Do not add a fixed seed to repair a session declaration.',
        'algorithm_methods':{
            'grid.flood_region':'Arguments: state_id, start_coordinate, through_values, blocked_values, diagonal, include_boundary, blocked_coordinates. Returns a finite connected region, expands through through_values, includes nonblocked boundary if requested. Can implement zero-area opening with numbered boundary.',
            'sequence.merge_equal':'Arguments: values, empty_value, multiplier (NOT output length). Example: sequence.merge_equal([2,2,0,0],0,2) => [4,0,0,0]. Compress, merge each adjacent equal pair once, pad to the input length; score via sequence.merge_score with the same args. Match the multiplier to source arithmetic.',
            'topology.neighbors':'Arguments: topology_id, coordinate, include_diagonals. Use to compute counts and source first-click protected region; random.sample/draw plus foreach can relocate and recompute state.',
            'rejection_sampling':'A source loop that draws a random cell and redraws while it is occupied picks uniformly among the eligible cells. Express it as ONE random draw over the filtered domain (e.g. the sites whose value is empty), not as chained systems that retry; keep the source value draw (e.g. random.choice((2, 4))) as written. source_equivalence checks positions, values and their frequencies against the original.',
            'visibility':'Authoritative local rules know the complete board. Scene variants can conceal unrevealed values while displaying cover/flag states. This supports local hidden presentation, NOT secure per-player observations or hidden-information AI; those remain unsupported.',
            'behavior_tests':'Optional seed selects a deterministic session. Optional fixture {cells:[{state,coordinate,value}],otherwise:value,globals:{state_id:value}} sets a typed test scenario only, never the exported game. Alternatively cells may include {state,otherwise:value} to fill one grid before coordinate overrides; do not combine per-state fills with top-level otherwise or duplicate fills. Unspecified cells retain initialized state. All fixtures must satisfy Rule invariants. Rule/Scene/Input replay share setup. Test normal startup as well as fixtures; fixture-only tests do not establish startup behavior.'},
        'spatial_grid_layout':{'owner':'engine','policy':'cubeengine.volume-grid/1',
            'meaning':'Spatial Lift rank-3 rect_grid uses one continuous centered orthogonal volume. Each logical coordinate maps to one cubic cell at uniform pitch. Default layer focus changes picking only; the optional host depth slice hides other layers for interior access. Neither mode rearranges the volume.',
            'authoring':'Declare topology_visualizer for every target grid. Author state colours, textures, glyphs, text, lifecycle HUD and entity appearances. Engine owns grid cell shell, 3D position, collider and overview camera; source-world background planes/layer labels do not become 3D playfield geometry. No exploded/side-by-side planes.'},
        'scene_components':list(COMPONENTS),'builtin_geometry':list(BUILTIN_GEOMETRY),
        'collider_shapes':list(COLLIDERS),'light_kinds':list(LIGHTS),'asset_recipes':list(RECIPES),
        'asset_recipe_inputs':'billboard, extrusion and cube_face_projection require one decodable image. Extrusion requires alpha_cutoff 1..255 (default 1), at most 16384 pixels; crop explicitly with atlas_region when intended. Asset compilation verifies the geometry before Scene generation.',
        'bindable_component_properties':BINDABLE,'bindable_node_properties':['active'],
        'scene_binding_sources':['state','parameter','outcome','flow','entity_component','interaction','expression'],
        'scene_parameter_bindings':'Read immutable Rule configuration with source {kind:parameter, parameter:<Rule parameter ID>}. Use Rule state and actions for mutable menu selection.',
        'rule_outcome_bindings':'Result text needs no extra Rule state: source {kind:flow, property:terminal} is true once the game has ended; outcome_status is ongoing or the matched outcome status (e.g. win/draw); winner is the first winning participant id (empty when none); outcome is the matched outcome id. Example HUD: if(read(flow.terminal), <winner/draw text via map on flow.winner or flow.outcome_status>, <turn text via flow.current_actor>).',
        'scene_binding_transforms':['direct','not','map','numeric','format','digit','integer_format'],
        'number_display':'digit {place:0 units/1 tens/2 hundreds, minimum:0, maximum:999, values:[ten original digit texture IDs]} reads numeric Rule state and drives renderer.texture. integer_format {width:3,minimum:0,maximum:999} gives zero-padded text. Keep source sprite typography via digit when source uses an atlas.',
        'pointer_feedback':'interaction {property:pressed or hovered, scope:target or any, control:mouse.button.primary (optional), node:scene node subtree filter (optional)} is host visual state. Bind to renderer.pressed; pressed_style overrides its current Rule variant during a valid hold, reverting on release/drag/cancel. Each Rule variant may have its own pressed_style. Face buttons can use target scope, board-wide excited face uses any + board node filter. This feedback requires no gameplay Rule action and no extra game state.',
        'presentation_expression':expression_contract(),
        'scene_binding_note':'Use source {kind:expression,expression:AST} for combinations of Rule state, flow and host interaction. Example: all(read(interaction.pressed), eq(read(state),literal(value))). Each operand is an explicit {op:read,source:existing descriptor}, {op:literal,value:...}, or {op:operator,args:[...]}. No arbitrary Python, mutations or additional Rule state for purely visual conditions. Use if for conditional appearance/texture/text; existing transforms apply to the computed result. topology_sites/entity_nodes component selectors may name a uniquely identified component in a prefab child; ambiguous descendant IDs are rejected.',
        'input_controls':{'keyboard':KEY_CONTROLS,'mouse':MOUSE_CONTROLS},
        'input_focus':HOST_FOCUS,'input_value_types':HOST_VALUE_TYPES,
        'input_phases':['press','release'],'input_event_data':['rule_coordinate','rule_entity_id','scene_node_id','rule_topology','pointer_click','pointer_gesture','pointer_swipe_direction','pointer_swipe_distance_px'],
        'pointer_routing':{'filter_fields':['node','subtree','topology','gesture','direction','min_distance_px'],
            'trigger_example':{'kind':'control','device':'mouse','control':'mouse.button.primary','phase':'release','modifiers':[],'modifier_policy':'exact','pointer':{'node':'scene:button','gesture':'click'}},
            'semantics':'ProjectView buffers pointer input until release. Same-target press/release emits click; an empty viewport click emits background_click; primary-button drag emits swipe with dominant direction and physical viewport-pixel distance. UI host buttons are excluded. Drag does not also emit click. Right-drag remains camera orbit. Use pointer.gesture=background_click for only blank space, or gesture=swipe with direction and min_distance_px for source swipes. pointer.node matches scene_node_id, subtree=true includes descendants; topology restricts the Rule board.',
            'phase_note':'Mouse press/release phases are delivered together after a completed gesture, not as raw mouse-down timing. Continuous/raw pointer movement remains unsupported.'},
        'input_consume_policies':['binding','first_match','first_legal','all_events'],
        'input_action_selection':'first_match consumes before Rule legality and is NOT a legal-action fallback. For several actions on one physical control use a first_legal context with distinct binding priorities: the authoritative Rule runtime selects the first currently legal action, consumes once, and applies only that action. Do not make overlapping non-consuming bindings and assume mutual exclusion will fix the conflict.',
        'host_commands':{'quit':'Close only the current game player; no Rule action required.',
            'restart':'Reset the entire Project session, including after a terminal outcome. Use this for full-game restart; gameplay Rule actions are illegal once terminal.'},
        'host_command_target':{'kind':'host_command','command':'quit or restart',
            'when':'optional {state:<global Rule state ID>,one_of:[typed values]} restricts this host command to source lifecycle stages; use separate intents for differently gated keys'},
        'lifecycle_note':'Preserve source quit/restart controls using explicit digital host_command intents and bindings. A host command when guard reads authoritative Rule state before routing, without changing it. Partial gameplay resets remain Rule actions.',
        'action_catalogue':'Action parameter domains are evaluated once at runtime construction. Enumerate the complete finite domain (e.g. every board coordinate); express changing eligibility in action legality, not in the domain. Newly spawned entity IDs cannot dynamically extend the action catalogue.',
        'unsupported_in_this_backend':['mesh/capsule picking collider','spot light','mesh_substitution/custom_renderer',
            'arbitrary Python/Rule effects in Scene expressions','screen pixel event_position/event_delta','touch/gamepad/continuous gesture input',
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
    for field in ('binding_sources','binding_transforms'):
        if set(profile()['scene_'+field])!=set(SCENE_COMPILER_CAPABILITIES[field]):
            issues.append('Scene '+field+' contract drift; restart the application after upgrading')
    if not set(RECIPES)<=set(ASSET_COMPILER_CAPABILITIES['derivation_strategies']): issues.append('Asset recipe registry drift')
    operations={v['properties']['op']['const'] for v in expression_schema(False)['oneOf']}
    if operations!=_EXPRESSION_OPS: issues.append('Rule expression operand contract drift')
    if set(COMMAND_CONTRACTS)!=_COMMAND_OPS: issues.append('Rule command contract drift')
    if set(profile()['host_commands'])!=set(INPUT_COMPILER_CAPABILITIES.get('host_commands',())):
        issues.append('Input host command contract drift; restart the application after upgrading')
    if set(profile()['pointer_routing']['filter_fields'])!=set(INPUT_COMPILER_CAPABILITIES.get('pointer_filters',())):
        issues.append('Input pointer filter contract drift')
    if set(profile()['input_consume_policies'])!=set(INPUT_COMPILER_CAPABILITIES.get('consume_policies',())):
        issues.append('Input selection policy contract drift')
    if set(COMPONENTS['renderer']['properties']['spatial_role']['enum'])!=set(profile()['presentation_patterns']['roles']):
        issues.append('Renderer spatial-role schema and generation profile drift')
    return issues


def references(documents):
    rule=documents['rule_ir']
    return {'scene_nodes':[{'id':n['id'],'parent':n.get('parent'),
                           'components':[{'id':c['id'],'type':c['type']} for c in n.get('components',[])]}
                          for n in documents.get('scene_ir',{}).get('nodes',[])],
        'rule_states':[{k:v[k] for k in ('id','type','scope','topology','entity_type') if k in v}
        for v in rule.get('state',{}).get('variables',[])],
        'rule_topologies':rule.get('topologies',[]),
        'rule_actions':[{'id':a['id'],'parameters':a.get('parameters',[])} for a in rule.get('actions',[])],
        'asset_resources':[{'id':a['id'],'kind':a['kind'],'media_type':a['media_type']}
            for key in ('assets','derivations') for a in documents['asset_ir'].get(key,[])]}
