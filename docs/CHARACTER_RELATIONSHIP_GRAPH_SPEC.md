# 角色关系网设计与版本化创作规格

- 状态：Proposed（开发前规格）
- 版本：v1.0
- 日期：2026-07-15
- 适用基线：React/Vite SPA + FastAPI + SQLAlchemy/Alembic + SQLite WAL + 持久化 Worker
- 所属流程：G2 故事与剧本
- 目标：把角色关系从 Story Bible 中的自由文本升级为可编辑、可校验、可批准、可追溯并能驱动剧本生成的结构化创作基线

## 1. 执行结论

角色关系网不是故事完成后的展示图，而是剧本生成前必须确认的创作输入。系统必须先形成角色与关系基线，再生成分集大纲和结构化剧本。

本规格确定以下产品和工程原则：

1. 角色关系网属于 G2“故事与剧本”的内部子步骤，不增加第六个用户级门禁。
2. 关系网使用独立的 `RelationshipGraphVersion`，不再把正式关系继续保存为 `list[str]`。
3. 角色节点来自 `StoryBibleVersion.characters`，关系网不复制角色姓名、角色职责等事实源字段。
4. 同一角色对只保存一条规范关系边，通过多标签、双向认知和状态轴表达复杂关系，避免图中出现多条重叠边。
5. 关系内容与图形布局分离。拖拽节点、缩放和筛选不改变创作版本，也不触发下游失效。
6. 只有 `DRAFT` 版本可以直接修改语义内容；待审核、已批准、已替代和失败版本都不可原地修改。
7. 已批准版本需要修改时，必须复制为新草稿，并先完成影响分析。
8. AI 可以建议、生成和检查关系，但不能自动解除核心关系锁定，也不能自动批准正式基线。
9. 批准关系基线后才允许生成剧本；剧本中的误判、认证和关系重排必须引用已批准关系。
10. 轮询和任务刷新不得覆盖本地尚未保存的关系编辑。

## 2. 当前基线与目标差距

### 2.1 当前实现

当前 Story Bible 已包含：

- `characters: list[BibleCharacter]`；
- `relationships: list[str]`；
- 世界规则、伏笔与连续性约束。

当前爆款叙事引擎已包含：

- `misjudgment_chain`；
- `authentication_ladder`；
- `relationship_reorders`；
- `emotional_order_rebuild`。

当前 `StoryPage` 能展示角色文字设定和“关系重排”列表，但不能：

- 直观看到角色之间的完整网络；
- 区分明面关系、真实关系和双方认知；
- 编辑或锁定核心关系；
- 将关系变化绑定到场景、证据或认证节拍；
- 比较关系版本；
- 在关系变化后计算受影响的剧本和生产资产；
- 通过强类型校验拒绝孤立角色、无触发的关系突变或无效角色引用。

### 2.2 目标状态

目标流程为：

```mermaid
flowchart LR
    D["批准故事方向"] --> S["生成 Story Bible 与关系草案"]
    S --> R["编辑并确认角色关系基线"]
    R --> O["生成分集大纲与剧本"]
    O --> Q["审核剧本与关系变化"]
    Q --> A["批准 G2"]
    A --> P["进入前期资产"]
```

角色关系网必须能够回答五个创作问题：

1. 谁与谁存在关系？
2. 双方分别如何理解这段关系？
3. 真实关系与明面关系有什么差异？
4. 哪个剧情事件改变了关系？
5. 变化之后如何推动后续行动、冲突和情绪兑现？

## 3. 产品范围

### 3.1 MVP 覆盖

- AI 根据已批准 Story DNA 和 Story Bible 生成关系草案；
- 从 Story Bible 角色生成关系节点；
- 新增、编辑、删除和锁定关系；
- 明面关系与真实关系双层表达；
- 双方认知、信任、情感温度、权力与冲突强度；
- 开场状态、关键转折和本集结尾状态；
- 关系变化绑定剧情事件、认证节拍和剧本场景；
- 关系网校验、提交、批准、复制修订和版本比较；
- 关系变化后的下游影响分析；
- 剧本场景与关系变化双向定位；
- 图形视图与无障碍列表视图；
- 本地未保存编辑保护和乐观锁冲突处理。

### 3.2 MVP 明确不做

- 多人实时协作和细粒度 RBAC；
- 多季、数百角色的大型 IP 知识图谱；
- 自动计算中心性、传播力等社交网络分析指标；
- 自动根据商业表现修改角色关系；
- 动画播放整季关系演变；
- 为关系节点直接生成角色图片或 Look；
- 将关系图布局纳入创作版本和审批内容；
- 区域版本关系差异的自动生成与当地审核闭环。

区域适配、观众认知层、多季关系弧线和表现反馈回写留到 Phase 2。

## 4. 用户流程与界面

### 4.1 G2 内部流程

目标项目流程：

```text
PROPOSAL_READY
→ STORY_STRUCTURE_RUNNING
→ RELATIONSHIP_READY
→ SCRIPT_PACKAGE_RUNNING
→ SCRIPT_READY
→ STORY_APPROVED
```

用户仍只感知一个 G2 门禁，但 G2 内部包含两个确认点：

