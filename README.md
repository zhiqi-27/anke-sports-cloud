# Anke Sports Cloud

体育日历、个人配置与原始观看链接的业务服务。原产品名 SportsCal 已更名为 **Anke Sports**。

Python/FastAPI + Firebase Authentication + Azure Functions + Azure MySQL + Azure Storage Queue。参考 FormaLM 的服务分层，使用独立账号、密钥与资源。`anke-sports` 为独立客户端仓库。

## 本地运行

需要 Python 3.12 与 uv。

```sh
uv sync --frozen
cp .env.example .env
ANKE_SPORTS_ENV=local ANKE_SPORTS_LOCAL_PREVIEW=true uv run uvicorn app.main:app --host 127.0.0.1 --port 8787 --no-access-log
```

另开终端运行持久任务消费：

```sh
ANKE_SPORTS_ENV=local ANKE_SPORTS_LOCAL_PREVIEW=true uv run python -m app.worker
```

`.env.example` 明确启用本机体验模式：SQLite `data/anke-sports.db`、合成演示赛程、本机独立体验身份。启动仅监听 loopback；生产模式禁止 SQLite 和体验身份。不要将此命令作为公网部署方式。

重启本地服务时同样保留上述两个显式参数；缺省配置关闭体验登录。当前运行的完整命令和进程见 [STATE.md](STATE.md)。

前端通过同源 `/api/` 代理连接。Web 默认 127.0.0.1:3000。私人订阅地址通过已登录页面复制，服务访问日志应始终关闭。其令牌仅授权读取已发布的个人 Feed，不是写入凭据。

在设置页手动获取 F1；首次成功后，本地 worker 按数据库中的时间每 6 小时更新已启用的数据源；启动即检查，重启不会重新等待六小时或提前抓取。NBA、足球和 YouTube 的 key 只配置于服务端；不复制 FormaLM 凭据。YouTube 频道确认、后台补查、匹配、人工确认与移除/固定已接入本地流程；真实 API、Hub 与 Azure 尚未联调。详见 [内容链路](docs/content-pipeline.md)。

## 检查与契约

```sh
uv run ruff check .
uv run pytest -q
uv run python -m scripts.export_contracts
```

`contracts/openapi.json` 与 `contracts/config.schema.json` 从 Pydantic 生成。客户端在自己的仓库运行 `npm run contracts`。两仓分别检查、提交、发布，不假定共享 Git 历史。

数据库：本机演示可自动建表；正式环境必须使用迁移。

```sh
uv run alembic upgrade head
uv run alembic check
```

迁移前应明确连接的目标环境，备份并确认恢复方式。以上命令未在 Azure 数据库执行。初始迁移在独立 SQLite 校验库通过；MySQL 仅生成并检查了离线 DDL。锁定依赖由 `uv.lock` 管理，Functions 构建使用导出的 `requirements.txt`。

## 当前状态

MCP 已提供匿名与私人 Streamable HTTP、网页授权、短期令牌和撤销；入口、权限、重试约定与本地验收命令见 [MCP 与应用连接](docs/mcp-and-connections.md)。Chrome 本地安装包位于客户端仓库，实际 Chrome 运行仍待验收。

官方直播草稿、审核发布、地区/观看条件、撤回、到期和 HEAD 检查已接入；维护者白名单默认空，自动联网检查默认关闭。使用与证据边界见 [直播入口维护](docs/broadcasts.md)。

后台任务支持领取版本校验、Provider 串行处理/熔断和可审计的失败重放。复现真实子进程中断、SQLite 备份恢复及操作 CLI，见 [任务恢复手册](docs/job-recovery.md)。本地 `.env` 可配置独立 Provider key；正式云环境未验收。

完整验收边界见 [STATE.md](STATE.md)、[当前架构](docs/architecture.md) 与 [验收证据](evidence/local-2026-09-09.md)。Codex CLI 0.153.4 的本地 OAuth、工具发现和撤销已实测；实际工具调用、系统日历刷新、Firebase 真实登录、Azure 触发器和手机内容直达仍待验收。可运行 `uv run python -m scripts.check_codex_discovery` 检查已授权的本机 Codex 连接，操作与清理步骤见 [MCP 文档](docs/mcp-and-connections.md)。

游客公共球队/赛事订阅已接入。后台发布公共快照，`GET /api/v1/public-feed?source_key=…`读取公开状态与地址；生产来源需先核验分发资格并配置。边界、迁移和重试见 [docs/public-feeds.md](docs/public-feeds.md)。

个人关注支持 `POST /api/v1/me/follows/preview` 预览新增、移除、重叠与历史保留；Web确认后携带摘要和幂等键保存。计算规则、暂停状态与冲突处理见 [docs/follow-changes.md](docs/follow-changes.md)。

赛程查询和Feed发布按日期缩小候选、批量读取可见链接。20,000场活动数据的本机HTTP查询、时区排序与账号隔离已验证；复现命令和完整容量限制见 [docs/schedule-queries.md](docs/schedule-queries.md)。

定时截止、手动/自动去重、直播分批到期与巡检、迁移和复现命令见 [持续更新调度](docs/scheduling.md)。

账号删除会撤销私人订阅与授权，防止删除前请求恢复个人数据；Firebase 身份清理由可重试任务执行。保留字段、失败重放与外部缓存边界见 [账号删除](docs/account-deletion.md)，真实云身份清理尚未验收。

通知到1,000账号发布的本机容量、关注候选筛选、个人发布任务合并及重复通知验收见 [内容容量](docs/content-capacity.md)。真实YouTube项目配额与云端容量仍待完成。

YouTube Data API 需要独立项目ID及Key，API与worker共用持久预算。默认9,000是本服务上限，不是Google实际余额。配置、迁移与恢复见 [项目预算](docs/youtube-budget.md)。
