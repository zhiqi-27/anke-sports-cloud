# Azure 低成本开发环境选择

2026-09-10。用户要求参考 Anke Money，重新评估成本；先前 MySQL / US$40 月预算方案未获批准。用户随后明确选择 **Serverless + Periodic**：采用独立 Cosmos DB for NoSQL Serverless 与周期备份，取代 Azure MySQL 目标。现有 SQL 实现和本机数据作为迁移基线保留，尚未改造成 Cosmos；资源还未创建。

## 建议采用的组合

保留 Firebase Google 登录、Python/FastAPI、Azure Functions Flex、Storage Queue、Key Vault 和托管身份；数据库选为**独立 Cosmos DB for NoSQL Serverless，East Asia 单区域**。Functions 无常驻实例。初期仅准备一个独立开发环境，公测/生产环境另行评估。

早先只读快照曾记录 Anke Money 开发为 Serverless、生产为 Autoscale。用户最新说明生产也已改为 **Serverless + Periodic**，并计划流量增长后原地转 Provisioned，再调整 Autoscale；以该说明更新参考基准，本批未重新读取或修改 Money 资源。只参考计费、升级路径与分区事务设计，不复制账号、数据、凭据或资源；历史账单保持在 Git 忽略文件，不进入开源资料。

## 可核对的价格

币种 USD，区域 East Asia，查询日 2026-09-10。[Azure 零售价格 API](https://prices.azure.com/api/retail/prices) 的选取记录见 [价格证据](../evidence/azure-cosmos-pricing-2026-09-10.json)。

| 选项 | 假设 | 数据库月费用 |
| --- | --- | ---: |
| 原 MySQL B1ms | 全月 730 小时 + 20 GB | US$23.88 基础费用 |
| Cosmos Serverless 轻用量示例 | 100 万 RU + 1 GB 数据及索引 | US$0.56 |
| Cosmos Serverless 较高用量示例 | 1,000 万 RU + 1 GB 数据及索引 | US$3.35 |

Serverless 单价为 **US$0.31 / 百万 RU**，存储 **US$0.25 / GB/月**。RU 是数据库工作量单位，不等于 HTTP 请求次数；查询、写入、跨分区扫描和后台任务均计入。它按实际使用收费，没有预留吞吐的最低费用。[官方计费说明](https://learn.microsoft.com/en-us/azure/cosmos-db/serverless)

Functions 若假设**包含 HTTP、Timer 和 Queue 的总量**为 5 万次/月，平均每次 2 GB × 0.3 秒，按已查询的付费单价且不扣免费额度，约 US$1.13。与上面两个数据库示例合计分别为 US$1.69 / US$4.48，再加 Storage、Key Vault、日志、备份、出站流量和税费。实际调度可能超过该调用假设，需要测量后修订。建议以 **US$5–10/月作为早期低流量的设计目标**，不是报价、实测结果或自动停机上限；体育数据授权和其他第三方服务不包含在内。

Functions 的免费额度按订阅共享，不能替每个产品重复计算。[官方价格](https://azure.microsoft.com/en-us/pricing/details/functions/) Cosmos 免费层与 Serverless 不能叠加；当前订阅的免费层资格已被既有账号使用，不为本产品占用或迁移它。[免费层规则](https://learn.microsoft.com/en-us/azure/cosmos-db/free-tier)

MySQL B1s 已在区域规格清单中出现，但补查其价格遇到零售 API 429；未将未核实的价格列入对比。该选项仍保留常驻数据库费用，优势是保留现有 SQL 实现。

## 实现影响和验收入口

当前 `app/db.py`、`service.py`、`actions.py`、`content.py` 和 `jobs.py` 直接使用 SQLAlchemy、唯一约束、行锁与跨表事务。因此 Cosmos 需要一轮存储与事务边界改造，不能只换连接地址。HTTP/OpenAPI、Web、扩展、MCP 的产品契约尽量保留。

1. 定义个人分区：将同一用户的配置、命令回执、审计和 outbox 意图放在同一容器、同一分区；通过 ETag 条件写入维护版本与并发。公共赛事及共享频道单独分区，公共变更与其 outbox 意图一起提交，再以可重放任务更新个人投影。不能把跨分区更新描述为同一事务。[事务范围](https://learn.microsoft.com/en-us/azure/cosmos-db/transactional-batch)
2. 先完成最小纵向验证：Firebase 账号 → 关注更新 → 原子 outbox → Queue 重复投递 → 已发布 ICS；验证改期和令牌轮换的 UID 稳定、相同内容的 ETag 稳定、冲突拒绝与删除决定保留。
3. 再处理公共赛程索引、创作者反向订阅、项目级配额、任务租约、账号删除和公开/私人 MCP。避免分钟定时器无条件扫描全部分区；记录每个操作的实际 RU。
4. 大型 Feed 发布不能假定无限事务大小。需先写不可变版本及完整清单，再用受条件保护的发布指针切换，读端只读取完成版本；失败继续提供上一有效版本。清理与账号撤销需验证竞态，私人 Feed 令牌仍不进入日志或导出。
5. Cosmos 适配器的真实事务、RU、429 重试、备份恢复、MI/RBAC 和云端 Timer/Queue 需要独立证据。当前 191 项通过的后端测试属于现有 SQL 实现，不算 Cosmos 验收。

选型已确认，正在更新架构约定和部署模板；原 MySQL 创建方案停止推进。Periodic 采用开发环境默认每4小时一次、保留8小时（两份），并显式保留 Geo 冗余；这是实现默认值，可后续调整。周期备份的恢复需要向 Azure 请求并恢复到新账号，不把它当作即时回滚；恢复后重建网络/RBAC并重放删除决定。官方说明：[周期备份与恢复](https://learn.microsoft.com/en-us/azure/cosmos-db/periodic-backup-restore-introduction)。

完整 Cosmos 存储实现、RU/并发/恢复和真实云运行仍待验收；不会把已经通过的 SQL 测试改名为 Cosmos 测试。


新模板、分区/投影迁移合同、Periodic 默认值及后续原地升级约束见 [Cosmos 存储设计](cosmos-storage-design.md)。
