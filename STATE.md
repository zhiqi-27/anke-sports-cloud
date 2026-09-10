# Anke Sports 服务端状态

当前摘要：2026-09-10，文档日历 HTTP、完整 Provider 快照、Change Feed/Queue 投递和独立 worker 已接入；浏览器保存关注，实际 HTTP 读回12条演示 ICS、稳定 UID/304。最终277 passed/2 skipped，Ruff及完整OpenAPI比较通过。新预览 localhost:3006/calendar（session67857/API PID83337、worker PID83338），IAB标签28已保留；原主预览/Firebase服务未重启。Serverless + Periodic及未来原地Provisioned→Autoscale方案保持。真实Cosmos/Azure未调用、资源未创建，内容/公共Feed/OAuth/删除等尚未迁移。以下历史批次保留当时状态。

更新：2026-09-10。主 API 127.0.0.1:8787，session43380/PID44497；worker session62679/PID44496；显式启用本地体验并关闭访问日志。显式本地SQLite/体验身份。主库163条比赛（48演示+115 Jolpica F1分场次），无合成频道/视频或公共直播记录。

已实现账号验证入口、个人配置/链接、稳定投影与ICS、事务outbox、local worker/Azure触发器、体育Provider接口、共享YouTube发现/签名通知/补查/匹配/人工确认、PKCE/刷新/撤销和HTTP MCP（公开3、私人11工具）。真实 Firebase 登录与专用身份清理、YouTube 小规模本机发现已有证据；Hub、真实内容到ICS、Cosmos、云队列与触发器仍未验收。

新增直播审核、候选URL注册表、地区/条件、草稿与发布版本分离、版本冲突、到期/撤回、HEAD检查和设备观察。业务变化、审计与outbox原子提交。维护者白名单默认空，自动联网检查默认关闭；到期处理独立。直播批68项pytest、ruff通过，2项已有弃用警告。OpenAPI与客户端类型同步。说明：docs/broadcasts.md；证据：evidence/local-2026-09-10.md。

迁移7b26efb1d423经SQLite升级/回退/再升级/check与MySQL离线DDL后应用本机。备份data/before-broadcast-20260910-002301.db为0600；原有19表逐行相同，主库广播记录0。旧迁移721f5dc7b8c2的备份data/before-oauth-20260909-230049.db保留。未连接Azure MySQL。

独立直播验收 http://localhost:3001/maintenance ：API session29715/PID92350，客户端3002 session12042/PID17719。临时库、合成赛程/来源/HEAD/观察；通过浏览器保存审核发布撤回及真实HTTP ICS读取，UID不变、SEQUENCE 2→3、事件保留。设备表单三层均未测试，非手机证据。为用户检查保留进程，停止API即删除临时库；该进程早于最后两处审计actor/元数据时间修正，最新源代码由68项测试覆盖。

Chrome ZIP已在客户端生成；实际Chrome安装因URL策略被拒，未绕过。目标MCP客户端、真实NBA/足球/YouTube、真实官方直播/设备/网络、并发规模（已补齐广播有界候选调度，积压/发布规模仍待验证）、隐私/脱敏/恢复演练、公测与发布仍需继续；Google直连属M6。

用户授权先本地、后托管Chrome创建配置独立云资源，登录由用户完成。没有push、部署、远端迁移或修改FormaLM资源。完整目标未完成。

## 后台恢复批次

新增任务租约和尝试次数条件提交、Provider单通道与熔断、5次崩溃上限、审计重放、任务完成时间、独立维护调度与脱敏错误。个人投影先锁定并刷新账号配置；无内容变化的恢复保持ICS版本/ETag。体育/YouTube key支持.env，使用SecretStr且显式进程环境优先。

最终83项pytest/ruff通过；真实子进程中断/事务回滚/租约恢复、CLI预览重放去重、22表备份还原和独立密钥恢复通过。详情docs/job-recovery.md、evidence/recovery-2026-09-10.md和机器JSON。没有将SQLite证据等同MySQL或Azure运行。

已应用本机迁移6e9edd6daa17；0600备份data/before-recovery-20260910-005423.db，原21表旧列/行相同、163场保留。NBA和足球各一条真实缺key失败已通过设置页触发并读回；保留错误/冷却，不假报成功，未访问上游。API健康读回local/ok，当前worker仍运行。T13/T33及QA-31外部项继续。

## 公共Feed批次

新增public_feeds和7229fa56d28e迁移，复用个人投影的描述/UID/版本/撤销/HTTP条件请求逻辑。游客只读快照，Provider与直播变更原子入队、每天检查窗口，个人数据不会混入。部署环境按具体已核验来源键启用公共分发，演示来源禁用。

本机0600备份data/before-public-feeds-20260910-012243.db，迁移前后原22表相同，主库163条保留。运行后有14份公共来源Feed；演示湖人12条，既有真实F1在窗口内85个分场次，HTTP 304通过。没有新增上游获取。92项pytest/ruff通过，客户端类型/构建与游客下载哈希验证通过。docs/public-feeds.md和evidence/public-feeds-2026-09-10.md记录详情。

主API session88286/PID23809，worker70713/PID18603运行最新代码。原3001临时直播API为旧版本，只保留既有维护演示，不能作为公共Feed验证环境。真实分发/HTTPS/手机/云/规模仍待完成。


## 最新批次：关注变更预览（T11 / T23）

“我的关注”先预览再确认，由后端共用投影筛选规则计算新增、移除、重叠保留和历史保留；支持无日期、暂停、待发布提示。Web提交绑定确认摘要和幂等键，赛程变化要求重新预览，配置版本变化重新读取关注。旧HTTP/MCP无摘要调用继续兼容。

后端98项pytest/ruff、Web/扩展类型和最终production build通过；新增6项针对所有权、只读、并集/屏蔽、历史/日期、摘要截断、冲突和幂等的测试。隔离浏览器确认：16条原订阅变为10条有效比赛+6条原UID移除通知，全部UID不变；取消预览不写配置，过期预览拒绝写入。修复长弹窗底部提交后看不到顶部错误的问题，错误会获得焦点并滚入视口。1440/1280/1024均无横向溢出，键盘打开/关闭/操作和实际HTTP发布通过。

主API session88286/PID23809、worker session89692/PID23829运行本批代码；主Web3000 session43940，验收production Web3002 session12042/PID17719。无需迁移，users/feeds/events/projections/sources五表完整行哈希保持相同，163条比赛保留。主API /api/v1/health=local/ok，匿名预览401。

