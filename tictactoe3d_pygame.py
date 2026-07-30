import json
import os
import random
import sys
import time

import pygame

from tictactoe3d_logic import (
    create_board,
    game_status,
    get_cell,
    get_legal_moves,
    is_valid_move,
    set_cell,
)
from tictactoe3d_nnet import NNetWrapper  # 引入你的 AI 包裝層
from game_for_training import TicTacToe3DGame  # 引入遊戲規則層
from MCTS import MCTS
from utils import dotdict
import numpy as np

# -----------------------------
# 畫面常數設定
# -----------------------------
WINDOW_WIDTH = 980
WINDOW_HEIGHT = 620
FPS = 60

GRID_SIZE = 3
CELL_SIZE = 86
LAYER_GAP = 36
TOP_MARGIN = 120
LEFT_MARGIN = 56

LAYER_PIXEL = GRID_SIZE * CELL_SIZE
RESTART_BTN_RECT = pygame.Rect(WINDOW_WIDTH - 190, 20, 150, 42)
MENU_BTN_RECT = pygame.Rect(WINDOW_WIDTH - 190, 72, 150, 42)

# 主選單按鈕
FIRST_HUMAN_RECT = pygame.Rect(280, 200, 180, 48)
FIRST_AI_RECT = pygame.Rect(520, 200, 180, 48)
DIFF_EASY_RECT = pygame.Rect(220, 320, 150, 48)
DIFF_MED_RECT = pygame.Rect(415, 320, 150, 48)
DIFF_HARD_RECT = pygame.Rect(610, 320, 150, 48)
START_BTN_RECT = pygame.Rect(365, 430, 250, 56)

SCREEN_MENU = "menu"
SCREEN_GAME = "game"

# 難度 → MCTS temperature（越高越隨機＝越容易）
DIFFICULTY_TEMP = {
    "easy": 1.0,
    "medium": 0.5,
    "hard": 0.0,
}
DIFFICULTY_LABEL = {
    "easy": "簡單",
    "medium": "中等",
    "hard": "困難",
}

BG_COLOR = (245, 247, 250)
TEXT_COLOR = (30, 33, 38)
GRID_COLOR = (90, 100, 115)
CELL_BG = (255, 255, 255)
X_COLOR = (45, 120, 220)
O_COLOR = (220, 80, 80)
HIGHLIGHT_COLOR = (44, 170, 100)
BTN_COLOR = (70, 110, 190)
BTN_SELECTED = (44, 170, 100)
BTN_TEXT_COLOR = (255, 255, 255)
BTN_MUTED = (150, 160, 175)

args = dotdict({
    'num_channels': 128,
    'dropout': 0.3,
    'numMCTSSims': 400,
    'cpuct': 1.2,
})

_ai_engine = None


def _debug_log(hypothesis_id, location, message, data, run_id='pre-fix'):
    # region agent log
    try:
        with open('debug-d98ade.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                'sessionId': 'd98ade',
                'runId': run_id,
                'hypothesisId': hypothesis_id,
                'location': location,
                'message': message,
                'data': data,
                'timestamp': int(time.time() * 1000),
            }) + '\n')
    except OSError:
        pass
    # endregion


def _get_ai_engine():
    """Lazy-init and reuse game, network, and MCTS across AI turns."""
    global _ai_engine
    if _ai_engine is None:
        game = TicTacToe3DGame()
        nnet = NNetWrapper(game, args)

        ckpt_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'checkpoints')
        nnet.load_checkpoint(ckpt_dir, 'best.pth.tar')

        _ai_engine = {
            'game': game,
            'nnet': nnet,
            'args': args,
        }
    return _ai_engine


