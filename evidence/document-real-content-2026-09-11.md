# 真实视频到个人 ICS · 2026-09-11

## 同事件内容更新补验

通过运行中的 3009 HTTP 服务与独立后台 worker，临时切换本地体验身份的防剧透偏好并恢复：83 条 Feed 事件 UID 集合保持一致，正赛事件的描述变化且保留真实视频链接，SEQUENCE 从 2 到 3；恢复后描述回到原文，SEQUENCE 到 4，条件读取 304。原偏好已读回确认恢复。此项补足内容修改前后版本证据，不代表首次自动附链或外部客户端刷新已验收。见 [HTTP 结果](real-feed-revision-2026-09-11.json)；复现脚本 experiments/real_feed_revision.py。

后续浏览器核验：启动 `experiments.document_real_ui`，访问 localhost:3009，在页面选择真实赛程并进入本地体验。月历显示 20 个窗口内赛段；9 月 6 日正赛抽屉显示 Race Highlights 的自动关联链接，展开日历描述后同一原链接可见。浏览器页已留给用户检查。未点击外部视频，不作为播放证明。主应用视觉未改；独立品牌预览未集成。

真实 Jolpica 赛程和 YouTube 元数据，经文档 Provider、创作者匹配、队列与个人 Feed 发布路径交付：

- Italian Grand Prix · 正赛：https://www.youtube.com/watch?v=uptj3to1l7o
- Italian Grand Prix · 排位赛：https://www.youtube.com/watch?v=aTqIIB2HrNg

隔离本地身份，ASGI TestClient、documents-local 和本地队列处理器；不是 Azure、Firebase、浏览器或手机验收。YouTube 本轮预留 6 单位，恢复后仍为 6。沿用 data/document-youtube-live.db 持久账本；数据与加密密钥保留在忽略目录中，未修改主用户配置。

首次实验缺少 Provider 标准化字段，校验失败；随后改用实际 Provider 队列入口。第二次达到实验 200 次处理上限，184 条任务已完成、67 条待处理；继续同一队列至空闲，最终 10 项检查通过。业务重试规则未修改。

恢复后的基线与结果 UID 一致，两个事件 SEQUENCE 均为 2 到 2，条件读取返回 304。首次发布前版本快照未保存，因此本次不能证明版本 1 到 2 的更新。视频内容、播放及手机显示尚未验证。

本机原始结果：data/document-real-content-2026-09-11-run3.json；失败记录也保留。实验脚本 experiments/document_real_content.py 目前固定输出路径且拒绝覆盖，后续复用前需要指定新的输出路径。
