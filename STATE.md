# Anke Sports · 服务端状态

> 2026-09-17范围覆盖：不做微信、YouTube视频/AI匹配；新增手动单场增删，关注自动比赛不可单删，MCP同等支持。无需兼容1.0前版本。A 范围清理与源模型/生成契约已部署到开发环境；B 后端已部署并完成线上回读；C 的 Web/MCP 入口尚未实现或验收。见[1.0定义](<../anke-sports 文档/Anke_Sports_1.0定义.md>)。

更新：2026-09-18。上一轮 A 从 `8a26fba` 继续修改，已提交为`5db5283`并推送、部署到现有开发 Function App；B 与本轮直播产品候选均已在该基线上完成实现、回归并以代码包部署到同一开发 Function App（本轮未 push Git）。以[工作区STATE](../STATE.md)及[实施计划](<../anke-sports 文档/Anke_Sports_实施计划.md>)为当前入口。

2026-09-18 赛果标题逻辑已推送并部署到开发 Function App：足球/篮球供应商在完赛且比分完整时写入独立 `Event.result`；防剧透开启时，个人事件读取、Feed 与 ICS 按关注球队分别隐藏最近一场已结束比赛的结果，更早比赛仍显示，关闭后所有完整赛果显示；公共日历与未纳入个人日历的事件保持中性标题。开赛前 2 小时至开赛后 4 小时刷新频率提高到 15 分钟，其余供应商仍为 6 小时。提交 `7172088`，运行包 SHA-256 `f1dee03d619cc80db5b2756adfddc2b176375bbfcaba42e68be7ddec292f300a`，OneDeploy `d9700541-b3a9-4ae6-898f-36d3807e716b`；服务端全量 `258 passed / 2 skipped`、Ruff、OpenAPI 与客户端类型检查通过。见[日历赛果标题规则](docs/calendar-result-titles.md)和[开发部署记录](evidence/spoiler-result-deployment-2026-09-18.md)。

2026-09-18 球队跨赛事赛程已部署开发 Function App：football-data 适配器在英超球队目录基础上读取每支球队的赛程接口，接入上游返回的其他赛事并按 `football-data:match:<id>` 去重；来源与参与者契约补充可选 `logo_url`，SQL/文档事件视图保持两端序列化一致。服务端全量 `253 passed / 2 skipped`、Ruff 和 diff 检查通过；运行包 SHA-256 `8322f65ad0f12cab721822c532d13206f591d205513f859ea07a6616f0048102`，OneDeploy `11ded782-5f09-4000-810c-9feda6569c0d`，health/status、OpenAPI 和 5 个 Functions 回读通过。见[球队日历开发部署记录](evidence/team-calendar-deployment-2026-09-18.md)。

2026-09-18 已按授权单独触发一次开发环境 `football-data` 刷新：临时 staging-only 入口和一次性 token 在成功入队、outbox 派发后均已移除；`last_success` 从 `2026-09-18T07:12:36.908694+00:00` 更新至 `2026-09-18T09:25:15.533867+00:00`，状态 idle、无错误。公网回读 Liverpool `football-data:team:64` 得到 29 场，包含英超与欧冠；最终恢复原始包 OneDeploy `3d812e9b-1240-4a5d-9da4-0bbd7a3b3ef2`，5 个 Functions、health/status 均正常。见[Provider单次刷新记录](evidence/football-data-force-refresh-2026-09-18.md)。

2026-09-18 直播契约与中国大陆入口纠正已部署到开发环境：版权矩阵按联赛/地区/偏好选择官方直播产品页并生成稳定事件入口；NBA 腾讯使用 `https://sports.qq.com/kbsweb/index.htm#nba`、NBA 咪咕使用 `https://www.miguvideo.com/p/home/3cd6ba04967742879aaa40bee02a99a6`、F1 腾讯使用 `https://sports.qq.com/kbsweb/#100360`，英超咪咕保持原入口。用户手动附加链接后覆盖官方产品，事件读取、ICS 与描述均只交付手动入口；详情描述已精简为入口标题、URL、时长与赛程来源。后端包 `data/anke-sports-broadcast-url-correction-20260918.zip` 为 192228 字节、SHA-256 `52f7d97745c973c3846fd779640f2e4996f867b8f914b220acf7e7c107e5d090`，OneDeploy `71e5ac19-4444-4c94-9d8e-3388bcd46a70` 成功；health、状态和版权矩阵接口已回读。详见[中国大陆入口纠正](evidence/broadcast-url-correction-2026-09-18.md)。

2026-09-18 手动链接范围与人工类型契约已部署到开发环境：个人链接不再要求命中官方平台白名单，人工输入只保留 URL/标题并统一按 `live` 保存，但仍受 HTTPS、公开域名、无凭据/跳转和非媒体流安全校验；SQL、文档存储、配置导入及跨用户隔离回归通过。运行包 `data/anke-sports-manual-link-type-20260918.zip` 的 SHA-256 为 `d237272f3348ff24d62987f1443e6e78c7c51670cbcf43eac66595f2d2ddc06a`，OneDeploy `777d3cf6-424d-4849-b17f-07e95d04ce1e` 成功；线上 OpenAPI 已回读 `AddLink` 只含 URL/标题。见[个人链接范围与人工类型记录](evidence/personal-link-scope-2026-09-18.md)。

