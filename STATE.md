# Anke Sports · 服务端状态

更新：2026-09-15。以[工作区STATE](../STATE.md)及[实施计划](<../anke-sports 文档/Anke_Sports_实施计划.md>)为当前入口。

官方转播地区矩阵与个人偏好已部署到 Azure dev：覆盖美国、中国大陆、日本及 15 个欧洲国家代码，按 F1、NBA、英超分别约束可发布版权平台。用户偏好以“地区 + 联赛”保存；多个版权方按偏好排序，个人日历每场只交付一个直播入口。`official_match` 发布会拒绝赛事或地区不匹配的平台。Peacock、U-NEXT、DAZN、Viaplay、Prime Video 等只有在 URL 路径命中已验证 App Link 范围时才提示手机尝试打开 App；FOD 当前标为网页交接。详见[地区与移动端规则](docs/official-broadcast-regions.md)及[整合发布证据](evidence/integrated-release-2026-09-15.md)。

成本优先的 YouTube AI v4 匹配已发布到 Azure dev：使用稳定版 `gemini-3.1-flash-lite`，逐一输出每个 `视频 × 比赛` 的独立相关性置信度：`≥0.90` 自动挂入、`0.55–0.899` 进入备选、`<0.55` 排除；同一视频可关联多场，每场自动视频数量不限。AI 同时输出最多 3 个 emoji 开头的中文内容标签。输入包含一页最多 12 条公开顶层评论，评论按 relevance 读取、限长去重且不含作者信息；评论关闭使用空样本，临时失败保留旧样本并继续处理视频。视频画面、音频和字幕尚未接入。规则见[AI 视频自动匹配](docs/ai-video-matching.md)。

后端提交 `1abd2aa` 已整合发布；当前运行包 SHA-256 `ef1e6543…9bc0`，OneDeploy `6a641c89-c1ed-41bf-8011-a5a9d0a82e87` 远程构建成功，六函数和双入口健康/平台矩阵回读通过。完整回归为 `398 passed / 2 skipped`。未 push。

已部署：三Provider、季前赛显式抓取、Spurs消歧、Logo兼容、source_id过滤、直接关注规则、客队排除、个人Feed取消关注隐藏，以及P2账号/OAuth/转播文档服务。公共Feed保留停用且白名单为空，不进入v1验收；公开赛程与匿名MCP查询仍在范围。

当前本地 v4 验证为 396 passed / 2 skipped、评论与 AI 定向 88 项、Ruff、Bicep ARM 编译和 SQLite 全迁移至 `78a26d8eb91f` 通过；用例确认同一视频对两场比赛分别为 `0.93`/`0.92` 时两场都会自动挂入，且无 margin reason code，并覆盖评论限量去重、评论关闭、临时失败保留旧样本及 SQL/document 双路径。已部署的 v3 此前完成 OneDeploy active/complete、六函数和双入口健康/status 200/no-store、Key Vault 引用 Resolved，并用真实结构化调用返回自动挂入、2 个标签和置信度代码；这些部署证据不代表 v4 已发布。密钥不在 shell、源码或文档中，临时 Vault 管理权限已移除。见[Gemini 部署证据](evidence/gemini-video-matching-deployment-2026-09-15.md)。

剩余：逐场添加并审核真实官方内容页、手机 App 内准确内容/播放回读、个人ICS与设备更新回读、真实季前赛/视频覆盖、独立测试账号生命周期与Queue路径、公网MCP授权/查询/写入/到期/撤销、最小恢复和正式发布。旧调度时间已过去，不能以预定时间推断Feed已重建。SQL仍为本地基线，真实云验收分别留证。

本次未触发真实 Provider/视频任务，也未创建、发布或修改任何具体场次的转播记录；真实视频准确率和手机转播播放仍待验收。
