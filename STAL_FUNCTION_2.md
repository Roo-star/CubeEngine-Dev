# STAL Function 2 — 合法性判定與狀態轉移

## 產品定義

Function 2 的工作不是定義某一款遊戲「什麼棋可以怎麼走」，而是執行 SRTP
已提供的動作規則：

1. 保存一個有限、離散且穩定編碼的完整 action space。
2. 對指定棋盤 revision 無副作用地判斷單一動作是否合法。
3. 列出當前局面的全部合法動作，並產生供 MCTS／Policy Network 使用的固定長度 mask。
4. 預覽執行動作後的棋盤，不改動正式狀態。
5. 將合法動作轉換為一個或多個單格更新，並以一次 revision 原子提交。
6. 用結構化原因拒絕未知、違規、過期或產生非法狀態轉移的動作。

因此它服務三類使用者：

- 3D UX：點擊前檢查、顯示不能操作的原因、避免提交舊畫面的動作。
- AI Engine：取得 action 數量、合法 action codes、0/1 mask 與 next state。
- SRTP：插入真正的遊戲語義，而不讓規則寫死在 STAL。

## 職責邊界

STAL 必須自行保證的基礎合法性：

- action code 存在，且 action space 的 code 必須是 `0..N-1`，才能形成穩定 AI mask。
- 套用時可指定 `expected_revision`，拒絕基於過期局面的請求。
- transition 至少更新一格；座標必須在 XYZ 範圍內；狀態必須為整數。
- 同一 transition 不可重複寫同一格；所有更新先驗證後一次提交，失敗不留下半步狀態。

SRTP 必須提供的遊戲規則：

- action 的種類和參數，例如落子、移動、交換、旋轉、技能或 pass。
- 當前狀態下的合法條件，例如空格、地形、資源、玩家權限、回合或路徑。
- 合法 action 如何轉成棋盤格更新。
- 如規則需要玩家、回合、庫存或階段，透過 `context` 傳入；STAL 不建立預設玩家。

Function 2 **不要求**玩家／AI 對手、輪替、空格落子、連線長度、勝負條件或
棋盤填滿。這些不是所有遊戲共有的合法性條件。

## MVP 限制

目前支援「有限、離散、可預先枚舉，且結果可表達為一組整數 cell updates」的動作。
這足以服務多數回合制 Grid-Based 遊戲和 MCTS。

以下能力應在後續 Rule Schema 明確需要時擴展，而不是現在猜測：

- 無限／連續 action space。
- 動作直接修改棋盤以外的完整 game state（手牌、資源、計時器等）。
- 隨機結果、同時行動或非確定性 transition。
- 網路多寫者的強一致性；目前 revision 防護針對本機 UI／AI 工作流。

若新遊戲包含上述資料，後續應把 Function 1 的 Board State 擴展為可版本化的
Game State，再讓相同 Action Engine 操作它。

## 核心 API

```python
from stal import (
    ActionEngine,
    ActionRejection,
    Battlefield,
    CellUpdate,
    GridRules,
    coordinate_write_actions,
)

board = Battlefield(GridRules(4, 3, 2))
actions = coordinate_write_actions(board)  # 只建立 code ↔ coordinate，不附加規則

def legality(state, action, context):
    coordinate = action.parameters["coordinate"]
    if state.get_cell(coordinate) != 0:
        return ActionRejection("occupied", "Target cell is occupied.")
    if "piece_state" not in context:
        return ActionRejection("missing_piece", "SRTP did not provide piece_state.")
    return None

def transition(state, action, context):
    return (
        CellUpdate(action.parameters["coordinate"], context["piece_state"]),
    )

engine = ActionEngine(board, actions, legality, transition)

engine.action_count
engine.all_actions()
engine.validate(0, {"piece_state": 7})
engine.legal_action_codes({"piece_state": 7})
engine.legal_action_mask({"piece_state": 7})
preview = engine.next_state(0, {"piece_state": 7})
result = engine.apply(0, {"piece_state": 7}, expected_revision=0)
```

`Action.parameters` 對 STAL 是不透明資料；其含義只由 SRTP rule adapter 解釋。
移動類動作可回傳兩個 `CellUpdate`（清除起點、寫入終點），兩格只增加一次
board revision，UI 也只收到一個包含全部 changes 的 `action_applied` 事件。

## SRTP、UI、AI 的配合準備

SRTP Rule Schema 後續至少要能產生：

- `action_schema_version` 與穩定 action catalog；
- legality rule adapter；
- transition rule adapter；
- action 所需 context 欄位及型別；
- 若有終局／分數判定，另接 Function 3 evaluation hook。

3D UX 不可直接呼叫 `set_cell()` 模擬玩家動作；應先 `validate()`，再以當時的
revision 呼叫 `apply()`。編輯器／除錯工具仍可直接改棋盤。

AI Engine 應使用 `action_count` 建 policy head，以 `legal_action_mask()` 遮罩非法
動作，以 `next_state()` 展開搜尋節點。模型 artifact 必須記錄 action schema
version；action code 意義改變時，舊模型不可直接沿用。

## 驗收標準

- 非立方 XYZ 棋盤的 action code 穩定且無重複。
- 合法判定和 next-state 預覽不改變正式棋盤。
- 未知 action、規則拒絕、越界 transition、重複寫入與 stale revision 均被拒絕。
- 多格動作要麼全部成功，要麼完全不改變棋盤。
- 合法 action list 和 mask 與單步 `validate()` 結果一致。
- 不註冊任何玩家、輪替或連線規則，也能配置並執行非棋類動作。

執行測試：

```powershell
C:\Users\Yingr\.pyenv\pyenv-win\versions\3.9.1\python.exe -m unittest discover -s tests -v
```

也可以執行 `python -m stal.workbench`，在 `Temporary game examples` 載入
三個臨時 SRTP-style JSON 規則。Function 2 的所有 action 可在 Dear PyGui
工作台中逐項檢查，也可按 `Open unified Ursina game window` 在三維棋盤上
直接點擊或使用方向鍵遊玩。兩個界面讀取的是同一份完整規則對象。
