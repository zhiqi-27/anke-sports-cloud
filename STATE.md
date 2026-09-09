# Anke Sports 服务端状态

2026-09-09。本机 API 127.0.0.1:8787，exec session 24223，PID 80583。Worker session 87008，每分钟内容调度、每 6 小时已启用赛事来源；访问日志关闭。

显式本地适配：SQLite 与本机独立体验账号。已实现 FastAPI、Firebase 验证入口、配置/链接、稳定投影与 ICS、事务 outbox、worker 和 Azure Functions 触发器代码。F1 Jolpica 已写入 115 个分场次（23 个大奖赛）；主预览共 163 条真实与演示赛程。

创作者链路已接入：共享频道/视频发现、14 天首次补查、6 小时轮询、签名通知与续租/退订协议、规则匹配、待确认/确认/忽略、固定/移除、28 天元数据清理。真实 YouTube 请求与 Hub 未验收，默认关闭 WebSub。主库没有合成频道或视频。

45 项 pytest、ruff、OpenAPI 导出通过。新迁移 b5e150717423 在独立 SQLite 库及主体验库执行；主库先备份，既有数据逐行相同。MySQL 仅离线 DDL；未连接 Azure 数据库。ICS 实验输入 5 阶段、4 个固定事件身份已准备。详细证据：evidence/local-2026-09-09.md。

浏览器使用临时独立合成库和客户端构建产物，验证作者添加、后台发现、人工确认、暂停、删除影响和固定保留。实验进程及临时库已清理；可按 docs/content-pipeline.md 复现。

仍需：官方直播审核/巡检、目标 MCP 客户端联调、NBA/足球完整 Provider 验证、真实标注匹配集、隐私/并发/规模检查、真实 Firebase/Azure、遥测脱敏、系统日历和手机观察、恢复演练、发布；Google 直连属 M6。缺外部访问只阻塞相应验收。

用户决定先完成本地；随后授权托管 Chrome 创建配置独立云资源，用户负责登录。没有 push、云部署、远端迁移或修改 FormaLM 资源。

MCP 与应用授权已接入：匿名3工具、私人11工具，共用 actions 服务、scope/issuer/resource检查、PKCE/刷新/撤销、24小时幂等回执。网页本地授权→官方SDK真实HTTP查询→网页撤销→401已验证，未对原关注/Feed做写入。新增迁移721f5dc7b8c2，本地备份后原有14表逐行一致。详见 docs/mcp-and-connections.md；外部平台验收仍未通过。

Chrome配套验收新增2项隔离协议测试：回调注册约束扩展Origin、Bearer独立身份、幂等/屏蔽/撤销。47项pytest与ruff通过，2项原有弃用警告。仅测试与文档变化，无迁移或主库写入。客户端已生成MV3本地包；实际Chrome安装因浏览器URL策略被拒绝，未绕过；合成界面不替代真实授权/activeTab证据。详见../anke-sports/extension/evidence.md。
