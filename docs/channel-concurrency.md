# YouTube 后台并发与恢复

对应 T13、T15–T18。视频与频道元数据共用采集；个人匹配、人工选择和Feed仍属于各自用户。实现与本地并发证据不等同真实YouTube/Azure验收。

## 两类执行租约

`ChannelWork` 按频道保存 `:data` 与 `:hub` 两条记录。数据租约覆盖 `youtube_poll`、`youtube_videos`、`youtube_channel_metadata` 和 `youtube_rematch`；Hub租约覆盖 `youtube_subscribe`。它们是5分钟Worker执行租约，与Hub协商的订阅期限分别管理。

领取记录任务ID、尝试次数和截止时间；资源租约先于任务行取得，提交时保持同一顺序。两个不同数据任务处理同一频道时，后者等待且不消耗尝试次数；其他频道与本频道Hub任务仍可领取。执行过长被新任务接替时，旧执行的业务修改、后续outbox、成功或失败结果都会被条件提交拒绝并回滚。

网络失败按频道退避，保留Retry-After；连续3次失败开始熔断，缺key直接终止该任务并等待6小时。人工修改触发的本地重新匹配不受API冷却限制，也不会冒充一次网络恢复。成功的数据请求清除网络错误及旧的创作者失败提示。连续崩溃耗尽5次执行时，标记失败并释放仍由该任务持有的频道租约。

## Hub 意图与通知

Hub请求需要持久化的Worker Claim。提交待验证意图之前检查频道、任务、尝试次数、租约和未过期条件。尚在15分钟验证窗口内的相同意图不被重复任务替换；有效订阅尚未到续订时间时不重复发送。HTTP非成功响应保留其状态与Retry-After，错误使用脱敏代码。

挑战验证、通知回执和内容调度在读取协议状态前取得频道行锁；本地SQLite使用无内容变化的UPDATE取得写锁。相同签名通知并发到达时，回执与一个视频刷新任务原子提交。接受过的同一挑战可重试应答，采用规范化参数摘要识别，不重新延长订阅期限；不同挑战、不同租约或当前关注状态不匹配仍拒绝。

回调地址保持稳定。Hub订阅身份由topic与callback组合确定，续订应更新该订阅；异步核验需要在请求发出前让意图可读。这些协议依据来自 [PubSubHubbub 0.4](https://pubsubhubbub.github.io/PubSubHubbub/pubsubhubbub-core-0.4.html)。YouTube的通知是更新提示，随后仍读取Data API确认实际频道和视频元数据；通知能力参考 [YouTube官方文档](https://developers.google.com/youtube/v3/guides/push_notifications)。本次没有调用真实Hub或验证长期续订。

外部HTTP不在SQL事务里，不能承诺只发出一次请求。意图提交后仍可能有进程退出或响应丢失；待验证状态、协议重试和定期补查共同恢复，实际Hub行为需另行观察。

## 个人选择与过期清理

自动匹配在取得用户写锁后重新读取关注、block/pin和匹配决定。人工审核在同一锁下重新核对待确认记录的版本；手动附加链接也先刷新用户和已有链接。已忽略的视频、已删除的创作者或已变更的待确认记录不会被旧ORM对象覆盖。

元数据到期清理使用读取时的 `updated_at` 作条件更新。扫描之后若网络任务已刷新该视频/频道，旧清理跳过它，不隐藏新数据或撤回相关链接。

## 运行与验收

迁移 `a8c502e7d134` 新增 `channel_work` 和 `channel_sync.verification_digest`。部署顺序为暂停Worker/写入、备份、迁移、启动新API/Worker并读回。回退前应停写并理解新增租约/回执信息的丢失；本机备份可恢复至原始完整状态，不用云端资源练习回退。

运行 `uv run pytest tests/test_channel_jobs.py -q`。18个用例覆盖独立SQLite连接的竞争、旧请求晚返回、错误晚返回、限流、Hub意图、重复通知、人工选择、过期清理及签名通知到ICS。完整记录见 [验收证据](../evidence/channel-concurrency-2026-09-10.md)。大规模、真实MySQL事务/死锁恢复、Azure队列及长期Hub运行仍未验收。
