# Azure 开发资源预演

2026-09-11，Azure CLI `deployment sub what-if`，名称 anke-sports-dev-preflight-20260911，East Asia，infra/main.bicep 与 infra/dev.bicepparam。进程正常退出，云端 status Succeeded、error null。

21 项变更全部为 Create，全部限定到 anke-sports-dev 资源组及其子资源，没有 Modify 或 Delete。目标包括：Cosmos 账号 anke-sports-dev-mtcflttk、数据库与两个容器及数据面角色；Storage ankesportsdevmtcflttk 及 Blob/Queue/Table；Key Vault ankesports-dev-mtcflttk；专用身份、网络、限定角色；Functions Flex 计划、Function App anke-sports-dev-mtcflttk 与配置。

本地模板明确 Cosmos Serverless + Periodic、Functions 无常驻实例。What-if 使用 ResourceIdOnly，不包含逐项属性比较；不能替代部署后容量、网络、RBAC 或运行验证。未执行 deployment create，未创建资源，未写任何密钥。

现有 Azure Deploy 技能要求 prepare/validate 工作流完成并生成带验证证据的 .azure/deployment-plan.md；当前非 azd 项目尚未满足该技能前置状态，不能直接把本记录标成该技能的 Validated。实际创建前仍需完成适用于本仓 Bicep 的部署验证流程。
