# ADR-0002：版本化多模态生产工作流

- 状态：Accepted
- 日期：2026-07-14
- 依赖：ADR-0001

## 背景

ADR-0001 为本地单用户、单集、确定性 Mock 的 Run-first MVP 建立了可靠基线。现已批准的产品流程要求增加多目标 Brief、Story DNA、Story Bible、分集剧本、多角色与视觉圣经、动态 Storyboard、正式图片/视频、配音、口型、BGM、SFX、多轨 Timeline 和多平台导出。

这些能力会扩大 ADR-0001 中“多集、全片真实视频、云对象存储不在范围内”的边界，因此不能通过零散 Provider 调用隐式扩张。

## 决策

### 保留

- React/Vite、FastAPI、SQLAlchemy/Alembic、SQLite WAL、本地内容寻址资产目录；
- 单用户本地工作台；
- 服务端持久化 Job、lease、heartbeat、retry、cancel 和重启恢复；
- 不可变 Version、Take、Timeline、Change Set、Export 和审计；
- 无外部 Key 时完整可运行的确定性 Mock；
- Provider Secret 只存在服务端；
- 未批准候选不能进入正式 Timeline。

### 扩展

- 文本层允许多个 Episode 的 Story Bible、Outline 和 Script Version；
- 媒体生产仍按 Episode 分批执行，默认先批准首集，再每批最多 3 集；
- Worker 演进为 Handler Registry + 持久化 Workflow DAG，但暂不拆微服务；
- Timeline 演进为视频、对白、BGM、环境音、SFX 和字幕多轨；
- Provider 通过统一 Adapter 和 Generation Record 接入；
- 真实图片、视频、语音、音乐和音效必须与 Mock 共用业务合同。

### 视频源图媒体可达性

Seedance 需要能够从云端读取源图，而工作台资产默认保存在本地目录。本 ADR 选择 **Seedream 原始 URL 短时快速路径 + 私有火山 TOS 短期预签名 GET URL** 作为普通镜头关键帧的媒体可达方案：

1. Seedream 原始 HTTPS URL 仅在明确记录的快速路径截止时间内直传 Seedance，默认窗口为 10 分钟；缺少截止时间、无法解析或已经到期时不得继续复用；
2. 快速路径不可用时，Worker 只在服务端把已批准关键帧上传到私有 Bucket，并强制对象 ACL 为 Private；
3. 每个视频 Job 使用独立 Object Key，默认生成 2 小时有效的 HTTPS 预签名 URL；
4. TOS 签名 URL 只在 Worker 内存中交给 Seedance，不写入 Job、Generation Record、日志或浏览器；
5. Seedance 任务结束或取消后立即删除临时对象；对象同时设置 1 天过期作为进程崩溃的清理兜底；
6. 数据库只保存 TOS Bucket、Object Key、源资产 ID/Hash、到期时间、请求 ID 和清理状态；
7. 用户手工填写 URL 仅保留为 Advanced 调试能力，不作为默认产品主路径。

`PROVIDER_MEDIA_STAGING_V1` 只有在 TOS AK、SK、Bucket、Region 和 Endpoint 配置完整时才能打开。Flag 打开但配置不完整时 `/health/ready` 返回 503；Flag 关闭时继续显式记录 `BLOCKED_BY_MEDIA_STAGING` 或采用已声明的静态运镜降级。

## 硬约束

- 不因扩展 Workflow 而原地覆盖已批准资产或版本；
- 不持有数据库写事务等待外部 Provider；
- SQLite 阶段 Worker 默认小并发；达到持续写竞争阈值后另立 PostgreSQL ADR；
- 真人声音克隆默认关闭，必须有明确授权证据；
- 自动发布到平台不属于本 ADR；
- 真实 Provider 测试不进入默认离线 CI，不得输出 Secret；
- 临时、Mock、权利受限或未批准资产必须在 Preview 和 Manifest 中可见，并阻断正式 Export。

## 已接受的阶段性实现与待选型项

Workflow v2 的业务合同、版本实体、Workflow DAG、G1–G5、多轨 Timeline、Rights Preflight 和 Export Matrix 已按本 ADR 实现。为了在真实供应商未确定时仍保持完整、可测试的业务闭环，当前阶段接受以下实现：

- TTS、Lip Sync、Music 和 SFX 采用统一 Adapter 合同与确定性 Mock；每个结果仍写入 Generation Record、权利、QC 和 Review 记录；
- Lip Sync 不可用或失败时保留原视频，并明确记录 `SOURCE_VIDEO_UNCHANGED` / `VOICE_OVER`，不生成伪成功结果；
- 普通镜头关键帧优先使用仍处于 10 分钟窗口内的 Seedream 原始 URL；其余情况采用私有 TOS 与短期预签名 URL，签名凭证不得持久化，清理失败必须进入审计元数据并由 Bucket 生命周期兜底；
- Export Profile 先提供可版本化的默认编码、字幕和响度字段，不将其描述为所有平台的最终官方规范。

下列事项仍需后续 ADR 或供应商选型确认：

- TTS、Lip Sync、Music 和 SFX 的真实 Provider；
- SQLite 并发阈值与迁移 PostgreSQL 的触发指标；
- 各平台正式响度、字幕和编码 Export Profile。

这些选择不阻断已接受的本地确定性闭环；进入对应真实 Provider 的正式生产启用前，必须以补充 ADR 记录供应商、数据出境、权利、失败语义、成本上限和合同测试结果。

## 后果

- 可以在保留当前可运行闭环的同时逐步开放多模态能力；
- 数据模型和迁移数量增加，但每个版本和媒体结果都有可审计血缘；
- SQLite/本地资产仍适合单用户试点，不承诺公网高并发；
- Provider 不稳定或不可用时，系统可以显式降级，不产生 Timeline 空洞或虚假成功。
