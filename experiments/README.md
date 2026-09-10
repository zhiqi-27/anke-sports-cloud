# M0 实验入口

这些脚本准备可重复输入，不代替真机验收。

## T01：同一订阅地址，四种变更

```sh
uv run python -m experiments.ics_lab init
uv run python -m experiments.ics_lab serve
```

只在本机 8890 提供 `/calendar.ics`。使用 Apple Calendar 本机测试可添加该地址；Google 云端抓取与手机测试需要独立可达 HTTPS 实验地址。尚未部署这个实验源。

初始订阅后，依次执行并观察，每一步留足客户端实际刷新时间：

```sh
uv run python -m experiments.ics_lab publish --phase 1
uv run python -m experiments.ics_lab publish --phase 2
uv run python -m experiments.ics_lab publish --phase 3
uv run python -m experiments.ics_lab publish --phase 4
```

对应：描述/链接变化、开始时间 +2h、取消、过去事件追加复盘。四个事件每阶段身份固定；未变更事件的版本和时间戳固定。实验仅合成内容，example.com 是占位链接。重复 `init` 不覆盖已有身份。

在 [观察表](observations.csv) 填写真正观察到的时间、客户端版本与结果。没有看到手机变化不能填写通过；不要将私人订阅地址填入表格。实验结束在客户端删除实验订阅。

## T02：具体内容直达

使用实际明确对应比赛的官方 HTTPS 链接；依次记录：比赛匹配、App 打开到具体内容、账号权益能播放。打开平台首页不通过。不猜测私有 URL Scheme，也不把 YouTube 网页成功视为原生 App 播放通过。

## T03 / T04

Jolpica 已获取公开响应样本；NBA/football-data keys 需独立配置。YouTube 推送订阅、续订、补查与回调已有本地实现和隔离验证；独立 Key 已在 Cloud Shell 完成频道/上传列表/视频详情三次真实读取，本机安全下载与应用联调、真实 Hub 长期续订仍待完成。见 [YouTube 开发接入](../docs/youtube-development.md)。
