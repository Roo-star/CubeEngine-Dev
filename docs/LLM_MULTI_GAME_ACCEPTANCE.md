# 本轮多游戏验收：以井字棋为主示范

2026-09-22，按产品设计师要求更新。工作目录 `E:/CubeEngine/CubeEngine-SRTP`，本地开发与测试分支 `SRTP`。本轮不再只用 Snake 代表通用转换。

## 三种文件不要混淆

1. 原始源文件：如 `.py`；通过 ENTRY 导入，用于源码分析和 LLM 编译。
2. 生成的 Source / Target 包：包含 Manifest 与四种 IR；APPROVE 处理是否接受，ATTACH 加载运行。
3. 参考 fixture：预先构造的规则包，可以验证运行时和界面，但不能证明真实模型完成了转换。

之前的“成功 Source”明确指 `job:3031d47765d64c65` 的 Snake 草稿，现存 `.cubeengine_llm/snake.game.with.python.and.pygame/source/project.manifest.json`。其 `report.json` 为 `ok=true`、`compile_ready=false`（仍需用户批准）。这只是生成通过结构检查，固定坐标动作并未完整重建 Snake。

之前提供的 Snake 三维示例是 `.cubeengine_llm/round4_snake_target/project.manifest.json`，随工程师 `1feefe6` 提交；Manifest 明确写了 `playable_overlay=snake_playable_fixture`。不是用户输入 Design Intent 后现场生成。

## ATTACH PROJECT MANIFEST 的作用与操作

它读取已有完整项目包，检查 Manifest 对四份 IR 的版本引用，再打开 Project Session；不会调用模型，不会执行二维到三维转换，也不会补齐缺失 IR。

操作步骤：

1. ENTRY 导入对应的源 `.py`。这建立当前项目上下文；不能用 ATTACH 代替源代码导入。
2. 展开 Project Session Core，点击 ATTACH PROJECT MANIFEST。
3. 在完整包所在目录选择 `project.manifest.json`，不要选择源 `.py`、proposal 或单独一份 Rule IR。
4. 已批准/无需批准的完整包会直接打开 Project Session。若仅待设计者审批，再点 APPROVE LLM MANIFEST；其他必需缺口或文件版本不一致不能用批准绕过。
5. 包内 IR 必须与 Manifest 一起保留。不要把一个 Manifest 单独复制到别处。

## 现在可直接查看的井字棋参考包

- 二维：`artifacts/tictactoe_source/project.manifest.json`
- 三维：`artifacts/tictactoe_target/project.manifest.json`
- 对应规则样例：`srtp/examples/tictactoe_2d.py`

两包随工程师 `79d7c05` 提交进入仓库，其 `job.json` 标记 `runtime.backend_id=fixture`，因此只能作为参考运行结果。`examples/tictactoe_2d.py` 只有棋盘、合法落子、获胜、满盘判断函数，没有完整 GUI/主循环，不能当作可直接 PLAY 的完整游戏。

本次已经离线验证（没有调用 API）：

- 二维包载入为 3×3；依次落子 `(0,0)、(0,1)、(1,0)、(1,1)、(2,0)`，五次均接受，首位玩家获胜。
- 三维包载入为 3×3×3；依次落子 `(0,0,0)、(0,1,0)、(1,1,1)、(0,2,0)、(2,2,2)`，五次均接受，首位玩家以空间对角线获胜。

可以先用 ENTRY 导入上述规则样例，再分别 ATTACH 二维、三维参考包，理解最终交互应是什么。参考包通过不等于 LLM 通过。

## 本轮矩阵

以下五个文件均在本次离线检查中完成导入与证据提取；未进行实时模型转换，也未宣称原游戏都能成功启动。

| 优先级 | 游戏 / 源入口 | 三维设计约定 | 核心验收 |
|---|---|---|---|
| P0 主示范 | 井字棋：`srtp/examples/tictactoe_2d.py`，当前仅规则样例，需补齐完整源应用 | 3×3×3；双方交替点选任意空格；无重力；任意直线三连胜 | 合法空盘、占格、回合、49 条获胜线、平局、终局、Z=1 退化回二维 |
| P1 同轮 | Connect Four：`srtp/reference_games/turtle_connect_complete/connect_complete.py` | 保留 7×6 XY；增加 4 层 Z；每个 (X,Z) 列向最低空 Y 下落；三维直线四连胜 | 列满、重力、轮流、跨层连线、原二维规则保留 |
| P1 同轮 | 扫雷：`srtp/reference_games/pygame_minesweeper/run_game.py` | 保留 XY 尺寸，增加三层；26 邻域；本轮约定保留总雷数 | 三维邻雷数、空区展开、旗标、踩雷失败、全部安全格揭开获胜；首击策略沿用源规则 |
| P1 同轮 | 2048：`srtp/reference_games/pygame_2048/main.py` | 4×4×4；沿六个轴向滑动；每格每步合并至多一次；有效移动后在三维空格生成数字 | 合并次序、分数、禁止重复合并、随机生成、六方向合法移动与终局 |
| P1 回归 | Snake：`srtp/reference_games/pygame_snake/snake.py` | 明确层数、六方向移动、三维食物与碰撞 | 连续移动、尾部更新、增长、计分、反向限制、终局 |

三维规则约定是本轮明确的测试规格，不是默认推断：扫雷 6 邻域或 26 邻域、2048 是否跨层合并等，必须写进 Design Intent 或向设计师确认。

## 井字棋主例的 Design Intent

将二维 3×3 井字棋扩展成 3×3×3。两名玩家交替在任意空格落子，不使用重力。禁止覆盖已有棋子。在三维空间任意方向的直线上连成三个同色棋子即获胜，包括轴向、平面对角线和空间对角线，共 49 条获胜线。棋盘满且无人获胜为平局；终局后禁止继续落子。点击不同 Z 层选择落子位置。Z=1 时与原二维井字棋行为一致。保留原来的双方标识。

当前界面中文字体尚未修好时可用英文表达相同需求：

Extend the 3x3 Tic-Tac-Toe board to 3x3x3. Two players alternate placing a mark in any empty cell, with no gravity and no overwriting. Three identical marks on any straight line win, including axis-aligned, face-diagonal and space-diagonal lines (49 winning lines). A full board without a winner is a draw. Reject moves after game over. Let the user click a cell in a selected Z layer. With Z=1, preserve the original 2D rules and player identities.

## 必须先解除的通用性阻断

- 当前 `_validate_playable_session` 将非空初始摆放当作所有棋盘的必要条件。本次将可正常下棋的二维井字棋参考 IR 送入该校验，复现 `/state/initial_effects: No non-empty initial board placement`。合法空盘不应被阻断，禁止预放棋子凑通过。
- 提示词存在 head/body/food 等 Snake 专用假设；通用模板应按源证据描述玩法，不强加蛇身、食物或方向键。
- 通用棋盘的视觉/输入必须区分双方棋子、数字、雷格等，不能把所有值 1/2/-1 都显示成蛇身/蛇头/食物。
- `ok=true` 必须与行为验收分开记录；同一个模型和编译入口不意味着各游戏语义复杂度相同，也不能拿单游戏成功替代多游戏证据。

## 每个游戏都报告同一条链路

源代码可运行 → Source 生成 → Source 规则与原游戏一致 → 设计意图明确 → Target 生成 → 实际三维操作/终局测试。

每格记录通过、失败或未测及原因，附提交、模型、调用证据和完整包。公共 API 预检失败时，不继续对五个游戏重复消耗额度；标明“服务阻断，未进入游戏转换”。服务可用后，同轮跑多游戏，不等 Snake 完成才开始其他游戏。
