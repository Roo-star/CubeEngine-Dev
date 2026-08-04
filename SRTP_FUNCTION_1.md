# SRTP Function 1 — 規則文件讀取

## 1. 產品結論

Function 1 不是「把任何程式碼猜成一個遊戲」。它是可驗證的規則編譯前端：

1. 讀取 JSON 或 Python 規則來源。
2. 將來源中的空間、參與者、實體、狀態、動作、流程、隨機性、目標、終局與模式
   填入同一個 `cubeengine.srtp/rule-schema-v1`。
3. 對每個推導保留來源、方法與 confidence。
4. 做語法、類型、必填、引用、語義與可執行性校驗。
5. 只有被證明可安全編譯的規則才進入 STAL/Ursina。
6. 無法唯一判定的部分輸出結構化 Function 2／LLM handoff，不偷偷使用遊戲名稱或常識猜值。

因此，「成功讀取」和「完整理解」是兩種狀態：

- `complete`：Schema 對 Function 1 已完整且有可執行動作。
- `partial`：已取得可靠資料，但部分規則需設計師或 LLM 補全。
- `blocked`：缺少必要空間尺度、格式錯誤或結構不安全，不能生成可信戰場。

## 2. 為什麼不是一張封閉的遊戲類型表

一款遊戲可同時是「格內移動 + 即時 + 單人 + 隨機生成 + 隱藏資訊 + 計分」，
將它硬塞進「棋類／消除／蛇／俄羅斯方塊」單一分類會丟失規則。SRTP 使用多個正交維度：

| 維度 | 主要值 | 解決的問題 |
|---|---|---|
| 空間 | cell grid／intersection／edge／graph／continuous | 哪些位置可被尋址、單位是什麼 |
| 主體性 | placement／direct selection／controlled entity／system-driven | 玩家是否控制棋子、角色或只點空間 |
| 時間 | turn-based／simultaneous／tick-based／real-time／hybrid | 規則何時更新、誰現在能行動 |
| 參與者 | zero／single／multi symmetric／multi asymmetric／chance | 玩家、敵人、系統和機率角色如何分工 |
| 狀態 | board-only／extended game state／hidden state | 是否還需蛇身、手牌、分數、計時、階段等資料 |
| 不確定性 | deterministic／stochastic／mixed | 機率事件與分佈是否存在 |
| 資訊 | perfect／imperfect／partial／hidden | 每個角色能觀察什麼 |
| 終局 | pattern／achievement／elimination／score／resource／timeout／no-action | 如何結束及如何解釋結果 |
| 模式 | base + named overrides | 同一遊戲的規則變體如何共享骨幹 |

這種方式可以表達：

- 五子棋：intersection + placement + turn-based + symmetric + line outcome。
- 掃雷：cell + direct reveal + single-player + generated hidden state；沒有棋子也完全合理。
- 貪吃蛇：cell + controlled avatar + tick-based + stochastic food；狀態必須超出單格陣列，保存身體順序與方向。
- 俄羅斯方塊：cell + system-spawned polyomino + tick-based + rotation/collision/lock；不是棋子供應模型。
- 吃豆人：cell/graph + real-time controlled avatar + asymmetric state-machine enemies。
- 打磚塊：continuous 或高頻離散近似 + controlled paddle + physics-driven ball；超過 STAL v1 離散動作核心時必須明確標記。

## 3. Rule Schema 的分層

Schema 的頂層固定為：

1. `source`：文件、格式、hash、逐欄 provenance。
2. `game`：識別、描述及正交分類。
3. `space`：拓撲、XYZ site count、座標錨點、鄰接、邊界。
4. `participants`：人、AI、system、chance；不預設必須有兩名玩家。
5. `entities`：piece、avatar、terrain、projectile、collectible、marker 等，含 owner、state、supply。
6. `state`：棋盤 cell states、遊戲變數、資訊可見性。
7. `setup`：初始放置與生成器。
8. `flow`：回合、同步、tick、即時、phase。
9. `actions`：actor、verb、target、parameters、preconditions、effects、timing。
10. `randomness`：事件、概率與分佈。
11. `goals`：短期、中間、終極或玩家自定目標。
12. `outcomes`：condition、priority、terminal、winner/loser/score。
13. `modes`：基礎 Schema 上的具名 overrides。
14. `ui_hints`：不影響規則真值的呈現建議。
15. `extensions`：未映射來源欄位、解析狀態與未來擴展。

