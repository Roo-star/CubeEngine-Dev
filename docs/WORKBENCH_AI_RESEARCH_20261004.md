# WorkbenchAI：接入研究與產品設計依據

日期：2026-10-04。基準：`SRTP_agentic` 的 `c22f6de`；開發分支：`WorkbenchAI`。

## 範圍與證據

本輪完成交接文件第 2 項的工作臺接入；2048 轉換由工程師另行負責。整體 UI 只交付三款可比較的提案，未選稿前不替換現有工作臺。

研究材料包括目前程式、原生工作臺截圖、9 月 30 日已批准的真實 Target、AI `Dev` 分支 `2f42b6b`、使用者提供的四張風格參考，以及下列一手文件。這是桌面研究及操作走查；沒有進行真人訪談，人物與旅程中的需求推斷都屬待驗證假設。

## 引擎與 AI 的實際邊界

1. SourceGameImporter 採集來源證據；SourceToIRCompiler 產生 Rule／Asset／Scene／Input，Source 經批准後才能 Spatial Lift。
2. Project Manifest 封存四 IR 依賴及來源身分；ProjectSession／ProjectHost 和 RuleRuntime 是實玩權威。Workbench 內的格子視圖是診斷介面，Ursina 視窗是實際三維遊玩。
3. `alphazero_v1` 已有九 API，但只接受一個 enum 棋盤、輪流制、完整資訊、確定性的受限遊戲。內建 9-API SELF-TEST 不能代表目前 Target 合格。
4. 最新生成井字棋使用 event-driven flow、整數棋盤、獨立 player global，和舊編碼契約不相容。直接換按鈕名稱或塞入手寫 TicTacToe3DGame 都會掩蓋這個差異。
5. AI-Dev 的 Coach 使用 MCTS 採樣、最近 15 輪樣本、負樣本權重 3、PyTorch 訓練、平衡先手的 Arena，以及 0.55 更新門檻。網路最後一層 3D 卷積和全連接維度把支援範圍限制在 3×3×3。

因此新增**結構驗證的 alternating-placement 適配契約**，而非放寬舊 eligibility：只能是一個棋盤、一個輪流玩家值、空格落子、對稱的成線勝負及滿盤和局。匹配 AST 與資料結構，不檢查遊戲名稱。額外狀態、動作、事件、隨機、隱藏資訊或未證明的所有權全部拒絕。原 Rule 不修改、不重新封存；MCTS 每次分支仍呼叫原 RuleRuntime。身份對換和原生回放有多種隨機走法測試。

## 同類方法及取捨

