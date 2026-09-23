# 修复验证与验收放行记录

状态：**离线工程验收通过；真实联调在 Input 阶段按约定停止，整条转换尚未放行。**

目录 `E:/CubeEngine/CubeEngine-SRTP`，分支 `SRTP`。经用户授权调用模型 2 次，服务报告费用合计 US$0.1800529；没有修改或复制外部 AI 工程的代码。

## 真实调用与随后修复

授权总上限为 8 次 HTTP 请求，HTTP 自动重试和语义修复重试均关闭，遇编译错误停止。实际复用付费 Rule/Asset，Scene 生成通过；Input 明确指出原游戏 Escape 退出没有可用的宿主目标，因此返回 required unresolved。这是引擎能力缺口，不是服务器限额，也不应删除诊断强行批准。已停止，没有发起 Lift。

原响应和费用保存于 [真实联调记录](LLM_LIVE_ACCEPTANCE_20260923.json)，原模型定义保存在 `tests/fixtures/llm_input_failure_20260923.json`。Scene 也已加入可复用付费断点。

随后离线修复：Input IR 增加严格白名单 `host_command`（`quit` / `restart`），贯通编译器、模型能力说明、ProjectSession、ProjectHost 和 Ursina 窗口。R 能在获胜/平局后重开，Escape 请求退出当前游戏窗口；不借用终局后会被拒绝的 Rule 动作，不退出 Workbench。原失败响应保持失败，测试副本显式修改绑定用于验证新能力，不写入用户成功产物。

本次修复后的全量结果为 442 项中 441 通过、0 失败/错误、1 项付费 API 跳过，三个真实渲染探针通过。之后另有 2 项离线预算测试通过：续跑保留原收据及累计次数，最多再发 6 次，不能覆盖收据或重置费用计数。预算测试使用 mock，未增加真实调用。真实新 Input / Lift 以及新生成包的视觉验收仍待完成；恢复调用已单独询问，等待确认。

## 本轮实际结果

2026-09-23 执行统一验收命令，耗时约 56 秒：

- 442 项单元/集成测试：441 通过、0 失败、0 错误。
- 唯一跳过项为明确需要付费 API 的 `LiveSmokeTests.test_live_source_proposal_envelope`。
- 三个真实 Ursina/Panda 离屏探针全部通过。
- 测试主进程与渲染子进程禁止 socket connect/connect_ex；HTTP 流程测试使用 httpx.MockTransport，不读取真实 Key。
- 报告保存每项成功测试名称、失败详情、跳过原因、渲染探针输出和进程退出码。原始记录：[LLM_OFFLINE_ACCEPTANCE_20260923.json](LLM_OFFLINE_ACCEPTANCE_20260923.json)。

## 怎样判断修复确实有效

| 证据 | 通过意味着什么 | 不代表什么 |
| --- | --- | --- |
| 原付费响应离线回放 | 真实错误被重现；Rule/Asset 可复检；错误 Scene 一次给出完整诊断；获胜者信息不再丢失 | 没有把原错误 Scene 自动改写成成功作品 |
| 正向及反向回归 | 合法组件/绑定/资源可执行；错误结构、不可选中场景、伪造输入数据被拒绝；同错修复及时停止 | 不是让所有模型响应无条件通过 |
| 生产 HTTP 适配器全流程 | JSON 请求/解析、Source、一次修复、缓存、批准、Lift、真实输入路由、三维斜线获胜、终局拒绝和重开能衔接；缓存恢复不再请求已完成阶段 | HTTP 响应为标注的测试夹具，不能据此宣称模型真实转换质量 |
| 真实 Ursina 渲染 | 相机、HUD 生命周期、选取碰撞体、几何配方、图集动画、挤出网格和音频能够在实际渲染器执行 | 不是完整源游戏截图保真验收 |
| 真实 MCTS/Coach 测试 | 实际训练框架能消费编译后的游戏接口并完成一局自我对弈 | 不代表神经网络训练质量或 Workbench AI 对手功能已完成 |

新增加的 HTTP 全流程用例位于 `tests/test_llm_http_pipeline.py`。成功流程在临时目录使用明确的工程测试定义，生成产物不会替换用户游戏结果；失败用例确认连续同错会停止，失败阶段不进入成功目录。

## 上一轮环境错误已经解决

原因一：测试将上级 `E:/CubeEngine` 当成训练源码目录，但实际工程是同级 `CubeEngine-AI-Dev`。现在默认解析正确的同级工程，并允许 `CUBEENGINE_AI_ROOT` 覆盖其他安装位置；保留旧单仓布局兼容。文件不存在会明确失败，不自动跳过或换成假 Coach。

原因二：Workbench 使用的 Python 3.9 环境缺 `tqdm`。已安装 `tqdm 4.70.1` 及 Windows 依赖 `colorama 0.4.6`，并在 SRTP 的 `requirements.txt` 记录 `tqdm>=4.66,<5`，避免只修当前机器而遗漏后续安装。

`tests.test_alphazero_v1` 的 12 项测试全部通过；其中使用真实 `Coach` 与 `MCTS`。测试中的均匀策略网络是原有接口测试夹具，不是训练完成的模型。

## 一条命令复现

在 `E:/CubeEngine/CubeEngine-SRTP`，使用安装本项目依赖的 Python：

```powershell
python -m tests.run_offline_acceptance
```

当前机器实际使用的解释器是 `C:/Users/Yingr/.pyenv/pyenv-win/versions/3.9.1/python.exe`。若终端默认 Python 不同，用该解释器执行上述模块。

任何用例失败、渲染探针失败或出现未经允许的跳过，命令返回非零状态。PASS 表示列明的离线工程验收通过，报告始终保留 `live_conversion_certified=false`。

## 付费 Compile 前仍缺什么

有限测试不能证明未来任意 LLM 输出永远没有 blocker。当前流程仍使用 JSON 模式与本地 IR 验证；模型可能误解语义、遗漏结构或返回不支持的内容。因此不能把“441 项测试通过”改写成“下一次模型一定成功”。

如果验收要求是“用户点击 Compile 之前，工程侧已经确认实际生成可用”，还需要从已保存断点恢复真实 Source→Lift 集成验证，再检查生成包的三维行为和呈现。它会产生新的 OpenRouter 费用，累计总请求数必须包括已发生的 2 次；关闭 HTTP 自动重试，出现编译错误立即停止收费调用并转离线诊断；请求上限不等于固定金额上限。

在完成这项真实验收前，不把当前状态标记为真实转换已放行，也不要求用户靠连续点击 Compile 完成回归。
