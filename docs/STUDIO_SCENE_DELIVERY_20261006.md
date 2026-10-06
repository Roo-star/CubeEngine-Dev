# CubeEngine Studio：中央真實場景與桌面交互重做

2026-10-06，開發分支 `WorkbenchAI`，修改前基準 `000fd8f`。本輪按使用者最新回饋重做：中央必須是實際場景，不能用抽象拓樸立方體代替遊戲；UI 必須有可讀字級、層級、圖示、明確操作與狀態。先前 A 版「正式遊戲另開視窗」的設計已被取代。原始四 IR、批准產物及遊戲程式均未手改。

## 開啟

雙擊本目錄的 **Run CubeEngine Workbench.bat**。預設入口已切換到新版 Qt Studio；目前這台機器的 `.venv-workbench` 已備妥。

在其他 Windows 開發環境，先依專案原本方式安裝引擎依賴，再建立獨立環境：

```powershell
python -m venv --system-site-packages .venv-workbench
.\.venv-workbench\Scripts\python.exe -m pip install -r requirements-workbench.txt
.\.venv-workbench\Scripts\python.exe -m srtp.workbench
```

可同時指定來源與批准場景：

```powershell
.\.venv-workbench\Scripts\python.exe -m srtp.workbench --source srtp/reference_games/pygame_tictactoe/main.py --project tests/fixtures/workbench_training_target_20261004/project.manifest.json
```

`--legacy-ui` 只保留舊 DPG 驗收入口，不是預設產品，也不會在 Qt 缺失時偷偷退回舊畫布。

## 現在可見、可操作的產品

| 區域 | 實際交互 |
| --- | --- |
| 左側工具列 | 統一向量圖示；匯入、物件、資產、訓練設定、實驗、診斷、轉換與說明；有提示文字與 accessible name |
| 場景物件 | 顯示編譯後 Scene 的真實父子關係，搜尋、展開、選取；與引擎中的物件同步 |
| 中央「場景」 | Ursina/Panda3D 的原生子視窗，使用正式 Scene／Asset renderer；右鍵旋轉、滾輪縮放、框選、聚焦、深度選擇與只看本層 |
| 中央「試玩」 | 同一正式 renderer 執行原 InputIR／RuleRuntime；播放、暫停／繼續、重新開始；停止回到編輯 |
| 中央「原作 2D」 | 原始 Pygame 程式在獨立程序中直接運行，原始邏輯解析度保留，畫面等比縮放到中央；支援暫停、繼續、重開；切離頁面暫停原作 |
| 右上屬性 | 選中物件的位置、旋轉、縮放、可見性；彩色 XYZ 標識、清楚的等寬數字、聚焦、還原草稿 |
| 資產／事件 | 真實素材圖片縮圖及實際 renderer 使用分組；點 renderer 分組可定位場景物件；完整錯誤與執行記錄放在事件頁 |
| 右下 AI | 目前遊戲資格、原因、迭代／局數、進度、開始、取消、續跑及結果；不同遊戲的舊局數不混入當前遊戲 |
| 轉換對話框 | 保留來源分析、空間轉換、意圖、尺寸、候選批准、安全來源參數、規則、證據、診斷及交接資料的既有控制器 |
| 版本與引擎檢查 | 物件面板的專案選單可查看版本／hash、分析規則、檢查結果，開啟產物資料夾；Core／9 API 內建自測獨立在背景執行；匯入選單保留匯出來源分析套件 |

井字棋 Target 顯示 84 個實際 Scene 物件；本機既有掃雷 Target 顯示 1,211 個物件與真實數字／按鈕素材。數量只是這兩份產物的觀測結果，不是按遊戲名稱寫死的 UI。中央沒有另外繪製一個裝飾方塊或拿 screenshot 假裝即時場景。

快捷鍵：`Ctrl+O` 匯入、`Ctrl+Shift+O` 掛載場景、`Ctrl+S` 儲存草稿、`Ctrl+Z / Ctrl+Y` 復原／重做、`F5` 播放／停止。文字欄位保留本身的文字復原操作。

## 編輯與版本邊界

目前提供物件級別的場景佈局編輯，不是完整 Unity 功能集。變更保存在獨立 `cubeengine.scene-layout-draft/1.0` JSON，綁定批准 Project 的 hash。從「匯入 → 開啟場景佈局草稿」讀回時，會先完整檢查版本、物件、屬性與有限數值；不相容草稿不會部分套用。可復原／重做最近 64 次佈局操作。

進入試玩會恢復已批准的呈現，離開試玩後重新套用編輯草稿。草稿不覆寫 Rule、Scene IR 或訓練來源；目前尚未實裝「把草稿提交為新的批准 Scene」、3D 拖曳 gizmo、建立 prefab 或任意資產拖入實例化。現有批准 Scene 內的資產會透過正式 renderer 實體化；資產瀏覽器的定位操作不代表新增物件功能。

