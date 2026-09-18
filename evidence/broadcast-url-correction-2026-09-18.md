# 2026-09-18 中国大陆直播入口纠正

## 产品入口契约

本次只调整中国大陆的 NBA 与 F1 官方产品入口；英超入口保持原值：

| 赛事 | 版权方 | URL |
| --- | --- | --- |
| NBA | 腾讯体育 | `https://sports.qq.com/kbsweb/index.htm#nba` |
| NBA | 咪咕视频 | `https://www.miguvideo.com/p/home/3cd6ba04967742879aaa40bee02a99a6` |
| F1 | 腾讯体育 | `https://sports.qq.com/kbsweb/#100360` |
| 英超 | 咪咕视频 | `https://www.miguvideo.com/mgs/website/prd/sportsHomePage.html?pageId=0c40bbc85fa345bbba20f8e5fd11a922` |

入口仍由地区/联赛版权矩阵与用户直播偏好选择；本次没有修改手动链接覆盖、来源合并、删除或场馆逻辑。

## 本地验证

- `uv run pytest -q`：**250 passed, 2 skipped**。
- `uv run ruff check app tests`：通过。
- `git diff --check`：通过。

## 开发环境发布与回读

- 目标：现有 Azure 开发 Function App `anke-sports-dev-mtcflttk`，East Asia；未改正式环境、基础设施、配置、RBAC、数据或 Git。
- 运行包：`data/anke-sports-broadcast-url-correction-20260918.zip`，192228 字节、85 个运行时文件，SHA-256 `52f7d97745c973c3846fd779640f2e4996f867b8f914b220acf7e7c107e5d090`。
- OneDeploy：`71e5ac19-4444-4c94-9d8e-3388bcd46a70`，状态 4、完成且 active。
- Azure 直连和 `https://sports.anke-ai.com` 代理的 health 均返回 `ok / staging / cosmos`。
- 两个入口的 `/api/v1/platforms` 回读均返回上表四个赛事/版权方 URL；英超咪咕 URL 与发布前保持一致。
