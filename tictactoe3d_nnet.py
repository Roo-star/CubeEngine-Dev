import sys
sys.path.append('..')
from utils import *

import os
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from NeuralNet import NeuralNet  # alpha-zero-general 的基類

# ==========================================
# 1. 純神經網路架構 (遺傳自 nn.Module)
# ==========================================
class TicTacToe3DNet(nn.Module):
    def __init__(self, game, args):
        super(TicTacToe3DNet, self).__init__()
        # 取得遊戲棋盤維度 (3, 3, 3) 與動作數 (27)
        self.board_z, self.board_x, self.board_y = game.getBoardSize()
        self.action_size = game.getActionSize()
        self.args = args

        # 3D 卷積層層組 (PyTorch 輸入格式: Batch x Channel x Z x X x Y)
        # 第一層輸入 Channel=1 (因為只有一張 3D 棋盤)
        self.conv1 = nn.Conv3d(1, args.num_channels, kernel_size=3, padding=1)
        self.bn1 = nn.BatchNorm3d(args.num_channels)

        self.conv2 = nn.Conv3d(args.num_channels, args.num_channels, kernel_size=3, padding=1)
        self.bn2 = nn.BatchNorm3d(args.num_channels)

        self.conv3 = nn.Conv3d(args.num_channels, args.num_channels, kernel_size=3, padding=1)
        self.bn3 = nn.BatchNorm3d(args.num_channels)

        # 第四層對應 Keras 的 padding='valid' (不補零)，會把 3x3x3 壓縮成 1x1x1
        self.conv4 = nn.Conv3d(args.num_channels, args.num_channels, kernel_size=3, padding=0)
        self.bn4 = nn.BatchNorm3d(args.num_channels)

        # 全連接層 (Fully Connected)
        # 經過 conv4 後大小變為 num_channels * 1 * 1 * 1
        self.fc1 = nn.Linear(args.num_channels, 1024)
        self.fc_bn1 = nn.BatchNorm1d(1024)

        self.fc2 = nn.Linear(1024, 512)
        self.fc_bn2 = nn.BatchNorm1d(512)

        # 雙輸出頭 (Dual Heads)
        self.fc_pi = nn.Linear(512, self.action_size) # 策略頭 (Policy Head)
        self.fc_v = nn.Linear(512, 1)                  # 價值頭 (Value Head)

    def forward(self, s):
        # s 的輸入形狀: [Batch, Z, X, Y]
        # 💡 修正維度：插入 Channel 軸變為 [Batch, 1, Z, X, Y] 以符合 PyTorch 3D 卷積要求
        s = s.unsqueeze(1) 

        # 卷積前向傳播
        s = F.relu(self.bn1(self.conv1(s)))
        s = F.relu(self.bn2(self.conv2(s)))
        s = F.relu(self.bn3(self.conv3(s)))
        s = F.relu(self.bn4(self.conv4(s))) # 輸出形狀: [Batch, num_channels, 1, 1, 1]

        # Flatten (展平) 進入全連接層
        s = s.view(-1, self.args.num_channels)

        # 全連接與 Dropout
        s = F.dropout(F.relu(self.fc_bn1(self.fc1(s))), p=self.args.dropout, training=self.training)
        s = F.dropout(F.relu(self.fc_bn2(self.fc2(s))), p=self.args.dropout, training=self.training)

        # 輸出處理
        pi = self.fc_pi(s)
        v = self.fc_v(s)

        # 💡 alpha-zero-general 標準做法：
        # 策略頭輸出 log_softmax (搭配後續的 NLLLoss 損失函數)
        # 價值頭輸出 tanh 限制在 [-1, 1] 之間
        return F.log_softmax(pi, dim=1), torch.tanh(v)


