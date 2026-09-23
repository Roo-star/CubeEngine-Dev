# LLM 功能深度诊断与工程师推进要求

日期：2026-09-22。唯一日常目录：`E:/CubeEngine/CubeEngine-SRTP`。本地修复分支：`SRTP`；工程师交付从 `origin/llm` 通过 Git 合并。不要复制工程或为每个提交另建目录。

## 决策结论

当前卡住的是三个独立层面：API 服务可访问性、生成结果质量、运行时/Workbench 集成。换 API 只解决其中一部分；重试免费 Gemini 不能作为产品验收方案。

推荐工程师先验证阿里云 Model Studio 的香港端点，接入可配置的 OpenAI 兼容客户端，使用有预算上限的正式 API 额度。具体模型必须在实际账户、区域下确认可用，再用同一组规则样例比较，不直接宣称某模型最强或一定转换成功。现有 Gemini 保留为可选供应商；不再是唯一验收入口。若已有 Azure 企业账户，可评估 Azure 上的 OpenAI 模型；不要为本轮演示同时接四家。

OpenAI 兼容协议不等于 OpenAI 官方服务。Google Gemini API 和 OpenAI API 当前官方支持列表均未列出香港；从香港本地直连 OpenAI 不应被承诺为直接替换方案。不要把更换 VPN 国家作为交付要求。

## 已核实的访问事实

