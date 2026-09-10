# 独立开发云环境

更新于2026-09-10。用户授权托管Chrome创建配置独立资源；再次确认后端使用Azure Functions。身份、计算与业务存储的边界保持不变。

| 服务 | 目标与职责 | 当前证据 |
| --- | --- | --- |
| Firebase | `anke-sports-dev`，项目显示名称Anke Sports Dev；Google登录 | 已创建，真实登录/验签/个人设置读写/退出通过 |
| Firebase Web | Anke Sports Web Dev；`anke-sports-dev.firebaseapp.com` | 已注册，localhost与127.0.0.1已授权 |
| YouTube Data API | 独立 `anke-sports-youtube-dev` Key，仅允许 YouTube API | 已安全保存；隔离 API/worker 读取 69 条真实视频与预算通过；自动附链/Hub 待验 |
| Firebase服务账号 | Anke Sports Auth Dev；独立认证验签和身份清理 | Authentication Admin；专用真实测试身份撤销、删除及远端清理 27 项检查通过 |
| Azure Functions | 新建Anke Sports独立Function App；Python3.12/FastAPI、HTTP/MCP/Queue/Timer | `function_app.py`入口已实现；Azure资源尚未创建 |
| Azure Cosmos DB | 独立 NoSQL Serverless + Periodic；公共比赛、个人分区、投影/outbox | 用户已选定，Bicep/ARM validate 通过；资源及存储适配未完成，SQL 留作迁移基线 |
| Azure Storage Queue | `anke-sports-jobs` 任务分发；与权威存储的 outbox 共用处理器 | SQL 本地处理器通过；Cosmos 适配与云触发器待验证 |

Firebase保持Spark免费计划；未启用Analytics、Gemini、Hosting、Firestore或Firebase Storage。Google登录提供商展示名称为Anke Sports Dev，使用项目所属账号的支持邮箱。服务账号不是项目Owner/Editor，不复用其他产品身份。

## 本地真实登录验收

- 独立Web入口：`http://localhost:3003/calendar`，后端8788；本地体验身份关闭，`/auth/local`按契约返回404，匿名访问个人日历401。
- 客户端来自当时仓库源码副本，独立构建目录，不修改主Web3000的环境、原用户或待确认草稿。数据库为独立`data/firebase-auth-dev.db`，初始无赛程；这是身份验收实例。
- 浏览器完成真实Google登录；服务端从Firebase验证的UID创建个人账号，Admin按同一UID读回并确认提供商为Google。
- 通过UI关闭防剧透并保存；新页面读取同一Google会话和已保存偏好；再恢复初始配置。个人revision为2，两个发布任务均完成。最后通过UI退出，个人导出和删除操作禁用。
- 证据：`evidence/firebase-admin-read-2026-09-10.json`、`firebase-local-boundary-2026-09-10.json`、`firebase-login-2026-09-10.json`。

本机凭据仅存Git忽略的0600文件：`data/firebase-dev-web.json`、`data/firebase-dev-service-account.json`。服务账号下载原件在安全写入后移除；未输出私钥或提交凭据。`data/firebase-dev-runtime.json`保存独立本地实例参数。复现启动命令：

```sh
uv run python data/run-firebase-dev.py api
uv run python data/run-firebase-dev.py web
uv run python data/run-firebase-dev.py worker
```

