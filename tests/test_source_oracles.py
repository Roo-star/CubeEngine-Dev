"""Original source algorithms are independent oracles, not model-written tests."""
import importlib.util
import ast
import json
import itertools
import random
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import numpy as np

from srtp.acceptance_replay import compare_trace
from srtp.ir_v2.sequence_functions import merge_equal, sequence_function_specs
from srtp.project_viewer import ProjectHost
from srtp.reference_games.pygame_tictactoe.main import TicTacToe

ROOT=Path(__file__).resolve().parents[1]


def trusted_reference(name,path,package=False):
    spec=importlib.util.spec_from_file_location(name,path,submodule_search_locations=[str(path.parent)] if package else None)
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
    return module


class SourceOracleTests(unittest.TestCase):
    def test_connect_four_original_winner_against_runtime_queries(self):
        from srtp.ir_v2 import compile_rule_ir, seal_rule_ir
        from srtp.llm_compiler_v1.program_builder import expression
        path=ROOT/'srtp/reference_games/turtle_connect_complete/connect_complete.py'
        tree=ast.parse(path.read_text(encoding='utf-8'))
        # Extract the original pure function verbatim; no Turtle window or rewritten oracle.
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='check_winner')
        scope={'CONNECT_N':4}
        exec(compile(ast.Module(body=[function],type_ignores=[]),str(path),'exec'),scope)
        document=json.loads((ROOT/'artifacts/tictactoe_source/rule.rule-ir.json').read_text())
        for axis,extent in zip(document['topologies'][0]['axes'],(7,6)): axis['extent']=extent
        runtime=compile_rule_ir(seal_rule_ir(document)); self.addCleanup(runtime.close)
        query=expression("grid.has_line('rule:state.board_cell', param.player, 4)")
        for seed in range(100):
            rng=random.Random(seed); grid=runtime.state.grids['rule:state.board_cell']; grid.fill(0)
            board={}
            for coordinate in rng.sample(list(itertools.product(range(7),range(6))),seed%43):
                value=rng.choice((-1,1)); grid[coordinate]=value
                board[coordinate]='red' if value==-1 else 'yellow'
            for value,player in ((1,'yellow'),(-1,'red')):
                self.assertEqual(runtime.evaluator.evaluate(query,runtime._context({'player':value})),
                                 scope['check_winner'](board,player),(seed,player))

    def test_snake_original_movement_and_growth_against_sequence_expression(self):
        from pygame.math import Vector2
        from srtp.ir_v2.expression import ExpressionEvaluator, EvaluationContext
        from srtp.llm_compiler_v1.program_builder import expression
        path=ROOT/'srtp/reference_games/pygame_snake/snake.py'
        tree=ast.parse(path.read_text(encoding='utf-8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='SNAKE')
        scope={'Vector2':Vector2}
        exec(compile(ast.Module(body=[cls],type_ignores=[]),str(path),'exec'),scope)
        original=scope['SNAKE'].__new__(scope['SNAKE'])
        original.body=[Vector2(5,10),Vector2(4,10),Vector2(3,10)]
        evaluator=ExpressionEvaluator(); evaluator.functions.update(sequence_function_specs(None))
        query=expression('sequence.concat([vector.add(sequence.get(param.body, 0), param.direction)], sequence.slice(param.body, 0, param.keep))')
        body=tuple(tuple(p) for p in original.body); rng=random.Random(72)
        for step in range(100):
            direction=rng.choice(((1,0),(-1,0),(0,1),(0,-1))); grow=step%7==0
            original.direction=Vector2(*direction); original.new_block=grow
            original.move_snake()
            body=evaluator.evaluate(query,EvaluationContext(parameters={'body':body,'direction':direction,'keep':len(body) if grow else len(body)-1}))
            self.assertEqual(tuple(tuple(p) for p in original.body),body,step)

    def test_tictactoe_original_source_against_ir_action_traces(self):
        host=ProjectHost(ROOT/'artifacts/tictactoe_source/project.manifest.json')
        self.addCleanup(host.close)
        game=TicTacToe()
        def source_state():
            return {'board':[[(-1 if v==2 else v) for v in row] for row in game.board],
                    'terminal':bool(game.winner or game.draw)}
        def target_state():
            snap=host.controller.snapshot()
            return {'board':[[snap.grid[x][y] for x in range(3)] for y in range(3)],'terminal':snap.terminal}
        for seed in range(50):
            game.reset(); host.controller.reset()
            rng=random.Random(seed)
            moves=list(itertools.product(range(3),repeat=2)); rng.shuffle(moves)
            moves=moves[:3]+[moves[0]]+moves[3:]+[moves[1]]
            result=compare_trace(moves,lambda c:game.place(*c),lambda c:host.mouse('mouse.button.primary',{'coordinate':c}).accepted,
                                 source_state,target_state)
            self.assertTrue(result['passed'],result)

    def test_2048_all_short_rows_match_original_merge(self):
        source=trusted_reference('oracle_2048',ROOT/'srtp/reference_games/pygame_2048/logic.py')
        for row in itertools.product((0,2,4,8),repeat=4):
            board=[list(row)]+[[0]*4 for _ in range(3)]
            expected=source.moveLeft(deepcopy(board))[0]
            self.assertEqual(list(merge_equal(row,0,2)[0]),expected,row)

    def test_minesweeper_region_matches_original_source_after_first_click(self):
        source=trusted_reference('oracle_mines',ROOT/'srtp/reference_games/pygame_minesweeper/minesweeper/core/__init__.py',True)
        saved=random.getstate()
        try:
            for seed in range(12):
                random.seed(seed)
                board=source.Board(9,9,10)
                expected={(t.i,t.j) for t in board.tile_open(4,4)}
                values=np.array([[(-2 if t.type==source.BoardTile.mine else t.number) for t in row] for row in board._board],dtype=object)
                state=SimpleNamespace(grids={'test:grid':values})
                fn=sequence_function_specs(None)['core:grid.flood_region'].evaluator
                actual=set(fn(('test:grid',(4,4),[0],[-2],True,True,[]),SimpleNamespace(runtime=state)))
                self.assertEqual(actual,expected,seed)
        finally:
            random.setstate(saved)

    def test_counterexample_reports_first_actual_difference(self):
        result=compare_trace([{'move':1}],lambda e:True,lambda e:True,lambda:{'score':2},lambda:{'score':0})
        self.assertFalse(result['passed'])
        self.assertEqual(result['counterexample']['source'],{'score':2})


if __name__=='__main__': unittest.main()
