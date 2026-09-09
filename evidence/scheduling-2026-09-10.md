# 持续更新调度验收 · 2026-09-10

后端141项pytest、ruff和差异格式检查通过。新增14项调度用例及1项迁移用例；两个既有Starlette/AnyIO弃用警告仍存在。OpenAPI/config schema重新生成后字节相同，无客户端业务改动或依赖变化，因此没有重复Web/扩展构建。

## 行为证据

- 到期Provider在worker启动时入队并执行；紧接着重启不重复抓取。六小时边界、最近执行、Retry-After冷却、未启用来源和已存在任务均覆盖。
- 6个独立SQLite连接并发运行手动/自动Provider调度只产生1份任务；直播同样验证6连接并发、人工检查与定时检查去重。回滚不会留下检查任务。
- 发布时间支持非UTC偏移，旧记录可分批归一化。关闭联网时仍处理到期，实际Feed只移除链接，原UID保持、SEQUENCE加1。
- 25/26/72小时后开赛及已开赛场次的巡检时间边界通过；改期重新唤醒检查且不改审核证据。
- 旧URL尚有待处理检查时重新审核新URL，新检查可入队；实际worker忽略旧URL任务，仅对新URL调用合成HEAD。
- 候选读取后另一数据库连接完成续期，过期处理重新读到新版本，不撤下入口。
- 限制每批4条的验证依次入队4/4/1，后续轮次不加载未来记录；待执行旧任务不会占满候选页。
- 旧审核数据SQLite升级/回退/再升级/check通过。MySQL离线DDL包含新列与索引，未连接真实MySQL。

## 规模与新进程

最终证据 `scheduler-capacity-final-2026-09-10.json`：临时SQLite，20,000场合成比赛、20,000条合成审核记录；3条到期、7条待检查。一轮22.44ms、76条SQL，只加载10条不同审核记录，7份检查仍为pending（未发HEAD）。20轮空闲检查P95为0.44ms，每轮2条SQL，加载0条审核记录。

记录了实际执行的两条候选SQL的SQLite EXPLAIN：分别命中 `ix_broadcast_expiry` / `ix_broadcast_due`；网络候选的outbox相关子查询仍使用kind索引，不将这一结果外推为大量历史任务下的容量证明。首次 `scheduler-capacity-2026-09-10.json` 的测量也保留，其查询计划只针对基本索引查询；最终JSON补齐完整候选SQL计划。两次业务源码相同，JSON附源文件SHA256。

两个全新操作系统进程运行真正的 `worker.main --once` 流程，第一次合成抓取1次，第二次0次，均退出0。Provider网络输入明确替换为合成函数；调度、数据库领取和完成事务没有替换。

已用当前安装的Azure Functions Python包实际加载`function_app`，注册5个入口；`update_schedules`读回为每分钟、useMonitor=true。仅证明本地注册配置可解析，不证明云端执行。

## 主库与浏览器

本地备份 `data/before-scheduler-20260910-032643.db` 为0600、SQLite integrity_check=ok。已应用 `c72b961e430a`，24张原表的原字段/完整行哈希在迁移后及重启后相同，163场比赛、原账号/关注/订阅保留。证据 `scheduler-main-migration-2026-09-10.json` 与 `scheduler-main-readback-2026-09-10.json`。

API session69237/PID29651，worker session84983/PID29664，显式本地体验模式、访问日志关闭。健康local/ok；local_preview=true、Firebase未配置。本地登录和个人配置读取200；9月匿名47条演示赛程响应与之前完整JSON相同。F1未到下一轮，主worker启动没有提前追加Provider任务。一次HTTP验收误将登录后响应与匿名基线比较，纠正为相同匿名身份后通过，临时验收会话已清理，原会话未改。

浏览器临时标签19显示原本地账号、湖人关注和48场月历（含8月31日），实际截图已查看，控制台error为0；19已关闭，关注待确认草稿16、公共订阅12、旧合成维护10保留。此处是浏览器可用性检查，不是新增布局验收或设备证据。

未完成：真实Provider/HEAD、本批真实MySQL/Azure Timer与Queue、大批积压清空延迟、1,000用户发布扇出、通知到Feed延迟、真实外部日历与手机内容直达。本批没有云资源、push、部署或真实上游请求，T13/T19/T20/T33继续in_progress。
