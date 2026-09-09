# MCP 与应用连接

当前为本地实现与验收，尚未通过真实 Firebase、Azure 或 Codex 客户端集成验收。Chrome 本地安装包已在客户端仓库生成，实际安装与授权仍待验收。

## 使用入口

服务运行后，Streamable HTTP 地址为：

- `http://localhost:8787/mcp/public`：匿名、只读、3 个公开查询工具。
- `http://localhost:8787/mcp`：授权后的 11 个工具；最小权限 `calendar:read`。
- 网页 `/connect`：从客户端发起请求后显示账号、客户端自报名称、精确回调地址和权限；直接打开没有请求参数时显示引导。
- 网页设置中的「已连接的应用」：查看授权范围、到期时间并撤销。

地址以 `ANKE_SPORTS_PUBLIC_URL` 为准。OAuth 的 `resource` 必须使用同一个规范地址，不能在 `localhost` 与 `127.0.0.1` 之间替换。当前 `ANKE_SPORTS_WEB_URL=http://localhost:3000`；它与 127.0.0.1 的浏览器 cookie 独立，本地授权页可能需要再次选择体验账号。

## 工具与业务边界

| 工具 | 权限与行为 |
| --- | --- |
| `search_sources` | 公开对象，显式 real/demo；最多100条，`offset/next_offset` |
| `get_schedule` | 带时区的 `[from_time,to_time)`，最长180天，最多100条/页；`followed=true` 需要私人连接 |
| `get_event` | 比赛、来源、时间和可见链接；私人连接应用个人 block/pin |
| `get_my_calendar` | `calendar:read`；配置版本与订阅源发布状态，不返回私人地址 |
| `update_follows` | `calendar:write`；add/remove、expected_revision、幂等键 |
| `add_creator` | `calendar:write`；共用频道解析、范围和版本检查，需要真实 YouTube 配置 |
| `attach_event_link` | `calendar:write`；共用 HTTPS 校验和持久隐藏规则 |
| `remove_event_link` | `calendar:write`；只对本人隐藏，不删除其他人的链接 |
| `export_config` | `calendar:read`；配置格式同 Web，不包含凭据和 Feed 地址 |
| `import_config` | `calendar:write`；必须先 dry_run，再用精确 confirmation 和版本应用 |
| `get_calendar_feed` | **另需 `feed:read`**；敏感地址不进入日志或导出 |

分页游标绑定查询、账号配置版本和比赛更新时间；这些内容变化时返回 `CURSOR_EXPIRED`，重新请求第一页。Web 自动读取后续页，超过10000条时要求缩小范围，不静默截断。当前 SQL 查询仍需对大规模赛程进一步优化，输出边界不等于性能压测已通过。

写工具必须传8–128字符的 `idempotency_key`，允许字母、数字及 `._:-`。相同账号、键、操作和参数在24小时内返回原结果，包括当时的配置版本；随后读取当前状态。同键不同参数返回冲突。HTTP 对应写接口接受 `Idempotency-Key`；旧 Web 调用可不提供。业务变更、outbox 和回执在同一事务提交，回执不包含 Feed 令牌。MySQL 的账号行锁及并发行为仍需在 staging 实测。

## 授权实现默认值

以下是当前可调整的实现默认值，不代表已经验证了所有外部客户端：

- Firebase ID token 仅在 Web 的账号身份确认中使用。扩展/MCP 获得 Anke Sports 自有 opaque token，不转发 Google 凭据。
- PKCE S256、10分钟授权请求、2分钟单次授权码、15分钟 access token、最多7天 grant。refresh token 每次兑换即轮换，旧 refresh token 重放撤销整个连接。
- 数据库只保存授权码、access/refresh token 的哈希；动态客户端元数据加密保存。token 绑定 owner、client、issuer、resource、scopes 和到期时间，撤销后每次请求重新检查。
- 扩展资源是 `${PUBLIC_URL}/api/v1`，MCP 资源是 `${PUBLIC_URL}/mcp`，两者不能混用。扩展令牌不能管理连接、再次批准授权或删除账号。
- 支持动态客户端注册、授权服务器元数据、受保护资源元数据与 `iss` 响应。仅接受已登记的 HTTPS 或 loopback 回调。客户端应校验 metadata issuer、state、iss 和精确回调，并在 authorize/token 都发送 resource。
- `feed:read` 在网页默认不选。撤销应用使其令牌失效；已分享的 Feed 地址须独立轮换才能失效，原事件 UID 不变。
- 最小连接只读；写入和地址权限需要客户端显式请求。当前工具返回清楚的权限错误，自动增量授权和 Client ID Metadata Documents 尚未实现；动态注册用于兼容路径。
- 本地 worker 每分钟、Azure timer 每5分钟清理过期请求/令牌/回执和撤销的 grant；无引用的动态注册30天后清理。删除账号同时删除其连接、请求与回执。

HTTP 授权处理器和 MCP 传输使用锁定的官方 Python SDK `1.30.0`。本项目补充两项适配：token handler 将 resource 绑定传给 provider；revocation handler 允许无 client_secret 的 public client（此 SDK 版本的 revoke 请求模型错误地将该字段设为必填）。升级 SDK 时须复跑 OAuth/HTTP 测试。

## 可复现的本地浏览器验收

启动 API、Web、worker 后：

```sh
uv run python -m scripts.check_oauth_locally
```

打开脚本输出的 loopback URL，在网页确认本地体验账号。保持私人地址权限未选，允许连接。脚本在内存中完成 PKCE 和官方 SDK 的真实 HTTP 查询，回调立即跳转到不含凭据的结果地址。到设置撤销「本地连接验收」，再点结果页的检查撤销；原令牌应返回401。验证结束停止脚本；不要公开该脚本的监听端口。

自动验收：`uv run pytest -q tests/test_oauth.py tests/test_mcp.py`。包含不同账号隔离、拒绝/过期/重放/撤销、API 与 MCP audience 隔离、跨传输同键重试、失败回滚、持久 block、导入预览和分页变化。

云端仍需：独立 Firebase 实际登录、HTTPS 公网回调、Azure ASGI 启停与 MySQL 并发、限流/滥用测试、目标 MCP 客户端实际授权流程、Chrome 安装及 service worker 生命周期验证。当前注册数量和请求体上限不能代替生产限流。

依据：[MCP 官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk)、[MCP 2026-07-28 授权规范](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)、[OAuth 令牌撤销 RFC 7009](https://www.rfc-editor.org/rfc/rfc7009)。
