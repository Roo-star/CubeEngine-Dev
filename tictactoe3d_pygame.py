import os
import random
import sys

import pygame

from tictactoe3d_logic import (
    create_board,
    game_status,
    get_cell,
    get_legal_moves,
    is_valid_move,
    set_cell,
)
from tictactoe3d_nnet import NNetWrapper # 引入你的 AI 包裝層
from game_for_training import TicTacToe3DGame # 引入遊戲規則層
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


BG_COLOR = (245, 247, 250)
TEXT_COLOR = (30, 33, 38)
GRID_COLOR = (90, 100, 115)
CELL_BG = (255, 255, 255)
X_COLOR = (45, 120, 220)
O_COLOR = (220, 80, 80)
HIGHLIGHT_COLOR = (44, 170, 100)
BTN_COLOR = (70, 110, 190)
BTN_TEXT_COLOR = (255, 255, 255)
args = dotdict({'num_channels': 128, 'dropout': 0.3})


def ai_move(board, player):
    """
    AI 落子介面 (玩家2)。

    目前先用隨機策略當佔位：從合法步中隨機選一格。
    未來可在這裡替換為「訓練好的 AI 模型推理」，
    而不用修改其他 pygame 畫面或流程程式碼。
    """
    # 1. 初始化 AI
    game = TicTacToe3DGame()
    nnet = NNetWrapper(game, args)
    
    # 2. 載入訓練好的最佳權重
    nnet.load_checkpoint('./checkpoints/', 'best.pth.tar')
    
    # 3. 將棋盤轉為 AI 視角 (Canonical Form)
    canonical_board = game.getCanonicalForm(board, player)
    
    # 4. 取得 AI 的策略機率分佈 (pi)
    pi, _ = nnet.predict(canonical_board)
    
    # 5. 根據策略，選擇機率最高的合法動作
    valids = game.getValidMoves(canonical_board, 1)
    
    # 遮罩非法落子：將非法動作機率設為 0
    pi = pi * valids
    
    # 選擇機率最高的一步
    action = int(np.argmax(pi))
    
    # 將 action 解碼為 (x, y, z) 並回傳
    x = action // 9
    y = (action % 9) // 3
    z = action % 3
    return (x, y ,z)


def load_font(size, bold=False):
    """
    安全載入字型：
    - 優先直接讀取 Windows 常見中文字型檔，避免 SysFont 在部分環境崩潰。
    - 若都找不到，退回 pygame 預設字型，確保程式可運行。
    """
    normal_candidates = [
        r"C:\Windows\Fonts\msjh.ttc",      # Microsoft JhengHei
        r"C:\Windows\Fonts\msyh.ttc",      # Microsoft YaHei
        r"C:\Windows\Fonts\simhei.ttf",    # SimHei
        r"C:\Windows\Fonts\arial.ttf",     # Arial
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


def draw_board(screen, board, cell_rects, fonts, hover_cell=None):
    """繪製三層棋盤、座標與棋子標記。"""
    font_small, font_mid, font_big = fonts

    for z in range(GRID_SIZE):
        ox, oy = get_layer_origin(z)

        # 層標題: z=0 / z=1 / z=2
        layer_title = font_mid.render(f"第 {z} 層 (z={z})", True, TEXT_COLOR)
        screen.blit(layer_title, (ox, oy - 34))

        # 每層外框
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

                # 格內小字標示座標，方便點擊辨識
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


def draw_status(screen, status, current_player, fonts):
    """顯示當前輪到誰與對局狀態。"""
    _, font_mid, _ = fonts

    if status == "ongoing":
        turn_text = "輪到你 (玩家1) 落子" if current_player == 1 else "AI 思考中 (玩家2)"
        status_text = "對局狀態: 進行中"
    elif status == "win_1":
        turn_text = "結果: 玩家1勝"
        status_text = "你贏了！按 R 或按右上角按鈕重開。"
    elif status == "win_2":
        turn_text = "結果: AI勝"
        status_text = "AI 獲勝。按 R 或按右上角按鈕重開。"
    else:
        turn_text = "結果: 平局"
        status_text = "棋盤已滿。按 R 或按右上角按鈕重開。"

    info_1 = font_mid.render(turn_text, True, TEXT_COLOR)
    info_2 = font_mid.render(status_text, True, TEXT_COLOR)

    screen.blit(info_1, (42, 24))
    screen.blit(info_2, (42, 60))


def draw_restart_button(screen, font):
    """繪製重新開始按鈕。"""
    pygame.draw.rect(screen, BTN_COLOR, RESTART_BTN_RECT, border_radius=8)
    label = font.render("重新開始 (R)", True, BTN_TEXT_COLOR)
    label_rect = label.get_rect(center=RESTART_BTN_RECT.center)
    screen.blit(label, label_rect)


def reset_game():
    """重置對局狀態。"""
    return create_board(), "ongoing", 1


def try_player_move(board, cell_rects, mouse_pos):
    """嘗試處理玩家1點擊落子，成功則回傳 True。"""
    for (x, y, z), rect in cell_rects.items():
        if rect.collidepoint(mouse_pos):
            if is_valid_move(board, x, y, z):
                set_cell(board, x, y, z, 1)
                return True
            return False
    return False


def do_ai_turn(board, player):
    """執行 AI (玩家2) 落子。"""
    # 如果遊戲是 1, 2，但 AI 訓練時是 1, -1
    move = ai_move(board, player)
    if move is None:
        return
    x, y, z = move
    if is_valid_move(board, x, y, z):
        set_cell(board, x, y, z, player)


def main():
    pygame.init()
    pygame.display.set_caption("3D 井字棋 (3x3x3) - 玩家 vs AI")
    screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
    clock = pygame.time.Clock()

    # 用字型檔直載，避開 SysFont 在某些 Windows+pyenv 環境的崩潰問題。
    font_small = load_font(16)
    font_mid = load_font(26)
    font_big = load_font(52, bold=True)
    fonts = (font_small, font_mid, font_big)

    board, status, current_player = reset_game()
    cell_rects = build_cell_rects()

    running = True
    while running:
        hover_cell = None
        mouse_pos = pygame.mouse.get_pos()
        for key, rect in cell_rects.items():
            if rect.collidepoint(mouse_pos):
                hover_cell = key
                break

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_r:
                board, status, current_player = reset_game()
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if RESTART_BTN_RECT.collidepoint(event.pos):
                    board, status, current_player = reset_game()
                    continue

                if status == "ongoing" and current_player == 1:
                    moved = try_player_move(board, cell_rects, event.pos)
                    if moved:
                        status = game_status(board)
                        if status == "ongoing":
                            current_player = -1

        # AI 回合: 玩家下完後自動執行
        if status == "ongoing" and current_player == -1:
            do_ai_turn(board, current_player)
            status = game_status(board)
            if status == "ongoing":
                current_player = 1

        screen.fill(BG_COLOR)
        draw_status(screen, status, current_player, fonts)
        draw_restart_button(screen, font_small)
        draw_board(screen, board, cell_rects, fonts, hover_cell=hover_cell)

        guide = font_small.render(
            "操作: 左鍵點格子落子 (玩家1=X), AI 為玩家2=O",
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