候选版本已整体停用视频产品能力并部署：删除创作者、待确认、YouTube webhook/维护等公开接口，移除内容Timer与MCP创作者工具，旧 `selection` 单场排除入口也不存在；人工新建链接不再暴露类型，统一按`live`个人观看入口保存，公共维护记录仍可保留`watch_along`历史内容类型。旧视频任务不再进入 active claim，不静默完成；历史视频行、表和模块保留为追溯材料，当前运行时不加载，也未做破坏性迁移。`Config.manual_events`、私有事件 `calendar.sources/can_remove` 已进入服务端 schema、OpenAPI 和客户端生成类型；B 已补齐单场增删、来源合并/去重、权限/revision/幂等、原子 outbox/投影和导入绕过保护，见[候选与开发部署证据](evidence/manual-calendar-backend-2026-09-17.md)。

F1关注代码已发布为只能选择具体车队：Jolpica当前赛季Constructors形成车队目录，全部车队同时写入每个大奖赛/session的participants，因此不同车队命中相同赛程，车队ID用于个人关注与后续官方频道内容范围。赛事/联赛均不能直接关注。适配器真实接口读回为11支车队、115个session事件且每场包含11个车队；云端快照仍是02:30:26 UTC旧数据，首次正常刷新在08:30:26 UTC后才具备资格，因此当前公网目录仍为0支F1车队。未迁移旧F1整赛关注或验证个人Feed。

官方转播地区矩阵与个人偏好已部署到 Azure dev：覆盖美国、中国大陆、日本及 15 个欧洲国家代码，按 F1、NBA、英超分别约束可发布版权平台。用户偏好以“地区 + 联赛”保存；多个版权方按偏好排序，个人日历每场只交付一个直播入口。`official_match` 发布会拒绝赛事或地区不匹配的平台。Peacock、U-NEXT、DAZN、Viaplay、Prime Video 等只有在 URL 路径命中已验证 App Link 范围时才提示手机尝试打开 App；FOD 当前标为网页交接。详见[地区与移动端规则](docs/official-broadcast-regions.md)及[整合发布证据](evidence/integrated-release-2026-09-15.md)。

此前的YouTube搜索、评论读取、AI评分、自动挂入和备选流程已经退出当前产品范围。相关模块、表和证据仅作历史追溯，不是当前兼容前置，也不再由HTTP、Timer、Queue或MCP入口触发。具体边界见[退出能力记录](docs/retired-capabilities.md)。

当前线上直播产品运行包 SHA-256 `52f7d97745c973c3846fd779640f2e4996f867b8f914b220acf7e7c107e5d090`，注册 5 个现行 Functions。部署后 Azure 直连与 Cloudflare 代理的 health/status/版权矩阵接口均正常；OpenAPI 已回读手动单场 POST/DELETE，未认证请求被 401 拦截。7 个已退休 YouTube/AI App Settings 已删除，现行配置保留。

登录失败修复已重新部署到同一开发 Function App：现行 `Config` 对已有账户的历史持久化配置做只读边界投影，过滤已退休字段/不支持的关注类型并补齐当前默认字段，不改写原始文档；回归覆盖旧配置读取。候选包 `data/anke-sports-login-fix-20260917.zip` 为 190390 字节、SHA-256 `0d4923519cb40818d200e6aba257f544e1858d83c7d4114d8e45d9af725d08ed`，OneDeploy `c1878908-f695-48af-8ffb-6f62f0aeb39f` 成功并成为 active。部署后同一 Chrome 认证会话回读 `/me/calendar`、关注事件均为 200，页面显示已登录用户和 3 场比赛；匿名个人接口仍为 401。详见[evidence/login-recovery-2026-09-17.md](evidence/login-recovery-2026-09-17.md)。

已部署：三Provider、季前赛显式抓取、Spurs消歧、Logo兼容、source_id过滤、直接关注规则、客队排除、个人Feed取消关注隐藏，以及P2账号/OAuth/转播文档服务。公共Feed保留停用且白名单为空，不进入v1验收；公开赛程与匿名MCP查询仍在范围。

剩余：实现 C 的 Web/MCP 对等入口，再做 D 全链路验收。旧视频 API/ICS 不可见、个人 Feed 语义、直播链接设备播放、独立测试账号生命周期、Cosmos/Queue真实事务路径、公网MCP授权/查询/写入/到期/撤销、最小恢复和正式发布仍待完成。SQL仍为本地基线，`documents-local` 不是 Cosmos/Queue 证据，真实云验收分别留证。详见[evidence/a-scope-deployment-2026-09-17.md](evidence/a-scope-deployment-2026-09-17.md)及[evidence/manual-calendar-backend-2026-09-17.md](evidence/manual-calendar-backend-2026-09-17.md)。

本次未触发真实Provider或内容任务，也未修改任何具体场次的转播记录；未删除历史表、历史文件或已有工作，仅删除了开发 App Settings 中已确认退休的 YouTube/AI 配置名。