def ai_move(board, player, temp=0.0):
    """AI move using MCTS + trained network. temp>0 softens play (easier)."""
    engine = _get_ai_engine()
    game = engine['game']
    nnet = engine['nnet']

    canonical_board = game.getCanonicalForm(board, player)
    valids = game.getValidMoves(canonical_board, 1)

    raw_pi, _ = nnet.predict(canonical_board)
    raw_pi = raw_pi * valids
    raw_action = int(np.argmax(raw_pi))

    mcts = MCTS(game, nnet, args)
    pi = mcts.getActionProb(canonical_board, temp=temp)
    pi = np.asarray(pi, dtype=float) * valids
    sum_pi = float(np.sum(pi))
    if sum_pi <= 0:
        action = int(np.argmax(valids))
    elif temp == 0:
        action = int(np.argmax(pi))
    else:
        pi = pi / sum_pi
        action = int(np.random.choice(len(pi), p=pi))

    # region agent log
    top3 = np.argsort(pi)[-3:][::-1].tolist()
    _debug_log('H2', 'tictactoe3d_pygame.py:ai_move', 'ai move with temp', {
        'temp': temp,
        'chosen_action': action,
        'raw_chosen_action': raw_action,
        'mcts_top3': [{'action': int(a), 'prob': float(pi[a])} for a in top3],
    })
    # endregion

    x = action // 9
    y = (action % 9) // 3
    z = action % 3
    return (x, y, z)


def load_font(size, bold=False):
    """
    安全載入字型：
    - 優先直接讀取 Windows 常見中文字型檔，避免 SysFont 在部分環境崩潰。
    - 若都找不到，退回 pygame 預設字型，確保程式可運行。
    """
    normal_candidates = [
        r"C:\Windows\Fonts\msjh.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    bold_candidates = [
        r"C:\Windows\Fonts\msjhbd.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\arialbd.ttf",
    ] + normal_candidates

    candidates = bold_candidates if bold else normal_candidates
    for font_path in candidates:
        if os.path.exists(font_path):
            return pygame.font.Font(font_path, size)

    return pygame.font.Font(None, size)


def get_layer_origin(z):
    """回傳第 z 層 (3x3 平面) 左上角像素座標。"""
    x = LEFT_MARGIN + z * (LAYER_PIXEL + LAYER_GAP)
    y = TOP_MARGIN
    return x, y


def build_cell_rects():
    """建立所有格子的可點擊區域映射: (x, y, z) -> Rect。"""
    rects = {}
    for z in range(GRID_SIZE):
        ox, oy = get_layer_origin(z)
        for y in range(GRID_SIZE):
            for x in range(GRID_SIZE):
                rect = pygame.Rect(
                    ox + x * CELL_SIZE,
                    oy + y * CELL_SIZE,
                    CELL_SIZE,
                    CELL_SIZE,
                )
                rects[(x, y, z)] = rect
    return rects


def draw_button(screen, rect, label, font, selected=False, muted=False):
    """繪製可選中的按鈕。"""
    if selected:
        color = BTN_SELECTED
    elif muted:
        color = BTN_MUTED
    else:
        color = BTN_COLOR
    pygame.draw.rect(screen, color, rect, border_radius=8)
    text = font.render(label, True, BTN_TEXT_COLOR)
    screen.blit(text, text.get_rect(center=rect.center))