主页面 http://127.0.0.1:3000/following 标签16保留新增NBA的待确认预览（新增8，含历史3，结果20），未保存，原关注不变。原公共订阅标签12、维护标签10保留。关注夹具3003/标签15已关闭，临时库已删除；experiments/follows_ui.py可重新创建。viewport已重置。3001仍是旧版独立合成维护API，不用于新功能验收。

说明：docs/follow-changes.md；证据：evidence/follow-preview-2026-09-10.md 和同名JSON。T11/T23保持in_progress，真实Firebase多端、MySQL并发/规模、外部日历移除/刷新仍待验收。下一步继续原完整目标：YouTube跨任务并发、隐私脱敏、目标MCP客户端与规模；后续托管Chrome云资源创建授权保留。无push、部署或云资源变更。


## 最新批次：YouTube 并发与恢复（T13 / T15–T18）

新增频道data/hub执行租约与条件提交，旧任务内容/错误不能覆盖新任务。通知与调度并发去重；Hub待验证意图受Claim保护，同一挑战重试不延长订阅。网络限流与本地重新匹配分开处理，连续崩溃耗尽次数后明确失败。匹配/人工审核/手动附加先锁用户并重读个人选择；过期清理以updated_at条件更新，防止误删刚刷新的元数据。

完整后端116项pytest、ruff通过，新增18项频道用例；最终挑战摘要编码调整后相关35项再次通过。真实独立SQLite连接验证旧响应/错误、个人选择、清理竞争、通知/调度去重；ASGI签名通知→共享Worker→ICS保持UID，重复通知不改变版本。真实Hub、Firebase、MySQL、Azure和设备仍未验收。Web/扩展typecheck通过，OpenAPI完全相同；客户端本批仅更新状态文档，没有新UI源码或重复构建。

本机迁移a8c502e7d134新增channel_work和验证摘要，0600备份data/before-channel-work-20260910-021321.db；原23表既有列/行在迁移与最终重启后逐项相同，163场保留，channel_work=0，未往主库加入合成频道/视频。临时SQLite空库/旧数据升级、回退、再升级、模型check通过；MySQL仅离线DDL，未发现可用本机MySQL/Docker命令。

主API session88286/PID23809、worker session89692/PID23829运行最终代码，健康local/ok；带时区9月演示赛程查询返回47条。主Web3000、production演示Web3002、旧合成维护API3001、扩展预览18792保持运行；原关注预览标签16、公共订阅12、维护10保留，未确认NBA草稿。

说明：docs/channel-concurrency.md；证据：evidence/channel-concurrency-2026-09-10.md及JSON。T13/T15–T18保持in_progress。下一步：实际目标MCP客户端、项目级API配额/规模、隐私脱敏和后续独立云资源与设备验收。托管Chrome授权继续保留。无push、部署或云资源变更，完整目标继续。


## 最新批次：Codex 实际授权与工具发现（T28 / T29）

安装的codex-cli 0.153.4已通过真实HTTP完成动态注册、S256只读授权、私人11个/公共3个工具发现。Chrome授权页确认只读scope，设置页可撤销；撤销后Codex发现0个私人工具，CLI退出后凭据清除、状态notLoggedIn。后台已清理本次grant，主API健康local/ok，主库仍163场、1个用户、0个创作者/视频。关注草稿与业务配置未改。

新增scripts/check_codex_discovery.py和连接操作文档，探测器不创建任务/模型回合，不执行业务工具，也不修改Codex配置文件。只接受loopback，读取有效配置后在本进程禁用其他MCP，再做inventory；实际4种状态、ruff、差异格式及非loopback拒绝通过。客户端本批只有README/STATE，无业务源码、迁移、依赖或重复全量测试/构建。

Chrome回调最终页出现ERR_BLOCKED_BY_CLIENT，未重试或绕过。CLI成功及随后独立进程发现证明授权传输可用；完成页显示仍不是通过。authStatus=oAuth只表示客户端保存了凭据，撤销后仍可能存在，不能作服务端接受授权的证明。

证据anke-sports-cloud/evidence/codex-client-2026-09-10.md及5份脱敏JSON；操作docs/mcp-and-connections.md。T28/T29保持in_progress：实际Codex业务查询/写入、长周期refresh、真实Firebase/HTTPS/Azure仍未验收；Python SDK业务测试独立保留。下一步继续规模/项目级配额、隐私脱敏及目标客户端实际工具调用，已有inventory无需无变化重复。

主Web3000 session43940、API8787 session88286/PID23809、worker session89692/PID23829、旧直播夹具3001/生产预览3002/扩展预览18792保持运行；关注预览16、公共订阅12、维护10保留。临时Chrome授权和设置页已关闭。未添加长期Codex配置，测试授权与客户端凭据均清理。未来托管Chrome创建独立云资源、用户负责登录的授权继续有效；本批无云资源、push或部署。完整产品目标继续。


## 最新批次：赛程查询与发布容量（T12 / T21 / T22 / T28）

HTTP/MCP共用查询先按日期缩小候选，只读取筛选/游标字段，分页后加载完整比赛及本人/公开链接；复用原屏蔽、固定、创作者、地区和审核规则。修复不同时区偏移与重复小时按字符串排序的问题；选中比赛在两次读取之间改版时返回409。个人/公共Feed也按窗口过滤旧赛季并批量读取链接。没有跨账号结果缓存或接口/迁移变化。

隔离SQLite和真实loopback HTTP：20,000活动比赛+20,000历史比赛、1,000存储账号、200创作者、10,000私人链接。原单请求赛程P95约2秒、每页约20,000条SQL；最终20样本/类单请求P95 79–124ms，4并发619–818ms，每页3–6条SQL。200场单Feed初次发布SQL 407→9，0.4246→0.2356秒；仅1份Feed，不是1,000用户并发或通知到发布验收。中间未达标结果保留；最终JSON记录源文件hash。

后端125项全量pytest/ruff通过；新增重复小时用例后相关10项再次通过。OpenAPI/config schema字节相同，客户端无业务源码或依赖变化，没有重复构建。主库七表(users/events/feeds/projections/links/broadcast_records/creators)完整行hash不变，163场、1用户、217投影保留；9月47条API演示赛程完整JSON不变。新游客浏览器月历48场（含8月31日）与9月10日GSW/BOS抽屉正常，console error0；临时17已关闭，原关注预览16/公共订阅12/维护10保留。

主API已重启为session46872/PID28321，worker为session1516/PID28335，访问日志关闭；主Web3000 session43940、旧直播夹具3001、production预览3002和扩展预览18792仍保留。旧API88286/worker89692已确认正常终止。此前/tmp/anke-sports-codex-schema.To77Hv的安全钩子清理拒绝未重试，目录保留。

