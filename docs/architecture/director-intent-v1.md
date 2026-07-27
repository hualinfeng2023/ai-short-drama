# DirectorIntent v1 与变更预览

## 结论

当前系统有可用的 Director 审查基础，但没有完整的导演意图编译器。

| 能力 | 当前状态 | 代码证据 |
| --- | --- | --- |
| 选中 `ScriptScene` / `Scene` 发起审查 | 已实现 | `server/app/domain/director.py` 的 `DirectorProposalRequest` |
| 输出 2–3 个可执行的 Scene / Line 修改方案 | 已实现 | `DirectorReviewOutput`、`DirectorOption`、`DirectorProposedChange` |
| 人工确认后执行，支持批准、拒绝、回滚 | 已实现 | `server/app/api/v1/director.py` |
| 影响分析、`ChangeSet`、版本比较 | 已实现 | `server/app/services/domain_commands.py` |
| 正式时间线只读预览 | 部分实现 | `_director_timeline_preview` 目前只覆盖对白、字幕和时长投影 |
| 解析“这里”对应的情节点、人物目标和时间范围 | 缺失 | 当前 `_scene_context` 只有场景与台词上下文 |
| 统一叙事、摄影、表演、声音、节奏意图 | 缺失 | 当前补丁白名单只覆盖 Scene / Line 字段 |
| 分镜、提示词、音频、时间线继承同一意图版本 | 缺失 | 当前无 `intent_id` / `intent_version` / 消费回执 |
| 电影术语的情境证据与冲突门禁 | 缺失 | 当前只有目标 ID 与字段白名单校验 |

因此，`DirectorIntent` 应当建立在现有 canonical state、`ChangeSet` 和 Film IR
之上，作为版本化的意图附着与变更预览；它不能成为新的 Story / Scene / Shot
事实源。

## 首版承重原则

1. 先解析作用域，再生成专业表达。`RESOLVED` 必须同时绑定情节点、人物目标、
   场景和时间范围；不能确定时返回 `AMBIGUOUS` 或 `UNRESOLVED`，不得猜测。
2. 每个叙事、摄影、表演、声音、节奏结论必须引用当前 canonical state / Film IR
   的证据。专业术语没有证据或可观察效果时，不得进入可确认状态。
3. `BLOCKING` 冲突只有 `PASS` 才能确认。`UNKNOWN` 与 `FAIL` 都应 fail closed。
4. 确认的是 `intent_id + intent_version + context_fingerprint`。上游版本变化后，
   意图进入 `STALE`，不能静默传播。
5. 分镜、提示词、音频和时间线必须声明继承目标。后续接入时，每个消费者还要
   写回独立消费证据；仅检查字段存在不等于证明意图真的生效。

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
- 不在 Schema 尚未接入消费者时声称“已经共同继承”。
- 不让模型仅靠术语风格判断可信度。
- 不把摄影或声音意图偷偷塞入现有 Scene / Line 文本补丁。
- 不因定义契约而触发图片、视频、配音或音乐生成。
