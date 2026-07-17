# 测试与验收证据

## 角色关系网 PR-5 全量验收

日期：2026-07-16  
结论：角色关系从故事结构草稿、人工编辑与锁定、批准后生成剧本，到版本 Diff、影响确认、修改版和下游过期门禁形成完整闭环。

### 自动化门禁

```bash
cd server
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest -q

cd ..
npm run lint
npm run typecheck
npm test -- --run
npm run build
```

结果：

- Pytest：107 项通过，0 失败；关系专项覆盖未知角色、自环、重复角色对、核心角色孤立、缺少主冲突、关系节拍连续性、揭示前秘密泄露、乐观锁、关系锁、批准只读、修订确认哈希、重复修改版、Diff 分级、Change Set 和剧本过期门禁。
- Vitest：10 个测试文件、47 项测试通过，0 失败；覆盖关系工作区映射、关系版本来源、草稿序列化、轮询时本地修改保护、版本冲突和修订影响合同。
- TypeScript、Ruff 与 Vite production build：通过。
- Alembic：全新隔离数据库从 `0001_read_only_baseline` 顺序升级至 `0021_rel_revision_sets (head)`，模型与迁移无漂移。

### 浏览器核心闭环

在隔离 FastAPI/Vite 服务和专用 SQLite 数据库上验证：

1. 已批准版本只读，并显示“创建修改版”；
2. 影响分析定位到受影响集数、具体场景、需重生成资产、保留资产和预计成本；
3. 确认后自动切换到新草稿，旧批准版本仍可读取；
4. 草稿编辑在轮询期间不被覆盖，保存并刷新后持久化；
5. 关系锁定后语义字段不可编辑，解除锁定前显示影响提示；
6. 剧本场景可回跳到对应关系变化；
7. 版本比较把真实关系变化归类为 P1，并显示中文字段名称；
8. 存在未批准关系修改版时，页面显示旧剧本过期提示并禁用剧本批准；
9. 窄屏布局仍能读取版本状态、切换列表视图和完成核心关系检查；
10. 页面控制台无应用错误，错误和动作提示使用自然简体中文。

浏览器截图保存在 Codex 验收证据目录，隔离服务在验收后关闭。

## Workflow v2 增量验收

日期：2026-07-15  
结论：Brief v2 至多轨成片、G5 审批和多平台/多语言交付矩阵的主链路已实现，并通过自动化、迁移和真实浏览器路由验证。

### 自动化门禁

```bash
cd server
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest -q
.venv/bin/alembic current
.venv/bin/alembic check

cd ..
npm run lint
npm run typecheck
npm test -- --run
npm run build
```

结果：

- Ruff：全部 Python 文件通过检查与格式校验。
- Pytest：66 项通过，0 失败；其中完整链路覆盖 G1–G5、项目关联数据级联删除、45/60/90 秒 Brief 与方案/剧本时长合同、故事方向决策维度和提问式续作钩子拒绝、Brief 智能命名、Doubao Seed 叙事重构与新增事实拒绝、“必须满足”智能代写、“必须避免”智能建议、长耗时文本任务阶段进度及本地回退、正式媒体、Seedream 原始 URL 快速路径选择与到期边界、音频 Cue、口型降级、多轨 Timeline、8 项整片 QC、2 个 Profile × 2 种语言的 4 个 Delivery，以及 28 个导出制品登记。
- Vitest：6 个测试文件、27 项测试通过，0 失败。
- TypeScript lint/typecheck 与 Vite production build：通过。
- Alembic：实际本地库与全新测试库均到达 `0018_export_profiles_v2 (head)`；`alembic check` 返回 `No new upgrade operations detected.`。

### 端到端验收断言

