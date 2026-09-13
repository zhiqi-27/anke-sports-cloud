# Anke Sports · 服务端状态

更新：2026-09-13。完整当前结论以[工作区 STATE](../STATE.md)为准，[整体计划](<../anke-sports 文档/Anke_Sports_实施计划.md>)规定后续顺序。

独立Azure开发环境已部署三Provider，Cosmos个人Feed已交付Mac。F1有自动视频到手机样本，英超为人工附链到Mac；NBA视频待内容。2026-09-13 已将 NBA 季前赛显式抓取/标注与 `matching-v4` Spurs 跨运动消歧部署到开发环境；首次部署后 NBA 真实同步尚未发生。公共Feed、官方直播、OAuth、账号删除的文档模式代码已部署到开发环境（P2 aff58eb）；公共源白名单仍为空，真实账号及转播播放路径尚未签收。MCP真实客户端仍待P3，SQL为本地基线。临时Vault写入角色已撤销，不重做密钥配置。

## 验收边界

最后一次云端证据中的个人配置为F1、马刺、利物浦，共132场；用户2026-09-13反馈已取消F1，因此该快照不再代表当前关注。iPhone截图确认旧Feed把用户移除的未来F1输出为取消日程；本地候选已改为从个人Feed隐藏用户移除事件，同时保留历史比赛、赛事源真实取消和公共Feed墓碑。尚未部署或修改真实账号。Mac赛事与英超链接已有实际读回；NBA/英超手机显示、英超手机播放由用户自行验收，英超自动匹配尚待验。F1试用收尾按用户决定后置。当前身份读取 Key Vault secret 被 RBAC 拒绝，未临时提权，因此季前赛真实数量仍未知。

## Git 与运行

发布基线包含三Provider目录/状态适配、免费额度请求节流、IaC Key Vault引用、部署记录和2026-09-12验收证据。新包部署ID为c802a62c-1f93-45bd-8731-fe55e62421f7；部署包与Git提交仍是不同证据。

2026-09-13取消关注隐藏候选完成：SQL与文档/Cosmos路径定向60 passed，全量383 passed / 2 skipped / 2既有依赖弃用警告，Ruff与`git diff --check`通过；详见[个人Feed语义证据](evidence/personal-feed-unfollow-2026-09-13.md)。该候选尚未部署。此前开发环境 OneDeploy `a80acfdd-d2bb-49f1-9d17-cecc1183969f` 仍为当前线上后端；没有 push 或前端发布。

历史过程保存在[整理前快照](<../anke-sports 文档/archive/2026-09-12-status-before-consolidation/>)；不重复放进当前状态。

最新目标为完整v1上线，Chrome/MCP/公共Feed/官方直播和账号生命周期进入上线计划；Google直连仍延期。具体阶段与环境决策以整体计划为准。

## P2 候选

2026-09-13：账号删除/授权/公共订阅/官方转播接入文档模式，US/CN 为首批地区。见 [实现与边界](docs/p2-cloud-business.md)及[验收记录](evidence/p2-local-2026-09-13.md)。已发布到现有Azure开发环境，部署 `3a2b84d5-b398-4781-a102-6fd69fac5edd` 成功，见[部署记录](evidence/p2-deployment-2026-09-13.md)。没有push，也没有修改真实账号或创建真实转播记录。
