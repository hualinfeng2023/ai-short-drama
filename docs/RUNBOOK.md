# 本地运行手册

## 启动与停止

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
curl --fail http://127.0.0.1:8000/health/ready
```

首次启动会自动执行 Alembic migration 与幂等 Seed。停止服务使用 `docker compose down`；默认保留 `ai_short_drama_data` volume。

当前数据库迁移头为 `0021_rel_revision_sets`。升级已有本地库前应先备份数据目录，然后执行：

```bash
cd server
.venv/bin/alembic upgrade head
.venv/bin/alembic current
.venv/bin/alembic check
```

Workflow v2 的三个生产工作台分别为：

- `/projects/{project_id}/preproduction`：角色、Look、Voice、Location、Prop、Visual Bible 与 G3；
- `/projects/{project_id}/storyboard`：动态 Shot Spec、Storyboard、Animatic、Workflow DAG 与 G4；
- `/projects/{project_id}/production`：音频、Lip Sync、多轨 Timeline、8 项整片 QC、G5 与 Delivery Matrix。

角色关系网位于 `/projects/{project_id}/story`，处于“故事设定”与“分集大纲”之间。关键接口为：

- `GET /api/v1/projects/{project_id}/relationship-graphs`：读取全部关系版本与编辑能力；
- `PATCH /api/v1/relationship-graphs/{graph_id}`：原子保存草稿，必须同时提交项目和关系版本锁；
- `GET /api/v1/relationship-graphs/{from_id}/diff/{to_id}`：比较两个关系版本；
- `POST /api/v1/projects/{project_id}/relationship-revision-impact`：只读分析修订影响；
- `POST /api/v1/projects/{project_id}/relationship-revisions`：确认影响哈希后创建修改版。

关系修改版创建后，已有分集大纲和剧本保留为历史资产，但故事工作区会返回 `relationship_graph_stale=true`。此时剧本批准接口返回 `SCRIPT_RELATIONSHIP_GRAPH_OUTDATED`，必须先批准新关系版本并重新生成剧本。影响范围变化时重新分析，不要复用旧 `impact_hash`。

常见关系修订错误：

- `RELATIONSHIP_VERSION_CONFLICT`：关系版本已变化，保留本地编辑并刷新比较；
- `INVALID_CHARACTER_REFERENCE`：关系引用不在当前角色设定中，本次保存不会落库；
- `RELATIONSHIP_REVISION_ALREADY_OPEN`：已有草稿或待审核修改版，继续处理现有版本；
- `RELATIONSHIP_REVISION_IMPACT_STALE`：项目或下游资产变化，重新查看影响范围；
- `GRAPH_APPROVED`：批准版本只读，必须使用“创建修改版”。

## Seedance 私有 TOS 暂存

普通镜头关键帧默认保存在本地。启用真实 Seedance 自动视频前，需要创建一个 **Private** TOS Bucket，并向服务端身份授予目标前缀内最小范围的 `PutObject`、`GetObject` 和 `DeleteObject` 权限；不需要公开读或 Bucket 列表权限。

```dotenv
ARK_API_KEY=服务端方舟密钥
SEEDREAM_SOURCE_URL_FAST_PATH_SECONDS=600
TOS_ACCESS_KEY=服务端TOS访问密钥
TOS_SECRET_KEY=服务端TOS私钥
TOS_SECURITY_TOKEN=
TOS_ENDPOINT=tos-cn-beijing.volces.com
TOS_REGION=cn-beijing
TOS_BUCKET=你的私有Bucket
TOS_PRESIGN_TTL_SECONDS=7200
TOS_OBJECT_PREFIX=ai-short-drama/media-staging
TOS_OBJECT_EXPIRES_DAYS=1
TOS_CLEANUP_ON_COMPLETION=1
PROVIDER_MEDIA_STAGING_V1=1
```

重启服务后检查：

```bash
curl --fail http://127.0.0.1:8000/health/ready
curl --fail http://127.0.0.1:8000/meta/config
```

`checks.media_staging.status` 必须为 `ok`，`media_staging_configured` 必须为 `true`。Seedream 原始 URL 只在 `SEEDREAM_SOURCE_URL_FAST_PATH_SECONDS` 定义的短窗口内作为快速路径，默认 600 秒；过期、缺少截止时间或无法解析时自动改走私有 TOS。TOS 预签名 URL 是临时持有者凭证，只存在于 Worker 内存；数据库和日志只记录脱敏后的 Bucket、Object Key、源资产 Hash、到期时间和清理结果。建议同时在 Bucket 上配置 1 天生命周期规则，清理 Worker 崩溃时遗留的对象。

## 就绪诊断

`/health/ready` 同时检查数据目录、SQLite migration、FFmpeg/ffprobe、CJK 字体、Worker heartbeat，以及启用时的 TOS 暂存配置。任一项失败会返回 503，并在 `checks` 中给出具体依赖。

常用诊断：

```bash
docker compose ps
docker compose logs --no-color app
docker compose exec -T app python -m alembic current
docker compose exec -T app python -m app.smoke --runs 2
```

## 任务恢复

Worker 每个任务持有 15 秒 lease 并持续写 heartbeat。进程重启后：

- 过期的 RUNNING 任务进入 RETRY_WAIT；
- 已请求取消的任务进入 CANCELLED；
- 达到最大尝试次数的任务进入 FAILED；
- Revision/Export 失败或取消会恢复项目状态，Export 同时释放未提交积分。

任务中心可查看进度、取消、重试和错误代码；SSE 断线后使用事件 sequence 续传，3 秒轮询是 UI 后备路径。

## 数据与安全

- 服务默认只发布到 `127.0.0.1:8000`。
- 容器以 UID/GID 10001 非 root 用户运行。
- `.env` 与 `server/.env` 不提交；Provider Key 不进入浏览器、资产元数据或 Manifest。
- TOS Bucket 必须保持 Private；预签名 URL 不持久化、不写日志、不发送到浏览器，默认 2 小时失效。
- 上传只接受 TXT/MD/PDF/DOCX/PNG/JPG/WebP/MP4 文件流，不接受 URL/ZIP；单文件和项目总量均有限制。
- 清空全部本地数据是破坏性操作：先停止服务，再由操作者明确删除 Docker volume。本项目不会自动执行该操作。

## 发布前检查

执行 README 中全部前后端命令、Alembic drift check、容器内 20 次 smoke，并在桌面浏览器完成创建、上传、G1–G5、正式媒体、音频、多轨 Preview、Delivery、下载和刷新恢复。

Workflow v2 的最低回归命令：

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

真实 Provider Smoke 不进入默认离线 CI。执行前必须确认 Secret 只在服务端环境变量中，并检查 Generation Record、下载资产、QC、Review 和降级原因是否完整持久化。
