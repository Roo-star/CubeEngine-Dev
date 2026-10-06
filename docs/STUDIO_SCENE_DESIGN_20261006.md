# 場景工作臺設計修正

2026-10-06。使用者選 A 的色彩方向保留，但最新回饋已明確要求重新設計中央工作區與交互。這是結合實際程式架構、六張視覺參考、桌面研究與操作走查的設計決策；沒有進行真人訪談或把假想旅程當作使用者研究結果。

## 對上一版問題的判斷

上一版把「空間」解讀成拓樸展示，讓真正作品離開主工作區。使用者的回饋說明：主工作區應承載作品本身、其他面板圍繞當前選中物件工作。裝飾性的完整大立方體、密集文字區和正式播放器分離，不能滿足這個目標。

參考圖共同具有穩定的側欄、分層卡片、有限的強調色、清楚的按鈕與局部工具，不是任意鋪滿發光區塊。CubeEngine 採用這套層級：最左常用工具 → 左側物件階層 → 中央作品 → 右側上下文屬性／訓練。圖示用於反覆且容易辨識的動作，匯入、試玩、空間轉換等階段名稱仍保留文字，避免只能猜圖示。

## 原則、依据與具體做法

| 一手資料 | 採用的設計／工程決定 |
| --- | --- |
| [Microsoft Windows design basics](https://learn.microsoft.com/en-us/windows/apps/design/basics/) | 穩定的視覺層級與控制元件；文字 15px、次要文字 14px、標題 18/25px，常用按鈕至少 38px 高，具有 hover、focus、disabled、selected 狀態 |
| [Microsoft icons guidance](https://github.com/MicrosoftDocs/windows-dev-docs/blob/docs/hub/apps/develop/ui/controls/icons.md) | 同一套 24px 基準的向量線條圖示，實際按鈕用 19px 圖形並提供 tooltip／accessible name；不用 emoji 或依賴未知字型的符號 |
| [Qt Window Embedding Example](https://doc.qt.io/qt-6/qtdoc-demos-windowembedding-example.html) | 原生視窗可以嵌入桌面 UI，但要明確處理生命週期與尺寸。採用 Qt 實體容器與自有遊戲子程序，實際 GUI 與 renderer 分離 |
| [Panda3D WindowProperties](https://docs.panda3d.org/1.10/python/reference/panda3d.core.WindowProperties) | 使用 parent window、origin、size 承載現有 Ursina renderer，保留 Scene/Asset 的正式呈現，不另造拓樸畫布 |
| [Microsoft SetParent](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setparent) | Pygame 的 SDL 視窗保持自有 HWND，設定子視窗樣式與 parent；不能讓來源視窗生命週期破壞 Qt 容器。DPI 與遊戲像素需要明確換算和驗證 |
| [VS Code custom layout](https://code.visualstudio.com/docs/configure/custom-layout) | 中央作品、左側導覽、右側屬性、底部資產／診斷保持位置；可調整分隔，不把主要操作埋入長篇記錄 |
| [Ableton Live concepts](https://www.ableton.com/en/live-manual/12/live-concepts/) | 借用創作工具的固定播放控制與上下文屬性；資料分析卡片的外觀只服務狀態，不主導整個作品工作區 |

原自對弈研究、ML-Agents／OpenSpiel／MLflow 方法比較與資格模型延續 [10 月 4 日研究](WORKBENCH_AI_RESEARCH_20261004.md)。本輪研究的重點是創作工作流、場景擁有權、桌面互動與實際嵌入環境，而非更換訓練演算法。

## 目標旅程與恢復路徑

| 階段 | 使用者想完成的事 | 操作與回饋 | 失敗／返回時的處理 |
| --- | --- | --- | --- |
| 匯入 | 先确认是自己的遊戲 | 明確「匯入」入口；專案名稱；原作在中央運行 | 清除上個來源的 Target 與選中物件；錯誤留在診斷 |
| 轉換 | 形成可批准的空間版本 | 第二層對話框聚合意圖、尺寸、分析、生成、批准 | 保留既有批准與取消邏輯；不把改尺寸偽裝成場景已生成 |
| 編輯 | 找物件、看效果、改佈局 | 場景點選／物件樹同步；右上 XYZ、顯示、聚焦 | 復原／重做、草稿保存／讀回；版本不符先阻擋，避免部分套用 |
| 試玩 | 在作品所在的位置驗證行為 | 明確的場景／試玩頁與固定播放、暫停、重開 | 試玩用批准版本；回到場景恢復佈局草稿，規則與外觀草稿各有歸屬 |
| 訓練 | 判斷能否訓練並短跑 | 右下資格＋原因、局數、進度、開始／取消 | 無對手時顯示不適用；啟動錯誤有可查原因；換遊戲不沿用舊數據 |
| 調參與結果 | 理解一次實驗並继续 | 設定分成試跑規模／模型與執行；輸出與 checkpoint；獨立結果視窗 | 相容 checkpoint 檢查；原 Rule 重放；歷史產物不冒充當前遊戲 |

視覺層級使用石墨底色、稍亮的工作卡片、淺紫主要動作、綠色合格狀態與暖色不適用狀態。XYZ 用低飽和紅／綠／藍定位軸向。程式生成的物件名稱保持原樣，不按遊戲名稱杜撰模型或素材。

已做真實桌面畫面與控件檢查；數字微調框在本機出現異常字形，因此產品數值改用有範圍、類型及小數位驗證的文字輸入。尚待使用者實際操作的重點為跨原生視窗焦點、習慣快捷鍵、視窗分隔比例及更長時間創作舒適度。這些不以美觀主觀宣稱或自動化測試取代。
