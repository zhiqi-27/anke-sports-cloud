# Anke Sports 开发部署执行记录

Status: Validated
Recipe: bicep

> 当前 A 候选（2026-09-17）不需要微信、YouTube、AI 或创作者配置；以下历史部署段落仅保留为已发生操作的证据，不是当前发布前置。A 本轮未部署。

本文件只记录当前部署执行与验证，不替代工作区实施计划或任务表。沿用用户已授权的独立资源创建配置、Azure Functions、Cosmos Serverless + Periodic 及 sports.anke-ai.com；不是生产环境授权。既有仓库使用 standalone Bicep，不初始化 azd。

## 1. Target

Subscription: a1187bf2-2e2f-4e05-aaea-407163a009f5 / Azure subscription 1
Region: eastasia
Resource group: anke-sports-dev
Template: infra/main.bicep
Parameters: infra/dev.bicepparam
Web origin: https://sports.anke-ai.com

## 2. Scope

独立 Functions Flex（无 always-ready）、Cosmos NoSQL Serverless/Strong/Periodic、Storage、Key Vault、网络与专用托管身份。21 项预演全部 Create，无现有产品修改。后端运行时关闭本地体验，启用 Firebase，先验证真实日历/个人 ICS 与现行直播入口。创作者/视频能力不在当前候选；未适配模块仍按接口明确返回未开放。

## 3. Cost

设计目标与假设见 ../docs/azure-low-cost-proposal.md；US$5–10/月是历史低流量设计目标，不是报价或硬上限。不新建 MySQL、常驻实例、额外监控平台或其他产品共享资源。

## 4. Secrets and access

Key Vault 中准备当前模板声明的 feed-encryption-key、firebase-credentials 和已选赛程 Provider 密钥；不准备 YouTube/AI 密钥。源码只含引用。托管身份权限仅到本资源组内所需资源。不得打印密钥。

## 5. Recovery

创建失败先查询部署操作，仅修失败项；不删除其他资源。代码包回退到上个已验收包；初次部署无上个版本，失败则停用相关函数并保留诊断。Cosmos Periodic 恢复到新账号，需恢复网络/RBAC和删除决定，不承诺即时原地回滚。删除资源需另行授权。

## 6. All validation checks pass

- [x] Core Validation: CLI/auth/build/validate/what-if helper
- [x] Azure Policy Validation
- [x] Build Verification
- [x] Static Role Verification
- [x] Record Proof and resolve errors

### A 候选预部署验证 · 2026-09-17

- [x] 读取并确认现有订阅、East Asia、资源组和 Function App 目标；未创建新资源。
- [x] 服务端全量测试 `239 passed / 2 skipped`、Ruff、compileall、diff check。
- [x] 客户端 contracts/typecheck/build、Worker `3/3` 测试和 diff check。
- [x] Bicep 编译及 subscription what-if；结果为现有 21 项 Deploy、无 Delete，本次不应用基础设施变更。
- [x] 静态复核 Cosmos、Storage、Key Vault 托管身份角色范围；不新增 RBAC。
- [x] 活跃 OpenAPI/配置/客户端契约无旧 selection、creator、YouTube/AI 字段或路径；当前部署不需要相关密钥。

## 7. Validation Proof

2026-09-17 A 候选复核：Azure CLI 当前订阅为 `Azure subscription 1` / `a1187bf2-2e2f-4e05-aaea-407163a009f5`，资源组 `anke-sports-dev` 位于 East Asia 且状态 `Succeeded`；目标 Function App 为 `anke-sports-dev-mtcflttk`。Bicep 编译通过，subscription what-if 仅报告现有 21 项 `Deploy`、无 `Delete`，本次只发布代码包，不应用基础设施 what-if。

服务端全量回归 `239 passed, 2 skipped`，Ruff、compileall、`git diff --check` 通过；客户端 `npm run typecheck`、`npm run build`、Worker 测试 `3/3` 和 `git diff --check` 通过。OpenAPI/config schema 已重新导出，客户端类型已重新生成。静态 RBAC 仍为数据库级 Cosmos Data Contributor、Storage 数据角色和 Key Vault Secrets User，无新增权限。活跃契约扫描未发现旧 selection、creator、YouTube/AI 配置或公开路径。

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

