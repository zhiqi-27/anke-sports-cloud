# Anke Sports · 服务端状态

2026-09-11 最新：Cloudflare Web 已发布到 https://sports.anke-ai.com/calendar，Worker 版本 83295a47-3110-45d3-8125-a992cd7bf72d。Firebase 已添加 sports.anke-ai.com 授权域名并读回。公网 calendar=200、status=200/no-store、匿名个人日历=401/no-store；Chrome 页面实际显示20场F1，登录对话框可打开。真实Google用户登录、个人ICS和手机同步仍待验收。此状态取代下方未发布/等待解锁的历史记录。

2026-09-11 最新：云 jolpica 任务完成，last_success=08:09:43 UTC、idle、0 failures，真实 sources 已返回 F1。代码部署成功、六函数注册。证据见 evidence/azure-runtime-2026-09-11.md；下文 queued/部署中是历史过程。Cloudflare 发布待 Mac 解锁完成 Wrangler 授权；真实云个人 Feed 与手机验收仍待完成。

代码部署 session13898 已正常退出，六个函数已注册，jolpica应用设置读回正确。手动调用 Azure admin/functions/update_schedules 返回202（密钥仅进程内使用），随后公开状态确认 jolpica activity=queued、last_success=null、error空；已证明云端生成任务，尚未证明队列处理完成或自然定时周期。继续观察该任务，不重复触发。

2026-09-11 云API已响应：/api/v1/status=200/cosmos/local_preview=false，匿名个人日历401，本地登录404。Oryx构建和上传完成，session13898等待sync triggers。已提交启用jolpica配置（session77230），模板同步；云赛程/真实Firebase用户和Feed尚待验收。证据 evidence/azure-runtime-2026-09-11.md。

2026-09-11 基础资源部署 Succeeded/error null。Cosmos 读回 EnableServerless/Periodic/Strong/Succeeded；Function App 读回 Running、httpsOnly=true，主机 anke-sports-dev-mtcflttk.azurewebsites.net。代码 zip 远端构建已发起 session 13898，包 affc54da6e77664888403be3cc32fcc5927a84c42687ab5cad6623d1d9ba96da；尚未收到结果，不得重复发布。3009 本地真实轮询 worker PID21630 已 TERM 停止，API仍可读原数据；云端预算账本不包含先前本地已用6单位，需单独记录，不称Google全局余额。

2026-09-11 Vault 初始化完成：ankesports-dev-mtcflttk 内 firebase-credentials、youtube-api-key、feed-encryption-key 已保存并读回 enabled=true。仅使用独立项目凭据；云 Feed 加密密钥另行生成并本地0600保存。临时 Secrets Officer assignment 7e3734a1-7113-466b-a622-6cd071206f42 已删除（命令成功），正在读回核对。Cosmos 仍 Running，session 48469 未结束；Storage/Queue/Vault/MI/网络/Flex计划已 Succeeded，应用代码尚未发布。

Azure 最新读回：deployment anke-sports-dev-20260911 = Running/error null；anke-sports-dev 资源组已 Succeeded，子部署 anke-sports-dev-services = Running。原创建进程 PID 28749 / session 48469 仍在运行，保持原任务。覆盖下文“查不到资源组”的历史状态。

创建命令 session 48469 最近轮询仍运行；读回 deployment anke-sports-dev-20260911 和资源组时尚未找到，因此尚未确认云端开始创建。继续跟进原命令，勿因读回暂缺重复提交。前端已完成带 Firebase Web 配置的静态构建。

2026-09-11：azure-validate 工作流完成，.azure/deployment-plan.md 为 Validated。已发起实际开发资源创建：deployment anke-sports-dev-20260911，exec session 48469。状态尚未返回，后续必须轮询此句柄/部署名，不能重复创建；当前不是已部署应用。密钥、代码和Cloudflare仍未发布。

2026-09-11 验证工作流已按 standalone Bicep 启动，记录 .azure/deployment-plan.md（Approved，未标 Validated）。核心验证 session 98448：CLI/auth/build 已通过，当前等待 Azure validate，随后 helper 会执行 what-if。策略只读查询 session 55893 已结束：仅列出 SecurityCenterBuiltIn。继续轮询原句柄，不重复启动。没有部署资源。

2026-09-11 Azure session 24304 已成功结束：21 项全部 Create，无现有资源修改/删除，error null；见 evidence/azure-preflight-2026-09-11.md。尚未执行创建，Azure Deploy 技能的验证流程前置记录仍未完成。下文“预演正在运行”为历史状态。

2026-09-11 Azure 只读预演正在运行：exec session 24304，deployment name anke-sports-dev-preflight-20260911，参数 infra/dev.bicepparam，ResourceIdOnly 输出。最近轮询仍未结束；继续等同一句柄，勿重复发起或当成部署成功。没有运行 deployment create。

