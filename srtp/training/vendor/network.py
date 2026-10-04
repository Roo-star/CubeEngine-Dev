"""AI-Dev 2f42b6b 3D network architecture, unchanged; see ORIGIN.md."""
import torch
from torch import nn
from torch.nn import functional as F

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
