# Official broadcast deployment · 2026-09-15

Target: existing Azure development Function App `anke-sports-dev-mtcflttk` in East Asia. This was a code-only update. No infrastructure, app setting, RBAC, database, Provider state, public Feed allowlist, user data or broadcast record was changed.

The candidate used the active Gemini v3 archive `anke-sports-20260915-gemini-31.zip` (SHA-256 `08394b774f2691938d3dfdae024ccaf83057c664706ccf4e60b1c20c75ee0816`) as its recovery/base package. A deterministic overlay replaced exactly:

- `app/platforms.py`
- `app/broadcast_rules.py`
- `app/broadcast_schemas.py`
- `app/broadcasts.py`
- `app/document_broadcasts.py`

The resulting archive SHA-256 is `485f306fbb90436378bb6d5ac60f8f0e1b24edc3a5128e2a149a8ce49358934e`. Manifest comparison confirmed no other runtime file changed, which preserves the deployed Gemini v3 files while excluding local v4 work.

Validation: full current worktree suite passed 397 tests with 2 skips after using the repository fallback Git; the isolated HEAD suite passed 386 tests with 2 skips after one timing-sensitive Provider test passed on immediate retry. The exact active-overlay package passed the 25 SQL/document broadcast tests, Ruff, runtime import and rights-matrix assertions. The Web contract was exported from this exact runtime.

OneDeploy `c3b4f77d-9c34-4a05-8d91-2a23986e6b43` completed through the Azure CLI health check. Management readback reports `BuildSuccessful` with no errors; all six Functions are registered. Azure-direct and Cloudflare-routed health returned HTTP 200 with `staging` and `cosmos`. Both origins returned 26 platform candidates, including US Apple TV, CN Tencent/Migu, JP FOD/U-NEXT and country-specific European rights. FOD read back as `web_handoff`; U-NEXT read back as `verified_https_app_link`.

This proves deployment and the selection/validation contract. It does not prove a particular event can play, because no real per-event broadcast publication or phone device test was created in this deployment.
