> 历史迁移记录：当前已改用 OpenRouter，以下 OpenAI 直连配置不再生效。请使用 [OpenRouter 设置](OPENROUTER_MIGRATION_20260923.md)。

# CubeEngine 全部模型调用切换到 OpenAI

日期：2026-09-23。开发分支 `SRTP`，现有目录 `E:/CubeEngine/CubeEngine-SRTP`。

## 最后一步：填写 Key

打开 **`E:/CubeEngine/CubeEngine-SRTP/.env`**，将这一行的占位内容替换为自己的 OpenAI API Key：

```dotenv
OPENAI_API_KEY=PASTE_YOUR_OPENAI_API_KEY_HERE
```

模型等其他项已经配置好，无需改代码或复制项目目录。保存后完全关闭 Workbench，再双击根目录 `Run SRTP Workbench.bat`。

选择完整二维井字棋 → **COMPILE LLM → SOURCE IR**。新生成报告应显示 `provider=openai`，默认 `model=gpt-6-sol`。Source 成功后批准，输入 Design Intent，运行 Spatial Lift；Target 通过并批准后在 Transformed 3D 中测试。编译失败仍先检查 Diagnostics。

## 范围与实现

已扫描当前产品源码和启动脚本，外接模型调用统一经 `srtp/llm_compiler_v1/client.py`。新客户端固定调用官方 `https://api.openai.com/v1/responses`，覆盖：

- Workbench 的 Source 四 IR 分阶段编译与语义修复。
- 自然语言 Design Intent 解析、Spatial Lift 规则/表现/输入生成。
- Inspector 尺寸转换所走的同一 LLM 编译路径。
- 源码按需补读对话；source_requests/source_results 消息完整保留。
- CLI 和仍保留的旧单次提案编译路径。

移除 FreeFlow 依赖及 Gemini/Groq 调用、自动回退、轮换 Key 逻辑。旧 Key 不再生效；根目录与旧嵌套 `.env` 的旧提供商配置已清理。根目录是唯一有效配置文件，两份私有文件仍受 Git ignore 保护，模板 `.env.example` 不含密钥。

不修改历史产物的 provider 标签，不把旧 Gemini 包改称 OpenAI 生成。模型、推理设置、输出预算、传输实现进入编译缓存指纹，防止迁移后旧缓存冒充新的模型结果。

本次修改只在当前产品 SRTP 目录实施，历史测试副本、备份目录及工程师 llm 分支未改动。

## 默认参数

- 模型：`gpt-6-sol`；兼顾复杂代码任务与费用，可通过 `CUBEENGINE_LLM_OPENAI_MODEL` 调整。
- 推理强度：`medium`；请求不发送与推理模式冲突的 temperature/top_p。
- 输出上限：32768，包含推理及可见输出；超时 180 秒。
- JSON mode：要求一个完整 JSON 对象，再执行引擎自身的严格 IR、证据和行为校验。当前动态字段及源码工具回复未改为严格 Structured Outputs，不能将 JSON 格式保证等同于游戏规则正确。
- `store=false`；不创建远端 conversation，不上传文件到 Files API；编译所需源码仍作为模型请求内容发送。
- 网络断开、部分 429 和 5xx 最多额外重试两次；认证、权限、模型缺失、额度不足不循环重试。
- 接收 incomplete、refusal、内容过滤时拒绝把结果当作完整 IR；错误不回显 Key、请求头或原始服务错误正文。

HTTP 依赖使用当前已安装的 httpx 0.28.1，兼容 Workbench 的 Python 3.9.1；本机不需要为这次迁移另外安装 SDK。

## 验证边界

新增 `tests/test_openai_transport.py` 使用离线 HTTP mock，检查官方端点、认证头、JSON/推理参数、多轮消息、响应内容提取、断线/限流/账单错误、截断/拒绝和缓存模型隔离。Source→批准→Lift 的集成测试使用完整 HTTP 请求路径及引擎执行验证，但响应是明确的测试数据。

执行结果：编译器、契约、阻断回归、源码读取、转换流水线和 Snake 等 115 项测试中 114 通过、1 项真实 API 测试跳过。补充内容过滤与自然语言 Intent→Lift HTTP 路径后，17 项接口/配置测试再次全部通过。两批有重复测试，不相加作为独立用例数。CLI `--help` 已验证标注 OpenAI Responses 后端，`git diff --check` 通过。

真实 OpenAI Key 由用户最后手动填写，本轮未发起真实 OpenAI 模型调用。离线测试证明客户端和编译流程衔接，不证明账户地区、账单、模型权限可用，也不代表真实模型已经完成高质量游戏转换。

## 首次实测后的 429 修正

用户首次 Source 作业 `f65dcdc28ea9462a8ee838442dff4e71` 被旧客户端归为普通限流。旧记录未保留具体 code，不能反推原服务端正文。随后一次极小诊断请求用当前 Key 得到 HTTP 429、`error.code=credit_balance_exhausted`、`error.type=insufficient_quota`，明确当前账户的 API 预付余额已经耗尽。未发送游戏源码，未重试；证据在 `.cubeengine_llm/diagnostics/openai_429_20260923.json`。

根因：旧代码使用 `code or type`，具体余额错误码遮住了宽泛的 insufficient_quota，同时旧余额分类表未覆盖新错误码。现同时检查两者，分别提示余额、组织/项目支出上限、组织使用限额；即使收到 Retry-After 也不会对账单错误重试。错误中保存已识别的原因和真实 HTTP 尝试数，不回显密钥或原始错误正文。

下一步由 Key 所属组织的账单管理员在 [API Billing](https://platform.openai.com/settings/organization/billing) 恢复预付余额。修复客户端只能纠正诊断及无效重试，不能替账户充值。`unresolved: 5` 是编译未拿到规则后保留的初始化缺项，不表示模型另外生成了五个错误。

修正后的 OpenAI 接口回归 12 项全部通过，包含具体账单错误码、宽泛类型兜底、无效重试停止及密钥不回显。

## 官方依据

- [模型及适用任务](https://developers.openai.com/api/docs/models)
- [Responses API 迁移](https://developers.openai.com/api/docs/guides/migrate-to-responses)
- [GPT-6 请求参数与推理兼容性](https://developers.openai.com/api/docs/guides/latest-model)
- [JSON mode 与 Structured Outputs 的区别](https://developers.openai.com/api/docs/guides/structured-outputs)

动态模型解析脚本因网络获取失败；本次模型及参数选择使用已读取的官方网页，没有依赖脚本猜测模型名。
