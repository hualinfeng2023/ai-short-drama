# Apple UI 逐页补齐与验收

验收日期：2026-07-16

验收视口：桌面端 `1440 × 904`，移动端 `390 × 844`。

本轮覆盖此前未完整确认的 9 个页面。所有最终截图均来自修复后的本地运行页面；出现浏览器合成残留的截图未纳入验收证据。

## 1. AI 新建项目

健康度：通过。

- 修复移动端标题被模式切换器挤成单字换行的问题。
- 将模式切换器、示例、素材行和编辑器在窄屏改为自然纵向流。
- 保留真实创建流程、安全说明、素材权利确认和服务连接状态。
- 桌面证据：[修复后](after/01-new-project-desktop.png)
- 移动证据：[修复前](before/01-new-project-mobile.png) · [修复后](after/01-new-project-mobile.png)

## 2. 项目简报

健康度：通过，原阻断问题已修复。

- 旧版简报与旧版导演方案缺少数组字段时，不再触发 `Cannot read properties of undefined (reading 'map')`。
- 兼容旧数据中的 `platform_targets`、`shots`、`assumptions` 等可选字段。
- 新增旧版导演方案映射回归测试。
- 桌面证据：[修复前](before/02-brief-desktop.png) · [修复后](after/02-brief-desktop.png)
- 移动证据：[修复前](before/02-brief-mobile.png) · [修复后](after/02-brief-mobile.png)

## 3. 故事与剧本

健康度：通过。

- 确认标题、创作基准、故事方向入口和返回/刷新操作在桌面与手机端均完整。
- 修复事件连接历史回放引发的连接池耗尽后，深链接不再误报项目或服务不可用。
- 桌面证据：[修复后](after/03-story-desktop.png)
- 移动证据：[修复前](before/03-story-mobile.png) · [修复后](after/03-story-mobile.png)

## 4. 角色形象

健康度：通过。

- 已锁定角色不再展示确定性模拟服务生成的彩色条纹占位图。
- 改用当前项目的真实角色参考镜头，并明确标注“已锁定形象 · 项目参考镜头”。
- 收紧桌面候选卡片宽度，避免单个候选无限放大；手机端保持单列大图。
- 桌面证据：[修复前](before/04-characters-desktop.png) · [修复后](after/04-characters-desktop.png)
- 移动证据：[修复后](after/04-characters-mobile.png)

## 5. 前期资产

健康度：通过。

- 与角色页统一真实参考镜头和候选卡片密度。
- 增加加载失败后的明确回退路径，避免服务异常时一直显示转圈。
- 桌面证据：[修复前](before/05-preproduction-desktop.png) · [修复后](after/05-preproduction-desktop.png)
- 移动证据：[修复后](after/05-preproduction-mobile.png)

## 6. 动态分镜

健康度：通过。

- 将原先只有“查看任务”的空白状态补成完整阶段页面。
- 增加“检查前期资产”和“查看生成任务”两个明确下一步，并保留返回第 3 阶段入口。
- 增加读取失败状态，避免请求失败后无限转圈。
- 桌面证据：[修复前](before/06-storyboard-desktop.png) · [修复后](after/06-storyboard-desktop.png)
- 移动证据：[修复后](after/06-storyboard-mobile.png)

## 7. 正式制作与交付

健康度：通过。

- 确认音频、多轨时间线、质量检查和交付矩阵在两种视口下保持清晰层级。
- 增加数据读取失败的明确回退状态，避免无限转圈。
- 桌面证据：[修复后](after/07-production-desktop.png)
- 移动证据：[修复后](after/07-production-mobile.png)

## 8. 完整小样

健康度：通过。

- 确认播放器、时间线、局部修改、审批和导出区域在桌面与手机端都可继续滚动操作。
- 增加项目工作台不可用和无镜头时的明确空状态。
- 桌面证据：[修复前](before/08-preview-desktop.png) · [修复后](after/08-preview-desktop.png)
- 移动证据：[修复后](after/08-preview-mobile.png)

## 9. 审核中心

健康度：通过。

- 移除 CSS 渐变制作的“演示帧”。
- 优先显示真实候选画面，其次显示当前画面；只有媒体尚未生成时才显示文字空状态。
- 桌面证据：[修复前](before/09-reviews-desktop.png) · [修复后](after/09-reviews-desktop.png)
- 移动证据：[修复后](after/09-reviews-mobile.png)

## 跨页面稳定性修复

- 新 SSE 连接从当前最新事件序号开始，不再把全部历史事件逐条回放给刚打开的页面。
- 工作台和任务中心对刷新请求增加了并发保护，事件突发时不会重复占用数据库连接。
- 连续切换全部页面后：浏览器错误为 0，后端连接池超时为 0，HTTP 500 为 0。
- `/health/ready` 返回 HTTP 200，数据库、迁移、任务进程和媒体工具均为正常状态。

## 自动化验证

- 前端类型检查：通过。
- 前端单元测试：9 个测试文件、45 个测试全部通过。
- 前端生产构建：通过。
- 后端 Ruff：通过。
- 后端测试：107 个测试全部通过。
- 9 个手机页面均满足 `document.documentElement.scrollWidth === 390`，未发现横向溢出。

## 浏览器批注回归：场景工作台角色参考图

- 修复右侧“角色参考与造型”仍直接显示彩色条纹模拟资源的问题。
- 场景工作台现在按角色绑定关系，从项目镜头中优先选择近景或中近景的真实角色参考帧。
- 同一规则同步应用到“复核角色一致性”弹窗，不再存在第二条遗漏的占位图渲染路径。
- 当前场景证据：[连续性面板](../shot-workspace-reference-fix.png) · [角色复核弹窗](../shot-workspace-reference-modal-fix.png)
- 图片实测尺寸为 `1600 × 2848`，浏览器错误为 0。

## 浏览器批注回归：AI 故事生成动效

- 点击“AI 重构叙事”后保留当前原文，等待阶段依次说明人物冲突、情绪节奏和可拍摄叙事的处理状态。
- 结果返回后切换为真实写入进度，并在 `1100–2400ms` 内逐段显示新内容；完成后恢复编辑、保存和撤销能力。
- 动画只作用于故事字段，使用轻量流光边框、状态胶囊和单个呼吸图标，不对整页做装饰性动画。
- `prefers-reduced-motion: reduce` 下取消持续流光并直接显示完整结果。
- 真实流程证据：[构思阶段](../brief-ai-generation-thinking.png) · [逐段写入](../brief-ai-generation-writing.png) · [生成完成](../brief-ai-generation-complete.png)
- 实测过程：原文 `730` 字 → 写入阶段 `526` 字、`95%` → 完成 `556` 字；浏览器错误为 0。
- SQLite 改用无连接池模式，30 次、15 并发项目读取全部返回 HTTP 200，未再出现连接池耗尽。
