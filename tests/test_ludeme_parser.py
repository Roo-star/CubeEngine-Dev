"""Tests for Ludeme parser."""

from __future__ import annotations

import unittest
from pathlib import Path

from ludeme.parser import parse_ludeme_file, parse_ludeme_text
from ludeme.validate import validation_errors


EXAMPLES = Path(__file__).resolve().parents[1] / "ludeme" / "examples"


class LudemeParserTests(unittest.TestCase):
    def test_parse_2d_fixture(self) -> None:
        game = parse_ludeme_file(EXAMPLES / "tictactoe_2d.cube.lud")
        self.assertEqual(game.name, "Tic-Tac-Toe")
        self.assertEqual(game.players, 2)
        self.assertEqual(game.dimensions, (3, 3, 1))
        self.assertEqual(len(validation_errors(game)), 0)

    def test_parse_3d_fixture(self) -> None:
        game = parse_ludeme_file(EXAMPLES / "tictactoe_3x3x3.cube.lud")
        self.assertEqual(game.dimensions, (3, 3, 3))

    def test_comments_and_meta(self) -> None:
        text = """
        ; comment
        (meta (format "cubeengine.ludeme/0.1"))
        (game "Demo"
          (players 2)
          (equipment {
            (board (rect 2 2 1))
            (piece "A" P1)
            (piece "B" P2)
          })
          (rules
            (play (move Add (to (sites Empty))))
            (end (if (is Line 2) (result Mover Win)))
          )
        )
        """
        game = parse_ludeme_text(text)
        self.assertEqual(game.board.x, 2)
        self.assertEqual(game.format_version, "cubeengine.ludeme/0.1")


if __name__ == "__main__":
    unittest.main()
