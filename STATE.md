# Anke Sports · 服务端状态

更新：2026-09-13。完整当前结论以[工作区 STATE](../STATE.md)为准，[整体计划](<../anke-sports 文档/Anke_Sports_实施计划.md>)规定后续顺序。

独立Azure开发环境已部署三Provider，Cosmos个人Feed已交付Mac。F1有自动视频到手机样本，英超为人工附链到Mac；NBA视频待内容。公共Feed、官方直播、OAuth/MCP、账号删除等文档模式适配仍有缺口；SQL为本地基线。临时Vault写入角色已撤销，不重做密钥配置。

## 验收边界

最后一次云端证据中的个人配置为F1、马刺、利物浦，共132场；用户2026-09-13反馈已取消F1，因此该快照不再代表当前关注。本轮没有读取或修改真实账号。Mac赛事与英超链接已有实际读回；NBA/英超手机显示、英超手机播放与自动匹配尚待验。F1试用收尾按用户决定后置。

## Git 与运行

发布基线包含三Provider目录/状态适配、免费额度请求节流、IaC Key Vault引用、部署记录和2026-09-12验收证据。新包部署ID为c802a62c-1f93-45bd-8731-fe55e62421f7；部署包与Git提交仍是不同证据。

2026-09-13本地复核：`uv run pytest -q`为371 passed / 2 skipped / 2依赖弃用警告，`uv run ruff check .`、Bicep编译与已跟踪`infra/main.json`逐字一致、`git diff --check`通过。为浏览器验收短暂启动SQL本地API，完成后已停止；没有push或新部署。

历史过程保存在[整理前快照](<../anke-sports 文档/archive/2026-09-12-status-before-consolidation/>)；不重复放进当前状态。

最新目标为完整v1上线，Chrome/MCP/公共Feed/官方直播和账号生命周期进入上线计划；Google直连仍延期。具体阶段与环境决策以整体计划为准。
