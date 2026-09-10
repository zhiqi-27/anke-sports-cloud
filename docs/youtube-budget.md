# YouTube 项目级请求预算

SQL完整内容流程中的HTTP频道解析、MCP添加创作者、后台uploads补查、视频详情与频道元数据共用 `youtube_budgets`。所有实例必须使用同一权威SQL数据库及拥有API key的同一个 `ANKE_SPORTS_YOUTUBE_PROJECT_ID`。Key轮换不改变桶；不允许通过轮换项目绕过额度。不会复制FormaLM配置。

文档模式已接入ETag项目账本、HTTP频道解析、创作者保存和共享轮询/匹配，SQL/文档共用传输、太平洋窗口与安全等待规则；Key改为请求头传递，已通过真实频道读取。每个轮询步骤成功后持久推进，额度等待不会重读已提交的uploads页。WebSub仍待接入；迁移时不能并行启用同项目两份独立账本，见 [文档YouTube范围](document-youtube.md) 和 [创作者流程](document-creators.md)。

配置 `YOUTUBE_API_KEY`、`ANKE_SPORTS_YOUTUBE_PROJECT_ID` 与 `ANKE_SPORTS_YOUTUBE_DAILY_BUDGET`。默认 9,000 是本服务自己的每日上限，不是 Google 已批准额度、实际用量或余额。此版本只允许 `channels`、`playlistItems`、`videos`，每次请求预留 1 单位；分页逐请求计数，不使用 search。依据 [官方成本表](https://developers.google.com/youtube/v3/determine_quota_cost)（2026-09-10 核对，页面更新于 2026-09-04），日界线为太平洋时间午夜，含夏令时。

## 事务与调度

- 发出 HTTP 前，在独立短事务中锁定项目桶并提交预留。超时、无效响应、业务回滚或过期 worker 都不退回计数；崩溃可能保守多计，避免漏计。
- 联网前不能持有业务写锁。添加创作者先准备频道元数据，之后锁账号，重新验证删除状态、配置版本和幂等回执；已完成的相同幂等命令不再联网。并行尚未完成的同键请求可能各自读取一次元数据，但只提交一次业务结果，且两次网络请求均计数。
- 一次 uploads 分页只抓 uploads，原子产生独立视频详情任务。达到预算后无需回滚、重复抓取已经成功的分页。同页已排队视频不再加入 retained 补查；跨分页/重复通知仍可能有保守重复请求。
- 当天减少配置上限立即生效；增加上限在下一个太平洋日生效。滚动部署遵守当天已落库的较低上限。API key 变化、应用重启、任务重放都不重置计数。
- 本地预算耗尽或 Google 返回 quotaExceeded/dailyLimitExceeded 时等待下个太平洋午夜；429 与 rateLimitExceeded/userRateLimitExceeded 使用 Retry-After，至少 60 秒。共享等待只影响 Data API 请求，不阻塞已有视频重新匹配、Feed 发布和 Hub 续订。错误分类见 [官方错误说明](https://developers.google.com/youtube/v3/docs/errors)。
- 任务领取前可直接顺延到共享恢复时间，不消耗 attempts。请求途中收到配额等待时增加 `quota_waits`；attempts 始终递增以保护旧 worker 的提交检查。五次失败上限使用 `attempts - quota_waits`，真正失败仍按原规则终止。旧日的迟到 quotaExceeded 不封锁新日；已在途请求的成功也不解除其他请求设置的等待。

`GET /api/v1/status.youtube_budget` 返回是否配置、等待/可用状态、恢复时间及本服务预留计数，不返回项目 ID、Key、Google 错误正文或请求 URL。创作者页面只显示影响用户的更新暂缓、最早自动重试时间和保留已有内容；等待时 30 秒轮询，恢复后继续原流程。服务器恢复时间不是承诺手机同步时间。

## 迁移与恢复

迁移 `e42c08f771d3` 新建预算表，并为既有 outbox 增加 `quota_waits=0`。先停旧 API/worker、备份、迁移，再启动新版本。SQLite 为本地适配器；MySQL 仅已验证离线 DDL，真实并发锁与 Azure 多实例仍需验收。

回退会丢失预算账本，不能在同一天直接恢复联网；从旧备份恢复也可能丢失备份之后的预留。恢复期间应暂时禁用 YouTube key，核对真实 Google 项目用量，或者等待下一个太平洋日再启用。其他共享该 Google 项目的程序不在此账本内，仍可能使 Google 比本服务更早限流。项目必须专用于 Anke Sports，所有请求路径使用本模块。

本机证据见 [配额验收](../evidence/youtube-budget-2026-09-10.md)。真实 key/Hub、Google 配额行为、MySQL/Azure 和设备刷新没有因本地测试而通过。
