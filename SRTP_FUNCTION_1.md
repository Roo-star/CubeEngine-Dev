# SRTP Function 1 — 完整源遊戲匯入與規則讀取

## 1. 產品定義修正

Function 1 的輸入不是一份為 CubeEngine 特製的參數 JSON，而是使用者原本可以運行的遊戲專案、入口腳本、依賴與素材。

Function 1 必須分開回答三個問題：

1. **原版能否運行**：原入口、原素材、原控制和原時間流程是否仍可玩。
2. **CubeEngine 理解了多少**：每項空間、狀態、動作、隨機性、目標與終局判定是否有來源證據。
3. **是否已能忠實升維**：每一個引用 X/Y 的機制是否已定義對應的 Z 行為。

`原版可玩`、`規則已理解`、`3D 已編譯` 是三個不同狀態，不能互相冒充。

Rule Schema 仍然存在，但它只是有證據的中間表示（IR），不是原遊戲，也不能單獨證明轉化成功。

## 2. 正確的使用流程

```text
完整源遊戲專案
  → 專案盤點（入口、語言、框架、依賴、素材、授權）
  → 原版 2D 啟動（fidelity reference）
  → 全專案靜態分析（不執行來源）
  → Rule Schema + 來源行號 + 理解覆蓋率
  → 僅顯示來源確實存在的可調項
  → 保留 X/Y，設計 Z
  → 逐項完成 movement / collision / spawn / neighbourhood / outcome / renderer lift
  → 忠實的 3D 重建版本
```

沒有通過前四步，不應開啟一個通用立方體假裝它是轉化結果。

## 3. 為何需要 importer / adapter

成熟引擎並不使用一個萬用表單讀懂所有來源格式：

- Unity 以內建 importer 和 `ScriptedImporter` 處理不同資產格式，並為每種來源提供專用 import settings：<https://docs.unity3d.com/cn/2022.2/Manual/BuiltInImporters.html>
- Godot 的 import plugin 必須聲明自己能理解的副檔名、輸出資源類型及 import 參數：<https://docs.godotengine.org/en/stable/tutorials/plugins/editor/import_plugins.html>
- Libretro 也不是自動理解任意遊戲；原程式需先實作統一的輸入、音訊、影像及逐幀執行 API：<https://docs.libretro.com/development/cores/developing-cores/>

CubeEngine 因此採用兩級介面：

- `Source importer`：理解 Python、JavaScript、Unity/C#、Godot 等來源專案的結構與語義。
- `Mechanic/renderer adapter`：把來源框架中的移動、繪圖、碰撞、計時等行為忠實映射到 3D。

聲稱「完全自動理解並等價轉換任意語言、任意引擎、任意遊戲」在技術上不可驗證。動態程式碼、反射、原生插件、物理引擎、網路服務與缺失素材都可能只在運行時決定行為。可行方案是 importer 插件 + 靜態證據 + 受控運行觀測 + LLM/設計師確認 + fidelity tests。

## 4. Source Game Package

Function 1 的正式輸出是 `cubeengine.srtp/source-game-package-v1`，內含：

- 原專案根目錄與入口
- 原版啟動命令、工作目錄、框架與依賴
- 原始檔／素材清單與整體 hash
- 授權與上游來源
- Rule Schema IR
- 每項來源參數的原值、位置、適用性與修改方式
- 規則理解覆蓋率
- 2D→3D mechanic lift 清單及狀態
- Function 2 LLM handoff

詳細欄位見 [SRTP_SOURCE_GAME_PACKAGE.md](SRTP_SOURCE_GAME_PACKAGE.md)。

## 5. 來源參數的狀態

Workbench 不再顯示通用的 anchor / flow / randomness 下拉選單。這些是原規則，不是設計師隨意切換的遊戲類型。

每個來源項必須屬於以下狀態之一：

| 狀態 | 含義 | Workbench 行為 |
|---|---|---|
| `data_file` | 來源明確存於獨立 JSON 等資料檔 | 可在衍生副本安全修改 |
| `runtime_argument` | 來源入口正式提供參數 | 可調整後重新啟動 |
| `adapter_setting` | importer 能證明是孤立的框架設定 | 可調整 |
| `source_patch` | 值寫在多處程式邏輯中 | 顯示來源，但不能假裝單欄修改安全 |
| `fixed_by_source` | 這是遊戲身份的一部分 | 顯示唯讀／N/A |
| `unresolved` | Function 1 無法證明 | 交給 Function 2 或設計師確認 |