- Brief v2 可保存主市场、目标用户、平台、语言及优先级，并生成 3 个差异化故事方向。
- 项目库支持经二次确认删除非当前项目，并级联清理全部持久化关联记录；当前工作区项目的删除入口保持禁用。
- Story Direction v2 强制提供观众匹配、视觉抓手、选择代价、关键转折、风险和四段式续作剧情铺垫；问号、观众提问与评论区 CTA 无法通过生成合同。
- 新建项目未手填名称时会基于 Brief 自动命名；Brief 编辑页可请求 AI 候选名称、撤销并在人工确认后保存新版本。
- “故事想法”可把当前完整 Brief 发送给 Doubao Seed 重构叙事；响应必须通过逻辑检查和零新增事实约束，结果支持撤销且不会自动持久化，Provider 不可用时明确失败。
- Brief 的主时长与各平台目标支持 45、60、90 秒；90 秒可保存为新版本，并能完整拆分为无时长缺口的故事方向、分集大纲与结构化剧本。
- “必须满足”可基于当前尚未保存的 Brief 智能代写 3–6 条具体要求；结果只增量追加并去重，支持撤销，只有用户保存新版本后才写入项目事实源。
- “必须避免”可基于当前尚未保存的 Brief 智能建议 3–6 条具体、可核验的规避项；覆盖版权、连续性、无铺垫反转、画幅和节奏风险，结果支持增量去重、撤销与显式本地回退，不自动保存。
- Story DNA、Story Bible、Episode Outline、Script 均为不可变版本，支持结构化编辑和批准。
- 角色候选、Look、Voice、Location、Prop 与 Visual Bible 有稳定 ID、引用资产和 G3 门禁。
- Script 动态拆解为 Shot Spec、Storyboard、Animatic 和 Workflow DAG，并通过 G4。
- 关键帧和视频走统一 Generation Record、QC、Review 合同；本地图片无法被视频 Provider 访问时明确记录降级，不伪装成功。
- Seedream 原始 URL 仅在默认 10 分钟且截止时间明确的窗口内直传 Seedance；到期、时间戳缺失或异常后，普通批准关键帧以 Private ACL 上传 TOS，使用 2 小时预签名 URL 调用 Seedance，并在结束后删除。TOS 签名 URL 不进入 Job 或 Generation Record；配置不完整时 readiness 返回 503。
- Dialogue、VoiceOver、BGM、Ambience、SFX 生成独立 Cue/Take；Lip Sync 仅消费已批准对白与视频，失败时保留原视频并显式回退为 Voice-over。
- Timeline 包含 VIDEO、DIALOGUE、BGM、AMBIENCE、SFX、SUBTITLE 六轨；G5 要求 8 项整片 QC 全部通过。
- Delivery 先执行权利预检，再生成 Profile × Language 矩阵；不同语言字幕独立，画面母版、音频分轨和 QC 报告复用且可追溯；不会自动发布到外部平台。

### 浏览器门禁

在本地 FastAPI/Vite 开发服务上验证工作流页面与 Brief 编辑页：

1. 打开并刷新 G3 `/projects/{id}/preproduction`，旧 Seed 数据安全呈现，缺少 Look 时 G3 按钮保持禁用。
2. 打开 G4 `/projects/{id}/storyboard`，未生成数据时显示明确空状态。
3. 打开并刷新 G5 `/projects/{id}/production`，音频、Timeline、QC 与 Delivery 区域均能恢复为空状态。
4. 三个页面的面包屑和工作台跳转正确；浏览器 `errors` 为空，console 只有 Vite 连接和 React 开发提示。
5. 在 `http://127.0.0.1:5173/projects/{id}` 检查“必须满足”的 AI 智能代写入口；Standard 与 Cinema 模式下按钮、字段和提示区域对齐，语义标签可访问，浏览器 `errors` 为空。验收未点击生成按钮，未触发真实方舟计费调用。
6. 同一 Brief 编辑页已将字段明确标为“短剧名称”，并提供“AI 重构叙事”入口；Standard 与 Cinema 模式布局、语义标签和对比度正常。OpenAPI 已登记 `/story-rewrites`，浏览器验收未点击按钮，未触发真实 Doubao Seed 计费调用。
7. 目标时长下拉包含 45、60、90 秒；在可编辑 Draft 中选择 90 秒后“保存新版本”启用且“生成 3 个故事方向”保持门禁，刷新/关闭前未保存，接口确认项目仍为 60 秒。已进入 `SCRIPT_READY` 的项目按版本规则只读，但仍正确展示 90 秒选项。
8. “必须避免”字段提供“AI 智能建议”按钮；可编辑 Draft 中按钮启用，`SCRIPT_READY` 项目中与字段一致保持禁用。按钮、字段标签和焦点语义可访问，1055×998 视口布局正常，浏览器 `errors` 为空。验收未点击按钮，未触发真实方舟计费调用。
9. 视觉风格根据当前故事想法与题材动态推荐；“末日 + 重生 + 神秘药丸”的奇幻故事正确推荐“奇幻史诗”。字段标题显示“建议”tag，下拉对应选项显示“奇幻史诗 · 建议”，且不会覆盖已保存的“写实电影感”。1055×998 桌面视口布局正常，语义标签完整，浏览器 `errors` 为空。
10. 任务列表不再把固定 `estimated_seconds` 显示成含义不明的 `15s`；运行中任务分别显示每秒变化的“已用时”和“参考预估”，同时展示百分比、阶段说明与流动进度条。服务端长耗时文本调用每 2 秒持久化阶段进度并触发 SSE；取消按钮在 1055×998 深色模式下与耗时信息和卡片边缘保持独立间距。验收使用浏览器模拟响应，未创建真实任务或触发模型计费。
11. 成功任务按产物类型展示上下文 CTA；“3 个故事方向”任务显示“查看 3 个方向”，点击后进入 `/projects/{id}/story`，页面确认展示 3 个方向及比较/批准能力。Story Package、角色资产、分镜和制作结果也映射到对应工作区；顶部“打开当前工作区”复用同一映射。1055×998 布局正常，浏览器 `errors` 为空。

