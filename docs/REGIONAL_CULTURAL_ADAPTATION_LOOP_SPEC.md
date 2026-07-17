# 区域文化适配闭环产品与开发规格

- 状态：Proposed
- 版本：v1.0
- 日期：2026-07-14
- 模块名称：区域版本中心（Regional Adaptation Loop）
- 适用基线：Brief v2、Story/Script Version、Workflow DAG、Review Gate、Generation/QC/Review、Multitrack Timeline、Export Profile
- 建议起始迁移：0019

## 1. 执行结论

区域文化适配不是简单的翻译或字幕本地化，而是：

> 在保留同一套 Story DNA 和 IP 核心的前提下，针对不同地区、受众和平台，对剧情结构、人物关系、冲突动机、情绪表达、对白语气、视觉设定和叙事节奏进行可控调整。

本模块把以下链路变成可持续运行的产品闭环：

**区域文化画像 → 剧情与角色适配 → 版本差异比较 → 当地审核 → 表现反馈 → 反哺画像**

核心要求是让每一环都成为“可存、可比、可回收”的版本化对象：

- 可存：输入、输出、规则、证据和决策均持久化，不依赖聊天记录或临时 Prompt；
- 可比：规范版本、区域版本和画像版本之间有结构化 Diff；
- 可回收：上线表现能回链到适配项，并形成画像更新提案；
- 可审核：自动检查不能代替当地人工审核；
- 可追溯：历史项目永久引用当时使用的画像版本，不随画像更新而漂移。

## 2. 产品定位

用户可见名称建议为“区域版本中心”。

其产品承诺是：

> 一套故事内核，生成多个真正面向当地受众的区域版本，并确保每次调整都有依据、可审核、可回退。

它与普通本地化的区别如下：

| 普通本地化 | 区域文化适配 |
|---|---|
| 翻译文本 | 调整人物、关系、动机、冲突、节奏和表达 |
| 以语言为单位 | 以市场 × 受众 × 平台为单位 |
| 主要产出字幕或配音 | 产出完整区域 Story、Script、Character 和 Production Baseline |
| 通常不解释变化原因 | 每项变化都有文化依据、风险与影响范围 |
| 反馈停留在报表 | 反馈生成画像更新提案并进入下一版本 |

## 3. 范围与非目标

### 3.1 本规格覆盖

- 地区、受众和平台维度的文化画像；
- 地区偏好、禁忌、情绪表达、冲突类型和节奏偏好；
- Story DNA 不变量与允许调整范围；
- 区域化 Story、Script、Character、Look、Voice、Location 和 Prop 建议；
- 规范版本与区域版本的结构化 Diff；
- 当地审核、返工、拒绝、人工覆盖与版本锁定；
- 表现指标和定性反馈导入；
- 表现反馈与 Diff Item、场景、角色、台词之间的关联；
- 基于证据的画像更新提案；
- 新旧画像版本比较、审核和启用。

### 3.2 首版不做

- 自动将相关性包装为因果结论；
- 未经人工审核自动更新文化画像；
- 自动发布到外部平台；
- 把地区画像视为所有当地用户的固定刻板印象；
- 基于敏感个人属性做个体级定向；
- 在没有样本量、时间窗口和来源信息时自动学习；
- 用区域适配覆盖或修改规范 Story、Script 和已批准资产。

## 4. 现有系统基线与缺口

### 4.1 可复用能力

现有系统已经具备：

- BriefVersion 中的 primary_market、secondary_markets、canonical_language 和 localization_targets；
- StoryVersion、StoryBibleVersion、EpisodeOutlineVersion 和 ScriptVersion；
- Character、CharacterLookVersion、VoiceProfile、LocationVersion、PropVersion 和 VisualBibleVersion；
- WorkflowRun、WorkflowNode、JobDependency 和 ReviewGate；
- GenerationRecord、QualityCheck 和 ReviewRecord；
- 不可变 Timeline、比较、批准、回滚和影响分析；
- Export Profile、Rights Preflight 和多语言导出。

### 4.2 当前缺口

目前市场与语言主要是 Brief 输入和文案本地化参数，尚缺少：

