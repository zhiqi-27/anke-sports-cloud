# Gemini 视频匹配开发部署 · 2026-09-15

## 结果

- 目标：Azure subscription 1 / `anke-sports-dev` / East Asia / `anke-sports-dev-mtcflttk`。
- 范围：只合并 Gemini 开发设置并发布 Functions 代码包；未应用 Bicep what-if、未发布前端、未 push Git、未触发真实视频任务或修改用户数据。
- 模型：稳定版 `gemini-3.1-flash-lite`。原计划的 `gemini-2.5-flash-lite` 对该新 Google 项目返回 404，因此未部署。
- 候选包：`data/anke-sports-20260915-gemini-31.zip`，SHA-256 `08394b774f2691938d3dfdae024ccaf83057c664706ccf4e60b1c20c75ee0816`，181944 bytes，81 个 allowlisted runtime files。
- OneDeploy：`12e39360-d581-4003-a834-86be02a4e0c1`，2026-09-15T04:02:18Z–04:03:11Z，status 4、active=true、complete=true。

## 验证

- 本地：394 passed / 2 skipped；Ruff、`git diff --check`、3 个 Functions 包测试通过。
- 模板：Bicep 重新编译与跟踪的 `infra/main.json` 一致；ARM subscription validate 为 `Succeeded`。基础设施预演未应用。
- 配置：`ANKE_SPORTS_MATCHING_AI_ENABLED=true`，模型为 `gemini-3.1-flash-lite`；`GEMINI_API_KEY` 只保存为 Key Vault 引用，引用状态 `Resolved`，未将密钥值写入源码、文档或部署记录。
- 运行：六个 Functions 注册；Azure 直连和 `sports.anke-ai.com` 的 health/status 均返回 HTTP 200 与 `Cache-Control: no-store`，health 为 `staging` / `cosmos`。
- 模型：使用应用的实际 `matching-ai-v3` schema 对合成 Thunder @ Spurs 元数据执行一次真实调用，结果为 `automatic`、2 个 emoji 标签并含 AI confidence code。该项只证明 API、schema 和阈值路径，不证明真实视频准确率。
- 权限：运行身份仍只有专用 Vault 的 Key Vault Secrets User、专用 Storage 的 Queue Data Contributor / Blob Data Owner，以及专用 Cosmos `anke-sports` 数据库的 Built-in Data Contributor。临时操作员 Secrets Officer 权限已移除。

## 恢复与未验收项

- 恢复包：`data/anke-sports-20260914-calendar-following-v2.zip`，SHA-256 `1954e4e7688e740ad76a3ad3a50a97f0bbe72070197ca80bd09d94512f0ba5e5`。若必须回退，同时删除本轮新增的 3 个 Gemini 设置；不得删除资源或用户数据。
- 尚未验收：真实 YouTube 发现任务、NBA/英超样本校准、评论与视频内容输入、产品日历和个人 ICS 实际内容、iPhone 跳转 YouTube App。iPhone 按 owner 决定由 owner 自验。
