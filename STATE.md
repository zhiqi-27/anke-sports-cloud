# Anke Sports 服务端状态

2026-09-09。本机 API 127.0.0.1:8787，exec session 90809，PID 52684。Worker session 59824，每 6 小时调度已启用来源；访问日志关闭。

当前适配：SQLite 与本机独立体验账号。已实现 FastAPI、Firebase 验证入口、个人配置、链接、稳定投影与 ICS、outbox、本地 worker、Azure Functions 触发器代码。F1 Jolpica 已成功写入 115 个分场次（23 个大奖赛）。

16 项 pytest、ruff、契约导出通过；本机独立数据库初始迁移与 schema 对比通过；MySQL 离线 DDL 通过，Azure 数据库未执行。ICS 实验输入 5 阶段、4 个固定事件身份已准备。详细证据：evidence/local-2026-09-09.md。

未完成：YouTube 自动通知/补查/续订/匹配/审核、官方直播审核和巡检、MCP、NBA/足球完整 Provider 验证、完整隐私/并发/规模测试、Firebase 真实账号、Azure 触发器与遥测脱敏、手机/系统日历真实更新、恢复演练和开源发布。缺外部访问只阻塞相应验收。

没有 push、云部署、远端迁移或修改 FormaLM 资源。继续保留完整目标，不因本地流程通过而将全部任务标为完成。
