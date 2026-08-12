import unittest
from pathlib import Path

import numpy as np

from srtp.ir_v2 import (
    bracketed_run,
    bracketed_sites,
    compile_rule_ir,
    connected,
    directions,
    load_rule_ir,
    neighbors,
    ray,
    region,
    shortest_path,
)


ROOT = Path(__file__).parents[1]
OTHELLO = ROOT / "srtp" / "examples" / "rule_ir_v2" / "othello_2d.rule-ir.json"
BOARD = "rule:state.board_cell"
BLACK = "rule:participant.black"
WHITE = "rule:participant.white"


class BoundedGridTopologyTests(unittest.TestCase):
    def test_canonical_directions_neighbors_and_ray(self):
        self.assertEqual(len(directions(2, True)), 8)
        self.assertEqual(len(directions(3, True)), 26)
        self.assertEqual(directions(2, False), ((-1, 0), (0, -1), (0, 1), (1, 0)))
        self.assertEqual(neighbors((3, 3), (0, 0), False), ((0, 1), (1, 0)))
        self.assertEqual(ray((4, 4), (1, 1), (1, 1)), ((2, 2), (3, 3)))

    def test_region_and_connectivity_are_deterministic(self):
        self.assertEqual(
            region((5, 5), (2, 2), 1, "manhattan"),
            ((2, 2), (1, 2), (2, 1), (2, 3), (3, 2)),
        )
        self.assertTrue(connected((4, 4), ((0, 0), (0, 1), (1, 1))))
        self.assertFalse(connected((4, 4), ((0, 0), (1, 1))))
        self.assertTrue(connected((4, 4), ((0, 0), (1, 1)), include_diagonals=True))

    def test_shortest_path_uses_stable_tie_breaking_and_obstacles(self):
        path = shortest_path((4, 4), (0, 0), (2, 2), blocked=((0, 1), (1, 1)))
        self.assertEqual(path, ((0, 0), (1, 0), (2, 0), (2, 1), (2, 2)))
        self.assertEqual(shortest_path((2, 2), (0, 0), (1, 1), blocked=((0, 1), (1, 0))), ())

    def test_bracketed_runs_are_game_agnostic(self):
        board = np.zeros((5, 5), dtype=object)
        board[(2, 1)] = -1
        board[(2, 2)] = -1
        board[(2, 3)] = 1
        board[(1, 1)] = -1
        board[(0, 2)] = 1

        self.assertEqual(bracketed_run(board, (2, 0), (0, 1), -1, 1), ((2, 1), (2, 2)))
        self.assertEqual(
            bracketed_sites(board, (2, 0), -1, 1, include_diagonals=True),
            ((1, 1), (2, 1), (2, 2)),
        )
        self.assertEqual(bracketed_run(board, (4, 4), (-1, 0), -1, 1), ())


class OthelloRuleIRTests(unittest.TestCase):
    def test_source_initial_position_and_legal_moves(self):
        runtime = compile_rule_ir(load_rule_ir(OTHELLO))
        board = runtime.state.grids[BOARD]

        self.assertEqual(runtime.action_count, 64)
        self.assertEqual(runtime.state.current_actor, BLACK)
        self.assertEqual(board[(3, 3)], -1)
        self.assertEqual(board[(4, 4)], -1)
        self.assertEqual(board[(3, 4)], 1)
        self.assertEqual(board[(4, 3)], 1)
        self.assertEqual(
            [(action.code, action.parameters["target"]) for action in runtime.legal_actions()],
            [(19, (2, 3)), (26, (3, 2)), (37, (4, 5)), (44, (5, 4))],
        )

    def test_place_flips_every_bracketed_ray_and_advances_turn(self):
        runtime = compile_rule_ir(load_rule_ir(OTHELLO))

        report = runtime.apply_action(19)

        self.assertEqual(runtime.state.grids[BOARD][(2, 3)], 1)
        self.assertEqual(runtime.state.grids[BOARD][(3, 3)], 1)
        self.assertEqual(runtime.state.current_actor, WHITE)
        self.assertFalse(report.outcome.terminal)
        self.assertEqual(
            [(action.code, action.parameters["target"]) for action in runtime.legal_actions()],
            [(18, (2, 2)), (20, (2, 4)), (34, (4, 2))],
        )

    def test_turn_eligibility_automatically_passes_ineligible_player(self):
        runtime = compile_rule_ir(load_rule_ir(OTHELLO))
        board = runtime.state.grids[BOARD]
        board.fill(1)
        board[(0, 1)] = -1
        board[(0, 2)] = 0
        board[(7, 1)] = -1
        board[(7, 2)] = 0

        runtime.apply_action(2)

        self.assertEqual(runtime.state.current_actor, BLACK)
        self.assertEqual([action.code for action in runtime.legal_actions()], [58])

        result = runtime.apply_action(58).outcome
        self.assertTrue(result.terminal)
        self.assertEqual(result.status, "win")
        self.assertEqual(result.winners, (BLACK,))
        self.assertEqual(result.losers, (WHITE,))
        self.assertEqual(result.scores, {"black": 64, "white": 0})

    def test_complete_game_replays_deterministically_without_game_adapter(self):
        first = compile_rule_ir(load_rule_ir(OTHELLO))
        second = compile_rule_ir(load_rule_ir(OTHELLO))
        turns = 0

        while not first.evaluate_outcome().terminal:
            action = first.legal_actions()[0]
            first.apply_action(action.code)
            second.apply_action(action.code)
            self.assertEqual(first.state.state_hash(), second.state.state_hash())
            turns += 1
            self.assertLessEqual(turns, 60)

        outcome = first.evaluate_outcome()
        self.assertIn(outcome.status, ("win", "draw"))
        self.assertTrue(outcome.terminal)
        self.assertEqual(first.legal_actions(), ())
        self.assertEqual(sum(outcome.scores.values()), int(np.count_nonzero(first.state.grids[BOARD])))


if __name__ == "__main__":
    unittest.main()