1. 关系基线确认：确认 Story Bible 角色设定和角色关系；
2. 剧本确认：确认大纲、剧本和关系变化的实际落点。

### 4.2 Story 页面布局

在“故事设定集”与“短剧创作引擎”之间增加全宽“角色关系设计”区块。

区块包含：

1. 顶部状态与工具栏；
2. 中央关系图；
3. 右侧角色或关系属性面板；
4. 底部关系变化时间线；
5. 与图同步的关系列表视图。

顶部工具栏提供：

- `明面关系 / 真实关系`；
- `开场 / 关键转折 / 本集结尾`；
- `全部角色 / 核心角色 / 阵营`；
- `图形视图 / 列表视图`；
- `当前版本 / 与上一版比较`；
- `AI 补全关系`；
- `检查关系网`；
- `保存草稿`；
- `确认关系并生成剧本`。

### 4.3 图形语义

关系图不只依赖颜色表达状态：

| 视觉属性 | 语义 |
|---|---|
| 实线 | 已公开或双方明确知道的关系 |
| 虚线 | 隐藏关系、单方认知或误判 |
| 箭头 | 控制、依附、债务或权力方向 |
| 线宽 | 冲突强度 |
| 线旁文字 | 主要关系标签 |
| 节点描边 | 核心角色、次要角色或阵营 |
| 锁图标 | 已锁定的核心角色或关系 |

颜色必须同时配合文字、线型或图标，不能成为唯一信息来源。

### 4.4 关系属性面板

点击关系线后显示并允许在草稿状态编辑：

- 关系标签；
- 关系方向；
- 明面关系；
- 真实关系；
- A 如何理解 B；
- B 如何理解 A；
- 信任等级；
- 情感温度；
- 权力平衡；
- 冲突强度；
- 剧情功能；
- 关系秘密；
- 是否为核心关系；
- 关系变化事件。

点击角色节点后显示：

- 角色姓名、职责、欲望、恐惧、秘密；
- 与当前角色直接相关的全部关系；
- 当前角色是否孤立；
- 当前角色在关系变化中的参与场景；
- 跳转到 Story Bible 角色文字设定的入口。

角色的 `character_key`、来源版本和审计字段不可在关系网中修改。

### 4.5 关系变化时间线

时间线至少支持：

- 开场状态；
- 一至多个关键变化；
- 本集结尾状态。

每个变化事件显示：

- 发生集数与顺序；
- 触发事件；
- 触发证据；
- 变化前状态；
- 变化后状态；
- 情绪后果；
- 观众可见范围；
- 关联误判、认证或场景。

剧本生成后，时间线事件可以绑定 `scene_id`；生成前只绑定稳定的叙事节拍或认证序号。

### 4.6 本地编辑保护

前端必须分离：

- `serverSnapshot`：服务端最后确认的数据；
- `localDraft`：用户正在编辑的数据；
- `dirtyFields`：尚未成功保存的字段；
- `jobState`：生成与校验任务状态。

轮询或 SSE 只能更新任务、审批和远端版本提示。只要 `localDraft` 处于 dirty 状态，就不能用新的 workspace 响应覆盖它。

发生版本冲突时显示三个明确选项：

1. 保留本地修改并创建新版本；
2. 查看本地与服务端差异；
3. 放弃本地修改并载入服务端版本。

## 5. 编辑状态与权限合同

### 5.1 关系网版本状态

复用现有 `VersionStatus`：

| 状态 | 语义内容可编辑 | 布局偏好可编辑 | 允许操作 |
|---|---:|---:|---|
| `GENERATING` | 否 | 是 | 查看进度、取消生成 |
| `DRAFT` | 是 | 是 | 修改、保存、AI 补全、校验、提交或批准 |
| `READY_FOR_REVIEW` | 否 | 是 | 查看、评论、批准、撤回审核 |
| `APPROVED` | 否 | 是 | 查看、比较、发起修订 |
| `SUPERSEDED` | 否 | 是 | 查看历史、比较、复制为草稿 |
| `FAILED` | 否 | 是 | 查看错误、重试生成 |

说明：

- 布局偏好包括节点坐标、缩放、筛选和折叠状态；
- 布局偏好不进入内容哈希，不产生关系版本，也不触发下游失效；
- 当前单用户 MVP 可以从 `DRAFT` 直接“确认并批准”；如果后续启用独立审核人，则走 `DRAFT → READY_FOR_REVIEW → APPROVED`；
- `READY_FOR_REVIEW` 在尚未产生审核记录时可以撤回为 `DRAFT`；一旦已有审核决定，只能复制为新草稿。

### 5.2 项目流程门禁

