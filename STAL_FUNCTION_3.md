# STAL Function 3 — 局面、目標與終局判定

## 產品定義

Function 3 接收 SRTP 提供的判定規則，讀取 STAL 的當前棋盤狀態，輸出一個讓
3D UX、AI Engine、訓練管線和 IPC 都能理解的統一 Outcome Report。

它回答的是：

- 當前局面是否仍在進行？
- 是否已達成某個目標或終止條件？
- 狀態名稱和原因是什麼？
- 如遊戲存在競爭者，誰勝、誰負？
- 如遊戲採計分制，目前分數是多少？
- 哪一條 SRTP 規則產生了這個結果？

Function 3 不等於「檢查幾子連線」。連線只是 SRTP 可以提供的一種 evaluator。
其他 evaluator 可以是到達目標、無合法動作、消除完成、存活時間、分數門檻、
捕獲目標、資源耗盡或任何設計師定義的條件。

## 核心要求

STAL 負責：

1. 無副作用地執行所有已配置的 outcome rules。
2. 沒有規則命中時明確回傳 `ongoing`，不自行猜測平局或勝負。
3. 分離 `status` 和 `is_terminal`；例如 `score_update` 可以是非終局狀態。
4. 支援沒有玩家、單一玩家、團隊、多人、多贏家與無贏家的結果。
5. 以明確 priority 處理同時命中的條件。
6. 同優先級規則結果矛盾時拋出 conflict，禁止靜默選擇一個錯誤答案。
7. 回傳 board revision、命中的 rule IDs、原因、分數和自訂 details。
8. 可掛回 Battlefield evaluation hook，讓 Function 2 套用動作後自動得到判定。

SRTP 負責：

- 判定條件的語義與算法。
- 規則 ID 和優先級。
- 哪些狀態屬於 terminal。
- winner／loser／draw／score 的遊戲含義。
- 判定所需的玩家、回合、資源、計時等 context。

## 為何不能內建「所有勝負種類庫」

玩法種類不是封閉集合。若 STAL 嘗試枚舉「連線、翻轉、蛇、塔防、消除……」，
每遇到新遊戲都必須修改核心，SRTP 也失去存在意義。

較穩定的設計是：

- SRTP 保存可擴展的規則語彙與轉換器。
- SRTP 把規則編譯成 `OutcomeRule` callback。
- STAL 只保證 callback 的執行、輸出格式、優先級、衝突和狀態一致性。

若某種高頻規則值得複用，例如 N 維連線檢查，可以未來建立
`SRTP rule library`，但不能把它當作 STAL 的強制預設。

## 核心 API

```python
from stal import (
    Battlefield,
    GridRules,
    OutcomeEngine,
    OutcomeRule,
    OutcomeSignal,
)

board = Battlefield(GridRules(4, 3, 2))

def goal_rule(state, context):
    if state.get_cell(context["goal"]) == context["target_state"]:
        return OutcomeSignal(
            status="goal_reached",
            is_terminal=True,
            reason="The configured target was reached.",
            winners=(context["actor"],),
            scores={context["actor"]: 1},
        )
    return None

outcomes = OutcomeEngine(board, (
    OutcomeRule("reach_target", goal_rule, priority=100),
))

report = outcomes.evaluate({
    "goal": (3, 2, 1),
    "target_state": 7,
    "actor": "team_a",
})
```

`OutcomeSignal.status` 是開放字串，不被限制為 `win/loss/draw`。`is_terminal`
才是 AI 或遊戲循環停止搜索的可靠字段。

若要讓每次 Function 2 動作後自動判定：

```python
outcomes.attach_to_battlefield(context_provider)
```

Function 2 的 `TransitionResult.evaluation` 和 `board.last_evaluation` 隨即會帶有
Function 3 的結果。

## 多條規則同時命中

例如「達成勝利」和「棋盤已滿」可能在最後一步同時成立。SRTP 應設定：

- 勝利規則：priority 100
- 棋盤滿平局：priority 10

Function 3 選擇最高優先級結果。若兩條 priority 100 的規則分別宣告不同贏家，
引擎回報 `OutcomeConflictError`，提示 Rule Schema 有歧義，而不是依註冊順序
隨機決定結果。

## 和 Grid-Based 玩法種類的關係

- 格內落子／交叉點落子：兩者都可映射到離散 coordinate；外觀錨點由 3D UX
  決定，落子合法條件由 SRTP 決定。
- 翻轉棋：Function 2 一個 action 原子更新多格；Function 3 由 SRTP 判斷無步、
  棋盤結束及分數。
- 移動類遊戲：Function 2 使用 source／target action；Function 3 判斷捕獲、
  到達、分數或無合法移動。
- 貪吃蛇：單步的格子變更可由 Function 2 執行，但蛇身順序、方向、速度、食物
  等完整狀態不能只依賴目前的單層三維陣列。完整支援前應先將 Board State
  擴成 Game State；碰撞／死亡等語義仍由 SRTP evaluator 定義。

## GUI 驗收

在專案目錄執行：

```powershell
C:\Users\Yingr\.pyenv\pyenv-win\versions\3.9.1\python.exe -m stal.workbench
```

1. 在 `Temporary game examples` 選擇一個 JSON。
2. 按 `Load selected temporary game JSON`。
3. 按 `Generate / Apply JSON Rules`。
4. 在右側 Function 2 Action Space 點擊 action。
5. 觀察 Board State、拒絕訊息、revision 和 Function 3 Outcome Report。
6. 按 `Reset current state` 可重新載入 JSON 的初始狀態。
7. 也可按 `Open unified Ursina game window`，在 3D 交互後直接查看 F3 狀態。

這些規則只供 STAL 離線驗收，不代表任何正式遊戲，也不取代 SRTP。
Ursina 永遠先生成 Function 1；若同一規則對象存在 F2/F3，便在相同視窗啟用。

## 驗收標準

- 無規則時回傳 ongoing，不假設玩家或平局。
- 支援 terminal／non-terminal、自訂狀態、分數及零至多個參與者。
- 判定只讀狀態，不修改棋盤或 revision。
- 多規則優先級結果穩定。
- 同級矛盾不被掩蓋。
- SRTP evaluator 錯誤和非法回傳值能被清楚識別。
- Function 2 動作後可透過 Battlefield hook 自動取得結果。
- 自動測試與 GUI 測試規則均不依賴井字棋連線。
