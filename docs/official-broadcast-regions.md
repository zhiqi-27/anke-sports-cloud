# 官方转播地区与移动端打开方式

更新：2026-09-15。这个清单用于帮助维护者选择版权平台，不能替代对具体场次、具体内容页、付费条件和地区的逐场审核。

## 当前覆盖

- 美国：F1 为 Apple TV；NBA 由 NBA Tap to Watch 按场次路由到 ESPN、Peacock 或 Prime Video；英超为 NBC Sports / Peacock。
- 中国大陆：F1 为腾讯体育；NBA 为腾讯体育或咪咕；英超为咪咕。
- 日本：F1 在 2026–2030 年为富士电视台，在线直播入口为 FOD；英超为 U-NEXT。NBA 的旧 Rakuten 合作资料不足以证明 2026 年当前权利，因此暂不作为可发布版权方，使用 NBA 官方按地区入口时仍须逐场复核。
- 欧洲：版权按国家销售，产品保存 ISO 两位国家代码，不能用一个 `EU` 代码发布。当前矩阵覆盖英国、爱尔兰、法国、德国、奥地利、瑞士、意大利、西班牙、葡萄牙、荷兰、比利时、丹麦、芬兰、挪威、瑞典和波兰；没有列入矩阵的国家先保持不可发布。

权利来源以赛事方为主：[F1 全球转播清单](https://www.formula1.com/en/information/f1-broadcast-information.45y3LNsT1D6VoK0ZmX8ciJ)、[F1 美国 Apple TV 合作](https://www.apple.com/newsroom/2025/10/apple-is-the-exclusive-new-broadcast-partner-for-formula-1-in-the-us/)、[F1 中国腾讯合作](https://corp.formula1.com/formula-1-renews-partnership-with-tencent-to-broadcast-f1-in-mainland-china/)、[F1 日本富士电视台合作](https://corp.formula1.com/fuji-tv-to-exclusively-broadcast-formula-1-in-japan-in-new-long-term-deal/)、[NBA Tap to Watch](https://pr.nba.com/nba-tap-to-watch-initiative/)、[NBA 美国媒体协议](https://www.nba.com/news/nba-media-agreements-2024)、[NBA 中国观看说明](https://support.watch.nba.com/hc/en-us/articles/115000586373-Accessing-NBA-League-Pass-in-China)和[英超 2025–28 转播方](https://www.premierleague.com/en/media/broadcasters)。

## 手机打开方式

产品只保存无凭据的官方 HTTPS 内容页。手机点击时在当前窗口导航，让 iOS Universal Links 或 Android App Links 接管；未安装 App 时由同一 URL 回落到网页。不会保存私有 URL Scheme、媒体流地址、访问令牌或带签名的播放地址。

同一场比赛在同一地区有多个版权方时，用户在“直播和创作者内容”页面按地区和联赛选择首选直播方。偏好以“地区 + 联赛”保存，切换地区不会覆盖其他地区的选择。赛事详情不承担偏好设置；个人日历的 URL 与描述只交付一条首选直播入口。未选择时使用排序最前的已发布入口。

2026-09-15 对公开关联文件的检查确认，Peacock `/watch/*`、U-NEXT `/livedetail/*`、DAZN 的赛事/赛程页、Viaplay `/sport/*` 和 Prime Video 的详情/播放页存在 App Link 配置。咪咕只在其公开声明的 `/wap/resource/migu/*` 等路径上标记为 App Link。FOD 未公开可验证的 Universal Link/App Link 关联文件，因此日本 F1 显示为“官方网页打开”；只有实际设备回读通过后，才会在记录中增加 App 内准确内容和播放证据。

## 发布约束

`official_match` 必须同时满足：平台在当前赛事和国家的权利矩阵内、URL 是具体内容页、维护者确认官方来源与具体场次、复查期限不超过七天。平台主页或只说明转播安排的页面应发布为 `programme`，不能标作比赛直播。
