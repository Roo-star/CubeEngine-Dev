# CubeEngine LLM Compiler — OpenRouter 使用说明

当前后端是官方 OpenRouter Responses API，唯一端点为 `https://openrouter.ai/api/v1/responses`。
完整配置、迁移范围与验收边界见 [迁移记录](../../docs/OPENROUTER_MIGRATION_20260923.md)。

## 你需要做的设置

只编辑项目根目录 `E:/CubeEngine/CubeEngine-SRTP/.env`，填写一条：

```dotenv
OPENROUTER_API_KEY=你的OpenRouter_API_Key
```

其他设置已准备好，默认模型 `openai/gpt-6-sol`、推理强度 `medium`。
无需在 `srtp/llm_compiler_v1/.env` 配置；不读取 OpenAI/Gemini/Groq Key，不自动切回其他服务。
填写 OpenRouter 账户创建的 Key；旧 OpenAI/Gemini Key 不能用于此入口。充值和 Key 限额在 OpenRouter 账户管理。

## Workbench 流程

1. 保存 `.env`，关闭并重新启动 `Run SRTP Workbench.bat`。
2. 选完整二维源码，例如 Source Library 中的二维井字棋。
3. 点击 **COMPILE LLM → SOURCE IR**，等待 Diagnostics。
4. Source 成功后 **APPROVE LLM MANIFEST**。
5. 输入三维转换意图，点击 **RUN SPATIAL LIFT → TARGET**。
6. Target 成功后批准，再从 **Transformed 3D / PLAY** 验证实际表现。

修改模型或推理设置后阶段缓存自动失效。历史成功包仍保留历史来源，不会改标签冒充 OpenRouter 产物。

## 配置

| 配置项 | 默认 | 作用 |
|---|---|---|
| OPENROUTER_API_KEY | 必填 | 单个 API Key，不能用 Key 数组 |
| CUBEENGINE_LLM_OPENROUTER_MODEL | openai/gpt-6-sol | Source、Intent、Lift、源码补读共用的 OpenRouter 模型 |
| CUBEENGINE_LLM_REASONING_EFFORT | medium | 推理强度；应与所选模型能力匹配 |
| CUBEENGINE_LLM_MAX_TOKENS | 32768 | 每次响应推理与可见输出合计上限 |
| CUBEENGINE_LLM_TIMEOUT_S | 180 | 单次请求读写超时秒数 |
| CUBEENGINE_LLM_CHAT_RETRIES | 2 | 瞬时网络/服务错误的最多额外重试次数 |
| CUBEENGINE_LLM_MAX_REQUESTS | 12 | 单次任务总 HTTP 请求上限，包含源码补读、生成修复与传输重试；不是美元预算 |

当前配置按 GPT-6 推理模型设置，不发送 temperature/top_p。使用 JSON mode；动态 IR schema 仍由引擎校验，不冒充已启用严格 Structured Outputs。
请求声明 `provider.require_parameters=true`，只选择支持所需参数的上游。OpenRouter 可以在同一模型的供应商之间重试；没有配置其他模型回退。

## CLI

在项目根目录运行：

```powershell
python -m srtp.llm_compiler_v1 --source srtp/reference_games/pygame_tictactoe/main.py --out .cubeengine_llm/tictactoe_openrouter/source
```

CLI 和 Workbench 使用同一个 OpenRouter 客户端。普通验收建议直接用 Workbench 完成批准与 Lift。

## 错误处理

- 缺少/占位 Key：本地立即提示根目录 `.env`，不发送请求。
- 401：检查 OpenRouter Key。
- 403：检查 OpenRouter Key 权限、隐私/安全设置及账号/网络地区。
- 404：检查模型名与账号的模型访问权限。
- 429 限流：有限重试；较长 Retry-After 直接提示等待。
- 402 余额或 Key 支出上限不足：不循环重试；检查 OpenRouter credits 和 Key 限额。
- 402 单次请求预算过大：增加 OpenRouter 余额或降低输出预算/输入规模；不会静默截短 IR。
- 402 明确为在途请求预算占用且带短 Retry-After：按提示有限重试；较长等待直接显示。
- 服务过载、超时：最多额外重试两次，然后返回具体错误。
- incomplete/截断：不接受半份 JSON；返回编译修复反馈。
- 拒绝/内容过滤：停止该请求，不当作语义错误反复修复。
- HTTP 200 但响应包含 error/failed：仍按失败处理，不把部分输出封装成成功包，也不自动重复生成。
- IR 校验或行为失败：仍由现有分阶段修复处理，不能用换模型绕过。

