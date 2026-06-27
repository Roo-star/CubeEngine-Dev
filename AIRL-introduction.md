## 1. 地基:`tictactoe3d_logic.py` 提供什麼

這個文件是純 Python、無圖形依賴,已寫好並通過自我測試。它提供 3D 井字棋(3×3×3)的完整規則:

| 函數                           | 簽名              | 說明                                         |
| ------------------------------ | ----------------- | -------------------------------------------- |
| `get_cell(board,x,y,z)`        | `→ int`           | 讀某格(0 空 / 1 玩家1 / 2 玩家2)             |
| `is_valid_move(board,x,y,z)`   | `→ bool`          | 該步是否合法                                 |
| `get_legal_moves(board)`       | `→ [(x,y,z),...]` | 所有合法落子                                 |
| `check_winner(board)`          | `→ 0/1/2`         | 誰贏(0=還沒)                                 |
| `is_full(board)`               | `→ bool`          | 棋盤是否滿                                   |
| `game_status(board)`           | `→ str`           | `'win_1'/'win_2'/'draw'/'ongoing'`           |


| 函數                           | 簽名              | 說明                                         |
| ------------------------------ | ----------------- | -------------------------------------------- |
| `create_board()`               | `→ board`         | 建立空棋盤                                   |
| `get_cell(board,x,y,z)`        | `→ int`           | 讀某格(0 空 / 1 玩家1 / 2 玩家2)             |
| `set_cell(board,x,y,z,player)` | `→ None`          | 放子                                         |
| `is_valid_move(board,x,y,z)`   | `→ bool`          | 該步是否合法                                 |
| `get_legal_moves(board)`       | `→ [(x,y,z),...]` | 所有合法落子                                 |
| `check_winner(board)`          | `→ 0/1/2`         | 誰贏(0=還沒)                                 |
| `is_full(board)`               | `→ bool`          | 棋盤是否滿                                   |
| `game_status(board)`           | `→ str`           | `'win_1'/'win_2'/'draw'/'ongoing'`           |
| `ai_move_basic(board,player)`  | `→ (x,y,z)`       | 啟發式基本 AI(會擋會連),目前可視化的佔位對手 |
| `ALL_LINES`                    | `list`            | 49 條獲勝連線(含體對角線),已預先生成         |

| `N`                            | `3`               | 棋盤邊長                                     |
**棋盤表示**:`board` 是 3×3×3 的巢狀 list,`board[x][y][z]` ∈ {0,1,2}。座標 x/y/z 各取 0/1/2。

## <!--2. 訓練方案(對接 alpha-zero-general)--> 這是AI的方案，請根據實際情況參考！

### 3.1 alpha-zero-general 需要你實現的東西

alpha-zero-general 的架構是:給它一個 **Game 類**(描述遊戲規則)和一個 **NNet 類**(神經網絡),它自帶 MCTS 和自對弈訓練循環。所以你要做兩件事:

**(A) 寫一個 Game 適配類**,把 `tictactoe3d_logic.py` 的規則包裝成 alpha-zero-general 要求的接口。它要求的標準方法大致是:

| alpha-zero-general 要求的方法         | 你用 logic 文件怎麼實現                                      |
| ------------------------------------- | ------------------------------------------------------------ |
| `getInitBoard()`                      | 回傳 `create_board()`(可轉成 numpy array)                    |
| `getBoardSize()`                      | `(3, 3, 3)`                                                  |
| `getActionSize()`                     | `27`(3×3×3 個落子位置)                                       |
| `getNextState(board, player, action)` | 把 action(0–26)解碼成 (x,y,z),用 `set_cell` 落子,回傳新棋盤 + 換手 |
| `getValidMoves(board, player)`        | 用 `get_legal_moves` 產生長度 27 的 0/1 向量                 |
| `getGameEnded(board, player)`         | 用 `check_winner` / `is_full`:贏回傳 +1、輸 −1、平局回傳極小值、未結束回傳 0 |
| `getCanonicalForm(board, player)`     | 標準做法:`board * player`(讓網絡永遠從「當前玩家」視角看)    |
| `getSymmetries(board, pi)`            | 3D 對稱(旋轉/鏡像)做數據增強;**MVP 可先回傳 `[(board, pi)]` 不做增強**,先跑通再優化 |
| `stringRepresentation(board)`         | 把 board 轉成字串/bytes 當 MCTS 的 key(如 `board.tobytes()`) |

> action 編碼建議:`action = x*9 + y*3 + z`,解碼 `x=action//9, y=(action%9)//3, z=action%3`。保持一致即可。

