# 文档存储的日历纵向路径

2026-09-10。Anke Sports 的开发、生产云目标均为 Cosmos NoSQL **Serverless + Periodic**；以后按实测负载原地转手动 Provisioned，再调整 Autoscale。Azure 资源尚未创建。Anke Money 当前生产组合以用户本次说明为准，没有读取或修改其资源。

## 当前可运行范围

`app.main` 按 `ANKE_SPORTS_STORAGE_BACKEND` 选择组合：`sql` 保留完整迁移基线，`documents-local` / `cosmos` 使用新的 `document_api`。新路径没有导入 `app.db`，失败不回落 SQL。原主预览和 Firebase 实例仍使用 SQL。

已接入同一路径和响应模型：健康/集成状态、来源、比赛列表/详情、本地或 Firebase 身份验证、个人日历、关注预览/保存、偏好、配置导出、私人地址/轮换/暂停/恢复/预览、ICS GET/HEAD/304。API 验证 Firebase ID token 的代码共用原验证器，启用撤销检查；**本批没有重新进行真实 Firebase 验收**。

本批新增个人原链接附加、屏蔽/固定、单场加入/排除/重置、配置导入预览与确认。链接、配置、新任务和精确幂等回执在个人分区同事务提交；屏蔽后重新附加不会撤销屏蔽。共享链接筛选和导入规则由 SQL 与文档模式共用，合并超出配置上限返回明确错误。预览不修改配置或创建任务，确认绑定账号、版本和完整配置。未迁移创作者在导入中标为 unresolved，拒绝应用。

尚未接入文档模式：体育 Provider 抓取/定时更新、创作者/YouTube、直播、公共 Feed、账号删除、OAuth/MCP、Google 直连。相关接口明确返回 `DOCUMENT_FEATURE_UNAVAILABLE`。包含尚未迁移创作者配置的账号不能被静默发布为空链接日历。设置页的 Provider/YouTube 不代表后台已启用。

完整客户端合同继续以 SQL 基线导出，并已核对未变化。导出脚本拒绝在文档模式下覆盖完整合同，避免误把当前接口子集作为产品全部接口。新路径不是完整 Cosmos 迁移，也不是可直接公测的部署包。

## 公共赛程与身份

可信 Provider/迁移器通过 `Catalog.publish` 提交规范化事件和来源，传入抓取前的 `expected_revision` 与明确的完整抓取结果；没有开放上传真实赛程的 HTTP 入口。

权威入口为 `state / provider:<provider> / schedule`，引用按内容摘要寻址的不可变块。块位于 `state / catalog:<provider>:<摘要前两位>`，包含事件 ID 分桶、日期月份查询和来源；`indexes` 仅负责 Provider 发现与身份归属预留。缺块或摘要不符时读取失败，不返回半份赛程。新批次指针与 `catalog_changed` outbox 在 Provider 分区内原子提交；不能把跨分区块准备描述为全局事务。

输入遗漏不会删除旧比赛，取消需显式事件状态。改期保持事件 ID；同一来源键替换 ID、重复记录、跨 Provider 抢占 ID 被拒绝。内容相同保留原版本/更新时间；真实变更使用本服务更新时间驱动列表游标失效。前一完整快照仍可读，失败暂存块暂不自动清理。

个人 Feed 发布读取已完成的赛程快照，使用共用日历规则，并在切换正文前再次核对赛程版本、账号/Feed ETag 和任务租约。跨分区变化通过后续任务收敛；不承诺全库瞬时事务。

## 后台投递

大配置与大回执通过 `document_values` 存为个人分区内的不可变分块和摘要清单，全部准备完成后才在命令事务中切换 `config_ref` / `result_ref`。读取验证每块及完整摘要。2,000 条长链接覆盖规则的导入、精确回执重放、新连接重读、Feed 发布与令牌轮换均已本地验证；分块失败、最终提交失败、坏块、删除后的重放均不会返回部分配置。未引用块暂不自动清理，不代表长期存储成本已受控。

发布器在读取个人链接前捕获账号 ETag，在最终发布时同时验证账号、Feed 与租约；链接更新即使保留配置版本，也会使旧快照失效。

