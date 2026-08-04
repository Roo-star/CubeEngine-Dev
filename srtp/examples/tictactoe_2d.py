"""Static-analysis fixture. Importing this file is not required by SRTP."""

BOARD_WIDTH = 3
BOARD_HEIGHT = 3
NUM_PLAYERS = 2
COORDINATE_ANCHOR = "cell_center"
WIN_LENGTH = 3


def create_board():
    return [[0 for _ in range(BOARD_WIDTH)] for _ in range(BOARD_HEIGHT)]


def set_cell(board, x, y, player):
    board[y][x] = player


def is_valid_move(board, x, y):
    return 0 <= x < BOARD_WIDTH and 0 <= y < BOARD_HEIGHT and board[y][x] == 0


def check_winner(board):
    lines = []
    for row in range(BOARD_HEIGHT):
        lines.append([board[row][column] for column in range(BOARD_WIDTH)])
    for column in range(BOARD_WIDTH):
        lines.append([board[row][column] for row in range(BOARD_HEIGHT)])
    lines.append([board[index][index] for index in range(BOARD_WIDTH)])
    lines.append([board[index][BOARD_WIDTH - 1 - index] for index in range(BOARD_WIDTH)])
    for line in lines:
        if line[0] != 0 and all(value == line[0] for value in line):
            return line[0]
    return 0


def is_full(board):
    return all(value != 0 for row in board for value in row)
