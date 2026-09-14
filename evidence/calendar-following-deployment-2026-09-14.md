# Calendar scope and follow policy deployment · 2026-09-14

## Published backend

- Target: existing Azure development Function App `anke-sports-dev-mtcflttk`, East Asia.
- Source commits: `8fb47a4`, `83570f5`, `02513e0`, and public-readback correction `d58c6be`.
- Final package: `data/anke-sports-20260914-calendar-following-v2.zip`.
- SHA-256: `1954e4e7688e740ad76a3ad3a50a97f0bbe72070197ca80bd09d94512f0ba5e5`.
- Recovery package: `data/anke-sports-20260913-logos-backfill.zip`, SHA-256 `9c7dfd853579650baa690e320c74bbfc2833d40ba1821457331106148cec1c70`.
- Final OneDeploy: `14fb2ac0-4b07-446f-854a-77e5673274fb`, status 4, active/complete, remote build; completed at 2026-09-14T07:52:19Z.

The first package completed as OneDeploy `973aea1d-3c3a-4170-8716-b6888552fc7d`. Public review then found a preseason guest opponent in the NBA source catalogue. The final package keeps guest opponents on their games but excludes them from source selection and direct follows; the initial deployment is superseded.

## Verification

- Ruff, `git diff --check`, 38 targeted tests, 3 package tests, and full `386 passed / 2 skipped` passed.
- Fresh Bicep compilation was byte-identical to tracked `infra/main.json`; no infrastructure was deployed.
- Six Functions are registered. Azure-direct health, Cloudflare-routed health, and public status returned HTTP 200 with staging/Cosmos runtime.
- F1 source filtering returned 50 events in the checked range and every result matched `jolpica:f1`.
- Public sources expose 30 NBA teams and zero London Lions entries.
- Runtime identity roles remain Vault Secrets User, Storage Queue Data Contributor, Storage Blob Data Owner, and Cosmos Built-in Data Contributor scoped to the `anke-sports` database.

This was a code-only development deployment. It did not change infrastructure, settings, RBAC, user configuration, provider jobs, Feed tokens, or Git remotes.