- 可复用、可版本化的区域文化画像；
- 独立的适配合同与 Story DNA 不变量；
- 区域 Script/Character 的明确血缘；
- 可查询的结构化文化适配 Diff；
- 当地审核策略版本；
- 区域版本锁定基线；
- 表现数据与具体适配项之间的回链；
- 画像更新提案和学习审核。

本模块应作为现有生产图上的新域，不建立一套与 Story、Script、Review 和 Workflow 平行的孤立系统。

## 5. 产品闭环

~~~mermaid
flowchart LR
    C["规范 Story、Script 与 Character"] --> A["区域适配任务"]
    P["区域文化画像 vN"] --> A
    A --> V["区域 Story、Script 与 Character Version"]
    V --> D["结构化 Diff"]
    D --> Q["自动文化与连续性检查"]
    Q --> R["当地审核"]
    R --> L["锁定区域基线"]
    L --> E["区域生产与导出"]
    E --> F["表现与本地反馈"]
    F --> U["画像更新提案"]
    U --> G["学习审核"]
    G --> P2["区域文化画像 vN+1"]
    P2 --> A2["下一轮区域适配"]
~~~

## 6. 设计原则

### 6.1 画像按版本快照消费

每次 RegionalAdaptationRun 必须绑定一个明确的 RegionalCultureProfileVersion ID。

禁止使用“运行时读取最新画像”的隐式逻辑，否则同一个任务重试时可能产生不同结果。

### 6.2 规范版本永不被区域版本覆盖

区域适配必须创建新版本，并记录：

- source canonical version；
- target market；
- culture profile version；
- adaptation plan version；
- parent regional version。

### 6.3 自动检查不等于当地批准

模型或规则可以发现禁忌、Story DNA 偏离、时长异常和角色关系冲突，但最终区域基线必须由具名审核人批准。

### 6.4 反馈只能产生提案

表现数据只能创建 CultureProfileUpdateProposal。提案批准后才创建 RegionalCultureProfileVersion vN+1。

### 6.5 Diff 是一等公民

Diff 必须可被 API 查询、UI 展示、审核引用、影响分析消费，也必须能与表现数据关联。

### 6.6 文化规则必须带证据和有效期

任何偏好或禁忌都应尽量记录：

- 来源；
- 适用受众；
- 适用平台；
- 置信度；
- 生效时间；
- 是否经过当地人工确认。

## 7. 领域模型

### 7.1 对象总览

| 对象 | 类型 | 作用 |
|---|---|---|
| RegionalCultureProfile | 稳定实体 | 表示一个地区 × 受众 × 平台画像 |
| RegionalCultureProfileVersion | 不可变版本 | 保存某一时点的画像规则和证据 |
| RegionalAdaptationRun | 工作流根实体 | 记录一次规范内容到区域版本的适配 |
| AdaptationPlanVersion | 不可变版本 | 锁定保持项、允许变化项和适配策略 |
| CharacterAdaptationVersion | 不可变版本 | 保存区域角色关系、动机和设计变化 |
| AdaptationDiffSet | 不可变结果 | 表示一次完整版本比较 |
| AdaptationDiffItem | 结构化差异 | 表示一个字段或语义单元的变化 |
| RegionalReviewPolicyVersion | 不可变版本 | 保存当地审核和合规规则 |
| RegionalBaseline | 锁定基线 | 指向获准进入生产的区域版本集合 |
| RegionalPerformanceSnapshot | 不可变快照 | 保存平台表现数据 |
| RegionalFeedbackItem | 可审核事实 | 保存争议点、当地意见和定性反馈 |
| AdaptationOutcomeObservation | 分析结果 | 关联适配项与表现变化，不宣称因果 |
| CultureProfileUpdateProposal | 审核对象 | 建议如何更新画像 |

## 8. 数据结构

### 8.1 RegionalCultureProfile

稳定实体，用于承载画像身份。

建议字段：

- id；
- profile_key；
- market_code；
- audience_segment；
- platform；
- status；
- current_version_id；
- created_at；
- updated_at。

唯一约束建议：

**market_code + audience_segment + platform**

同一市场可以为不同受众、平台保留独立画像。

### 8.2 RegionalCultureProfileVersion

不可变版本。

建议字段：

