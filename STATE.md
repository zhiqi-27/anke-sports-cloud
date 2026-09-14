# Anke Sports · 服务端状态

更新：2026-09-13。完整当前结论以[工作区 STATE](../STATE.md)为准，[整体计划](<../anke-sports 文档/Anke_Sports_实施计划.md>)规定后续顺序。

独立Azure开发环境已部署三Provider，Cosmos个人Feed已交付Mac。F1有自动视频到手机样本，英超为人工附链到Mac；NBA视频待内容。2026-09-13 已将 NBA 季前赛显式抓取/标注与 `matching-v4` Spurs 跨运动消歧部署到开发环境；历史证据尚未签收部署后的季前赛数量。官方直播、OAuth、账号删除的文档模式代码已部署到开发环境。公共订阅于2026-09-14移出v1，公共Feed后端保留且白名单继续为空。MCP真实客户端仍待P3，SQL为本地基线。

## 验收边界

最后一次云端证据中的个人配置为F1、马刺、利物浦，共132场；用户2026-09-13反馈已取消F1，因此该快照不再代表当前关注。iPhone截图确认旧Feed把用户移除的未来F1输出为取消日程；修复已部署，个人Feed现在隐藏用户移除事件，同时保留历史比赛、赛事源真实取消和公共Feed墓碑。部署没有修改真实账号或立即重建其Feed，设备结果仍待每日维护发布和刷新读回。Mac赛事与英超链接已有实际读回；NBA/英超手机显示、英超手机播放由用户自行验收，英超自动匹配尚待验。F1试用收尾按用户决定后置。当前身份读取 Key Vault secret 被 RBAC 拒绝，未临时提权，因此季前赛真实数量仍未知。

## Git 与运行

发布基线包含三Provider目录/状态适配、免费额度请求节流、IaC Key Vault引用、部署记录和2026-09-12验收证据。新包部署ID为c802a62c-1f93-45bd-8731-fe55e62421f7；部署包与Git提交仍是不同证据。

2026-09-13取消关注隐藏提交`cfb3798`已部署：OneDeploy `9ea4685d-a09a-4eef-b33f-5ca5fa3ef60d`为status 4、active/complete，六函数和双入口健康读回通过；详见[语义证据](evidence/personal-feed-unfollow-2026-09-13.md)与[部署记录](evidence/personal-feed-unfollow-deployment-2026-09-13.md)。没有push或前端发布，真实个人Feed尚待重建和设备刷新。

历史过程保存在[整理前快照](<../anke-sports 文档/archive/2026-09-12-status-before-consolidation/>)；不重复放进当前状态。

最新目标为完整v1上线，分发集成只保留公网MCP；Chrome扩展及商店发布已于2026-09-13取消，公共订阅于2026-09-14移出v1。官方直播和账号生命周期仍在上线计划，Google直连继续延期。

## P2 已部署，业务验收待完成

2026-09-13：账号删除/授权/公共Feed后端/官方转播接入文档模式，US/CN为首批转播地区。公共Feed随后按产品决策保持停用，不再进入v1验收。已发布到现有Azure开发环境；没有修改真实账号或创建真实转播记录。

2026-09-13范围澄清：不做Skill，之前仅为使用场景讨论。公网MCP仍按既定计划交付；本次仅更新文档，无产品代码修改或部署。

本次刷新核对HEAD `40f74c0`；最新运行代码证据为`cfb3798`（包含P2），旧c802部署仅为历史。当前剩余工作以真实账号、公开投影、转播和公网MCP验收为主；未重跑测试或部署。

## 2026-09-13 球队 Logo（本地待发布）

球队来源契约新增可选`logo_url`：英超沿用football-data目录的`crest`，NBA按球队缩写指向NBA官方CDN；比赛参与者契约不携带Logo。提交`243c5c9`及旧目录兼容提交`1f86ae9`已发布，最终OneDeploy `96140a70-67a1-4fa8-a96d-375daca9c899` active/complete。公网读回30支NBA与20支足球球队Logo，唯一无验证映射的London Lions回退`LON`；未等待或强制Provider同步。见[发布记录](evidence/team-logo-deployment-2026-09-13.md)。

## 2026-09-14 公开赛程单选筛选（本地待发布）

`GET /api/v1/events`新增可选`source_id`，赛事ID返回该赛事全部比赛，球队ID只返回参与者包含该队的比赛；SQL基线与Cosmos文档运行时共用相同筛选和游标绑定规则，MCP读取也接受同一参数。OpenAPI已重新导出。完整测试`384 passed, 2 skipped`，Ruff检查通过；尚未部署Azure。

关注预览与保存新增服务端范围校验：赛车系列赛可整体关注，篮球、足球等非赛车球队可直接关注；篮球、足球整联赛和赛车车队均不能写入，旧客户端绕过界面会收到`FOLLOW_SCOPE_NOT_ALLOWED`。季前赛客队仍保留为比赛参与者，但不会出现在可关注目录或被直接关注。最终提交`d58c6be`已发布为OneDeploy `14fb2ac0-4b07-446f-854a-77e5673274fb`，status 4、active/complete；公网NBA目录为30支且无London Lions。17项初始关注流程、38项最终定向测试及完整测试`386 passed, 2 skipped`、Ruff与`git diff --check`通过。见[部署记录](evidence/calendar-following-deployment-2026-09-14.md)。
