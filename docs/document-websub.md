# 文档模式的 YouTube 通知与续订

2026-09-10。文档模式已实现持久订阅意图、回调验证、签名通知、频道共享收件记录、后台元数据校验、续订/退订和轮询补查。默认仍关闭 WebSub；本批使用合成Hub和YouTube响应，真实本机HTTP与独立进程已验证，未连接真实Hub、YouTube API或Azure。

## 协议与权威数据

[YouTube 官方通知说明](https://developers.google.com/youtube/v3/guides/push_notifications)说明上传和标题/简介修改会发出Atom通知。本实现只从通知提取频道、视频ID和版本，标题/正文不作为比赛链接元数据；后台继续通过共用项目预算读取Data API并运行既有匹配与发布逻辑。

订阅请求发到固定Google Hub，禁止跟随重定向。公网回调必须HTTPS，拒绝用户名、密码、query或fragment作为基础地址。每个共享频道保存独立随机callback ID、加密签名密钥、待确认意图、租约、重试时间和任务ID；callback目录仅保存其摘要到频道的路由，实际请求还需匹配权威频道记录。

Hub请求前，同频道分区先提交意图和任务租约检查。回调验证topic、所请求动作、活跃关注状态、挑战和有效期；重复挑战只重回应答，不延长旧租约。Hub可能在外发请求返回前完成验证，晚到的HTTP失败不能覆盖已确认的状态。无网络响应时保留15分钟意图，早醒不重新发送；五次请求未确认或失败后进入冷却。Hub拒绝已确认租约也会撤销本地通知接收资格。协议参考：[PubSubHubbub 0.4](https://pubsubhubbub.github.io/PubSubHubbub/pubsubhubbub-core-0.4.html)。

协商租约按实际返回秒数保存，约80%时到期续订。续订使用原callback和secret，已有租约有效时继续接收通知。最后一个启用关注暂停或移除后立即忽略通知，随后调度退订；恢复关注后重新建立订阅。Hub不可用不关闭每6小时的uploads/保留视频补查。当前调度仍由本地分钟检查、Azure每5分钟Timer触发，实际时间误差受调度和重试影响，不承诺精确到秒。

## 持久通知与后台处理

入站流最多64KiB、50条；XML禁止外部实体。签名支持经过格式校验的HMAC-SHA1/SHA256，无效签名和不匹配频道应答204但不保存。有效通知按频道+视频+上游updated版本摘要去重；保留原始时间小数精度。缺少updated的旧格式使用规范化条目摘要。SQL与文档模式共用这部分校验，XML排版或标题提示变化不会重复处理相同显式版本。

每个新版本记录和一个唤醒任务与WebSub状态同频道分区提交。提交失败返回非成功状态，不能先回应成功再丢任务；全部已存在时直接确认。请求线程不调用YouTube，不保存原始XML。

通知与定期轮询复用同一频道抓取任务，避免两个处理器相互覆盖元数据。每次取最多45条待处理版本，视频引用、处理完成标记、频道/任务状态和变更通知最多93项原子写。正在抓取时收到的新通知在该任务末尾继续处理；如果窗口碰到任务结束，持久唤醒或下一轮调度恢复处理。失败保留旧视频与待处理版本，冷却同样约束通知唤醒。

处理完的去重记录7天后清除，每轮最多删除100条；未处理任务保留。此清理只针对通知提示，**不是过期视频元数据清理或不可变孤立块GC**。频道目录/去重记录扫描、真实RU和大规模吞吐仍需测量。

## 配置、复现与状态

云目标为独立Anke Sports的Cosmos Serverless + Periodic，开发与生产一致。启用时需要既有独立YouTube项目预算、可达HTTPS回调和 `ANKE_SPORTS_YOUTUBE_WEBSUB_ENABLED=true`；本批没有修改常驻实例或云应用设置。不要同时启用同一Google项目的SQL/文档两份独立额度账本。

```sh
uv run pytest -q tests/test_document_websub.py
uv run python -m experiments.document_websub_verify --output data/document-websub-http-new.json
```

实验要求新的输出路径，创建临时文档库、合成密钥、独立API和worker进程，只允许模拟Google响应及实际loopback回调。它会重启自己拥有的worker，确认租约/通知回执保持，再暂停创作者并确认退订。全部进程和临时数据在结束时清理，不占用桌面浏览器。

18项新增用例与完整361项回归通过。独立HTTP检查中UID不变，SEQUENCE 2→3，再投递相同版本与重启均不重复读取视频；GET304/HEAD通过。见 [验收证据](../evidence/document-websub-2026-09-10.md)。

`CreatorView.websub_status`返回当前协议状态；默认关闭时为disabled。集成状态中的youtube_push仍为not_tested，表示尚未完成真实Hub外部验收。3008创作者演示保留上一批运行代码，未因本批重启；本批不声称新的浏览器渲染证据。

真实Hub是否发送所协商的签名、公网HTTPS到Azure Functions、自然长租约/漏通知、配额与200条真实标注视频、Azure Queue/Timer、Cosmos权限/RU/恢复和手机日历仍需分别验证。元数据到期物理清理、直播/公共Feed/OAuth/MCP/账号删除等剩余文档路径继续。
