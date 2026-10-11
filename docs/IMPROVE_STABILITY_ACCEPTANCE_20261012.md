# `improve_stability` 驗收（2026-10-12）

驗收提交：`9b62212de9cb3e2883cb640198b6145fbff8b09e`（AI 管線主要改動在父提交 `96b324b`，穩定性改動在 `d1c8365`）。驗收基準為 `WorkbenchAI` 的 `436b429`，使用其最新已批准 3D 井字棋 Target：`.cubeengine_llm/tic.tac.toe/target_20261006-220937/project.manifest.json`。本輪沒有付費模型 API 請求，也沒有把工程師分支合併入 `WorkbenchAI`。

## 結論

**後端核心路徑通過；本次提交尚不能按「現有 WorkbenchAI UI 可直接訓練」交付。** Target 的 Rule IR 可推導封存 AlphaZero 1.1 adapter，九 API 符合性通過，AI-Dev Coach／MCTS／真實 PyTorch 網路可產生樣本、訓練、Arena、checkpoint，Rule Runtime 重放一致。工程師提交中的 UI 接在舊 DearPyGui `srtp.workbench`；WorkbenchAI 使用 Qt `srtp.studio` 和另一套 `srtp.training`，需要明確合併及接線。

## 可重現證據

- GitHub `origin/improve_stability` 在驗收時為 `9b62212`；`origin/WorkbenchAI` 為 `436b429`。用隔離 checkout 驗收工程師提交，獨立的 `CubeEngine-AI-Dev` 保持 `Dev` 分支 `2f42b6b`，以 `CUBEENGINE_AI_ROOT` 指向它。不可把 AI-Dev 目錄本身切到 `improve_stability` 後的缺檔當作工程師缺陷。
- 上述最新 Target：`game_eligible=true`、`trainable=true`（使用 WorkbenchAI 的 `.venv-training`，內有 PyTorch），Rule hash `2299a11b95dec524e93ec1e8f0349c74ee10efc61565d1c4e8afa8511a2b5bcb`，adapter hash `70d3acd23021cee20c8b1da7f4bd8ca868aa7fd5ca4234239100b13b181cea2f`；棋盤 3×3×3、28 個動作碼、48 個驗證對稱，九 API conformance 全部通過。第 28 個動作碼為適配層保留的 pass，網路輸出大小因此為 28。
- 真實網路短跑：`E:/CubeEngine/tmp/ai-acceptance-20261012/train_20261012-004415`。一輪、2 局自對弈、2 局 Arena，模型被接受；`replay_failures=0`，保存 `sessions/1/best.pth.tar`、自對弈樣本、`published/best.pth.tar`、adapter manifest、binding、Arena JSONL。相容 checkpoint 成功重載續跑；續跑的 Arena 2 局也通過重放。新舊模型的強度沒有由這個極短測試證明。
- 真實取消：`E:/CubeEngine/tmp/ai-cancel-20261012` 在 200 局計畫的第 2 局後停止，結果 `cancelled`；從已接受模型複製的 run 續跑再取消後，舊 `published/best.pth.tar` 仍存在。
- 真實對戰：同一 Target 的 `ProjectHost` 載入上述模型；人類經 Input IR 落子後，AI 在背景選擇合法動作，由 Rule Runtime 執行，局面 revision 由 0 到 2。
- 測試：`tests.test_ai_training_pipeline` 11 項通過；`tests.test_srtp_workbench`、`test_2048_paid_run_fixes`、`test_provenance_preflight`、`test_ai_opponent`、`test_alphazero_derived` 合計執行 48 項，47 通過、1 跳過。另一組 `test_source_equivalence`、`test_scene_draft`、`test_2048_paid_run_fixes` 共 36 項中 5 項視覺閘門失敗；最小 Ursina offscreen 初始化在本機也以相同 `buttonThrowers[0]` 錯誤失敗，屬此執行環境的渲染限制，不能據此判定這五項為提交回歸。
- 2048 Rule IR 被拒絕（單人、隨機、非零和等）；現存掃雷 Target Rule IR 亦被拒絕（單人、隨機、非零和等）。沒有將轉換成功誤判成可套用雙人零和訓練。

## 需退回工程師修正

1. **模型載入須驗證完整 binding。** `srtp/ai_training/opponent.py` 的 `find_models` 只按 `rule_content_hash` 選 run；`_load` 讀 adapter 與 plan，沒有核對 run 的 `adapter_hash`、`adapter_version`、network／architecture 與實際封存檔。我把一份 run 的 `binding.json.adapter_hash` 改為 64 個零後，`find_models` 仍列出該模型（1 個候選）。至少要拒絕 binding 與封存 adapter／checkpoint／plan 不一致的 run，並補負例測試。這是要求的防錯載條件。
2. **補齊可重現交付證據。** `docs/WORKBENCH_LLM_TODO.md` 宣稱較長的真實訓練與 56／56 Arena 重放，卻引用不存在的 `docs/AI_SELF_PLAY_WORKBENCH.md`；`docs/AI_TRAINING_ACCEPTANCE_20261003.json`、`docs/AI_PLAY_ACCEPTANCE_20261003.json` 也未提交。兩支驗收腳本的 `BUNDLE` 指向未提交的 `.cubeengine_llm/acceptance_20260927/target`，在乾淨 checkout 無法執行。請用提交內的已批准 fixture 或可指定的 Target 路徑重做可重現腳本，附實跑報告與環境。
3. **提供能從產品啟動的訓練環境。** 目前 `Run CubeEngine Workbench.bat` 指定的 Python 3.9.1 在本機沒有 PyTorch；從該解譯器檢查最新 Target 得到 `trainable=false`、原因為缺少 PyTorch。改用現有 `.venv-training` 則可訓練。請讓 Workbench 的資格檢查及子程序明確選擇並顯示訓練 Python，提供可重現安裝／啟動方式，不能只靠驗收者手動切換解譯器。

## 本地整合工作與尚未驗證

- 合併 `origin/improve_stability` 到 `WorkbenchAI` 的預演有 `srtp/project_viewer.py`、`srtp/transform_runner.py`、`srtp/workbench.py` 三個衝突；此次尚未合併。Qt Studio 右側訓練區仍連 `srtp.training.TrainingPanel`，工程師新增面板為 DearPyGui `srtp.ai_training.AiTrainingPanel`。需選定一套後端契約、接上 Qt UI，再做可見窗口的 Target→資格→訓練→取消／續跑→試玩回歸。
- 本次沒有新模型生成的 2048 Source→Lift→實玩驗收。`d1c8365` 的來源函數等價檢查和相關離線測試不等於 2048 已轉換成功。若要付費驗收，應先固定最新程式、重用有效已付費階段，只對缺失階段提出最小請求與費用上限供使用者核准。
