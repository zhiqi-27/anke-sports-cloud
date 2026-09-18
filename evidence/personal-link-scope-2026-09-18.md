# 2026-09-18 用户手动链接范围与类型契约

## 产品契约

- 用户手动添加的链接不受官方版权平台目录限制；人工添加契约不再要求选择 `live` / `watch_along`。
- 仍只接受安全的 HTTPS 网页：公开域名、无账号密码、无凭据/跳转参数、无媒体流或媒体片段路径。
- 链接保存为添加用户的私有链接；该用户的事件详情、日历描述和 ICS 使用它覆盖官方产品入口。
- 其他用户不读取该链接，仍使用自己的地区/直播偏好和官方产品入口。
- 删除/屏蔽该用户的手动链接后，该用户恢复看到官方产品；不影响其他用户。

## 实现范围

- SQL `attach_link` 与文档存储 `Content.attach` 改用个人 URL 安全规范化，不再调用官方平台白名单校验。
- `AddLink` 的 HTTP/MCP 输入只保留 `url` 和可选 `title`；新建与更新的人工链接统一按 `live` 保存，历史人工 `watch_along` 在读取时按 `live` 展示。
- 配置导入中的个人 `link_overrides` 也使用同一规范化边界。
- 官方维护入口继续使用 `candidate_url` 和版权/平台校验，不因本次放开个人链接而扩大公共发布能力。

## 本地验证

- 后端全量：**252 passed, 2 skipped**；Ruff 和 `git diff --check` 通过。
- 覆盖未知平台 URL、媒体/凭据拒绝、SQL 与文档写入、添加者/其他用户可见性隔离。
- OpenAPI `AddLink` 已去掉 `kind`，MCP 与 HTTP 共用该契约；SQL/文档写入统一保存 `live`，历史人工 `watch_along` 读取时不再展示类型差异。
- Web typecheck、生产构建和 Wrangler dry-run 通过；手动链接表单已移除“直播入口/同步解说”选择和旧平台限制错误提示。

## 开发环境部署与回读

- 后端目标：现有 Azure 开发 Function App `anke-sports-dev-mtcflttk`；运行包 `data/anke-sports-manual-link-type-20260918.zip`，85 个运行时文件、192248 字节，SHA-256 `d237272f3348ff24d62987f1443e6e78c7c51670cbcf43eac66595f2d2ddc06a`。
- OneDeploy `777d3cf6-424d-4849-b17f-07e95d04ce1e` 返回 `Deployment was successful.`；Function App 回读为 Running，直连 health/status HTTP 200，运行时为 `staging/cosmos`。
- 直连 `/openapi.json` HTTP 200，线上 `AddLink` schema 只含 `url` 和 `title`，没有 `kind`。
- 前端 Worker `anke-sports-web` 已发布到 `https://sports.anke-ai.com`，版本 `a84c5e18-ebc3-4302-9846-0d57edea1df3`；构建读取 93 个资源并上传 32 个变化资源。公共 `/calendar` HTTP 200，`/api/v1/health` 代理 HTTP 200；已发布人工链接表单脚本不含 `name="link-kind"`。

本次未使用真实账号提交或删除链接，未修改任何真实用户数据；正式环境未变更。登录态手动写入与设备/ICS回读仍未在本次部署中执行。