## 11. Team logo code-only update · 2026-09-13

Owner explicitly approved deployment of backend commit `243c5c9` and Web commits `a5f5cf7`/`85f0af4`. Azure target remains the validated development Function App in subscription `a1187bf2-2e2f-4e05-aaea-407163a009f5`, East Asia. Backend scope is code-only OneDeploy; no Bicep, settings, RBAC, schema/data migration or Git push. The production Cosmos document path accepts the additive optional source field; the SQL migration remains a local baseline artifact.

Validation proof: full backend suite `383 passed, 2 skipped`, Ruff, OpenAPI export, migration upgrade/downgrade and `alembic check` passed. Functions package tests are `3 passed`; candidate `data/anke-sports-20260913-logos.zip` has SHA-256 `fb05ace51154ba4a89873d6c5231266e952ed8e5f88fbc3773a820b5eb6c845a` and contains the logo provider, contract and migration modules. Target resource is Running/HTTPS-only and the current recovery package `data/anke-sports-20260913-unfollow.zip` retains SHA-256 `0097eb0ffacbaf9dd3211e5a3a24598145a7987175907eced2e0509bf845e9a4`. Web typecheck/build and Wrangler dry-run passed with 76 assets; Wrangler account matches the configured Worker account.

Recovery: if Functions startup or health readback fails, republish the exact unfollow recovery archive. If the Worker fails public readback, use Wrangler version rollback to the prior active version. Do not delete resources or mutate user configuration.

Deployment-time compatibility correction: the first successful code package exposed `logo_url` only after the next provider refresh. Commit `1f86ae9` adds read-time derivation for existing normalized NBA and football-data team IDs, so no provider-state or user-data mutation is required. Full suite remains `383 passed, 2 skipped`; final candidate `data/anke-sports-20260913-logos-backfill.zip` has SHA-256 `9c7dfd853579650baa690e320c74bbfc2833d40ba1821457331106148cec1c70` and passed the 3 packaging tests. Recovery remains the pre-logo archive above.

Deployment proof: final OneDeploy `96140a70-67a1-4fa8-a96d-375daca9c899` completed with status 4, active/complete and remote build. Public health is 200/no-store and the existing six Functions remain registered. Public sources return 30 NBA and 20 football Logo URLs immediately; two sampled assets returned 200. Live Storage, Queue, Vault and database-scoped Cosmos roles remain unchanged. Web Worker `3676cf53-3ac8-47ed-abee-f9dec91533cb` uploaded the regenerated static assets; signed-in browser readback visibly confirmed logos on the following page and in the match detail drawer while calendar cells remain logo-free.

## 12. Calendar scope and follow-policy code-only update · 2026-09-14

Owner explicitly approved deployment of backend commits `8fb47a4`, `83570f5`, and `02513e0` plus Web commits `2cdfd4d`, `f216427`, and `b774ca8`. Azure remains limited to the existing development Function App `anke-sports-dev-mtcflttk` in subscription `a1187bf2-2e2f-4e05-aaea-407163a009f5`, East Asia. This is code-only OneDeploy followed by publication of the existing `anke-sports-web` Worker; no Bicep, app settings, RBAC, schema/data migration, user configuration, Provider state, Git push, or new resource.

Validation proof: plan status remains Validated; Azure account, subscription, target, region, Running state and HTTPS-only setting read back. Full backend suite is `386 passed, 2 skipped`; Ruff and diff checks passed. Functions packaging tests are `3 passed`; deterministic candidate `data/anke-sports-20260914-calendar-following.zip` has SHA-256 `f9dbaeab9f32529f20755ad444d99033009d6ca95dfe804472dcbcd7fe00c46a`. Fresh Bicep compilation is byte-identical to tracked `infra/main.json` and infrastructure will not be deployed. Recovery remains `data/anke-sports-20260913-logos-backfill.zip`, SHA-256 `9c7dfd853579650baa690e320c74bbfc2833d40ba1821457331106148cec1c70`, with active OneDeploy `96140a70-67a1-4fa8-a96d-375daca9c899`. Web production-shaped static build contains the new calendar/follow flow, reads 76 assets, and passes Wrangler dry-run under the configured account.

