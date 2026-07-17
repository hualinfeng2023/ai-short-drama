# 剧创 AI · 可运行 MVP

根据 `AI_Short_Drama_PRD_MVP_v1.0.docx` 与 `AI_Short_Drama_MVP_Build_Plan_v1.0.docx` 实现的本地单用户短剧生产工作台。默认使用确定性 Mock Provider，不配置外部密钥也能走通完整产品闭环，并输出真实可播放的 H.264/AAC MP4。

## 一键运行

需要 Docker Desktop。首次启动：

```bash
cp .env.example .env
docker compose up --build
```

打开 `http://127.0.0.1:8000`。数据保存在 Docker volume `ai_short_drama_data`，重启容器不会丢失。

停止服务：

```bash
docker compose down
```

`.env` 中的 `ARK_API_KEY` 默认留空。此时图片、角色候选和 Storyboard 均由确定性 Mock 生成；FFmpeg 仍会生成 720p、24fps、60 秒的真实 Preview。配置 `ARK_API_KEY` 后，可选用火山方舟 Seedream 镜头图片和 Seedance 单镜头视频，密钥只由服务端读取。

## 已实现闭环

1. 从一句话创建 Project；可流式上传 TXT/MD/PDF/DOCX/PNG/JPG/WebP/MP4 参考素材并写入不可变 Brief Version。
2. 持久化任务生成三幕八镜、总长 60 秒的导演方案，确认假设后批准。
3. 生成两个真实 PNG 角色候选并锁定角色。
4. 创建三场八镜 Storyboard、当前 Take 和完整 Timeline。
5. FFmpeg 生成 720×1280 或 1280×720 的 H.264/AAC Preview，以及 SRT、WebVTT、Manifest。
6. 先分析 Revision 影响，再生成不可变 Timeline vN+1；支持版本比较、批准与回滚。
7. 权利确认和积分预留通过后，导出 MP4、SRT、VTT、JSON Manifest。
8. Project、Brief、任务、事件、资产、Take、Timeline、Change Set、审批、审计与积分账本均写入 SQLite。

Workflow v2 在上述兼容闭环上新增：多目标 Brief、3 个故事方向、Story DNA/Bible/Outline/Script、多角色与视觉圣经、动态 Storyboard/Animatic、图片和视频 QC、Dialogue/BGM/Ambience/SFX、Lip Sync 显式降级、六轨 Timeline、8 项整片 QC、G1–G5，以及多 Profile × 多语言 Delivery Matrix。音频类 Provider 当前使用确定性 Mock 适配器；真实供应商选型和视频源图媒体暂存按 `docs/ADR-0002-production-workflow-v2.md` 管理。

Brief 编辑页的“必须满足”和“必须避免”均可基于当前未保存内容请求 AI 建议；结果只在浏览器中增量追加并去重，支持撤销，只有用户明确保存新版本后才进入项目事实源。

视觉风格会根据故事想法中的叙事信号与题材动态标记推荐项；字段标题展示“建议”tag，下拉列表中的对应风格同时显示“· 建议”，推荐不会自动覆盖用户当前选择。

任务中心将“已用时”和“参考预估”分开展示：运行中计时每秒更新，长耗时文本模型调用每 2 秒持久化并通过 SSE 推送阶段进度；活动进度条保持流动反馈，取消与重试操作保留独立安全间距。

成功任务会在行内提供与产物匹配的下一步 CTA，例如“查看 3 个方向”“查看创作包”“查看分镜”；任务页顶部“打开当前工作区”也会跟随最新成功产物进入正确工作区。

任务使用幂等键、租约、心跳、重试、取消和进程重启恢复；SSE 会把持久化进度推送到任务中心与工作台。失败或取消 Revision/Export 时，实体状态和积分预留会安全恢复。

## 本地开发

需要 Node.js 22.22+、Python 3.12、uv、FFmpeg/ffprobe 和可用的 CJK 字体。

后端：

```bash
cd server
uv sync --frozen
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

前端另开终端：

```bash
npm ci
npm run dev
```

Vite 会把 `/api`、`/health`、`/meta` 代理到 `http://127.0.0.1:8000`。前端开发地址为 `http://127.0.0.1:5173`。

## 健康检查与验证

- 存活：`GET /health/live`
- 就绪：`GET /health/ready`
- 运行能力：`GET /meta/config`
- OpenAPI：`GET /docs`

前端验证：

```bash
npm run lint
npm run typecheck
npm run test
npm run build
```

后端验证：

```bash
cd server
UV_CACHE_DIR=/tmp/ai-short-drama-uv-cache uv run ruff check .
UV_CACHE_DIR=/tmp/ai-short-drama-uv-cache uv run pytest -q
UV_CACHE_DIR=/tmp/ai-short-drama-uv-cache uv run alembic check
```

对已启动的服务执行完整 API/媒体闭环；`--runs 2` 还会验证相同 Mock 输入产生相同 MP4/SRT/VTT 哈希：

```bash
cd server
uv run python -m app.smoke --base-url http://127.0.0.1:8000 --runs 2
```

容器内也可直接执行：

```bash
docker compose exec -T app python -m app.smoke --runs 2
```

## 主要接口