| 參考 | 一手依據 | CubeEngine 採用方式 | 本輪不引入的部分 |
| --- | --- | --- | --- |
| Unity ML-Agents | 訓練獨立執行，run-id 管理輸出，能中止／續跑；self-play 需要多 agent 環境。[訓練文件](https://github.com/unity-technologies/ml-agents/blob/develop/com.unity.ml-agents/Documentation~/Training-ML-Agents.md)、[設定文件](https://unity-technologies.github.io/ml-agents/Training-Configuration-File/) | 主介面和訓練程序分離；預設短程 CPU 試跑；啟動前顯示資格及原因 | 不把 CubeEngine 轉成 Unity 環境；不把單人遊戲虛構成兩隊 |
| OpenSpiel AlphaZero | Game、MCTS、policy/value network、actor／learner／evaluator 分工，輸出設定、checkpoint 與機器可讀記錄。[官方說明](https://openspiel.readthedocs.io/en/latest/alpha_zero.html) | 九 API 作邊界；Run 封存設定、事件與棋譜；結果必須可用原規則重放 | 暫不擴展多 actor、批次推論或宣称通用棋力 |
| MLflow | Run 同時記錄參數、程式版本、metrics 與 artifacts，模型／資料也應有可追蹤關係。[官方 Tracking](https://mlflow.org/docs/latest/ml/tracking/) | 每個 Run 獨立資料夾；Rule、adapter、網路、trainer 版本和 hash 綁定全部樣本與報告 | 不增加伺服器、帳號或外部遙測；檔案格式保持本機可讀 |
| VS Code | 主工作區、兩側欄和下方 Panel 可分離，主內容保持穩定。[官方 Custom Layout](https://code.visualstudio.com/docs/configure/custom-layout) | 中央主畫布不被 AI 對話面板取代；右上屬性、右下訓練；診斷可收起 | 這次不實裝整套可拖曳 docking framework |
| Ableton Live | Browser、主要創作視圖、Control Bar 和局部 Device View 協作。[官方 Live Concepts](https://www.ableton.com/en/live-manual/12/live-concepts/) | 借用創作工具的穩定播放控制與上下文工具；Lab 稿使用底部局部模組 | 不把機器學習參數做成難讀的旋鈕或裝飾儀表 |

執行環境採可選 PyTorch worker。Workbench 不 import torch，現有 Python 3.9／Dear PyGui 可獨立使用；工作目錄 `.venv-training` 安裝 CPU torch 2.6.0 作本輪驗收。依據 [PyTorch 官方版本安裝資料](https://pytorch.org/get-started/previous-versions/) 使用官方 wheel。CUDA 選項可見，但本次只驗收 CPU；選擇不可用 CUDA 會清楚報錯。

## UX 原則及落地

- **狀態可見**：長任務顯示目前階段、已完成對局、迭代、時間、錯誤及最後安全存檔；進度條代表工作階段，不冒充棋力或準確剩餘時間。[NN/g：Visibility of System Status](https://www.nngroup.com/articles/visibility-system-status/)
- **逐步揭露**：第一層只放遊戲資格、短程試跑、取消及結果；epochs、通道、學習率、輸出與續跑放第二層。[NN/g：Progressive Disclosure](https://www.nngroup.com/articles/progressive-disclosure/)
- **阻擋要說明原因**：空白、待批准、不適用、執行失敗必須分開；掃雷不是「載入失敗」，而是此演算法不支援其資訊模型。[Carbon：Empty States](https://www.carbondesignsystem.com/building-blocks/core/patterns/empty-states)
- **旅程以使用者目標組織**：階段不等於內部檔案／IR 名稱。以下旅程包含操作、疑問、風險與回復路徑。[NN/g：Journey Mapping](https://www.nngroup.com/articles/journey-mapping-101/)

## 核心旅程（待真人驗證）

主要使用者假設：有可執行 2D source 的創作者，希望變成可玩的 3D 版本，對訓練細節不熟悉。次要使用者是重現問題／調整 AI 的工程師。

| 階段 | 使用者要完成什麼 | 目前摩擦／疑問 | 新介面的反應 | 失敗／離開後如何恢復 |
| --- | --- | --- | --- | --- |
| 匯入 | 選專案或入口，確認是正確遊戲 | Source、Project Manifest、Rule IR 都在同層 | 主入口「匯入遊戲」；進階文件掛載移到檔案選單；顯示來源標題 | 保留錯誤及原路徑，禁止把舊 preview 冒充新來源 |
| 理解 | 先玩原作，知道引擎讀到什麼 | 資料多、沒有明確下一步 | Source 預览和可收起的素材／規則摘要；主動作「分析與轉換」 | 顯示失敗階段及可續用階段，不要求無差別重跑 |
| 空間轉換 | 描述 3D 意圖、調整尺寸 | 尺寸、自然語言、執行散落 | 「空間設定」聚合意圖＋維度；把現有批准／生成順序做成流程 | 意圖變更使舊候選失效，已批准版本仍有明確身分 |
| 審核／試玩 | 對照原作，批准可用版本 | 技術診斷格子易被當成正式遊戲 | Source／3D／Compare 切換；批准顯示版本；Play 明示開啟 Ursina | 未批准不能開訓；重開只重置遊戲，不改規則 |
| 可選訓練 | 問「這款能不能訓練？」並試跑 | 當前只有內建 self-test | 右下顯示當前 Target 資格；井字棋合格；掃雷／2048 顯示原因 | 不適用不阻擋試玩、匯出與轉換 |
| 觀察 | 確認有在跑、可離開面板 | 終端日誌難讀；可能凍結 | 常駐狀態、對局／迭代／loss、可取消；主畫布仍可操作 | 取消顯示保留到哪一輪；部分候選更新不冒充 checkpoint |
| 比較／續跑 | 找結果並續練 | 容易載錯遊戲模型 | 「使用上一存檔」、選檔前後核對，結果保留 Run 與 Rule 身分 | 不相容時列出差異；舊 run 不覆寫，新 run 記 parent checkpoint |

## 三稿共同契約

- 左側是遊戲／來源與產物；中央是預覽／比較；右下永遠是 AI 訓練，單人遊戲仍保留簡潔的不適用說明。
- 全局只保留一個與目前階段一致的主動作；試玩保持固定位置；訓練開始是局部主動作。
- 「已轉換」「已批准」「可訓練」「已完成一輪」「模型被採用」是不同狀態；不以一個綠色成功標籤替代。
- 顯示名稱用自然語言，完整 ID／hash 放詳細資料及報告；絕不把示意曲線稱作實際訓練數據。
- 1440×900 為主要設計尺寸；1366×768 保留主動作與訓練狀態；更窄時收起 library，再將 inspector 變成抽屜。面板捲動不得遮住安全取消。
- 8px 間距基準，正文 14px、輔助 12px；不以極細／極暗字追求風格。選取、警告、錯誤均有文字／圖標雙重識別。鍵盤焦點可見，主要控制支援 Tab／Enter，動效尊重減少動態偏好。
- 對話生成、來源轉換與 AI 自對弈分別命名；右下不是聊天機器人。

## 提案差異

| 稿 | 資訊組織 | 視覺 | 最適合 | 取捨 |
| --- | --- | --- | --- | --- |
| A Prism Studio（建議） | 穩定三欄、上方階段、中央大畫布；右上 properties、右下 training | 石墨灰，少量紫／青，霧面層次 | 日常創作與反覆試玩 | 同屏資訊多，要維持逐步揭露 |
| B Flow Atelier | 左側以任務旅程取代檔案樹，中央 Source／Target 比較，右下訓練 | 溫暖炭灰、薄荷、米白字 | 初次匯入與逐步轉換 | 資深使用者需快捷跳轉，避免過度嚮導化 |
| C Signal Lab | 大型上半部畫布，下半部控制模組；右下訓練像獨立工作儀器 | 深黑、橙色能量點、克制發光 | 空間調校、現場展示、偏創作工具的品牌 | 低高度螢幕須可收起底部詳細控制 |

採用使用者參考圖的暗色材質、精緻分區、少量亮色與有節奏的數值；不複製金融看板的圖表密度，不把裝飾性 meter 填滿創作畫布。

## 既有功能的搬遷清單（選稿後才實裝）

| 既有需求 | 新位置 |
| --- | --- |
| Entry／Project、reference library、來源資訊 | Library／匯入遊戲；Source 詳情 |
| Compile Source、Approve、Spatial Lift、Cancel conversion | 上方旅程及當前階段主動作；批准差異視窗 |
| 自然語言 intent、X/Y/Z、Lift policy | 空間設定／Properties |
| Source 2D／Transformed 3D、Play／Stop | 畫布工具列，正式 3D 仍呼叫既有 Ursina host |
| Project Session、Reset、Replay、9-API、Core self-test | 診斷工作區；內建測試與目前 Target 資格有不同標示 |
| Source parameters、Apply to copy | Properties 的 Source 分頁及另存 variant |
| Attach Rule／Manifest、IR 摘要、Scene／Input／Rule 審核 | 檔案選單／Artifacts；審核詳情 |
| Save analysis／export | 頂部 Export 選單 |
| 自對弈啟動、設定、取消、續跑、樣本、checkpoint、報告 | 固定右下 Training dock、Run 詳情視窗 |

## 選稿後的驗證安排

先用 5 位測試者（創作者 3、工程師 2）的任務走查驗證假設；這是建議方案，並非已完成調研。任務包含匯入井字棋／掃雷、找到批准、啟動短程訓練、安全取消、載入不相容 checkpoint、找回最近報告。記錄完成時間、求助次數、誤操作、對狀態的理解與主觀負担；不先訂假的量化改善結論。首要成功準則：無人把單人遊戲誤當可 self-play、無人混淆內建 self-test 與當前 Target 驗收、取消與續跑可被找到。選定稿後再實裝整體布局、字型、快捷鍵與 100%／125%／150% DPI 驗收。
