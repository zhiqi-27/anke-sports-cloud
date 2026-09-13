# NBA / 英超真实接口验收 · 2026-09-12

范围：本机直接运行当前 fetch_schedule，凭据仅在进程内从用户指定 Markdown 读取。未写入环境文件、Git、日志或云端。未发布到 Cosmos 或修改个人 Feed。

| 数据源 | UTC 验证时间 | 球队 | 比赛 | 返回日期 | 时间精度 |
| --- | --- | --- | --- | --- | --- |
| football-data | 12:29:52 | 20 | 380 | 2026-08-21 至 2027-05-30 | 380 exact |
| BALLDONTLIE | 12:30:44 | 30 | 325 | 2026-10-20 至 2026-12-03 | 325 exact |

英超：目录返回的赛季年份传入 matches 请求，30 finished / 350 scheduled。NBA：当前日期前 7 天至后 90 天查询，325 scheduled；此结果不是完整赛季覆盖，默认查询不含季前赛。两者均完整返回且规范化成功，未触发 HTTP 限流或 180 秒截止。

此结果证明密钥可用、真实目录和赛程可读取，不代表赛程与联盟官网逐场核对完成、云端同步、三运动混合关注、手机显示或内容匹配完成。

本地 Provider 回归此前 19 passed；本轮未改业务代码。下一步：准备独立 Azure 开发环境的密钥引用、Provider 启用与后端发布，随后验证 F1/NBA/英超混合个人订阅及手机更新。不得变更现有私人 Feed URL。

## 云端准备与阻塞

目标订阅与既有计划一致；2026-09-12 读回 Function App 为 Running。Provider 与打包测试 22 passed。新包 `data/anke-sports-20260912-multisport.zip` SHA256 `540961d4c13f32db9866a46703652ed91794e34d47522b6110822dfdb5a2bf46`，仅包含显式运行时白名单，不含凭据文档。

待执行变更：在既有 Vault `ankesports-dev-mtcflttk` 写入 `balldontlie-api-key` / `football-data-api-key`；配置同名环境变量的 Key Vault 引用；发布新后端包，然后将 enabled providers 扩为 jolpica、balldontlie、football-data。infra 已同步目标引用，但未执行资源部署；不重建基础设施。

当前 CLI 账号读取 Vault 元数据返回 ForbiddenByRbac / DeniedWithNoValidRBAC，未尝试绕过或自行提权。需用户授权在此 Vault 范围临时授予当前账号 Key Vault Secrets Officer，完成写入后移除并读回。应用现有 Secrets User 权限无需扩大。

恢复路径：将 enabled providers 恢复为仅 jolpica，代码回退上次已验收包；保留现有密钥与 Feed 身份。未执行数据库迁移。完整本轮发布验证尚未完成，未写云密钥、未发布代码或改线上配置。

## 云端发布（2026-09-12 后续）

用户明确允许临时 Vault 范围提权。两项密钥写入成功后立即删除该临时 Secrets Officer 分配，列表读回为空；原应用 Secrets User 保留。密钥值未打印或写入仓库。

部署 c802a62c-1f93-45bd-8731-fe55e62421f7：管理接口确认 status=4、complete=true、active=true，CLI 正常退出（CLI 过滤结果为 null，完成证据采用独立 deployment list）。远端 Oryx 构建完成，六个函数已注册。三项应用设置读回为两个 Key Vault 引用及 jolpica/balldontlie/football-data 启用列表。

模板校验按 subscription scope 通过，完整 what-if 存在额外 drift，本轮没有应用 ARM 模板。仅发布代码包及合并三个应用设置。手动调用 update_schedules 返回 202；此时云端首次同步结果仍待核验，未修改用户关注或个人 Feed token。

## 云端首次同步成功

2026-09-12：football-data last_success 12:47:23.384257 UTC；balldontlie 12:48:00.339698 UTC。两项均 enabled=true、idle、error 空、consecutive_failures=0；jolpica 原成功状态保持。公开 sources 返回 53 项（F1 1、NBA 31、英超 21），Chrome 真实 Google 会话关注页显示 3 个赛事 / 50 支球队，F1 仍选中。

公开 events 查询 [2026-10-01T00:00:00Z, 2026-11-01T00:00:00Z) 返回 HTTP 200 / 141 项：racing 19、football 38、basketball 84。日期不带时间的初次探针不合契约，修正为 ISO UTC 后成功。

## 真实登录混合关注预览

Chrome 当前账号保留 F1，临时勾选整个 NBA / 英超并调用预览：新增 605 场、移除未来比赛 0、结果总计 686 场（原 F1 81 场保留），窗口 2026-06-14 至 2027-03-11，Asia/Shanghai。未点击确认保存，未改变已保存关注。用户尚未指定球队/整个联赛偏好；浏览器停留可审阅预览，待用户选择后完成保存及手机验收。

## 用户指定球队已保存

用户选择马刺、利物浦。真实 Chrome 会话保留 F1，取消整 NBA/英超草稿，改选 San Antonio Spurs 和 Liverpool FC。预览新增 51 场（含 3 场历史）、移除 0、总计 132。第一次提交返回配置版本冲突；重新读取关注并重算后第二次提交成功。页面读回“已保存”，侧栏 F1/LIV/SAS，三个相应 checkbox=1，NBA/英超整联赛 checkbox=0。未旋转订阅 token；Feed 发布与手机验收待核验。

订阅页后续读回：3 个关注 / 132 场选中比赛 / 订阅源更新中。Mac 原生工具报告已锁定，等待用户解锁；本次未读取私人 Feed、未确认新投影发布、未取得新增球队的 Mac/手机显示证据。

## Mac 实际拉取与网页范围复核

2026-09-12 Mac 与网页复核：用户反馈未见 NBA/英超。订阅页已显示“3 个关注 · 132 场选中比赛，订阅源已更新”。Mac 执行“显示 → 刷新日历”后，实际在 Anke Sports 日历中读回 11 月 10 日马刺对太阳、11 月 21 日利物浦对曼联及其他两队比赛（与另一个既有 San Antonio Spurs 日历区分）。网页此前为 9 月/全部赛事；切到我的关注/10 月后显示 28 场，马刺与利物浦场次均标为已加入个人日历。用户反馈的 Mac 缺失为此次刷新前旧内容；网页范围与月份造成另两项观感。未改业务代码或订阅 URL，网页重新进入默认全部赛事的现有行为仍在，手机本轮未验。
