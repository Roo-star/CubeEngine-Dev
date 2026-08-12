"""Visual-fidelity tests that do not require opening a GPU window."""

import unittest
from pathlib import Path

from srtp.presentation_bridge import discover_presentation


REFERENCE_ROOT = Path(__file__).resolve().parents[1] / "srtp" / "reference_games"


class PresentationBridgeTests(unittest.TestCase):
    def test_minesweeper_uses_real_source_sprites_for_every_visible_state(self):
        profile = discover_presentation("minesweeper", REFERENCE_ROOT / "pygame_minesweeper")

        covered = profile.image_for("covered")
        flag = profile.image_for("flag")
        three = profile.image_for("3")

        self.assertEqual(profile.strategy, "source_spritesheet_cube_faces")
        self.assertEqual(covered.size, (16, 16))
        self.assertNotEqual(covered.tobytes(), flag.tobytes())
        self.assertNotEqual(covered.tobytes(), three.tobytes())

    def test_2048_combines_number_and_source_colour_into_one_cell_texture(self):
        profile = discover_presentation("2048", REFERENCE_ROOT / "pygame_2048")

        two = profile.image_for("tile", 2)
        four = profile.image_for("tile", 4)

        self.assertEqual(profile.strategy, "source_palette_generated_cube_faces")
        self.assertEqual(two.size, (128, 128))
        self.assertNotEqual(two.tobytes(), four.tobytes())

    def test_snake_maps_source_head_body_tail_and_food_assets(self):
        profile = discover_presentation("snake", REFERENCE_ROOT / "pygame_snake")

        self.assertEqual(profile.strategy, "source_sprite_surface_projection")
        self.assertIsNotNone(profile.image_for("head_right"))
        self.assertIsNotNone(profile.image_for("body_horizontal"))
        self.assertIsNotNone(profile.image_for("tail_left"))
        self.assertIsNotNone(profile.image_for("food"))


if __name__ == "__main__":
    unittest.main()
