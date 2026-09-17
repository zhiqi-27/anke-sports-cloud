# B 单场日历后端 · 候选与开发部署证据

日期：2026-09-17（Asia/Shanghai）

## 结果

B 先在服务端本地候选版本完成，覆盖 SQL 本地基线和 `documents-local` 文档适配器；随后按用户显式请求发布到既有开发 Function App。前面的本地适配器回归不代表真实 Cosmos/Queue 事务已完成，也不代表正式环境已发布。

- `POST /api/v1/me/calendar/events/{event_id}` 添加已有赛程的独立 `manual_events` 来源。
- `DELETE /api/v1/me/calendar/events/{event_id}` 只移除本人手动来源；关注来源存在时返回 `EVENT_MANAGED_BY_FOLLOW`，不修改关注。
- 手动来源与球队关注分别记录，事件详情只对已认证的本人返回 `calendar.sources` 和 `can_remove`；个人 Feed/ICS 按事件去重。
- 添加/删除使用当前 `expected_revision`，写入配置与 projection outbox 同一原子边界；可选 `Idempotency-Key` 在 24 小时内返回同一结果，重复添加不增加 revision 或任务。
- SQL Projection 和文档 Feed projection 均按 `feed + event` 派生稳定 ID；删除后再次添加复用原 UID。
- 配置导入不能增删手动来源；原样导出/导入可校验，试图通过 `replace`/`merge` 改动 `manual_events` 返回 `MANUAL_EVENTS_IMPORT_FORBIDDEN`。事件 ID 与来源键的导入解析分别覆盖手动来源和直播链接配置。

## 验证

- `uv run pytest -q` → **243 passed, 2 skipped**。
- `uv run ruff check .` → **All checks passed**。
- `uv run python -m scripts.export_contracts` → OpenAPI 已包含新增 POST/DELETE 路由和 `CalendarEventChange`；配置 schema 保持 `manual_events` 契约。
- Web 仓库 `npm run contracts` → 重新生成 `src/lib/generated.ts`。
- Web 仓库 `npm run typecheck` → 通过。
- `git diff --check` → 两仓均通过。

## 覆盖的行为场景

`tests/test_manual_calendar.py` 覆盖 SQL 与文档适配器的手动增删、同键重放、新键重复、revision 冲突、outbox/投影、手动与关注重叠、两支球队共同覆盖、部分取消关注、导入绕过、未登录拒绝和删除后重加稳定 UID。

## 本地候选阶段未声称完成

本地回归阶段没有推送或部署 B 代码。Web 按计划未加入 B 的交互入口，MCP 对等增删工具属于 C；真实客户端授权和设备 Feed 拉取仍属于后续 D/E 验收。

## 开发环境部署 · 2026-09-17

- 目标：Azure subscription `Azure subscription 1` / `a1187bf2-2e2f-4e05-aaea-407163a009f5`，资源组 `anke-sports-dev`，East Asia，现有 Function App `anke-sports-dev-mtcflttk`。目标回读为 Running、development 标签；没有触及正式环境。
- 候选包：`data/anke-sports-b-20260917.zip`，189729 字节，SHA-256 `562bcd79a1074aad29df8b41c8e4ba034ad725ae26503183150b32103439f902`，包含 85 个 allowlisted runtime files。
- 回退包：`data/anke-sports-a-20260917.zip`，SHA-256 `64f47c69f0fab235b8193d7b887910063933bcb762364ca3e9140f3ae2ab356d`；未执行回退。
- Azure CLI `az functionapp deployment source config-zip --build-remote true` 返回 `Deployment was successful.`，部署 ID 为 `44df243c-abf7-45a6-9a5f-4ed247dcc50a`。本次没有应用 Bicep what-if、变更设置/RBAC、迁移 schema、修改用户数据或 push Git。
- 5 个 Functions 已注册：`advance_calendar_window`、`dispatch_outbox`、`http_app_func`、`process_job`、`update_schedules`。
- `https://anke-sports-dev-mtcflttk.azurewebsites.net/api/v1/health` 与 `/api/v1/status` 均返回 HTTP 200、`Cache-Control: no-store`；health 为 `ok / staging / cosmos`，status 显示 Firebase 已配置且三个现行 Provider 无连续失败。
- `https://anke-sports-dev-mtcflttk.azurewebsites.net/openapi.json` 返回 HTTP 200，含 `/api/v1/me/calendar/events/{event_id}` 的 POST/DELETE 与 `CalendarEventChange.expected_revision`。Azure 直连和 `https://sports.anke-ai.com` 代理的未认证 POST/DELETE 探针均返回 HTTP 401 `AUTH_REQUIRED`，未产生数据写入。
- 前端 Worker 未重新部署；B 代码可通过 Azure 直连或既有 `sports.anke-ai.com` API 代理测试。真实登录后的添加/删除、Cosmos/Queue 真实事务回读、设备 ICS 和正式发布仍未声称完成。