- id；
- profile_id；
- version；
- parent_version_id；
- schema_version；
- preferences_json；
- taboos_json；
- emotion_patterns_json；
- conflict_patterns_json；
- pacing_preferences_json；
- character_archetypes_json；
- dialogue_conventions_json；
- visual_conventions_json；
- review_rules_json；
- evidence_json；
- confidence_score；
- effective_from；
- effective_to；
- content_hash；
- status；
- approved_by；
- approved_at；
- created_at。

内容结构建议：

#### preferences_json

- preferred_genres；
- preferred_relationships；
- preferred_endings；
- aspirational_signals；
- relatable_settings。

#### taboos_json

- prohibited_topics；
- sensitive_topics；
- stereotype_risks；
- platform_restrictions；
- legal_or_policy_notes。

#### emotion_patterns_json

- directness；
- intensity；
- humor_style；
- affection_expression；
- anger_expression；
- reconciliation_style。

#### conflict_patterns_json

- common_conflicts；
- acceptable_intensity；
- villain_archetypes；
- family_conflict_rules；
- romance_conflict_rules；
- workplace_conflict_rules。

#### pacing_preferences_json

- expected_hook_seconds；
- reversal_frequency；
- dialogue_density；
- preferred_episode_duration；
- cliffhanger_style；
- tolerance_for_exposition。

### 8.3 RegionalAdaptationRun

一次区域适配的根实体。

建议字段：

- id；
- project_id；
- workflow_run_id；
- source_story_version_id；
- source_story_bible_version_id；
- source_script_version_id；
- source_visual_bible_version_id；
- culture_profile_version_id；
- review_policy_version_id；
- target_market；
- target_audience_segment；
- target_platform；
- target_language；
- status；
- current_gate；
- config_version；
- created_by；
- created_at；
- updated_at；
- completed_at。

幂等业务键建议包含：

**source_script_version_id + culture_profile_version_id + target_platform + config_version**

### 8.4 AdaptationPlanVersion

它是规范版本与区域版本之间的适配合同。

建议字段：

- id；
- adaptation_run_id；
- version；
- parent_version_id；
- invariants_json；
- mutable_scopes_json；
- character_rules_json；
- conflict_rules_json；
- dialogue_rules_json；
- pacing_rules_json；
- visual_rules_json；
- rights_and_policy_rules_json；
- risk_budget_json；
- provider；
- model；
- config_version；
- generation_evidence_json；
- content_hash；
- status；
- approved_by；
- approved_at；
- created_at。

#### invariants_json

必须明确列出不可改变内容：

- core_premise；
- protagonist_goal；
- central_emotional_arc；
- canonical_character_keys；
- key_relationships；
- required_story_beats；
- IP_signatures；
- rights_constraints。

#### mutable_scopes_json

允许调整内容：

- names；
- occupations；
- family_structure；
- social_setting；
- secondary_relationships；
- conflict_trigger；
- dialogue_tone；
- humor；
- ending_tone；
- pacing；
- locations；
- costumes；
- props。

### 8.5 ScriptVersion 扩展

不新增完全平行的 RegionalScript 表，建议扩展现有 ScriptVersion：

- variant_kind：CANONICAL 或 REGIONAL；
- market_code；
- audience_segment；
- target_platform；
- target_language；
- source_canonical_script_version_id；
- culture_profile_version_id；
- adaptation_plan_version_id；
- adaptation_run_id。

已有 version、parent_version_id、content_hash、status 和 approved 字段继续使用。

约束：

- REGIONAL 必须有 source_canonical_script_version_id；
- CANONICAL 不得引用区域 Script；
- 区域 Script 的修改不得更新规范 Script；
- 区域 Script 的后续返工以其前一区域版本为 parent。

### 8.6 CharacterAdaptationVersion

用于承载区域化的角色叙事身份，避免把所有变化继续塞入 Story Bible JSON。

建议字段：

- id；
- project_id；
- adaptation_run_id；
- canonical_character_id；
- version；
- parent_version_id；
- market_code；
- localized_name；
- localized_role；
- occupation；
- family_structure_json；
- relationship_adjustments_json；
- motivation_adjustments_json；
- personality_expression_json；
- dialogue_voice_json；
- visual_implications_json；
- voice_implications_json；
- invariants_json；
- rationale_json；
- content_hash；
- status；
- approved_by；
- approved_at；
- created_at。

现有 CharacterLookVersion 和 VoiceProfile 继续承载正式视觉和声音版本；CharacterAdaptationVersion 只描述区域叙事与设计意图。

