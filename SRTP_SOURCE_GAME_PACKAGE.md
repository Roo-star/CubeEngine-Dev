# SRTP Source Game Package v1

`cubeengine.srtp/source-game-package-v1` 是完整源遊戲匯入結果。它包住 Rule Schema IR，但不取代原專案。

## 核心區段

- `root`, `entrypoint`：原版專案位置與入口。
- `runtime`：原版啟動命令、cwd、框架、依賴、缺失依賴及相容處理。
- `inventory`：參與分析的程式、資料與素材。
- `license`：授權文件與上游來源。
- `analysis_coverage`：space、state、actions、flow、randomness、outcomes、visual、modes 的 proven/partial/unresolved/N/A 狀態。
- `source_parameters`：只收錄來源中可定位的設定。
- `rule_report`：Rule Schema、diagnostics、provenance 及 Function 2 handoff。
- `transformation`：來源 X/Y、目標 XYZ 與每項 mechanic lift。

`rule_report.schema.ui_hints.interaction_contract` 另保存：

- 由來源行號證明的鍵盤／滑鼠輸入、事件與 handler；
- 3D gameplay 對應輸入；
- CubeEngine 固定保留的視角／圖層輸入；
- WASD、右鍵等衝突的分流決策；
- 每項輸入應產生的可見回饋與 focus 要求。

## 三個獨立 readiness

### Original runtime

- `runnable`：入口與依賴可用。
- `blocked`：入口、框架或依賴不足。

### Rule understanding

每個 category 分別標示，禁止用單一 `success=true` 掩蓋未知語義。

### Transformation

- `ready`：所有來源 mechanic 已有可執行 3D lift。
- `partial`：仍需 adapter、LLM 或 designer decision。
- `blocked`：來源語義或執行條件不足。

## 來源修改原則

1. 不直接覆寫使用者上傳的原版專案。
2. `data_file` 等孤立安全設定建立 `.cubeengine_variants` 衍生副本。
3. 需要同時修改多處算法的值標為 `source_patch`。
4. 改變 cell/intersection、flow、randomness 等核心身份不是「參數調整」，除非原遊戲本身提供這個 mode。
5. 任何改動都必須重新跑 original/variant fidelity checks。
