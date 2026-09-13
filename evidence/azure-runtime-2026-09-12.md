# 开发环境运行复查 · 2026-09-12

通过 https://sports.anke-ai.com/api/v1/status 实际读取：storage=cosmos，local_preview=false，firebase_configured=true；jolpica last_success=2026-09-12T08:13:19.792299+00:00，activity=idle，consecutive_failures=0，error为空。本次仅GET读取，没有手动触发任务。更新时间晚于昨日首次触发，证明云端已有后续成功更新；未查询触发日志，不能据此单独断言具体触发来源。

云YouTube账本本日reserved_units=0/20，未验证创作者云端发现。真实用户个人Feed、手机更新及App直达仍未验证。

本地仓库实际位置已确认为 /Users/shizhiqi/Developer/Anke/Anke Sports/anke-sports-cloud；旧 Documents/Zhiqi/Work 路径已不存在。未修改任务绑定、自动化或其他项目。现有 experiments/firebase_lifecycle.py 只适用于隔离本地SQLite，并非云端验收脚本，未将其直接指向云端运行。

## 订阅地址链路

只读 Azure appsettings 确认 WEB_URL=https://sports.anke-ai.com，PUBLIC_URL=https://anke-sports-dev-mtcflttk.azurewebsites.net。document_api 的个人地址接口在 PUBLIC_URL 下生成 /feeds/{token}.ics；Azure 同一路由支持 GET/HEAD。因此当前个人订阅直接走 Azure HTTPS，不依赖 Cloudflare 仅转发 /api/* 的 Worker。没有读取真实用户令牌；这项配置核对不代替已登录个人 Feed 验收。