- `POST /api/v1/projects`：创建 Draft 与 Brief v1。
- `POST /api/v1/projects/{id}/assets`：带权利确认、大小/文件头/安全解析的参考素材上传。
- `POST /api/v1/projects/{id}/director-proposals`：生成持久化导演方案任务。
- `POST /api/v1/projects/{id}/director-proposals/{version}/approve`：批准方案并进入角色候选。
- `POST /api/v1/projects/{id}/characters/{character_id}/lock`：锁定角色并启动 Storyboard/Preview。
- `GET /api/v1/projects/{id}/previews`：读取 Timeline 历史与媒体资产。
- `GET /api/v1/projects/{id}/preproduction`：读取角色、Look、Voice、Location、Prop、Visual Bible 与 G3 状态。
- `GET /api/v1/projects/{id}/storyboard-workspace`：读取动态 Shot Spec、Animatic、Workflow DAG 与 G4 状态。
- `GET /api/v1/projects/{id}/audio-workspace`：读取 Audio Cue/Take、Lip Sync 与显式降级状态。
- `GET /api/v1/projects/{id}/timeline-workspace`：读取六轨 Timeline、整片 QC、G5 与交付状态。
- `POST /api/v1/projects/{id}/export-profiles`、`/exports/matrix`：创建导出规格与 Profile × Language 交付矩阵。
- `POST /api/v1/projects/{id}/revision-impact`、`/revisions`：影响分析和局部修改。
- `POST /api/v1/previews/{id}/approve`、`/rollback`：批准或回滚 Preview。
- `POST /api/v1/projects/{id}/exports`：执行权利门控后的导出。
- `POST /api/v1/shots/{id}/prompt-enhance`：结合镜头上下文智能改写画面描述；优先调用方舟文本模型，不可用时返回本地结构化增强结果。
- `GET /api/v1/assets/{id}/content`：支持 HTTP Range 的媒体读取。
- `GET /api/v1/projects/{id}/events`：支持断线续传的 SSE 事件流。

所有创建型写接口要求 `Idempotency-Key`；同一键对应不同载荷返回 `409 IDEMPOTENCY_CONFLICT`，过期实体版本返回 `409 VERSION_CONFLICT`。

项目首次创建时，若用户未手填短剧名称，服务端会根据故事想法、题材、风格、独立叙事定位和市场生成名称；Brief 编辑页也提供可撤销的 AI 一键智能修改。方舟文本服务不可用时采用明确标注的本地命名回退，AI 候选名称只有在用户保存 Brief 新版本后才成为项目事实。

“故事想法”支持调用 Doubao Seed 文本模型重构叙事。请求会携带当前尚未保存的完整 Brief，提示词禁止新增或替换人物、关系、能力、道具、地点、时间线、冲突、选择、结局及世界规则，并要求模型返回逻辑检查和空的新增事实清单；不合格响应会被拒绝。重构结果只进入可撤销草稿，不提供伪装成 Seed 的本地回退，人工保存新版本后才持久化。

Brief 的“必须满足”字段支持 AI 智能代写：系统读取当前表单中的故事、题材、风格、时长、画幅、叙事主角、目标受众、情绪回报、市场、平台、语言和已有约束，返回 3–6 条可执行要求，并以增量去重方式加入草稿。用户可一键撤销；方舟不可用时会明确提示本地回退，内容仍需人工确认并保存新版本后才持久化。

Brief 目标时长支持 45、60 和 90 秒；所选时长会同步进入主/次平台目标、故事方向、分集大纲和结构化剧本合同。

Brief v3 将叙事定位拆成互不推断的项目字段：`narrative_protagonist`（男性／女性／双主角／群像）、`target_audience`（男频／女频／泛人群）和 `emotional_rewards`（爱情／身份／事业／复仇／亲情／权力／公共使命）。年龄、性别、兴趣等细分信息只进入可选的 `audience_profile`；例如“25—40岁女性”可以是某个项目画像，但不是系统默认世界观。新项目在用户确认叙事主角和至少一种情绪回报前不能生成故事方向。

生成提示词和结构化输出都携带同一份独立叙事定位合同，定位字段由服务端按项目简报锁定，不能被模型改写。系统禁止根据男性主角自动套用战神、赘婿或后宫模板，也禁止根据女性受众自动生成大女主；模型引入未被用户明确要求的上述模板时，任务会失败并返回真实诊断。

首批选题池配比按内容形态配置，仅用于多项目组合规划，不改写单项目：真人仿真短剧为女频 50%、泛人群 30%、男频 20%；AI 漫剧与高概念奇幻为男频 50%、泛人群 30%、女频 20%。

## 可选真实 Provider

图片默认端点为 `https://ark.cn-beijing.volces.com/api/v3/images/generations`，模型为 `doubao-seedream-5-0-260128`。智能改写默认使用 Responses API 和 `doubao-seed-2-0-lite-260215`。视频默认端点为 `https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks`，模型为 `doubao-seedance-1-5-pro-251215`。全部可通过 `.env` 覆盖。

Seedance 图生视频仅在默认 10 分钟窗口内把 Seedream 原始 URL 作为快速路径；过期或缺少截止时间后，改用可选的私有 TOS 媒体暂存读取本地批准关键帧。Worker 上传私有对象、生成短期预签名 HTTPS URL、完成后清理，TOS 签名 URL 不进入数据库或浏览器。配置 `TOS_ACCESS_KEY`、`TOS_SECRET_KEY`、`TOS_BUCKET` 后打开 `PROVIDER_MEDIA_STAGING_V1=1`；配置不完整时 `/health/ready` 会明确失败。未启用时不影响默认 Mock MVP 的完整 Preview、Revision、审批和导出闭环。

## 设计与范围

界面提供 `standard`、`focus`、`cinema` 三种视觉密度，共享同一信息架构和持久化状态合同。当前 MVP 是本地单用户工作台，不包含多租户、协同编辑、支付、通用云资产库、生产级内容审核或平台发布；TOS 只用于 Seedance 源图的临时私有暂存，权利预检仅用于演示，不构成法律意见。
