# WorkbenchAI 交付與驗收

基準：`SRTP_agentic` / `c22f6de`。本輪開發：`WorkbenchAI`。原 `Dev` 訓練分支、2048 轉換程式及原生成 Target 不修改。整體 UI 重構尚未實裝，等待使用者從三稿選擇。

## 現在可以操作什麼

使用本目錄原有 **Run CubeEngine Workbench.bat** 啟動。右側分成上方 Inspector、下方 **AI SELF-PLAY / TRAINING**。

1. 在右下 `Attach Target...` 選擇目前已批准的 `project.manifest.json`。這台電腦的真實驗收包為 `E:/CubeEngine/CubeEngine-SRTP/.cubeengine_llm/tic.tac.toe/target/project.manifest.json`。也可以在原本批准流程中自動掛載。
2. 資格檢查在背景執行。合格後會顯示當前遊戲名稱、雙人／完整資訊／3×3×3，以及「Current Target: nine APIs passed」。不合格顯示原因，不會生成假對手；原本的 9-API SELF-TEST 仍是獨立的內建自測。
3. `Run settings / short trial` 可選追加迭代、每輪局數、MCTS、epochs 和平衡先手的 Arena 局數；進階可選通道、batch、seed、學習率、採用門檻、裝置和 Python。預設為 CPU、1 輪、2 局、8 次搜尋、1 epoch、2 局 Arena。
4. `Output / resume` 可選輸出位置和相容的 `checkpoint.json`。按 Start 啟動獨立程序，顯示進度、時間、迭代／對局／樣本、loss、Arena 勝負、候選模型採用與錯誤。
5. Cancel 在搜尋／訓練批次間退出，保留最近的完整迭代；未完成更新不覆蓋 checkpoint。切換來源、重新掛載或關閉 Workbench 會要求停止原任務。背景程序也檢查 Workbench 心跳，避免 UI 退出後無限繼續。
6. `Use last checkpoint` 選回相容存檔；Start 追加所選輪次。重啟 Workbench 後也可用檔案選擇器續跑。每次續跑另建 Run，不覆寫舊實驗。
7. `Results` 打開本機輸出目錄；`Run log / replay` 查看事件、錯誤及 `Verify saved games`，用原 RuleRuntime 重放自對弈與 Arena 棋譜。

首次發布明確限定已證明的 **3×3×3 雙人輪流落子／對稱成線勝負契約**；按結構識別，不按遊戲名稱分支。現在用真實生成的 3D 井字棋驗收。掃雷的隱藏資訊、2048 的單人與隨機轉移，以及其他未有完整狀態編碼的遊戲都不合格。轉換成功不等於可訓練。

## 技術與存檔契約

- `training/adapter.py`：新版本化編碼契約，把生成 Rule 的 explicit player global 和棋盤映射到既有九 API Game。嚴格驗證 actor、有限動作、空格寫入、交替玩家、對稱零和終局與可重建狀態；不更改 Rule，也不放寬 `alphazero_v1` 原門檻。
- `training/vendor/`：AI `Dev` / `2f42b6b` 的原 MCTS 和原 3D CNN 架構；來源記於 ORIGIN.md。`coach.py` 適配 Coach／Arena 生命周期，增加可取消、進度、回放與存檔；保留負樣本權重 3、15 輪歷史、0.55 採用門檻。和局訓練目標正規化為 0。
- `worker.py`／`session.py`：背景子程序與非阻塞事件读取；運算不在 Dear PyGui render thread。錯誤保存在 `error.txt`／`worker.log`。取消超時可終止本次子程序，已完整發布的存檔不受影響。
- `artifacts.py`：JSON＋numpy arrays，不反序列化舊 pickle。每個 checkpoint、samples、棋譜、報告均帶 Rule ID／版本／revision／hash、adapter 版本／revision／hash、Target ID／hash、trainer 版本／實作 hash 和網路結構。模型與樣本檔案另外核對 SHA-256；外遊戲、不同通道或修改過的 Rule 會拒載。
- `latest.json` 僅在完整 checkpoint 寫入後原子更新。取消恢復點是完整迭代邊界，並非正在處理的一局或半個訓練 batch。CPU RNG 與樣本歷史會恢復；原 AI-Dev 每輪新建 optimizer 的行為保留。
- 本輪安裝的 `.venv-training` 在工作目錄內，只有可選 worker 需要 torch。Workbench 會自動找到它。可依 `requirements-training.txt` 重建；預設啟動腳本無須替換。CUDA 尚未在本機驗收。