### 8.7 AdaptationDiffSet

表示一次完整比较。

建议字段：

- id；
- adaptation_run_id；
- source_entity_type；
- source_entity_id；
- target_entity_type；
- target_entity_id；
- culture_profile_version_id；
- algorithm_version；
- summary_json；
- risk_summary_json；
- downstream_impact_json；
- estimated_rebuild_cost_json；
- content_hash；
- status；
- created_at。

同一 Source、Target 和 algorithm_version 应产生确定性相同的 Diff Hash。

### 8.8 AdaptationDiffItem

建议字段：

- id；
- diff_set_id；
- entity_type；
- entity_key；
- field_path；
- change_type；
- before_json；
- after_json；
- reason_code；
- rationale；
- culture_rule_path；
- culture_evidence_ids_json；
- invariant_rule_path；
- risk_level；
- risk_tags_json；
- downstream_impacts_json；
- estimated_cost_json；
- review_status；
- reviewed_by；
- reviewed_at。

change_type 枚举：

- PRESERVED；
- LOCALIZED；
- ADAPTED；
- ADDED；
- REMOVED；
- REORDERED。

### 8.9 RegionalReviewPolicyVersion

用于保存当地审核规则，不把规则硬编码在 UI 或 Prompt 中。

建议字段：

- id；
- market_code；
- audience_segment；
- platform；
- version；
- parent_version_id；
- prohibited_rules_json；
- mandatory_review_rules_json；
- escalation_rules_json；
- required_reviewer_roles_json；
- evidence_requirements_json；
- effective_from；
- effective_to；
- content_hash；
- status；
- approved_by；
- approved_at；
- created_at。

### 8.10 RegionalBaseline

只有该对象锁定后，区域版本才能进入正式生产。

建议字段：

- id；
- project_id；
- adaptation_run_id；
- market_code；
- story_version_id；
- story_bible_version_id；
- script_version_id；
- character_adaptation_ids_json；
- visual_bible_version_id；
- adaptation_plan_version_id；
- diff_set_id；
- review_policy_version_id；
- review_record_ids_json；
- baseline_hash；
- status；
- locked_by；
- locked_at。

锁定后不得原地修改；任何返工都创建新区域版本和新 Baseline。

### 8.11 RegionalPerformanceSnapshot

建议字段：

- id；
- project_id；
- regional_baseline_id；
- export_id；
- platform；
- market_code；
- audience_segment；
- period_start；
- period_end；
- traffic_source；
- impressions；
- views；
- completion_rate；
- interaction_rate；
- share_rate；
- conversion_rate；
- dropoff_points_json；
- sample_size；
- source；
- raw_evidence_asset_id；
- imported_by；
- imported_at；
- content_hash。

不同平台指标口径可能不同，必须保留原始字段、归一化字段和口径版本。

### 8.12 RegionalFeedbackItem

建议字段：

- id；
- performance_snapshot_id；
- source_type；
- source_reference；
- feedback_type；
- sentiment；
- severity；
- summary；
- evidence_json；
- episode_id；
- scene_id；
- character_id；
- script_line_id；
- diff_item_id；
- verified；
- verified_by；
- verified_at；
- created_at。

feedback_type 建议包括：

- PRAISE；
- CONFUSION；
- CULTURAL_MISMATCH；
- CONTROVERSY；
- PACING；
- CHARACTER；
- RELATIONSHIP；
- DIALOGUE；
- ENDING；
- PLATFORM_POLICY。

### 8.13 AdaptationOutcomeObservation

它表达“观察到的关联”，不表达已证实因果。

建议字段：

- id；
- regional_baseline_id；
- diff_item_id；
- performance_snapshot_id；
- comparison_baseline_id；
- metric_key；
- observed_delta；
- sample_size；
- confidence_score；
- confounders_json；
- interpretation；
- status；
- reviewed_by；
- reviewed_at；
- created_at。

### 8.14 CultureProfileUpdateProposal

建议字段：

- id；
- profile_id；
- base_profile_version_id；
- proposed_patch_json；
- rationale_json；
- supporting_feedback_ids_json；
- supporting_observation_ids_json；
- sample_summary_json；
- confidence_score；
- causality_warning；
- risk_level；
- status；
- decision；
- reviewed_by；
- reviewed_at；
- created_at。

