# 2026-09-18 默认直播产品与个人覆盖

## 产品决定

默认直播入口不再要求维护者逐场录入。服务端使用 `app/platforms.py` 的版权矩阵和产品目录：

- 先按比赛 `competition_id` 与用户 `watch_region` 筛选候选版权方；
- 有 `地区 + 联赛` 直播偏好时，优先选择该版权方；
- 没有偏好时选择该地区/联赛排序最前、已配置产品 URL 的版权方；
- 日历和个人 ICS 使用该版权方的官方直播产品页；产品页不冒充逐场播放证据。

当前目录中的代表性入口包括 NBA Watch、Peacock NBA、Prime Video Sports、腾讯体育赛程、咪咕体育频道、Peacock Premier League、Apple TV F1 和 FOD F1。产品页来自对应平台的公开官方页面，移动端仍只在 URL 命中现有 App Link 规则时尝试 App 直达。

## 个人覆盖

用户通过比赛抽屉手动附加 `live` 或 `watch_along` 链接后：

- 该用户的事件读取、日历描述和 ICS 只返回手动入口；
- 官方产品入口不与手动入口并列；
- 删除/屏蔽手动入口后，官方产品入口可以恢复；
- 其他用户仍使用自己的地区/偏好和官方产品目录。

## 本地证据

- `tests/test_broadcast_products.py` 覆盖默认产品、地区版权和手动覆盖。
- `tests/test_document_broadcasts.py` 覆盖文档模式事件读取的自动产品与手动覆盖。
- `tests/test_broadcasts.py`、`tests/test_calendar_flow.py` 和 `tests/test_video_retirement.py` 与本规则一起通过。
- 本地验证：后端全量 `uv run pytest -q` 为 **248 passed, 2 skipped**；Ruff、OpenAPI 导出、`git diff --check` 通过。客户端合同生成、typecheck、生产构建和 diff 检查通过。

## 开发环境发布与回读

- 目标：Azure subscription `Azure subscription 1` / `a1187bf2-2e2f-4e05-aaea-407163a009f5`，资源组 `anke-sports-dev`，East Asia，现有 Function App `anke-sports-dev-mtcflttk`；未触及正式环境、Bicep、RBAC、配置、数据迁移或 Git push。
- 最终后端包：`data/anke-sports-calendar-copy-20260918.zip`，192171 字节、85 个显式运行时文件，SHA-256 `ad6ba6b7ac4193d674e1aae505baecadab938ccfe865a01389a44048b4c67745`。
- Azure 远程构建发布成功，最终 OneDeploy `66aa80f1-3fa0-4cf3-9055-c98b23d6ba0c`；直连 health 返回 `200 / ok / staging / cosmos`，Cloudflare 代理赛事接口返回官方产品链接。
- 未登录公共读取以默认地区返回 `NBC Sports · Premier League`；已登录的中国地区浏览器回读同一场赛事显示 `咪咕视频 · Premier League`，说明地区偏好在个人日历读取边界生效；抽屉同时不再显示场馆信息。
- 日历描述已收敛为“观看直播 / 直播方与赛事 / 入口 URL / 预计时长 / 赛程来源”；不再输出观看条件、地区、版权核验日期、核验 URL或赛程来源 URL。
- 官方产品入口不显示删除按钮；展开核验记录后不显示网页检查或无设备观察提示。删除入口仅保留给 `origin=manual` 的用户链接。
- 手动覆盖、真实账号写入、个人 ICS 设备刷新与播放仍未在公网执行；对应覆盖规则已有本地回归。
