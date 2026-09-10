# 文档仓储与发布器第一批实现

2026-09-10。这是 Cosmos 迁移的代码与本地验证里程碑；现有产品 HTTP/Functions 入口仍为 SQL，Azure 资源未创建，不能作为 Cosmos 云上线证据。

## 实际通过的检查

- **267 passed、2 skipped、2 warnings**：提取共享日历规则、调整配置保护、加入首批 30 个文档测试后执行完整后端 pytest。两项跳过需要显式本机 MySQL，两个警告来自已有 Starlette/AnyIO 弃用。
- 随后补充过期租约重新领取、删除标记优先于幂等回执、发布块损坏拒绝返回等 3 项测试；最终文档专项 **33 passed**，ruff 通过。依赖固定为 `azure-cosmos==4.17.0`，其他已锁定版本未升级。
- 新进程独立打开临时持久文档库并读回已发布 ICS，没有导入 SQL 运行时；选择文档模式时误导入 SQL 明确失败。
- 当前 OpenAPI 与已提交契约相同。客户端无源码或契约变化，没有重复 Web 构建。
- Strong 模板 Bicep 编译和 Azure 订阅级 Provider validate 均通过；验证参数 Web URL 为 `.invalid` 占位，仅供 validate，未创建或部署任何资源。

事务测试使用独立连接的本机文档适配器，验证并发写入只能提交一份、配置/回执/outbox不部分提交、用户隔离与分页、创建/配置/Feed令牌身份、改期保持UID和SEQUENCE变化、相同内容版本/时间/ETag稳定、未关注历史比赛保留、移除未来比赛保留原UID取消通知。

250 个含长中文/emoji场馆的合成事件触发多块发布；注入中途存储失败后旧 Feed 正文/ETag 保持，恢复后完整250条可读。写完块后再注入配置变化、账号删除标记或租约更换，旧发布者无法切换指针，也不能完成任务。删除已发布块时读取失败，不返回半份日历。

## SDK 证据和修正

使用真实 Cosmos SDK，传输层完全离线且拒绝未预期请求，没有 Azure 数据面调用。直接检查 SDK 发出的事务请求：单一 partition header、atomic标记、If-Match、参数化有界查询、Strong头。读取404、批操作409/412、429等待与403分类另有检查。

测试发现 SDK 的 `CosmosBatchOperationError` 不继承 `CosmosHttpResponseError`，现已分别处理，避免把版本冲突误报为可重试服务故障。测试也证明仅 `logging_enable=False` 仍会让 SDK 的 HTTP 日志包含 partition header；客户端改为使用独立禁用的 logger，应用只输出操作类型、逻辑容器、状态、RU与耗时摘要。测试中的 RU 2.5 是传输夹具值，**不是 Azure 实测 RU**。

依据：[SDK 官方用法](https://learn.microsoft.com/en-us/python/api/overview/azure/cosmos-readme?view=azure-python)、[事务批处理](https://learn.microsoft.com/en-us/azure/cosmos-db/transactional-batch)、[一致性保证](https://learn.microsoft.com/en-us/azure/cosmos-db/consistency-levels)。服务端读取采用 Strong 以保留跨实例撤销语义；费用需按实际 RU 重新测量。

## 未完成

同一产品 HTTP 路由与 Firebase、权威公共赛事/来源索引、Queue/Change Feed 分发和补发、创作者/个人链接/删除/OAuth全部模块、最大配置分块、旧/孤立generation GC、SQL导入、真实Cosmos MI/RBAC/RU/429/恢复、设备刷新。本轮未重启已有主预览和Firebase服务，没有切换其存储或写入实验数据。设计和代码入口见 [存储合同](../docs/cosmos-storage-design.md)，机器摘要见 [JSON](document-foundation-2026-09-10.json)。