说明anke-sports-cloud/docs/schedule-queries.md；证据evidence/schedule-queries-2026-09-10.md及6份JSON。上述任务保持in_progress；下一步继续广播有界调度/并发去重、通知到发布规模、项目级配额和隐私脱敏。真实MySQL/Azure/Firebase/YouTube与设备验收、Codex业务调用、Chrome实际安装仍未完成。未来托管Chrome云资源配置授权保留。本批无上游请求、云资源、push或部署，完整产品目标继续。

## 2026-09-10 · 本地体验重启参数修正

性能批次已提交：后端 `095e122`、客户端文档 `8e6d727`。随后发现重启时漏传 `ANKE_SPORTS_LOCAL_PREVIEW=true`，页面暂时退回游客；这是启动配置遗漏，未改生产认证默认值。旧 API session46872/PID28321、worker session1516/PID28335 已正常停止。

当前 API session27052/PID28765，worker session84881/PID28778。两者从后端目录启动，必须显式带以下四个本地参数（无云凭据）：

```sh
ANKE_SPORTS_ENV=local ANKE_SPORTS_LOCAL_PREVIEW=true ANKE_SPORTS_PUBLIC_URL=http://localhost:8787 ANKE_SPORTS_WEB_URL=http://localhost:3000 uv run uvicorn app.main:app --host 127.0.0.1 --port 8787 --no-access-log
ANKE_SPORTS_ENV=local ANKE_SPORTS_LOCAL_PREVIEW=true ANKE_SPORTS_PUBLIC_URL=http://localhost:8787 ANKE_SPORTS_WEB_URL=http://localhost:3000 uv run python -m app.worker
```

两条命令分别运行在独立终端。不要依赖缺省配置自动开启体验身份。

真实HTTP读回：status.local_preview=true、firebase_configured=false；本地登录与读取个人日历均200，原配置逐项相同。仅注销验收脚本自己的临时cookie，原浏览器会话保留。七张业务表行哈希和9月47条演示赛程完整响应与性能改动前一致。浏览器新标签18读到本地账号及原湖人关注，全部48场、筛选12场、控制台error为0；已查看实际渲染后关闭18，原待确认草稿16、公共订阅12、维护10保留。

其余服务与数据不变；本次修正只有运行参数和证据文档，没有业务源码、契约、迁移、云操作、push或部署。完整产品和外部验收仍继续。


## 最新批次：持续更新调度（T13 / T19 / T20 / T33）

Provider启动即检查持久截止时间，之后每分钟检查；成功/执行六小时周期与冷却均保留，已有任务复用。手动与自动请求共用锁定去重。直播按到期/待查索引各取最多100个ID，逐条事务，关闭联网仍撤下到期入口；UTC审核期限、开赛前24小时切换每小时检查、改期唤醒和新URL检查已验证。

后端141项pytest/ruff通过，新增16项包含独立SQLite连接并发与迁移回退。20,000条合成比赛/直播记录的一轮只加载10条，22.44ms；20轮空闲P95 0.44ms/2条SQL，完整候选查询计划命中新索引。两次全新worker进程第一次合成抓取1次，第二次0次。不是实际HEAD、真实上游、MySQL/Azure、大批积压或1,000用户发布证明。OpenAPI字节相同，客户端没有业务源码变化。

本机0600备份data/before-scheduler-20260910-032643.db，迁移c72b961e430a已应用；24张原表原字段/行在迁移和重启后相同，163场、1个账号与原关注/订阅保留。主API session69237/PID29651、worker session84983/PID29664运行新代码，显式本地体验/loopback且关闭访问日志；旧API27052/worker84881已正常停止。真实HTTP健康/本地身份/原配置通过，9月匿名47条赛程JSON不变；未到下一轮的F1没有因启动而提前抓取。

浏览器19实际显示本地账号、湖人关注和48场月历，console error0，检查后关闭；原关注草稿16、公共订阅12、旧合成维护10保留。Web3000 session43940、production预览3002 session12042、旧直播夹具3001 session29715、扩展预览18792 session25462保持原状态，3001不代表本批新服务。

说明anke-sports-cloud/docs/scheduling.md；证据evidence/scheduling-2026-09-10.md、scheduler-capacity-final-2026-09-10.json及主库迁移/读回JSON。所有本批任务保持in_progress。继续项目级YouTube配额、通知到发布/积压规模、隐私删除与脱敏、真实MCP业务调用、Chrome和云/设备验收；托管Chrome创建独立云资源、用户负责登录的授权仍有效。本批无云资源、push、部署或真实上游请求。完整产品目标继续。


## 最新批次：账号删除与隐私竞态（T07 / T31 / T33）

账号删除锁定当前用户，清除个人配置、授权/会话、链接/匹配、投影、命令回执、个人任务与重放记录，撤销Feed并擦除正文与密文。旧配置、授权交换、命令和Worker不能恢复删除账号。Firebase清理固定项目并入outbox，失败可重放；只用合成适配器验证，真实云身份清理尚未验收。HTTP异常仅记录请求ID/错误类别，SQL隐藏参数。最小撤销记录与共享元数据保留，详情见anke-sports-cloud/docs/account-deletion.md。

后端154项pytest/ruff通过，新增13项包含独立SQLite连接及真实HTTP竞态；两条既有Starlette/AnyIO弃用警告。Web/扩展typecheck、production build通过，OpenAPI和生成客户端更新。独立IPv6合成账号网页验证取消不变、键盘确认、完整退出到游客及外部缓存提示；1440×1000、1280×800、1024×800确认弹窗无溢出，console error0。隐私夹具3004已正常停止、临时库清理，20/21临时标签关闭。主体验账号删除按钮仍禁用，原关注草稿16/公共订阅12/旧维护10保留。

无迁移。0600备份data/before-privacy-20260909-200145.db（UTC文件名）；重启与临时会话登出后全部25表行哈希不变，163场与原账号/关注/Feed保留。证据evidence/privacy-2026-09-10.md及privacy-browser-before/after、privacy-main-readback JSON。现有自动调度于本地03:43成功获取真实Jolpica，重启未提前再次抓取；合成删除测试没有Firebase/上游调用。

当前主API8787 session58923/PID31467，worker session41622/PID31478；四个显式local/preview/public/web参数与关闭访问日志保持。主Web3000 session43940/PID53698；production Web3002 session73957/PID31177已更新最终构建。旧合成维护API3001 session29715/PID92350、扩展预览18792 session25462/PID86801仍保留。旧主API29651/worker29664/production17719已正常结束。