| 项目状态 | 是否开放关系语义编辑 | 规则 |
|---|---:|---|
| `DRAFT` | 否 | 尚未形成稳定角色来源 |
| `PROPOSAL_RUNNING` | 否 | 故事方向正在生成 |
| `PROPOSAL_READY` | 否 | 先确认故事方向 |
| `STORY_STRUCTURE_RUNNING` | 否 | Story Bible 与关系草案正在生成 |
| `RELATIONSHIP_READY` | 是 | 只有当前 `DRAFT` 关系版本可直接修改 |
| `SCRIPT_PACKAGE_RUNNING` | 否 | 剧本正在消费已批准关系基线 |
| `SCRIPT_READY` | 不可直接修改基线 | 创建新关系草稿后，当前剧本标记为过期并禁止批准 |
| `STORY_APPROVED` 及以后 | 不可直接修改基线 | 必须创建正式 Change Set，并先确认下游影响 |
| `BLOCKED` | 否 | 先解除阻断或回退到可编辑阶段 |
| `ARCHIVED` | 否 | 只允许查看历史 |

### 5.3 最终可编辑判定

服务端是编辑权限事实源，不允许只靠前端按钮控制。

```text
can_edit_semantics =
    graph.status == DRAFT
    and project_edit_window_open
    and active_relationship_job is None
    and approval_in_progress is False
    and expected_project_version == project.lock_version
    and expected_graph_version == graph.lock_version
```

单条关系还需满足：

```text
can_edit_relationship =
    can_edit_semantics
    and relationship.locked is False
```

如果关系已锁定，用户必须先执行明确的“解除核心关系锁定”动作，并查看影响提示。

### 5.4 后端返回统一编辑能力

关系网读取接口必须返回服务端计算后的能力对象，前端不自行复制完整状态判断：

```json
{
  "editability": {
    "semantic_editable": true,
    "layout_editable": true,
    "can_submit": true,
    "can_approve": true,
    "can_create_revision": false,
    "active_job": false,
    "reason_code": null,
    "reason_message": null,
    "requires_impact_confirmation": false
  }
}
```

常见只读原因：

- `GRAPH_GENERATING`：关系网正在生成；
- `GRAPH_SUBMITTED`：当前版本已提交审核；
- `GRAPH_APPROVED`：当前版本已经批准；
- `GRAPH_SUPERSEDED`：当前版本已被替代；
- `ACTIVE_RELATIONSHIP_JOB`：存在未完成关系生成任务；
- `SCRIPT_CONSUMING_GRAPH`：剧本正在使用该基线生成；
- `PROJECT_BLOCKED`：项目处于阻断状态；
- `PROJECT_ARCHIVED`：项目已归档；
- `VERSION_CONFLICT`：服务端版本已变化；
- `RELATIONSHIP_LOCKED`：该核心关系已锁定。

## 6. 领域模型

### 6.1 RelationshipGraphVersion

新增表 `relationship_graph_versions`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 版本实体 ID |
| `project_id` | UUID FK | 所属项目 |
| `story_bible_version_id` | UUID FK | 节点事实源 |
| `version` | int | 项目内递增版本号 |
| `parent_version_id` | UUID nullable | 修订来源版本 |
| `status` | string | `VersionStatus` |
| `schema_version` | string | 初始值 `relationship-graph-v1` |
| `config_version` | string | 生成合同版本 |
| `provider` | string | 生成供应商 |
| `model` | string | 生成模型 |
| `critic_json` | JSON text | 质量检查结果 |
| `content_hash` | SHA-256 | 规范化语义内容哈希 |
| `lock_version` | int | 关系网自身乐观锁 |
| `approved_at` | datetime nullable | 批准时间 |
| `approved_by` | string nullable | 批准人 |
| `created_at` | datetime | 创建时间 |

约束：

- `UniqueConstraint(project_id, version)`；
- 同一项目最多一个当前 `APPROVED` 基线；
- `APPROVED`、`SUPERSEDED` 内容不可更新；
- `story_bible_version_id` 在当前版本内不可修改；
- 只有语义内容进入 `content_hash`。

### 6.2 RelationshipEdge

新增表 `relationship_edges`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 关系边 ID |
| `graph_version_id` | UUID FK | 所属关系网版本 |
| `relationship_key` | string | 稳定业务键，例如 `protagonist-witness` |
| `character_pair_key` | string | 服务端生成的无序角色对键，用于阻止反向重复建边 |
| `source_character_key` | string | 来源角色 Key |
| `target_character_key` | string | 目标角色 Key |
| `directionality` | enum | `BIDIRECTIONAL` 或 `DIRECTED` |
| `relationship_types_json` | JSON array | 亲属、恋爱、盟友、敌对、控制、债务等 |
| `surface_relationship` | text | 明面关系 |
| `true_relationship` | text | 作者视角下的真实关系 |
| `source_view_json` | JSON object | A 对 B 的认知 |
| `target_view_json` | JSON object | B 对 A 的认知 |
| `trust_level` | int | -2 至 2 |
| `emotional_temperature` | int | -2 至 2 |
| `power_balance` | int | -2 至 2；负值偏向 source，正值偏向 target |
| `conflict_intensity` | int | 0 至 4 |
| `story_function` | text | 该关系如何驱动剧情 |
| `secret` | text nullable | 关系秘密 |
| `is_core` | bool | 是否为核心关系 |
| `locked` | bool | 是否禁止直接编辑 |
| `ordinal` | int | 稳定显示顺序 |

规范规则：

