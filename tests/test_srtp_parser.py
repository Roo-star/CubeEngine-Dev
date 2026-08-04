"""SRTP Function 1 parser, evidence and boundary tests."""

import json
import tempfile
import unittest
from pathlib import Path

from srtp import RULE_SCHEMA_VERSION, RuleFileParser, parse_rule_file


EXAMPLES = Path(__file__).parents[1] / "srtp" / "examples"


class SrtpParserTests(unittest.TestCase):
    def test_generic_json_fills_complete_canonical_schema(self):
        report = parse_rule_file(EXAMPLES / "placement_line_3x3.json")

        self.assertEqual(report.readiness, "complete")
        self.assertTrue(report.previewable)
        self.assertEqual(report.schema["schema_version"], RULE_SCHEMA_VERSION)
        self.assertEqual(report.schema["space"]["dimensions"], {"x": 3, "y": 3, "z": 1})
        self.assertEqual(report.schema["space"]["coordinate_anchor"], "cell_center")
        self.assertEqual([item["id"] for item in report.schema["participants"]], ["cross", "circle"])
        self.assertEqual([item["state_value"] for item in report.schema["entities"]], [1, 2])
        self.assertEqual(report.schema["actions"][0]["verb"], "place")
        self.assertEqual([item["condition"]["op"] for item in report.schema["outcomes"]], ["line", "all_cells_not_equal"])
        self.assertIn("space.dimensions.x", report.provenance)
        self.assertEqual(len(report.schema["source"]["sha256"]), 64)

    def test_json_rectangular_array_shape_is_used_as_logical_grid(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "array_game.json"
            path.write_text(json.dumps({
                "name": "Array Game",
                "board": [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]],
                "single_player": True,
                "placement": "cell",
                "turn_based": True,
                "actions": [{"id": "choose", "verb": "select"}],
            }), encoding="utf-8")
            report = parse_rule_file(path)

        self.assertEqual(report.schema["space"]["dimensions"], {"x": 4, "y": 3, "z": 1})
        self.assertEqual(report.provenance["space.dimensions.x"][0].method, "array_shape")

    def test_ascii_level_map_and_pixel_ratio_can_prove_grid_units(self):
        parser = RuleFileParser()
        with tempfile.TemporaryDirectory() as folder:
            level_path = Path(folder) / "level.json"
            level_path.write_text(json.dumps({
                "name": "ASCII Level", "level": ["....#", "..@.#", "#####"],
                "single_player": True, "placement": "cell", "actions": ["select"], "turn_based": True,
            }), encoding="utf-8")
            level_report = parser.parse(level_path)
            pixel_path = Path(folder) / "pixel_board.json"
            pixel_path.write_text(json.dumps({
                "name": "Pixel Board", "board": {
                    "width": 300, "height": 600, "unit": "pixels", "cell_size": 30, "placement": "cell"
                },
                "single_player": True, "turn_based": True, "actions": ["select"],
            }), encoding="utf-8")
            pixel_report = parser.parse(pixel_path)

        self.assertEqual(level_report.schema["space"]["dimensions"], {"x": 5, "y": 3, "z": 1})
        self.assertEqual(pixel_report.schema["space"]["dimensions"], {"x": 10, "y": 20, "z": 1})
        self.assertEqual(pixel_report.provenance["space.dimensions.x"][0].method, "derived_pixel_ratio")

    def test_literal_nested_board_constructor_proves_dimensions(self):
        source = '''
NUM_PLAYERS = 2
COORDINATE_ANCHOR = "cell"
def create_board():
    return [[0 for x in range(7)] for y in range(5)]
def set_cell(board, x, y, player):
    board[y][x] = player
def is_valid_move(board, x, y):
    return board[y][x] == 0
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "constructor.py"
            path.write_text(source, encoding="utf-8")
            report = parse_rule_file(path)

        self.assertEqual(report.schema["space"]["dimensions"], {"x": 7, "y": 5, "z": 1})
        self.assertEqual(report.provenance["space.dimensions.x"][0].detail, "nested board list-comprehension bounds")

    def test_python_is_analysed_without_executing_top_level_code(self):
        source = '''
