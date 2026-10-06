# 原有四款 3D Demo：恢復入口與中央嵌入

2026-10-06，分支 `WorkbenchAI`，修改前基準 `4846b4a`。原有四款示範仍保存在程式庫；新版 Studio 遺漏了原有 adapter 播放入口。此前以「掛載 Manifest」回答這四款的載入方式不準確，此處更正並補回入口。

## 開啟及載入

雙擊本工作目錄的 **Run CubeEngine Workbench.bat**，在左側範例選擇器下方點 **3D Demo**，選擇：

- **3D 貪吃蛇**
- **3D 掃雷**
- **3D 四子棋**
- **3D 2048**

選取後，原有 Ursina Demo 直接嵌入中央 **3D Demo** 頁。無須找 Manifest、重新轉換或呼叫模型。上方圖示可停止／播放、暫停／繼續、重新開始；右鍵拖曳旋轉、滾輪縮放，中央框選按鈕恢復視角。點中央取得鍵盤焦點；原有按鍵、點擊、切層與透明度控制保留。可以切到 **原作 2D** 遊玩原版，返回 Demo 時沿用同一局；離開的遊戲會暫停。

左側 **切換範例並運行** 繼續載入五款原作 2D；**3D Demo** 是原有四款空間示範的直接入口。正式四 IR 專案繼續透過 **掛載場景** 載入批准的 Project Manifest。

## 四款程式的實際位置

原有規則在 [transformed_games.py](../srtp/transformed_games.py)，原有 Ursina 播放器在 [transformed_viewer.py](../srtp/transformed_viewer.py)，啟動合約在 [transform_runner.py](../srtp/transform_runner.py)。本輪沒有修改四款遊戲規則、來源程式或使用者生成產物。

| Demo | 保留的原作入口 | 原有 adapter |
| --- | --- | --- |
| 3D 貪吃蛇 | `srtp/reference_games/pygame_snake/snake.py` | `snake` |
| 3D 掃雷 | `srtp/reference_games/pygame_minesweeper/run_game.py` | `minesweeper` |
| 3D 四子棋 | `srtp/reference_games/turtle_connect_complete/connect_complete.py` | `connect` |
| 3D 2048 | `srtp/reference_games/pygame_2048/main.py` | `2048` |

選單採用明確的示範目錄，共用原啟動器的 adapter、尺寸及來源參數驗證。它不依遊戲名稱修補模型輸出，也不在批准失敗時自動替代生成專案。

## 宿主修正及範圍

新增專屬 Demo 子程序／視窗，沿用原有 renderer 和 adapter；Panda3D 子視窗原點設為中央宿主的 `(0, 0)`，避免 Ursina 依整個螢幕居中而把畫面移出中央。縮放使用實際 DPI 尺寸，HUD 按嵌入區域寬高比重新定位，操作提示不再被左右邊界裁切。

宿主暫停會停止原規則的遊戲輸入和時間推進；命令處理、Qt 界面與呈現持續運行。切換 Demo／來源及關閉工作臺會釋放其自有子程序，保留一般匯入來源時不執行程式的行為。

這四款是原有 adapter 試玩版本，沒有正式 SceneIR 物件編輯或訓練綁定。右上屬性說明試玩範圍，右下 AI 顯示 **此示範版本未接入自對弈訓練**，開始按鈕禁用。選 Demo 會清除原先批准場景／訓練 Target；同入口已有批准版本的快取也不會被偷偷掛回。正式批准場景的編輯和合格 3D 井字棋訓練保留既有路徑。

## 驗證

- `tests.studio_demo_probe`：**31 項通過**。透過真實 Qt 選單逐款啟動，檢查原生子視窗歸屬、實際 3D 物件與中央桌面像素、原遊戲狀態變化、暫停時输入／時間凍結及 Qt 心跳、重開狀態、縮小視窗、切到原作後返回、舊程序釋放、正式批准場景恢復。結果及四張真實視窗截圖位於 [artifacts/studio-demos](../artifacts/studio-demos/acceptance.json)。桌面截圖遇到測試視窗最小化會先恢復該自有視窗；不接受 1×1 空截圖。
- `tests.test_srtp_transformed_games tests.test_srtp_presentation_bridge tests.test_studio_scene tests.test_srtp_workbench`：**38 項通過**。包含原四款規則、素材橋接、Demo 不恢復快取訓練版本、來源切換清除 Demo，以及未批准場景不自動以 Demo 替代。
- `tests.studio_source_probe`：**53 項通過**。補回 Demo 後再次檢查五款原作 2D 的啟動、操作、縮放、錯誤／重試，以及批准 3D 場景關聯的清除與恢復。结果保存在 [artifacts/source-switch/acceptance.json](../artifacts/source-switch/acceptance.json)。

輸入使用 Qt 控件及顯式測試模式的遊戲入口，截圖是實際 Windows 視窗；沒有 OS 輸入注入，不宣稱完整真人焦點／快捷鍵驗收。原生嵌入本輪只驗證 Windows；沒有把原有四款 Demo 當作新的模型自動轉換驗收，也沒有新訓練驗收。

**本輪 0 次模型 API、0 新模型費用。** 使用四款 Demo 不需要付費生成；任意來源的新轉換仍屬原有轉換工作線。

```powershell
.\.venv-workbench\Scripts\python.exe -m tests.studio_demo_probe
.\.venv-workbench\Scripts\python.exe -m tests.studio_source_probe
.\.venv-workbench\Scripts\python.exe -m unittest tests.test_srtp_transformed_games tests.test_srtp_presentation_bridge tests.test_studio_scene tests.test_srtp_workbench
```