- 一个无序角色对在同一关系版本中只能有一条规范关系边；
- 复杂关系通过 `relationship_types_json` 和双向认知表达，不重复建边；
- `source_character_key` 与 `target_character_key` 必须存在于来源 Story Bible；
- 禁止自环关系；
- `relationship_key` 在项目内保持稳定，修订时不得无故重建；
- 删除角色关系只允许发生在草稿版本。

### 6.3 RelationshipBeat

新增表 `relationship_beats`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 关系变化 ID |
| `graph_version_id` | UUID FK | 所属关系网版本 |
| `relationship_edge_id` | UUID FK | 关联关系 |
| `episode_ordinal` | int | 所属集数 |
| `sequence` | int | 同一关系内连续递增 |
| `scene_ordinal` | int nullable | 剧本生成后的场景序号 |
| `trigger_type` | enum | 触发类型 |
| `trigger_ref` | string nullable | 误判、认证、节拍或场景引用 |
| `before_state_json` | JSON object | 变化前状态 |
| `after_state_json` | JSON object | 变化后状态 |
| `evidence` | text | 触发证据或可见行动 |
| `emotional_consequence` | text | 情绪后果 |
| `audience_visibility` | enum | `HIDDEN`、`PARTIAL`、`REVEALED` |
| `ordinal` | int | 稳定排序 |

`trigger_type` 初始枚举：

- `STORY_EVENT`；
- `MISJUDGMENT`；
- `AUTHENTICATION`；
- `REVEAL`；
- `CHOICE`；
- `BETRAYAL`；
- `PAYOFF`。

### 6.4 RelationshipGraphView

布局数据不进入创作版本。MVP 可先保存在浏览器；如果需要跨设备恢复，再新增 `relationship_graph_views`：

- `project_id`；
- `graph_version_id`；
- `actor`；
- `node_positions_json`；
- `viewport_json`；
- `filters_json`；
- `updated_at`。

布局数据可以在任何非归档版本中更新，不改变 `content_hash`，不触发审批和下游失效。

## 7. 强类型生成合同

### 7.1 Story Structure 生成合同升级

旧 `StoryBible` 合同：

```text
relationships: list[str]
```

新写入不再把正式关系保存在 `StoryBible` 内。`GENERATE_STORY_STRUCTURE` 返回两个并列、各自版本化的对象：

```text
story_bible: StoryBibleV2
relationship_graph: RelationshipGraphPayload
```

`RelationshipGraphPayload` 至少包含：

- `schema_version`；
- `edges`；
- `beats`；
- `core_relationship_keys`；
- `generation_notes`。

`StoryBibleV2` 继续保存 `characters`、世界规则、伏笔和连续性约束，但不再写入新的自由文本 `relationships`。关系节点继续由 `StoryBibleV2.characters` 派生，`RelationshipGraphPayload` 不重复保存角色姓名和职责。

旧 `relationships: list[str]` 仅保留在历史读取和转换适配器中，不作为新版本事实源。

### 7.2 爆款叙事引擎升级

`RelationshipReorder` 从自由 `relationship_key` 升级为强引用：

```text
relationship_key
source_character_key
target_character_key
before_state
trigger_auth_sequence
after_state
emotional_consequence
relationship_beat_id
```

模型校验必须确保：

- `relationship_key` 存在于已批准关系网；
- source/target 与关系网一致；
- `trigger_auth_sequence` 引用有效认证步骤；
- `relationship_beat_id` 属于该关系；
- `before_state` 与关系变化前状态一致；
- `after_state` 与关系变化后状态一致；
- 关系变化不能只写情绪描述，必须产生行动、权力、信任或目标变化。

### 7.3 合同版本建议

- `relationship-graph-v1`；
- `story-bible-v2-relationship-graph`；
- `story-structure-v1-relationship-graph`；
- `script-v4-relationship-driven`；
- `story-package-v4-relationship-driven`。

历史 `relationships: list[str]` 必须继续可读，不允许一次迁移破坏旧项目。

## 8. 工作流与任务

### 8.1 项目状态变更

新增：

- `STORY_STRUCTURE_RUNNING`：Story Bible 与关系草案生成中；
- `RELATIONSHIP_READY`：关系草案可编辑、待确认；
- `SCRIPT_PACKAGE_RUNNING`：基于已批准关系网生成大纲和剧本。

现有 `STORY_PACKAGE_RUNNING` 进入兼容状态：

- 旧数据继续显示为“故事资料生成中”；
- 新请求不再写入该状态；
- 正在执行的旧任务允许自然完成；
- 不做破坏性状态回写。

### 8.2 Job 拆分

将当前 `GENERATE_STORY_PACKAGE` 拆为：

1. `GENERATE_STORY_STRUCTURE`
   - 输入：已批准 Story DNA、Brief、目标时长与语言；
   - 输出：`StoryBibleVersion DRAFT`、`RelationshipGraphVersion DRAFT`；
   - 成功状态：`RELATIONSHIP_READY`。

2. `GENERATE_SCRIPT_PACKAGE`
   - 输入：已批准 Story DNA、Story Bible、Relationship Graph；
   - 输出：Episode Outline、Script、Short Drama Engine、Breakout Engine；
   - 成功状态：`SCRIPT_READY`。

