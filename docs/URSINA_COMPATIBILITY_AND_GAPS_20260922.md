# 旧包 Ursina 兼容修复及引擎侧缺口

2026-09-22，当前 SRTP 工作目录；未调用 LLM。

## 修复内容

旧 Scene 中有状态绑定，却没有 `variants` 外观字典。此前新增的严格检查直接阻止打开这些包。本次在 **ProjectHost 的查看路径** 加入有限、明确的旧语义显示协议，支持 empty / positive / negative / body / head / food；没有游戏名判断，没有更改 Rule IR，没有重写或重新批准原 Manifest。

- empty 是留有格间间隙的透明格子；positive / negative 使用立体叉和圆环；head / body / food 使用不同的标记、尺寸和配色。
- 显式的外观字典、文字、纹理等优先。未知状态仍报错，包括只会在未来出现的绑定状态。
- 这些是查看旧包的**兼容外观**。窗口明确显示 `Legacy appearance ... source visual fidelity not verified`；不能声称恢复了原游戏的素材或完整界面。
- 新编译仍使用严格 ScenePresentation，不允许以兼容字典代替模型/构建器应生成的外观定义。
- 添加层选择和重开按钮、悬停边框、透明外壳的深度写入处理；选择深度改变可点击层，不隐藏其余空间。重复刷新不重建未变的碰撞体。
- 修复 ProjectHost 鼠标/键盘/坐标操作混用的输入序号冲突，重开后沿用同一入口规则。

## 可用入口

重启当前 Workbench，选择完整二维井字棋源游戏，在 Project Session Core 点击 ATTACH PROJECT MANIFEST，选择现有 `artifacts/tictactoe_target/project.manifest.json`。然后切换 Transformed 3D，点击 PLAY。

此路径直接运行原来的参考包，**无需重新 Compile、Approve 或 Spatial Lift，也不会调用 LLM**。可看到透明三维棋盘和立体叉/圆环；用 Next / Previous 选择深度，All layers 恢复全部，鼠标点击落子、右键拖拽转动、滚轮缩放、Restart 重开。层选择用于避免外层遮挡内部点击。

这是旧包运行能力验证，不是新生成游戏、原作高保真还原或完整自动转换的验收。

## 已验证的范围

- 井字棋 Source、Target、Snake playable 三个现有包打开后外观诊断为空，所有 JSON 字节哈希保持不变。
- 当前已保存的 LLM Snake Target（20×20×5）通过兼容外观检查；没有因此宣称其移动、增长和碰撞规则正确。
- 实际 Ursina 几何渲染、透明格子、叉/圆环，使用原包，测试不再临时注入外观字典。
- 实际三维射线命中中间层中心格；通过 Input IR 落子、占位拒绝、重开、回放；渲染前后规则状态一致。
- 未使用可见原生窗口执行人工游玩验收。当前环境通过 Panda GraphicsBuffer 渲染真实 Ursina 组件，测试入口为 `python -m tests.ursina_scene_probe`；截图在 `.cubeengine_llm/backend_checks/scene_asset_backend.png`。

## 除 LLM 服务外仍未完成的工作

| 优先级 | 工作 | 产品通过条件 |
|---|---|---|
| P0 | 默认视觉还原 | 原素材、配色、字体、图集、HUD、层级、状态反馈可追踪；源图像或颜色改变后结果跟随改变 |
| P0 | 表达与执行覆盖 | 所需几何、材质、时钟、随机、实体和输入能力实际可执行；不支持项在编译阶段明确报告 |
| P0 | 独立行为/视觉基准 | 从原游戏采集输入轨迹与截图；Z=1 保持规则/外观；不能仅用生成器自己的断言证明正确 |
| P1 | 三维交互完成度 | 可靠内部选择、相机重置、拖拽与点击区分、终局反馈、AI 对手运行接入，与手写标准对照 |
| P1 | Workbench 任务可靠性 | 异步、取消、重复点击隔离、失败恢复与成果版本一致；不让用户手工猜包或拼包 |
| P1 | 可移植包与性能 | 原资源可随包转移，换路径能打开；大棋盘渲染/输入/规则有可重复的性能指标 |

完整动画、音效和特定几何配方目前不齐；这些工程缺口不能归因于 Gemini。当前 Codex 负责继续实现，不需要用户转派其他工程师。多游戏真实生成的最终验收仍需要可用模型，但基准、后端和任务系统可以先完成。
