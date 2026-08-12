"""Complete Turtle Connect Four reference used for SRTP outcome acceptance.

The drawing/input vocabulary follows Grant Jenks' Apache-2.0 Free Python Games
Connect exercise, while this source variant completes its documented winner
and full-board exercises so Function 1 has a real terminal rule to read.
"""

from turtle import Turtle, Screen


COLUMNS = 7
ROWS = 6
CONNECT_N = 4
CELL_SIZE = 56
BOARD_WIDTH = COLUMNS * CELL_SIZE
BOARD_HEIGHT = ROWS * CELL_SIZE

screen = Screen()
screen.setup(BOARD_WIDTH + 80, BOARD_HEIGHT + 120)
screen.title("Connect Four — complete Turtle source")
screen.bgcolor("#1f6fce")
screen.tracer(False)

drawer = Turtle(visible=False)
drawer.penup()
status_writer = Turtle(visible=False)
status_writer.penup()

state = {
    "player": "yellow",
    "board": {},
    "status": "playing",
}


def cell_center(column, row):
    left = -BOARD_WIDTH / 2
    bottom = -BOARD_HEIGHT / 2
    return (
        left + column * CELL_SIZE + CELL_SIZE / 2,
        bottom + row * CELL_SIZE + CELL_SIZE / 2,
    )


def grid():
    """Draw the source board and its empty circular sockets."""

    drawer.clear()
    for column in range(COLUMNS):
        for row in range(ROWS):
            x, y = cell_center(column, row)
            drawer.goto(x, y - CELL_SIZE * 0.38)
            drawer.dot(CELL_SIZE * 0.76, "#eef4fb")
    redraw_pieces()
    draw_status("Yellow to move")


def redraw_pieces():
    for (column, row), player in state["board"].items():
        x, y = cell_center(column, row)
        drawer.goto(x, y - CELL_SIZE * 0.38)
        drawer.dot(CELL_SIZE * 0.72, player)


def draw_status(message):
    status_writer.clear()
    status_writer.goto(0, BOARD_HEIGHT / 2 + 22)
    status_writer.color("white")
    status_writer.write(message, align="center", font=("Arial", 15, "bold"))
    screen.update()


def check_winner(board, player):
    """Return True when player has CONNECT_N contiguous source pieces."""

    directions = ((1, 0), (0, 1), (1, 1), (1, -1))
    for column, row in board:
        if board.get((column, row)) != player:
            continue
        for dx, dy in directions:
            if all(board.get((column + dx * step, row + dy * step)) == player for step in range(CONNECT_N)):
                return True
    return False


def is_full(board):
    return len(board) == COLUMNS * ROWS


def tap(x, _y):
    """Drop a piece in the clicked column, then evaluate win/draw."""

    if state["status"] != "playing":
        return
    column = int((x + BOARD_WIDTH / 2) // CELL_SIZE)
    if not 0 <= column < COLUMNS:
        return
    row = next((candidate for candidate in range(ROWS) if (column, candidate) not in state["board"]), None)
    if row is None:
        draw_status("That column is full")
        return

    player = state["player"]
    state["board"][(column, row)] = player
    x_center, y_center = cell_center(column, row)
    drawer.goto(x_center, y_center - CELL_SIZE * 0.38)
    drawer.dot(CELL_SIZE * 0.72, player)

    if check_winner(state["board"], player):
        state["status"] = "win"
        draw_status(player.title() + " wins — click R to restart")
    elif is_full(state["board"]):
        state["status"] = "draw"
        draw_status("Draw — click R to restart")
    else:
        state["player"] = "red" if player == "yellow" else "yellow"
        draw_status(state["player"].title() + " to move")


def reset():
    state["player"] = "yellow"
    state["board"].clear()
    state["status"] = "playing"
    grid()


grid()
screen.onclick(tap)
screen.onkey(reset, "r")
screen.onkey(reset, "R")
screen.listen()
screen.mainloop()
