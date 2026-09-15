# 文档模式的 YouTube 请求与频道解析

2026-09-10。本页记录创作者流程的请求预算与频道解析基础；后续已接入 [创作者保存、共享轮询、匹配和个人ICS](document-creators.md)。开发与生产目标继续使用 Cosmos Serverless + Periodic，Azure资源未创建。

## 已接入

`POST /api/v1/me/creators/resolve` 使用既有登录与来源检查，接受频道ID、@handle、频道页或公开视频页，返回确认后的频道ID、名称和规范频道URL。频道解析不会写入个人关注、链接或发布任务；频道保存和持续发现仍需后续实现。游客请求在联网前拒绝。

SQL与文档模式共用 `youtube_transport`。只允许 `channels`、`playlistItems`、`videos`、`commentThreads` 四个读取端点，HTTP前先预留请求额度。Key放入 `X-Goog-Api-Key` 请求头，重定向不跟随；原始上游错误和凭据不进入API错误正文。频道ID不一致、不完整响应和非公开视频不会被当成解析成功。

字段与费用依据：[频道查询](https://developers.google.com/youtube/v3/docs/channels/list)、[上传列表查询](https://developers.google.com/youtube/v3/docs/playlistItems/list)、[视频查询](https://developers.google.com/youtube/v3/docs/videos/list)、[评论查询](https://developers.google.com/youtube/v3/docs/commentThreads/list)。四个方法均为每次1单位，分页另计；本服务默认9,000只是内部上限。[Google建议使用请求头传递Key](https://docs.cloud.google.com/docs/authentication/api-keys-best-practices)；此前真实YouTube请求只验证了前三个端点，本地评论实现尚未形成真实API验收。

## 额度记录与并发

文档记录位于 `indexes / youtube-budget:<项目ID摘要> / daily`，多个API/worker实例必须使用同一个权威存储和Google项目。Key轮换不改变分区，项目ID与Key都不在状态接口中返回。预算写入indexes，不触发state的outbox投递。

每次请求先用ETag条件提交预留，再执行HTTP。并发冲突重读，最多32次后返回可重试的繁忙错误，不能绕过预留发请求。事务失败或提交结果不确定时不发HTTP；已经可能成功的预留不退还，因此崩溃可能保守多计。业务回滚与已提交的额度记录独立。

沿用SQL的太平洋日期窗口与夏令时规则；同日降低上限立即持久生效，增加上限下个日界线生效。全局限流保存恢复时间，短等待不能覆盖长等待；旧日的迟到配额响应不封锁新日，迟到成功也不能清除等待。损坏记录与系统时钟倒退时停止请求。`GET /api/v1/status`返回内部预留计数与等待状态。最初解析批次标记not_migrated；当前 `integrations.youtube_discovery=polling_available`，实际频道成功时间和额度状态另行读回，WebSub随后已接入本地路径，真实Hub仍未验收，见 [WebSub说明](document-websub.md)。

这些记录不是Google实际用量/余额。迁移时不能让同一项目的SQL和文档请求分别使用两份独立账本运行：停止旧请求路径后迁入当日账本，或禁用请求并等下个太平洋日再统一启用。当前没有执行线上切换或修改已有常驻服务配置。

## 验证与后续

新增25项测试覆盖独立连接的共享上限、Key轮换、提交失败/响应丢失、跨日/夏令时、限流竞争、损坏数据、认证/来源/只读解析和错误脱敏。新进程读取同一文件仍保留计数，且文档HTTP不导入SQL运行时；本地适配器并不等于Cosmos模拟器。完整回归327项通过，见 [证据](../evidence/document-youtube-2026-09-10.md)。

真实只读实验：

```sh
uv run python -m experiments.document_youtube_live --key-file data/youtube-dev.json --output data/document-youtube-live-20260910.json
```

实验只接受既有独立Anke Sports专用Key文件（0600），读取同一频道的ID与handle，共两次请求。专用本地账本 `data/document-youtube-live.db` 保留，单日上限4，重复运行不会重置；输出文件必须新建。该账本只计算此实验，未合并历史实验或Google Console的请求。不会写入用户关注、视频或任务，不操作浏览器、Firebase登录或Azure资源。

后续批次已接入创作者保存/暂停/删除、共享频道任务、uploads分页/视频元数据、匹配/待确认、个人屏蔽和ICS发布，见 [当前说明](document-creators.md) 与 [合成HTTP验收](../evidence/document-creators-2026-09-10.md)。频道资料缺失仍拒绝静默发布空链接日历。真实Hub通知/长期续订、元数据清理、真实视频到ICS、真实Google限流、Cosmos多实例与RU和手机日历继续，不能用本页双次真实频道读取代替上述验收。
