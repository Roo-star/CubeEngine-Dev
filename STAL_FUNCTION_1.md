# STAL Function 1 — X × Y × Z 空間拓撲生成

## 已校正的職責邊界

STAL 不是遊戲規則引擎。它的工作是把 SRTP 已確認的空間尺寸生成為一個有單一真實狀態的三維棋盤／戰場：

- 建立 `cells[x, y, z]` 三維整數陣列；每格初始狀態為 `0`。
- 固定零基座標系與 X/Y/Z 順序，提供 `get_cell`、`set_cell`、`clear_cell`、範圍檢查與完整座標列舉。
- 提供穩定的「座標索引」編碼，方便未來 AI 或 UI 互相定位格子；它不是遊戲 action 或合法步。
- 保存棋盤 revision、傳送狀態變更事件、提供棋盤快照，讓 GUI、IPC 和 AI 都讀同一個核心狀態。
- 提供 SRTP 掛接點：寫入合法性驗證和任何勝負／分數／局面判定。

STAL **不**要求連線長度、玩家、AI 對手、輪替、落子規則、勝負條件或平局規則。這些屬於 SRTP 的完整 Rule Schema。MVP 的 3D 井字棋會由 SRTP 之後提供「三子連線」規則，但它不是 STAL 的預設行為。

## 最小輸入契約：只有 XYZ

```json
{
  "schema_version": "cubeengine.srtp-stal/topology-v1",
  "game_id": "empty_3d_grid",
  "dimensions": {"x": 3, "y": 3, "z": 3},
  "metadata": {"display_name": "3 x 3 x 3 Empty Topology"}
}
```

只有 `dimensions.x`、`dimensions.y`、`dimensions.z` 是 STAL 生成所必填的參數，且必須是正整數。`game_id` 和 `metadata` 有預設值，便於保存與辨識。

「坐標慣例、初始空格狀態、陣列型別、越界行為」確實也是必要的引擎決策，但不需要讓設計師每局重新配置：STAL 統一為零基 `(x,y,z)`、`cells[x,y,z]`、初始 `0`、越界拒絕。這讓 XYZ 已足以安全生成一個空間戰場。

如果某遊戲需要地形、單位、物品等複合格子資料，下一階段可在不改 XYZ 契約下新增 state layer 或 cell metadata；不要現在把它誤設成玩家／棋子規則。

## 核心 API

```python
from stal import Battlefield, GridRules

board = Battlefield(GridRules(x=4, y=3, z=2))
board.set_cell((0, 0, 0), 17)       # 17 的遊戲意義由 SRTP 定義
state = board.get_cell((0, 0, 0))
is_addressable = board.is_valid_write((1, 1, 1), 99)
coordinates = board.coordinates()
snapshot = board.board_copy()
assessment = board.assess_topology()
```

`register_write_validator()` 是 SRTP 為落子、移動、重力、地形限制等規則掛接的入口。`register_evaluation_hook()` 是 SRTP 為勝負、平局、分數或自訂目標掛接的入口。沒有掛接規則時，`evaluate()` 回傳 `unresolved`，不會假裝知道遊戲結果。

## 視覺驗收

在 `E:\CubeEngine\CubeEngine-Dev` 執行：

```powershell
python -m unittest discover -s tests -v
python -m stal.workbench
```

Dear PyGui 工作台可使用完整規則 JSON，或在沒有規則文件時使用暫時的
`3 x 3 x 3` 文字模板。XYZ template 只更新目前 JSON 的 `dimensions`，不會
再刪除 Function 2/3 規則。對三個臨時遊戲，依賴尺寸的初始狀態也會同步調整。

按 `Open unified Ursina game window` 後：

- 只有 XYZ 時，提供 Function 1 拓撲與 `0/1` 狀態編輯。
- 有 action/outcome 規則時，同一個 XYZ 棋盤直接啟用 Function 2/3 交互。
- 支援 Ursina 原生 EditorCamera：右鍵 orbit、中鍵平移、滾輪縮放，以及
  實體鍵盤 WASD 90°軸對齊視角、鍵盤 Z 層高亮和座標懸停。

這個 `0/1` 點擊只是 STAL 視覺驗收的 state 編輯，不表示玩家、對手或任何遊戲規則。

## 輕量合理性審查

STAL 只可判斷拓撲規模：超過 512 格會提示 3D 視口成本，任一軸超過 32 格會建議使用切片／UX 配置。狀態空間、每回合 action 數、MCTS 成本、勝負線數都取決於 SRTP 規則，不能在沒有規則時由 STAL 臆測。

## 目前與下一步

11 項自動測試驗證 XYZ 陣列、非立方尺寸、讀寫、座標索引、SRTP validator/evaluator 掛接、拓撲警示與 JSON 輸入。

OpenAI API 環境已準備好，但 GPT 轉換器會在 SRTP 開始時實作：自然語言 → 完整 Rule Schema → Schema 驗證 → 取出 XYZ 交給 STAL。這避免 LLM 或 UI 越過 SRTP 直接改寫棋盤規則。
