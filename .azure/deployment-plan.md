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

## 8. 2026-09-13 P1 code-only update

Approved target remains subscription `a1187bf2-2e2f-4e05-aaea-407163a009f5`, East Asia, resource group `anke-sports-dev`, Function App `anke-sports-dev-mtcflttk`. The owner explicitly requested deployment after reviewing the local P1 result.

Scope is code-only Functions publication: explicit NBA preseason fetch/label, delayed-state normalization, and `matching-v4` Spurs cross-sport disambiguation. No infrastructure deployment, schema migration, app-setting mutation, RBAC change, frontend publication, Git push, or production action.

Validation steps:

- [x] Azure CLI context, target Function state, current deployment and recovery package read back
- [x] Full test suite, Ruff, deterministic Functions package and manifest validation
- [x] Bicep build equivalence and static least-privilege role review remain unchanged
- [x] Remote-build zip publication completes and registers the existing six functions
- [x] HTTPS health/status/calendar readback and managed-identity role assignments verified
- [ ] Provider sync completes; preseason and Spurs results recorded without reading secrets

Recovery: retain the current successful deployment package and deployment ID before publication. If the new package cannot start or pass health checks, republish that exact prior package; do not change infrastructure or delete data.

### Validation proof · 2026-09-13

- Azure CLI account is `Azure subscription 1` / `a1187bf2-2e2f-4e05-aaea-407163a009f5`; target resource readback is Running in East Asia at `anke-sports-dev-mtcflttk.azurewebsites.net`.
- Current active OneDeploy is `c802a62c-1f93-45bd-8731-fe55e62421f7`, status 4, complete and active. Recovery archive `data/anke-sports-20260912-multisport.zip` remains present with SHA-256 `540961d4c13f32db9866a46703652ed91794e34d47522b6110822dfdb5a2bf46`.
- Full suite immediately before this deployment request: 374 passed / 2 skipped / 2 dependency warnings. Deployment-focused provider, matching, content and package suite: 70 passed / 2 dependency warnings. Ruff and diff checks passed.
- Deterministic source package `data/anke-sports-20260913-p1.zip`: SHA-256 `3509a4bd56827bfc224fbf539993e608cb2049dc48dcabe465fb21788010ff28`, 160233 bytes, 71 allowlisted runtime files; manifest includes both changed runtime modules.
- Fresh Bicep compilation is byte-identical to tracked `infra/main.json`. Infrastructure is not being deployed. Static roles remain scoped to the dedicated Cosmos database, Storage account and Key Vault for the existing user-assigned runtime identity; no subscription-level data role or new assignment was introduced.
- Traditional publishing-profile listing is unsupported for Flex Consumption. Target and recovery were instead verified through the supported resource and OneDeploy management endpoints; this requires no configuration change.

### Deployment proof · 2026-09-13

OneDeploy `a80acfdd-d2bb-49f1-9d17-cecc1183969f` completed with status 4 and is active. Six functions registered. Direct Azure and Cloudflare-routed health endpoints returned 200 with staging/Cosmos identity, public calendar returned 200, and status was dynamic/no-store. Live Storage, Queue, Vault and database-scoped Cosmos roles match the static plan.

The remaining Provider checkbox stays open: NBA `last_success` 00:52:31 UTC preceded deployment completion 01:29:25 UTC. The normal six-hour refresh was not bypassed by mutating Cosmos state, so real preseason readback remains pending even though deployment itself is healthy.

## 9. P2 code-only update · 2026-09-13

Owner explicitly approved deployment of candidate aff58eb to existing anke-sports-dev / anke-sports-dev-mtcflttk. Subscription and East Asia target read back; Running. No infrastructure, settings, RBAC, data migration or frontend deployment is in this update.

### Section 7: Validation Proof (P2)

- azure-validate workflow: existing plan loaded, core validation with explicit subscription passed CLI/auth/Bicep compilation/ARM validation/what-if. First helper invocation hit a Bash empty-array issue; explicit subscription resolved it. What-if reports 8 Create/21 Modify/14 Delete text lines of infrastructure drift; NONE of these infrastructure operations will be applied.
- Candidate SHA256 3a237a834315addccb1c46d63bb015f7936bb6899c58d3561ea4620c870ce663 and recovery P1 SHA256 3509a4bd56827bfc224fbf539993e608cb2049dc48dcabe465fb21788010ff28 verified. Packaging tests 3 passed, candidate runtime manifest previously matched files; final P2 related suite 121 passed.
- Policy assignment readback remains SecurityCenterBuiltIn. No provisioning or policy change. Static and live roles match: database-scoped Cosmos contributor; dedicated Storage Blob/Queue and Vault Secrets User. No permissions expanded.
- Current active deployment readback is a80acfdd-d2bb-49f1-9d17-cecc1183969f. If startup/health fails, republish exact P1 package; do not delete resources or data.
- Public feed source allowlist is empty and broadcast network checks are false. Keep these existing settings; report their effect distinctly from code deployment.

### P2 deployment proof

OneDeploy 3a2b84d5-b398-4781-a102-6fd69fac5edd completed 2026-09-13T03:03:05Z, status4/active/complete. Six functions and dual-origin health/new API routes verified. Public feed remains unavailable per empty allowlist; actual user identity and playback not exercised. See evidence/p2-deployment-2026-09-13.md.

## 10. Personal Feed unfollow visibility update · 2026-09-13

Owner explicitly approved deployment of commit `cfb3798` to the same development Function App. Scope is code-only: personal Feeds omit user-removed projections while retaining them internally for stable UID reuse; upstream cancellation and public Feed tombstones remain unchanged. No infrastructure, settings, RBAC, data migration, frontend, Feed-token rotation or Git push.

Validation: full suite 383 passed / 2 skipped, targeted personal/public Feed suite 60 passed, package tests 3 passed, Ruff and diff checks passed. Deterministic archive SHA-256 is `0097eb0ffacbaf9dd3211e5a3a24598145a7987175907eced2e0509bf845e9a4`; exact P2 recovery archive SHA-256 is `3a237a834315addccb1c46d63bb015f7936bb6899c58d3561ea4620c870ce663`. Bicep output remained byte-identical and was not deployed.

OneDeploy `9ea4685d-a09a-4eef-b33f-5ca5fa3ef60d` completed 2026-09-13T11:44:53Z with status 4, active/complete and remote build. Six Functions and both Azure/direct and Cloudflare-routed health/status endpoints were verified. Live Storage, Queue, Vault and database-scoped Cosmos roles remain unchanged. Existing personal Feed content was not mutated during deployment; daily window maintenance must republish it before Apple Calendar can remove old future F1 entries. See `evidence/personal-feed-unfollow-deployment-2026-09-13.md`.