raise RuntimeError("this must never execute")
BOARD_WIDTH = 4
BOARD_HEIGHT = 3
NUM_PLAYERS = 2
COORDINATE_ANCHOR = "cell_center"
def set_cell(board, x, y, player):
    board[y][x] = player
def is_valid_move(board, x, y):
    return board[y][x] == 0
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "unsafe_if_imported.py"
            path.write_text(source, encoding="utf-8")
            report = parse_rule_file(path)

        self.assertEqual(report.schema["space"]["dimensions"], {"x": 4, "y": 3, "z": 1})
        self.assertTrue(report.schema["actions"][0]["executable"])
        self.assertTrue(any(item.code == "python.not_executed" for item in report.diagnostics))

    def test_recognised_python_fixture_is_directly_previewable(self):
        report = parse_rule_file(EXAMPLES / "tictactoe_2d.py")

        self.assertEqual(report.readiness, "complete")
        self.assertTrue(report.previewable)
        self.assertEqual(report.schema["flow"]["model"], "turn_based")
        self.assertTrue(all(item["executable"] for item in report.schema["outcomes"]))

    def test_complex_python_keeps_geometry_but_routes_semantics_to_llm(self):
        report = parse_rule_file(EXAMPLES / "tetris_like_partial.py")

        self.assertEqual(report.schema["space"]["dimensions"], {"x": 10, "y": 20, "z": 1})
        self.assertEqual(report.schema["flow"]["model"], "tick_based")
        self.assertEqual(report.schema["randomness"]["model"], "stochastic")
        self.assertEqual(report.schema["actions"][0]["verb"], "move")
        self.assertFalse(report.schema["actions"][0]["executable"])
        self.assertEqual(report.readiness, "partial")
        self.assertFalse(report.previewable)
        self.assertTrue(report.needs_llm)
        self.assertTrue(report.llm_handoff()["unresolved"])

    def test_missing_grid_is_not_guessed_and_can_be_designer_corrected(self):
        source = '''
NUM_PLAYERS = 2
def set_cell(board, x, y, player):
    board[y][x] = player
def is_valid_move(board, x, y):
    return board[y][x] == 0
'''
        parser = RuleFileParser()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "missing_size.py"
            path.write_text(source, encoding="utf-8")
            report = parser.parse(path)
            self.assertEqual(report.readiness, "blocked")
            report = parser.apply_overrides(report, {
                "space.dimensions.x": 8,
                "space.dimensions.y": 6,
                "space.dimensions.z": 1,
                "space.coordinate_anchor": "cell_center",
                "flow.model": "turn_based",
            })

        self.assertEqual(report.schema["space"]["dimensions"], {"x": 8, "y": 6, "z": 1})
        self.assertFalse(any(item.code == "python.grid_unresolved" for item in report.diagnostics))
        self.assertEqual(report.provenance["space.dimensions.x"][-1].method, "designer_override")
        self.assertTrue(report.previewable)

    def test_unsupported_script_format_produces_function2_handoff(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rules.lua"
            path.write_text("board = make_board()", encoding="utf-8")
            report = parse_rule_file(path)

        self.assertEqual(report.readiness, "blocked")
        self.assertTrue(report.needs_llm)
        self.assertTrue(any(item.code == "source.unsupported_format" for item in report.diagnostics))

    def test_schema_document_is_valid_json_and_matches_version(self):
        path = Path(__file__).parents[1] / "srtp" / "rule-schema-v1.schema.json"
        schema_document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(schema_document["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema_document["properties"]["schema_version"]["const"], RULE_SCHEMA_VERSION)

    def test_malformed_canonical_section_types_return_diagnostics_not_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "malformed.json"
            path.write_text(json.dumps({
                "schema_version": RULE_SCHEMA_VERSION,
                "game": "not an object",
                "space": "not an object",
                "state": "not an object",
                "flow": "not an object",
                "randomness": "not an object",
                "actions": "not a list"
            }), encoding="utf-8")
            report = parse_rule_file(path)

        self.assertEqual(report.readiness, "blocked")
        self.assertTrue(any(item.code in ("field.object", "field.list") for item in report.diagnostics))


if __name__ == "__main__":
    unittest.main()
