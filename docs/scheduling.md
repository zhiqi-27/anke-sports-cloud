# 持续更新与调度

本地 worker 和 Azure 入口共用业务调度；进程内计时只决定何时检查数据库，更新是否到期由持久状态决定。当前实测为本地 SQLite，Azure/MySQL 尚未联调。

## 体育数据

worker 启动立即检查，之后每分钟检查一次。只有启用的已知 Provider，且距离上次成功及上次执行均至少六小时、冷却期限已到，才可创建新一轮任务。已有 pending/running 任务继续负责重试，不再另起一份。失败任务的退避、Retry-After、租约及五次上限沿用原规则；终止失败后的新周期也不会因重启而无限加速。

手动获取和自动调度先锁 Provider，再读取当前任务状态，复用同一份待处理工作。用户手动获取可以早于六小时周期，但实际执行仍遵守 Provider 冷却。SQLite 用写锁、MySQL 用当前锁定读取；真实 MySQL 并发和锁等待尚需测试。

Azure 的 `update_schedules` 改为每分钟检查；`update_content` 保持每五分钟。`use_monitor=True` 保留。Timer 的表达式和监控参数参考 [Microsoft Timer trigger 文档](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-timer?pivots=programming-language-python)。源代码配置通过本地检查，不代表宿主已部署或触发。

## 直播入口

到期处理与网络巡检各自读取最多100个候选ID，按到期时间及ID排序；每条记录独立事务，不加载整个已发布集合。新索引分别覆盖 `status/expires_at/link_id` 和 `status/next_check_at/link_id`。

审核期限统一存储为UTC。到期检查不受自动联网开关影响；撤下过期链接时，审计、事件变化与Feed发布outbox原子提交。候选读取后再次锁定并重读当前审核版本，防止撤下刚刚续期的记录。

自动联网默认关闭。启用后复用手动检查的去重判断，按记录与当前URL摘要寻找待处理任务；旧URL的任务不会阻止新审核链接被检查。列表查询先排除已有任务，避免待重试记录占满候选页。HEAD仍由队列执行，调度器自身不联网。

成功检查通常在六小时后再查，距离开赛不足24小时则约每小时检查；从较远的时间进入这24小时窗口时，也会在边界醒来。来源改期会重新唤醒相关已发布链接的检查，不改变既有审核和网络证据。临时失败保留原来的一小时重查规则。

单轮的候选数有上限；积压会在后续轮次继续处理。大批到期记录、1,000用户投影发布、大量历史outbox下的相关子查询，以及真实MySQL/Azure端到端延迟仍需容量验证。定时任务不能保证外部日历客户端立即拉取。

## 迁移与恢复

迁移 `c72b961e430a` 增加 `expires_at` 和两个索引。旧审核记录以空 `expires_at` 标识尚需归一化，后台每轮最多处理100条；已发布记录的 `next_check_at` 被置空，以便重新评估巡检。已有URL、审核内容、UID和发布版本不被迁移改写。联网仍受原配置控制。

新版本需要先停止服务并完成迁移，再启动API/worker。生产迁移必须明确独立目标和恢复方案。当前本机已备份到 `data/before-scheduler-20260910-032643.db`，权限0600，完整性检查通过。24张原表在迁移和重启后原字段/行哈希相同（主库没有直播审核记录，因此无旧巡检时间需重置）。有旧审核数据的升级、回退、再升级和模型检查另在临时SQLite中通过；MySQL只验证离线DDL。

回退先停止写入并保存迁移后的新增业务数据，再选择旧代码配合 `alembic downgrade a8c502e7d134` 或恢复备份。降级保留审核数据，但不会还原迁移重置的旧巡检时间；不能直接覆盖已有新写入的主库。加密密钥继续使用原独立密钥，勿将其放入仓库。

## 复现

```sh
uv run pytest -q tests/test_scheduling.py tests/test_scheduling_migration.py
uv run python -m experiments.scheduler_capacity --output evidence/scheduler-capacity-new.json
```

脚本要求新输出文件、本地环境，自动建立和清理临时SQLite。两个全新进程调用真实worker入口，只有Provider抓取替换为明确合成输入；2万条比赛与直播记录用于验证候选加载、任务入队和实际SQL查询计划。不会调用真实Provider或HEAD，也不会修改主库。完整证据见 [本批验收](../evidence/scheduling-2026-09-10.md)。
