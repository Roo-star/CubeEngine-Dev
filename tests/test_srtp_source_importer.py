"""Acceptance tests against complete third-party source games."""

import tempfile
import unittest
import shutil
import json
from pathlib import Path

from srtp.source_importer import SourceGameImporter
from srtp.variant import SourceVariantBuilder


REFERENCE_ROOT = Path(__file__).resolve().parents[1] / "srtp" / "reference_games"


class SourceGameImporterTests(unittest.TestCase):
    def setUp(self):
        self.importer = SourceGameImporter()

    def test_snake_is_a_runnable_tick_game_with_source_lattice_and_collision(self):
        package = self.importer.import_path(
            REFERENCE_ROOT / "free_python_games" / "freegames" / "snake.py"
        )

        self.assertTrue(package.runtime.runnable)
        self.assertEqual(package.runtime.command[-2:], ["-m", "freegames.snake"])
        self.assertEqual(package.transformation.source_dimensions, {"x": 38, "y": 38, "z": 1})
        self.assertEqual(package.rule_report.schema["flow"]["tick_rate"], 10.0)
        self.assertEqual(
            {item["id"] for item in package.rule_report.schema["actions"]},
            {"change_direction", "advance"},
        )
        self.assertIn("collision_loss", {item["id"] for item in package.rule_report.schema["outcomes"]})

    def test_minesweeper_distinguishes_playable_surface_from_padded_internal_state(self):
        package = self.importer.import_path(
            REFERENCE_ROOT / "free_python_games" / "freegames" / "minesweeper.py"
        )

        self.assertEqual(package.transformation.source_dimensions, {"x": 8, "y": 8, "z": 1})
        self.assertEqual(package.rule_report.schema["space"]["coordinate_anchor"], "cell_center")
        self.assertEqual(package.rule_report.schema["state"]["information"], "hidden")
        self.assertEqual(package.rule_report.schema["actions"][0]["id"], "reveal_cell")
        self.assertEqual(package.parameter("source_mine_count").value, 8)
        self.assertEqual(package.coverage.categories["goals/outcomes"], "partial")

    def test_multifile_2048_preserves_assets_and_reads_move_randomness_and_outcomes(self):
        package = self.importer.import_path(REFERENCE_ROOT / "pygame_2048" / "main.py")

        self.assertTrue(package.runtime.runnable)
        self.assertEqual(package.runtime.framework, "pygame")
        self.assertEqual(package.transformation.source_dimensions, {"x": 4, "y": 4, "z": 1})
        self.assertEqual(package.rule_report.schema["actions"][0]["id"], "shift_merge")
        self.assertEqual(package.rule_report.schema["randomness"]["model"], "stochastic")
        self.assertEqual(
            {item["id"] for item in package.rule_report.schema["outcomes"]},
            {"source_win", "source_loss"},
        )
        self.assertGreaterEqual(len(package.assets), 3)
        self.assertTrue(package.parameter("source_visual_size").safely_editable)
        self.assertEqual(package.coverage.categories["modes"], "proven")

    def test_static_project_import_does_not_execute_user_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "executed.txt"
            (root / "main.py").write_text(
                "from pathlib import Path\nPath({0!r}).write_text('bad')\nBOARD_WIDTH=3\nBOARD_HEIGHT=4\n".format(str(marker)),
                encoding="utf-8",
            )

            package = self.importer.import_path(root / "main.py")

            self.assertFalse(marker.exists())
            self.assertEqual(package.runtime.kind, "python")

    def test_safe_data_setting_creates_variant_without_modifying_upstream(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "pygame_2048"
            shutil.copytree(str(REFERENCE_ROOT / "pygame_2048"), str(source))
            original = json.loads((source / "constants.json").read_text(encoding="utf-8"))
            package = self.importer.import_path(source / "main.py")

            variant_entry = SourceVariantBuilder().create(
                package, package.parameter("source_visual_size"), "640"
            )

            variant = json.loads((variant_entry.parent / "constants.json").read_text(encoding="utf-8"))
            unchanged = json.loads((source / "constants.json").read_text(encoding="utf-8"))
            self.assertEqual(variant["size"], 640)
            self.assertEqual(unchanged, original)


if __name__ == "__main__":
    unittest.main()