以上本机文件与启动器不随Git分发；其他开发者应为自己的独立项目配置环境，参考`.env.example`与[Firebase Admin官方设置](https://firebase.google.com/docs/admin/setup)。后台需要验证账号撤销，不能只使用前端Web配置替代Admin身份。角色定义见[官方Authentication角色](https://docs.cloud.google.com/iam/docs/roles-permissions/firebaseauth)。

## Azure接入顺序

当前浏览器已登录Azure，看到Azure subscription 1；曾打开的`speech`资源组属于已有语音/头像资源，仅作只读核对。尚未将其选为Anke Sports部署目标。继续使用独立资源组和独立数据库、队列及身份。

1. 已确认 Cosmos Serverless + Periodic；按 [分区与迁移合同](cosmos-storage-design.md) 实现仓储和同分区事务，先走通 Firebase → 关注 → outbox → Queue → 已发布 ICS。不可直接把 SQL 实现连接到 Cosmos。
2. 完成所有既有业务模块适配，核对目标费用与 Web/API HTTPS 地址、独立身份/RBAC、平台日志脱敏，再构建对应的 Functions 包。
3. 在独立 Cosmos 验证 RU、并发/429、稳定身份、完整投影、删除和恢复；验收 Functions HTTP、Queue/Timer 与重试。Periodic 恢复至新账号，需重建网络/RBAC并重放删除决定。
4. 真实 YouTube Hub、长期续订和设备日历各自验收；流量增长时评估原地转手动 Provisioned，再调整 Autoscale。

目前没有 Azure 资源创建、部署、远端数据库迁移或 Git 推送。Firebase 专用真实测试身份的删除/撤销清理已通过；Google 浏览器删除、多设备与部署域名回调尚未验收。Azure Prepare 当前安装版本仅适用于显式 azd 或已有 azure.yaml 的项目，本项目未选择 azd；不因该技能自动引入部署工具或额外审批流程。


## 浏览器验收补充（2026-09-10）

上述真实Google身份验收在Chrome完成。Codex内置浏览器曾返回`auth/popup-closed-by-user`，再次发起时未出现可完成登录的弹窗；具体宿主原因未定位。Chrome当前账号刷新后保留，两个浏览器不共享会话。用户决定停止内置浏览器排查；不得据此宣称该浏览器登录通过。

前端将登录失败放入对话框，提供复制当前页面地址的恢复操作，并在Firebase成功后等待后端个人日历读回再完成登录。Chrome实际关闭Google弹窗后，中文错误和重试入口可见；复制地址实际系统剪贴板读回正确。Web和扩展类型检查、生产构建通过。尚未新做真实MCP授权、多设备或Firebase删除验收。


Functions已完成真实Core Tools/Azurite本机宿主验收与安全源码打包，参见[复现与边界](functions-runtime.md)。Azure资源和远端部署状态仍为未执行。

独立 Azure 开发 Bicep 已替换为 Cosmos Serverless + Periodic，并通过编译及订阅级 validate，详见 [当前设计](cosmos-storage-design.md) 与 [模板证据](../evidence/azure-cosmos-template-2026-09-10.json)。原 [MySQL 计划](azure-development-plan.md) 仅作历史记录。未创建收费资源，未把模板验证记为部署。


## YouTube 接入进展（2026-09-10）

已在同一独立项目启用 YouTube Data API/API Keys API，创建专用受限 Key；未修改 Firebase Browser key，Spark 计划保持。用户解锁 Mac 后已安全保存并清理临时明文。本机隔离 API/worker 两轮各 11 项通过，每轮预留 8 单位、读取 69 条视频。实际没有自动附链，183 条待审核/15 条拒绝；Hub、真实视频到 ICS、手机仍待验收。见 [YouTube 开发接入](youtube-development.md)。

用户已确定 Serverless + Periodic，并说明 Anke Money 生产也采用同样配置，未来原地转 Provisioned → Autoscale；本批没有重新读取或修改 Money 资源。见 [成本选择](azure-low-cost-proposal.md)。当前 SQL 业务运行时还未迁移，Azure 资源未创建。

真实 Firebase 专用测试身份通过实际 ID/refresh 撤销、HTTP 删除、独立 worker 清理、旧 Feed/下游授权拒绝和远端不存在读回，共 27 项。测试账号与临时 API/库已清理，没有操作已有 Google 用户；完整范围见 [生命周期证据](../evidence/firebase-lifecycle-2026-09-10.md)。
