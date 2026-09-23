# 2026-09-24：字體與診斷修復

分支 SRTP。本次程式修復尚未提交／推送；前一輪使用者已推送的更新不包含本次新增修改。付費 API 呼叫：0。

## 2048

實測作業 `cc8a2799ae7b47e1a0e4dfe900aa1f2c` 的 Rule 已通過，失敗發生在 Asset：模型看不到源码 `pygame.font.SysFont(c['font'], ..., bold=True)` 使用的 Verdana Bold。

本機存在原字體 `C:/Windows/Fonts/verdanab.ttf`。之前只索引 `Font(None, ...)`，現在增加靜態解析 SysFont／import 別名／字面 JSON 配置，提供來源位置、字體家族／樣式、實際字體哈希及可用 runtime URI；沒有執行遊戲源码或替換字型。

新增 `srtp/system_fonts.py`，接入 `runtime_assets.py` 及模型說明。資源可隨包攜帶，搬移後從包內讀取；字體缺失或樣式需未支持的合成處理時，已知靜態需求會在呼叫模型前報錯。保留未知分發許可標記，不虛構字體授權。

## 掃雷

原 `InvariantViolation.diagnostics` 是 dataclass 物件；生成階段直接放入下一輪 Prompt，JSON 序列化崩潰，真實規則錯誤因而被蓋住。已在診斷進入修復歷史、Prompt、報告前轉為含識別碼／名稱的文字，不吞掉規則失敗，也不使用全域 `default=str` 放寬 IR。

原付費回應離線重放發現：某測試 fixture 在初始化10顆雷的棋盤上，將另一空格設為雷，造成11顆，違反 `rule:invariant.mines`。現在共用測試 setup 會在操作前檢查 invariant，指出 fixture 覆蓋初始化狀態而非清空，並提供格值數量；模型可修正測試，而非誤改遊戲規則。原模型定義未被手工改成通過。

## 驗證

- 34項針對性測試通過。
- 全量469項：468通過、1项付費測試跳過、0失敗；4個實際渲染探針通過。驗收過程禁止外連；費用測試中的請求日誌是 mock。
- 確認原 Verdana Bold 的哈希和字形、資源打包搬移後無系統字體仍能讀取、缺字體在 API 前停止。
- 掃雷原回應能保存失敗報告並進入受限修復，沒有再次出現診斷物件 JSON 崩潰。
- 新回歸：`tests/test_font_and_diagnostic_failures.py`；現場資料：`tests/fixtures/field_runs_20260924/`。

未進行新模型生成，不宣稱掃雷和2048後續完整流程已通過。工程師交接已改為接手真實 API 聯調、基於失敗的模型調校和人工遊玩驗收；本次已知程式修復不再重複派工。
