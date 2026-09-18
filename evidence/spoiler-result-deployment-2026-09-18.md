# 赛果标题与防剧透开发部署记录

日期：2026-09-18（Asia/Shanghai）

## 已发布

- Git 提交：`7172088`（`feat: finalize calendar result delivery`），已推送 `main`。
- 语义：防剧透开启时，仅隐藏每个用户所关注球队最近一场已结束比赛的结果；更早比赛仍显示，未关注球队、手动单场和公共标题不受影响。关闭后，完整比分正常显示。
- 结果字段和标题规则已贯通个人事件、Feed、ICS、文档运行时与 MCP 相关读取路径。
- 赛果刷新：开赛前 2 小时至开赛后 4 小时为 15 分钟，其余仍为 6 小时；不完整上游结果不会覆盖已有有效赛程。

## 构建与部署

- 全量测试：`258 passed / 2 skipped`；Ruff、OpenAPI 导出、客户端类型检查、`git diff --check` 通过。
- 运行包：`data/anke-sports-spoiler-20260918.zip`，195927 bytes。
- SHA-256：`f1dee03d619cc80db5b2756adfddc2b176375bbfcaba42e68be7ddec292f300a`。
- Azure OneDeploy：`d9700541-b3a9-4ae6-898f-36d3807e716b`，状态成功且为当前 active 部署。
- Function App：`anke-sports-dev-mtcflttk`（资源组 `anke-sports-dev`）。

## 线上回读

- Function 列表保持 5 个：`advance_calendar_window`、`dispatch_outbox`、`http_app_func`、`process_job`、`update_schedules`。
- 直连 `/api/v1/health`、`/api/v1/status`、`/openapi.json` 均为 HTTP 200；运行时为 `staging`，存储后端为 `cosmos`，三个 Provider 均启用且 idle、无错误。
- 公网代理的未认证个人单场写入返回 HTTP 401；本轮没有修改真实账号数据。

## 边界

- 本次只部署开发环境，正式环境未变更。
- 本轮未做真实账号写入回读、设备端 ICS/日历刷新或移动端播放验收；这些不以 HTTP 200 代替。
