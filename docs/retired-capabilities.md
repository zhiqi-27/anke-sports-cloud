# 1.0 范围退出与当前契约

生效日期：2026-09-17。本文是 [Anke Sports 1.0 定义](<../../anke-sports 文档/Anke_Sports_1.0定义.md>) 与 [实施计划](<../../anke-sports 文档/Anke_Sports_实施计划.md>) 的实现边界记录，服务于双仓本地候选版本。

## 当前保留的能力

- 关注只接受具体球队或车队，配置形状为 `Follow { type: "team", source_key }`。
- 独立手动来源使用 `Config.manual_events[{ event_id }]`。它不要求球队关注；重复 `event_id` 在配置契约中拒绝，接口层的幂等增删由后续 B/C 实现。
- 私有事件视图可返回 `calendar.sources` 与 `calendar.can_remove`：来源区分 `follow` 和 `manual`；只有存在手动来源且没有关注覆盖时才可单独移除。公共事件视图不返回个人来源。
- 人工链接写入不再暴露 `live`/`watch_along` 类型，服务端统一按 `live` 个人观看入口保存；公共维护记录仍可保留同步解说等内容类型。人工链接使用独立的 HTTPS、公开域名和凭据安全校验，不要求命中官方平台目录。直播入口与个人日历来源是两套独立概念。
- 当前 OpenAPI 从 SQL 基线导出，客户端通过 `npm run contracts` 生成；文档模式不另行维护一套公开契约。

## 已退出的入口、接口、工具与任务

- Web 的创作者、视频内容、待确认和事件 `selection` 入口已移除。
- HTTP 的 creator/review/YouTube webhook 与相关维护接口已移除；`/api/v1/events/{event_id}/selection` 不存在。
- MCP 不再注册 `add_creator`；当前私人工具为 10 个，手动日历增删工具属于后续 C，不把目标名称当作已在线能力。
- YouTube/AI 配置、provider key、内容 Timer、视频发现/匹配运行时引用已从当前活动路径移除。任务 claim 只接受当前活动集合：`identity_cleanup`、`projection`、`public_projection`、`provider`、`broadcast_check`；旧视频任务不再被 claim 或静默完成。

## 历史材料与数据处理

旧视频表、ORM 模型、模块、测试证据、文档和可能存在的旧队列行暂时保留，用于追溯已有工作；它们不属于 1.0 当前接口或兼容前置，当前运行时不加载。此次没有删除历史数据、执行迁移、覆盖未提交改动、push 或部署。

## 本地验证边界

本记录对应本地候选版本。已完成的 A 验证包括 schema/OpenAPI 导出、客户端生成、旧入口断言、旧链接过滤、旧任务不可 claim 和服务端全量回归（239 passed / 2 skipped）；B 的手动增删、C 的 MCP 对等工具、D 的双仓全链路与 E 的部署/发布验证仍未完成。线上开发环境仍是旧版本，不能用来证明本记录已部署。
