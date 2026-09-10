# Anke Sports · 服务端状态

更新：2026-09-10。本地MVP可试用，尚未云端公测。用户已解锁。[总进度](../STATE.md)。

## 运行与实现边界

- SQL基线：主API8787/PID44497；Firebase独立API8788/PID44563。均在本次只读核验返回200。本机SQLite，不是云数据库。
- 文档模式：3007/PID94103为Provider/F1样本，3008/PID1760为创作者合成预览，均健康200。前端借用3002；后端版本与主SQL分开。
- 现有SQL基线包含公共Feed、直播维护、账号生命周期与OAuth/MCP；文档模式已接入日历/关注/配置/个人链接/个人ICS、Provider、创作者轮询/匹配/人工确认与WebSub，但仍缺公共Feed、直播、账号删除、OAuth/MCP等完整适配。
- 3008未重启到最新WebSub代码；最新独立WebSub实验已停止。其他worker历史句柄本次未复核，不作存活承诺。没有迁移主库或重启服务。

## 现有证据

最近业务代码b86749e；后续1f1d261仅同步UI检查。最近完整后端记录361 passed / 2 skipped / 2既有警告，见[WebSub证据](evidence/document-websub-2026-09-10.md)。本次文档更新没有重跑这些测试。

[Codex实际业务25项](evidence/codex-business-2026-09-10.md)在合成身份/SQL下通过；[Firebase专用身份27项](evidence/firebase-lifecycle-2026-09-10.md)、[YouTube真实读取](evidence/youtube-live-2026-09-10.md)、[F1文档Provider](evidence/document-providers-2026-09-10.md)各有独立环境证据，不合并为真实云端全链路。

[最新UI/ICS](../anke-sports/output/playwright/lean-check-2026-09-10.md)确认3008个人日历下载12条唯一事件及合成原链接。公共Feed仍未开放，前端提示不等于后端适配完成。

## 云目标与下一步

Firebase + FastAPI/Azure Functions + Cosmos NoSQL **Serverless + Periodic** + Queue/timer。开发/生产同一初始容量策略；MySQL仅旧方案与本地迁移基线。模板validate有记录，Azure资源与远端部署按现有交付记录尚未执行，本次未查询云端。

先收敛一个真实赛事/创作者到个人ICS的路径，再明确HTTPS、独立开发目标和相应必需适配，验证真实日历更新。不是先补全所有文档存储模块。通用GC、假设性规模工作后置；上线能力必要的权限、保留规则与恢复仍须满足。

[当前架构](docs/architecture.md)、[云接入](docs/cloud-development.md)、[Cosmos设计](docs/cosmos-storage-design.md)、[文档模式边界](docs/document-runtime.md)。无push、云操作或部署；本轮只更新文档。