业务先原子提交 outbox，再由 `document_worker.dispatch` 读取 `state` 的 LatestVersion Change Feed，发送只包含版本、分区和任务 ID 的 Queue 消息。断点和投递租约放在 `indexes`，避免断点更新触发自身。全部发送成功后才能前移断点；发送成功但响应丢失、断点提交失败均可重复发送，任务租约/CAS 拒绝重复业务提交。

运行中任务的租约到期时间也投递为延迟消息；可重试失败写回带退避时间的 outbox，由新变更补发。最多五次执行，任务和业务完成同事务；不吞掉无法持久化的失败记录。每次 Functions 投递调用独占一个 SDK 客户端，避免共享 `last_response_headers` 干扰续读状态。

SDK 固定 4.17.0，离线实测发现：跨多个物理分区只读取第一分页后，其初始续读令牌仍可能含未访问分区的空 token，SDK 自身恢复时拒绝。实现使用公开 `read_feed_ranges` 和分页 API 完成首轮，再保存原始 opaque token，不解析/篡改 SDK 令牌。首次最多100个范围，后续一次有界分页；物理分区真实拆分/恢复还需云验收。[SDK 源码](https://github.com/Azure/azure-sdk-for-python/blob/azure-cosmos_4.17.0/sdk/cosmos/azure-cosmos/azure/cosmos/_change_feed/composite_continuation_token.py)、[公开接口](https://learn.microsoft.com/en-us/python/api/azure-cosmos/azure.cosmos.containerproxy?view=azure-python)。

文档模式的 Azure Functions 注册 HTTP、分钟投递、Queue 处理和每日窗口任务；不会启动尚未迁移的 SQL Provider/YouTube 定时器。每日窗口任务与公共赛程变更按每页100个已登记 owner 生成幂等个人任务。当前每次变化仍遍历 owner 目录，反向关注索引及费用验证是后续工作，不能声称已具备规模化成本证据。

## 本地检查

独立 UI 验收使用已有 `:3002` 客户端构建，并在另一个端口代理 HTML。所有比赛标为演示，临时库和独立 worker 在停止实验后清理：

```sh
ANKE_DOCUMENT_UI_PORT=3007 uv run python -m experiments.document_ui
```

打开 [日历](http://localhost:3007/calendar) 或 [我的关注](http://localhost:3007/following)，进入本地体验，选择演示联赛、预览、保存；也可以单场加入并手动附加原链接。12条演示比赛包含历史场次。该实验的持久文档/本地队列都使用独立 SQLite 文件，只是显式本地适配器，**不是 Cosmos 或 Azure Queue 模拟器**。不会读写原用户数据库或调用平台。端口未指定时默认3006；本次3007为新代码，旧3006预览保持原进程。

最新288项回归通过、2项条件MySQL跳过。浏览器单场加入→附加演示链接→移除→再次附加仍屏蔽，以及独立HTTP/worker的同UID与SEQUENCE 2→3已读回，见 [链接与配置证据](../evidence/document-content-2026-09-10.md)。演示URL未访问，不是视频匹配、可播放性或设备证据。

普通文档模式需明确配置 `ANKE_SPORTS_STORAGE_BACKEND=documents-local`、独立 `ANKE_SPORTS_DOCUMENT_LOCAL_PATH`、加密密钥及本地身份开关，再分别启动 `uvicorn app.main:app` 与 `python -m app.document_worker`。本地队列支持持久延迟消息；普通本地 worker 还没有接入每日窗口调度，不替代 Azure Timer 验收。

本地日志只存变更的容器/分区/文档身份，不保存历史私人正文；事务回滚同时回滚变更日志。旧的本地文档库会补建日志并登记当前文档，不允许打开原业务 SQLite 库。

## 剩余验收

真实 Cosmos 身份权限、RU/429、分区拆分和吞吐、Azure Queue/poison/Timer、Periodic 恢复、删除决定重放、SQL 数据迁移仍未通过。历史任务/孤立块 GC、超大 Feed 流式读取与反向索引仍需实现；当前链接列表按用户分页加载，规模/RU仍待验证。容量转换保持独立管理流程，参见 [存储与扩容方案](cosmos-storage-design.md)。
