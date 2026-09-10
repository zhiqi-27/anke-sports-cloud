# YouTube 独立开发接入

2026-09-10：独立项目专用 Key 已安全保存到本机，下载和 Cloud Shell 的临时明文已清理。本机隔离 API/worker 已读取 69 条真实视频；198 条匹配候选中 183 条待审核、15 条拒绝，未自动附链。WebSub 与真实视频到日历的完整链路仍待验收。

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

以上是最初 Cloud Shell 证明的范围。测试频道名称与句柄不构成本产品的“官方来源”审核；视频未因此被认定为某场比赛的前瞻或复盘。该次脱敏结果见 [验收记录](../evidence/youtube-cloud-api-2026-09-10.md) 和 [JSON](../evidence/youtube-cloud-api-2026-09-10.json)。

## 本机安全保存与应用验证

用户解锁 Mac 后，已找到精确下载的专用 JSON，校验项目、资源名称与字节一致，并排他写入 Git 忽略的 `data/youtube-dev.json`（0600）。下载原件及 Cloud Shell `/home/z24develop/anke-sports-youtube-dev.json` 已删除；此前错误表单产生的 `z24develop.zip` 已按精确路径清理，没有读取或解包。云端专用 Key 保留。不要再次创建 Key，也不要通过聊天、终端输出或截图传递 `api_key`。

`experiments.youtube_live` 以真实 Provider 和后台处理器验证频道解析、添加、幂等重试、uploads 补查、视频详情、频道资料、重新匹配和项目预算。只复制主库已缓存的 115 条公共 Jolpica 场次到临时 SQLite，不重新抓取赛程，不读写已有个人配置；账号明确为本地验收身份。API 与独立 worker 进程共用该实验的持久预算。

两轮均通过 11 项检查，各预留 8 个 API 配额单位。第二轮补充实际匹配原因：69 条视频、198 条候选，183 条 `needs_review`、15 条 `reject`、0 条个人链接；85 个窗口内 ICS 事件保持 UID，空闲重复执行无额外 API 调用，条件读取 304。主要原因包括内容类型未知、场次不明确、日期冲突；没有放宽规则来制造自动匹配通过。匹配数是视频/比赛组合，不是 198 条视频；人工标注数为 0。

已知实验用量为 Cloud Shell 3 + 本机两轮各 8 = 19 个估算/预留单位。两轮临时账本彼此独立，不是项目全局累计或 Google 剩余额度；之后持续运行的 API/worker 必须统一接入同一项目预算存储。主预览和 Firebase 验收实例本轮未注入 Key、未自动添加创作者，不能据此声称用户当前页面已持续发现真实视频。

复现一次有界实验（读取真实 API，会消耗配额；只接受新输出路径）：

```sh
uv run python -m experiments.youtube_live \
  --key-file data/youtube-dev.json \
  --public-schedule-db data/anke-sports.db \
  --output data/youtube-live-new-run.json
```

每次实验临时上限 20 单位，Key 仅在进程内及子进程环境传入，不进入命令行。两次临时 API/数据库均已清理。完整证据见 [本机真实发现](../evidence/youtube-live-2026-09-10.md)。

WebSub 需要独立公开可达的 HTTPS 回调，再单独验收 Hub 挑战、签名、通知、续订和补查。Cloud Shell 的登录保护预览不能替代公开回调。还需真实人工标注、确认/屏蔽到 ICS、手机播放和 Azure/Cosmos 验收。

应用已有的配额和恢复规则见 [项目预算](youtube-budget.md)，云身份和部署现状见 [开发云环境](cloud-development.md)。本批新增实验与证据，未修改运行中的应用环境、原业务数据库或 Azure 资源。