T07/T31/T33保持in_progress。后续继续项目级YouTube配额、通知到Feed/积压规模及新增个人锁的容量影响、Codex实际业务调用，真实Firebase/MySQL/Azure、Chrome安装和设备验收；备份恢复保留删除决定与云日志/告警仍待验收。主页面 http://127.0.0.1:3000/calendar 可继续检查。托管Chrome创建并配置独立云资源、用户负责登录的授权保留。本批无push、部署或云资源变更，完整目标继续。


## 最新批次：通知到多人 Feed 容量（T13 / T15 / T16 / T18）

Feed先按关注/显式加入/既有投影在SQL缩小候选，再应用原排除与历史规则；SQLite结构化JSON成员与MySQL JSON_CONTAINS分支分开。视频匹配读取发布时间窗口及所有旧关联，仍能撤销改期到窗口外的链接。账号锁下合并同用户pending/attempts=0的发布任务；running/重试任务保留后继，重试deadline不变。无跨账号结果缓存。

隔离20,000活动比赛、1,000账号、200活跃创作者、每Feed30场：同一热门创作者通知应更新900份，100份预先屏蔽。基线150秒后仅完成556份/剩381任务；最终900份全发布，P95 34.64秒、36.21秒清空。实际loopback签名HTTP、worker、ICS；YouTube是合成适配器。最终解析全部1,000份ICS的30,000个UID均不变。三条不同但元数据相同的通知只保留1,000条待发布任务，44.15秒清空，所有Feed版本/ETag/时间不变。P95为一条通知下900份Feed的观察值，不是多通知独立样本或云端承诺。

后端158项pytest/ruff通过，新增4项含独立SQLite连接并发、结构化ID与全扫描语义对比、旧关联撤销。两条既有弃用警告。OpenAPI/config schema字节一致；客户端本批仅文档，无新UI或重复构建。MySQL只做SQL编译。证据anke-sports-cloud/evidence/content-capacity-2026-09-10.md；原始before/after/final/verified JSON均保留，verified包含完整UID检查，源hash与最终代码一致。

无迁移。0600备份data/before-content-capacity-20260909-202020.db；主账号实际完成一次Feed发布，24张非outbox表原行hash相同，旧outbox行相同，仅多一条done任务。163场、原账号/关注/Feed/投影与会话保留；真实HTTP原配置、Feed正文逐字相同及200/304通过。首次核对脚本用错jobs表名，已按outbox改正，未重复发布。仅注销脚本临时cookie，原草稿与浏览器标签未操作。

主API8787 session89340/PID32417，worker session88069/PID32428运行最终代码，四个显式local/preview/public/web参数、关闭访问日志保持。旧31467/31478正常停止。Web3000 session43940/PID53698、production3002 session73957/PID31177、旧维护夹具3001 session29715/PID92350、扩展预览18792 session25462/PID86801继续运行。原关注草稿16/公共订阅12/维护10保留。容量实验临时HTTP与数据库均已正常清理。

T13/T15/T16/T18继续in_progress。项目级YouTube配额仍未实现（已核对官方成本/太平洋午夜规则）；后续继续配额、同时多频道/历史积压、Codex真实业务调用，以及Firebase/MySQL/Azure、Chrome安装和设备验收。托管Chrome创建配置独立云资源、用户负责登录的授权继续有效。无真实上游请求、push、部署或云资源变更；完整目标继续，本回合有代码与容量证据进展。


## 最新批次：YouTube 项目预算（T13 / T14 / T15 / T33）

HTTP/MCP/后台 Data API 请求在独立 SQL 短事务预留额度，按项目与太平洋日计数；失败/业务回滚不退额，换Key不重置。网络准备先于账号写锁，之后复查删除、版本、幂等回执。uploads 分页和视频详情分成独立任务；共享配额等待不耗尽五次真实失败上限，attempts 保持递增以拒绝旧worker提交。创作者页显示最早恢复时间，30秒轮询并自动退出等待；保留已有日历。

后端174项全量pytest/ruff通过（2条既有弃用警告），新增16项覆盖独立连接并发、超时/业务回滚、HTTP与MCP回执、网络中删除/版本变化、夏令时、迟到响应、跨日限流、任务恢复及迁移。Web/扩展typecheck、最终production build通过，OpenAPI及客户端已更新。独立IPv6合成页面完成等待/恢复实际渲染，console error0；Google/Hub无真实调用。

主库备份data/before-youtube-budget-20260909-205106.db（UTC文件名，0600），已迁移e42c08f771d3。24张原业务表旧列/行哈希相同（另有alembic_version更新），预算表为空、旧quota_waits均0。真实HTTP原配置/Feed正文不变，200/304通过；163场、原关注和浏览器会话保留。MySQL仅离线DDL。

主API8787 session45462/PID34189、worker session77294/PID34200运行最终源码，四个显式local/preview/public/web参数与关闭访问日志保持。production Web3002 session65485/PID33834、主Web3000 session43940/PID53698。旧API32417/worker32428/production31177已停止；隔离配额API3004 session40885已停止并清理，临时22/23/24已关闭，原草稿16/公共订阅12/旧维护10保留，viewport已重置。

说明anke-sports-cloud/docs/youtube-budget.md，证据evidence/youtube-budget-2026-09-10.md及youtube-budget-main-2026-09-10.json。预算为本服务预留值，不是Google真实额度/余额。T13/T14/T15/T33仍in_progress；下一步仍需同时多频道/积压、Codex真实业务调用，真实Firebase/MySQL/Azure/YouTube、Chrome安装和设备验收。M6 Google直连未实现。完整目标保持active，本回合有实现和验收进展。用户未来托管Chrome创建配置独立云资源、负责登录的授权有效；无push、部署或云资源变更。


## 2026-09-10 · 真实 MySQL 与 Firebase 开始接入

实际 MySQL Community 8.4.11 临时进程完成后端验证：MySQL 模式179 passed，SQLite177 passed/2 skipped；ruff通过，契约字节不变。修复精确身份比较、事务旧快照和预算首次插入竞争。说明docs/mysql-validation.md，证据evidence/mysql-2026-09-10.md；不是Azure运行证明。CI新增固定版本MySQL但尚未push/执行。

主SQLite备份data/before-mysql-compat-20260909-232308.db（UTC，0600）并迁移f809a45c2d71；25张业务表逐行哈希一致，163场保留。主API session27072/PID37845、worker session29987/PID37859，保留原四个显式本地参数。状态与赛程200；首次读回脚本使用错误start/end参数得到422，改为契约from/to后通过。证据evidence/mysql-main-2026-09-10.json。