完整欄位參見 `SRTP_RULE_SCHEMA_V1.md` 與 `srtp/rule-schema-v1.schema.json`。

## 4. 網格尺度如何判定

邏輯 site count 與像素尺寸必須分開。Function 1 按證據強度依次使用：

1. **規範欄位**：`space.dimensions` 或明確 width/height/rows/columns。
2. **Level/map shape**：矩形二維陣列或等長 ASCII rows；`X=每行 site 數`、`Y=行數`。
3. **像素比例**：只有來源同時聲明 `unit=pixels` 和 `cell_size/tile_size`，且可整除時，才以
   `logical width = pixel width / cell width` 推導。
4. **Python board constructor**：`np.zeros(shape)` 或 `create_board()` 的巢狀 list comprehension。
5. **座標迴圈約束**：能唯一對應 x/y/z 變數的 `range()` 邊界。
6. **單一 N + 軸使用證據**：N 配合二維或三維 subscript rank。

Function 1 不接受以下猜法：

- 只知道 action count=200 就自行分解成 10×20；因數分解不唯一。
- 把 window width、sprite pixel size 或 HUD size 當棋盤格數。
- 因為文件名叫 Tetris 就寫死 10×20。
- 因為遊戲像五子棋就自動判斷是交叉點；必須有 source evidence 或設計師 override。

若上述信息都不存在，`space.dimensions` 保持 unresolved，輸出 error 與 LLM handoff。

## 5. 棋子、無棋子與行動對象

`participant`、`entity` 和 `action target` 分開表示：

- participant 是決策來源，不等於棋子。
- entity 是持續存在或生成的遊戲對象，不一定可由玩家控制。
- action target 可以是 coordinate、entity、region、direction、value 或 no-target。

所以：

- 掃雷可有 participant 和 reveal action，但沒有 piece。
- 貪吃蛇的 avatar 是 entity，food 是 system-owned collectible。
- 俄羅斯方塊是 system 生成 entity，玩家對它 move/rotate，供應由 generator 描述。
- 圍棋的 stone 有 unlimited/generated supply；國際象棋的 piece 是 finite initial supply。
- 非對稱敵人使用另一 participant、system actor 或 state-machine entity，不強迫與玩家共享 action。

## 6. 動作、規則與狀態轉移

每個 action 都拆成：

```text
actor + verb + target + parameters + preconditions + effects + timing
```

- `preconditions` 回答「現在能不能做」。
- `effects` 回答「做成後哪個狀態如何改」。
- `timing` 回答「何時做、是否屬於某 phase/tick」。
- action 彼此透過 flow、phase、cooldown、resources 和 state variables 關聯，而不是互相直接呼叫。

目前 STAL adapter 可執行的 declarative MVP 子集：

- coordinate `place` + `cell_equals` + `set_cell`
- coordinate `select/toggle/reveal` + `toggle_cell`
- `line`
- `all_cells_not_equal`
- `count_state_at_least`
- `state_at_coordinate`

其他動作仍會被 Schema 保存，但設為 `executable=false`，避免把 source function 名稱誤當完整語義。

## 7. 欄位校驗

Function 1 逐層檢查：

