"""The four original SRTP demonstrations, presented as an explicit library.

These entries select existing sources and their registered adapters. They are
never a fallback for a rejected generated Project Manifest.
"""
from pathlib import Path

SOURCES = Path(__file__).resolve().parents[1] / 'reference_games'
DEMOS = (
    ('3D Snake', SOURCES / 'pygame_snake/snake.py'),
    ('3D Minesweeper', SOURCES / 'pygame_minesweeper/run_game.py'),
    ('3D Connect Four', SOURCES / 'turtle_connect_complete/connect_complete.py'),
    ('3D 2048', SOURCES / 'pygame_2048/main.py'),
)