批准逻辑：

1. 校验 base_profile_version_id 仍是有效基线；
2. 应用 proposed_patch_json；
3. 创建新的 RegionalCultureProfileVersion；
4. 记录 parent_version_id 和来源 Proposal；
5. 不修改历史 Profile Version；
6. 新版本只影响后续 Adaptation Run。

## 9. 工作流

### 9.1 Workflow Type

建议新增：

**REGIONAL_ADAPTATION_V1**

### 9.2 Workflow Node

建议节点：

| Node Key | 输入 | 输出 | 失败处理 |
|---|---|---|---|
| PROFILE_SNAPSHOT | Profile Version | 固定画像快照 | 阻断 |
| ADAPTATION_CONTRACT | Canonical Versions、Profile | Adaptation Plan | 等待 RA2 |
| ADAPT_STORY | Plan、Canonical Story | Regional Story/Bible | 重试或返工 |
| ADAPT_CHARACTERS | Plan、Canonical Characters | Character Adaptations | 重试或返工 |
| ADAPT_SCRIPT | Regional Story、Canonical Script | Regional Script | 重试或返工 |
| COMPUTE_DIFF | Canonical、Regional | Diff Set/Items | 阻断 |
| CULTURAL_QC | Diff、Profile、Review Policy | Quality Checks | 高风险转人工 |
| LOCAL_REVIEW | Diff、Regional Versions | Review Records | 等待 RA3 |
| LOCK_REGIONAL_BASELINE | 已批准版本 | Regional Baseline | 阻断 |
| INGEST_PERFORMANCE | Platform Data | Performance Snapshot | 可延后 |
| ANALYZE_OUTCOME | Snapshot、Diff | Outcome Observations | 低置信度保留 |
| PROPOSE_PROFILE_UPDATE | Feedback、Observations | Update Proposal | 等待 RA4 |
| PROFILE_UPDATE_REVIEW | Proposal | Profile vN+1 或拒绝 | 人工决定 |

节点仍使用现有通用状态：

**BLOCKED → READY → QUEUED → RUNNING → WAITING_REVIEW → SUCCEEDED**

异常状态：

**RETRY_WAIT、FAILED、CANCELLED、SKIPPED、FALLBACK_SUCCEEDED**

### 9.3 Run Phase

建议在 RegionalAdaptationRun.status 使用：

- DRAFT；
- PROFILE_LOCKED；
- PLAN_PENDING_REVIEW；
- ADAPTING；
- DIFF_READY；
- LOCAL_REVIEW_PENDING；
- CHANGES_REQUESTED；
- REJECTED；
- APPROVED；
- REGIONAL_BASELINE_LOCKED；
- PUBLISHED；
- FEEDBACK_COLLECTING；
- FEEDBACK_READY；
- PROFILE_UPDATE_PENDING；
- CLOSED。

## 10. 审核门禁

### 10.1 RA1 区域画像确认

触发条件：

- 新市场；
- 新受众或新平台；
- 画像置信度低于阈值；
- 画像已过有效期；
- 画像规则存在冲突。

审核人：

- 区域负责人；
- 研究负责人；
- 必要时当地文化顾问。

决策：

- APPROVE；
- REQUEST_EVIDENCE；
- REQUEST_CHANGES；
- REJECT。

### 10.2 RA2 适配合同确认

审核内容：

- Story DNA 不变量；
- 可变范围；
- 是否允许改变角色关系；
- 是否允许改变结局；
- 最大风险和成本边界；
- 必须当地人工确认的项目。

审核人：

- IP/创意负责人；
- 制片负责人。

未批准 Adaptation Plan 不得启动区域剧本生成。

### 10.3 RA3 当地内容审核

审核包必须包含：

- 画像版本；
- 适配计划；
- 规范版本；
- 区域版本；
- Diff；
- 自动检查结果；
- 下游影响；
- 预计返工成本。

决策：

- APPROVE；
- APPROVE_WITH_OVERRIDE；
- REQUEST_CHANGES；
- REJECT。

Override 必须记录：

- 被覆盖的规则；
- 原因；
- 风险；
- 审核人；
- 是否仅本项目有效。

### 10.4 RA4 画像学习审核

审核内容：