**(B) 寫一個 3D-CNN(PyTorch),雙輸出頭**:

- 輸入:3×3×3 的棋盤張量(可加 channel 維,如 shape `(1,3,3,3)`)。
- 骨幹:幾層 3D 卷積(`nn.Conv3d`)。
- **兩個輸出頭**:
  - **policy 頭**:輸出 27 維(每個格子的落子機率),softmax。
  - **value 頭**:輸出 1 維(當前局面勝率),tanh 到 −1~1。
- 這對接 alpha-zero-general 的 NNet wrapper(`train` / `predict` / `save_checkpoint` / `load_checkpoint`)

### 3.2 訓練 pipeline(你已熟悉的 5 步)

1. 用 PyTorch 定義上面的 3D-CNN(雙頭)。
2. 用 alpha-zero-general 啟動前幾輪自對弈 → 得到訓練數據(狀態, 策略 π, 結果 z)。
3. 更新網絡權重。
4. 新版本與舊版本對弈 → 評估是否變強(arena)→ 得到新數據。
5. 更新權重 + 超參數調整(learning rate、batch size、MCTS 模擬次數 numMCTSSims 等)。
6. 循環迭代直到收斂。



## 3.★ 最關鍵:訓練好後,怎麼交回去替換 `ai_move`

可視化程式(Ursina)裡有一個**固定接口的插槽**:

python

```python
def ai_move(board):
    """
    輸入:當前棋盤 board(tictactoe3d_logic 的 3×3×3 list,0/1/2)
    輸出:AI 選擇的落子 (x, y, z)
    目前內部呼叫 ai_move_basic(board, 2)（佔位）。
    """
    ...
```

**你要做的,就是提供一個「同樣接口」的版本,把內部換成你的模型推理:**

python

```python
# ai_move_trained.py（你交付的文件,範例骨架）
import torch
from tictactoe3d_logic import get_legal_moves
# from your_nnet import YourNNet   # 你的網絡定義

_model = None

def _load_model():
    global _model
    if _model is None:
        _model = YourNNet()                       # 建立網絡
        _model.load_state_dict(torch.load("model_best.pt"))  # 加載訓練好的權重
        _model.eval()
    return _model

def ai_move(board):
    """
    與佔位版完全相同的接口:輸入 board,輸出 (x,y,z)。
    內部:用訓練好的模型(可加 MCTS)選出最佳落子。
    """
    model = _load_model()

    # 1. 把 board 轉成模型輸入張量(shape 要和訓練時一致,例如 (1,1,3,3,3))
    state = board_to_tensor(board)

    # 2. 模型推理 → policy(27 維機率)
    with torch.no_grad():
        policy, value = model(state)
    policy = policy.squeeze().cpu().numpy()

    # 3. 只在合法落子中選機率最高的(避免選到已佔格)
    legal = get_legal_moves(board)             # [(x,y,z), ...]
    best, best_p = None, -1.0
    for (x, y, z) in legal:
        a = x*9 + y*3 + z                      # 與訓練時的 action 編碼一致
        if policy[a] > best_p:
            best_p, best = policy[a], (x, y, z)
    return best

    # 進階:也可在這裡跑 MCTS（用訓練好的網絡引導）再回傳落子,棋力更強。
```

### 交付的接口契約(務必遵守,否則接不上)

| 項目    | 規定                                                         |
| ------- | ------------------------------------------------------------ |
| 函數名  | `ai_move(board)`                                             |
| 輸入    | `board` = `tictactoe3d_logic` 的 3×3×3 巢狀 list,值 0/1/2    |
| 輸出    | 一個 tuple `(x, y, z)`,且必須是**合法空格**(在 `get_legal_moves` 裡) |
| 不可    | 不要改 board(不要在函數裡 set_cell 修改原棋盤);只回傳座標    |
| AI 扮演 | 在可視化中 AI 是玩家 2,但 `ai_move` 內部如需 player 參數,固定用 2 |

**只要這個接口一致,替換就是「把可視化裡的 `ai_move` 指向你的版本」這一步的事,其他程式完全不動。**

### 最終交付清單

1. **訓練好的模型權重檔**(如 `model_best.pt`)。
2. **網絡定義**(你的 `YourNNet` 類,讓加載權重時能重建網絡)。
3. **`ai_move(board)` 函數**(內部加載模型 + 推理,接口如上)。
4. (附)Game 適配類與訓練腳本,方便日後重訓/調參。