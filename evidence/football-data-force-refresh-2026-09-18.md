# football-data 开发环境单次强制刷新

更新时间：2026-09-18（Asia/Shanghai）

## 范围

- 目标：现有 East Asia 开发 Function App `anke-sports-dev-mtcflttk`。
- Provider：仅 `football-data`。
- 正式环境、Web Worker、Cosmos 数据结构、网络规则和 RBAC 均未修改；未 push Git。

## 执行

- 刷新前 `last_success`：`2026-09-18T07:12:36.908694+00:00`。
- 临时 ASGI 管理入口仅在 staging、仅接受 `football-data`，由一次性环境 token 保护；触发响应为 `queued: true`。
- outbox 派发响应：HTTP 202。
- 刷新完成后 `last_success`：`2026-09-18T09:25:15.533867+00:00`；状态为 `idle`，`error` 为空，连续失败次数为 0。

## 回读

- 直接 Azure API health：HTTP 200，`environment=staging`、`storage_backend=cosmos`。
- Cloudflare 公网 API status：HTTP 200，读取到相同的 Provider 更新时间和空错误。
- Liverpool `football-data:team:64` 在 `2026-09-01` 至 `2027-02-01` 查询到 29 场，来源包含 `football-data:PL` 和 `football-data:CL`。
- 临时入口已从源码删除，一次性 token 已从开发 App Settings 删除；调用该路径落入未迁移兜底 HTTP 503，不再存在该管理路由。
- 最终恢复原始包：`data/anke-sports-team-calendar-20260918.zip`，193120 bytes，SHA-256 `8322f65ad0f12cab721822c532d13206f591d205513f859ea07a6616f0048102`；OneDeploy `3d812e9b-1240-4a5d-9da4-0bbd7a3b3ef2`。
- 最终注册 Functions 恢复为 5 个：`advance_calendar_window`、`dispatch_outbox`、`http_app_func`、`process_job`、`update_schedules`。

本记录证明的是一次开发环境 Provider 刷新和公网读取，不代表正式环境发布、设备端验收或播放直达。
