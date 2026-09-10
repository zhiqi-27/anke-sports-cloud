# 新存储的赛程抓取与定期更新

2026-09-10。适用于 `documents-local` / `cosmos` 组合。真实 F1 抓取已在独立本地 HTTP + worker 环境通过；不是 Azure/Cosmos 数据面或手机日历验收。

## 数据来源与共用规则

`provider_adapters.fetch_schedule` 只返回完整解析后的事件与来源，不导入数据库。SQL 和文档实现共用它，保留原有 source_key、时区、日期精度和比赛状态。SQL 继续用已有比赛 ID；文档实现优先保留已导入的 ID，只为新比赛生成确定性 ID。

| 来源 | 当前范围 | 校验与证据 |
| --- | --- | --- |
| Jolpica | 当前年份 F1，各分站的正赛、练习、排位和冲刺独立事件 | 总数匹配、重复赛道身份拒绝、无时间时保留日期；真实抓取/个人 ICS 通过 |
| BALLDONTLIE | NBA，过去7天至未来90天查询窗口 | 完整 cursor 分页、metadata、循环/页数限制、未知时间、失败保留旧数据；目前仅离线样本，无独立真实 Key 验收 |
| football-data | 英超 PL | 数量声明一致、延后/取消/未知时间、只知日期时不伪造开始时间；目前仅离线样本，无独立真实 Key 验收 |

字段和范围对应来源文档：[Jolpica race/session 字段](https://github.com/jolpica/jolpica-f1/blob/main/docs/endpoints/races.md)、[BALLDONTLIE games 与分页](https://docs.balldontlie.io/#get-all-games)、[football-data match 状态](https://docs.football-data.org/general/v4/match.html)。Jolpica 是赛程来源，不因此获得赛事官方或直播认证；授权、覆盖和展示许可继续按 T03 单独记录。

每个HTTP请求30秒超时；分页前后检查180秒抓取预算，超过预算的结果不提交。抓取不跟随重定向。缺少 Key 直接失败，错误保存为安全代码，不保存上游异常正文、请求头或凭据。

## 启用方式

本地体验登录后，在设置页手动请求抓取；同一路径为 `POST /api/v1/local/providers/{provider}/sync`。重复点击复用同一待执行任务。首次成功后，本地状态开启该来源的六小时更新；缺Key或失败不能显示成功。

文档模式的 staging/production 使用明确配置：

```dotenv
ANKE_SPORTS_ENABLED_SPORTS_PROVIDERS=["jolpica"]
```

仅接受三个已实现的来源ID。默认 `[]`，生产不会因为存在旧状态或一个Key就自行启用来源；生产环境没有本地抓取HTTP入口。Timer 可据显式配置为全新数据库创建首个任务。移出配置后，当前配置的worker不再抓取待执行任务，状态显示禁用，已有赛程保留。该列表控制抓取；公共Feed授权列表独立管理。

实际云部署时需在独立 Function App 配置此环境变量和相应来源凭据；本批未创建资源、修改云设置或部署。

## 原子提交与重试

`state / provider:<id> / sync` 保存启用状态、最后尝试/成功、失败次数、下次允许时间及唯一待执行任务。手动和定时请求先以 ETag 原子提交该状态和 outbox；不能并发追加同来源抓取。

worker领取任务后，在联网前持久化尝试时间并检查租约。全部页面解析后，先准备不可变赛程块，再把赛程指针、成功状态、当前任务完成、新 catalog_changed 任务放进同分区事务。内容完全相同则保持赛程版本和公开时间，只记录本次成功并结束任务。导入ID、改期UID与Feed条件请求保持原语义。

失败与延迟也同事务记录；无法保存失败就抛出错误，由队列/租约恢复重试，不回落成仅确认任务。连续三次失败进入更长冷却，五次尝试后终止当前任务；缺Key立即终止。成功重置失败次数。租约过期或新worker接管时，旧结果不能更新赛程或成功时间。

HTTP `Retry-After` 最多保留30天。队列单次延迟最多6天，早到的唤醒只重新延期，不发网络请求、不消耗尝试次数；避免一条超长限流任务阻塞整个 Change Feed 投递。

## 调度与本地检查

Azure每分钟检查来源状态；本地worker启动即检查，之后每分钟检查，六小时条件由持久时间决定，重启不会提前抓取。生产只对配置内的来源检查。每天UTC窗口任务使用同一确定性日期ID，本地与Azure Timer共用处理函数。

```sh
uv run python -m app.document_worker --once
uv run python -m experiments.document_provider_live
```

后一个实验创建独立临时库、API和worker，真实读取Jolpica，经关注预览/确认生成个人ICS，核对UID/304/HEAD、API来源与重启后的调度；最后退出本地会话并清理所有临时进程和数据。主预览、Firebase账号和云资源不参与。

本批完整回归302项通过；最终禁用状态细化后相关20项通过。真实链路8项检查、窗口内85条ICS、另一个查询范围内60条事件，见 [验收证据](../evidence/document-providers-2026-09-10.md)。并发、改期、部分分页、长限流、失败提交和时间边界主要为合成故障测试；真实六小时自然周期、Azure Timer/Queue、RU/429、恢复与设备仍需验收。
