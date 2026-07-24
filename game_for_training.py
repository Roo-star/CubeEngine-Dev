import numpy as np
from Game import Game  # 繼承 alpha-zero-general 的基類

class TicTacToe3DGame(Game):
    WINNING_LINES = None  # 类变量
    def __init__(self):
        super(TicTacToe3DGame, self).__init__()
        self.grid_size = 3

    def getInitBoard(self):
        """回傳初始化的 3D 棋盤 (3x3x3 numpy 陣列)"""
        return np.zeros((3, 3, 3), dtype=int)

    def getBoardSize(self):
        """回傳棋盤維度，供神經網路讀取"""
        return (3, 3, 3)

    def getActionSize(self):
        """回傳所有可能的總步數 (3x3x3 = 27)"""
        return 27

    def getNextState(self, board, player, action):
        """
        執行落子動作，回傳 (新棋盤, 下一個玩家)
        """
        # 防禦層：確保傳進來的是 numpy 陣列
        b = np.copy(np.asarray(board).reshape(3, 3, 3))
        
        # 將 0-26 的動作編碼，解碼成 (x, y, z) 空間座標
        x = action // 9
        y = (action % 9) // 3
        z = action % 3
        
        # 在對應的矩陣位置落子 (注意索引維持 z, y, x)
        if b[z, y, x] != 0:
            raise ValueError(f"Invalid move: position ({x},{y},{z}) already occupied")
        b[z, y, x] = player
        
        # 回傳新棋盤與換手（1 變 -1，-1 變 1）
        return b, -player

    def getValidMoves(self, board, player):
        """
        回傳一個長度為 27 的 0/1 向量，1 代表可以落子，0 代表已被佔用
        """
        b = np.asarray(board).reshape(3, 3, 3)
        valid_moves = np.zeros(self.getActionSize(), dtype=int)
        
        # 遍歷所有 27 個動作，解碼檢查是否為空
        for action in range(self.getActionSize()):
            x = action // 9
            y = (action % 9) // 3
            z = action % 3
            
            if b[z, y, x] == 0:
                valid_moves[action] = 1
                
        return valid_moves
        # b = np.asarray(board).reshape(-1)  # 展平为27维
        # return (b == 0).astype(int) 

    def getGameEnded(self, board, player):
        """
        檢查遊戲是否結束。
        回傳:
             1: 當前 player 獲勝
            -1: 當前 player 輸了 (對手獲勝)
          1e-4: 平局 (用極小值區分未結束)
             0: 遊戲尚未結束，繼續下
        """
        b = np.asarray(board).reshape(3, 3, 3)
        winner = self._check_winner_internal(b)
        
        if winner == player:
            return 1
        elif winner == -player:
            return -1
            
        # 如果沒有贏家，且棋盤滿了，則是平局
        if not np.any(b == 0):
            return 1e-4  # alpha-zero-general 規範的平局極小值
            
        return 0

    def getCanonicalForm(self, board, player):
        """
        【核心數學翻轉】讓神經網路永遠以「當前玩家」的視角看棋盤。
        如果當前玩家是 -1，整個棋盤乘以 -1 會讓他的棋子在矩陣中變成 1，對手變成 -1。
        """
        b = np.asarray(board).reshape(3, 3, 3)
        return b * player

    def getSymmetries(self, board, pi):
        """
        為 3D 井字棋量身打造的 3D 空間數據增強 (支援 48 種旋轉與鏡像組合)
        完美適配目前的 (z, y, x) 棋盤與一維 pi 映射
        """
        # 1. 將一維的 pi 向量還原為 3D 矩陣
        # 根據 action 解碼邏輯 (x=action//9, z=action%3)，初始 reshape 出來的維度是 (X, Y, Z)
        pi_xyz = np.reshape(pi, (3, 3, 3))
        
        # 2. 【核心軸對齊】將 pi 的維度從 (X, Y, Z) 轉換成與 board 一致的 (Z, Y, X)
        # 這樣後續的空間旋轉函數才能同時正確作用在棋盤與策略機率上
        pi_board = pi_xyz.transpose(2, 1, 0)
        
        symmetries_dict = {}

        # 3. 窮舉 3D 空間的所有旋轉與翻轉組合 (共 48 種可能，去重後保留獨特盤面)
        for x_rot in range(4):
            for y_rot in range(4):
                for z_rot in range(4):
                    for flip in [False, True]:
                        # 複製當前狀態進行旋轉
                        b = np.copy(board)
                        p = np.copy(pi_board)
                        
                        # 繞各個軸旋轉
                        if x_rot > 0:
                            b = np.rot90(b, x_rot, axes=(1, 2))  # Y-X 平面旋轉
                            p = np.rot90(p, x_rot, axes=(1, 2))
                        if y_rot > 0:
                            b = np.rot90(b, y_rot, axes=(0, 2))  # Z-X 平面旋轉
                            p = np.rot90(p, y_rot, axes=(0, 2))
                        if z_rot > 0:
                            b = np.rot90(b, z_rot, axes=(0, 1))  # Z-Y 平面旋轉
                            p = np.rot90(p, z_rot, axes=(0, 1))
                        
                        # 鏡像翻轉 (增加防守對稱性)
                        if flip:
                            b = np.flip(b, axis=0)  # 沿 Z 軸翻轉
                            p = np.flip(p, axis=0)
                            
                        # 使用 tobytes() 作為唯一的字典 Key 進行盤面去重
                        b_bytes = b.tobytes()
                        if b_bytes not in symmetries_dict:
                            # 4. 【還原軸順序】將 p 從 (Z, Y, X) 轉回 (X, Y, Z)，再拉平成一維 27 維向量
                            # 這樣才能完美對應你的 x = action // 9 解碼規則
                            p_flat = p.transpose(2, 1, 0).ravel()
                            
                            symmetries_dict[b_bytes] = (b, list(p_flat))
                            
        return list(symmetries_dict.values())

    def stringRepresentation(self, board):
        """將棋盤轉成 bytes，做為 MCTS 字典的唯一 Key"""
        b = np.asarray(board).reshape(3, 3, 3)
        return b.tobytes()  
        # return np.asarray(board).tobytes() 

    # ==========================================================
    # 內部私有檢查邏輯（相容 1 與 -1 的 3D 連線演算法）
    # ==========================================================
    def _check_winner_internal(self, board):
        """遍歷 3D 空間 49 條連線，回傳贏家 (1 或 -1)，無人贏則回傳 0"""
        winning_lines = []
        # 1. 正交線
        for i in range(3):
            for j in range(3):
                winning_lines.append([(i, j, 0), (i, j, 1), (i, j, 2)])
                winning_lines.append([(i, 0, j), (i, 1, j), (i, 2, j)])
                winning_lines.append([(0, i, j), (1, i, j), (2, i, j)])
        # 2. 2D 對角線
        for i in range(3):
            winning_lines.append([(i, 0, 0), (i, 1, 1), (i, 2, 2)])
            winning_lines.append([(i, 0, 2), (i, 1, 1), (i, 2, 0)])
            winning_lines.append([(0, i, 0), (1, i, 1), (2, i, 2)])
            winning_lines.append([(0, i, 2), (1, i, 1), (2, i, 0)])
            winning_lines.append([(0, 0, i), (1, 1, i), (2, 2, i)])
            winning_lines.append([(0, 2, i), (1, 1, i), (2, 0, i)])
        # 3. 3D 大對角線
        winning_lines.append([(0, 0, 0), (1, 1, 1), (2, 2, 2)])
        winning_lines.append([(0, 0, 2), (1, 1, 1), (2, 2, 0)])
        winning_lines.append([(0, 2, 0), (1, 1, 1), (2, 0, 2)])
        winning_lines.append([(0, 2, 2), (1, 1, 1), (2, 0, 0)])

        for line in winning_lines:
            p1 = board[line[0][0], line[0][1], line[0][2]]
            p2 = board[line[1][0], line[1][1], line[1][2]]
            p3 = board[line[2][0], line[2][1], line[2][2]]
            if p1 == p2 == p3 and p1 != 0:
                return int(p1)
        return 0