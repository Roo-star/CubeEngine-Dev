"""Offline spatial Rule draft validation. Approval and bundling are separate."""
from copy import deepcopy
import json
from ..llm_compiler_v1.compiler import load_compile_report_from_bundle
from ..llm_compiler_v1.lift_templates import LiftTemplate, apply_lift_template
from ..ir_v2 import seal_rule_ir, compile_rule_ir, resolve_rule_configuration
from .render_profile import spatial_settings


def make_rule_draft(manifest, dimensions, parameters):
    spatial_settings(dimensions,1,1)
    report=load_compile_report_from_bundle(manifest.parent,manifest_path=manifest)
    rule=report.documents['rule_ir']
    if not isinstance(parameters,dict):raise ValueError('Parameter overrides must be a JSON object')
    resolve_rule_configuration(rule,overrides=parameters)
    grids=[t for t in rule['topologies'] if t['kind']=='rect_grid']
    if len(grids)!=1 or len(grids[0]['axes'])!=3:
        raise ValueError('Volume editing currently requires one three-dimensional grid')
    grid=grids[0]
    result=apply_lift_template(rule,LiftTemplate(grid['id'],{a['name']:dimensions[i] for i,a in enumerate(grid['axes'])},movement='planar'))
    if result.residual:raise ValueError('This Rule requires a structural review: '+ '; '.join(result.residual[:3]))
    candidate=deepcopy(result.rule)
    for definition in candidate.get('parameters',[]):
        if definition['id'] in parameters:definition['default']={'op':'literal','value':parameters[definition['id']]}
    for mode in candidate.get('modes',[]):
        if mode.get('id')=='rule:mode.default':
            mode['overrides']=[entry for entry in mode.get('overrides',[]) if entry['parameter'] not in parameters]
    candidate=seal_rule_ir(candidate,revision=rule['revision']+1)
    runtime=compile_rule_ir(candidate)
    try:
        actions=runtime.legal_actions(); state=runtime.state
        validation={'initial_legal_actions':len(actions),'dimensions':list(state.topologies[grid['id']])}
    finally:runtime.close()
    # A validated Rule is not a reviewed, sealed four-document Target bundle.
    return {'format':'cubeengine.spatial-rule-draft/1.0','base_manifest_hash':json.loads(manifest.read_text(encoding='utf-8'))['content_hash'],
            'base_rule_hash':rule['content_hash'],'rule':candidate,'parameter_overrides':parameters,
            'validation':validation,'changes':result.report(),'requires_target_review':True}
