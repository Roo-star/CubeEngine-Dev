"""Tests for Ludeme runtime."""

from __future__ import annotations

import unittest
from pathlib import Path

from ludeme.parser import parse_ludeme_file
from ludeme.runtime import LudemeRuntime


EXAMPLES = Path(__file__).resolve().parents[1] / "ludeme" / "examples"


class LudemeRuntimeTests(unittest.TestCase):
    def test_2d_winning_line(self) -> None:
        runtime = LudemeRuntime(parse_ludeme_file(EXAMPLES / "tictactoe_2d.cube.lud"))
        moves = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (0, 2, 0)]
        for coord in moves:
            result = runtime.apply_move(coord)
            self.assertTrue(result.accepted)
        outcome = runtime.evaluate()
        self.assertTrue(outcome.is_terminal)
        self.assertEqual(outcome.winner, 1)

    def test_reject_occupied_cell(self) -> None:
        runtime = LudemeRuntime(parse_ludeme_file(EXAMPLES / "tictactoe_2d.cube.lud"))
        self.assertTrue(runtime.apply_move((0, 0, 0)).accepted)
        rejected = runtime.apply_move((0, 0, 0))
        self.assertFalse(rejected.accepted)

    def test_3d_vertical_line(self) -> None:
        runtime = LudemeRuntime(parse_ludeme_file(EXAMPLES / "tictactoe_3x3x3.cube.lud"))
        sequence = [(0, 0, 0), (1, 0, 0), (0, 0, 1), (1, 0, 1), (0, 0, 2)]
        for coord in sequence:
            runtime.apply_move(coord)
        outcome = runtime.evaluate()
        self.assertTrue(outcome.is_terminal)
        self.assertEqual(outcome.winner, 1)

    def test_draw_on_full_board(self) -> None:
        runtime = LudemeRuntime(parse_ludeme_file(EXAMPLES / "tictactoe_2d.cube.lud"))
        runtime.cells = {
            (0, 0, 0): 1, (1, 0, 0): 2, (2, 0, 0): 1,
            (0, 1, 0): 2, (1, 1, 0): 1, (2, 1, 0): 2,
            (0, 2, 0): 2, (1, 2, 0): 1, (2, 2, 0): 2,
        }
        outcome = runtime.evaluate()
        self.assertEqual(outcome.status, "draw")


if __name__ == "__main__":
    unittest.main()
