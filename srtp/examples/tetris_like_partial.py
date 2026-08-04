"""Shows Function 1's boundary: geometry is explicit, timing/physics need semantic conversion."""

import random

BOARD_WIDTH = 10
BOARD_HEIGHT = 20
TICK_RATE = 60


def create_board():
    return [[0 for _ in range(BOARD_WIDTH)] for _ in range(BOARD_HEIGHT)]


def move_object(board, falling_piece, direction):
    """Collision, wall kicks and locking are intentionally hidden behind source logic."""
    return falling_piece.try_translate(direction, board)


def update(board, falling_piece):
    if not move_object(board, falling_piece, (0, 1)):
        falling_piece.lock_into(board)
        return random.choice(("I", "O", "T", "S", "Z", "J", "L"))
    return falling_piece
