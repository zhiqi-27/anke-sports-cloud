# Team logo backend deployment · 2026-09-13

## Published artifact

- Final source commits: `243c5c9` and compatibility follow-up `1f86ae9`.
- Target: existing development Function App `anke-sports-dev-mtcflttk` in East Asia.
- Final package: `data/anke-sports-20260913-logos-backfill.zip`.
- SHA-256: `9c7dfd853579650baa690e320c74bbfc2833d40ba1821457331106148cec1c70`.
- Recovery package: `data/anke-sports-20260913-unfollow.zip`, SHA-256 `0097eb0ffacbaf9dd3211e5a3a24598145a7987175907eced2e0509bf845e9a4`.

The first additive contract package completed as OneDeploy `5f16e6a9-d170-450d-850a-23d51676cfef`; it required a future Provider refresh to populate existing source rows and was superseded by the compatibility package. Final OneDeploy `96140a70-67a1-4fa8-a96d-375daca9c899` completed at 2026-09-13T14:22:27Z with status 4, active/complete and remote build.

## Verification

- Full suite: 383 passed / 2 skipped; Ruff, OpenAPI generation, migration upgrade/downgrade, `alembic check`, package tests and diff checks passed.
- Six Functions remained registered; public health returned HTTP 200, `staging` / `cosmos`, with `Cache-Control: no-store`.
- Public real-source readback returned 54 sources and 51 teams. Logo coverage is 30 NBA and 20 football teams; two sampled asset requests returned HTTP 200.
- The remaining `London Lions` BALLDONTLIE source has no NBA official-ID mapping and intentionally falls back to `LON` instead of using an unverified logo.
- Live roles were unchanged: Key Vault Secrets User, Storage Queue Data Contributor, Storage Blob Data Owner, and Cosmos Built-in Data Contributor scoped to the `anke-sports` database.

This was a code-only development deployment. No Bicep, app settings, RBAC, user configuration, provider state, SQL migration, Git push or production infrastructure was changed.
