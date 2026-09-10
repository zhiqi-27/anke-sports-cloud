# YouTube 独立开发接入

2026-09-10：独立项目的专用 Key 已创建，Cloud Shell 中三次真实 Data API 读取通过。本机尚未收到密钥文件，应用 API/worker、推送回调和日历链路仍待真实联调。

## 已确认的配置

| 项目 | 已读回结果 |
| --- | --- |
| Google/Firebase 项目 | `anke-sports-dev`，项目编号 `736683203171` |
| Firebase 计划 | Spark，未升级计费 |
| 启用服务 | `youtube.googleapis.com`、`apikeys.googleapis.com` |
| 专用 Key ID | `anke-sports-youtube-dev` |
| Key 显示名称 | Anke Sports YouTube Dev |
| Key 资源 | `projects/736683203171/locations/global/keys/anke-sports-youtube-dev` |
| API 限制 | 仅 `youtube.googleapis.com` |
| IP 限制 | 尚未设置；本机和未来 Azure 出口尚未固定 |

没有修改或复用 Firebase 自动创建的 Browser key，没有为 YouTube 新建服务账号或授予 IAM 角色。创建命令与返回的 Key 值在 Cloud Shell Python 进程内部处理；终端只打印资源名称、限制和文件权限，不打印密钥。已有专用 Key 不应重复创建。

## 实际验证

Cloud Shell 在 `2026-09-10T01:43:12Z` 开始执行一次有界只读实验：

1. `channels.list`：`forHandle=@Formula1`，返回频道 `UCB_qr75-ydFVKSF9Dmo6izg` 和 uploads 列表。
2. `playlistItems.list`：读取该 uploads 列表的前三条，实际返回三条。
3. `videos.list`：读取第一条视频，返回公开状态；视频的 `channelId` 与第一步频道一致。

三次请求均为 HTTP 200。按当前官方文档，每种读取各耗 1 个配额单位，合计估算 3；这不是 Google 配额控制台的实际余额。此次配置实验不经过应用的 SQL 预算账本，应作为额外已知用量记录。没有调用搜索、上传、修改或订阅用户频道接口。来源：[channels.list](https://developers.google.com/youtube/v3/docs/channels/list)、[playlistItems.list](https://developers.google.com/youtube/v3/docs/playlistItems/list)、[videos.list](https://developers.google.com/youtube/v3/docs/videos/list)。

这只证明专用 Key 和公共元数据 API 可用。测试频道名称与句柄不构成本产品的“官方来源”审核；视频未被认定为某场比赛的前瞻或复盘。未测试本机网络、应用频道解析/补查/预算、WebSub 通知及续订、匹配质量、HTTP ICS 或手机播放。脱敏实测结果见 [验收记录](../evidence/youtube-cloud-api-2026-09-10.md) 和 [JSON](../evidence/youtube-cloud-api-2026-09-10.json)。

## 恢复本机接入

密钥临时文件位于本项目 Cloud Shell 的 `/home/z24develop/anke-sports-youtube-dev.json`，创建时权限 0600，字段为 `project_id`、`key_resource`、`api_key`。本机目标是 Git 忽略的 `data/youtube-dev.json`，当前不存在。不要通过聊天、终端输出或截图传递 `api_key`。

用户目前无法解锁 Mac，因此暂停原生下载窗口检查。Cloud Shell 传输表显示成功不能证明文件已经落到本机；后续恢复流程是：

1. 检查当前浏览器下载/保存窗口，只完成专用 JSON 的下载。已执行的精确命令为 `cloudshell download /home/z24develop/anke-sports-youtube-dev.json`；不要盲目重复提交或下载整个 home 目录。官方支持该命令，见 [Cloud Shell 文件传输](https://docs.cloud.google.com/shell/docs/uploading-and-downloading-files)。
2. 本机仅解析对应文件，校验上表中的项目与完整资源名称；以排他创建方式安全写入 `data/youtube-dev.json`，权限 0600。若目标已存在，先核对而不是覆盖。
3. 确认字节一致及 Git 忽略规则后，删除本次下载原件和 Cloud Shell 临时密钥 JSON。清理文件不等于撤销云端 Key。检查并清理本次两次错误表单尝试产生的 home 归档（如有），不要解包或读取其他文件。
4. 在独立验收实例中从文件读取 `api_key`，只通过子进程环境传入 `YOUTUBE_API_KEY`；设置 `ANKE_SPORTS_YOUTUBE_PROJECT_ID=anke-sports-dev`。不把 Key 放入命令行参数、客户端、导出或日志。API 和 worker 必须共用同一持久预算数据库。
5. 使用应用本身的频道解析、添加、后台补查和发布路径验证结果与预算计数。保留主预览、现有关注和真实 Firebase 身份库，不自动导入测试视频。缺 Key 状态继续如实显示。
6. WebSub 需要独立公开可达的 HTTPS 回调，再单独验收 Hub 挑战、签名、通知、续订和补查。Cloud Shell 的登录保护预览不能替代公开回调，Azure 或其他收费资源尚未获创建批准。

应用已有的配额和恢复规则见 [项目预算](youtube-budget.md)，云身份和部署现状见 [开发云环境](cloud-development.md)。本次未修改应用源码、环境、业务数据库或 Azure 资源。
