# ADR-0001：Run-first 本地 MVP 基线

- 状态：Accepted
- 日期：2026-07-13

## 决策

首版固定为单用户、单 Project 验证集、1–3 个 Scene、6–10 个 Shot。运行栈为 React/Vite SPA、FastAPI、SQLite WAL、本地内容寻址资产目录和单持久化 Worker；Docker Compose 提供无外部 Key 的一键启动。

确定性 Mock 是默认 Provider，必须生成真实 PNG、MP4、SRT、VTT 与 Manifest。FFmpeg 输出 720p、24fps、H.264/AAC。真实 Seedream/Seedance 仅作为服务端可选适配器，缺少 Key 时不得影响 Mock 主闭环。

所有异步命令先持久化，再由带 lease、heartbeat、retry、cancel 和重启恢复的 Worker 执行。Approved Take 与 Timeline 不覆盖；Revision 创建新版本，Export 回链批准的 baseline hash。积分账本 append-only，失败/取消释放预留。

参考素材仅接受原始文件流，不接受远程 URL；按类型限制大小并验证 magic、路径、压缩包、图像尺寸或 ffprobe 结果，从而不引入 SSRF 与路径穿越面。

## 后果

- 本地演示可重复、可测试，不依赖付费或不稳定外部服务。
- SQLite/单 Worker 不用于多租户和公网高并发；扩展前必须另立 ADR。
- Mock、临时资产和基础权利预检必须在 UI、数据库与 Manifest 中明确标识。
- 多集、团队、支付、发布中心、专业 NLE、全片真实视频与公网 Beta 均不在本 ADR 范围。
