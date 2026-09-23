# 完整游戏 → 可玩三维游戏：目标与当前交付

> **历史阶段记录，开发优先级已更新。** 请以 [自动转换 CTO 决策](E:/CubeEngine/CubeEngine-SRTP/docs/LLM_CONVERSION_CTO_DECISION_20260922.md) 为准。本文下方的参考包 Attach、Target 优先路由、通用网格窗口检查只证明局部基础能力，不再作为用户的产品验收步骤。完整外观恢复是默认转换能力，与规则并行开发；正式验收必须从原始二维游戏源码在 Workbench 内完成自动转换，不手工提供目标 Manifest。

用户确认的技术方向：LLM 生成 IR，现有 Rule/Input/Project Runtime 执行规则，Ursina 负责三维交互呈现。二维井字棋先闭环，再验收 Connect Four、扫雷、2048 和 Snake。所有本地修改仍在 SRTP。

## 目标与验收

入口必须是完整可玩的二维源码，包含界面、输入、主循环和结束条件。编译器产物必须在 Transformed 3D 中打开真实 Ursina 窗口，可旋转观察、选择格子、操作游戏，并且遵守转换后的规则。分层网格与 IR 文件仅作为诊断，不作为完成标志。

井字棋默认验收：3×3×3、无重力、X/O 轮流在空格落子；平面及空间直线三连获胜（49 条线）；占格、越界和终局后落子拒绝；满盘无赢家为平局；可重开。Z=1 应退化为二维规则。参考包测试和真实模型转换分别记录，不能相互替代。

## 本次已落地

- 完整源游戏：`srtp/reference_games/pygame_tictactoe/main.py`，已加入 Source Library 首项；Pygame 窗口、主循环、落子、胜负、平局、重开和退出。主循环通过无屏幕显示驱动启动/绘制/退出检查。
- `srtp/project_viewer.py`：真实 Ursina 三维网格呈现；通过 ProjectHost 加载批准的 Manifest。点击进入 Input IR，合法性/状态/胜负交给 Rule Runtime；时钟型游戏由运行时时钟推进。没有使用 Snake/Connect 等手写游戏类来代替模型规则。
- Transformed 3D 优先打开当前批准或明确附加的 Target；自动发现的目标检查源证据哈希。目标损坏/未批准时不静默回退到手写适配游戏。没有目标时，现有适配演示明确标为 built-in adapter demo。
- Approve Target 后选择 Transformed 3D，提示 PLAY；再次 Lift 明确表示重新生成。
- 修复通用空棋盘误判：井字棋不能被要求预放 Snake 的头/身体/食物。
- 模型对象误填 ID/类型造成 TypeError 的补丁路径转为可修复诊断；归一化错误提供文件/行号并保留失败产物。状态类型/拓扑引用错误与输入动作对象 ID 做类型保护。

## 尚未完成，不能宣称全链路通过

- 新 Ursina host 是通用网格呈现第一版：有真正三维几何、相机和交互，但尚未完整渲染 Scene/Asset IR 中所有模型、贴图、动画及源游戏视觉样式。目前格值以数字/颜色呈现；X/O 艺术呈现也待完善。这与“恢复游戏全部外观”仍有差距。
- 模型输出的语义正确性仍需独立验收。固定坐标动作通过 schema 不等于正确移动，旧 Snake 待办继续保留。
- 当前扫雷异常只有 Console 的 unhashable dict，没有该次原始响应/堆栈；不能断言已准确复现它的具体字段。本次修复了可复现的同类类型错误与处理路径，仍需新一轮现场验证。旧扫雷包还存在空棋盘误判，此问题已改。
- 不把现有井字棋工程参考包当成新源码的模型转换成果。真实 Gemini Source 验收单独进行并记录服务结果。

## 本地验证

相关 102 项测试：101 通过，1 项真实 API 测试跳过。覆盖完整二维井字棋规则、运行时三维对角线胜利/占格/终局拒绝/重开、空棋盘、类型错误、生成目标路由与过期目标拒绝，以及已有编译回归。Ursina 真窗口启动、渲染、退出成功；截图 `URSINA_PROJECT_TICTACTOE_20260922.png` 来自工程参考 IR 的真实窗口，不是模型生成图。截图只证明渲染路径，点击规则另由同一 ProjectHost 测试验证。

## 使用者测试步骤

1. 重启当前目录 Workbench，选择 Tic Tac Toe · Complete Pygame source，Source 2D → PLAY，先确认完整二维游戏。
2. 验证新三维入口时，可明确附加 `artifacts/tictactoe_target/project.manifest.json`，切到 Transformed 3D → PLAY。这是参考包测试。
3. 验证真实模型时：重新选择该源码，Compile → Approve Source → 输入下述要求 → Run Spatial Lift → Approve Target → Transformed 3D → PLAY。
4. 三维操作：鼠标右键拖动旋转、滚轮缩放、`[`/`]` 选择可落子的层、左键落子、R 重开。方向键/PageUp/PageDown 仍交给目标 Input IR，是否产生动作取决于模型绑定。

建议 Design Intent：Convert this complete 2D Tic Tac Toe game into a playable 3×3×3 game. Preserve X/O alternating turns and empty-cell placement. No gravity. Three marks in any straight axis, face diagonal, or space diagonal win. A full board with no winner is a draw. Reject occupied cells and moves after the game ends. Allow restart. Render the entire board as a rotatable 3D volume and allow selecting cells on all three Z layers.

下一阶段优先级：真实井字棋 Source/Lift 行为验收 → Scene/Asset 显示适配与 X/O 视觉 → 自动玩法验收阻止伪成功 → 扫雷/Connect/2048 → Snake 连续移动。不能以继续增加 Key 代替以上工程工作。

## 本轮真实 API 结果

用户明确允许新增井字棋源码及证据发送至 Gemini 后，进行了最多两次生成尝试。收到实际模型输出，最终失败包为 `.cubeengine_llm/tic.tac.toe/source.failed/cf97496c15fc48c28b91fd55da9d9dc9`：动作参数类型写成 core:coordinate，合法类型为 core:coord。已增加这个明确别名的规范化和回归。

更关键的是，保存输出把棋盘写在 state.cell_states，落子动作总是写值 1，未忠实表达轮流与胜负。因此不能批准它为正确井字棋，修复类型名也不等于闭环完成。本轮没有继续增加真实请求，没有批准或发布该失败产物。