截图：`/private/tmp/ai-short-drama-v2-production.png`、`/tmp/brief-requirements-ai.png`、`/tmp/brief-requirements-ai-cinema.png`、`/tmp/brief-story-rewrite.png`、`/tmp/brief-story-rewrite-cinema.png`、`/tmp/brief-duration-90.png`、`/private/tmp/brief-avoidances-ai.png`、`/private/tmp/brief-avoidances-ai-dark.png`、`/private/tmp/brief-visual-style-recommendation.png`、`/private/tmp/tasks-running-progress-fix.png`、`/private/tmp/tasks-running-progress-fix-cinema.png`、`/private/tmp/tasks-completed-cta.png`。

### 已知且显式保留的边界

- TTS、Lip Sync、Music、SFX 当前使用 Adapter-first 的确定性 Mock；真实供应商仍需单独选型和合同测试。
- Seedance 源图已支持私有 TOS 与短期预签名 URL；真实环境仍需提供 TOS 凭证、Private Bucket 和最小权限策略。`PROVIDER_MEDIA_STAGING_V1` 未启用时，系统只允许显式阻断或静态运镜降级。
- 自动发布、多租户、在线协作、生产级内容合规与法律判断不在本轮范围。

## Run-first MVP 基线验收

日期：2026-07-13  
结论：Run-first MVP 的自动化、迁移、容器、稳定性和真实浏览器门禁全部通过。

## 自动化门禁

```bash
npm run lint
npm run typecheck
npm test -- --run
npm run build

cd server
.venv/bin/ruff check .
.venv/bin/ruff format --check .
UV_CACHE_DIR=/private/tmp/ai-short-drama-uv-cache .venv/bin/pytest -q
```

结果：

- Ruff：57 个 Python 文件通过检查与格式校验。
- Pytest：26 项通过，0 失败。
- Vitest：2 个测试文件、7 项测试通过，0 失败。
- TypeScript lint/typecheck：通过。
- Vite production build：通过；生成 `dist/index.html`、CSS 和 JS 资产。

覆盖项目/Brief 幂等与乐观锁、迁移、任务领取/心跳/恢复/取消/重试、角色与 Storyboard、Hero Shot 注入失败与可逆降级、真实媒体、Range、Revision 不变哈希、比较/回滚、权利与积分、Export，以及上传大小、magic、路径穿越、压缩包攻击和解析边界。

## 数据库迁移门禁

在全新临时 SQLite 数据库执行：

```bash
.venv/bin/alembic upgrade head
.venv/bin/alembic check
```

结果：从 `0001_read_only_baseline` 顺序升级至 `0005_reference_asset_metadata`；`alembic check` 返回 `No new upgrade operations detected.`，模型与迁移无漂移。

## Docker 与稳定性门禁

```bash
docker compose --progress plain build
HOST_PORT=8030 docker compose -p ai-short-drama-final up -d
docker compose -p ai-short-drama-final exec -T app id -u
docker compose -p ai-short-drama-final exec -T app \
  .venv/bin/python -m app.smoke --base-url http://127.0.0.1:8000 --runs 20
```

结果：

- 最新镜像从锁文件安装 31 个生产依赖并成功构建前端、后端和 FFmpeg 运行时。
- 容器健康检查为 `healthy`；`/health/ready` 的数据库、迁移、媒体工具和 worker 全部为 `ok`。
- 前端 `/` 返回 200，`/meta/config` 正确声明 Mock、持久任务、参考素材上传与媒体流水线能力。
- 容器用户 UID 为 `10001`，不是 root。
- 20/20 完整产品闭环通过；相同 Mock 输入在全部运行中产生一致的 MP4/SRT/VTT SHA-256。

Smoke 从空 Project 输入开始，上传并回链参考素材，依次完成 Proposal、Approve、Character Lock、Storyboard、Hero Shot 失败降级、Preview、Revision、Compare、Approve、Rights Block 与 Export；`ffprobe` 校验 H.264/AAC、720p、24fps 和 60 秒时长。

## 浏览器门禁

在桌面 Chrome 隔离会话中，以容器前端 `http://127.0.0.1:8030` 完成：

1. 输入故事并选择 `README.md`，未确认素材权利时阻止提交；确认后上传成功。
2. 同源 API 核验素材持久化为 `REFERENCE_TEXT`，原文件名为 `README.md`，权利状态为 `USER_CONFIRMED`。
3. 生成导演方案、确认假设、批准 Story、选择并锁定角色。
4. 生成 3 场、8 镜头、60 秒真实 H.264/AAC Preview；页面媒体控件与时间线正常。
5. 执行局部 Revision，得到 Timeline V2；打开 V1/V2 比较并保留 V2。
6. 批准 Timeline，确认导出风险提示，生成 MP4、SRT、VTT、manifest 四件套，积分扣减 10。
7. 刷新 Preview 路由后，Timeline V2、批准状态、比较入口和四个下载链接全部恢复。

浏览器项目 ID：`56252e02-47f1-4a60-a6d5-8175795ad5c8`。最终 `errors` 与 `console` 均为空。截图：`/private/tmp/ai-short-drama-final-preview.png`。
