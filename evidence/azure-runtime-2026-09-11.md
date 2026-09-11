# Azure 首次运行证据

最新读回：代码部署 status=4、complete=true、active=true，CLI 已退出。jolpica last_success=2026-09-11T08:09:43.187029+00:00、enabled=true、activity=idle、consecutive_failures=0、error 为空。真实 sources 返回 jolpica:f1 / F1 世界锦标赛 / demo=false。现有云任务已处理成功；下文 queued 与等待部署为过程记录，不是当前状态。尚未证明真实用户个人 Feed 或自然定时周期。

补充：部署CLI已正常退出，函数列表注册了六个预期入口。真实设置启用jolpica。经宿主管理接口手动触发update_schedules返回202，后续状态读回jolpica已queued，无error、last_success仍null；这是云端任务生成证据，不能代替队列完成或自然定时验收。

开发资源部署已 Succeeded。Cosmos 实际读回 Serverless、Periodic、Strong；Function App HTTPS-only、Running。三个独立 Key Vault 密钥启用，初始化临时 Secrets Officer 角色已移除并读回为空。

代码部署 bcf08ca7-d71b-4517-b2ea-44ccfc01f64f：远端 Oryx 构建与包上传完成，命令 session13898 仍等待触发器同步，不能提前称整个部署已完成。

对 https://anke-sports-dev-mtcflttk.azurewebsites.net 的实际 HTTP 检查：

- GET /api/v1/status：200 JSON，storage=cosmos，local_preview=false，firebase_configured=true；YouTube配置可用，云账本已用0/限制20。本地历史6单位不计入该账本，非Google全局余额。
- GET /api/v1/me/calendar：401 AUTH_REQUIRED。
- POST /api/v1/auth/local：404 NOT_FOUND。

这些结果证明云HTTP与基本匿名边界，不证明实际Firebase用户验签、云个人Feed或手机同步。准备启用 jolpica（模板和应用配置同步），真实云赛程处理尚待验收。没有向公网暴露本地体验账号。
