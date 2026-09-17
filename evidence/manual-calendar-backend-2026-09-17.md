# B 单场日历后端 · 本地候选证据

日期：2026-09-17（Asia/Shanghai）

## 结果

B 已在服务端本地候选版本完成，覆盖 SQL 本地基线和 `documents-local` 文档适配器；本记录不代表 Cosmos/Queue 开发环境已部署，也不代表正式环境已发布。

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

## 尚未声称完成

本轮没有推送或部署 B 代码；现有开发环境仍运行上一轮 A 版本。Web 按计划未加入 B 的交互入口，MCP 对等增删工具属于 C；Cosmos/真实 Azure Queue、真实客户端授权和设备 Feed 拉取仍属于后续 D/E 验收。
