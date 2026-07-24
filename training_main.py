import logging
import os
from Coach import Coach
from game_for_training import TicTacToe3DGame
from tictactoe3d_nnet import NNetWrapper as NNet
from utils import dotdict

# 設定日誌格式，方便觀察 AI 自對弈與對決進度
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

# ==========================================================
# 超參數設定中心 (目前為 A.4 Pipeline 快速驗證極小化配置)
# ==========================================================
args = dotdict({
    # 1. 總訓練大循環次數
    'numIters': 100,                  # 驗證用：只跑 3 輪迭代。正式訓練建議：50+
    
    # 2. 自對弈（Self-Play）參數
    'numEps': 100,                    # 驗證用：每輪只自己跟自己下 4 盤收集棋譜。正式訓練建議：100+
<<<<<<< HEAD
    'tempThreshold': 10,           # 前幾步探索；超過後 temp=0 以強化終局戰術（如擋子）
=======
    'tempThreshold': 30,            # 前幾步探索落子的隨機度閥值
>>>>>>> origin/dev
    'maxlenOfQueue': 200000,        # 記憶體中最多存放的棋譜步數
    
    # 3. MCTS (蒙地卡羅樹搜尋) 腦內模擬次數
    'numMCTSSims': 400,              # 驗證用：每次落子只模擬 10 步（關鍵加速點）。正式訓練建議：100~200
    
    # 4. 新舊模型對決（Arena）參數
    'arenaCompare': 40,              # 驗證用：新舊模型只互打 2 盤（必須是偶數）。正式訓練建議：40+
    'updateThreshold': 0.55,        # 新模型在對決中勝率必須超過 55% 才會被錄用為下一代 best.pth.tar
    'cpuct': 1.2,                   # MCTS 的探索與維護權重常數 (UCT 算法中的 C)

    # 5. 模型儲存與載入設定
    'checkpoint': './checkpoints/',  # 權重儲存資料夾
    'load_model': True,            # 是否要讀取舊模型繼續練
    'load_folder_file': ('./checkpoints/', 'best.pth.tar'),
    'numItersForTrainExamplesHistory': 15, # 保留過去幾輪的棋譜一起訓練

    # 6. 3D 神經網路內部超參數 (傳遞給 TicTacToe3DNNet)
    'lr': 0.001,                    # 學習率
    'dropout': 0.3,                 # 防止過擬合的丟棄率
<<<<<<< HEAD
    'epochs': 10,                    # 驗證用：每輪棋譜只練 2 個 Epoch。正式訓練建議：10~15
    'num_channels': 128,             # 3D-CNN 卷積核的通道深度
=======
    'epochs': 6,                    # 驗證用：每輪棋譜只練 2 個 Epoch。正式訓練建議：10~15
    'num_channels': 64,             # 3D-CNN 卷積核的通道深度
>>>>>>> origin/dev
})

def main():
    # 自動建立權重儲存資料夾，避免 PyTorch 存檔時找不到路徑崩潰
    if not os.path.exists(args.checkpoint):
        log.info(f"建立權重儲存路徑: {args.checkpoint}")
        os.makedirs(args.checkpoint)

    log.info('--- 步驟 1: 載入 3D 井字棋遊戲規則介面 ---')
    g = TicTacToe3DGame()

    log.info('--- 步驟 2: 初始化 PyTorch 3D 神經網路大腦 ---')
    nnet = NNet(g, args)

    if args.load_model:
        log.info(f"載入現有的模型權重: {args.load_folder_file[0]}{args.load_folder_file[1]}")
        nnet.load_checkpoint(args.load_folder_file[0], args.load_folder_file[1])
    else:
        log.info('未偵測到或未啟用舊權重，AI 將從純隨機的初始大腦開始進化。')

    log.info('--- 步驟 3: 喚醒 Coach 總教練，開始組裝自動化 Pipeline ---')
    c = Coach(g, nnet, args)

<<<<<<< HEAD
    log.info('--- 步驟 4: 啟動進化循環 (自對弈 -> 收集 -> 訓練 -> 對決 -> 遞代) ---')
=======
    log.info('--- 步驟 4: 啟動進化循環 (自對弈 -> 收集 -> 訓練 -> 對決 -> 跌代) ---')
>>>>>>> origin/dev
    c.learn()

if __name__ == "__main__":
    main()