- 样本量是否足够；
- 指标口径是否一致；
- 是否存在流量、投放、演员、发布时间等混杂因素；
- 反馈是否能回链到具体适配项；
- 修改建议是否会强化刻板印象；
- 新规则的有效期和置信度。

只有 RA4 批准后才能创建画像新版本。

## 11. Diff 规则

### 11.1 匹配规则

比较时优先使用稳定业务键：

- character_key；
- relationship_key；
- episode_ordinal；
- scene_key；
- line_key；
- beat_key；
- location_key；
- prop_key。

不得仅依赖数据库行 ID 或文本顺序。

### 11.2 风险分级

| 风险 | 典型变化 | 默认处理 |
|---|---|---|
| P0 | IP 身份、权利边界、核心设定、强制 Story Beat | 阻断，除非建立新规范版本 |
| P1 | 结局、主冲突、主角目标、核心人物关系 | IP 负责人和当地审核双确认 |
| P2 | 职业、地点、节奏、冲突触发、对白语气 | 当地审核 |
| P3 | 称谓、措辞和轻量视觉调整 | 可批量确认 |

### 11.3 不变量检查

以下变化默认触发 P0：

- 删除核心主角；
- 改变核心主角目标；
- 改变不可变 IP 关系；
- 删除必需 Story Beat；
- 突破权利或授权边界；
- 将规范结局改为适配合同未允许的结局。

### 11.4 下游影响规则

| Diff 类型 | 必须失效或重建 |
|---|---|
| 台词内容或语气 | TTS、字幕、Lip Sync、受影响混音 |
| 角色姓名或称谓 | Script、字幕、TTS、发音词典 |
| 角色关系或动机 | Story Bible、Outline、Script、Storyboard |
| 角色外观 | Look、参考图、关键帧、视频 Take |
| 地点或道具 | Location/Prop Version、Shot Spec、图片和视频 |
| 节奏或时长 | Script Timing、Storyboard、Animatic、Timeline、BGM |
| 结局或核心冲突 | 后续全部 Story、Script 和 Production 节点 |

未受影响资产必须保持 Hash 不变。

## 12. 反馈回写机制

### 12.1 数据导入

支持首版手工 CSV/JSON 导入，后续再接平台 API。

导入必须带：

- 区域 Baseline；
- Export；
- 市场；
- 平台；
- 时间窗口；
- 流量来源；
- 样本量；
- 指标口径；
- 原始证据。

### 12.2 归一化

不同平台指标必须映射到统一指标字典，同时保存平台原始值。

建议统一指标：

- completion_rate；
- interaction_rate；
- share_rate；
- conversion_rate；
- early_dropoff_rate；
- dispute_rate。

### 12.3 反馈绑定

反馈可以绑定到：

- 整个区域 Baseline；
- Episode；
- Scene；
- Story Beat；
- Character；
- Relationship；
- Script Line；
- AdaptationDiffItem。

无法绑定到具体对象的反馈只作为整体市场信号，不能自动用于字段级画像更新。

### 12.4 分析与提案

系统可以生成：

- 可能有效的适配；
- 可能失败的适配；
- 争议集中点；
- 需要进一步实验的假设；
- 建议保持、弱化、强化或废弃的画像规则。

分析结果必须显示：

- 样本量；
- 对照基线；
- 置信度；
- 混杂因素；
- 因果警告。

### 12.5 画像更新

更新顺序：

**Feedback → Observation → Update Proposal → RA4 Review → Profile vN+1**

禁止：

- Feedback 直接写 Profile；
- 单次爆款直接成为地区规则；
- 单条负面评论直接进入禁忌；
- 低样本量结果自动影响下一批项目。

## 13. API 草案

### 13.1 区域画像

- POST /api/v1/regional-culture-profiles
- GET /api/v1/regional-culture-profiles
- GET /api/v1/regional-culture-profiles/{profile_id}
- POST /api/v1/regional-culture-profiles/{profile_id}/versions
- GET /api/v1/regional-culture-profiles/{profile_id}/compare/{left_version}/{right_version}
- POST /api/v1/regional-culture-profile-versions/{version_id}/approve

### 13.2 区域适配

