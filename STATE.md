# Anke Sports · 服务端状态

更新：2026-09-15。以[工作区STATE](../STATE.md)及[实施计划](<../anke-sports 文档/Anke_Sports_实施计划.md>)为当前入口。

官方转播地区矩阵已部署到 Azure dev：覆盖美国、中国大陆、日本及 15 个欧洲国家代码，按 F1、NBA、英超分别约束可发布版权平台。`official_match` 发布会拒绝赛事或地区不匹配的平台；平台主页和仅说明转播安排的页面只能标为播出信息。公开元数据现在返回平台名及移动端打开方式。Peacock、U-NEXT、DAZN、Viaplay、Prime Video 等只有在 URL 路径命中已验证 App Link 范围时才提示手机尝试打开 App；FOD 当前标为网页交接。详见[地区与移动端规则](docs/official-broadcast-regions.md)及[部署证据](evidence/official-broadcast-deployment-2026-09-15.md)。

成本优先的 YouTube AI 匹配已发布到 Azure dev：使用稳定版 `gemini-3.1-flash-lite`，通过 Gemini 官方 `generateContent` 与结构化 JSON 输出调用。当前本地待审 v4 改为逐一输出每个 `视频 × 比赛` 的独立相关性置信度：`≥0.90` 自动挂入、`0.55–0.899` 进入备选、`<0.55` 排除，不再比较第一/第二候选或计算差值；同一视频可关联多场，每场自动视频数量不限。AI 同时输出最多 3 个 emoji 开头的中文内容标签，Web 与个人 ICS 均展示标签和原始 YouTube URL。输入已加入一页最多 12 条公开顶层评论，评论按 relevance 读取、限长去重且不含作者信息；评论关闭使用空样本，临时失败保留旧样本并继续处理视频。模型失败或输出无效会中止写入并重试，旧日历不变。视频画面、音频和字幕尚未接入。规则见[AI 视频自动匹配](docs/ai-video-matching.md)。

后端 HEAD `406b800`，本轮开始时工作树干净；AI v4 变更尚未提交或 push。当前运行包以已部署 Gemini v3 包 `08394b…0816` 为基线，仅替换 5 个转播模块，SHA-256 `485f306f…8934e`；OneDeploy `c3b4f77d-9c34-4a05-8d91-2a23986e6b43` 远程构建成功，六函数和双入口健康/平台矩阵回读通过。

已部署：三Provider、季前赛显式抓取、Spurs消歧、Logo兼容、source_id过滤、直接关注规则、客队排除、个人Feed取消关注隐藏，以及P2账号/OAuth/转播文档服务。公共Feed保留停用且白名单为空，不进入v1验收；公开赛程与匿名MCP查询仍在范围。

当前本地 v4 验证为 396 passed / 2 skipped、评论与 AI 定向 88 项、Ruff、Bicep ARM 编译和 SQLite 全迁移至 `78a26d8eb91f` 通过；用例确认同一视频对两场比赛分别为 `0.93`/`0.92` 时两场都会自动挂入，且无 margin reason code，并覆盖评论限量去重、评论关闭、临时失败保留旧样本及 SQL/document 双路径。已部署的 v3 此前完成 OneDeploy active/complete、六函数和双入口健康/status 200/no-store、Key Vault 引用 Resolved，并用真实结构化调用返回自动挂入、2 个标签和置信度代码；这些部署证据不代表 v4 已发布。密钥不在 shell、源码或文档中，临时 Vault 管理权限已移除。见[Gemini 部署证据](evidence/gemini-video-matching-deployment-2026-09-15.md)。

剩余：逐场添加并审核真实官方内容页、手机 App 内准确内容/播放回读、个人ICS与设备更新回读、真实季前赛/视频覆盖、独立测试账号生命周期与Queue路径、公网MCP授权/查询/写入/到期/撤销、最小恢复和正式发布。旧调度时间已过去，不能以预定时间推断Feed已重建。SQL仍为本地基线，真实云验收分别留证。

本次 v4 评论输入与逐候选评分实现仍未部署、提交或 push；Azure dev 保留 v3 AI 运行文件和 3 个 Gemini dev 设置，只增加上述转播模块。未触发真实 Provider/视频任务，也未创建、发布或修改任何具体场次的转播记录。
