# Anke Sports 服务端状态

更新：2026-09-10。主 API 127.0.0.1:8787，session69237/PID29651；worker session84983/PID29664；显式启用本地体验并关闭访问日志。显式本地SQLite/体验身份。主库163条比赛（48演示+115 Jolpica F1分场次），无合成频道/视频或公共直播记录。

已实现账号验证入口、个人配置/链接、稳定投影与ICS、事务outbox、local worker/Azure触发器、体育Provider接口、共享YouTube发现/签名通知/补查/匹配/人工确认、PKCE/刷新/撤销和HTTP MCP（公开3、私人11工具）。真实Firebase/YouTube/云队列与触发器仍未验收。

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

后端141项pytest/ruff通过，新增15项包含独立SQLite连接并发与迁移回退。20,000条合成比赛/直播记录的一轮只加载10条，22.44ms；20轮空闲P95 0.44ms/2条SQL，完整候选查询计划命中新索引。两次全新worker进程第一次合成抓取1次，第二次0次。不是实际HEAD、真实上游、MySQL/Azure、大批积压或1,000用户发布证明。OpenAPI字节相同，客户端没有业务源码变化。

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
