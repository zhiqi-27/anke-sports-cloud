# 真实 Firebase 测试身份生命周期

2026-09-10，独立项目 `anke-sports-dev`。最终 [机器记录](firebase-lifecycle-2026-09-10.json) 为 27 项通过，身份和临时 API/SQLite 清理均读回成功。测试使用新生成、无邮箱/电话/Google 提供商绑定的专用 UID；没有操作已有个人账号。

## 实际链路

Firebase Admin 创建测试身份 → Custom Token 经官方 REST 交换真实 ID/refresh → 禁用本地体验的 loopback HTTP API 验签和配置/个人链接写入 → 已发布 ICS。真实 revoke 后旧 ID 和 refresh 被拒绝，重新认证后账号可用。

删除使用实际确认端点，提交后用户撤销标记立即生效；worker 尚未运行时 Firebase 身份仍存在，但个人 API、私人 Feed、下游 access/refresh 已被拒绝。另一进程领取精确 `identity_cleanup` 任务，实际调用 Admin 删除；再从 Firebase 读回身份不存在、旧 refresh 返回 `USER_NOT_FOUND`。完成任务不可再次领取，远端已不存在时重复清理幂等。

临时 SQL 中个人配置/链接/授权已清除，另一个合成所有者及共享事件保留。公开比赛和附链为明确的实验数据，不是真实体育内容验收。

## 实验修正与清理

首轮在新身份创建后发生实验脚本 `KeyError`：误认为 `signInWithCustomToken` 响应必有 `localId`。改为验证返回 ID token 的签名和 UID；没有修改应用逻辑或放宽断言。首轮 finally 已删除远端身份，随后独立 Admin 读回确认不存在并移除清理记录；临时目录数量为零。

最终脚本的源文件 SHA256 随 JSON 保存。新增 12 项本地安全边界测试与原有 13 项账号删除测试合计 **25 passed**；ruff 通过，存在两项已有 Starlette/AnyIO 弃用警告。pytest 不连接 Firebase。未重复整个后端套件或客户端构建。

凭据只从项目专用、Git 忽略的 0600 文件读取，tokens 不进入输出/证据。清理仅匹配生成 UID 和该轮 display marker，拒绝提供商绑定或所属关系改变的账号。复现见 [账号删除说明](../docs/account-deletion.md)。

## 尚未证明

Google 浏览器重新认证/账号删除、多设备离线缓存、实际 Chrome/Codex 客户端与 Firebase 的组合、Azure Queue/Cosmos、宿主日志脱敏和恢复防账号复活。此处的下游授权是实际 HTTP consent/PKCE，未启动浏览器或 Codex。

接口依据：[Firebase Admin 用户管理](https://firebase.google.com/docs/auth/admin/manage-users)、[会话管理](https://firebase.google.com/docs/auth/admin/manage-sessions)、[REST 身份接口](https://firebase.google.com/docs/reference/rest/auth)。
