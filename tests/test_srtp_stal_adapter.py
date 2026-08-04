"""End-to-end Rule Schema → STAL Function 1/2/3 runtime tests."""

import unittest
from pathlib import Path

from srtp import parse_rule_file
from stal import UnifiedRuleRuntime


EXAMPLES = Path(__file__).parents[1] / "srtp" / "examples"


class SrtpStalAdapterTests(unittest.TestCase):
    def test_json_rules_alternate_roles_reject_occupied_and_report_win(self):
        report = parse_rule_file(EXAMPLES / "placement_line_3x3.json")
        runtime = UnifiedRuleRuntime(report.schema)

        first = runtime.click_coordinate((0, 0, 0))
        occupied = runtime.click_coordinate((0, 0, 0))
        self.assertTrue(first.accepted)
        self.assertFalse(occupied.accepted)
        self.assertEqual(occupied.reason_code, "occupied")
        self.assertEqual(runtime.session.runtime_data["current_role"], "circle")

        runtime.click_coordinate((0, 1, 0))
        runtime.click_coordinate((1, 0, 0))
        runtime.click_coordinate((1, 1, 0))
        result = runtime.click_coordinate((2, 0, 0))
        self.assertEqual(result.outcome.status, "win")
        self.assertTrue(result.outcome.is_terminal)
        self.assertEqual(result.outcome.winners, ("cross",))
        self.assertEqual(runtime.session.runtime_data["current_role"], "cross")

    def test_python_rules_have_same_playable_contract(self):
        report = parse_rule_file(EXAMPLES / "tictactoe_2d.py")
        runtime = UnifiedRuleRuntime(report.schema)

        self.assertEqual(runtime.board.rules.dimensions, (3, 3, 1))
        for coordinate in ((0, 0, 0), (0, 1, 0), (1, 0, 0), (1, 1, 0), (2, 0, 0)):
            result = runtime.click_coordinate(coordinate)
        self.assertEqual(result.outcome.status, "win")
        self.assertEqual(result.outcome.winners, ("player_1",))

    def test_intersection_selection_toggles_without_terminal_assumption(self):
        report = parse_rule_file(EXAMPLES / "intersection_selection_5x5.json")
        runtime = UnifiedRuleRuntime(report.schema)

        self.assertEqual(report.schema["space"]["coordinate_anchor"], "grid_intersection")
        self.assertTrue(runtime.click_coordinate((2, 3, 0)).accepted)
        self.assertEqual(runtime.board.get_cell((2, 3, 0)), 1)
        self.assertTrue(runtime.click_coordinate((2, 3, 0)).accepted)
        self.assertEqual(runtime.board.get_cell((2, 3, 0)), 0)
        self.assertEqual(runtime.outcome().status, "ongoing")


if __name__ == "__main__":
    unittest.main()
