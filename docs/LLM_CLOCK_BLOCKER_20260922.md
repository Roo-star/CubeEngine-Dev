# Source 编译阻断：scheduler.clock（2026-09-22）

当前诊断目录及分支：`E:/CubeEngine/CubeEngine-SRTP`，`SRTP`。本次只做离线诊断，没有调用模型、读取 Key、修改失败包或批准草稿。

## 现场与直接原因

失败包：`.cubeengine_llm/snake.game.with.python.and.pygame/source.failed/72f8a574873747019cd677943d231608`。

保存的 proposal 在 Rule IR `/flow` 中生成了 `model=fixed_tick`、`scheduler.clock=timer`、`tick_hz=8`。引擎允许的 clock 只有 `turn`、`event_queue`、`fixed_tick`、`real_time`，因此拒绝 `timer`。这次明确的阻断是模型输出与引擎契约不一致，不是记录中的配额、地区或网络错误。不能据此保证以后请求不再遇到服务限制。

现有归一化已有 tick/ticks/tick_based 等别名，但没有 timer；后续 setdefault 不覆盖已存在的非法值。Source 提示没有列出 clock 全部允许值；修复反馈只有 Unsupported scheduler clock，没有实际值和允许值，修复信息不充分。

`attempts=3` 是编译尝试次数，不代表使用了三个 Key。当前包不足以还原每一次原始模型响应，不能断言三次都生成了 timer。`unresolved=5` 是补丁未能应用后仍存在的初始待解决项，不是五个 API 错误。

## 离线对照结果

1. 加载该包保存的四份初始 IR 和 proposal，运行 `validate_and_apply_proposal`：复现原始 clock 错误。
2. 仅在内存副本将上述 clock 改为 fixed_tick：四 IR 补丁校验返回成功。没有写回现场文件。
3. 对结果继续运行 `_validate_playable_session`：出现 required unresolved `/state/variables`，原因是没有 topology_site 状态变量，Project Session 无法显示棋盘。

后续问题的具体证据：生成结果把棋盘放在 `state.grids`；`state.variables` 只有全局 snake_dir。四个方向动作把蛇头写到固定坐标 (10,9)/(10,11)/(9,10)/(11,10)，没有按当前位置推进。即使修正 clock，也不能把这份结果作为可玩的贪吃蛇验收，更不能声称完成 3D 转换。

## 交给工程师的修复范围

- 从引擎契约提供合法 clock 值及与 flow.model 的一致性约束；修复反馈带路径、实际值、合法值。不要对所有游戏无条件把 timer 替换成 fixed_tick。
- 对这类已明确 fixed_tick 和 tick_hz 的输出采用有条件、可记录的规范化，或定向要求模型修复；保留本失败样本作为离线回归。
- 正确生成 topology_site 棋盘变量；检查 grid.set 引用存在、坐标更新依赖当前状态，不能仅凭四 IR 格式合法即宣告玩法成立。
- 保存每轮模型响应与验证结果，能够判断修复是否实际生效，避免让用户盲目重试。
- 延续以二维井字棋 → 三维井字棋为主验收；空棋盘应合法；Snake 作为回归，不能把 Snake 预放头/身体/食物要求推广到全部棋类。

## 后续修复与分支核对

用户授权直接修复后，已成功 fetch origin llm，远端最新仍是 1feefe6cd3de795511e2075f54767cade750a26d。修复前 SRTP 与该提交的 `_coerce_flow_object` AST 完全相同；prompts.py 和 rule_ir.py 无差异，本问题涉及的逻辑没有因分支合并改变。

已在 SRTP 修改生产代码：仅当 flow.model 已明确 fixed_tick 或 real_time 时将 timer 转成对应时钟；缺失频率仍报错，其他模型不猜测。提示明确合法时钟，校验错误返回实际值和合法值供模型修复。

新增四项回归覆盖明确模型、歧义模型、缺失频率和保留合法时钟。编译器、阻断回归和 Workbench 共 82 项测试，81 通过、1 跳过真实 API 测试。保存的现场 proposal 经生产 `_coerce_rule_ir_operation` 转换再应用四 IR 补丁，成功。原失败文件未改动。

蛇头固定坐标移动已单列待办；棋盘状态声明仍是独立阻断。用户需关闭旧进程，从当前 SRTP 的启动脚本重启后重新 Compile；离线验证不代表完整 Source/Lift 或真实服务调用成功。
