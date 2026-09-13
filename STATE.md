# Anke Sports · 服务端状态

更新：2026-09-13。完整当前结论以[工作区 STATE](../STATE.md)为准，[整体计划](<../anke-sports 文档/Anke_Sports_实施计划.md>)规定后续顺序。

独立Azure开发环境已部署三Provider，Cosmos个人Feed已交付Mac。F1有自动视频到手机样本，英超为人工附链到Mac；NBA视频待内容。2026-09-13 已将 NBA 季前赛显式抓取/标注与 `matching-v4` Spurs 跨运动消歧部署到开发环境；首次部署后 NBA 真实同步尚未发生。公共Feed、官方直播、OAuth、账号删除的文档模式已新增本地候选实现，尚未部署；公网仍为此前有缺口的版本。MCP真实客户端仍待P3，SQL为本地基线。临时Vault写入角色已撤销，不重做密钥配置。

## 验收边界

最后一次云端证据中的个人配置为F1、马刺、利物浦，共132场；用户2026-09-13反馈已取消F1，因此该快照不再代表当前关注。本轮没有读取或修改真实账号。Mac赛事与英超链接已有实际读回；NBA/英超手机显示、英超手机播放由用户自行验收，英超自动匹配尚待验。F1试用收尾按用户决定后置。当前身份读取 Key Vault secret 被 RBAC 拒绝，未临时提权，因此季前赛真实数量仍未知。

## Git 与运行

发布基线包含三Provider目录/状态适配、免费额度请求节流、IaC Key Vault引用、部署记录和2026-09-12验收证据。新包部署ID为c802a62c-1f93-45bd-8731-fe55e62421f7；部署包与Git提交仍是不同证据。

2026-09-13 P1 定向复核 52 passed；最终全量 374 passed / 2 skipped / 2 依赖弃用警告，部署前重点回归 70 passed，Ruff 与 `git diff --check` 通过。开发环境 OneDeploy `a80acfdd-d2bb-49f1-9d17-cecc1183969f` 成功并读回六个函数、双入口健康和运行角色；没有 push 或前端发布。详见[P1矩阵](evidence/p1-core-matrix-2026-09-13.md)、[部署记录](evidence/p1-deployment-2026-09-13.md)与[支持矩阵](docs/support-matrix.md)。

历史过程保存在[整理前快照](<../anke-sports 文档/archive/2026-09-12-status-before-consolidation/>)；不重复放进当前状态。

最新目标为完整v1上线，Chrome/MCP/公共Feed/官方直播和账号生命周期进入上线计划；Google直连仍延期。具体阶段与环境决策以整体计划为准。

## P2 候选

2026-09-13：账号删除/授权/公共订阅/官方转播接入文档模式，US/CN 为首批地区。见 [实现与边界](docs/p2-cloud-business.md)及[验收记录](evidence/p2-local-2026-09-13.md)。本轮没有发布到 Azure、没有 push，也没有修改真实账号或创建真实转播记录。
