# Anke Sports 开发部署执行记录

Status: Validated
Recipe: bicep

本文件只记录当前部署执行与验证，不替代工作区实施计划或任务表。沿用用户已授权的独立资源创建配置、Azure Functions、Cosmos Serverless + Periodic 及 sports.anke-ai.com；不是生产环境授权。既有仓库使用 standalone Bicep，不初始化 azd。

## 1. Target

Subscription: a1187bf2-2e2f-4e05-aaea-407163a009f5 / Azure subscription 1
Region: eastasia
Resource group: anke-sports-dev
Template: infra/main.bicep
Parameters: infra/dev.bicepparam
Web origin: https://sports.anke-ai.com

## 2. Scope

独立 Functions Flex（无 always-ready）、Cosmos NoSQL Serverless/Strong/Periodic、Storage、Key Vault、网络与专用托管身份。21 项预演全部 Create，无现有产品修改。后端运行时关闭本地体验，启用 Firebase，先验证真实日历/创作者/个人 ICS。未适配模块仍按接口明确返回未开放。

## 3. Cost

设计目标与假设见 ../docs/azure-low-cost-proposal.md；US$5–10/月是历史低流量设计目标，不是报价或硬上限。不新建 MySQL、常驻实例、额外监控平台或其他产品共享资源。

## 4. Secrets and access

Key Vault 中准备独立 feed-encryption-key、firebase-credentials、youtube-api-key；源码只含引用。托管身份权限仅到本资源组内所需资源。不得打印密钥。云轮询开启前停止同项目本地真实轮询，保留当天预算记录。

## 5. Recovery

创建失败先查询部署操作，仅修失败项；不删除其他资源。代码包回退到上个已验收包；初次部署无上个版本，失败则停用相关函数并保留诊断。Cosmos Periodic 恢复到新账号，需恢复网络/RBAC和删除决定，不承诺即时原地回滚。删除资源需另行授权。

## 6. All validation checks pass

- [x] Core Validation: CLI/auth/build/validate/what-if helper
- [x] Azure Policy Validation
- [x] Build Verification
- [x] Static Role Verification
- [x] Record Proof and resolve errors

## 7. Validation Proof

2026-09-11 standalone Bicep validate-deployment.sh：CLI/auth/build/target validate/what-if 全部 PASS，OVERALL PASS。最终 helper 按格式化文本的 + 行统计为22、~ 行0、- 行0；该计数包含输出格式影响，不作为精确资源数，精确资源列表以前一次 ResourceIdOnly 的21项为准。session 98448 正常退出。

策略：subscription assignment 仅 SecurityCenterBuiltIn，无参数覆盖或 notScopes；Microsoft cloud security benchmark 的默认参数查询未发现 Deny、Modify、DeployIfNotExists。ARM target validate 通过，无部署拒绝。

构建：scripts.package_functions 输出 affc54da6e77664888403be3cc32fcc5927a84c42687ab5cad6623d1d9ba96da，159262字节；tests/test_functions_package.py 为3 passed、2既有警告。已有文档模式/匹配相关83项回归；这些证据不冒充云运行。

静态权限：Cosmos Data Contributor 限定本数据库；Blob Data Owner/Queue Data Contributor 限本Storage；Key Vault Secrets User 限本Vault，均授予专用MI。未授予订阅级权限；部署后仍须实测传播、网络、Feed撤销和应用身份。

2026-09-11，独立 what-if 已 Succeeded，21 Create/0 Modify/0 Delete，见 ../evidence/azure-preflight-2026-09-11.md。Bicep 参数编译与源码打包已通过。该项单独不代表完整验证；以上补充记录为本轮工作流证据。

## 2026-09-12 Multi-sport update validation — historical blocked checkpoint

This paragraph records the temporary pre-authorization checkpoint and is superseded by section 7 below. At that checkpoint provider/package tests were 22 passed and runtime archive SHA256 was 540961d4c13f32db9866a46703652ed91794e34d47522b6110822dfdb5a2bf46; Vault metadata access was blocked by RBAC and no update had yet been deployed. No infrastructure recreation or database migration was planned.

## 7. Validation Proof — 2026-09-12 update

Azure validate workflow resumed after owner authorization. Two Vault secrets written successfully; temporary Secrets Officer role removed and assignment readback empty. Existing application Secrets User remains vault-scoped. validate-deployment.sh --scope sub --location eastasia --template infra/main.bicep --parameters infra/dev.bicepparam: OVERALL PASS (CLI/auth/build/ARM validation/what-if). Initial resource-group scope mismatch corrected. What-if text line counts include unrelated infrastructure drift; no ARM deployment is authorized or executed in this update. Execute only remote-build zip publication and explicit three-setting merge. Policy assignment remains SecurityCenterBuiltIn. Provider/package tests 22 passed, compiled runtime package hash recorded above. Prior recovery package affc54da6e77664888403be3cc32fcc5927a84c42687ab5cad6623d1d9ba96da verified. No schema changes or RBAC expansion for application identity.
