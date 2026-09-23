"""Independent trace comparison: the source oracle supplies expected behavior.

Callables are trusted harness adapters, never Python supplied by a model. The
comparison is game-agnostic and keeps the first counterexample for reproduction.
"""
from copy import deepcopy


def compare_trace(events, source_step, target_step, source_snapshot, target_snapshot):
    checked=[]
    for index,event in enumerate(events):
        expected_accept=source_step(deepcopy(event))
        actual_accept=target_step(deepcopy(event))
        expected,actual=source_snapshot(),target_snapshot()
        row={'index':index,'event':deepcopy(event),'source_accepted':expected_accept,
             'target_accepted':actual_accept,'source':deepcopy(expected),'target':deepcopy(actual)}
        if expected_accept != actual_accept or expected != actual:
            return {'passed':False,'checked_steps':index+1,'counterexample':row}
        checked.append(row)
    return {'passed':True,'checked_steps':len(checked),'counterexample':None}
