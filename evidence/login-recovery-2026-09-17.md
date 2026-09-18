# Anke Sports 登录失败修复与开发复测 · 2026-09-17

## 原因与修复

现行 `Config` 使用严格字段契约。已有账户仍可能保存 A 范围清理前的关注类型和视频相关配置字段；认证成功后读取个人日历时，这些字段会在当前响应模型校验处触发 503，前端因此只显示“请求未完成”。

`app/config_rules.py` 新增 `normalize_stored_config`，在账户读取边界仅投影到当前 `Config`：保留当前偏好、合法 team follow、手动事件和链接覆盖，并按稳定键去重；退休字段和不支持的行不会进入 API。`app/document_accounts.py` 同时覆盖 inline config 与 `config_ref` 路径。读取不会改写原始文档，用户下一次执行当前配置变更时才使用当前契约写回。

## 验证

- 后端全量：`uv run pytest -q` → **244 passed, 2 skipped**；Ruff、compileall、Functions 包测试 `3 passed`、`git diff --check` 均通过。
- 回归：`test_legacy_stored_config_is_read_as_the_current_contract` 验证旧 `competition` follow、`creators`、`event_overrides` 和 `content_search_windows` 不会进入当前响应。
- 包：`data/anke-sports-login-fix-20260917.zip`，190390 字节，SHA-256 `0d4923519cb40818d200e6aba257f544e1858d83c7d4114d8e45d9af725d08ed`；显式白名单、远程构建。
- 目标：订阅 `a1187bf2-2e2f-4e05-aaea-407163a009f5`，资源组 `anke-sports-dev`，East Asia，现有 `anke-sports-dev-mtcflttk`；未执行基础设施部署、RBAC/配置修改、数据迁移或 Git push。
- OneDeploy `c1878908-f695-48af-8ffb-6f62f0aeb39f` → **successful**, `active: true`；5 个现行 Functions 仍注册。
- Azure 直连 `/api/v1/health`、`/api/v1/status`、`/openapi.json` → **200**；匿名 `/api/v1/me/calendar` → **401 AUTH_REQUIRED**。
- 当前 Chrome 会话重新加载后显示已登录用户“Anke Sports 用户”、3 场比赛；Cloudflare tail 记录带认证的 `/api/v1/me/calendar` 与 `/api/v1/events` → **200**，无异常。

## 边界

这是开发环境线上复测，不代表生产发布、全新账号生命周期、设备 ICS 或真实 Cosmos/Queue 写事务的完整验收。前端 Worker 本轮未重新部署。
