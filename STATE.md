# Anke Sports 服务端状态

更新：2026-09-10。主 API 127.0.0.1:8787，session79551/PID5876；worker session72823/PID5887；关闭访问日志。显式本地SQLite/体验身份。主库163条比赛（48演示+115 Jolpica F1分场次），无合成频道/视频或公共直播记录。

已实现账号验证入口、个人配置/链接、稳定投影与ICS、事务outbox、local worker/Azure触发器、体育Provider接口、共享YouTube发现/签名通知/补查/匹配/人工确认、PKCE/刷新/撤销和HTTP MCP（公开3、私人11工具）。真实Firebase/YouTube/云队列与触发器仍未验收。

新增直播审核、候选URL注册表、地区/条件、草稿与发布版本分离、版本冲突、到期/撤回、HEAD检查和设备观察。业务变化、审计与outbox原子提交。维护者白名单默认空，自动联网检查默认关闭；到期处理独立。直播批68项pytest、ruff通过，2项已有弃用警告。OpenAPI与客户端类型同步。说明：docs/broadcasts.md；证据：evidence/local-2026-09-10.md。

迁移7b26efb1d423经SQLite升级/回退/再升级/check与MySQL离线DDL后应用本机。备份data/before-broadcast-20260910-002301.db为0600；原有19表逐行相同，主库广播记录0。旧迁移721f5dc7b8c2的备份data/before-oauth-20260909-230049.db保留。未连接Azure MySQL。

独立直播验收 http://localhost:3001/maintenance ：API session29715/PID92350，客户端3002 session12338。临时库、合成赛程/来源/HEAD/观察；通过浏览器保存审核发布撤回及真实HTTP ICS读取，UID不变、SEQUENCE 2→3、事件保留。设备表单三层均未测试，非手机证据。为用户检查保留进程，停止API即删除临时库；该进程早于最后两处审计actor/元数据时间修正，最新源代码由68项测试覆盖。

Chrome ZIP已在客户端生成；实际Chrome安装因URL策略被拒，未绕过。目标MCP客户端、真实NBA/足球/YouTube、真实官方直播/设备/网络、并发规模（当前广播调度全量扫描）、隐私/脱敏/恢复演练、公测与发布仍需继续；Google直连属M6。

用户授权先本地、后托管Chrome创建配置独立云资源，登录由用户完成。没有push、部署、远端迁移或修改FormaLM资源。完整目标未完成。

## 后台恢复批次

新增任务租约和尝试次数条件提交、Provider单通道与熔断、5次崩溃上限、审计重放、任务完成时间、独立维护调度与脱敏错误。个人投影先锁定并刷新账号配置；无内容变化的恢复保持ICS版本/ETag。体育/YouTube key支持.env，使用SecretStr且显式进程环境优先。

最终83项pytest/ruff通过；真实子进程中断/事务回滚/租约恢复、CLI预览重放去重、22表备份还原和独立密钥恢复通过。详情docs/job-recovery.md、evidence/recovery-2026-09-10.md和机器JSON。没有将SQLite证据等同MySQL或Azure运行。

已应用本机迁移6e9edd6daa17；0600备份data/before-recovery-20260910-005423.db，原21表旧列/行相同、163场保留。NBA和足球各一条真实缺key失败已通过设置页触发并读回；保留错误/冷却，不假报成功，未访问上游。API健康读回local/ok，当前worker仍运行。T13/T33及QA-31外部项继续。
