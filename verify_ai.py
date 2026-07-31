"""
本地驗收腳本（你自己用，未提交到 git）。
用法：在倉庫根目錄執行  ->  python verify_ai.py
檢查：① 遊戲規則/勝負判定 ② checkpoint 能否加載 ③ AI 落子是否合法、整局是否不崩。
這是「管線通不通」層面的驗收，不評判 AI 棋力。
"""
import sys, random
import numpy as np


def ok(msg):  print("  [OK]  " + msg)
def bad(msg): print("  [FAIL]" + msg)


def main():
    print("== 1. 遊戲規則 ==")
    from game_for_training import TicTacToe3DGame
    g = TicTacToe3DGame()
    b = g.getInitBoard()
    assert b.shape == (3, 3, 3) and g.getActionSize() == 27
    assert g.getValidMoves(b, 1).sum() == 27
    win = g.getInitBoard()
    for z in range(3):
        win, _ = g.getNextState(win, 1, 0 * 9 + 0 * 3 + z)
    assert g.getGameEnded(win, 1) == 1
    ok("規則 / 合法步 / 勝負判定 正確")

    print("== 2 & 3. 加載工程師的 ai_move 並驗證合法性 ==")
    import tictactoe3d_pygame as pg              # 直接用交付代碼（含它自己的 num_channels）
    from tictactoe3d_logic import create_board, is_valid_move, set_cell, game_status

    mv = pg.ai_move(create_board(), 1)
    x, y, z = mv
    assert is_valid_move(create_board(), x, y, z), f"AI 空盤返回非法步 {mv}"
    ok(f"checkpoint 加載成功，空盤合法步 = {mv}")

    board = create_board(); player = 1; n = 0
    while game_status(board) == "ongoing" and n < 27:
        if player == 1:
            empties = [(x, y, z) for z in range(3) for y in range(3) for x in range(3)
                       if is_valid_move(board, x, y, z)]
            x, y, z = random.choice(empties); set_cell(board, x, y, z, 1)
        else:
            x, y, z = pg.ai_move(board, -1)
            assert is_valid_move(board, x, y, z), f"AI 第 {n} 步非法 {(x,y,z)}"
            set_cell(board, x, y, z, -1)
        player *= -1; n += 1
    ok(f"人機對弈 {n} 步全程合法，結果 = {game_status(board)}")
    print("\n>>> 驗收冒煙測試全部通過 <<<")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\n!!! 驗收失敗:", repr(e))
        sys.exit(1)
