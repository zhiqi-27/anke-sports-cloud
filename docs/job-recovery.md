# 后台任务、失败处理与恢复

本地与 Azure 入口共用 `app/worker.py`、`app/jobs.py`。SQL outbox 是任务状态的权威来源；Azure Queue 只发送任务 ID，重复消息不会构成第二份业务命令。字段、迁移和本地恢复证据已实现，Azure/MySQL 实际运行仍待验收。

## 领取与提交

领取通过任务 ID、当前状态、尝试次数及到期时间的条件更新完成，租约为 5 分钟。每次执行保留自己的领取版本；完成或失败必须再次匹配该版本。业务变化、后续 outbox 与完成记录在同一事务提交，过期 Worker 晚返回时整笔结果回滚。任务被取消或账号删除移除了任务后，旧 Worker 也无法提交。

同一个体育 Provider 另有共享租约，阻止两个不同任务同时发布该来源的结果。其他任务等待时不消耗尝试次数；本地最多约 15 秒复查正在使用的 Provider，熔断等待保持其原期限。个人 Feed 在读取配置前锁定所属用户，再读取当前配置，避免缓存快照覆盖新关注。SQLite 的锁行为与 MySQL 不同，不能将本机并发测试当作 Azure MySQL 证明；锁定读取与 ORM 刷新行为参考 [SQLAlchemy Session API](https://docs.sqlalchemy.org/en/20/orm/session_api.html)。

YouTube已增加按频道的数据/Hub两类执行租约与相同的条件提交，网络重试冷却不会阻止本地重新匹配。详见 [频道并发与恢复](channel-concurrency.md)。

租约不构成外部 HTTP 的“只调用一次”承诺。若执行超过 5 分钟被重新领取，可能重复读取上游，旧结果会被拒绝。WebSub 包含用于异步核验的预提交意图，外部订阅请求允许按协议重发；实际 Hub、长耗时任务及大规模视频并发仍需单独验收。

## 重试、熔断与界面

- 最多 5 次实际执行，包括未报告结果的崩溃。最后一次租约失效后进入 failed，不再无上限执行。缺少 API key 直接进入 failed。
- 普通失败指数退避；429/503 支持 Retry-After 的秒数与 HTTP 日期，接受的服务端等待上限为 30 天。错误只保存枚举或异常类别，避免请求 URL、header、SQL 参数和密钥进入记录。
- 同一体育来源连续 3 次失败后至少等待 15 分钟，进一步失败延长至最多 6 小时；缺 key 等待 6 小时。完整获取成功后清除失败计数与熔断时间。等待期间保留最后有效赛历。
- `/api/v1/status` 区分 idle/queued/running/waiting。设置页在处理期间轮询，等待期间降低频率，结束后显示成功或失败及最早重试时间；该时间不是手机显示时间。
- `finished_at` 记录任务结果时间，独立于 Feed/事件更新时间。无内容变化的重放不会增加 ICS 版本或 ETag，同时能够清除旧的失败提示。
- 各维护调度独立尝试，某一调度失败不会使本地队列停工。`--once` 有调度/存储错误时非零退出。Azure 队列保持明文 JSON 对应 `messageEncoding: none`，异常向宿主传播时仅包含脱敏代码；配置依据 [Azure Queue trigger](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-storage-queue-trigger)。宿主 poison queue 和告警仍需真实环境验收。

Provider key 可放在进程环境或本地 `.env`：`BALLDONTLIE_API_KEY`、`FOOTBALL_DATA_API_KEY`、`YOUTUBE_API_KEY`。进程环境优先，显式空值会禁用对应 key；配置 repr/JSON 不输出明文。仍需新建独立资源，不能使用 FormaLM 的凭据。

## 查看和重放一条失败任务

先修复失败原因，再列出脱敏任务。命令不会显示 payload 或私人订阅地址：

```sh
uv run python -m scripts.recover_jobs --environment local
```

使用读回的任务 ID 与 attempts，先预览。以下 ID 是需替换的占位符，原因中不要填密钥或私人内容：

```sh
uv run python -m scripts.recover_jobs --environment local --job-id FAILED_JOB_ID --expected-attempts 5 --reason '已修复依赖，重新生成订阅'
uv run python -m scripts.recover_jobs --environment local --job-id FAILED_JOB_ID --expected-attempts 5 --reason '已修复依赖，重新生成订阅' --apply
```

默认 dry-run；`--apply` 创建新的关联任务，原失败记录保留。一个原任务只有一个重放后继，重复运行读回同一个 ID；并发冲突需重新读取，不重置原任务尝试次数。已删除账号的普通个人任务拒绝重放；唯一例外是固定原 Firebase 项目的 `identity_cleanup`，见 [账号删除](account-deletion.md)。新任务仍遵守 Provider 冷却时间，重放不绕过限流。环境参数必须与配置一致；staging/production 运行前仍需明确目标授权和恢复方案。

## 本机恢复实验

```sh
uv run python -m experiments.worker_recovery --output evidence/worker-recovery-2026-09-10.json
```

该脚本新建临时 SQLite 与独立密钥，启动真实子进程，等投影 flush 后终止进程，确认退出后仅推进该夹具的租约时间，然后重领任务。还会测试 CLI dry-run/重放/去重，并把数据库备份恢复到另一文件，逐表比对并校验 Feed 凭据。全部数据为合成；不联网、不修改主体验库，结束删除临时文件。它证明进程退出、事务回滚与恢复流程，不证明真实等待了 5 分钟或云端恢复。

正式恢复必须同时保证数据库备份与对应加密密钥可恢复，密钥独立保管，不进入 Git。停写后备份；在新目标还原并验证身份、Feed 凭据、最后有效投影、outbox 和重放审计，再考虑切换服务。切换前应评估备份之后的数据，不能直接覆盖仍有新写入的数据库。

本机本次迁移为 `6e9edd6daa17`，备份 `data/before-recovery-20260910-005423.db`（0600）。既有 21 表原列/行一致，新增状态字段不反填未知的历史完成时间。MySQL 只检查了离线 DDL。证据见 [恢复验收](../evidence/recovery-2026-09-10.md)。T13/T33 保持 in_progress。

持续调度已改用持久时间判断及分批候选，手动/自动请求共用去重。最新迁移、行为和验收范围见 [持续更新调度](scheduling.md)。