- POST /api/v1/projects/{project_id}/regional-adaptations
- GET /api/v1/projects/{project_id}/regional-adaptations
- GET /api/v1/regional-adaptations/{run_id}
- POST /api/v1/regional-adaptations/{run_id}/adaptation-plans
- POST /api/v1/adaptation-plans/{plan_id}/approve
- POST /api/v1/regional-adaptations/{run_id}/generate
- GET /api/v1/regional-adaptations/{run_id}/diff

### 13.3 当地审核

- GET /api/v1/regional-adaptations/{run_id}/review-package
- POST /api/v1/regional-adaptations/{run_id}/review
- POST /api/v1/regional-adaptations/{run_id}/lock-baseline

### 13.4 表现反馈

- POST /api/v1/regional-baselines/{baseline_id}/performance-snapshots
- GET /api/v1/regional-baselines/{baseline_id}/performance
- POST /api/v1/performance-snapshots/{snapshot_id}/feedback
- POST /api/v1/regional-baselines/{baseline_id}/analyze-outcomes
- POST /api/v1/regional-culture-profiles/{profile_id}/update-proposals
- POST /api/v1/culture-profile-update-proposals/{proposal_id}/decide

所有创建型接口必须继续使用 Idempotency-Key；所有修改和批准接口必须使用 expected_version 或等价的乐观锁。

## 14. 前端产品面

建议提供四个主要页面。

### 14.1 区域画像库

- 地区、受众和平台筛选；
- 当前版本和历史版本；
- 证据与置信度；
- 新旧版本比较；
- 待审核画像更新提案。

### 14.2 区域适配工作台

- 左侧规范版本；
- 右侧区域版本；
- 中间结构化 Diff；
- 保持不变和允许变化的明确标识；
- 角色关系图和变化说明；
- 下游影响与预计重建成本。

### 14.3 当地审核中心

- 按市场、平台、风险和状态筛选；
- 高风险逐项确认；
- 低风险批量确认；
- Request Changes、Reject 和 Override；
- 审核意见回链到 Diff Item。

### 14.4 表现与学习

- 区域版本表现；
- 关键跳出点；
- 争议和当地反馈；
- 适配项与表现关联；
- 画像更新提案；
- 画像 vN 与 vN+1 比较。

## 15. 迁移与交付计划

### PR-24 / Migration 0019：区域文化画像

范围：

- RegionalCultureProfile；
- RegionalCultureProfileVersion；
- RegionalReviewPolicyVersion；
- 画像 CRUD、比较和批准；
- RA1。

验收：

- 同一市场可创建多个受众/平台画像；
- 历史版本不可变；
- 新版本可比较、批准和回退；
- 适配任务锁定具体画像版本。

### PR-25 / Migration 0020：区域适配与 Diff

范围：

- RegionalAdaptationRun；
- AdaptationPlanVersion；
- ScriptVersion 区域字段；
- CharacterAdaptationVersion；
- AdaptationDiffSet/Item；
- Workflow Nodes；
- RA2。

验收：

- 同一规范 Script 可生成两个区域版本；
- 规范版本 Hash 不变；
- Diff 可确定性重建；
- 每个变化有原因、文化规则、风险和影响范围。

### PR-26：当地审核与区域基线

范围：

- 当地审核包；
- RA3；
- Request Changes、Reject 和 Override；
- RegionalBaseline；
- 与 Storyboard、Media Workflow 和 Export Gate 联动。

验收：

- 未批准区域版本不能进入正式生产；
- Override 有完整审计；
- 返工创建新版本而不是覆盖旧版本；
- 锁定 Baseline 可刷新恢复。

### PR-27 / Migration 0021：表现反馈与画像学习

范围：

- RegionalPerformanceSnapshot；
- RegionalFeedbackItem；
- AdaptationOutcomeObservation；
- CultureProfileUpdateProposal；
- RA4；
- 画像 vN+1 生成。

验收：

- 可导入带样本量和时间窗口的表现数据；
- 反馈可绑定 Scene、Character、Line 和 Diff Item；
- 系统只生成更新提案，不直接修改画像；
- 批准后创建新画像版本；
- 历史适配任务仍引用旧画像版本。

## 16. 测试策略

### 16.1 数据与迁移

- 空库升级至最新 Head；
- 0018 顺序升级到 0019、0020、0021；
- 旧 Project 无区域数据时仍可正常运行；
- Profile、Plan、Script、Diff 和 Baseline 不可变；
- 所有 Content Hash 稳定；
- Alembic Check 无漂移。

