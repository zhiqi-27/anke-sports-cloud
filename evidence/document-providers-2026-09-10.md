# 文档赛程更新验收

2026-09-10。开发与生产仍为 Serverless + Periodic 目标；Azure资源未创建。本批为本地文档适配器、离线样本和真实Jolpica读取，未使用Cosmos数据面。

## 本地代码检查

- 完整回归 **302 passed / 2 skipped**，15.58秒；跳过项为条件MySQL测试，两条既有Starlette/AnyIO弃用警告。随后仅细化禁用来源的状态输出，相关Provider/文档HTTP **20项通过**。Ruff及完整OpenAPI/config schema比较通过。
- 新增14项Provider测试，覆盖HTTP重复请求、全部分页成功后发布、NBA分页/metadata/日期、足球数量/状态、F1各session、未知来源、缺Key、并发手动/定时共用任务、重启/冷却、三次熔断、21天Retry-After早醒延期、失败提交原子回滚、过期worker、新旧ID保留和显式配置启动/关闭。
- 日历窗口本地与Azure函数共用日期ID，启动后恢复持久任务；离线加载实际Functions SDK，确认分钟来源更新、每日窗口、outbox和Queue函数已注册且文档组合未导入SQL。
- 失败存储不进行仅修改job的兜底确认；旧赛程、成功时间和fanout保持原子边界。相关测试使用真实本地适配器的ETag/事务错误，不把样本当作真实Azure故障。

## 真实HTTP与独立worker

`experiments.document_provider_live` 在新进程中创建临时文档库、API和独立worker，通过本地认证HTTP请求真实Jolpica抓取并保存F1关注。最终8项检查通过：抓取完成、个人ICS发布、唯一UID、304、HEAD、日历来源/关注状态、新worker完成启动周期后未提前重抓且Feed不变、退出会话。

个人滚动窗口 **85条ICS事件**；另一组过去30天至未来120天查询返回 **60条真实事件**。两者是不同窗口，不能把60/85说成上游整年总数。Feed revision2；内容和UID集合仅保留摘要。API、worker、新的单周期worker、本地会话、临时目录均已清理。

最终实验在开始和结束核对源摘要一致，记录见 [机器证据](document-providers-2026-09-10.json)。早期两轮同样各8项/85条通过，之后因显式配置与状态完善再次验证；只以最后一轮源码绑定结果作为本批最终真实证据。没有凭据、私人Feed地址或原始上游异常写入证据。

## 可检查页面与当前边界

独立[演示日历](http://localhost:3007/calendar)已重启到新后端，并通过HTTP载入真实F1关注及85条已发布事件。旧3007临时实例正常关闭并清理其夹具；主SQL、Firebase和其他产品实例未重启。新的API/HTML代理session7461/PID94103、worker PID94104保留供检查；页面需要重新进入本地体验并切换“真实赛程”。

用户告知Mac锁定且不在旁边，本批没有浏览器或原生UI操作、登录确认或解锁请求。HTML/HTTP可达与数据读回已有证据，**新页面的浏览器渲染尚未检查**，需解锁的步骤暂停。已有演示比赛仍明确标为demo，F1事件不混入演示数据。

本批不证明真实NBA/足球权限、自然六小时周期、Cosmos身份/RU/429/恢复、Azure Queue/Timer、手机日历或直播直达；不代表完整迁移。创作者/YouTube、公共Feed、直播、OAuth/MCP、账号删除等文档路径仍需完成。两个仓库分别记录状态；客户端没有源码、合同或依赖变更，没有重复构建。无push、部署或主数据库迁移。