3. `SUGGEST_RELATIONSHIP_GRAPH_CHANGES`
   - 输入：当前草稿、用户意图和锁定关系；
   - 输出：建议 Diff，不直接覆盖草稿；
   - 用户明确应用后才写入关系草稿。

### 8.3 幂等与失败恢复

建议业务键：

```text
{project_id}:GENERATE_STORY_STRUCTURE:{story_version_id}:{config_version}
{project_id}:GENERATE_SCRIPT_PACKAGE:{relationship_graph_id}:{content_hash}:{config_version}
{graph_id}:SUGGEST_RELATIONSHIP_GRAPH_CHANGES:{lock_version}:{request_hash}
```

失败恢复：

- Story Structure 失败：项目回到 `PROPOSAL_READY` 或进入 `BLOCKED`；
- Script Package 失败：项目回到 `RELATIONSHIP_READY`，已批准关系基线保持不变；
- AI 建议失败：草稿不变，只显示可重试错误；
- 任务重试不得重复创建版本；
- Job、版本落库和项目状态更新应在同一事务边界或通过可恢复业务键保证一致性。

## 9. API 合同

### 9.1 读取

```text
GET /api/v1/projects/{project_id}/relationship-graphs
GET /api/v1/relationship-graphs/{graph_id}
GET /api/v1/relationship-graphs/{graph_id}/validation
GET /api/v1/relationship-graphs/{from_id}/diff/{to_id}
```

`story-workspace` 同时返回：

- 当前关系网摘要；
- 当前批准关系网 ID；
- 是否存在未保存或未批准修订；
- `editability`；
- 当前剧本使用的关系网 ID；
- 关系网是否过期。

### 9.2 生成与草稿

```text
POST /api/v1/projects/{project_id}/relationship-graphs/generate
PATCH /api/v1/relationship-graphs/{graph_id}
POST /api/v1/relationship-graphs/{graph_id}/ai-suggestions
POST /api/v1/relationship-graphs/{graph_id}/revisions
```

草稿更新采用原子全量语义保存，避免多条关系修改只成功一部分：

```json
{
  "expected_project_version": 7,
  "expected_graph_version": 3,
  "edges": [],
  "beats": [],
  "actor": "创作者"
}
```

服务端：

1. 检查项目和关系网乐观锁；
2. 检查可编辑状态；
3. 验证全部角色引用和关系结构；
4. 在单事务内替换草稿语义内容；
5. 重算内容哈希和 `lock_version`；
6. 返回规范化关系网与最新编辑能力。

### 9.3 审核与批准

```text
POST /api/v1/relationship-graphs/{graph_id}/submit
POST /api/v1/relationship-graphs/{graph_id}/withdraw
POST /api/v1/relationship-graphs/{graph_id}/approve
```

批准关系网必须原子完成：

1. 校验无 BLOCKER；
2. 校验来源 Story Bible 仍是当前版本；
3. 将 Story Bible 与关系网一起锁定为结构基线；
4. 将旧批准关系网设为 `SUPERSEDED`；
5. 写入 Review Record 与 Event Log；
6. 项目转为 `SCRIPT_PACKAGE_RUNNING`；
7. 创建幂等的 `GENERATE_SCRIPT_PACKAGE` Job。

如果任一步失败，不能只批准关系网而不创建可恢复的剧本任务。

### 9.4 修订与影响分析

```text
POST /api/v1/projects/{project_id}/relationship-revision-impact
POST /api/v1/projects/{project_id}/relationship-revisions
```

修订请求复用现有 `ChangeSet`：

```json
{
  "base_relationship_graph_id": "uuid",
  "relationship_keys": ["protagonist-witness"],
  "intent": "将审问关系调整为相互利用，并把结盟延后到第三场",
  "expected_version": 12
}
```

系统先返回影响范围、预计重生成内容和是否触碰已批准资产；用户确认后才创建新草稿。

## 10. 错误码与用户操作

