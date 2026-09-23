# Spatial Lift actor 修复

开发位置：E:/CubeEngine/CubeEngine-SRTP，分支 SRTP。
现场：target.failed/f80f45065a3b434d9bde737ccc1bac16。

## 原因

模型添加 move_z_up/move_z_down 时遗漏 actor 和 timing。源规则包含四个玩家输入动作，以及系统 advance 动作；旧继承函数要求全部动作使用相同 actor/timing，因此无法继承。actor 缺失导致 AST 校验失败。

发给 Lift 的源动作摘要只有 ID/name/effect_ops，省略 actor/timing 和实际 effects。另一个缺陷是普通补丁校验失败未进入修复循环，只有缺失 proposal 或空 effects 等部分问题会重试。

## 修复

- 新动作由 Input intent 关联时，从已有 Input 关联动作中取得一致的 actor/timing；不从动作名称猜测，不覆盖显式值。无法取得一致值时交给模型修复。
- 继承仅作用于完整动作补丁，不再修改动作字段内的表达式补丁。
- Source 摘要提供 actor、timing、parameters、precondition、真实 effects、participants、flow 和 Input intents。
- 普通校验错误也反馈给模型，在 max_repairs 范围内重试；修复提示针对实际错误，不再一概当作空 effects，也不要求把移动强改为固定坐标。
- 现场模型还生成了 grid.set + delta 的残缺效果。增加基本必需字段检查，要求 state/topology/coordinate/value，避免补完 actor 后把这种动作发布为成功。

## 验证及边界

- 现场 proposal 经修复后的继承逻辑，两个新增动作正确取得 human.player 和 phase.input，四 IR 补丁校验通过。
- 用原保存响应离线重放真实 Lift 编译路径：一次意图解析加三次 Lift，后两次提示均含缺失 grid.set 字段的具体路径；重复残缺输出最终失败，没有发布 Target。
- 相关 93 项测试：92 通过，1 跳过真实 API 测试。新增覆盖玩家/系统区分、歧义拒绝推断、显式 actor 与字段表达式保留、摘要内容和残缺效果拦截。
- 未调用真实模型，未修改 .env，未修改旧失败包，未替使用者 Approve。没有宣称三维游戏已生成成功。

## 使用者重测

关闭 Workbench，从当前目录 Run SRTP Workbench.bat 重启。保留已批准的 Source，选中同一 Snake，填 z=5，直接 Run Spatial Lift。若界面重启未保留 Source 附件，通过 ATTACH PROJECT MANIFEST 选择 `.cubeengine_llm/snake.game.with.python.and.pygame/source/project.manifest.json`，无需重新生成 Source。成功后再验收 Target；失败则查看新一轮 Diagnostics。