Recovery: if Functions startup, registration, or health/API readback fails, republish the exact logo-backfill archive. If Worker public readback fails, roll back to Worker version `3676cf53-3ac8-47ed-abee-f9dec91533cb`. Do not delete resources or mutate user data.

Public readback correction: the first deployment exposed 31 basketball sources because a preseason guest opponent was retained in the historical catalog. Commit `d58c6be` keeps such opponents in event participants but removes them from the selectable source catalog and rejects direct follows. Targeted provider/follow tests are `38 passed`; full suite remains `386 passed, 2 skipped`. Corrected deterministic package `data/anke-sports-20260914-calendar-following-v2.zip` has SHA-256 `1954e4e7688e740ad76a3ad3a50a97f0bbe72070197ca80bd09d94512f0ba5e5`. Recovery remains the same pre-update logo-backfill package.

Deployment proof: initial OneDeploy `973aea1d-3c3a-4170-8716-b6888552fc7d` completed but was superseded after the public catalogue check. Corrected OneDeploy `14fb2ac0-4b07-446f-854a-77e5673274fb` completed with status 4, active/complete and remote build. Six Functions are registered; Azure-direct and Cloudflare-routed health plus public status return 200 with staging/Cosmos runtime. `source_id=jolpica:f1` returned 50 matching events in the checked range. Public sources now expose 30 NBA teams and no London Lions entry. Live Vault, Storage and database-scoped Cosmos roles match the validated plan. Web Worker `a87681cd-476e-41b3-9e08-c287e8d25bc4` is active on `sports.anke-ai.com`; signed-in browser readback confirmed the two-level team picker and personal-calendar default scope without changing the account.

## 13. Gemini video matching update · 2026-09-15

Owner authorized the existing Anke Sports development target: subscription `a1187bf2-2e2f-4e05-aaea-407163a009f5`, East Asia, resource group `anke-sports-dev`, Function App `anke-sports-dev-mtcflttk`. Scope is the cost-first Gemini 3.1 Flash-Lite matching code, an explicit dev-only app-setting merge, and code-only OneDeploy. Do not apply the infrastructure what-if, migrate SQL, publish the frontend, push Git, mutate provider state, or touch FormaLM resources.

### Validation steps

- [x] Confirm Azure account, subscription, resource group, Function App state, current deployment-status availability, and recovery package.
- [x] Run the full backend suite, Ruff, diff check, deterministic Functions packaging, and package manifest tests.
- [x] Compile Bicep and verify the tracked JSON; run validation/what-if only as evidence and do not apply infrastructure drift.
- [x] Statically verify least-privilege managed-identity roles and confirm no application RBAC expansion.
- [x] Verify the dedicated `gemini-api-key` secret metadata without reading its value; merge only the Gemini Key Vault reference, model, and feature flag into dev settings.
- [x] Publish the deterministic code package through OneDeploy and verify completion, six registered Functions, health/status routes, Key Vault reference resolution, and one bounded Gemini schema smoke test.

Recovery: retain the current active deployment package and deployment ID before publication. If startup, registration, or health fails, republish that exact package and restore the three prior app-setting values. Do not delete resources or user data.

### Validation proof