| HTTP | 错误码 | 用户提示 | 用户操作 |
|---:|---|---|---|
| 404 | `RELATIONSHIP_GRAPH_NOT_FOUND` | 角色关系网不存在 | 返回故事工作区并刷新 |
| 409 | `RELATIONSHIP_GRAPH_NOT_EDITABLE` | 当前关系版本不可编辑 | 查看锁定原因或创建修改版 |
| 409 | `ACTIVE_RELATIONSHIP_JOB` | 关系任务正在执行 | 等待完成或取消任务 |
| 409 | `RELATIONSHIP_VERSION_CONFLICT` | 关系版本已变化 | 比较本地与服务端版本 |
| 409 | `VERSION_CONFLICT` | 项目版本已变化 | 刷新项目后重试 |
| 409 | `RELATIONSHIP_LOCKED` | 核心关系已锁定 | 查看影响并显式解除锁定 |
| 409 | `SCRIPT_CONSUMING_GRAPH` | 剧本正在使用该关系基线生成 | 等待任务完成后发起修订 |
| 409 | `STORY_BIBLE_OUTDATED` | 角色设定已变化 | 基于最新 Story Bible 创建关系草稿 |
| 409 | `RELATIONSHIP_REVISION_REQUIRED` | 当前基线已经批准 | 创建新的关系修订版本 |
| 409 | `DOWNSTREAM_IMPACT_CONFIRMATION_REQUIRED` | 修改会影响已批准内容 | 查看并确认影响范围 |
| 422 | `INVALID_CHARACTER_REFERENCE` | 关系引用了不存在的角色 | 选择有效角色或更新 Story Bible |
| 422 | `DUPLICATE_RELATIONSHIP_PAIR` | 同一角色对存在重复关系 | 合并为一条多标签关系 |
| 422 | `SELF_RELATIONSHIP_NOT_ALLOWED` | 不能创建角色与自身的关系 | 选择两个不同角色 |
| 422 | `CORE_CHARACTER_ISOLATED` | 核心角色没有有效关系 | 补充至少一条剧情关系 |
| 422 | `MISSING_PRIMARY_CONFLICT` | 缺少主冲突关系 | 指定至少一条核心冲突关系 |
| 422 | `INVALID_RELATIONSHIP_BEAT` | 关系变化缺少有效触发 | 补充事件、证据和前后状态 |
| 422 | `RELATIONSHIP_GRAPH_VALIDATION_FAILED` | 关系网尚未达到批准条件 | 查看检查结果并修正阻断项 |
| 409 | `PROJECT_BLOCKED` | 项目当前已阻断 | 先处理阻断原因 |
| 409 | `PROJECT_ARCHIVED` | 归档项目不可修改 | 恢复项目或只读查看 |

错误响应继续遵循现有结构：

```json
{
  "code": "RELATIONSHIP_GRAPH_NOT_EDITABLE",
  "message": "当前关系版本已经批准，不能直接修改。",
  "details": {
    "graph_status": "APPROVED",
    "project_status": "SCRIPT_READY"
  },
  "user_action": "创建新的关系修订版本。"
}
```

## 11. 校验与批准门禁

### 11.1 校验等级

- `BLOCKER`：禁止批准和剧本生成；
- `WARNING`：允许批准，但必须展示；
- `INFO`：创作建议，不影响流程。

### 11.2 结构校验

BLOCKER：

- source/target 不存在；
- 自环关系；
- 同一角色对重复建边；
- 数值状态超出范围；
- 关系变化序号不连续；
- 关系变化引用不存在的关系；
- 草稿的 Story Bible 来源已过期。

### 11.3 创作校验

BLOCKER：

- 主角没有任何关系；
- 核心角色为孤立节点；
- 没有主冲突关系；
- 核心关系没有剧情功能；
- 明面关系与真实关系不同，但没有揭示或认证计划；
- 关系从敌对直接跳到信任，但没有事件、证据和情绪后果；
- 已锁定核心关系被生成结果擅自改变。

WARNING：

- 核心角色关系过密，图形和剧情可能难以理解；
- 次要角色没有关系变化；
- 多条核心关系在同一场景同时重排；
- 关系强度变化很大但行动变化不明确；
- 结尾关系状态与续作触发缺少联系。

### 11.4 跨合同校验

生成剧本后必须检查：

- 所有 `MisjudgmentStep.observer_key` 都存在；
- 所有 `AuthenticationStage.who_updates_belief` 都存在；
- 所有 `RelationshipReorder.relationship_key` 都属于批准关系网；
- 关系重排引用有效认证步骤和关系变化；
- 关系变化绑定的场景存在；
- 变化前后状态与关系网定义一致；
- 剧本没有在设定揭示点之前泄露关系秘密；
- 核心关系至少在一个场景中产生可观察行动或状态变化。

## 12. 版本 Diff 与下游影响

### 12.1 Diff 分类

关系版本比较至少输出：

- 新增关系；
- 删除关系；
- 关系标签变化；
- 明面关系变化；
- 真实关系变化；
- 双方认知变化；
- 信任、情感、权力或冲突变化；
- 关系变化事件新增、删除、移动或重写；
- 核心锁定变化；
- 纯布局变化。

### 12.2 影响等级

| 等级 | 变化 | 处理 |
|---|---|---|
| P0 | 角色 Key、锁定核心身份、不可变 IP 关系 | 阻断，必须先新建 Story Bible 或规范版本 |
| P1 | 主冲突、真实关系、结尾关系状态、核心关系删除 | 明确确认，重生成 Outline、Script 及全部下游依赖 |
| P2 | 关系节拍、认知、信任、权力、次要关系 | 定位受影响场景，进行局部或整集重生成 |
| P3 | 文案标签、说明文字 | 重新校验，通常不失效媒体 |
| P4 | 节点布局、缩放和筛选 | 无创作影响，不创建版本 |

### 12.3 依赖传播

| 关系变化 | 最小失效范围 |
|---|---|
| 新增或删除核心角色关系 | Relationship Graph、Outline、Script、Storyboard 与相关媒体；仅在角色设定或连续性冲突时使 Story Bible 过期 |
| 修改真实关系或主冲突 | Outline、Script 及全部派生资产 |
| 修改关系变化触发 | 对应 Script Scene、Shot、临时声音和 Animatic |
| 修改观众可见范围 | Script Line、旁白、字幕和预告性镜头 |
| 修改信任或权力状态 | 对应场景动作、对白、表演意图和构图提示 |
| 修改关系标签说明 | 关系显示与校验，通常不失效媒体 |
| 修改布局 | 无失效 |

