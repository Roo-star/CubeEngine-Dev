# Spatial Lift：合法滑鼠綁定被 Target 初始化改壞

分支：SRTP。現場：`target.failed/e5bb35a476634edc921ae00efbd1909c`。本輪 API 呼叫：0。

## 原因與修復

Source 的 clear_question、reveal 原本都是合法的 `mouse / mouse.button.primary`，靠場景目標及 `first_legal` 區分用途。建立 Target 草稿時，舊的自動避開按鍵衝突程序把它們換成 `keyboard.key.a`、`keyboard.key.arrow_left`，卻保留 mouse 裝置。四 IR 聯合驗證因此在 Rule 階段失敗；模型只獲准修改 Rule，無法修復引擎先前改壞的 Input。

- Target 初始化改為只複製、更新文件身分和依賴雜湊，完整保留批准的遊戲語義與操作。沒有修改使用者 Manifest，也沒有掃雷名稱分支。
- 建立 Target 後、解析自然語言之前先檢查四 IR；分階段入口也檢查本地基線。引擎自行產生的錯誤不再交給付費 Rule 呼叫重試。
- 重播原回應發現 512 格批次更新會產生超過 1,024 個無監聽者的狀態通知。現在僅省略無靜態訂閱者的內部派送，保留事件序號；真正的循環仍受原上限保護並回滾。
- 補齊純表達式鏈式比較、in/not in，以及明確的測試資料逐狀態填充值；同步模型契約。這些不替換遊戲規則，也不清除未實作功能的 unresolved。
- 相同來源檔、批准 Source、自然語言、語言與模型設定可恢復已保存的 Intent 和 Rule 回應，並重新執行目前驗證。舊快取遷移只接受完全相同輸入指紋、且尚無成功階段的 Rule 失敗；舊的錯誤 Input 僅在副本中用來核對歷史指紋，從不作為執行基線。

## 已證明與未證明

原始回應保存於 `tests/fixtures/field_runs_20260924/minesweeper_lift.json`，保留來源及原報告雜湊。沒有手改模型的 Rule 或行為測試。

原付費 Rule 的五項測試全部通過：標旗／問號／清除、拒絕開啟標旗格、三維首擊鄰域安全、踩雷後終止、開啟第 502 個安全格勝利。另有中性案例驗證共享滑鼠操作保留、呼叫前攔截、大量狀態更新與確定性重播、真正循環回滾、測試資料型別、快取失配拒絕。

生產管綫離線回放確認 Intent 和 Rule 都可恢復，接著在 Asset 模型邊界刻意停止。詳見 `LLM_SPATIAL_FIELD_REPLAY_20260924.json`。此停止是禁止新呼叫的測試措施，不是已確認的 Asset 編譯錯誤。

本輪沒有新的 Target Asset／Scene／Input，**沒有新增可直接遊玩的完整三維產物**。既有五項測試是模型所附測試，不能代替獨立的源遊戲等價或完整產品驗收。品質報告其餘 pending 是尚未完成驗收，並非八個已發現的新錯誤。

## Workbench 的下一步

重新啟動本目錄的 Workbench，使 Python 載入更新；保留已批准的 Source、原 Prompt、模型設定和快取。無須重新 Compile Source。再次 Run Spatial Lift 時會先恢復並本地驗證 Intent／Rule，再生成剩餘 Target 階段；若改變來源、Prompt 或模型設定，相關快取不會被強行重用。

這次錯誤的修復可用保存回應免費驗證，已完成。要取得完整新 Target，仍需要 Asset、Scene、Input 的新模型回應，理論最低為三次回應；讀源碼與修復可能增加 HTTP 次數。尚未執行或批准這部分付費驗收，也不以此文件承諾下一次整條管綫必定成功。後續受控驗收應只續剩餘階段、明確限制 HTTP 次數與重試，按當時實際模型費率及準備好的請求 token 估價；請求數不能當作美元上限。

全量單元／整合測試 514 項：513 通過、1 項需付費 API 的測試跳過；六項 Ursina 渲染／拾取探針全部通過。完整報告見 `LLM_SPATIAL_BASELINE_ACCEPTANCE_20260924.json`，包含逐項結果及受測檔案雜湊。
