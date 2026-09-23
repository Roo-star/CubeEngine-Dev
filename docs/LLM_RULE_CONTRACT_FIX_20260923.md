# Rule IR 生成与执行契约修复

日期：2026-09-23。目录 `E:/CubeEngine/CubeEngine-SRTP`，分支 `SRTP`。

## 现场结论

用户 Source 作业 `3dd9405d03f74b3c8dc5963605904089` 的三轮已保存模型响应表明：

1. 第一轮把棋盘变量的单格类型写成不存在的 `core:grid`；第二轮已改掉。
2. `foreach` 使用 `domain/scope`，运行时实际消费 `query/as/effects`。第三轮补了 `as`，仍未补 `query`。
3. 各轮的 `grid.set` 都使用 `target`，运行时实际要求字面的 `state` ID。
4. 第三轮另外将布尔变量声明为取值 0/1 的枚举，却用 false 初始化；严格类型系统区分 false 与 0。

旧生成 schema 对类型和命令操作数过于宽泛，与真实运行时不一致。补丁应用又只抛出第一个校验错误，有限修复轮次依次花在早已可以同时检查出的字段上。最终诊断累积历史错误，使已修好的类型错误仍然出现。

这是编译器接入契约及修复反馈机制缺陷，不是该现场的 API 限额或用户操作问题。修复没有把旧错误规则自动改成合法规则，也没有替换为手写游戏。

## 实施

- 新增 `srtp/ir_v2/command_contracts.py`：按真实 EffectTransaction 的操作数定义命令契约，供生成 schema 与 Rule 校验器共同消费；区分表达式、字面 Rule ID、局部变量名和嵌套命令。
- 生成 schema 使用运行时的真实内建类型集合和逐命令分支，明确 `foreach`、`grid.set`、状态写入及随机分布格式。棋盘是存储作用域，不是 `core:grid` 类型。
- 校验所有命令必需操作数以及 ID/局部名称形状；嵌套循环和初始效果也检查。
- 构建器在补丁应用前对完整候选 Rule 执行批量静态校验；字面初始值使用真实 RuleTypeRegistry 检查。最多反馈 40 项错误，不为凑通过而更改语义。
- 模型收到当前 `repair_diagnostics` 和单独的 `repair_history`；最终诊断只展示当前失败，历史仍在 compilation_trace 中保留。
- 新契约、校验器和类型模块纳入阶段缓存指纹，避免继续使用旧契约下的缓存。
- 行为执行、来源证据、批准、哈希与发布门槛保持有效；未通过的候选不会替换成功文档。

## 验证与边界

两个相关测试批次共 156 项，155 通过、1 跳过。覆盖作者契约、修复闭环、Rule 运行时、编译器、Snake 回归、源读取、多游戏算法对照、Workbench、ProjectHost；不是整仓测试全部通过的声明。

新增测试验证：第一轮一次给出类型、query、as、state 错误；下一轮已修的错误从当前列表消失但历史保留；字面类型错误同轮反馈；修正的 foreach/grid.set 经过真实初始化和落子行为测试后，才继续其余三个 IR 阶段。

未修改三份原模型响应的离线回放：第一轮可同时指出 8 处结构/类型错误；第三轮剩余 6 处错误，包括原先隐藏的布尔枚举初始化问题。仍拒绝旧错误响应是预期结果，回放不是生成成功。

真实 Gemini 验证共两次请求，关闭外层自动重试，均返回 “This model is currently experiencing high demand”，没有得到生成结果。因此此次只能确认上述编译器缺陷已修复并通过本地回归，不能宣称新模型输出或完整 2D→3D 工作流已验收。

本地证据（不含密钥）：

- `.cubeengine_llm/diagnostics/authoring_contract_20260923_replay.json`
- `.cubeengine_llm/diagnostics/authoring_contract_20260923_live.json`
- `.cubeengine_llm/diagnostics/authoring_contract_20260923_live_2.json`

后续真实验收必须重新启动 Workbench 加载修复后的 Python 模块，以完整井字棋入口执行新的 Compile；旧失败包不能直接 Approve。服务恢复后还须通过生成的 Rule 行为、外观、输入和 Spatial Lift 验收。静态契约修复不等于模型语义正确或三维呈现质量已达标。
