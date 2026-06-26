import numpy as np
from Game import Game  # 繼承 alpha-zero-general 的基類

class TicTacToe3DGame(Game):
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
        MVP 階段的數據增強實作：先不進行 3D 空間的旋轉/鏡像增強，
        直接原樣回傳，確保 pipeline 最快跑通。後期優化再加入旋轉。
        """
        return [(board, pi)]

    def stringRepresentation(self, board):
        """將棋盤轉成 bytes，做為 MCTS 字典的唯一 Key"""
        b = np.asarray(board).reshape(3, 3, 3)
        return b.tobytes()

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