安全修改永遠建立新的 source-derived variant，保留上游來源不變。

## 6. 真實開源驗收遊戲

### Snake

來源：[Grant Jenks / Free Python Games](https://github.com/grantjenks/free-python-games)，Apache-2.0。

Function 1 從未修改的來源中辨識：

- 38×38 有效移動晶格（由 `inside()` 邊界與 10 單位步長共同推導）
- 鍵盤四方向輸入
- 100ms 更新間隔（10Hz）
- snake ordered body、food collectible
- food 隨機生成
- 越界／自身碰撞終止

3D 不可只增加棋盤 Z：必須同時增加 ±Z 控制、3D 身體碰撞、3D 食物生成與原視覺的立體表達。

### Minesweeper

來源同上，Apache-2.0。

Function 1 正確區分：

- 內部 10×10 padded neighbour map
- 玩家真正可點擊的 8×8 surface
- cell click/reveal
- hidden bombs / shown / neighbour counts 三組狀態
- 8 個隨機 mine placements
- 點中 mine 的失敗條件
- 來源沒有完整寫出「揭開所有安全格」的勝利檢查，因此 goals/outcomes 覆蓋率為 partial，而不是自行補規則

3D lift 需要將同一來源鄰域、mine count、flood reveal 與 mine distribution 一起升維。

### 2048

來源：[Rajit Banerjee / 2048-pygame](https://github.com/rajitbanerjee/2048-pygame)，MIT。

Function 1 讀取完整多文件專案及素材，辨識：

- 4×4 logical board
- 全盤四方向 shift/merge
- 2/4 隨機生成
- WIN / LOSE / PLAY 狀態
- light/dark source modes
- JSON 中獨立的尺寸、padding、font 與 font size
- Pygame 圖片素材及原輸入映射

其中視覺 JSON 值可以在衍生副本修改；4×4 則硬編碼於多個算法，不能以單一尺寸欄位安全改寫。

## 7. Workbench 驗收

啟動：

```powershell
cd E:\CubeEngine\CubeEngine-SRTP
C:\Users\Yingr\.pyenv\pyenv-win\versions\3.9.1\python.exe -m srtp.workbench
```

建議驗收順序：

1. 選擇一個真實開源遊戲。
2. 按 `Import project / understand rules`。
3. 先按 `Run original 2D`，確認原遊戲、素材和控制正常。
4. 查看 `Source-backed parameters` 的值、來源行號、適用性及 edit mode。
5. 查看 Rule Schema IR 與 coverage；partial 必須說明缺什麼。
6. 保留來源 X/Y，輸入 Z；查看 `3D lift plan`。
7. 在 lift 尚未編譯時，`Open faithful transformed preview` 必須拒絕開啟通用立方體。

Windows 上會嘗試把 Turtle/Pygame 原視窗嵌入 Workbench；框架不接受 reparent 時會保留為獨立原版視窗。Pygame 官方提供 `SDL_WINDOWID` 嵌入方式，但並非所有來源都在建立 display 前遵守該契約：<https://www.pygame.org/docs/ref/display.html>。Qt 對 foreign window 有正式的 `QWindow::fromWinId()` / `createWindowContainer()` 支援；若「穩定嵌入任意原版視窗」成為產品硬需求，預覽外殼應遷移至 PySide6/PyQt：<https://doc.qt.io/qt-6/qtdoc-demos-windowembedding-example.html>。

## 8. 當前能力與邊界

| 來源 | 原版運行 | 全專案盤點 | 規則靜態分析 | 安全資料變體 | 忠實 3D compiler |
|---|---:|---:|---:|---:|---:|
| Python/Turtle | 是 | 是 | 部分，附證據 | 依來源 | 尚未 |
| Python/Pygame | 是 | 是 | 部分，附證據 | JSON data 可用 | 尚未 |
| Canonical JSON | 無原遊戲可運行 | 單檔 | 是 | 是 | 僅既有 declarative subset |
| HTML/JavaScript | 瀏覽器基線 | 基礎 | 尚未 | 尚未 | 尚未 |
| Unity/C# | 需對應 Unity 版本 | 尚未 | 尚未 | 尚未 | 尚未 |
| Godot | 需對應 Godot 版本 | 尚未 | 尚未 | 尚未 | 尚未 |

這個 Function 1 版本已建立正確入口與驗收邏輯，但沒有宣稱 Snake、Minesweeper 或 2048 已完成 3D 轉化。忠實 3D compiler 是後續 SRTP 2D→3D mapping 與 framework adapter 的工作。
