# 账号删除与隐私边界

`DELETE /api/v1/me` 需要当前账号验证与 `confirmed: true`。Chrome/MCP 的下游访问令牌不能调用此端点。本地共享体验账号的网页按钮禁用；独立删除夹具使用临时库验收真实路由，不操作主体验账号。

## 事务内清理

`app/privacy.py` 先锁定并重新读取用户，与 HTTP/MCP 写入、授权码交换、刷新令牌、Feed 发布、个人任务入队使用同一账号锁顺序。清空个人配置与显示名，增加版本并保留 deleted 标记；删除私人链接、匹配、投影、会话、应用授权及令牌、命令回执、个人 outbox 和对应重放记录。Feed 撤销并暂停，清空正文、加密凭据和 ETag。事务失败会回滚，不返回删除成功。

保留最小撤销记录：不可读的账号 ID、deleted/版本/创建时间；Feed ID、owner ID、token hash、版本和时间等撤销字段。它们用于拒绝旧身份与旧订阅、避免账号复活，并非所有数据库行物理消失。其他用户及共享公开赛程、频道/视频元数据不随一个账号删除；公开元数据继续遵守独立的到期策略。

配置 CAS 包含 deleted=false；共享业务命令在幂等回执重放前重新验证账号。已排队的频道抓取、视频和作者资料刷新在联网前检查是否仍有关注者或保留链接的所有者。最后一个所有者消失则跳过。已经开始的外部请求可能完成，但不能在删除后重建个人匹配、投影或个人任务。网络阶段不持有跨任务的用户写锁，否则会阻止租约恢复。MySQL 并发隔离行为仍需真实实例验证。

## Firebase 清理和重试

生产 Firebase 身份通过服务端验证后，删除事务写入 `identity_cleanup` outbox，固定当时的独立项目 ID。Worker 使用绑定同一项目的命名 Admin app 调用 `auth.delete_user(uid, app=app)`；Firebase 已没有该用户时按幂等成功处理。此 API 的服务端权限及删除方法参见 [Firebase Admin 用户管理](https://firebase.google.com/docs/auth/admin/manage-users#delete_a_user)。

删除响应的 `identity_cleanup: queued` 只表示已排队。账号立即被 Anke Sports 的撤销记录拒绝，外部身份可能仍在重试。Worker 的项目配置变化时拒绝执行，不能把队列中的 UID 发往另一个项目。普通失败遵循现有重试/失败机制；修正原项目配置后按 [任务恢复手册](job-recovery.md) dry-run、重放。只有 `identity_cleanup` 可以为已删除账号重放，其他个人任务仍拒绝。已完成的清理任务和最小撤销记录目前保留在数据库，需要部署时纳入备份保留与恢复规程。

独立真实项目 `anke-sports-dev` 已完成专用临时身份的撤销、刷新和删除验收：真实 HTTP 删除写入 outbox，另一进程执行实际 Firebase Admin 清理，读回远端身份不存在；旧 ID/refresh、私人 Feed 与下游授权均被拒绝。27 项检查和临时数据清理通过，见 [真实身份生命周期证据](../evidence/firebase-lifecycle-2026-09-10.md)。项目不符、故障与重放的测试另有合成适配器覆盖。

本次使用新建的无邮箱、无 Google 提供商绑定的测试身份和临时 SQLite；没有删除用户已有的 Google 账号。Google 浏览器重新认证/删除、多设备缓存、Cosmos 分区事务、Azure Queue 与云清理告警仍未验收。

## 浏览器和外部缓存

后端删除本地会话 cookie。前端在服务端成功后退出 Firebase SDK，完整导航到游客日历以丢弃已挂载的个人界面。SDK 退出失败会单独提示，不会把已完成的数据删除误报为失败。账号查询忽略较早返回的请求；401/ACCOUNT_DELETED 清空身份，回到窗口时重新读取。账号变化会清除抽屉、关注/导入草稿和个人偏好。

服务器不能收回已经发送的响应、浏览器离线副本或系统日历缓存。页面明确提示在系统日历里移除旧订阅；真实设备缓存清除不是本地测试的通过项。备份恢复后也必须保留或重放删除决定，不能直接用较旧备份恢复已删除账号；正式备份保留和恢复防复活尚待部署环境验收。

HTTP 未预期异常只记录不透明请求 ID 和错误类别，响应返回统一 503；SQLAlchemy 隐藏参数。访问日志继续关闭。合成私人 Feed URL 出现在异常内容时，不得出现在 HTTP 正文或应用日志。宿主/代理日志仍需部署时验证。

## 复现

```sh
uv run pytest -q tests/test_account_deletion.py
uv run python -m experiments.privacy_ui
```

网页夹具需客户端 production server 在 3002，浏览器访问 `http://[::1]:3004/fixture/login`。IPv6 主机隔离主体验 cookie。仅夹具的 status 响应关闭共享体验 UI 保护，并以“隔离删除验收（合成账号）”标识；身份仍为本地适配器，无 Firebase 请求。退出进程后临时库清理。证据见 [删除验收](../evidence/privacy-2026-09-10.md)。

独立真实身份实验（会创建并清理本项目专用测试账号）：

```sh
uv run python -m experiments.firebase_lifecycle \
  --service-account data/firebase-dev-service-account.json \
  --web-config data/firebase-dev-web.json \
  --output data/firebase-lifecycle-new-run.json
```

两个配置必须属于 `anke-sports-dev`，为权限 0600 的普通文件。脚本只接受新输出路径，在创建远端身份前建立排他清理记录；拒绝已有用户、提供商绑定或所有权标记不符的清理对象，异常仅输出安全错误类别。中断后先检查对应 `*.cleanup.json` 的项目/UID/标记并读回 Firebase，不要重复创建或对其他账号执行清理。
