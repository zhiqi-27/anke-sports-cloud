# 个人 Feed 取消关注隐藏部署 · 2026-09-13

## 发布对象

- 源代码提交：`cfb3798712f657e4b7c01ba0d8d0707affd160f6`
- 目标：`anke-sports-dev` / `anke-sports-dev-mtcflttk`，East Asia
- 源码包：`data/anke-sports-20260913-unfollow.zip`
- SHA-256：`0097eb0ffacbaf9dd3211e5a3a24598145a7987175907eced2e0509bf845e9a4`
- 回滚包：`data/p2-functions-2026-09-13.zip`
- 回滚 SHA-256：`3a237a834315addccb1c46d63bb015f7936bb6899c58d3561ea4620c870ce663`

本次仅发布 Functions 代码。没有执行 Bicep、迁移数据、修改应用设置/RBAC、发布前端、轮换 Feed 地址或 Git push。

## 发布前验证

- 全量 383 passed / 2 skipped / 2 既有依赖弃用警告。
- 个人/公共 Feed 边界定向 60 passed；Functions 打包 3 passed。
- Ruff 与 `git diff --check` 通过。
- 确定性包清单包含 `app/calendar.py`、`app/calendar_rules.py` 和 `app/document_feeds.py`。
- Bicep 编译与已跟踪 `infra/main.json` 逐字一致；基础设施未执行。

## 部署与读回

- OneDeploy：`9ea4685d-a09a-4eef-b33f-5ca5fa3ef60d`
- 时间：2026-09-13 11:43:15–11:44:53 UTC
- 管理接口：status 4、active=true、complete=true、remoteBuild=true。
- 六个 Functions 均已注册。
- Azure 直连和 `https://sports.anke-ai.com` 的 health/status 均为 HTTP 200；环境仍为 staging，status 保持 `no-store`。
- 运行身份角色读回不变：Vault Secrets User、Storage Queue Data Contributor、Storage Blob Data Owner，以及仅限 `anke-sports` 数据库的 Cosmos Built-in Data Contributor。

## 个人 Feed 边界

代码已在线，但部署不会修改真实账号或立即重写已有 ICS。`advance_calendar_window` 每日 00:00 UTC 为所有活动账号排队重建；下一次计划窗口为 2026-09-14 00:00 UTC（上海时间 08:00），随后仍需 Queue 完成发布和 Apple 日历刷新。当前无法在不使用用户身份或改动其配置的情况下证明截图中的未来 F1 已从真实 Feed 消失。
