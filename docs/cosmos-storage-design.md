# Cosmos 存储与开发环境

2026-09-10 用户决定：Anke Sports 的**开发、生产环境都使用 Cosmos DB for NoSQL Serverless + Periodic**，不会因部署到生产就默认改为 Provisioned 或 Autoscale。后续负载增长时，原地转手动 Provisioned，完成后调整为 Autoscale。Firebase 继续负责身份，FastAPI/Azure Functions Flex Consumption 负责业务，Web/扩展/MCP 不直连数据库。当前 Azure 资源尚未创建；此处是环境目标，不是生产部署记录。

部署模板已替换为该目标；原 MySQL 模板保存在 `infra/legacy-mysql/`，停止推进。文档存储、完整赛程快照、日历 HTTP、租约与 Change Feed/Queue 投递已接入，见 [当前运行范围](document-runtime.md)。**完整 Cosmos 迁移未完成；当前接口子集不能作为公测部署包。** 现有本机预览、Firebase 验收库和已通过的 SQL 测试保持各自证据。

## 独立开发资源

订阅与租户沿用已核对的 Anke Sports 开发目标，资源组 `anke-sports-dev`，区域 East Asia。所有资源新建于本产品范围，不使用 Anke Money/FormaLM 数据或身份。

| 项目 | 配置 |
| --- | --- |
| 数据库 | 单区域 Cosmos NoSQL，Serverless，无预留吞吐、无免费层、Strong consistency |
| Periodic | 每 240 分钟一次，保留 8 小时，Geo 冗余；两份为开发默认值 |
| 计算 | Functions Flex，Python 3.12，2 GB，无 always-ready，最大实例数40 |
| 数据身份 | 独立用户分配托管身份；Cosmos Data Contributor 仅作用于本数据库 |
| 网络 | Functions 独立子网的 Cosmos service endpoint；数据库 VNet 规则限制，仅显式传入的开发者公网 IP 例外 |
| 凭据 | Cosmos 禁用本地 Key 认证；Firebase/Feed 凭据存独立 Key Vault；队列使用托管身份 |
| 容器 | `state`、`indexes`，均使用 `/pk`；无 throughput/autoscale 参数；默认不自动过期 |

`state` 不为大 payload、密文、ICS 正文建索引；调度字段位于顶层。`indexes` 是可重建查询/路由投影。模板不开启分析存储、不引入 Private Endpoint 固定开销。当前没有申请新的云资源或开通收费实例。

