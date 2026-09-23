# 真实生成的井字棋打开为空白：诊断与修复

后续纠正：三个横向平面本身属于转换失败。现已继续修复引擎的整体体积布局，见 [体积布局契约](VOLUME_LAYOUT_CONTRACT_20260923.md)。下文是空白修复当时的历史记录。

开发位置：`E:/CubeEngine/CubeEngine-SRTP`，分支 `SRTP`。本轮付费 API 请求 **0**。

## 原因

用户实际生成的 Target Manifest 内容哈希为 `3da4ef982f53ee957815170155c7e321e30d991ecf76cd1392b8e3ec7a0b41d8`。该包包含 27 个可选格子；不是生成了空包。

Scene 把相机放在 `(0,0,14)`，旋转为 `(0,0,0)`。Ursina 零旋转朝 **+Z**，棋盘却位于 Z=0 至 -2.5，全部在镜头后面。生成契约此前没有明确正向约定，播放器也没有做初始可见性检查。普通 IR 结构验证通过不等于相机能看见内容。

额外复现两个执行端缺陷：符号画在不透明格子内部，导致落子后看不见；切换深度时 Ursina 的 `collider=None` 不移除旧碰撞节点，必须显式清理。

## 已修改

- 通用 `ProjectCameraRig` 计算实际游戏几何边界，检查真实镜头投影、近远裁剪及取景范围；保留有效的原视角，对背向/裁剪棋盘的视角自动取景并显示调整提示。使用实际游戏节点而非装饰背景计算轨道中心，Reset view 同步恢复相机平滑状态。
- 面向镜头的 X/O 图形移至格子的朝向镜头表面，旋转时更新，避免被格子本体遮挡。三维球形标记保持原本语义。
- 深度选择显式清理旧碰撞节点；播放器工具栏跟随窗口宽度布局，不再裁掉左右控件。
- 模型契约明确 +Z 相机正向、正交尺寸、字体 scale 的单位，以及 index_to_world 同时影响几何与位置（剪切矩阵会让格子变形）。这些修复属于引擎，未修改用户 Manifest 或任何已生成 IR。

## 验证证据

- 使用用户原包启动真实 Ursina 原生窗口：修复前重现空白，修复后显示棋盘。图像分别位于 `.cubeengine_llm/backend_checks/generated_target_before.png` 与 `generated_target_after.png`。
- 原包的不可变测试快照在 `tests/fixtures/generated_tictactoe_target_20260923`，附 origin 哈希与来源说明；不是手写成功样例。资源随包复制，无 API Key。
- `python -m tests.ursina_generated_board_probe`：确认原相机不可见；修复后在视野内；有效相机不被改变；27 个格子在选择其深度后均能被真实射线逐个选中；实际渲染像素检测到蓝 X、橙 O；空间对角线获胜、回放、R 重开、相机复位通过。测试前后 Manifest 哈希相同。
- 完整离线回归 444 项：443 通过、0 失败/错误、1 项付费 API 跳过；四个真实 Ursina/Panda 渲染探针通过，包括本次实际生成包。原始记录：`docs/LLM_OFFLINE_ACCEPTANCE_20260923.json`。

## 用户复验

关闭原来的 Ursina 游戏窗口，保留当前 Target 选择，在 Workbench 的 **Transformed 3D → Play** 重新打开。不需要重新 Compile、Run Spatial Lift 或 Approve。若当前选择丢失，重新附加原 `.cubeengine_llm/tic.tac.toe/target/project.manifest.json`。

## 质量边界

本次解决“空白窗口及看不见落子”。该模型输出仍采用横向错开的三层布局，且剪切使相邻格子边界不清、HUD 字号过小；这不等于达到目标中的完整立体棋盘与源外观保真质量。没有为制造漂亮验收图而手工替换模型场景。此类布局/可读性问题需要继续完善生成约束和视觉验收，不计为已完成。
