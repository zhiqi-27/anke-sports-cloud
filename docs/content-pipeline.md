# 创作者与日历内容链路

2026-09-09。本地实现已接入桌面；真实 YouTube、Azure 与设备验收待执行。

## 数据与处理

`Creator` / `Video` 保存共享频道和元数据；`ChannelSync` 为活跃频道保存一份检查时间与租约；`VideoMatch`、链接、block/pin 属于个人账号。相同频道供多人使用时共用发现任务，个人范围与确认结果互不共享。

2026-09-10补充：频道数据任务与Hub请求分别取得执行租约，旧任务不能覆盖新结果；通知/调度并发去重，个人决定锁定后重读，过期清理条件更新。详见 [频道并发与恢复](channel-concurrency.md) 及其本地证据。

频道 URL、handle、视频 URL 经官方 API 解析，桌面先展示频道身份，用户确认后保存关注。初次检查近 14 天 uploads，逐页处理并检测循环游标；每 6 小时补查一次。上传列表缺少某视频不代表删除，已知视频另经 videos.list 刷新可用性。失败保留上次有效数据，超过 28 天未刷新的缓存元数据清理并停止展示，短于官方 30 天上限。

WebSub 默认关闭；开启需独立 API 配置与可达 HTTPS 回调。随机 callback ID、精确 topic、已持久化且未过期的请求意图共同校验挑战；协商的 secret 加密保存，用 HMAC 校验原始通知。无效签名按协议应答后忽略；限制 64 KiB、50 个条目并禁用 XML 外部实体。同一通知只入队一次，随后读取官方 Data API 验证实际频道和视频。到租约 80% 时续订；最后一个活跃关注停止后请求退订，停止关联并忽略新通知。已暂停创作者的原链接继续保留。

本地 worker 每分钟检查内容调度，Azure timer 每 5 分钟执行同一调度函数；实际 Azure 触发、网络回调和长期租约未测试。

## 匹配与人工选择

先按个人赛事和作者范围限定候选，再检查双方球队/大奖赛分场次、前瞻/复盘词、显式日期、发布时间窗口与排除词。前瞻窗口 7 天、复盘窗口 3 天；相同对手多场、F1 未指明分场次或类型不清时进入待确认。规则版本和原因代码随结果保存，不显示概率或未经标注集验证的准确率。

标题改变导致矛盾时撤下自动链接。确认会固定链接；忽略/移除会写入个人 block，后台不会加回。暂停保留旧关联，删除作者前预览影响；手动/确认/固定的链接保留。公共比赛 ID、个人 Feed UID 不因内容变化或令牌轮换而变化。

当前使用早期规模的扫描与查询；未通过 20k 赛事、多用户并发、200 条真实标注视频或 98% 精确率验收。完整分批索引/分页与规模优化仍在后续任务中。

## 本地复现

正常服务不伪造 YouTube 结果；未配置 key 时明确报错。自动测试：`uv run pytest -q`。

完整 UI 合成验证使用独立临时 SQLite，退出自动清理，不接触 `data/anke-sports.db`：

1. 客户端仓库 `npm run build`，随后 `npm run start -- --port 3002`。
2. 本仓 `uv run python -m experiments.creator_ui`。
3. 打开 `http://localhost:3001/creators`，进入本地体验，输入 `@local-fixture`。频道、视频、比赛与账号均标注合成测试；不要把链接视为可播放视频。
4. 验证确认、范围、暂停、人工关联和删除保留；结束后停止两个验证进程。

## 官方参考

- [YouTube 推送通知](https://developers.google.com/youtube/v3/guides/push_notifications)：上传与标题/简介变更通知。
- [PubSubHubbub 0.4](https://pubsubhubbub.github.io/PubSubHubbub/pubsubhubbub-core-0.4.html)：订阅意图、租约和通知签名。
- [playlistItems.list](https://developers.google.com/youtube/v3/docs/playlistItems/list)：uploads 列表分页。
- [YouTube Developer Policies](https://developers.google.com/youtube/terms/developer-policies)：API 数据刷新/删除要求。实现默认值不等同完整平台合规或授权验收。
