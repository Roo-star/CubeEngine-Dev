"""Rule tests for the four playable SRTP 3D transformation adapters."""

import unittest

from srtp.transformed_games import Connect3D, Game2048_3D, Minesweeper3D, Snake3D


class TransformedGameTests(unittest.TestCase):
    def test_snake_moves_through_z_and_retains_growth(self):
        game = Snake3D((6, 6, 4), seed=3)
        game.food = (3, 3, 3)
        game.body = [(3, 3, 1)]

        self.assertTrue(game.set_direction((0, 0, 1)))
        self.assertEqual(game.step(), "moved")
        self.assertEqual(game.step(), "food")
        self.assertEqual(game.score, 1)
        self.assertEqual(len(game.body), 2)

    def test_minesweeper_uses_spatial_neighbours_and_z_scaled_attempts(self):
        game = Minesweeper3D((8, 8, 3), source_mines=8, seed=0)

        self.assertEqual(game.mine_attempts, 24)
        game.mines = {(1, 1, 1)}
        self.assertEqual(game.adjacent_mines((0, 0, 0)), 1)
        self.assertEqual(game.reveal((1, 1, 1)), "mine")

    def test_connect_preserves_y_gravity_across_z_columns(self):
        game = Connect3D((4, 4, 3))

        self.assertEqual(game.drop(2, 1), (2, 0, 1))
        self.assertEqual(game.drop(2, 1), (2, 1, 1))
        self.assertEqual(game.board[(2, 0, 1)], "yellow")
        self.assertEqual(game.board[(2, 1, 1)], "red")

    def test_2048_merges_once_along_the_new_z_axis(self):
        game = Game2048_3D((2, 2, 4), seed=0)
        game.board = {(0, 0, 0): 2, (0, 0, 1): 2, (0, 0, 2): 2}

        self.assertTrue(game.move(2, -1, spawn=False))
        self.assertEqual(game.board, {(0, 0, 0): 4, (0, 0, 1): 2})
        self.assertEqual(game.score, 4)


if __name__ == "__main__":
    unittest.main()
