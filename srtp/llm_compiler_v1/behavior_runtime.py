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