批准新关系修订前必须展示：

- 受影响集数；
- 受影响场景；
- 受影响角色和关系；
- 需重新生成的资产类型；
- 不受影响并会保留的资产；
- 预计任务时长和积分。

## 13. 前端合同

### 13.1 类型

新增：

- `RelationshipGraphVersionRecord`；
- `RelationshipEdgeRecord`；
- `RelationshipBeatRecord`；
- `RelationshipGraphValidation`；
- `RelationshipGraphDiff`；
- `RelationshipGraphEditability`。

`StoryWorkspace` 增加：

- `relationshipGraphVersions`；
- `currentRelationshipGraphId`；
- `approvedRelationshipGraphId`；
- `scriptRelationshipGraphId`；
- `relationshipGraphOutdated`。

### 13.2 组件建议

新增组件目录：

```text
src/components/relationship-graph/
  RelationshipGraphSection.tsx
  RelationshipGraphCanvas.tsx
  RelationshipGraphList.tsx
  RelationshipInspector.tsx
  RelationshipTimeline.tsx
  RelationshipValidationPanel.tsx
  RelationshipVersionDiff.tsx
  relationshipGraphState.ts
```

`StoryPage` 负责流程编排，不直接承担图布局、编辑器状态和校验展示细节。

### 13.3 UI 状态要求

每个不可编辑状态必须展示原因，不能只禁用控件：

- “关系网正在生成，完成后可继续编辑。”
- “当前版本已提交审核，请先撤回。”
- “当前版本已经批准，请创建修改版。”
- “剧本正在使用该关系基线生成，暂时不可修改。”
- “核心关系已锁定，解除后可能导致剧本失效。”

保存状态必须明确显示：

- 未保存；
- 正在保存；
- 已保存；
- 保存失败；
- 版本冲突。

### 13.4 无障碍要求

- 所有关系都能在列表视图中读取和编辑；
- 键盘可以选择节点、关系和时间线事件；
- 图形颜色不是唯一状态信号；
- 关系标签和锁定状态具有屏幕阅读器文本；
- 缩放不能阻断页面级键盘导航；
- 校验错误可以直接跳转到对应角色、关系或事件。

## 14. 后端改动范围

建议新增或修改：

```text
server/migrations/versions/0019_character_relationship_graph.py
server/app/db/models.py
server/app/domain/statuses.py
server/app/schemas.py
server/app/services/relationship_graphs.py
server/app/services/creative_story.py
server/app/services/text_provider.py
server/app/api/v1/relationship_graphs.py
server/app/api/v1/stories.py
server/app/jobs/handlers/proposal.py
server/app/jobs/registry.py
server/app/main.py
```

测试建议新增：

```text
server/tests/test_relationship_graphs.py
server/tests/test_relationship_graph_validation.py
server/tests/test_relationship_graph_revisions.py
```

现有 `test_text_provider.py`、`test_jobs.py`、`test_migrations.py` 和 `test_status_contracts.py` 同步扩展。

## 15. 兼容与迁移

### 15.1 数据库迁移

Migration `0019`：

- 创建关系网版本、关系边和关系变化表；
- 增加必要索引和唯一约束；
- 不删除 Story Bible 旧 `relationships` JSON；
- 不批量改写已批准历史版本；
- 为新表提供可重复运行的安全回填逻辑。

### 15.2 旧数据读取

旧 Story Bible 的 `relationships: list[str]` 通过兼容适配器生成只读关系摘要：

- 能从文本和角色数量安全推导时，生成 `LEGACY_DERIVED` 草稿建议；
- 无法可靠确定角色对时，只显示原始文本，不伪造结构化关系；
- 用户点击“转换为关系网”后创建新草稿；
- 转换结果必须人工确认后才能批准和生成新剧本。

### 15.3 状态兼容

- 旧 `STORY_PACKAGE_RUNNING` 保持可读；
- 旧 Job 不重写类型；
- 新接口返回本地化显示文案，但协议枚举保持英文稳定值；
- 历史 `story-package-v3-breakout-engine` 和 `script-v3-breakout-engine` 继续可读；
- 只有新生成内容写入 v4 合同。

## 16. 验收标准

### 16.1 核心闭环

1. 用户批准故事方向后，系统生成 Story Bible 和关系草稿，不直接生成剧本。
2. 用户能够查看至少两个角色和一条结构化关系。
3. 用户可以在草稿状态修改明面关系、真实关系和至少一个关系变化事件。
4. 刷新任务状态或等待轮询时，本地未保存关系编辑不会被覆盖。
5. 用户批准关系基线后，系统基于其内容生成 Outline 和 Script。
6. 剧本中的关系重排能够回链到关系网和具体变化事件。
7. 关系校验存在 BLOCKER 时不能批准或生成剧本。
8. 已批准关系版本不能原地修改。
9. 创建修订会保留旧批准版本，并生成新草稿。
10. 批准修订前能看到受影响场景和资产范围。

