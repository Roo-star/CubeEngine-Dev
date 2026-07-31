"""End-to-end tests for the temporary SRTP-style GUI acceptance games."""

import json
import unittest
from pathlib import Path

from stal import InvalidActionError
from stal.demo_rule_adapter import build_demo_session, resize_rule_object


EXAMPLES = Path(__file__).parents[1] / "stal" / "examples"


def load_demo(filename):
    with (EXAMPLES / filename).open(encoding="utf-8") as file:
        return build_demo_session(json.load(file))


class DemoRuleAdapterTests(unittest.TestCase):
    def test_placement_rejects_occupied_and_finishes_when_full(self):
        session = load_demo("demo_single_cell_placement_2x2x2.json")
        session.actions.apply(0)
        with self.assertRaises(InvalidActionError) as rejected:
            session.actions.apply(0)
        self.assertEqual(rejected.exception.decision.reason_code, "occupied")
        for code in range(1, session.actions.action_count):
            session.actions.apply(code)
        report = session.outcomes.evaluate()
        self.assertEqual(report.status, "placement_complete")
        self.assertTrue(report.is_terminal)
        self.assertEqual(report.winners, ("selector",))
        self.assertEqual(report.details["condition_type"], "all_cells_equal")

    def test_multi_selection_can_keep_multiple_cells_selected(self):
        session = load_demo("demo_multi_cell_selection_3x3x2.json")
        session.actions.apply(0)
        session.actions.apply(1)
        self.assertEqual(int((session.board.cells == 1).sum()), 2)
        session.actions.apply(0)
        self.assertEqual(int((session.board.cells == 1).sum()), 1)
        session.actions.apply(0)
        session.actions.apply(2)
        report = session.outcomes.evaluate()
        self.assertEqual(report.status, "ongoing")
        self.assertFalse(report.is_terminal)
        session.actions.apply(3)
        self.assertEqual(int((session.board.cells == 1).sum()), 4)

    def test_moving_cube_eats_food_and_respawns_it(self):
        session = load_demo("demo_free_cube_movement_4x3x2.json")
        self.assertEqual(session.actions.validate(1).reason_code, "out_of_bounds")

        def coordinate_for(state):
            return next(
                coordinate
                for coordinate in session.board.coordinates()
                if session.board.get_cell(coordinate) == state
            )

        direction_codes = (
            (0, 0, 1),  # X: + / -
            (1, 2, 3),  # Y: + / -
            (2, 4, 5),  # Z: + / -
        )
        for expected_count in range(1, 4):
            target = coordinate_for(2)
            for axis, positive_code, negative_code in direction_codes:
                while coordinate_for(1)[axis] != target[axis]:
                    current = coordinate_for(1)
                    delta = target[axis] - current[axis]
                    code = positive_code if delta > 0 else negative_code
                    session.actions.apply(code)
            self.assertEqual(session.runtime_data["collected"], expected_count)
            self.assertEqual(int((session.board.cells == 1).sum()), 1)
            self.assertEqual(int((session.board.cells == 2).sum()), 1)

        report = session.outcomes.evaluate()
        self.assertEqual(report.status, "ongoing")
        self.assertFalse(report.is_terminal)

    def test_resizing_preserves_rule_type_and_regenerates_valid_fixture_state(self):
        path = EXAMPLES / "demo_free_cube_movement_4x3x2.json"
        with path.open(encoding="utf-8") as file:
            source = json.load(file)
        resized = resize_rule_object(source, (5, 4, 3))
        self.assertEqual(resized["dimensions"], {"x": 5, "y": 4, "z": 3})
        self.assertEqual(resized["demo_rules"]["type"], "axis_runner")
        self.assertEqual(resized["initial_state"][1]["coordinate"], [4, 3, 2])
        session = build_demo_session(resized)
        for item in resized["initial_state"]:
            self.assertTrue(session.board.in_bounds(tuple(item["coordinate"])))

    def test_function3_result_is_read_from_json_not_hardcoded_by_game_type(self):
        path = EXAMPLES / "demo_multi_cell_selection_3x3x2.json"
        with path.open(encoding="utf-8") as file:
            source = json.load(file)
        source["outcome_rules"] = [{
            "rule_id": "custom_two_selection_win",
            "priority": 100,
            "condition": {
                "type": "count_state_at_least",
                "state": 1,
                "min_count": 2,
            },
            "result": {
                "status": "designer_custom_win",
                "is_terminal": True,
                "winners": ["custom_subject"],
            },
        }]
        session = build_demo_session(source)
        session.actions.apply(0)
        self.assertEqual(session.outcomes.evaluate().status, "ongoing")
        session.actions.apply(1)
        report = session.outcomes.evaluate()
        self.assertEqual(report.status, "designer_custom_win")
        self.assertEqual(report.winners, ("custom_subject",))
        self.assertEqual(report.details["required_cells"], 2)


if __name__ == "__main__":
    unittest.main()