- 已实际读取 [Google AI Studio / Gemini API 地区列表](https://ai.google.dev/gemini-api/docs/available-regions)，列表没有香港。Gemini 消费者网页/应用与开发者 API 是不同服务，不能从网页可用推断 API 可用。本次消费者网页地区文档读取超时，不对其最新香港开放时间作断言。
- 已实际读取 [OpenAI API 地区列表](https://developers.openai.com/api/docs/supported-countries)，列表没有香港。
- 已实际读取 [Model Studio OpenAI 兼容接口文档](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope)，其中明确列出香港端点 `https://{WorkspaceId}.cn-hongkong.maas.aliyuncs.com/compatible-mode/v1`。需要实际工作空间 ID、该区域密钥、可用模型和额度；不能把占位符直接当可调用地址。
- [DeepSeek 官方接口文档](https://api-docs.deepseek.com/)提供 OpenAI 兼容调用方式，可作为另一候选；本次未验证本账户准入、额度、模型质量或实时调用，不能承诺已可用。
- [Azure 模型与部署文档](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure?view=foundry-classic)说明不同模型有不同部署区域。需要按企业订阅确认，不能把 Azure 的可用性和 OpenAI 直连的地区规则混为一谈。
- 免费 API 也限制每分钟请求、每分钟输入量、每天请求量等；付费同样有额度限制，只是等级和额度不同。Google 限额按项目而不是 Key 计算，同项目多个 Key 不会增加项目配额。[官方限额说明](https://ai.google.dev/gemini-api/docs/rate-limits)
- AI Studio 配额页 Permission Denied 不能单凭文字归因：可能是地区准入、账号/年龄验证或项目访问权限。必须由拥有项目权限的人在可使用服务的环境中核验。不断刷新同一个链接不是解决方案。

## 网络出口：已知与未知

2026-09-22 16:19:45（UTC+8），同一 Python/httpx 环境分别访问 ipify 和 ipinfo，两个结果一致：当前公网出口 `45.140.221.10`。ipinfo 标为 `SG`、网络 `AS142594 SpeedyPage Ltd`。

这是当前诊断目的站看到的出口；不能证明 Google 目的站走同一条路，也不能证明 Google 的 IP 地区库给出相同归属。不能据此推断历史 VPN 节点或真实物理位置。

本次进程读取到的 HTTP_PROXY / HTTPS_PROXY / ALL_PROXY 未设置，Windows WinHTTP 显示直连。系统级 VPN/TUN、分应用代理或分流仍可能存在，未修改任何网络设置。历史编译日志没有记录公网 IP、代理配置或请求 ID，无法还原每次呼叫的 IP。不同错误在不同时刻出现不等于证明 VPN 曾切换。

联网核查最初被工具自动审批超时阻断；使用者明确授权后，官方文档和两项出口查询已完成。Cloudflare 出口探测超时，不能把单个探测站超时当作 Google API 断网证据。未进行携带密钥的实时模型调用。

## 现场调用顺序与归因

以下时间来自本地失败报告文件时间，表示保存失败结果的时间，不精确等于 HTTP 发起时间。

| 本地时间 | 现场结果 | 归因及处理 |
|---|---|---|
| 14:39 | Source 草稿生成成功 | 只通过结构检查，不代表游戏行为正确 |
| 14:46 / 14:51 | Gemini high demand | 服务容量暂时不足；有限退避后停止 |
| 15:07 | Source JSON 截断 | 生成/解析失败；旧保存逻辑另造成 Manifest 与四份 IR 混用 |
| 15:27 | Unsupported scheduler clock | 规则结构不符合引擎规范；不能用换网络解释 |
| 15:30 | JSON 缺分隔符 | 输出格式失败；需要结构化输出、完整响应及结束原因 |
| 15:42 | rate limited | 服务端限流/配额；包装库未保留具体维度 |
| 15:48:36、15:48:40、15:49:11 | User location is not supported | 服务按该次请求判定不满足地区准入；重试同一路径不能解决 |
| 15:53 | Server disconnected | 连接/服务断开；不能仅凭该文本定位用户网络或服务器 |
| 15:56、16:03 | rate limited | 地区拒绝和额度错误在不同调用发生；不能混作同一根因 |

`unresolved: 5` 是棋盘、动作、场景、资源、输入绑定五类未生成内容，不是五把 Key，也不是需要设计师补五个配置。

## 当前代码与产物的真实能力

### 服务层

- `srtp/llm_compiler_v1/client.py` 仅装配 GeminiProvider / GroqProvider。仓库 `.env` 中只有 Gemini 凭据，没有 Groq、OpenAI、DashScope、DeepSeek 凭据。本次只核验了配置是否存在，没有输出密钥。
- 默认模型硬编码为 `gemini-3.6-flash`，允许环境变量覆盖。不能把修改 Key 当作更换供应商；需要供应商适配和端点配置。
- 本机安装的 FreeFlow Gemini 适配器没有在请求中启用 JSON MIME/schema 约束；当前依靠提示词约束加事后解析。
- 适配器只读取响应 `parts[0].text`，并丢弃 usage，限流时改写为通用错误。应检查完整文本块、原始结束原因、HTTP 状态、配额维度和 Retry-After。上述是确认的诊断能力缺陷，不能在没有原始响应时武断认定某次截断只由这一处造成。
- 当前 Snake 首次 Source 提示约 26,223 个字符（不是 token 数），单次要求四种 IR；默认输出上限 12,288 tokens，失败还可能触发修复请求。一次点击并不一定等于一次模型调用。免费额度不适合作为稳定验收保障。

### 规则层

保存的成功 Source 中，右方向动作固定写入 `[6,10]`，上下左右也都是固定坐标写入；未形成完整蛇身随时间移动、尾部更新、吃食物增长、碰撞和结束逻辑。`ok=true`、审批或文件能够打开均不能证明已经还原原游戏。

入口不是只接受 Snake：Source Library 还有 Minesweeper、Connect Four、2048。导入原项目、播放原项目、调用传统适配器，与 LLM 完整重建是不同能力。当前没有足够证据宣称这些游戏都能自动转换；甚至 Snake 的真实模型端到端转换也尚未达到玩法验收标准。

### 产品展示层

当前 Project Session 的渲染器展示矩形棋盘；二维显示 Source Plane，三维显示并排的 `Z LAYER 0/1/2...`。这是规则状态预览，不是自动生成完整三维 Pygame/自由视角游戏。顶端 Transformed 3D 的传统适配演示也不能算本次 LLM 输出。

成功的自然语言 Lift 应产生：解析后的设计意图、转换计划、Target 的 Rule/Scene/Asset/Input 四种 IR、绑定它们的 Manifest。用户批准后，Project Session 应表现为要求的层数、键位、规则变化；规则不支持时应指出具体缺口。

例如“保留 20×20，增加三层，PageUp/PageDown 移动 Z，食物分布到各层”应能看见三层棋盘，并验证蛇头跨层、食物和碰撞行为。文本解释、READY 标签、JSON 文件存在都不够。

## 设计师现在如何先看到结果

现有 `.cubeengine_llm/round4_snake_target/project.manifest.json` 明确注明 `playable_overlay=snake_playable_fixture`，属于工程师手写规则示例，不能证明本轮 LLM 生成成功。

本次已离线加载该包并实际执行：

| 操作 | 蛇头位置 | 结果 |
|---|---|---|
| 右移 | `[6,10,0]` | accepted |
| PageUp | `[6,10,1]` | accepted |
| 再右移 | `[7,10,1]` | accepted |
| PageDown | `[7,10,0]` | accepted |

对应状态图见 `LLM_SPATIAL_LIFT_PREVIEW_20260922.png`。它是现有运行时的真实快照，绘制为便于阅读的三层图，而非 Workbench 截图或实时模型输出。

在当前 SRTP Workbench 导入 Snake，使用 ATTACH PROJECT MANIFEST 选择上面的完整包，即可先评审这种多层棋盘交互。不要点击 COMPILE 来覆盖示例，也不要把该演示计为模型生成验收。

## 工程师按三个关口推进

### 第一关：一个稳定可调用的服务

优先：验证 Model Studio 香港工作空间与正式额度，并接入供应商无关客户端。若账户不具备条件，先报告具体资格/计费阻断，再选择有明确准入的候选，不能默默转回免费 Gemini。

交付物：

1. 配置 provider、base_url、model、对应密钥变量，配置与实际请求一致。不同供应商使用各自凭据，不能把 OpenAI Key 发给第三方端点。
2. 预检只发一个很小的 JSON 请求，确认访问、认证、模型可用和格式支持，再发完整游戏请求。
3. 记录供应商、实际模型、时间、HTTP 状态、请求 ID、错误码、Retry-After、配额维度、输入/输出 token 和耗时。保护密钥；可选的出口诊断必须注明只是探测站观测，不能当作 Google 所见 IP。
4. 地区拒绝、无效凭据、无模型权限立即停止；临时服务忙有限退避；限流按具体额度/Retry-After 处理，日额度或零额度不能短周期重试。
5. 在设计师实际使用的同一网络和工作目录验证，不能只提交工程师电脑上的成功截图。记录模型版本、费用和调用次数。

完成标准：小请求与代表性大请求都通过；失败可准确定位。不得承诺任何免费/付费供应商永不繁忙。

### 第二关：先证明自然语言真的改变三维规则

用一份明确标注为基准、可运行的 Source IR 隔离 Source 重建问题，调用真实 LLM 做 Lift。基准不是最终真实源代码验收。

至少两个意图：三层与五层；结果中拓扑、输入、邻接、移动、食物与碰撞的对应变化必须能解释并实际测试。存在歧义先让设计师确认，不能像“6”那次继续执行。Z=1 时保持 XY 行为；Z>1 时新增动作确实改变 Z 坐标。

交付：完整包、请求/响应证据、操作步骤和屏幕录制。任何 fixture/人工补丁明确标注，不能覆盖后仍宣称纯模型成功。

### 第三关：真实游戏 Source → Lift 全链路

按产品设计师最新要求，本轮主示范改为二维井字棋 → 三维井字棋，同时建立 Connect Four、扫雷、2048、Snake 的验收矩阵。Snake 降为回归案例，不能再代表通用转换能力。具体入口、参考包来源及验收条件见 `LLM_MULTI_GAME_ACCEPTANCE.md`。合法空棋盘必须支持，不能沿用 Snake 的“必须预放身体/食物”门槛。

井字棋 Source 验收：合法空棋盘、双方交替落子、禁止重复占格、二维行列与对角线获胜、平局、终局后禁止落子、重置与确定性回放。当前 `srtp/examples/tictactoe_2d.py` 是静态规则样例，不是完整可玩应用；工程师需补齐或选定完整二维源游戏，不能将静态样例当作完整源项目验收。

井字棋 Target 验收：以上规则在 3×3×3 中仍成立，点击选择具体层与格子，49 条三维获胜线均正确，Z=1 时回归二维规则。其他游戏分别测试重力、邻域、数字合并、连续移动，不仅看 schema 是否通过。

生成流程应分阶段、启用供应商支持的结构化输出/Schema，限定每步职责并保存完整原始输出；局部失败局部修复。没有更改的 Source 不应每次重新生成，减少成本与失败机会。

## 已做与仍未完成

本地 `a0d8178` 已修复失败覆盖完整包、审批前文件一致性校验、按文件名绕过哈希匹配、失败 Lift 继承 compile_ready、服务繁忙有限退避；85 项离线测试通过、1 项联网测试跳过。这些属于局部工程修复，不代表服务可访问或完整游戏已转换。

本次深度诊断没有购买服务、修改 VPN、切换模型、发送带密钥的测试调用，未宣称真实 LLM Lift 已成功。当前优先级是第一关与第二关，不是继续美化 Console。

产品设计师需要评审“意图是否被正确理解、转换结果是否符合玩法”，供应商权限、网络、密钥、配额和可复现交付由工程师先打通。
