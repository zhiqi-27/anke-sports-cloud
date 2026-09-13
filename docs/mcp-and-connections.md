# MCP 与应用连接

当前为本地实现与验收。已用安装的 Codex CLI 0.153.4 完成真实 HTTP OAuth、工具发现和撤销后重新发现；Codex 实际工具调用、真实 Firebase 与 Azure 仍未验收。用户已于2026-09-13取消Chrome扩展，公网闭环只验MCP。

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

分页游标绑定查询、账号配置版本和比赛更新时间；这些内容变化时返回 `CURSOR_EXPIRED`，重新请求第一页。Web 自动读取后续页，超过10000条时要求缩小范围，不静默截断。查询现已按日期筛选、分页后批量读取链接；本机20,000场活动数据的HTTP场景有实测，完整云容量仍未通过。详见[查询与容量边界](schedule-queries.md)。

写工具必须传8–128字符的 `idempotency_key`，允许字母、数字及 `._:-`。相同账号、键、操作和参数在24小时内返回原结果，包括当时的配置版本；随后读取当前状态。同键不同参数返回冲突。HTTP 对应写接口接受 `Idempotency-Key`；旧 Web 调用可不提供。业务变更、outbox 和回执在同一事务提交，回执不包含 Feed 令牌。MySQL 的账号行锁及并发行为仍需在 staging 实测。

## 授权实现默认值

以下是当前可调整的实现默认值，不代表已经验证了所有外部客户端：

- Firebase ID token 仅在 Web 的账号身份确认中使用。MCP 获得 Anke Sports 自有 opaque token，不转发 Google 凭据。既有扩展资源分支只保留历史兼容，不属于当前交付。
- PKCE S256、10分钟授权请求、2分钟单次授权码、15分钟 access token、最多7天 grant。refresh token 每次兑换即轮换，旧 refresh token 重放撤销整个连接。
- 数据库只保存授权码、access/refresh token 的哈希；动态客户端元数据加密保存。token 绑定 owner、client、issuer、resource、scopes 和到期时间，撤销后每次请求重新检查。
- MCP资源是 `${PUBLIC_URL}/mcp`。既有 `${PUBLIC_URL}/api/v1` 扩展audience继续保持隔离，但不进入当前发布或验收。
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

## 连接 Codex

以下使用本机规范地址。长期使用时，在 Codex 设置的 MCP servers 中添加 Streamable HTTP 地址，或运行：

```sh
codex mcp add anke_sports --url http://localhost:8787/mcp
codex mcp login anke_sports --scopes calendar:read
```

在浏览器确认 Anke Sports 账号、回调和读取权限。需要修改关注或链接时，重新授权并显式请求 `calendar:read,calendar:write`。只有确实需要读取私人订阅地址时才额外申请 `feed:read`；该地址不能放进公开记录。发现11个工具并不表示拥有全部权限，业务调用仍逐项检查 scope。Web 设置可以撤销连接；`codex mcp logout anke_sports` 清理客户端保存的凭据。

Codex 的配置文件也可使用以下内容（选择用户配置或受信任项目的 `.codex/config.toml`；不要覆盖其他设置）：

```toml
[mcp_servers.anke_sports]
url = "http://localhost:8787/mcp"
```

只读公开赛程可另设 `http://localhost:8787/mcp/public`，不需要登录。配置和 OAuth 支持依据：[OpenAI 官方 MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。本项目没有为当前用户自动写入长期 MCP 配置。

### 临时检查，不改 Codex 配置文件

先启动 Anke Sports API/Web。选择一个尚未配置过的独立名称，使用进程级覆盖发起登录：

```sh
codex -c 'mcp_servers.anke_sports_local_check.url="http://localhost:8787/mcp"' mcp login anke_sports_local_check --scopes calendar:read
uv run python -m scripts.check_codex_discovery
uv run python -m scripts.check_codex_discovery --name anke_sports_public_check --url http://localhost:8787/mcp/public
```

在 Web 设置中撤销刚才的 Codex 连接后，再检查并清理客户端凭据：

```sh
uv run python -m scripts.check_codex_discovery --expect unavailable
codex -c 'mcp_servers.anke_sports_local_check.url="http://localhost:8787/mcp"' mcp logout anke_sports_local_check
```

脚本调用安装版本的 Codex App Server `config/read` 和 `mcpServerStatus/list`，只接受 loopback HTTP。它先读取有效配置，再通过本进程覆盖禁用其他已配置 MCP、插件和 apps，并检查全部 inventory 页；不会创建任务、调用模型、执行业务工具、读取令牌或改配置文件。无thread的条目可能返回null运行状态，须结合明确disabled配置和零工具判断。可用 `--codex` 指定可执行文件、`--output` 写入新的脱敏 JSON。CLI 登录会按 Codex 自身设置保存 OAuth 凭据，需按上述步骤清理。

本机验证结果：私人11个工具、匿名3个；网页撤销后私人0个工具。撤销后 `authStatus` 仍可能是 `oAuth`，它表示客户端有已保存凭据，不能作为服务端仍接受授权的证据。退出登录后为 `notLoggedIn`。只有结合 Web 撤销、服务端健康和授权记录清理，才能把失败发现归因于本次撤销；单独的 `--expect unavailable` 也可能是网络或启动失败。

本次 Chrome 回调最终页显示 `ERR_BLOCKED_BY_CLIENT`，未重试被拦截页面。Codex CLI 已明确报告登录成功，随后独立 Codex 进程发现11个工具；授权传输成功与浏览器完成页显示分别记录。详见 [Codex 实测证据](../evidence/codex-client-2026-09-10.md)。

### 实际 Codex 业务调用

```sh
uv run python -m experiments.codex_business --output data/codex-business-new.json
```

该实验自动创建独立临时SQLite/API/合成身份，经同一HTTP授权接口允许明确的测试权限。安装的Codex使用不落盘的临时协议上下文调用工具；没有模型回合或用户持久任务，也不更改用户Codex配置。临时授权和客户端凭据在finally中撤销/退出，服务与数据库清理。不要连接现有业务库运行该实验。

本机25项检查通过：查询与分页、只读权限拒绝、关注写入/重试/参数冲突、身份参数拒绝、跨HTTP/MCP同键链接、持久屏蔽、配置导出/预览/应用、真实HTTP ICS稳定UID与200/304、模拟access过期后的实际Codex刷新轮换，以及撤销后拒绝调用。完整证据和旧探测隔离范围更正见 [实际业务验收](../evidence/codex-business-2026-09-10.md)。

完整客户端验收仍需自然时间过期、模型自行选择工具及真实YouTube创作者接入。云端还需独立 Firebase 身份与MCP组合、HTTPS 公网回调、Azure ASGI 启停与数据库并发、限流/滥用。Chrome安装及service worker生命周期已退出范围。当前注册数量和请求体上限不能代替生产限流。

依据：[MCP 官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk)、[MCP 2026-07-28 授权规范](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)、[OAuth 令牌撤销 RFC 7009](https://www.rfc-editor.org/rfc/rfc7009)。
