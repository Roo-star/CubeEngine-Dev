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

    def test_complete_pygame_snake_extracts_keys_assets_tick_and_adapter(self):
        package = self.importer.import_path(REFERENCE_ROOT / "pygame_snake" / "snake.py")

        self.assertTrue(package.runtime.runnable)
        self.assertEqual(package.transformation.source_dimensions, {"x": 20, "y": 20, "z": 1})
        self.assertEqual(package.transformation.adapter_id, "snake")
        self.assertEqual(package.parameter("source_tick_ms").value, 125)
        self.assertGreater(len(package.assets), 10)
        controls = package.rule_report.schema["ui_hints"]["controls"]
        self.assertIn("Arrow Up", {item["input"] for item in controls})
        self.assertEqual({item["id"] for item in package.rule_report.schema["actions"]}, {"change_direction", "advance"})

    def test_classic_pygame_minesweeper_extracts_click_flag_and_first_class_adapter(self):
        package = self.importer.import_path(REFERENCE_ROOT / "pygame_minesweeper" / "run_game.py")

        self.assertTrue(package.runtime.runnable)
        self.assertEqual(package.transformation.source_dimensions, {"x": 10, "y": 10, "z": 1})
        self.assertEqual(package.transformation.adapter_id, "minesweeper")
        self.assertEqual(package.parameter("source_mine_count").value, 10)
        self.assertEqual(
            {item["id"] for item in package.rule_report.schema["actions"]},
            {"reveal_cell", "toggle_flag"},
        )
        controls = package.rule_report.schema["ui_hints"]["controls"]
        self.assertIn("Right click", {item["input"] for item in controls})
        self.assertEqual(
            {item["id"] for item in package.rule_report.schema["outcomes"]},
            {"mine_loss", "safe_cells_cleared"},
        )

    def test_complete_turtle_connect_proves_source_dimensions_and_terminal_rule(self):
        package = self.importer.import_path(
            REFERENCE_ROOT / "turtle_connect_complete" / "connect_complete.py"
        )

        self.assertTrue(package.runtime.runnable)
        self.assertEqual(package.transformation.source_dimensions, {"x": 7, "y": 6, "z": 1})
        self.assertEqual(package.transformation.adapter_id, "connect")
        self.assertEqual(package.transformation.readiness, "ready")
        self.assertEqual(package.parameter("source_connect_n").value, 4)
        self.assertEqual(package.rule_report.schema["outcomes"][0]["condition"]["length"], 4)
        self.assertTrue(package.rule_report.schema["outcomes"][0]["executable"])

    def test_source_visuals_expose_a_traceable_3d_presentation_mapping(self):
        cases = (
            ("pygame_snake/snake.py", "source_sprite_surface_projection"),
            ("pygame_minesweeper/run_game.py", "source_spritesheet_cube_faces"),
            ("pygame_2048/main.py", "source_palette_generated_cube_faces"),
        )
        for relative, strategy in cases:
            package = self.importer.import_path(REFERENCE_ROOT / relative)
            mapping = package.rule_report.schema["ui_hints"]["presentation_mapping"]
            self.assertEqual(mapping["strategy"], strategy)
            self.assertTrue(mapping["mapped_roles"])

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

    def test_coordinate_guard_proves_board_size_for_oop_project(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "main.py").write_text(
                "class Board:\n"
                "    def checkCoordRange(self, x, y):\n"
                "        return x >= 0 and x < 8 and y >= 0 and y < 8\n",
                encoding="utf-8",
            )

            package = self.importer.import_path(root / "main.py")

            self.assertEqual(package.transformation.source_dimensions, {"x": 8, "y": 8, "z": 1})

    def test_whole_project_llm_handoff_contains_runtime_and_acceptance_gate(self):
        package = self.importer.import_path(REFERENCE_ROOT / "pygame_snake" / "snake.py")

        handoff = package.llm_handoff()

        self.assertEqual(handoff["handoff_version"], "cubeengine.srtp/source-project-llm-handoff-v2")
        self.assertIn("runtime", handoff["source_project"])
        self.assertIn("transformation_gaps", handoff)
        self.assertTrue(handoff["acceptance_gate"]["z_equals_one_must_match_source"])
        self.assertIn("getValidMoves", handoff["static_analysis"]["downstream_game_api"])

    def test_nested_src_entry_keeps_project_root_for_source_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "game"
            (root / "src").mkdir(parents=True)
            (root / "images").mkdir()
            (root / "requirements.txt").write_text("pygame\n", encoding="utf-8")
            (root / "src" / "main.py").write_text(
                "import pygame\npygame.image.load('images/board.png')\n", encoding="utf-8"
            )
            (root / "images" / "board.png").write_bytes(b"image")

            package = self.importer.import_path(root)

            self.assertEqual(package.root, root.resolve())
            self.assertEqual(package.entrypoint, (root / "src" / "main.py").resolve())
            self.assertEqual(Path(package.runtime.cwd), root.resolve())

    def test_namespace_package_main_preserves_python_module_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "game"
            package_dir = root / "falling_blocks"
            package_dir.mkdir(parents=True)
            (root / "pyproject.toml").write_text("[project]\nname='falling-blocks'\n", encoding="utf-8")
            (package_dir / "__main__.py").write_text("import pygame\nfrom .game import run\n", encoding="utf-8")
            (package_dir / "game.py").write_text("def run(): pass\n", encoding="utf-8")

            package = self.importer.import_path(root)

            self.assertEqual(package.runtime.command[-2:], ["--module", "falling_blocks.__main__"])

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

    def test_proven_timer_literal_creates_playable_source_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "free_python_games"
            shutil.copytree(str(REFERENCE_ROOT / "free_python_games"), str(source))
            original_path = source / "freegames" / "snake.py"
            original = original_path.read_text(encoding="utf-8")
            package = self.importer.import_path(original_path)
            parameter = package.parameter("source_tick_ms")

            variant_entry = SourceVariantBuilder().create(package, parameter, "160")

            self.assertIn("ontimer(move, 160)", variant_entry.read_text(encoding="utf-8"))
            self.assertEqual(original_path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
