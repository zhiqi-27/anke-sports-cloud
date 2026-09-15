# Integrated backend release · 2026-09-15

Commit `1abd2aa` integrates Gemini v4 video matching, public-comment evidence, official-broadcast rights for the United States, mainland China, Japan and supported European countries, and the region-plus-competition broadcast preference used by personal calendars.

Validation before release: full suite `398 passed, 2 skipped`; Functions package tests `3 passed`; Ruff and diff checks passed. Bicep compiled byte-for-byte to tracked `infra/main.json`; infrastructure was not applied. Deterministic code package `data/anke-sports-20260915-integrated.zip` is 185557 bytes with SHA-256 `ef1e654349c77fc5fee43003e517d9b74b3759281128aea0fedce5b1aafd9bc0`.

The package was deployed to the existing East Asia development Function App `anke-sports-dev-mtcflttk`. OneDeploy `6a641c89-c1ed-41bf-8011-a5a9d0a82e87` completed successfully through the Azure CLI remote-build and health workflow. Six Functions are registered. Azure-direct and Cloudflare-routed health returned HTTP 200 with `staging` and `cosmos`; both origins returned 26 broadcast platforms. The live OpenAPI document contains `broadcast_platforms`, and unauthenticated maintenance access remains HTTP 401. Live RBAC readback remains limited to vault Secrets User, Storage Queue Contributor, Storage Blob Owner and Cosmos built-in data contributor scoped to the dedicated `anke-sports` database.

No Bicep deployment, RBAC mutation, app-setting mutation, provider sync, user preference write, public Feed enablement, SQL migration or Git push occurred. Real per-event broadcast records and phone playback remain separate acceptance work.