## 离线验证

完整离线工程验收（全仓测试＋三个实际 Ursina 渲染探针，禁止测试网络连接）：在仓库根目录执行 `python -m tests.run_offline_acceptance`。报告包含逐项结果，任何失败或非预期跳过均返回非零状态；不宣称真实模型生成已通过。

训练集成测试默认使用同级 `CubeEngine-AI-Dev`；其他安装位置通过 `CUBEENGINE_AI_ROOT` 指向含 Coach.py/MCTS.py/Arena.py 的真实源码目录。SRTP requirements 已包含 tqdm，不能用跳过训练用例代替依赖修复。

```powershell
python -m unittest tests.test_openrouter_transport tests.test_llm_authoring_contract tests.test_engine_conversion_pipeline
```

使用 HTTP mock 检查真实请求序列及响应解析，不使用真实 Key、不联网、不代表模型生成质量验收。

## 资源预检与付费断点恢复

编译前静态识别 Pygame `Font(None, size)`，把本机安装依赖中的默认字体以 `runtime://pygame/default-font` 提供给模型。模型能引用实际字体，不必猜字体文件或把它标为缺失。成功包携带原字体字节；移动包后不依赖开发机安装目录。暂未确认的字体分发许可保留 unknown，不编造许可。

纯程序绘制的游戏允许显式完成的空 Asset 清单；初始未分析清单或必需 unresolved 仍被阻断。源码读取直接返回 citation 对象，也支持严格验证后的 `path:start-end@sha256` 引用。模型不能靠伪造路径、哈希或行号通过验证。

同一次失败修复得到相同诊断时停止继续生成。引擎升级后可以复用输入、模型配置未变的已付费阶段，但每次都通过当前全部校验和执行测试；不复用旧验证结论。不要删除游戏目录旁的 `source.stages.json`，否则将失去已接受阶段的断点。

失败但已返回的模型定义单独保存在 `rejected_stages`。恢复时先用当前契约离线复检，再把原定义与当前错误交给第一次修复；不会当作已通过阶段或发布成功包。已接受但尚未轮到复检的阶段也保留，避免中途取消导致丢失付费输出。

## 当前后端生成契约

每个阶段接收 `backend_profile` 和前序阶段的 `reference_catalog`，生成 schema 明确组件属性、绑定结构、资源配方及真实输入范围。引擎注册表与契约不一致时在模型调用前停止。Scene 验证重放 Rule 行为轨迹，Input 验证使用真实 Scene 选取数据与宿主事件格式。

当前宿主的游戏输入是键盘/鼠标 control 的 press/release，不宣称支持所有 Input IR 触发器。动作参数域在运行时初始化时生成稳定目录；枚举完整有限域，把动态合法性写入 legality。不能依赖新实体 ID 自动扩展动作目录。Scene 状态转换仅支持 direct/not/map/numeric/format，不能在 Scene binding 填 Rule AST。完整能力边界见 `backend_profile`。

这些约束通过 JSON 模式的上下文发送，本地强校验；不等于供应商服务端强制 IR schema，也不等于独立源游戏保真验收。离线测试与真实渲染探针见 `docs/LLM_CONTRACT_AUDIT_20260923.md`。

`report.json` 的 `compilation_trace.api_usage` 保存 HTTP 请求数及服务返回的输入/输出 token、费用（若有）。费用缺失记为 null；服务未返回的用量/费用不可推算为免费。总账单以 OpenRouter Activity 为准。请求数上限不能保证固定费用；金额上限需使用 OpenRouter Key 的 credit limit。
# 宿主生命周期输入

Input IR 可以通过 `{"kind":"host_command","command":"quit"}` 请求退出当前游戏播放器，通过 `{"kind":"host_command","command":"restart"}` 重置完整游戏会话（包含终局后）。这两个命令只接受 digital intent，不允许参数、任意宿主方法或命令执行。普通游戏操作仍使用 `rule_action`；局部规则重置也应由 Rule 表达。模型生成契约与执行器使用同一命令白名单。升级后应重新启动 Workbench，避免旧进程保留旧的能力定义。
