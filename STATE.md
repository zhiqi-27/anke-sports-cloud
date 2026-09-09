# Anke Sports 服务端状态

更新：2026-09-10。主 API 127.0.0.1:8787，session18261/PID97300；worker session71359；关闭访问日志。显式本地SQLite/体验身份。主库163条比赛（48演示+115 Jolpica F1分场次），无合成频道/视频或公共直播记录。

已实现账号验证入口、个人配置/链接、稳定投影与ICS、事务outbox、local worker/Azure触发器、体育Provider接口、共享YouTube发现/签名通知/补查/匹配/人工确认、PKCE/刷新/撤销和HTTP MCP（公开3、私人11工具）。真实Firebase/YouTube/云队列与触发器仍未验收。

新增直播审核、候选URL注册表、地区/条件、草稿与发布版本分离、版本冲突、到期/撤回、HEAD检查和设备观察。业务变化、审计与outbox原子提交。维护者白名单默认空，自动联网检查默认关闭；到期处理独立。68项pytest、ruff通过，2项已有弃用警告。OpenAPI与客户端类型同步。说明：docs/broadcasts.md；证据：evidence/local-2026-09-10.md。

迁移7b26efb1d423经SQLite升级/回退/再升级/check与MySQL离线DDL后应用本机。备份data/before-broadcast-20260910-002301.db为0600；原有19表逐行相同，主库广播记录0。旧迁移721f5dc7b8c2的备份data/before-oauth-20260909-230049.db保留。未连接Azure MySQL。

独立直播验收 http://localhost:3001/maintenance ：API session29715/PID92350，客户端3002 session83466。临时库、合成赛程/来源/HEAD/观察；通过浏览器保存审核发布撤回及真实HTTP ICS读取，UID不变、SEQUENCE 2→3、事件保留。设备表单三层均未测试，非手机证据。为用户检查保留进程，停止API即删除临时库；该进程早于最后两处审计actor/元数据时间修正，最新源代码由68项测试覆盖。

Chrome ZIP已在客户端生成；实际Chrome安装因URL策略被拒，未绕过。目标MCP客户端、真实NBA/足球/YouTube、真实官方直播/设备/网络、并发规模（当前广播调度全量扫描）、隐私/脱敏/恢复演练、公测与发布仍需继续；Google直连属M6。

用户授权先本地、后托管Chrome创建配置独立云资源，登录由用户完成。没有push、部署、远端迁移或修改FormaLM资源。完整目标未完成。
