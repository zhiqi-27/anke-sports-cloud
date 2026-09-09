# Anke Sports 服务端状态

更新：2026-09-10。主 API 127.0.0.1:8787，session42770/PID18592；worker session70713/PID18603；关闭访问日志。显式本地SQLite/体验身份。主库163条比赛（48演示+115 Jolpica F1分场次），无合成频道/视频或公共直播记录。

已实现账号验证入口、个人配置/链接、稳定投影与ICS、事务outbox、local worker/Azure触发器、体育Provider接口、共享YouTube发现/签名通知/补查/匹配/人工确认、PKCE/刷新/撤销和HTTP MCP（公开3、私人11工具）。真实Firebase/YouTube/云队列与触发器仍未验收。

新增直播审核、候选URL注册表、地区/条件、草稿与发布版本分离、版本冲突、到期/撤回、HEAD检查和设备观察。业务变化、审计与outbox原子提交。维护者白名单默认空，自动联网检查默认关闭；到期处理独立。直播批68项pytest、ruff通过，2项已有弃用警告。OpenAPI与客户端类型同步。说明：docs/broadcasts.md；证据：evidence/local-2026-09-10.md。

迁移7b26efb1d423经SQLite升级/回退/再升级/check与MySQL离线DDL后应用本机。备份data/before-broadcast-20260910-002301.db为0600；原有19表逐行相同，主库广播记录0。旧迁移721f5dc7b8c2的备份data/before-oauth-20260909-230049.db保留。未连接Azure MySQL。

独立直播验收 http://localhost:3001/maintenance ：API session29715/PID92350，客户端3002 session12042/PID17719。临时库、合成赛程/来源/HEAD/观察；通过浏览器保存审核发布撤回及真实HTTP ICS读取，UID不变、SEQUENCE 2→3、事件保留。设备表单三层均未测试，非手机证据。为用户检查保留进程，停止API即删除临时库；该进程早于最后两处审计actor/元数据时间修正，最新源代码由68项测试覆盖。

Chrome ZIP已在客户端生成；实际Chrome安装因URL策略被拒，未绕过。目标MCP客户端、真实NBA/足球/YouTube、真实官方直播/设备/网络、并发规模（当前广播调度全量扫描）、隐私/脱敏/恢复演练、公测与发布仍需继续；Google直连属M6。

用户授权先本地、后托管Chrome创建配置独立云资源，登录由用户完成。没有push、部署、远端迁移或修改FormaLM资源。完整目标未完成。

## 后台恢复批次

新增任务租约和尝试次数条件提交、Provider单通道与熔断、5次崩溃上限、审计重放、任务完成时间、独立维护调度与脱敏错误。个人投影先锁定并刷新账号配置；无内容变化的恢复保持ICS版本/ETag。体育/YouTube key支持.env，使用SecretStr且显式进程环境优先。

最终83项pytest/ruff通过；真实子进程中断/事务回滚/租约恢复、CLI预览重放去重、22表备份还原和独立密钥恢复通过。详情docs/job-recovery.md、evidence/recovery-2026-09-10.md和机器JSON。没有将SQLite证据等同MySQL或Azure运行。

已应用本机迁移6e9edd6daa17；0600备份data/before-recovery-20260910-005423.db，原21表旧列/行相同、163场保留。NBA和足球各一条真实缺key失败已通过设置页触发并读回；保留错误/冷却，不假报成功，未访问上游。API健康读回local/ok，当前worker仍运行。T13/T33及QA-31外部项继续。

## 公共Feed批次

新增public_feeds和7229fa56d28e迁移，复用个人投影的描述/UID/版本/撤销/HTTP条件请求逻辑。游客只读快照，Provider与直播变更原子入队、每天检查窗口，个人数据不会混入。部署环境按具体已核验来源键启用公共分发，演示来源禁用。

本机0600备份data/before-public-feeds-20260910-012243.db，迁移前后原22表相同，主库163条保留。运行后有14份公共来源Feed；演示湖人12条，既有真实F1在窗口内85个分场次，HTTP 304通过。没有新增上游获取。92项pytest/ruff通过，客户端类型/构建与游客下载哈希验证通过。docs/public-feeds.md和evidence/public-feeds-2026-09-10.md记录详情。

主API session42770/PID18592，worker70713/PID18603运行最新代码。原3001临时直播API为旧版本，只保留既有维护演示，不能作为公共Feed验证环境。真实分发/HTTPS/手机/云/规模仍待完成。


## 最新批次：关注变更预览（T11 / T23）

“我的关注”先预览再确认，由后端共用投影筛选规则计算新增、移除、重叠保留和历史保留；支持无日期、暂停、待发布提示。Web提交绑定确认摘要和幂等键，赛程变化要求重新预览，配置版本变化重新读取关注。旧HTTP/MCP无摘要调用继续兼容。

后端98项pytest/ruff、Web/扩展类型和最终production build通过；新增6项针对所有权、只读、并集/屏蔽、历史/日期、摘要截断、冲突和幂等的测试。隔离浏览器确认：16条原订阅变为10条有效比赛+6条原UID移除通知，全部UID不变；取消预览不写配置，过期预览拒绝写入。修复长弹窗底部提交后看不到顶部错误的问题，错误会获得焦点并滚入视口。1440/1280/1024均无横向溢出，键盘打开/关闭/操作和实际HTTP发布通过。

主API session42770/PID18592、worker session70713/PID18603运行本批代码；主Web3000 session43940，验收production Web3002 session12042/PID17719。无需迁移，users/feeds/events/projections/sources五表完整行哈希保持相同，163条比赛保留。主API /api/v1/health=local/ok，匿名预览401。

主页面 http://127.0.0.1:3000/following 标签16保留新增NBA的待确认预览（新增8，含历史3，结果20），未保存，原关注不变。原公共订阅标签12、维护标签10保留。关注夹具3003/标签15已关闭，临时库已删除；experiments/follows_ui.py可重新创建。viewport已重置。3001仍是旧版独立合成维护API，不用于新功能验收。

说明：docs/follow-changes.md；证据：evidence/follow-preview-2026-09-10.md 和同名JSON。T11/T23保持in_progress，真实Firebase多端、MySQL并发/规模、外部日历移除/刷新仍待验收。下一步继续原完整目标：YouTube跨任务并发、隐私脱敏、目标MCP客户端与规模；后续托管Chrome云资源创建授权保留。无push、部署或云资源变更。
