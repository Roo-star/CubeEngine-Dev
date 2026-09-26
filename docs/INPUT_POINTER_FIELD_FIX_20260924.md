# 掃雷 Input 阻斷：通用拾取、完整點擊與合法動作選擇

2026-09-24；開發分支 SRTP，原失敗包和使用者 Manifest／付費缓存保持不變。本輪修復沒有新增模型呼叫。

## 現場與責任

作業 `a1c79ab4aca046b19b919f750f9e269e`：Rule／Asset 復用成功，**新模型 Scene 已通過**（317 節點、12 個規則畫面、1,536 個互動畫面）。所以之前的 Scene 組合能力已取得真實生成證據；這不代表完整 Source 或三維遊玩通過。

Input 的兩條報錯包含兩種問題：

1. 播放器原有同目標按下／放開、拖曳取消處理，但生成契約沒有充分說明，而且送往 Input 的事件丟掉了 Scene 節點身份。
2. Input 缺少指定 Scene 控件的篩選，無法把同一個滑鼠鍵分別用於場景按鈕和棋盤。

另審查到被 unresolved 遮住的下一個錯誤：模型為左鍵生成兩個不消耗事件的重疊綁定。它們即使有互斥規則條件，也不會自動繞過 Input 衝突校驗；若先清除問號再執行揭格，更可能一個點擊執行兩次操作。這是通用「按狀態選擇動作」問題，一起修正。

## 已改動

- Input trigger 新增 `pointer` 條件：精確場景節點、子樹、Rule topology、確認完整點擊。適用任意按鈕／棋盤，沒有掃雷名稱分支。
- 子樹判斷使用真實 Scene 父子關係；不以節點名稱前綴猜測。拾取資料由 Ursina、ProjectHost 和編譯驗證共用。
- 原有 PointerGesture 確認點擊後，Host 發出附帶 `pointer_click` 的事件。不同目標放開、拖曳返回、空白／UI、切層清除不會派發確認點擊。這是完成點擊適配器，不宣稱已支持連續原始滑鼠按壓遊戲。
- 新增明確的 `first_legal` 策略：按優先序檢查當前 Rule 合法性，執行第一個合法動作並消耗一次。原 `first_match` 行為保留，兩者差異寫進生成契約。
- 同步 Schema、IR 校驗、衝突分析、Router、ProjectSession、ProjectHost、模型 Prompt／能力表／Scene 節點目錄、缓存指紋。
- 編譯驗證逐一檢查帶指標篩選的綁定；不能因已有 R 鍵，就讓不存在或不可達的場景按鈕漏過。

## 驗證與可驗收範圍

保存原模型 Input 到 `tests/fixtures/field_runs_20260924/minesweeper_input.json`，原件仍會被拒絕。測試明確建立 Input 修正版；未注入使用者缓存或產物。

離線檢查包含：

- 真實保存的 Rule／Asset／新 Scene＋測試 Input 經完整 SourceToIRCompiler、封包、測試批准、ProjectHost。
- 棋盤右鍵標旗、場景按鈕重置整個 session、棋盤點擊不重開、未確認的裸 release 不重開。
- 高優先動作不合法時選下一個；問號清除之後不會在同一次點擊再揭格。
- 放開到其他目標、拖曳返回、清除 pending、空白 UI 的取消；不同名字的按鈕、真正的子樹和只是名字相似的節點。
- 即使存在鍵盤替代，錯誤 Scene 按鈕仍被編譯 gate 攔截。
- Ursina 離屏射線確實拾取新 Scene 的按鈕與格子；輸入路由進 Rule 後旗子畫面像素改變，按鈕派發 restart。完整 session 重置另由 ProjectHost 整合測試覆蓋。

固定全量入口：`python -m tests.run_offline_acceptance --output docs/LLM_INPUT_POINTER_ACCEPTANCE_20260924.json`；網路禁止。結果、檔案雜湊與跳過項見該報告。

最終結果：**496 項測試，495 通過、1 項真實 API 測試跳過、0 失敗；6 項 Ursina 渲染／拾取檢查通過。** 新增機制與原有轉換功能均在同一離線回歸中測試。

**使用者現在不能把原失敗包當成新的完整可玩產品。** 引擎已新增上述能力；現有包還缺模型以新契約生成的 Input。重啟 Workbench 才會載入一致的新後端／生成契約。新 Input、完整 Source 通過與 Spatial Lift／三維完整遊玩，仍各自需要驗證。統一 Z／V／透明度等玩家 UX 待辦不因本次而標為完成。

## 付費記錄與下一個最小測試

之前使用者批准的單次 Scene 驗證已執行 1 HTTP，68,662 input／132 output token，US$0.1729735；第二次請求被攔截，未取得可驗證 Scene。該驗收腳本沒有保存第一個中間回應，無法事後確定具體補讀內容或服務重試情況。現已補齊中間回應／source trace 留存、單次驗收零 HTTP 重試及剩餘請求預算提示。舊收據保持原狀。

使用者隨後的 `a1c79...` 已產出通過的 Scene，因此**不再重付 Scene 生成費用**。

本輪已本地準備新的 Input 單階段驗證，收據：`.cubeengine_llm/diagnostics/input_repair_proposal_20260924.json`。

- 重新本地驗證既有 Rule、44 項 Asset、新 Scene 均通過；只發送此公開掃雷範例必要源碼、三份 IR、Input 拒絕回應及新契約到既有 OpenRouter 模型。
- 最多 1 次 HTTP；不自動重試，不生成 Scene／Lift，不批准或發布 Manifest。
- 準備上下文 198,926 UTF-8 bytes，粗估 49,731 input token，輸出最多 16,384 token。依上下文估算誤差和上一筆實際收費，預算估計 **US$0.15–0.35**，不是總金額硬上限；實際費用以服務回報為準。
- 告知模型只剩一次回應，使用已給證據生成或具體報告缺失；保留所有中間回應，不能保證模型必定遵循或成功。
- 這是新的付費 Input 驗證，**尚未執行，需要使用者另行決策**。之前的單次 Scene 授權已用完，不能沿用。
