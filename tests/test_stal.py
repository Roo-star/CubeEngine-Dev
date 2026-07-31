"""Acceptance tests for STAL Function 1: pure X × Y × Z topology."""

import json
import unittest
from pathlib import Path

from stal import Battlefield, GridRules, MoveError, RuleEvaluation, RuleInputError, parse_rule_text


class GridRulesTests(unittest.TestCase):
    def test_extracts_xyz_without_requiring_game_rules(self):
        rules = GridRules.from_mapping({
            "schema_version": "cubeengine.srtp/v1",
            "game_id": "a_future_game",
            "dimensions": {"x": 4, "y": 3, "z": 2},
            "players": [1, 2],
            "turn_order": "alternating",
            "win_condition": {"type": "line", "length": 3},
        })
        self.assertEqual(rules.dimensions, (4, 3, 2))
        self.assertNotIn("players", rules.to_mapping())
        self.assertNotIn("win_condition", rules.to_mapping())

    def test_rejects_invalid_dimensions_only(self):
        with self.assertRaises(RuleInputError):
            GridRules(x=0, y=3, z=4)

    def test_temporary_text_adapter_needs_only_xyz(self):
        rules = parse_rule_text("建立一個 4 x 3 x 2 戰場")
        self.assertEqual(rules.dimensions, (4, 3, 2))
        with self.assertRaises(RuleInputError):
            parse_rule_text("建立一個空間戰場")


class BattlefieldTests(unittest.TestCase):
    def test_cube_is_real_xyz_array_and_state_is_rule_neutral(self):
        board = Battlefield(GridRules(3, 3, 3))
        self.assertEqual(board.cells.shape, (3, 3, 3))
        self.assertEqual(board.get_cell((2, 1, 0)), 0)
        board.set_cell((2, 1, 0), 17)
        self.assertEqual(int(board.cells[2, 1, 0]), 17)
        self.assertEqual(board.last_evaluation.status, "unresolved")

    def test_rectangular_topology_has_no_hardcoded_dimension_or_turn(self):
        board = Battlefield(GridRules(4, 3, 2))
        self.assertEqual(board.cells.shape, (4, 3, 2))
        self.assertEqual(len(board.coordinates()), 24)
        board.set_cell((3, 2, 1), 1)
        board.set_cell((3, 2, 1), 2)  # STAL permits overwrite; SRTP may forbid it.
        self.assertEqual(board.get_cell((3, 2, 1)), 2)

    def test_coordinate_codec_is_lossless_for_every_cell(self):
        board = Battlefield(GridRules(4, 3, 2))
        indices = {board.action_codec.encode(coordinate) for coordinate in board.coordinates()}
        self.assertEqual(indices, set(range(24)))
        self.assertEqual(board.action_codec.decode(23), (3, 2, 1))
        self.assertEqual(board.coordinate_indices(), list(range(24)))

    def test_write_validity_checks_topology_not_game_rules(self):
        board = Battlefield(GridRules(3, 3, 3))
        self.assertTrue(board.is_valid_write((1, 1, 1), 1))
        self.assertTrue(board.is_valid_write((1, 1, 1), 99))
        self.assertFalse(board.is_valid_write((3, 1, 1), 1))
        self.assertFalse(board.is_valid_write((1, 1, 1), "occupied"))
        board.register_write_validator(lambda _, coordinate, __: "centre is reserved" if coordinate == (1, 1, 1) else None)
        self.assertFalse(board.is_valid_write((1, 1, 1), 1))
        with self.assertRaisesRegex(MoveError, "centre is reserved"):
            board.set_cell((1, 1, 1), 1)

    def test_rule_layer_can_supply_its_own_evaluation(self):
        board = Battlefield(GridRules(3, 3, 3))
        board.register_evaluation_hook(
            lambda state: RuleEvaluation(status="custom_goal", reason="SRTP-defined example")
            if state.get_cell((0, 0, 0)) == 7 else None
        )
        result = board.set_cell((0, 0, 0), 7)
        self.assertEqual(result.status, "custom_goal")
        self.assertEqual(result.reason, "SRTP-defined example")

    def test_topology_assessment_warns_without_assuming_state_space(self):
        board = Battlefield(GridRules(9, 9, 9))
        assessment = board.assess_topology()
        self.assertEqual(assessment.cell_count, 729)
        self.assertEqual(assessment.coordinate_count, 729)
        self.assertFalse(assessment.is_recommended)
        self.assertTrue(any("viewport" in warning for warning in assessment.warnings))

    def test_example_json_is_valid_topology_contract(self):
        path = Path(__file__).parents[1] / "stal" / "examples" / "tictactoe_3x3x3.json"
        with path.open(encoding="utf-8") as file:
            rules = GridRules.from_mapping(json.load(file))
        self.assertEqual(rules.to_mapping()["dimensions"], {"x": 3, "y": 3, "z": 3})


if __name__ == "__main__":
    unittest.main()
