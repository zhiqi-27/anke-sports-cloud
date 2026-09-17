# Anke Sports · 服务端状态

> 2026-09-17范围覆盖：不做微信、YouTube视频/AI匹配；新增手动单场增删，关注自动比赛不可单删，MCP同等支持。无需兼容1.0前版本。A 范围清理与源模型/生成契约已在本地候选版本完成；B/C 行为与 MCP 工具尚未实现或验收。见[1.0定义](<../anke-sports 文档/Anke_Sports_1.0定义.md>)。

更新：2026-09-17。本轮从HEAD `8a26fba`上的既有未提交工作继续修改；未push、未部署。以[工作区STATE](../STATE.md)及[实施计划](<../anke-sports 文档/Anke_Sports_实施计划.md>)为当前入口。

本地候选版本已整体停用视频产品能力：删除创作者、待确认、YouTube webhook/维护等公开接口，移除内容Timer与MCP创作者工具，旧 `selection` 单场排除入口也不存在；新建链接仅接受`live`和`watch_along`。旧视频任务不再进入 active claim，不静默完成；历史视频行、表和模块保留为追溯材料，当前运行时不加载，也未做破坏性迁移。`Config.manual_events`、私有事件 `calendar.sources/can_remove` 已进入服务端 schema、OpenAPI 和客户端生成类型；手动增删行为仍待 B/C。线上开发环境仍运行上一版，本轮未部署。

F1关注代码已发布为只能选择具体车队：Jolpica当前赛季Constructors形成车队目录，全部车队同时写入每个大奖赛/session的participants，因此不同车队命中相同赛程，车队ID用于个人关注与后续官方频道内容范围。赛事/联赛均不能直接关注。适配器真实接口读回为11支车队、115个session事件且每场包含11个车队；云端快照仍是02:30:26 UTC旧数据，首次正常刷新在08:30:26 UTC后才具备资格，因此当前公网目录仍为0支F1车队。未迁移旧F1整赛关注或验证个人Feed。

官方转播地区矩阵与个人偏好已部署到 Azure dev：覆盖美国、中国大陆、日本及 15 个欧洲国家代码，按 F1、NBA、英超分别约束可发布版权平台。用户偏好以“地区 + 联赛”保存；多个版权方按偏好排序，个人日历每场只交付一个直播入口。`official_match` 发布会拒绝赛事或地区不匹配的平台。Peacock、U-NEXT、DAZN、Viaplay、Prime Video 等只有在 URL 路径命中已验证 App Link 范围时才提示手机尝试打开 App；FOD 当前标为网页交接。详见[地区与移动端规则](docs/official-broadcast-regions.md)及[整合发布证据](evidence/integrated-release-2026-09-15.md)。

此前的YouTube搜索、评论读取、AI评分、自动挂入和备选流程已经退出当前产品范围。相关模块、表和证据仅作历史追溯，不是当前兼容前置，也不再由HTTP、Timer、Queue或MCP入口触发。具体边界见[退出能力记录](docs/retired-capabilities.md)。

线上运行包仍为 SHA-256 `bbe0ef48…6d73e`，含六个Functions。本地候选版本移除内容Timer；本轮尚未部署或push，不能用线上健康回读证明本地改动。

已部署：三Provider、季前赛显式抓取、Spurs消歧、Logo兼容、source_id过滤、直接关注规则、客队排除、个人Feed取消关注隐藏，以及P2账号/OAuth/转播文档服务。公共Feed保留停用且白名单为空，不进入v1验收；公开赛程与匿名MCP查询仍在范围。

剩余：后续实现 B/C 的手动单场增删与 MCP 对等工具，再做 D 全链路验收。若后续获准部署，验证 Functions 数量、旧视频 API 404/422、个人 ICS 中旧视频不可见以及直播链接仍可用。独立测试账号生命周期、Queue路径、公网MCP授权/查询/写入/到期/撤销、最小恢复和正式发布仍待完成。SQL仍为本地基线，真实云验收分别留证。

本次未触发真实Provider或内容任务，也未修改任何具体场次的转播记录；未删除历史表、历史文件或已有未提交工作。
