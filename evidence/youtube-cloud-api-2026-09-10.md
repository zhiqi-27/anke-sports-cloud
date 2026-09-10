# YouTube 专用 Key 与真实公共读取

日期：2026-09-10。证据来自已授权 Chrome 内 Firebase 项目的 Cloud Shell 终端脱敏输出；不是本机后端测试，也没有将完整上游响应或密钥存入 Git。

## 配置结果

- 核对当前项目 `anke-sports-dev` / `736683203171`，状态 ACTIVE；Firebase UI 仍为 Spark / No-cost。
- 启用 YouTube Data API 和 API Keys API，创建独立 `anke-sports-youtube-dev` Key；读回唯一 API target 为 `youtube.googleapis.com`。
- 未改已有 Firebase Browser key、服务账号/IAM、计费计划或任何其他产品资源。
- 专用密钥只保存在 Cloud Shell 的 0600 文件；本机目标与 Downloads 中的同名文件均未找到。Key 值未出现在模型/终端输出中。

## 三次真实只读请求

| 方法 | 输入范围 | 结果 |
| --- | --- | --- |
| channels.list | `@Formula1`，snippet/contentDetails | 200；频道与 uploads ID 可读 |
| playlistItems.list | 返回的 uploads ID，maxResults=3 | 200；三条上传记录 |
| videos.list | 第一条视频 ID，snippet/status | 200；public，channelId 与频道一致 |

调用时间为 `2026-09-10T01:43:12.115074+00:00`；精简结果和输入保存在 [JSON](youtube-cloud-api-2026-09-10.json)。三种 list 方法官方成本各 1 单位，估算共 3；只记录此次实验，没有读取 Google 项目剩余额度，未写入应用 SQL 预算。官方方法资料链接位于 [接入说明](../docs/youtube-development.md)。没有搜索、上传或对频道/视频执行写入。

## 下载与恢复状态

两次 Download 表单虽然输入了 JSON 路径，传输表却显示 `/home/z24develop`；原因未证实。随后官方 `cloudshell download` 命令的确认窗口和传输表显示正确 JSON 路径。三项状态均为 Success，但两次浏览器 download 事件等待超时，本机文件名检查没有发现对应 JSON 或 home 归档。不能把该 UI 状态等同本机下载完成。

Mac 处于锁定状态，用户回答暂时无法解锁，已停止原生窗口检查。后续仅完成专用文件的安全保存；如果本次错误尝试产生的 home 归档落盘，应定位后删除，不解包查看。不要重复创建 Key。Cloud Shell 曾断线，使用 Reconnect 后恢复，三次 API 请求是在恢复后实际执行。

Cloud Console 独立标签曾出现 `ERR_CERT_COMMON_NAME_INVALID`，没有绕过证书页；配置使用正常 TLS 的 Firebase 控制台内 Cloud Shell。真实 Codex 内置浏览器登录问题按用户要求没有恢复排查。

## 本机与验收边界

本轮只新增证据与说明，未改业务源码、依赖、数据库、启动配置或重启服务。8787/8788 健康检查与 3000/3002/3003 日历页均返回 200；这仅证明原入口可达。未重复运行无代码改动对应的全量测试或构建。

未验收：本机/应用真实 YouTube 连接、API/worker 共用预算、WebSub 通知与长期续订、真实匹配质量、真实视频进入 Feed、手机日历刷新、App 内容直达/播放、Azure。未进行付费 Azure 创建、部署或 Git 推送。T04/T14/T15 保持 in_progress。
