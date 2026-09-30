"""Shared, typed scenario setup for Rule, Scene and Input acceptance only."""
from srtp.ir_v2 import compile_rule_ir
from srtp.session_random import session_sources


def test_runtime(rule, case):
    unknown=set(case)-{'name','steps','expect','fixture','seed'}
    if unknown:
        raise ValueError('Unsupported behavior test fields: '+str(sorted(unknown)))
    runtime=compile_rule_ir(rule,random_sources=session_sources(rule,case.get('seed',0)))
    try:
        fixture=case.get('fixture')
        if fixture is None: return runtime
        if not isinstance(fixture,dict) or set(fixture)-{'cells','globals','otherwise'}:
            raise ValueError('Scenario fixture supports cells, globals and otherwise only')
        cells=fixture.get('cells',[])
        if not isinstance(cells,list) or len(cells)>4096:
            raise ValueError('Scenario cells must be a bounded list')
        state=runtime.state
        fills={}
        overrides=[]
        for cell in cells:
            if not isinstance(cell,dict) or cell.get('state') not in state.grids:
                raise ValueError('Fixture cells require a declared grid state')
            key=cell['state']
            if set(cell)=={'state','otherwise'}:
                if key in fills or 'otherwise' in fixture:
                    raise ValueError('Fixture has conflicting otherwise fills for '+key)
                state.type_registry.validate(cell['otherwise'],state.variable_definitions[key]['type'],'fixture otherwise')
                fills[key]=cell['otherwise']
            elif set(cell)=={'state','coordinate','value'}:
                overrides.append(cell)
            else:
                raise ValueError('Fixture cell requires state/coordinate/value or state/otherwise')
        if 'otherwise' in fixture:
            for key in {cell['state'] for cell in cells}:
                state.type_registry.validate(fixture['otherwise'],state.variable_definitions[key]['type'],'fixture otherwise')
                state.grids[key].fill(fixture['otherwise'])
        for key,value in fills.items():
            state.grids[key].fill(value)
        for cell in overrides:
            key=cell['state']; coordinate=tuple(cell['coordinate']); grid=state.grids[key]
            if len(coordinate)!=len(grid.shape) or any(type(c) is not int or not 0<=c<grid.shape[i] for i,c in enumerate(coordinate)):
                raise ValueError('Fixture coordinate outside grid')
            state.type_registry.validate(cell['value'],state.variable_definitions[key]['type'],'fixture cell')
            grid[coordinate]=cell['value']
        for key,value in fixture.get('globals',{}).items():
            state.set_state_value(key,value)
        violations=[d for d in runtime.evaluate_invariants() if d.severity=='error']
        if violations:
            from collections import Counter
            counts={key:dict(Counter(str(v) for v in state.grids[key].flat)) for key in sorted({c['state'] for c in cells})}
            raise ValueError('Test '+str(case.get('name','unnamed'))+' fixture violates '+
                '; '.join(d.identifier+': '+d.name for d in violations)+
                '; fixture overlays initialized state (unspecified cells are retained). Grid value counts: '+str(counts))
        return runtime
    except Exception:
        runtime.close();raise


MAX_TEST_TICKS = 4096
MAX_TEST_STEPS = 256
MAX_ADVANCE_NS = 10_000_000_000


def behavior_step_contract():
    return {
        'action': {'action': 'declared Rule action ID', 'parameters': {}, 'accepted': 'boolean'},
        'clock': {'advance_ticks': 'integer 0..4096 logical ticks',
                  'advance_ns': 'integer 0..10000000000 elapsed simulation nanoseconds'},
        'ordering': 'Check/apply (or reject) an action FIRST, then advance attached time. Use a separate clock-only step to wait before an action.',
        'limits': {'steps_per_case': MAX_TEST_STEPS, 'total_ticks_per_case': MAX_TEST_TICKS},
        'unknown_fields': 'Rejected, never ignored.',
        'state_expectations': ['terminal', 'status', 'current_actor', 'cells', 'globals',
                               'winners', 'phase', 'tick']}


