# 文档 YouTube 请求与频道解析验收

2026-09-10。完整产品目标继续；本批只完成新存储的请求额度与频道解析，并保留SQL原有完整内容流程。用户Mac仍按锁定约束处理，没有浏览器、解锁或交互登录操作。

## 已验证

- 首轮相关测试66项通过。最终完整回归 **327 passed / 2 skipped**，16.01秒；两项条件MySQL跳过、两条既有Starlette/AnyIO弃用警告。新增文档YouTube测试25项。Ruff和完整OpenAPI/config schema一致性通过。
- 12个并发请求使用独立持久文档连接，内部额度设为6，恰好6次请求发至模拟上游；更换Key后仍不能继续。新进程读回相同计数，无SQL运行时导入。预留写入indexes，不产生state/outbox变更。
- 模拟真实本地事务失败、提交成功但响应丢失：没有未预留的HTTP发送；保守预留保留。跨太平洋日、夏令时、减额/增额、时钟倒退、共享限流和旧日迟到响应边界通过。故障为合成注入，不是Azure/Google真实故障。
- 实际ASGI路由验证游客401、错误Origin403、已登录解析成功、个人账号/配置不变；未知、错误频道ID、不完整资料和非公开视频拒绝。DEBUG日志捕获不含合成Key或原始上游错误正文。

## 真实 YouTube 读取

`experiments.document_youtube_live`使用既有Anke Sports专用Key，实际通过 `X-Goog-Api-Key` 请求头读取频道。频道ID和 `@Formula1` 均解析到 `UCB_qr75-ydFVKSF9Dmo6izg`；两次已预留请求、重新打开账本一致、没有业务state写入、SQL模块未导入，共6项检查通过。

本实验内部上限4、预留2，专用 `data/document-youtube-live.db`（0600）保留供重跑计数；不代表Google总额度或剩余额度，也不合并早期实验请求。源文件摘要在实验前后核对一致，见 [机器证据](document-youtube-2026-09-10.json)。专用Key没有输出、复制到证据或写入Git。

## 保留的边界

真实外部读取验证的是共享transport与文档额度服务；本地HTTP行为由ASGI测试验证。没有真实Firebase登录、Cosmos数据面、Azure部署、Hub、自动视频到ICS、手机或浏览器渲染证据。新存储中的创作者保存/发现/匹配仍未迁移，不能把频道解析或额度configured状态当作持续后台更新已启用。

3007 F1预览继续保留上一批Provider版本，本批未重启任何常驻API/worker；没有迁移主数据库或更改已有Firebase/YouTube配置。客户端只同步说明和状态，业务源码、依赖、契约未变，没有重复构建。两仓分别本地提交，无push或部署。
