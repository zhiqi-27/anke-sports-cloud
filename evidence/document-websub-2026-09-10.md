# 文档WebSub验收

2026-09-10。本批为本地持久文档适配器、合成YouTube/Hub响应、实际loopback HTTP和独立API/worker进程。没有真实Hub、YouTube API、Firebase或Azure调用；查阅了官方协议文档。开发与生产云目标保持Cosmos Serverless + Periodic，资源未创建。

18项新增WebSub用例覆盖持久意图先于外发、提前回调不被晚到错误覆盖、重复挑战不延长租约、共享callback/secret续订、有效旧租约接收通知、暂停退订/恢复、Hub拒绝、过期意图、错误topic/租约、SHA1/SHA256签名、外部实体/大小/条数、纳秒版本、语义去重、50条通知分批、失败回滚、旧worker租约、早醒与五次失败冷却、存储故障不确认任务及7天已处理提示清理。

首轮相关测试因测试夹具缺少队列version、更新参数多传channel_id出现3项失败，修正测试请求后51项相关测试通过（14.59秒）。后续完善Hub拒绝和存储故障/高精度版本检查，最终完整 **361 passed / 2 skipped / 2 warnings**（31.75秒）。跳过项为条件MySQL测试，两条为既有Starlette/AnyIO弃用警告。Ruff、完整SQL OpenAPI/config schema一致性和diff检查通过。客户端本批没有业务源码、契约或依赖变化，没有重复构建。

独立HTTP实验使用临时库与专用合成环境，从正常HTTP保存关注与创作者，worker向模拟Hub发出请求；模拟Hub经真实loopback GET验证API的持久意图，再经真实POST发送签名通知。视频元数据改变后，原事件UID保留、SEQUENCE 2→3；相同显式版本的不同提示文字不会重复抓取。脚本停止自己拥有的worker并确认退出，再以同一数据启动新worker；未重复订阅或请求视频，GET304/HEAD通过。暂停后完成unsubscribe challenge，后续通知被忽略。

实验共4组检查通过；模拟调用计数：channels 2、playlistItems 1、videos 2，Hub subscribe 1、unsubscribe 1。含一次worker重启。源码摘要前后一致；实验API、两代worker均已停止，临时文档、合成密钥和日志已清理。精确时间、源码/Feed摘要和清理结果见 [机器证据](document-websub-2026-09-10.json)。这不是自然长租约观测、真实Google投递或云容量证明。

发现并修复通知唤醒可能绕过频道终止失败冷却的问题，定时轮询和通知现在共用持久retry_at。通知数据只在元数据校验和引用写入成功的同一事务中标记已处理；失败不能丢失通知或覆盖旧视频。处理完的提示清理不等于YouTube元数据物理清理，后者和孤立块GC仍未完成。

Mac锁定期间没有浏览器或原生UI操作，没有要求用户解锁。原3008创作者/3007 F1、主SQL/Firebase和其他产品实例未重启，本批没有重新确认其历史进程。没有主数据迁移、云资源创建、push或部署。真实Hub签名/HTTPS/长租约、标注/真实内容到ICS、Azure Queue/Timer/Cosmos/Periodic恢复和手机验收继续；完整产品与存储迁移仍未完成。