def validate_behavior_step(step):
    if not isinstance(step, dict):
        raise ValueError('Behavior step must be an object')
    unknown = set(step) - {'action', 'parameters', 'accepted', 'advance_ns', 'advance_ticks'}
    if unknown:
        raise ValueError('Unsupported behavior step fields: ' + str(sorted(unknown)))
    clock_fields = set(step) & {'advance_ns', 'advance_ticks'}
    if len(clock_fields) > 1:
        raise ValueError('Use only one of advance_ticks or advance_ns per behavior step')
    if 'action' in step:
        if not isinstance(step['action'], str) or not step['action']:
            raise ValueError('Behavior action must be a declared Rule action ID')
        if type(step.get('accepted')) is not bool:
            raise ValueError('Every test action requires an explicit accepted boolean')
        if not isinstance(step.get('parameters', {}), dict):
            raise ValueError('Behavior action parameters must be an object')
    elif not clock_fields or set(step) - clock_fields:
        raise ValueError('Clock-only step requires advance_ticks or advance_ns without action fields')
    for key, maximum in [('advance_ticks', MAX_TEST_TICKS), ('advance_ns', MAX_ADVANCE_NS)]:
        if key in step and (type(step[key]) is not int or not 0 <= step[key] <= maximum):
            raise ValueError(key + ' must be an integer within 0..' + str(maximum))


class BehaviorReplay:
    """Bounded interpreter shared by Rule, Scene and Input acceptance."""
    def __init__(self, runtime, name='unnamed'):
        self.runtime = runtime
        self.name = name
        self.steps = 0
        self.ticks = 0

    def execute(self, step):
        import json
        validate_behavior_step(step)
        self.steps += 1
        if self.steps > MAX_TEST_STEPS:
            raise ValueError('Behavior test exceeds 256 steps')
        runtime = self.runtime
        duration = step.get('advance_ns')
        ticks = step.get('advance_ticks', 0)
        if duration is not None:
            scheduler = runtime.document.get('flow', {}).get('scheduler', {})
            hz = scheduler.get('tick_hz')
            if scheduler.get('clock') not in ('fixed_tick', 'real_time') or type(hz) is not int or hz <= 0:
                raise ValueError('advance_ns requires a fixed_tick or real_time clock with positive tick_hz')
            ticks = 0 if runtime.paused else (runtime.time_remainder_units + duration * hz) // 1_000_000_000
        if self.ticks + ticks > MAX_TEST_TICKS:
            raise ValueError('Behavior test exceeds total logical tick budget ' + str(MAX_TEST_TICKS))
        changed = rejected = False
        if 'action' in step:
            if step['action'] not in {a['id'] for a in runtime.document.get('actions', [])}:
                raise ValueError('Behavior test references an undeclared action: ' + step['action'])
            candidates = [a for a in runtime.all_actions() if a.action_id == step['action'] and
                          json.dumps(dict(a.parameters), sort_keys=True) == json.dumps(step.get('parameters', {}), sort_keys=True)]
            legal = len(candidates) == 1 and runtime.is_legal(candidates[0])
            if legal != step['accepted']:
                raise ValueError('Test {0}: expected accepted={1} for {2}, got {3}; before action phase={4}, tick={5}. Attached clock advancement runs AFTER the action assertion; use a separate clock step to wait first.'.format(
                    self.name, step['accepted'], step['action'], legal, runtime.state.phase, runtime.state.tick))
            if legal:
                runtime.apply_action(candidates[0]); changed = True
            else:
                rejected = True
        before_tick = runtime.state.tick
        if duration is not None:
            runtime.advance_time_ns(duration)
            while not runtime.paused and runtime.time_remainder_units >= 1_000_000_000:
                report = runtime.advance_time_ns(0)
                if not report.consumed_ticks:
                    raise ValueError('Behavior clock backlog made no progress')
        else:
            for _ in range(ticks):
                runtime.advance_tick()
        advanced = runtime.state.tick - before_tick
        self.ticks += advanced
        return {'changed': changed or advanced > 0, 'rejected': rejected, 'ticks': advanced,
                'phase': runtime.state.phase, 'tick': runtime.state.tick}
