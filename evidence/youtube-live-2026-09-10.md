# 本机 API/worker 真实 YouTube 发现

2026-09-10，独立 `anke-sports-dev` 专用 Key。两轮实验均 **11 项通过**；最终 [机器记录](youtube-live-2026-09-10.json) 包含实际匹配决策和源文件 SHA256。

## 结果和范围

真实 HTTP 解析 `@Formula1`、添加创作者、重复幂等提交后无新增配额；另一个进程使用实际后台处理器完成 uploads、视频和频道资料更新、重新匹配。使用本机临时 SQLite 与显式本地测试身份，仅读取原库公共 Jolpica 的 115 条缓存场次，没有重新向体育 Provider 抓取或改动原个人配置。

| 实际观测 | 结果 |
| --- | --- |
| 公开视频元数据 | 69 条，均 available |
| 视频/比赛候选组合 | 198 条；183 needs_review、15 reject |
| 自动/个人附链 | 0 |
| 个人 Feed | 85 个窗口内事件，UID 保持；0 个新增 YouTube 链接 |
| 每轮预算账本 | 预留 8 单位，实验上限 20，API 与 fresh worker 共用 |
| 空闲重复执行 | 无额外 Google 调用，Feed 条件请求 304 |
| 清理 | 两轮临时 API/数据库均移除 |

待审主要原因是内容类型未知、场次不明确和无明确日期，拒绝含日期冲突；完整计数可重叠。同一视频可以对应多场候选，198 不是视频数量。“Drivers React After The Race”等标题没有自动被当作规则所需的明确复盘；本次没有修改匹配词表。人工标注数为 0，不证明精确率/召回率目标，也不证明内容关联判断正确。

首轮已观察 69 条视频和 0 链接；第二轮补充决策/原因明细并将稳定 UID 检查名称改为“发现后保持身份”，避免暗示已经自动附入内容。第二轮 JSON 的 `real_video_to_ics_verified` 明确为 false。

Cloud Shell 先前三次读取估算 3 单位，本机两轮各预留 8，已知合计 19。各实验账本独立，不能当 Google 项目余额或持续服务的全局预算。主预览/Firebase 实例本轮没有注入 Key 或新增创作者；持续启用前必须让 API/worker 共用同一项目预算存储。

## 凭据与待验收

用户解锁后，专用 JSON 已按精确项目/资源校验，排他存入 Git 忽略 `data/youtube-dev.json`，权限 0600；下载原件和 Cloud Shell 临时明文已删除。错误表单产生的精确 home 归档已清理，未读取/解包；专用云 Key 保留，没有改动 Firebase Browser key 或 IAM。

WebSub 公网 HTTPS 通知/续订、长期补查、真实人工审核到 ICS、真实 Firebase 身份组合、Cosmos/云调度、手机播放均未验收。此次是实际元数据发现与本机预算证明，不是视频到手机日历的完整验收。复现和资源说明见 [YouTube 接入](../docs/youtube-development.md)。
