"""Tic Tac Toe.

Complete two-player 2D game. Click to place; R restarts; Esc quits.
"""

BOARD_WIDTH = 3
BOARD_HEIGHT = 3
CONNECT_N = 3
CELL_SIZE = 140
WINDOW_WIDTH = 480
WINDOW_HEIGHT = 570
BOARD_LEFT = 30
BOARD_TOP = 100


class TicTacToe:
    def __init__(self):
        self.reset()

    def reset(self):
        self.board = [[0 for _ in range(BOARD_WIDTH)] for _ in range(BOARD_HEIGHT)]
        self.current_player = 1
        self.winner = 0
        self.draw = False

    def place(self, x, y):
        if self.winner or self.draw or not (0 <= x < BOARD_WIDTH and 0 <= y < BOARD_HEIGHT):
            return False
        if self.board[y][x] != 0:
            return False
        self.board[y][x] = self.current_player
        lines = list(self.board) + [[self.board[y][x] for y in range(3)] for x in range(3)]
        lines += [[self.board[i][i] for i in range(3)], [self.board[i][2-i] for i in range(3)]]
        if any(all(cell == self.current_player for cell in line) for line in lines):
            self.winner = self.current_player
        elif all(cell for row in self.board for cell in row):
            self.draw = True
        else:
            self.current_player = 3 - self.current_player
        return True


def main():
    import pygame
    pygame.init()
    screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
    pygame.display.set_caption('Tic Tac Toe - Complete 2D Source')
    font = pygame.font.Font(None, 36)
    small = pygame.font.Font(None, 25)
    clock = pygame.time.Clock()
    game = TicTacToe()
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_r:
                    game.reset()
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mx, my = event.pos
                game.place((mx - BOARD_LEFT) // CELL_SIZE, (my - BOARD_TOP) // CELL_SIZE)
        screen.fill((22, 28, 40))
        symbol = lambda p: 'X' if p == 1 else 'O'
        status = ('Winner: ' + symbol(game.winner)) if game.winner else ('Draw' if game.draw else 'Turn: ' + symbol(game.current_player))
        screen.blit(font.render(status, True, (230, 235, 245)), (30, 30))
        screen.blit(small.render('Click an empty cell. R: restart. Esc: quit.', True, (170, 185, 205)), (30, 65))
        for y in range(3):
            for x in range(3):
                rect = pygame.Rect(BOARD_LEFT+x*CELL_SIZE+4, BOARD_TOP+y*CELL_SIZE+4, CELL_SIZE-8, CELL_SIZE-8)
                pygame.draw.rect(screen, (42, 54, 72), rect, border_radius=12)
                player = game.board[y][x]
                if player == 1:
                    pygame.draw.line(screen, (70, 190, 250), (rect.left+30, rect.top+30), (rect.right-30, rect.bottom-30), 8)
                    pygame.draw.line(screen, (70, 190, 250), (rect.right-30, rect.top+30), (rect.left+30, rect.bottom-30), 8)
                elif player == 2:
                    pygame.draw.circle(screen, (250, 170, 75), rect.center, 40, 8)
        pygame.display.flip()
        clock.tick(60)
    pygame.quit()


if __name__ == '__main__':
    main()
