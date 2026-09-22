# Workbench / LLM 待办

更新：2026-09-22。开发目录：`E:/CubeEngine/CubeEngine-SRTP`，开发分支：`SRTP`。
`llm` 是工程师交付分支，只拉取和测试；本地修复及更新均进入 `SRTP`。

## P0：阻断当前测试

- [x] 修复失败的 Source 重编译覆盖旧 IR、保留旧 Manifest，导致 Approve 报 missing pinned rule_ir。失败输出隔离，成功包完整替换并保留旧包，审批前校验文件版本；离线回归通过。
- [x] 修复 bundle loader 按文件名兜底绕过 Manifest 哈希匹配，禁止把损坏的包继续送入 Spatial Lift。
- [x] Gemini 繁忙自动进行有上限的退避重试，默认共 3 次、间隔 2/4 秒；耗尽后停止，服务故障不会再进入模型 JSON 修复循环。
- [ ] 服务持续繁忙时验证可用备用模型/服务。目前本地仅配置 Gemini，未配置 Groq；没有自动切换的备用凭据。重试不能保证服务恢复。
- [x] 修复失败 Lift 继承 Source 的 compile_ready=true，避免错误报告把 Source 的状态当成 Target 成功。
- [x] 从现场快照恢复完整真实 Source 草稿到当前工作目录，保留失败现场；恢复结果仍待使用者批准，未代替用户 Approve。
- [ ] 在当前 SRTP 上完成 Source → Approve → Lift → Approve 的实际验收。离线测试不代表 Gemini 服务恢复或三维玩法成功。

## P1：功能完整性

- [ ] 实际 Source 贪吃蛇连续移动、尾部更新、食物、增长、碰撞、计分和结束状态验证。
- [ ] 三维目标确实实现 Z 轴操作与三维规则，而非手写 fixture 替代模型输出。
- [ ] Design Intent 存在歧义、未解决项或待确认项时阻止继续 Lift。
- [ ] 中文输入/显示与多行需求编辑。

## P2：界面与诊断体验

- [ ] Console 分类显示服务繁忙、连接故障、配置错误、规则校验错误，自动展开诊断。暂缓，已有 Diagnostics 可查，不优先于阻断修复。
- [ ] 编译过程进度、取消、重复点击保护。
- [ ] Source Plane 的视野、图例、控件提示与输入方式说明。

## 当前证据与集成范围

- 已切换到 `SRTP`；原有 Python 环境配置已恢复，并保留切分支前的 stash。
- 已用正式 Git 合并 `5f7e637` 将 `origin/llm` 的 `1feefe6cd3de795511e2075f54767cade750a26d` 整合进 `SRTP`。两条分支最初历史无共同祖先，但框架快照 `10ea353` 与 `9197133` 内容完全一致；本次合并保留两边历史，此后普通 merge 即可。手工复制的临时集成方式已取消。
- 15:07 Source 失败：模型 JSON 截断；四份 IR 与旧 Manifest 的哈希均不匹配。现场保存在历史测试目录的 `manual_test_records/approve-mismatch-20260922-150714`。
- 之前 Lift 失败：Gemini 返回 high demand。这与上述文件损坏是两个独立原因。

## 固定工作方式

只使用 `E:/CubeEngine/CubeEngine-SRTP`，日常保持 `SRTP`。更新前先提交本地开发改动；然后 `git fetch origin`、`git merge origin/llm`，在同一目录测试、继续修改。需要发布本地更新时推送 `origin/SRTP`。`main` 暂时不用，工程师的 `llm` 分支不承载本地修复。

旧 `work/llm-manual-1feefe6` 仅保留历史证据，不再作为启动入口；不再创建逐提交测试目录。