自對弈的 Rule／adapter／模型／trainer hash 約束、背景 worker、安全取消、完整 checkpoint 與相容續跑沿用既有訓練後端。只開放已證明相容的雙人 3×3×3 遊戲，目前以 3D 井字棋驗收。掃雷 Target 明確顯示沒有雙人對手並停用開始；沒有建立假對手。

原生嵌入首版限 Windows；原作適配器目前限 Pygame。Turtle、瀏覽器來源及其他視窗框架不能宣稱已支援嵌入。任意遊戲訓練、CUDA、全面的新模型轉換驗收都不是本輪通過項目。

## 架構與可靠性

- Qt 負責分區、對話框、圖示、字型與輸入欄；`FieldPort` 讓既有轉換／訓練控制器保留同一組欄位與回呼。
- `QProcess` 監管 renderer／原作，UI 不等待遊戲迴圈；區域尺寸換算成實際 DPI 像素，連同 Ursina 的 cached window size 一起更新。
- Ursina 使用 Panda3D 的 parent window 支援嵌入；Qt 容器與遊戲視窗有各自的生命週期。
- Pygame 保有自己的 SDL 子視窗，不能借用 Qt 的 HWND。來源重複 `set_mode` 時重建自己擁有的 display；這修正了掃雷先建 10×10 bootstrap display 再建正式 display 的失敗。子視窗父子關係會檢查。
- 擁有者關閉、管線 EOF 或來源切換會結束自有子程序；逾時有界限，不用停止整台機器上的 Python。編輯命令排隊並驗證；原始 Rule board 不作為 UI 編輯資料暴露。
- 來源切換清除 Target 身分、屬性、物件與訓練綁定。背景訓練取消和歷史輸出仍由既有 supervisor 處理。

## 驗收與限制

證據目錄：`artifacts/studio-v2/`；`integration.json` 保存最終的實測清單與實際訓練目錄，畫面均來自運行中的產品。

| 驗收 | 結果／範圍 |
| --- | --- |
| 原生整合探針 | **30 項通過**；使用真實 Qt 元件、Ursina/SDL 子 HWND、正式 Scene／Asset、原 Rule、CPU trainer。最終含掃雷 Target 的檢查清單見 integration.json |
| 場景編輯 | 真實物件選取、XYZ 修改、復原／重做、保存／讀回、Play 恢復批准呈現、InputIR 接受操作、暫停 |
| 版面 | 1440×840 與 1240×780 邏輯像素；驗證視窗未被最小寬度撐大，renderer 跟隨實際像素、開始／取消在卡片內。對話框依可用螢幕範圍限高，可捲動 |
| 原作 | 真實 Pygame 井字棋、掃雷；子視窗歸屬、幀、暫停／繼續、切換及多次 display 建立 |
| 訓練 | 新 UI 啟動真實 CPU 一輪，2 自對弈＋2 Arena，選取相容 checkpoint，以原 Rule 重放 4 局；UI timer 全程持續。完整續跑及不同階段取消的先前後端驗收仍見 10 月 4 日交付，本輪不冒稱重新覆蓋所有取消點 |
| 單元／回歸 | 62 項相關檢查：59 通過、3 失敗。三项失敗以修改前 HEAD 的控制器／播放器在同一工作樹、同一 fixture 與環境重現；詳見 focused-tests.txt、baseline-tests.txt。數值輸入完成後另跑 35 項相關測試全部通過，包含新增整數／浮點及非法輸入檢查 |
| 人工輸入界線 | Computer Use runtime 初始化失敗，故本輪不宣稱 OS 滑鼠人工驗收。探針使用 Qt 控件回呼與僅測試模式開放的遊戲事件入口；HWND 歸屬及原生桌面畫面確有檢查，但不能代替所有跨視窗焦點／快捷鍵的真人操作驗收 |

三項既有失敗分別是兩項 `test_host_input_commands` 的來源 evidence hash 不匹配，以及 `test_ir_acceptance_workbench` 的既有 acceptance gate 未通過。沒有刪掉驗證、修改失敗 fixture 或以本輪 UI 檢查替代整庫全通過聲明。

本輪 **0 次模型 API、0 新模型費用**。使用封存批准產物，無須重新 Compile 或付費生成來驗收 UI。2048 新轉換仍由原工程師工作線負責。

重現：

```powershell
.\.venv-workbench\Scripts\python.exe -m unittest tests.test_studio_scene tests.test_workbench_training tests.test_srtp_workbench
.\.venv-workbench\Scripts\python.exe -m tests.studio_scene_probe
# 要額外檢查本機批准的掃雷產物，追加 --mines-target <project.manifest.json>
```

研究與交互推導見 [場景工作臺設計修正](STUDIO_SCENE_DESIGN_20261006.md)。