2026-09-11 真实 Feed 更新补验：3009 HTTP 与独立 worker 下，防剧透描述修改及恢复使正赛 SEQUENCE 2→3→4，83 条 UID 保持，原偏好已恢复，304 通过。见 evidence/real-feed-revision-2026-09-11.json。域名答复仍待收到，未进行云部署。

2026-09-11 部署准备：Azure CLI 只读确认当前订阅 Enabled，未找到名称含 anke-sports 的资源组。Bicep 补齐 YouTube 项目、20 单位开发预算与 Key Vault 引用，编译通过。源码包 data/anke-sports-20260911-functions.zip，SHA256 affc54da6e77664888403be3cc32fcc5927a84c42687ab5cad6623d1d9ba96da，159262 字节；需要远端构建，未部署。桌面 HTTPS 域名待定，资源与密钥引用值未创建。

2026-09-11 真实数据桌面预览：3009 / API PID 21629 / worker PID 21630 / session 16914。`experiments.document_real_ui` 使用保留的本地文档账本与独立体验身份，前端代理至 3002。浏览器已确认正赛自动关联原链接及 ICS 描述；不是 Firebase 或 Azure 验收。停止时保留数据，后台 YouTube 共用既有单日 20 单位本地预算。

2026-09-11：真实意大利大奖赛正赛、排位赛的视频链接已进入隔离本地个人 ICS，10 项检查通过；重复读取版本稳定、304。首次发布前版本快照缺失，未验证云或手机。[记录](evidence/document-real-content-2026-09-11.md)。

更新：2026-09-10。本地MVP可试用，尚未云端公测。用户已解锁。[总进度](../STATE.md)。

## 本轮匹配修正 · 2026-09-11

工作区 matching-v3 新增明确赛段 Highlights 标题识别；匹配/SQL 内容 45 项、文档内容/创作者/Feed 38 项回归通过。[范围与证据](evidence/matching-highlights-2026-09-11.md)。未重启现有进程或部署，真实视频到个人 ICS 验收仍待完成。下述运行证据为 2026-09-10 历史记录。


## 运行与实现边界

- SQL基线：主API8787/PID44497；Firebase独立API8788/PID44563。均在本次只读核验返回200。本机SQLite，不是云数据库。
- 文档模式：3007/PID94103为Provider/F1样本，3008/PID1760为创作者合成预览，均健康200。前端借用3002；后端版本与主SQL分开。
- 现有SQL基线包含公共Feed、直播维护、账号生命周期与OAuth/MCP；文档模式已接入日历/关注/配置/个人链接/个人ICS、Provider、创作者轮询/匹配/人工确认与WebSub，但仍缺公共Feed、直播、账号删除、OAuth/MCP等完整适配。
- 3008未重启到最新WebSub代码；最新独立WebSub实验已停止。其他worker历史句柄本次未复核，不作存活承诺。没有迁移主库或重启服务。

## 现有证据

最近业务代码b86749e；后续1f1d261仅同步UI检查。最近完整后端记录361 passed / 2 skipped / 2既有警告，见[WebSub证据](evidence/document-websub-2026-09-10.md)。本次文档更新没有重跑这些测试。

[Codex实际业务25项](evidence/codex-business-2026-09-10.md)在合成身份/SQL下通过；[Firebase专用身份27项](evidence/firebase-lifecycle-2026-09-10.md)、[YouTube真实读取](evidence/youtube-live-2026-09-10.md)、[F1文档Provider](evidence/document-providers-2026-09-10.md)各有独立环境证据，不合并为真实云端全链路。

[最新UI/ICS](../anke-sports/output/playwright/lean-check-2026-09-10.md)确认3008个人日历下载12条唯一事件及合成原链接。公共Feed仍未开放，前端提示不等于后端适配完成。

## 云目标与下一步

Firebase + FastAPI/Azure Functions + Cosmos NoSQL **Serverless + Periodic** + Queue/timer。开发/生产同一初始容量策略；MySQL仅旧方案与本地迁移基线。模板validate有记录，Azure资源与远端部署按现有交付记录尚未执行，本次未查询云端。

先收敛一个真实赛事/创作者到个人ICS的路径，再明确HTTPS、独立开发目标和相应必需适配，验证真实日历更新。不是先补全所有文档存储模块。通用GC、假设性规模工作后置；上线能力必要的权限、保留规则与恢复仍须满足。

[当前架构](docs/architecture.md)、[云接入](docs/cloud-development.md)、[Cosmos设计](docs/cosmos-storage-design.md)、[文档模式边界](docs/document-runtime.md)。无push、云操作或部署；本轮只更新文档。
