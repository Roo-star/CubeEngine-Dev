"""Acceptance tests for the unified Function 1/2/3 interaction runtime."""

import json
import unittest
from pathlib import Path

from stal import UnifiedRuleRuntime
from stal.rules import GridRules


EXAMPLES = Path(__file__).parents[1] / "stal" / "examples"


def runtime_for(filename):
    with (EXAMPLES / filename).open(encoding="utf-8") as file:
        return UnifiedRuleRuntime(json.load(file))


class UnifiedRuntimeTests(unittest.TestCase):
    def test_topology_only_click_uses_function1_editing(self):
        runtime = UnifiedRuleRuntime(GridRules(2, 2, 2).to_mapping())
        self.assertFalse(runtime.has_game_rules)
        result = runtime.click_coordinate((1, 1, 1))
        self.assertTrue(result.accepted)
        self.assertEqual(runtime.board.get_cell((1, 1, 1)), 1)

    def test_placement_click_runs_legality_and_outcome_chain(self):
        runtime = runtime_for("demo_single_cell_placement_2x2x2.json")
        first = runtime.click_coordinate((0, 0, 0))
        second = runtime.click_coordinate((0, 0, 0))
        self.assertTrue(first.accepted)
        self.assertFalse(second.accepted)
        self.assertEqual(second.reason_code, "occupied")

    def test_multi_selection_clicks_keep_multiple_coordinates_selected(self):
        runtime = runtime_for("demo_multi_cell_selection_3x3x2.json")
        runtime.click_coordinate((0, 0, 0))
        runtime.click_coordinate((0, 0, 1))
        result = runtime.click_coordinate((0, 1, 0))
        self.assertTrue(result.accepted)
        self.assertEqual(result.update_count, 1)
        self.assertEqual(result.outcome.status, "ongoing")
        fourth = runtime.click_coordinate((0, 1, 1))
        self.assertTrue(fourth.accepted)
        self.assertEqual(int((runtime.board.cells == 1).sum()), 4)

    def test_runner_supports_direction_keys_and_adjacent_coordinate_clicks(self):
        runtime = runtime_for("demo_free_cube_movement_4x3x2.json")
        self.assertEqual(runtime.direction("-X").reason_code, "out_of_bounds")
        self.assertTrue(runtime.direction("+X").accepted)
        self.assertTrue(runtime.click_coordinate((1, 1, 0)).accepted)
        self.assertEqual(runtime.board.get_cell((1, 1, 0)), 1)

    def test_reset_restores_json_initial_state(self):
        runtime = runtime_for("demo_single_cell_placement_2x2x2.json")
        runtime.click_coordinate((0, 0, 0))
        runtime.reset()
        self.assertEqual(runtime.board.get_cell((0, 0, 0)), 0)


if __name__ == "__main__":
    unittest.main()
