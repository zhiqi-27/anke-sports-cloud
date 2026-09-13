# Anke Sports Cloud

体育赛程、个人配置、原始观看链接与ICS的权威业务服务。客户端在独立仓`anke-sports`。

目标：Firebase认证、Python/FastAPI、Azure Functions、Cosmos NoSQL **Serverless + Periodic**、Storage Queue/timer。开发和生产都从该模式开始。独立 Azure 开发环境已部署，Cosmos 个人关注/Feed 路径已连通 Mac Apple 日历；SQL/SQLite 保留为本地基线，其余文档模块仍有适配与验收缺口。详见[当前状态](STATE.md)与[架构](docs/architecture.md)。

## 本地运行

需要Python 3.12与uv。首次准备：

```sh
uv sync --frozen
```

尚无`.env`时，从`.env.example`复制并按说明配置；不要覆盖已有本机凭据。分别启动API和worker：

```sh
ANKE_SPORTS_ENV=local ANKE_SPORTS_LOCAL_PREVIEW=true uv run uvicorn app.main:app --host 127.0.0.1 --port 8787 --no-access-log
ANKE_SPORTS_ENV=local ANKE_SPORTS_LOCAL_PREVIEW=true uv run python -m app.worker
```

这是两个终端中的命令。仅监听loopback，SQLite与体验身份仅用于本机。前端默认3000通过同源代理访问。私人Feed令牌是只读秘密，不应进入访问日志或Git。

## 实现边界

| 路径 | 已有实现与证据 | 仍未完成 |
| --- | --- | --- |
| SQL基线 | 赛程/关注/创作者/个人与公共Feed、直播维护、OAuth/MCP、账号生命周期 | 真实手机、Hub长期更新、云端全链路 |
| 文档模式 | 日历/关注/个人链接/配置/ICS、Provider、创作者/轮询/匹配/人工确认、WebSub | 公共Feed、直播、OAuth/MCP、账号删除等完整适配；核心关注与个人Feed已有真实Cosmos链路证据 |
| Azure | 独立开发资源、HTTPS、Firebase 登录、云任务更新及个人 Feed 至 Mac 日历 | 云故障与 Periodic 恢复演练、告警、持续更新与手机验收 |

[3008合成创作者](http://localhost:3008/creators)可检查个人链接与ICS；公共订阅尚未开放。3008后端是创作者批次，最新WebSub实验已退出。这是历史本地入口，当前存活状态须重新检查；公网进展见 STATE。

## 检查与证据

```sh
uv run ruff check .
uv run pytest -q
uv run python -m scripts.export_contracts
```

仅在契约变化时导出并在客户端运行`npm run contracts`。完整OpenAPI由SQL基线导出，不能以文档模式接口子集覆盖。锁定依赖见`uv.lock`，Functions打包使用`requirements.txt`。

当前本地P1工作树完整回归为374 passed / 2 skipped（2026-09-13，本地SQL/文档适配器测试）；尚未部署或push。既有[361项WebSub阶段记录](evidence/document-websub-2026-09-10.md)保留为当时证据。其他主要证据：

- [真实F1样本及文档Provider](evidence/document-providers-2026-09-10.md)
- [P1三运动与内容最小矩阵](evidence/p1-core-matrix-2026-09-13.md)、[开发环境部署](evidence/p1-deployment-2026-09-13.md)、[客户端支持矩阵](docs/support-matrix.md)
- [创作者合成链路](evidence/document-creators-2026-09-10.md)、[真实YouTube读取](evidence/youtube-live-2026-09-10.md)
- [Codex实际业务调用](evidence/codex-business-2026-09-10.md)、[Firebase专用身份生命周期](evidence/firebase-lifecycle-2026-09-10.md)
- [最新Web/ICS实操](../anke-sports/output/playwright/lean-check-2026-09-10.md)

## 开发与部署

先让一项真实赛事和创作者通过个人ICS可用，再补该路径必需的独立开发环境与手机验证。不要为了完整架构先实施所有迁移、GC或容量项目。未实现的后台能力仍需明确提示，不能假报可用。

[云配置现状](docs/cloud-development.md)、[Cosmos设计](docs/cosmos-storage-design.md)、[文档运行边界](docs/document-runtime.md)、[Functions运行](docs/functions-runtime.md)、[MCP](docs/mcp-and-connections.md)。旧MySQL模板/费用文档只作历史记录，不能直接执行。已授权的托管Chrome方式保留，具体部署必须使用核对过的独立目标，禁止复用其他产品资源。

模块手册与必要验收保留在`docs/`、`evidence/`；旧STATE流水已删除，下一步只看当前STATE。双仓分别提交，无push或部署。