### 16.2 合同测试

- 画像 Schema 校验；
- 适配计划必须包含 Invariants；
- REGIONAL Script 必须引用 Canonical Script；
- Diff Item 必须引用稳定 Entity Key；
- 高风险变化必须进入审核；
- 未批准 Baseline 不得进入生产；
- Feedback 不得直接修改 Profile。

### 16.3 Workflow

- 新画像等待 RA1；
- Plan 等待 RA2；
- 当地审核等待 RA3；
- Request Changes 后只重跑受影响节点；
- Profile Update 等待 RA4；
- Job 重试仍使用相同画像快照；
- 服务重启后状态和 Gate 可恢复。

### 16.4 Diff

- 相同输入产生相同 Diff Hash；
- 规范不变量变化触发 P0；
- 台词变化只失效相关音频、字幕和口型；
- 角色 Look 变化失效相关图片和视频；
- 未受影响资产 Hash 保持不变。

### 16.5 浏览器 Smoke

1. 创建或选择区域画像；
2. 选择规范 Script 和目标市场；
3. 确认适配合同；
4. 生成区域 Script 和 Character 版本；
5. 查看结构化 Diff；
6. 请求一次返工；
7. 当地审核并锁定区域 Baseline；
8. 导入表现数据和当地反馈；
9. 创建画像更新提案；
10. 批准生成画像 vN+1；
11. 刷新页面，确认全部版本、审核和证据恢复。

## 17. 风险与控制

| 风险 | 影响 | 控制 |
|---|---|---|
| 文化画像固化刻板印象 | 内容失真或冒犯 | 证据、有效期、置信度、当地审核 |
| 模型擅自改变 Story DNA | IP 失控 | Adaptation Contract、P0 不变量门禁 |
| 地区规则快速过时 | 适配失效 | Profile Version、有效期和反馈更新 |
| 将相关性误判为因果 | 错误学习 | 对照基线、样本量、混杂因素和人工审核 |
| Diff 只做文本比较 | 无法计算影响 | 稳定 Entity Key、字段级和语义级 Diff |
| 区域版本覆盖规范版本 | 历史和 IP 丢失 | Canonical/Regional 血缘和不可变版本 |
| 反馈直接污染画像 | 下一批内容系统性偏移 | Update Proposal + RA4 |
| 区域版本数量爆炸 | 成本和管理失控 | 主市场优先、复用 Baseline、批量策略 |
| 当地审核成为瓶颈 | 上线周期延长 | 风险分级、低风险批量确认、高风险逐项确认 |

## 18. 待确认决策

进入 PR-25 前需要确认：

1. 首批试点市场、受众和平台；
2. Story DNA 默认不可变量；
3. 是否允许区域版本改变结局；
4. 当地审核人的角色和责任边界；
5. 首版表现数据采用手工导入还是平台 API；
6. 最低样本量与置信度阈值；
7. 地区画像是否项目私有、组织共享或平台级共享；
8. 哪些画像证据允许跨项目复用；
9. 是否需要双语对照和当地语言回译；
10. 如何处理同一区域内不同群体之间的偏好冲突。

不明确时建议默认：

- 先试点一个主市场和一个对照市场；
- 不允许改变核心结局和主角目标；
- 表现数据先使用手工 CSV/JSON 导入；
- 画像更新必须人工批准；
- 画像按市场 × 受众 × 平台拆分；
- 所有区域版本保留规范语言回译摘要。

## 19. 完成定义

区域文化适配闭环完成必须满足：

- 区域文化画像可版本化、比较、批准和回退；
- 每次适配锁定确切画像版本；
- 规范 Story、Script 和 Character 永不被区域版本覆盖；
- 同一规范 Script 可生成多个独立区域版本；
- 每项变化有原因、依据、风险和影响范围；
- 当地审核支持通过、拒绝、返工和人工覆盖；
- 只有锁定的 RegionalBaseline 能进入正式生产和导出；
- 表现反馈可回链到区域 Baseline 和具体 Diff Item；
- 反馈只产生画像更新提案；
- 批准提案后创建画像新版本；
- 历史项目继续引用原画像版本；
- 服务重启、页面刷新和任务重试不破坏版本血缘；
- 无外部 Provider Key 时仍可通过确定性 Mock 完成整条闭环。

