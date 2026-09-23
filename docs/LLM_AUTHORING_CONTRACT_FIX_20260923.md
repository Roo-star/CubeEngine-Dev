# 井字棋 Source 生成契约修复

现场：`.cubeengine_llm/tic.tac.toe/source.failed/5c08fca253294fb4b4ce631ce13386f4/report.json`。
本地 SRTP 修改；未调用 API、未改现场响应、未改成功产物。

## 原因

模型已返回响应，非配额、地区、连接或用户操作问题。第一次和第三次回复包含 document_id、revision、content_hash、ir_version、provenance、dependencies，被定义构建器整体拒绝；第二次已去除这些字段，但 actor 使用 `op:state.get` 被拒绝。

引擎 Prompt 要求只提供语义定义，同时发送的完整 IR schema 却将封装字段列为 required。表达式 schema 只规定 op 是字符串，未列出实际支持的操作；命令 schema 没有描述 target 等字段必须是表达式。修复反馈此前只保留最近一条错误，容易出现前一错误回归。

## 修改

- 生成专用 schema 排除引擎字段，磁盘 IR schema 和正式验证不放宽。
- 输入误带完整 IR 时，忽略根级封装字段，身份/哈希/依赖/来源记录由引擎重新建立。记录忽略字段；不接受模型伪造审批或依赖，不静默删除未知语义。
- 生成 schema 提供合法 AST 操作、`expr` 简写，以及命令 target/value/coordinate 等表达式字段。
- 明确 `state.get` 是函数调用，`var.NAME` 是局部绑定；不把局部变量悄悄解释为游戏状态。
- 布尔/空值简写支持 JSON 拼法 true/false/null；未知表达式带精确字段路径报错。
- 修复上下文保留本阶段历次不同错误；每次 trace 保留当次错误及原响应。

## 验证与边界

回归覆盖四阶段收到完整 IR 仍可通过确定性构建、伪造引擎字段无效、未知语义仍拒绝、非法 actor 不被放行、生成 schema 与实际约束一致。

原始三次响应离线重放后，封装字段错误消失，仍会拒绝前两次的 actor 表达式，以及第三次 `/actions/0/effects/0/target` 非表达式的问题。新格式说明有助于下一次生成/修复，但不是对下一次真实模型输出必然通过的保证。不能批准旧失败包，不能把该修复称为完整转换验收成功。
