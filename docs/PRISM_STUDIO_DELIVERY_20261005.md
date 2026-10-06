# A · Prism Studio 原生工作臺交付

> 歷史記錄：使用者於 2026-10-06 要求重做中央場景及交互。預設產品已由 [真正嵌入场景的新 Studio](STUDIO_SCENE_DELIVERY_20261006.md) 取代；下文的拓樸預覽與外部遊戲視窗只描述舊版。

使用者選定 A 後，在 `WorkbenchAI` 實裝。基於前一階段 `0b378d9` 的自對弈後端與 [研究／旅程設計](WORKBENCH_AI_RESEARCH_20261004.md)，使用原有 Dear PyGui、Workbench 控制器與正式播放器。HTML 三稿保留為設計提案；本次截圖來自實際軟體。

## 開啟與現在可操作的內容

開啟本目錄的 `Run CubeEngine Workbench.bat`。淺紫主色、青色狀態與石墨背景沿用 A；Windows 首次視窗自動配合可用桌面範圍，選單可調整 100%／125%／150% 字級。

| 區域 | 操作與內容 |
| --- | --- |
| 左側遊戲庫 | 匯入完整專案／入口、內建來源、最近開啟、原始遊戲與生成產物。窄視窗自動收起，可從檢視選單切換 |
| 上方流程列 | 匯入 → 來源分析／空間轉換 → 批准與試玩 → AI 訓練；生成、取消與批准沿用原控制器 |
| 中央工作區 | Source 2D、Target 3D、對照；目標拓樸與規則狀態預覽、右鍵旋轉、滾輪縮放、分層、透明度、重設及試玩 |
| 右上設定 | 下次轉換尺寸與自然語言意圖、來源參數、目前規則；修改待轉換尺寸不會偽裝成已批准版本已改動 |
| 右下 AI 自對弈 | 遊戲資格及原因、短程參數、輸出與續跑設定、進度、迭代／局數、固定的開始／取消／上一存檔／結果入口 |
| 詳情視窗 | 產物與 hash、批准候選、原始診斷、重放、AI 結果、事件與错误。原資料仍可查閱，主畫布不塞滿紀錄 |

「短程試跑設定」集中迭代、局數、搜尋、epochs、Arena、裝置、Python、輸出路徑及 checkpoint。執行期間由原本的背景 worker 工作；完整存檔、取消、版本綁定與續跑契約均沿用上一階段。只對符合既有適配器契約的雙人 3×3×3 Target 開放，現在用 3D 井字棋驗收。

正式原作與正式 3D 遊戲仍在各自視窗執行。中央是規則狀態／拓樸預覽；Source 頁呈現来源資訊及開啟原作入口，對照頁呈現來源資訊和目標狀態，並不是兩個嵌入式完整遊戲畫面。井字棋可在預覽中選格，輸入仍交由原 InputIR／RuleRuntime 判定；預覽、播放器與訓練使用各自的遊戲局面，訓練不接管正在試玩的局面。

直接載入生成 Manifest 時不假裝已載入原作。切換來源會清除上一款遊戲的待批准候選、Target 與訓練綁定，避免誤用舊遊戲。最近開啟路徑只記在本機 `.cubeengine_workbench/studio.json`，不進 Git。

## 掃雷與未編碼狀態

實際掃雷 Target 被判為不具雙人對手，右下顯示不適用並停用開始。它的現有 metadata 雖標示完整資訊，中央仍不能據此暴露原始棋盤：只有目前 Rule hash 對應到已驗證狀態編碼的版本才可顯示棋子並選格，其他版本僅畫空間拓樸，正式玩法仍走正式播放器。沒有修改生成 Rule／Manifest，沒有按遊戲名稱特判。

## 驗收與證據

證據目錄：`artifacts/prism-studio/`。截圖 `prism-completed.png` 是真實短程訓練完成後的工作臺；畫布中的棋局另由原 Rule 接受五次輸入形成。

| 檢查 | 結果與邊界 |
| --- | --- |
| 相關自動化 | 43 項通過，涵蓋既有 Workbench／訓練／九 API，加上畫布選格、投影邊界、過大拓樸、內部狀態保護與來源切換。見 focused-tests.txt |
| 原生 UI 探針 | 真正 DPG 控件回呼／畫面；1440×940 的 100%／125%／150%，1366×768 與 1024×768 的 100% 通過開始／取消／續跑在面板內及工具列不重疊檢查。見 acceptance.json 與各尺寸截圖；未宣稱所有尺寸、縮放交叉組合皆已驗收 |
| 真實 CPU 訓練 | 從新 UI 啟動 worker 完成一輪、2 自對弈＋2 Arena；期間持續渲染 252 幀，選取上一 checkpoint 並以原 Rule 重放 4 局通過。本輪驗證 UI 接線；完整續跑與各階段取消的真實訓練證據見上一階段交付 |
| 原生 Windows 滑鼠 | 直接點擊預覽格子，Rule 接受並更新 X；直接點「開始短程試跑」完成一輪、11 樣本、checkpoint 1，開啟詳情確認結果。見 native-interaction.json、native-training-report.json |
| 正式播放器入口 | 直接點工作臺「試玩」開啟已批准井字棋的 Ursina 視窗，滑鼠選格後玩家 X → O、revision 1，Esc 返回工作臺。本輪為啟動／輸入冒煙驗收，未將一次落子當成完整渲染與所有規則驗收 |
| 真實掃雷產物 | 掛載現有批准 Target 後，自對弈拒絕、拓樸預覽不顯示內部格值。見 prism-minesweeper-target.png；未啟動任何掃雷自對弈 |

全庫回歸共 **690 項：681 通過、1 項付費測試跳過、7 failure、1 error**，耗時 666.5 秒。8 個失敗測試名稱與前一階段完全相同：3 項 source evidence hash、1 項 repair 次數預期、1 項獨立 oracle 路徑，以及 3 項 visual gate 的 None 錯誤。前一階段已用原 agentic Workbench 在同環境重現；本輪沒有新增失敗項。見 `full-regression.txt`、`regression-summary.json`，以及上一階段的 `baseline-failures.txt`。不將整體渲染回歸宣稱為全通過。

回歸輸出中的 `PAID REQUEST` 字樣來自替換 HTTP 請求的預算模擬測試，不是本次真實付費請求。

本次未新增 LLM API 呼叫，API 費用為 0；不需要為 UI 驗收重新付費转换。任意遊戲訓練、將模型接回玩家對戰，以及完整 Scene 嵌入工作臺都不屬於已交付能力。原模型轉換、生成資產與訓練演算法未因換 UI 重寫。

## 重跑

```powershell
$env:CUBEENGINE_AI_ROOT='E:\CubeEngine\CubeEngine-AI-Dev'
python -m unittest tests.test_prism_studio tests.test_workbench_training tests.test_srtp_workbench tests.test_alphazero_v1 -q
python -m tests.prism_studio_probe
python -m srtp.workbench --project tests/fixtures/workbench_training_target_20261004/project.manifest.json
```

探針使用封存的既有批准產物，並啟動真實本機 CPU 訓練；不是新模型转换。`--capture` 可輸出原生工作臺畫面後退出，`--size 1440x940` 可指定驗收尺寸；一般使用不必指定大小。
