# Anke Sports · 当前架构决策

2026-09-09。用户最新决策优先于原 v1 文档中的建议技术栈。

| 边界 | 决策 | 状态 |
| --- | --- | --- |
| 产品 | Anke Sports；桌面 Web、Chrome 扩展、外部 MCP | 已确认 |
| UI | Apple Sports 的克制色彩、紧凑卡片和文字层级；桌面月/周/日程及事件抽屉 | Web 本地实现 |
| 身份 | Firebase Auth 客户端 ID Token，Admin 验证签名、发行方、有效期与撤销 | 入口已写，真实账号待验 |
| 权威业务 | Python/FastAPI，HTTP/Queue/MCP 共用服务层 | HTTP 与任务处理已实现；MCP 待接 |
| 数据 | Azure MySQL；SQLite 为显式本地适配 | SQLite 验证；MySQL 迁移与 TLS 待云实测 |
| 后台任务 | SQL outbox 与业务变更同一事务；Azure Storage Queue/timer，重试/租约 | 本地验证；Azure 触发器待实测 |
| 日历交付 | 发布时计算投影、保存 ICS；读取只返回稳定内容与条件请求响应 | 本地验证 |

```mermaid
flowchart LR
  Web[桌面 Web] --> API[FastAPI / Azure Functions]
  Extension[Chrome 扩展 · 待实现] --> API
  MCP[MCP · 待实现] --> API
  Auth[Firebase Auth] --> API
  API --> SQL[(Azure MySQL)]
  SQL --> Outbox[事务 outbox]
  Outbox --> Queue[Azure Queue / Timer]
  Queue --> Worker[赛程 / 内容 / 投影 worker]
  Worker --> SQL
  SQL --> ICS[已发布的 ICS]
  ICS --> Calendar[Apple / Google 日历]
```

## 关键规则

- 公共赛事 ID、个人投影 ID 与 Feed 访问令牌分别保存。改期或补充内容不改变 UID；内容不变不增加 SEQUENCE、DTSTAMP、ETag。
- 当前 F1 输入使用 season + circuitId + session 作为来源键；不使用可重排的轮次和开始时间。若同季多站共用同一 circuitId，停止该批更新并要求显式消歧，避免合并。
- 原始第三方响应成功且全部分页完整后才更新；任何异常回滚整批，不按缺失结果清空赛程。
- 历史投影保留近 90 天；默认发布未来 180 天。取消关注后的近期历史记录继续接收内容；明确排除比赛生成取消投影。
- 私人链接和公共来源分离，用户 block 覆盖自动发现。自动发现、匹配与待确认已通过隔离本地测试；手工添加不获得官方认证。
- 公共接口返回已接入来源，未承诺全运动、全赛季覆盖。时间以赛事官方发布为准。
- 列表当前针对早期数据规模；游标分页、批量读取、20k 赛事压测仍需完成，不能据此宣布规模验收通过。

## 交付顺序

M0：ICS 真日历变更、内容直达矩阵、三类 Provider、YouTube 推送 PoC。外部实验未通过前，不承诺手机自动更新时间或具体 App 唤起。

M1：先让本地 Web → 关注 → 持久任务 → 个人 ICS 可操作，并补真实 Firebase 和 staging 验证。当前实现处于此阶段；实现结果不替代 M0 设备证据。

M2：YouTube 通知/续订/补查、作者范围、明确匹配/待确认/人工纠错；补齐 NBA、足球、F1 适配器边界与覆盖证据。

M3：明确审核来源的本场直播入口、地区/观看条件、链接失效维护。

M4：Chrome 380×560 独立弹窗与 MCP，统一账号、配置、权限与接口。MCP 认证与敏感写操作需按目标客户端实际能力验收。

M5：恢复演练、完整 QA、支持范围、许可与开源发布。M6：Google OAuth 独立辅助日历增强同步。

不承诺固定周数。原始 T01–T35 与 QA-01–QA-32 编号保留，逐项附证据再改完成状态。

## 云环境准备

用户已确认尚未创建独立资源，先完成本地实现；之后授权使用托管 Chrome 创建和配置，账户登录由用户完成。需独立 Anke Sports Firebase 项目、Azure 订阅/资源组/区域、Web 与 API HTTPS 地址、MySQL 与 Storage。部署前明确目标和费用边界，验证 MySQL 备份恢复与迁移回滚；无需改动现有 FormaLM。

Firebase projectId 与加密 key 在非 local 模式强制要求，数据库连接验证 TLS。Feed 私密路径还需要验证 Azure 平台请求遥测与反向代理日志脱敏；目前仅关闭本机访问日志和降低 Functions 主机日志级别，尚不能声明云端秘密不落日志的验收通过。
