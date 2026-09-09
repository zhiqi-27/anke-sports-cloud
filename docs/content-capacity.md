# 多人内容发布与任务合并

通知仍由后台验证签名、读取共享视频元数据，再按个人范围匹配和发布 Feed。Web、扩展与 MCP 不承担持续监听。本轮优化不改变公开比赛、私人链接/屏蔽、稳定 UID 或 Feed 已发布快照的权限边界。

## 候选范围与提交

个人 Feed 的 SQL 先筛选关注的赛事/单场/球队、明确加入的比赛和已有投影，再应用原来的日期、排除与历史保留规则。团队匹配使用 JSON 结构里的 `id`，不会因为名字含有某个 ID、ID 前缀相同或包含引号/通配符就错误加入。SQLite 使用 [json_each](https://www.sqlite.org/json1.html#the_json_each_and_json_tree_table_valued_functions)，Azure MySQL 分支使用 [JSON_CONTAINS](https://dev.mysql.com/doc/refman/8.4/en/json-search-functions.html)。MySQL 只完成方言 SQL 编译，运行和查询计划还需真实实例验证。

已有投影使用 Feed 子查询，避免大量历史事件构成无限长的 SQL 参数列表。SQL 候选只是缩小加载范围，最终 inclusion/history 判断仍在共享业务层执行；没有跨账号结果缓存。JSON 条件仍可能扫描候选行，并非所有关注查询都命中结构化球队索引，不能推断任意规模下开销恒定。

视频匹配先按发布时间附近的保守日期窗口取候选，留出时区偏移余量；最终规则仍使用真实时间比较 7 天前瞻、3 天复盘窗口。此前关联过的比赛无论是否还在时间窗口内都会读取，保证改期、改标题或视频不可用后仍能撤下旧的自动链接。个人关注集合只编译一次，不在每场比赛上重新构造。

个人发布入队前锁定当前账号。若该账号已有 attempts=0 且 pending 的 projection，合并到该任务；任务执行时读取最新配置与链接。running 或正在重试的任务可能持有旧快照，不能吸收新变化；新变化保留一个后续任务，已有重试的 deadline 不变。不同账号互不合并。业务变化和 outbox 仍同事务提交；既有租约版本校验与删除防复活保护保留。

## 容量复现

```sh
uv run python -m experiments.content_capacity --output evidence/content-capacity-new.json --deadline 150 --burst
```

脚本建立临时 SQLite、独立密钥、真实 loopback HTTP 服务，构造 20,000 场活动比赛、200 个被关注的创作者、1,000 个账号，各有 30 场已发布比赛。1,000 人关注同一热门创作者，其中 100 人事先屏蔽目标视频，900 人应收到新前瞻。初始快照是夹具准备，不计入通知发布延迟。

真实 HTTP 接收签名通知，实际 worker/匹配/投影/ICS 实现执行，共享视频请求用明确的合成适配器替换，不接触 YouTube 或主体验库。检查所有 30,000 个已发布 UID、屏蔽结果、队列清空及私人 Feed HTTP 200/304。`--burst` 再连续提交三条不同通知，元数据内容相同，验证只有每账号一条待发布任务，处理后 Feed 版本/ETag/时间保持不变。

P95 是一条通知下 900 个目标 Feed 的观察延迟，测量包含 HTTP 入站、共享读取、匹配、任务等待与串行发布。它不是不同通知的独立统计样本，也不是 1,000 用户并发访问、200 个频道同时更新或云端延迟承诺。脚本每次处理后查询 Feed 记录，测量有少量观察开销。证据和之前未达标的结果见 [容量验收](../evidence/content-capacity-2026-09-10.md)。

## 仍需完成

项目级 YouTube 请求预算与限流未实现。官方当前列出的 channels.list、playlistItems.list 和 videos.list 各花费 1 单位，翻页分别计费，日配额按太平洋时间午夜重置；应读取独立项目实际配额，不把缺省值当作已获额度。参考 [YouTube 配额表](https://developers.google.com/youtube/v3/determine_quota_cost) 和 [quotaExceeded 错误](https://developers.google.com/youtube/v3/docs/errors)。当前频道级重试与共享发现不等同项目级配额保护，也不能通过增加项目绕过配额。

尚需真实推送/续订、项目配额、MySQL/Azure、长期历史 outbox 积压、更多同时活跃频道、内容标注集质量和设备刷新验收。不能用本机合成容量结果把这些项标为完成。