每次輸出包括 `request.json`、`run.json`、封存的 project／rule／adapter／conformance、`events.jsonl`、`games/*.json`、帶 hash 的 `checkpoint-NNNN/`、`latest.json` 和 `report.json`。部分迭代樣本保存在 `iteration-samples.json`，明確不作續跑恢復點。

## 已完成驗證

證據位於 `artifacts/workbench-ai/`；大型模型與样本留在 `.cubeengine_training/`，不放入 Git。

| 驗證 | 結果及範圍 |
| --- | --- |
| 相關自動化測試 | 37 項通過，包含新適配器、重命名中性案例、35 局隨機 canonical／原生回放對照、單人／隱藏資訊／未編碼狀態拒絕、checkpoint 錯綁及損壞拒絕、工作臺與既有九 API。見 focused-tests.txt |
| 真實 PyTorch CPU 短程管線 | 使用 9/30 真實已批准 Target，1 輪、2 自對弈、1 optimizer step、2 Arena、11 樣本；保存 checkpoint 0／1，原 Rule 重放 4 局。見 acceptance.json |
| 續跑 | 從迭代 1 載回并追加 1 輪；與同 seed 不中斷兩輪的所有模型陣列逐項完全一致 |
| 取消／重新開始 | 自對弈、訓練 batch、Arena 三個階段各取消一次，再從完整 checkpoint 成功跑完一輪；共 9 次接受測試 Run，無假網路／假訓練 |
| 存檔發布邊界 | 額外在真實 Arena 完成、checkpoint 尚未發布的瞬間取消；報告完成輪次仍為 0，與保留的 checkpoint 一致。見 commit-boundary.json |
| 真正 Dear PyGui 回呼／畫面 | 使用實際 Start、Use last checkpoint、Verify saved games 控件回呼；訓練中持續渲染超過 200 幀。見 ui-probe.json、workbench-trained.png。桌面工具授權等待逾時，因此不冒充人工滑鼠操作錄影 |
| 原生規則對照 | 最新 Target 的 49 條獲勝線逐步與獨立原生井字棋規則比較通過。見 native-rule-regression.json；此檢查不宣稱完整 Ursina 滑鼠／渲染驗收 |
| 首次全庫離線回歸 | 683 項：674 通過、1 付費測試跳過、7 failure、1 error。8 項失敗用原 `SRTP_agentic` Workbench 在同環境記憶體載入後重現相同結果；其他既有模組未修改。見 full-regression.txt、baseline-failures.txt |

全庫既有問題分別是：3 個 source evidence hash 失配案例、1 個 repair 次數預期、1 個獨立 oracle 路徑假設，以及 3 個 visual gate 回傳 None 的渲染檢查。本輪不以刪除檢查或改生成產物製造通過。額外沿用舊 Ursina 探針檢查最新 Target 時，在其假設所有 cell 都帶 `logical_parent` 的位置中止；因此沒有新增「完整實玩已驗收」的聲稱。原已成功的井字棋／掃雷轉換和播放器程式沒有改動，但以上渲染驗收缺口仍需對應現場環境處理。

沒有新 LLM API 呼叫、費用為 0。此次只用既有合法批准產物跑本機訓練，不需要支付新轉換呼叫。短程跑通不表示高棋力、任意遊戲可訓練或 2048 已完成轉換；把模型接回玩家對戰／推論也是後續功能。

可重跑：

```powershell
$env:CUBEENGINE_AI_ROOT='E:\CubeEngine\CubeEngine-AI-Dev'
python -m unittest tests.test_workbench_training tests.test_srtp_workbench tests.test_alphazero_v1 -q
python -m tests.training_acceptance --target tests/fixtures/workbench_training_target_20261004/project.manifest.json
python -m tests.workbench_training_probe
.\.venv-training\Scripts\python.exe -m tests.training_commit_probe
```

## UI 提案

[研究與旅程設計](WORKBENCH_AI_RESEARCH_20261004.md)記錄一手來源、技術取捨、既有功能清單、三稿差異與待真人驗證假設。

`docs/design/workbench-ai/index.html` 是本機可開啟的互動提案，完全不呼叫實際訓練。上方切換 A／B／C；切換遊戲可看不適用入口，也可以示意開始、取消、續跑與結果。三張 PNG 分別為 `studio.png`、`flow.png`、`lab.png`。互動、控制台錯誤與 1440／1366／1024／768／390 寬度已做瀏覽器檢查，結果在 visual-qa.json。

建議 A Prism Studio 作實裝基準；B 偏第一次轉換的引導，C 偏創作工具品牌感。**依使用者要求，選稿後才進行整體 UI 實裝。**