用户切换Google账号后已创建独立Firebase项目anke-sports-dev（Anke Sports Dev），Spark免费方案，Analytics/Gemini关闭；注册Web应用Anke Sports Web Dev，启用Google登录，localhost与127.0.0.1授权域名读回通过。仅Web配置保存本机data/firebase-dev-web.json（0600、Git忽略）。服务端凭据及真实登录仍在接入，主预览未切换到Firebase。此前“未创建云资源”仅为历史状态。用户再次确认后端使用Azure Functions；Azure资源仍未创建/部署，YouTube真实配置尚未完成。


## 2026-09-10 · Firebase真实登录已验证

独立项目anke-sports-dev启用Google登录、localhost/127.0.0.1授权，专用Anke Sports Auth Dev服务账号使用Authentication Admin角色。本机0600凭据放在Git忽略的data目录，下载原件已移除。真实Google登录后，后端Firebase验签所得UID与Admin读回同一用户相符；UI保存防剧透、新页面保留登录及偏好、恢复原配置和退出均通过。新账号个人revision=2，2个发布任务done。真实删除/撤销和多账号/多设备尚未验证。

身份验收使用独立空库，不改主3000预览：Web http://localhost:3003/calendar（session91087/PID38482），API8788（session85403/PID38459），worker（session73541/PID38782）。源码副本data/firebase-dev-webapp，本机启动器data/run-firebase-dev.py，运行参数data/firebase-dev-runtime.json。最后页面为游客，用户可自行Google登录继续检查。主API8787仍session27072/PID37845，主worker29987/PID37859；原数据/关注草稿保留。MySQL临时进程已通过mysqladmin正常关闭，session31795退出0，缓存留在本机。

用户明确确认Azure Functions后端；Azure账号已登录，查看过Azure subscription 1与speech资源组，仅只读。独立Anke Sports Functions/MySQL/Storage、区域/规格/费用与HTTPS域名尚未配置或部署。安装的Azure Prepare已更新1.2.44，限定显式azd/已有azure.yaml，本项目尚未选择azd，因此不套用其流程。资源分工和接入顺序已写docs/cloud-development.md。任务T07仍in_progress，缺失云权限/设备只影响对应验收。完整产品目标未完成；当前goal工具状态为usageLimited，未擅自修改。

MySQL后端本地提交5ef0118；客户端状态提交b70ef07。Firebase本批为本地配置与真实外部验收，无客户端业务源码变化；后端/客户端相关文档另行本地提交。没有push或Azure部署。


## 2026-09-10 · 登录反馈收尾，转入 Functions 验证

Codex内置浏览器未完成Google弹窗，曾返回auth/popup-closed-by-user；确切宿主原因未定位，不能认定Google拦截。Chrome实际账号状态刷新后保留，标签210263706保留给用户；两个浏览器不共享登录状态。用户明确停止排查内置浏览器。已完成的前端调整保留：登录错误显示在对话框内、等待时仍可复制地址、收到后端/me/calendar身份结果才进入成功回调；MCP/扩展网页授权共用组件。实际Chrome关闭弹窗后出现中文错误，重试可用；内置浏览器复制地址通过系统剪贴板核对。Web/扩展typecheck和production build通过。

主3000预览保留；真实Firebase实例仍Web3003 session91087/PID38482、API8788 session85403/PID38459、worker73541/PID38782，五个修改过的客户端文件已同步到隔离源码副本。无后端身份逻辑、云配置或数据库变更。下一批继续Functions安全打包与真实Core Tools/Azurite宿主验证，云部署与设备验收仍未完成。无push/部署。


## 2026-09-10 · Functions 本机真实宿主与安全打包（T05/T13/T28/T33）

已用Core Tools4.13.0、Python3.12、Azurite3.36.0运行43文件的实际源码归档；独立SQLite、一次性模拟器账号和合成比赛，无云凭据/真实上游。真实分钟Timer→Queue→outbox→ICS通过，重复消息不重做、304/正文/ETag保持；公共MCP工具发现与get_event调用通过，经宿主管理接口触发内容维护后过期请求实际删除。测试日志不含Feed令牌/存储密钥；不等同Azure平台日志证明。脚本正常退出，临时进程、数据和日志已清理。

补充.funcignore和显式运行时白名单打包脚本，拒绝符号链接与缺失入口；归档SHA256为0a54a4a8d9fb14d67e1c1d785eb3cb48ee7962bba63fa901b190e67b4df6552c。修复SDK默认请求版本高于Azurite的400问题，固定双方支持的2025-11-05；未关闭版本检查。180项pytest通过/2项MySQL条件跳过，ruff通过，依赖导出一致。CI新增打包检查，未push/远程运行。docs/functions-runtime.md及evidence/functions-runtime-2026-09-10.json保留证据。

主API8787 session27072/PID37845、worker29987/PID37859、Web3000 session43940/PID53698继续；本批仅Functions投递入口变化，主uvicorn/worker无需重启。production构建预览3002已重启为session20042/PID40149；真实FirebaseWeb3003 session91087/PID38482、API8788 session85403/PID38459、worker73541/PID38782保留。用户明确停止Codex内置浏览器登录排查，Chrome现有登录页保留。

下一批继续独立Azure开发资源与费用边界、远端Linux构建、MySQL TLS、Functions真实Firebase、Queue死信/告警/遥测，再推进YouTube/真实日历设备。Azure未创建/部署，无push，任务保持in_progress，完整产品尚未完成。


## 2026-09-10 · Azure开发模板、托管身份与费用确认（T07/T13/T33）

实际订阅a1187bf2-2e2f-4e05-aaea-407163a009f5/租户5ba85645-4c35-4efd-93ea-11c0890472d8已核对，East Asia支持Flex/MySQL8.4B1ms，相关Provider已注册。新建infra/main.bicep与resources.bicep：独立anke-sports-dev资源组，Flex Python3.12、专用VNet/MySQL B1ms20GB7天备份、LRS存储、托管身份及Key Vault。Bicep0.46.1编译及Azure订阅validate Succeeded；验证用HTTPS占位Web来源，未执行what-if/create/deploy。API public_url引用Azure实际defaultHostName。

云队列发送器支持绑定同名的托管身份配置，禁止回退本机Azure账号；原Azurite连接保持。Firebase新增SecretStr JSON配置，拒绝其他项目/非法格式并脱敏报错，保留ADC本机路径。真实独立Firebase Admin只读验证通过，未用真实Key Vault/RBAC；191项pytest通过/2项MySQL条件跳过，ruff通过，契约未变。44文件实际源码包下Core Tools/Azurite全链路回归通过，包hash0f0e28483705cdb78241b6f398335ac170981551cc1d4b18e79765202daac8a1；临时宿主/模拟器均正常退出清理。