1. 文件：大小、UTF-8、格式、JSON/Python syntax。
2. 結構：required section、list/object/type、正整數 dimensions。
3. 引用：owner、actor、turn order 必須引用已聲明 participant。
4. ID：同 section 唯一。
5. 語義：`$actor_state` 必須能映射到 entity 的 integer state。
6. 機率：stochastic/mixed 必須有 event/distribution 描述。
7. 可執行性：action/outcome 是否落在已知 declarative operation 子集。
8. 安全：Python 只做 AST parse；限制 2 MiB、20,000 AST nodes、不 import、不 exec、不 eval。

正式 JSON Schema 使用 Draft 2020-12；當前 Python runtime 同時提供不依賴第三方包的語義 validator。

## 8. Workbench 的可調參數決策

適合表單的欄位：

- X/Y/Z logical site count
- coordinate anchor
- topology
- flow model
- information model
- randomness model

不應做成 slider 的欄位：

- 複合合法條件
- 多步效果
- 機率分佈
- phase graph
- 多條 outcome priority
- 模式 overrides

原因是 slider 適合連續標量，但這些是結構和語義；誤改一個節點可能令規則不可判定。
Workbench 會將安全覆寫記成 `designer_override` provenance，不修改原文件。

## 9. Function 2／LLM 邊界

每個 unresolved diagnostic 都包含：

- code / path / severity
- 現有 evidence
- 建議補充
- `requires_llm=true`
- partial Schema
- 完整 provenance

典型 handoff：

- Lua／C#／自定 DSL 尚無 deterministic extractor。
- Python 函數能辨認名稱，但 collision、pathfinding、physics、wall-kick、combo 等語義無法靜態等價轉換。
- grid 尺度不存在或多解。
- cell/intersection anchor 無證據。
- 隨機 API 被發現，但概率分佈由運行狀態決定。
- hidden information、simultaneous action、real-time scheduling 需要跨函數理解。

Function 2 應只補 unresolved paths，並在輸出後再次經相同 validator；它不能繞過 Schema。

## 10. 研究基礎

- [Ludii Game Description Language universal model](https://ludii.games/publications/ARXIV2022-3.pdf)：players、equipment，以及 start/play/end rules。
- [Stanford Game Description Language](https://logic.stanford.edu/ggp/notes/gdl.html)：role、init/true、legal、next、goal、terminal。
- [VGDL / GVGAI description](https://gaigresearch.github.io/gvgaibook/PDF/chapters/ch02.pdf?raw=true)：SpriteSet、InteractionSet、TerminationSet、LevelMapping，以及 grid/continuous arcade games。
- [OpenSpiel core API](https://openspiel.readthedocs.io/en/stable/api_reference.html)：legal actions、chance、simultaneous nodes、information/observation state、rewards/returns。
- [Regular Boardgames](https://arxiv.org/abs/1706.02462)：以 board、pieces、variables 與 regular-action rules 表達高 branching-factor board games。
- [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12)：結構與驗證契約。
- [Python AST documentation](https://docs.python.org/3.12/library/ast.html)：AST-only 解析能力與資源耗盡風險。

這些框架的共同核心不是「遊戲名稱分類」，而是 `initial state + actors/actions + legality + transition + terminal/utility`；
SRTP Schema 在此基礎上加入 CubeEngine 必需的空間錨點、3D 轉換準備、來源證據與設計師工作流。

## 11. 驗收

啟動：

```powershell
cd E:\CubeEngine\CubeEngine-SRTP
C:\Users\Yingr\.pyenv\pyenv-win\versions\3.9.1\python.exe -m srtp.workbench
```

建議依次驗收四個 sample：

1. JSON placement：完整解析、雙方輪替、occupied、三連線、draw。
2. JSON intersection：只在交點切換，無棋子與無終局也成立。
3. Python Tic-Tac-Toe：AST 讀取後直接可玩，證明腳本沒有被 import。
4. Python Tetris-like：得到 10×20、tick、random、move；物理語義留在 LLM handoff，Ursina preview 被正確阻止。

自動測試：

```powershell
C:\Users\Yingr\.pyenv\pyenv-win\versions\3.9.1\python.exe -m unittest discover -s tests -v
```