# ==========================================
# 2. 演算法外層包裝 (負責訓練、預測、對接 Coach)
# ==========================================
class NNetWrapper(NeuralNet):
    # 1. 建立一個類別變數，用來暫存從 main.py 傳進來的超參數
    args = None

    def __init__(self, game, args=None):
        # 2. 如果有傳入 args (例如 main.py 第一次初始化時)，就把它存入類別變數
        if args is not None:
            NNetWrapper.args = args
        # 3. 如果沒傳入 (例如 Coach.py 初始化 pnet 時)，就去讀取之前暫存的參數
        elif NNetWrapper.args is None:
            raise ValueError("第一次初始化 NNetWrapper 時必須提供 args 參數！")
        
        self.args = NNetWrapper.args
        self.game = game
        
        self.nnet = TicTacToe3DNet(game, self.args) 
        
        # 後續的其他設定
        self.board_z, self.board_x, self.board_y = game.getBoardSize()
        self.action_size = game.getActionSize()
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.nnet.to(self.device)
        print(f"【系統提示】AI 訓練大腦目前運行在：{self.device}")

    def train(self, examples):
        """
        examples: 包含 list of [(board, pi, v), ...] 的訓練資料集
        """
        optimizer = optim.Adam(self.nnet.parameters(), lr=self.args.lr)
        self.nnet.train()

        # 取得設定的 batch_size，若無設定則預設為 64
        batch_size = self.args.get('batch_size', 64)

        for epoch in range(self.args.epochs):
            batch_count = 0
            total_loss_sum = 0.0  # 用來計算當前 Epoch 的平均 Loss

            # 將資料切成小批次 (Batch) 進行訓練
            for i in range(0, len(examples), batch_size):
                batch = examples[i:i + batch_size]
                
                # 💡 【核心修正點 1】如果最後一個 Batch 只有 1 個樣本，直接捨棄，防止 BatchNorm 崩潰
                if len(batch) <= 1:
                    continue

                boards, pis, vs = list(zip(*batch))
                
                # 💡 【核心修正點 2】轉為 FloatTensor 搭配 float32，對 CPU/GPU 效能與記憶體更友善
                boards = torch.FloatTensor(np.array(boards).astype(np.float32)).to(self.device)
                target_pis = torch.FloatTensor(np.array(pis)).to(self.device)
                target_vs = torch.FloatTensor(np.array(vs)).to(self.device)

                # 前向傳播
                out_pi, out_v = self.nnet(boards)

                # 計算損失 (Loss = PolicyLoss + ValueLoss)
                l_pi = -torch.sum(target_pis * out_pi) / len(boards)
                l_v = torch.sum((target_vs - out_v.view(-1)) ** 2) / len(boards)
                total_loss = l_pi + l_v

                # 反向傳播
                optimizer.zero_grad()
                total_loss.backward()
                optimizer.step()
                
                total_loss_sum += total_loss.item()
                batch_count += 1
            
            # 💡 稍微優化 log：顯示平均 Loss 會比單看最後一個小批次的 Loss 更精準
            avg_loss = total_loss_sum / batch_count if batch_count > 0 else 0
            print(f"Epoch {epoch+1}/{self.args.epochs} | Avg Loss: {avg_loss:.4f}")

    def predict(self, board):
        """
        給 MCTS 腦內模擬或實際落子時調用的預測介面。
        """
        # 💡 修正後：確保輸入資料與神經網路在同一個設備上
        board = torch.FloatTensor(board.astype(np.float32)).to(self.device)
            
        board = board.unsqueeze(0) # 補上 Batch 維度 [1, Z, X, Y]
        self.nnet.eval()           # 切換至評估模式
        
        with torch.no_grad():
            pi, v = self.nnet(board)

        return torch.exp(pi).data.cpu().numpy()[0], v.data.cpu().numpy()[0]

    def save_checkpoint(self, folder='checkpoint', filename='checkpoint.pth.tar'):
        """儲存權重到檔案系統"""
        if not os.path.exists(folder):
            os.makedirs(folder)
            
        filepath = os.path.join(folder, filename)
        torch.save({
            'state_dict': self.nnet.state_dict(),
        }, filepath)
        print(f"模型權重已儲存至: {filepath}")

    def load_checkpoint(self, folder='checkpoint', filename='checkpoint.pth.tar'):
        """從檔案系統載入權重"""
        filepath = os.path.join(folder, filename)
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"找不到權重檔案: {filepath}")
            
        checkpoint = torch.load(filepath, map_location='cuda' if torch.cuda.is_available() else 'cpu')
        self.nnet.load_state_dict(checkpoint['state_dict'])
        print(f"模型權重已載入: {filepath}")