# P1 后端开发环境部署 · 2026-09-13

目标为既有 Azure 开发环境，不是正式环境、Git push 或生产发布。前端没有代码变化，因此没有重复发布 Cloudflare Worker。

## 发布包与回退

- Function App：`anke-sports-dev-mtcflttk`，资源组 `anke-sports-dev`，East Asia。
- 新包：`data/anke-sports-20260913-p1.zip`，SHA-256 `3509a4bd56827bfc224fbf539993e608cb2049dc48dcabe465fb21788010ff28`，160233 bytes，71 个显式白名单运行时文件。
- OneDeploy：`a80acfdd-d2bb-49f1-9d17-cecc1183969f`，2026-09-13 01:27:45–01:29:25 UTC，status 4、complete true、active true；远程构建、触发器同步及清理均完成。
- 回退包：`data/anke-sports-20260912-multisport.zip`，SHA-256 `540961d4c13f32db9866a46703652ed91794e34d47522b6110822dfdb5a2bf46`；前一活动部署为 `c802a62c-1f93-45bd-8731-fe55e62421f7`。

## 发布后读回

- 六个既有函数均已注册：HTTP、队列处理、outbox、赛程更新、内容更新和每日窗口。
- `https://anke-sports-dev-mtcflttk.azurewebsites.net/api/v1/health` 返回 200，`status=ok`、`environment=staging`、`storage_backend=cosmos`。
- `https://sports.anke-ai.com/api/v1/health` 返回相同健康信息；`/api/v1/status` 返回 200、`Cache-Control: no-store`；`https://sports.anke-ai.com/calendar` 返回 200。
- 运行身份保留专用 Storage Blob Data Owner、Storage Queue Data Contributor、Key Vault Secrets User；Cosmos Built-in Data Contributor 只限定到 `anke-sports` 数据库。没有新增或扩大角色。
- 三个 Provider 均 enabled/idle、error 空、consecutive_failures 0。部署后公开赛程查询返回 474 场和 23 场马刺赛事。

## 季前赛边界

NBA 最近成功同步为 2026-09-13 00:52:31 UTC，早于本次部署结束 01:29:25 UTC，因此当前快照仍由旧代码产生，季前赛条目为 0 不能证明来源没有季前赛。按六小时刷新，最早常规重抓时间约为 06:52 UTC / 上海 14:52；新代码将届时分别读取非季前赛和显式 `season_type=preseason`。

本轮没有修改 Provider 状态或 Cosmos 数据来强制提前抓取，没有读取 Key Vault secret，也没有为了验收临时增加管理员入口。下一次成功同步后，需只读核对 `[季前赛]` 数量、马刺场次及 Provider `last_success`，才能把真实季前赛读回标为通过。