详见docs/azure-development-plan.md，模板、价格、Firebase JSON和Functions回归证据位于evidence。官方零售价MySQL基线约US$23.88/月，Functions等按量另计；建议参考预算US$40/月，不是硬上限。用户随后要求参考Anke Money降低费用，原方案未获批准，暂缓创建收费资源。首次Pricing工具SKU映射为空、补查零售API遇429；保留成功数据，不假报免费额度。

主API8787 session27072/PID37845、worker29987/PID37859、Web3000 session43940/PID53698，构建预览3002 session20042/PID40149；Firebase Web3003 session91087/PID38482、API8788 session85403/PID38459、worker73541/PID38782均保留。本批可选云凭据/队列路径仅在独立进程验证，未重启现有预览或迁移主库。Chrome登录可用页保持；按用户要求不再排查Codex内置浏览器登录。

下一步先评估低成本数据库选型，原MySQL创建暂缓；后续仍需准备真实Web HTTPS来源、私网迁移执行器/业务账号、Linux远端构建、真实Azure身份与SQL TLS、遥测脱敏/死信告警。YouTube、MCP目标客户端完整业务、Chrome安装和设备日历仍需验收。完整目标保持进行中，未push/部署。


## 2026-09-10 · 用户要求重新评估 Azure 成本

原 MySQL B1ms / US$40 月参考预算未获批准，用户要求参考 Anke Money 寻找更低成本方案。只读核对证实 Money 开发环境采用 Cosmos Serverless，生产为 autoscale 最大1,000 RU/s；实际账单保存在后端 Git 忽略的 data/money-cost-summary.md，未写入公开证据。订阅免费层已被其他账号使用，不改动既有产品资源。

已准备 docs/azure-low-cost-proposal.md：独立 Cosmos Serverless + 保留 Firebase/Functions/Queue/Key Vault 的候选。香港单价 US$0.31/百万 RU、US$0.25/GB月；100万/1,000万 RU +1GB的数据库示例为US$0.56/3.35，非实测或月账单承诺。价格证据 evidence/azure-cosmos-pricing-2026-09-10.json。整体早期低流量设计目标US$5–10/月，仍取决于后台调度、实际RU与其他用量。

本批只做只读成本核对和计划更新，SQL源码/现有数据/主进程未变。SQL→Cosmos的分区、原子outbox、ETag、稳定Feed发布和删除竞态需要实做实测；现有191项SQL测试不代表Cosmos已实现。数据库架构尚未更改；原MySQL模板保留供比较，不创建。未push、部署、迁移或修改任何既有Azure资源。完整产品目标继续，选型只影响相关后端与云验收。


## 2026-09-10 · 匹配规则 v2 与离线差异（T17/T18）

修复简介旧推广、比分和歧义日期触发错误自动附加。标题须自身明确比赛对象/分场次及阶段，ISO/中文/英文日期参与消歧；仅简介支持时保留人工候选。新增三个中文原因提示、无数据库/网络副作用的离线回放及规则源/数据哈希。32条显式合成样本：正确自动关联9→13、错误7→0、遗漏5→1；覆盖率50%→40.63%。这不是200条真实视频或98%验收，工具不自动改写线上关联。

后端214 passed/2 MySQL条件跳过、ruff通过，Web/扩展typecheck和Web production build通过；OpenAPI/config字节未变。新测试验证旧自动链接撤回时UID稳定、SEQUENCE增加，固定与屏蔽保留、重复重算保持ETag。实际IPv6隔离UI键盘确认/忽略后候选3→2→1，HTTP ICS的UID不变、SEQUENCE1→2、条件304；1440/1280/1024无横向溢出，1440/1024截图已检查，console error0。

可检查合成页面 http://[::1]:3004/creators ：实验session63263/PID44193，临时库，标签26保留；浏览器viewport已重置。其频道/比赛/视频均合成，未访问YouTube，停止实验即删除临时库。主库与真实Firebase验收库的8张相关表各自重启前后哈希相同；主库163场、217投影、无创作者/视频，未加入测试内容或迁移。

当前主API8787 session43380/PID44497，worker session62679/PID44496；保留四个显式local/preview/public/web参数，关闭访问日志。主Web3000 session43940/PID53698；构建预览3002 session31276/PID44147。Firebase API8788 session92869/PID44563，worker session24522/PID44564，Web3003 session91087/PID38482；保持原启动器和配置，仅同步三个前端原因文案，没有重新登录或排查IAB登录。旧主/Firebase API及worker、旧3002进程均已正常停止。

说明docs/matching-replay.md；证据evidence/matching-replay-before/after-2026-09-10.json、matching-ui-2026-09-10.json、matching-main-readback-2026-09-10.json。源码包已重建为44文件，SHA256 1a7c4b12cf7867724824ee0c8de7516b18fef89f3ddebdd250c4b32f870049b8，本批没有重复Core Tools宿主验收。前一轮云身份/未获批准的资源与价格候选已本地提交2263269；没有push或Azure创建/部署。

继续完整产品目标：低成本数据库选型待用户决定；真实YouTube采集/Hub与人工标注质量、真实体育来源覆盖、目标MCP业务调用、Chrome安装/生命周期、Azure及设备日历验收仍未完成。用户已停止IAB Google登录排查，保持该范围。此次为实际实现与运行证据进展，未把任务标done。


## 2026-09-10 · Codex实际业务调用与探测隔离（T28/T29）

安装的codex-cli 0.153.4在ephemeral/path=null的临时协议上下文实际调用MCP，没有模型回合或持久用户任务。25项隔离检查通过：公开来源/分页/事件、只读拒写、关注与链接写入、重复与冲突、任意userId拒绝、私人地址权限、跨HTTP/MCP同键、持久屏蔽、配置导出/预览/确认导入、受控access过期后的refresh轮换和撤销后实际401。worker发布后真实HTTP ICS正文与投影一致，UID不变、SEQUENCE递增、200/304通过。未把受控过期当作自然15分钟/7天验收。

修正旧探测脚本仅禁用显式MCP、未禁用插件/apps的问题。新脚本以进程覆盖禁用插件和apps，核对配置与全部inventory页；有thread时其他运行时须disabled且零工具，无thread的null状态须同时匹配明确disabled配置。旧授权/发现证据保留并补充范围更正。25项针对性pytest/ruff通过（11项新增隔离测试，2条既有弃用警告）；未重复全量后端或Web构建。

