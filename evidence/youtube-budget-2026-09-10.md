# 项目级 YouTube 预算 · 本地验收

后端全量 `uv run pytest -q`：174 passed，2 条既有 Starlette/AnyIO 弃用警告；`uv run ruff check .` 通过。本批新增 16 项（15 项预算/集成参数化用例与 1 项迁移用例）。Web/扩展 typecheck、契约重新生成、最终 production build 通过。

## 隔离验证

`tests/test_youtube_budget.py` 使用临时磁盘 SQLite、独立 SQL 连接和 HTTPX MockTransport，仅 Google 返回值是合成，无真实 API key/上游调用。

- 12 个并发调用共用上限 6，只发出 6 个请求；切换 Key 不能继续发出。channels/playlistItems/videos 共用桶。
- 超时仍扣额；频道准备成功后配置冲突，业务回滚而预算保留。网络中途从独立连接删除账号或修改配置，最终分别 403/409，不恢复创作者。
- HTTP resolve 与官方 MCP SDK add_creator 使用同一桶；已完成同键重放不再调用 Google。两个在途相同命令各计一次网络预留，最终同一回执、只改一次账号版本。最终回执使用锁定读，避免 MySQL 一致性快照读取旧回执；实际 MySQL 并发仍未验证。
- 太平洋春秋夏令时、下调立即生效与上调次日生效、跨午夜 Retry-After、旧日迟到额度错误以及慢请求成功均覆盖。
- 429 HTML、403 rateLimitExceeded + 7200 秒 Retry-After 与 quotaExceeded 分类；状态 API 不输出项目 ID/Key。
- 连续 7 次容量等待后仍可执行，attempts 保持 1–7，旧 claim 提交被拒；之后 5 次真实失败才终止。
- 单日 1 单位：uploads 任务成功并排入视频子任务，子任务不消耗领取次数便等到下日，随后成功抓取详情。缺项目或未列入成本表的 endpoint 不联网。

`tests/test_youtube_budget_migration.py` 在旧版 outbox 留有三次尝试的合成任务，升级/check/回退各两轮，原列逐项相同，新增等待次数为 0。MySQL 只编译 `c72b961e430a:head --sql`；不是 MySQL 实际迁移或并发证明。

## 浏览器

`experiments/youtube_budget_ui.py` 是临时 SQLite + IPv6 loopback 的合成账号，代理最终 production Web3002，禁止访问 YouTube。独立标签 22/23，不触碰主关注草稿16。

已查看实际 1440×1000、1280×800、1024 桌面渲染。检查中修复了等待提示与表单间距，以及“正在检查”与全局等待矛盾；最终窄桌面提示可读，1440恢复态布局正常。POST 合成恢复入口后，同一标签不刷新便由正常轮询移除暂缓提示、恢复频道检查文案。待确认区继续显示；未声称产生真实视频或完成后台同步。console error 0。临时标签22/23关闭、viewport恢复，API3004正常停止并清理临时库。

## 主体验库

备份 `data/before-youtube-budget-20260909-205106.db`（UTC 文件名，0600，integrity_check=ok）。停旧 API/worker 后迁移到 e42c08f771d3；24 张原业务表旧列/行逐项哈希相同（另有 alembic_version 更新）。新预算表为空，所有旧 job 的 quota_waits=0。

重启后真实 HTTP 本地登录/原配置、私人 Feed 正文逐字相同、200/304 通过；只注销脚本自己的会话。163场、原账号/关注/Feed/投影均保留，重启没有提前抓 F1。记录见 [脱敏读回 JSON](youtube-budget-main-2026-09-10.json)。主浏览器临时标签24显示本地账号、湖人关注与48场月历，实际渲染正常、console error0；关闭24，原标签1/2/10/12/16保留。

当前主 API session45462/PID34189，worker session77294/PID34200；显式 local/preview/public/web 参数及关闭访问日志。production Web3002 session65485，主 Web3000 session43940；旧合成直播3001不代表当前后端。没有真实上游请求、云资源变更、push或部署。

T13/T14/T15/T33 保持 in_progress。尚缺 Google 项目/key、真实配额与Hub、跨频道积压、真实 MySQL/Azure、Chrome安装、Codex实际业务调用与设备刷新证据。此次账本是本服务预留值，不等于 Google 的用量或剩余额度。
