import sys
sys.path.append('..')
from utils import *

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
        self.board_z, self.board_x, self.board_y = game.getBoardSize()
        self.action_size = game.getActionSize()
        self.args = args

        # --- 擴展為 6 層卷積 ---
        # 保持前 3 層為特徵提取 (Padding 1, 維度不變)
        self.conv1 = nn.Conv3d(1, args.num_channels, 3, padding=1)
        self.bn1 = nn.BatchNorm3d(args.num_channels)
        
        self.conv2 = nn.Conv3d(args.num_channels, args.num_channels, 3, padding=1)
        self.bn2 = nn.BatchNorm3d(args.num_channels)
        
        self.conv3 = nn.Conv3d(args.num_channels, args.num_channels, 3, padding=1)
        self.bn3 = nn.BatchNorm3d(args.num_channels)

        # 第 4, 5 層持續擴展特徵深度
        self.conv4 = nn.Conv3d(args.num_channels, args.num_channels, 3, padding=1)
        self.bn4 = nn.BatchNorm3d(args.num_channels)
        
        self.conv5 = nn.Conv3d(args.num_channels, args.num_channels, 3, padding=1)
        self.bn5 = nn.BatchNorm3d(args.num_channels)

        # 第 6 層：將 3x3x3 壓縮至 1x1x1 (Padding 0)
        self.conv6 = nn.Conv3d(args.num_channels, args.num_channels, 3, padding=0)
        self.bn6 = nn.BatchNorm3d(args.num_channels)

        # 全連接層定義 (從 args 讀取維度)
        fc1_dim = getattr(args, 'fc1_dim', 1024)
        fc2_dim = getattr(args, 'fc2_dim', 512)
        
        self.fc1 = nn.Linear(args.num_channels, fc1_dim)
        self.fc_bn1 = nn.BatchNorm1d(fc1_dim)
        self.fc2 = nn.Linear(fc1_dim, fc2_dim)
        self.fc_bn2 = nn.BatchNorm1d(fc2_dim)

        self.fc_pi = nn.Linear(fc2_dim, self.action_size)
        self.fc_v = nn.Linear(fc2_dim, 1)

    def forward(self, s):
        s = s.unsqueeze(1) 
        
        # 依序執行 6 層
        s = F.relu(self.bn1(self.conv1(s)))
        s = F.relu(self.bn2(self.conv2(s)))
        s = F.relu(self.bn3(self.conv3(s)))
        s = F.relu(self.bn4(self.conv4(s)))
        s = F.relu(self.bn5(self.conv5(s)))
        s = F.relu(self.bn6(self.conv6(s))) # 輸出 [Batch, num_channels, 1, 1, 1]

        s = s.view(-1, self.args.num_channels)
        
        s = F.dropout(F.relu(self.fc_bn1(self.fc1(s))), p=self.args.dropout, training=self.training)
        s = F.dropout(F.relu(self.fc_bn2(self.fc2(s))), p=self.args.dropout, training=self.training)

        return F.log_softmax(self.fc_pi(s), dim=1), torch.tanh(self.fc_v(s))


# ==========================================
# 2. 演算法外層包裝 (負責訓練、預測、對接 Coach)
# ==========================================
class NNetWrapper(NeuralNet):
    def __init__(self, game, args):
        self.nnet = TicTacToe3DNet(game, args)
        self.board_z, self.board_x, self.board_y = game.getBoardSize()
        self.action_size = game.getActionSize()
        self.args = args

        # 自動偵測並掛載 本地 GPU (Spike A.0 核心)
        if torch.cuda.is_available():
            self.nnet.cuda()

    def train(self, examples):
        """
        對應 Keras 的 model.fit()。alpha-zero-general 會自動把棋譜傳進這裡。
        """
        optimizer = optim.Adam(self.nnet.parameters(), lr=self.args.lr)
        self.nnet.train() # 切換至訓練模式 (啟用 Dropout 和 BatchNorm)

        for epoch in range(self.args.epochs):
            # 這裡框架傳進來的 examples 包含 (board, pi, v)
            # 依據你的資料集結構將其打包成 PyTorch Tensor 並送入 GPU
            pass 

    def predict(self, board):
        """
        給 MCTS 腦內模擬或實際落子時調用的預測介面。
        """
        # 確保輸入是 FloatTensor 並且有 Batch 維度
        board = torch.FloatTensor(board.astype(np.float32))
        if torch.cuda.is_available():
            board = board.cuda()
            
        board = board.unsqueeze(0) # 補上 Batch 維度 [1, Z, X, Y]
        self.nnet.eval()           # 切換至評估模式 (關閉 Dropout)
        
        with torch.no_grad():      # 關閉梯度計算以加速預測
            pi, v = self.nnet(board)

        # 轉回 numpy 回傳給框架
        return torch.exp(pi).data.cpu().numpy()[0], v.data.cpu().numpy()[0]

    def save_checkpoint(self, folder='checkpoint', filename='checkpoint.pth.tar'):
        # 實作模型儲存邏輯 (框架要求)
        pass

    def load_checkpoint(self, folder='checkpoint', filename='checkpoint.pth.tar'):
        # 實作模型讀取邏輯 (框架要求)
        pass