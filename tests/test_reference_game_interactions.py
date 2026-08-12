"""Interaction acceptance tests for the bundled playable source references."""

import json
import os
import sys
import unittest
from pathlib import Path


REFERENCE_ROOT = Path(__file__).resolve().parents[1] / "srtp" / "reference_games"


class ReferenceGameInteractionTests(unittest.TestCase):
    def test_minesweeper_source_ui_routes_right_flag_and_left_flood_reveal(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        root = REFERENCE_ROOT / "pygame_minesweeper"
        sys.path.insert(0, str(root))
        try:
            import pygame
            from minesweeper.user_interface import UserInterface

            pygame.init()
            ui = UserInterface(10, 10, 10, lambda _seconds: None)
            board_ui = ui._components[0]

            ui.event_handler(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=3, pos=(17, 70)))
            self.assertEqual(board_ui.flagged(), 1)
            ui.event_handler(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=3, pos=(17, 70)))
            ui.event_handler(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=3, pos=(17, 70)))
            self.assertEqual(board_ui.flagged(), 0)

            ui.event_handler(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(17, 70)))
            ui.event_handler(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(17, 70)))
            self.assertGreater(ui._board._opened, 0)
            self.assertFalse(ui._board.is_game_over)
        finally:
            try:
                pygame.quit()
            except UnboundLocalError:
                pass
            sys.path.remove(str(root))

    def test_2048_source_accepts_current_pygame_arrow_codes(self):
        constants = json.loads((REFERENCE_ROOT / "pygame_2048" / "constants.json").read_text(encoding="utf-8"))

        self.assertEqual(constants["keys"]["1073741906"], "w")
        self.assertEqual(constants["keys"]["1073741905"], "s")
        self.assertEqual(constants["keys"]["1073741904"], "a")
        self.assertEqual(constants["keys"]["1073741903"], "d")


if __name__ == "__main__":
    unittest.main()
