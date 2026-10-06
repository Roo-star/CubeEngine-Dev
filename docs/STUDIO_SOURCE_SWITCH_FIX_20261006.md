# 範例切換、啟動與中央黑屏修正

分支：`WorkbenchAI`；修改前基準：`2f5139d`。回應「除井字棋外，切换遊戲無法啟動、中央沒有場景」。本輪修改宿主與工作臺，不修改原作、遊戲規則或既有批准產物。

## 使用方式與可見結果

重開 **Run CubeEngine Workbench.bat**，在左側頂部 **切換範例並運行** 選擇遊戲。井字棋、貪吃蛇、掃雷、四子棋、2048 均直接在中央「原作 2D」運行，可暫停、繼續、重啟及切換，畫面按可用空間等比縮放。2048 先在原作選單選主題、目標，再按 play，方向鍵或 WASD 移動。

自行匯入的來源仍先做靜態載入，使用者按播放才執行。未掛載已批准空間版本時，3D 場景／試玩不可點擊，旁邊說明掛載條件，原作可繼續遊玩。這次沒有把五個原作都宣稱為已完成 3D 轉換。

同一次 Studio 工作階段內，重新選取**相同入口、相同來源版本**可恢復曾成功掛載的場景關聯；再切到「場景」即載入該版本。換遊戲或修改來源會清除舊場景／訓練綁定。此關聯尚未跨工作臺重啟保存，重新開啟後可用「掛載場景」選既有批准產物。

## 根因與修正

| 已重現問題 | 宿主修正 |
| --- | --- |
| 選範例只匯入，不啟動；3D 未掛載仍能進空白頁 | 明確的選取並運行入口、3D 可用條件及原作回退；普通匯入保留非執行行為 |
| 蛇與 2048 的多個模組持有 display Surface，重複設定同模式時被宿主銷毀 | 同模式沿用原 Surface；真正換尺寸仍重建自有 SDL 視窗，保留掃雷 bootstrap 相容性 |
| 視窗小於 800×800 時，蛇有軟體幀但實際中央全黑 | 開啟非整數等比縮放；驗收加入桌面中央區域的像素檢查，不只看程序及 source_frame |
| 2048 在 Windows 字型登錄掃描拋 TypeError | 沿用既有 runtime_bootstrap 的字型備援與旧 NumPy 別名相容處理 |
| 2048 只在操作時重畫，宿主命令依賴 display.update 便會停住 | 在 pygame 事件迴圈也處理命令；resize 重畫最後一幀；暫停期間仍泵送原生訊息，避免切往 3D 的焦點阻塞 |
| Turtle 四子棋被直接拒絕 | 新增 Turtle／Tk 自有子視窗適配器，保留原 Canvas 與回呼；Tk 主執行緒處理命令，暫停阻擋遊戲輸入與 Turtle 計時回呼 |
| 錯誤只顯示「請看診斷」 | 中央顯示實際失敗原因及重試方式，完整記錄保留在事件頁 |

縮放設定核對了 [Pygame 2.6.1 display 實作](https://github.com/pygame/pygame/blob/2.6.1/src_c/display.c#L1133-L1143)：SCALED 整數縮放開關讀取字面 `SDL_HINT_RENDER_SCALE_QUALITY`，與 SDL 濾波用的 `SDL_RENDER_SCALE_QUALITY` 不同。兩者分別設定，來源邏輯座標不變。

## 離線驗收

- `tests.studio_source_probe`：**53 項通過**。真實 Qt 控件、五個原作程序與原生子 HWND；逐一選擇、操作、暫停／繼續、重啟、縮小視窗及切換。驗證井字棋落子、蛇移動、掃雷開格、四子棋黃／紅落子及暫停阻擋、2048 選單進遊戲與移動。檢查中央可見像素、舊視窗退出、Qt 容器存活、3D 關聯恢復；中性名稱測試來源重現 display 生命週期與錯誤／重試。結果：`artifacts/source-switch/acceptance.json`。
- `tests.test_studio_scene tests.test_workbench_training tests.test_srtp_workbench`：**37 項通過**，包含改名／修改來源不復用舊批准場景，以及缺少 3D 時不進空白頁。
- `tests.studio_scene_probe --mines-target <本機既有批准掃雷>`：**30 項通過**，回歸原生 3D 場景、選取／編輯／草稿、InputIR、掃雷單人資格阻擋及真實 CPU 短程訓練；4 局存檔按原 Rule 重放成功。結果：`artifacts/studio-v2/integration.json`。
- `artifacts/source-switch/example-1.png` 至 `example-5.png` 是運行中的桌面視窗截圖，`before.json` 保存修正前的啟動結果。遊戲原生幀另外保存供比對，沒有拼接或模擬中央場景。

輸入探針使用 Qt 控件和顯式測試模式下的遊戲事件入口，沒有 OS 滑鼠注入；不冒稱全套真人焦點／快捷鍵驗收。Turtle 支援原 Canvas、事件與 Turtle 計時器；任意繞過這些介面的執行緒／阻塞迴圈沒有通用可暫停保證。Windows 以外、瀏覽器來源、任意遊戲轉換及任意 AI 訓練不在本輪通過範圍。先前交付列明的整庫既有失敗沒有在此被改寫成全通過。

**本輪 0 次模型 API、0 新模型費用**；不需重新 Compile 或付費生成來驗收此次修復。新 3D 轉換仍屬既有轉換工作線。

```powershell
.\.venv-workbench\Scripts\python.exe -m tests.studio_source_probe
.\.venv-workbench\Scripts\python.exe -m unittest tests.test_studio_scene tests.test_workbench_training tests.test_srtp_workbench
```
