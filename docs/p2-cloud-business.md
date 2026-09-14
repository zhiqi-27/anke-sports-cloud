# P2 文档模式业务补齐（2026-09-13）

## 实现与边界

新增 `document_privacy`、`document_oauth`、`document_public_feeds`、`document_broadcasts`，通过现有 FastAPI、Cosmos Store、Change Feed/Queue worker 接入。SQL 与文档模式共用 OAuth 和转播纯规则；文档进程禁止加载 SQL，独立进程回归覆盖此边界。2026-09-14产品决策移除Web公共订阅入口；公共Feed后端继续保留，部署白名单保持为空。

- 删除：账号 tombstone、Feed 撤销、清理任务在所有者分区原子提交。旧身份/授权/Feed 立即拒绝；后台删除 Firebase 身份和分区私人文档，其他账号及公共赛程不受影响。保留最小 tombstone、撤销 Feed 和清理结果。Firebase 操作失败可重试；达到任务重试上限需要运维恢复。外部日历缓存仍由用户移除订阅。
- 备份：本次没有更改现有 Serverless/Periodic 配置。备份中的历史数据不承诺立即物理删除；恢复必须重放删除决定后才开放流量，真实恢复演练尚未完成。当前 tombstone 不是独立备份的删除日志，不能声称已解决所有恢复场景。
- 授权：SDK 标准 OAuth/PKCE 路由、Firebase/本地会话同意、15分钟访问令牌、7天授权、scope/resource 校验、刷新轮换及复用撤销。授权码、访问/刷新令牌只存摘要；客户端元数据加密。请求路由与所有者分区不能跨分区原子提交：先一次性消费请求，再创建所有者授权码，崩溃时重新授权，不能把未完成请求当成授权。授权码消费、grant、tokens、账号 ETag 在一个事务内。
- 公共订阅：已移出v1并保持停用。保留的实现仍只允许源白名单（local 可用演示源），匿名读取已发布不可变 generation；公共投影不读取个人链接/关注。未来只有出现明确免登录分享或合作分发需求时再启用并重新验收。
- 官方转播：维护者核验具体场次/证据、地区及观看条件后发布，最多7天有效期；草稿与发布内容分离。发布/撤回/到期与 fanout outbox 原子写入同一分区。HTTP 探测不代表播放或官方身份；设备观察单独记录。地区 include/exclude 和个人 block 保留。过期会在后台调度撤回；原生日历何时刷新不由服务端保证。

## 首批地区与来源

用户确定美国 US、中国 CN（2026-09-13）。以下是版权/平台级来源核对，**不是具体场次播放验收**。新增候选域名只允许稳定 HTTPS 内容页面，继续拒绝凭据、媒体流及不安全跳转。

| 运动 | 美国 | 中国 | 官方依据 |
| --- | --- | --- | --- |
| F1 | Apple TV | 腾讯体育 | [Apple TV 观看说明](https://tv.apple.com/us/info/watch-f1)、[F1 与腾讯至2027协议](https://corp.formula1.com/formula-1-renews-partnership-with-tencent-to-broadcast-f1-in-mainland-china/) |
| 英超 | NBC / Peacock，按场次 | 咪咕 | [英超2025–28转播商](https://www.premierleague.com/en/media/broadcasters)、[Peacock专区](https://www.peacocktv.com/sports/premier-league) |
| NBA | ABC/ESPN、NBC/Peacock、Prime Video，按场次 | 腾讯或咪咕 | [NBA观看指引](https://www.nba.com/news/how-to-watch-games-2026-27-season)、[NBA中国访问说明](https://support.watch.nba.com/hc/en-us/articles/115000586373-Accessing-NBA-League-Pass-in-China) |

美国与中国真实场次链接尚未冻结/发布，也未验证当地账号、付费权限、App直达或播放。不会将平台首页批量附加到赛事。

## 当前部署与待验收状态

候选`aff58eb`已于2026-09-13部署现有开发环境；后续`cfb3798`取消关注隐藏修复同样已部署并包含P2。见[部署证据](../evidence/p2-deployment-2026-09-13.md)及[最新代码部署](../evidence/personal-feed-unfollow-deployment-2026-09-13.md)。没有结构性Cosmos迁移；个人Feed URL与UID算法保留。新路由读回通过，不等于真实账号、公共投影和转播播放已验收；公共源白名单为空，转播联网检查未启用。

真实账号删除验收必须使用独立测试账号；真实用户账号不作为破坏性样本。公共源白名单保持为空，除非未来出现明确的免登录分享或合作分发需求；官方转播维护者身份仍需在环境中核验后启用。外部 MCP 客户端接入仍属于 P3。
