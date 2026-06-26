import numpy as np

def create_board():
    """
    初始化 3D 井字棋遊戲狀態。
    
    回傳:
        board (np.ndarray): 3x3x3 的三維陣列，0 代表空格。
        status (str): 遊戲狀態，初始為 "ongoing"。
        current_player (int): 當前玩家，1 代表玩家 X，2 代表 AI O。
    """
    # 建立一個 3x3x3 的整數陣列，預設全部填 0 (空格)
    board = np.zeros((3, 3, 3), dtype=int)
    
    return board

def is_valid_move(board, x, y, z):
    """
    檢查指定的 3D 空間座標是否為合法落子點。
    
    參數:
        board (np.ndarray): 3x3x3 的棋盤陣列。
        x, y, z (int): 空間座標。
        
    回傳:
        bool: 合法回傳 True，若越界或已有棋子則回傳 False。
    """
    # 1. 安全檢查：確保座標在 3x3x3 的範圍內
    if not (0 <= x < 3 and 0 <= y < 3 and 0 <= z < 3):
        return False
        
    # 2. 檢查該位置是否為空格 (0)
    # 注意：NumPy 陣列索引維持 [z, y, x]
    return board[z, y, x] == 0

def set_cell(board, x, y, z, player):
    """
    在指定的 3D 空間座標落子（變更棋盤狀態）。
    
    參數:
        board (np.ndarray): 3x3x3 的棋盤陣列。
        x, y, z (int): 空間座標。
        player (int): 玩家代號 (1 代表玩家 X, 2 代表 AI O)。
    """
    # 直接寫入矩陣
    board[z, y, x] = player

def get_cell(board, x, y, z):
    """
    讀取指定空間座標的格點狀態（已加入免崩潰相容層）。
    """
    # 💡 核心修正：不管傳進來的是 flat tuple、nested tuple 還是 ndarray，
    # 通通安全地轉換並還原成 3x3x3 的 3D 矩陣
    board_np = np.asarray(board).reshape(3, 3, 3)
    
    # 使用還原後的 board_np 進行索引
    if 0 <= x < 3 and 0 <= y < 3 and 0 <= z < 3:
        return int(board_np[z, y, x])
    return 0

def is_valid_move(board, x, y, z):
    """
    檢查該落子位置是否合法。
    """
    # 座標必須在 3x3x3 內，且該位置必須是空格 (0)
    if not (0 <= x < 3 and 0 <= y < 3 and 0 <= z < 3):
        return False
    return board[z, y, x] == 0

def get_legal_moves(board):
    """
    獲取目前棋盤上所有合法的落子座標列表。
    
    回傳: [(x, y, z), ...] 元組列表，順序與同事的 UI 映射一致
    """
    legal_moves = []
    for z in range(3):
        for y in range(3):
            for x in range(3):
                if board[z, y, x] == 0:
                    legal_moves.append((x, y, z))
    return legal_moves

def check_winner(board):
    """
    檢查目前是否有玩家勝出。在 3x3x3 的立體空間中，總共有 49 條可能的連線。
    
    回傳: 0 (尚未有人獲勝) / 1 (玩家1贏) / 2 (玩家2贏)
    """
    # 收集 3D 空間中所有可能的 49 條直線組合
    winning_lines = []

    # 1. 正交直線 (平行於 X, Y, Z 軸)共 27 條
    for i in range(3):
        for j in range(3):
            winning_lines.append([(i, j, 0), (i, j, 1), (i, j, 2)])  # 固定 Z, Y，改變 X
            winning_lines.append([(i, 0, j), (i, 1, j), (i, 2, j)])  # 固定 Z, X，改變 Y
            winning_lines.append([(0, i, j), (1, i, j), (2, i, j)])  # 固定 Y, X，改變 Z

    # 2. 2D 平面對角線 (X-Y, X-Z, Y-Z 三個切面)共 18 條
    for i in range(3):
        # X-Y 平面上的對角線 (固定 Z)
        winning_lines.append([(i, 0, 0), (i, 1, 1), (i, 2, 2)])
        winning_lines.append([(i, 0, 2), (i, 1, 1), (i, 2, 0)])
        # X-Z 平面上的對角線 (固定 Y)
        winning_lines.append([(0, i, 0), (1, i, 1), (2, i, 2)])
        winning_lines.append([(0, i, 2), (1, i, 1), (2, i, 0)])
        # Y-Z 平面上的對角線 (固定 X)
        winning_lines.append([(0, 0, i), (1, 1, i), (2, 2, i)])
        winning_lines.append([(0, 2, i), (1, 1, i), (2, 0, i)])

    # 3. 3D 空間立體跨層大對角線 (穿越正立方體中心的 4 條線)
    winning_lines.append([(0, 0, 0), (1, 1, 1), (2, 2, 2)])
    winning_lines.append([(0, 0, 2), (1, 1, 1), (2, 2, 0)])
    winning_lines.append([(0, 2, 0), (1, 1, 1), (2, 0, 2)])
    winning_lines.append([(0, 2, 2), (1, 1, 1), (2, 0, 0)])

    # 4. 開始走查這 49 條線，檢查是否滿足三子連線
    for line in winning_lines:
        p1 = board[line[0][0], line[0][1], line[0][2]]
        p2 = board[line[1][0], line[1][1], line[1][2]]
        p3 = board[line[2][0], line[2][1], line[2][2]]
        
        # 只要三點值相同且不為 0，就代表有人連線成功
        if p1 == p2 == p3 and p1 != 0:
            return int(p1)

    return 0

def is_full(board):
    """
    檢查棋盤是否已經完全下滿。
    """
    # 只要矩陣中沒有任何一個位置等於 0，就代表滿了
    return not np.any(board == 0)

def game_status(board):
    """
    評估當前遊戲狀態，供主邏輯迴圈決定是否結束遊戲。
    
    回傳文字狀態: 'win_1' / 'win_2' / 'draw' / 'ongoing'
    """
    winner = check_winner(board)
    if winner == 1:
        return 'win_1'
    elif winner == -1:
        return 'win_2'
    
    if is_full(board):
        return 'draw'
        
    return 'ongoing'