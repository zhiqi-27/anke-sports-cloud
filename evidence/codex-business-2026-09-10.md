# Codex 实际业务调用 · 2026-09-10

安装的 `codex-cli 0.153.4` 通过 `mcpServer/tool/call`，实际访问 Anke Sports 的 Streamable HTTP 服务。最终 [25项机器结果](codex-business-2026-09-10.json) 全部通过。每次运行新建临时 SQLite、loopback API 和合成本地身份；没有使用主预览或 Firebase 验收数据库。

## 本次通过

- 公共来源查询、两页不重叠赛程与单场读取；私人只读连接不能修改关注或读取私人地址。
- 写连接修改关注、相同幂等请求重试、同键不同参数拒绝；传入任意 `userId` 拒绝，`calendar:write` 不会隐含 `feed:read`。
- 实际 MCP 附加链接，再通过 HTTP 同键重试得到同一结果。worker 发布后真实 HTTP ICS 与投影正文相同，200/304通过；UID不变，SEQUENCE递增。
- 移除链接后再次附加仍被屏蔽，已发布 ICS 移除链接但保留 UID；无新任务时重复读取正文与 ETag 相同。
- 配置导出不含凭据；导入预览不写配置，缺少预览凭证不能应用，准确凭证应用成功。
- **主动使临时库内 access token 过期**后，实际 Codex 自动恢复请求，并轮换 refresh token。这是401恢复测试，没有等待真实15分钟或7天。
- HTTP撤销grant后，同一已连接客户端的业务调用失败；观察到新的 MCP 401，API仍健康。随后 unique-name `codex mcp logout` 与独立 inventory 确认客户端凭据已清理。

合成身份通过HTTP登录和同一授权预览/确认接口建立。动态注册、S256、准确resource/scopes/state/issuer和Codex实际loopback回调均经过检查。没有操作用户浏览器或复用其cookies。

## 探测隔离修正

旧脚本只禁用显式 `mcp_servers`，没有禁用插件或内置 apps。此次发现旧配置仍可加载插件服务；在修正前未执行业务工具。这不推翻先前目标服务的授权/发现结果，但旧记录不能证明所有其他运行时被禁用。

新脚本以TOML表覆盖禁用各插件，并设置本进程 `features.apps=false`。`config/read`核验后检查全部 inventory 页。无thread的inventory对禁用条目返回null状态，必须同时验证其配置为disabled且工具为空；thread范围则必须为明确disabled且工具为空。意外插件、starting/connected服务、仍暴露工具的disabled条目均拒绝。

实际调用使用 `ephemeral=true` 且返回 `path=null` 的临时协议上下文，没有模型回合或持久任务。它只用于让安装的客户端调度明确指定的工具，**不证明模型自行选择工具或理解自然语言任务**。

## 复现与验证

```sh
uv run python -m experiments.codex_business --output data/codex-business-new.json
uv run pytest -q tests/test_codex_probe.py tests/test_oauth.py tests/test_mcp.py
```

要求新输出文件，原证据不覆盖。临时授权、客户端凭据、HTTP服务和数据库均清理；输出仅保留检查名、状态计数和源码哈希。未将动态端口、OAuth码/令牌、Feed地址或用户配置写入证据。

最终25项针对性pytest、ruff通过，两个既有Starlette/AnyIO弃用警告。公开入口的旧探测命令也在修正后重新运行通过，见 [隔离发现结果](codex-discovery-isolated-2026-09-10.json)。无业务代码、契约、迁移、依赖或UI变化，未重复完整构建或Core Tools测试。

真实Firebase身份加MCP、HTTPS/Azure、自然过期、真实YouTube创作者和设备日历仍待验收。该实验不联网访问视频，`LocalVideo1`只是合成URL。T28/T29继续in_progress，没有将本机结果等同部署或最终验收。

协议方法以本机生成的实验性JSON Schema核对；配置依据：[OpenAI配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)，协议说明：[Codex App Server](https://learn.chatgpt.com/docs/app-server)。
