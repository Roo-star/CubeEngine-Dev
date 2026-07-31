# STAL Function 1/2/3 — 統一 3D 驗收

## 啟動

```powershell
cd E:\CubeEngine\CubeEngine-Dev
C:\Users\Yingr\.pyenv\pyenv-win\versions\3.9.1\python.exe -m stal.workbench
```

1. 選擇 `Game 1`、`Game 2` 或 `Game 3`。
2. 按 `Load selected temporary game JSON`。
3. 按 `Generate / Apply JSON Rules`。
4. 按 `Open unified Ursina game window`。

三個 JSON 都暫時視為「已由 SRTP 產生的 3D 規則對象」。

## Game 1 — Single-cell Placement

文件：`stal/examples/demo_single_cell_placement_2x2x2.json`

規則：

- 點擊一個空座標，放入一個立方體。
- 已落子的座標不可重複使用，回傳 `occupied`。
- 每格都落子後完成。

JSON 中的 Function 3 條件：

```json
{
  "condition": {
    "type": "all_cells_equal",
    "state": 1
  },
  "result": {
    "status": "placement_complete",
    "is_terminal": true,
    "winners": ["selector"]
  }
}
```

## Game 2 — Intersection Selection

文件：`stal/examples/demo_multi_cell_selection_3x3x2.json`

規則：

- 球形節點代表線與線的交點，只有交點可點擊。
- 線段之間的空格不是 action coordinate，因此不可選。
- 點擊交點將其選中，再點一次取消選中。
- 可同時保留任意數量的交點，沒有「最多三個」限制。
- 這是持續操作範例，沒有配置終局條件；Function 3 正確回傳 `ongoing`。

## Game 3 — Moving Cube Collector

文件：`stal/examples/demo_free_cube_movement_4x3x2.json`

規則：

- 藍色小立方體從 `(0,0,0)` 開始，它是唯一的可移動主體。
- 鍵盤上下左右會依目前畫面方向，讓藍色立方體移動一格。
- PageUp／PageDown 可沿畫面深度對應的 Z 軸移動。
- 綠色小球是收集物；藍色立方體移到它的位置便視為吃到。
- 每吃一球，`collected` 加一，並在另一個空座標生成新球。
- 移動只受 XYZ 邊界限制；越界會被 Function 2 拒絕。
- 這是持續收集範例，沒有配置終局條件；Function 3 正確回傳 `ongoing`。

## Function 3 如何驗證

每次 Function 2 成功更新棋盤後：

1. Battlefield 完成全部 cell updates。
2. revision 增加一次。
3. Function 3 逐條讀取 JSON 的 `outcome_rules`。
4. 執行 `condition`。
5. 未命中時回傳 `ongoing`。
6. 命中時使用 JSON 的 `result` 產生 status、terminal、winner 和 reason。

目前臨時 Schema 支援三種可見、可測的條件：

- `all_cells_equal`
- `count_state_at_least`
- `state_at_coordinate`

Game 1 直接驗收 terminal outcome；另有自動測試臨時向 Game 2 注入 JSON
門檻、status 和 winner，再重新編譯並確認結果同步改變，用以證明 Function 3
讀取的是文件內容，而不是按照遊戲名稱寫死結果。Game 2、3 本身沒有
`outcome_rules`，可驗收「沒有終局規則時保持 ongoing」。

## Ursina 操作

- 鼠標懸浮任何格子：該格變成黃色高亮；單擊後才落子／選取／移動。
- 方向鍵：依目前畫面上下左右，映射到最近的 XYZ 邏輯方向來移動 Game 3。
- PageUp／PageDown：移動 Game 3 的 Z。
- 右鍵按住拖拽：使用 Ursina 原生 EditorCamera 繞棋盤自由 orbit。
- 中鍵拖拽：平移視角。
- 鍵盤 WASD：先從自由拖拽角度吸附回正面正交視圖，再將棋盤向該方向旋轉 90°；
  每次完成後只呈現一個軸對齊正面。
- 滾輪：縮放。
- `Z`：依序切換高亮 Z 層。
- `[`／`]`：向前／向後切換高亮 Z 層。
- `V`：顯示全部層。
- `R`：恢復 JSON 初始狀態。
- `H`：恢復正面、零旋轉、預設中心與距離。

3D 相機不再由 STAL 自製拖拽程式旋轉棋盤 root；hover ray、點擊 collider 和
視角全部使用 Ursina 原生 EditorCamera 的同一套座標系。

畫面不提供視角、移動或層切換按鈕；全部使用滑鼠和實體鍵盤。

如果只有 XYZ，Ursina 只啟用 Function 1 拓撲狀態編輯。

## 修改 XYZ

Workbench 的 `Apply XYZ to current JSON` 會保留完整規則文件：

- Game 1：建立新尺寸的空落子棋盤。
- Game 2：建立新尺寸的空交點選取網格。
- Game 3：可移動立方體保持在 `(0,0,0)`，初始收集球更新到
  `(X-1,Y-1,Z-1)`；吃球後仍依新尺寸的所有空座標生成。
