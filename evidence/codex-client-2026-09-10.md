# Codex 本地客户端验收 · 2026-09-10

环境：安装的 `codex-cli 0.153.4`，Anke Sports 本机 API8787/Web3000，SQLite、本地体验账号。主后端业务代码 `07c0ad9`，未部署或使用 Firebase。日期按 Asia/Shanghai 记录。

## 实际操作与结果

1. 使用进程级 `-c` 配置与独立名称 `anke_sports_local_check` 执行 `codex mcp login --scopes calendar:read`。Codex 默认策略成功动态注册，授权请求携带 `S256`、loopback callback 和规范 `resource=http://localhost:8787/mcp`。
2. CLI 打开 Chrome 的 Anke Sports 授权页。点击本地体验身份后，页面显示客户端自报名称 Codex、准确回调、资源与唯一勾选权限「查看我的日历」。允许连接；CLI 返回 `Successfully logged in`、退出码0。没有申请写入或 `feed:read`。
3. 新的 Codex App Server 进程执行 `mcpServerStatus/list`，通过真实 HTTP 初始化和工具发现返回11个工具。其凭据由 Codex 保存/使用，验证脚本未读写令牌。
4. 另一个公开入口探测返回3个匿名工具。公开端 `authStatus=notLoggedIn` 是正常结果，不代表连接失败。
5. Chrome 设置页显示 Codex、仅「查看我的日历」和7天到期时间。点击「撤销连接」后，页面显示没有连接的应用。
6. 新的 Codex 进程再次发现私人入口：0个工具；客户端仍报告 `oAuth`。这个字段不是有效授权证明。随后 `codex mcp logout` 明确移除本次凭据；再次探测为 `notLoggedIn`、0个工具。
7. 主 API 健康为local/ok；本次 grant 最终已被 worker 清理，active_grants=0。主库仍163场、1个用户、0个创作者与0个视频。没有保存关注草稿或修改比赛、链接、Feed配置。

## 浏览器与实现限制

- Chrome 回调最终页显示 `ERR_BLOCKED_BY_CLIENT`。未重试或绕过该拦截；它发生在 CLI 已成功接收授权的流程中。后续独立客户端发现证明授权可用于初始化，但完成页的视觉反馈不能标为通过。
- 本次只执行 Codex 授权和 inventory。没有新建 Codex 任务、启动模型回合，也没有调用业务工具。因此不证明 Codex 能实际选择工具、查询赛程或写入关注；此前 Python SDK 工具测试保持独立证据。
- 主配置文件没有修改；进程级覆盖会合并已有 MCP 表，而非整体替换。脚本先读取有效配置，再禁用其他已配置服务，仅在隔离确认后调用 inventory。初版引用键的覆盖方式被 Codex 拒绝，改为 TOML 表内的明确键后通过；这只是探测器修正，没有降低服务验证要求。
- `--expect unavailable` 仅检查没有发现工具，也可能由网络/启动失败导致。这里结合撤销 UI、授权记录清理和仍正常的 API 判断；不要据其单独推断401或令牌失效原因。
- 动态注册元数据按既有保留规则清理；清除客户端凭据与撤销服务端 grant 是两个步骤。本次均已完成。
- 没有云资源创建、push、部署、数据库迁移或依赖更新。真实 Firebase、Azure、长期刷新、Chrome 扩展安装和手机日历仍未验收。

## 可检查的文件

- `codex-private-discovery-2026-09-10.json`：实际私人11个工具。
- `codex-public-discovery-2026-09-10.json`：实际公共3个工具。
- `codex-revoked-discovery-2026-09-10.json`：Web撤销后0个工具，客户端仍有凭据。
- `codex-logged-out-discovery-2026-09-10.json`：CLI退出后notLoggedIn、0个工具。
- `codex-cleanup-2026-09-10.json`：服务健康、授权清理和业务记录计数。
- `scripts/check_codex_discovery.py`：可复现的已安装 Codex 探测，原始配置、RPC错误和凭据不进入输出。

本批验证：探测器实际运行上述4种状态通过、ruff与差异格式检查通过。没有修改业务处理器或UI，未重复运行全量测试/构建；上一批后端116项测试的范围见频道并发证据。

协议依据：[OpenAI MCP 配置](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)、[Codex App Server](https://learn.chatgpt.com/docs/app-server)。App Server 方法结构同时使用本机0.153.4生成的JSON Schema核对。
