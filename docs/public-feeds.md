# 公共球队与赛事日历

开发包要求游客可获取公共 Feed，并提示公共日历到个人日历的重复订阅风险。本次为 T12/T25 补齐该路径；私人 Feed 继续保留独立身份、凭据与个人配置。

## 数据与发布

`public_feeds` 每个 Source 一行，不创建用户、会话或私人令牌。公共 Feed ID 使用来源键的稳定摘要和 `public_` 前缀；事件继续使用共用 `projections` 中持久保存的 ID 生成 UID。公共和私人订阅的同场 UID 独立，不能依赖系统日历自动跨 Feed 去重。

公共投影仅匹配同一 demo/real 数据集中的球队或赛事，默认窗口为过去90天至未来180天。它与个人投影共用链接选择、描述、版本、序列化和撤销记录逻辑；传入的用户始终为空，个人创作者、手动链接、地区偏好、固定和屏蔽记录不参与计算。公共入口仍须有已发布的审核记录，草稿或旧的裸 Link 不能获得公开资格。公共观看条件和地区限制保留在描述中。

Provider 成功和直播发布/撤回会在原事务中写入 `public_projection` outbox；后台维护每天至少调度一次窗口更新。相同日期的常规调度去重，明确内容变更会再次入队，即使已有执行中的任务也不丢失后续变更。重建先锁定 Feed，再计算当前内容；采用通用任务租约和完成校验。内容相同不增加版本、更新时间或 ETag。失败回滚发布，读取继续返回上次完整快照。

当前每个公共 Feed 仍扫描该数据集赛程，首轮容量目标尚未验证；新增公开来源前需测量任务延迟并改进批量读取。SQLite 锁测试不能替代 Azure MySQL 并发验收。

## HTTP 与匿名访问

- `GET /api/v1/public-feed?source_key=…` 返回名称、demo、状态、条数、发布版本/时间和公开 URL。不可用或首次未发布时 URL 为空。读取不会创建 Feed 或任务，也不读取登录账号。
- `GET/HEAD /public-feeds/{public_id}.ics` 返回持久快照，支持 ETag、Last-Modified 和304；HEAD保留内容长度而无正文。浏览器下载文件名为 `anke-sports-public.ics`。
- 公共响应使用 `public, max-age=0, must-revalidate`；私人 `/feeds/{token}.ics` 保持 `private, no-cache`。两类标识不能在另一条路径中使用。条件请求遵循 [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#name-conditional-requests)，错误或未发布状态不能冒充空日历200。

本地模式可测试已有来源，包括明确标记的演示数据。非 local 环境默认不开放公共分发；完成来源许可、署名和覆盖核验后，将具体来源键加入 `ANKE_SPORTS_PUBLIC_FEED_SOURCE_KEYS` JSON数组。演示来源即使被误列入也不会在部署环境开放。移除来源配置会停止该地址后续读取，无法抹去客户端缓存；重新启用保留原投影身份。不要通过更换来源键实现改名或改期。

## 用户操作

订阅页提供公共球队/赛事选择、公开地址、复制和一次性下载。游客操作不要求登录；个人组合日历仍需账号。复制失败时保留可选中的地址供手动复制。页面明确区分源发布、客户端下次刷新和一次性文件。

Apple 日历可通过“新建日历订阅”添加网址；Google 的“通过网址添加”在电脑网页端操作。引导链接指向 [Apple 官方说明](https://support.apple.com/guide/calendar/subscribe-to-calendars-icl1022/mac) 和 [Google 官方说明](https://support.google.com/calendar/answer/37100?hl=zh-Hans)。这次只核查文档和本地流程，未在真实日历账号中添加订阅。

改用个人日历时，先保存相同球队关注，再添加个人地址，并由用户在系统日历中移除旧公共订阅。多个公共来源之间也可能重复；产品不能宣称能替用户删除外部订阅。

## 迁移与证据

新增迁移 `7229fa56d28e`，父版本 `6e9edd6daa17`。本机升级前停 API/worker并备份，原22张表逐行相同；后续生成公共投影仅新增自身行。降级前必须停止新任务；降级表结构本身不能代替发布后的数据恢复方案。应保留 PublicFeed 和 Projection 备份，避免还原后丢失 UID/版本历史。

复现自动边界：`uv run pytest -q tests/test_public_feeds.py`。本次浏览器下载、匿名读取、布局与数据库证据见 [公共订阅验收](../evidence/public-feeds-2026-09-10.md)。真实 HTTPS、Google/Apple 刷新、平台播放、MySQL和来源分发资格仍为未验收项。
