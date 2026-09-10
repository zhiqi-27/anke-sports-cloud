# Anke Sports 独立开发环境

状态：原 MySQL 模板本机编译与 Azure validate 通过，尚未部署。用户未批准其费用，要求参考 Anke Money 降低成本；现评估独立 Cosmos Serverless 候选，见 [成本与改造方案](../docs/azure-low-cost-proposal.md)。数据库选型尚未变更，不能按下文旧规格创建资源。2026-09-10。

目标为开发/验收环境，不迁移本机数据、不修改现有其他产品资源、不发布生产版本。

## 已核对的上下文

- 默认订阅 Azure subscription 1：`a1187bf2-2e2f-4e05-aaea-407163a009f5`，Enabled。
- 租户 `5ba85645-4c35-4efd-93ea-11c0890472d8`；独立 Firebase 项目 `anke-sports-dev`。
- 当前无 Anke Sports 资源组。Web/App/MySQL/Storage/Network/KeyVault Provider 已注册。
- 订阅策略查询返回 SecurityCenterBuiltIn 默认策略；无据此推定模板已通过策略验证。
- East Asia 支持 Functions Flex Consumption 和 MySQL 8.4 Burstable B1ms。

## 准备方案

拟使用独立资源组 `anke-sports-dev`，区域 `eastasia`。Functions Linux/Python3.12、Flex Consumption、无常驻实例；独立 MySQL8.4 B1ms/20GiB、7天备份；独立 Storage LRS/队列与 Key Vault。MySQL通过专用VNet子网访问，不开放全网防火墙。

工作顺序：生成可审查Bicep → 编译与成本核对 → 确定计费上限和目标 → ARM验证/what-if → 创建开发资源 → 凭据配置/最小权限/迁移 → 发布代码 → 真实HTTP/Google登录/Timer/Queue/MCP/ICS与日志验收。

用户已授权托管浏览器配置独立资源，登录由用户完成。具体计费规格尚未确认；先把模板与费用准备完整。Azure Prepare仅适用于azd，本项目使用直接Bicep/CLI准备，没有擅自引入azure.yaml。

## 验证与恢复

Bicep0.46.1编译通过，Azure订阅级validate返回Succeeded，见evidence/azure-dev-template-validation-2026-09-10.json。Web来源仍为HTTPS占位值。尚未执行what-if/创建/发布；不把validate当作远端运行证明。源码包哈希0f0e28483705cdb78241b6f398335ac170981551cc1d4b18e79765202daac8a1。

首次部署为空环境：先建基础设施，密钥只写受控存储，再用独立迁移身份初始化空库；日常更新需先备份、保留前版代码包。迁移失败先停止发布并判断数据变化，不自动删库或删除资源组。数据库私网内的迁移执行路径和恢复演练尚需实现。

## 未完成

费用确认、远端资源、Key Vault读取与托管身份、Functions真实Firebase、私网迁移/MySQL TLS、平台遥测脱敏、云死信/告警、Web HTTPS、YouTube与设备验收。

费用与具体规格见[开发资源计划](../docs/azure-development-plan.md)：MySQL基础约US$23.88/月；Functions等按量另计，建议US$40/月参考预算，不是硬上限。
