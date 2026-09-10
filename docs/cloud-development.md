# 独立开发云环境

更新于2026-09-10。用户授权托管Chrome创建配置独立资源；再次确认后端使用Azure Functions。身份、计算与业务存储的边界保持不变。

| 服务 | 目标与职责 | 当前证据 |
| --- | --- | --- |
| Firebase | `anke-sports-dev`，项目显示名称Anke Sports Dev；Google登录 | 已创建，真实登录/验签/个人设置读写/退出通过 |
| Firebase Web | Anke Sports Web Dev；`anke-sports-dev.firebaseapp.com` | 已注册，localhost与127.0.0.1已授权 |
| Firebase服务账号 | Anke Sports Auth Dev；独立认证验签和身份清理 | 已创建，赋予Firebase Authentication Admin；实际Admin读取通过 |
| Azure Functions | 新建Anke Sports独立Function App；Python3.12/FastAPI、HTTP/MCP/Queue/Timer | `function_app.py`入口已实现；Azure资源尚未创建 |
| Azure MySQL | 独立业务数据库；公共比赛、个人配置、投影与事务outbox | 本机MySQL8.4.11测试及迁移通过；云资源/网络/TLS待配置与验证 |
| Azure Storage Queue | `anke-sports-jobs`任务分发，与SQL任务状态共用业务处理器 | 本地处理器通过；云触发器待验证 |

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

1. 核定开发环境的订阅、区域、Functions计划与MySQL规格、费用边界、Web/API HTTPS地址，并保存具体资源清单。
2. 根据现有Functions入口生成资源配置，保留Firebase验签与SQL事务边界；为凭据选择受控配置/身份方案，验证平台日志不会记录私人Feed地址。
3. 对独立目标执行迁移前备份与恢复检查，验证Azure MySQL TLS、Functions HTTP、Queue与Timer、重试和服务端到Feed的完整路径。
4. 真实YouTube API/Hub和设备日历验收在各自条件具备后继续；不把Firebase登录通过视作这些项目已通过。

目前没有Azure资源创建、部署、数据库迁移或Git推送。Firebase账号删除/撤销清理、多设备、部署域名回调仍未验收。Azure Prepare当前安装版本仅适用于显式azd或已有azure.yaml的项目，本项目未选择azd；不因该技能自动引入部署工具或额外审批流程。


## 浏览器验收补充（2026-09-10）

上述真实Google身份验收在Chrome完成。Codex内置浏览器曾返回`auth/popup-closed-by-user`，再次发起时未出现可完成登录的弹窗；具体宿主原因未定位。Chrome当前账号刷新后保留，两个浏览器不共享会话。用户决定停止内置浏览器排查；不得据此宣称该浏览器登录通过。

前端将登录失败放入对话框，提供复制当前页面地址的恢复操作，并在Firebase成功后等待后端个人日历读回再完成登录。Chrome实际关闭Google弹窗后，中文错误和重试入口可见；复制地址实际系统剪贴板读回正确。Web和扩展类型检查、生产构建通过。尚未新做真实MCP授权、多设备或Firebase删除验收。


Functions已完成真实Core Tools/Azurite本机宿主验收与安全源码打包，参见[复现与边界](functions-runtime.md)。Azure资源和远端部署状态仍为未执行。

独立Azure开发Bicep已通过本机编译和订阅级validate；托管身份队列适配、Firebase JSON凭据及真实Admin只读验证已完成。具体资源与费用已提交确认，详见[Azure开发环境计划](azure-development-plan.md)。未创建收费资源，未把模板验证记为部署。
