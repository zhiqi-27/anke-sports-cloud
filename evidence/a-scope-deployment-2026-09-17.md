# A 范围清理与契约 · 开发环境发布证据

日期：2026-09-17（Asia/Shanghai）  
状态：开发环境已发布；生产、设备验收和正式公开发布未宣称完成。

## 目标与提交

- Azure Functions：`anke-sports-dev-mtcflttk`，资源组 `anke-sports-dev`，East Asia。
- Web Worker：`anke-sports-web`，自定义域 `https://sports.anke-ai.com`。
- cloud 提交：`5db528340cf6fdb0491d69469beedccf49f5319c`。
- web 提交：`dc15b05a66bc808fbc0cbaf2965af19dd8f8336b`。

## Azure Functions

- 源码包：`data/anke-sports-a-20260917.zip`，187845 字节，SHA-256 `64f47c69f0fab235b8193d7b887910063933bcb762364ca3e9140f3ae2ab356d`。
- 发布方式：现有 Function App 的 zip remote build；Azure CLI 返回 `Deployment was successful.`。没有执行 Bicep what-if 的资源操作。
- 注册 Functions：`advance_calendar_window`、`dispatch_outbox`、`http_app_func`、`process_job`、`update_schedules`。
- 运行时回读：`/api/v1/health` 返回 `{"status":"ok","product":"Anke Sports","environment":"staging","storage_backend":"cosmos"}`；`/api/v1/status` 返回 Firebase 已配置、balldontlie/football-data/jolpica 均 enabled 且无连续失败。
- 配置清理：已从开发 App Settings 删除 `ANKE_SPORTS_YOUTUBE_PROJECT_ID`、`ANKE_SPORTS_YOUTUBE_DAILY_BUDGET`、`ANKE_SPORTS_YOUTUBE_WEBSUB_ENABLED`、`ANKE_SPORTS_MATCHING_AI_ENABLED`、`ANKE_SPORTS_MATCHING_AI_MODEL`、`YOUTUBE_API_KEY`、`GEMINI_API_KEY`。现行 Firebase、Cosmos、Storage、Queue、Provider 和广播检查配置未改动。

## 范围证据

- 当前运行时、OpenAPI、MCP 工具集合和客户端生成类型不再暴露退出能力入口；历史视频模块/数据仍保留作追溯，不作为兼容前置。
- 直播与 `watch_along` 链接保留；A 不实现手动单场增删，B/C 与 MCP 对等行为仍待实现和验收。
- 没有删除资源、历史表、历史文件或用户数据，没有应用 Bicep 基础设施变更，没有新增 RBAC。

## 未覆盖

本次证据不等同于设备 ICS、App Links/播放、真实用户登录生命周期、个人 Feed 完整行为、Queue 失败恢复、MCP 公网授权或生产发布验收。
