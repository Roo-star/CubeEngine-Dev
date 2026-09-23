# CubeEngine 切换 OpenRouter / GPT-6 Sol

日期：2026-09-23。目录 `E:/CubeEngine/CubeEngine-SRTP`，开发分支 `SRTP`。

## 使用者最后一步

打开根目录 `.env`，只替换 `OPENROUTER_API_KEY` 的占位值：

```dotenv
OPENROUTER_API_KEY=你的OpenRouter_Key
CUBEENGINE_LLM_OPENROUTER_MODEL=openai/gpt-6-sol
```

模型和其余参数已配置。Key 从 https://openrouter.ai/settings/keys 创建，余额在 https://openrouter.ai/settings/credits 管理。旧 OpenAI Key 不通用；无需给 OpenAI 账户充值来使用此入口。

保存后关闭 Workbench，重新运行 `Run SRTP Workbench.bat`，选择二维井字棋并点击 **COMPILE LLM → SOURCE IR**。Diagnostics 的 provider 应为 `openrouter`，模型为 `openai/gpt-6-sol`。正常成功后依次批准 Source、填写意图、执行 Spatial Lift、批准 Target、在 Transformed 3D 中游玩。

## 改动范围

- Workbench 与 CLI 共用 `OpenRouterLLMClient`。Source 四 IR、自然语言 Intent、Spatial Lift、源码补读、生成修复均走 `https://openrouter.ai/api/v1/responses`。
- 使用 OpenRouter 命名空间模型 ID，默认固定 `openai/gpt-6-sol`。没有其他模型回退列表，不使用 OpenAI/Gemini/Groq Key。
- Responses 保留完整对话、JSON mode、推理设置、输出上限和 `store=false`；显式非流式请求。`provider.require_parameters=true` 避免上游忽略必要参数。
- 根目录 `.env` 已移除旧 OpenAI 配置并预留新 Key；客户端只读取 OpenRouter 配置，不读取嵌套 `.env`。
- 缓存身份包含网关、模型、参数要求和端点；源码签名含客户端/环境加载代码。旧模型调用缓存不能冒充本次结果。历史产物不修改来源标签。
- 保留引擎 IR、行为、批准和渲染检查。更换服务不降低生成质量门槛，也不能保证任何一次模型生成必然通过。

## 错误和重试

401/403/404、余额与 Key 支出上限、输入/参数问题直接停止；限流、网络与服务暂时故障最多额外重试两次。短 Retry-After 等待，超过 30 秒则提示用户等待。

402 区分 credits、Key 上限、单次请求估算预算过大，以及带 Retry-After 的在途预算占用。HTTP 200 中的 error/failed 也拒绝接受；读取 Responses 顶层 `error_type`，不能让通用 `server_error` 掩盖认证问题。这类已返回失败正文的生成不会自动重复收费请求。不显示原始服务错误消息或 Key。

## 验证与边界

离线测试共 120 项：119 通过，1 项真实 API 测试跳过。

```powershell
python -m unittest tests.test_openrouter_transport tests.test_llm_compiler_v1 tests.test_llm_blockers tests.test_llm_source_workspace tests.test_llm_authoring_contract tests.test_engine_conversion_pipeline tests.test_llm_snake_vertical_slice
```

15 项 HTTP 模拟测试覆盖接口地址、鉴权、模型、JSON/推理/路由参数、对话续接、余额/限流/超时、HTTP 200 失败正文、密钥保密、Source→批准→Inspector Lift→自然语言 Lift 和缓存隔离。其余为编译器及转换管线回归。

本轮没有真实 OpenRouter Key、没有发送游戏源码或收费生成请求。用户填 Key 后的模型准入、真实生成质量和三维游玩仍需实际验收；模拟响应不计入产品生成质量验收。

## 官方接口依据

- [模型 ID](https://openrouter.ai/openai/gpt-6-sol)
- [Responses API](https://openrouter.ai/docs/api/api-reference/responses/create-responses)
- [路由参数要求](https://openrouter.ai/docs/guides/routing/provider-selection)
- [错误格式](https://openrouter.ai/docs/api_reference/errors-and-debugging)
- [额度、402 与 429](https://openrouter.ai/docs/api_reference/limits)