复现：后端 uv run python -m experiments.codex_business --output data/codex-business-new.json 。证据anke-sports-cloud/evidence/codex-business-2026-09-10.md及JSON，最终源码哈希已核对。临时SQLite/API/本地cookie、服务端grant、unique-name Codex凭据均清理，没写用户Codex配置。主预览和Firebase数据库未用于测试，没有迁移、实际上游或云资源调用；本批客户端只有README/STATE变化，服务端没有业务处理器/契约/依赖变化。

现有进程PID均读回存活，8787/8788健康及3000/3002/3003页面均200。主API session43380/PID44497、worker62679/PID44496、Web43940/PID53698；Firebase API92869/PID44563、worker24522/PID44564、Web91087/PID38482；构建预览31276/PID44147、合成匹配63263/PID44193均未重启。本批未操作浏览器标签；用户明确停止的IAB登录排查没有恢复。

T28/T29保持in_progress；真实Firebase身份与MCP组合、自然时间过期、模型自行选择工具、真实YouTube、HTTPS/Azure与设备日历尚未验收。低成本Cosmos候选仍未选定，未创建收费资源或改造SQL架构。无push/部署，完整目标继续。


## 2026-09-10 · YouTube 独立配置与云端实际读取（T04/T14/T15）

已通过授权 Chrome 的 Firebase Cloud Shell 核对 anke-sports-dev/736683203171，启用 youtube.googleapis.com/apikeys.googleapis.com，创建专用 anke-sports-youtube-dev Key，API target 读回仅 YouTube。未改 Firebase Browser key、IAM 或计费计划，Spark 保持。Key 未输出到模型上下文；Cloud Shell 专用 JSON 创建时0600，已有资源不能重复创建。

Cloud Shell 重连后于01:43:12 UTC实际执行三次只读 Data API：@Formula1频道、uploads前三条、第一条视频详情均200，public且频道归属一致。共三次/估算3配额单位，不经过应用SQL预算，不是Google余额。未给频道附加官方审核或给视频标注比赛关系。证据anke-sports-cloud/evidence/youtube-cloud-api-2026-09-10.md及JSON；资源和恢复步骤docs/youtube-development.md。

本机Downloads与后端data/youtube-dev.json未找到文件。用户答复暂时无法解锁，已暂停原生下载窗口检查。两次Download表单实际传输表为/home/z24develop，精确cloudshell download命令的确认及传输表才指向专用JSON；UI均显示Success，但浏览器download事件超时，不能判定本机落盘。下次先检查现有下载状态，仅安全保存专用JSON至Git忽略0600文件，核对后删除下载原件及云端临时JSON；如本次错误尝试产生home归档，只清理该新产物，不解包。不要再创建Key或读取其他凭据。Mac锁定是否导致下载未完成尚未证实。

Chrome标签210263789保留Firebase Cloud Shell，URL为https://console.firebase.google.com/u/1/project/anke-sports-dev/overview?cloudshell=true，iframe I0_1789003549632；原生窗口待用户可解锁后恢复。独立Cloud Console标签曾遇证书名称错误，没有绕过TLS。用户已停止的Codex内置浏览器Google登录排查未恢复。

本批没有应用源码、契约、依赖、数据库、运行配置变化或服务重启。8787/8788健康与3000/3002/3003日历页均200；只是原服务可达证据，未重复全量测试/构建。原主API session43380/PID44497、worker62679/PID44496、Web43940/PID53698；Firebase API92869/PID44563、worker24522/PID44564、Web91087/PID38482；构建预览31276/PID44147、合成匹配63263/PID44193按原记录保留，本轮未逐个核对PID。真实应用频道/worker/预算、Hub及长期续订、匹配质量、Feed/手机仍未验收。Azure低成本选型未改变、未创建收费资源，无push/部署；完整目标继续。

## 2026-09-10 · Cosmos 选择、Firebase 生命周期与 YouTube 本机发现

用户确定 Azure 数据层为 Cosmos NoSQL Serverless + Periodic；Anke Money 生产也已改成该组合，未来流量增长后原地转手动 Provisioned、再调整 Autoscale。Money 的最新状态来自本次用户说明，没有重新读取/修改其资源。当前设计 docs/cosmos-storage-design.md、当前 infra 模板和根/后端 AGENTS 已更新；旧 MySQL 模板移至 infra/legacy-mysql。Bicep 编译无警告，订阅级 Provider validate 为 Succeeded，独立资源组读回不存在；首次 validate 的本机50秒超时重试后通过。没有创建、部署或迁移 Azure。Cosmos 业务适配仍未实现，现有 SQL 测试、Functions 包和演示不能代表新存储可用。

Mac 解锁后将精确专用 JSON 排他存入 Git 忽略 data/youtube-dev.json（0600），校验项目/资源/字节一致，删除下载原件、Cloud Shell 临时明文和错误下载归档；未解包该归档，云 Key 保留。浏览器解锁/下载不再是阻塞。Firebase Cloud Shell 原标签可供用户检查，不需要再下载或创建 Key；本轮未恢复用户已停止的 IAB 登录排查。

experiments.firebase_lifecycle 使用新建无 Google 提供商绑定的专用真实身份、loopback HTTP 和临时 SQLite，真实 ID/refresh 撤销、恢复认证、关注/链接/ICS、HTTP consent/PKCE、确认删除、立即撤销 Feed/下游授权、独立 worker Admin 清理、远端不存在与重复清理均通过，共27项。首次实验脚本误读REST响应字段导致 KeyError，已修正为验证签名ID token；首轮账号也已独立读回不存在，清理记录/临时目录移除。最终测试账号和临时API/库清理均成功，没有操作已有 Google 用户。不是 Google 浏览器删除、设备、Codex/Firebase组合或 Cosmos/Azure 验收。

experiments.youtube_live 只复制原库115条公共 Jolpica缓存事件到临时库，以实际 API/worker/预算完成两轮各11项检查，每轮69条真实视频、198个视频/场次组合。第二轮明确183待审、15拒绝、0个人链接；85个窗口内ICS保持UID、条件请求304，但没有真实视频进入ICS，人工标注数0。主要原因是类型/场次不明确等，没有放宽规则。两轮各8单位，加先前Cloud Shell3，已知19估算/预留单位；实验账本独立，不是Google余额。两轮临时API/库已清理。

