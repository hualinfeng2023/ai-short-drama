# DirectorIntent v1 与变更预览

## 结论

当前系统已经具备 DirectorIntent v1 的可确认编译链路。它复用现有
Director Proposal / ChangeSet，不新增事实源；时间线、分镜、实际生成提示词和
音频均通过同一 intent 版本产生可验证回执。

| 能力 | 当前状态 | 代码证据 |
| --- | --- | --- |
| 选中 `ScriptScene` / `Scene` 发起审查 | 已实现 | `server/app/domain/director.py` 的 `DirectorProposalRequest` |
| 输出 2–3 个可执行的 Scene / Line 修改方案 | 已实现 | `DirectorReviewOutput`、`DirectorOption`、`DirectorProposedChange` |
| 人工确认后执行，支持批准、拒绝、回滚 | 已实现 | `server/app/api/v1/director.py` |
| 影响分析、`ChangeSet`、版本比较 | 已实现 | `server/app/services/domain_commands.py` |
| 正式时间线只读预览 | 部分实现 | `_director_timeline_preview` 目前只覆盖对白、字幕和时长投影 |
| 解析“这里”对应的情节点、人物目标和时间范围 | 已实现 | `server/app/services/director_intent.py` 的 context resolver |
| 统一叙事、摄影、表演、声音、节奏意图 | 已实现 | `DirectorIntentCompilationOutput` 与五通道 preview |
| 时间线继承同一意图版本 | 已实现 | 确认后写入 `TIMELINE / INHERITED` 消费回执 |
| 分镜、提示词、音频继承同一意图版本 | 已实现 | `director_intent_consumption.py`、`ShotSpec.prompt_json`、`AudioCue.payload_json` 与 `GenerationRecord.director_intent_json` |
| 电影术语的情境证据与冲突门禁 | 已实现 | evidence refs、置信度、blocking conflict 与 fail-closed |
| 用户可见修改预览与确认 | 已实现 | `DirectorReviewCard` 的五通道、冲突和继承状态面板 |

`DirectorIntent` 已建立在现有 canonical state、`ChangeSet` 和 Film IR 之上，
作为版本化的意图附着与变更预览；它不是新的 Story / Scene / Shot 事实源。

## 首版承重原则

1. 先解析作用域，再生成专业表达。`RESOLVED` 必须同时绑定情节点、人物目标、
   场景和时间范围；不能确定时返回 `AMBIGUOUS` 或 `UNRESOLVED`，不得猜测。
2. 每个叙事、摄影、表演、声音、节奏结论必须引用当前 canonical state / Film IR
   的证据。专业术语没有证据或可观察效果时，不得进入可确认状态。
3. `BLOCKING` 冲突只有 `PASS` 才能确认。`UNKNOWN` 与 `FAIL` 都应 fail closed。
4. 确认的是 `intent_id + intent_version + context_fingerprint`。上游版本变化后，
   意图进入 `STALE`，不能静默传播。
5. 分镜、提示词、音频和时间线必须声明继承目标。每个消费者都要写回产物 ID、
   消费快照哈希和对应实体 ID；仅检查字段存在不等于证明意图真的生效。

## Schema

可执行 Pydantic 定义位于
`server/app/domain/director_intent.py`。顶层对象为：

- `DirectorIntent`
  - 用户原话与语言
  - `scope`：场景、情节点、人物目标、时间范围、候选目标、上下文指纹
  - `evidence`：事实来源、字段路径、版本、哈希、主张与置信度
  - `directives`：叙事、摄影、表演、声音、节奏五个固定通道
  - `rationale`：这些变化为什么服务当前情境
  - `conflict_checks`：角色锁定、世界规则、因果、范围、时间、跨通道、术语落地
  - `inheritance_targets`：分镜、提示词、音频、时间线
  - `state`、`can_confirm` 与阻断原因
- `DirectorIntentChangePreview`
  - 固定为 `READ_ONLY`
  - canonical source 固定为 `FILM_IR`
  - 五个通道的修改前、修改后、理由、置信度与证据引用
  - 明确保持不变的事实
  - 确认后的下游继承摘要

## “这里更紧张”的预览结构

```text
作用范围
  情节点：主角必须阻止对方离开
  人物目标：阻止离开，不是解释前因
  时间段：00:42.000–00:50.000
  解析置信度：0.81

修改预览
  叙事：冲突提前，删除不影响因果成立的解释
  摄影：中景推进到近景，减少人物周围负空间
  表演：缩短反应停顿，增加急促呼吸与克制手部动作
  声音：降低环境声，加入克制的低频压力层
  节奏：主要镜头从 4 秒压缩到 2.5 秒

为什么有效
  行动更早发生，视听窗口同时收紧，减少解释造成的泄压；
  人物目标、角色身份和世界规则保持不变。

冲突检查
  角色锁定：通过
  世界规则：通过
  术语落地：通过

确认后
  分镜、提示词、音频、时间线读取同一 intent_id / intent_version；
  当前预览不修改正式时间线，也不触发媒体生成。
```

## 首版不做什么

- 不新增独立 Canvas / Director 状态源。
- 不在消费者尚未产生产物时声称“已经继承”；确认后先标记 `PENDING`。
- 不让模型仅靠术语风格判断可信度。
- 不把摄影或声音意图偷偷塞入现有 Scene / Line 文本补丁。
- 不因定义契约而触发图片、视频、配音或音乐生成。

## 当前实现边界

- `CharacterGoal` 由 canonical ScriptVersion payload 持有并投影进 Film IR；
  缺少明确人物目标时，意图预览会阻断确认，不让模型补猜。
- 确认令牌绑定 `intent_id`、`intent_version`、预览内容与
  `context_fingerprint`；执行前重新解析上下文，上游变化会返回
  `DIRECTOR_INTENT_STALE_CONTEXT`。
- 意图编译成功与失败分别记录为 `DIRECTOR_INTENT_COMPILATION`；
  provider 合同失败不会创建 ChangeSet。
- `director_intent_consumption.py` 只选择当前批准剧本血缘链上、已确认且离当前
  版本最近的场景意图；其他分支或其他场景不会被静默传播。
- 分镜规划把 `STORYBOARD` 快照写入 Storyboard / ShotSpec；实际出图时从持久化
  ShotSpec 重建提示词，并把 `PROMPT` 快照写入 GenerationRecord。
- 声音规划把 `AUDIO` 快照写入 SoundBrief / AudioCue；正式音频生成记录继续
  保存同一快照。
- 确认后下游回执先为 `PENDING`；只有生成事务成功并取得 Storyboard、
  ShotSpec 或 AudioCue 证据后，才更新为 `INHERITED`。
- Film IR 通过 `DIRECTS_STORYBOARD`、`DIRECTS_GENERATION_PROMPT` 和
  `DIRECTS_AUDIO_CUE` 显式展示已确认意图到下游产物的血缘。