周期备份默认两份免费，额外份数按备份存储计费。恢复通过 Azure 支持流程恢复到新账号，网络、防火墙和数据面 RBAC 等需重新配置；不能把“已开启备份”写成“已完成恢复演练”。参见 [Periodic 备份](https://learn.microsoft.com/en-us/azure/cosmos-db/periodic-backup-restore-introduction)。

实现中将最初 Session 默认值改为 **Strong**：Session 的读写保证依赖同一客户端/共享 session token，新的 Functions 实例不能据此保证立刻读到另一个实例提交的私人令牌撤销。账号、Feed 和索引读回使用 Strong，SDK 不降低一致性；这是为保留已有撤销语义所作的实现选择，仍采用 Serverless + Periodic。Strong 读取 RU 通常约为 Session 的两倍，早期费用示例必须按实测 RU 更新，不能把原请求数直接当 RU。更新后的模板已重新通过 Azure Provider validate。参见 [一致性保证](https://learn.microsoft.com/en-us/azure/cosmos-db/consistency-levels)、[一致性覆盖限制](https://learn.microsoft.com/en-us/azure/cosmos-db/how-to-manage-consistency)。

## 事务与分区设计

以下是迁移实现合同，当前 SQL 代码不能视为已经满足 Cosmos 合同。

| `state.pk` | 原子管理的内容 | 读写要求 |
| --- | --- | --- |
| `user:<SHA256(Firebase UID)>` | 个人配置/撤销标记、Feed 身份及发布指针、链接/屏蔽、命令回执、个人 outbox、授权记录 | 命令读取最新账号状态，用 ETag/CAS 同批提交配置、回执与 outbox；旧身份/旧回执不得恢复已删除用户 |
| `event:<稳定比赛ID>` | 单场公共比赛版本、来源映射引用、公共链接与变更 outbox | 改期保持同一 ID；发布新版本后通过可重放 outbox 更新索引与个人投影 |
| `channel:<频道ID>` | 频道发现租约、视频元数据、通知去重、续订状态、相关 outbox | 租约令牌与 ETag 拒绝旧 worker；个人屏蔽仍由用户分区决定 |
| `provider:<Provider>` | 已完成赛程批次指针、抓取租约/错误、批次发布意图 | 先完整暂存分页及清单，再提交批次指针；失败保留上次有效赛程 |
| `budget:<YouTube项目ID>` | 太平洋日预算、预留计数、限流恢复时间 | 每次真实 API 调用前独立事务预留；业务失败不退额，换 Key 不重置 |

每个文档有固定 `id`、`pk`、`kind`、schema/version、顶层索引字段和业务 payload。动态唯一性使用同分区确定性 ID 与 create-if-absent；不得依赖跨分区唯一约束。迁移保留 SQL 中既有比赛/Feed/链接身份和 UID，不按新容器生成替代身份。

Cosmos transactional batch 仅覆盖同容器、同逻辑分区；大小/操作数上限必须在仓储边界校验。跨用户批量发布与跨频道变更采用重放和条件提交，不能描述为一次全局事务。参见 [事务批处理](https://learn.microsoft.com/en-us/azure/cosmos-db/transactional-batch)。

## 发布、索引与后台投递

- 大 Feed 先写不可变 generation 文档/分块和完整清单，确认全部写入后，以账号状态与旧发布指针的 ETag 条件切换。读端只读取完成代次；未完成或失败时继续返回旧正文。内容相同时不切换代次、版本、时间或 HTTP ETag。
- 私人 token 的 hash lookup 只定位 owner/Feed；必须再读取用户分区的撤销状态与发布指针。缓存或旧索引不能绕过删除决定。原始 token 不进入分区键、日志、追踪或导出。
- 赛事日期/来源索引、频道关注者索引和任务到期索引可重建。索引变化与源分区之间按版本去重；查询回读权威版本。先定义有界索引分桶与游标，避免分钟定时器扫描整个 `state`。
- 业务与源 outbox 同批提交后，由投递器发 Queue 消息；重复投递由任务ID/执行租约拒绝，投递失败可补发。跨分区通知分别建任务，完成记录须证明每个目标版本已处理。
- 更新账号删除时立即撤销用户分区，外部 Firebase 清理独立重试。Periodic 恢复或 SQL 导入之前必须重放删除决定，不能仅恢复一个较旧的数据库快照。
- 所有仓储入口记录聚合操作的 RU、延迟、429/重试次数与不透明任务标识；禁止记录文档 payload、Feed 路径或身份凭据。

## 实现与验收顺序

1. 抽出配置/身份、Feed、事件和 outbox 仓储；实现 SDK ManagedIdentityCredential、明确的本地身份入口、ETag/批处理错误映射和 RU 计量。通过环境显式选择适配器，禁止 Cosmos 失败后回落到本地 SQL。
2. 完成同一产品 HTTP 路由的纵向路径：真实 Firebase → 关注写入/冲突/幂等 → 原子 outbox → Queue 重投 → 个人 ICS。先证明身份、UID、版本、撤销与大 Feed 发布，再扩展覆盖。
3. 迁移公共赛事/Provider 完整批次、YouTube 共享内容/配额/租约、人工纠错/屏蔽、直播、公共 Feed 与 OAuth/MCP。保持现有 OpenAPI 和配置导入导出兼容；必要契约变化同步客户端。
4. 使用真实独立 Cosmos 验证并发冲突、429、执行崩溃恢复、RU、长 Feed、反向关注索引和删除；既有 SQLite/MySQL 测试作为回归，不能替代 Cosmos 证据。
5. SQL→Cosmos 导入先 dry-run 清单，再校对逐项身份/数量/内容摘要和私人令牌保护。新环境通过完整验收后才切换；原本机数据保留为可恢复基线。
6. 校验新目标资源/费用/HTTPS 来源、部署运行时、验证 Firebase/Timer/Queue/WebSub/平台日志，并实施 Periodic 恢复演练。设备日历和客户端直达独立记录。

## 当前代码与明确边界

最新Provider批次：三类共用解析器、文档状态/发布事务、定时抓取与每日窗口已接入；真实Jolpica到85条个人ICS通过。生产启用来源需显式配置；302项回归及最后20项针对性检查见 [证据](../evidence/document-providers-2026-09-10.md)。真实云仍未验收。

当前新增：个人链接/屏蔽/固定、单场选择、配置导入和大配置/回执分块；288项回归及独立UI/HTTP检查通过，见 [最新证据](../evidence/document-content-2026-09-10.md)。下文保留前两批的证据范围。

最新批次：新增 document_catalog、document_runtime、document_api、document_worker；app.main/function_app 按存储类型组合，原 SQL 入口移至 sql_app。277项回归通过，独立进程和浏览器/HTTP确认12条演示事件已发布。内容、公共 Feed、OAuth/MCP、删除等尚未迁移，真实云与设备未验收。[说明](document-runtime.md) / [证据](../evidence/document-runtime-2026-09-10.md)。以下首批仓储证据保留。

| 模块 | 已落地内容 |
| --- | --- |
| `app/document_store.py` | Cosmos SDK 4.17.0、明确 MI/本地 CLI 身份、同分区批处理/ETag、有界分页、错误/RU 摘要；独立 SQLite 文档适配器仅供本地验证 |
| `app/document_accounts.py` | 账号/Feed/首个任务同批创建，配置/回执/新任务同批提交，冲突拒绝，重复命令，令牌路由与轮换，领取/过期租约/完成条件 |
| `app/document_feeds.py` | 不可变分块及完整摘要清单，账号/Feed/任务同时 CAS 后发布；相同内容保留公开版本、时间和 ETag，旧 worker/中断不能覆盖旧发布 |
| `app/calendar_rules.py` | 从原 SQL 日历模块提取共用选择、历史保留、内容描述、ICS 序列化规则；原模块继续导出相同入口 |

当前通过 33 项文档存储/配置测试，包括真实 Cosmos SDK 配合完全离线传输层的请求序列化与错误处理检查；250 个合成事件多块发布中断后保留旧 Feed，单独进程重读持久文件通过。全量回归曾通过 267 项/2 项条件 MySQL 跳过，随后补充 3 项边界用例并执行上述 33 项。OpenAPI 未变，**没有真实 Cosmos RU、数据面认证、恢复或 HTTP/Queue 纵向证据**。详情见 [本批证据](../evidence/document-foundation-2026-09-10.md)。

`ANKE_SPORTS_STORAGE_BACKEND=sql|cosmos|documents-local` 必须明确区分。`app.main` / `function_app.py` 已接入文档日历组合；选择文档模式后导入 `app.db` 仍会明确失败，防止误用本地 SQL。完整产品切换须等剩余模块和云验收完成。真实 Cosmos 只用独立托管身份；本机 CLI 需明确 tenant/subscription，且不得用于部署环境。个人链接/创作者/删除/OAuth 等剩余模块接入并验收后再切换主服务。

第一批批处理上限 100 操作、按 SDK 转义后的 JSON 计 1,000,000 字节，单文档上限 512,000 字节；Feed 分块原文为 128,000 UTF-8 字节，含 emoji/转义膨胀验证。最大配置/回执分块已由最新批次本地验证；超大 Feed 流式读取、未发布/旧 generation 清理和资源恢复仍需实现与验收。部分写入只留下不可见块，当前不自动 GC，不能宣称存储占用已受长期控制。发布器要求调用方提供权威赛事与已筛选链接，尚未用它代替 Provider 完整批次协议。

## 后续升级容量

Anke Money 生产环境已由用户说明改为 Serverless + Periodic；此前“生产为 Autoscale”的记录仅是旧的只读快照。没有重新读取或修改 Money 资源。

Azure 支持账号原地从 Serverless 转手动 Provisioned，转换所有容器，初始每个容器按其物理分区数 × 5,000 RU/s 配置。迁移完成后再调整手动吞吐或改 Autoscale。转换不可逆，进行期间不能执行管理操作，迁移耗时没有 SLA。参见 [原地转换官方说明](https://learn.microsoft.com/en-us/azure/cosmos-db/how-to-change-capacity-mode)。

按实测月 RU、持续/峰值负载、429 与计费对比评估时机，不按注册人数自动触发。升级前冻结容器及物理分区清单、估算转换当时和 Autoscale 的费用，确认恢复策略；转换后先读回所有容器状态，再调整 Autoscale 上限，并更新 IaC，防止再次应用当前 Serverless 创建模板。容量升级不隐含切换备份模式，Periodic 继续保留；将来如需改备份，再作为独立变更。