def draw_menu(screen, fonts, human_first, difficulty):
    """繪製開局主選單：先手與難度。"""
    font_small, font_mid, font_big = fonts

    title = font_big.render("3D 井字棋", True, TEXT_COLOR)
    screen.blit(title, title.get_rect(center=(WINDOW_WIDTH // 2, 80)))

    subtitle = font_mid.render("對戰前設定", True, TEXT_COLOR)
    screen.blit(subtitle, subtitle.get_rect(center=(WINDOW_WIDTH // 2, 140)))

    first_label = font_mid.render("誰先下？", True, TEXT_COLOR)
    screen.blit(first_label, (280, 165))
    draw_button(screen, FIRST_HUMAN_RECT, "玩家先手", font_small, selected=human_first)
    draw_button(screen, FIRST_AI_RECT, "AI 先手", font_small, selected=not human_first)

    diff_label = font_mid.render("難度（MCTS temp）", True, TEXT_COLOR)
    screen.blit(diff_label, (280, 285))
    draw_button(screen, DIFF_EASY_RECT, "簡單", font_small, selected=difficulty == "easy")
    draw_button(screen, DIFF_MED_RECT, "中等", font_small, selected=difficulty == "medium")
    draw_button(screen, DIFF_HARD_RECT, "困難", font_small, selected=difficulty == "hard")

    temp = DIFFICULTY_TEMP[difficulty]
    tip = font_small.render(
        f"目前: {'玩家' if human_first else 'AI'}先手 · {DIFFICULTY_LABEL[difficulty]} (temp={temp})",
        True,
        (75, 75, 75),
    )
    screen.blit(tip, tip.get_rect(center=(WINDOW_WIDTH // 2, 390)))

    draw_button(screen, START_BTN_RECT, "開始遊戲", font_mid)


def draw_board(screen, board, cell_rects, fonts, hover_cell=None):
    """繪製三層棋盤、座標與棋子標記。"""
    font_small, font_mid, font_big = fonts

    for z in range(GRID_SIZE):
        ox, oy = get_layer_origin(z)

        layer_title = font_mid.render(f"第 {z} 層 (z={z})", True, TEXT_COLOR)
        screen.blit(layer_title, (ox, oy - 34))

        pygame.draw.rect(
            screen,
            GRID_COLOR,
            pygame.Rect(ox, oy, LAYER_PIXEL, LAYER_PIXEL),
            width=2,
            border_radius=5,
        )

        for y in range(GRID_SIZE):
            for x in range(GRID_SIZE):
                rect = cell_rects[(x, y, z)]
                pygame.draw.rect(screen, CELL_BG, rect)
                pygame.draw.rect(screen, GRID_COLOR, rect, width=1)

                if hover_cell == (x, y, z):
                    pygame.draw.rect(screen, HIGHLIGHT_COLOR, rect, width=3)

                coord_text = font_small.render(f"({x},{y},{z})", True, (95, 95, 95))
                screen.blit(coord_text, (rect.x + 5, rect.y + 4))

                cell_value = get_cell(board, x, y, z)
                if cell_value == 1:
                    mark = font_big.render("X", True, X_COLOR)
                    mark_rect = mark.get_rect(center=rect.center)
                    screen.blit(mark, mark_rect)
                elif cell_value == -1:
                    mark = font_big.render("O", True, O_COLOR)
                    mark_rect = mark.get_rect(center=rect.center)
                    screen.blit(mark, mark_rect)


def draw_status(screen, status, current_player, fonts, difficulty):
    """顯示當前輪到誰與對局狀態。"""
    _, font_mid, _ = fonts

    if status == "ongoing":
        turn_text = "輪到你 (玩家 X) 落子" if current_player == 1 else "AI 思考中 (O)"
        status_text = f"對局狀態: 進行中 · 難度 {DIFFICULTY_LABEL[difficulty]}"
    elif status == "win_1":
        turn_text = "結果: 玩家勝"
        status_text = "你贏了！按 R / 重新開始，或返回主選單。"
    elif status == "win_2":
        turn_text = "結果: AI 勝"
        status_text = "AI 獲勝。按 R / 重新開始，或返回主選單。"
    else:
        turn_text = "結果: 平局"
        status_text = "棋盤已滿。按 R / 重新開始，或返回主選單。"

    screen.blit(font_mid.render(turn_text, True, TEXT_COLOR), (42, 24))
    screen.blit(font_mid.render(status_text, True, TEXT_COLOR), (42, 60))


def draw_game_buttons(screen, font):
    """繪製重新開始與返回主選單按鈕。"""
    draw_button(screen, RESTART_BTN_RECT, "重新開始 (R)", font)
    draw_button(screen, MENU_BTN_RECT, "主選單", font)


def reset_game(human_first=True):
    """重置對局；human_first=False 時由 AI（-1）先下。"""
    start_player = 1 if human_first else -1
    # region agent log
    _debug_log('H3', 'tictactoe3d_pygame.py:reset_game', 'game reset start player', {
        'human_first': human_first,
        'start_player': start_player,
    })
    # endregion
    return create_board(), "ongoing", start_player


def try_player_move(board, cell_rects, mouse_pos):
    """嘗試處理玩家 (X=1) 點擊落子，成功則回傳 True。"""
    for (x, y, z), rect in cell_rects.items():
        if rect.collidepoint(mouse_pos):
            if is_valid_move(board, x, y, z):
                set_cell(board, x, y, z, 1)
                return True
            return False
    return False


def do_ai_turn(board, player, temp=0.0):
    """執行 AI (O=-1) 落子。"""
    move = ai_move(board, player, temp=temp)
    if move is None:
        return
    x, y, z = move
    if is_valid_move(board, x, y, z):
        set_cell(board, x, y, z, player)


def handle_menu_click(pos, human_first, difficulty):
    """處理主選單點擊，回傳 (human_first, difficulty, start_game)。"""
    if FIRST_HUMAN_RECT.collidepoint(pos):
        return True, difficulty, False
    if FIRST_AI_RECT.collidepoint(pos):
        return False, difficulty, False
    if DIFF_EASY_RECT.collidepoint(pos):
        return human_first, "easy", False
    if DIFF_MED_RECT.collidepoint(pos):
        return human_first, "medium", False
    if DIFF_HARD_RECT.collidepoint(pos):
        return human_first, "hard", False
    if START_BTN_RECT.collidepoint(pos):
        # region agent log
        _debug_log('H1', 'tictactoe3d_pygame.py:handle_menu_click', 'start game settings', {
            'human_first': human_first,
            'difficulty': difficulty,
            'temp': DIFFICULTY_TEMP[difficulty],
        })
        # endregion
        return human_first, difficulty, True
    return human_first, difficulty, False


def main():
    pygame.init()
    pygame.display.set_caption("3D 井字棋 (3x3x3) - 玩家 vs AI")
    screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
    clock = pygame.time.Clock()

    font_small = load_font(16)
    font_mid = load_font(26)
    font_big = load_font(52, bold=True)
    fonts = (font_small, font_mid, font_big)

    screen_state = SCREEN_MENU
    human_first = True
    difficulty = "medium"

    board, status, current_player = reset_game(human_first)
    cell_rects = build_cell_rects()

    running = True
    while running:
        mouse_pos = pygame.mouse.get_pos()
        hover_cell = None
        if screen_state == SCREEN_GAME:
            for key, rect in cell_rects.items():
                if rect.collidepoint(mouse_pos):
                    hover_cell = key
                    break

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_r:
                if screen_state == SCREEN_GAME:
                    board, status, current_player = reset_game(human_first)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if screen_state == SCREEN_MENU:
                    human_first, difficulty, start = handle_menu_click(
                        event.pos, human_first, difficulty
                    )
                    if start:
                        board, status, current_player = reset_game(human_first)
                        screen_state = SCREEN_GAME
                else:
                    if MENU_BTN_RECT.collidepoint(event.pos):
                        # region agent log
                        _debug_log('H4', 'tictactoe3d_pygame.py:main', 'back to menu', {
                            'from': 'game',
                            'to': 'menu',
                        })
                        # endregion
                        screen_state = SCREEN_MENU
                        continue
                    if RESTART_BTN_RECT.collidepoint(event.pos):
                        board, status, current_player = reset_game(human_first)
                        continue
                    if status == "ongoing" and current_player == 1:
                        moved = try_player_move(board, cell_rects, event.pos)
                        if moved:
                            status = game_status(board)
                            if status == "ongoing":
                                current_player = -1

        if screen_state == SCREEN_GAME and status == "ongoing" and current_player == -1:
            do_ai_turn(board, current_player, temp=DIFFICULTY_TEMP[difficulty])
            status = game_status(board)
            if status == "ongoing":
                current_player = 1

        screen.fill(BG_COLOR)
        if screen_state == SCREEN_MENU:
            draw_menu(screen, fonts, human_first, difficulty)
        else:
            draw_status(screen, status, current_player, fonts, difficulty)
            draw_game_buttons(screen, font_small)
            draw_board(screen, board, cell_rects, fonts, hover_cell=hover_cell)
            guide = font_small.render(
                "操作: 左鍵落子 (你=X), AI=O  ·  R=重開  ·  主選單可改先手/難度",
                True,
                (75, 75, 75),
            )
            screen.blit(guide, (42, WINDOW_HEIGHT - 36))

        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
