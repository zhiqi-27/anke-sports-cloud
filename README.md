# Anke Sports Cloud

Anke Sports 1.0：关注球队/车队或手动添加单场比赛，通过个人日历查看赛程并配置比赛直播入口。手动单场可删除，关注自动加入的比赛不可单删；Web与MCP共用规则。手动来源与事件私有视图契约已落地，增删行为待后续 B/C 流程实现与验收。仅Google登录，不提供微信、YouTube内容添加或AI匹配。

当前定义以[1.0定义](<../anke-sports 文档/Anke_Sports_1.0定义.md>)为准；旧内容能力说明仅作历史，开发不要求1.0前兼容。

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
| SQL基线 | 赛程/关注/个人与公共Feed、直播维护、OAuth/MCP、账号生命周期；历史视频表仅作追溯存储，当前运行时不加载 | 手动单场增删、真实手机、Hub长期更新、云端全链路 |
| 文档模式 | 日历/关注/个人链接/配置/ICS、Provider；历史视频任务不再进入 active claim | 直播、OAuth和账号删除代码已部署；公共Feed保留为白名单为空的停用实现，真实业务签收及公网MCP闭环待完成 |
| Azure | 独立开发资源、HTTPS、Firebase 登录、云任务更新及个人 Feed 至 Mac 日历 | 云故障与 Periodic 恢复演练、告警、持续更新与手机验收 |

3008是历史文档模式入口，创作者页面已退出产品范围。公共订阅已移出v1，后端代码保留且部署白名单为空；退出范围、保留的历史材料和当前契约边界见[退出能力记录](docs/retired-capabilities.md)及 STATE。

## 检查与证据

```sh
uv run ruff check .
uv run pytest -q
uv run python -m scripts.export_contracts
```

仅在契约变化时导出并在客户端运行`npm run contracts`。完整OpenAPI由SQL基线导出，不能以文档模式接口子集覆盖。锁定依赖见`uv.lock`，Functions打包使用`requirements.txt`。

本轮 A 变更后的本地回归为239 passed / 2 skipped（另有2项依赖弃用警告）；`ruff`、`compileall`、OpenAPI 导出和客户端生成已通过。未部署或push；既有[361项WebSub阶段记录](evidence/document-websub-2026-09-10.md)保留为当时历史证据。其他主要证据：

- [真实F1样本及文档Provider](evidence/document-providers-2026-09-10.md)
- [P1三运动与内容最小矩阵](evidence/p1-core-matrix-2026-09-13.md)、[开发环境部署](evidence/p1-deployment-2026-09-13.md)、[客户端支持矩阵](docs/support-matrix.md)
- 历史视频证据仍保留在 `evidence/`，不作为 1.0 当前能力或验收证据
- [Codex实际业务调用](evidence/codex-business-2026-09-10.md)、[Firebase专用身份生命周期](evidence/firebase-lifecycle-2026-09-10.md)
- [最新Web/ICS实操](../anke-sports/output/playwright/lean-check-2026-09-10.md)

## 开发与部署

先让一项真实赛事及经审核的直播入口通过个人ICS可用，再补该路径必需的独立开发环境与手机验证。不要为了完整架构先实施所有迁移、GC或容量项目。未实现的后台能力仍需明确提示，不能假报可用。

[云配置现状](docs/cloud-development.md)、[Cosmos设计](docs/cosmos-storage-design.md)、[文档运行边界](docs/document-runtime.md)、[Functions运行](docs/functions-runtime.md)、[MCP](docs/mcp-and-connections.md)。旧MySQL模板/费用文档只作历史记录，不能直接执行。具体部署必须使用核对过的独立目标，禁止复用其他产品资源。Chrome扩展已退出产品范围，不再准备相关发布配置。

模块手册与必要验收保留在`docs/`、`evidence/`；旧STATE流水已删除，下一步只看当前STATE。提交、push与部署分别见STATE及发布证据。

## P2 文档模式已部署开发环境（2026-09-13）

账号删除、OAuth、停用保留的公共Feed与官方转播已接入文档模式并部署开发环境；运行方式沿用现有API与worker命令。见 [实现与部署边界](docs/p2-cloud-business.md)及[本地验收](evidence/p2-local-2026-09-13.md)。已按用户授权部署到现有开发环境；[部署记录](evidence/p2-deployment-2026-09-13.md)区分代码部署、配置开放与真实账号/播放验收。

当前介绍见[项目介绍](<../anke-sports 文档/Anke_Sports_项目介绍.md>)。线上仍含旧版本能力；本地候选版本已经关闭视频产品入口和投递路径，尚未部署。最新状态以STATE为准。
