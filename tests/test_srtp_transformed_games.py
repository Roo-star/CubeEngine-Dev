"""Rule tests for the four playable SRTP 3D transformation adapters."""

import unittest

from srtp.transformed_games import Connect3D, Game2048_3D, Minesweeper3D, Snake3D
from srtp.transformed_viewer import layer_display_policy


class TransformedGameTests(unittest.TestCase):
    def test_layer_focus_never_hides_committed_state_on_other_layers(self):
        occupied = layer_display_policy(2, active_z=0, show_all_layers=False, stateful=True)
        empty = layer_display_policy(2, active_z=0, show_all_layers=False, stateful=False)
        focused = layer_display_policy(0, active_z=0, show_all_layers=False, stateful=False)

        self.assertTrue(occupied["visible"])
        self.assertFalse(occupied["interactive"])
        self.assertFalse(empty["visible"])
        self.assertTrue(focused["visible"])
        self.assertTrue(focused["interactive"])

    def test_snake_waits_for_first_direction_like_the_complete_source(self):
        game = Snake3D((8, 8, 3), seed=1)
        initial = list(game.body)

        self.assertFalse(game.started)
        self.assertEqual(game.step(), "waiting_for_input")
        self.assertEqual(game.body, initial)
        self.assertTrue(game.set_direction((0, 1, 0)))
        self.assertTrue(game.started)
        self.assertEqual(game.step(), "moved")

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

    def test_minesweeper_first_click_is_safe_and_right_click_flags_block_reveal(self):
        game = Minesweeper3D((6, 6, 3), source_mines=6, seed=4)
        flagged = (0, 0, 0)

        self.assertEqual(game.toggle_flag(flagged), "flagged")
        self.assertEqual(game.reveal(flagged), "ignored")
        self.assertEqual(game.toggle_flag(flagged), "flag_removed")
        first = (3, 3, 1)
        game.reveal(first)

        self.assertNotIn(first, game.mines)
        self.assertTrue(all(neighbour not in game.mines for neighbour in game.neighbours(first)))
        self.assertEqual(len(game.mines), game.mine_attempts)

    def test_minesweeper_resize_preserves_source_density_not_only_layer_count(self):
        game = Minesweeper3D((20, 10, 3), source_mines=10, source_area=100, seed=1)

        self.assertEqual(game.mine_attempts, 60)

    def test_connect_preserves_y_gravity_across_z_columns(self):
        game = Connect3D((4, 4, 3))

        self.assertEqual(game.drop(2, 1), (2, 0, 1))
        self.assertEqual(game.drop(2, 1), (2, 1, 1))
        self.assertEqual(game.board[(2, 0, 1)], "yellow")
        self.assertEqual(game.board[(2, 1, 1)], "red")

    def test_connect_lifts_source_connect_four_outcome_into_3d(self):
        game = Connect3D((5, 5, 5), connect_n=4)
        game.board = {(index, index, index): "yellow" for index in range(3)}
        game.player = "yellow"

        game.board[(3, 3, 3)] = "yellow"
        line = game._winning_line((3, 3, 3), "yellow")
        game.winner = "yellow" if line else None
        game.winning_coordinates = line

        self.assertEqual(game.status, "win")
        self.assertEqual(len(game.winning_coordinates), 4)

    def test_2048_merges_once_along_the_new_z_axis(self):
        game = Game2048_3D((2, 2, 4), seed=0)
        game.board = {(0, 0, 0): 2, (0, 0, 1): 2, (0, 0, 2): 2}

        self.assertTrue(game.move(2, -1, spawn=False))
        self.assertEqual(game.board, {(0, 0, 0): 4, (0, 0, 1): 2})
        self.assertEqual(game.score, 4)


if __name__ == "__main__":
    unittest.main()
