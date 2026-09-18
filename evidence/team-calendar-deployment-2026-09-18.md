# 球队跨赛事日历开发部署证据

日期：2026-09-18（Asia/Shanghai）

## 发布

- 目标：Azure subscription `Azure subscription 1`（`a1187bf2-2e2f-4e05-aaea-407163a009f5`）的 `anke-sports-dev` / East Asia / `anke-sports-dev-mtcflttk`；正式环境未触及。
- 运行包：`data/anke-sports-team-calendar-20260918.zip`，193120 字节，SHA-256 `8322f65ad0f12cab721822c532d13206f591d205513f859ea07a6616f0048102`。
- Azure CLI `az functionapp deployment source config-zip --build-remote true` 成功；OneDeploy `11ded782-5f09-4000-810c-9feda6569c0d`。
- 本次只发布现有 Function App 代码，没有应用 Bicep、修改 App Settings/RBAC、迁移 schema、修改用户数据或 push Git。

## 线上回读

- Function App 资源状态 `Running`，HTTPS-only；5 个现行 Functions 已注册：`advance_calendar_window`、`dispatch_outbox`、`http_app_func`、`process_job`、`update_schedules`。
- Azure 直连 `/api/v1/health` 与 `/api/v1/status` 均 HTTP 200，运行时为 `staging/cosmos`，三个 Provider 无连续失败。
- `/openapi.json` HTTP 200，已回读日历单场 POST/DELETE 契约和 `ParticipantView.logo_url`。
- Web Worker 发布后，`sports.anke-ai.com/api/v1/health` 与 `/api/v1/status` 代理均 HTTP 200。
- 当前登录浏览器点击侧栏 `Liverpool FC` 后进入 `/calendar?team=football-data%3Ateam%3A64`，页面显示 `Liverpool FC 日历` 和 `返回我的日历`；未执行写入操作。

## 边界

球队页代码已部署并按球队源查询跨赛事赛程，按上游比赛 ID 稳定去重。当前线上登录会话回读到的是现有快照中的 3 场英超；本次没有强制触发上游刷新，因此不把未刷新快照误报为已回读欧冠/足总杯数据。真实 Cosmos/Queue 写入、设备 ICS、播放和正式环境仍未验收。
