# P1 三运动与内容最小关键矩阵 · 2026-09-13

本文件记录发布前的本地实现与合成回归，不代表真实 NBA 季前赛读回、真实英超自动发现或 iPhone 验收。随后开发环境部署见[P1部署记录](p1-deployment-2026-09-13.md)；部署过程没有修改真实账号、Feed、频道或云端赛程数据。

## 本轮实现

- NBA 抓取现在分别读取 BALLDONTLIE 默认的非季前赛结果和显式 `season_type=preseason` 结果，所有分页完成后才合并发布。季前赛事件保留原比赛 ID，并在标题前显示 `[季前赛]`。
- 官方接口说明明确：省略 `season_type` 时排除季前赛。因此不能再把默认响应称为全赛季；v1 的实现目标为过去 7 天至未来 90 天窗口内的非季前赛和季前赛，不代表全年覆盖。
- NBA `delayed` 与 `suspended` 按延期处理；`canceled` / `abandoned` 按取消处理。未知时间继续生成日期事件，不伪造开赛时刻。
- 匹配规则升级为 `matching-v4`，增加马刺、雷霆、热刺别名。`Spurs` 是跨运动歧义词：单独出现时不产生候选；只有另一支参赛队也被标题识别后，才允许进入对应比赛判断。

来源：[BALLDONTLIE Games](https://docs.balldontlie.io/#get-all-games)。

## 已通过的本地矩阵

2026-09-13 定向运行 52 项，全部通过；最终全量 374 passed / 2 skipped，另有 2 条既有第三方弃用警告。

| 边界 | 本地结果 | 证据性质 |
| --- | --- | --- |
| NBA 默认与季前赛 | 分两组完整分页；季前赛显式标注；同一上游 ID 若跨组重复则整批拒绝 | 合成上游 |
| 主客队、时区、跨午夜、未知时间 | 客队在前、主队在后；精确时刻保留带时区 UTC；查询按绝对时刻；未知时刻为日期事件 | 合成回归 |
| 延期、取消 | 延期为 `TENTATIVE`，取消/用户移除为 `CANCELLED`；均保持 UID 并递增 SEQUENCE | 合成回归 |
| 重复与无变化 | UID 稳定；内容相同不增加 SEQUENCE、不改变 ETag | 合成回归 |
| 上游部分失败 | 不完整分页或最终事务失败不切换赛程指针，旧 Feed 字节与 ETag 保留 | 合成故障 |
| 订阅令牌 | 轮换后旧地址 404，新地址内容与 UID 不变 | 合成回归 |
| Feed 暂停/恢复 | 暂停期间继续提供最后完整快照；恢复后发布积压变更 | 合成回归 |
| 马刺/热刺 | `Spurs vs Thunder 2026-10-08 preview` 只自动命中 NBA；`Spurs season preview` 不产生 NBA 或英超候选 | 合成回归 |
| 同队不同日期 | 同一对手相邻比赛且日期不明确时均保持待确认，不按候选顺序强选 | 合成回归 |
| 其他联赛、泛赛季内容 | 需命中比赛双方；trade/draft/season review 等主题拒绝 | 合成回归 |
| 用户移除/固定 | 屏蔽后重新发现仍不恢复；固定链接在标题变化或移除频道后保留 | 合成回归 |

涉及用例：`test_document_providers.py`、`test_matching_replay.py`、`test_document_feeds.py`、`test_document_runtime.py`、`test_document_content.py`、`test_document_creators.py`。

## 尚未通过

- 当前登录身份没有 Key Vault secret 读取权限；本地真实 NBA 探针返回 `ForbiddenByRbac`，因此没有读取或保存真实上游内容。需要在部署后由现有 Function 托管身份同步，再只读核对季前赛数量、马刺场次和状态。
- 英超 NBC Sports 频道尚未加入真实持续发现；现有利物浦复盘仍是人工固定样本。真实频道接入后若不能唯一匹配，应停在待确认。
- NBA 没有与当前具体日期、对阵相符的视频，不附旧赛季、热刺或泛赛季内容。
- iPhone 由用户执行，步骤见[支持矩阵](../docs/support-matrix.md)。用户回报前保持未验收。
