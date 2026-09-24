# 9 月 24 日：掃雷 Scene／Turtle 字體／2048 型別異常修復

開發位置：`CubeEngine-SRTP`，分支 `SRTP`。本輪只修改引擎、生成規格與測試；沒有修改使用者的失敗 Manifest，也沒有呼叫付費 API。

| 現場問題 | 原因與修復 | 驗證範圍 |
| --- | --- | --- |
| 掃雷 `2710ec96d8ec47229e9675ff41061aa5`：Scene 3 個 unresolved | 模型明確指出缺少原圖集計時數字、笑臉與格子按壓反饋。新增 `digit`／`integer_format`、宿主 `interaction` 綁定與 `pressed_style`，同步模型生成規格、驗證器、投影器與 Ursina。 | 保存實際 Rule／Asset／被拒 Scene；原拒絕仍可重現。另以明確標記的測試修正驗證 000→007→123→999、上限截斷、按壓／取消、問號格與笑臉原素材。不是新模型生成成功的證明。 |
| 掃雷的下一層結構問題 | 實際 Scene 將圖像元件放在 prefab 子節點，原綁定只找根節點。現在支援唯一可辨識的子元件，歧義仍拒絕；修正同名前綴節點誤套 variant 的診斷。 | 實際資料有 317 個 Scene 節點；離屏渲染驗證按下改變像素、取消恢復像素，Rule 狀態未變。 |
| Turtle `5d1586ffb8644ebfae42e47053aa8cbe`：Arial 15 bold 未索引 | 舊索引只理解 Pygame SysFont，漏掉 `Turtle.write(font=(...))`。加入 Turtle 字體宣告分析，精確辨識 Arial Bold、原文件行號／哈希，沿用資源打包流程。 | 真實 `connect_complete.py:69`、原字體內容哈希、離開系統字體後仍可讀取打包資源。 |
| 2048：`expected str, bytes or os.PathLike object, not int` | Windows 的 Pygame 字體掃描器會把每一項字體註冊值當路徑；含整數設定時可重現同一異常。改為型別檢查後讀取字體路徑，按字體自身 family/style 精確匹配。 | 注入整數註冊值可令舊掃描器報相同錯誤，新實作通過。2048 背景編譯預檢能把 Verdana Bold 送到模型呼叫邊界，該邊界由離線測試攔截。原截圖未保存堆疊，本次本機未找到該整數設定，**不能斷言原現場唯一根因已確證**。 |

額外補上背景編譯異常堆疊留存：`.cubeengine_llm/<遊戲>/source.errors/<工作ID>.txt`（Lift 對應 `target.errors`），避免下次只剩一句無法定位的 TypeError。

驗證結果：476 項測試，475 通過、1 項付費測試跳過、0 失敗；5 個實際 Ursina 渲染探針全部通過。本輪 API 呼叫為 0。

驗證入口：`python -m tests.run_offline_acceptance --output docs/LLM_FIELD_ACCEPTANCE_20260924.json`。測試程序及渲染子程序禁止連線；詳細結果見 [離線驗收報告](LLM_FIELD_ACCEPTANCE_20260924.json)。

使用方式：關閉並重新啟動 Workbench，再選原始遊戲跑 Compile。保留 `.cubeengine_llm` 中的階段缓存，現有機制會重新驗證相容的已付費階段；失敗 Scene／Asset 仍需要模型依新規格生成。不要把原失敗包直接 Approve。

邊界：本輪驗證的是引擎新增能力與已保存失敗的回歸；尚未取得新的模型 Scene／Input／Lift，不承諾下一輪任意生成必然成功。通用 Z/V/透明度/WASD 操作 UX 仍是獨立待辦，本輪只補此次 Scene 所需的瞬時按壓反饋。