- Azure CLI is authenticated as `zero24dev@outlook.com` to `Azure subscription 1` / `a1187bf2-2e2f-4e05-aaea-407163a009f5`. Resource group `anke-sports-dev` is East Asia; `anke-sports-dev-mtcflttk` is Running and HTTPS-only. The deployment-status endpoint currently returns no history, so recovery is anchored to the retained package `data/anke-sports-20260914-calendar-following-v2.zip`, SHA-256 `1954e4e7688e740ad76a3ad3a50a97f0bbe72070197ca80bd09d94512f0ba5e5`.
- Google project `anke-sports-dev` has Gemini API enabled. Authorization key `Anke Sports Gemini API Key` is restricted to Gemini API and bound to the dedicated zero-role service account `anke-sports-gemini@anke-sports-dev.iam.gserviceaccount.com`. The key value was not written to source or documentation.
- Key Vault secret `gemini-api-key` exists and is enabled. A vault-scoped Secrets Officer assignment was used only for the write, removed immediately afterward, and the local clipboard was cleared. The application identity continues to use the existing vault-scoped Secrets User role.
- Full backend suite after the model correction: `394 passed, 2 skipped`; Ruff and `git diff --check` passed. Packaging tests: `3 passed`. Deterministic candidate `data/anke-sports-20260915-gemini-31.zip` has SHA-256 `08394b774f2691938d3dfdae024ccaf83057c664706ccf4e60b1c20c75ee0816`, 181944 bytes and 81 allowlisted runtime files; its manifest includes the Gemini matcher and additive SQL-baseline migration.
- Fresh Bicep compilation matches tracked `infra/main.json`. ARM subscription validation succeeded after replacing two embedded JSON string literals with Bicep-native `string(array)` expressions. ResourceIdOnly what-if succeeded and reports the existing 21 resources as Deploy; it will not be applied because this release is an explicit settings merge plus code-only OneDeploy.
- Static RBAC review found no new role assignment. Existing scopes remain: Cosmos data contributor at the dedicated database, Storage Blob/Queue data roles at the dedicated account, and Key Vault Secrets User at the dedicated vault, all assigned to the existing runtime managed identity.
- The originally selected `gemini-2.5-flash-lite` returned HTTP 404 for this newly created project. Current Google documentation identifies stable `gemini-3.1-flash-lite` as the cost-efficient high-volume model with video input and structured output. A bounded live call using the vault-held key returned HTTP 200, `STOP`, valid schema and one label. Temporary caller Secrets Officer access was removed after the test.

### Deployment proof

- OneDeploy `12e39360-d581-4003-a834-86be02a4e0c1` completed 2026-09-15T04:03:11Z with status 4, active/complete and remote build. The prior deployment is inactive and the retained `data/anke-sports-20260914-calendar-following-v2.zip` remains the recovery package.
- All six existing Functions are registered. Azure-direct and Cloudflare-routed `/api/v1/health` and `/api/v1/status` returned HTTP 200 with `Cache-Control: no-store`; health reports `staging` and `cosmos`.
- Dev settings read back as enabled with model `gemini-3.1-flash-lite`; `GEMINI_API_KEY` is a Key Vault reference and its reference status is `Resolved` using the user-assigned runtime identity.
- The deployed package was followed by an actual application-level synthetic metadata call through `matching-ai-v3`: it returned `automatic`, two valid labels and an AI confidence code. This proves the configured schema path, not real-video matching accuracy or calendar delivery.
- Live roles remain unchanged: Key Vault Secrets User at the dedicated vault, Storage Queue Data Contributor and Storage Blob Data Owner at the dedicated storage account, and Cosmos DB Built-in Data Contributor at the dedicated `anke-sports` database. Temporary operator Secrets Officer access reads back empty.

## 14. Event-driven YouTube discovery update · 2026-09-15

The owner explicitly requested deployment to the existing Anke Sports development environment. Scope is a code-only Functions publication to `anke-sports-dev-mtcflttk`, followed by the existing `anke-sports-web` Worker publication. It adds deterministic per-event YouTube search, Gemini scoring with comments, shared discovery runs, separate search quota, channel reputation metadata, user search-window preferences, and the user-facing copy cleanup. AnySearch remains disabled. No Bicep deployment, app-setting mutation, RBAC change, database migration, user-data mutation, Git push, or production action is authorized.

Validation checklist:

- [x] Confirm Azure subscription, resource group, Function App and Cloudflare account.
- [x] Run backend Ruff, tests, package manifest checks and `git diff --check`.
- [x] Run frontend contract generation, typecheck, static export build and Wrangler dry-run.
- [x] Review static RBAC and confirm infrastructure is unchanged.
- [x] Record frozen candidate and recovery identifiers before publication.
- [x] Publish both targets and verify deployment completion, registered Functions, public health/status, Worker version and public UI assets.

Recovery: retain the current active OneDeploy and its package identifier before publication. If the new Functions package does not become active or fails health/status readback, republish the retained prior package. If Web readback fails, roll back to the prior active Worker version. Do not delete resources or mutate user data.

### Validation proof

