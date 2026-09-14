# Anke Sports · 服务端状态

更新：2026-09-14。以[工作区STATE](../STATE.md)及[实施计划](<../anke-sports 文档/Anke_Sports_实施计划.md>)为当前入口。

后端HEAD `406b800`，刷新前工作树干净。最新有证据的运行代码`d58c6be`，OneDeploy `14fb2ac0-4b07-446f-854a-77e5673274fb`，开发环境active/complete；后续HEAD为文档调整。

已部署：三Provider、季前赛显式抓取、Spurs消歧、Logo兼容、source_id过滤、直接关注规则、客队排除、个人Feed取消关注隐藏，以及P2账号/OAuth/转播文档服务。公共Feed保留停用且白名单为空，不进入v1验收；公开赛程与匿名MCP查询仍在范围。

最新发布记录为386 passed / 2 skipped，38项定向、3项打包、Ruff与diff通过；六函数/双入口健康、NBA30队且无London Lions已读回。见[部署证据](evidence/calendar-following-deployment-2026-09-14.md)。本次未重跑、未查询实时部署。

剩余：个人ICS与设备更新回读、真实季前赛/视频覆盖、独立测试账号生命周期与Queue路径、US/CN官方场次、公网MCP授权/查询/写入/到期/撤销、最小恢复和正式发布。旧调度时间已过去，不能以预定时间推断Feed已重建。SQL仍为本地基线，真实云验收分别留证。

本次只刷新文档，未部署、push、修改账号或启动服务。旧过程已保存在工作区归档。
