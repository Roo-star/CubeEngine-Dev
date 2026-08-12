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
  → 以來源 X/Y 為預設，設計師可調整目標 X/Y/Z
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

來源：[Anish Kumar Vedant / Snake-game](https://github.com/anishvedant/Snake-game)，MIT；包含完整 Pygame 素材、聲音、分數與暫停介面。

Function 1 從未修改的來源中辨識：

- 20×20 邏輯晶格
- 方向鍵、Space 暫停與 Escape 離開
- 125ms 更新間隔（8Hz）
- snake ordered body、food collectible
- food 隨機生成
- 越界／自身碰撞終止

3D 不可只增加棋盤 Z：必須同時增加 ±Z 控制、3D 身體碰撞、3D 食物生成與原視覺的立體表達。

### Minesweeper

來源：[pygame-minesweeper](https://pypi.org/project/pygame-minesweeper/)，MIT；包含經典 sprite、計時、旗標、重開與完整核心規則。

Function 1 正確區分：

- Basic 預設為 10×10、10 mines，並保留其他 difficulty/custom 模式
- 左鍵揭露、右鍵依次切換旗標／問號／未標記
- 第一次揭露保護、零鄰雷 flood reveal
- hidden mine map、revealed map、flags、timer 與剩餘 mine 顯示
- 點中 mine 失敗；所有安全格揭露即勝利

3D lift 將同一鄰域、mine count、flood reveal、旗標與 mine distribution 一起升維。Mine 數量按來源密度縮放至 X×Y×Z，使用唯一座標並在第一次點擊時隨機生成；Workbench 直接顯示公式與結果，來源文件不被改寫。

### 2048

來源：[Rajit Banerjee / 2048-pygame](https://github.com/rajitbanerjee/2048-pygame)，MIT。

Function 1 讀取完整多文件專案及素材，辨識：

- 4×4 logical board
- 全盤四方向 shift/merge
- 2/4 隨機生成
- WIN / LOSE / PLAY 狀態
- light/dark source modes
- JSON 中獨立的尺寸、padding、font 與 font size
- Pygame 圖片素材及原輸入映射；兼容 Pygame 1/2 方向鍵碼
- 2D 可用方向鍵、WASD 或滑鼠拖曳；3D 以方向鍵／滑動／空間軸 handle 避開 WASD 視角衝突

其中視覺 JSON 值可以在衍生副本修改；4×4 則硬編碼於多個算法，不能以單一尺寸欄位安全改寫。

### Turtle Connect

先前採用的 Free Python Games 教學檔在原始碼中明確把 winner detection
留作 TODO，因此當時的 N/A 並不是解析器漏讀，而是來源根本沒有可讀取的
勝負規則；用該檔驗收 Function 1 是錯誤的產品選擇。

Workbench 現改用獨立標示的完整 Turtle Connect Four 來源。Function 1 可從
來源證明 7×6、`CONNECT_N = 4`、點擊落子、滿盤和局及連線勝利。3D 版本保留
connect-four 長度，並在立方網格的 13 組無重複直線方向判定、顯示勝者與勝利
座標。這條終局不是 Ursina 預覽自行猜出的規則。

## 7. Presentation mapping

Function 1 現會在 Rule Schema 的 `ui_hints.presentation_mapping` 保存素材角色、
映射策略與未解項。已證明的 tile、sprite、顏色、字體和 vector primitive 才會
自動映射；數字與格子狀態被合成為同一個立方體表面材質，不再使用會與棋盤
分離的浮動文字。

單張正面圖片不能唯一決定物件背面、深度、拓撲、骨骼和遮擋面，因此不能把
這類推測冒充「自動忠實轉化」。這些項會留給 Function 2、專用 renderer adapter
或設計師確認。完整分層與驗收約束見 [SRTP_PRESENTATION_MAPPING.md](SRTP_PRESENTATION_MAPPING.md)。

## 8. Workbench 驗收

啟動：

```powershell
cd E:\CubeEngine\CubeEngine-SRTP
C:\Users\Yingr\.pyenv\pyenv-win\versions\3.9.1\python.exe -m srtp.workbench
```

建議驗收順序：

1. 從左側 Source Library 選擇遊戲；切換選項會立即載入與分析。
2. 保持上方 `Source 2D`，按唯一的 `PLAY` 控制；原版會在原生 Windows 遊戲視窗運行。
3. 右側 Inspector 顯示唯讀 Source plane 與可調的 Target `X / Y / Z`；初值採來源 X/Y。下方直接說明隨機生成／密度／Z 升維策略。
4. 切換上方 `Transformed 3D`，按同一個 `PLAY`；已註冊的來源適配器會開啟 Ursina Play Mode。
5. Rule Schema、Diagnostics 與 Function 2 handoff 收在左側 Analysis，不佔據主要設計工作流。

Windows 不再把 Turtle/Pygame 視窗強制 reparent 到 Dear PyGui。實測這會讓子視窗被 GPU viewport 遮蔽；原版與 Ursina 因此使用可靠的原生獨立視窗。若「任意外來遊戲視窗穩定內嵌」成為硬需求，預覽外殼應遷移到正式支援 `QWindow::fromWinId()` / `createWindowContainer()` 的 PySide6/PyQt：<https://doc.qt.io/qt-6/qtdoc-demos-windowembedding-example.html>。

## 9. 當前能力與邊界

| 來源 | 原版運行 | 全專案盤點 | 規則靜態分析 | 安全資料變體 | 忠實 3D compiler |
|---|---:|---:|---:|---:|---:|
| Python/Turtle | 是 | 是 | 部分，附證據 | 依來源 | Connect 適配器 |
| Python/Pygame | 是 | 是 | 部分，附證據 | JSON／已證明單一 literal | Snake / Minesweeper / 2048 適配器 |
| Canonical JSON | 無原遊戲可運行 | 單檔 | 是 | 是 | 僅既有 declarative subset |
| HTML/JavaScript | 瀏覽器基線 | 基礎 | 尚未 | 尚未 | 尚未 |
| Unity/C# | 需對應 Unity 版本 | 尚未 | 尚未 | 尚未 | 尚未 |
| Godot | 需對應 Godot 版本 | 尚未 | 尚未 | 尚未 | 尚未 |

目前四個 reference 的 3D compiler 是明確註冊的 vertical slice，不是任意 Python 遊戲的通用轉化器。未註冊來源仍必須停在 `needs_adapter`，不能退回通用立方體。