- Azure CLI read back `Azure subscription 1` / `a1187bf2-2e2f-4e05-aaea-407163a009f5`, resource group `anke-sports-dev`, East Asia Function App `anke-sports-dev-mtcflttk`, Running and HTTPS-only. Six pre-update Functions are registered. Wrangler 4.131.0 is authenticated to account `6be1e7b072eb22de20bb3c58fbf56585`; pre-update Worker version is `77332df3-ca6c-4d37-b6ce-a97135b8819c`.
- Backend commit `aba78a5` passed Ruff, `406 passed / 2 skipped`, three package tests and `git diff --check`. Candidate `data/anke-sports-20260915-event-discovery.zip` is 194345 bytes with SHA-256 `ee73544c34cf9ef20986d78d34bdd2a2f1f151a3f08ddb68f7faa7491460158b`; its 83-file manifest includes `app/document_discovery.py`, `app/document_worker.py` and `function_app.py`.
- Frontend commit `5191038` passed contract generation, typecheck, a production-shaped static export and Wrangler dry-run with 99 assets.
- Fresh Bicep compilation is byte-identical to tracked `infra/main.json`; no infrastructure file changed and Bicep will not be applied. Live roles remain vault-scoped Secrets User, storage-scoped Queue Data Contributor and Blob Data Owner, plus Cosmos Built-in Data Contributor scoped to the dedicated `anke-sports` database. No permission is expanded.
- Azure deployment-status history is empty for this Flex app, so the documented prior active code package remains the recovery source; the Web recovery version is the directly read back version above.

### Deployment proof

- Azure accepted OneDeploy `ef8e70c4-b39d-4378-a357-0e1fd1106c45`; the CLI completed trigger synchronization and health validation with `Deployment was successful.` Six Functions are registered. Azure-direct and Cloudflare-routed health are HTTP 200 with `staging` / `cosmos`; public status exposes `event_search_only`, disabled Web fallback and 80 available daily search calls. Flex publishing credentials and the management deployment-status collection are unavailable, so this release does not claim a separate status-4/active readback.
- Wrangler uploaded 39 changed assets and activated Worker version `75891ba3-a299-4007-8e8e-33f80105200a` on `https://sports.anke-ai.com`. The public route is HTTP 200 and the deployed asset contains the new concise video copy without the removed threshold explanation.
- No real scheduled discovery, user Feed refresh or device playback was forced during deployment. See `../evidence/event-youtube-discovery-deployment-2026-09-15.md`.

## 15. Official-channel scope and F1 team-only update · 2026-09-16

The owner explicitly requested deployment to the existing Anke Sports development environment. Scope is a code-only Functions publication to `anke-sports-dev-mtcflttk`, followed by the existing `anke-sports-web` Worker publication. It retires event-wide YouTube search scheduling, adds verified official-channel scope fields, and changes direct follows to concrete teams/constructors while keeping the shared F1 session calendar. No Bicep, app-setting, RBAC, schema migration, user-data mutation, Git push or production action is authorized.

Validation and recovery: the full backend suite is `406 passed / 2 skipped`, Ruff and both repository diff checks passed, the three package tests passed, and the 83-file candidate SHA-256 is `bbe0ef48cc350a902475ab8a138b6cb48356bf17cfba476d9a61a5f5e176d73e`. Recovery retains the prior package SHA-256 `ee73544c34cf9ef20986d78d34bdd2a2f1f151a3f08ddb68f7faa7491460158b`; Web recovery is the immediately previous version `3d2f44ff-e62b-4d6c-a875-a54ead104ee7`. The clean 99-asset Web build passed Firebase configuration verification and Wrangler dry-run.

Deployment proof: Azure CLI completed remote build, trigger synchronization and health validation with `Deployment was successful.` Six Functions remain registered; both origins returned HTTP 200 and public status reports `official_catalog_foundation`. Wrangler activated version `269a483f-2c19-4006-8e5b-6da40cd96e57`; no-cache live assets contain the F1 team-only copy and omit the retired search-time copy. The live Jolpica snapshot predates the deployment and still has zero constructors; it becomes normally eligible for refresh after 2026-09-16T08:30:26Z. No Provider state was forced. See `../evidence/official-channel-f1-team-deployment-2026-09-16.md`.
