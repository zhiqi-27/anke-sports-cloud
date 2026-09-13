# P2 开发环境代码部署 · 2026-09-13

用户明确授权部署现有开发环境。发布的是冻结候选 aff58eb，不包含部署期间工作区随后出现的日历相关改动。

- 目标：anke-sports-dev / anke-sports-dev-mtcflttk，East Asia；应用环境标记 staging，Cosmos。
- 源码包：data/p2-functions-2026-09-13.zip，SHA-256 `3a237a834315addccb1c46d63bb015f7936bb6899c58d3561ea4620c870ce663`。
- 回滚包：data/anke-sports-20260913-p1.zip，SHA-256 `3509a4bd56827bfc224fbf539993e608cb2049dc48dcabe465fb21788010ff28`，已核验；未执行回滚。
- OneDeploy：`3a2b84d5-b398-4781-a102-6fd69fac5edd`，03:01:25–03:03:05 UTC；status 4、complete=true、active=true。Azure CLI 发布进程最终退出0。
- 只部署 Functions 代码并远程构建；没有部署基础设施、变更配置/RBAC、数据迁移、前端发布或 Git push。

## 读回

- 六函数注册：advance_calendar_window、dispatch_outbox、http_app_func、process_job、update_content、update_schedules。
- Azure 和 https://sports.anke-ai.com/api/v1/health 均200，staging/Cosmos；网站 /calendar 为200。
- Azure `/.well-known/oauth-authorization-server` 为200，issuer为 `https://anke-sports-dev-mtcflttk.azurewebsites.net/`。网站域名的同路径未代理该协议；客户端应使用该Azure issuer，不标记网站域名OAuth发现完成。
- 两个入口 `/api/v1/platforms` 均200/12项，匿名 `/api/v1/me/connections` 均401 AUTH_REQUIRED，no-store；说明新路由已接入，不再是未迁移503。
- 实际源 `/api/v1/public-feed` 为200、status=unavailable、无URL，符合云设置 `ANKE_SPORTS_PUBLIC_FEED_SOURCE_KEYS=[]`。`ANKE_SPORTS_BROADCAST_CHECKS_ENABLED=false`。未擅自开放分发或联网检查。
- 三Provider enabled/idle、错误为空、consecutive_failures=0；最近成功时间均早于此次部署，所以不能作为部署后新抓取证明。
- 现有运行身份的 Storage Blob/Queue、Vault 与数据库范围 Cosmos 角色已读回，无扩权。

## 尚未验收

授权同意/令牌/删除账号的真实Firebase账号路径、公共源白名单启用及ICS云端发布、US/CN真实转播场次/设备播放仍未完成。本轮没有修改真实账号或创建转播记录。开发环境代码部署成功不等于四项产品能力全面云端签收。