最终新增安全边界及删除相关25项pytest、ruff通过；两项已有弃用警告。最终两个探测器和相关应用源哈希与证据一致。客户端只有README/STATE变化，无源码/契约/依赖修改，未重复构建。证据（相对于服务仓）：evidence/firebase-lifecycle-2026-09-10.md及JSON、evidence/youtube-live-2026-09-10.md及JSON、evidence/azure-cosmos-template-2026-09-10.json。

原主API43380/PID44497、worker62679/PID44496、Web43940/PID53698；Firebase API92869/PID44563、worker24522/PID44564、Web91087/PID38482；构建预览31276/PID44147、匹配验收63263/PID44193按原记录保留，本轮未重启或逐项核对PID。没有为这些常驻实例配置YouTube Key/创作者，不声称页面已持续发现真实视频；现有用户数据没有迁移。实验和ARM验证进程均已退出。

下一步优先实现 Cosmos 仓储的 Firebase→关注→同分区outbox→Queue→已发布ICS纵向路径，再覆盖原SQL业务；持续YouTube配置需共用同一项目预算，真实Hub/续订、人工标注到ICS、云身份/RU/429/恢复与设备验收继续。T04/T07/T14–T18等保持in_progress，完整目标未完成。两仓本批分别本地提交，不push/部署。

## 2026-09-10 · Cosmos 文档仓储与发布器第一批代码

新增服务仓 app/document_store.py、document_accounts.py、document_feeds.py；Cosmos SDK固定4.17.0，使用独立MI，显式本机CLI需指定tenant/subscription。单分区原子批处理、ETag冲突、配置+幂等回执+outbox、领取/过期租约、令牌路由/轮换、不可变分块/清单与最终CAS发布已实现。app/calendar_rules.py抽出原SQL共用选择/历史保留/描述/ICS规则，SQL入口继续使用同一逻辑，OpenAPI完全相同。文档模式导入SQL运行时明确拒绝，不能回落到原业务库。

本地验证用专用持久文档适配器（SQLite仅存文档和ETag，独立连接），不得冒充Cosmos模拟器。真实SDK经过完全离线的HttpTransport，验证实际partition/If-Match/atomic/Strong请求、参数化分页、404及409/412/429/403；没有Azure数据面请求。修复SDK批错误不继承普通CosmosHttpResponseError、logging_enable=false仍输出分区头的问题；专用禁用logger隔离SDK原始日志，应用只留操作/状态/RU摘要。测试RU值为夹具，未实测云RU。

250个长中文/emoji合成事件多块发布中断仍保留旧Feed；恢复后完整250条可读。改期/轮换保持UID、重复内容版本/时间/ETag稳定；历史保留、未来取消、配置/删除/新租约阻止旧发布者、过期任务重新领取、删除先于回执重放、坏块拒绝半份ICS，以及独立进程重读均通过。全量pytest267 passed/2个条件MySQL skipped/2已有弃用警告（11.92秒）；随后只新增3项测试，最终文档33项通过（1.18秒），ruff通过。

因多台Functions客户端不能靠Session保证立刻读到其他实例的令牌撤销，模板/SDK改为Strong；保持Serverless + Periodic，Strong读取RU约为Session两倍，费用需要实测。Bicep与订阅级Provider validate再次Succeeded（anke-sports-cosmos-strong-validate）；仅validation占位Web URL，没有创建/部署资源、迁移数据或触碰其他产品。文档/机器证据：服务仓 evidence/document-foundation-2026-09-10.md及JSON，设计 docs/cosmos-storage-design.md。

产品app.main/function_app.py尚未接入文档仓储，现有SQL运行实例未重启；因此这些测试不是产品HTTP、真实Firebase+Cosmos或Queue/云恢复证据。8787/8788健康只作可达性读回，结果见JSON。此前YouTube/Firebase凭据和常驻实例配置未变，未重启或重新登录；本轮无浏览器操作。旧主API43380/PID44497、worker62679/PID44496、Web43940/PID53698；Firebase API92869/PID44563、worker24522/PID44564、Web91087/PID38482按原记录保留，PID未重新核对。本批测试/ARM校验句柄均已退出。

下一步：权威公共赛程/来源索引及完整批次读取→既有HTTP路由/Firebase/关注预览→Queue/Change Feed出站投递与补发→个人链接/创作者/删除/OAuth剩余模块。之后验证真实Cosmos权限/RU/429/恢复和全链路。最大配置/回执分块、旧或孤立generation GC、超大Feed流式读取仍待实现；当前不自动GC，不能声称长期存储受控。第一批不是完整存储迁移，更不代替T01–T35/设备等完整验收。


## 2026-09-10 · 文档日历接口与后台投递

新增权威完整赛程块/指针、同一路径的日历/关注/私人Feed HTTP及持久任务投递；原SQL组合移至sql_app，默认仍为SQL，文档模式不导入app.db。业务保存/回执/outbox原子提交，后台发布后重复命令仍返回原响应；改期和令牌轮换保持UID、内容不变保持版本/ETag。SDK4.17初始多分区空token问题以公开API首轮读取处理，两个分区/304的实际离线SDK测试通过。断点存indexes，不自触发；Queue不确定发送可重放，租约/退避持久化。

最终277项pytest通过、2项MySQL条件跳过、2条既有弃用警告（12.89秒）；Ruff、diff检查及完整OpenAPI一致。客户端无源码/契约/依赖变化，没有重复构建。新文档模式未迁移接口明确失败，完整合同导出拒绝接口子集覆盖。方案与证据：服务仓docs/document-runtime.md、evidence/document-runtime-2026-09-10.md及JSON。

浏览器在localhost:3006/following进入本地体验，选择演示联赛→预览12条（含2条历史）→确认保存，月历读回12场已加入。HTTP独立读回同一临时体验账号revision1、Feed revision2/published/12条唯一UID、304与HEAD通过；脚本自己的临时会话退出，浏览器会话保留，console error为空。API/HTML proxy session67857/PID83337，独立worker PID83338，IAB标签28保留日历。数据全合成；停止实验清理临时库。首次3005端口被既有node占用，本次失败启动已清理后改用3006，没有停止原服务。

主API/worker及Firebase/YouTube凭据配置未改，未重启已有实例。没有真实Firebase/Cosmos/Azure/上游调用、云资源创建、部署、push或主数据迁移。普通本地文档worker尚未自动执行每日窗口；Azure已注册每日窗口但未实测。每次catalog变化按页扫描owner目录，反向关注索引、旧任务/孤立块GC、大配置分块、SQL迁移以及内容/直播/公共Feed/OAuth/删除等仍待完成。此批不是整产品或完整存储迁移完成。