### 16.2 编辑状态

1. `DRAFT` 且项目处于 `RELATIONSHIP_READY` 时可编辑。
2. `GENERATING`、`READY_FOR_REVIEW`、`APPROVED`、`SUPERSEDED`、`FAILED` 不可编辑语义内容。
3. 所有版本均可调整个人布局偏好，归档项目除外。
4. 存在关系生成任务时，即使版本为草稿也不可保存语义修改。
5. 项目或关系网 `lock_version` 不匹配时返回冲突，不允许最后写入覆盖。
6. 锁定关系不可直接修改，解除时必须显示影响提示。

### 16.3 校验与合同

1. 不存在的角色 Key 被拒绝。
2. 自环关系被拒绝。
3. 同一角色对重复关系被拒绝。
4. 核心孤立角色被拒绝。
5. 无主冲突关系的图被拒绝。
6. 无触发事件的重大关系突变被拒绝。
7. 关系重排引用不存在关系时，整个剧本包被拒绝。
8. 关系秘密在设定揭示点之前泄露时产生 BLOCKER 或明确审查项。
9. 旧 Story Bible 仍能读取，不因新合同升级报错。

## 17. 测试计划

### 17.1 后端单元测试

- 关系数据范围和唯一约束；
- 角色引用、自环和重复角色对；
- 关系变化连续性；
- 核心角色连通性；
- 明面/真实关系与揭示计划；
- 爆款引擎与批准关系网交叉校验；
- 内容哈希排除布局字段；
- 编辑能力计算和只读原因。

### 17.2 后端集成测试

- `PROPOSAL_READY → STORY_STRUCTURE_RUNNING → RELATIONSHIP_READY`；
- 关系草稿保存与乐观锁冲突；
- 关系批准与 Script Job 原子创建；
- Script Job 失败后回到 `RELATIONSHIP_READY`；
- 已批准版本修改返回 409；
- 修订复制、Diff、影响分析和旧版本保留；
- Job 重放不重复创建关系版本或剧本版本；
- Migration 升级和旧数据读取。

### 17.3 前端测试

- 状态与按钮权限映射；
- 只读原因显示；
- dirty draft 不被轮询覆盖；
- 关系编辑、保存和冲突处理；
- 图形与列表选择同步；
- 校验错误跳转；
- 版本 Diff 展示；
- 中文状态、错误和动作提示完整。

### 17.4 浏览器验收

至少覆盖：

1. 生成关系草稿；
2. 编辑核心关系并保存；
3. 在轮询期间保持本地编辑；
4. 锁定关系并验证不可编辑；
5. 批准关系并生成剧本；
6. 从剧本场景跳回关系变化；
7. 已批准版本创建修订；
8. 查看关系版本差异和下游影响；
9. 小于 1024px 时保持现有只读策略；
10. 图形视图不可用时仍可通过列表完成核心任务。

## 18. 分阶段实施建议

### PR-1：领域模型与兼容读取

- Migration `0019`；
- Relationship Graph/Edge/Beat 模型与 schema；
- 旧 `relationships: list[str]` 兼容适配器；
- 基础校验和测试。

### PR-2：两阶段故事生成

- 拆分 Story Structure 与 Script Package Job；
- 新增项目状态与状态迁移；
- Text Provider 强类型关系合同；
- 关系网与爆款引擎交叉校验。

### PR-3：关系编辑器与审核

- Story 页面关系区块；
- 图形、列表、属性面板和时间线；
- 编辑能力、保存、校验、锁定与批准；
- 本地草稿保护。

### PR-4：版本 Diff 与修订影响

- 关系版本比较；
- Change Set 集成；
- 下游失效传播；
- Script 与关系变化双向定位。

### PR-5：全量回归与浏览器验证

- 后端、前端、迁移和构建回归；
- 负向合同测试；
- 浏览器核心闭环；
- 中文 UI 和错误提示检查；
- 文档、运行手册和测试证据更新。

## 19. 开发前锁定决策

以下决定作为默认实施基线，除非后续明确变更：

1. 关系网是 G2 内部子步骤，不新增用户级门禁。
2. 先确认关系，再生成剧本。
3. 关系网是独立版本实体，不继续使用自由文本作为事实源。
4. 角色节点来自 Story Bible，不重复维护角色事实。
5. 同一角色对只有一条规范关系边。
6. 只有草稿可直接编辑；批准后必须复制修订。
7. 布局与语义内容分离。
8. AI 建议以 Diff 形式返回，不直接覆盖草稿。
9. 关系批准与 Script Job 创建必须原子化。
10. 旧项目、旧状态和旧合同继续可读。

## 20. 完成定义

本功能完成，不以“页面上能画出节点和线”为标准，而以以下闭环为标准：

```text
故事方向
→ Story Bible 与关系草案
→ 人工编辑、校验和批准
→ 关系驱动的剧本生成
→ 场景与关系变化可追溯
→ 批准后不可变
→ 修订前影响分析
→ 只重生成受影响内容
```

只有数据结构、生成合同、状态门禁、UI 编辑、版本 Diff、影响传播和负向测试全部成立，角色关系网才算成为真实的创作能力。
